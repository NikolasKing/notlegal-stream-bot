import asyncio
import html
import logging
import os
from datetime import datetime, timedelta, timezone

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
MEDIA_FORM_URL = os.getenv("NL_MEDIA_FORM_URL", "").strip() or FORM_URL
TEAM_FORM_URL = os.getenv("NL_TEAM_FORM_URL", "").strip() or FORM_URL

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


TEMPLATE_COMMANDS = {
    "call",
    "missing",
    "accept_stream",
    "accept_media",
    "accept_team",
    "reject",
    "remind",
    "close_no_answer",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Подать заявку")],
            [KeyboardButton(text="Медиа / стример"), KeyboardButton(text="Команда проекта")],
            [KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие или напиши сообщение",
    )


def url_keyboard(text: str, url: str) -> InlineKeyboardMarkup | None:
    if not url:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, url=url)]
        ]
    )


def get_command_name(message: Message) -> str:
    if not message.text:
        return ""

    raw_command = message.text.split(maxsplit=1)[0].lower()
    raw_command = raw_command.lstrip("/")
    return raw_command.split("@", 1)[0]


def get_command_args(message: Message) -> str:
    if not message.text:
        return ""

    parts = message.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def set_bot_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню NotLegal RP"),
        BotCommand(command="apply", description="Подать заявку в команду проекта"),
        BotCommand(command="media", description="Заявка для медиа и стримеров"),
        BotCommand(command="team", description="Заявка в команду проекта"),
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


async def get_bot_stats() -> dict:
    day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        users_cursor = await db.execute("SELECT COUNT(*) FROM users")
        users_count = (await users_cursor.fetchone())[0]

        messages_cursor = await db.execute("""
            SELECT COUNT(DISTINCT user_id || ':' || COALESCE(user_message_id, 0))
            FROM message_links
        """)
        messages_count = (await messages_cursor.fetchone())[0]

        recent_cursor = await db.execute("""
            SELECT COUNT(DISTINCT user_id || ':' || COALESCE(user_message_id, 0))
            FROM message_links
            WHERE created_at >= ?
        """, (day_ago,))
        recent_messages_count = (await recent_cursor.fetchone())[0]

        open_cursor = await db.execute("""
            SELECT COUNT(*)
            FROM message_links
            WHERE status = 'open'
        """)
        open_links_count = (await open_cursor.fetchone())[0]

        answered_cursor = await db.execute("""
            SELECT COUNT(*)
            FROM message_links
            WHERE status = 'answered'
        """)
        answered_links_count = (await answered_cursor.fetchone())[0]

    return {
        "users_count": users_count,
        "messages_count": messages_count,
        "recent_messages_count": recent_messages_count,
        "open_links_count": open_links_count,
        "answered_links_count": answered_links_count,
    }


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
        "и бот отправит ответ пользователю\n\n"
        "Шаблоны для ответа: <code>/tpl</code>"
    )


async def notify_admin_about_user_message(message: Message):
    user = message.from_user

    if not user:
        return

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


def build_template_text(command: str, args: str) -> tuple[str | None, str | None]:
    safe_args = html.escape(args)

    if command == "call":
        if not args:
            return None, "Укажи время после команды, например: <code>/call завтра в 19:00 МСК</code>"

        return (
            "Заявка выглядит интересно, хотим коротко пообщаться голосом\n\n"
            f"Сможешь выйти на созвон {safe_args}? Обсудим опыт, направление и что можно делать в NotLegal RP",
            None,
        )

    if command == "missing":
        return (
            "Посмотрели заявку, но не хватает данных для решения\n\n"
            "Пришли, пожалуйста:\n"
            "1. ссылку на канал / портфолио / прошлый проект\n"
            "2. сколько времени готов уделять NotLegal RP\n"
            "3. какой формат работы тебе интересен\n\n"
            "После этого сможем нормально рассмотреть заявку",
            None,
        )

    if command == "accept_stream":
        return (
            "Мы посмотрели заявку и готовы взять тебя в Стримерскую Лигу NotLegal RP\n\n"
            "Следующий шаг: добавляем тебя в рабочий Discord, выдаём роль и даём первые задачи по контенту\n\n"
            "Для старта нужно:\n"
            "1. ознакомиться с правилами стримерской программы\n"
            "2. согласовать первый стрим или первый контент\n"
            "3. держать связь с куратором",
            None,
        )

    if command == "accept_media":
        return (
            "Заявка подходит, готовы взять тебя в медиа-направление NotLegal RP\n\n"
            "Для старта добавим тебя в рабочий Discord, выдадим роль и дадим первый тестовый план по контенту\n\n"
            "Первый фокус: короткие ролики, клипы, ситуации с сервера и материалы, которые помогут привести новых игроков",
            None,
        )

    if command == "accept_team":
        role = safe_args or "выбранное направление"

        return (
            f"Мы рассмотрели заявку и готовы взять тебя в команду NotLegal RP на направление: {role}\n\n"
            "Следующий шаг: добавляем тебя в рабочий Discord, выдаём роль, знакомим с регламентом и даём первое тестовое задание\n\n"
            "После тестового задания поймём, в какой зоне ты будешь полезнее всего",
            None,
        )

    if command == "reject":
        return (
            "Спасибо за заявку в NotLegal RP\n\n"
            "Сейчас мы не готовы взять тебя в это направление. Причина не в том, что заявка плохая, просто на текущем этапе нам нужен немного другой опыт и формат участия\n\n"
            "Ты можешь следить за новостями проекта, развивать опыт и подать заявку позже повторно",
            None,
        )

    if command == "remind":
        return (
            "Напоминаю по заявке в NotLegal RP\n\n"
            "Мы готовы продолжить рассмотрение, но ждём от тебя ответ / недостающие данные\n\n"
            "Если заявка ещё актуальна, напиши сюда сегодня или завтра",
            None,
        )

    if command == "close_no_answer":
        return (
            "Так как ответа по заявке не было, мы пока закрываем её\n\n"
            "Если желание присоединиться к NotLegal RP останется, можешь подать заявку повторно через форму",
            None,
        )

    return None, "Неизвестный шаблон. Список шаблонов: <code>/tpl</code>"


