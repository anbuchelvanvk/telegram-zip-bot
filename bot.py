import os
import zipfile
import shutil
import asyncio
import threading
import logging
logging.basicConfig(level=logging.INFO)
from aiohttp import web
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from dotenv import load_dotenv

load_dotenv()

# Telegram Credentials
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')

FORCE_SUB_CHANNEL = os.environ.get('FORCE_SUB_CHANNEL', '')

app = Client(
    "zip_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

from functools import wraps

extracted_sessions = {}

processing_semaphore = None
queue_count = 0

def get_semaphore():
    global processing_semaphore
    if processing_semaphore is None:
        processing_semaphore = asyncio.Semaphore(2)
    return processing_semaphore

def queue_task(func):
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        global queue_count
        semaphore = get_semaphore()
        queue_msg = None
        if semaphore.locked():
            queue_count += 1
            queue_msg = await message.reply_text(f"⏳ **Server Busy!**\nBoth slots are in use. You are at position **#{queue_count}** in the queue. Please wait...")
        async with semaphore:
            if queue_msg:
                queue_count -= 1
                try: await queue_msg.delete()
                except Exception: pass
            return await func(client, message, *args, **kwargs)
    return wrapper

TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)


async def check_force_sub(client: Client, message: Message) -> bool:
    """
    Checks if a user is subscribed to the FORCE_SUB_CHANNEL.
    Returns True if subscribed or if bot lacks admin rights to check, 
    otherwise prompts the user and returns False.
    """
    try:
        member = await client.get_chat_member(FORCE_SUB_CHANNEL, message.from_user.id)
        if member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]:
            raise UserNotParticipant()
        return True
    except UserNotParticipant:
        join_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Channel to Use Bot", url=f"https://t.me/{FORCE_SUB_CHANNEL}")]
        ])
        await message.reply_text(
            "⚠️ **Access Denied**\n\n"
            "You must subscribe to our channel before you can use this bot. "
            "Please join using the button below and then try again!",
            reply_markup=join_btn
        )
        return False
    except Exception as e:
        # If bot is not an admin in the channel, it can't check participants.
        # Allow the user to proceed to prevent locking everyone out.
        print(f"Force Sub Error: {e}")
        return True


@app.on_message(filters.command(["start", "help"]))
async def send_welcome(client: Client, message: Message):
    if not await check_force_sub(client, message):
        return
    welcome_text = (
        "An Exclusive Bot from @QualityPixels\n\n"
        "✨ Features:\n"
        "1. Unzip archives easily\n"
        "2. Send any `.zip` file to immediately extract it\n"
        "3. Download individual files or send everything at once\n"
        "4. Fast and simple processing\n\n"
        "**How to use:**\n"
        "👉 Simply send a `.zip` file to the bot and it will Unzip it for you!"
    )
    await message.reply_text(welcome_text)


# Function to track download/upload progress
async def progress(current, total, message: Message, text: str):
    # Only update every 10% to avoid flooding Telegram API with edits (FloodWait limit)
    if total > 0:
        percent = current * 100 // total
        if percent % 10 == 0:
            try:
                # current // (1024*1024) gives MB
                await message.edit_text(f"{text} {percent}% ({current // (1024*1024)}MB / {total // (1024*1024)}MB)")
            except Exception:
                pass


async def cleanup_session(client: Client, chat_id: int, session_id: str, message_id: int, user_temp_dir: str):
    await asyncio.sleep(120)
    if chat_id in extracted_sessions and session_id in extracted_sessions[chat_id]:
        shutil.rmtree(user_temp_dir, ignore_errors=True)
        del extracted_sessions[chat_id][session_id]
        try:
            await client.edit_message_text(
                chat_id, 
                message_id, 
                "⏳ **Session Expired**\nFiles were automatically deleted from the server after 2 minutes of inactivity to save space."
            )
        except Exception:
            pass

