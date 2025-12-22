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
            
            # Парсим JSON ответ согласно документации Unisender
            # API всегда возвращает JSON в формате {"result": {...}} или {"error": "..."}
            try:
                result = response.json()
                logger.debug(f"Ответ от Unisender API ({method}): тип={type(result).__name__}, ключи={list(result.keys()) if isinstance(result, dict) else 'N/A'}")
            except (ValueError, TypeError, json.JSONDecodeError) as json_error:
                error_msg = f"Ошибка парсинга JSON ответа от Unisender API: {json_error}. Ответ сервера: {response.text[:500]}"
                logger.error(f"Ошибка Unisender API для метода {method}: {error_msg}")
                raise Exception(f"Unisender API ошибка: {error_msg}")
            
            # Проверяем, что результат является словарем
            # Если результат - строка (не должно быть согласно документации, но обрабатываем для надежности)
            if isinstance(result, str):
                logger.warning(f"Unisender API вернул строку вместо словаря для метода {method}: {result[:100]}")
                try:
                    result = json.loads(result)
                except (ValueError, TypeError, json.JSONDecodeError):
                    error_msg = f"Неожиданный тип ответа от Unisender API: строка '{result[:100]}'. Ожидался словарь."
                    logger.error(f"Ошибка Unisender API для метода {method}: {error_msg}")
                    raise Exception(f"Unisender API ошибка: {error_msg}")
            
            if not isinstance(result, dict):
                error_msg = f"Неожиданный тип ответа от Unisender API: {type(result).__name__}. Ожидался словарь, получено: {result}"
                logger.error(f"Ошибка Unisender API для метода {method}: {error_msg}")
                raise Exception(f"Unisender API ошибка: {error_msg}")
            
            # Проверяем наличие ошибок в ответе согласно документации Unisender
            # Формат ошибки: {"error": "текст ошибки"} или {"error": "текст", "code": "код"}
            if isinstance(result, dict) and 'error' in result:
                error_value = result.get('error', 'Неизвестная ошибка')
                error_code = result.get('code')
                
                # Обрабатываем error_value в зависимости от его типа
                if isinstance(error_value, dict):
                    # Если error_value - это словарь, извлекаем сообщение
                    error_msg = error_value.get('message', str(error_value))
                elif isinstance(error_value, str):
                    # Если error_value - это строка, используем её как есть
                    error_msg = error_value
                else:
                    # В остальных случаях преобразуем в строку
                    error_msg = str(error_value)
                
                # Формируем полное сообщение об ошибке
                full_error_msg = str(error_msg)
                if error_code:
                    full_error_msg = f"{error_msg} (код: {error_code})"
                
                logger.error(f"Ошибка Unisender API для метода {method}: {full_error_msg}")
                raise Exception(f"Unisender API ошибка: {full_error_msg}")
            
            # Согласно документации Unisender, успешные ответы содержат ключ 'result'
            # Возвращаем весь ответ, включая поле 'result' если оно есть
            return result
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе к Unisender API ({method}): {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON ответа от Unisender API ({method}): {e}")
            logger.debug(f"Ответ сервера: {response.text[:500]}")
            raise
        except Exception as e:
            # Перехватываем все остальные исключения и логируем их
            logger.error(f"Неожиданная ошибка при запросе к Unisender API ({method}): {e}", exc_info=True)
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
            Результат отправки с информацией о статусе.
            Согласно документации Unisender, успешный ответ имеет формат:
            {"result": {"email_id": "...", "status": "..."}}
        """
        try:
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
            
            logger.info(f"📧 Отправка email на {email} через Unisender API")
            result = self._make_request('sendEmail', params)
            
            # Подробное логирование результата для диагностики
            logger.debug(f"Ответ от Unisender API при отправке email на {email}: тип={type(result).__name__}, значение={result}")
            
            # Проверяем, что результат является словарем
            if not isinstance(result, dict):
                error_msg = f"Неожиданный тип ответа от Unisender API при отправке email: {type(result).__name__}. Ожидался словарь, получено: {result}"
                logger.error(f"Неожиданная ошибка при отправке email на {email}: {error_msg}")
                logger.error(f"Детали результата: тип={type(result)}, значение={result}, repr={repr(result)}")
                raise Exception(f"Unisender API ошибка: {error_msg}")
            
            # Логируем структуру результата для диагностики
            logger.debug(f"Структура результата отправки email на {email}: ключи={list(result.keys()) if isinstance(result, dict) else 'N/A'}")
            
            # Проверяем наличие ошибок в результате (на случай, если они не были обработаны в _make_request)
            if isinstance(result, dict) and 'error' in result:
                error_value = result.get('error')
                logger.warning(f"Обнаружена ошибка в результате отправки email на {email}: {error_value}")
            
            # Согласно документации Unisender, успешные ответы содержат поле 'result'
            # Логируем успешную отправку
            if isinstance(result, dict) and 'result' in result:
                logger.info(f"✅ Email успешно отправлен на {email}. Результат: {result.get('result')}")
            else:
                logger.warning(f"⚠️ Ответ от Unisender API для {email} не содержит поле 'result': {result}")
            
            # Возвращаем весь ответ (включая поле 'result' если оно есть)
            return result
            
        except Exception as e:
            # Логируем ошибку с правильным сообщением и подробной информацией
            error_msg = str(e)
            error_type = type(e).__name__
            logger.error(f"❌ Неожиданная ошибка при отправке email на {email}: {error_msg}")
            logger.error(f"   Тип ошибки: {error_type}")
            logger.error(f"   Параметры запроса: email={email}, sender_email={sender_email}, subject={subject[:50]}...")
            logger.error(f"   Детали исключения:", exc_info=True)
            raise
    
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
    
    def send_email_safe(
        self,
        email: str,
        sender_name: str,
        sender_email: str,
        subject: str,
        body: str,
        list_id: Optional[int] = None,
        tags: Optional[List[str]] = None,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """
        Безопасная обертка над send_email с улучшенной обработкой ошибок и логированием
        
        Эта функция безопасно вызывает send_email и обрабатывает результат,
        избегая ошибок типа 'str' object has no attribute 'get'
        
        Args:
            email: Email адрес получателя
            sender_name: Имя отправителя
            sender_email: Email адрес отправителя
            subject: Тема письма
            body: Тело письма (HTML или текст)
            list_id: ID списка контактов (опционально)
            tags: Список тегов для контакта (опционально)
            **kwargs: Дополнительные параметры
            
        Returns:
            Результат отправки (словарь) или None в случае ошибки
        """
        try:
            logger.info(f"🔵 Вызов send_email_safe для {email}")
            result = self.send_email(
                email=email,
                sender_name=sender_name,
                sender_email=sender_email,
                subject=subject,
                body=body,
                list_id=list_id,
                tags=tags,
                **kwargs
            )
            
            # Подробное логирование результата
            logger.debug(f"Результат send_email для {email}: тип={type(result).__name__}")
            
            # Проверяем, что результат - это словарь
            if not isinstance(result, dict):
                logger.error(f"⚠️ send_email вернул не словарь для {email}: тип={type(result).__name__}, значение={result}")
                return None
            
            # Безопасная обработка результата
            if 'error' in result:
                error_value = result.get('error')
                logger.warning(f"⚠️ Ошибка в результате отправки email на {email}: {error_value}")
                return result
            
            if 'result' in result:
                result_data = result.get('result')
                logger.info(f"✅ Email успешно отправлен на {email}. Результат: {result_data}")
                return result
            
            logger.warning(f"⚠️ Неожиданный формат результата для {email}: {result}")
            return result
            
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logger.error(f"❌ Ошибка в send_email_safe для {email}: {error_type} - {error_msg}")
            logger.error(f"   Детали исключения:", exc_info=True)
            return None
