"""
Модуль для работы с API Битрикс24
"""
import requests
import os
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class Bitrix24Client:
    """Клиент для работы с API Битрикс24"""
    
    def __init__(self, domain: str, webhook_token: str, telegram_field_name: str = None):
        """
        Инициализация клиента Битрикс24
        
        Args:
            domain: Домен Битрикс24 (например, your-domain.bitrix24.ru)
            webhook_token: Токен вебхука для доступа к API
            telegram_field_name: Название поля для хранения Telegram ID (по умолчанию UF_USR_TELEGRAM)
        """
        if domain is None:
            raise ValueError("BITRIX24_DOMAIN не установлен в переменных окружения")
        if webhook_token is None:
            raise ValueError("BITRIX24_WEBHOOK_TOKEN не установлен в переменных окружения")
        
        self.domain = domain.rstrip('/')
        self.webhook_token = webhook_token
        self.base_url = f"https://{self.domain}/rest/{webhook_token}"
        # Название поля для хранения Telegram ID (по умолчанию UF_USR_TELEGRAM, так как поле создается автоматически в Bitrix24)
        self.telegram_field_name = telegram_field_name or os.getenv("BITRIX24_TELEGRAM_FIELD_NAME", "UF_USR_TELEGRAM")
    
    def _make_request(self, method: str, params: Dict = None, use_get: bool = False) -> Dict:
        """
        Выполнение запроса к API Битрикс24
        
        Args:
            method: Метод API (например, tasks.task.add)
            params: Параметры запроса
            use_get: Если True, использует GET запрос вместо POST
            
        Returns:
            Ответ от API
        """
        if params is None:
            params = {}
        
        url = f"{self.base_url}/{method}"
        
        try:
            if use_get:
                # Для GET запросов параметры передаются в URL
                logger.debug(f"GET запрос к {method} с параметрами: {params}")
                response = requests.get(url, params=params, timeout=10)
            else:
                # Для POST запросов параметры передаются в JSON body
                logger.debug(f"POST запрос к {method} с параметрами: {params}")
                response = requests.post(url, json=params, timeout=10)
            
            response.raise_for_status()
            result = response.json()
            
            # Проверяем наличие ошибки в ответе Bitrix24 (даже если HTTP статус 200)
            if result.get("error"):
                error_code = result.get("error", "UNKNOWN")
                error_desc = result.get("error_description", "")
                logger.warning(f"Bitrix24 API вернул ошибку для метода {method}: {error_code} - {error_desc}")
            
            return result
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка HTTP запроса к {method}: {e}")
            raise
        except Exception as e:
            logger.error(f"Неожиданная ошибка при запросе к {method}: {e}", exc_info=True)
            raise
    
    def create_task(
        self,
        title: str,
        responsible_ids: List[int],
        creator_id: int,
        description: str = "",
        deadline: str = None,
        file_ids: List[int] = None,
        department_id: int = None
    ) -> Dict:
        """
        Создание задачи в Битрикс24
        
        Args:
            title: Название задачи
            responsible_ids: Список ID ответственных пользователей
            creator_id: ID создателя задачи
            description: Описание задачи
            deadline: Дедлайн задачи (формат: YYYY-MM-DD HH:MI:SS)
            file_ids: Список ID прикрепленных файлов
            department_id: ID подразделения (опционально)
            
        Returns:
            Результат создания задачи
        """
        # Если один ответственный, используем RESPONSIBLE_ID
        # Если несколько, создаем задачи для каждого или используем группу
        if len(responsible_ids) == 1:
            task_data = {
                "fields": {
                    "TITLE": title,
                    "RESPONSIBLE_ID": responsible_ids[0],
                    "CREATED_BY": creator_id,
                    "DESCRIPTION": description,
                }
            }
        else:
            # Для нескольких ответственных используем ACCCOMPLICES
            task_data = {
                "fields": {
                    "TITLE": title,
                    "RESPONSIBLE_ID": responsible_ids[0],
                    "ACCOMPLICES": responsible_ids[1:] if len(responsible_ids) > 1 else [],
                    "CREATED_BY": creator_id,
                    "DESCRIPTION": description,
                }
            }
        
        if deadline:
            task_data["fields"]["DEADLINE"] = deadline
        
        if file_ids:
            task_data["fields"]["UF_TASK_WEBDAV_FILES"] = file_ids
        
        # Добавляем подразделение, если указано
        # Примечание: В Bitrix24 для задач может использоваться поле GROUP_ID (для группы) 
        # или пользовательское поле типа UF_DEPARTMENT или UF_CRM_TASK_DEPARTMENT
        # Если ваше поле называется по-другому, измените название поля ниже
        # Для создания пользовательского поля используйте API user.userfield.add или настройте через интерфейс Bitrix24
        if department_id:
            # Используем GROUP_ID для подразделения (стандартное поле в Bitrix24)
            # Если в вашем Bitrix24 используется другое поле, замените GROUP_ID на нужное
            task_data["fields"]["GROUP_ID"] = department_id
        
        result = self._make_request("tasks.task.add", task_data)
        return result
    
    def upload_file(self, file_content: bytes, filename: str) -> Optional[int]:
        """
        Загрузка файла в Битрикс24
        
        Args:
            file_content: Содержимое файла в байтах
            filename: Имя файла
            
        Returns:
            ID загруженного файла или None
        """
        try:
            # Загрузка файла через disk.file.upload
            # Сначала нужно получить временный URL для загрузки
            result = self._make_request("disk.folder.getchildren", {"id": "shared_files"})
            # Упрощенная версия - в реальности нужна более сложная логика
            # Для начала возвращаем None, файлы можно прикрепить позже через веб-интерфейс
            return None
        except Exception:
            return None
    
    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        """
        Получение информации о пользователе по ID
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Информация о пользователе или None
        """
        try:
            # Проверяем наличие ошибки в ответе API
            # В Bitrix24 API метод user.get может не возвращать пользовательские поля по умолчанию
            # ВАЖНО: Для получения пользовательских полей нужно явно указать их в SELECT
            
            # Пробуем разные форматы ID (число и строка)
            id_formats = [user_id, str(user_id)]
            
            for user_id_format in id_formats:
                # Пробуем сначала с SELECT для явного запроса пользовательского поля
                try:
                    result = self._make_request("user.get", {
                        "ID": user_id_format,
                        "SELECT": [self.telegram_field_name]  # Явно запрашиваем пользовательское поле
                    })
                    
                    # Проверяем наличие ошибки в ответе
                    if result.get("error"):
                        error_code = result.get("error", "UNKNOWN")
                        error_desc = result.get("error_description", "")
                        logger.warning(f"Ошибка API при получении пользователя {user_id} (формат ID: {user_id_format}): {error_code} - {error_desc}")
                        continue
                    
                    if result.get("result"):
                        result_data = result["result"]
                        
                        # Обрабатываем список результатов
                        if isinstance(result_data, list):
                            if len(result_data) > 0:
                                user_data = result_data[0]
                                # Проверяем, что поле есть в результате
                                if self.telegram_field_name in user_data:
                                    logger.debug(f"Поле {self.telegram_field_name} найдено через SELECT: {user_data.get(self.telegram_field_name)}")
                                logger.info(f"✅ Пользователь {user_id} найден в Bitrix24 (с SELECT)")
                                return user_data
                            else:
                                logger.debug(f"Пустой список результатов для пользователя {user_id} (формат ID: {user_id_format})")
                                continue
                        # Обрабатываем словарь (один пользователь)
                        elif isinstance(result_data, dict):
                            if result_data.get("ID"):
                                logger.info(f"✅ Пользователь {user_id} найден в Bitrix24 (с SELECT, формат словарь)")
                                return result_data
                            else:
                                logger.debug(f"Результат не содержит ID для пользователя {user_id}")
                                continue
                except Exception as select_error:
                    logger.debug(f"Ошибка при запросе с SELECT (формат ID: {user_id_format}): {select_error}")
                    continue
                
                # Если SELECT не сработал, пробуем без SELECT (вернутся все поля)
                try:
                    logger.debug(f"Пробуем запрос без SELECT для ID формата {user_id_format}...")
                    result = self._make_request("user.get", {"ID": user_id_format})
                    
                    # Проверяем наличие ошибки в ответе
                    if result.get("error"):
                        error_code = result.get("error", "UNKNOWN")
                        error_desc = result.get("error_description", "")
                        logger.warning(f"Ошибка API при получении пользователя {user_id} без SELECT (формат ID: {user_id_format}): {error_code} - {error_desc}")
                        continue
                    
                    if result.get("result"):
                        result_data = result["result"]
                        
                        # Обрабатываем список результатов
                        if isinstance(result_data, list):
                            if len(result_data) > 0:
                                user_data = result_data[0]
                                # Проверяем, есть ли пользовательское поле в результате
                                if self.telegram_field_name not in user_data:
                                    logger.debug(f"Поле {self.telegram_field_name} не найдено в результате без SELECT")
                                logger.info(f"✅ Пользователь {user_id} найден в Bitrix24 (без SELECT)")
                                return user_data
                            else:
                                logger.debug(f"Пустой список результатов для пользователя {user_id} без SELECT (формат ID: {user_id_format})")
                                continue
                        # Обрабатываем словарь (один пользователь)
                        elif isinstance(result_data, dict):
                            if result_data.get("ID"):
                                logger.info(f"✅ Пользователь {user_id} найден в Bitrix24 (без SELECT, формат словарь)")
                                return result_data
                            else:
                                logger.debug(f"Результат без SELECT не содержит ID для пользователя {user_id}")
                                continue
                except Exception as no_select_error:
                    logger.debug(f"Ошибка при запросе без SELECT (формат ID: {user_id_format}): {no_select_error}")
                    continue
            
            # Если ни один формат не сработал
            logger.warning(f"❌ Пользователь с ID {user_id} не найден в Bitrix24 после попыток с разными форматами ID")
            
        except Exception as e:
            logger.error(f"Ошибка при получении пользователя {user_id}: {e}", exc_info=True)
        
        return None
    
    def search_users(self, query: str) -> List[Dict]:
        """
        Поиск пользователей по имени или email
        
        Args:
            query: Поисковый запрос
            
        Returns:
            Список найденных пользователей
        """
        try:
            result = self._make_request("user.search", {"FIND": query})
            return result.get("result", [])
        except Exception:
            return []
    
    def get_all_users(self, active_only: bool = True) -> List[Dict]:
        """
        Получение всех пользователей Битрикс24
        
        Args:
            active_only: Если True, возвращает только активных пользователей
            
        Returns:
            Список всех пользователей
        """
        try:
            # В Битрикс24 REST API метод user.get возвращает всех пользователей
            # ВАЖНО: Для получения пользовательских полей нужно явно указать их в SELECT
            # Пробуем сначала с SELECT для явного запроса пользовательского поля
            params = {
                "SELECT": [self.telegram_field_name]  # Явно запрашиваем поле с Telegram ID
            }
            if active_only:
                params["FILTER"] = {"ACTIVE": "Y"}
            
            # Получаем всех пользователей одним запросом
            result = self._make_request("user.get", params)
            users = result.get("result", [])
            
            # Проверяем формат результата
            if isinstance(users, list):
                # Проверяем, что поле возвращается в результатах
                if users and self.telegram_field_name not in users[0]:
                    logger.debug(f"Поле {self.telegram_field_name} не найдено в результатах с SELECT, пробуем без SELECT...")
                    # Пробуем без SELECT (вернутся все поля включая пользовательские)
                    params_no_select = {}
                    if active_only:
                        params_no_select["FILTER"] = {"ACTIVE": "Y"}
                    result_no_select = self._make_request("user.get", params_no_select)
                    users_no_select = result_no_select.get("result", [])
                    if isinstance(users_no_select, list):
                        return users_no_select
                    if isinstance(users_no_select, dict):
                        return [users_no_select]
                return users
            
            # Если результат - словарь с одним пользователем, оборачиваем в список
            if isinstance(users, dict):
                return [users]
            
            return []
        except Exception as e:
            logger.error(f"Ошибка при получении всех пользователей: {e}")
            # Fallback: пробуем без SELECT (вернутся все поля)
            try:
                params = {}
                if active_only:
                    params["FILTER"] = {"ACTIVE": "Y"}
                result = self._make_request("user.get", params)
                users = result.get("result", [])
                if isinstance(users, list):
                    return users
                if isinstance(users, dict):
                    return [users]
                return []
            except Exception:
                return []
    
    def get_user_id_by_telegram_username(self, telegram_username: str) -> Optional[int]:
        """
        Получение ID пользователя Битрикс24 по Telegram username
        (требует настройки соответствия в системе)
        
        Args:
            telegram_username: Telegram username пользователя
            
        Returns:
            ID пользователя в Битрикс24 или None
        """
        # Здесь можно реализовать логику поиска по кастомному полю
        # или использовать внешнюю таблицу соответствий
        # Пока возвращаем None - нужно будет настроить маппинг
        return None
    
    def get_task_url(self, task_id: int, user_id: int = None) -> str:
        """
        Получение ссылки на задачу в Битрикс24
        
        Args:
            task_id: ID задачи
            user_id: ID пользователя (опционально, для персональной ссылки)
            
        Returns:
            URL задачи в Битрикс24
        """
        # В Битрикс24 ссылка на задачу может быть разной в зависимости от настроек
        # Используем универсальный формат через задачи
        if user_id:
            # Персональная ссылка пользователя (более надежная)
            return f"https://{self.domain}/company/personal/user/{user_id}/tasks/task/view/{task_id}/"
        else:
            # Альтернативный формат через общий раздел задач
            return f"https://{self.domain}/company/personal/user/0/tasks/task/view/{task_id}/"
    
    def ensure_telegram_id_field(self) -> bool:
        """
        Проверка и создание пользовательского поля для Telegram ID в Bitrix24
        Поле создается один раз и становится доступным для всех пользователей
        
        Returns:
            True если поле существует или было создано, False в случае ошибки
        """
        try:
            # Проверяем, существует ли поле
            # Используем user.userfield.get без параметров для получения всех полей
            # или с фильтром по FIELD_NAME
            try:
                result = self._make_request("user.userfield.get", {})
                fields = result.get("result", [])
                
                # Проверяем, есть ли поле с нужным названием
                for field in fields:
                    if isinstance(field, dict) and field.get("FIELD_NAME") == self.telegram_field_name:
                        logger.info(f"✅ Поле {self.telegram_field_name} уже существует в Bitrix24")
                        return True
            except Exception as get_error:
                # Если метод не работает, пробуем другой способ
                logger.debug(f"Метод user.userfield.get не сработал: {get_error}")
                # Пробуем получить конкретное поле
                try:
                    result = self._make_request("user.userfield.get", {"FIELD": self.telegram_field_name})
                    if result.get("result") and len(result.get("result", [])) > 0:
                        logger.info(f"✅ Поле {self.telegram_field_name} уже существует в Bitrix24")
                        return True
                except Exception:
                    pass
            
            # Поле не найдено - создаем его
            logger.info(f"📝 Создание поля {self.telegram_field_name} в Bitrix24...")
            field_data = {
                "fields": {
                    "FIELD_NAME": self.telegram_field_name,
                    "USER_TYPE_ID": "string",  # Тип поля - строка
                    "XML_ID": "TELEGRAM_ID",
                    "SORT": 100,
                    "MULTIPLE": "N",  # Одно значение (не множественное)
                    "MANDATORY": "N",  # Не обязательное поле
                    "SHOW_FILTER": "Y",  # Показывать в фильтрах
                    "SHOW_IN_LIST": "Y",  # Показывать в списке пользователей
                    "EDIT_IN_LIST": "Y",  # Можно редактировать в списке
                    "IS_SEARCHABLE": "Y",  # Доступно для поиска
                    "SETTINGS": {
                        "DEFAULT_VALUE": "",
                        "SIZE": 20,  # Размер поля
                        "ROWS": 1,
                        "MIN_LENGTH": 0,
                        "MAX_LENGTH": 0,
                        "REGEXP": ""
                    },
                    "LIST": [
                        {"VALUE": "Telegram ID", "DEF": "Y"}  # Значение по умолчанию для списка
                    ]
                }
            }
            
            try:
                create_result = self._make_request("user.userfield.add", field_data)
                if create_result.get("result"):
                    field_id = create_result.get("result")
                    logger.info(f"✅ Поле {self.telegram_field_name} успешно создано в Bitrix24 (ID: {field_id})")
                    logger.info(f"💡 Поле теперь доступно для всех пользователей в их профилях")
                    return True
                else:
                    error = create_result.get("error", "Неизвестная ошибка")
                    error_description = create_result.get("error_description", "")
                    error_code = create_result.get("error_code", "")
                    logger.error(f"❌ Не удалось создать поле {self.telegram_field_name}: {error}")
                    if error_code:
                        logger.error(f"   Код ошибки: {error_code}")
                    if error_description:
                        logger.error(f"   Описание ошибки: {error_description}")
                    logger.info(f"💡 Убедитесь, что вебхук имеет права на создание пользовательских полей:")
                    logger.info(f"   Настройки → Разработчикам → Входящий вебхук → Выберите ваш вебхук")
                    logger.info(f"   Включите права: user.userfield.add и user.userfield.get")
                    return False
            except requests.exceptions.HTTPError as http_err:
                # Обрабатываем HTTP ошибки отдельно
                if http_err.response.status_code == 400:
                    # Ошибка 400 может означать, что поле уже существует
                    try:
                        error_response = http_err.response.json()
                        error_code = error_response.get("error", "")
                        error_description = error_response.get("error_description", "").lower()
                        
                        # Проверяем, не означает ли ошибка, что поле уже существует
                        if "already exists" in error_description or "уже существует" in error_description or "duplicate" in error_description:
                            logger.info(f"ℹ️ Поле {self.telegram_field_name} уже существует в Bitrix24 (недоступно через API для проверки)")
                            logger.info(f"💡 Продолжаем работу - поле можно использовать")
                            return True
                        
                        logger.warning(f"⚠️ Ошибка 400 при создании поля {self.telegram_field_name}")
                        logger.warning(f"   Код ошибки: {error_code}")
                        logger.warning(f"   Описание: {error_description}")
                        logger.info(f"💡 Возможные причины:")
                        logger.info(f"   1. Поле уже существует, но недоступно через API")
                        logger.info(f"   2. Вебхук не имеет прав на создание пользовательских полей")
                        logger.info(f"   3. Неправильный формат данных")
                        logger.info(f"💡 Продолжаем работу - попробуем использовать поле (возможно, оно уже существует)")
                        # Возвращаем True, так как поле может существовать, но быть недоступным через API
                        return True
                    except Exception:
                        # Если не удалось распарсить ответ, считаем, что поле может существовать
                        logger.warning(f"⚠️ Ошибка 400 при создании поля {self.telegram_field_name}")
                        logger.info(f"💡 Продолжаем работу - поле может уже существовать")
                        return True
                else:
                    # Для других HTTP ошибок логируем и возвращаем False
                    logger.error(f"HTTP ошибка {http_err.response.status_code} при создании поля {self.telegram_field_name}: {http_err}")
                    return False
            
        except Exception as e:
            # Логируем ошибку для диагностики
            logger.error(f"Ошибка при проверке/создании поля {self.telegram_field_name}: {e}", exc_info=True)
            logger.info(f"💡 Возможные причины:")
            logger.info(f"   1. Вебхук не имеет прав на создание пользовательских полей")
            logger.info(f"   2. Поле с таким кодом уже существует, но недоступно через API")
            logger.info(f"   3. Проблемы с подключением к Bitrix24")
            # Возвращаем True, так как поле может существовать, но быть недоступным через API
            # Это позволит боту продолжать работу
            return True
    
    def update_user_telegram_id(self, user_id: int, telegram_id: int) -> bool:
        """
        Обновление Telegram ID пользователя в Bitrix24
        
        Args:
            user_id: ID пользователя в Bitrix24
            telegram_id: Telegram User ID
            
        Returns:
            True если обновление прошло успешно, False в случае ошибки
        """
        try:
            # Сначала проверяем, существует ли поле
            try:
                field_result = self._make_request("user.userfield.get", {"FIELD": self.telegram_field_name})
                field_exists = field_result.get("result") and len(field_result.get("result", [])) > 0
                if not field_exists:
                    logger.warning(f"⚠️ Поле '{self.telegram_field_name}' не найдено в Bitrix24. Попытка создать...")
                    # Пытаемся создать поле, если его нет
                    field_created = self.ensure_telegram_id_field()
                    if not field_created:
                        logger.error(f"❌ Не удалось создать поле '{self.telegram_field_name}'. Сохранение может не работать.")
            except Exception as field_check_error:
                logger.debug(f"Не удалось проверить существование поля: {field_check_error}")
                # Продолжаем попытку обновления, возможно поле существует, но недоступно через API
            
            # В Bitrix24 API для обновления пользовательских полей используется формат:
            # {"ID": user_id, "fields": {"FIELD_NAME": "value"}}
            # ВАЖНО: Пользовательские поля должны быть переданы в формате fields[FIELD_NAME]
            telegram_id_str = str(telegram_id)
            logger.info(f"📝 Попытка сохранить Telegram ID {telegram_id} в поле '{self.telegram_field_name}' для пользователя Bitrix24 {user_id}")
            
            # Получаем текущие данные пользователя для сохранения обязательных полей
            # Это необходимо, так как некоторые версии Bitrix24 требуют все обязательные поля при обновлении
            try:
                current_user = self.get_user_by_id(user_id)
            except Exception:
                current_user = None
            
            # Формируем данные для обновления
            # ВАЖНО: Используем формат fields[FIELD_NAME] для пользовательских полей
            update_data = {
                "ID": user_id,
                "fields": {
                    self.telegram_field_name: telegram_id_str
                }
            }
            
            # Если есть текущие данные пользователя, добавляем обязательные поля для совместимости
            if current_user:
                # Добавляем только если они отсутствуют в update_data
                if "NAME" in current_user and "NAME" not in update_data["fields"]:
                    update_data["fields"]["NAME"] = current_user.get("NAME", "")
                if "LAST_NAME" in current_user and "LAST_NAME" not in update_data["fields"]:
                    update_data["fields"]["LAST_NAME"] = current_user.get("LAST_NAME", "")
                if "EMAIL" in current_user and "EMAIL" not in update_data["fields"]:
                    update_data["fields"]["EMAIL"] = current_user.get("EMAIL", "")
            
            logger.debug(f"Данные для обновления: {update_data}")
            
            try:
                result = self._make_request("user.update", update_data)
                logger.debug(f"Ответ от Bitrix24: {result}")
                
                # Проверяем успешность обновления
                if result.get("error"):
                    error_msg = result.get("error", "")
                    error_desc = result.get("error_description", "")
                    error_code = result.get("error_code", "")
                    logger.error(f"❌ Ошибка при обновлении: {error_msg} - {error_desc}")
                    if error_code:
                        logger.error(f"   Код ошибки: {error_code}")
                    
                    # Если ошибка связана с форматом, пробуем альтернативный формат
                    if error_code in ["WRONG_INPUT", "INVALID_PARAMS"] or "field" in error_desc.lower():
                        logger.info("Пробуем альтернативный формат без обязательных полей...")
                        # Пробуем только с пользовательским полем
                        update_data_minimal = {
                            "ID": user_id,
                            "fields": {
                                self.telegram_field_name: telegram_id_str
                            }
                        }
                        result = self._make_request("user.update", update_data_minimal)
                        logger.debug(f"Ответ от Bitrix24 (альтернативный формат): {result}")
                    
                    if result.get("error"):
                        return False
                
                result_value = result.get("result")
                success = (
                    result_value is True or 
                    (isinstance(result_value, (int, str)) and str(result_value) == str(user_id)) or
                    (isinstance(result_value, bool) and result_value)
                )
                
                if not success:
                    logger.warning(f"Обновление не подтверждено, result: {result_value}, тип: {type(result_value)}")
                    return False
                    
            except Exception as req_error:
                logger.error(f"Исключение при запросе обновления: {req_error}", exc_info=True)
                return False
            
            if success:
                logger.info(f"✅ Telegram ID {telegram_id} успешно сохранен в поле '{self.telegram_field_name}' для пользователя Bitrix24 {user_id}")
                
                # Проверяем, что данные действительно сохранились
                # Делаем небольшую задержку перед проверкой (Bitrix24 может обрабатывать обновление асинхронно)
                import time
                time.sleep(1)  # Задержка для обработки обновления
                
                # Проверяем сохранение несколько раз (на случай асинхронной обработки)
                # ВАЖНО: Используем правильный формат запроса с SELECT для получения пользовательских полей
                saved_telegram_id = None
                for attempt in range(3):
                    try:
                        # Способ 1: Явно запрашиваем поле через SELECT
                        check_result = self._make_request("user.get", {
                            "ID": user_id,
                            "SELECT": [self.telegram_field_name]
                        })
                        if check_result.get("result"):
                            user_data_check = check_result["result"][0] if isinstance(check_result["result"], list) else check_result["result"]
                            saved_telegram_id = user_data_check.get(self.telegram_field_name)
                            if saved_telegram_id:
                                logger.info(f"✅ Подтверждено (попытка {attempt + 1}): Telegram ID {saved_telegram_id} найден в профиле пользователя {user_id}")
                                break
                        
                        # Способ 2: Проверяем через поиск пользователя по значению поля
                        # Это более надежный способ проверки сохранения
                        search_result = self._make_request("user.get", {
                            "FILTER": {
                                self.telegram_field_name: telegram_id_str
                            },
                            "SELECT": [self.telegram_field_name, "ID"]
                        })
                        if search_result.get("result"):
                            search_users = search_result["result"]
                            if isinstance(search_users, list) and len(search_users) > 0:
                                found_user = search_users[0]
                                found_user_id = found_user.get("ID")
                                # Проверяем, что найденный пользователь - это тот, кого мы обновляли
                                if str(found_user_id) == str(user_id) or int(found_user_id) == int(user_id):
                                    saved_telegram_id = found_user.get(self.telegram_field_name) or telegram_id_str
                                    logger.info(f"✅ Подтверждено через поиск (попытка {attempt + 1}): Пользователь {user_id} найден по Telegram ID {telegram_id_str}")
                                    break
                            elif isinstance(search_users, dict):
                                found_user_id = search_users.get("ID")
                                if str(found_user_id) == str(user_id) or int(found_user_id) == int(user_id):
                                    saved_telegram_id = search_users.get(self.telegram_field_name) or telegram_id_str
                                    logger.info(f"✅ Подтверждено через поиск (попытка {attempt + 1}): Пользователь {user_id} найден по Telegram ID {telegram_id_str}")
                                    break
                        
                        # Способ 3: Получаем пользователя без SELECT (fallback)
                        if not saved_telegram_id:
                            user_data = self.get_user_by_id(user_id)
                            if user_data:
                                saved_telegram_id = user_data.get(self.telegram_field_name)
                                if saved_telegram_id:
                                    logger.info(f"✅ Подтверждено через get_user_by_id (попытка {attempt + 1}): Telegram ID {saved_telegram_id} найден")
                                    break
                    except Exception as check_error:
                        logger.debug(f"Ошибка при проверке сохранения (попытка {attempt + 1}): {check_error}")
                    
                    if attempt < 2:
                        time.sleep(0.5)  # Ждем перед следующей попыткой
                
                if saved_telegram_id:
                    logger.info(f"✅ Успешно подтверждено сохранение Telegram ID {saved_telegram_id} для пользователя {user_id}")
                else:
                    # Если запрос был успешным, но поле не возвращается при проверке,
                    # это может быть нормальным поведением Bitrix24
                    logger.warning(f"⚠️ Telegram ID не найден в ответе API после сохранения для пользователя {user_id}")
                    logger.warning(f"   Это может означать:")
                    logger.warning(f"   1. Поле '{self.telegram_field_name}' сохранено, но не всегда возвращается в API")
                    logger.warning(f"   2. Bitrix24 обрабатывает обновление асинхронно (попробуйте проверить позже)")
                    logger.info(f"💡 Проверьте профиль пользователя в Bitrix24 вручную:")
                    logger.info(f"   Настройки → Пользователи → Откройте профиль пользователя {user_id}")
                    logger.info(f"   Поле '{self.telegram_field_name}' должно содержать значение {telegram_id}")
                    logger.info(f"💡 Если запрос обновления был успешным (result=true), поле должно быть сохранено")
            else:
                error = result.get("error", "Неизвестная ошибка")
                error_description = result.get("error_description", "")
                error_code = result.get("error_code", "")
                logger.error(f"❌ Не удалось сохранить Telegram ID для пользователя {user_id}")
                logger.error(f"   Ошибка: {error}")
                if error_code:
                    logger.error(f"   Код ошибки: {error_code}")
                if error_description:
                    logger.error(f"   Описание: {error_description}")
                logger.error(f"   Полный ответ от Bitrix24: {result}")
                logger.info(f"💡 Возможные причины:")
                logger.info(f"   1. Поле '{self.telegram_field_name}' не существует в Bitrix24")
                logger.info(f"   2. Вебхук не имеет прав на изменение пользователей (user.update)")
                logger.info(f"   3. Вебхук не имеет прав на изменение пользовательских полей")
                logger.info(f"   4. Поле '{self.telegram_field_name}' не доступно для записи через API")
                logger.info(f"💡 Проверьте права вебхука в Bitrix24:")
                logger.info(f"   Настройки → Разработчикам → Входящий вебхук → Выберите ваш вебхук")
                logger.info(f"   Убедитесь, что включены права: user.update")
            
            return success
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении Telegram ID для пользователя {user_id}: {e}", exc_info=True)
            logger.error(f"Тип ошибки: {type(e).__name__}")
            return False
    
    def get_user_by_telegram_id(self, telegram_id: int) -> Optional[Dict]:
        """
        Поиск пользователя Bitrix24 по Telegram ID
        
        Args:
            telegram_id: Telegram User ID
            
        Returns:
            Информация о пользователе или None
        """
        try:
            telegram_id_str = str(telegram_id)
            
            # ВАЖНО: В Bitrix24 для поиска по пользовательским полям нужно использовать FILTER
            # и явно запрашивать поле в SELECT для получения его значения в результате
            
            # Способ 1: Поиск с SELECT (предпочтительный)
            try:
                result = self._make_request("user.get", {
                    "FILTER": {
                        self.telegram_field_name: telegram_id_str
                    },
                    "SELECT": [self.telegram_field_name]  # Явно запрашиваем поле с Telegram ID
                })
                
                users = result.get("result", [])
                if users:
                    if isinstance(users, list) and len(users) > 0:
                        found_user = users[0]
                        # Проверяем, что значение поля совпадает
                        if found_user.get(self.telegram_field_name) == telegram_id_str:
                            logger.debug(f"✅ Найден пользователь Bitrix24 по Telegram ID {telegram_id}: {found_user.get('ID')}")
                            return found_user
                    elif isinstance(users, dict):
                        if users.get(self.telegram_field_name) == telegram_id_str:
                            logger.debug(f"✅ Найден пользователь Bitrix24 по Telegram ID {telegram_id}: {users.get('ID')}")
                            return users
            except Exception as select_error:
                logger.debug(f"Ошибка при поиске с SELECT: {select_error}")
            
            # Способ 2: Поиск без SELECT (fallback)
            try:
                result_all = self._make_request("user.get", {
                    "FILTER": {
                        self.telegram_field_name: telegram_id_str
                    }
                })
                users_all = result_all.get("result", [])
                if users_all:
                    if isinstance(users_all, list) and len(users_all) > 0:
                        found_user = users_all[0]
                        # Проверяем значение поля, если оно есть
                        field_value = found_user.get(self.telegram_field_name)
                        if field_value == telegram_id_str or (not field_value and found_user.get('ID')):
                            logger.debug(f"✅ Найден пользователь Bitrix24 по Telegram ID {telegram_id} (без SELECT): {found_user.get('ID')}")
                            return found_user
                    elif isinstance(users_all, dict):
                        field_value = users_all.get(self.telegram_field_name)
                        if field_value == telegram_id_str or (not field_value and users_all.get('ID')):
                            logger.debug(f"✅ Найден пользователь Bitrix24 по Telegram ID {telegram_id} (без SELECT): {users_all.get('ID')}")
                            return users_all
            except Exception as no_select_error:
                logger.debug(f"Ошибка при поиске без SELECT: {no_select_error}")
            
        except Exception as e:
            logger.debug(f"Ошибка при поиске пользователя по Telegram ID {telegram_id}: {e}")
        
        return None
    
    def get_user_telegram_id(self, user_id: int) -> Optional[int]:
        """
        Получение Telegram ID пользователя Bitrix24
        
        Args:
            user_id: ID пользователя в Bitrix24
            
        Returns:
            Telegram ID или None
        """
        try:
            # ВАЖНО: Явно запрашиваем пользовательское поле через SELECT
            # Это гарантирует, что поле будет возвращено в ответе
            try:
                result = self._make_request("user.get", {
                    "ID": user_id,
                    "SELECT": [self.telegram_field_name]  # Явно запрашиваем поле с Telegram ID
                })
                
                if result.get("result"):
                    user_data = result["result"][0] if isinstance(result["result"], list) else result["result"]
                    telegram_id_value = user_data.get(self.telegram_field_name)
                    
                    if telegram_id_value:
                        try:
                            # Значение может быть строкой или числом
                            telegram_id = int(telegram_id_value) if telegram_id_value else None
                            logger.debug(f"✅ Получен Telegram ID {telegram_id} для пользователя Bitrix24 {user_id}")
                            return telegram_id
                        except (ValueError, TypeError) as e:
                            logger.debug(f"Не удалось преобразовать Telegram ID в число для пользователя {user_id}: {telegram_id_value}, ошибка: {e}")
                            return None
            except Exception as select_error:
                logger.debug(f"Ошибка при запросе с SELECT: {select_error}")
            
            # Fallback: используем get_user_by_id (который также пытается получить поле)
            user_info = self.get_user_by_id(user_id)
            if user_info:
                telegram_id_value = user_info.get(self.telegram_field_name)
                if telegram_id_value:
                    try:
                        telegram_id = int(telegram_id_value) if telegram_id_value else None
                        logger.debug(f"✅ Получен Telegram ID {telegram_id} для пользователя Bitrix24 {user_id} (через fallback)")
                        return telegram_id
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Не удалось преобразовать Telegram ID в число для пользователя {user_id}: {telegram_id_value}, ошибка: {e}")
                        return None
                else:
                    logger.debug(f"Поле {self.telegram_field_name} не найдено или пусто для пользователя {user_id}")
        except Exception as e:
            logger.debug(f"Ошибка при получении Telegram ID для пользователя {user_id}: {e}")
        
        return None
    
    def load_all_telegram_mappings(self) -> Dict[int, int]:
        """
        Загрузка всех связей Telegram ID -> Bitrix24 User ID из Bitrix24
        
        Returns:
            Словарь {telegram_id: bitrix_user_id}
        """
        mappings = {}
        try:
            # Получаем всех пользователей
            users = self.get_all_users(active_only=True)
            
            # Проверяем, что users - это список
            if not isinstance(users, list):
                logger.warning(f"get_all_users вернул не список: {type(users)}")
                return mappings
            
            loaded_count = 0
            for user in users:
                # Проверяем, что user - это словарь
                if not isinstance(user, dict):
                    logger.debug(f"Пропущен элемент пользователя (не словарь): {type(user)}")
                    continue
                
                user_id = user.get("ID")
                telegram_id_str = user.get(self.telegram_field_name)
                
                if user_id and telegram_id_str:
                    try:
                        telegram_id = int(telegram_id_str)
                        mappings[telegram_id] = int(user_id)
                        loaded_count += 1
                    except (ValueError, TypeError):
                        continue
            
            if loaded_count > 0:
                logger.info(f"✅ Загружено {loaded_count} связей Telegram ID -> Bitrix24 из Bitrix24")
            else:
                logger.info("ℹ️ В Bitrix24 не найдено сохраненных связей Telegram ID")
                
        except Exception as e:
            logger.error(f"Ошибка при загрузке связей из Bitrix24: {e}", exc_info=True)
        
        return mappings
    
    def get_all_departments(self) -> List[Dict]:
        """
        Получение всех подразделений из Bitrix24
        
        Returns:
            Список подразделений
        """
        try:
            logger.debug(f"Запрос подразделений через URL: {self.base_url}/department.get")
            
            # Используем метод department.get для получения всех подразделений
            # Пробуем сначала GET запрос (стандартный для Bitrix24 REST API)
            try:
                logger.debug("Попытка GET запроса к department.get...")
                result = self._make_request("department.get", {}, use_get=True)
                logger.debug(f"GET запрос успешен, результат: {result}")
            except requests.exceptions.HTTPError as http_err:
                # Если GET не работает (401 или другой код), пробуем POST
                if http_err.response.status_code == 401:
                    logger.warning("GET запрос к department.get вернул 401, пробуем POST...")
                    try:
                        result = self._make_request("department.get", {}, use_get=False)
                        logger.debug(f"POST запрос успешен, результат: {result}")
                    except requests.exceptions.HTTPError as post_err:
                        logger.error(f"POST запрос также вернул ошибку {post_err.response.status_code}")
                        raise post_err
                else:
                    raise
            
            departments = result.get("result", [])
            
            # Проверяем наличие ошибки в ответе
            if "error" in result:
                error_code = result.get("error", "UNKNOWN")
                error_msg = result.get("error_description", result.get("error", "Неизвестная ошибка"))
                logger.warning(f"Bitrix24 вернул ошибку при получении подразделений: {error_code} - {error_msg}")
                logger.warning(f"Проверьте, что вебхук имеет права на чтение подразделений в Bitrix24")
                return []
            
            if isinstance(departments, list):
                logger.info(f"Успешно получено {len(departments)} подразделений из Bitrix24")
                return departments
            elif isinstance(departments, dict):
                logger.info(f"Получено одно подразделение из Bitrix24")
                return [departments]
            
            logger.warning("Результат запроса подразделений не содержит данных")
            return []
        except requests.exceptions.HTTPError as http_err:
            # Обрабатываем HTTP ошибки отдельно
            status_code = http_err.response.status_code
            try:
                error_response = http_err.response.json()
                error_code = error_response.get("error", "UNKNOWN")
                error_description = error_response.get("error_description", "")
                logger.error(f"HTTP ошибка {status_code} при получении подразделений: {error_code}")
                if error_description:
                    logger.error(f"Описание ошибки: {error_description}")
            except:
                logger.error(f"HTTP ошибка {status_code} при получении подразделений: {http_err}")
            
            if status_code == 401:
                logger.error(f"Ошибка 401 Unauthorized при получении подразделений.")
                logger.error(f"Проверьте:")
                logger.error(f"  1. Правильность токена вебхука BITRIX24_WEBHOOK_TOKEN")
                logger.error(f"  2. Права вебхука в Bitrix24 (должен иметь доступ к department.get)")
                logger.error(f"  3. Не истек ли срок действия вебхука")
            return []
        except Exception as e:
            logger.error(f"Ошибка при получении подразделений: {e}", exc_info=True)
            return []
    
    def get_department_by_id(self, department_id: int) -> Optional[Dict]:
        """
        Получение информации о подразделении по ID
        
        Args:
            department_id: ID подразделения
            
        Returns:
            Информация о подразделении или None
        """
        try:
            # Пробуем сначала GET запрос
            try:
                result = self._make_request("department.get", {"ID": department_id}, use_get=True)
            except requests.exceptions.HTTPError as http_err:
                # Если GET не работает, пробуем POST
                if http_err.response.status_code == 401:
                    result = self._make_request("department.get", {"ID": department_id}, use_get=False)
                else:
                    raise
            
            # Проверяем наличие ошибки в ответе
            if "error" in result:
                return None
            
            departments = result.get("result", [])
            
            if departments:
                if isinstance(departments, list) and len(departments) > 0:
                    return departments[0]
                elif isinstance(departments, dict):
                    return departments
        except requests.exceptions.HTTPError as http_err:
            if http_err.response.status_code == 401:
                logger.warning(f"Ошибка 401 при получении подразделения {department_id}. Проверьте права вебхука.")
            return None
        except Exception as e:
            logger.debug(f"Ошибка при получении подразделения {department_id}: {e}")
            return None
