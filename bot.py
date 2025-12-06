"""
Telegram бот для создания задач в Битрикс24 через @ упоминания
"""
import os
import re
import logging
import threading
import asyncio
import secrets
from datetime import datetime
from typing import Dict, Optional, List
from urllib.parse import urlencode
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler
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
# Название поля для Telegram ID можно настроить через переменную окружения BITRIX24_TELEGRAM_FIELD_NAME
# По умолчанию используется UF_TELEGRAM (так как пользователь создал поле с названием "Telegram")
bitrix_client = Bitrix24Client(
    domain=os.getenv("BITRIX24_DOMAIN"),
    webhook_token=os.getenv("BITRIX24_WEBHOOK_TOKEN"),
    telegram_field_name=os.getenv("BITRIX24_TELEGRAM_FIELD_NAME", "UF_TELEGRAM")
)

# Проверяем и создаем поле для Telegram ID в Bitrix24 при старте
# Поле создается автоматически через API, если вебхук имеет права user.userfield
# По умолчанию используется поле UF_TELEGRAM (можно настроить через BITRIX24_TELEGRAM_FIELD_NAME)
# Поле создается один раз и становится доступным для всех пользователей
try:
    field_created = bitrix_client.ensure_telegram_id_field()
    if field_created:
        logger.info(f"✅ Поле {bitrix_client.telegram_field_name} проверено/создано в Bitrix24")
        logger.info(f"💡 Поле доступно для всех пользователей в их профилях")
    else:
        logger.warning(f"⚠️ Не удалось создать поле {bitrix_client.telegram_field_name} в Bitrix24.")
        logger.info(f"💡 Возможные причины:")
        logger.info(f"   1. Вебхук не имеет прав на создание пользовательских полей (user.userfield)")
        logger.info(f"   2. Поле уже существует, но недоступно через API")
        logger.info(f"   Решение: Добавьте права user.userfield к вебхуку или создайте поле вручную:")
        logger.info(f"   Настройки → Пользователи → Пользовательские поля → Создать поле '{bitrix_client.telegram_field_name}'")
except Exception as e:
    logger.error(f"❌ Ошибка при проверке/создании поля {bitrix_client.telegram_field_name}: {e}", exc_info=True)
    logger.warning("Бот будет работать, но сохранение Telegram ID в Bitrix24 может не работать")

# Загружаем существующие связи из Bitrix24 при старте
# Это позволяет восстановить маппинг после перезапуска бота
try:
    loaded_mappings = bitrix_client.load_all_telegram_mappings()
    if loaded_mappings:
        TELEGRAM_TO_BITRIX_MAPPING.update(loaded_mappings)
        logger.info(f"✅ Восстановлено {len(loaded_mappings)} связей из Bitrix24")
    else:
        logger.info("ℹ️ В Bitrix24 пока нет сохраненных связей. Используйте команду /link для связывания.")
except Exception as e:
    logger.error(f"Ошибка при загрузке связей из Bitrix24: {e}", exc_info=True)
    logger.warning("Бот будет работать, но связи нужно будет устанавливать заново")

# Состояния диалога
WAITING_FOR_RESPONSIBLES, WAITING_FOR_DEADLINE, WAITING_FOR_DESCRIPTION, WAITING_FOR_FILES = range(4)

# Хранилище соответствий Telegram User ID -> Bitrix24 User ID
# В продакшене это должно быть в базе данных
TELEGRAM_TO_BITRIX_MAPPING: Dict[int, int] = {}

# Хранилище соответствий Telegram username -> Bitrix24 User ID (для поиска по имени)
USERNAME_TO_BITRIX_MAPPING: Dict[str, int] = {}

# Маппинг Telegram thread_id -> Bitrix24 Department ID
# Формат: {thread_id: department_id}
# thread_id - это ID темы в супергруппе Telegram
# department_id - это ID подразделения в Bitrix24
# Можно настроить через переменную окружения THREAD_DEPARTMENT_MAPPING в формате JSON:
# {"123": 5, "456": 10} где 123 и 456 - thread_id, 5 и 10 - department_id
THREAD_TO_DEPARTMENT_MAPPING: Dict[int, int] = {}

# Загружаем маппинг из переменной окружения при старте
try:
    import json
    thread_mapping_str = os.getenv("THREAD_DEPARTMENT_MAPPING")
    if thread_mapping_str:
        thread_mapping_dict = json.loads(thread_mapping_str)
        # Преобразуем ключи в int (Telegram thread_id всегда int)
        THREAD_TO_DEPARTMENT_MAPPING = {int(k): int(v) for k, v in thread_mapping_dict.items()}
        logger.info(f"✅ Загружено {len(THREAD_TO_DEPARTMENT_MAPPING)} маппингов thread_id -> department_id")
    else:
        logger.info("ℹ️ THREAD_DEPARTMENT_MAPPING не установлен. Автоматический выбор отдела по теме отключен.")
except Exception as e:
    logger.warning(f"⚠️ Ошибка при загрузке THREAD_DEPARTMENT_MAPPING: {e}. Автоматический выбор отдела по теме отключен.")

# Загружаем и логируем все подразделения из Bitrix24 при старте
# ВАЖНО: Вызывается ПОСЛЕ инициализации THREAD_TO_DEPARTMENT_MAPPING
def log_all_departments():
    """Функция для логирования всех подразделений из Bitrix24"""
    try:
        departments = bitrix_client.get_all_departments()
        if departments:
            # Сортируем по ID для удобства
            try:
                departments_sorted = sorted(departments, key=lambda x: int(x.get('ID', 0)))
            except (ValueError, TypeError):
                departments_sorted = departments
            
            logger.info("")
            logger.info("=" * 70)
            logger.info("📋 СПИСОК ПОДРАЗДЕЛЕНИЙ ИЗ BITRIX24:")
            logger.info("=" * 70)
            logger.info(f"{'ID':<10} | {'Название':<40} | {'Родитель':<10}")
            logger.info("-" * 70)
            
            for dept in departments_sorted:
                dept_id = str(dept.get('ID', 'N/A'))
                dept_name = dept.get('NAME', 'Без названия')
                dept_parent = dept.get('PARENT', '')
                dept_parent_str = str(dept_parent) if dept_parent else '-'
                
                # Обрезаем длинные названия для читаемости
                dept_name_display = dept_name[:40] if len(dept_name) <= 40 else dept_name[:37] + "..."
                
                logger.info(f"{dept_id:<10} | {dept_name_display:<40} | {dept_parent_str:<10}")
            
            logger.info("-" * 70)
            logger.info(f"✅ Всего найдено подразделений: {len(departments)}")
            logger.info("=" * 70)
            logger.info("")
            
            # Также выводим формат для THREAD_DEPARTMENT_MAPPING
            if THREAD_TO_DEPARTMENT_MAPPING:
                logger.info("💡 Текущий маппинг thread_id -> department_id:")
                for thread_id, dept_id in sorted(THREAD_TO_DEPARTMENT_MAPPING.items()):
                    dept_info = next((d for d in departments if str(d.get('ID')) == str(dept_id)), None)
                    dept_name = dept_info.get('NAME', 'Неизвестно') if dept_info else 'Неизвестно'
                    logger.info(f"   Thread ID {thread_id} -> Department ID {dept_id} ({dept_name})")
                logger.info("")
        else:
            logger.info("ℹ️ В Bitrix24 не найдено подразделений")
    except Exception as e:
        logger.error(f"Ошибка при загрузке подразделений из Bitrix24: {e}", exc_info=True)
        logger.warning("Бот будет работать, но список подразделений недоступен")

