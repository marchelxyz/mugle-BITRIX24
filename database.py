"""
Модуль для работы с PostgreSQL базой данных
"""
import os
import logging
import json
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import ThreadedConnectionPool
from typing import Dict, Optional, List, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Пул соединений для PostgreSQL
_connection_pool: Optional[ThreadedConnectionPool] = None


def get_database_url() -> Optional[str]:
    """
    Получение URL базы данных из переменных окружения
    
    Render предоставляет DATABASE_URL напрямую
    """
    # Сначала пробуем DATABASE_URL (стандарт для Render)
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    
    # Если нет DATABASE_URL, пробуем собрать из отдельных переменных
    db_host = os.getenv("POSTGRES_HOST")
    db_port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB")
    db_user = os.getenv("POSTGRES_USER")
    db_password = os.getenv("POSTGRES_PASSWORD")
    
    if all([db_host, db_name, db_user, db_password]):
        return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    
    return None


def init_connection_pool():
    """Инициализация пула соединений с PostgreSQL"""
    global _connection_pool
    
    if _connection_pool is not None:
        # Проверяем, что пул все еще работает
        try:
            test_conn = _connection_pool.getconn()
            _connection_pool.putconn(test_conn)
            return
        except Exception as e:
            logger.warning(f"⚠️ Пул соединений недоступен, переинициализация: {e}")
            _connection_pool = None
    
    database_url = get_database_url()
    if not database_url:
        logger.warning("⚠️ DATABASE_URL не установлен. PostgreSQL функции будут недоступны.")
        return
    
    try:
        # Render использует SSL для подключения к PostgreSQL
        # Преобразуем postgres:// в postgresql:// и добавляем SSL параметры
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        
        # Создаем пул соединений (минимум 1, максимум 5 соединений)
        _connection_pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=database_url,
            connect_timeout=10
        )
        # Проверяем соединение, получая и возвращая одно соединение
        test_conn = _connection_pool.getconn()
        _connection_pool.putconn(test_conn)
        logger.info("✅ Пул соединений с PostgreSQL успешно инициализирован и проверен")
    except Exception as e:
        logger.error(f"❌ Ошибка при инициализации пула соединений PostgreSQL: {e}", exc_info=True)
        _connection_pool = None


@contextmanager
def get_db_connection():
    """Контекстный менеджер для получения соединения с БД"""
    if _connection_pool is None:
        raise RuntimeError("Пул соединений не инициализирован. Вызовите init_connection_pool() сначала.")
    
    conn = None
    try:
        conn = _connection_pool.getconn()
        yield conn
        conn.commit()
    except psycopg2.OperationalError as e:
        logger.error(f"❌ Ошибка подключения к PostgreSQL: {e}", exc_info=True)
        if conn:
            try:
                conn.rollback()
            except:
                pass
        raise
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        raise
    finally:
        if conn:
            try:
                _connection_pool.putconn(conn)
            except Exception as e:
                logger.error(f"❌ Ошибка при возврате соединения в пул: {e}", exc_info=True)


