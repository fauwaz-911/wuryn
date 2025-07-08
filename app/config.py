
# Optional: config loader if needed in future
import os
from dotenv import load_dotenv

# Load environment variables from .env (for local use)
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID")
