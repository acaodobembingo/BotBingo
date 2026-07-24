from dotenv import load_dotenv
load_dotenv()
import logging
import json
import sqlite3
import os
import urllib.request
import urllib.error
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==============================================================================
# CONFIGURAÇÕES INICIAIS DO BOT
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID"))
DB_NAME = "bingo_beneficente.db"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ==============================================================================
# CHAMADA PARA A API DO GEMINI (HTTP / ESTÁVEL)
# ==============================================================================
def chamar_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    json_data = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=json_data, headers=headers, method='POST')

    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode('utf-8')
            res_json = json.loads(res_body)
            candidates = res_json.get('candidates', [])
            if candidates and 'content' in candidates[0]:
                return candidates[0]['content']['parts'][0]['text']
            return "Não consegui processar sua dúvida agora. Digite /start para ver o menu."
    except Exception as e:
        logging.error(f"Erro Gemini: {e}")
        return "Erro ao processar resposta no momento. Digite /start para ver o menu."

# ==============================================================================
# BANCO DE DADOS (SQLITE)
# ==============================================================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS configuracao (
            id INTEGER PRIMARY KEY,
            titulo TEXT,
            historia TEXT,
            premio TEXT,
            premio2 TEXT DEFAULT NULL,
            premio3 TEXT DEFAULT NULL,
            valor_cota REAL,
            qtd_numeros INTEGER DEFAULT 100,
            chave_pix TEXT,
            data_sorteio TEXT,
            media_id TEXT DEFAULT NULL,
            media_type TEXT DEFAULT NULL
        )
    ''')

    # Atualiza tabela caso venha de versão antiga sem premio2/premio3
    try:
        c.execute('ALTER TABLE configuracao ADD COLUMN premio2 TEXT DEFAULT NULL')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE configuracao ADD COLUMN premio3 TEXT DEFAULT NULL')
    except sqlite3.OperationalError:
        pass

    c.execute('SELECT COUNT(*) FROM configuracao')
    if c.fetchone()[0] == 0:
        c.execute('''
            INSERT INTO configuracao (id, titulo, historia, premio, premio2, premio3, valor_cota, qtd_numeros, chave_pix, data_sorteio, media_id, media_type)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
        ''', (
            "Ação Beneficente Família Alves",
            "Estamos realizando esta ação beneficente para arrecadar fundos para exames e tratamentos de saúde.",
            "1º PRÊMIO: Smartphone 128GB (ou R$ 1.000,00 no Pix)",
            None,
            None,
            10.00,
            100,
            "74981552779",
            "A definir (Loteria Federal)"
        ))

    c.execute('''
        CREATE TABLE IF NOT EXISTS bilhetes (
            numero INTEGER PRIMARY KEY,
            user_id INTEGER,
            username TEXT,
            status TEXT DEFAULT 'LIVRE'
        )
    ''')

    c.execute('SELECT COUNT(*) FROM bilhetes')
    if c.fetchone()[0] == 0:
        for i in range(100):
            c.execute('INSERT INTO bilhetes (numero, status) VALUES (?, ?)', (i, 'LIVRE'))

    conn.commit()
    conn.close()

def get_config():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT titulo, historia, premio, premio2, premio3, valor_cota, qtd_numeros, chave_pix, data_sorteio, media_id, media_type FROM configuracao WHERE id = 1')
    res = c.fetchone()
    conn.close()
    return {
        "titulo": res[0], "historia": res[1], "premio": res[2],
        "premio2": res[3], "premio3": res[4], "valor_cota": res[5],
        "qtd_numeros": res[6], "chave_pix": res[7], "data_sorteio": res[8],
        "media_id": res[9], "media_type": res[10]
    }

def update_config_field(field_name, value):
    campos_permitidos = ["titulo", "historia", "premio", "premio2", "premio3", "valor_cota", "qtd_numeros", "chave_pix", "data_sorteio", "media_id", "media_type"]
    if field_name not in campos_permitidos:
        return

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(f"UPDATE configuracao SET {field_name} = ? WHERE id = 1", (value,))
    conn.commit()
    conn.close()

def reconfigurar_quantidade_bilhetes(nova_qtd):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM bilhetes")
    for i in range(nova_qtd):
        c.execute('INSERT INTO bilhetes (numero, status) VALUES (?, ?)', (i, 'LIVRE'))
    c.execute("UPDATE configuracao SET qtd_numeros = ? WHERE id = 1", (nova_qtd,))
    conn.commit()
    conn.close()

def reset_memoria_bingo():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE bilhetes SET status = 'LIVRE', user_id = NULL, username = NULL")
    conn.commit()
    conn.close()

def formatar_numero(num, qtd_total):
    if qtd_total <= 100:
        return f"{num:02d}"
    elif qtd_total <= 1000:
        return f"{num:03d}"
    else:
        return f"{num:04d}"

# ==============================================================================
# FLUXO DO CLIENTE
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = get_config()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM bilhetes WHERE status = 'PAGO'")
    pagos = c.fetchone()[0]
    conn.close()

    progresso = int((pagos / cfg['qtd_numeros']) * 100)

    texto_premios = f"🥇 *1º PRÊMIO:* {cfg['premio']}\n"
    if cfg['premio2']:
        texto_premios += f"🥈 *2º PRÊMIO:* {cfg['premio2']}\n"
    if cfg['premio3']:
        texto_premios += f"🥉 *3º PRÊMIO:* {cfg['premio3']}\n"

    formato_nome = "Dezenas (00-99)" if cfg['qtd_numeros'] == 100 else ("Centenas (000-999)" if cfg['qtd_numeros'] == 1000 else "Milhares (0000-9999)")

    texto = (
        f"🤝 *{cfg['titulo']}*\n"
        f"────────────────────────────────────────\n"
        f"📖 *NOSSA HISTÓRIA:*\n{cfg['historia']}\n\n"
        f"🎁 *PREMIAÇÃO:*\n{texto_premios}\n"
        f"🎟️ *VALOR DA COTA:* R$ {cfg['valor_cota']:.2f}\n"
        f"🔢 *MODALIDADE:* {formato_nome}\n"
        f"📅 *SORTEIO:* {cfg['data_sorteio']}\n\n"
        f"📊 *PROGRESSO:* {progresso}% concluído ({pagos}/{cfg['qtd_numeros']})\n"
        f"────────────────────────────────────────\n"
        f"Escolha uma opção abaixo para colaborar:"
    )

    keyboard = [
        [InlineKeyboardButton("🎟️ Escolher / Ver Números", callback_data="ver_numeros")],
        [InlineKeyboardButton("📋 Meus Bilhetes", callback_data="meus_bilhetes")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    chat_id = update.effective_chat.id

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass

    if cfg['media_id'] and cfg['media_type']:
        if cfg['media_type'] == 'photo':
            await context.bot.send_photo(chat_id=chat_id, photo=cfg['media_id'], caption=texto, parse_mode="Markdown", reply_markup=reply_markup)
        elif cfg['media_type'] == 'video':
            await context.bot.send_video(chat_id=chat_id, video=cfg['media_id'], caption=texto, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await context.bot.send_message(chat_id=chat_id, text=texto, parse_mode="Markdown", reply_markup=reply_markup)

async def meus_bilhetes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    cfg = get_config()

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT numero, status FROM bilhetes WHERE user_id = ?", (user_id,))
    meus = c.fetchall()
    conn.close()

    if not meus:
        texto = "📋 *SEUS BILHETES*\n────────────────────────────────────────\nVocê ainda não escolheu nenhum número."
    else:
        texto = "📋 *SEUS BILHETES*\n────────────────────────────────────────\n"
        for num, status in meus:
            st = "✅ Pago/Confirmado" if status == "PAGO" else "⏳ Reservado (Aguardando Comprovante)"
            num_fmt = formatar_numero(num, cfg['qtd_numeros'])
            texto += f"• *Nº {num_fmt}*: {st}\n"

    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="menu_principal")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception:
        await query.message.delete()
        await context.bot.send_message(chat_id=query.message.chat_id, text=texto, parse_mode="Markdown", reply_markup=reply_markup)

async def ver_numeros(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cfg = get_config()

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT status, COUNT(*) FROM bilhetes GROUP BY status")
    stats = dict(c.fetchall())

    pagos = stats.get('PAGO', 0)
    reservados = stats.get('RESERVADO', 0)
    livres = stats.get('LIVRE', 0)

    if cfg['qtd_numeros'] <= 100:
        c.execute("SELECT numero, status FROM bilhetes ORDER BY numero ASC")
        bilhetes = c.fetchall()
        grid = ""
        for num, status in bilhetes:
            icon = "🟩" if status == 'LIVRE' else ("🟥" if status == 'PAGO' else "🟨")
            grid += f"{formatar_numero(num, cfg['qtd_numeros'])} {icon} | "
            if (num + 1) % 5 == 0:
                grid += "\n"

        texto = (
            f"🎟️ *SELEÇÃO DE NÚMEROS BENEFICENTES*\n"
            f"────────────────────────────────────────\n"
            f"Legenda: 🟩 Livre | 🟥 Pago | 🟨 Reservado\n\n"
            f"{grid}\n"
            f"Para reservar, digite o número desejado no chat (Exemplo: `{formatar_numero(7, cfg['qtd_numeros'])}`)."
        )
    else:
        exemplo = formatar_numero(123 if cfg['qtd_numeros'] == 1000 else 1234, cfg['qtd_numeros'])
        texto = (
            f"🎟️ *CONSULTA DE NÚMEROS BENEFICENTES*\n"
            f"────────────────────────────────────────\n"
            f"Total de bilhetes: *{cfg['qtd_numeros']}*\n"
            f"🟩 Disponíveis: *{livres}*\n"
            f"🟨 Reservados: *{reservados}*\n"
            f"🟥 Confirmados/Pagos: *{pagos}*\n"
            f"────────────────────────────────────────\n"
            f"Para escolher e reservar o seu bilhete, **digite o número desejado no chat**!\n"
            f"👉 Exemplo: Digite `{exemplo}` no chat."
        )

    conn.close()

    keyboard = [[InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="menu_principal")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception:
        await query.message.delete()
        await context.bot.send_message(chat_id=query.message.chat_id, text=texto, parse_mode="Markdown", reply_markup=reply_markup)

async def processar_mensagem_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    texto = update.message.text.strip()

    if user_id == ADMIN_TELEGRAM_ID and context.user_data.get("editing_field"):
        field = context.user_data["editing_field"]

        if field == "valor_cota":
            try:
                valor = float(texto.replace(",", "."))
                update_config_field(field, valor)
            except ValueError:
                await update.message.reply_text("❌ Valor inválido! Digite apenas números. Ex: 10.00")
                return
        else:
            update_config_field(field, texto)

        context.user_data["editing_field"] = None
        await update.message.reply_text(f"✅ Campo *{field.upper()}* atualizado com sucesso!", parse_mode="Markdown")
        await admin_panel(update, context)
        return

    if texto.isdigit():
        num = int(texto)
        cfg = get_config()

        if num < 0 or num >= cfg['qtd_numeros']:
            ex_max = formatar_numero(cfg['qtd_numeros'] - 1, cfg['qtd_numeros'])
            await update.message.reply_text(f"❌ Número fora do limite! Escolha entre `0` e `{ex_max}`.")
            return

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT status FROM bilhetes WHERE numero = ?", (num,))
        res = c.fetchone()

        if not res or res[0] != 'LIVRE':
            await update.message.reply_text("⚠️ Este número já está reservado ou foi pago. Escolha outro!")
            conn.close()
            return

        c.execute("UPDATE bilhetes SET status = 'RESERVADO', user_id = ?, username = ? WHERE numero = ?",
                  (user_id, update.message.from_user.username or "SemUsername", num))
        conn.commit()
        conn.close()

        num_fmt = formatar_numero(num, cfg['qtd_numeros'])
        resposta = (
            f"⏳ *RESERVA CONFIRMADA - Nº {num_fmt}*\n"
            f"────────────────────────────────────────\n"
            f"Valor da cota: *R$ {cfg['valor_cota']:.2f}*\n\n"
            f"🔑 *CHAVE PIX (Copia e Cola):*\n`{cfg['chave_pix']}`\n\n"
            f"⚠️ *INSTRUÇÕES:*\n"
            f"1. Faça a transferência do valor via Pix.\n"
            f"2. Envie o **comprovante (Foto ou PDF)** AQUI no chat para liberação do seu bilhete!"
        )
        await update.message.reply_text(resposta, parse_mode="Markdown")
    else:
        prompt = f"O usuário enviou a mensagem: '{texto}'. Responda de forma curta e amigável dizendo como funciona a Ação Beneficente do Bingo e incentivando a digitar /start para participar."
        resposta_ia = chamar_gemini(prompt)
        await update.message.reply_text(resposta_ia)

async def receber_midia_comprovante(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    cfg = get_config()

    if user_id == ADMIN_TELEGRAM_ID and context.user_data.get("editing_media"):
        if update.message.photo:
            media_id = update.message.photo[-1].file_id
            media_type = 'photo'
        elif update.message.video:
            media_id = update.message.video.file_id
            media_type = 'video'
        else:
            await update.message.reply_text("⚠️ Envie uma Foto ou Vídeo válido.")
            return

        update_config_field("media_id", media_id)
        update_config_field("media_type", media_type)
        context.user_data["editing_media"] = False

        await update.message.reply_text(f"✅ *Mídia do Perfil atualizada com sucesso!* ({media_type.upper()})", parse_mode="Markdown")
        await admin_panel(update, context)
        return

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT numero FROM bilhetes WHERE user_id = ? AND status = 'RESERVADO'", (user_id,))
    reserva = c.fetchone()

    if not reserva:
        await update.message.reply_text("⚠️ Você não possui nenhum número reservado pendente de pagamento.")
        conn.close()
        return

    num_reservado = reserva[0]
    conn.close()

    num_fmt = formatar_numero(num_reservado, cfg['qtd_numeros'])
    await update.message.reply_text("📥 *Comprovante recebido!* Enviamos para o administrador liberar seu número em instantes.", parse_mode="Markdown")

    keyboard = [
        [
            InlineKeyboardButton("✅ Aprovar", callback_data=f"admin_app_{num_reservado}_{user_id}"),
            InlineKeyboardButton("❌ Rejeitar", callback_data=f"admin_rej_{num_reservado}_{user_id}")
        ]
    ]

    await context.bot.send_message(
        chat_id=ADMIN_TELEGRAM_ID,
        text=f"📥 *NOVO COMPROVANTE RECEBIDO*\n\n"
             f"• Cliente: @{update.message.from_user.username}\n"
             f"• Número Reservado: *Nº {num_fmt}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    if update.message.photo:
        await context.bot.send_photo(chat_id=ADMIN_TELEGRAM_ID, photo=update.message.photo[-1].file_id)
    elif update.message.document:
        await context.bot.send_document(chat_id=ADMIN_TELEGRAM_ID, document=update.message.document.file_id)

# ==============================================================================
# PAINEL COMPLETO DE ADMIN
# ==============================================================================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    if user_id != ADMIN_TELEGRAM_ID:
        return

    texto = "🛠️ *PAINEL DE CONTROLE - ADMINISTRADOR*\n────────────────────────────────────────\nSelecione uma opção de gestão:"

    keyboard = [
        [InlineKeyboardButton("📊 Relatório Financeiro", callback_data="admin_relatorio")],
        [InlineKeyboardButton("⏳ Reservas Pendentes", callback_data="admin_pendentes")],
        [InlineKeyboardButton("⚙️ Editar Dados da Ação", callback_data="admin_config_menu")],
        [InlineKeyboardButton("🏆 Marcar Ganhadores", callback_data="admin_ganhador_prompt")],
        [InlineKeyboardButton("🧹 Zerar Memória / Novo Bingo", callback_data="admin_reset_confirm")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        try:
            await update.callback_query.message.edit_text(texto, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception:
            await update.callback_query.message.delete()
            await context.bot.send_message(chat_id=user_id, text=texto, parse_mode="Markdown", reply_markup=reply_markup)

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    cfg = get_config()

    if data == "admin_menu":
        context.user_data["editing_field"] = None
        context.user_data["editing_media"] = False
        await admin_panel(update, context)
        return

    if data == "admin_relatorio":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT status, COUNT(*) FROM bilhetes GROUP BY status")
        stats = dict(c.fetchall())
        conn.close()

        pagos = stats.get('PAGO', 0)
        reservados = stats.get('RESERVADO', 0)
        livres = stats.get('LIVRE', 0)

        texto = (
            f"📊 *RELATÓRIO FINANCEIRO*\n"
            f"────────────────────────────────────────\n"
            f"🟢 Arrecadado: *R$ {pagos * cfg['valor_cota']:.2f}*\n"
            f"🟡 A Receber: *R$ {reservados * cfg['valor_cota']:.2f}*\n"
            f"────────────────────────────────────────\n"
            f"🟥 Pagas: *{pagos}* | 🟨 Reservadas: *{reservados}* | 🟩 Livres: *{livres}*"
        )
        keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_menu")]]
        await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_pendentes":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT numero, user_id, username FROM bilhetes WHERE status = 'RESERVADO'")
        reservas = c.fetchall()
        conn.close()

        if not reservas:
            texto = "⏳ *RESERVAS PENDENTES*\n────────────────────────────────────────\nNenhuma reserva pendente."
            keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_menu")]]
            await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        texto = "⏳ *RESERVAS PENDENTES:*\n────────────────────────────────────────\n"
        keyboard = []
        for num, u_id, u_name in reservas:
            num_fmt = formatar_numero(num, cfg['qtd_numeros'])
            texto += f"• *Nº {num_fmt}* - @{u_name}\n"
            keyboard.append([
                InlineKeyboardButton(f"✅ Aprovar {num_fmt}", callback_data=f"admin_app_{num}_{u_id}"),
                InlineKeyboardButton(f"❌ Liberar {num_fmt}", callback_data=f"admin_rej_{num}_{u_id}")
            ])

        keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="admin_menu")])
        await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_config_menu":
        status_media = f"Ativa ({cfg['media_type'].upper()})" if cfg['media_id'] else "Sem Mídia"

        p2 = cfg['premio2'] if cfg['premio2'] else "Não configurado"
        p3 = cfg['premio3'] if cfg['premio3'] else "Não configurado"

        texto = (
            f"⚙️ *CONFIGURAÇÃO DA AÇÃO*\n"
            f"────────────────────────────────────────\n"
            f"1. Título: *{cfg['titulo']}*\n"
            f"2. 🥇 1º Prêmio: *{cfg['premio']}*\n"
            f"3. 🥈 2º Prêmio: *{p2}*\n"
            f"4. 🥉 3º Prêmio: *{p3}*\n"
            f"5. Valor Cota: *R$ {cfg['valor_cota']:.2f}*\n"
            f"6. Total de Números: *{cfg['qtd_numeros']}*\n"
            f"7. Chave Pix: *{cfg['chave_pix']}*\n"
            f"8. Data Sorteio: *{cfg['data_sorteio']}*\n"
            f"9. Mídia Perfil: *{status_media}*\n\n"
            f"Clique no campo que deseja alterar:"
        )
        keyboard = [
            [InlineKeyboardButton("✏️ Título", callback_data="cfg_edit_titulo"), InlineKeyboardButton("🔢 Modo (Dezena/Centena/Milhar)", callback_data="cfg_edit_qtd_numeros")],
            [InlineKeyboardButton("🥇 1º Prêmio", callback_data="cfg_edit_premio"), InlineKeyboardButton("🥈 2º Prêmio", callback_data="cfg_edit_premio2")],
            [InlineKeyboardButton("🥉 3º Prêmio", callback_data="cfg_edit_premio3"), InlineKeyboardButton("✏️ Valor Cota", callback_data="cfg_edit_valor_cota")],
            [InlineKeyboardButton("✏️ Chave Pix", callback_data="cfg_edit_chave_pix"), InlineKeyboardButton("✏️ História/Descrição", callback_data="cfg_edit_historia")],
            [InlineKeyboardButton("✏️ Data Sorteio", callback_data="cfg_edit_data_sorteio"), InlineKeyboardButton("🖼️ Alterar Mídia", callback_data="cfg_edit_media")],
            [InlineKeyboardButton("🗑️ Remover Mídia", callback_data="cfg_del_media")],
            [InlineKeyboardButton("⬅️ Voltar ao Painel Admin", callback_data="admin_menu")]
        ]
        await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "cfg_edit_qtd_numeros":
        texto = (
            "🔢 *ESCOLHA O FORMATO DA RIFA/BINGO*\n"
            "────────────────────────────────────────\n"
            "⚠️ *Atenção:* Alterar a quantidade de números vai **zerar a cartela atual** para reiniciar no novo formato!\n\n"
            "Escolha uma das opções abaixo:"
        )
        keyboard = [
            [InlineKeyboardButton("Dezena (00 a 99) - 100 Números", callback_data="set_qtd_100")],
            [InlineKeyboardButton("Centena (000 a 999) - 1.000 Números", callback_data="set_qtd_1000")],
            [InlineKeyboardButton("Milhar (0000 a 9999) - 10.000 Números", callback_data="set_qtd_10000")],
            [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_config_menu")]
        ]
        await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("set_qtd_"):
        nova_qtd = int(data.split("_")[2])
        reconfigurar_quantidade_bilhetes(nova_qtd)
        await query.message.edit_text(f"✅ Formato atualizado para **{nova_qtd} Números** com sucesso!", parse_mode="Markdown")

    elif data == "cfg_edit_media":
        context.user_data["editing_media"] = True
        await query.message.edit_text(
            "🖼️ *ALTERAR FOTO OU VÍDEO DO PERFIL*\n"
            "────────────────────────────────────────\n"
            "Envie uma **Foto** ou **Vídeo** diretamente aqui no chat para salvar como imagem principal da ação:",
            parse_mode="Markdown"
        )

    elif data == "cfg_del_media":
        update_config_field("media_id", None)
        update_config_field("media_type", None)
        await query.message.edit_text("🗑️ Mídia removida com sucesso! O perfil voltou a ser apenas texto.")

    elif data.startswith("cfg_edit_"):
        field = data.replace("cfg_edit_", "")
        context.user_data["editing_field"] = field
        nome_exibicao = field.upper().replace("PREMIO2", "2º PRÊMIO").replace("PREMIO3", "3º PRÊMIO").replace("PREMIO", "1º PRÊMIO")
        await query.message.edit_text(
            f"✏️ *ALTERAR {nome_exibicao}*\n────────────────────────────────────────\n"
            f"Envie o novo texto/valor diretamente aqui no chat (Ou envie `Nenhum` para desativar este prêmio):",
            parse_mode="Markdown"
        )

    elif data == "admin_reset_confirm":
        texto = (
            "⚠️ *ATENÇÃO: DADOS DA AÇÃO SERÃO ZERADOS!*\n"
            "────────────────────────────────────────\n"
            "Isso vai apagar TODAS as reservas e pagamentos, liberando os números para um novo sorteio.\n\n"
            "Deseja realmente zerar a memória do Bingo?"
        )
        keyboard = [
            [InlineKeyboardButton("🔥 SIM, ZERAR TUDO", callback_data="admin_reset_do")],
            [InlineKeyboardButton("❌ CANCELAR", callback_data="admin_menu")]
        ]
        await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_reset_do":
        reset_memoria_bingo()
        await query.message.edit_text("🧹 *MEMÓRIA ZERADA COM SUCESSO!*\nTodos os números foram liberados para um novo sorteio.", parse_mode="Markdown")

    elif data.startswith("admin_app_"):
        _, _, num, user_id = data.split("_")
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE bilhetes SET status = 'PAGO' WHERE numero = ?", (int(num),))
        conn.commit()
        conn.close()

        num_fmt = formatar_numero(int(num), cfg['qtd_numeros'])
        await query.message.edit_text(f"✅ Bilhete *Nº {num_fmt}* APROVADO!", parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=int(user_id), text=f"🎉 Seu pagamento do *Nº {num_fmt}* foi aprovado! Seu bilhete está confirmado.", parse_mode="Markdown")
        except Exception:
            pass

    elif data.startswith("admin_rej_"):
        _, _, num, user_id = data.split("_")
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE bilhetes SET status = 'LIVRE', user_id = NULL, username = NULL WHERE numero = ?", (int(num),))
        conn.commit()
        conn.close()

        num_fmt = formatar_numero(int(num), cfg['qtd_numeros'])
        await query.message.edit_text(f"❌ Bilhete *Nº {num_fmt}* liberado.", parse_mode="Markdown")

    elif data == "admin_ganhador_prompt":
        texto = "🏆 *QUAL PRÊMIO VOCÊ DESEJA SORTEAR/MARCAR?*"
        keyboard = [
            [InlineKeyboardButton("🥇 1º Prêmio", callback_data="win_prize_1")],
        ]
        if cfg['premio2']:
            keyboard.append([InlineKeyboardButton("🥈 2º Prêmio", callback_data="win_prize_2")])
        if cfg['premio3']:
            keyboard.append([InlineKeyboardButton("🥉 3º Prêmio", callback_data="win_prize_3")])

        keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="admin_menu")])
        await query.message.edit_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("win_prize_"):
        prize_num = data.split("_")[2]
        context.user_data["selecting_winner_prize"] = prize_num

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT numero, username FROM bilhetes WHERE status = 'PAGO' ORDER BY numero ASC")
        pagos = c.fetchall()
        conn.close()

        if not pagos:
            await query.message.edit_text("⚠️ Não há bilhetes pagos para realizar o sorteio.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_menu")]]))
            return

        if cfg['qtd_numeros'] <= 100:
            keyboard = []
            row = []
            for num, uname in pagos:
                num_fmt = formatar_numero(num, cfg['qtd_numeros'])
                row.append(InlineKeyboardButton(f"🏆 {num_fmt}", callback_data=f"admin_win_{num}"))
                if len(row) == 4:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="admin_menu")])
            await query.message.edit_text(f"🏆 *SELECIONE O NÚMERO GANHADOR DO {prize_num}º PRÊMIO:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            context.user_data["editing_field"] = f"winner_{prize_num}"
            await query.message.edit_text(
                f"🏆 *DIGITE O NÚMERO GANHADOR DO {prize_num}º PRÊMIO*\n"
                f"────────────────────────────────────────\n"
                f"Digite o número sorteado diretamente aqui no chat:",
                parse_mode="Markdown"
            )

    elif data.startswith("admin_win_"):
        num = int(data.split("_")[2])
        prize_num = context.user_data.get("selecting_winner_prize", "1")

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, username FROM bilhetes WHERE numero = ?", (num,))
        res = c.fetchone()
        conn.close()

        if res:
            u_id, u_name = res
            nome_premio = cfg['premio'] if prize_num == "1" else (cfg['premio2'] if prize_num == "2" else cfg['premio3'])
            num_fmt = formatar_numero(num, cfg['qtd_numeros'])

            texto = (
                f"🎉 *GANHADOR REGISTRADO ({prize_num}º PRÊMIO)!*\n"
                f"────────────────────────────────────────\n"
                f"🎟️ Número: *Nº {num_fmt}*\n"
                f"👤 Cliente: @{u_name}\n"
                f"🎁 Prêmio: *{nome_premio}*"
            )
            await query.message.edit_text(texto, parse_mode="Markdown")
            try:
                await context.bot.send_message(
                    chat_id=u_id,
                    text=f"🥳 *PARABÉNS!* Seu bilhete *Nº {num_fmt}* foi o GANHADOR do *{prize_num}º PRÊMIO*: {nome_premio}!",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

# ==============================================================================
# INICIALIZAÇÃO DO BOT
# ==============================================================================
def render_termux_dashboard():
    os.system("clear" if os.name == "posix" else "cls")
    print("==================================================")
    print("   BOT BINGO BENEFICENTE - PAINEL DO SERVIDOR     ")
    print("==================================================")
    print(" Status do Bot: 🟢 ONLINE")
    print(" Conexão Telegram: OK")
    print(" Banco de Dados: OK (bingo_beneficente.db)")
    print(" Módulo de IA: Gemini 2.5-Flash (Ativo via HTTP)")
    print("==================================================")

def main():
    init_db()
    render_termux_dashboard()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))

    app.add_handler(CallbackQueryHandler(ver_numeros, pattern="^ver_numeros$"))
    app.add_handler(CallbackQueryHandler(meus_bilhetes, pattern="^meus_bilhetes$"))
    app.add_handler(CallbackQueryHandler(start, pattern="^menu_principal$"))
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^(admin_|cfg_|set_qtd_|win_prize_)"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem_texto))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, receber_midia_comprovante))

    app.run_polling()

if __name__ == "__main__":
    main()

