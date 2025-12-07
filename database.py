"""
Модуль для работы с PostgreSQL базой данных
"""
import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from typing import Dict, Optional, List
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
        return
    
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
        logger.info("✅ Пул соединений с PostgreSQL успешно инициализирован")
    except Exception as e:
        logger.error(f"❌ Ошибка при инициализации пула соединений PostgreSQL: {e}", exc_info=True)
        _connection_pool = None


@contextmanager
def get_db_connection():
    """Контекстный менеджер для получения соединения с БД"""
    if _connection_pool is None:
        raise RuntimeError("Пул соединений не инициализирован. Вызовите init_connection_pool() сначала.")
    
    conn = _connection_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _connection_pool.putconn(conn)


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
                
                # Создаем индексы для быстрого поиска
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_telegram_to_bitrix_bitrix_id 
                    ON telegram_to_bitrix(bitrix_user_id)
                """)
                
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_username_to_bitrix_bitrix_id 
                    ON username_to_bitrix(bitrix_user_id)
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


def set_telegram_to_bitrix_mapping(telegram_id: int, bitrix_user_id: int) -> bool:
    """Сохранение маппинга Telegram ID -> Bitrix24 User ID"""
    if _connection_pool is None:
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
                conn.commit()
                return True
    except Exception as e:
        logger.debug(f"Ошибка при сохранении маппинга Telegram {telegram_id} -> Bitrix {bitrix_user_id}: {e}")
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
