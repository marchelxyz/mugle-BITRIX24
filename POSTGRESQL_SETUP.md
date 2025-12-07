# Настройка PostgreSQL на Render

## Быстрый старт

1. **Создайте PostgreSQL базу данных в Render:**
   - Зайдите в ваш Dashboard на Render
   - Нажмите "New +" → "PostgreSQL"
   - Выберите план (Free tier доступен)
   - Дождитесь создания базы данных

2. **Подключите базу данных к вашему сервису:**
   - В настройках вашего Web Service на Render
   - Перейдите в раздел "Environment"
   - Render автоматически добавит переменную `DATABASE_URL` при подключении базы данных
   - Или подключите вручную через "Add Environment Variable"

3. **Перезапустите сервис:**
   - После добавления переменной `DATABASE_URL` перезапустите сервис
   - Бот автоматически создаст необходимые таблицы при первом запуске

## Что хранится в PostgreSQL

База данных хранит следующие маппинги:

- **telegram_to_bitrix** - соответствия Telegram User ID → Bitrix24 User ID
- **username_to_bitrix** - соответствия Telegram username → Bitrix24 User ID  
- **thread_to_department** - соответствия Telegram thread_id → Bitrix24 Department ID

## Проверка работы

После запуска бота вы должны увидеть в логах:

```
✅ Пул соединений с PostgreSQL успешно инициализирован
✅ Таблицы базы данных успешно созданы/проверены
✅ PostgreSQL база данных успешно инициализирована
```

Если PostgreSQL недоступен, бот будет работать с локальным хранилищем (в памяти) как fallback.

## Миграция данных

Если у вас уже были данные в Bitrix24, бот автоматически синхронизирует их при первом запуске:

```
✅ Синхронизировано X связей из Bitrix24 в PostgreSQL
```

## Альтернативная настройка (без Render)

Если используете другой хостинг, можете указать переменные окружения отдельно:

```bash
POSTGRES_HOST=your-host
POSTGRES_PORT=5432
POSTGRES_DB=your-database
POSTGRES_USER=your-username
POSTGRES_PASSWORD=your-password
```

Или используйте полный `DATABASE_URL`:

```bash
DATABASE_URL=postgresql://user:password@host:port/database
```
