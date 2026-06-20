import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command

# --- БЕЗОПАСНОЕ ПОЛУЧЕНИЕ ПЕРЕМЕННЫХ ИЗ ОКРУЖЕНИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

# Проверка: если токен не задан — бот не запустится
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан! Добавь переменную окружения BOT_TOKEN.")
if ADMIN_ID == 0:
    raise ValueError("❌ ADMIN_ID не задан! Добавь переменную окружения ADMIN_ID.")
# ----------------------------------------------------

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище для связи: ID пользователя -> ID его последнего сообщения админу
user_last_message_id = {}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Это бот поддержки NotLegal RP.\n\n"
        "Напиши мне свой вопрос, и я передам его нашей команде. "
        "Мы постараемся ответить как можно быстрее!"
    )

@dp.message()
async def handle_user_message(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    username = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"

    admin_text = (
        f"✉️ **Новое сообщение от пользователя**\n"
        f"👤 Имя: {user_name}\n"
        f"🆔 ID: {user_id}\n"
        f"🔗 Юзернейм: {username}\n"
        f"────────────────────\n"
        f"📝 Текст:\n{message.text}" if message.text else "📎 Вложение"
    )

    try:
        if message.text:
            sent_msg = await bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode="Markdown")
        elif message.photo:
            sent_msg = await bot.send_photo(chat_id=ADMIN_ID, photo=message.photo[-1].file_id, caption=admin_text)
        elif message.voice:
            sent_msg = await bot.send_voice(chat_id=ADMIN_ID, voice=message.voice.file_id, caption=admin_text)
        else:
            sent_msg = await bot.forward_message(chat_id=ADMIN_ID, from_chat_id=user_id, message_id=message.message_id)

        user_last_message_id[user_id] = sent_msg.message_id
        await message.answer("✅ Твоё сообщение отправлено в поддержку. Мы свяжемся с тобой в ближайшее время!")

    except Exception as e:
        logging.error(f"Ошибка при пересылке сообщения от {user_id}: {e}")
        await message.answer("❌ Произошла ошибка. Попробуй ещё раз позже.")

@dp.message(F.reply_to_message)
async def handle_admin_reply(message: types.Message):
    if message.reply_to_message.from_user.id == bot.id:
        target_user_id = None
        for uid, mid in user_last_message_id.items():
            if mid == message.reply_to_message.message_id:
                target_user_id = uid
                break

        if target_user_id:
            try:
                await bot.send_message(chat_id=target_user_id, text=f"💬 **Ответ от поддержки:**\n{message.text}")
                await message.answer("✅ Ответ отправлен пользователю.")
            except Exception as e:
                logging.error(f"Ошибка при отправке ответа пользователю {target_user_id}: {e}")
                await message.answer("❌ Не удалось отправить ответ.")
        else:
            await message.answer("⚠️ Не удалось определить пользователя для ответа.")
    else:
        await message.answer("ℹ️ Чтобы ответить пользователю, используй функцию 'Ответить' на его сообщении.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
