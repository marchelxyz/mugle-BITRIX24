"""
Модуль для работы с API Unisender для массовых email-рассылок
Документация: https://www.unisender.com/ru/support/api/common/bulk-email/
"""
import requests
import os
import logging
import json
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class UnisenderClient:
    """Клиент для работы с API Unisender"""
    
    BASE_URL = "https://api.unisender.com/ru/api"
    
    def __init__(self, api_key: str = None):
        """
        Инициализация клиента Unisender
        
        Args:
            api_key: API ключ Unisender (можно получить в личном кабинете)
        """
        self.api_key = api_key or os.getenv("UNISENDER_API_KEY")
        if not self.api_key:
            raise ValueError("UNISENDER_API_KEY не установлен в переменных окружения")
    
    def _make_request(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Выполнение запроса к API Unisender
        
        Args:
            method: Метод API (например, sendEmail, importContacts)
            params: Параметры запроса
            
        Returns:
            Ответ от API
        """
        if params is None:
            params = {}
        
        # Добавляем обязательные параметры
        params['api_key'] = self.api_key
        params['format'] = 'json'
        
        url = f"{self.BASE_URL}/{method}"
        
        try:
            response = requests.post(url, data=params)
            response.raise_for_status()
            
            result = response.json()
            
            # Проверяем наличие ошибок в ответе
            if 'error' in result:
                error_msg = result.get('error', 'Неизвестная ошибка')
                logger.error(f"Ошибка Unisender API для метода {method}: {error_msg}")
                raise Exception(f"Unisender API ошибка: {error_msg}")
            
            return result
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе к Unisender API ({method}): {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON ответа от Unisender API ({method}): {e}")
            logger.debug(f"Ответ сервера: {response.text[:500]}")
            raise
    
    def send_email(
        self,
        email: str,
        sender_name: str,
        sender_email: str,
        subject: str,
        body: str,
        list_id: Optional[int] = None,
        tags: Optional[List[str]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Отправка одиночного email через метод sendEmail
        
        Документация: https://www.unisender.com/ru/support/api/messages/sendemail/
        
        Args:
            email: Email адрес получателя
            sender_name: Имя отправителя
            sender_email: Email адрес отправителя
            subject: Тема письма
            body: Тело письма (HTML или текст)
            list_id: ID списка контактов (опционально)
            tags: Список тегов для контакта (опционально)
            **kwargs: Дополнительные параметры (wrap_type, attachments и т.д.)
            
        Returns:
            Результат отправки с информацией о статусе
        """
        params = {
            'email': email,
            'sender_name': sender_name,
            'sender_email': sender_email,
            'subject': subject,
            'body': body,
        }
        
        if list_id:
            params['list_id'] = list_id
        
        if tags:
            params['tags'] = ','.join(tags)
        
        # Добавляем дополнительные параметры
        params.update(kwargs)
        
        return self._make_request('sendEmail', params)
    
    def import_contacts(
        self,
        contacts: List[Dict[str, Any]],
        field_names: List[str] = None,
        list_ids: Optional[List[int]] = None,
        override_lists: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Импорт контактов через метод importContacts
        
        Документация: https://www.unisender.com/ru/support/api/contacts/importcontacts/
        
        Args:
            contacts: Список контактов, каждый контакт - словарь с полями
            field_names: Список названий полей (например, ['email', 'Name', 'phone'])
            list_ids: ID списков, в которые добавить контакты
            override_lists: Если True, контакты будут добавлены только в указанные списки
            **kwargs: Дополнительные параметры
            
        Returns:
            Результат импорта с информацией о статусе
        """
        if not contacts:
            raise ValueError("Список контактов не может быть пустым")
        
        # Если field_names не указаны, определяем из первого контакта
        if not field_names:
            field_names = list(contacts[0].keys())
        
        params = {}
        
        # Добавляем названия полей
        for idx, field_name in enumerate(field_names):
            params[f'field_names[{idx}]'] = field_name
        
        # Добавляем данные контактов
        for contact_idx, contact in enumerate(contacts):
            for field_idx, field_name in enumerate(field_names):
                value = contact.get(field_name, '')
                params[f'data[{contact_idx}][{field_idx}]'] = value
        
        if list_ids:
            for idx, list_id in enumerate(list_ids):
                params[f'list_ids[{idx}]'] = list_id
        
        if override_lists:
            params['override_lists'] = 1
        
        # Добавляем дополнительные параметры
        params.update(kwargs)
        
        return self._make_request('importContacts', params)
    
    def subscribe(
        self,
        email: str,
        list_ids: List[int],
        tags: Optional[List[str]] = None,
        double_optin: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Подписка контакта на списки через метод subscribe
        
        Документация: https://www.unisender.com/ru/support/api/contacts/subscribe/
        
        Args:
            email: Email адрес контакта
            list_ids: Список ID списков для подписки
            tags: Список тегов для контакта
            double_optin: Использовать двойную подписку (подтверждение по email)
            **kwargs: Дополнительные параметры (fields и т.д.)
            
        Returns:
            Результат подписки
        """
        params = {
            'email': email,
        }
        
        for idx, list_id in enumerate(list_ids):
            params[f'list_ids[{idx}]'] = list_id
        
        if tags:
            params['tags'] = ','.join(tags)
        
        if double_optin:
            params['double_optin'] = 1
        
        # Добавляем дополнительные параметры
        params.update(kwargs)
        
        return self._make_request('subscribe', params)
    
    def get_lists(self) -> Dict[str, Any]:
        """
        Получение списка всех списков контактов через метод getLists
        
        Документация: https://www.unisender.com/ru/support/api/contacts/getlists/
        
        Returns:
            Список списков контактов
        """
        return self._make_request('getLists', {})
    
    def create_campaign(
        self,
        message_id: int,
        list_id: int,
        start_time: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Создание рассылки через метод createCampaign
        
        Документация: https://www.unisender.com/ru/support/api/messages/createcampaign/
        
        Args:
            message_id: ID сообщения (созданного через createEmailMessage)
            list_id: ID списка контактов для рассылки
            start_time: Время начала рассылки (формат: YYYY-MM-DD HH:MM:SS)
            **kwargs: Дополнительные параметры
            
        Returns:
            Результат создания рассылки с campaign_id
        """
        params = {
            'message_id': message_id,
            'list_id': list_id,
        }
        
        if start_time:
            params['start_time'] = start_time
        
        # Добавляем дополнительные параметры
        params.update(kwargs)
        
        return self._make_request('createCampaign', params)
    
    def create_email_message(
        self,
        sender_name: str,
        sender_email: str,
        subject: str,
        body: str,
        list_id: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Создание email сообщения через метод createEmailMessage
        
        Документация: https://www.unisender.com/ru/support/api/messages/createemailmessage/
        
        Args:
            sender_name: Имя отправителя
            sender_email: Email адрес отправителя
            subject: Тема письма
            body: Тело письма (HTML или текст)
            list_id: ID списка контактов (опционально)
            **kwargs: Дополнительные параметры
            
        Returns:
            Результат создания сообщения с message_id
        """
        params = {
            'sender_name': sender_name,
            'sender_email': sender_email,
            'subject': subject,
            'body': body,
        }
        
        if list_id:
            params['list_id'] = list_id
        
        # Добавляем дополнительные параметры
        params.update(kwargs)
        
        return self._make_request('createEmailMessage', params)
