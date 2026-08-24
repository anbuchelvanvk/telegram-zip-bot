import sqlite3
import os
import json
import asyncio
import aiohttp
from pyrogram import Client

DB_FILE = "cache.db"
DB_MSG_ID_FILE = "db_msg_id.txt"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''CREATE TABLE IF NOT EXISTS zip_cache
                  (file_unique_id TEXT PRIMARY KEY, message_ids TEXT)''')
    return conn

async def download_db(client: Client, log_channel_id: int):
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
            async with session.post(url, json={"chat_id": log_channel_id}) as resp:
                data = await resp.json()
                
            if data.get("ok"):
                pinned = data["result"].get("pinned_message", {})
                doc = pinned.get("document")
                if doc and doc.get("file_name") == DB_FILE:
                    print("Downloading DB from pinned message via HTTP...")
                    file_id = doc["file_id"]
                    msg_id = pinned["message_id"]
                    
                    # Get file path
                    f_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile"
                    async with session.post(f_url, json={"file_id": file_id}) as f_resp:
                        f_data = await f_resp.json()
                        
                    if f_data.get("ok"):
                        file_path = f_data["result"]["file_path"]
                        download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                        
                        # Download actual file
                        async with session.get(download_url) as d_resp:
                            with open(DB_FILE, 'wb') as f:
                                f.write(await d_resp.read())
                                
                        with open(DB_MSG_ID_FILE, 'w') as f:
                            f.write(str(msg_id))
                        print("DB downloaded and loaded.")
                        return True
    except Exception as e:
        print(f"Error downloading DB: {e}")
    
    print("No pinned DB found, starting fresh.")
    get_connection().close()
    return False

async def upload_db(client: Client, log_channel_id: int):
    try:
        import bot
        msg_id = await bot.http_send_document(log_channel_id, DB_FILE, "System Database")
        
        async with aiohttp.ClientSession() as session:
            pin_url = f"https://api.telegram.org/bot{BOT_TOKEN}/pinChatMessage"
            await session.post(pin_url, json={"chat_id": log_channel_id, "message_id": msg_id, "disable_notification": True})
            
            if os.path.exists(DB_MSG_ID_FILE):
                with open(DB_MSG_ID_FILE, 'r') as f:
                    old_id = int(f.read().strip())
                del_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                await session.post(del_url, json={"chat_id": log_channel_id, "message_id": old_id})
                
        with open(DB_MSG_ID_FILE, 'w') as f:
            f.write(str(msg_id))
            
    except Exception as e:
        print(f"Error uploading DB: {e}")

def get_cached_msgs(file_unique_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT message_ids FROM zip_cache WHERE file_unique_id=?", (file_unique_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None

async def save_cached_msgs(client: Client, log_channel_id: int, file_unique_id: str, message_ids: list):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO zip_cache (file_unique_id, message_ids) VALUES (?, ?)", 
                   (file_unique_id, json.dumps(message_ids)))
    conn.commit()
    conn.close()
    await upload_db(client, log_channel_id)
