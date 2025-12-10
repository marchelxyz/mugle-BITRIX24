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
    
    def _make_request(self, method: str, params: Dict = None, use_get: bool = False, files: Dict = None) -> Dict:
        """
        Выполнение запроса к API Битрикс24
        
        Args:
            method: Метод API (например, tasks.task.add)
            params: Параметры запроса
            use_get: Если True, использует GET запрос вместо POST
            files: Словарь файлов для multipart/form-data запроса
            
        Returns:
            Ответ от API
        """
        if params is None:
            params = {}
        
        url = f"{self.base_url}/{method}"
        
        if use_get:
            # Для GET запросов параметры передаются в URL
            response = requests.get(url, params=params)
        elif files:
            # Для POST запросов с файлами используем multipart/form-data
            response = requests.post(url, data=params, files=files)
        else:
            # Для POST запросов параметры передаются в JSON body
            response = requests.post(url, json=params)
        
        response.raise_for_status()
        return response.json()
    
    def create_task(
        self,
        title: str,
        responsible_ids: List[int],
        creator_id: int,
        description: str = "",
        deadline: str = None,
        file_ids: List[int] = None,
        department_id: int = None,
        files: List[tuple] = None
    ) -> Dict:
        """
        Создание задачи в Битрикс24
        
        Args:
            title: Название задачи
            responsible_ids: Список ID ответственных пользователей
            creator_id: ID создателя задачи
            description: Описание задачи
            deadline: Дедлайн задачи (формат: YYYY-MM-DD HH:MI:SS)
            file_ids: Список ID прикрепленных файлов (если файлы уже загружены на диск)
            department_id: ID подразделения (опционально)
            files: Список кортежей (filename, file_content) для прямого прикрепления файлов к задаче
            
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
        
        # Добавляем подразделение, если указано
        # Примечание: В Bitrix24 для задач может использоваться поле GROUP_ID (для группы) 
        # или пользовательское поле типа UF_DEPARTMENT или UF_CRM_TASK_DEPARTMENT
        # Если ваше поле называется по-другому, измените название поля ниже
        # Для создания пользовательского поля используйте API user.userfield.add или настройте через интерфейс Bitrix24
        if department_id:
            # Используем GROUP_ID для подразделения (стандартное поле в Bitrix24)
            # Если в вашем Bitrix24 используется другое поле, замените GROUP_ID на нужное
            task_data["fields"]["GROUP_ID"] = department_id
        
        # Если файлы переданы напрямую, пробуем прикрепить их при создании задачи
        if files and not file_ids:
            logger.info(f"Попытка прямого прикрепления {len(files)} файлов к задаче при создании")
            result = self._create_task_with_files(task_data, files)
            if result:
                return result
            logger.warning("Прямое прикрепление файлов не сработало, пробуем загрузить на диск")
            # Если прямой способ не сработал, загружаем файлы на диск
            file_ids = []
            for filename, file_content in files:
                file_id = self.upload_file(file_content, filename)
                if file_id:
                    file_ids.append(file_id)
        
        # Прикрепляем файлы через ID (если они были загружены на диск)
        if file_ids:
            logger.info(f"Прикрепление {len(file_ids)} файлов к задаче через ID")
            # Пробуем разные форматы для прикрепления файлов
            # Формат 1: UF_TASK_WEBDAV_FILES (стандартный)
            task_data["fields"]["UF_TASK_WEBDAV_FILES"] = file_ids
            # Также пробуем FILES (альтернативный формат)
            # task_data["fields"]["FILES"] = file_ids
        
        result = self._make_request("tasks.task.add", task_data)
        return result
    
    def _create_task_with_files(self, task_data: Dict, files: List[tuple]) -> Optional[Dict]:
        """
        Создание задачи с прямым прикреплением файлов через multipart/form-data
        
        В Bitrix24 файлы можно прикрепить напрямую к задаче при создании через поле FILES
        """
        try:
            url = f"{self.base_url}/tasks.task.add"
            
            # Подготавливаем данные задачи в формате для multipart
            form_data = {}
            for key, value in task_data.get("fields", {}).items():
                if isinstance(value, list):
                    # Для массивов (например, ACCOMPLICES) передаем каждый элемент отдельно
                    for i, item in enumerate(value):
                        form_data[f"fields[{key}][{i}]"] = str(item)
                else:
                    form_data[f"fields[{key}]"] = str(value)
            
            # Подготавливаем файлы для multipart
            # В Bitrix24 файлы передаются через поле FILES с индексами
            files_dict = {}
            for i, (filename, file_content) in enumerate(files):
                # Определяем MIME тип по расширению файла
                import mimetypes
                mime_type, _ = mimetypes.guess_type(filename)
                if not mime_type:
                    mime_type = 'application/octet-stream'
                
                files_dict[f"FILES[{i}]"] = (filename, file_content, mime_type)
            
            logger.debug(f"Попытка создания задачи с {len(files)} файлами через multipart/form-data")
            response = requests.post(url, data=form_data, files=files_dict)
            response.raise_for_status()
            result = response.json()
            
            if result.get("result"):
                logger.info(f"✅ Задача создана с прямым прикреплением {len(files)} файлов")
                return result
            
            error = result.get("error", "")
            error_description = result.get("error_description", "")
            if error:
                logger.debug(f"Ошибка при создании задачи с файлами: {error} - {error_description}")
            
            return None
            
        except Exception as e:
            logger.debug(f"Ошибка при прямом прикреплении файлов к задаче: {e}")
            return None
    
    def upload_file(self, file_content: bytes, filename: str, folder_id: str = "shared_files") -> Optional[int]:
        """
        Загрузка файла в Битрикс24
        
        Args:
            file_content: Содержимое файла в байтах
            filename: Имя файла
            folder_id: ID папки для загрузки (по умолчанию "shared_files" - общие файлы)
            
        Returns:
            ID загруженного файла или None
        """
        logger.info(f"📤 Начинаем загрузку файла {filename} (размер: {len(file_content)} байт) в папку {folder_id}")
        
        # Пробуем сначала через disk.folder.uploadfile (правильный метод)
        logger.debug(f"Попытка 1: Загрузка через disk.folder.uploadfile")
        result = self._upload_file_via_disk_folder(file_content, filename, folder_id)
        if result:
            logger.info(f"✅ Файл {filename} успешно загружен (ID: {result})")
            return result
        
        # Если не сработало, пробуем через multipart/form-data
        logger.debug(f"Попытка 2: Загрузка через multipart/form-data")
        result = self._upload_file_via_multipart(file_content, filename, folder_id)
        if result:
            logger.info(f"✅ Файл {filename} успешно загружен через multipart (ID: {result})")
            return result
        
        # Если ничего не сработало, пробуем получить реальный ID папки shared_files
        logger.debug(f"Попытка 3: Получение ID папки shared_files и повторная попытка")
        real_folder_id = self._get_shared_files_folder_id()
        if real_folder_id and real_folder_id != folder_id:
            logger.info(f"Найден ID папки shared_files: {real_folder_id}, пробуем загрузить снова")
            result = self._upload_file_via_disk_folder(file_content, filename, real_folder_id)
            if result:
                logger.info(f"✅ Файл {filename} успешно загружен с реальным ID папки (ID: {result})")
                return result
        
        # Если ничего не сработало, пробуем старый метод как fallback
        logger.debug(f"Попытка 4: Использование старого метода")
        result = self._upload_file_alternative(file_content, filename, folder_id)
        if result:
            logger.info(f"✅ Файл {filename} успешно загружен через старый метод (ID: {result})")
            return result
        
        logger.error(f"❌ Не удалось загрузить файл {filename} ни одним из методов")
        logger.error(f"💡 Проверьте права вебхука на загрузку файлов (disk)")
        return None
    
    def _upload_file_via_disk_folder(self, file_content: bytes, filename: str, folder_id: str) -> Optional[int]:
        """
        Загрузка файла через disk.folder.uploadfile (правильный метод Bitrix24)
        """
        try:
            import base64
            
            file_base64 = base64.b64encode(file_content).decode('utf-8')
            
            # Пробуем разные форматы для disk.folder.uploadfile
            # Формат 1: с data[NAME] (стандартный формат Bitrix24)
            upload_data_v1 = {
                "id": folder_id,
                "data[NAME]": filename,
                "fileContent": file_base64
            }
            
            try:
                result = self._make_request("disk.folder.uploadfile", upload_data_v1)
                
                if result.get("result"):
                    file_data = result["result"]
                    file_id = None
                    if isinstance(file_data, dict):
                        file_id = file_data.get("ID") or file_data.get("id")
                    elif isinstance(file_data, (int, str)):
                        file_id = file_data
                    
                    if file_id:
                        logger.info(f"✅ Файл {filename} успешно загружен через disk.folder.uploadfile (ID: {file_id})")
                        return int(file_id)
                
                error = result.get("error", "")
                error_description = result.get("error_description", "")
                if error:
                    logger.debug(f"disk.folder.uploadfile (формат 1) вернул ошибку: {error} - {error_description}")
            except Exception as e1:
                logger.debug(f"Ошибка при загрузке через формат 1: {e1}")
            
            # Формат 2: с data как объект
            upload_data_v2 = {
                "id": folder_id,
                "data": {
                    "NAME": filename
                },
                "fileContent": file_base64
            }
            
            try:
                result = self._make_request("disk.folder.uploadfile", upload_data_v2)
                
                if result.get("result"):
                    file_data = result["result"]
                    file_id = None
                    if isinstance(file_data, dict):
                        file_id = file_data.get("ID") or file_data.get("id")
                    elif isinstance(file_data, (int, str)):
                        file_id = file_data
                    
                    if file_id:
                        logger.info(f"✅ Файл {filename} успешно загружен через disk.folder.uploadfile формат 2 (ID: {file_id})")
                        return int(file_id)
                
                error = result.get("error", "")
                error_description = result.get("error_description", "")
                if error:
                    logger.debug(f"disk.folder.uploadfile (формат 2) вернул ошибку: {error} - {error_description}")
            except Exception as e2:
                logger.debug(f"Ошибка при загрузке через формат 2: {e2}")
            
            return None
            
        except Exception as e:
            logger.debug(f"Ошибка при загрузке файла {filename} через disk.folder.uploadfile: {e}")
            return None
    
    def _upload_file_via_multipart(self, file_content: bytes, filename: str, folder_id: str) -> Optional[int]:
        """
        Загрузка файла через multipart/form-data (альтернативный метод)
        """
        try:
            import base64
            
            file_base64 = base64.b64encode(file_content).decode('utf-8')
            
            url = f"{self.base_url}/disk.folder.uploadfile"
            
            # Пробуем разные форматы multipart
            # Формат 1: стандартный multipart
            files = {
                'file': (filename, file_content, 'application/octet-stream')
            }
            data = {
                'id': folder_id,
                'data[NAME]': filename
            }
            
            response = requests.post(url, files=files, data=data)
            response.raise_for_status()
            result = response.json()
            
            if result.get("result"):
                file_data = result["result"]
                file_id = None
                if isinstance(file_data, dict):
                    file_id = file_data.get("ID") or file_data.get("id")
                elif isinstance(file_data, (int, str)):
                    file_id = file_data
                
                if file_id:
                    logger.info(f"✅ Файл {filename} успешно загружен через multipart/form-data (ID: {file_id})")
                    return int(file_id)
            
            # Формат 2: с fileContent в base64
            data2 = {
                'id': folder_id,
                'data[NAME]': filename,
                'fileContent': file_base64
            }
            
            response2 = requests.post(url, data=data2)
            response2.raise_for_status()
            result2 = response2.json()
            
            if result2.get("result"):
                file_data = result2["result"]
                file_id = None
                if isinstance(file_data, dict):
                    file_id = file_data.get("ID") or file_data.get("id")
                elif isinstance(file_data, (int, str)):
                    file_id = file_data
                
                if file_id:
                    logger.info(f"✅ Файл {filename} успешно загружен через multipart с base64 (ID: {file_id})")
                    return int(file_id)
            
            return None
            
        except Exception as e:
            logger.debug(f"Ошибка при загрузке файла {filename} через multipart: {e}")
            return None
    
    def _get_shared_files_folder_id(self) -> Optional[str]:
        """
        Получение реального ID папки shared_files через API
        """
        try:
            # Пробуем получить список папок диска
            result = self._make_request("disk.folder.getchildren", {
                "id": "0"  # Корневая папка
            })
            
            if result.get("result"):
                folders = result["result"]
                if isinstance(folders, list):
                    for folder in folders:
                        if isinstance(folder, dict):
                            name = folder.get("NAME", "")
                            if name == "Общие файлы" or name == "shared_files" or folder.get("ID") == "shared_files":
                                folder_id = folder.get("ID")
                                logger.debug(f"Найден ID папки shared_files: {folder_id}")
                                return folder_id
                elif isinstance(folders, dict):
                    # Если результат - одна папка
                    if folders.get("NAME") == "Общие файлы" or folders.get("ID") == "shared_files":
                        return folders.get("ID")
            
            return None
        except Exception as e:
            logger.debug(f"Ошибка при получении ID папки shared_files: {e}")
            return None
    
    def _upload_file_alternative(self, file_content: bytes, filename: str, folder_id: str) -> Optional[int]:
        """
        Старый альтернативный способ загрузки файла (fallback)
        """
        try:
            import base64
            
            file_base64 = base64.b64encode(file_content).decode('utf-8')
            
            # Старый формат - пробуем как есть
            upload_data = {
                "id": folder_id,
                "data": {
                    "NAME": filename
                },
                "fileContent": file_base64
            }
            
            result = self._make_request("disk.folder.uploadfile", upload_data)
            
            if result.get("result"):
                file_data = result["result"]
                file_id = None
                if isinstance(file_data, dict):
                    file_id = file_data.get("ID") or file_data.get("id")
                elif isinstance(file_data, (int, str)):
                    file_id = file_data
                
                if file_id:
                    logger.info(f"✅ Файл {filename} успешно загружен через старый метод (ID: {file_id})")
                    return int(file_id)
            
            logger.warning(f"⚠️ Все методы загрузки файла {filename} не сработали")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка при альтернативной загрузке файла {filename}: {e}", exc_info=True)
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
            # В Bitrix24 API метод user.get может не возвращать пользовательские поля по умолчанию
            # Пробуем сначала без SELECT - это должно вернуть все поля включая пользовательские
            result = self._make_request("user.get", {"ID": user_id})
            if result.get("result"):
                user_data = result["result"][0] if isinstance(result["result"], list) else result["result"]
                
                # Проверяем, есть ли пользовательское поле в результате
                if self.telegram_field_name not in user_data:
                    # Если поля нет, пробуем явно запросить его через SELECT
                    logger.debug(f"Поле {self.telegram_field_name} не найдено в результате, пробуем явный запрос через SELECT")
                    try:
                        result_with_select = self._make_request("user.get", {
                            "ID": user_id,
                            "SELECT": [self.telegram_field_name]
                        })
                        if result_with_select.get("result"):
                            user_data_select = result_with_select["result"][0] if isinstance(result_with_select["result"], list) else result_with_select["result"]
                            # Объединяем данные
                            user_data.update(user_data_select)
                    except Exception as select_error:
                        logger.debug(f"Ошибка при запросе с SELECT: {select_error}")
                
                return user_data
        except Exception as e:
            logger.debug(f"Ошибка при получении пользователя {user_id}: {e}")
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
            # Используем фильтр для активных пользователей, если нужно
            # Запрашиваем все необходимые поля: ID, NAME, LAST_NAME, EMAIL, LOGIN и пользовательское поле с Telegram ID
            params = {
                "SELECT": ["ID", "NAME", "LAST_NAME", "EMAIL", "LOGIN", self.telegram_field_name]  # Запрашиваем все необходимые поля
            }
            if active_only:
                params["FILTER"] = {"ACTIVE": "Y"}
            
            # Получаем всех пользователей одним запросом
            # Битрикс24 обычно возвращает всех пользователей сразу
            result = self._make_request("user.get", params)
            users = result.get("result", [])
            
            # Фильтруем валидных пользователей
            valid_users = []
            if isinstance(users, list):
                for user in users:
                    # Пропускаем пустые списки и невалидные элементы
                    if isinstance(user, dict) and user.get("ID"):
                        # Проверяем, что это не пустой словарь
                        if user:
                            valid_users.append(user)
                    elif isinstance(user, list):
                        # Пропускаем пустые списки
                        logger.debug(f"Пропущен пустой список в результате user.get")
                        continue
                    else:
                        logger.debug(f"Пропущен невалидный элемент пользователя: {type(user)}, значение: {user}")
                        continue
                
                # Логируем всех пользователей с их ID и именами
                logger.info("=" * 80)
                logger.info("📋 СПИСОК ВСЕХ ПОЛЬЗОВАТЕЛЕЙ BITRIX24:")
                logger.info("=" * 80)
                for user in valid_users:
                    user_id = user.get("ID", "N/A")
                    name = user.get("NAME", "").strip()
                    last_name = user.get("LAST_NAME", "").strip()
                    full_name = f"{name} {last_name}".strip()
                    email = user.get("EMAIL", "").strip()
                    login = user.get("LOGIN", "").strip()
                    telegram_id = user.get(self.telegram_field_name, "").strip()
                    
                    # Формируем строку для логирования
                    log_line = f"ID: {user_id}"
                    if full_name:
                        log_line += f" | Имя: {full_name}"
                    if login:
                        log_line += f" | Login: {login}"
                    if telegram_id:
                        log_line += f" | Telegram ID: {telegram_id}"
                    
                    logger.info(log_line)
                logger.info("=" * 80)
                logger.info(f"Всего пользователей: {len(valid_users)}")
                logger.info("=" * 80)
                
                return valid_users
            
            # Если результат - словарь с одним пользователем, оборачиваем в список
            if isinstance(users, dict) and users.get("ID"):
                # Логируем одного пользователя
                user = users
                user_id = user.get("ID", "N/A")
                name = user.get("NAME", "").strip()
                last_name = user.get("LAST_NAME", "").strip()
                full_name = f"{name} {last_name}".strip()
                email = user.get("EMAIL", "").strip()
                login = user.get("LOGIN", "").strip()
                telegram_id = user.get(self.telegram_field_name, "").strip()
                
                log_line = f"ID: {user_id}"
                if full_name:
                    log_line += f" | Имя: {full_name}"
                if login:
                    log_line += f" | Login: {login}"
                if telegram_id:
                    log_line += f" | Telegram ID: {telegram_id}"
                
                logger.info("=" * 80)
                logger.info("📋 ПОЛЬЗОВАТЕЛЬ BITRIX24:")
                logger.info(log_line)
                logger.info("=" * 80)
                
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
                
                # Фильтруем валидных пользователей
                valid_users = []
                if isinstance(users, list):
                    for user in users:
                        # Пропускаем пустые списки и невалидные элементы
                        if isinstance(user, dict) and user.get("ID"):
                            if user:
                                valid_users.append(user)
                        elif isinstance(user, list):
                            logger.debug(f"Пропущен пустой список в результате user.get (fallback)")
                            continue
                        else:
                            logger.debug(f"Пропущен невалидный элемент пользователя (fallback): {type(user)}")
                            continue
                    
                    # Логируем всех пользователей с их ID и именами (fallback)
                    logger.info("=" * 80)
                    logger.info("📋 СПИСОК ВСЕХ ПОЛЬЗОВАТЕЛЕЙ BITRIX24 (fallback):")
                    logger.info("=" * 80)
                    for user in valid_users:
                        user_id = user.get("ID", "N/A")
                        name = user.get("NAME", "").strip()
                        last_name = user.get("LAST_NAME", "").strip()
                        full_name = f"{name} {last_name}".strip()
                        email = user.get("EMAIL", "").strip()
                        login = user.get("LOGIN", "").strip()
                        telegram_id = user.get(self.telegram_field_name, "").strip()
                        
                        log_line = f"ID: {user_id}"
                        if full_name:
                            log_line += f" | Имя: {full_name}"
                        if login:
                            log_line += f" | Login: {login}"
                        if telegram_id:
                            log_line += f" | Telegram ID: {telegram_id}"
                        
                        logger.info(log_line)
                    logger.info("=" * 80)
                    logger.info(f"Всего пользователей: {len(valid_users)}")
                    logger.info("=" * 80)
                    
                    return valid_users
                
                if isinstance(users, dict) and users.get("ID"):
                    # Логируем одного пользователя (fallback)
                    user = users
                    user_id = user.get("ID", "N/A")
                    name = user.get("NAME", "").strip()
                    last_name = user.get("LAST_NAME", "").strip()
                    full_name = f"{name} {last_name}".strip()
                    email = user.get("EMAIL", "").strip()
                    login = user.get("LOGIN", "").strip()
                    telegram_id = user.get(self.telegram_field_name, "").strip()
                    
                    log_line = f"ID: {user_id}"
                    if full_name:
                        log_line += f" | Имя: {full_name}"
                    if login:
                        log_line += f" | Login: {login}"
                    if telegram_id:
                        log_line += f" | Telegram ID: {telegram_id}"
                    
                    logger.info("=" * 80)
                    logger.info("📋 ПОЛЬЗОВАТЕЛЬ BITRIX24 (fallback):")
                    logger.info(log_line)
                    logger.info("=" * 80)
                    
                    return [users]
                
                return []
            except Exception as fallback_error:
                logger.error(f"Ошибка при fallback запросе пользователей: {fallback_error}")
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
    
    def update_user_telegram_id_via_standard_field(self, user_id: int, telegram_id: int) -> bool:
        """
        Альтернативный метод: Сохранение Telegram ID в стандартное поле пользователя
        
        Использует поле PERSONAL_NOTES или другое доступное стандартное поле
        для хранения Telegram ID.
        
        Args:
            user_id: ID пользователя в Bitrix24
            telegram_id: Telegram User ID
            
        Returns:
            True если обновление прошло успешно, False в случае ошибки
        """
        try:
            # Используем поле PERSONAL_NOTES для хранения Telegram ID
            # Формат: "TELEGRAM_ID:123456789" для возможности парсинга
            telegram_id_str = f"TELEGRAM_ID:{telegram_id}"
            
            # Пробуем обновить через user.update со стандартным полем
            update_data = {
                "ID": user_id,
                "fields": {
                    "PERSONAL_NOTES": telegram_id_str
                }
            }
            
            try:
                result = self._make_request("user.update", update_data)
                
                if result.get("error"):
                    logger.warning(f"Ошибка при обновлении через PERSONAL_NOTES: {result.get('error_description', '')}")
                    return False
                
                result_value = result.get("result")
                success = (
                    result_value is True or 
                    (isinstance(result_value, (int, str)) and str(result_value) == str(user_id)) or
                    (isinstance(result_value, bool) and result_value)
                )
                
                if success:
                    logger.info(f"✅ Telegram ID {telegram_id} сохранен в PERSONAL_NOTES для пользователя {user_id}")
                    return True
                else:
                    logger.warning(f"Обновление через PERSONAL_NOTES не подтверждено: {result_value}")
                    return False
                    
            except Exception as e:
                logger.error(f"Ошибка при обновлении через PERSONAL_NOTES: {e}", exc_info=True)
                return False
                
        except Exception as e:
            logger.error(f"Ошибка при сохранении Telegram ID через стандартное поле: {e}", exc_info=True)
            return False
    
    def update_user_telegram_id(self, user_id: int, telegram_id: int) -> bool:
        """
        Обновление Telegram ID пользователя в Bitrix24
        
        Пробует несколько методов сохранения в следующем порядке:
        1. Через пользовательское поле (user.update) - основной метод
        2. Через стандартное поле пользователя (PERSONAL_NOTES) - fallback
        
        Args:
            user_id: ID пользователя в Bitrix24
            telegram_id: Telegram User ID
            
        Returns:
            True если обновление прошло успешно хотя бы одним методом, False в случае ошибки
        """
        telegram_id_str = str(telegram_id)
        logger.info(f"📝 Попытка сохранить Telegram ID {telegram_id} для пользователя Bitrix24 {user_id}")
        
        # Метод 1: Попытка сохранить через пользовательское поле (основной метод)
        success_via_userfield = self._update_user_telegram_id_via_userfield(user_id, telegram_id)
        if success_via_userfield:
            logger.info(f"✅ Telegram ID успешно сохранен через пользовательское поле")
            return True
        
        logger.warning(f"⚠️ Сохранение через пользовательское поле не удалось, пробуем альтернативный метод...")
        
        # Метод 2: Попытка сохранить через стандартное поле (fallback)
        logger.info(f"Пробуем сохранить через стандартное поле PERSONAL_NOTES...")
        success_via_standard = self.update_user_telegram_id_via_standard_field(user_id, telegram_id)
        if success_via_standard:
            logger.info(f"✅ Telegram ID успешно сохранен через стандартное поле")
            return True
        
        logger.error(f"❌ Не удалось сохранить Telegram ID ни одним из методов")
        return False
    
    def _update_user_telegram_id_via_userfield(self, user_id: int, telegram_id: int) -> bool:
        """
        Внутренний метод: Попытка сохранить Telegram ID через пользовательское поле
        
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
            
            # В Bitrix24 API метод user.update может принимать данные в разных форматах
            # Пробуем несколько вариантов для максимальной совместимости
            
            telegram_id_str = str(telegram_id)
            logger.info(f"📝 Попытка сохранить Telegram ID {telegram_id} в поле '{self.telegram_field_name}' для пользователя Bitrix24 {user_id}")
            
            # Вариант 1: Формат с "id" (строчными) - как в официальном примере от Bitrix24
            # Формат: {"id": user_id, "fields": {"UF_TELEGRAM_ID": telegram_id}}
            # Некоторые версии Bitrix24 требуют именно "id" вместо "ID"
            # Источник: официальный пример от Bitrix24 для обновления профиля сотрудника
            update_data_v1 = {
                "id": user_id,
                "fields": {
                    self.telegram_field_name: telegram_id_str
                }
            }
            
            # Вариант 1a: Формат с "ID" (заглавными) - стандартный для Bitrix24 REST API
            # Согласно документации Bitrix24, для обновления пользовательских полей используется формат:
            # {"ID": user_id, "fields": {"FIELD_NAME": "value"}}
            update_data_v1a = {
                "ID": user_id,
                "fields": {
                    self.telegram_field_name: telegram_id_str
                }
            }
            
            logger.debug(f"Попытка 1: Формат с 'id' (строчными) - {update_data_v1}")
            try:
                result = self._make_request("user.update", update_data_v1)
                logger.debug(f"Ответ от Bitrix24 (попытка 1): {result}")
                
                # Проверяем успешность обновления
                # Bitrix24 API может возвращать:
                # - {"result": true} - успешно
                # - {"result": user_id} - успешно с ID пользователя
                # - {"error": "...", "error_description": "..."} - ошибка
                if result.get("error"):
                    success = False
                    error_msg = result.get("error", "")
                    error_desc = result.get("error_description", "")
                    error_code = result.get("error_code", "")
                    logger.warning(f"Ошибка при обновлении (попытка 1 с 'id'): {error_msg} - {error_desc}")
                    if error_code:
                        logger.warning(f"Код ошибки: {error_code}")
                else:
                    result_value = result.get("result")
                    success = (
                        result_value is True or 
                        (isinstance(result_value, (int, str)) and str(result_value) == str(user_id)) or
                        (isinstance(result_value, bool) and result_value)
                    )
                    if success:
                        logger.debug(f"Обновление успешно (попытка 1 с 'id'), result: {result_value}")
                    else:
                        logger.warning(f"Обновление не подтверждено (попытка 1 с 'id'), result: {result_value}, тип: {type(result_value)}")
            except Exception as req_error:
                logger.error(f"Исключение при запросе обновления (попытка 1 с 'id'): {req_error}", exc_info=True)
                success = False
                result = {"error": str(req_error)}
            
            # Если первый вариант не сработал, пробуем с "ID" (заглавными)
            if not success:
                logger.debug(f"Попытка 1a: Формат с 'ID' (заглавными) - {update_data_v1a}")
                try:
                    result = self._make_request("user.update", update_data_v1a)
                    logger.debug(f"Ответ от Bitrix24 (попытка 1a): {result}")
                    
                    if result.get("error"):
                        success = False
                        error_msg = result.get("error", "")
                        error_desc = result.get("error_description", "")
                        error_code = result.get("error_code", "")
                        logger.warning(f"Ошибка при обновлении (попытка 1a с 'ID'): {error_msg} - {error_desc}")
                        if error_code:
                            logger.warning(f"Код ошибки: {error_code}")
                    else:
                        result_value = result.get("result")
                        success = (
                            result_value is True or 
                            (isinstance(result_value, (int, str)) and str(result_value) == str(user_id)) or
                            (isinstance(result_value, bool) and result_value)
                        )
                        if success:
                            logger.debug(f"Обновление успешно (попытка 1a с 'ID'), result: {result_value}")
                        else:
                            logger.warning(f"Обновление не подтверждено (попытка 1a с 'ID'), result: {result_value}, тип: {type(result_value)}")
                except Exception as req_error:
                    logger.error(f"Исключение при запросе обновления (попытка 1a с 'ID'): {req_error}", exc_info=True)
                    success = False
                    result = {"error": str(req_error)}
            
            # Если первые варианты не сработали, пробуем альтернативные форматы
            if not success:
                error_msg = result.get("error", "")
                error_desc = result.get("error_description", "")
                logger.warning(f"Предыдущие варианты не сработали: {error_msg} - {error_desc}")
                logger.info(f"Пробуем альтернативные форматы...")
                
                # Вариант 2: Прямая передача полей с "id" (строчными)
                update_data_v2 = {
                    "id": user_id,
                    self.telegram_field_name: telegram_id_str
                }
                logger.debug(f"Попытка 2: Прямая передача полей с 'id' - {update_data_v2}")
                try:
                    result = self._make_request("user.update", update_data_v2)
                    logger.debug(f"Ответ от Bitrix24 (попытка 2): {result}")
                    if result.get("error"):
                        success = False
                        logger.warning(f"Ошибка при обновлении (попытка 2): {result.get('error')} - {result.get('error_description', '')}")
                    else:
                        result_value = result.get("result")
                        success = (
                            result_value is True or 
                            (isinstance(result_value, (int, str)) and str(result_value) == str(user_id)) or
                            (isinstance(result_value, bool) and result_value)
                        )
                except Exception as req_error:
                    logger.error(f"Исключение при запросе обновления (попытка 2): {req_error}", exc_info=True)
                    success = False
                    result = {"error": str(req_error)}
                
                # Если второй вариант не сработал, пробуем третий вариант - с "ID" (заглавными)
                if not success:
                    logger.warning(f"Попытка 2 не сработала, пробуем вариант 2a с 'ID'...")
                    update_data_v2a = {
                        "ID": user_id,
                        self.telegram_field_name: telegram_id_str
                    }
                    logger.debug(f"Попытка 2a: Прямая передача полей с 'ID' - {update_data_v2a}")
                    try:
                        result = self._make_request("user.update", update_data_v2a)
                        logger.debug(f"Ответ от Bitrix24 (попытка 2a): {result}")
                        if result.get("error"):
                            success = False
                            logger.warning(f"Ошибка при обновлении (попытка 2a): {result.get('error')} - {result.get('error_description', '')}")
                        else:
                            result_value = result.get("result")
                            success = (
                                result_value is True or 
                                (isinstance(result_value, (int, str)) and str(result_value) == str(user_id)) or
                                (isinstance(result_value, bool) and result_value)
                            )
                    except Exception as req_error:
                        logger.error(f"Исключение при запросе обновления (попытка 2a): {req_error}", exc_info=True)
                        success = False
                        result = {"error": str(req_error)}
                
                # Если и третий вариант не сработал, пробуем четвертый вариант - только поле
                if not success:
                    logger.warning(f"Предыдущие варианты не сработали, пробуем вариант 3...")
                    # Вариант 3: Только поле в корне запроса (некоторые версии Bitrix24 требуют такой формат)
                    update_data_v3 = {
                        self.telegram_field_name: telegram_id_str
                    }
                    logger.debug(f"Попытка 3: Только поле - {update_data_v3}")
                    try:
                        # Пробуем с "id" (строчными)
                        result = self._make_request("user.update", {"id": user_id, **update_data_v3})
                        logger.debug(f"Ответ от Bitrix24 (попытка 3 с 'id'): {result}")
                        if result.get("error"):
                            # Пробуем с "ID" (заглавными)
                            result = self._make_request("user.update", {"ID": user_id, **update_data_v3})
                            logger.debug(f"Ответ от Bitrix24 (попытка 3 с 'ID'): {result}")
                        
                        if result.get("error"):
                            success = False
                            logger.warning(f"Ошибка при обновлении (попытка 3): {result.get('error')} - {result.get('error_description', '')}")
                        else:
                            result_value = result.get("result")
                            success = (
                                result_value is True or 
                                (isinstance(result_value, (int, str)) and str(result_value) == str(user_id)) or
                                (isinstance(result_value, bool) and result_value)
                            )
                    except Exception as e:
                        logger.error(f"Исключение при третьей попытке: {e}", exc_info=True)
                        success = False
                        result = {"error": str(e)}
            
            if success:
                logger.info(f"✅ Telegram ID {telegram_id} успешно сохранен в поле '{self.telegram_field_name}' для пользователя Bitrix24 {user_id}")
                
                # Проверяем, что данные действительно сохранились
                # Делаем небольшую задержку перед проверкой (Bitrix24 может обрабатывать обновление асинхронно)
                import time
                time.sleep(1)  # Увеличиваем задержку до 1 секунды
                
                # Проверяем сохранение несколько раз (на случай асинхронной обработки)
                # ВАЖНО: Явно запрашиваем поле через SELECT, так как пользовательские поля могут не возвращаться по умолчанию
                saved_telegram_id = None
                for attempt in range(3):
                    try:
                        # Способ 1: Явно запрашиваем поле через SELECT для надежности
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
                        
                        # Способ 2: Проверяем через поиск пользователя по значению поля (альтернативный способ)
                        # Это может работать даже если поле не возвращается в user.get
                        search_result = self._make_request("user.get", {
                            "FILTER": {
                                "ID": user_id,
                                self.telegram_field_name: telegram_id_str
                            },
                            "SELECT": [self.telegram_field_name]
                        })
                        if search_result.get("result"):
                            search_users = search_result["result"]
                            if isinstance(search_users, list) and len(search_users) > 0:
                                found_user = search_users[0]
                                if found_user.get("ID") == str(user_id) or found_user.get("ID") == user_id:
                                    saved_telegram_id = found_user.get(self.telegram_field_name) or telegram_id_str
                                    logger.info(f"✅ Подтверждено через поиск (попытка {attempt + 1}): Пользователь найден по Telegram ID {telegram_id_str}")
                                    break
                    except Exception as check_error:
                        logger.debug(f"Ошибка при проверке сохранения (попытка {attempt + 1}): {check_error}")
                    
                    if attempt < 2:
                        time.sleep(0.5)  # Ждем перед следующей попыткой
                
                if not saved_telegram_id:
                    # Если запрос был успешным, но поле не возвращается при проверке,
                    # это может быть нормальным поведением Bitrix24 - поле сохранено, но не всегда возвращается в API
                    logger.warning(f"⚠️ Telegram ID не найден в профиле пользователя {user_id} после сохранения при проверке через API.")
                    logger.warning(f"   Это может означать, что:")
                    logger.warning(f"   1. Поле '{self.telegram_field_name}' не возвращается в API (но может быть сохранено)")
                    logger.warning(f"   2. Bitrix24 обрабатывает обновление асинхронно (попробуйте проверить позже)")
                    logger.warning(f"   3. Поле сохранено, но API не возвращает его в ответах (известная особенность Bitrix24)")
                    logger.info(f"💡 Проверьте профиль пользователя в Bitrix24 вручную:")
                    logger.info(f"   Настройки → Пользователи → Откройте профиль пользователя {user_id}")
                    logger.info(f"   Поле '{self.telegram_field_name}' должно содержать значение {telegram_id}")
                    logger.info(f"💡 Если запрос обновления был успешным (result=true), поле должно быть сохранено,")
                    logger.info(f"   даже если оно не возвращается при проверке через API.")
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
        
        Ищет в следующем порядке:
        1. В пользовательском поле (UF_USR_TELEGRAM)
        2. В стандартном поле PERSONAL_NOTES (формат: "TELEGRAM_ID:123456789")
        
        Args:
            telegram_id: Telegram User ID
            
        Returns:
            Информация о пользователе или None
        """
        telegram_id_str = str(telegram_id)
        
        # Метод 1: Поиск через пользовательское поле
        try:
            result = self._make_request("user.get", {
                "FILTER": {
                    self.telegram_field_name: telegram_id_str
                },
                "SELECT": [self.telegram_field_name]
            })
            
            users = result.get("result", [])
            if users:
                if isinstance(users, list) and len(users) > 0:
                    logger.debug(f"Найден пользователь Bitrix24 по Telegram ID {telegram_id} через пользовательское поле: {users[0].get('ID')}")
                    return users[0]
                elif isinstance(users, dict):
                    logger.debug(f"Найден пользователь Bitrix24 по Telegram ID {telegram_id} через пользовательское поле: {users.get('ID')}")
                    return users
        except Exception as e:
            logger.debug(f"Ошибка при поиске через пользовательское поле: {e}")
        
        # Метод 2: Поиск через стандартное поле PERSONAL_NOTES
        try:
            # Ищем в формате "TELEGRAM_ID:123456789"
            search_pattern = f"TELEGRAM_ID:{telegram_id_str}"
            
            # Получаем всех пользователей и ищем в PERSONAL_NOTES
            # Примечание: Bitrix24 может не поддерживать поиск по PERSONAL_NOTES через FILTER,
            # поэтому получаем всех пользователей и фильтруем локально
            result_all = self._make_request("user.get", {
                "SELECT": ["ID", "PERSONAL_NOTES"]
            })
            
            users_all = result_all.get("result", [])
            if isinstance(users_all, list):
                for user in users_all:
                    personal_notes = user.get("PERSONAL_NOTES", "")
                    if personal_notes and search_pattern in personal_notes:
                        # Найден пользователь, получаем полную информацию
                        user_id = user.get("ID")
                        if user_id:
                            full_user_info = self.get_user_by_id(int(user_id))
                            if full_user_info:
                                logger.debug(f"Найден пользователь Bitrix24 по Telegram ID {telegram_id} через PERSONAL_NOTES: {user_id}")
                                return full_user_info
        except Exception as e:
            logger.debug(f"Ошибка при поиске через PERSONAL_NOTES: {e}")
        
        return None
    
    def get_user_telegram_id(self, user_id: int) -> Optional[int]:
        """
        Получение Telegram ID пользователя Bitrix24
        
        Ищет в следующем порядке:
        1. В пользовательском поле (UF_USR_TELEGRAM)
        2. В стандартном поле PERSONAL_NOTES (формат: "TELEGRAM_ID:123456789")
        
        Args:
            user_id: ID пользователя в Bitrix24
            
        Returns:
            Telegram ID или None
        """
        try:
            # Метод 1: Пробуем получить из пользовательского поля
            user_info = self.get_user_by_id(user_id)
            if user_info:
                telegram_id_value = user_info.get(self.telegram_field_name)
                if telegram_id_value:
                    try:
                        telegram_id = int(telegram_id_value) if telegram_id_value else None
                        logger.debug(f"Получен Telegram ID {telegram_id} из пользовательского поля для пользователя Bitrix24 {user_id}")
                        return telegram_id
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Не удалось преобразовать Telegram ID в число: {telegram_id_value}, ошибка: {e}")
                
                # Метод 2: Пробуем получить из стандартного поля PERSONAL_NOTES
                personal_notes = user_info.get("PERSONAL_NOTES", "")
                if personal_notes:
                    # Ищем формат "TELEGRAM_ID:123456789"
                    import re
                    match = re.search(r'TELEGRAM_ID:(\d+)', personal_notes)
                    if match:
                        try:
                            telegram_id = int(match.group(1))
                            logger.debug(f"Получен Telegram ID {telegram_id} из PERSONAL_NOTES для пользователя Bitrix24 {user_id}")
                            return telegram_id
                        except (ValueError, TypeError) as e:
                            logger.debug(f"Не удалось преобразовать Telegram ID из PERSONAL_NOTES: {e}")
                
                logger.debug(f"Telegram ID не найден ни в пользовательском поле, ни в PERSONAL_NOTES для пользователя {user_id}")
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
