import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command

# --- БЕЗОПАСНОЕ ПОЛУЧЕНИЕ ПЕРЕМЕННЫХ ИЗ ОКРУЖЕНИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан! Добавь переменную окружения BOT_TOKEN.")
if not ADMIN_ID:
    raise ValueError("❌ ADMIN_ID не задан! Добавь переменную окружения ADMIN_ID.")
# ----------------------------------------------------

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Словарь для хранения связи: ID пользователя -> ID последнего сообщения админу
user_last_message_id = {}

# ========================================================
# ХЕНДЛЕР №1 (ВАЖНО: СТАВИМ ПЕРВЫМ!)
# Обрабатывает ответы админа на сообщения бота
# ========================================================
@dp.message(F.reply_to_message)
async def handle_admin_reply(message: types.Message):
    # Проверяем, что это сообщение от админа
    if message.from_user.id != ADMIN_ID:
        logging.info("Ответ не от админа, игнорируем")
        return

    # Проверяем, что это ответ на сообщение, отправленное ботом
    if message.reply_to_message.from_user.id != bot.id:
        logging.info("Ответ не на сообщение бота, игнорируем")
        return

    reply_to_msg_id = message.reply_to_message.message_id
    logging.info(f"🔍 Админ ответил на сообщение {reply_to_msg_id}")

    # Ищем пользователя, которому принадлежало это сообщение
    target_user_id = None
    for uid, mid in user_last_message_id.items():
        if mid == reply_to_msg_id:
            target_user_id = uid
            break

    if target_user_id:
        try:
            await bot.send_message(
                chat_id=target_user_id,
                text=f"💬 **Ответ от поддержки:**\n{message.text}"
            )
            await message.answer("✅ Ответ отправлен пользователю.")
            logging.info(f"✅ Ответ отправлен пользователю {target_user_id}")
        except Exception as e:
            logging.error(f"Ошибка при отправке ответа: {e}")
            await message.answer("❌ Не удалось отправить ответ.")
    else:
        logging.warning(f"⚠️ Не найден пользователь для сообщения {reply_to_msg_id}")
        await message.answer(
            "⚠️ Не удалось определить пользователя.\n"
            "Возможно, сообщение устарело или пользователь удалён."
        )

# ========================================================
# ХЕНДЛЕР №2
# Обрабатывает все сообщения от пользователей (не админов)
# ========================================================
@dp.message()
async def handle_user_message(message: types.Message):
    # Если сообщение от админа — пропускаем (чтобы не зациклиться)
    if message.from_user.id == ADMIN_ID:
        logging.info("Сообщение от админа, пропускаем")
        return

    user_id = message.from_user.id
    user_name = message.from_user.full_name
    username = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"

    # Формируем текст для админа
    if message.text:
        admin_text = (
            f"✉️ **Новое сообщение от пользователя**\n"
            f"👤 Имя: {user_name}\n"
            f"🆔 ID: {user_id}\n"
            f"🔗 Юзернейм: {username}\n"
            f"────────────────────\n"
            f"📝 Текст:\n{message.text}"
        )
    else:
        admin_text = (
            f"✉️ **Новое сообщение от пользователя**\n"
            f"👤 Имя: {user_name}\n"
            f"🆔 ID: {user_id}\n"
            f"🔗 Юзернейм: {username}\n"
            f"────────────────────\n"
            f"📎 Вложение (см. пересланное сообщение)"
        )

    try:
        # Отправляем админу текстовое сообщение с информацией
        sent_msg = await bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            parse_mode="Markdown"
        )

        # Если есть вложение — пересылаем его отдельно
        if message.photo:
            await bot.send_photo(chat_id=ADMIN_ID, photo=message.photo[-1].file_id)
        elif message.voice:
            await bot.send_voice(chat_id=ADMIN_ID, voice=message.voice.file_id)
        elif message.document:
            await bot.send_document(chat_id=ADMIN_ID, document=message.document.file_id)
        elif message.sticker:
            await bot.send_sticker(chat_id=ADMIN_ID, sticker=message.sticker.file_id)
        elif message.video:
            await bot.send_video(chat_id=ADMIN_ID, video=message.video.file_id)
        elif message.audio:
            await bot.send_audio(chat_id=ADMIN_ID, audio=message.audio.file_id)
        elif message.text is None:
            # Если ничего из вышеперечисленного — пересылаем целиком
            sent_msg = await bot.forward_message(
                chat_id=ADMIN_ID,
                from_chat_id=user_id,
                message_id=message.message_id
            )

        # Сохраняем связь: ID пользователя -> ID сообщения, отправленного админу
        user_last_message_id[user_id] = sent_msg.message_id
        logging.info(f"✅ Сохранена связь: пользователь {user_id} -> сообщение {sent_msg.message_id}")

        # Подтверждение пользователю
        await message.answer("✅ Твоё сообщение отправлено в поддержку. Мы свяжемся с тобой в ближайшее время!")

    except Exception as e:
        logging.error(f"❌ Ошибка при пересылке сообщения от {user_id}: {e}")
        await message.answer("❌ Произошла ошибка. Попробуй ещё раз позже.")

# ========================================================
# ХЕНДЛЕР №3 (опционально)
# Команда /start
# ========================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Это бот поддержки NotLegal RP.\n\n"
        "Напиши мне свой вопрос, и я передам его нашей команде.\n"
        "Мы постараемся ответить как можно быстрее!"
    )

# ========================================================
# ЗАПУСК БОТА
# ========================================================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
