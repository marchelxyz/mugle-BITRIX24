"""
Модуль для работы с API Битрикс24
"""
import requests
import os
import logging
from datetime import datetime, timedelta
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
    
    def _make_request(self, method: str, params: Dict = None, use_get: bool = False, files: Dict = None, use_form_data: bool = False) -> Dict:
        """
        Выполнение запроса к API Битрикс24
        
        Args:
            method: Метод API (например, tasks.task.add)
            params: Параметры запроса
            use_get: Если True, использует GET запрос вместо POST
            files: Словарь файлов для multipart/form-data запроса
            use_form_data: Если True, использует form-data вместо JSON (для методов disk.folder.uploadfile)
            
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
        elif use_form_data:
            # Для POST запросов с form-data (например, disk.folder.uploadfile)
            response = requests.post(url, data=params)
        else:
            # Для POST запросов параметры передаются в JSON body
            response = requests.post(url, json=params)
        
        # Улучшенная обработка ошибок для диагностики
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            # Логируем детали ошибки перед повторным выбросом
            try:
                error_json = response.json()
                error_code = error_json.get("error", "")
                error_description = error_json.get("error_description", "")
                logger.error(f"HTTP ошибка {response.status_code} для метода {method}: {error_code} - {error_description}")
                logger.debug(f"Полный ответ: {error_json}")
            except:
                logger.error(f"HTTP ошибка {response.status_code} для метода {method}: {response.text[:500]}")
            raise
        
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
        
        # Сначала создаем задачу без файлов
        result = self._make_request("tasks.task.add", task_data)
        
        # Если задача создана успешно и есть файлы для прикрепления
        if result.get("result") and result["result"].get("task"):
            task_id = result["result"]["task"]["id"]
            
            # Если файлы переданы напрямую, загружаем их на диск
            if files and not file_ids:
                logger.info(f"Загрузка {len(files)} файлов на диск для прикрепления к задаче {task_id}")
                file_ids = []
                for filename, file_content in files:
                    file_id = self.upload_file(file_content, filename)
                    if file_id:
                        file_ids.append(file_id)
                        logger.info(f"✅ Файл {filename} загружен на диск (ID: {file_id})")
                    else:
                        logger.warning(f"⚠️ Не удалось загрузить файл {filename} на диск")
            
            # Прикрепляем файлы к задаче через tasks.task.update
            if file_ids:
                logger.info(f"Прикрепление {len(file_ids)} файлов к задаче {task_id} через tasks.task.update")
                attach_result = self._attach_files_to_task(task_id, file_ids)
                if attach_result:
                    logger.info(f"✅ Файлы успешно прикреплены к задаче {task_id}")
                else:
                    logger.warning(f"⚠️ Не удалось прикрепить файлы к задаче {task_id}, но задача создана")
        
        return result
    
    def _attach_files_to_task(self, task_id: int, file_ids: List[int]) -> bool:
        """
        Прикрепление файлов к существующей задаче через tasks.task.update
        
        Args:
            task_id: ID задачи
            file_ids: Список ID файлов, загруженных на диск
            
        Returns:
            True если файлы успешно прикреплены, False в противном случае
        """
        if not file_ids:
            logger.warning("Список ID файлов пуст, нечего прикреплять")
            return False
        
        try:
            # Пробуем разные форматы для прикрепления файлов
            # Формат 1: UF_TASK_WEBDAV_FILES (стандартный формат Bitrix24)
            # В Bitrix24 файлы прикрепляются через пользовательское поле UF_TASK_WEBDAV_FILES
            # Это поле содержит массив ID файлов из диска
            update_data = {
                "taskId": task_id,
                "fields": {
                    "UF_TASK_WEBDAV_FILES": file_ids
                }
            }
            
            logger.info(f"Попытка прикрепления {len(file_ids)} файлов к задаче {task_id} через UF_TASK_WEBDAV_FILES")
            logger.debug(f"Данные запроса: {update_data}")
            result = self._make_request("tasks.task.update", update_data)
            
            if result.get("result"):
                logger.info(f"✅ Файлы успешно прикреплены к задаче {task_id} через UF_TASK_WEBDAV_FILES")
                return True
            
            # Если первый формат не сработал, логируем ошибку и пробуем альтернативные форматы
            error = result.get("error", "")
            error_description = result.get("error_description", "")
            if error:
                logger.warning(f"Формат UF_TASK_WEBDAV_FILES не сработал: {error} - {error_description}")
            
            # Формат 2: Пробуем через disk.file.attach для каждого файла отдельно
            # Это более надежный способ прикрепления файлов к задачам
            logger.info(f"Попытка прикрепления файлов к задаче {task_id} через disk.file.attach")
            attached_count = 0
            for file_id in file_ids:
                try:
                    attach_data = {
                        "id": file_id,
                        "entityType": "tasks",
                        "entityId": task_id
                    }
                    logger.debug(f"Прикрепление файла {file_id} к задаче {task_id}")
                    attach_result = self._make_request("disk.file.attach", attach_data)
                    
                    if attach_result.get("result"):
                        attached_count += 1
                        logger.info(f"✅ Файл {file_id} успешно прикреплен к задаче {task_id}")
                    else:
                        error = attach_result.get("error", "")
                        error_description = attach_result.get("error_description", "")
                        logger.warning(f"⚠️ Не удалось прикрепить файл {file_id}: {error} - {error_description}")
                except Exception as e:
                    logger.error(f"Ошибка при прикреплении файла {file_id}: {e}", exc_info=True)
            
            if attached_count > 0:
                logger.info(f"✅ Успешно прикреплено {attached_count} из {len(file_ids)} файлов к задаче {task_id}")
                return True
            else:
                logger.error(f"❌ Не удалось прикрепить ни один файл к задаче {task_id}")
                return False
            
        except Exception as e:
            logger.error(f"Ошибка при прикреплении файлов к задаче {task_id}: {e}", exc_info=True)
            return False
    
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
        
        # Если ничего не сработало, пробуем альтернативный метод как fallback
        logger.debug(f"Попытка 4: Использование альтернативного метода")
        result = self._upload_file_alternative(file_content, filename, folder_id)
        if result:
            logger.info(f"✅ Файл {filename} успешно загружен через альтернативный метод (ID: {result})")
            return result
        
        # Пробуем загрузить в корневую папку (ID = 0)
        logger.debug(f"Попытка 5: Загрузка в корневую папку (ID=0)")
        result = self._upload_file_via_disk_folder(file_content, filename, "0")
        if result:
            logger.info(f"✅ Файл {filename} успешно загружен в корневую папку (ID: {result})")
            return result
        
        # Пробуем через disk.file.uploadfile (альтернативный метод Bitrix24)
        logger.debug(f"Попытка 6: Загрузка через disk.file.uploadfile")
        result = self._upload_file_via_disk_file_uploadfile(file_content, filename, folder_id)
        if result:
            logger.info(f"✅ Файл {filename} успешно загружен через disk.file.uploadfile (ID: {result})")
            return result
        
        logger.error(f"❌ Не удалось загрузить файл {filename} ни одним из методов")
        logger.error(f"💡 Проверьте права вебхука на загрузку файлов (disk)")
        logger.error(f"💡 Проверьте, что папка '{folder_id}' существует и доступна")
        return None
    
    def _upload_file_via_disk_folder(self, file_content: bytes, filename: str, folder_id: str) -> Optional[int]:
        """
        Загрузка файла через disk.folder.uploadfile (правильный метод Bitrix24)
        """
        try:
            import base64
            
            file_base64 = base64.b64encode(file_content).decode('utf-8')
            file_size_mb = len(file_content) / (1024 * 1024)
            
            logger.debug(f"Попытка загрузки файла {filename} (размер: {file_size_mb:.2f} MB) в папку {folder_id}")
            logger.debug(f"Размер base64: {len(file_base64)} символов")
            
            # Пробуем разные форматы для disk.folder.uploadfile
            # Формат 1: с data[NAME] (стандартный формат Bitrix24)
            upload_data_v1 = {
                "id": folder_id,
                "data[NAME]": filename,
                "fileContent": file_base64
            }
            logger.debug(f"Формат 1: id={folder_id}, data[NAME]={filename}, fileContent length={len(file_base64)}")
            
            try:
                result = self._make_request("disk.folder.uploadfile", upload_data_v1, use_form_data=True)
                
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
                error_code = result.get("error_code", "")
                if error:
                    logger.warning(f"⚠️ disk.folder.uploadfile (формат 1) вернул ошибку: {error}")
                    if error_code:
                        logger.warning(f"   Код ошибки: {error_code}")
                    if error_description:
                        logger.warning(f"   Описание: {error_description}")
            except requests.exceptions.HTTPError as http_err:
                if http_err.response.status_code == 400:
                    try:
                        error_json = http_err.response.json()
                        error_code = error_json.get("error", "")
                        error_description = error_json.get("error_description", "")
                        logger.warning(f"⚠️ HTTP 400 при загрузке через формат 1: {error_code} - {error_description}")
                        logger.info(f"💡 Проверьте:")
                        logger.info(f"   - Существует ли папка с ID '{folder_id}'")
                        logger.info(f"   - Права вебхука на disk.folder.uploadfile")
                        logger.info(f"   - Размер файла не превышает лимиты Bitrix24")
                    except:
                        logger.warning(f"⚠️ HTTP 400 при загрузке через формат 1: {http_err}")
                else:
                    logger.debug(f"Ошибка HTTP {http_err.response.status_code} при загрузке через формат 1: {http_err}")
            except Exception as e1:
                logger.debug(f"Ошибка при загрузке через формат 1: {e1}")
            
            # Формат 2: пробуем получить реальный ID папки и использовать его
            # Иногда "shared_files" - это строка, а нужен числовой ID
            try:
                # Пробуем получить информацию о папке
                folder_info = self._make_request("disk.folder.get", {"id": folder_id})
                if folder_info.get("result"):
                    real_folder_id = folder_info["result"].get("ID") or folder_info["result"].get("id") or folder_id
                    logger.debug(f"Получен реальный ID папки: {real_folder_id}")
                else:
                    real_folder_id = folder_id
            except:
                real_folder_id = folder_id
            
            upload_data_v2 = {
                "id": real_folder_id,
                "data[NAME]": filename,
                "fileContent": file_base64
            }
            
            try:
                result = self._make_request("disk.folder.uploadfile", upload_data_v2, use_form_data=True)
                
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
                            folder_id = folder.get("ID")
                            # Ищем папку "Общие файлы" или "shared_files"
                            if (name == "Общие файлы" or name == "shared_files" or 
                                folder_id == "shared_files" or 
                                "общие" in name.lower() or "shared" in name.lower()):
                                logger.debug(f"Найден ID папки shared_files: {folder_id} (имя: {name})")
                                return folder_id
                elif isinstance(folders, dict):
                    # Если результат - одна папка
                    name = folders.get("NAME", "")
                    folder_id = folders.get("ID")
                    if (name == "Общие файлы" or name == "shared_files" or 
                        folder_id == "shared_files" or
                        "общие" in name.lower() or "shared" in name.lower()):
                        logger.debug(f"Найден ID папки shared_files: {folder_id} (имя: {name})")
                        return folder_id
            
            logger.debug("Папка shared_files не найдена в списке папок диска")
            return None
        except Exception as e:
            logger.debug(f"Ошибка при получении ID папки shared_files: {e}")
            return None
    
    def _upload_file_alternative(self, file_content: bytes, filename: str, folder_id: str) -> Optional[int]:
        """
        Альтернативный способ загрузки файла через disk.folder.uploadfile с правильным форматом
        """
        try:
            import base64
            
            file_base64 = base64.b64encode(file_content).decode('utf-8')
            
            # Используем плоский формат data[NAME] вместо вложенного объекта
            # Это правильный формат для Bitrix24 API
            upload_data = {
                "id": folder_id,
                "data[NAME]": filename,
                "fileContent": file_base64
            }
            
            try:
                result = self._make_request("disk.folder.uploadfile", upload_data, use_form_data=True)
                
                if result.get("result"):
                    file_data = result["result"]
                    file_id = None
                    if isinstance(file_data, dict):
                        file_id = file_data.get("ID") or file_data.get("id")
                    elif isinstance(file_data, (int, str)):
                        file_id = file_data
                    
                    if file_id:
                        logger.info(f"✅ Файл {filename} успешно загружен через альтернативный метод (ID: {file_id})")
                        return int(file_id)
                
                # Логируем ошибку из ответа API
                error = result.get("error", "")
                error_description = result.get("error_description", "")
                if error:
                    logger.warning(f"⚠️ Альтернативный метод вернул ошибку: {error} - {error_description}")
                
            except requests.exceptions.HTTPError as http_err:
                # Обрабатываем HTTP ошибки отдельно для лучшей диагностики
                if http_err.response.status_code == 400:
                    try:
                        error_json = http_err.response.json()
                        error_code = error_json.get("error", "")
                        error_description = error_json.get("error_description", "")
                        logger.warning(f"⚠️ Ошибка 400 при загрузке файла {filename}: {error_code} - {error_description}")
                        logger.info(f"💡 Возможные причины:")
                        logger.info(f"   1. Неправильный формат данных (проверьте формат data[NAME])")
                        logger.info(f"   2. Папка с ID '{folder_id}' не существует или недоступна")
                        logger.info(f"   3. Вебхук не имеет прав на загрузку файлов (disk.folder.uploadfile)")
                        logger.info(f"   4. Файл слишком большой или имеет неподдерживаемый формат")
                    except:
                        logger.warning(f"⚠️ Ошибка 400 при загрузке файла {filename}: {http_err}")
                else:
                    raise
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка при альтернативной загрузке файла {filename}: {e}", exc_info=True)
            return None
    
    def _upload_file_via_disk_file_uploadfile(self, file_content: bytes, filename: str, folder_id: str) -> Optional[int]:
        """
        Альтернативный способ загрузки файла через disk.file.uploadfile
        Этот метод может работать, когда disk.folder.uploadfile не работает
        """
        try:
            import base64
            
            file_base64 = base64.b64encode(file_content).decode('utf-8')
            
            # Метод disk.file.uploadfile требует немного другой формат
            # Пробуем разные варианты
            upload_data_v1 = {
                "id": folder_id,
                "data[NAME]": filename,
                "fileContent": file_base64
            }
            
            try:
                result = self._make_request("disk.file.uploadfile", upload_data_v1, use_form_data=True)
                
                if result.get("result"):
                    file_data = result["result"]
                    file_id = None
                    if isinstance(file_data, dict):
                        file_id = file_data.get("ID") or file_data.get("id")
                    elif isinstance(file_data, (int, str)):
                        file_id = file_data
                    
                    if file_id:
                        logger.info(f"✅ Файл {filename} успешно загружен через disk.file.uploadfile (ID: {file_id})")
                        return int(file_id)
                
                error = result.get("error", "")
                error_description = result.get("error_description", "")
                if error:
                    logger.debug(f"disk.file.uploadfile вернул ошибку: {error} - {error_description}")
            except requests.exceptions.HTTPError as http_err:
                if http_err.response.status_code == 400:
                    logger.debug(f"disk.file.uploadfile вернул ошибку 400: {http_err}")
                else:
                    raise
            
            return None
            
        except Exception as e:
            logger.debug(f"Ошибка при загрузке файла {filename} через disk.file.uploadfile: {e}")
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
    
    def get_tasks(self, filter_params: Dict = None, select: List[str] = None) -> List[Dict]:
        """
        Получение списка задач из Bitrix24
        
        Args:
            filter_params: Параметры фильтрации (например, {"<DEADLINE": "2024-01-01", "!STATUS": "5"})
                          Поддерживаются операторы: <, >, <=, >=, ! (отрицание)
            select: Список полей для выборки (по умолчанию основные поля)
            
        Returns:
            Список задач
        """
        try:
            params = {}
            
            # Добавляем фильтры
            if filter_params:
                # Bitrix24 использует формат фильтров через FILTER
                # Операторы указываются в ключах: "<DEADLINE", ">=DEADLINE", "!STATUS"
                params["FILTER"] = filter_params
            
            # Добавляем поля для выборки
            if select:
                params["SELECT"] = select
            else:
                # По умолчанию выбираем основные поля
                params["SELECT"] = [
                    "ID", "TITLE", "DESCRIPTION", "DEADLINE", "STATUS",
                    "RESPONSIBLE_ID", "CREATED_BY", "CREATED_DATE", "CHANGED_DATE"
                ]
            
            # Добавляем параметры для получения всех задач (без пагинации)
            params["ORDER"] = {"DEADLINE": "ASC"}  # Сортируем по дедлайну
            
            result = self._make_request("tasks.task.list", params)
            
            if result.get("result"):
                tasks = result["result"].get("tasks", [])
                # Преобразуем формат ответа в более удобный
                formatted_tasks = []
                
                # Bitrix24 возвращает задачи в формате словаря {task_id: task_data}
                if isinstance(tasks, dict):
                    for task_id, task_data in tasks.items():
                        formatted_task = {
                            "id": task_id,
                            "title": self._get_task_field(task_data, ['title', 'TITLE', 'Title'], ""),
                            "description": self._get_task_field(task_data, ['description', 'DESCRIPTION', 'Description'], ""),
                            "deadline": self._get_task_field(task_data, ['deadline', 'DEADLINE', 'Deadline']),
                            "status": self._get_task_field(task_data, ['status', 'STATUS', 'Status']),
                            "responsibleId": self._get_task_field(task_data, ['responsibleId', 'RESPONSIBLE_ID', 'responsible_id', 'RESPONSIBLEID']),
                            "createdBy": self._get_task_field(task_data, ['createdBy', 'CREATED_BY', 'created_by', 'CREATEDBY']),
                            "createdDate": self._get_task_field(task_data, ['createdDate', 'CREATED_DATE', 'created_date', 'CREATEDDATE']),
                            "changedDate": self._get_task_field(task_data, ['changedDate', 'CHANGED_DATE', 'changed_date', 'CHANGEDDATE'])
                        }
                        formatted_tasks.append(formatted_task)
                elif isinstance(tasks, list):
                    # Если задачи возвращаются как список
                    for task_data in tasks:
                        task_id = task_data.get("ID") or task_data.get("id")
                        if task_id:
                            formatted_task = {
                                "id": task_id,
                                "title": self._get_task_field(task_data, ['title', 'TITLE', 'Title'], ""),
                                "description": self._get_task_field(task_data, ['description', 'DESCRIPTION', 'Description'], ""),
                                "deadline": self._get_task_field(task_data, ['deadline', 'DEADLINE', 'Deadline']),
                                "status": self._get_task_field(task_data, ['status', 'STATUS', 'Status']),
                                "responsibleId": self._get_task_field(task_data, ['responsibleId', 'RESPONSIBLE_ID', 'responsible_id', 'RESPONSIBLEID']),
                                "createdBy": self._get_task_field(task_data, ['createdBy', 'CREATED_BY', 'created_by', 'CREATEDBY']),
                                "createdDate": self._get_task_field(task_data, ['createdDate', 'CREATED_DATE', 'created_date', 'CREATEDDATE']),
                                "changedDate": self._get_task_field(task_data, ['changedDate', 'CHANGED_DATE', 'changed_date', 'CHANGEDDATE'])
                            }
                            formatted_tasks.append(formatted_task)
                
                return formatted_tasks
            
            return []
        except Exception as e:
            logger.error(f"Ошибка при получении задач: {e}", exc_info=True)
            return []
    
    def get_overdue_tasks(self, exclude_status: List[int] = None) -> List[Dict]:
        """
        Получение просроченных задач из Bitrix24
        
        Использует несколько стратегий для надежного получения просроченных задач:
        1. Попытка фильтрации через API с оператором <DEADLINE
        2. Если не работает, получение всех незавершенных задач и фильтрация в коде
        
        Args:
            exclude_status: Список статусов для исключения (по умолчанию [5] - завершенные)
            
        Returns:
            Список просроченных задач с полями: id, title, deadline, status, responsibleId, createdBy
        """
        if exclude_status is None:
            exclude_status = [5]  # По умолчанию исключаем завершенные задачи
        
        now = datetime.now()
        overdue_tasks = []
        
        try:
            logger.info(f"🔍 Поиск просроченных задач (текущее время: {now})")
            
            # Стратегия 1: Попытка фильтрации через API
            # Пробуем разные форматы даты для совместимости
            deadline_formats = [
                now.strftime('%Y-%m-%d %H:%M:%S'),  # С временем
                now.strftime('%Y-%m-%d'),  # Только дата
                now.strftime('%Y-%m-%dT%H:%M:%S'),  # ISO формат
            ]
            
            for deadline_format in deadline_formats:
                try:
                    filter_params = {
                        "<DEADLINE": deadline_format
                    }
                    
                    # Добавляем фильтры по статусу
                    if len(exclude_status) == 1:
                        filter_params["!STATUS"] = str(exclude_status[0])
                    elif len(exclude_status) > 1:
                        # Для нескольких статусов используем фильтр через OR
                        filter_params["!STATUS"] = exclude_status
                    
                    logger.debug(f"   Попытка фильтрации с форматом даты: {deadline_format}")
                    tasks = self.get_tasks(filter_params=filter_params)
                    
                    if tasks:
                        logger.info(f"✅ Найдено {len(tasks)} просроченных задач через API фильтр (формат: {deadline_format})")
                        # Дополнительно проверяем в коде, так как API может вернуть лишние задачи
                        for task in tasks:
                            if self._is_task_overdue(task, now):
                                overdue_tasks.append(task)
                        
                        if overdue_tasks:
                            logger.info(f"✅ После проверки в коде: {len(overdue_tasks)} просроченных задач")
                            return overdue_tasks
                except Exception as e:
                    logger.debug(f"   Фильтр с форматом {deadline_format} не сработал: {e}")
                    continue
            
            # Стратегия 2: Получение всех незавершенных задач и фильтрация в коде
            logger.info("   Использование стратегии 2: получение всех задач и фильтрация в коде")
            
            # Получаем все задачи с нужными статусами
            filter_params = {}
            if len(exclude_status) == 1:
                filter_params["!STATUS"] = str(exclude_status[0])
            elif len(exclude_status) > 1:
                filter_params["!STATUS"] = exclude_status
            
            all_tasks = self.get_tasks(filter_params=filter_params)
            logger.info(f"   Получено {len(all_tasks)} незавершенных задач")
            
            # Фильтруем просроченные задачи
            for task in all_tasks:
                if self._is_task_overdue(task, now):
                    overdue_tasks.append(task)
            
            logger.info(f"✅ Найдено {len(overdue_tasks)} просроченных задач")
            return overdue_tasks
            
        except Exception as e:
            logger.error(f"❌ Ошибка при получении просроченных задач: {e}", exc_info=True)
            return []
    
    def _is_task_overdue(self, task: Dict, current_time: datetime = None) -> bool:
        """
        Проверка, просрочена ли задача
        
        Args:
            task: Данные задачи
            current_time: Текущее время для сравнения (по умолчанию datetime.now())
            
        Returns:
            True если задача просрочена, False иначе
        """
        from datetime import timezone
        
        if current_time is None:
            current_time = datetime.now()
        
        deadline_str = self._get_task_field(task, ['deadline', 'DEADLINE', 'Deadline'])
        
        if not deadline_str:
            return False  # Если нет дедлайна, задача не может быть просрочена
        
        try:
            # Парсим дату дедлайна в разных форматах
            deadline_dt = None
            
            # ISO формат с временной зоной (2024-01-15T18:00:00+03:00 или 2024-01-15T18:00:00Z)
            if 'T' in deadline_str or 'Z' in deadline_str:
                deadline_dt = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                if deadline_dt.tzinfo:
                    # ВАЖНО: Конвертируем в UTC перед удалением временной зоны
                    # Это гарантирует правильное сравнение с datetime.now()
                    deadline_dt = deadline_dt.astimezone(timezone.utc).replace(tzinfo=None)
            # Формат YYYY-MM-DD HH:MI:SS
            elif ' ' in deadline_str:
                deadline_dt = datetime.strptime(deadline_str, '%Y-%m-%d %H:%M:%S')
            # Формат YYYY-MM-DD
            elif len(deadline_str) == 10:
                deadline_dt = datetime.strptime(deadline_str, '%Y-%m-%d')
                # Если указана только дата, считаем дедлайн на конец дня
                deadline_dt = deadline_dt.replace(hour=23, minute=59, second=59)
            
            if deadline_dt:
                is_overdue = deadline_dt < current_time
                logger.debug(f"🔍 Проверка просроченности задачи {task.get('id')}: deadline={deadline_dt}, current={current_time}, overdue={is_overdue}")
                return is_overdue
            
        except Exception as e:
            logger.debug(f"Ошибка при парсинге дедлайна '{deadline_str}' для задачи {task.get('id')}: {e}")
        
        return False
    
    def _get_task_field(self, task_data: Dict, field_variants: List[str], default=None):
        """
        Безопасное извлечение поля задачи с поддержкой разных форматов (camelCase, UPPERCASE, snake_case)
        
        Args:
            task_data: Словарь с данными задачи
            field_variants: Список вариантов названий поля (например, ['title', 'TITLE', 'Title'])
            default: Значение по умолчанию, если поле не найдено
            
        Returns:
            Значение поля или default
        """
        for variant in field_variants:
            value = task_data.get(variant)
            if value is not None and value != "":
                return value
        return default
    
    def get_task_by_id(self, task_id: int) -> Optional[Dict]:
        """
        Получение задачи по ID
        
        Args:
            task_id: ID задачи
            
        Returns:
            Информация о задаче или None
        """
        try:
            # Пробуем разные варианты параметров для совместимости с разными версиями Bitrix24
            result = None
            
            # Вариант 1: с параметром "id"
            try:
                logger.debug(f"🔍 Попытка 1: tasks.task.get с параметром 'id' для задачи {task_id}")
                result = self._make_request("tasks.task.get", {"id": task_id})
                logger.info(f"✅ Успешно получен ответ через вариант 1 (id={task_id})")
            except Exception as e1:
                logger.debug(f"⚠️ Вариант 1 не сработал: {e1}")
                # Вариант 2: с параметром "taskId"
                try:
                    logger.debug(f"🔍 Попытка 2: tasks.task.get с параметром 'taskId' для задачи {task_id}")
                    result = self._make_request("tasks.task.get", {"taskId": task_id})
                    logger.info(f"✅ Успешно получен ответ через вариант 2 (taskId={task_id})")
                except Exception as e2:
                    logger.debug(f"⚠️ Вариант 2 не сработал: {e2}")
                    # Вариант 3: с параметром "TASKID"
                    try:
                        logger.debug(f"🔍 Попытка 3: tasks.task.get с параметром 'TASKID' для задачи {task_id}")
                        result = self._make_request("tasks.task.get", {"TASKID": task_id})
                        logger.info(f"✅ Успешно получен ответ через вариант 3 (TASKID={task_id})")
                    except Exception as e3:
                        logger.debug(f"⚠️ Вариант 3 не сработал: {e3}")
                        # Fallback: используем tasks.task.list с фильтром по ID
                        logger.warning(f"⚠️ Метод tasks.task.get недоступен, используем tasks.task.list для задачи {task_id}")
                        try:
                            list_result = self.get_tasks(filter_params={"ID": task_id})
                            if list_result:
                                logger.info(f"✅ Получена задача через tasks.task.list (fallback)")
                                # Возвращаем первую найденную задачу
                                return list_result[0] if isinstance(list_result, list) and len(list_result) > 0 else None
                        except Exception as e4:
                            logger.warning(f"❌ Все варианты получения задачи {task_id} не сработали: {e4}")
                            result = None
            
            if result:
                # Логируем полный ответ от API
                import json
                logger.info(f"🔍 ПОЛНЫЙ ОТВЕТ ОТ tasks.task.get ДЛЯ ЗАДАЧИ {task_id}:")
                logger.info(f"   Тип результата: {type(result)}")
                logger.info(f"   Ключи верхнего уровня: {list(result.keys()) if isinstance(result, dict) else 'N/A'}")
                logger.info(f"   Полный JSON ответа (первые 2000 символов): {json.dumps(result, ensure_ascii=False, indent=2)[:2000]}")
                
                if result.get("result"):
                    task_data = result["result"].get("task")
                    if task_data:
                        # Логируем структуру task_data
                        logger.info(f"📋 СТРУКТУРА task_data:")
                        logger.info(f"   Тип: {type(task_data)}")
                        if isinstance(task_data, dict):
                            logger.info(f"   Все доступные ключи: {list(task_data.keys())}")
                            logger.info(f"   Полный JSON task_data (первые 2000 символов): {json.dumps(task_data, ensure_ascii=False, indent=2)[:2000]}")
                            
                            # Извлекаем поля с поддержкой разных форматов (camelCase и UPPERCASE)
                            title = self._get_task_field(task_data, ['title', 'TITLE', 'Title'], "")
                            description = self._get_task_field(task_data, ['description', 'DESCRIPTION', 'Description'], "")
                            deadline = self._get_task_field(task_data, ['deadline', 'DEADLINE', 'Deadline'])
                            status = self._get_task_field(task_data, ['status', 'STATUS', 'Status'])
                            responsible_id = self._get_task_field(task_data, ['responsibleId', 'RESPONSIBLE_ID', 'responsible_id', 'RESPONSIBLEID'])
                            created_by = self._get_task_field(task_data, ['createdBy', 'CREATED_BY', 'created_by', 'CREATEDBY'])
                            created_date = self._get_task_field(task_data, ['createdDate', 'CREATED_DATE', 'created_date', 'CREATEDDATE'])
                            changed_date = self._get_task_field(task_data, ['changedDate', 'CHANGED_DATE', 'changed_date', 'CHANGEDDATE'])
                            
                            logger.info(f"🔍 ПОИСК ПОЛЕЙ ОТВЕТСТВЕННОГО И СОЗДАТЕЛЯ:")
                            logger.info(f"   RESPONSIBLE_ID (прямой): {task_data.get('RESPONSIBLE_ID')}")
                            logger.info(f"   responsibleId (camelCase): {task_data.get('responsibleId')}")
                            logger.info(f"   responsible_id (snake_case): {task_data.get('responsible_id')}")
                            logger.info(f"   Найденный responsible_id: {responsible_id}")
                            logger.info(f"   CREATED_BY (прямой): {task_data.get('CREATED_BY')}")
                            logger.info(f"   createdBy (camelCase): {task_data.get('createdBy')}")
                            logger.info(f"   created_by (snake_case): {task_data.get('created_by')}")
                            logger.info(f"   Найденный created_by: {created_by}")
                            logger.info(f"   Найденный title: {title}")
                            logger.info(f"   Найденный description: {description[:100] if description else 'None'}...")
                            
                            return {
                                "id": task_id,
                                "title": title,
                                "description": description,
                                "deadline": deadline,
                                "status": status,
                                "responsibleId": responsible_id,
                                "createdBy": created_by,
                                "createdDate": created_date,
                                "changedDate": changed_date
                            }
                        else:
                            logger.warning(f"⚠️ task_data не является словарем: {type(task_data)}, значение: {task_data}")
                    else:
                        logger.warning(f"⚠️ В result нет ключа 'task'. Доступные ключи: {list(result['result'].keys()) if isinstance(result.get('result'), dict) else 'N/A'}")
                        logger.warning(f"   Полный result['result']: {json.dumps(result.get('result'), ensure_ascii=False, indent=2)[:1000]}")
                else:
                    logger.warning(f"⚠️ В ответе нет ключа 'result'. Полный ответ: {json.dumps(result, ensure_ascii=False, indent=2)[:1000]}")
                    if result.get("error"):
                        logger.error(f"❌ Ошибка в ответе: {result.get('error')} - {result.get('error_description', '')}")
            else:
                logger.warning(f"⚠️ Результат запроса tasks.task.get для задачи {task_id} = None (все варианты запросов не вернули данных)")
            
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении задачи {task_id}: {e}", exc_info=True)
            return None
    
    def get_task_comment(self, task_id: int, comment_id: int) -> Optional[Dict]:
        """
        Получение комментария к задаче по ID
        
        Args:
            task_id: ID задачи
            comment_id: ID комментария
            
        Returns:
            Информация о комментарии или None
        """
        try:
            # Пробуем разные варианты параметров для совместимости с разными версиями Bitrix24
            result = None
            
            # Вариант 1: camelCase параметры
            try:
                result = self._make_request("tasks.task.comment.get", {
                    "taskId": task_id,
                    "commentId": comment_id
                })
            except Exception as e1:
                # Вариант 2: UPPERCASE параметры
                try:
                    result = self._make_request("tasks.task.comment.get", {
                        "TASKID": task_id,
                        "COMMENTID": comment_id
                    })
                except Exception as e2:
                    # Вариант 3: смешанный формат
                    try:
                        result = self._make_request("tasks.task.comment.get", {
                            "TASKID": task_id,
                            "commentId": comment_id
                        })
                    except Exception as e3:
                        logger.warning(f"Все варианты вызова tasks.task.comment.get не сработали для комментария {comment_id} к задаче {task_id}")
            
            if result and result.get("result"):
                comment_data = result["result"].get("comment")
                if comment_data:
                    return {
                        "id": comment_id,
                        "taskId": task_id,
                        "authorId": comment_data.get("AUTHOR_ID"),
                        "postMessage": comment_data.get("POST_MESSAGE"),
                        "createdDate": comment_data.get("CREATED_DATE"),
                        "updatedDate": comment_data.get("UPDATED_DATE"),
                        "files": comment_data.get("FILES", [])
                    }
            
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении комментария {comment_id} к задаче {task_id}: {e}", exc_info=True)
            return None
    
    def get_recent_task_comments(self, since: datetime = None) -> List[Dict]:
        """
        Получение недавних комментариев к задачам
        
        ВАЖНО: Метод tasks.task.commentitem.getlist не существует в Bitrix24 API.
        Этот метод всегда возвращает пустой список.
        
        Для отслеживания изменений задач (комментарии, статусы) необходимо использовать
        исходящий вебхук Bitrix24 (Outgoing Webhook).
        
        Настройка исходящего вебхука:
        1. Bitrix24 → Настройки → Разработчикам → Исходящий вебхук
        2. Создайте новый вебхук с событиями задач:
           - ONTASKADD - Создание задачи
           - ONTASKUPDATE - Обновление задачи
           - ONTASKDELETE - Удаление задачи
           - ONTASKCOMMENTADD - Добавление комментария к задаче
           - ONTASKCOMMENTUPDATE - Обновление комментария к задаче
           - ONTASKCOMMENTDELETE - Удаление комментария к задаче
        3. Укажите URL вашего сервера для приема событий: https://your-domain.com/api/bitrix/webhook
        
        Args:
            since: Дата начала периода (по умолчанию последний час)
            
        Returns:
            Пустой список (метод не поддерживается в Bitrix24 API)
        """
        logger.warning("⚠️ Метод get_recent_task_comments не поддерживается в Bitrix24 API")
        logger.info("💡 Метод tasks.task.commentitem.getlist не существует")
        logger.info("💡 Для отслеживания изменений задач используйте исходящий вебхук Bitrix24")
        logger.info("   Настройка: Bitrix24 → Настройки → Разработчикам → Исходящий вебхук")
        logger.info("   События задач: ONTASKADD, ONTASKUPDATE, ONTASKDELETE")
        logger.info("   События комментариев: ONTASKCOMMENTADD, ONTASKCOMMENTUPDATE, ONTASKCOMMENTDELETE")
        return []
    
    def get_task_chat_message(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """
        Получение сообщения из чата задачи
        
        ПРИМЕЧАНИЕ: После обновления Bitrix24 комментарии к задачам стали сообщениями в чатах.
        Используйте этот метод вместо get_task_comment для получения комментариев.
        
        Args:
            chat_id: ID чата задачи (из поля chatId задачи)
            message_id: ID сообщения (MESSAGE_ID из вебхука ONTASKCOMMENTADD)
            
        Returns:
            Информация о сообщении или None
        """
        try:
            # Пробуем разные варианты параметров
            result = None
            
            # Вариант 1: camelCase параметры
            try:
                logger.debug(f"🔍 Попытка 1: im.message.get с параметрами chatId={chat_id}, id={message_id}")
                result = self._make_request("im.message.get", {
                    "chatId": chat_id,
                    "id": message_id
                })
                logger.debug(f"✅ Вариант 1 успешен: получен результат")
            except Exception as e1:
                logger.debug(f"❌ Вариант 1 не сработал: {type(e1).__name__}: {e1}")
                # Вариант 2: UPPERCASE параметры
                try:
                    logger.debug(f"🔍 Попытка 2: im.message.get с параметрами CHAT_ID={chat_id}, ID={message_id}")
                    result = self._make_request("im.message.get", {
                        "CHAT_ID": chat_id,
                        "ID": message_id
                    })
                    logger.debug(f"✅ Вариант 2 успешен: получен результат")
                except Exception as e2:
                    logger.debug(f"❌ Вариант 2 не сработал: {type(e2).__name__}: {e2}")
                    # Вариант 3: смешанный формат
                    try:
                        logger.debug(f"🔍 Попытка 3: im.message.get с параметрами CHAT_ID={chat_id}, id={message_id}")
                        result = self._make_request("im.message.get", {
                            "CHAT_ID": chat_id,
                            "id": message_id
                        })
                        logger.debug(f"✅ Вариант 3 успешен: получен результат")
                    except Exception as e3:
                        logger.warning(f"⚠️ Все варианты вызова im.message.get не сработали для сообщения {message_id} в чате {chat_id}")
                        logger.warning(f"   Ошибка 1: {type(e1).__name__}: {e1}")
                        logger.warning(f"   Ошибка 2: {type(e2).__name__}: {e2}")
                        logger.warning(f"   Ошибка 3: {type(e3).__name__}: {e3}")
                        # Пробуем альтернативный метод - получить список сообщений и найти нужное
                        try:
                            logger.debug(f"🔍 Попытка альтернативного метода: im.message.list с CHAT_ID={chat_id}")
                            list_result = self._make_request("im.message.list", {
                                "CHAT_ID": chat_id,
                                "LIMIT": 100
                            })
                            if list_result and list_result.get("result"):
                                messages = list_result["result"] if isinstance(list_result["result"], list) else [list_result["result"]]
                                for msg in messages:
                                    msg_id = msg.get("id") or msg.get("ID")
                                    if msg_id and str(msg_id) == str(message_id):
                                        logger.info(f"✅ Найдено сообщение {message_id} через im.message.list")
                                        return {
                                            "id": msg_id,
                                            "chatId": chat_id,
                                            "authorId": msg.get("authorId") or msg.get("AUTHOR_ID"),
                                            "message": msg.get("message") or msg.get("MESSAGE"),
                                            "date": msg.get("date") or msg.get("DATE"),
                                            "files": msg.get("files") or msg.get("FILES", [])
                                        }
                        except Exception as e4:
                            logger.debug(f"❌ Альтернативный метод im.message.list тоже не сработал: {type(e4).__name__}: {e4}")
            
            if result and result.get("result"):
                message_data = result["result"]
                # Может быть словарь или список
                if isinstance(message_data, dict):
                    return {
                        "id": message_data.get("id") or message_data.get("ID"),
                        "chatId": message_data.get("chatId") or message_data.get("CHAT_ID") or chat_id,
                        "authorId": message_data.get("authorId") or message_data.get("AUTHOR_ID"),
                        "message": message_data.get("message") or message_data.get("MESSAGE"),
                        "date": message_data.get("date") or message_data.get("DATE"),
                        "files": message_data.get("files") or message_data.get("FILES", [])
                    }
                elif isinstance(message_data, list) and len(message_data) > 0:
                    # Если вернулся список, берем первое сообщение
                    msg = message_data[0]
                    return {
                        "id": msg.get("id") or msg.get("ID"),
                        "chatId": msg.get("chatId") or msg.get("CHAT_ID") or chat_id,
                        "authorId": msg.get("authorId") or msg.get("AUTHOR_ID"),
                        "message": msg.get("message") or msg.get("MESSAGE"),
                        "date": msg.get("date") or msg.get("DATE"),
                        "files": msg.get("files") or msg.get("FILES", [])
                    }
            
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении сообщения {message_id} из чата {chat_id}: {e}", exc_info=True)
            return None
    
    def get_task_chat_messages(self, chat_id: int, limit: int = 50) -> List[Dict]:
        """
        Получение последних сообщений из чата задачи
        
        ПРИМЕЧАНИЕ: После обновления Bitrix24 комментарии к задачам стали сообщениями в чатах.
        Используйте этот метод для получения всех комментариев к задаче.
        
        Args:
            chat_id: ID чата задачи (из поля chatId задачи)
            limit: Количество сообщений (по умолчанию 50)
            
        Returns:
            Список сообщений из чата
        """
        try:
            result = self._make_request("im.message.get", {
                "CHAT_ID": chat_id,
                "LIMIT": limit
            })
            
            if result and result.get("result"):
                messages = result["result"]
                if isinstance(messages, list):
                    return [
                        {
                            "id": msg.get("id") or msg.get("ID"),
                            "chatId": msg.get("chatId") or msg.get("CHAT_ID") or chat_id,
                            "authorId": msg.get("authorId") or msg.get("AUTHOR_ID"),
                            "message": msg.get("message") or msg.get("MESSAGE"),
                            "date": msg.get("date") or msg.get("DATE"),
                            "files": msg.get("files") or msg.get("FILES", [])
                        }
                        for msg in messages
                    ]
                elif isinstance(messages, dict):
                    # Если вернулся один объект, оборачиваем в список
                    return [{
                        "id": messages.get("id") or messages.get("ID"),
                        "chatId": messages.get("chatId") or messages.get("CHAT_ID") or chat_id,
                        "authorId": messages.get("authorId") or messages.get("AUTHOR_ID"),
                        "message": messages.get("message") or messages.get("MESSAGE"),
                        "date": messages.get("date") or messages.get("DATE"),
                        "files": messages.get("files") or messages.get("FILES", [])
                    }]
            
            return []
        except Exception as e:
            logger.error(f"Ошибка при получении сообщений из чата {chat_id}: {e}", exc_info=True)
            return []
    
    def get_task_chat_info(self, chat_id: int) -> Optional[Dict]:
        """
        Получение информации о чате задачи
        
        Args:
            chat_id: ID чата задачи (из поля chatId задачи)
            
        Returns:
            Информация о чате или None
        """
        try:
            result = self._make_request("im.chat.get", {
                "CHAT_ID": chat_id
            })
            
            if result and result.get("result"):
                chat_data = result["result"]
                return {
                    "id": chat_data.get("id") or chat_data.get("ID") or chat_id,
                    "title": chat_data.get("title") or chat_data.get("TITLE"),
                    "type": chat_data.get("type") or chat_data.get("TYPE"),
                    "avatar": chat_data.get("avatar") or chat_data.get("AVATAR"),
                    "ownerId": chat_data.get("ownerId") or chat_data.get("OWNER_ID"),
                    "members": chat_data.get("members") or chat_data.get("MEMBERS", [])
                }
            
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении информации о чате {chat_id}: {e}", exc_info=True)
            return None
    
    def get_task_comment_text_multiple_methods(self, task_id: int, message_id: int, chat_id: int = None) -> Optional[str]:
        """
        Получение текста комментария к задаче с использованием максимума возможных методов.
        Пробует все методы по очереди до первого рабочего.
        
        Args:
            task_id: ID задачи
            message_id: ID сообщения (MESSAGE_ID из вебхука ONTASKCOMMENTADD)
            chat_id: ID чата задачи (опционально, будет получен автоматически если не указан)
            
        Returns:
            Текст комментария или None, если не удалось получить
        """
        # Если chat_id не указан, получаем его из задачи
        if not chat_id:
            try:
                task_info = self.get_task_by_id(task_id)
                if task_info:
                    chat_id = task_info.get('chatId') or task_info.get('chat_id')
                    if chat_id:
                        logger.info(f"✅ Получен chatId {chat_id} для задачи {task_id}")
                    else:
                        logger.warning(f"⚠️ У задачи {task_id} нет chatId")
                else:
                    logger.warning(f"⚠️ Не удалось получить информацию о задаче {task_id}")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при получении chatId для задачи {task_id}: {e}")
        
        # Список методов для попытки получения комментария
        methods = []
        
        # Метод 1: im.message.get с chatId и id (camelCase)
        if chat_id:
            methods.append({
                'name': 'Метод 1: im.message.get (chatId, id)',
                'func': lambda: self._try_get_message_method1(chat_id, message_id)
            })
        
        # Метод 2: im.message.get с CHAT_ID и ID (UPPERCASE)
        if chat_id:
            methods.append({
                'name': 'Метод 2: im.message.get (CHAT_ID, ID)',
                'func': lambda: self._try_get_message_method2(chat_id, message_id)
            })
        
        # Метод 3: im.message.get с CHAT_ID и id (смешанный)
        if chat_id:
            methods.append({
                'name': 'Метод 3: im.message.get (CHAT_ID, id)',
                'func': lambda: self._try_get_message_method3(chat_id, message_id)
            })
        
        # Метод 4: im.message.get только с ID (без CHAT_ID)
        methods.append({
            'name': 'Метод 4: im.message.get (только ID)',
            'func': lambda: self._try_get_message_method4(message_id)
        })
        
        # Метод 5: im.message.get с MESSAGE_ID вместо ID
        methods.append({
            'name': 'Метод 5: im.message.get (MESSAGE_ID)',
            'func': lambda: self._try_get_message_method5(message_id)
        })
        
        # Метод 6: im.message.list с последующим поиском по ID
        if chat_id:
            methods.append({
                'name': 'Метод 6: im.message.list + поиск по ID',
                'func': lambda: self._try_get_message_method6(chat_id, message_id)
            })
        
        # Метод 7: tasks.task.comment.get (старый метод, может не работать)
        methods.append({
            'name': 'Метод 7: tasks.task.comment.get',
            'func': lambda: self._try_get_message_method7(task_id, message_id)
        })
        
        # Метод 8: im.dialog.messages.get (если существует)
        if chat_id:
            methods.append({
                'name': 'Метод 8: im.dialog.messages.get',
                'func': lambda: self._try_get_message_method8(chat_id, message_id)
            })
        
        # Метод 9: im.dialog.get + im.message.list
        if chat_id:
            methods.append({
                'name': 'Метод 9: im.dialog.get + im.message.list',
                'func': lambda: self._try_get_message_method9(chat_id, message_id)
            })
        
        # Метод 10: im.chat.get + im.message.list
        if chat_id:
            methods.append({
                'name': 'Метод 10: im.chat.get + im.message.list',
                'func': lambda: self._try_get_message_method10(chat_id, message_id)
            })
        
        # Метод 11: im.message.get с chatId и messageId
        if chat_id:
            methods.append({
                'name': 'Метод 11: im.message.get (chatId, messageId)',
                'func': lambda: self._try_get_message_method11(chat_id, message_id)
            })
        
        # Метод 12: im.message.get с CHAT_ID и MESSAGE_ID
        if chat_id:
            methods.append({
                'name': 'Метод 12: im.message.get (CHAT_ID, MESSAGE_ID)',
                'func': lambda: self._try_get_message_method12(chat_id, message_id)
            })
        
        # Метод 13: task.commentitem.get (новый метод из документации Bitrix24)
        methods.append({
            'name': 'Метод 13: task.commentitem.get',
            'func': lambda: self._try_get_message_method13(task_id, message_id)
        })
        
        # Метод 14: forum.message.get (получение комментария через форум API)
        methods.append({
            'name': 'Метод 14: forum.message.get (через форум)',
            'func': lambda: self._try_get_message_method14(task_id, message_id)
        })
        
        # Метод 15: im.dialog.messages.get (новый метод из документации Bitrix24)
        if chat_id:
            methods.append({
                'name': 'Метод 15: im.dialog.messages.get (новый API)',
                'func': lambda: self._try_get_message_method15(chat_id, message_id)
            })
        
        # Пробуем все методы по очереди
        for method_info in methods:
            try:
                logger.info(f"🔍 Попытка: {method_info['name']}")
                result = method_info['func']()
                if result:
                    # Обрабатываем разные форматы ответа (словарь или список)
                    if isinstance(result, list):
                        # Если результат - список, берем первое сообщение
                        if len(result) > 0:
                            result = result[0]
                        else:
                            logger.debug(f"⚠️ Метод {method_info['name']} вернул пустой список")
                            continue
                    
                    if isinstance(result, dict):
                        # Извлекаем текст из разных возможных полей
                        comment_text = (
                            result.get('message') or 
                            result.get('MESSAGE') or 
                            result.get('postMessage') or 
                            result.get('POST_MESSAGE') or
                            result.get('text') or
                            result.get('TEXT')
                        )
                        if comment_text:
                            logger.info(f"✅ Успешно: {method_info['name']} - получен текст комментария")
                            return str(comment_text)
                        else:
                            logger.debug(f"⚠️ Метод {method_info['name']} вернул результат, но без текста сообщения")
                            logger.debug(f"   Доступные поля: {list(result.keys())}")
                    else:
                        logger.debug(f"⚠️ Метод {method_info['name']} вернул неожиданный тип результата: {type(result)}")
                else:
                    logger.debug(f"❌ Метод {method_info['name']} вернул None")
            except Exception as e:
                logger.debug(f"❌ Метод {method_info['name']} вызвал ошибку: {type(e).__name__}: {e}")
                continue
        
        logger.warning(f"⚠️ Все методы получения текста комментария не сработали для сообщения {message_id} к задаче {task_id}")
        return None
    
    def _try_get_message_method1(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """Метод 1: im.message.get с chatId и id (camelCase)"""
        result = self._make_request("im.message.get", {
            "chatId": chat_id,
            "id": message_id
        })
        return result.get("result") if result else None
    
    def _try_get_message_method2(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """Метод 2: im.message.get с CHAT_ID и ID (UPPERCASE)"""
        result = self._make_request("im.message.get", {
            "CHAT_ID": chat_id,
            "ID": message_id
        })
        return result.get("result") if result else None
    
    def _try_get_message_method3(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """Метод 3: im.message.get с CHAT_ID и id (смешанный)"""
        result = self._make_request("im.message.get", {
            "CHAT_ID": chat_id,
            "id": message_id
        })
        return result.get("result") if result else None
    
    def _try_get_message_method4(self, message_id: int) -> Optional[Dict]:
        """Метод 4: im.message.get только с ID (без CHAT_ID)"""
        result = self._make_request("im.message.get", {
            "ID": message_id
        })
        return result.get("result") if result else None
    
    def _try_get_message_method5(self, message_id: int) -> Optional[Dict]:
        """Метод 5: im.message.get с MESSAGE_ID вместо ID"""
        result = self._make_request("im.message.get", {
            "MESSAGE_ID": message_id
        })
        return result.get("result") if result else None
    
    def _try_get_message_method6(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """Метод 6: im.message.list с последующим поиском по ID"""
        result = self._make_request("im.message.list", {
            "CHAT_ID": chat_id,
            "LIMIT": 100
        })
        if result and result.get("result"):
            messages = result["result"] if isinstance(result["result"], list) else [result["result"]]
            for msg in messages:
                msg_id = msg.get("id") or msg.get("ID")
                if msg_id and str(msg_id) == str(message_id):
                    return msg
        return None
    
    def _try_get_message_method7(self, task_id: int, message_id: int) -> Optional[Dict]:
        """Метод 7: tasks.task.comment.get (старый метод)"""
        try:
            result = self._make_request("tasks.task.comment.get", {
                "taskId": task_id,
                "commentId": message_id
            })
            if result and result.get("result"):
                comment_data = result["result"].get("comment")
                if comment_data:
                    return {
                        "message": comment_data.get("POST_MESSAGE"),
                        "authorId": comment_data.get("AUTHOR_ID")
                    }
        except:
            pass
        return None
    
    def _try_get_message_method8(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """
        Метод 8: im.dialog.messages.get
        
        Получает сообщения диалога через API im.dialog.messages.get.
        Пробует разные форматы DIALOG_ID: chat{ID}, {ID}, числовой формат.
        
        См. документацию: IM_DIALOG_MESSAGES_GET_API.md
        """
        try:
            # Пробуем разные форматы DIALOG_ID согласно документации
            dialog_id_variants = [
                f"chat{chat_id}",  # Формат chat29 (рекомендуемый для чатов)
                str(chat_id),       # Формат 29
                chat_id             # Числовой формат
            ]
            
            for dialog_id in dialog_id_variants:
                try:
                    result = self._make_request("im.dialog.messages.get", {
                        "DIALOG_ID": dialog_id,
                        "LIMIT": 100
                    })
                    
                    if result and result.get("result"):
                        result_data = result["result"]
                        
                        # Извлекаем массив сообщений (может быть в поле messages или напрямую)
                        messages = None
                        if isinstance(result_data, dict):
                            messages = result_data.get("messages") or result_data.get("MESSAGES")
                            if not messages and isinstance(result_data.get("result"), list):
                                messages = result_data.get("result")
                        elif isinstance(result_data, list):
                            messages = result_data
                        
                        if messages and isinstance(messages, list):
                            # Ищем нужное сообщение по ID
                            for msg in messages:
                                msg_id = msg.get("id") or msg.get("ID")
                                if msg_id and str(msg_id) == str(message_id):
                                    return msg
                            
                            # Если нашли сообщения, но не нашли нужное, пробуем следующий формат
                            if messages:
                                break
                except Exception as e:
                    # Если ошибка доступа или диалог не найден, пробуем следующий формат
                    error_str = str(e)
                    if "ACCESS_ERROR" in error_str or "DIALOG_ID_EMPTY" in error_str or "DIALOG_NOT_FOUND" in error_str or "404" in error_str:
                        continue
                    # Для других ошибок логируем и пробуем следующий формат
                    logger.debug(f"Ошибка при вызове im.dialog.messages.get с DIALOG_ID={dialog_id}: {e}")
                    continue
        except Exception as e:
            logger.debug(f"Ошибка в _try_get_message_method8: {e}")
        return None
    
    def _try_get_message_method9(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """Метод 9: im.dialog.get + im.message.list"""
        try:
            # Получаем информацию о диалоге
            dialog_result = self._make_request("im.dialog.get", {
                "DIALOG_ID": chat_id
            })
            if dialog_result:
                # Пробуем получить сообщения через im.message.list
                return self._try_get_message_method6(chat_id, message_id)
        except:
            pass
        return None
    
    def _try_get_message_method10(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """Метод 10: im.chat.get + im.message.list"""
        try:
            # Получаем информацию о чате
            chat_result = self._make_request("im.chat.get", {
                "CHAT_ID": chat_id
            })
            if chat_result:
                # Пробуем получить сообщения через im.message.list
                return self._try_get_message_method6(chat_id, message_id)
        except:
            pass
        return None
    
    def _try_get_message_method11(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """Метод 11: im.message.get с chatId и messageId"""
        result = self._make_request("im.message.get", {
            "chatId": chat_id,
            "messageId": message_id
        })
        return result.get("result") if result else None
    
    def _try_get_message_method12(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """Метод 12: im.message.get с CHAT_ID и MESSAGE_ID"""
        result = self._make_request("im.message.get", {
            "CHAT_ID": chat_id,
            "MESSAGE_ID": message_id
        })
        return result.get("result") if result else None
    
    def _try_get_message_method13(self, task_id: int, item_id: int) -> Optional[Dict]:
        """
        Метод 13: task.commentitem.get (новый метод из документации Bitrix24)
        
        Параметры согласно документации:
        - TASKID (integer) — идентификатор задачи
        - ITEMID (integer) — идентификатор комментария
        """
        try:
            # Пробуем разные варианты названия метода и параметров
            variants = [
                # Вариант 1: task.commentitem.get с TASKID и ITEMID (как в документации)
                {
                    "method": "task.commentitem.get",
                    "params": {"TASKID": task_id, "ITEMID": item_id}
                },
                # Вариант 2: tasks.task.commentitem.get с TASKID и ITEMID
                {
                    "method": "tasks.task.commentitem.get",
                    "params": {"TASKID": task_id, "ITEMID": item_id}
                },
                # Вариант 3: task.commentitem.get с taskId и itemId (camelCase)
                {
                    "method": "task.commentitem.get",
                    "params": {"taskId": task_id, "itemId": item_id}
                },
                # Вариант 4: tasks.task.commentitem.get с taskId и itemId
                {
                    "method": "tasks.task.commentitem.get",
                    "params": {"taskId": task_id, "itemId": item_id}
                },
                # Вариант 5: task.commentitem.get с TASK_ID и ITEM_ID
                {
                    "method": "task.commentitem.get",
                    "params": {"TASK_ID": task_id, "ITEM_ID": item_id}
                },
                # Вариант 6: tasks.task.commentitem.get с TASK_ID и ITEM_ID
                {
                    "method": "tasks.task.commentitem.get",
                    "params": {"TASK_ID": task_id, "ITEM_ID": item_id}
                },
            ]
            
            for variant in variants:
                try:
                    logger.debug(f"Попытка метода {variant['method']} с параметрами {variant['params']}")
                    result = self._make_request(variant["method"], variant["params"])
                    
                    if result and result.get("result"):
                        # Обрабатываем результат - может быть в разных форматах
                        comment_data = result["result"]
                        
                        # Если результат - словарь с ключом "comment" или "item"
                        if isinstance(comment_data, dict):
                            # Пробуем разные возможные ключи
                            comment = (
                                comment_data.get("comment") or 
                                comment_data.get("item") or 
                                comment_data.get("COMMENT") or
                                comment_data.get("ITEM") or
                                comment_data  # Если сам результат и есть комментарий
                            )
                            
                            if isinstance(comment, dict):
                                # Извлекаем текст из разных возможных полей
                                comment_text = (
                                    comment.get("POST_MESSAGE") or
                                    comment.get("postMessage") or
                                    comment.get("MESSAGE") or
                                    comment.get("message") or
                                    comment.get("TEXT") or
                                    comment.get("text") or
                                    comment.get("CONTENT") or
                                    comment.get("content")
                                )
                                
                                if comment_text:
                                    return {
                                        "message": comment_text,
                                        "authorId": comment.get("AUTHOR_ID") or comment.get("authorId"),
                                        "id": item_id
                                    }
                            elif isinstance(comment, str):
                                # Если результат - просто строка с текстом
                                return {
                                    "message": comment,
                                    "id": item_id
                                }
                        
                        # Если результат - строка напрямую
                        elif isinstance(comment_data, str):
                            return {
                                "message": comment_data,
                                "id": item_id
                            }
                        
                        logger.debug(f"Метод {variant['method']} вернул результат, но не удалось извлечь текст")
                        logger.debug(f"   Структура результата: {type(comment_data)}, ключи: {list(comment_data.keys()) if isinstance(comment_data, dict) else 'N/A'}")
                except Exception as e:
                    error_str = str(e)
                    # Если метод не найден (404), пробуем следующий вариант
                    if "404" in error_str or "not found" in error_str.lower() or "Method not found" in error_str:
                        logger.debug(f"Метод {variant['method']} не найден, пробуем следующий вариант")
                        continue
                    # Для других ошибок логируем и пробуем следующий вариант
                    logger.debug(f"Ошибка при вызове {variant['method']}: {e}")
                    continue
            
            return None
        except Exception as e:
            logger.debug(f"Ошибка в _try_get_message_method13: {e}")
            return None
    
    def _try_get_message_method14(self, task_id: int, message_id: int) -> Optional[Dict]:
        """
        Метод 14: forum.message.get (получение комментария через форум API)
        
        Согласно PHP коду Bitrix24, комментарии к задачам хранятся в форуме:
        - Топик форума имеет XML_ID = 'TASK_' + taskId
        - Сообщения в топике - это комментарии к задаче
        - PARAM1 != 'TK' для обычных комментариев
        
        Параметры:
        - task_id: ID задачи
        - message_id: ID сообщения (комментария) в форуме
        """
        try:
            # Пробуем разные варианты получения сообщения через форум API
            variants = [
                # Вариант 1: forum.message.get с ID сообщения
                {
                    "method": "forum.message.get",
                    "params": {"ID": message_id}
                },
                # Вариант 2: forum.message.get с id (camelCase)
                {
                    "method": "forum.message.get",
                    "params": {"id": message_id}
                },
                # Вариант 3: forum.message.get с MESSAGE_ID
                {
                    "method": "forum.message.get",
                    "params": {"MESSAGE_ID": message_id}
                },
                # Вариант 4: forum.message.get с messageId
                {
                    "method": "forum.message.get",
                    "params": {"messageId": message_id}
                },
            ]
            
            for variant in variants:
                try:
                    logger.debug(f"Попытка метода {variant['method']} с параметрами {variant['params']}")
                    result = self._make_request(variant["method"], variant["params"])
                    
                    if result and result.get("result"):
                        message_data = result["result"]
                        
                        # Если результат - словарь с сообщением
                        if isinstance(message_data, dict):
                            # Извлекаем текст из разных возможных полей
                            message_text = (
                                message_data.get("POST_MESSAGE") or
                                message_data.get("postMessage") or
                                message_data.get("MESSAGE") or
                                message_data.get("message") or
                                message_data.get("TEXT") or
                                message_data.get("text") or
                                message_data.get("CONTENT") or
                                message_data.get("content")
                            )
                            
                            if message_text:
                                return {
                                    "message": message_text,
                                    "authorId": message_data.get("AUTHOR_ID") or message_data.get("authorId"),
                                    "id": message_id
                                }
                        
                        # Если результат - строка напрямую
                        elif isinstance(message_data, str):
                            return {
                                "message": message_data,
                                "id": message_id
                            }
                        
                        logger.debug(f"Метод {variant['method']} вернул результат, но не удалось извлечь текст")
                        logger.debug(f"   Структура результата: {type(message_data)}, ключи: {list(message_data.keys()) if isinstance(message_data, dict) else 'N/A'}")
                except Exception as e:
                    error_str = str(e)
                    # Если метод не найден (404), пробуем следующий вариант
                    if "404" in error_str or "not found" in error_str.lower() or "Method not found" in error_str:
                        logger.debug(f"Метод {variant['method']} не найден, пробуем следующий вариант")
                        continue
                    # Для других ошибок логируем и пробуем следующий вариант
                    logger.debug(f"Ошибка при вызове {variant['method']}: {e}")
                    continue
            
            # Если прямой метод не сработал, пробуем получить через топик задачи
            # Согласно PHP коду: XML_ID топика = 'TASK_' + taskId
            topic_xml_id = f"TASK_{task_id}"
            
            # Пробуем получить список сообщений из топика задачи
            list_variants = [
                # Вариант 1: forum.message.list с фильтром по топику
                {
                    "method": "forum.message.list",
                    "params": {
                        "FILTER": {
                            "TOPIC.XML_ID": topic_xml_id,
                            "!PARAM1": "TK"
                        }
                    }
                },
                # Вариант 2: forum.message.list с XML_ID топика
                {
                    "method": "forum.message.list",
                    "params": {
                        "XML_ID": topic_xml_id
                    }
                },
                # Вариант 3: forum.topic.get по XML_ID, затем forum.message.list
                {
                    "method": "forum.topic.get",
                    "params": {
                        "XML_ID": topic_xml_id
                    }
                },
            ]
            
            topic_id = None
            for list_variant in list_variants:
                try:
                    logger.debug(f"Попытка получения топика/сообщений через {list_variant['method']}")
                    list_result = self._make_request(list_variant["method"], list_variant["params"])
                    
                    if list_result and list_result.get("result"):
                        result_data = list_result["result"]
                        
                        # Если получили топик, извлекаем его ID
                        if isinstance(result_data, dict) and list_variant["method"] == "forum.topic.get":
                            topic_id = result_data.get("ID") or result_data.get("id")
                            if topic_id:
                                logger.debug(f"Найден топик с ID {topic_id} для задачи {task_id}")
                                break
                        
                        # Если получили список сообщений
                        elif isinstance(result_data, list) or (isinstance(result_data, dict) and "messages" in result_data):
                            messages = result_data if isinstance(result_data, list) else result_data.get("messages", [])
                            
                            # Ищем нужное сообщение по ID
                            for msg in messages:
                                msg_id = msg.get("ID") or msg.get("id")
                                if msg_id and str(msg_id) == str(message_id):
                                    # Нашли нужное сообщение
                                    message_text = (
                                        msg.get("POST_MESSAGE") or
                                        msg.get("postMessage") or
                                        msg.get("MESSAGE") or
                                        msg.get("message") or
                                        msg.get("TEXT") or
                                        msg.get("text")
                                    )
                                    
                                    if message_text:
                                        return {
                                            "message": message_text,
                                            "authorId": msg.get("AUTHOR_ID") or msg.get("authorId"),
                                            "id": message_id
                                        }
                except Exception as e:
                    error_str = str(e)
                    if "404" in error_str or "not found" in error_str.lower() or "Method not found" in error_str:
                        logger.debug(f"Метод {list_variant['method']} не найден")
                        continue
                    logger.debug(f"Ошибка при вызове {list_variant['method']}: {e}")
                    continue
            
            # Если получили topic_id, пробуем получить сообщения из топика
            if topic_id:
                try:
                    logger.debug(f"Попытка получить сообщения из топика {topic_id}")
                    messages_result = self._make_request("forum.message.list", {
                        "TOPIC_ID": topic_id,
                        "FILTER": {
                            "!PARAM1": "TK"
                        }
                    })
                    
                    if messages_result and messages_result.get("result"):
                        messages = messages_result["result"]
                        if isinstance(messages, list):
                            for msg in messages:
                                msg_id = msg.get("ID") or msg.get("id")
                                if msg_id and str(msg_id) == str(message_id):
                                    message_text = (
                                        msg.get("POST_MESSAGE") or
                                        msg.get("postMessage") or
                                        msg.get("MESSAGE") or
                                        msg.get("message")
                                    )
                                    
                                    if message_text:
                                        return {
                                            "message": message_text,
                                            "authorId": msg.get("AUTHOR_ID") or msg.get("authorId"),
                                            "id": message_id
                                        }
                except Exception as e:
                    logger.debug(f"Ошибка при получении сообщений из топика {topic_id}: {e}")
            
            return None
        except Exception as e:
            logger.debug(f"Ошибка в _try_get_message_method14: {e}")
            return None
    
    def _try_get_message_method15(self, chat_id: int, message_id: int) -> Optional[Dict]:
        """
        Метод 15: im.dialog.messages.get (новый метод из документации Bitrix24)
        
        Получает список последних сообщений в чате и ищет нужное сообщение по ID.
        
        Согласно документации:
        - DIALOG_ID может быть в формате 'chat{chat_id}' или просто '{chat_id}'
        - Если не переданы LAST_ID и FIRST_ID, будут загружены последние 20 сообщений
        - Для загрузки предыдущих сообщений используется LAST_ID
        - Для загрузки следующих сообщений используется FIRST_ID
        
        Параметры:
        - chat_id: ID чата задачи
        - message_id: ID сообщения (комментария), которое нужно найти
        """
        try:
            # Пробуем разные форматы DIALOG_ID
            dialog_id_variants = [
                f"chat{chat_id}",  # Формат chat29
                str(chat_id),      # Формат 29
                chat_id            # Числовой формат
            ]
            
            for dialog_id in dialog_id_variants:
                try:
                    # Сначала пробуем получить последние сообщения (без фильтров)
                    logger.debug(f"Попытка получить сообщения через im.dialog.messages.get с DIALOG_ID={dialog_id}")
                    result = self._make_request("im.dialog.messages.get", {
                        "DIALOG_ID": dialog_id,
                        "LIMIT": 100  # Увеличиваем лимит для поиска нужного сообщения
                    })
                    
                    if result and result.get("result"):
                        result_data = result["result"]
                        
                        # Извлекаем массив сообщений
                        messages = None
                        if isinstance(result_data, dict):
                            messages = result_data.get("messages") or result_data.get("MESSAGES")
                        elif isinstance(result_data, list):
                            messages = result_data
                        
                        if messages and isinstance(messages, list):
                            # Ищем нужное сообщение по ID
                            for msg in messages:
                                msg_id = msg.get("id") or msg.get("ID")
                                if msg_id and str(msg_id) == str(message_id):
                                    # Нашли нужное сообщение
                                    message_text = (
                                        msg.get("text") or
                                        msg.get("TEXT") or
                                        msg.get("message") or
                                        msg.get("MESSAGE")
                                    )
                                    
                                    if message_text:
                                        return {
                                            "message": message_text,
                                            "authorId": msg.get("author_id") or msg.get("AUTHOR_ID"),
                                            "id": message_id,
                                            "date": msg.get("date") or msg.get("DATE")
                                        }
                            
                            # Если не нашли в первых 100 сообщениях, пробуем загрузить предыдущие
                            # Используем LAST_ID с минимальным ID из полученных сообщений
                            if len(messages) > 0:
                                min_id = None
                                for msg in messages:
                                    msg_id = msg.get("id") or msg.get("ID")
                                    if msg_id:
                                        msg_id_int = int(msg_id)
                                        if min_id is None or msg_id_int < min_id:
                                            min_id = msg_id_int
                                
                                # Если нужное сообщение имеет больший ID, чем минимальный в выборке,
                                # значит оно в более старых сообщениях - пробуем загрузить их
                                if min_id and int(message_id) < min_id:
                                    logger.debug(f"Сообщение {message_id} не найдено в первых 100, пробуем загрузить предыдущие (LAST_ID={min_id})")
                                    
                                    # Загружаем предыдущие сообщения
                                    prev_result = self._make_request("im.dialog.messages.get", {
                                        "DIALOG_ID": dialog_id,
                                        "LAST_ID": min_id,
                                        "LIMIT": 100
                                    })
                                    
                                    if prev_result and prev_result.get("result"):
                                        prev_data = prev_result["result"]
                                        prev_messages = None
                                        if isinstance(prev_data, dict):
                                            prev_messages = prev_data.get("messages") or prev_data.get("MESSAGES")
                                        elif isinstance(prev_data, list):
                                            prev_messages = prev_data
                                        
                                        if prev_messages:
                                            # Ищем в предыдущих сообщениях
                                            for msg in prev_messages:
                                                msg_id = msg.get("id") or msg.get("ID")
                                                if msg_id and str(msg_id) == str(message_id):
                                                    message_text = (
                                                        msg.get("text") or
                                                        msg.get("TEXT") or
                                                        msg.get("message") or
                                                        msg.get("MESSAGE")
                                                    )
                                                    
                                                    if message_text:
                                                        return {
                                                            "message": message_text,
                                                            "authorId": msg.get("author_id") or msg.get("AUTHOR_ID"),
                                                            "id": message_id,
                                                            "date": msg.get("date") or msg.get("DATE")
                                                        }
                                
                                # Если нужное сообщение имеет больший ID, чем максимальный в выборке,
                                # значит оно в более новых сообщениях - пробуем загрузить их
                                else:
                                    max_id = None
                                    for msg in messages:
                                        msg_id = msg.get("id") or msg.get("ID")
                                        if msg_id:
                                            msg_id_int = int(msg_id)
                                            if max_id is None or msg_id_int > max_id:
                                                max_id = msg_id_int
                                    
                                    if max_id and int(message_id) > max_id:
                                        logger.debug(f"Сообщение {message_id} не найдено в первых 100, пробуем загрузить следующие (FIRST_ID={max_id})")
                                        
                                        # Загружаем следующие сообщения
                                        next_result = self._make_request("im.dialog.messages.get", {
                                            "DIALOG_ID": dialog_id,
                                            "FIRST_ID": max_id,
                                            "LIMIT": 100
                                        })
                                        
                                        if next_result and next_result.get("result"):
                                            next_data = next_result["result"]
                                            next_messages = None
                                            if isinstance(next_data, dict):
                                                next_messages = next_data.get("messages") or next_data.get("MESSAGES")
                                            elif isinstance(next_data, list):
                                                next_messages = next_data
                                            
                                            if next_messages:
                                                # Ищем в следующих сообщениях
                                                for msg in next_messages:
                                                    msg_id = msg.get("id") or msg.get("ID")
                                                    if msg_id and str(msg_id) == str(message_id):
                                                        message_text = (
                                                            msg.get("text") or
                                                            msg.get("TEXT") or
                                                            msg.get("message") or
                                                            msg.get("MESSAGE")
                                                        )
                                                        
                                                        if message_text:
                                                            return {
                                                                "message": message_text,
                                                                "authorId": msg.get("author_id") or msg.get("AUTHOR_ID"),
                                                                "id": message_id,
                                                                "date": msg.get("date") or msg.get("DATE")
                                                            }
                        
                        logger.debug(f"Метод im.dialog.messages.get вернул результат, но сообщение {message_id} не найдено")
                        logger.debug(f"   Структура результата: {type(result_data)}, ключи: {list(result_data.keys()) if isinstance(result_data, dict) else 'N/A'}")
                        logger.debug(f"   Количество сообщений: {len(messages) if messages else 0}")
                        
                        # Если нашли сообщения, но не нашли нужное, пробуем следующий формат DIALOG_ID
                        if messages:
                            break
                except Exception as e:
                    error_str = str(e)
                    # Если ошибка доступа или диалог не найден, пробуем следующий формат
                    if "ACCESS_ERROR" in error_str or "DIALOG_ID_EMPTY" in error_str or "404" in error_str:
                        logger.debug(f"Ошибка доступа или формат DIALOG_ID={dialog_id} не подошел, пробуем следующий")
                        continue
                    # Для других ошибок логируем и пробуем следующий формат
                    logger.debug(f"Ошибка при вызове im.dialog.messages.get с DIALOG_ID={dialog_id}: {e}")
                    continue
            
            return None
        except Exception as e:
            logger.debug(f"Ошибка в _try_get_message_method15: {e}")
            return None
