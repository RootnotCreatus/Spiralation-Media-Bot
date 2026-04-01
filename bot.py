import asyncio
import logging
import os
from typing import Dict, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Для ссылок вида https://t.me/c/2380511510/... это обычно chat_id = -1002380511510.
# Если Telegram вернёт ошибку chat not found, подставь фактический chat_id своей группы.
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", "-1002380511510"))
PERSISTENCE_FILE = os.getenv("PERSISTENCE_FILE", "bot_state.pkl")
MEDIA_GROUP_DELAY = float(os.getenv("MEDIA_GROUP_DELAY", "1.2"))

# Ограничение доступа. Если переменная пустая, бот пустит любого, кто напишет ему в личку.
# Пример: ADMIN_USER_IDS=123456789,987654321
_raw_admin_ids = os.getenv("ADMIN_USER_IDS", "").strip()
ALLOWED_USER_IDS = {
    int(part.strip())
    for part in _raw_admin_ids.split(",")
    if part.strip().isdigit()
}

TOPICS: Dict[str, int] = {
    "Base": 1,
    "Медиа": 8,
    "Релизы": 11,
    "Разговорник": 14,
    "Полезности": 64,
}

TOPIC_BY_THREAD_ID = {thread_id: label for label, thread_id in TOPICS.items()}


def is_allowed(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == "private")


