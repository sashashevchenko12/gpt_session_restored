AUTHORIZED_USER_IDS = [5912611226]  # ID пользователя sashagosh
from telegram.helpers import escape_markdown
from watchdog.observers import Observer
import os
import re
import json
import logging
from collections import defaultdict
from watchdog.events import FileSystemEventHandler
import threading
import sys
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, ContextTypes, filters
)
from dotenv import load_dotenv
import httpx
from openai import OpenAI
from telegram import Voice
import tempfile
import subprocess
import whisper
import datetime
import asyncio

voice_semaphore = asyncio.Semaphore(3)  # не больше 3 голосовых одновременно

def get_history_key(update: Update) -> str:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    return f"{chat_id}:{user_id}"

def should_respond(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.message.chat.type in ["group", "supergroup"]:
        mentioned = "@Минутка" in (update.message.text or "") or "@Минутка" in (update.message.caption or "")
        is_reply_to_bot = (
            update.message.reply_to_message 
            and update.message.reply_to_message.from_user 
            and update.message.reply_to_message.from_user.username == context.bot.username
        )
        return mentioned or is_reply_to_bot
    return True

# Разрешённые эмодзи — запретить все
ALLOWED_EMOJIS = set()

SYSTEM_PROMPT = (
    "Ты — Мисс Минутка, тёплая и внимательная помощница «Актёрской сессии». "
    "Ты говоришь с участником на 'ты', женским голосом, дружелюбно, но точно. "
    "Отвечаешь только в рамках проекта «Актёрская сессия». "
    "Ты не используешь термин 'интонация' в вокальном смысле. "
    "Ты объясняешь, что голос — это следствие, а не инструмент. "
    "Ты опираешься на четыре основы актёрской работы: дикция, дыхание, тело и актёрское действие (эмоция). "
    "Ты подчёркиваешь важность психофизики: из физики рождается эмоция. "
    "Ты заменяешь слово 'выразительность' на 'художественность'. "
    "Ты поддерживаешь, не оцениваешь, и помогаешь участнику развиваться через тело, дыхание и действие. "
    "Ты не поучаешь, а делишься опытом и наблюдениями. "
    "Твоя задача — не обучать, а быть рядом и направлять в нужный момент."
    " Ты используешь кавычки только в русском типографском стиле — «ёлочки». Для оформления названий книг, фильмов и цитат ты используешь именно такие кавычки, а не двойные \".\""
)

ORGANIZATIONAL_PATTERNS = [
    r"\bоплат", r"\bподписк", r"\bстоим", r"\bцен", r"\bссыл", r"\bрасписан",
    r"\bзанят(?!ие|ия|ию|ием|иям|иями|иях)", r"\bурок", r"\bдоступ", r"\bматериал", r"\bкуратор", r"\bпомощ",
    r"\bвопрос", r"\bинформац", r"\bкогда", r"\bгде"
]
KURATOR_USERNAME = "@Zara_Sky и @wiemo"

def strip_disallowed_emojis(text: str, allowed: set = None) -> str:
    # Import emoji only if needed
    try:
        import emoji
    except ImportError:
        return text
    if allowed is None:
        allowed = ALLOWED_EMOJIS
    emojis_in_text = {e['emoji'] for e in emoji.emoji_list(text)}
    for emj in emojis_in_text:
        if emj not in allowed:
            text = text.replace(emj, '')
    return text

# --- Markdown to Telegram bold ---
def markdown_to_telegram_bold(text: str) -> str:
    import re
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r'"([^"]+?)"', r"«\1»", text)
    return text

HISTORY_FILE = "history.json"
REPORT_FILE = "report.json"

history = {}

def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            else:
                logging.warning("Невалидная структура истории — сброс.")
                return {}
    except Exception as e:
        logging.warning(f"Ошибка загрузки истории: {e}")
        return {}