def init_database():
    """Инициализация базы данных - создание таблиц если их нет"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Таблица для маппинга Telegram ID -> Bitrix24 User ID
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS telegram_to_bitrix (
                        telegram_id BIGINT PRIMARY KEY,
                        bitrix_user_id INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Таблица для маппинга Telegram username -> Bitrix24 User ID
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS username_to_bitrix (
                        telegram_username VARCHAR(255) PRIMARY KEY,
                        bitrix_user_id INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Таблица для маппинга Telegram thread_id -> Bitrix24 Department ID
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS thread_to_department (
                        thread_id BIGINT PRIMARY KEY,
                        department_id INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Таблица для отслеживания отправленных уведомлений о задачах
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS task_notifications (
                        id SERIAL PRIMARY KEY,
                        notification_key VARCHAR(255) UNIQUE NOT NULL,
                        task_id INTEGER NOT NULL,
                        notification_type VARCHAR(50) NOT NULL,
                        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        extra_data TEXT
                    )
                """)
                
                # Таблица для хранения состояния задач (для определения изменений)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS task_states (
                        task_id INTEGER PRIMARY KEY,
                        status VARCHAR(50),
                        deadline TIMESTAMP,
                        responsible_id INTEGER,
                        title VARCHAR(500),
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        state_json JSONB
                    )
                """)
                
                # Таблица для хранения всех данных исходящих вебхуков от Bitrix24
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS webhook_events (
                        id SERIAL PRIMARY KEY,
                        event VARCHAR(100) NOT NULL,
                        event_handler_id VARCHAR(50),
                        received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        data_json JSONB NOT NULL,
                        ts VARCHAR(50),
                        auth_domain VARCHAR(255),
                        auth_member_id VARCHAR(255),
                        auth_application_token VARCHAR(255)
                    )
                """)
                
                # Создаем индексы для быстрого поиска
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_telegram_to_bitrix_bitrix_id 
                    ON telegram_to_bitrix(bitrix_user_id)
                """)
                
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_username_to_bitrix_bitrix_id 
                    ON username_to_bitrix(bitrix_user_id)
                """)
                
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_task_notifications_key 
                    ON task_notifications(notification_key)
                """)
                
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_task_notifications_task_id 
                    ON task_notifications(task_id)
                """)
                
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_task_states_task_id 
                    ON task_states(task_id)
                """)
                
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_task_states_updated_at 
                    ON task_states(updated_at)
                """)
                
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_webhook_events_event 
                    ON webhook_events(event)
                """)
                
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_webhook_events_received_at 
                    ON webhook_events(received_at)
                """)
                
                conn.commit()
                logger.info("✅ Таблицы базы данных успешно созданы/проверены")
    except Exception as e:
        logger.error(f"❌ Ошибка при инициализации базы данных: {e}", exc_info=True)
        raise


# Функции для работы с telegram_to_bitrix маппингом

def get_bitrix_user_id_by_telegram_id(telegram_id: int) -> Optional[int]:
    """Получение Bitrix24 User ID по Telegram ID"""
    if _connection_pool is None:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT bitrix_user_id FROM telegram_to_bitrix WHERE telegram_id = %s",
                    (telegram_id,)
                )
                result = cur.fetchone()
                return result[0] if result else None
    except Exception as e:
        logger.debug(f"Ошибка при получении Bitrix ID по Telegram ID {telegram_id}: {e}")
        return None


def get_telegram_id_by_bitrix_id(bitrix_user_id: int) -> Optional[int]:
    """Получение Telegram ID по Bitrix24 User ID"""
    if _connection_pool is None:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT telegram_id FROM telegram_to_bitrix WHERE bitrix_user_id = %s",
                    (bitrix_user_id,)
                )
                result = cur.fetchone()
                return result[0] if result else None
    except Exception as e:
        logger.debug(f"Ошибка при получении Telegram ID по Bitrix ID {bitrix_user_id}: {e}")
        return None


