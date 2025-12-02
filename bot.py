"""
Telegram бот для создания задач в Битрикс24 через @ упоминания
"""
import os
import re
import logging
import threading
import asyncio
from datetime import datetime
from typing import Dict, Optional, List
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
from bitrix24_client import Bitrix24Client
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

# Загрузка переменных окружения (только если файл .env существует)
# В Railway переменные окружения настраиваются в интерфейсе
if os.path.exists('.env'):
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

# Состояния диалога
WAITING_FOR_RESPONSIBLES, WAITING_FOR_DEADLINE, WAITING_FOR_DESCRIPTION, WAITING_FOR_FILES = range(4)

# Хранилище соответствий Telegram User ID -> Bitrix24 User ID
# В продакшене это должно быть в базе данных
TELEGRAM_TO_BITRIX_MAPPING: Dict[int, int] = {}

# Хранилище соответствий Telegram username -> Bitrix24 User ID (для поиска по имени)
USERNAME_TO_BITRIX_MAPPING: Dict[str, int] = {}


def parse_initial_message(text: str, bot_username: str) -> Optional[str]:
    """
    Парсинг начального сообщения вида "@bot, текст задачи"
    
    Args:
        text: Текст сообщения
        bot_username: Username бота (без @)
        
    Returns:
        Текст задачи или None
    """
    # Паттерн для поиска упоминания бота и текста задачи
    # Формат: @bot, текст задачи или @bot текст задачи
    patterns = [
        rf'@{bot_username}[,\s]+(.+)',
        rf'@{bot_username}\s+(.+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            task_text = match.group(1).strip()
            if task_text:
                return task_text
    
    return None


def parse_responsibles(responsibles_text: str) -> List[str]:
    """
    Парсинг списка ответственных через запятую
    
    Args:
        responsibles_text: Текст с именами через запятую
        
    Returns:
        Список имен
    """
    # Разделяем по запятой и очищаем от пробелов
    names = [name.strip() for name in responsibles_text.split(',')]
    return [name for name in names if name]


def parse_deadline(deadline_text: str) -> Optional[str]:
    """
    Парсинг даты в формате дд.мм.гг чч:мм
    
    Args:
        deadline_text: Текст с датой
        
    Returns:
        Дата в формате YYYY-MM-DD HH:MI:SS или None
    """
    try:
        # Паттерн для дд.мм.гг чч:мм
        pattern = r'(\d{2})\.(\d{2})\.(\d{2,4})\s+(\d{2}):(\d{2})'
        match = re.match(pattern, deadline_text.strip())
        
        if not match:
            return None
        
        day, month, year, hour, minute = match.groups()
        
        # Обработка года (если 2 цифры, добавляем 20)
        if len(year) == 2:
            year = f"20{year}"
        
        # Формируем дату
        date_str = f"{year}-{month}-{day} {hour}:{minute}:00"
        
        # Проверяем валидность даты
        datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        
        return date_str
    except Exception as e:
        logger.error(f"Ошибка парсинга даты: {e}")
        return None


def find_bitrix_user_by_name(name: str) -> Optional[int]:
    """
    Поиск пользователя Битрикс24 по имени и фамилии
    
    Args:
        name: Имя и фамилия (например, "Иван Иванов")
        
    Returns:
        ID пользователя Битрикс24 или None
    """
    # Сначала проверяем маппинг по полному имени (если был добавлен через link_username)
    # Но обычно это будет поиск через API
    
    # Ищем через API Битрикс24
    users = bitrix_client.search_users(name)
    
    if users:
        # Ищем точное совпадение по имени и фамилии
        name_parts = name.lower().split()
        for user in users:
            user_name = user.get('NAME', '').lower()
            user_last_name = user.get('LAST_NAME', '').lower()
            full_name = f"{user_name} {user_last_name}".strip()
            
            # Проверяем точное совпадение или совпадение по частям
            if (full_name == name.lower() or 
                (len(name_parts) >= 2 and 
                 user_name == name_parts[0] and user_last_name == name_parts[1])):
                return int(user.get("ID"))
        
        # Если точного совпадения нет, возвращаем первого найденного
        return int(users[0].get("ID"))
    
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "Привет! Я бот для создания задач в Битрикс24.\n\n"
        "Чтобы создать задачу, упомяни меня в сообщении:\n"
        "@бот, текст задачи\n\n"
        "Пример:\n"
        "@bitmugle, Зум по встрече с партнерами"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    await update.message.reply_text(
        "📋 Как использовать бота:\n\n"
        "1. Упомяни меня в сообщении с текстом задачи\n"
        "2. Ответь на вопросы бота для уточнения деталей\n\n"
        "Пример:\n"
        "@bitmugle, Зум по встрече с партнерами\n\n"
        "Команды:\n"
        "/start - Начать работу\n"
        "/help - Показать справку\n"
        "/link bitrix_id - Связать ваш Telegram аккаунт с ID пользователя Битрикс24\n"
        "/link_username @username bitrix_id - Связать Telegram username с пользователем Битрикс24\n"
        "/cancel - Отменить создание задачи"
    )


async def link_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для связывания Telegram User ID с ID пользователя Битрикс24"""
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Использование: /link bitrix_user_id\n\n"
            "Пример: /link 123\n\n"
            "Эта команда свяжет ваш Telegram аккаунт с пользователем Битрикс24."
        )
        return
    
    telegram_user_id = update.effective_user.id
    
    try:
        bitrix_user_id = int(context.args[0])
        
        # Проверяем, существует ли пользователь в Битрикс24
        user_info = bitrix_client.get_user_by_id(bitrix_user_id)
        if not user_info:
            await update.message.reply_text(
                f"❌ Пользователь с ID {bitrix_user_id} не найден в Битрикс24"
            )
            return
        
        TELEGRAM_TO_BITRIX_MAPPING[telegram_user_id] = bitrix_user_id
        await update.message.reply_text(
            f"✅ Связь установлена:\n"
            f"Ваш Telegram аккаунт → {user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')} "
            f"(ID: {bitrix_user_id})"
        )
    except ValueError:
        await update.message.reply_text("❌ ID пользователя должен быть числом")


async def link_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для связывания Telegram username с ID пользователя Битрикс24"""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Использование: /link_username @username bitrix_user_id\n\n"
            "Пример: /link_username @ivanov 123\n\n"
            "Эта команда свяжет Telegram username с пользователем Битрикс24 для поиска по имени."
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
        
        USERNAME_TO_BITRIX_MAPPING[telegram_username] = bitrix_user_id
        await update.message.reply_text(
            f"✅ Связь установлена:\n"
            f"@{telegram_username} → {user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')} "
            f"(ID: {bitrix_user_id})"
        )
    except ValueError:
        await update.message.reply_text("❌ ID пользователя должен быть числом")


async def start_task_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало создания задачи - парсинг начального сообщения"""
    if not update.message or not update.message.text:
        return ConversationHandler.END
    
    text = update.message.text
    bot_username = context.bot.username
    
    # Парсим начальное сообщение
    task_title = parse_initial_message(text, bot_username)
    
    if not task_title:
        return ConversationHandler.END
    
    # Сохраняем данные задачи в контексте
    context.user_data['task_title'] = task_title
    context.user_data['task_files'] = []
    
    # Получаем ID создателя задачи
    telegram_user_id = update.effective_user.id
    creator_id = TELEGRAM_TO_BITRIX_MAPPING.get(telegram_user_id)
    
    if not creator_id:
        await update.message.reply_text(
            "❌ Ваш Telegram аккаунт не связан с Битрикс24.\n\n"
            "Используйте команду:\n"
            "/link bitrix_user_id\n\n"
            "Чтобы узнать свой ID в Битрикс24, зайдите в профиль и посмотрите в URL."
        )
        return ConversationHandler.END
    
    context.user_data['creator_id'] = creator_id
    
    # Задаем первый вопрос
    await update.message.reply_text(
        f"📋 Задача: {task_title}\n\n"
        "1️⃣ На кого задача? (Имя и Фамилия)\n"
        "Можно указать несколько человек через запятую.\n\n"
        "Пример: Иван Иванов, Петр Петров"
    )
    
    return WAITING_FOR_RESPONSIBLES


async def handle_responsibles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответа на вопрос об ответственных"""
    if not update.message or not update.message.text:
        return WAITING_FOR_RESPONSIBLES
    
    responsibles_text = update.message.text.strip()
    responsible_names = parse_responsibles(responsibles_text)
    
    if not responsible_names:
        await update.message.reply_text(
            "❌ Пожалуйста, укажите хотя бы одного ответственного.\n"
            "Формат: Имя Фамилия (можно несколько через запятую)"
        )
        return WAITING_FOR_RESPONSIBLES
    
    # Ищем пользователей в Битрикс24
    responsible_ids = []
    not_found = []
    
    for name in responsible_names:
        bitrix_id = find_bitrix_user_by_name(name)
        if bitrix_id:
            responsible_ids.append(bitrix_id)
        else:
            not_found.append(name)
    
    if not responsible_ids:
        await update.message.reply_text(
            f"❌ Не удалось найти ни одного пользователя в Битрикс24.\n\n"
            f"Проверьте правильность написания имен или используйте команду:\n"
            f"/link_username @username bitrix_id\n"
            f"для связывания Telegram username с пользователем Битрикс24."
        )
        return WAITING_FOR_RESPONSIBLES
    
    if not_found:
        await update.message.reply_text(
            f"⚠️ Не найдены пользователи: {', '.join(not_found)}\n"
            f"Продолжаем с найденными пользователями..."
        )
    
    context.user_data['responsible_ids'] = responsible_ids
    
    # Задаем следующий вопрос
    await update.message.reply_text(
        "2️⃣ Какой срок? (формат: дд.мм.гг чч:мм)\n\n"
        "Пример: 25.12.24 15:30"
    )
    
    return WAITING_FOR_DEADLINE


async def handle_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответа на вопрос о сроке"""
    if not update.message or not update.message.text:
        return WAITING_FOR_DEADLINE
    
    deadline_text = update.message.text.strip()
    
    # Парсим дату
    deadline = parse_deadline(deadline_text)
    
    if not deadline:
        await update.message.reply_text(
            "❌ Неверный формат даты.\n"
            "Используйте формат: дд.мм.гг чч:мм\n\n"
            "Пример: 25.12.24 15:30"
        )
        return WAITING_FOR_DEADLINE
    
    context.user_data['deadline'] = deadline
    
    # Задаем следующий вопрос
    await update.message.reply_text(
        "3️⃣ Введите описание задачи (можно пропустить, отправьте '-' или 'пропустить')"
    )
    
    return WAITING_FOR_DESCRIPTION


async def handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответа на вопрос об описании"""
    if not update.message or not update.message.text:
        return WAITING_FOR_DESCRIPTION
    
    description = update.message.text.strip()
    
    # Проверяем, хочет ли пользователь пропустить
    if description.lower() in ['-', 'пропустить', 'skip', 'нет']:
        description = ""
    
    context.user_data['description'] = description
    
    # Задаем последний вопрос
    await update.message.reply_text(
        "4️⃣ Прикрепите файлы (если нужно, отправьте файлы, или отправьте '-' чтобы пропустить)"
    )
    
    return WAITING_FOR_FILES


async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка файлов или завершение создания задачи"""
    # Проверяем, хочет ли пользователь пропустить файлы
    if update.message and update.message.text:
        text = update.message.text.strip()
        if text.lower() in ['-', 'пропустить', 'skip', 'нет', 'готово']:
            # Пропускаем файлы и создаем задачу
            return await create_task(update, context)
    
    # Обработка файлов
    if update.message and update.message.document:
        file = await update.message.document.get_file()
        file_data = await file.download_as_bytearray()
        
        # Сохраняем информацию о файле
        # В реальности нужно загрузить файл в Битрикс24
        if 'task_files' not in context.user_data:
            context.user_data['task_files'] = []
        
        context.user_data['task_files'].append({
            'filename': update.message.document.file_name,
            'data': file_data
        })
        
        await update.message.reply_text(
            f"✅ Файл '{update.message.document.file_name}' получен.\n"
            f"Отправьте еще файлы или '-' чтобы завершить."
        )
        return WAITING_FOR_FILES
    
    if update.message and update.message.photo:
        # Обработка фото
        photo = update.message.photo[-1]  # Берем фото наибольшего размера
        file = await photo.get_file()
        file_data = await file.download_as_bytearray()
        
        if 'task_files' not in context.user_data:
            context.user_data['task_files'] = []
        
        context.user_data['task_files'].append({
            'filename': f'photo_{photo.file_id}.jpg',
            'data': file_data
        })
        
        await update.message.reply_text(
            "✅ Фото получено.\n"
            "Отправьте еще файлы или '-' чтобы завершить."
        )
        return WAITING_FOR_FILES
    
    # Если нет файлов и нет текста "-", ждем дальше
    if update.message:
        await update.message.reply_text(
            "Отправьте файлы или '-' чтобы завершить создание задачи."
        )
    
    return WAITING_FOR_FILES


async def create_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание задачи в Битрикс24"""
    try:
        task_title = context.user_data.get('task_title')
        responsible_ids = context.user_data.get('responsible_ids')
        creator_id = context.user_data.get('creator_id')
        description = context.user_data.get('description', '')
        deadline = context.user_data.get('deadline')
        
        if not all([task_title, responsible_ids, creator_id]):
            await update.message.reply_text(
                "❌ Ошибка: не хватает данных для создания задачи."
            )
            return ConversationHandler.END
        
        # Создаем задачу
        result = bitrix_client.create_task(
            title=task_title,
            responsible_ids=responsible_ids,
            creator_id=creator_id,
            description=description,
            deadline=deadline,
            file_ids=None  # Файлы пока не загружаем
        )
        
        if result.get("result") and result["result"].get("task"):
            task_id = result["result"]["task"]["id"]
            
            # Формируем список ответственных
            responsibles_info = []
            for resp_id in responsible_ids:
                user_info = bitrix_client.get_user_by_id(resp_id)
                if user_info:
                    name = f"{user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')}".strip()
                    responsibles_info.append(name)
            
            # Получаем ссылку на задачу
            task_url = bitrix_client.get_task_url(task_id, creator_id)
            
            response_text = (
                f"✅ Задача создана!\n\n"
                f"📋 Задача: {task_title}\n"
                f"👤 Ответственные: {', '.join(responsibles_info)}\n"
            )
            
            if deadline:
                response_text += f"📅 Срок: {deadline}\n"
            
            if description:
                response_text += f"📝 Описание: {description[:100]}...\n" if len(description) > 100 else f"📝 Описание: {description}\n"
            
            response_text += f"🆔 ID задачи: {task_id}\n\n"
            response_text += f"🔗 Ссылка на задачу: {task_url}"
            
            # Отправляем сообщение в чат (в тот же чат, где была создана задача)
            await update.message.reply_text(response_text)
        else:
            error_msg = result.get('error_description', 'Неизвестная ошибка')
            await update.message.reply_text(
                f"❌ Ошибка при создании задачи: {error_msg}"
            )
    except Exception as e:
        logger.error(f"Ошибка при создании задачи: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Произошла ошибка при создании задачи. Попробуйте позже."
        )
    finally:
        # Очищаем данные пользователя
        context.user_data.clear()
    
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена создания задачи"""
    context.user_data.clear()
    await update.message.reply_text("❌ Создание задачи отменено.")
    return ConversationHandler.END


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Простой HTTP handler для health check"""
    def do_GET(self):
        if self.path == '/' or self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Отключаем логирование health check запросов
        pass


def start_health_check_server(port: int):
    """Запуск простого HTTP сервера для health check на корневом пути"""
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        logger.info(f"Health check server запущен на порту {port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Ошибка при запуске health check server: {e}")


def start_health_check_thread(port: int):
    """Запуск health check server в отдельном потоке"""
    thread = threading.Thread(target=start_health_check_server, args=(port,), daemon=True)
    thread.start()
    return thread




def main():
    """Запуск бота"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не установлен в переменных окружения")
    
    # Создаем приложение
    application = Application.builder().token(token).build()
    
    # Создаем ConversationHandler для диалога создания задачи
    task_creation_handler = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                start_task_creation
            )
        ],
        states={
            WAITING_FOR_RESPONSIBLES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_responsibles)
            ],
            WAITING_FOR_DEADLINE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_deadline)
            ],
            WAITING_FOR_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)
            ],
            WAITING_FOR_FILES: [
                MessageHandler(
                    filters.TEXT | filters.Document.ALL | filters.PHOTO,
                    handle_files
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("link", link_user))
    application.add_handler(CommandHandler("link_username", link_username))
    application.add_handler(task_creation_handler)
    
    # Проверяем, используется ли webhook (для Railway/продакшена)
    port = int(os.getenv("PORT", 0))
    webhook_url = os.getenv("WEBHOOK_URL")
    
    # Если порт установлен (Railway автоматически устанавливает PORT), используем webhook
    if port > 0:
        # Используем webhook для Railway
        if not webhook_url:
            # Пытаемся получить URL из переменных Railway
            railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
            if railway_domain:
                webhook_url = f"https://{railway_domain}"
            else:
                logger.warning("PORT установлен, но WEBHOOK_URL не найден. Используется polling.")
                application.run_polling(allowed_updates=Update.ALL_TYPES)
                return
        
        logger.info(f"Запуск бота с webhook на порту {port}...")
        logger.info(f"Webhook URL: {webhook_url}/{token}")
        
        # Запускаем webhook с использованием aiohttp для поддержки health check
        if AIOHTTP_AVAILABLE:
            try:
                logger.info("Инициализация webhook с aiohttp...")
                
                # Создаем aiohttp приложение
                aio_app = web.Application()
                
                # Инициализируем Telegram приложение при старте aiohttp
                async def post_init(aio_app):
                    try:
                        logger.info("Инициализация Telegram приложения...")
                        await application.initialize()
                        logger.info("Telegram приложение инициализировано")
                        
                        await application.start()
                        logger.info("Telegram приложение запущено")
                        
                        # Устанавливаем webhook
                        logger.info(f"Установка webhook на {webhook_url}/{token}...")
                        webhook_result = await application.bot.set_webhook(
                            url=f"{webhook_url}/{token}",
                            allowed_updates=Update.ALL_TYPES,
                            drop_pending_updates=True
                        )
                        logger.info(f"Webhook установлен успешно: {webhook_result}")
                    except Exception as init_error:
                        logger.error(f"КРИТИЧЕСКАЯ ОШИБКА при инициализации Telegram приложения: {init_error}", exc_info=True)
                        # Не поднимаем исключение, чтобы сервер продолжал работать
                        # Сервер должен работать даже если Telegram бот не инициализирован
                        logger.warning("Сервер продолжит работу, но Telegram бот может быть недоступен")
                
                async def post_shutdown(aio_app):
                    logger.info("post_shutdown вызван - остановка Telegram приложения...")
                    try:
                        await application.stop()
                        await application.shutdown()
                        logger.info("Telegram приложение остановлено")
                    except Exception as shutdown_error:
                        logger.error(f"Ошибка при остановке Telegram приложения: {shutdown_error}", exc_info=True)
                
                # Обработчик для health check
                async def health_check(request):
                    logger.debug(f"Health check запрос: {request.path}")
                    return web.Response(text='OK')
                
                # Обработчик для webhook от Telegram
                async def webhook_handler(request):
                    try:
                        # Получаем данные от Telegram
                        data = await request.json()
                        update = Update.de_json(data, application.bot)
                        
                        # Обрабатываем обновление (Telegram ожидает быстрый ответ)
                        # Обработка происходит в фоне через application.process_update
                        await application.process_update(update)
                        
                        return web.Response(text='OK')
                    except Exception as e:
                        logger.error(f"Ошибка при обработке webhook: {e}", exc_info=True)
                        return web.Response(text='Error', status=500)
                
                # Регистрируем маршруты
                aio_app.router.add_get('/', health_check)
                aio_app.router.add_get('/health', health_check)
                aio_app.router.add_post(f'/{token}', webhook_handler)
                
                # Инициализируем приложение
                aio_app.on_startup.append(post_init)
                aio_app.on_cleanup.append(post_shutdown)
                
                # Запускаем сервер используя явное управление event loop
                logger.info(f"Запуск aiohttp сервера на 0.0.0.0:{port}...")
                logger.info("Сервер будет работать до получения сигнала остановки...")
                
                # Используем явное управление event loop для лучшего контроля
                async def run():
                    # Создаем runner и запускаем сервер
                    runner = web.AppRunner(aio_app)
                    await runner.setup()
                    site = web.TCPSite(runner, '0.0.0.0', port)
                    await site.start()
                    logger.info(f"Сервер успешно запущен на 0.0.0.0:{port}")
                    
                    # Ждем бесконечно (сервер будет работать до получения сигнала остановки)
                    try:
                        import signal
                        stop = asyncio.Event()
                        
                        def signal_handler():
                            logger.info("Получен сигнал остановки")
                            stop.set()
                        
                        # Регистрируем обработчики сигналов
                        loop = asyncio.get_running_loop()
                        for sig in (signal.SIGTERM, signal.SIGINT):
                            loop.add_signal_handler(sig, signal_handler)
                        
                        # Ждем сигнала остановки
                        await stop.wait()
                    except Exception as e:
                        logger.error(f"Ошибка при работе сервера: {e}", exc_info=True)
                    finally:
                        logger.info("Остановка сервера...")
                        await runner.cleanup()
                
                # Запускаем event loop
                try:
                    asyncio.run(run())
                except KeyboardInterrupt:
                    logger.info("Получен KeyboardInterrupt")
                except Exception as run_error:
                    logger.error(f"Ошибка при запуске сервера: {run_error}", exc_info=True)
                    raise
                
            except KeyboardInterrupt:
                logger.info("Получен сигнал остановки (KeyboardInterrupt)")
                try:
                    asyncio.run(application.stop())
                    asyncio.run(application.shutdown())
                except Exception as shutdown_error:
                    logger.error(f"Ошибка при остановке: {shutdown_error}")
            except Exception as e:
                logger.error(f"Критическая ошибка при запуске webhook: {e}", exc_info=True)
                import traceback
                logger.error("Полный traceback:")
                traceback.print_exc()
                try:
                    asyncio.run(application.stop())
                    asyncio.run(application.shutdown())
                except Exception as shutdown_error:
                    logger.error(f"Ошибка при остановке после ошибки: {shutdown_error}")
                raise
        else:
            # Используем стандартный run_webhook, если aiohttp недоступен
            try:
                logger.info("Инициализация webhook...")
                
                application.run_webhook(
                    listen="0.0.0.0",
                    port=port,
                    url_path=token,
                    webhook_url=f"{webhook_url}/{token}",
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True
                )
                logger.warning("Webhook завершил работу (это не должно происходить в нормальном режиме)")
            except KeyboardInterrupt:
                logger.info("Получен сигнал остановки (KeyboardInterrupt)")
                try:
                    application.stop()
                    application.shutdown()
                except Exception as shutdown_error:
                    logger.error(f"Ошибка при остановке: {shutdown_error}")
            except Exception as e:
                logger.error(f"Критическая ошибка при запуске webhook: {e}", exc_info=True)
                import traceback
                logger.error("Полный traceback:")
                traceback.print_exc()
                try:
                    application.stop()
                    application.shutdown()
                except Exception as shutdown_error:
                    logger.error(f"Ошибка при остановке после ошибки: {shutdown_error}")
                raise
    else:
        # Используем polling для локальной разработки
        logger.info("Запуск бота в режиме polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