@app.on_message(filters.document | filters.photo | filters.audio | filters.video)
@queue_task
async def handle_docs(client: Client, message: Message):
    if not await check_force_sub(client, message):
        return
    chat_id = message.chat.id
    
    try:
        # Get file name
        file_name = "unknown_file"
        if message.document:
            file_name = message.document.file_name or "document"
        elif message.video:
            file_name = message.video.file_name or "video.mp4"
        elif message.audio:
            file_name = message.audio.file_name or "audio.mp3"
        elif message.photo:
            file_name = "photo.jpg"
            
        is_zip = file_name.lower().endswith('.zip')

        if is_zip:
            status_msg = await message.reply_text("📥 Downloading zip file (this might take a while for large files)...")
            user_temp_dir = os.path.join(TEMP_DIR, f"unzip_{chat_id}_{message.id}")
            os.makedirs(user_temp_dir, exist_ok=True)
            
            zip_path = os.path.join(user_temp_dir, file_name)
            
            # Download file using Pyrogram
            await client.download_media(
                message, 
                file_name=zip_path,
                progress=progress,
                progress_args=(status_msg, "📥 Downloading zip file...")
            )
            
            await status_msg.edit_text("📦 Extracting files...")
                
            try:
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(user_temp_dir)
                
                os.remove(zip_path)
                
                extracted_files = []
                for root, dirs, files in os.walk(user_temp_dir):
                    for file in files:
                        extracted_files.append(os.path.join(root, file))
                
                if not extracted_files:
                    await status_msg.edit_text("⚠️ The zip file was empty.")
                    shutil.rmtree(user_temp_dir, ignore_errors=True)
                else:
                    session_id = str(message.id)
                    if chat_id not in extracted_sessions:
                        extracted_sessions[chat_id] = {}
                    
                    extracted_sessions[chat_id][session_id] = {
                        "dir": user_temp_dir,
                        "files": extracted_files
                    }
                    
                    keyboard = []
                    keyboard.append([InlineKeyboardButton("📤 Send All Files", callback_data=f"sendall_{session_id}")])
                    
                    # Limit to 50 files to avoid telegram limit on buttons
                    for idx, ext_file in enumerate(extracted_files[:50]):
                        filename = os.path.basename(ext_file)
                        keyboard.append([InlineKeyboardButton(filename, callback_data=f"sendfile_{session_id}_{idx}")])
                    
                    if len(extracted_files) > 50:
                        keyboard.append([InlineKeyboardButton(f"...and {len(extracted_files)-50} more", callback_data="ignore")])
                        
                    keyboard.append([InlineKeyboardButton("❌ Cancel & Delete", callback_data=f"cancelzip_{session_id}")])
                    
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await status_msg.edit_text(
                        f"✅ Extracted {len(extracted_files)} files.\n\n"
                        f"⚠️ **Note:** Files will be automatically deleted after 2 minutes of inactivity.\n\n"
                        f"Select what to download:", 
                        reply_markup=reply_markup
                    )
                    
                    # Schedule 2-minute cleanup
                    asyncio.create_task(cleanup_session(client, chat_id, session_id, status_msg.id, user_temp_dir))
                            
            except zipfile.BadZipFile:
                await status_msg.edit_text("❌ The file provided is not a valid zip archive.")
                shutil.rmtree(user_temp_dir, ignore_errors=True)
            except Exception as e:
                await status_msg.edit_text(f"❌ Error unzipping: {e}")
                shutil.rmtree(user_temp_dir, ignore_errors=True)
                
        else:
            await message.reply_text(f"❌ I only accept `.zip` archives. Please send a `.zip` file!")
            
    except Exception as e:
        await message.reply_text(f"❌ An error occurred: {e}")