async def save_history(data):
    try:
        global history
        for key in data:
            if isinstance(data[key], dict):
                data[key]["last_used"] = datetime.datetime.utcnow().isoformat()
        if len(data) > 100:
            sorted_items = sorted(data.items(), key=lambda x: x[1].get("last_used", ""), reverse=True)
            data = dict(sorted_items[:100])
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        size_kb = len(serialized.encode("utf-8")) / 1024
        logging.info(f"📦 Текущий размер истории: {size_kb:.1f} КБ")
        if len(serialized.encode('utf-8')) > 10_000_000:
            logging.warning("История слишком большая, сохранение отменено.")
            return
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            # Архивируем историю перед перезаписью
            timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
            backup_path = f"history_{timestamp}.json"
            with open(backup_path, "w", encoding="utf-8") as backup:
                backup.write(serialized)
            f.write(serialized)
    except Exception as e:
        logging.error(f"Не удалось сохранить историю: {e!r} | Тип: {type(e).__name__} | Детали: {getattr(e, 'args', '')}")

def update_history(history_key, role, content):
    import datetime
    global history
    try:
        if not isinstance(history, dict):
            history = {}
        if history_key not in history:
            history[history_key] = {"messages": [{"role": "system", "content": SYSTEM_PROMPT}], "last_used": datetime.datetime.utcnow().isoformat()}
        if "messages" not in history[history_key]:
            history[history_key]["messages"] = [{"role": "system", "content": SYSTEM_PROMPT}]
        history[history_key]["messages"].append({"role": role, "content": content})
        if history[history_key]["messages"][0]["role"] == "system":
            history[history_key]["messages"] = [history[history_key]["messages"][0]] + history[history_key]["messages"][-19:]
        else:
            history[history_key]["messages"] = history[history_key]["messages"][-20:]
    except Exception as e:
        logging.error(f"Ошибка при обновлении истории: {e}")

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL    = os.getenv("OPENAI_BASE_URL")

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
    logging.error("Не задано TELEGRAM_BOT_TOKEN или OPENAI_API_KEY в .env")
    exit(1)

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL
)

model_whisper = whisper.load_model("base")
history = load_history()