def set_telegram_to_bitrix_mapping(telegram_id: int, bitrix_user_id: int) -> bool:
    """Сохранение маппинга Telegram ID -> Bitrix24 User ID"""
    global _connection_pool
    if _connection_pool is None:
        logger.warning(f"⚠️ Пул соединений PostgreSQL не инициализирован. Попытка переинициализации...")
        init_connection_pool()
        if _connection_pool is None:
            logger.warning(f"⚠️ Не удалось инициализировать пул соединений. Не удалось сохранить связь Telegram {telegram_id} -> Bitrix {bitrix_user_id}")
            return False
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO telegram_to_bitrix (telegram_id, bitrix_user_id, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (telegram_id) 
                    DO UPDATE SET bitrix_user_id = EXCLUDED.bitrix_user_id, updated_at = CURRENT_TIMESTAMP
                """, (telegram_id, bitrix_user_id))
                # Коммит выполняется автоматически контекстным менеджером get_db_connection
                logger.info(f"✅ Связь сохранена в PostgreSQL: Telegram {telegram_id} -> Bitrix {bitrix_user_id}")
                return True
    except RuntimeError as e:
        logger.error(f"❌ Ошибка при получении соединения с PostgreSQL: {e}", exc_info=True)
        # Пытаемся переинициализировать пул
        logger.info("Попытка переинициализации пула соединений...")
        _connection_pool = None
        init_connection_pool()
        return False
    except psycopg2.OperationalError as e:
        logger.error(f"❌ Ошибка подключения к PostgreSQL: {e}", exc_info=True)
        # Пытаемся переинициализировать пул
        logger.info("Попытка переинициализации пула соединений после операционной ошибки...")
        _connection_pool = None
        init_connection_pool()
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении маппинга Telegram {telegram_id} -> Bitrix {bitrix_user_id}: {e}", exc_info=True)
        logger.error(f"   Тип ошибки: {type(e).__name__}")
        return False


def get_all_telegram_to_bitrix_mappings() -> Dict[int, int]:
    """Получение всех маппингов Telegram ID -> Bitrix24 User ID"""
    if _connection_pool is None:
        return {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_id, bitrix_user_id FROM telegram_to_bitrix")
                results = cur.fetchall()
                return {row[0]: row[1] for row in results}
    except Exception as e:
        logger.debug(f"Ошибка при получении всех маппингов telegram_to_bitrix: {e}")
        return {}


def delete_telegram_to_bitrix_mapping(telegram_id: int) -> bool:
    """Удаление маппинга Telegram ID -> Bitrix24 User ID"""
    if _connection_pool is None:
        return False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM telegram_to_bitrix WHERE telegram_id = %s", (telegram_id,))
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        logger.debug(f"Ошибка при удалении маппинга Telegram {telegram_id}: {e}")
        return False


# Функции для работы с username_to_bitrix маппингом

def get_bitrix_user_id_by_username(telegram_username: str) -> Optional[int]:
    """Получение Bitrix24 User ID по Telegram username"""
    if _connection_pool is None:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT bitrix_user_id FROM username_to_bitrix WHERE telegram_username = %s",
                    (telegram_username.lower(),)
                )
                result = cur.fetchone()
                return result[0] if result else None
    except Exception as e:
        logger.debug(f"Ошибка при получении Bitrix ID по username {telegram_username}: {e}")
        return None


def set_username_to_bitrix_mapping(telegram_username: str, bitrix_user_id: int) -> bool:
    """Сохранение маппинга Telegram username -> Bitrix24 User ID"""
    if _connection_pool is None:
        return False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO username_to_bitrix (telegram_username, bitrix_user_id, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (telegram_username) 
                    DO UPDATE SET bitrix_user_id = EXCLUDED.bitrix_user_id, updated_at = CURRENT_TIMESTAMP
                """, (telegram_username.lower(), bitrix_user_id))
                conn.commit()
                return True
    except Exception as e:
        logger.debug(f"Ошибка при сохранении маппинга username {telegram_username} -> Bitrix {bitrix_user_id}: {e}")
        return False


def get_all_username_to_bitrix_mappings() -> Dict[str, int]:
    """Получение всех маппингов Telegram username -> Bitrix24 User ID"""
    if _connection_pool is None:
        return {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_username, bitrix_user_id FROM username_to_bitrix")
                results = cur.fetchall()
                return {row[0]: row[1] for row in results}
    except Exception as e:
        logger.debug(f"Ошибка при получении всех маппингов username_to_bitrix: {e}")
        return {}


# Функции для работы с thread_to_department маппингом

def get_department_id_by_thread_id(thread_id: int) -> Optional[int]:
    """Получение Bitrix24 Department ID по Telegram thread_id"""
    if _connection_pool is None:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT department_id FROM thread_to_department WHERE thread_id = %s",
                    (thread_id,)
                )
                result = cur.fetchone()
                return result[0] if result else None
    except Exception as e:
        logger.debug(f"Ошибка при получении Department ID по thread_id {thread_id}: {e}")
        return None


