"""
Модуль для отслеживания задач в Bitrix24 и отправки уведомлений в Telegram
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from bitrix24_client import Bitrix24Client

try:
    import database
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

logger = logging.getLogger(__name__)


class TaskNotificationService:
    """Сервис для отслеживания задач и отправки уведомлений"""
    
    def __init__(self, bitrix_client: Bitrix24Client, telegram_bot, telegram_group_id: int, telegram_thread_id: Optional[int] = None):
        """
        Инициализация сервиса уведомлений
        
        Args:
            bitrix_client: Клиент для работы с Bitrix24 API
            telegram_bot: Экземпляр Telegram бота для отправки сообщений
            telegram_group_id: ID Telegram супергруппы для отправки уведомлений
            telegram_thread_id: ID топика (thread) в группе для отправки уведомлений (опционально)
        """
        self.bitrix_client = bitrix_client
        self.telegram_bot = telegram_bot
        self.telegram_group_id = telegram_group_id
        self.telegram_thread_id = telegram_thread_id
        
        if telegram_thread_id:
            logger.info(f"✅ TaskNotificationService инициализирован для группы {telegram_group_id}, топик {telegram_thread_id}")
        else:
            logger.info(f"✅ TaskNotificationService инициализирован для группы {telegram_group_id}")
        
        # Настройки уведомлений из переменных окружения
        self.check_interval_minutes = int(os.getenv("TASK_NOTIFICATION_CHECK_INTERVAL", "60"))  # По умолчанию каждый час
        self.deadline_warning_hours = int(os.getenv("TASK_DEADLINE_WARNING_HOURS", "24"))  # За сколько часов предупреждать
        self.enable_overdue_notifications = os.getenv("ENABLE_OVERDUE_NOTIFICATIONS", "true").lower() == "true"
        self.enable_deadline_warnings = os.getenv("ENABLE_DEADLINE_WARNINGS", "true").lower() == "true"
        self.enable_comment_notifications = os.getenv("ENABLE_COMMENT_NOTIFICATIONS", "true").lower() == "true"
        
        # Используем БД для отслеживания отправленных уведомлений
        self.use_database = DATABASE_AVAILABLE
        # Fallback: множество для отслеживания в памяти, если БД недоступна
        self.sent_notifications: Set[str] = set()
    
    def _get_notification_key(self, task_id: int, notification_type: str, extra: str = "") -> str:
        """
        Генерация уникального ключа для уведомления
        
        Args:
            task_id: ID задачи
            notification_type: Тип уведомления (overdue, deadline_warning, comment)
            extra: Дополнительная информация (например, ID комментария)
            
        Returns:
            Уникальный ключ уведомления
        """
        return f"{task_id}_{notification_type}_{extra}"
    
    def _was_notification_sent(self, notification_key: str) -> bool:
        """Проверка, было ли уже отправлено уведомление"""
        if self.use_database:
            return database.was_notification_sent(notification_key)
        return notification_key in self.sent_notifications
    
    def _mark_notification_sent(self, notification_key: str, task_id: int, notification_type: str, extra_data: str = None):
        """Отметить уведомление как отправленное"""
        if self.use_database:
            database.mark_notification_sent(notification_key, task_id, notification_type, extra_data)
        else:
            self.sent_notifications.add(notification_key)
    
    async def _get_telegram_username(self, telegram_id: int) -> Optional[str]:
        """
        Получение Telegram username пользователя из чата
        
        Args:
            telegram_id: Telegram ID пользователя
            
        Returns:
            Username пользователя или None
        """
        try:
            chat_member = await self.telegram_bot.get_chat_member(
                chat_id=self.telegram_group_id,
                user_id=telegram_id
            )
            username = chat_member.user.username
            if username:
                return username
        except Exception as e:
            logger.debug(f"Не удалось получить username для пользователя {telegram_id}: {e}")
        return None
    
    async def _send_notification(self, message: str, user_telegram_ids: Optional[List[int]] = None):
        """
        Отправка уведомления в Telegram супергруппу
        
        Args:
            message: Текст сообщения
            user_telegram_ids: Список Telegram ID пользователей для упоминания через @username (опционально)
        """
        try:
            if self.telegram_thread_id:
                logger.info(f"📨 Подготовка уведомления для группы {self.telegram_group_id}, топик {self.telegram_thread_id}")
            else:
                logger.info(f"📨 Подготовка уведомления для группы {self.telegram_group_id}")
            logger.debug(f"Сообщение: {message}")
            logger.debug(f"Telegram ID пользователей для упоминания: {user_telegram_ids}")
            
            # Формируем текст с упоминанием пользователей через @username
            if user_telegram_ids:
                mentions = []
                for telegram_id in user_telegram_ids:
                    username = await self._get_telegram_username(telegram_id)
                    if username:
                        mentions.append(f"@{username}")
                    else:
                        # Если username нет, используем формат с user_id
                        try:
                            chat_member = await self.telegram_bot.get_chat_member(
                                chat_id=self.telegram_group_id,
                                user_id=telegram_id
                            )
                            user_name = chat_member.user.first_name or f"Пользователь {telegram_id}"
                            mentions.append(f"<a href='tg://user?id={telegram_id}'>{user_name}</a>")
                        except Exception:
                            mentions.append(f"<a href='tg://user?id={telegram_id}'>Пользователь</a>")
                
                if mentions:
                    mentions_str = ", ".join(mentions)
                    full_message = f"{mentions_str}, {message}"
                else:
                    full_message = message
            else:
                full_message = message
            
            logger.info(f"📤 Отправка сообщения в группу {self.telegram_group_id}...")
            if self.telegram_thread_id:
                logger.info(f"   Топик (thread_id): {self.telegram_thread_id}")
            logger.debug(f"Текст сообщения: {full_message}")
            
            # Формируем параметры для отправки сообщения
            send_params = {
                'chat_id': self.telegram_group_id,
                'text': full_message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': False
            }
            
            # Если указан thread_id, добавляем его для отправки в топик форума
            if self.telegram_thread_id:
                send_params['message_thread_id'] = self.telegram_thread_id
            
            # Пробуем отправить сообщение
            try:
                result = await self.telegram_bot.send_message(**send_params)
                if self.telegram_thread_id:
                    logger.info(f"✅ Уведомление успешно отправлено в группу {self.telegram_group_id}, топик {self.telegram_thread_id} (message_id: {result.message_id})")
                else:
                    logger.info(f"✅ Уведомление успешно отправлено в группу {self.telegram_group_id} (message_id: {result.message_id})")
            except Exception as send_error:
                # Если ошибка связана с thread_id (топик не найден), пробуем отправить без thread_id
                error_str = str(send_error)
                if 'thread' in error_str.lower() or 'Message thread not found' in error_str:
                    logger.warning(f"⚠️ Топик {self.telegram_thread_id} не найден, пробуем отправить без топика")
                    # Убираем thread_id и пробуем снова
                    send_params_without_thread = send_params.copy()
                    send_params_without_thread.pop('message_thread_id', None)
                    result = await self.telegram_bot.send_message(**send_params_without_thread)
                    logger.info(f"✅ Уведомление успешно отправлено в группу {self.telegram_group_id} без топика (message_id: {result.message_id})")
                else:
                    # Если другая ошибка, пробрасываем её дальше
                    raise
        except Exception as e:
            if self.telegram_thread_id:
                logger.error(f"❌ Ошибка при отправке уведомления в группу {self.telegram_group_id}, топик {self.telegram_thread_id}: {e}", exc_info=True)
            else:
                logger.error(f"❌ Ошибка при отправке уведомления в группу {self.telegram_group_id}: {e}", exc_info=True)
            logger.error(f"   Тип ошибки: {type(e).__name__}")
            logger.error(f"   Сообщение: {message}")
            logger.error(f"   Telegram ID пользователей: {user_telegram_ids}")
            if self.telegram_thread_id:
                logger.error(f"   Thread ID (топик): {self.telegram_thread_id}")
            # Пробуем получить информацию о группе для диагностики
            try:
                chat_info = await self.telegram_bot.get_chat(chat_id=self.telegram_group_id)
                logger.error(f"   Информация о группе: {chat_info.title} (тип: {chat_info.type})")
            except Exception as chat_error:
                logger.error(f"   Не удалось получить информацию о группе: {chat_error}")
    
    async def check_overdue_tasks(self):
        """Проверка просроченных задач"""
        if not self.enable_overdue_notifications:
            return
        
        try:
            logger.info("🔍 Проверка просроченных задач...")
            
            # Получаем все задачи с просроченным дедлайном
            # Используем фильтр по DEADLINE < текущая дата и STATUS != завершена
            now = datetime.now()
            
            # Пробуем разные форматы даты для совместимости с Bitrix24
            deadline_filter_with_time = now.strftime('%Y-%m-%d %H:%M:%S')
            deadline_filter_date_only = now.strftime('%Y-%m-%d')
            
            logger.info(f"   Текущее время: {now}")
            logger.info(f"   Фильтр: DEADLINE < {deadline_filter_with_time}, STATUS != 5")
            
            # Bitrix24 использует формат фильтров через операторы
            # Для просроченных задач: DEADLINE < текущая дата и STATUS не равен 5 (завершена)
            # Пробуем сначала с полным форматом даты и времени
            tasks = self.bitrix_client.get_tasks(
                filter_params={
                    "<DEADLINE": deadline_filter_with_time,
                    "!STATUS": "5"  # Исключаем завершенные задачи (статус 5 = завершена)
                }
            )
            
            logger.info(f"   Найдено задач с просроченным дедлайном (фильтр с временем): {len(tasks)}")
            
            # Если не найдено задач, пробуем фильтр только по дате (без времени)
            if len(tasks) == 0:
                logger.info(f"   Попытка фильтра только по дате: DEADLINE < {deadline_filter_date_only}")
                tasks = self.bitrix_client.get_tasks(
                    filter_params={
                        "<DEADLINE": deadline_filter_date_only,
                        "!STATUS": "5"
                    }
                )
                logger.info(f"   Найдено задач с просроченным дедлайном (фильтр только по дате): {len(tasks)}")
            
            # Если все еще нет задач, получаем все незавершенные задачи и фильтруем в коде
            if len(tasks) == 0:
                logger.info("   Получение всех незавершенных задач для фильтрации в коде...")
                all_tasks = self.bitrix_client.get_tasks(
                    filter_params={
                        "!STATUS": "5"
                    }
                )
                logger.info(f"   Получено незавершенных задач: {len(all_tasks)}")
                
                # Фильтруем задачи с просроченным дедлайном в коде
                tasks = []
                for task in all_tasks:
                    deadline_str = task.get("deadline")
                    if deadline_str:
                        try:
                            # Парсим дату дедлайна
                            if 'T' in deadline_str or 'Z' in deadline_str:
                                deadline_dt = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                                if deadline_dt.tzinfo:
                                    deadline_dt = deadline_dt.replace(tzinfo=None)
                            else:
                                deadline_dt = datetime.strptime(deadline_str, '%Y-%m-%d %H:%M:%S')
                            
                            # Проверяем, просрочена ли задача
                            if deadline_dt < now:
                                tasks.append(task)
                        except Exception as date_error:
                            logger.debug(f"   Ошибка при парсинге даты дедлайна задачи {task.get('id')}: {date_error}")
                
                logger.info(f"   Найдено просроченных задач после фильтрации в коде: {len(tasks)}")
            
            for task in tasks:
                task_id = task.get("id")
                deadline_str = task.get("deadline")
                responsible_id = task.get("responsibleId")
                
                logger.debug(f"   Обработка задачи {task_id}: дедлайн={deadline_str}, ответственный={responsible_id}")
                
                if not task_id or not deadline_str:
                    logger.debug(f"   Пропуск задачи {task_id}: отсутствует ID или дедлайн")
                    continue
                
                # Проверяем, не отправляли ли уже уведомление
                notification_key = self._get_notification_key(task_id, "overdue")
                if self._was_notification_sent(notification_key):
                    logger.debug(f"   Пропуск задачи {task_id}: уведомление уже отправлено")
                    continue
                
                logger.info(f"   Обработка просроченной задачи {task_id}: {task.get('title', 'Без названия')}")
                
                # Получаем полную информацию о задаче для получения создателя
                try:
                    task_info = self.bitrix_client.get_task_by_id(int(task_id))
                    created_by_id = task_info.get('createdBy') if task_info else None
                except Exception as e:
                    logger.debug(f"Не удалось получить полную информацию о задаче {task_id}: {e}")
                    created_by_id = None
                
                # Получаем Telegram ID ответственного и создателя через БД (только зарегистрированные пользователи)
                telegram_ids = []
                responsible_telegram_id = None
                created_by_telegram_id = None
                
                logger.debug(f"   Поиск Telegram ID: создатель={created_by_id}, ответственный={responsible_id}")
                
                # Сначала получаем создателя (если он есть и отличается от ответственного)
                if created_by_id and str(created_by_id) != str(responsible_id):
                    try:
                        if DATABASE_AVAILABLE:
                            created_by_telegram_id = database.get_telegram_id_by_bitrix_id(int(created_by_id))
                            if created_by_telegram_id:
                                telegram_ids.append(created_by_telegram_id)
                                logger.info(f"✅ Найден зарегистрированный пользователь (создатель): {created_by_telegram_id}")
                            else:
                                logger.debug(f"   Создатель {created_by_id} не зарегистрирован в системе")
                        else:
                            # Fallback: пробуем через Bitrix24Client
                            created_by_telegram_id = self.bitrix_client.get_user_telegram_id(int(created_by_id))
                            if created_by_telegram_id:
                                telegram_ids.append(created_by_telegram_id)
                                logger.info(f"✅ Найден Telegram ID создателя через Bitrix24Client: {created_by_telegram_id}")
                            else:
                                logger.debug(f"   Telegram ID создателя {created_by_id} не найден через Bitrix24Client")
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка при поиске Telegram ID для создателя {created_by_id}: {e}")
                
                # Затем получаем ответственного
                if responsible_id:
                    try:
                        if DATABASE_AVAILABLE:
                            responsible_telegram_id = database.get_telegram_id_by_bitrix_id(int(responsible_id))
                            if responsible_telegram_id and responsible_telegram_id not in telegram_ids:
                                telegram_ids.append(responsible_telegram_id)
                                logger.info(f"✅ Найден зарегистрированный пользователь (ответственный): {responsible_telegram_id}")
                            elif responsible_telegram_id:
                                logger.debug(f"   Ответственный {responsible_id} уже в списке уведомлений")
                            else:
                                logger.debug(f"   Ответственный {responsible_id} не зарегистрирован в системе")
                        else:
                            # Fallback: пробуем через Bitrix24Client
                            responsible_telegram_id = self.bitrix_client.get_user_telegram_id(int(responsible_id))
                            if responsible_telegram_id and responsible_telegram_id not in telegram_ids:
                                telegram_ids.append(responsible_telegram_id)
                                logger.info(f"✅ Найден Telegram ID ответственного через Bitrix24Client: {responsible_telegram_id}")
                            elif responsible_telegram_id:
                                logger.debug(f"   Ответственный {responsible_id} уже в списке уведомлений")
                            else:
                                logger.debug(f"   Telegram ID ответственного {responsible_id} не найден через Bitrix24Client")
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка при поиске Telegram ID для ответственного {responsible_id}: {e}")
                
                # Отправляем уведомление ТОЛЬКО если есть зарегистрированные пользователи
                if not telegram_ids:
                    logger.info(f"ℹ️ Нет зарегистрированных пользователей для уведомления о просроченной задаче {task_id}")
                    logger.info(f"   Создатель: {created_by_id}, Исполнитель: {responsible_id}")
                    continue
                
                # Формируем ссылку на задачу
                task_url = self.bitrix_client.get_task_url(int(task_id), responsible_id)
                
                # Формируем сообщение
                # Если есть и создатель, и ответственный (и они разные), упоминаем обоих
                if created_by_telegram_id and responsible_telegram_id and created_by_telegram_id != responsible_telegram_id:
                    message = f"исполнитель просрочил задачу <a href='{task_url}'>«{task.get('title', 'Без названия')}»</a>"
                else:
                    message = f"вы просрочили задачу <a href='{task_url}'>«{task.get('title', 'Без названия')}»</a>"
                
                # Отправляем уведомление
                await self._send_notification(message, telegram_ids)
                self._mark_notification_sent(notification_key, int(task_id), "overdue")
                
                logger.info(f"✅ Отправлено уведомление о просроченной задаче {task_id}")
        
        except Exception as e:
            logger.error(f"❌ Ошибка при проверке просроченных задач: {e}", exc_info=True)
    
    async def check_deadline_warnings(self):
        """Проверка задач с приближающимся дедлайном"""
        if not self.enable_deadline_warnings:
            return
        
        try:
            logger.info(f"🔍 Проверка задач с дедлайном через {self.deadline_warning_hours} часов...")
            
            # Вычисляем время предупреждения
            warning_time = datetime.now() + timedelta(hours=self.deadline_warning_hours)
            now = datetime.now()
            
            # Получаем задачи с дедлайном в ближайшие N часов
            # Bitrix24 использует операторы >= и <= для фильтров
            tasks = self.bitrix_client.get_tasks(
                filter_params={
                    ">=DEADLINE": now.strftime('%Y-%m-%d %H:%M:%S'),
                    "<=DEADLINE": warning_time.strftime('%Y-%m-%d %H:%M:%S'),
                    "!STATUS": "5"  # Исключаем завершенные задачи
                }
            )
            
            for task in tasks:
                task_id = task.get("id")
                deadline_str = task.get("deadline")
                responsible_id = task.get("responsibleId")
                
                if not task_id or not deadline_str:
                    continue
                
                # Проверяем, не отправляли ли уже уведомление
                notification_key = self._get_notification_key(task_id, "deadline_warning")
                if self._was_notification_sent(notification_key):
                    continue
                
                # Получаем Telegram ID ответственного через БД (только зарегистрированные пользователи)
                telegram_ids = []
                if responsible_id:
                    try:
                        if DATABASE_AVAILABLE:
                            telegram_id = database.get_telegram_id_by_bitrix_id(int(responsible_id))
                            if telegram_id:
                                telegram_ids.append(telegram_id)
                                logger.info(f"✅ Найден зарегистрированный пользователь (ответственный): {telegram_id}")
                        else:
                            # Fallback: пробуем через Bitrix24Client
                            telegram_id = self.bitrix_client.get_user_telegram_id(int(responsible_id))
                            if telegram_id:
                                telegram_ids.append(telegram_id)
                    except Exception as e:
                        logger.debug(f"Не удалось найти Telegram ID для ответственного {responsible_id}: {e}")
                
                # Отправляем уведомление ТОЛЬКО если есть зарегистрированные пользователи
                if not telegram_ids:
                    logger.info(f"ℹ️ Нет зарегистрированных пользователей для уведомления о приближающемся дедлайне задачи {task_id}")
                    logger.info(f"   Исполнитель: {responsible_id}")
                    continue
                
                # Формируем ссылку на задачу
                task_url = self.bitrix_client.get_task_url(int(task_id), responsible_id)
                
                # Формируем сообщение
                task_title = task.get("title", "Без названия")
                # Парсим дату дедлайна (Bitrix24 может возвращать в разных форматах)
                try:
                    # Пробуем разные форматы даты
                    if 'T' in deadline_str or 'Z' in deadline_str:
                        # ISO формат с временной зоной
                        deadline_dt = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                        # Убираем временную зону для вычисления разницы
                        if deadline_dt.tzinfo:
                            deadline_dt = deadline_dt.replace(tzinfo=None)
                    else:
                        # Простой формат YYYY-MM-DD HH:MI:SS
                        deadline_dt = datetime.strptime(deadline_str, '%Y-%m-%d %H:%M:%S')
                    
                    now = datetime.now()
                    hours_left = int((deadline_dt - now).total_seconds() / 3600)
                    if hours_left < 0:
                        hours_left = 0
                except Exception as date_error:
                    logger.warning(f"Ошибка при парсинге даты дедлайна {deadline_str}: {date_error}")
                    hours_left = self.deadline_warning_hours  # Используем значение по умолчанию
                
                message = f"вы почти просрочили задачу <a href='{task_url}'>«{task_title}»</a>"
                
                # Отправляем уведомление
                await self._send_notification(message, telegram_ids)
                self._mark_notification_sent(notification_key, int(task_id), "deadline_warning")
                
                logger.info(f"✅ Отправлено предупреждение о дедлайне задачи {task_id}")
        
        except Exception as e:
            logger.error(f"❌ Ошибка при проверке предупреждений о дедлайне: {e}", exc_info=True)
    
    async def check_task_comments(self, last_check_time: Optional[datetime] = None):
        """
        Проверка новых комментариев в задачах
        
        ВАЖНО: Метод tasks.task.commentitem.getlist не существует в Bitrix24 API.
        Для отслеживания изменений задач (комментарии, статусы) необходимо использовать
        исходящий вебхук Bitrix24 (Outgoing Webhook).
        
        События для настройки в исходящем вебхуке:
        - ONTASKADD - Создание задачи
        - ONTASKUPDATE - Обновление задачи (включая изменение статуса)
        - ONTASKDELETE - Удаление задачи
        - ONTASKCOMMENTADD - Добавление комментария к задаче
        - ONTASKCOMMENTUPDATE - Обновление комментария к задаче
        - ONTASKCOMMENTDELETE - Удаление комментария к задаче
        
        Args:
            last_check_time: Время последней проверки (опционально)
        """
        if not self.enable_comment_notifications:
            return
        
        # Отключаем проверку комментариев через API, так как метод не существует
        logger.warning("⚠️ Проверка комментариев через API отключена")
        logger.info("💡 Для отслеживания изменений задач (комментарии, статусы) используйте исходящий вебхук Bitrix24")
        logger.info("   Настройка: Bitrix24 → Настройки → Разработчикам → Исходящий вебхук")
        logger.info("   События задач: ONTASKADD, ONTASKUPDATE, ONTASKDELETE")
        logger.info("   События комментариев: ONTASKCOMMENTADD, ONTASKCOMMENTUPDATE, ONTASKCOMMENTDELETE")
        return
    
    def _detect_task_changes(self, task_info: Dict, previous_state: Optional[Dict] = None) -> Dict[str, any]:
        """
        Определение изменений в задаче на основе сравнения текущего состояния с предыдущим
        
        ВАЖНО: Bitrix24 исходящий вебхук НЕ передает полные данные задачи в FIELDS_BEFORE/FIELDS_AFTER,
        там только ID задачи. Поэтому мы сравниваем текущее состояние (из REST API) с предыдущим
        состоянием, сохраненным в БД.
        
        Args:
            task_info: Полная информация о задаче из REST API (текущее состояние)
            previous_state: Предыдущее состояние задачи из БД (опционально)
            
        Returns:
            Словарь с информацией об изменениях:
            {
                'deadline_changed': bool,
                'deadline_before': str или None,
                'deadline_after': str или None,
                'deadline_overdue': bool,  # Стал ли дедлайн просроченным
                'status_changed': bool,
                'status_before': str или None,
                'status_after': str или None,
                'responsible_changed': bool,
                'responsible_before': str или None,
                'responsible_after': str или None,
                'title_changed': bool,
                'changes': List[str]  # Список описаний изменений
            }
        """
        changes = {
            'deadline_changed': False,
            'deadline_before': None,
            'deadline_after': None,
            'deadline_overdue': False,
            'status_changed': False,
            'status_before': None,
            'status_after': None,
            'responsible_changed': False,
            'responsible_before': None,
            'responsible_after': None,
            'title_changed': False,
            'changes': []
        }
        
        # Нормализуем ключи (Bitrix24 может использовать разные форматы)
        def get_field(data: Dict, *keys):
            if not isinstance(data, dict):
                return None
            for key in keys:
                if key in data:
                    value = data[key]
                    # Обрабатываем None и пустые строки
                    if value is not None and value != '':
                        return value
            return None
        
        # Нормализуем дату для сравнения
        def normalize_date(date_str):
            """Нормализует дату для сравнения"""
            if not date_str:
                return None
            try:
                if isinstance(date_str, str):
                    # Убираем временную зону и нормализуем формат
                    date_str_clean = date_str.replace('Z', '').replace('+00:00', '')
                    if 'T' in date_str_clean:
                        date_str_clean = date_str_clean.replace('T', ' ')
                    # Парсим дату
                    if len(date_str_clean) > 19:
                        date_str_clean = date_str_clean[:19]
                    return datetime.strptime(date_str_clean, '%Y-%m-%d %H:%M:%S')
                return date_str
            except Exception as e:
                logger.debug(f"Ошибка при нормализации даты {date_str}: {e}")
                return date_str
        
        # Если нет данных задачи, не можем определить изменения
        if not task_info:
            logger.debug("Нет данных task_info для определения изменений")
            return changes
        
        logger.debug(f"Определение изменений: previous_state={previous_state is not None}, task_info={task_info is not None}")
        
        # Получаем значения полей из task_info (текущее состояние)
        deadline_after = get_field(task_info, 'DEADLINE', 'deadline', 'Deadline')
        status_after = get_field(task_info, 'STATUS', 'status', 'Status')
        responsible_after = get_field(task_info, 'RESPONSIBLE_ID', 'responsibleId', 'RESPONSIBLEID', 'responsible_id')
        title_after = get_field(task_info, 'TITLE', 'title', 'Title')
        
        # Если есть предыдущее состояние, сравниваем значения
        if previous_state:
            deadline_before = previous_state.get('deadline') or previous_state.get('DEADLINE')
            status_before = previous_state.get('status') or previous_state.get('STATUS')
            responsible_before = previous_state.get('responsible_id') or previous_state.get('RESPONSIBLE_ID')
            title_before = previous_state.get('title') or previous_state.get('TITLE')
            
            # Проверяем изменение дедлайна (с нормализацией для правильного сравнения)
            deadline_before_normalized = normalize_date(deadline_before)
            deadline_after_normalized = normalize_date(deadline_after)
            
            if deadline_before_normalized != deadline_after_normalized:
                changes['deadline_changed'] = True
                changes['deadline_before'] = deadline_before
                changes['deadline_after'] = deadline_after
                
                # Проверяем, просрочен ли дедлайн
                if deadline_after:
                    try:
                        # Парсим дату дедлайна
                        if isinstance(deadline_after, str):
                            if 'T' in deadline_after or 'Z' in deadline_after:
                                deadline_dt = datetime.fromisoformat(deadline_after.replace('Z', '+00:00'))
                                if deadline_dt.tzinfo:
                                    deadline_dt = deadline_dt.replace(tzinfo=None)
                            else:
                                deadline_dt = datetime.strptime(deadline_after, '%Y-%m-%d %H:%M:%S')
                        else:
                            deadline_dt = deadline_after
                        
                        # Если дедлайн просрочен, показываем это, иначе показываем изменение срока
                        if deadline_dt < datetime.now():
                            changes['deadline_overdue'] = True
                            changes['changes'].append('дедлайн просрочен')
                        else:
                            # Дедлайн изменен, но не просрочен
                            changes['changes'].append('изменен срок сдачи')
                    except Exception as e:
                        logger.debug(f"Ошибка при парсинге дедлайна {deadline_after}: {e}")
                        # Если не удалось распарсить, считаем что срок изменен
                        changes['changes'].append('изменен срок сдачи')
                elif deadline_before:
                    # Дедлайн был удален
                    changes['changes'].append('удален срок сдачи')
            
            # Проверяем изменение статуса
            if str(status_before) != str(status_after) if status_before and status_after else status_before != status_after:
                changes['status_changed'] = True
                changes['status_before'] = status_before
                changes['status_after'] = status_after
                
                status_name_after = self._get_status_name(status_after) if status_after else None
                if status_name_after:
                    changes['changes'].append(f'статус изменен на "{status_name_after}"')
                else:
                    changes['changes'].append('статус изменен')
            
            # Проверяем изменение ответственного
            if str(responsible_before) != str(responsible_after) if responsible_before and responsible_after else responsible_before != responsible_after:
                changes['responsible_changed'] = True
                changes['responsible_before'] = str(responsible_before) if responsible_before else None
                changes['responsible_after'] = str(responsible_after) if responsible_after else None
                changes['changes'].append('изменен исполнитель')
            
            # Проверяем изменение названия
            if title_before != title_after:
                changes['title_changed'] = True
                changes['changes'].append('изменено название')
        else:
            # Нет предыдущего состояния - проверяем текущее состояние
            # Проверяем, просрочен ли дедлайн (даже если он не был изменен)
            if deadline_after:
                try:
                    if isinstance(deadline_after, str):
                        if 'T' in deadline_after or 'Z' in deadline_after:
                            deadline_dt = datetime.fromisoformat(deadline_after.replace('Z', '+00:00'))
                            if deadline_dt.tzinfo:
                                deadline_dt = deadline_dt.replace(tzinfo=None)
                        else:
                            deadline_dt = datetime.strptime(deadline_after, '%Y-%m-%d %H:%M:%S')
                    else:
                        deadline_dt = deadline_after
                    
                    if deadline_dt < datetime.now():
                        changes['deadline_overdue'] = True
                        changes['deadline_after'] = deadline_after
                        changes['changes'].append('дедлайн просрочен')
                except Exception as e:
                    logger.debug(f"Ошибка при парсинге дедлайна {deadline_after}: {e}")
        
        logger.debug(f"Обнаруженные изменения: {changes['changes']}")
        return changes
    
    async def handle_task_event(self, event: str, task_data: Dict, auth_data: Dict = None, fields_before: Optional[Dict] = None, fields_after: Optional[Dict] = None):
        """
        Обработка события задачи из вебхука Bitrix24
        
        ВАЖНО: Bitrix24 исходящий вебхук НЕ передает полные данные задачи в FIELDS_BEFORE/FIELDS_AFTER,
        там только ID задачи. Поэтому мы:
        1. Получаем полную информацию о задаче через REST API (tasks.task.get)
        2. Получаем предыдущее состояние задачи из БД
        3. Сравниваем текущее состояние с предыдущим для определения изменений
        4. Сохраняем текущее состояние в БД для следующего сравнения
        5. Отправляем уведомление ТОЛЬКО ЕСЛИ пользователь зарегистрирован через LINK
        
        Args:
            event: Тип события (ONTASKADD, ONTASKUPDATE, ONTASKDELETE)
            task_data: Данные задачи из вебхука (обычно FIELDS_AFTER, содержит только ID)
            auth_data: Данные авторизации из вебхука (опционально)
            fields_before: Данные задачи до изменения (FIELDS_BEFORE) - НЕ ИСПОЛЬЗУЕТСЯ (только ID)
            fields_after: Данные задачи после изменения (FIELDS_AFTER) - НЕ ИСПОЛЬЗУЕТСЯ (только ID)
        """
        try:
            task_id = task_data.get('ID') or task_data.get('id')
            if not task_id:
                logger.warning(f"Не удалось получить ID задачи из данных: {task_data}")
                return
            
            task_id_int = int(task_id)
            event_upper = event.upper()
            
            # НЕ отправляем уведомления о созданных задачах (они уже отправляются при создании)
            if 'ONTASKADD' in event_upper:
                logger.debug(f"Пропускаем уведомление о создании задачи {task_id_int} (уже отправляется при создании)")
                return
            
            # Для ONTASKUPDATE получаем полную информацию о задаче через REST API
            task_info = None
            if 'ONTASKUPDATE' in event_upper:
                try:
                    # Используем основной клиент с BITRIX24_WEBHOOK_TOKEN для получения полной информации
                    task_info = self.bitrix_client.get_task_by_id(task_id_int)
                    if not task_info:
                        logger.warning(f"⚠️ Не удалось получить информацию о задаче {task_id_int} через REST API")
                        return
                    logger.debug(f"Получена информация о задаче {task_id_int}: deadline={task_info.get('deadline')}, status={task_info.get('status')}")
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка при получении задачи {task_id_int} через REST API: {e}")
                    return
            
            # Получаем предыдущее состояние задачи из БД
            previous_state = None
            if DATABASE_AVAILABLE and task_info:
                try:
                    previous_state = database.get_task_state(task_id_int)
                    if previous_state:
                        logger.debug(f"Получено предыдущее состояние задачи {task_id_int} из БД")
                    else:
                        logger.debug(f"Предыдущее состояние задачи {task_id_int} не найдено в БД (первое обновление)")
                except Exception as e:
                    logger.debug(f"Ошибка при получении предыдущего состояния задачи {task_id_int}: {e}")
            
            # Логируем данные для отладки
            logger.debug(f"Данные для определения изменений задачи {task_id_int}:")
            logger.debug(f"  previous_state: {previous_state is not None}")
            logger.debug(f"  task_info: deadline={task_info.get('deadline') if task_info else None}, status={task_info.get('status') if task_info else None}")
            
            # Получаем название задачи и ответственных
            if task_info:
                task_title = task_info.get('title', 'Без названия')
                responsible_id = task_info.get('responsibleId')
                created_by_id = task_info.get('createdBy')
            else:
                # Fallback на данные из вебхука
                task_title = task_data.get('TITLE') or task_data.get('title') or 'Без названия'
                responsible_id = task_data.get('RESPONSIBLE_ID') or task_data.get('responsibleId')
                created_by_id = task_data.get('CREATED_BY') or task_data.get('createdBy')
            
            # Проверяем регистрацию через БД (LINK) - только зарегистрированные пользователи
            telegram_ids = []
            
            # Проверяем ответственного
            if responsible_id:
                try:
                    if DATABASE_AVAILABLE:
                        telegram_id = database.get_telegram_id_by_bitrix_id(int(responsible_id))
                        if telegram_id:
                            telegram_ids.append(telegram_id)
                            logger.info(f"✅ Найден зарегистрированный пользователь (ответственный): {telegram_id}")
                    else:
                        # Fallback: пробуем через Bitrix24Client
                        telegram_id = self.bitrix_client.get_user_telegram_id(int(responsible_id))
                        if telegram_id:
                            telegram_ids.append(telegram_id)
                except Exception as e:
                    logger.debug(f"Не удалось найти Telegram ID для ответственного {responsible_id}: {e}")
            
            # Проверяем создателя (если он отличается от ответственного)
            if created_by_id and str(created_by_id) != str(responsible_id):
                try:
                    if DATABASE_AVAILABLE:
                        telegram_id = database.get_telegram_id_by_bitrix_id(int(created_by_id))
                        if telegram_id and telegram_id not in telegram_ids:
                            telegram_ids.append(telegram_id)
                            logger.info(f"✅ Найден зарегистрированный пользователь (создатель): {telegram_id}")
                    else:
                        # Fallback: пробуем через Bitrix24Client
                        telegram_id = self.bitrix_client.get_user_telegram_id(int(created_by_id))
                        if telegram_id and telegram_id not in telegram_ids:
                            telegram_ids.append(telegram_id)
                except Exception as e:
                    logger.debug(f"Не удалось найти Telegram ID для создателя {created_by_id}: {e}")
            
            # Отправляем уведомление ТОЛЬКО если есть зарегистрированные пользователи
            if not telegram_ids:
                logger.info(f"ℹ️ Нет зарегистрированных пользователей для уведомления о задаче {task_id_int}")
                logger.info(f"   Создатель: {created_by_id}, Исполнитель: {responsible_id}")
                return
            
            # Формируем ссылку на задачу
            task_url = self.bitrix_client.get_task_url(task_id_int, int(responsible_id) if responsible_id else None)
            
            # Формируем сообщение в зависимости от типа события
            if 'ONTASKUPDATE' in event_upper:
                # Определяем изменения через сравнение текущего состояния с предыдущим из БД
                task_changes = self._detect_task_changes(task_info, previous_state)
                
                logger.debug(f"Результат определения изменений для задачи {task_id_int}: {task_changes}")
                
                # Формируем сообщение на основе обнаруженных изменений
                if task_changes['changes']:
                    # Есть конкретные изменения - формируем детальное сообщение
                    changes_text = ", ".join(task_changes['changes'])
                    message = f"задача <a href='{task_url}'>«{task_title}»</a>: {changes_text}"
                    
                    # Если дедлайн просрочен, добавляем предупреждение в начало
                    if task_changes['deadline_overdue']:
                        message = f"⚠️ {message}"
                else:
                    # Нет конкретных изменений или они не определены
                    # Пробуем определить статус из task_info
                    status = None
                    if task_info:
                        status = task_info.get('status') or task_info.get('STATUS')
                    elif fields_after:
                        status = fields_after.get('STATUS') or fields_after.get('status')
                    
                    if status:
                        status_name = self._get_status_name(status)
                        message = f"задача <a href='{task_url}'>«{task_title}»</a>: статус изменен на \"{status_name}\""
                    else:
                        # Если ничего не определено, используем общее сообщение
                        message = f"задача <a href='{task_url}'>«{task_title}»</a> обновлена"
                
                notification_type = "task_updated"
            elif 'ONTASKDELETE' in event_upper:
                message = f"задача «{task_title}» удалена"
                notification_type = "task_deleted"
            else:
                logger.debug(f"Неизвестный тип события задачи: {event}")
                return
            
            # Проверяем, не отправляли ли уже уведомление для этого события
            notification_key = self._get_notification_key(task_id_int, notification_type, event_upper)
            if self._was_notification_sent(notification_key):
                logger.debug(f"Уведомление для события {event} задачи {task_id_int} уже отправлено")
                return
            
            # Отправляем уведомление с тегами зарегистрированных пользователей
            await self._send_notification(message, telegram_ids)
            
            # Отмечаем уведомление как отправленное
            self._mark_notification_sent(notification_key, task_id_int, notification_type, event_upper)
            
            # Сохраняем текущее состояние задачи в БД для следующего сравнения
            if DATABASE_AVAILABLE and task_info:
                try:
                    database.save_task_state(task_id_int, task_info)
                    logger.debug(f"Сохранено состояние задачи {task_id_int} в БД")
                except Exception as e:
                    logger.debug(f"Ошибка при сохранении состояния задачи {task_id_int}: {e}")
            
            logger.info(f"✅ Отправлено уведомление о событии {event} для задачи {task_id_int} (уведомлены: {len(telegram_ids)} пользователей)")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке события задачи {event}: {e}", exc_info=True)
    
    async def handle_task_comment_event(self, event: str, comment_data: Dict, auth_data: Dict = None):
        """
        Обработка события комментария к задаче из вебхука Bitrix24
        
        Оптимизированная версия:
        1. Получает полную информацию о комментарии через REST API Bitrix24
        2. Получает информацию о задаче (создатель, исполнитель)
        3. Находит Telegram ID через базу данных для зарегистрированных пользователей
        4. Отправляет уведомления только зарегистрированным пользователям (избегает спама)
        
        Args:
            event: Тип события (ONTASKCOMMENTADD, ONTASKCOMMENTUPDATE, ONTASKCOMMENTDELETE)
            comment_data: Данные комментария из вебхука
            auth_data: Данные авторизации из вебхука (содержит application_token для REST API)
        """
        try:
            task_id = comment_data.get('TASK_ID') or comment_data.get('taskId') or comment_data.get('TASKID')
            # ВАЖНО: Bitrix24 отправляет ID комментария как "0" в поле ID, реальный ID находится в MESSAGE_ID
            comment_id = comment_data.get('MESSAGE_ID') or comment_data.get('messageId') or comment_data.get('MESSAGEID')
            # Fallback на ID только если MESSAGE_ID нет
            if not comment_id or comment_id == '0':
                comment_id = comment_data.get('ID') or comment_data.get('id')
            
            logger.debug(f"Извлеченные данные из комментария: task_id={task_id}, comment_id={comment_id}")
            logger.debug(f"Полные данные комментария: {comment_data}")
            
            if not task_id:
                logger.warning(f"Не удалось получить ID задачи из данных: {comment_data}")
                return
            
            # Если comment_id равен "0" или отсутствует, это нормально для некоторых событий
            # но мы все равно можем обработать событие
            if not comment_id or comment_id == '0':
                logger.warning(f"⚠️ ID комментария равен 0 или отсутствует. Используем MESSAGE_ID или пропускаем получение комментария через API")
                comment_id_int = None
            else:
                try:
                    comment_id_int = int(comment_id)
                except (ValueError, TypeError):
                    logger.warning(f"⚠️ Неверный формат ID комментария: {comment_id}")
                    comment_id_int = None
            
            task_id_int = int(task_id)
            event_upper = event.upper()
            
            # Используем основной Bitrix24Client с вебхук токеном из переменных окружения
            # application_token из вебхука не является вебхук токеном для REST API и может не иметь прав
            # на выполнение методов tasks.task.get и tasks.task.comment.get
            api_client = self.bitrix_client
            
            # Если домен из вебхука отличается от домена основного клиента, создаем новый клиент
            if auth_data and auth_data.get('domain'):
                webhook_domain = auth_data['domain']
                main_domain = self.bitrix_client.domain
                
                # Если домены отличаются, создаем клиент с правильным доменом, но используем основной токен
                if webhook_domain != main_domain:
                    try:
                        from bitrix24_client import Bitrix24Client
                        main_webhook_token = os.getenv("BITRIX24_WEBHOOK_TOKEN")
                        if main_webhook_token:
                            api_client = Bitrix24Client(
                                domain=webhook_domain,
                                webhook_token=main_webhook_token
                            )
                            logger.debug(f"✅ Создан Bitrix24Client с доменом из вебхука {webhook_domain} и основным вебхук токеном")
                        else:
                            logger.warning(f"⚠️ BITRIX24_WEBHOOK_TOKEN не установлен, используем основной клиент")
                    except Exception as e:
                        logger.warning(f"⚠️ Не удалось создать Bitrix24Client с доменом {webhook_domain}: {e}")
                        logger.info("💡 Используем основной Bitrix24Client")
            
            # Получаем информацию о задаче для получения создателя, исполнителя и chatId
            # ВАЖНО: Сначала получаем задачу, чтобы получить chatId для работы с чатом
            try:
                logger.info(f"🔍 Запрос информации о задаче {task_id_int} через API клиент (домен: {api_client.domain})")
                task_info = api_client.get_task_by_id(task_id_int)
                
                # Получаем chatId задачи для работы с чатом
                chat_id = None
                if task_info:
                    chat_id = task_info.get('chatId') or task_info.get('chat_id')
                    if chat_id:
                        logger.info(f"💬 Найден chatId задачи {task_id_int}: {chat_id}")
                    else:
                        logger.debug(f"ℹ️ У задачи {task_id_int} нет chatId (возможно, старая версия Bitrix24)")
                
                if task_info:
                    logger.info(f"📦 Полученная информация о задаче {task_id_int}:")
                    logger.info(f"   Тип: {type(task_info)}")
                    logger.info(f"   Ключи: {list(task_info.keys()) if isinstance(task_info, dict) else 'N/A'}")
                    logger.info(f"   Полные данные: {task_info}")
                    
                    task_title = task_info.get('title', 'Без названия')
                    responsible_id = task_info.get('responsibleId')
                    created_by_id = task_info.get('createdBy')
                    logger.info(f"✅ Получена информация о задаче {task_id_int}: создатель={created_by_id}, исполнитель={responsible_id}, chatId={chat_id}")
                else:
                    logger.warning(f"⚠️ Не удалось получить информацию о задаче {task_id_int} (task_info = None)")
                    task_title = 'Без названия'
                    responsible_id = None
                    created_by_id = None
            except Exception as e:
                error_str = str(e)
                # Если ошибка 404 и использовался не основной клиент, пробуем основной клиент
                if '404' in error_str or 'Method not found' in error_str:
                    if api_client != self.bitrix_client:
                        logger.warning(f"⚠️ Метод недоступен для клиента с доменом {api_client.domain}, пробуем основной клиент")
                        try:
                            logger.info(f"🔍 Повторный запрос информации о задаче {task_id_int} через основной клиент (домен: {self.bitrix_client.domain})")
                            task_info = self.bitrix_client.get_task_by_id(task_id_int)
                            if task_info:
                                logger.info(f"📦 Полученная информация о задаче {task_id_int} через основной клиент:")
                                logger.info(f"   Тип: {type(task_info)}")
                                logger.info(f"   Ключи: {list(task_info.keys()) if isinstance(task_info, dict) else 'N/A'}")
                                logger.info(f"   Полные данные: {task_info}")
                                
                                task_title = task_info.get('title', 'Без названия')
                                responsible_id = task_info.get('responsibleId')
                                created_by_id = task_info.get('createdBy')
                                # Получаем chatId из задачи через основной клиент
                                chat_id = task_info.get('chatId') or task_info.get('chat_id')
                                if chat_id:
                                    logger.info(f"💬 Найден chatId задачи {task_id_int} через основной клиент: {chat_id}")
                                logger.info(f"✅ Получена информация о задаче через основной клиент: создатель={created_by_id}, исполнитель={responsible_id}, chatId={chat_id}")
                            else:
                                logger.warning(f"⚠️ Не удалось получить информацию о задаче {task_id_int} через основной клиент (task_info = None)")
                                task_title = 'Без названия'
                                responsible_id = None
                                created_by_id = None
                        except Exception as e2:
                            logger.warning(f"⚠️ Ошибка при получении задачи через основной клиент: {e2}")
                            task_title = 'Без названия'
                            responsible_id = None
                            created_by_id = None
                    else:
                        logger.warning(f"⚠️ Ошибка при получении задачи {task_id_int}: {e}")
                        task_title = 'Без названия'
                        responsible_id = None
                        created_by_id = None
                        chat_id = None
                else:
                    logger.warning(f"⚠️ Ошибка при получении задачи {task_id_int}: {e}")
                    task_title = 'Без названия'
                    responsible_id = None
                    created_by_id = None
                    chat_id = None
            
            # ПРИМЕЧАНИЕ: После обновления Bitrix24 комментарии к задачам стали сообщениями в чатах
            # Используем API чатов вместо API комментариев задач
            full_comment_info = None
            
            # Получаем сообщение из чата задачи (комментарий)
            if chat_id and comment_id_int and ('ONTASKCOMMENTADD' in event_upper or 'ONTASKCOMMENTUPDATE' in event_upper):
                # Используем MESSAGE_ID как ID сообщения в чате
                message_id = comment_data.get('MESSAGE_ID') or str(comment_id_int)
                try:
                    message_id_int = int(message_id)
                    logger.info(f"🔍 Попытка получить текст комментария: chatId={chat_id}, messageId={message_id_int}")
                    full_comment_info = api_client.get_task_chat_message(chat_id, message_id_int)
                    if full_comment_info:
                        logger.info(f"✅ Получена информация о сообщении {message_id_int} из чата {chat_id} через API чатов")
                        logger.info(f"   Автор сообщения: {full_comment_info.get('authorId')}")
                        comment_text = full_comment_info.get('message') or full_comment_info.get('MESSAGE')
                        if comment_text:
                            preview = str(comment_text)[:50] + "..." if len(str(comment_text)) > 50 else str(comment_text)
                            logger.info(f"   Текст комментария: {preview}")
                        else:
                            logger.warning(f"⚠️ Текст комментария не найден в ответе API")
                    else:
                        logger.warning(f"⚠️ Не удалось получить сообщение {message_id_int} из чата {chat_id} (full_comment_info = None)")
                        logger.info(f"💡 Проверьте права вебхука на метод im.message.get в разделе 'Мессенджер (im)'")
                except (ValueError, TypeError) as e:
                    logger.warning(f"⚠️ Неверный формат MESSAGE_ID: {message_id}, ошибка: {e}")
                except Exception as e:
                    error_str = str(e)
                    logger.warning(f"⚠️ Ошибка при получении сообщения из чата через API: {type(e).__name__}: {e}")
                    if 'Method not found' in error_str or 'Could not find description' in error_str:
                        logger.warning(f"💡 Метод im.message.get недоступен. Проверьте права вебхука:")
                        logger.warning(f"   1. Bitrix24 → Настройки → Разработчикам → Входящий вебхук")
                        logger.warning(f"   2. Выберите ваш вебхук → Права доступа")
                        logger.warning(f"   3. Найдите раздел 'Мессенджер (im)' и включите права на модуль")
                    else:
                        logger.debug(f"   Детали ошибки: {error_str}")
            elif not chat_id:
                logger.debug(f"ℹ️ У задачи {task_id_int} нет chatId, пропускаем получение сообщения через API чатов")
            elif not comment_id_int:
                logger.debug(f"ℹ️ ID комментария отсутствует, пропускаем получение сообщения через API")
            
            # Формируем ссылку на задачу
            task_url = self.bitrix_client.get_task_url(task_id_int, int(responsible_id) if responsible_id else None)
            
            # Находим Telegram ID через базу данных для зарегистрированных пользователей
            telegram_ids = []
            
            # Получаем автора комментария (из сообщения в чате или из вебхука)
            author_id = None
            if full_comment_info:
                # Используем данные из сообщения в чате
                author_id = full_comment_info.get('authorId') or full_comment_info.get('AUTHOR_ID')
                logger.debug(f"✅ Автор комментария из сообщения в чате: {author_id}")
            else:
                # Пробуем получить из вебхука (может отсутствовать)
                author_id = comment_data.get('AUTHOR_ID') or comment_data.get('authorId') or comment_data.get('AUTHORID')
                if not author_id:
                    logger.debug(f"ℹ️ AUTHOR_ID не найден в данных вебхука (это нормально для новых версий Bitrix24)")
                    logger.debug(f"💡 Автор будет определен через задачу (создатель/исполнитель)")
            
            # Ищем Telegram ID для создателя задачи (если он зарегистрирован)
            if created_by_id:
                try:
                    if DATABASE_AVAILABLE:
                        created_by_telegram_id = database.get_telegram_id_by_bitrix_id(int(created_by_id))
                        if created_by_telegram_id:
                            telegram_ids.append(created_by_telegram_id)
                            logger.info(f"✅ Найден Telegram ID для создателя задачи: {created_by_telegram_id}")
                        else:
                            logger.debug(f"Создатель задачи {created_by_id} не зарегистрирован в системе")
                    else:
                        # Fallback: пробуем через Bitrix24Client
                        created_by_telegram_id = self.bitrix_client.get_user_telegram_id(int(created_by_id))
                        if created_by_telegram_id:
                            telegram_ids.append(created_by_telegram_id)
                except Exception as e:
                    logger.debug(f"Не удалось найти Telegram ID для создателя {created_by_id}: {e}")
            
            # Ищем Telegram ID для исполнителя задачи (если он зарегистрирован)
            if responsible_id:
                try:
                    if DATABASE_AVAILABLE:
                        responsible_telegram_id = database.get_telegram_id_by_bitrix_id(int(responsible_id))
                        if responsible_telegram_id:
                            if responsible_telegram_id not in telegram_ids:
                                telegram_ids.append(responsible_telegram_id)
                                logger.info(f"✅ Найден Telegram ID для исполнителя задачи: {responsible_telegram_id}")
                        else:
                            logger.debug(f"Исполнитель задачи {responsible_id} не зарегистрирован в системе")
                    else:
                        # Fallback: пробуем через Bitrix24Client
                        responsible_telegram_id = self.bitrix_client.get_user_telegram_id(int(responsible_id))
                        if responsible_telegram_id and responsible_telegram_id not in telegram_ids:
                            telegram_ids.append(responsible_telegram_id)
                except Exception as e:
                    logger.debug(f"Не удалось найти Telegram ID для исполнителя {responsible_id}: {e}")
            
            # Ищем Telegram ID для автора комментария (если он зарегистрирован и отличается от создателя/исполнителя)
            if author_id:
                try:
                    author_id_int = int(author_id)
                    # Не добавляем автора, если он уже в списке (создатель или исполнитель)
                    should_add_author = True
                    if created_by_id and author_id_int == int(created_by_id):
                        should_add_author = False
                    if responsible_id and author_id_int == int(responsible_id):
                        should_add_author = False
                    
                    if should_add_author:
                        if DATABASE_AVAILABLE:
                            author_telegram_id = database.get_telegram_id_by_bitrix_id(author_id_int)
                            if author_telegram_id:
                                if author_telegram_id not in telegram_ids:
                                    telegram_ids.append(author_telegram_id)
                                    logger.info(f"✅ Найден Telegram ID для автора комментария: {author_telegram_id}")
                            else:
                                logger.debug(f"Автор комментария {author_id_int} не зарегистрирован в системе")
                        else:
                            # Fallback: пробуем через Bitrix24Client
                            author_telegram_id = self.bitrix_client.get_user_telegram_id(author_id_int)
                            if author_telegram_id and author_telegram_id not in telegram_ids:
                                telegram_ids.append(author_telegram_id)
                except Exception as e:
                    logger.debug(f"Не удалось найти Telegram ID для автора комментария {author_id}: {e}")
            
            # Если нет зарегистрированных пользователей, не отправляем уведомление (избегаем спама)
            if not telegram_ids:
                logger.info(f"ℹ️ Нет зарегистрированных пользователей для уведомления о комментарии {comment_id_int} к задаче {task_id_int}")
                logger.info(f"   Создатель: {created_by_id}, Исполнитель: {responsible_id}, Автор: {author_id}")
                return
            
            # Формируем сообщение в зависимости от типа события
            if 'ONTASKCOMMENTADD' in event_upper:
                # Если есть текст комментария, добавляем его в сообщение
                comment_text = ""
                if full_comment_info:
                    # Пробуем разные поля для текста сообщения
                    comment_message = (
                        full_comment_info.get('message') or 
                        full_comment_info.get('MESSAGE') or 
                        full_comment_info.get('postMessage') or
                        full_comment_info.get('POST_MESSAGE')
                    )
                    if comment_message:
                        comment_text_preview = str(comment_message)[:100]
                        if len(str(comment_message)) > 100:
                            comment_text_preview += "..."
                        comment_text = f": {comment_text_preview}"
                message = f"в задаче <a href='{task_url}'>«{task_title}»</a> новый комментарий{comment_text}"
                notification_type = "comment_added"
            elif 'ONTASKCOMMENTUPDATE' in event_upper:
                message = f"в задаче <a href='{task_url}'>«{task_title}»</a> обновлен комментарий"
                notification_type = "comment_updated"
            elif 'ONTASKCOMMENTDELETE' in event_upper:
                message = f"в задаче <a href='{task_url}'>«{task_title}»</a> удален комментарий"
                notification_type = "comment_deleted"
            else:
                logger.debug(f"Неизвестный тип события комментария: {event}")
                return
            
            # Проверяем, не отправляли ли уже уведомление для этого события
            # Используем MESSAGE_ID или comment_id для уникальности
            notification_extra = str(comment_id) if comment_id else f"msg_{comment_data.get('MESSAGE_ID', 'unknown')}"
            notification_key = self._get_notification_key(task_id_int, notification_type, notification_extra)
            if self._was_notification_sent(notification_key):
                logger.debug(f"Уведомление для события {event} комментария {notification_extra} уже отправлено")
                return
            
            # Отправляем уведомление в группу с упоминанием зарегистрированных пользователей
            await self._send_notification(message, telegram_ids)
            
            # Отмечаем уведомление как отправленное
            self._mark_notification_sent(notification_key, task_id_int, notification_type, notification_extra)
            
            comment_info = f"комментария {notification_extra}" if notification_extra else "комментария"
            logger.info(f"✅ Отправлено уведомление о событии {event} для {comment_info} к задаче {task_id_int} (уведомлены: {len(telegram_ids)} пользователей)")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке события комментария {event}: {e}", exc_info=True)
    
    def _get_status_name(self, status: str) -> str:
        """
        Получение названия статуса задачи
        
        Args:
            status: Код статуса задачи в Bitrix24
            
        Returns:
            Название статуса
        """
        status_map = {
            '1': 'Новая',
            '2': 'В работе',
            '3': 'Ожидает выполнения',
            '4': 'Требует внимания',
            '5': 'Завершена',
            '6': 'Отложена',
            '7': 'Отклонена'
        }
        return status_map.get(str(status), f'Статус {status}')
    
    async def run_periodic_check(self):
        """Запуск периодической проверки задач"""
        logger.info("🔄 Запуск периодической проверки задач...")
        
        # Проверяем просроченные задачи
        await self.check_overdue_tasks()
        
        # Проверяем предупреждения о дедлайне
        await self.check_deadline_warnings()
        
        # Проверка комментариев отключена - метод tasks.task.commentitem.getlist не существует
        # Для отслеживания изменений задач используйте исходящий вебхук Bitrix24
        # await self.check_task_comments()
        
        logger.info("✅ Периодическая проверка задач завершена")