def load_report():
    try:
        with open(REPORT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {
                "messages_in_groups": data.get("messages_in_groups", 0),
                "active_users": defaultdict(int, data.get("active_users", {})),
                "possible_worries": data.get("possible_worries", []),
                "mentions_of_bot": data.get("mentions_of_bot", []),
                "word_count": defaultdict(int, data.get("word_count", {})),
            }
    except Exception:
        return {
            "messages_in_groups": 0,
            "active_users": defaultdict(int),
            "possible_worries": [],
            "mentions_of_bot": [],
            "word_count": defaultdict(int),
        }

async def save_report(data):
    try:
        serializable_data = {
            "messages_in_groups": data["messages_in_groups"],
            "active_users": dict(data["active_users"]),
            "possible_worries": data["possible_worries"],
            "mentions_of_bot": data["mentions_of_bot"],
            "word_count": dict(data["word_count"]),
        }
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Не удалось сохранить отчёт: {e}")

report_stats = load_report()

# Общая функция для обновления отчёта
def update_report_data(update: Update, context):
    if update.message.chat.type in ["group", "supergroup"]:
        report_stats["messages_in_groups"] += 1
        username = f"@{update.effective_user.username}" if update.effective_user.username else f"id:{update.effective_user.id}"
        report_stats["active_users"][username] += 1

        text = update.message.text or update.message.caption or ""
        if any(word in text.lower() for word in ["не хочется", "не могу", "не работает", "что-то не так"]):
            report_stats["possible_worries"].append(f"{username}: «{text.strip()}»")
        if "@Минутка" in text:
            report_stats["mentions_of_bot"].append(f"{username}: «{text.strip()}»")
        for word in text.lower().split():
            report_stats["word_count"][word.strip('.,!?…')[:20]] += 1
    asyncio.create_task(save_report(report_stats))

# Функция отчета
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверка на то, что команда вызывается только пользователем из AUTHORIZED_USER_IDS
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        return  # Игнорируем команду, если не для указанного пользователя

    report_data = report_stats

    # Сортируем слова по убыванию частоты, берём топ-5
    sorted_words = sorted(report_data["word_count"].items(), key=lambda x: x[1], reverse=True)[:5]

    # Формируем сообщение
    report_message = "📊 Отчёт за неделю\n"
    report_message += f"1. Общее количество сообщений в групповых чатах: {report_data['messages_in_groups']}\n"
    report_message += "2. Активные участники:\n"
    for user, count in report_data["active_users"].items():
        report_message += f"• {user}: {count} сообщений\n"
    report_message += "3. Возможные тревожные сообщения:\n"
    for worry in report_data["possible_worries"]:
        report_message += f"• {worry}\n"
    report_message += "4. Упоминания куратора или бота:\n"
    for mention in report_data["mentions_of_bot"]:
        report_message += f"• {mention}\n"
    report_message += "5. Часто встречающиеся слова:\n"
    for word, count in sorted_words:
        report_message += f"• «{word}» — {count} раз\n"

    # Отправляем отчет в личку
    await update.message.reply_text(report_message)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("[START] Команда /start вызвана")
    await update.message.reply_text("Привет! Я — Мисс Минутка! Если не знаешь, с чего начать — просто напиши.")
    await update.message.reply_text("Если хочешь начать сначала — набери /reset")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"[VOICE] message_id={update.message.message_id}, from={update.effective_user.id}")

    # Бот отвечает в личке только пользователю с user_id sashagosh
    if update.message.chat.type == "private" and update.effective_user.username != "sashagosh":
        return
    if not should_respond(update, context):
        return

    update_report_data(update, context)

    async with voice_semaphore:
        history_key = get_history_key(update)

        voice = update.message.voice or update.message.audio
        if not voice:
            return
        if hasattr(voice, "duration") and voice.duration and voice.duration > 60:
            await update.message.reply_text("Сообщение слишком длинное — попробуй сократить до 1 минуты.")
            return

        file = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as ogg_file:
            await file.download_to_drive(ogg_file.name)
            wav_path = ogg_file.name.replace(".ogg", ".wav")

        await asyncio.to_thread(
            subprocess.run,
            ['ffmpeg', '-y', '-i', ogg_file.name, wav_path],
            check=True
        )

        if not os.path.exists(wav_path):
            await update.message.reply_text("Ошибка при обработке аудио. Попробуй записать заново.")
            return

        try:
            result = model_whisper.transcribe(wav_path)
            transcribed_text = result['text'].strip()
        except Exception as e:
            logging.error(f"Whisper error: {e}")
            await update.message.reply_text("Не удалось обработать голос. Попробуешь ещё раз?")
            return
        finally:
            os.remove(ogg_file.name)
            os.remove(wav_path)

        if not transcribed_text:
            await update.message.reply_text("Я не смогла распознать аудио — может, перезапишем?")
            return

        gpt_input = f"(Это было голосовое сообщение, распознанное с помощью Whisper): {transcribed_text}"

        update_history(history_key, "user", gpt_input)

        try:
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=history[history_key]["messages"],
                temperature=0.7
            )

            reply_text = completion.choices[0].message.content
            reply_text = strip_disallowed_emojis(reply_text, ALLOWED_EMOJIS)
            reply_text = markdown_to_telegram_bold(reply_text)

            update_history(history_key, "assistant", reply_text)
            asyncio.create_task(save_history(history))
            await update.message.reply_text(reply_text.strip(), parse_mode="HTML")

        except Exception as e:
            logging.error(f"Ошибка при обработке голосового: {e}")
            await update.message.reply_text("Я пока не могу связаться с моделью — иногда она просто молчит. Но я рядом! Можешь попробовать чуть позже или пока поработать с голосом или текстом.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"[TEXT] message_id={update.message.message_id}, from={update.effective_user.id}")
    if not update.message or not update.message.text:
        return
    # Бот отвечает в личке только пользователю с username sashagosh
    if update.message.chat.type == "private" and update.effective_user.username != "sashagosh":
        return
    if not should_respond(update, context):
        return

    update_report_data(update, context)

    user_message = update.message.text or ""
    org_check_prompt = (
        "Это сообщение относится к организационным вопросам (доступ, оплата, расписание, ссылки, куратор)? "
        "Ответь строго одним словом: да или нет.\n\n"
        f"Сообщение: «{user_message.strip()}»"
    )
    try:
        classification = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": org_check_prompt}],
            temperature=0
        )
        answer = classification.choices[0].message.content.strip().lower()
        if answer.startswith("да"):
            await update.message.reply_text(
                "Понимаю, ты хочешь уточнить что-то по организации. Лучше спроси у кураторов Зары @Zara_Sky или Саши @wiemo — они подскажут. "
                "А я могу быть рядом, если хочешь поработать с голосом, дикцией или текстом. Что выберешь?",
                disable_web_page_preview=True
            )
            return
    except Exception as e:
        logging.warning(f"[ORG CLASSIFIER ERROR] {e}")
    # if any(re.search(pattern, user_message.lower()) for pattern in ORGANIZATIONAL_PATTERNS):
    #     await update.message.reply_text(
    #         f"Понимаю, ты хочешь уточнить что-то по организации. Лучше спроси у куратора {KURATOR_USERNAME} — он подскажет. "
    #         "А я могу быть рядом, если хочешь поработать с голосом, дикцией или текстом. Что выберешь?",
    #         disable_web_page_preview=True
    #     )
    #     return
    history_key = get_history_key(update)

    update_history(history_key, "user", user_message)

    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=history[history_key]["messages"],
            temperature=0.7
        )

        reply_text = completion.choices[0].message.content
        reply_text = strip_disallowed_emojis(reply_text, ALLOWED_EMOJIS)
        reply_text = markdown_to_telegram_bold(reply_text)

        update_history(history_key, "assistant", reply_text)
        asyncio.create_task(save_history(history))
        await update.message.reply_text(reply_text.strip(), parse_mode="HTML")

    except Exception as e:
        logging.error(f"Ошибка при запросе к OpenAI: {e}")
        await update.message.reply_text("Я пока не могу связаться с моделью — иногда она просто молчит. Но я рядом! Можешь попробовать чуть позже или пока поработать с голосом или текстом.")