@app.on_callback_query()
async def handle_callbacks(client: Client, query):
    global queue_count
    data = query.data
    chat_id = query.message.chat.id
    
    if data == "ignore":
        await query.answer()
        return
        
    if data.startswith("sendfile_") or data.startswith("sendall_") or data.startswith("cancelzip_"):
        parts = data.split("_")
        action = parts[0]
        session_id = parts[1]
        
        session = extracted_sessions.get(chat_id, {}).get(session_id)
        if not session:
            await query.answer("⚠️ Session expired or files deleted.", show_alert=True)
            return
            
        extracted_files = session["files"]
        user_temp_dir = session["dir"]
        
        share_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Share QualityPixels", url="https://t.me/share/url?url=https://t.me/QualityPixels&text=Join%20QualityPixels%20for%20more%20awesome%20content!")]
        ])

        if action == "cancelzip":
            shutil.rmtree(user_temp_dir, ignore_errors=True)
            if session_id in extracted_sessions.get(chat_id, {}):
                del extracted_sessions[chat_id][session_id]
            await query.message.edit_text("🗑 Files deleted and session cancelled.")
            await query.answer()
            
        elif action == "sendall":
            in_queue = False
            semaphore = get_semaphore()
            if semaphore.locked():
                in_queue = True
                queue_count += 1
                await query.answer(f"⏳ Server busy! You are at position #{queue_count} in the queue. Please wait...", show_alert=True)
            async with semaphore:
                if in_queue:
                    queue_count -= 1
                await query.message.edit_text(f"📤 Uploading all {len(extracted_files)} files...")
                for i, ext_file in enumerate(extracted_files):
                    if not os.path.exists(ext_file):
                        continue
                        
                    dir_name = os.path.dirname(ext_file)
                    base_name = os.path.basename(ext_file)
                    if not base_name.startswith("@QualityPixels - "):
                        new_name = f"@QualityPixels - {base_name}"
                        new_path = os.path.join(dir_name, new_name)
                        os.rename(ext_file, new_path)
                        ext_file = new_path
                        extracted_files[i] = new_path
                        
                    base, ext = os.path.splitext(os.path.basename(ext_file))
                    ext_clean = ext.replace('.', '').upper()
                    caption = f"**👉🏽 {base}**\n**👉🏽 File Type: {ext_clean}**"
                    
                    up_msg = await query.message.reply_text(f"📤 Uploading {os.path.basename(ext_file)}...")
                    try:
                        await client.send_document(
                            chat_id, 
                            ext_file,
                            caption=caption,
                            reply_markup=share_markup,
                            progress=progress,
                            progress_args=(up_msg, f"📤 Uploading {os.path.basename(ext_file)}...")
                        )
                    except Exception as e:
                        print(f"Upload error: {e}")
                    await up_msg.delete()
                
                await query.message.edit_text("✅ All files uploaded!")
                shutil.rmtree(user_temp_dir, ignore_errors=True)
                if session_id in extracted_sessions.get(chat_id, {}):
                    del extracted_sessions[chat_id][session_id]

        elif action == "sendfile":
            idx = int(parts[2])
            if idx < len(extracted_files):
                in_queue = False
                semaphore = get_semaphore()
                if semaphore.locked():
                    in_queue = True
                    queue_count += 1
                    await query.answer(f"⏳ Server busy! You are at position #{queue_count} in the queue. Please wait...", show_alert=True)
                async with semaphore:
                    if in_queue:
                        queue_count -= 1
                    ext_file = extracted_files[idx]
                    if os.path.exists(ext_file):
                        dir_name = os.path.dirname(ext_file)
                        base_name = os.path.basename(ext_file)
                        if not base_name.startswith("@QualityPixels - "):
                            new_name = f"@QualityPixels - {base_name}"
                            new_path = os.path.join(dir_name, new_name)
                            os.rename(ext_file, new_path)
                            ext_file = new_path
                            extracted_files[idx] = new_path
                            
                        base, ext = os.path.splitext(os.path.basename(ext_file))
                        ext_clean = ext.replace('.', '').upper()
                        caption = f"**👉🏽 {base}**\n**👉🏽 File Type: {ext_clean}**"
                        
                        up_msg = await query.message.reply_text(f"📤 Uploading {os.path.basename(ext_file)}...")
                        try:
                            await client.send_document(
                                chat_id, 
                                ext_file,
                                caption=caption,
                                reply_markup=share_markup,
                                progress=progress,
                                progress_args=(up_msg, f"📤 Uploading {os.path.basename(ext_file)}...")
                            )
                        except Exception as e:
                            print(f"Upload error: {e}")
                        await up_msg.delete()
                        await query.answer("✅ File uploaded!")
                    else:
                        await query.answer("⚠️ File not found. It might have been deleted.", show_alert=True)
            else:
                await query.answer("⚠️ Invalid file selection.", show_alert=True)


async def handle_web(request):
    return web.Response(text="Bot is running!")

def start_web_server():
    web_app = web.Application()
    web_app.router.add_get('/', handle_web)
    port = int(os.environ.get("PORT", 8080))
    web.run_app(web_app, host='0.0.0.0', port=port, handle_signals=False)

if __name__ == '__main__':
    print("Bot is starting up on Render Free Tier! (2GB Limit unlocked!)")
    t = threading.Thread(target=start_web_server, daemon=True)
    t.start()
    app.run()
