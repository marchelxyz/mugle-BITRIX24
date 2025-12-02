"""
Telegram бот для создания задач в Битрикс24 через @ упоминания
"""
import os
import re
import logging
from typing import Dict, Optional
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from bitrix24_client import Bitrix24Client

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация клиента Битрикс24
bitrix_client = Bitrix24Client(
    domain=os.getenv("BITRIX24_DOMAIN"),
    webhook_token=os.getenv("BITRIX24_WEBHOOK_TOKEN")
)

# Хранилище соответствий Telegram username -> Bitrix24 User ID
# В продакшене это должно быть в базе данных
USER_MAPPING: Dict[str, int] = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "Привет! Я бот для создания задач в Битрикс24.\n\n"
        "Используй меня так:\n"
        "@бот @username текст задачи\n\n"
        "Пример:\n"
        "@бот @ivanov создать отчет по продажам"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    await update.message.reply_text(
        "📋 Как использовать бота:\n\n"
        "1. Упомяни меня и коллегу через @\n"
        "2. Напиши текст задачи\n\n"
        "Пример:\n"
        "@бот @ivanov подготовить презентацию к завтрашней встрече\n\n"
        "Команды:\n"
        "/start - Начать работу\n"
        "/help - Показать справку\n"
        "/link @username bitrix_id - Связать Telegram username с ID пользователя Битрикс24"
    )


async def link_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для связывания Telegram username с ID пользователя Битрикс24"""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Использование: /link @username bitrix_user_id\n\n"
            "Пример: /link @ivanov 123"
        )
        return
    
    telegram_username = context.args[0].lstrip('@')
    try:
        bitrix_user_id = int(context.args[1])
        
        # Проверяем, существует ли пользователь в Битрикс24
        user_info = bitrix_client.get_user_by_id(bitrix_user_id)
        if not user_info:
            await update.message.reply_text(
                f"❌ Пользователь с ID {bitrix_user_id} не найден в Битрикс24"
            )
            return
        
        USER_MAPPING[telegram_username] = bitrix_user_id
        await update.message.reply_text(
            f"✅ Связь установлена:\n"
            f"@{telegram_username} → {user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')} "
            f"(ID: {bitrix_user_id})"
        )
    except ValueError:
        await update.message.reply_text("❌ ID пользователя должен быть числом")


def parse_task_message(text: str, bot_username: str) -> Optional[Dict]:
    """
    Парсинг сообщения для извлечения задачи
    
    Args:
        text: Текст сообщения
        bot_username: Username бота (без @)
        
    Returns:
        Словарь с информацией о задаче или None
    """
    # Паттерн для поиска @ упоминаний
    # Формат: @бот @username текст задачи
    pattern = rf'@{bot_username}\s+@(\w+)\s+(.+)'
    match = re.search(pattern, text, re.IGNORECASE)
    
    if not match:
        return None
    
    telegram_username = match.group(1)
    task_text = match.group(2).strip()
    
    if not task_text:
        return None
    
    return {
        "telegram_username": telegram_username,
        "task_text": task_text
    }


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text
    bot_username = context.bot.username
    
    # Парсим сообщение
    task_info = parse_task_message(text, bot_username)
    
    if not task_info:
        # Если сообщение не содержит команду создания задачи, игнорируем
        return
    
    telegram_username = task_info["telegram_username"]
    task_text = task_info["task_text"]
    
    # Получаем ID пользователя Битрикс24
    bitrix_user_id = USER_MAPPING.get(telegram_username)
    
    if not bitrix_user_id:
        await update.message.reply_text(
            f"❌ Пользователь @{telegram_username} не связан с Битрикс24.\n\n"
            f"Используйте команду:\n"
            f"/link @{telegram_username} bitrix_user_id"
        )
        return
    
    # Получаем информацию о пользователе Битрикс24
    user_info = bitrix_client.get_user_by_id(bitrix_user_id)
    if not user_info:
        await update.message.reply_text(
            f"❌ Пользователь с ID {bitrix_user_id} не найден в Битрикс24"
        )
        return
    
    # Получаем ID создателя задачи (можно использовать ID пользователя Telegram)
    # Пока используем значение из переменной окружения или ID ответственного
    creator_id = int(os.getenv("BITRIX24_USER_ID", bitrix_user_id))
    
    try:
        # Создаем задачу в Битрикс24
        result = bitrix_client.create_task(
            title=task_text[:100],  # Ограничение длины названия
            responsible_id=bitrix_user_id,
            creator_id=creator_id,
            description=task_text if len(task_text) > 100 else ""
        )
        
        if result.get("result") and result["result"].get("task"):
            task_id = result["result"]["task"]["id"]
            await update.message.reply_text(
                f"✅ Задача создана!\n\n"
                f"📋 Задача: {task_text}\n"
                f"👤 Ответственный: {user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')}\n"
                f"🆔 ID задачи: {task_id}"
            )
        else:
            await update.message.reply_text(
                f"❌ Ошибка при создании задачи: {result.get('error_description', 'Неизвестная ошибка')}"
            )
    except Exception as e:
        logger.error(f"Ошибка при создании задачи: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Произошла ошибка при создании задачи. Попробуйте позже."
        )


def main():
    """Запуск бота"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не установлен в переменных окружения")
    
    # Создаем приложение
    application = Application.builder().token(token).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("link", link_user))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаем бота
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
