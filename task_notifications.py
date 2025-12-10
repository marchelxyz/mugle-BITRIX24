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
            
            result = await self.telegram_bot.send_message(**send_params)
            
            if self.telegram_thread_id:
                logger.info(f"✅ Уведомление успешно отправлено в группу {self.telegram_group_id}, топик {self.telegram_thread_id} (message_id: {result.message_id})")
            else:
                logger.info(f"✅ Уведомление успешно отправлено в группу {self.telegram_group_id} (message_id: {result.message_id})")
        except Exception as e:
            if self.telegram_thread_id:
                logger.error(f"❌ Ошибка при отправке уведомления в группу {self.telegram_group_id}, топик {self.telegram_thread_id}: {e}", exc_info=True)
            else:
                logger.error(f"❌ Ошибка при отправке уведомления в группу {self.telegram_group_id}: {e}", exc_info=True)
            logger.error(f"   Тип ошибки: {type(e).__name__}")
            logger.error(f"   Сообщение: {message}")
            logger.error(f"   Telegram ID пользователя: {user_telegram_id}")
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
            # Bitrix24 использует формат фильтров через операторы
            # Для просроченных задач: DEADLINE < текущая дата и STATUS не равен 5 (завершена)
            tasks = self.bitrix_client.get_tasks(
                filter_params={
                    "<DEADLINE": now.strftime('%Y-%m-%d %H:%M:%S'),
                    "!STATUS": "5"  # Исключаем завершенные задачи (статус 5 = завершена)
                }
            )
            
            for task in tasks:
                task_id = task.get("id")
                deadline_str = task.get("deadline")
                responsible_id = task.get("responsibleId")
                
                if not task_id or not deadline_str:
                    continue
                
                # Проверяем, не отправляли ли уже уведомление
                notification_key = self._get_notification_key(task_id, "overdue")
                if self._was_notification_sent(notification_key):
                    continue
                
                # Получаем полную информацию о задаче для получения создателя
                try:
                    task_info = self.bitrix_client.get_task_by_id(int(task_id))
                    created_by_id = task_info.get('createdBy') if task_info else None
                except Exception as e:
                    logger.debug(f"Не удалось получить полную информацию о задаче {task_id}: {e}")
                    created_by_id = None
                
                # Получаем Telegram ID ответственного и создателя
                telegram_ids = []
                responsible_telegram_id = None
                created_by_telegram_id = None
                
                # Сначала получаем создателя (если он есть и отличается от ответственного)
                if created_by_id and str(created_by_id) != str(responsible_id):
                    try:
                        created_by_telegram_id = self.bitrix_client.get_user_telegram_id(int(created_by_id))
                        if created_by_telegram_id:
                            telegram_ids.append(created_by_telegram_id)
                    except Exception as e:
                        logger.debug(f"Не удалось получить Telegram ID для создателя {created_by_id}: {e}")
                
                # Затем получаем ответственного
                if responsible_id:
                    try:
                        responsible_telegram_id = self.bitrix_client.get_user_telegram_id(int(responsible_id))
                        if responsible_telegram_id and responsible_telegram_id not in telegram_ids:
                            telegram_ids.append(responsible_telegram_id)
                    except Exception as e:
                        logger.debug(f"Не удалось получить Telegram ID для ответственного {responsible_id}: {e}")
                
                # Формируем ссылку на задачу
                task_url = self.bitrix_client.get_task_url(int(task_id), responsible_id)
                
                # Формируем сообщение
                # Если есть и создатель, и ответственный (и они разные), упоминаем обоих
                if created_by_telegram_id and responsible_telegram_id and created_by_telegram_id != responsible_telegram_id:
                    message = f"исполнитель просрочил задачу <a href='{task_url}'>«{task.get('title', 'Без названия')}»</a>"
                else:
                    message = f"вы просрочили задачу <a href='{task_url}'>«{task.get('title', 'Без названия')}»</a>"
                
                # Отправляем уведомление
                await self._send_notification(message, telegram_ids if telegram_ids else None)
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
                
                # Получаем Telegram ID ответственного
                telegram_ids = []
                if responsible_id:
                    telegram_id = self.bitrix_client.get_user_telegram_id(int(responsible_id))
                    if telegram_id:
                        telegram_ids.append(telegram_id)
                
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
                await self._send_notification(message, telegram_ids if telegram_ids else None)
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
    
    async def handle_task_event(self, event: str, task_data: Dict):
        """
        Обработка события задачи из вебхука Bitrix24
        
        Args:
            event: Тип события (ONTASKADD, ONTASKUPDATE, ONTASKDELETE)
            task_data: Данные задачи из вебхука
        """
        try:
            task_id = task_data.get('ID') or task_data.get('id')
            if not task_id:
                logger.warning(f"Не удалось получить ID задачи из данных: {task_data}")
                return
            
            task_id_int = int(task_id)
            event_upper = event.upper()
            
            # Получаем информацию о задаче
            task_title = task_data.get('TITLE') or task_data.get('title') or 'Без названия'
            responsible_id = task_data.get('RESPONSIBLE_ID') or task_data.get('responsibleId') or task_data.get('RESPONSIBLE_ID')
            status = task_data.get('STATUS') or task_data.get('status')
            deadline = task_data.get('DEADLINE') or task_data.get('deadline')
            
            # Получаем Telegram ID ответственного
            telegram_ids = []
            if responsible_id:
                try:
                    telegram_id = self.bitrix_client.get_user_telegram_id(int(responsible_id))
                    if telegram_id:
                        telegram_ids.append(telegram_id)
                except Exception as e:
                    logger.debug(f"Не удалось получить Telegram ID для пользователя {responsible_id}: {e}")
            
            # Формируем ссылку на задачу
            task_url = self.bitrix_client.get_task_url(task_id_int, int(responsible_id) if responsible_id else None)
            
            # Формируем сообщение в зависимости от типа события
            if 'ONTASKADD' in event_upper:
                message = f"создана новая задача <a href='{task_url}'>«{task_title}»</a>"
                notification_type = "task_added"
            elif 'ONTASKUPDATE' in event_upper:
                # Определяем, что именно изменилось
                status_name = self._get_status_name(status) if status else None
                if status_name:
                    message = f"задача <a href='{task_url}'>«{task_title}»</a> изменена: статус — {status_name}"
                else:
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
            
            # Отправляем уведомление в группу
            await self._send_notification(message, telegram_ids if telegram_ids else None)
            
            # Отмечаем уведомление как отправленное
            self._mark_notification_sent(notification_key, task_id_int, notification_type, event_upper)
            
            logger.info(f"✅ Отправлено уведомление о событии {event} для задачи {task_id_int}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке события задачи {event}: {e}", exc_info=True)
    
    async def handle_task_comment_event(self, event: str, comment_data: Dict):
        """
        Обработка события комментария к задаче из вебхука Bitrix24
        
        Args:
            event: Тип события (ONTASKCOMMENTADD, ONTASKCOMMENTUPDATE, ONTASKCOMMENTDELETE)
            comment_data: Данные комментария из вебхука
        """
        try:
            task_id = comment_data.get('TASK_ID') or comment_data.get('taskId') or comment_data.get('TASKID')
            comment_id = comment_data.get('ID') or comment_data.get('id')
            
            logger.debug(f"Извлеченные данные из комментария: task_id={task_id}, comment_id={comment_id}")
            logger.debug(f"Полные данные комментария: {comment_data}")
            
            if not task_id:
                logger.warning(f"Не удалось получить ID задачи из данных комментария: {comment_data}")
                return
            
            task_id_int = int(task_id)
            event_upper = event.upper()
            
            # Получаем информацию о задаче для формирования ссылки
            try:
                task_info = self.bitrix_client.get_task_by_id(task_id_int)
                task_title = task_info.get('title', 'Без названия') if task_info else 'Без названия'
                responsible_id = task_info.get('responsibleId') if task_info else None
            except Exception as e:
                logger.debug(f"Не удалось получить информацию о задаче {task_id_int}: {e}")
                task_title = 'Без названия'
                responsible_id = None
            
            # Формируем ссылку на задачу
            task_url = self.bitrix_client.get_task_url(task_id_int, int(responsible_id) if responsible_id else None)
            
            # Получаем автора комментария и ответственного за задачу для упоминания
            author_id = comment_data.get('AUTHOR_ID') or comment_data.get('authorId') or comment_data.get('AUTHOR_ID')
            telegram_ids = []
            
            # Добавляем ответственного за задачу (если он есть)
            if responsible_id:
                try:
                    responsible_telegram_id = self.bitrix_client.get_user_telegram_id(int(responsible_id))
                    if responsible_telegram_id:
                        telegram_ids.append(responsible_telegram_id)
                except Exception as e:
                    logger.debug(f"Не удалось получить Telegram ID для ответственного {responsible_id}: {e}")
            
            # Формируем сообщение в зависимости от типа события
            if 'ONTASKCOMMENTADD' in event_upper:
                message = f"в задаче <a href='{task_url}'>«{task_title}»</a> новый комментарий"
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
            notification_key = self._get_notification_key(task_id_int, notification_type, str(comment_id))
            if self._was_notification_sent(notification_key):
                logger.debug(f"Уведомление для события {event} комментария {comment_id} уже отправлено")
                return
            
            # Отправляем уведомление в группу с упоминанием ответственного
            await self._send_notification(message, telegram_ids if telegram_ids else None)
            
            # Отмечаем уведомление как отправленное
            self._mark_notification_sent(notification_key, task_id_int, notification_type, str(comment_id))
            
            logger.info(f"✅ Отправлено уведомление о событии {event} для комментария {comment_id} к задаче {task_id_int}")
            
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
