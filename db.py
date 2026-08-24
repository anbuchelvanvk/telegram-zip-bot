import sqlite3
import os
import json
import asyncio
from pyrogram import Client
from pyrogram.types import Message

DB_FILE = "cache.db"
DB_MSG_ID_FILE = "db_msg_id.txt"

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''CREATE TABLE IF NOT EXISTS zip_cache
                  (file_unique_id TEXT PRIMARY KEY, message_ids TEXT)''')
    return conn

async def download_db(client: Client, log_channel_id: int):
    try:
        chat = await client.get_chat(log_channel_id)
        if chat.pinned_message and chat.pinned_message.document:
            if chat.pinned_message.document.file_name == DB_FILE:
                print("Downloading DB from pinned message...")
                await client.download_media(chat.pinned_message, file_name=DB_FILE)
                with open(DB_MSG_ID_FILE, 'w') as f:
                    f.write(str(chat.pinned_message.id))
                print("DB downloaded and loaded.")
                return True
    except Exception as e:
        print(f"Error downloading DB: {e}")
    
    print("No pinned DB found, starting fresh.")
    get_connection().close()
    return False

async def upload_db(client: Client, log_channel_id: int):
    try:
        msg = await client.send_document(log_channel_id, DB_FILE, caption="System Database (Do not delete)")
        await msg.pin(disable_notification=True)
        
        # Delete old DB message if exists
        if os.path.exists(DB_MSG_ID_FILE):
            with open(DB_MSG_ID_FILE, 'r') as f:
                old_id = int(f.read().strip())
            try:
                await client.delete_messages(log_channel_id, old_id)
            except Exception: pass
            
        with open(DB_MSG_ID_FILE, 'w') as f:
            f.write(str(msg.id))
            
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
