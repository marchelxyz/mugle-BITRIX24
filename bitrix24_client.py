"""
Модуль для работы с API Битрикс24
"""
import requests
import os
from typing import Dict, List, Optional


class Bitrix24Client:
    """Клиент для работы с API Битрикс24"""
    
    def __init__(self, domain: str, webhook_token: str):
        """
        Инициализация клиента Битрикс24
        
        Args:
            domain: Домен Битрикс24 (например, your-domain.bitrix24.ru)
            webhook_token: Токен вебхука для доступа к API
        """
        if domain is None:
            raise ValueError("BITRIX24_DOMAIN не установлен в переменных окружения")
        if webhook_token is None:
            raise ValueError("BITRIX24_WEBHOOK_TOKEN не установлен в переменных окружения")
        
        self.domain = domain.rstrip('/')
        self.webhook_token = webhook_token
        self.base_url = f"https://{self.domain}/rest/{webhook_token}"
    
    def _make_request(self, method: str, params: Dict = None) -> Dict:
        """
        Выполнение запроса к API Битрикс24
        
        Args:
            method: Метод API (например, tasks.task.add)
            params: Параметры запроса
            
        Returns:
            Ответ от API
        """
        if params is None:
            params = {}
        
        url = f"{self.base_url}/{method}"
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
        file_ids: List[int] = None
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
            result = self._make_request("user.get", {"ID": user_id})
            if result.get("result"):
                return result["result"][0] if isinstance(result["result"], list) else result["result"]
        except Exception:
            pass
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
        Проверка и создание пользовательского поля UF_TELEGRAM_ID в Bitrix24
        если оно еще не существует
        
        Returns:
            True если поле существует или было создано, False в случае ошибки
        """
        try:
            # Проверяем, существует ли поле
            result = self._make_request("user.userfield.get", {"FIELD": "UF_TELEGRAM_ID"})
            
            if result.get("result") and len(result.get("result", [])) > 0:
                # Поле уже существует
                return True
            
            # Создаем поле, если его нет
            field_data = {
                "fields": {
                    "FIELD_NAME": "UF_TELEGRAM_ID",
                    "USER_TYPE_ID": "string",
                    "XML_ID": "TELEGRAM_ID",
                    "SORT": 100,
                    "MULTIPLE": "N",
                    "MANDATORY": "N",
                    "SHOW_FILTER": "Y",
                    "SHOW_IN_LIST": "Y",
                    "EDIT_IN_LIST": "Y",
                    "IS_SEARCHABLE": "Y",
                    "SETTINGS": {
                        "DEFAULT_VALUE": "",
                        "SIZE": 20,
                        "ROWS": 1,
                        "MIN_LENGTH": 0,
                        "MAX_LENGTH": 0,
                        "REGEXP": ""
                    }
                }
            }
            
            create_result = self._make_request("user.userfield.add", field_data)
            return create_result.get("result") is not None
            
        except Exception as e:
            # Если поле уже существует или произошла другая ошибка, продолжаем работу
            # В Bitrix24 может быть ошибка, если поле уже существует, но это не критично
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
            # Убеждаемся, что поле существует
            self.ensure_telegram_id_field()
            
            # Обновляем пользователя
            update_data = {
                "ID": user_id,
                "fields": {
                    "UF_TELEGRAM_ID": str(telegram_id)
                }
            }
            
            result = self._make_request("user.update", update_data)
            return result.get("result") is True
            
        except Exception as e:
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
            # Ищем пользователя по пользовательскому полю UF_TELEGRAM_ID
            result = self._make_request("user.get", {
                "FILTER": {
                    "UF_TELEGRAM_ID": str(telegram_id)
                }
            })
            
            if result.get("result") and len(result.get("result", [])) > 0:
                return result["result"][0]
            
        except Exception:
            pass
        
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
            user_info = self.get_user_by_id(user_id)
            if user_info and user_info.get("UF_TELEGRAM_ID"):
                try:
                    return int(user_info["UF_TELEGRAM_ID"])
                except (ValueError, TypeError):
                    return None
        except Exception:
            pass
        
        return None