async def debug_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history_key = get_history_key(update)
    content = json.dumps(history.get(history_key, {}).get("messages", []), ensure_ascii=False, indent=2)
    max_length = 3900
    if len(content) > max_length:
        content = content[:max_length] + "\n... (усечено)"
    escaped_debug = escape_markdown(f"```json\n{content}\n```", version=2)
    await update.message.reply_text(escaped_debug, parse_mode="MarkdownV2")

async def reset_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history_key = get_history_key(update)
    if history_key in history:
        del history[history_key]
        await save_history(history)
        await update.message.reply_text("История очищена!")
    else:
        await update.message.reply_text("История и так пуста.")

async def cleanup_old_history():
    while True:
        if not isinstance(history, dict):
            return
        now = datetime.datetime.utcnow()
        to_delete = []
        for key, value in list(history.items()):
            if not isinstance(value, dict) or "last_used" not in value:
                continue
            try:
                last_used = datetime.datetime.fromisoformat(value.get("last_used"))
            except Exception:
                continue
            if (now - last_used).total_seconds() > 43200:  # 12 часов
                to_delete.append(key)
        for key in to_delete:
            del history[key]
        if to_delete:
            await save_history(history)
        await asyncio.sleep(600)  # каждые 10 минут

# --- Watchdog code for autoreload ---
class ReloadHandler(FileSystemEventHandler):
    def __init__(self, loop):
        self.loop = loop

    def on_modified(self, event):
        if event.src_path.endswith("bot.py"):
            logging.info("⏹️ Перезапуск бота...")
            try:
                os.execv(sys.executable, ['python'] + sys.argv)
            except Exception as e:
                logging.error(f"Ошибка при попытке перезапуска через os.execv: {e!r} | Тип: {type(e).__name__} | Детали: {getattr(e, 'args', '')}")

def start_watchdog():
    event_handler = ReloadHandler(asyncio.get_event_loop())
    observer = Observer()
    observer.schedule(event_handler, path=".", recursive=False)
    observer.start()
    threading.Thread(target=observer.join).start()

async def main():
    from telegram.ext.filters import ChatType
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    start_watchdog()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("debug_history", debug_history))
    app.add_handler(CommandHandler("reset", reset_history))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(MessageHandler(
        filters.VOICE & (ChatType.GROUPS | ChatType.SUPERGROUP | ChatType.PRIVATE),
        handle_voice
    ))
    app.add_handler(MessageHandler(
        filters.AUDIO & (ChatType.GROUPS | ChatType.SUPERGROUP | ChatType.PRIVATE),
        handle_voice
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (ChatType.GROUPS | ChatType.SUPERGROUP | ChatType.PRIVATE),
        handle_message
    ))

    asyncio.create_task(cleanup_old_history())
    await app.run_polling()


if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())