async def send_template_reply(message: Message, command: str, args: str):
    if not message.reply_to_message:
        await message.answer(
            "Эту команду нужно отправлять ответом на сообщение кандидата\n\n"
            "Нажми на сообщение бота с заявкой → Ответить → напиши команду"
        )
        return

    reply_to_message_id = message.reply_to_message.message_id
    target_user_id = await get_user_by_admin_message(reply_to_message_id)

    if not target_user_id:
        await message.answer(
            "Не нашёл пользователя для этого сообщения\n\n"
            "Возможно, это старое сообщение или ответ был не на сообщение бота"
        )
        return

    template_text, error_text = build_template_text(command, args)

    if error_text:
        await message.answer(error_text)
        return

    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=(
                "<b>Ответ команды NotLegal RP:</b>\n\n"
                f"{template_text}"
            ),
        )

        await mark_answered(reply_to_message_id)
        await message.answer("Шаблон отправлен пользователю")

    except Exception as e:
        logger.exception("Ошибка при отправке шаблона пользователю: %s", e)
        await message.answer("Не удалось отправить шаблон пользователю. Смотри логи")


@dp.message(F.chat.id == ADMIN_CHAT_ID, Command("tpl"))
async def cmd_templates(message: Message):
    await message.answer(
        "<b>Шаблоны ответов NotLegal RP</b>\n\n"
        "Используй команды ответом на сообщение кандидата:\n\n"
        "<code>/call завтра в 19:00 МСК</code> — пригласить на созвон\n"
        "<code>/missing</code> — запросить недостающие данные\n"
        "<code>/accept_stream</code> — принять в Стримерскую Лигу\n"
        "<code>/accept_media</code> — принять в медиа\n"
        "<code>/accept_team PR/SMM</code> — принять в команду на направление\n"
        "<code>/reject</code> — мягкий отказ\n"
        "<code>/remind</code> — напомнить кандидату\n"
        "<code>/close_no_answer</code> — закрыть, если не ответил\n\n"
        "Важно: сначала нажми <b>Ответить</b> на сообщение бота с заявкой, потом отправь команду"
    )


@dp.message(F.chat.id == ADMIN_CHAT_ID, Command("stats"))
async def cmd_stats(message: Message):
    stats = await get_bot_stats()

    await message.answer(
        "<b>Статистика бота NotLegal RP</b>\n\n"
        f"Пользователей в базе: <b>{stats['users_count']}</b>\n"
        f"Сообщений от пользователей: <b>{stats['messages_count']}</b>\n"
        f"Сообщений за 24 часа: <b>{stats['recent_messages_count']}</b>\n"
        f"Открытых связок сообщений: <b>{stats['open_links_count']}</b>\n"
        f"Отвеченных связок сообщений: <b>{stats['answered_links_count']}</b>\n\n"
        "Заявки из Google Form считай по таблице. Эта статистика показывает сообщения, прошедшие через Telegram-бота"
    )


@dp.message(F.chat.id == ADMIN_CHAT_ID, Command(*TEMPLATE_COMMANDS))
async def handle_admin_template_command(message: Message):
    command = get_command_name(message)
    args = get_command_args(message)
    await send_template_reply(message, command, args)


