"""
Скрипт для установки webhook для Telegram бота
Используется после деплоя на Railway
"""
import os
import requests
from dotenv import load_dotenv

# Загрузка переменных окружения (если файл .env существует)
if os.path.exists('.env'):
    load_dotenv()

def set_webhook():
    """Установка webhook для Telegram бота"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    webhook_url = os.getenv("WEBHOOK_URL")
    
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN не установлен")
        return
    
    if not webhook_url:
        print("❌ WEBHOOK_URL не установлен")
        return
    
    # Формируем URL для webhook
    webhook_full_url = f"{webhook_url}/{token}"
    
    # Устанавливаем webhook
    api_url = f"https://api.telegram.org/bot{token}/setWebhook"
    response = requests.post(api_url, json={"url": webhook_full_url})
    
    if response.status_code == 200:
        result = response.json()
        if result.get("ok"):
            print(f"✅ Webhook успешно установлен: {webhook_full_url}")
        else:
            print(f"❌ Ошибка установки webhook: {result.get('description')}")
    else:
        print(f"❌ Ошибка HTTP: {response.status_code}")

if __name__ == "__main__":
    set_webhook()
