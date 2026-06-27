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
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)


BOT_TOKEN = os.getenv("NL_TG_KEY", "").strip()
ADMIN_CHAT_ID_RAW = os.getenv("NL_ADMIN_CHAT_ID", "").strip()
DB_PATH = os.getenv("NL_DB_PATH", "notlegal_bot.db").strip() or "notlegal_bot.db"
FORM_URL = os.getenv("NL_FORM_URL", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("NL_TG_KEY не задан в переменных окружения")

if BOT_TOKEN.startswith("bot"):
    raise RuntimeError("NL_TG_KEY нужно вставлять без префикса bot")

if "api.telegram.org" in BOT_TOKEN:
    raise RuntimeError("В NL_TG_KEY вставлена ссылка, а нужен только токен")

if "/" in BOT_TOKEN:
    raise RuntimeError("В NL_TG_KEY есть лишний символ /")

if ":" not in BOT_TOKEN:
    raise RuntimeError("NL_TG_KEY выглядит неверно")

if not ADMIN_CHAT_ID_RAW:
    raise RuntimeError("NL_ADMIN_CHAT_ID не задан в переменных окружения")

try:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW)
except ValueError:
    raise RuntimeError("NL_ADMIN_CHAT_ID должен быть числом")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("notlegal-bot")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Подать заявку")],
            [KeyboardButton(text="Статус заявки"), KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие или напиши сообщение",
    )


def apply_keyboard() -> InlineKeyboardMarkup | None:
    if not FORM_URL:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть форму заявки", url=FORM_URL)]
        ]
    )


async def set_bot_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать работу с ботом"),
        BotCommand(command="apply", description="Подать заявку в Лигу"),
        BotCommand(command="status", description="Узнать статус заявки"),
        BotCommand(command="help", description="Помощь и контакты"),
    ])


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
            utc_now_iso(),
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


def make_user_info_text(message: Message) -> str:
    user = message.from_user

    full_name = html.escape(user.full_name or "Без имени")
    username = f"@{html.escape(user.username)}" if user.username else "без username"

    return (
        "<b>Новое сообщение в поддержку NotLegal RP</b>\n\n"
        f"<b>Имя:</b> {full_name}\n"
        f"<b>ID:</b> <code>{user.id}</code>\n"
        f"<b>Username:</b> {username}\n"
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


@dp.message(F.chat.id == ADMIN_CHAT_ID, F.reply_to_message)
async def handle_admin_reply(message: Message):
    reply_to_message_id = message.reply_to_message.message_id
    target_user_id = await get_user_by_admin_message(reply_to_message_id)

    if not target_user_id:
        await message.answer(
            "Не нашёл пользователя для этого сообщения\n\n"
            "Возможно, это старое сообщение или ответ был не на сообщение бота"
        )
        return

    try:
        if message.text:
            await bot.send_message(
                chat_id=target_user_id,
                text=(
                    "<b>Ответ поддержки NotLegal RP:</b>\n\n"
                    f"{html.escape(message.text)}"
                ),
            )
        else:
            await bot.send_message(
                chat_id=target_user_id,
                text="<b>Ответ поддержки NotLegal RP:</b>",
            )

            await bot.copy_message(
                chat_id=target_user_id,
                from_chat_id=ADMIN_CHAT_ID,
                message_id=message.message_id,
            )

        await mark_answered(reply_to_message_id)
        await message.answer("Ответ отправлен пользователю")

    except Exception as e:
        logger.exception("Ошибка при отправке ответа пользователю: %s", e)
        await message.answer("Не удалось отправить ответ пользователю. Смотри логи")


@dp.message(CommandStart())
async def cmd_start(message: Message):
    if message.chat.id == ADMIN_CHAT_ID:
        await message.answer(
            "Бот NotLegal RP работает\n\n"
            "Когда пользователь напишет боту, сообщение придёт сюда"
        )
        return

    await message.answer(
        "<b>NotLegal RP</b>\n\n"
        "Привет. Здесь можно подать заявку, узнать статус или написать команде проекта\n\n"
        "Команды:\n"
        "/apply - подать заявку\n"
        "/status - узнать статус заявки\n"
        "/help - помощь и контакты",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("apply"))
@dp.message(F.text == "Подать заявку")
async def cmd_apply(message: Message):
    if FORM_URL:
        await message.answer(
            "<b>Заявка в NotLegal RP</b>\n\n"
            "Заполни форму по кнопке ниже. После отправки заявка попадёт команде проекта",
            reply_markup=apply_keyboard(),
        )
        return

    await message.answer(
        "<b>Заявка в NotLegal RP</b>\n\n"
        "Форма заявки пока не подключена к боту\n\n"
        "Напиши сюда, куда хочешь подать заявку: медиа, стример, PR, SMM, администрация, "
        "ивентер, дизайнер, лидер или другое направление\n\n"
        "Команда проекта получит сообщение и ответит тебе здесь"
    )


@dp.message(Command("status"))
@dp.message(F.text == "Статус заявки")
async def cmd_status(message: Message):
    await message.answer(
        "<b>Статус заявки</b>\n\n"
        "Если ты уже отправил заявку, напиши сюда:\n"
        "1. свой Telegram или Discord\n"
        "2. никнейм\n"
        "3. направление заявки\n\n"
        "Команда проверит заявку и ответит тебе в этом чате"
    )


@dp.message(Command("help"))
@dp.message(F.text == "Помощь")
async def cmd_help(message: Message):
    await message.answer(
        "<b>Помощь NotLegal RP</b>\n\n"
        "/start - главное меню\n"
        "/apply - подать заявку\n"
        "/status - узнать статус заявки\n"
        "/help - помощь\n\n"
        "Также можешь просто написать сообщение в этот чат. Команда проекта получит его и ответит здесь"
    )


@dp.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(
        "<b>Информация о чате</b>\n\n"
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"user_id: <code>{message.from_user.id}</code>"
    )


@dp.message(Command("health"))
async def cmd_health(message: Message):
    if message.chat.id != ADMIN_CHAT_ID:
        return

    me = await bot.get_me()

    await message.answer(
        "<b>Health check OK</b>\n\n"
        f"Бот: @{me.username}\n"
        f"Bot ID: <code>{me.id}</code>\n"
        f"Admin chat ID: <code>{ADMIN_CHAT_ID}</code>\n"
        f"DB: <code>{html.escape(DB_PATH)}</code>\n"
        f"Form URL: <code>{html.escape(FORM_URL or 'not set')}</code>"
    )


@dp.message(F.text.startswith("/"))
async def handle_unknown_command(message: Message):
    await message.answer(
        "Такой команды нет\n\n"
        "Доступные команды:\n"
        "/start\n"
        "/apply\n"
        "/status\n"
        "/help"
    )


@dp.message()
async def handle_user_message(message: Message):
    if message.chat.id == ADMIN_CHAT_ID:
        return

    try:
        await notify_admin_about_user_message(message)

        await message.answer(
            "Сообщение отправлено команде NotLegal RP\n\n"
            "Мы посмотрим и ответим тебе здесь"
        )

    except Exception as e:
        logger.exception("Ошибка обработки сообщения пользователя: %s", e)

        await message.answer(
            "Не удалось отправить сообщение команде\n"
            "Попробуй ещё раз чуть позже"
        )


async def main():
    try:
        await init_db()
        await set_bot_commands()

        me = await bot.get_me()
        logger.info("Бот запущен: @%s / id=%s", me.username, me.id)

        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
