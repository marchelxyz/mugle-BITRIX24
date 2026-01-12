"""
Модуль для обработки голосовых сообщений и распознавания речи через OpenAI Whisper + Google Gemini
"""
import os
import tempfile
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from telegram import Voice
from openai import OpenAI
from pydub import AudioSegment
import re
import json
import asyncio
from dateutil import parser
import google.generativeai as genai

logger = logging.getLogger(__name__)

class VoiceTaskProcessor:
    """Класс для обработки голосовых сообщений и извлечения данных задачи"""
    
    # Приоритеты моделей Gemini с автоматическим fallback
    MODEL_PRIORITIES = [
        'gemini-2.5-flash',  # Приоритет 1 - самая новая и быстрая
        'gemini-1.5-flash',  # Приоритет 2 - быстрая и широко доступная
        'gemini-1.5-pro',    # Приоритет 3 - более мощная модель
        'gemini-pro'         # Приоритет 4 - legacy версия для совместимости
    ]
    
    def __init__(self, openai_api_key: str, gemini_api_key: str, bitrix_client=None):
        """Инициализация процессора голосовых сообщений"""
        # OpenAI для распознавания речи (Whisper)
        self.openai_client = OpenAI(api_key=openai_api_key)
        
        # Google Gemini для обработки текста
        genai.configure(api_key=gemini_api_key)
        self.gemini_model = None
        self.gemini_model_name = None
        
        # Bitrix24 клиент для получения списка пользователей
        self.bitrix_client = bitrix_client
        
        self._initialize_gemini_model()
        
        logger.info("🎤 VoiceTaskProcessor инициализирован с OpenAI Whisper + Google Gemini")
    
    def _initialize_gemini_model(self):
        """Инициализация модели Gemini с автоматическим fallback"""
        for model_name in self.MODEL_PRIORITIES:
            try:
                logger.info(f"Попытка инициализации модели: {model_name}")
                model = genai.GenerativeModel(
                    model_name,
                    generation_config={
                        "response_mime_type": "application/json",
                        "temperature": 0.3
                    }
                )
                self.gemini_model = model
                self.gemini_model_name = model_name
                logger.info(f"Успешно инициализирована модель: {model_name}")
                return
            except Exception as e:
                logger.warning(f"Модель {model_name} недоступна при инициализации: {e}")
                continue
        
        # Если ни одна модель не доступна при инициализации, повторная попытка будет при первом запросе
        logger.warning("Ни одна модель Gemini не доступна при инициализации. Будет повторная попытка при первом запросе.")
        self.gemini_model = None
        self.gemini_model_name = None
    
    def _ensure_gemini_model_initialized(self):
        """Обеспечивает инициализацию модели, если она еще не инициализирована"""
        if self.gemini_model is not None:
            return
        
        logger.info("Повторная попытка инициализации модели Gemini при первом запросе")
        for model_name in self.MODEL_PRIORITIES:
            try:
                logger.info(f"Попытка инициализации модели: {model_name}")
                model = genai.GenerativeModel(
                    model_name,
                    generation_config={
                        "response_mime_type": "application/json",
                        "temperature": 0.3
                    }
                )
                self.gemini_model = model
                self.gemini_model_name = model_name
                logger.info(f"Успешно инициализирована модель: {model_name}")
                return
            except Exception as e:
                logger.warning(f"Модель {model_name} недоступна: {e}")
                continue
        
        raise RuntimeError("Не удалось инициализировать ни одну из доступных моделей Gemini")
    
    def _try_gemini_models_with_fallback(self, prompt: str) -> str:
        """
        Выполняет запрос к модели с автоматическим fallback на следующую модель при ошибке
        
        Args:
            prompt: Промпт для отправки в модель
            
        Returns:
            Текст ответа от модели
        """
        # Список моделей для попытки (начинаем с текущей, затем пробуем остальные)
        models_to_try = []
        if self.gemini_model_name:
            # Начинаем с текущей модели
            current_index = self.MODEL_PRIORITIES.index(self.gemini_model_name) if self.gemini_model_name in self.MODEL_PRIORITIES else 0
            models_to_try = self.MODEL_PRIORITIES[current_index:] + self.MODEL_PRIORITIES[:current_index]
        else:
            # Если модель не инициализирована, пробуем все по порядку
            models_to_try = self.MODEL_PRIORITIES
        
        last_error = None
        for model_name in models_to_try:
            try:
                # Если это не текущая модель, создаем новую
                if model_name != self.gemini_model_name:
                    logger.info(f"Попытка использовать модель: {model_name}")
                    model = genai.GenerativeModel(
                        model_name,
                        generation_config={
                            "response_mime_type": "application/json",
                            "temperature": 0.3
                        }
                    )
                else:
                    model = self.gemini_model
                
                # Выполняем запрос
                response = model.generate_content(prompt)
                result_text = response.text.strip()
                
                # Если успешно и использовали другую модель, обновляем текущую
                if model_name != self.gemini_model_name:
                    self.gemini_model = model
                    self.gemini_model_name = model_name
                    logger.info(f"Успешно переключились на модель: {model_name}")
                
                return result_text
                
            except Exception as e:
                logger.warning(f"Ошибка при использовании модели {model_name}: {e}")
                last_error = e
                continue
        
        # Если все модели не сработали, выбрасываем последнюю ошибку
        raise RuntimeError(f"Не удалось выполнить запрос ни к одной из моделей Gemini. Последняя ошибка: {last_error}")
    
    async def process_voice_message(self, voice: Voice, bot, telegram_user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Обрабатывает голосовое сообщение и извлекает данные задачи
        
        Args:
            voice: Объект голосового сообщения Telegram
            bot: Экземпляр бота для скачивания файла
            
        Returns:
            Словарь с извлеченными данными задачи или None в случае ошибки
        """
        try:
            # Скачиваем голосовое сообщение
            voice_file = await bot.get_file(voice.file_id)
            
            # Создаем временный файл
            with tempfile.NamedTemporaryFile(suffix='.oga', delete=False) as temp_file:
                await voice_file.download_to_drive(temp_file.name)
                temp_oga_path = temp_file.name
            
            # Проверяем размер файла
            file_size = os.path.getsize(temp_oga_path)
            max_size = 1024 * 1024  # 1 МБ
            
            if file_size > max_size:
                logger.warning(f"Файл большой ({file_size / (1024 * 1024):.2f} МБ), может потребоваться разделение")
            
            # Конвертируем в MP3 для Whisper
            temp_mp3_path = temp_oga_path.replace('.oga', '.mp3')
            try:
                audio = AudioSegment.from_file(temp_oga_path, format='ogg')
                audio.export(temp_mp3_path, format='mp3')
                logger.info(f"🔄 Аудио конвертировано в MP3: {temp_mp3_path}")
            except Exception as e:
                logger.error(f"❌ Ошибка конвертации аудио: {e}")
                self._cleanup_files([temp_oga_path, temp_mp3_path])
                return None
            
            # Распознаем речь через Whisper
            try:
                with open(temp_mp3_path, 'rb') as audio_file:
                    transcript = self.openai_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language='ru'  # Указываем русский язык
                    )
                
                recognized_text = transcript.text.strip()
                logger.info(f"🎯 Распознанный текст: {recognized_text}")
                
            except Exception as e:
                logger.error(f"❌ Ошибка распознавания речи: {e}")
                self._cleanup_files([temp_oga_path, temp_mp3_path])
                return None
            
            # Очищаем временные файлы
            self._cleanup_files([temp_oga_path, temp_mp3_path])
            
            # Получаем информацию о создателе задачи
            creator_info = None
            if telegram_user_id and self.bitrix_client:
                try:
                    logger.info(f"🔍 Поиск пользователя по Telegram ID: {telegram_user_id} (тип: {type(telegram_user_id)})")
                    
                    # Используем ту же логику, что и в мини-приложении
                    from bot import get_bitrix_user_id_by_telegram_id
                    creator_bitrix_id = get_bitrix_user_id_by_telegram_id(telegram_user_id)
                    
                    if creator_bitrix_id:
                        creator_info = self.bitrix_client.get_user_by_id(creator_bitrix_id)
                        logger.info(f"👤 Найден создатель задачи: {creator_info.get('NAME', '')} {creator_info.get('LAST_NAME', '')} (Bitrix ID: {creator_bitrix_id})")
                    else:
                        logger.warning(f"⚠️ Пользователь с Telegram ID {telegram_user_id} не найден в Bitrix24")
                except Exception as e:
                    logger.warning(f"Не удалось получить информацию о создателе: {e}")
            else:
                logger.warning(f"⚠️ Нет telegram_user_id или bitrix_client. telegram_user_id={telegram_user_id}, bitrix_client={bool(self.bitrix_client)}")
            
            # Парсим распознанный текст для извлечения данных задачи
            task_data = await self._parse_task_text_with_gemini(recognized_text, creator_info)
            
            if task_data:
                task_data['original_text'] = recognized_text
                logger.info(f"✅ Данные задачи извлечены: {task_data}")
                return task_data
            else:
                logger.warning("⚠️ Не удалось извлечь данные задачи из текста")
                return None
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки голосового сообщения: {e}", exc_info=True)
            return None
    
    def _cleanup_files(self, file_paths: list):
        """Очищает временные файлы"""
        for file_path in file_paths:
            try:
                if os.path.exists(file_path):
                    os.unlink(file_path)
                    logger.debug(f"🗑️ Удален временный файл: {file_path}")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка удаления файла {file_path}: {e}")
    
    async def _parse_multiple_tasks_with_gemini(self, text: str, creator_info: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Парсит распознанный текст с использованием Google Gemini для извлечения нескольких задач
        
        Args:
            text: Распознанный текст из голосового сообщения
            
        Returns:
            Список словарей с данными задач
        """
        try:
            # Получаем список пользователей для точного определения ответственных
            users_list = ""
            if self.bitrix_client:
                try:
                    users = self.bitrix_client.get_all_users(active_only=True)
                    if users:
                        users_list = "\n\nСПИСОК СОТРУДНИКОВ БИТРИКС24:\n"
                        for user in users[:50]:  # Ограничиваем первыми 50 для размера промпта
                            name = user.get('NAME', '') + ' ' + user.get('LAST_NAME', '')
                            name = name.strip()
                            if name:
                                users_list += f"- {name} (ID: {user['ID']})\n"
                        users_list += "\nВАЖНО: Используй ТОЛЬКО имена из этого списка для поля responsibles."
                except Exception as e:
                    logger.warning(f"Не удалось получить список пользователей: {e}")
            
            # Добавляем информацию о создателе задачи
            creator_info_text = ""
            if creator_info:
                creator_name = creator_info.get('NAME', '') + ' ' + creator_info.get('LAST_NAME', '')
                creator_name = creator_name.strip()
                if creator_name:
                    creator_info_text = f"\n\nПОЛЬЗОВАТЕЛЬ ОТПРАВИВШИЙ ГОЛОСОВОЕ СООБЩЕНИЕ: {creator_name} (ID: {creator_info.get('ID')})"
                    creator_info_text += "\nУЧИТЫВАЙ: Этот пользователь является СОЗДАТЕЛЕМ задачи в Bitrix24. Если в тексте не указаны ответственные, назначь задачу на этого пользователя."
            
            # Создаем промпт для Gemini
            current_datetime = datetime.now()
            current_date_str = current_datetime.strftime("%Y-%m-%d %H:%M:%S")
            current_date_only = current_datetime.strftime("%Y-%m-%d")
            
            prompt = f"""Ты — помощник для управления задачами в Битрикс24. Твоя задача — извлечь из текста пользователя детали ОДНОЙ ИЛИ НЕСКОЛЬКИХ задач и вернуть их в формате JSON массива.

Текущая дата и время: {current_date_str}
Сегодня: {current_date_only}{users_list}{creator_info_text}

Текст пользователя: "{text}"

Верни строго JSON массив со следующей структурой:
[
    {{
        "title": "Название задачи 1",
        "description": "Описание задачи 1 (опционально)",
        "responsibles": ["Имя1", "Имя2"],
        "deadline": "YYYY-MM-DD HH:MM",
        "priority": "low|medium|high",
        "confidence": 0.8
    }},
    {{
        "title": "Название задачи 2",
        "description": "Описание задачи 2 (опционально)",
        "responsibles": ["Имя1"],
        "deadline": "YYYY-MM-DD HH:MM",
        "priority": "medium",
        "confidence": 0.9
    }}
]

Правила:
1. Если пользователь говорит "сегодня", используй текущую дату ({current_date_only}) с временем 18:00 (МСК)
2. Если пользователь говорит "завтра", "послезавтра", "через 3 дня" — вычисли правильную дату относительно текущей даты с временем 18:00 (МСК)
3. Если указана относительная дата (например, "в пятницу"), вычисли дату относительно текущей недели с временем 18:00 (МСК)
4. Если указано конкретное время (например, "до 13.00", "к 15:30"), используй указанное время
5. Если время не указано, всегда устанавливай 18:00 (МСК)
6. Если дедлайн не указан, оставь поле null
7. Если ответственные не указаны, оставь массив пустым
8. Если приоритет не указан, используй "medium"
9. Уровень уверенности (confidence) от 0.0 до 1.0 в зависимости от четкости запроса
10. Всегда возвращай валидный JSON массив, без дополнительного текста или комментариев
11. Для поля responsibles используй ТОЛЬКО имена из предоставленного списка сотрудников
12. Извлекай описание из полного контекста голосового сообщения
13. Разделяй задачи по смыслу. Каждая отдельная задача должна быть отдельным элементом массива
14. Если в тексте только одна задача, верни массив с одним элементом

Примеры:
- "Создать задачу подготовить отчет по продажам до пятницы" -> [{{"title": "Подготовить отчет по продажам", "description": "Создать задачу подготовить отчет по продажам", "responsibles": [], "deadline": "2025-01-17 18:00", "priority": "medium", "confidence": 0.7}}]
- "Поручить Ивану провести анализ конкурентов, а Марии сделать презентацию до 15 марта" -> [{{"title": "Провести анализ конкурентов", "description": "Поручить Ивану провести анализ конкурентов", "responsibles": ["Иван"], "deadline": "2025-03-15 18:00", "priority": "medium", "confidence": 0.8}}, {{"title": "Сделать презентацию", "description": "Марии сделать презентацию", "responsibles": ["Мария"], "deadline": "2025-03-15 18:00", "priority": "medium", "confidence": 0.8}}]
- "Сегодня срочно исправить ошибку на сайте и обновить документацию" -> [{{"title": "Исправить ошибку на сайте", "description": "Сегодня срочно исправить ошибку на сайте", "responsibles": [], "deadline": "{current_date_only} 18:00", "priority": "high", "confidence": 0.9}}, {{"title": "Обновить документацию", "description": "Обновить документацию", "responsibles": [], "deadline": "{current_date_only} 18:00", "priority": "medium", "confidence": 0.8}}]

Верни только JSON массив:"""

            # Убеждаемся, что модель инициализирована
            self._ensure_gemini_model_initialized()
            
            # Отправляем запрос к Gemini с автоматическим fallback (синхронный API, оборачиваем в executor)
            loop = asyncio.get_event_loop()
            result_text = await loop.run_in_executor(
                None,
                lambda: self._try_gemini_models_with_fallback(prompt)
            )
            
            # Убираем возможные markdown блоки кода
            if result_text.startswith('```json'):
                result_text = result_text[7:]
            if result_text.endswith('```'):
                result_text = result_text[:-3]
            result_text = result_text.strip()
            
            # Парсим JSON
            try:
                tasks_data = json.loads(result_text)
                
                # Проверяем, что это массив
                if not isinstance(tasks_data, list):
                    logger.warning(f"Результат не является массивом, преобразуем в массив с одним элементом")
                    tasks_data = [tasks_data]
                
                # Нормализуем и валидируем каждую задачу
                processed_tasks = []
                for task in tasks_data:
                    processed_task = self._validate_and_format_task_data(task)
                    if processed_task:
                        processed_tasks.append(processed_task)
                
                logger.info(f"✅ Извлечено {len(processed_tasks)} задач из голосового сообщения")
                return processed_tasks
                
            except json.JSONDecodeError as e:
                logger.error(f"❌ Ошибка парсинга JSON из Gemini: {e}")
                logger.error(f"🔍 Текст ответа: {result_text}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Ошибка при извлечении задач: {e}")
            return []


    def _validate_and_format_task_data(self, task_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Валидирует и форматирует данные задачи
        
        Args:
            task_data: Сырые данные задачи из Gemini
            
        Returns:
            Отформатированные данные задачи или None
        """
        try:
            # Базовая валидация
            if not isinstance(task_data, dict):
                return None
            
            # Валидация и форматирование полей
            processed_task = {
                'title': task_data.get('title', 'Задача из голосового сообщения'),
                'description': task_data.get('description'),
                'responsibles': task_data.get('responsibles', []),
                'deadline': task_data.get('deadline'),
                'priority': task_data.get('priority', 'medium'),
                'confidence': task_data.get('confidence', 0.5)
            }
            
            # Форматируем дедлайн
            if processed_task['deadline']:
                processed_task['deadline'] = self._validate_and_format_date(processed_task['deadline'])
            
            # Форматируем описание в деловой стиль
            if processed_task.get('description'):
                processed_task['description'] = self._format_description_business_style(
                    processed_task['description'], 
                    processed_task['title']
                )
            
            # Убедимся, что responsibles это список
            if isinstance(processed_task['responsibles'], str):
                processed_task['responsibles'] = [processed_task['responsibles']]
            
            # Валидация приоритета
            if processed_task['priority'] not in ['low', 'medium', 'high']:
                processed_task['priority'] = 'medium'
            
            # Валидация уверенности
            try:
                confidence = float(processed_task['confidence'])
                processed_task['confidence'] = max(0.0, min(1.0, confidence))
            except (ValueError, TypeError):
                processed_task['confidence'] = 0.5
            
            return processed_task
            
        except Exception as e:
            logger.error(f"❌ Ошибка валидации данных задачи: {e}")
            return None


    async def _parse_task_text_with_gemini(self, text: str, creator_info: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """
        Парсит распознанный текст с использованием Google Gemini для извлечения данных задачи
        
        Args:
            text: Распознанный текст из голосового сообщения
            creator_info: Информация о создателе задачи
            
        Returns:
            Словарь с данными задачи или None
        """
        try:
            # Получаем список пользователей для точного определения ответственных
            users_list = ""
            if self.bitrix_client:
                try:
                    users = self.bitrix_client.get_all_users(active_only=True)
                    if users:
                        users_list = "\n\nСПИСОК СОТРУДНИКОВ БИТРИКС24:\n"
                        for user in users[:50]:  # Ограничиваем первыми 50 для размера промпта
                            name = user.get('NAME', '') + ' ' + user.get('LAST_NAME', '')
                            name = name.strip()
                            if name:
                                users_list += f"- {name} (ID: {user['ID']})\n"
                        users_list += "\nВАЖНО: Используй ТОЛЬКО имена из этого списка для поля responsibles."
                except Exception as e:
                    logger.warning(f"Не удалось получить список пользователей: {e}")
            
            # Добавляем информацию о создателе задачи
            creator_info_text = ""
            if creator_info:
                creator_name = creator_info.get('NAME', '') + ' ' + creator_info.get('LAST_NAME', '')
                creator_name = creator_name.strip()
                if creator_name:
                    creator_info_text = f"\n\nПОЛЬЗОВАТЕЛЬ ОТПРАВИВШИЙ ГОЛОСОВОЕ СООБЩЕНИЕ: {creator_name} (ID: {creator_info.get('ID')})"
                    creator_info_text += "\nУЧИТЫВАЙ: Этот пользователь является СОЗДАТЕЛЕМ задачи в Bitrix24. Если в тексте не указаны ответственные, назначь задачу на этого пользователя."
            
            # Создаем промпт для Gemini
            current_datetime = datetime.now()
            current_date_str = current_datetime.strftime("%Y-%m-%d %H:%M:%S")
            current_date_only = current_datetime.strftime("%Y-%m-%d")
            
            prompt = f"""Ты — помощник для управления задачами в Битрикс24. Твоя задача — извлечь из текста пользователя детали задачи и вернуть их в формате JSON.

Текущая дата и время: {current_date_str}
Сегодня: {current_date_only}{users_list}{creator_info_text}

Текст пользователя: "{text}"

Верни строго JSON со следующей структурой:
{{
    "title": "Название задачи",
    "description": "Описание задачи (опционально)",
    "responsibles": ["Имя1", "Имя2"],
    "deadline": "YYYY-MM-DD HH:MM",
    "priority": "low|medium|high",
    "confidence": 0.8
}}

Правила:
1. Если пользователь говорит "сегодня", используй текущую дату ({current_date_only}) с временем 18:00 (МСК)
2. Если пользователь говорит "завтра", "послезавтра", "через 3 дня" — вычисли правильную дату относительно текущей даты с временем 18:00 (МСК)
3. Если указана относительная дата (например, "в пятницу"), вычисли дату относительно текущей недели с временем 18:00 (МСК)
4. Если указано конкретное время (например, "до 13.00", "к 15:30"), используй указанное время
5. Если время не указано, всегда устанавливай 18:00 (МСК)
6. Если дедлайн не указан, оставь поле null
7. Если ответственные не указаны, оставь массив пустым
8. Если приоритет не указан, используй "medium"
9. Уровень уверенности (confidence) от 0.0 до 1.0 в зависимости от четкости запроса
10. Всегда возвращай валидный JSON, без дополнительного текста или комментариев
11. Для поля responsibles используй ТОЛЬКО имена из предоставленного списка сотрудников
12. Извлекай описание из полного контекста голосового сообщения, а не только из заголовка

Примеры:
- "Создать задачу подготовить отчет по продажам до пятницы" -> {{"title": "Подготовить отчет по продажам", "description": "Создать задачу подготовить отчет по продажам", "responsibles": [], "deadline": "2025-01-17 18:00", "priority": "medium", "confidence": 0.7}}
- "Поручить Ивану и Марии провести анализ конкурентов до 15 марта" -> {{"title": "Провести анализ конкурентов", "description": "Поручить Ивану и Марии провести анализ конкурентов", "responsibles": ["Иван", "Мария"], "deadline": "2025-03-15 18:00", "priority": "medium", "confidence": 0.8}}
- "Сегодня срочно исправить ошибку на сайте ответственный Петр" -> {{"title": "Исправить ошибку на сайте", "description": "Сегодня срочно исправить ошибку на сайте ответственный Петр", "responsibles": ["Петр"], "deadline": "{current_date_only} 18:00", "priority": "high", "confidence": 0.9}}
- "Сделать отчет до 13:00" -> {{"title": "Сделать отчет", "description": "Сделать отчет", "responsibles": [], "deadline": "2025-01-13 13:00", "priority": "medium", "confidence": 0.8}}

Верни только JSON:"""

            # Убеждаемся, что модель инициализирована
            self._ensure_gemini_model_initialized()
            
            # Отправляем запрос к Gemini с автоматическим fallback (синхронный API, оборачиваем в executor)
            loop = asyncio.get_event_loop()
            result_text = await loop.run_in_executor(
                None,
                lambda: self._try_gemini_models_with_fallback(prompt)
            )
            
            # Убираем возможные markdown блоки кода
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.startswith("```"):
                result_text = result_text[3:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            result_text = result_text.strip()
            
            # Парсим JSON
            result = json.loads(result_text)
            
            # Валидация и обработка результата
            processed_result = {
                'title': result.get('title', 'Задача из голосового сообщения'),
                'description': result.get('description'),
                'responsibles': result.get('responsibles', []),
                'deadline': result.get('deadline'),
                'priority': result.get('priority', 'medium'),
                'confidence': result.get('confidence', 0.5)
            }
            
            # Дополнительная обработка
            if processed_result['deadline']:
                processed_result['deadline'] = self._validate_and_format_date(processed_result['deadline'])
            
            # Форматируем описание в деловой стиль
            if processed_result.get('description'):
                processed_result['description'] = self._format_description_business_style(
                    processed_result['description'], 
                    processed_result['title']
                )
            
            # Убедимся, что responsibles это список
            if isinstance(processed_result['responsibles'], str):
                processed_result['responsibles'] = [processed_result['responsibles']]
            
            return processed_result
            
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON от Gemini: {e}")
            logger.error(f"Ответ Gemini: {result_text if 'result_text' in locals() else 'N/A'}")
            # Fallback к простому парсингу
            return self._parse_task_text_fallback(text)
        except Exception as e:
            logger.error(f"Ошибка обработки текста через Gemini: {e}")
            # Fallback к простому парсингу
            return self._parse_task_text_fallback(text, creator_info)
    
    def _parse_task_text_fallback(self, text: str, creator_info: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """
        Fallback метод для парсинга текста без использования AI
        
        Args:
            text: Распознанный текст из голосового сообщения
            creator_info: Информация о создателе задачи
            
        Returns:
            Словарь с данными задачи или None
        """
        try:
            # Приводим текст к нижнему регистру для удобства парсинга
            text_lower = text.lower()
            
            # Извлекаем ответственных
            responsibles = self._extract_responsibles(text_lower)
            
            # Если ответственные не найдены и есть создатель, назначаем на него
            if not responsibles and creator_info:
                creator_name = creator_info.get('NAME', '') + ' ' + creator_info.get('LAST_NAME', '')
                creator_name = creator_name.strip()
                if creator_name:
                    responsibles = [creator_name]
            
            # Извлекаем дедлайн
            deadline = self._extract_deadline(text_lower)
            
            # Извлекаем тему/заголовок задачи
            title = self._extract_title(text)
            
            # Извлекаем описание
            description = self._extract_description(text)
            
            # Форматируем описание в деловой стиль
            if description:
                description = self._format_description_business_style(description, title)
            
            # Собираем результат
            result = {
                'title': title,
                'description': description,
                'responsibles': responsibles,
                'deadline': deadline,
                'priority': 'medium',
                'confidence': self._calculate_confidence(responsibles, deadline, title)
            }
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга текста (fallback): {e}", exc_info=True)
            return None
    
    def _validate_and_format_date(self, date_str: str) -> Optional[str]:
        """Валидирует и форматирует дату с временем"""
        try:
            if not date_str:
                return None
            
            # Парсим дату (включая время)
            dt = parser.parse(date_str)
            
            # Форматируем в YYYY-MM-DD HH:MM
            return dt.strftime('%Y-%m-%d %H:%M')
            
        except Exception as e:
            logger.warning(f"Ошибка валидации даты {date_str}: {e}")
            return None
    
    def _extract_responsibles(self, text: str) -> list:
        """Извлекает ответственных из текста"""
        responsibles = []
        
        # Паттерны для поиска ответственных
        patterns = [
            r'(?:ответственный|ответственных|исполнитель|исполнители)?\s*[:\-]?\s*([а-яё\s]+)',
            r'(?:поручить|назначить)\s+([а-яё\s]+)',
            r'(?:для|кому)\s+([а-яё\s]+)',
            r'([а-яё]+(?:\s+[а-яё]+)?)\s+(?:должен|должна|нужно|сделать)',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                names = [name.strip() for name in match.split(',') if name.strip()]
                # Фильтруем слишком короткие "имена"
                names = [name for name in names if len(name) > 2]
                responsibles.extend(names)
        
        return list(set(responsibles))  # Убираем дубликаты
    
    def _extract_deadline(self, text: str) -> Optional[str]:
        """Извлекает дедлайн из текста"""
        # Паттерны для дат
        date_patterns = [
            r'до\s+(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)',
            r'до\s+(\d{1,2})\s+(янв|фев|мар|апр|мая|июн|июл|авг|сен|окт|ноя|дек)',
            r'(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)',
            r'сегодня|завтра|послезавтра',
            r'на\s+следующей\s+неделе',
            r'через\s+(\d+)\s+(день|дня|дней)'
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, text)
            if match:
                return self._parse_date_match(match)
        
        return None
    
    def _parse_date_match(self, match) -> Optional[str]:
        """Преобразует найденную дату в формат YYYY-MM-DD"""
        try:
            current_year = datetime.now().year
            month_mapping = {
                'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
                'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
                'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4, 'мая': 5, 'июн': 6, 'июл': 7,
                'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12
            }
            
            groups = match.groups()
            
            if 'сегодня' in match.group(0):
                return datetime.now().strftime('%Y-%m-%d')
            elif 'завтра' in match.group(0):
                return (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            elif 'послезавтра' in match.group(0):
                return (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
            elif 'следующей неделе' in match.group(0):
                return (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
            elif len(groups) >= 2 and groups[0].isdigit():
                day = int(groups[0])
                month_name = groups[1]
                if month_name in month_mapping:
                    month = month_mapping[month_name]
                    # Проверяем, не прошла ли дата в этом году
                    date_this_year = datetime(current_year, month, day)
                    if date_this_year > datetime.now():
                        return date_this_year.strftime('%Y-%m-%d')
                    else:
                        # Если дата прошла, берем следующий год
                        return datetime(current_year + 1, month, day).strftime('%Y-%m-%d')
            elif 'через' in match.group(0) and len(groups) >= 2:
                days = int(groups[0])
                return (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
                
        except Exception as e:
            logger.warning(f"⚠️ Ошибка парсинга даты: {e}")
        
        return None
    
    def _extract_title(self, text: str) -> str:
        """Извлекает заголовок задачи из текста"""
        # Ищем первые слова до первого знака препинания или ключевого слова
        title_patterns = [
            r'^([^,.!?]*(?:задача|задачу|сделать|выполнить)[^,.!?]*)',
            r'^([^,.!?]{10,50})',
        ]
        
        for pattern in title_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                if len(title) > 5:  # Минимальная длина заголовка
                    return title[:100]  # Ограничиваем длину
        
        # Если не нашли паттерн, берем первые 50 символов
        return text[:50].strip() + ('...' if len(text) > 50 else '')
    
    def _extract_description(self, text: str) -> str:
        """Извлекает описание задачи из текста"""
        # Убираем уже извлеченные элементы (ответственных, дедлайн)
        description = text
        
        # Убираем упоминания ответственных
        description = re.sub(r'(?:ответственный|ответственных|исполнитель|исполнители)?\s*[:\-]?\s*[а-яё\s,]+', '', description, flags=re.IGNORECASE)
        
        # Убираем упоминания дедлайнов
        description = re.sub(r'до\s+\d{1,2}\s+[а-яё]+', '', description, flags=re.IGNORECASE)
        description = re.sub(r'сегодня|завтра|послезавтра', '', description, flags=re.IGNORECASE)
        
        # Очищаем от лишних пробелов и знаков препинания
        description = re.sub(r'\s+', ' ', description).strip()
        description = description.strip('.,!?')
        
        return description[:500]  # Ограничиваем длину описания
    
    def _format_description_business_style(self, description: str, title: str) -> str:
        """
        Форматирует описание задачи в деловой стиль
        
        Args:
            description: Исходное описание из транскрибации
            title: Заголовок задачи для контекста
            
        Returns:
            Отформатированное описание в деловом стиле
        """
        if not description or len(description.strip()) < 5:
            return ""
        
        try:
            # Используем Gemini для форматирования описания
            import google.generativeai as genai
            
            genai.configure(api_key=self.gemini_api_key)
            model = genai.GenerativeModel('gemini-2.0-flash-exp')
            
            prompt = f"""
Преобразуй текст в деловое описание задачи.

ЗАДАЧА: {title}

ИСХОДНЫЙ ТЕКСТ (из голосового сообщения):
"{description}"

ИНСТРУКЦИИ:
1. Перефразируй текст в формальном деловом стиле
2. Убери разговорные выражения, слова-паразиты, эмоции
3. Добавь структуру и ясность
4. Используй профессиональную лексику
5. Сохраняй основной смысл и детали
6. Если нужно, добавь конкретики для ясности
7. Результат должен быть кратким и по делу (до 300 символов)

ПРИМЕРЫ:
ИСХОДНЫЙ: "надо быстренько сделать отчет по продажам, типа за квартал"
РЕЗУЛЬТАТ: "Подготовить квартальный отчет по продажам с анализом показателей"

ИСХОДНЫЙ: "созвониться с клиентами и узнать все по проекту"
РЕЗУЛЬТАТ: "Провести переговоры с клиентами для уточнения деталей проекта"

ИСХОДНЫЙ: "починить что-то на сайте, там все сломалось"
РЕЗУЛЬТАТ: "Выявить и устранить технические неисправности на сайте"

ОТВЕТ (только отформатированное описание):
"""
            
            response = model.generate_content(prompt)
            formatted_description = response.text.strip()
            
            # Дополнительная очистка
            formatted_description = re.sub(r'["\']', '', formatted_description)
            formatted_description = re.sub(r'\s+', ' ', formatted_description).strip()
            
            # Ограничиваем длину
            if len(formatted_description) > 300:
                formatted_description = formatted_description[:297] + "..."
            
            logger.info(f"📝 Описание отформатировано: '{description[:50]}...' -> '{formatted_description[:50]}...'")
            return formatted_description
            
        except Exception as e:
            logger.warning(f"⚠️ Не удалось отформатировать описание: {e}")
            # В случае ошибки возвращаем очищенное исходное описание
            return self._clean_description_basic(description)
    
    def _clean_description_basic(self, description: str) -> str:
        """
        Базовая очистка описания без использования AI
        
        Args:
            description: Исходное описание
            
        Returns:
            Очищенное описание
        """
        # Убираем разговорные выражения
        casual_words = [
            'короче', 'в общем', 'типа', 'как бы', 'вот', 'это самое',
            'ну', 'блин', 'честно говоря', 'по сути', 'на самом деле',
            'так сказать', 'знаешь', 'понимаешь', 'вроде', 'примерно'
        ]
        
        cleaned = description
        for word in casual_words:
            cleaned = re.sub(rf'\b{re.escape(word)}\b', '', cleaned, flags=re.IGNORECASE)
        
        # Убираем лишние пробелы и знаки
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        cleaned = cleaned.strip('.,!?')
        
        # Делаем первую букву заглавной
        if cleaned and len(cleaned) > 0:
            cleaned = cleaned[0].upper() + cleaned[1:]
        
        return cleaned[:300]
    
    def _calculate_confidence(self, responsibles: list, deadline: Optional[str], title: str) -> float:
        """
        Рассчитывает уверенность в правильно распознанной задаче
        
        Returns:
            Уровень уверенности от 0.0 до 1.0
        """
        confidence = 0.0
        
        # Наличие заголовка
        if title and len(title) > 10:
            confidence += 0.3
        
        # Наличие ответственных
        if responsibles:
            confidence += 0.3
        
        # Наличие дедлайна
        if deadline:
            confidence += 0.2
        
        # Дополнительные факторы
        if len(title) > 20:
            confidence += 0.1
        
        if responsibles and len(responsibles) > 1:
            confidence += 0.1
        
        return min(confidence, 1.0)
    
    def generate_clarification_questions(self, task_data: Dict[str, Any]) -> list:
        """
        Генерирует уточняющие вопросы на основе распознанных данных
        
        Args:
            task_data: Распознанные данные задачи
            
        Returns:
            Список уточняющих вопросов
        """
        questions = []
        
        if not task_data.get('responsibles'):
            questions.append("Кому должна быть назначена эта задача?")
        
        if not task_data.get('deadline'):
            questions.append("Какой дедлайн для этой задачи?")
        
        confidence = task_data.get('confidence', 0.0)
        if confidence < 0.5:
            questions.append("Пожалуйста, подтвердите правильность распознанных данных:")
            questions.append(f"Заголовок: {task_data.get('title', 'Не распознан')}")
            if task_data.get('responsibles'):
                questions.append(f"Ответственные: {', '.join(task_data['responsibles'])}")
            if task_data.get('deadline'):
                questions.append(f"Дедлайн: {task_data['deadline']}")
        
        return questions
        
    async def process_multiple_voice_tasks(self, voice_file: bytes, telegram_user_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Обрабатывает голосовое сообщение и извлекает несколько задач
        
        Args:
            voice_file: Данные голосового файла
            telegram_user_id: ID пользователя в Telegram
            
        Returns:
            Словарь с результатом обработки
        """
        try:
            # 1. Распознаем речь
            transcribed_text = await self.transcribe_audio(voice_file)
            if not transcribed_text:
                return {
                    'success': False,
                    'error': 'Не удалось распознать речь',
                    'transcribed_text': None
                }
            
            logger.info(f"🎯 Распознанный текст: {transcribed_text}")
            
            # 2. Получаем информацию о создателе
            creator_info = None
            if telegram_user_id and self.bitrix_client:
                try:
                    logger.info(f"🔍 Поиск пользователя по Telegram ID: {telegram_user_id} (тип: {type(telegram_user_id)})")
                    
                    # Используем ту же логику, что и в мини-приложении
                    from bot import get_bitrix_user_id_by_telegram_id
                    creator_bitrix_id = get_bitrix_user_id_by_telegram_id(telegram_user_id)
                    
                    if creator_bitrix_id:
                        creator_info = self.bitrix_client.get_user_by_id(creator_bitrix_id)
                        logger.info(f"👤 Найден создатель задачи: {creator_info.get('NAME', '')} {creator_info.get('LAST_NAME', '')} (Bitrix ID: {creator_bitrix_id})")
                    else:
                        logger.warning(f"⚠️ Пользователь с Telegram ID {telegram_user_id} не найден в Bitrix24")
                except Exception as e:
                    logger.warning(f"Не удалось получить информацию о создателе: {e}")
            
            # 3. Извлекаем несколько задач с помощью Gemini
            tasks_data = await self._parse_multiple_tasks_with_gemini(transcribed_text, creator_info)
            
            if not tasks_data:
                return {
                    'error': 'Не удалось извлечь задачи из текста',
                    'transcribed_text': transcribed_text
                }
            
            # 4. Добавляем дополнительную информацию
            for task in tasks_data:
                task['original_text'] = transcribed_text
                # НЕ назначаем ответственных автоматически - будем уточнять в UI
                # Это позволит пользователю выбрать ответственного вручную
            
            return {
                'success': True,
                'tasks': tasks_data,
                'transcribed_text': transcribed_text,
                'creator_info': creator_info,
                'tasks_count': len(tasks_data)
            }
            
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке голосового сообщения: {e}", exc_info=True)
            return {
                'success': False,
                'error': f'Ошибка обработки: {str(e)}',
                'transcribed_text': None
            }