def topic_keyboard(selected_thread_id: Optional[int] = None) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for label, thread_id in TOPICS.items():
        title = f"✅ {label}" if thread_id == selected_thread_id else label
        row.append(InlineKeyboardButton(title, callback_data=f"topic:{thread_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def current_topic_label(thread_id: Optional[int]) -> str:
    if thread_id is None:
        return "не выбран"
    return TOPIC_BY_THREAD_ID.get(thread_id, f"топик {thread_id}")


async def deny_access(update: Update) -> None:
    message = update.effective_message
    if message:
        await message.reply_text("Доступ закрыт.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        return
    if not is_allowed(update.effective_user.id if update.effective_user else None):
        await deny_access(update)
        return

    selected = context.user_data.get("selected_topic")
    text = (
        "Это бот-маршрутизатор для отправки сообщений в нужный топик.\n\n"
        "1. Выбери топик.\n"
        "2. Отправь сюда сообщение, фото, видео, документ, аудио, голосовое или альбом.\n"
        "3. Бот скопирует это в нужный топик группы.\n\n"
        f"Сейчас выбран: {current_topic_label(selected)}."
    )
    await update.effective_message.reply_text(
        text,
        reply_markup=topic_keyboard(selected),
    )


async def topics_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        return
    if not is_allowed(update.effective_user.id if update.effective_user else None):
        await deny_access(update)
        return

    selected = context.user_data.get("selected_topic")
    await update.effective_message.reply_text(
        f"Текущий топик: {current_topic_label(selected)}.",
        reply_markup=topic_keyboard(selected),
    )


async def where_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        return
    if not is_allowed(update.effective_user.id if update.effective_user else None):
        await deny_access(update)
        return

    selected = context.user_data.get("selected_topic")
    await update.effective_message.reply_text(
        f"Сейчас выбран: {current_topic_label(selected)}."
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        return
    if not is_allowed(update.effective_user.id if update.effective_user else None):
        await deny_access(update)
        return

    context.user_data.pop("selected_topic", None)
    await update.effective_message.reply_text(
        "Выбор топика сброшен. Выбери новый через /topics."
    )


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        return

    user_id = update.effective_user.id if update.effective_user else None
    await update.effective_message.reply_text(f"Твой Telegram user_id: {user_id}")


async def topic_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    if not is_allowed(query.from_user.id if query.from_user else None):
        await query.answer("Доступ закрыт.", show_alert=True)
        return

    data = query.data or ""
    if not data.startswith("topic:"):
        await query.answer()
        return

    try:
        thread_id = int(data.split(":", 1)[1])
    except ValueError:
        await query.answer("Некорректный топик.", show_alert=True)
        return

    if thread_id not in TOPIC_BY_THREAD_ID:
        await query.answer("Топик не найден в конфиге.", show_alert=True)
        return

    context.user_data["selected_topic"] = thread_id
    label = current_topic_label(thread_id)
    await query.answer(f"Выбран: {label}")

    try:
        await query.edit_message_text(
            f"Текущий топик: {label}.\nТеперь просто отправь сюда сообщение — бот перешлёт его в нужный топик.",
            reply_markup=topic_keyboard(thread_id),
        )
    except TelegramError:
        await query.message.reply_text(
            f"Текущий топик: {label}.",
            reply_markup=topic_keyboard(thread_id),
        )


async def relay_single_message(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    thread_id: int,
) -> None:
    await context.bot.copy_message(
        chat_id=TARGET_CHAT_ID,
        from_chat_id=message.chat_id,
        message_id=message.message_id,
        message_thread_id=thread_id,
    )


async def flush_media_group(
    application: Application,
    source_chat_id: int,
    key: str,
) -> None:
    await asyncio.sleep(MEDIA_GROUP_DELAY)

    media_groups = application.bot_data.setdefault("media_groups", {})
    group = media_groups.pop(key, None)
    if not group:
        return

    message_ids = sorted(set(group["message_ids"]))
    thread_id = group["thread_id"]
    label = current_topic_label(thread_id)

    try:
        await application.bot.copy_messages(
            chat_id=TARGET_CHAT_ID,
            from_chat_id=source_chat_id,
            message_ids=message_ids,
            message_thread_id=thread_id,
        )
        await application.bot.send_message(
            chat_id=source_chat_id,
            text=f"Альбом отправлен в {label}.",
        )
    except TelegramError as exc:
        logger.exception("Не удалось переслать альбом: %s", exc)
        await application.bot.send_message(
            chat_id=source_chat_id,
            text=(
                f"Не удалось отправить альбом в {label}.\n"
                f"Ошибка Telegram: {exc}"
            ),
        )


async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        return

    user_id = update.effective_user.id if update.effective_user else None
    if not is_allowed(user_id):
        await deny_access(update)
        return

    message = update.effective_message
    if not message:
        return

    thread_id = context.user_data.get("selected_topic")
    if thread_id is None:
        await message.reply_text(
            "Сначала выбери топик через /topics.",
            reply_markup=topic_keyboard(None),
        )
        return

    label = current_topic_label(thread_id)

    try:
        if message.media_group_id:
            media_groups = context.application.bot_data.setdefault("media_groups", {})
            key = f"{message.chat_id}:{message.media_group_id}"
            group = media_groups.get(key)

            if group is None:
                media_groups[key] = {
                    "thread_id": thread_id,
                    "message_ids": [message.message_id],
                }
                asyncio.create_task(
                    flush_media_group(context.application, message.chat_id, key)
                )
            else:
                group["message_ids"].append(message.message_id)

            return

        await relay_single_message(message, context, thread_id)
        await message.reply_text(f"Отправлено в {label}.")
    except TelegramError as exc:
        logger.exception("Не удалось переслать сообщение: %s", exc)
        await message.reply_text(
            f"Не удалось отправить сообщение в {label}.\nОшибка Telegram: {exc}"
        )


def build_application() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN")

    persistence = PicklePersistence(filepath=PERSISTENCE_FILE)

    application = (
        ApplicationBuilder()
        .token(token)
        .persistence(persistence)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("topics", topics_command))
    application.add_handler(CommandHandler("where", where_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("myid", myid_command))
    application.add_handler(CallbackQueryHandler(topic_callback, pattern=r"^topic:"))

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_private_message,
        )
    )

    return application


def main() -> None:
    if not ALLOWED_USER_IDS:
        logger.warning(
            "ADMIN_USER_IDS не задан. Сейчас бот принимает сообщения от любого пользователя."
        )

    application = build_application()
    logger.info("Бот запущен. TARGET_CHAT_ID=%s", TARGET_CHAT_ID)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