# Вызываем функцию логирования при старте (после инициализации THREAD_TO_DEPARTMENT_MAPPING)
log_all_departments()


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
    # Проверяем, есть ли параметр startapp (для открытия Mini App из меню прикрепления)
    if context.args and len(context.args) > 0:
        start_param = context.args[0]
        # Если это токен для Mini App (начинается с определенного префикса или имеет формат токена)
        if len(start_param) > 20:  # Предполагаем, что токены длинные
            # Открываем Mini App через кнопку
            webhook_url = os.getenv("WEBHOOK_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN")
            if webhook_url and not webhook_url.startswith("http"):
                webhook_url = f"https://{webhook_url}"
            
            if webhook_url:
                if webhook_url.endswith("/"):
                    webhook_url = webhook_url.rstrip("/")
                
                query_params = urlencode({"token": start_param})
                web_app_url = f"{webhook_url}/miniapp?{query_params}"
                web_app_info = WebAppInfo(url=web_app_url)
                
                button = InlineKeyboardButton(
                    "📋 Открыть форму создания задачи",
                    web_app=web_app_info
                )
                keyboard = InlineKeyboardMarkup([[button]])
                
                await update.message.reply_text(
                    "📋 Откройте форму для создания задачи:",
                    reply_markup=keyboard
                )
                return
    
    await update.message.reply_text(
        "Привет! Я бот для создания задач в Битрикс24.\n\n"
        "Чтобы создать задачу, упомяни меня в сообщении:\n"
        "@бот, текст задачи\n\n"
        "Пример:\n"
        "@bitmugle, Зум по встрече с партнерами"
    )


async def create_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /create - открывает Mini App для создания задачи"""
    webhook_url = os.getenv("WEBHOOK_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if webhook_url and not webhook_url.startswith("http"):
        webhook_url = f"https://{webhook_url}"
    
    if not webhook_url:
        await update.message.reply_text(
            "⚠️ Mini App недоступен. Используйте стандартный способ создания задачи через @ упоминание."
        )
        return
    
    # Убираем завершающий слеш
    if webhook_url.endswith("/"):
        webhook_url = webhook_url.rstrip("/")
    
    # Создаем уникальный токен для сессии Mini App
    session_token = secrets.token_urlsafe(32)
    
    # Получаем ID создателя задачи
    telegram_user_id = update.effective_user.id
    
    # Определяем Bitrix ID создателя
    creator_bitrix_id = TELEGRAM_TO_BITRIX_MAPPING.get(telegram_user_id)
    if not creator_bitrix_id:
        creator_info = bitrix_client.get_user_by_telegram_id(telegram_user_id)
        if creator_info:
            creator_bitrix_id = int(creator_info.get("ID"))
            TELEGRAM_TO_BITRIX_MAPPING[telegram_user_id] = creator_bitrix_id
    
    if not creator_bitrix_id:
        await update.message.reply_text(
            "❌ Ваш Telegram аккаунт не связан с Битрикс24.\n\n"
            "Используйте команду:\n"
            "/link bitrix_user_id"
        )
        return
    
    # Получаем информацию о создателе
    creator_info = bitrix_client.get_user_by_id(creator_bitrix_id)
    creator_name = f"{creator_info.get('NAME', '')} {creator_info.get('LAST_NAME', '')}".strip() if creator_info else f"ID: {creator_bitrix_id}"
    
    # Получаем thread_id (ID темы в супергруппе), если сообщение отправлено в теме
    thread_id = None
    department_id = None
    if update.message.message_thread_id:
        thread_id = update.message.message_thread_id
        # Автоматически определяем отдел на основе thread_id
        department_id = THREAD_TO_DEPARTMENT_MAPPING.get(thread_id)
        if department_id:
            logger.info(f"Автоматически определен отдел {department_id} для thread_id {thread_id}")
    
    # Сохраняем данные сессии
    context.bot_data[f"miniapp_session_{session_token}"] = {
        "creator_bitrix_id": creator_bitrix_id,
        "responsible_bitrix_id": None,
        "original_message_text": "",
        "creator_name": creator_name,
        "responsible_name": "",
        "creator_telegram_id": telegram_user_id,
        "responsible_telegram_id": None,
        "chat_id": update.message.chat_id,
        "message_id": update.message.message_id,
        "thread_id": thread_id,  # Сохраняем thread_id для определения отдела
        "department_id": department_id,  # Сохраняем автоматически определенный отдел
        "timestamp": datetime.now().isoformat()
    }
    
    # Формируем URL для Mini App
    query_params = urlencode({"token": session_token})
    web_app_url = f"{webhook_url}/miniapp?{query_params}"
    web_app_info = WebAppInfo(url=web_app_url)
    
    button = InlineKeyboardButton(
        "📋 Открыть форму создания задачи",
        web_app=web_app_info
    )
    keyboard = InlineKeyboardMarkup([[button]])
    
    await update.message.reply_text(
        "📋 Нажмите кнопку ниже, чтобы открыть форму создания задачи:",
        reply_markup=keyboard
    )


async def departments_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для получения списка всех подразделений из Bitrix24"""
    try:
        departments = bitrix_client.get_all_departments()
        
        if not departments:
            # Проверяем, была ли ошибка 401 в логах (это будет видно из логов)
            # Но для пользователя даем более информативное сообщение
            await update.message.reply_text(
                "ℹ️ В Bitrix24 не найдено подразделений или нет доступа к ним.\n\n"
                "Если вы видите ошибку 401 в логах, проверьте:\n"
                "1. Правильность токена вебхука BITRIX24_WEBHOOK_TOKEN\n"
                "2. Права вебхука в Bitrix24 (должен иметь доступ к department.get)\n"
                "3. Не истек ли срок действия вебхука\n\n"
                "Для настройки прав вебхука в Bitrix24:\n"
                "Настройки → Разработчикам → Входящий вебхук → Выберите ваш вебхук → "
                "Убедитесь, что включены права на чтение подразделений"
            )
            return
        
        # Формируем список подразделений
        dept_list = []
        for dept in departments:
            dept_id = dept.get('ID', 'N/A')
            dept_name = dept.get('NAME', 'Без названия')
            dept_list.append(f"ID: {dept_id} | {dept_name}")
        
        # Разбиваем на части, если список слишком длинный (Telegram ограничивает длину сообщения)
        message_text = "📋 Список подразделений из Bitrix24:\n\n"
        current_message = message_text
        
        for dept_line in dept_list:
            if len(current_message + dept_line + "\n") > 4000:  # Лимит Telegram ~4096 символов
                await update.message.reply_text(current_message)
                current_message = ""
            
            current_message += dept_line + "\n"
        
        if current_message != message_text:
            await update.message.reply_text(current_message)
        
        # Также логируем в консоль с красивым форматированием
        logger.info("")
        logger.info("=" * 70)
        logger.info("📋 СПИСОК ПОДРАЗДЕЛЕНИЙ ИЗ BITRIX24 (по запросу команды /departments):")
        logger.info("=" * 70)
        logger.info(f"{'ID':<10} | {'Название':<40} | {'Родитель':<10}")
        logger.info("-" * 70)
        
        # Сортируем по ID для удобства
        try:
            departments_sorted = sorted(departments, key=lambda x: int(x.get('ID', 0)))
        except (ValueError, TypeError):
            departments_sorted = departments
        
        for dept in departments_sorted:
            dept_id = str(dept.get('ID', 'N/A'))
            dept_name = dept.get('NAME', 'Без названия')
            dept_parent = dept.get('PARENT', '')
            dept_parent_str = str(dept_parent) if dept_parent else '-'
            
            # Обрезаем длинные названия для читаемости
            dept_name_display = dept_name[:40] if len(dept_name) <= 40 else dept_name[:37] + "..."
            
            logger.info(f"{dept_id:<10} | {dept_name_display:<40} | {dept_parent_str:<10}")
        
        logger.info("-" * 70)
        logger.info(f"✅ Всего найдено подразделений: {len(departments)}")
        logger.info("=" * 70)
        logger.info("")
        
    except Exception as e:
        logger.error(f"Ошибка при получении списка подразделений: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Ошибка при получении списка подразделений: {e}"
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
        "/create - Создать задачу (открывает форму)\n"
        "/help - Показать справку\n"
        "/link bitrix_id - Связать ваш Telegram аккаунт с ID пользователя Битрикс24\n"
        "  (Telegram ID будет сохранен в профиле пользователя в Bitrix24)\n"
        "/check_telegram_id bitrix_id - Проверить сохраненный Telegram ID в профиле пользователя\n"
        "/link_username @username bitrix_id - Связать Telegram username с пользователем Битрикс24\n"
        "/departments - Показать список всех подразделений из Bitrix24\n"
        "/cancel - Отменить создание задачи\n\n"
        "💡 После команды /link бот автоматически определяет ваш аккаунт "
        "по Telegram ID из Bitrix24!"
    )


