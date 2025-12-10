# Структура данных исходящего вебхука Bitrix24

## Общая структура

Вебхук от Bitrix24 отправляет данные в формате `application/x-www-form-urlencoded` (URL-encoded) или `application/json`. Данные имеют следующую структуру:

```json
{
  "event": "ТИП_СОБЫТИЯ",
  "event_handler_id": "ID_ОБРАБОТЧИКА",
  "data": {
    "FIELDS_BEFORE": {...},
    "FIELDS_AFTER": {...},
    "IS_ACCESSIBLE_BEFORE": "...",
    "IS_ACCESSIBLE_AFTER": "..."
  },
  "ts": "TIMESTAMP",
  "auth": {
    "domain": "домен.bitrix24.ru",
    "client_endpoint": "https://домен.bitrix24.ru/rest/",
    "server_endpoint": "https://oauth.bitrix24.tech/rest/",
    "member_id": "идентификатор_участника",
    "application_token": "токен_приложения"
  }
}
```

## Поля верхнего уровня

### `event` (строка, обязательно)
Тип события от Bitrix24. Примеры:
- `ONTASKCOMMENTADD` - добавление комментария к задаче
- `ONTASKCOMMENTUPDATE` - обновление комментария к задаче
- `ONTASKCOMMENTDELETE` - удаление комментария к задаче
- `ONTASKADD` - добавление задачи
- `ONTASKUPDATE` - обновление задачи
- `ONTASKDELETE` - удаление задачи
- `ONUSERADD` - добавление пользователя
- `ONUSERUPDATE` - обновление пользователя

### `event_handler_id` (строка)
ID обработчика события в Bitrix24. Пример: `"1195"`

### `ts` (строка)
Unix timestamp события. Пример: `"1765368003"`

### `auth` (объект, обязательно)
Данные авторизации для доступа к API Bitrix24:

- **`domain`** (строка) - домен портала Bitrix24
  - Пример: `"muglerest.bitrix24.ru"`
  
- **`client_endpoint`** (строка) - URL для REST API запросов к порталу
  - Пример: `"https://muglerest.bitrix24.ru/rest/"`
  
- **`server_endpoint`** (строка) - URL для OAuth сервера Bitrix24
  - Пример: `"https://oauth.bitrix24.tech/rest/"`
  
- **`member_id`** (строка) - идентификатор участника портала
  - Пример: `"9ab09aecc1e530d38fb4ee92714778a7"`
  
- **`application_token`** (строка) - токен приложения для авторизации API запросов
  - Пример: `"wymqesv4swrosw46iixe94i4qbguoiy4"`
  - ⚠️ **Важно:** Этот токен используется для проверки подлинности вебхука (переменная `BITRIX24_OUTGOING_WEBHOOK_TOKEN`)

### `data` (объект, обязательно)
Основные данные события. Структура зависит от типа события.

## Структура `data` для событий комментариев (ONTASKCOMMENTADD)

Для события `ONTASKCOMMENTADD` (добавление комментария к задаче) структура `data` следующая:

```json
{
  "FIELDS_BEFORE": null,
  "FIELDS_AFTER": {
    "ID": "338729",
    "TASK_ID": "40927"
  },
  "IS_ACCESSIBLE_BEFORE": "N",
  "IS_ACCESSIBLE_AFTER": null
}
```

### Поля в `data`:

- **`FIELDS_BEFORE`** (объект или null)
  - Данные до изменения (для событий обновления/удаления)
  - Для события добавления комментария: `null`
  
- **`FIELDS_AFTER`** (объект)
  - Данные после изменения (для событий добавления/обновления)
  - Для события `ONTASKCOMMENTADD` содержит:
    - **`ID`** (строка) - ID комментария
      - Пример: `"338729"`
    - **`TASK_ID`** (строка) - ID задачи, к которой добавлен комментарий
      - Пример: `"40927"`
    - ⚠️ **Примечание:** В текущих данных вебхука не передаются другие поля комментария (например, `AUTHOR_ID`, текст комментария, дата создания). Для получения полной информации о комментарии необходимо использовать REST API Bitrix24 с токеном из `auth.application_token`.

- **`IS_ACCESSIBLE_BEFORE`** (строка или null)
  - Доступность данных до изменения
  - Пример: `"N"` (нет) или `null`
  
- **`IS_ACCESSIBLE_AFTER`** (строка или null)
  - Доступность данных после изменения
  - Пример: `null` или `"Y"` (да)

## Структура `data` для событий задач (ONTASKADD, ONTASKUPDATE, ONTASKDELETE)

Для событий задач структура `data` аналогична, но `FIELDS_AFTER` содержит данные задачи:

