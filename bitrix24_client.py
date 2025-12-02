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
        responsible_id: int,
        creator_id: int,
        description: str = "",
        deadline: str = None
    ) -> Dict:
        """
        Создание задачи в Битрикс24
        
        Args:
            title: Название задачи
            responsible_id: ID ответственного пользователя
            creator_id: ID создателя задачи
            description: Описание задачи
            deadline: Дедлайн задачи (формат: YYYY-MM-DD HH:MI:SS)
            
        Returns:
            Результат создания задачи
        """
        task_data = {
            "fields": {
                "TITLE": title,
                "RESPONSIBLE_ID": responsible_id,
                "CREATED_BY": creator_id,
                "DESCRIPTION": description,
            }
        }
        
        if deadline:
            task_data["fields"]["DEADLINE"] = deadline
        
        result = self._make_request("tasks.task.add", task_data)
        return result
    
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
