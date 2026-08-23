@echo off
echo Installing requirements...
pip install -r requirements.txt
echo.
echo Starting the Telegram Bot...
python bot.py
pause