@dp.message(F.chat.id == ADMIN_CHAT_ID, F.reply_to_message)
async def handle_admin_reply(message: Message):
    if message.text and message.text.startswith("/"):
        await message.answer(
            "Команду не отправил пользователю\n\n"
            "Если хотел использовать шаблон, проверь список: <code>/tpl</code>\n"
            "Если хотел отправить обычный текст, убери символ / в начале"
        )
        return

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
            "Когда пользователь напишет боту, сообщение придёт сюда\n\n"
            "Админ-команды:\n"
            "/tpl — шаблоны ответов\n"
            "/stats — статистика сообщений\n"
            "/health — проверка бота"
        )
        return

    await message.answer(
        "<b>NotLegal RP</b>\n\n"
        "Привет. Здесь можно подать заявку в проект, связаться с командой или отправить сообщение по сотрудничеству\n\n"
        "Команды:\n"
        "/apply - подать заявку\n"
        "/media - медиа и стримеры\n"
        "/team - команда проекта\n"
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
            reply_markup=url_keyboard("Открыть форму заявки", FORM_URL),
        )
        return

    await message.answer(
        "<b>Заявка в NotLegal RP</b>\n\n"
        "Форма заявки пока не подключена к боту\n\n"
        "Напиши сюда, куда хочешь подать заявку: медиа, стример, PR, SMM, администрация, "
        "ивентер, дизайнер, лидер или другое направление\n\n"
        "Команда проекта получит сообщение и ответит тебе здесь"
    )


@dp.message(Command("media"))
@dp.message(F.text == "Медиа / стример")
async def cmd_media(message: Message):
    if MEDIA_FORM_URL:
        await message.answer(
            "<b>Заявка для медиа и стримеров NotLegal RP</b>\n\n"
            "Если хочешь снимать контент, стримить, делать Shorts/TikTok или обсудить рекламу, заполни форму по кнопке ниже\n\n"
            "Команда посмотрит заявку и ответит тебе здесь или свяжется по указанным контактам",
            reply_markup=url_keyboard("Открыть форму для медиа", MEDIA_FORM_URL),
        )
        return

    await message.answer(
        "<b>Заявка для медиа и стримеров NotLegal RP</b>\n\n"
        "Напиши сюда:\n"
        "1. где ты снимаешь или стримишь\n"
        "2. ссылку на канал\n"
        "3. средние просмотры или онлайн\n"
        "4. что хочешь предложить проекту\n\n"
        "Команда получит сообщение и ответит тебе здесь"
    )


@dp.message(Command("team"))
@dp.message(F.text == "Команда проекта")
async def cmd_team(message: Message):
    if TEAM_FORM_URL:
        await message.answer(
            "<b>Заявка в команду NotLegal RP</b>\n\n"
            "Если хочешь попасть в администрацию, PR, SMM, ивенты, дизайн, монтаж или другое направление, заполни форму по кнопке ниже",
            reply_markup=url_keyboard("Открыть форму в команду", TEAM_FORM_URL),
        )
        return

    await message.answer(
        "<b>Заявка в команду NotLegal RP</b>\n\n"
        "Напиши сюда:\n"
        "1. направление\n"
        "2. опыт\n"
        "3. сколько времени готов уделять\n"
        "4. Telegram или Discord для связи\n\n"
        "Команда получит сообщение и ответит тебе здесь"
    )


@dp.message(Command("help"))
@dp.message(F.text == "Помощь")
async def cmd_help(message: Message):
    await message.answer(
        "<b>Помощь NotLegal RP</b>\n\n"
        "/start - главное меню\n"
        "/apply - подать заявку\n"
        "/media - медиа и стримеры\n"
        "/team - команда проекта\n"
        "/help - помощь и контакты\n\n"
        "Также можешь просто написать сообщение в этот чат. Команда проекта получит его и ответит здесь"
    )


@dp.message(Command("status"))
@dp.message(F.text == "Статус заявки")
async def cmd_status(message: Message):
    await message.answer(
        "<b>Статус заявки</b>\n\n"
        "Если ты уже отправил заявку, напиши сюда Telegram или Discord, никнейм и направление заявки\n\n"
        "Команда проверит и ответит тебе в этом чате"
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
        f"Form URL: <code>{html.escape(FORM_URL or 'not set')}</code>\n"
        f"Media form URL: <code>{html.escape(MEDIA_FORM_URL or 'not set')}</code>\n"
        f"Team form URL: <code>{html.escape(TEAM_FORM_URL or 'not set')}</code>"
    )


@dp.message(F.text.startswith("/"))
async def handle_unknown_command(message: Message):
    if message.chat.id == ADMIN_CHAT_ID:
        await message.answer(
            "Такой админ-команды нет\n\n"
            "Админ-команды:\n"
            "/tpl\n"
            "/stats\n"
            "/health\n\n"
            "Команды для кандидатов:\n"
            "/start\n"
            "/apply\n"
            "/media\n"
            "/team\n"
            "/help"
        )
        return

    await message.answer(
        "Такой команды нет\n\n"
        "Доступные команды:\n"
        "/start\n"
        "/apply\n"
        "/media\n"
        "/team\n"
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