async def link_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для связывания Telegram User ID с ID пользователя Битрикс24"""
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Использование: /link bitrix_user_id\n\n"
            "Пример: /link 123\n\n"
            "Эта команда свяжет ваш Telegram аккаунт с пользователем Битрикс24.\n"
            "Telegram ID будет сохранен в профиле пользователя в Bitrix24."
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
        
        # Сохраняем Telegram ID в Bitrix24
        success = bitrix_client.update_user_telegram_id(bitrix_user_id, telegram_user_id)
        
        if success:
            # Также сохраняем в локальное хранилище для быстрого доступа
            TELEGRAM_TO_BITRIX_MAPPING[telegram_user_id] = bitrix_user_id
            
            # Проверяем, что данные действительно сохранились
            # Получаем обновленную информацию о пользователе
            updated_user_info = bitrix_client.get_user_by_id(bitrix_user_id)
            saved_telegram_id = None
            if updated_user_info:
                saved_telegram_id = updated_user_info.get(bitrix_client.telegram_field_name)
            
            response_text = (
                f"✅ Связь установлена и сохранена в Bitrix24:\n"
                f"Ваш Telegram аккаунт (ID: {telegram_user_id}) → "
                f"{user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')} "
                f"(ID: {bitrix_user_id})\n\n"
            )
            
            if saved_telegram_id:
                response_text += f"✅ Подтверждено: Telegram ID {saved_telegram_id} найден в профиле пользователя в Bitrix24\n\n"
            else:
                response_text += (
                    f"⚠️ Внимание: Telegram ID не найден в ответе API Bitrix24.\n"
                    f"Это может означать, что:\n"
                    f"1. Поле '{bitrix_client.telegram_field_name}' не возвращается в API\n"
                    f"2. Данные еще обрабатываются (попробуйте проверить профиль в Bitrix24 вручную)\n\n"
                )
            
            response_text += f"Теперь бот будет автоматически определять ваш аккаунт!"
            
            await update.message.reply_text(response_text)
        else:
            # Если не удалось сохранить в Bitrix24, сохраняем только локально
            TELEGRAM_TO_BITRIX_MAPPING[telegram_user_id] = bitrix_user_id
            await update.message.reply_text(
                f"⚠️ Связь установлена локально:\n"
                f"Ваш Telegram аккаунт → {user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')} "
                f"(ID: {bitrix_user_id})\n\n"
                f"❌ Не удалось сохранить в Bitrix24.\n\n"
                f"Возможные причины:\n"
                f"1. Поле '{bitrix_client.telegram_field_name}' не существует в Bitrix24\n"
                f"2. Вебхук не имеет прав на изменение пользователей (user.update)\n"
                f"3. Вебхук не имеет прав на изменение пользовательских полей\n\n"
                f"Проверьте права вебхука в Bitrix24:\n"
                f"Настройки → Разработчикам → Входящий вебхук → Выберите ваш вебхук"
            )
    except ValueError:
        await update.message.reply_text("❌ ID пользователя должен быть числом")
    except Exception as e:
        logger.error(f"Ошибка при связывании пользователя: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Произошла ошибка при связывании аккаунта. Попробуйте позже."
        )


async def check_telegram_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки сохраненного Telegram ID в профиле пользователя Bitrix24"""
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Использование: /check_telegram_id bitrix_user_id\n\n"
            "Пример: /check_telegram_id 123\n\n"
            "Эта команда проверит, сохранен ли Telegram ID в профиле пользователя Bitrix24."
        )
        return
    
    try:
        bitrix_user_id = int(context.args[0])
        
        # Получаем информацию о пользователе
        user_info = bitrix_client.get_user_by_id(bitrix_user_id)
        if not user_info:
            await update.message.reply_text(
                f"❌ Пользователь с ID {bitrix_user_id} не найден в Битрикс24"
            )
            return
        
        # Проверяем наличие Telegram ID
        telegram_id = user_info.get(bitrix_client.telegram_field_name)
        user_name = f"{user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')}".strip()
        
        response_text = (
            f"📋 Информация о пользователе Bitrix24:\n\n"
            f"👤 Имя: {user_name}\n"
            f"🆔 ID: {bitrix_user_id}\n"
            f"📱 Поле '{bitrix_client.telegram_field_name}': "
        )
        
        if telegram_id:
            response_text += f"✅ {telegram_id}\n\n"
            response_text += f"Telegram ID сохранен в профиле пользователя!"
        else:
            response_text += f"❌ Не найдено\n\n"
            response_text += (
                f"Telegram ID не найден в профиле пользователя.\n\n"
                f"Возможные причины:\n"
                f"1. Telegram ID еще не был сохранен (используйте /link bitrix_user_id)\n"
                f"2. Поле '{bitrix_client.telegram_field_name}' не возвращается в API\n"
                f"3. Поле не существует в Bitrix24\n\n"
                f"Проверьте профиль пользователя в Bitrix24 вручную:\n"
                f"Настройки → Пользователи → Откройте профиль пользователя → "
                f"Проверьте наличие поля '{bitrix_client.telegram_field_name}'"
            )
        
        await update.message.reply_text(response_text)
        
    except ValueError:
        await update.message.reply_text("❌ ID пользователя должен быть числом")
    except Exception as e:
        logger.error(f"Ошибка при проверке Telegram ID: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Произошла ошибка при проверке. Попробуйте позже."
        )


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
    
    # Получаем thread_id (ID темы в супергруппе), если сообщение отправлено в теме
    thread_id = None
    if update.message.message_thread_id:
        thread_id = update.message.message_thread_id
        # Автоматически определяем отдел на основе thread_id
        department_id = THREAD_TO_DEPARTMENT_MAPPING.get(thread_id)
        if department_id:
            context.user_data['department_id'] = department_id
            logger.info(f"Автоматически определен отдел {department_id} для thread_id {thread_id}")
    
    # Получаем ID создателя задачи
    telegram_user_id = update.effective_user.id
    
    # Сначала проверяем локальное хранилище
    creator_id = TELEGRAM_TO_BITRIX_MAPPING.get(telegram_user_id)
    
    # Если не найдено локально, ищем в Bitrix24
    if not creator_id:
        user_info = bitrix_client.get_user_by_telegram_id(telegram_user_id)
        if user_info:
            creator_id = int(user_info.get("ID"))
            # Сохраняем в локальное хранилище для быстрого доступа
            TELEGRAM_TO_BITRIX_MAPPING[telegram_user_id] = creator_id
            logger.info(f"Пользователь найден в Bitrix24 по Telegram ID {telegram_user_id}: {creator_id}")
    
    if not creator_id:
        await update.message.reply_text(
            "❌ Ваш Telegram аккаунт не связан с Битрикс24.\n\n"
            "Используйте команду:\n"
            "/link bitrix_user_id\n\n"
            "Чтобы узнать свой ID в Битрикс24, зайдите в профиль и посмотрите в URL.\n\n"
            "После связывания ваш Telegram ID будет сохранен в Bitrix24, "
            "и бот будет автоматически определять ваш аккаунт."
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
        
        # Получаем department_id из контекста (если был определен автоматически)
        department_id = context.user_data.get('department_id')
        
        # Создаем задачу
        result = bitrix_client.create_task(
            title=task_title,
            responsible_ids=responsible_ids,
            creator_id=creator_id,
            description=description,
            deadline=deadline,
            file_ids=None,  # Файлы пока не загружаем
            department_id=department_id
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


async def handle_reply_with_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик для reply-сообщений с упоминанием бота
    Когда пользователь отвечает на сообщение и тегает бота через @
    """
    if not update.message or not update.message.reply_to_message:
        return
    
    message = update.message
    reply_to = message.reply_to_message
    bot_username = context.bot.username
    
    if not bot_username:
        logger.warning("Bot username не установлен")
        return
    
    # Проверяем, что в сообщении есть упоминание бота
    text = message.text or message.caption or ""
    text_lower = text.lower()
    bot_username_lower = bot_username.lower()
    
    # Проверяем упоминание через @username в тексте
    has_mention_in_text = f"@{bot_username_lower}" in text_lower
    
    # Проверяем упоминание через entities (более надежный способ)
    has_mention_in_entities = False
    entities = message.entities or message.caption_entities or []
    for entity in entities:
        if entity.type == "mention":
            mention_text = text[entity.offset:entity.offset + entity.length].lower()
            if mention_text == f"@{bot_username_lower}":
                has_mention_in_entities = True
                break
    
    # Также проверяем, упоминается ли бот через text_mention (для reply-сообщений)
    has_bot_mention = False
    for entity in entities:
        if entity.type == "text_mention" and entity.user:
            if entity.user.id == context.bot.id:
                has_bot_mention = True
                break
    
    # Проверяем упоминание через @username или просто username в тексте
    has_mention = has_mention_in_text or has_mention_in_entities or has_bot_mention
    
    logger.info(f"Проверка упоминания бота: текст='{text}', has_mention_in_text={has_mention_in_text}, "
                f"has_mention_in_entities={has_mention_in_entities}, has_bot_mention={has_bot_mention}, "
                f"bot_username={bot_username}, entities_count={len(entities)}")
    
    if not has_mention:
        logger.debug("Упоминание бота не найдено в reply-сообщении")
        return
    
    # Получаем Telegram ID пользователя, который ответил (постановщик)
    creator_telegram_id = message.from_user.id
    
    # Получаем Telegram ID автора сообщения, на которое отвечают (исполнитель)
    responsible_telegram_id = reply_to.from_user.id
    
    # Получаем текст сообщения, на которое отвечают (будет описанием задачи)
    original_message_text = reply_to.text or reply_to.caption or ""
    
    # Получаем thread_id (ID темы в супергруппе), если сообщение отправлено в теме
    thread_id = None
    department_id = None
    if message.message_thread_id:
        thread_id = message.message_thread_id
        # Автоматически определяем отдел на основе thread_id
        department_id = THREAD_TO_DEPARTMENT_MAPPING.get(thread_id)
        if department_id:
            logger.info(f"Автоматически определен отдел {department_id} для thread_id {thread_id}")
    
    # Определяем Bitrix ID постановщика
    creator_bitrix_id = TELEGRAM_TO_BITRIX_MAPPING.get(creator_telegram_id)
    if not creator_bitrix_id:
        creator_info = bitrix_client.get_user_by_telegram_id(creator_telegram_id)
        if creator_info:
            creator_bitrix_id = int(creator_info.get("ID"))
            TELEGRAM_TO_BITRIX_MAPPING[creator_telegram_id] = creator_bitrix_id
    
    # Определяем Bitrix ID исполнителя
    responsible_bitrix_id = TELEGRAM_TO_BITRIX_MAPPING.get(responsible_telegram_id)
    if not responsible_bitrix_id:
        responsible_info = bitrix_client.get_user_by_telegram_id(responsible_telegram_id)
        if responsible_info:
            responsible_bitrix_id = int(responsible_info.get("ID"))
            TELEGRAM_TO_BITRIX_MAPPING[responsible_telegram_id] = responsible_bitrix_id
    
    if not creator_bitrix_id:
        await message.reply_text(
            "❌ Ваш Telegram аккаунт не связан с Битрикс24.\n\n"
            "Используйте команду:\n"
            "/link bitrix_user_id"
        )
        return
    
    # Получаем информацию о пользователях для отображения
    creator_info = bitrix_client.get_user_by_id(creator_bitrix_id)
    creator_name = f"{creator_info.get('NAME', '')} {creator_info.get('LAST_NAME', '')}".strip() if creator_info else f"ID: {creator_bitrix_id}"
    
    # Определяем имя исполнителя
    if not responsible_bitrix_id:
        # Если исполнитель не найден, используем имя из Telegram
        responsible_name = f"@{reply_to.from_user.username}" if reply_to.from_user.username else f"ID: {responsible_telegram_id}"
        logger.warning(f"Исполнитель {responsible_telegram_id} не найден в Bitrix24, будет предложено выбрать в Mini App")
    else:
        # Если исполнитель найден, получаем его имя из Bitrix24
        responsible_info = bitrix_client.get_user_by_id(responsible_bitrix_id)
        responsible_name = f"{responsible_info.get('NAME', '')} {responsible_info.get('LAST_NAME', '')}".strip() if responsible_info else f"ID: {responsible_bitrix_id}"
    
    # Формируем данные для Mini App
    webhook_url = os.getenv("WEBHOOK_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if webhook_url and not webhook_url.startswith("http"):
        webhook_url = f"https://{webhook_url}"
    
    # Убираем завершающий слеш, если есть
    if webhook_url and webhook_url.endswith("/"):
        webhook_url = webhook_url.rstrip("/")
    
    if not webhook_url:
        # Если нет webhook URL, используем альтернативный способ через callback
        await message.reply_text(
            "⚠️ Mini App недоступен. Используйте стандартный способ создания задачи через @ упоминание."
        )
        return
    
    # Создаем уникальный токен для сессии Mini App
    session_token = secrets.token_urlsafe(32)
    
    # Сохраняем данные сессии (в продакшене лучше использовать БД или Redis)
    # Время жизни сессии - 1 час
    context.bot_data[f"miniapp_session_{session_token}"] = {
        "creator_bitrix_id": creator_bitrix_id,
        "responsible_bitrix_id": responsible_bitrix_id,  # Может быть None
        "original_message_text": original_message_text,
        "creator_name": creator_name,
        "responsible_name": responsible_name,
        "creator_telegram_id": creator_telegram_id,
        "responsible_telegram_id": responsible_telegram_id,
        "chat_id": message.chat_id,  # Сохраняем chat_id для отправки ответа
        "message_id": message.message_id,  # Сохраняем message_id для ответа
        "thread_id": thread_id,  # Сохраняем thread_id для определения отдела
        "department_id": department_id,  # Сохраняем автоматически определенный отдел
        "timestamp": datetime.now().isoformat()
    }
    
    # Получаем username бота для создания Direct Link Mini App
    bot_username = context.bot.username
    if not bot_username:
        logger.error("Bot username не установлен, невозможно создать Direct Link Mini App")
        await message.reply_text(
            "⚠️ Ошибка конфигурации: username бота не установлен. Обратитесь к администратору."
        )
        return
    
    # Для Direct Link Mini Apps используем формат: https://t.me/botusername?startapp=token
    # Это позволяет открывать Mini App как мини-апп из публичных групп
    # Согласно документации: https://core.telegram.org/bots/webapps#direct-link-mini-apps
    # 
    # ВАЖНО: Для работы этого формата нужно настроить Main Mini App через BotFather:
    # 1. Откройте @BotFather в Telegram
    # 2. Отправьте команду /newapp или выберите вашего бота -> Bot Settings -> Main Mini App
    # 3. Укажите URL вашего Mini App (например: https://your-domain.com/miniapp)
    # 4. После настройки ссылка https://t.me/botusername?startapp=token будет открывать Mini App
    direct_link_url = f"https://t.me/{bot_username}?startapp={session_token}"
    
    # Также формируем прямой URL для Mini App (используется для приватных чатов и как fallback)
    query_params = urlencode({"token": session_token})
    web_app_url = f"{webhook_url}/miniapp?{query_params}"
    
    # Логируем URL для отладки
    logger.info(f"Создание кнопки Web App")
    logger.info(f"Direct Link URL: {direct_link_url}")
    logger.info(f"Web App URL: {web_app_url}")
    logger.info(f"Webhook URL: {webhook_url}, Session token length: {len(session_token)}")
    logger.info(f"Тип чата: {message.chat.type}, Chat ID: {message.chat_id}")
    
    # Проверяем тип чата
    # Web App кнопки работают только в приватных чатах согласно документации
    # Для публичных групп используем Direct Link Mini App формат
    chat_type = message.chat.type if hasattr(message.chat, 'type') else None
    is_private_chat = chat_type == 'private'
    
    logger.info(f"Тип чата определен: {chat_type}, приватный: {is_private_chat}")
    
    try:
        if is_private_chat:
            # Для приватных чатов используем Web App кнопку с прямым URL
            # Создаем WebAppInfo объект
            # В python-telegram-bot 20.x WebAppInfo принимает только url
            web_app_info = WebAppInfo(url=web_app_url)
            logger.info(f"WebAppInfo создан успешно для приватного чата, URL: {web_app_url[:100]}...")
            
            # Создаем кнопку с Web App
            # В python-telegram-bot 20.x первый параметр - text (позиционный), остальные - именованные
            button = InlineKeyboardButton(
                "📋 Создать задачу",  # Позиционный параметр для text
                web_app=web_app_info  # Именованный параметр
            )
            logger.info(f"Web App кнопка создана успешно")
        else:
            # Для групповых чатов используем Direct Link Mini App формат
            # Формат: https://t.me/botusername?startapp=token
            # Это позволяет открывать Mini App как мини-апп из публичных групп
            # Согласно документации: https://core.telegram.org/bots/webapps#direct-link-mini-apps
            logger.info(f"Используем Direct Link Mini App для группового чата")
            button = InlineKeyboardButton(
                "📋 Создать задачу",  # Позиционный параметр для text
                url=direct_link_url  # Именованный параметр - Direct Link формат
            )
            logger.info(f"Direct Link Mini App кнопка создана успешно")
        
        # Создаем клавиатуру
        keyboard = InlineKeyboardMarkup([[button]])
        logger.info(f"Клавиатура создана успешно")
        
    except TypeError as e:
        # TypeError может возникнуть, если неправильные параметры
        logger.error(f"TypeError при создании кнопки: {e}", exc_info=True)
        logger.error(f"Проверьте синтаксис InlineKeyboardButton")
        # Fallback: используем обычную URL кнопку
        logger.info("Попытка создать обычную URL кнопку как fallback")
        try:
            button = InlineKeyboardButton("📋 Создать задачу", url=web_app_url)
            keyboard = InlineKeyboardMarkup([[button]])
            logger.info("URL кнопка создана как fallback")
        except Exception as fallback_error:
            logger.error(f"Ошибка при создании fallback кнопки: {fallback_error}", exc_info=True)
            await message.reply_text(
                "⚠️ Ошибка при создании кнопки. Попробуйте позже."
            )
            return
    except Exception as e:
        logger.error(f"Ошибка при создании кнопки: {e}", exc_info=True)
        logger.error(f"Тип ошибки: {type(e).__name__}")
        # Fallback: используем обычную URL кнопку
        logger.info("Попытка создать обычную URL кнопку как fallback")
        try:
            button = InlineKeyboardButton("📋 Создать задачу", url=web_app_url)
            keyboard = InlineKeyboardMarkup([[button]])
            logger.info("URL кнопка создана как fallback")
        except Exception as fallback_error:
            logger.error(f"Ошибка при создании fallback кнопки: {fallback_error}", exc_info=True)
            await message.reply_text(
                "⚠️ Ошибка при создании кнопки. Попробуйте позже."
            )
            return
    
    message_text = (
        f"📋 Предложение создать задачу\n\n"
        f"👤 Постановщик: {creator_name}\n"
        f"🎯 Исполнитель: {responsible_name}\n"
        f"📝 Текст сообщения будет добавлен в описание задачи\n\n"
    )
    
    if not responsible_bitrix_id:
        message_text += "⚠️ Исполнитель не найден в Bitrix24. Вы сможете выбрать его в форме.\n\n"
    
    message_text += "Нажмите кнопку ниже, чтобы открыть форму создания задачи:"
    
    logger.info(f"Отправка сообщения с кнопкой создания задачи в чат {message.chat_id}")
    await message.reply_text(message_text, reply_markup=keyboard)
    logger.info("Сообщение с кнопкой успешно отправлено")


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


async def setup_menu_button(application: Application):
    """
    Настройка кнопки меню бота (Menu Button)
    Эта кнопка появляется внизу чата рядом с полем ввода
    и позволяет быстро открыть Mini App
    """
    try:
        webhook_url = os.getenv("WEBHOOK_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN")
        if webhook_url and not webhook_url.startswith("http"):
            webhook_url = f"https://{webhook_url}"
        
        if not webhook_url:
            logger.warning("WEBHOOK_URL не установлен, кнопка меню не будет настроена")
            return
        
        # Убираем завершающий слеш
        if webhook_url.endswith("/"):
            webhook_url = webhook_url.rstrip("/")
        
        # Создаем WebAppInfo для кнопки меню
        web_app_info = WebAppInfo(url=f"{webhook_url}/miniapp")
        
        # Устанавливаем кнопку меню как Web App
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="📋 Создать задачу",
                web_app=web_app_info
            )
        )
        logger.info("✅ Кнопка меню успешно установлена")
        logger.info(f"   URL Mini App: {webhook_url}/miniapp")
    except Exception as e:
        logger.error(f"❌ Ошибка при настройке кнопки меню: {e}", exc_info=True)
        logger.warning("Бот будет работать без кнопки меню")


async def setup_bot_commands(application: Application):
    """
    Настройка команд бота для доступа через меню прикрепления файлов
    Команды будут доступны в меню прикрепления (кнопка скрепки)
    """
    try:
        # Команды, которые будут доступны в меню прикрепления файлов
        commands = [
            BotCommand("start", "Начать работу с ботом"),
            BotCommand("create", "Создать задачу в Битрикс24"),
            BotCommand("help", "Показать справку"),
        ]
        
        # Устанавливаем команды для всех чатов
        await application.bot.set_my_commands(commands)
        logger.info("✅ Команды бота успешно установлены")
        logger.info("   Команды будут доступны в меню прикрепления файлов (кнопка скрепки)")
        
        # Также устанавливаем команды для меню прикрепления файлов
        # Это позволяет командам появляться в меню прикрепления
        try:
            await application.bot.set_my_commands(
                commands,
                scope=None,  # Для всех чатов
                language_code=None
            )
            logger.info("✅ Команды для меню прикрепления установлены")
        except Exception as scope_error:
            logger.warning(f"Не удалось установить команды для меню прикрепления: {scope_error}")
            logger.info("Команды все равно будут доступны через /")
            
    except Exception as e:
        logger.error(f"❌ Ошибка при настройке команд бота: {e}", exc_info=True)
        logger.warning("Бот будет работать без настроенных команд")




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
    # ВАЖНО: Обработчик reply-сообщений должен быть зарегистрирован ДО ConversationHandler,
    # чтобы он мог перехватить сообщения раньше
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("create", create_task_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("link", link_user))
    application.add_handler(CommandHandler("check_telegram_id", check_telegram_id))
    application.add_handler(CommandHandler("link_username", link_username))
    application.add_handler(CommandHandler("departments", departments_command))
    
    # Обработчик для reply-сообщений с упоминанием бота
    # Регистрируем ПЕРЕД ConversationHandler, чтобы он имел приоритет
    # Фильтр проверяет наличие reply и текста (или caption для медиа)
    # Проверка упоминания бота выполняется внутри функции
    application.add_handler(
        MessageHandler(
            filters.REPLY & (filters.TEXT | filters.Caption),
            handle_reply_with_mention
        )
    )
    
    # ConversationHandler для стандартного диалога создания задачи
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
                # Делаем это в фоне, чтобы сервер мог отвечать на health check сразу
                async def post_init(aio_app):
                    # Небольшая задержка, чтобы сервер успел запуститься и отвечать на health check
                    await asyncio.sleep(1)
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
                        
                        # Настраиваем кнопку меню (Menu Button)
                        await setup_menu_button(application)
                        
                        # Настраиваем команды бота для доступа через меню прикрепления файлов
                        await setup_bot_commands(application)
                    except Exception as init_error:
                        logger.error(f"КРИТИЧЕСКАЯ ОШИБКА при инициализации Telegram приложения: {init_error}", exc_info=True)
                        # Не поднимаем исключение, чтобы сервер продолжал работать
                        # Сервер должен работать даже если Telegram бот не инициализирован
                        logger.warning("Сервер продолжит работу, но Telegram бот может быть недоступен")
                
                async def post_shutdown(aio_app):
                    logger.info("post_shutdown вызван - остановка Telegram приложения...")
                    try:
                        # Проверяем, что приложение было запущено (безопасная проверка)
                        try:
                            is_running = application.running
                        except (AttributeError, RuntimeError):
                            is_running = False
                        
                        if is_running:
                            await application.stop()
                            await application.shutdown()
                            logger.info("Telegram приложение остановлено")
                        else:
                            logger.info("Telegram приложение не было запущено, пропускаем остановку")
                    except Exception as shutdown_error:
                        logger.error(f"Ошибка при остановке Telegram приложения: {shutdown_error}", exc_info=True)
                        # Не поднимаем исключение, чтобы сервер мог завершиться корректно
                
                # Обработчик для health check - должен отвечать быстро и без логирования
                # Этот endpoint должен быть доступен сразу после запуска сервера
                async def health_check(request):
                    # Не логируем health check запросы, чтобы не засорять логи
                    # Railway отправляет их очень часто (каждые несколько секунд)
                    # Важно: отвечаем быстро, даже если Telegram приложение еще не инициализировано
                    return web.Response(text='OK', status=200, headers={'Content-Type': 'text/plain'})
                
                # Обработчик для webhook от Telegram
                async def webhook_handler(request):
                    try:
                        # Проверяем, что приложение запущено (безопасная проверка)
                        try:
                            is_running = application.running
                        except (AttributeError, RuntimeError):
                            # Если приложение еще не запущено, возвращаем 503
                            logger.warning("Webhook запрос получен до запуска приложения")
                            return web.Response(text='Initializing', status=503)
                        
                        if not is_running:
                            logger.warning("Webhook запрос получен, но приложение не запущено")
                            return web.Response(text='Initializing', status=503)
                        
                        # Получаем данные от Telegram
                        data = await request.json()
                        update = Update.de_json(data, application.bot)
                        
                        # Обрабатываем обновление (Telegram ожидает быстрый ответ)
                        # Обработка происходит в фоне через application.process_update
                        await application.process_update(update)
                        
                        return web.Response(text='OK')
                    except Exception as e:
                        logger.error(f"Ошибка при обработке webhook: {e}", exc_info=True)
                        # Возвращаем 200, чтобы Telegram не повторял запрос
                        # Лучше обработать ошибку, чем получать повторные запросы
                        return web.Response(text='OK', status=200)
                
                # Обработчик для Mini App HTML
                async def miniapp_handler(request):
                    try:
                        # Читаем HTML файл
                        # Определяем путь к файлу относительно текущего скрипта
                        script_dir = os.path.dirname(os.path.abspath(__file__))
                        html_path = os.path.join(script_dir, 'static', 'miniapp.html')
                        
                        if not os.path.exists(html_path):
                            logger.error(f"HTML файл не найден: {html_path}")
                            return web.Response(text='Файл приложения не найден', status=404)
                        
                        with open(html_path, 'r', encoding='utf-8') as f:
                            html_content = f.read()
                        return web.Response(text=html_content, content_type='text/html')
                    except Exception as e:
                        logger.error(f"Ошибка при загрузке Mini App: {e}", exc_info=True)
                        return web.Response(text=f'Ошибка загрузки приложения: {str(e)}', status=500)
                
                # API: Получение данных сессии Mini App
                async def miniapp_session_handler(request):
                    try:
                        # Поддерживаем как GET (для токена), так и POST (для initData)
                        token = request.query.get('token')
                        
                        # Если токен указан, используем данные из сессии
                        if token:
                            session_key = f"miniapp_session_{token}"
                            session_data = application.bot_data.get(session_key)
                            
                            if not session_data:
                                return web.json_response({'error': 'Сессия не найдена или истекла'}, status=404)
                            
                            # Возвращаем данные без чувствительной информации
                            return web.json_response({
                                'creator_bitrix_id': session_data.get('creator_bitrix_id'),
                                'responsible_bitrix_id': session_data.get('responsible_bitrix_id'),
                                'original_message_text': session_data.get('original_message_text', ''),
                                'creator_name': session_data.get('creator_name', ''),
                                'responsible_name': session_data.get('responsible_name', ''),
                                'department_id': session_data.get('department_id'),
                                'thread_id': session_data.get('thread_id')
                            })
                        
                        # Если токен не указан, определяем пользователя из Telegram WebApp API
                        # Получаем initData из POST запроса или заголовков
                        init_data = None
                        if request.method == 'POST':
                            try:
                                post_data = await request.json()
                                init_data = post_data.get('initData')
                            except:
                                pass
                        
                        if not init_data:
                            init_data = request.query.get('initData') or request.headers.get('X-Telegram-Init-Data')
                        
                        if not init_data:
                            return web.json_response({'error': 'Токен или initData не указаны'}, status=400)
                        
                        # Парсим initData для получения Telegram User ID
                        # В реальности нужно проверить подпись initData, но для простоты парсим напрямую
                        try:
                            from urllib.parse import parse_qs, unquote
                            parsed_data = parse_qs(unquote(init_data))
                            user_data_str = parsed_data.get('user', [None])[0]
                            
                            if not user_data_str:
                                return web.json_response({'error': 'Данные пользователя не найдены в initData'}, status=400)
                            
                            import json
                            user_data = json.loads(user_data_str)
                            telegram_user_id = user_data.get('id')
                            
                            if not telegram_user_id:
                                return web.json_response({'error': 'Telegram User ID не найден'}, status=400)
                            
                            logger.info(f"Определение пользователя по Telegram ID: {telegram_user_id}")
                            
                            # Определяем Bitrix ID пользователя
                            creator_bitrix_id = TELEGRAM_TO_BITRIX_MAPPING.get(telegram_user_id)
                            if not creator_bitrix_id:
                                creator_info = bitrix_client.get_user_by_telegram_id(telegram_user_id)
                                if creator_info:
                                    creator_bitrix_id = int(creator_info.get("ID"))
                                    TELEGRAM_TO_BITRIX_MAPPING[telegram_user_id] = creator_bitrix_id
                                    logger.info(f"Пользователь найден в Bitrix24: {creator_bitrix_id}")
                            
                            if not creator_bitrix_id:
                                logger.warning(f"Пользователь {telegram_user_id} не найден в Bitrix24")
                                return web.json_response({
                                    'error': 'Пользователь не найден в Bitrix24',
                                    'error_code': 'USER_NOT_LINKED',
                                    'telegram_user_id': telegram_user_id
                                }, status=404)
                            
                            # Получаем информацию о пользователе
                            creator_info = bitrix_client.get_user_by_id(creator_bitrix_id)
                            creator_name = f"{creator_info.get('NAME', '')} {creator_info.get('LAST_NAME', '')}".strip() if creator_info else f"ID: {creator_bitrix_id}"
                            
                            logger.info(f"Пользователь определен: {creator_name} (ID: {creator_bitrix_id})")
                            
                            # Возвращаем данные для создания задачи от текущего пользователя
                            return web.json_response({
                                'creator_bitrix_id': creator_bitrix_id,
                                'responsible_bitrix_id': None,
                                'original_message_text': '',
                                'creator_name': creator_name,
                                'responsible_name': '',
                                'department_id': None,
                                'thread_id': None
                            })
                            
                        except (json.JSONDecodeError, KeyError, ValueError) as e:
                            logger.error(f"Ошибка парсинга initData: {e}", exc_info=True)
                            return web.json_response({'error': 'Ошибка парсинга данных пользователя'}, status=400)
                            
                    except Exception as e:
                        logger.error(f"Ошибка при получении сессии Mini App: {e}", exc_info=True)
                        return web.json_response({'error': 'Внутренняя ошибка сервера'}, status=500)
                
                # API: Получение списка пользователей
                async def miniapp_users_handler(request):
                    try:
                        # Получаем всех активных пользователей из Bitrix24
                        users = bitrix_client.get_all_users(active_only=True)
                        
                        # Форматируем список пользователей
                        users_list = []
                        for user in users:
                            name = f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
                            # Пропускаем пользователей без имени
                            if name:
                                users_list.append({
                                    'id': int(user.get('ID')),
                                    'name': name
                                })
                        
                        # Сортируем по имени для удобства
                        users_list.sort(key=lambda x: x['name'])
                        
                        return web.json_response(users_list)
                    except Exception as e:
                        logger.error(f"Ошибка при получении списка пользователей: {e}", exc_info=True)
                        return web.json_response({'error': 'Ошибка загрузки пользователей'}, status=500)
                
                # API: Получение списка подразделений
                async def miniapp_departments_handler(request):
                    try:
                        # Получаем все подразделения из Bitrix24
                        departments = bitrix_client.get_all_departments()
                        
                        # Форматируем список подразделений
                        departments_list = []
                        for dept in departments:
                            name = dept.get('NAME', '').strip()
                            # Пропускаем подразделения без имени
                            if name:
                                departments_list.append({
                                    'id': int(dept.get('ID')),
                                    'name': name
                                })
                        
                        # Сортируем по имени для удобства
                        departments_list.sort(key=lambda x: x['name'])
                        
                        return web.json_response(departments_list)
                    except Exception as e:
                        logger.error(f"Ошибка при получении списка подразделений: {e}", exc_info=True)
                        return web.json_response({'error': 'Ошибка загрузки подразделений'}, status=500)
                
                # API: Создание задачи из Mini App
                async def miniapp_create_task_handler(request):
                    try:
                        data = await request.json()
                        token = data.get('token')
                        
                        if not token:
                            return web.json_response({'error': 'Токен не указан'}, status=400)
                        
                        session_key = f"miniapp_session_{token}"
                        session_data = application.bot_data.get(session_key)
                        
                        if not session_data:
                            return web.json_response({'error': 'Сессия не найдена или истекла'}, status=404)
                        
                        # Получаем данные из запроса
                        title = data.get('title', '').strip()
                        creator_id = data.get('creator_id')
                        responsible_id = data.get('responsible_id')
                        deadline = data.get('deadline')
                        description = data.get('description', '').strip()
                        department_id = data.get('department_id')  # Может быть None
                        
                        if not title:
                            return web.json_response({'error': 'Название задачи обязательно'}, status=400)
                        if not creator_id:
                            return web.json_response({'error': 'Постановщик не указан'}, status=400)
                        if not responsible_id:
                            return web.json_response({'error': 'Исполнитель не указан'}, status=400)
                        
                        # Создаем задачу
                        result = bitrix_client.create_task(
                            title=title,
                            responsible_ids=[responsible_id],
                            creator_id=creator_id,
                            description=description,
                            deadline=deadline,
                            file_ids=None,
                            department_id=department_id
                        )
                        
                        if result.get("result") and result["result"].get("task"):
                            task_id = result["result"]["task"]["id"]
                            
                            # Получаем ссылку на задачу
                            task_url = bitrix_client.get_task_url(task_id, creator_id)
                            
                            # Получаем информацию о задаче для сообщения
                            responsible_info = bitrix_client.get_user_by_id(responsible_id)
                            responsible_name = ""
                            if responsible_info:
                                responsible_name = f"{responsible_info.get('NAME', '')} {responsible_info.get('LAST_NAME', '')}".strip()
                            
                            # Формируем текст сообщения
                            response_text = (
                                f"✅ Задача создана!\n\n"
                                f"📋 Задача: {title}\n"
                            )
                            
                            if responsible_name:
                                response_text += f"👤 Ответственный: {responsible_name}\n"
                            
                            if deadline:
                                response_text += f"📅 Срок: {deadline}\n"
                            
                            if description:
                                response_text += f"📝 Описание: {description[:100]}...\n" if len(description) > 100 else f"📝 Описание: {description}\n"
                            
                            response_text += f"🆔 ID задачи: {task_id}\n\n"
                            response_text += f"🔗 Ссылка на задачу: {task_url}"
                            
                            # Отправляем сообщение в чат с ссылкой на задачу
                            chat_id = session_data.get('chat_id')
                            message_id = session_data.get('message_id')
                            
                            if chat_id:
                                try:
                                    # Отправляем сообщение в чат
                                    await application.bot.send_message(
                                        chat_id=chat_id,
                                        text=response_text,
                                        reply_to_message_id=message_id
                                    )
                                    logger.info(f"Сообщение с ссылкой на задачу отправлено в чат {chat_id}")
                                except Exception as send_error:
                                    logger.error(f"Ошибка при отправке сообщения в чат: {send_error}", exc_info=True)
                                    # Продолжаем работу, даже если не удалось отправить сообщение
                            
                            # Удаляем сессию после успешного создания
                            if session_key in application.bot_data:
                                del application.bot_data[session_key]
                            
                            return web.json_response({
                                'success': True,
                                'task_id': task_id,
                                'task_url': task_url
                            })
                        else:
                            error_msg = result.get('error_description', 'Неизвестная ошибка')
                            return web.json_response({'error': f'Ошибка создания задачи: {error_msg}'}, status=500)
                            
                    except Exception as e:
                        logger.error(f"Ошибка при создании задачи из Mini App: {e}", exc_info=True)
                        return web.json_response({'error': 'Внутренняя ошибка сервера'}, status=500)
                
                # Регистрируем маршруты
                # ВАЖНО: health check должен быть зарегистрирован первым и отвечать сразу
                aio_app.router.add_get('/', health_check)
                aio_app.router.add_get('/health', health_check)
                aio_app.router.add_post(f'/{token}', webhook_handler)
                aio_app.router.add_get('/miniapp', miniapp_handler)
                aio_app.router.add_get('/api/miniapp/session', miniapp_session_handler)
                aio_app.router.add_post('/api/miniapp/session', miniapp_session_handler)  # Поддержка POST для initData
                aio_app.router.add_get('/api/miniapp/users', miniapp_users_handler)
                aio_app.router.add_get('/api/miniapp/departments', miniapp_departments_handler)
                aio_app.router.add_post('/api/miniapp/create-task', miniapp_create_task_handler)
                
                # Инициализируем приложение
                # Используем on_startup для инициализации Telegram в фоне
                aio_app.on_startup.append(post_init)
                aio_app.on_cleanup.append(post_shutdown)
                
                # Запускаем сервер используя явное управление event loop
                logger.info(f"Запуск aiohttp сервера на 0.0.0.0:{port}...")
                logger.info("Сервер будет работать до получения сигнала остановки...")
                
                # Используем явное управление event loop для лучшего контроля
                async def run():
                    runner = None
                    try:
                        # Создаем runner и запускаем сервер
                        runner = web.AppRunner(aio_app)
                        await runner.setup()
                        site = web.TCPSite(runner, '0.0.0.0', port)
                        await site.start()
                        logger.info(f"Сервер успешно запущен на 0.0.0.0:{port}")
                        logger.info("Health check endpoint доступен на / и /health")
                        logger.info("Сервер готов принимать запросы (Telegram приложение инициализируется в фоне)")
                        
                        # Ждем бесконечно - сервер будет работать до получения сигнала остановки
                        # Используем простой бесконечный цикл с периодическими проверками
                        import signal
                        shutdown_event = asyncio.Event()
                        
                        # Регистрируем обработчики сигналов через loop.add_signal_handler
                        loop = asyncio.get_running_loop()
                        
                        def handle_signal():
                            logger.info("Получен сигнал остановки")
                            shutdown_event.set()
                        
                        try:
                            # Используем add_signal_handler для правильной работы в async контексте
                            if hasattr(signal, 'SIGTERM'):
                                loop.add_signal_handler(signal.SIGTERM, handle_signal)
                            if hasattr(signal, 'SIGINT'):
                                loop.add_signal_handler(signal.SIGINT, handle_signal)
                        except (ValueError, OSError, RuntimeError) as sig_error:
                            logger.warning(f"Не удалось зарегистрировать обработчики сигналов через loop: {sig_error}")
                            logger.info("Используем альтернативный метод обработки сигналов")
                            # Fallback: используем стандартный signal.signal
                            try:
                                signal.signal(signal.SIGTERM, lambda s, f: shutdown_event.set())
                                signal.signal(signal.SIGINT, lambda s, f: shutdown_event.set())
                            except Exception as fallback_error:
                                logger.warning(f"Не удалось зарегистрировать обработчики сигналов: {fallback_error}")
                        
                        # Ждем сигнала остановки или работаем бесконечно
                        logger.info("Сервер работает. Ожидание сигнала остановки...")
                        logger.info("Сервер готов принимать запросы на порту %d", port)
                        logger.info("Health check доступен на / и /health - Railway может проверять статус")
                        try:
                            # Используем бесконечное ожидание с периодическими логами для диагностики
                            check_count = 0
                            while not shutdown_event.is_set():
                                await asyncio.sleep(60)  # Проверяем каждую минуту
                                check_count += 1
                                if check_count % 5 == 0:  # Каждые 5 минут
                                    logger.info(f"Сервер работает нормально (проверка #{check_count})")
                        except (asyncio.CancelledError, KeyboardInterrupt):
                            logger.info("Получен сигнал отмены (CancelledError/KeyboardInterrupt)")
                            shutdown_event.set()
                        except Exception as wait_error:
                            logger.error(f"Ошибка при ожидании сигнала: {wait_error}", exc_info=True)
                            # Fallback: бесконечное ожидание с периодическими проверками
                            logger.info("Переход на бесконечное ожидание...")
                            while not shutdown_event.is_set():
                                try:
                                    await asyncio.sleep(60)  # Проверяем каждую минуту
                                except (asyncio.CancelledError, KeyboardInterrupt):
                                    logger.info("Получен сигнал отмены в fallback цикле")
                                    shutdown_event.set()
                                    break
                    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                        logger.info("Получен сигнал остановки (KeyboardInterrupt/SystemExit/CancelledError)")
                        shutdown_event.set()
                    except Exception as e:
                        logger.error(f"Критическая ошибка при работе сервера: {e}", exc_info=True)
                        # Не поднимаем исключение, чтобы сервер продолжал работать
                        # Railway может перезапустить контейнер, если нужно
                        logger.warning("Сервер продолжит работу после ошибки")
                    finally:
                        if runner:
                            logger.info("Остановка сервера...")
                            try:
                                await runner.cleanup()
                                logger.info("Runner успешно очищен")
                            except Exception as cleanup_error:
                                logger.error(f"Ошибка при очистке runner: {cleanup_error}")
                
                # Запускаем event loop
                try:
                    logger.info("Запуск основного event loop...")
                    asyncio.run(run())
                    logger.info("Event loop завершен")
                except KeyboardInterrupt:
                    logger.info("Получен KeyboardInterrupt на верхнем уровне")
                except SystemExit:
                    logger.info("Получен SystemExit на верхнем уровне")
                except Exception as run_error:
                    logger.error(f"Ошибка при запуске сервера: {run_error}", exc_info=True)
                    # Не поднимаем исключение, чтобы Railway мог перезапустить контейнер
                    logger.error("Сервер завершился с ошибкой. Railway перезапустит контейнер.")
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
        
        # Настраиваем кнопку меню и команды перед запуском polling
        async def post_init_polling(app: Application):
            await setup_menu_button(app)
            await setup_bot_commands(app)
        
        application.post_init = post_init_polling
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
