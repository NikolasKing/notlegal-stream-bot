import asyncio
import html
import logging
import os
from datetime import datetime, timezone

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message


# =========================
# ENV
# =========================

BOT_TOKEN = os.getenv("NL_TG_KEY", "").strip()
ADMIN_CHAT_ID_RAW = os.getenv("NL_ADMIN_CHAT_ID", "").strip()
DB_PATH = os.getenv("NL_DB_PATH", "notlegal_bot.db").strip() or "notlegal_bot.db"

if not BOT_TOKEN:
    raise RuntimeError(
        "NL_TG_KEY не задан в переменных окружения. "
        "На Bothost добавь переменную NL_TG_KEY и вставь туда только токен от BotFather."
    )

if BOT_TOKEN.startswith("bot"):
    raise RuntimeError(
        "NL_TG_KEY нужно вставлять без префикса 'bot'. "
        "Нужен только сам токен от BotFather."
    )

if "api.telegram.org" in BOT_TOKEN:
    raise RuntimeError(
        "В NL_TG_KEY вставлена ссылка, а нужен только токен от BotFather."
    )

if "/" in BOT_TOKEN:
    raise RuntimeError(
        "В NL_TG_KEY есть лишний символ '/'. "
        "Вставь только токен, без ссылки и без метода /getMe."
    )

if ":" not in BOT_TOKEN:
    raise RuntimeError(
        "NL_TG_KEY выглядит неверно: в токене Telegram должен быть символ ':'."
    )

if not ADMIN_CHAT_ID_RAW:
    raise RuntimeError(
        "NL_ADMIN_CHAT_ID не задан в переменных окружения. "
        "Вставь туда свой Telegram ID или ID админ-группы."
    )

try:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW)
except ValueError:
    raise RuntimeError(
        "NL_ADMIN_CHAT_ID должен быть числом. "
        "Для лички это обычный Telegram ID, для группы обычно начинается с -100."
    )


# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("notlegal-bot")


# =========================
# BOT
# =========================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()


# =========================
# DATABASE
# =========================

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_links (
                admin_chat_id INTEGER NOT NULL,
                admin_message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                PRIMARY KEY (admin_chat_id, admin_message_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT
            )
        """)

        await db.commit()


async def save_user(user_id: int, username: str | None, full_name: str):
    now = utc_now_iso()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                last_seen_at = excluded.last_seen_at
        """, (user_id, username, full_name, now, now))

        await db.commit()


async def save_message_link(admin_message_id: int, user_id: int, user_message_id: int | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO message_links (
                admin_chat_id,
                admin_message_id,
                user_id,
                user_message_id,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, 'open', ?)
        """, (
            ADMIN_CHAT_ID,
            admin_message_id,
            user_id,
            user_message_id,
            utc_now_iso()
        ))

        await db.commit()


async def get_user_by_admin_message(admin_message_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT user_id
            FROM message_links
            WHERE admin_chat_id = ? AND admin_message_id = ?
            LIMIT 1
        """, (ADMIN_CHAT_ID, admin_message_id))

        row = await cursor.fetchone()
        return row[0] if row else None