```json
{
  "FIELDS_BEFORE": {...},  // или null для ONTASKADD
  "FIELDS_AFTER": {
    "ID": "40927",
    "CREATED_BY": "123",
    "RESPONSIBLE_ID": "456",
    "TITLE": "Название задачи",
    // ... другие поля задачи
  },
  "IS_ACCESSIBLE_BEFORE": "...",
  "IS_ACCESSIBLE_AFTER": "..."
}
```

## Структура `data` для событий пользователей (ONUSERADD, ONUSERUPDATE)

Для событий пользователей структура может быть разной:

**Вариант 1:**
```json
{
  "FIELDS": {
    "ID": "123",
    "NAME": "Имя",
    "LAST_NAME": "Фамилия",
    "UF_USR_TELEGRAM": "123456789",
    // ... другие поля пользователя
  }
}
```

**Вариант 2:**
```json
{
  "ID": "123",
  "NAME": "Имя",
  "LAST_NAME": "Фамилия",
  "UF_USR_TELEGRAM": "123456789",
  // ... другие поля пользователя
}
```

## Формат передачи данных

### URL-encoded формат (текущий формат)

Bitrix24 отправляет данные в формате `application/x-www-form-urlencoded`:

```
event=ONTASKCOMMENTADD&event_handler_id=1195&data[FIELDS_BEFORE]=undefined&data[FIELDS_AFTER][ID]=338729&data[FIELDS_AFTER][TASK_ID]=40927&data[IS_ACCESSIBLE_BEFORE]=N&data[IS_ACCESSIBLE_AFTER]=undefined&ts=1765368003&auth[domain]=muglerest.bitrix24.ru&auth[client_endpoint]=https://muglerest.bitrix24.ru/rest/&auth[server_endpoint]=https://oauth.bitrix24.tech/rest/&auth[member_id]=9ab09aecc1e530d38fb4ee92714778a7&auth[application_token]=wymqesv4swrosw46iixe94i4qbguoiy4
```

Код автоматически парсит такие данные в структурированный словарь Python.

### JSON формат

Bitrix24 также может отправлять данные в формате `application/json`:

```json
{
  "event": "ONTASKCOMMENTADD",
  "event_handler_id": "1195",
  "data": {
    "FIELDS_BEFORE": null,
    "FIELDS_AFTER": {
      "ID": "338729",
      "TASK_ID": "40927"
    },
    "IS_ACCESSIBLE_BEFORE": "N",
    "IS_ACCESSIBLE_AFTER": null
  },
  "ts": "1765368003",
  "auth": {
    "domain": "muglerest.bitrix24.ru",
    "client_endpoint": "https://muglerest.bitrix24.ru/rest/",
    "server_endpoint": "https://oauth.bitrix24.tech/rest/",
    "member_id": "9ab09aecc1e530d38fb4ee92714778a7",
    "application_token": "wymqesv4swrosw46iixe94i4qbguoiy4"
  }
}
```

## Обработка значений "undefined"

Bitrix24 может отправлять строку `"undefined"` для полей, которые не определены. Код автоматически преобразует такие значения в `None` (null в JSON).

## Проверка токена

Токен для проверки подлинности вебхука находится в:
- `auth.application_token` - основной источник токена
- Этот токен должен совпадать с переменной окружения `BITRIX24_OUTGOING_WEBHOOK_TOKEN`

## Пример полного вебхука (из логов)

```json
{
  "event": "ONTASKCOMMENTADD",
  "event_handler_id": "1195",
  "data": {
    "FIELDS_BEFORE": null,
    "FIELDS_AFTER": {
      "ID": "338729",
      "TASK_ID": "40927"
    },
    "IS_ACCESSIBLE_BEFORE": "N",
    "IS_ACCESSIBLE_AFTER": null
  },
  "ts": "1765368003",
  "auth": {
    "domain": "muglerest.bitrix24.ru",
    "client_endpoint": "https://muglerest.bitrix24.ru/rest/",
    "server_endpoint": "https://oauth.bitrix24.tech/rest/",
    "member_id": "9ab09aecc1e530d38fb4ee92714778a7",
    "application_token": "wymqesv4swrosw46iixe94i4qbguoiy4"
  }
}
```

## Получение дополнительных данных

Для получения полной информации о комментарии, задаче или пользователе необходимо использовать REST API Bitrix24:

1. Используйте `auth.client_endpoint` как базовый URL
2. Используйте `auth.application_token` как токен для авторизации
3. Выполните соответствующий REST API запрос:
   - Для комментария: `tasks.task.comment.get` с параметрами `TASKID` и `COMMENTID`
   - Для задачи: `tasks.task.get` с параметром `TASKID`
   - Для пользователя: `user.get` с параметром `ID`

## Связанные документы

- [`BITRIX_OUTGOING_WEBHOOK.md`](BITRIX_OUTGOING_WEBHOOK.md) - настройка исходящего вебхука
- [`ОТВЕТ_ПО_ТОКЕНУ.md`](ОТВЕТ_ПО_ТОКЕНУ.md) - настройка токена вебхука