def set_thread_to_department_mapping(thread_id: int, department_id: int) -> bool:
    """Сохранение маппинга Telegram thread_id -> Bitrix24 Department ID"""
    if _connection_pool is None:
        return False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO thread_to_department (thread_id, department_id, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (thread_id) 
                    DO UPDATE SET department_id = EXCLUDED.department_id, updated_at = CURRENT_TIMESTAMP
                """, (thread_id, department_id))
                conn.commit()
                return True
    except Exception as e:
        logger.debug(f"Ошибка при сохранении маппинга thread {thread_id} -> department {department_id}: {e}")
        return False


def get_all_thread_to_department_mappings() -> Dict[int, int]:
    """Получение всех маппингов Telegram thread_id -> Bitrix24 Department ID"""
    if _connection_pool is None:
        return {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT thread_id, department_id FROM thread_to_department")
                results = cur.fetchall()
                return {row[0]: row[1] for row in results}
    except Exception as e:
        logger.debug(f"Ошибка при получении всех маппингов thread_to_department: {e}")
        return {}


def delete_thread_to_department_mapping(thread_id: int) -> bool:
    """Удаление маппинга Telegram thread_id -> Bitrix24 Department ID"""
    if _connection_pool is None:
        return False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM thread_to_department WHERE thread_id = %s", (thread_id,))
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        logger.debug(f"Ошибка при удалении маппинга thread {thread_id}: {e}")
        return False


# Функции для работы с уведомлениями о задачах

def was_notification_sent(notification_key: str) -> bool:
    """Проверка, было ли уже отправлено уведомление"""
    if _connection_pool is None:
        return False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM task_notifications WHERE notification_key = %s",
                    (notification_key,)
                )
                result = cur.fetchone()
                return result is not None
    except Exception as e:
        logger.debug(f"Ошибка при проверке уведомления {notification_key}: {e}")
        return False


def mark_notification_sent(notification_key: str, task_id: int, notification_type: str, extra_data: str = None) -> bool:
    """Отметить уведомление как отправленное"""
    if _connection_pool is None:
        return False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO task_notifications (notification_key, task_id, notification_type, extra_data)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (notification_key) DO NOTHING
                """, (notification_key, task_id, notification_type, extra_data))
                conn.commit()
                return True
    except Exception as e:
        logger.debug(f"Ошибка при сохранении уведомления {notification_key}: {e}")
        return False


def get_notification_history(task_id: int = None, notification_type: str = None, limit: int = 100) -> List[Dict]:
    """Получение истории уведомлений"""
    if _connection_pool is None:
        return []
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = "SELECT * FROM task_notifications WHERE 1=1"
                params = []
                
                if task_id:
                    query += " AND task_id = %s"
                    params.append(task_id)
                
                if notification_type:
                    query += " AND notification_type = %s"
                    params.append(notification_type)
                
                query += " ORDER BY sent_at DESC LIMIT %s"
                params.append(limit)
                
                cur.execute(query, params)
                return cur.fetchall()
    except Exception as e:
        logger.debug(f"Ошибка при получении истории уведомлений: {e}")
        return []


# Функции для работы с данными вебхуков

