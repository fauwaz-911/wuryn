'''
7868677366:AAGcZrxUrxijX_HYLJlQCzOS35a4OyScUII
1245158718
'''
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID

def notify_user(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_USER_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except requests.RequestException as e:
        print("❌ Telegram error:", e)
