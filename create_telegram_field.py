#!/usr/bin/env python3
"""
Скрипт для создания пользовательского поля UF_TELEGRAM_ID в Bitrix24
Используется как альтернатива ручному созданию через интерфейс.

Требования:
- OAuth токен администратора Bitrix24 (не вебхук!)
- Или вебхук с правами администратора

Использование:
1. Получите OAuth токен администратора через:
   https://www.bitrix24.ru/apps/local/dev.php
   
2. Запустите скрипт:
   python create_telegram_field.py

Или установите переменные окружения:
export BITRIX24_DOMAIN=your-domain.bitrix24.ru
export BITRIX24_OAUTH_TOKEN=your_oauth_token_here
python create_telegram_field.py
"""

import os
import sys
import requests
import json

def create_telegram_field(domain: str, oauth_token: str) -> bool:
    """
    Создание поля UF_TELEGRAM_ID через API Bitrix24
    
    Args:
        domain: Домен Bitrix24 (например, your-domain.bitrix24.ru)
        oauth_token: OAuth токен администратора
        
    Returns:
        True если поле создано успешно, False в случае ошибки
    """
    base_url = f"https://{domain.rstrip('/')}/rest/user.userfield.add"
    
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
        },
        "auth": oauth_token
    }
    
    try:
        response = requests.post(base_url, json=field_data)
        response.raise_for_status()
        result = response.json()
        
        if result.get("result"):
            print("✅ Поле UF_TELEGRAM_ID успешно создано в Bitrix24!")
            return True
        else:
            error = result.get("error", "Неизвестная ошибка")
            error_description = result.get("error_description", "")
            print(f"❌ Не удалось создать поле: {error}")
            if error_description:
                print(f"   Описание: {error_description}")
            
            if error == "WRONG_AUTH" or error == "NO_AUTH_FOUND":
                print("\n⚠️ Проблема с токеном доступа!")
                print("   Убедитесь, что используете OAuth токен администратора, а не вебхук.")
                print("   Получить OAuth токен можно через: https://www.bitrix24.ru/apps/local/dev.php")
            
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Ошибка при запросе к API Bitrix24: {e}")
        return False
    except Exception as e:
        print(f"❌ Неожиданная ошибка: {e}")
        return False


def check_field_exists(domain: str, oauth_token: str) -> bool:
    """
    Проверка существования поля UF_TELEGRAM_ID
    
    Args:
        domain: Домен Bitrix24
        oauth_token: OAuth токен
        
    Returns:
        True если поле существует, False если нет
    """
    base_url = f"https://{domain.rstrip('/')}/rest/user.userfield.get"
    
    try:
        response = requests.get(base_url, params={"auth": oauth_token})
        response.raise_for_status()
        result = response.json()
        
        fields = result.get("result", [])
        for field in fields:
            if isinstance(field, dict) and field.get("FIELD_NAME") == "UF_TELEGRAM_ID":
                return True
        return False
    except Exception:
        return False


def main():
    """Основная функция"""
    print("=" * 60)
    print("Создание пользовательского поля UF_TELEGRAM_ID в Bitrix24")
    print("=" * 60)
    print()
    
    # Получаем параметры из переменных окружения или запрашиваем у пользователя
    domain = os.getenv("BITRIX24_DOMAIN")
    oauth_token = os.getenv("BITRIX24_OAUTH_TOKEN")
    
    if not domain:
        domain = input("Введите домен Bitrix24 (например, your-domain.bitrix24.ru): ").strip()
    
    if not oauth_token:
        print("\n⚠️ Для создания поля нужен OAuth токен администратора (не вебхук!)")
        print("   Получить OAuth токен можно через: https://www.bitrix24.ru/apps/local/dev.php")
        print("   Или создайте поле вручную через интерфейс Bitrix24:")
        print(f"   https://{domain}/bitrix/admin/userfield_edit.php?ENTITY_ID=USER&lang=ru")
        oauth_token = input("\nВведите OAuth токен администратора (или нажмите Enter для выхода): ").strip()
        
        if not oauth_token:
            print("Выход из программы.")
            sys.exit(0)
    
    print(f"\nДомен: {domain}")
    print("Проверка существования поля...")
    
    # Проверяем, существует ли поле
    if check_field_exists(domain, oauth_token):
        print("✅ Поле UF_TELEGRAM_ID уже существует в Bitrix24!")
        return
    
    print("Поле не найдено. Создание нового поля...")
    print()
    
    # Создаем поле
    success = create_telegram_field(domain, oauth_token)
    
    if success:
        print("\n🎉 Готово! Поле UF_TELEGRAM_ID создано и готово к использованию.")
    else:
        print("\n💡 Альтернативный способ:")
        print(f"   Создайте поле вручную через интерфейс Bitrix24:")
        print(f"   https://{domain}/bitrix/admin/userfield_edit.php?ENTITY_ID=USER&lang=ru")
        print("   Код поля: UF_TELEGRAM_ID, Тип: Строка")


if __name__ == "__main__":
    main()