def save_webhook_event(event: str, data: Dict[str, Any], event_handler_id: str = None, ts: str = None) -> bool:
    """
    Сохранение полного массива данных исходящего вебхука от Bitrix24
    
    Args:
        event: Тип события (например, ONTASKUPDATE, ONTASKCOMMENTADD)
        data: Полный словарь данных вебхука (включая data, auth и другие поля)
        event_handler_id: ID обработчика события (опционально)
        ts: Timestamp события (опционально)
        
    Returns:
        True если данные успешно сохранены, False в противном случае
    """
    if _connection_pool is None:
        logger.warning("⚠️ Пул соединений PostgreSQL не инициализирован. Не удалось сохранить данные вебхука.")
        return False
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Извлекаем данные из auth для удобного поиска
                auth_data = data.get('auth', {}) if isinstance(data.get('auth'), dict) else {}
                auth_domain = auth_data.get('domain')
                auth_member_id = auth_data.get('member_id')
                auth_application_token = auth_data.get('application_token')
                
                # Сохраняем полный массив данных как JSONB
                cur.execute("""
                    INSERT INTO webhook_events (
                        event, 
                        event_handler_id, 
                        data_json, 
                        ts, 
                        auth_domain, 
                        auth_member_id, 
                        auth_application_token
                    )
                    VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)
                """, (
                    event,
                    event_handler_id,
                    Json(data),  # Используем Json адаптер для правильной сериализации
                    ts,
                    auth_domain,
                    auth_member_id,
                    auth_application_token
                ))
                conn.commit()
                logger.debug(f"✅ Данные вебхука сохранены в БД: событие {event}")
                return True
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении данных вебхука в БД: {e}", exc_info=True)
        return False


def get_webhook_events(event: str = None, limit: int = 100, offset: int = 0) -> List[Dict]:
    """
    Получение истории вебхуков из базы данных
    
    Args:
        event: Фильтр по типу события (опционально)
        limit: Максимальное количество записей
        offset: Смещение для пагинации
        
    Returns:
        Список словарей с данными вебхуков
    """
    if _connection_pool is None:
        return []
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = "SELECT * FROM webhook_events WHERE 1=1"
                params = []
                
                if event:
                    query += " AND event = %s"
                    params.append(event)
                
                query += " ORDER BY received_at DESC LIMIT %s OFFSET %s"
                params.extend([limit, offset])
                
                cur.execute(query, params)
                return cur.fetchall()
    except Exception as e:
        logger.debug(f"Ошибка при получении истории вебхуков: {e}")
        return []


# Функции для работы с состоянием задач

def get_task_state(task_id: int) -> Optional[Dict]:
    """Получение сохраненного состояния задачи"""
    if _connection_pool is None:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM task_states WHERE task_id = %s",
                    (task_id,)
                )
                result = cur.fetchone()
                if result:
                    # Преобразуем в обычный dict и извлекаем state_json если есть
                    state = dict(result)
                    if state.get('state_json'):
                        # Объединяем данные из state_json с основными полями
                        json_data = state.pop('state_json')
                        if isinstance(json_data, dict):
                            state.update(json_data)
                    return state
                return None
    except Exception as e:
        logger.debug(f"Ошибка при получении состояния задачи {task_id}: {e}")
        return None


def save_task_state(task_id: int, task_data: Dict) -> bool:
    """Сохранение состояния задачи для последующего сравнения"""
    if _connection_pool is None:
        return False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Извлекаем ключевые поля
                status = task_data.get('status') or task_data.get('STATUS')
                deadline = task_data.get('deadline') or task_data.get('DEADLINE')
                responsible_id = task_data.get('responsibleId') or task_data.get('RESPONSIBLE_ID')
                title = task_data.get('title') or task_data.get('TITLE')
                
                # Сохраняем полные данные в JSONB
                cur.execute("""
                    INSERT INTO task_states (
                        task_id, status, deadline, responsible_id, title, updated_at, state_json
                    )
                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s::jsonb)
                    ON CONFLICT (task_id) 
                    DO UPDATE SET 
                        status = EXCLUDED.status,
                        deadline = EXCLUDED.deadline,
                        responsible_id = EXCLUDED.responsible_id,
                        title = EXCLUDED.title,
                        updated_at = CURRENT_TIMESTAMP,
                        state_json = EXCLUDED.state_json
                """, (
                    task_id,
                    str(status) if status else None,
                    deadline,
                    int(responsible_id) if responsible_id else None,
                    str(title)[:500] if title else None,
                    Json(task_data)  # Сохраняем полные данные
                ))
                conn.commit()
                return True
    except Exception as e:
        logger.debug(f"Ошибка при сохранении состояния задачи {task_id}: {e}")
        return False
