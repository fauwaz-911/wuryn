
import requests
import os

def notify_user(message: str):
    bot_token = os.getenv("7868677366:AAGcZrxUrxijX_HYLJlQCzOS35a4OyScUII")
    chat_id = os.getenv("1245158718")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"[Notification Failed] {e}")
