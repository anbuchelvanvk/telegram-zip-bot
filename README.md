# Telegram Zip/Unzip Bot

This bot allows you to easily ZIP and UNZIP files directly in Telegram. 

## Features
- **Zip Files**: Send files to the bot, then type `/zip` to get them as a single `.zip` archive.
- **Unzip Files**: Send a `.zip` file to the bot, and it will extract it and send you back all the files inside.

## How to run it on your computer

1. Talk to [@BotFather](https://t.me/BotFather) on Telegram and create a new bot. You will receive a **Bot Token**.
2. Open `bot.py` and replace `'YOUR_BOT_TOKEN_HERE'` with your actual token.
3. Double click on `run.bat` to install dependencies and start the bot.

## Keeping it online 24/7

The bot uses a method called `infinity_polling()` which makes it extremely robust against network disconnects. As long as the `run.bat` window is open on your computer, the bot will stay online and automatically recover from any temporary internet drops!

**Want it online when your PC is off?**
If you want it to run online all the time even when your computer is turned off, you'll need to upload this folder to a cheap VPS (Virtual Private Server) or a cloud platform (like Heroku, Render, or PythonAnywhere) and run `bot.py` there. 
