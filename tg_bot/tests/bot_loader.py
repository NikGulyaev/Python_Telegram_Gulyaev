import importlib.util
import sys
from pathlib import Path

# Загружаем tg_bot.py (скрипт бота) под отдельным именем bot_script,
# чтобы не конфликтовать с Django-пакетом tg_bot/
path = Path(__file__).resolve().parent.parent / "tg_bot.py"
spec = importlib.util.spec_from_file_location("bot_script", str(path))
bot = importlib.util.module_from_spec(spec)
sys.modules["bot_script"] = bot
spec.loader.exec_module(bot)