async def mark_answered(admin_message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE message_links
            SET status = 'answered'
            WHERE admin_chat_id = ? AND admin_message_id = ?
        """, (ADMIN_CHAT_ID, admin_message_id))

        await db.commit()


# =========================
# HELPERS
# =========================

def make_user_info_text(message: Message) -> str:
    user = message.from_user

    full_name = html.escape(user.full_name or "Без имени")
    username = f"@{html.escape(user.username)}" if user.username else "без username"

    return (
        "✉️ <b>Новое сообщение в поддержку Not Legal RP</b>\n\n"
        f"👤 <b>Имя:</b> {full_name}\n"
        f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
        f"🔗 <b>Username:</b> {username}\n"
        "────────────────────\n"
        "Ответь на это сообщение через функцию <b>Ответить</b>, "
        "и бот отправит ответ пользователю"
    )


async def notify_admin_about_user_message(message: Message):
    user = message.from_user

    await save_user(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name or "Без имени",
    )

    header = await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=make_user_info_text(message),
    )

    await save_message_link(
        admin_message_id=header.message_id,
        user_id=user.id,
        user_message_id=message.message_id,
    )

    try:
        copied = await bot.copy_message(
            chat_id=ADMIN_CHAT_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            reply_to_message_id=header.message_id,
        )

        await save_message_link(
            admin_message_id=copied.message_id,
            user_id=user.id,
            user_message_id=message.message_id,
        )

    except Exception as e:
        logger.exception("Не удалось скопировать сообщение пользователя админу: %s", e)


# =========================
# HANDLERS
# =========================

@dp.message(F.chat.id == ADMIN_CHAT_ID, F.reply_to_message)
async def handle_admin_reply(message: Message):
    reply_to_message_id = message.reply_to_message.message_id

    target_user_id = await get_user_by_admin_message(reply_to_message_id)

    if not target_user_id:
        await message.answer(
            "⚠️ Не нашёл пользователя для этого сообщения\n\n"
            "Возможно, это старое сообщение или ответ был не на сообщение бота"
        )
        return

    try:
        if message.text:
            await bot.send_message(
                chat_id=target_user_id,
                text=(
                    "💬 <b>Ответ поддержки Not Legal RP:</b>\n\n"
                    f"{html.escape(message.text)}"
                )
            )
        else:
            await bot.send_message(
                chat_id=target_user_id,
                text="💬 <b>Ответ поддержки Not Legal RP:</b>"
            )

            await bot.copy_message(
                chat_id=target_user_id,
                from_chat_id=ADMIN_CHAT_ID,
                message_id=message.message_id,
            )

        await mark_answered(reply_to_message_id)
        await message.answer("✅ Ответ отправлен пользователю")

    except Exception as e:
        logger.exception("Ошибка при отправке ответа пользователю: %s", e)
        await message.answer("❌ Не удалось отправить ответ пользователю. Смотри логи")


@dp.message(CommandStart())
async def cmd_start(message: Message):
    if message.chat.id == ADMIN_CHAT_ID:
        await message.answer(
            "✅ Бот Not Legal RP работает\n\n"
            "Когда пользователь напишет боту, заявка придёт сюда"
        )
        return

    await message.answer(
        "👋 Привет! Это бот поддержки Not Legal RP\n\n"
        "Напиши сюда свой вопрос, заявку или предложение — команда проекта получит сообщение и ответит тебе"
    )


@dp.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(
        "🆔 <b>Информация о чате</b>\n\n"
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"user_id: <code>{message.from_user.id}</code>"
    )


@dp.message(Command("health"))
async def cmd_health(message: Message):
    if message.chat.id != ADMIN_CHAT_ID:
        return

    me = await bot.get_me()

    await message.answer(
        "✅ <b>Health check OK</b>\n\n"
        f"🤖 Бот: @{me.username}\n"
        f"🆔 Bot ID: <code>{me.id}</code>\n"
        f"💬 Admin chat ID: <code>{ADMIN_CHAT_ID}</code>\n"
        f"🗄 DB: <code>{html.escape(DB_PATH)}</code>"
    )


@dp.message()
async def handle_user_message(message: Message):
    if message.chat.id == ADMIN_CHAT_ID:
        return

    try:
        await notify_admin_about_user_message(message)

        await message.answer(
            "✅ Сообщение отправлено команде Not Legal RP\n\n"
            "Мы посмотрим и ответим тебе здесь"
        )

    except Exception as e:
        logger.exception("Ошибка обработки сообщения пользователя: %s", e)

        await message.answer(
            "❌ Не удалось отправить сообщение команде\n"
            "Попробуй ещё раз чуть позже"
        )


# =========================
# STARTUP
# =========================

async def main():
    try:
        await init_db()

        me = await bot.get_me()
        logger.info("Бот запущен: @%s / id=%s", me.username, me.id)

        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
