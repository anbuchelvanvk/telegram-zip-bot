import os
import zipfile
import shutil
import asyncio
import threading
import logging
logging.basicConfig(level=logging.INFO)
import re
import aiohttp
from aiohttp import web
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, PeerIdInvalid
from dotenv import load_dotenv

load_dotenv()

# Telegram Credentials
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')

FORCE_SUB_CHANNEL = os.environ.get('FORCE_SUB_CHANNEL', '')

LOG_CHANNEL_ID = os.environ.get('LOG_CHANNEL_ID', '')
if LOG_CHANNEL_ID:
    try:
        LOG_CHANNEL_ID = int(LOG_CHANNEL_ID)
    except ValueError:
        pass

app = Client(
    "zip_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

from functools import wraps

user_files = {}
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


async def force_resolve_peer(chat_id):
    if not isinstance(chat_id, int):
        return True
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": "🔄 (System: Resolving Peer ID...)"}
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    msg_id = data.get("result", {}).get("message_id")
                    if msg_id:
                        del_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                        await session.post(del_url, json={"chat_id": chat_id, "message_id": msg_id})
                    await asyncio.sleep(1) 
                    return True
                return False
    except Exception as e:
        print(f"Force resolve error: {e}")
        return False


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
        
    if len(message.command) > 1:
        payload = message.command[1]
        if payload.startswith("file_"):
            if not LOG_CHANNEL_ID:
                await message.reply_text("❌ Database channel not configured.")
                return
            
            try:
                msg_id = int(payload.split("_")[1])
                status_msg = await message.reply_text("🔄 Fetching your file...")
                
                keyboard = [
                    [InlineKeyboardButton("📢 Share QualityPixels", url="https://t.me/share/url?url=https://t.me/QualityPixels&text=Join%20QualityPixels%20for%20more%20awesome%20content!")]
                ]
                
                await client.copy_message(
                    message.chat.id,
                    LOG_CHANNEL_ID,
                    msg_id,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                await status_msg.delete()
                return
            except Exception as e:
                await message.reply_text("❌ Invalid or expired link.")
                return

    welcome_text = (
        "Welcome to the ultimate Telegram Unzip Bot! 🗂\n"
        "What I can do for you:\n\n"
        "📂 **Unzip Files:** Forward me any `.zip` file, and I'll extract the contents instantly.\n"
        "🧩 **Split Archives:** Send me `.001`, `.002` split files, and type `/unzip` to seamlessly extract them!\n"
        "📲 **Smart Downloads:** Get a clean menu to download exactly what you need, or batch upload everything at once!\n\n"
        "Enjoy easy file management with fast servers and smart batch uploads!\n"
        "Brought to you by @QualityPixels."
    )
    await message.reply_text(welcome_text)

@app.on_message(filters.command("clear"))
async def clear_files(client: Client, message: Message):
    if not await check_force_sub(client, message):
        return
    chat_id = message.chat.id
    if chat_id in user_files and user_files[chat_id]:
        for file_path in user_files[chat_id]:
            if os.path.exists(file_path):
                os.remove(file_path)
        user_files[chat_id] = []
        await message.reply_text("🗑 Cleared your pending split files.")
    else:
        await message.reply_text("You don't have any pending split files.")

@app.on_message(filters.command("unzip"))
@queue_task
async def unzip_split_command(client: Client, message: Message):
    if not await check_force_sub(client, message):
        return
    chat_id = message.chat.id
    if chat_id not in user_files or len(user_files[chat_id]) < 1:
        await message.reply_text("⚠️ You need to queue split files (like .001, .002) before running /unzip.")
        return

    status_msg = await message.reply_text("🔄 Merging split files internally for extraction...")
    
    # Sort files alphabetically to ensure .001, .002, etc. are ordered
    files_to_merge = sorted(user_files[chat_id])
    
    user_temp_dir = os.path.join(TEMP_DIR, f"unzip_split_{chat_id}_{message.id}")
    os.makedirs(user_temp_dir, exist_ok=True)
    
    merged_zip = os.path.join(user_temp_dir, "merged.zip")
    
    try:
        with open(merged_zip, 'wb') as outfile:
            for file_path in files_to_merge:
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as infile:
                        shutil.copyfileobj(infile, outfile)
                        
        await status_msg.edit_text("📦 Extracting files from split archive...")
        
        try:
            with zipfile.ZipFile(merged_zip, 'r') as zip_ref:
                zip_ref.extractall(user_temp_dir)
            
            os.remove(merged_zip)
            
            extracted_files = []
            for root, dirs, files in os.walk(user_temp_dir):
                for file in files:
                    ext_file = os.path.join(root, file)
                    base_name = os.path.basename(ext_file)
                    if not base_name.startswith("@QualityPixels - "):
                        new_name = f"@QualityPixels - {base_name}"
                        new_path = os.path.join(root, new_name)
                        os.rename(ext_file, new_path)
                        ext_file = new_path
                    extracted_files.append(ext_file)
            
            if not extracted_files:
                await status_msg.delete()
                await message.reply_text("⚠️ The split archive was empty.")
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
                await status_msg.delete()
                new_msg = await message.reply_text(
                    f"✅ Extracted {len(extracted_files)} files.\n\n"
                    f"⚠️ **Note:** Files will be automatically deleted after 2 minutes of inactivity.\n\n"
                    f"Select what to download:", 
                    reply_markup=reply_markup
                )
                
                # Schedule 2-minute cleanup
                asyncio.create_task(cleanup_session(client, chat_id, session_id, new_msg.id, user_temp_dir))
                        
        except zipfile.BadZipFile:
            await status_msg.delete()
            await message.reply_text("❌ The merged file is not a valid zip archive.")
            shutil.rmtree(user_temp_dir, ignore_errors=True)
            
    except Exception as e:
        await status_msg.delete()
        await message.reply_text(f"❌ Error unzipping split files: {e}")
        shutil.rmtree(user_temp_dir, ignore_errors=True)
    finally:
        for file_path in user_files.get(chat_id, []):
            if os.path.exists(file_path):
                os.remove(file_path)
        user_files[chat_id] = []


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
            await client.delete_messages(chat_id, message_id)
            await client.send_message(
                chat_id, 
                "⏳ **Session Expired**\nFiles were automatically deleted from the server after 2 minutes of inactivity to save space."
            )
        except Exception:
            pass

async def cache_zip_files(client: Client, log_channel_id: int, extracted_files: list, file_unique_id: str):
    if not log_channel_id or not file_unique_id:
        return
    for ext_file in extracted_files:
        if not os.path.exists(ext_file):
            continue
            
        base, ext = os.path.splitext(os.path.basename(ext_file))
        ext_clean = ext.replace('.', '').upper()
        caption = f"**👉🏽 {base}**\n**👉🏽 File Type: {ext_clean}**\n\n#ZIP_{file_unique_id}"
        
        try:
            await client.send_document(log_channel_id, ext_file, caption=caption)
            await asyncio.sleep(2)
        except PeerIdInvalid:
            await force_resolve_peer(log_channel_id)
            try:
                await client.send_document(log_channel_id, ext_file, caption=caption)
                await asyncio.sleep(2)
            except Exception as e:
                print(f"Background cache error after resolve: {e}")
        except Exception as e:
            print(f"Background cache error: {e}")


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
            file_unique_id = message.document.file_unique_id if message.document else None
            
            if LOG_CHANNEL_ID and file_unique_id:
                status_msg = await message.reply_text("🔍 Checking cache...")
                cached_msgs = []
                try:
                    async for msg in client.search_messages(LOG_CHANNEL_ID, query=f"#ZIP_{file_unique_id}"):
                        if msg.document or msg.audio or msg.video:
                            cached_msgs.append(msg)
                except PeerIdInvalid:
                    await force_resolve_peer(LOG_CHANNEL_ID)
                    try:
                        async for msg in client.search_messages(LOG_CHANNEL_ID, query=f"#ZIP_{file_unique_id}"):
                            if msg.document or msg.audio or msg.video:
                                cached_msgs.append(msg)
                    except Exception as e:
                        print(f"Cache search error after resolve: {e}")
                except Exception as e:
                    print(f"Cache search error: {e}")
                    
                if cached_msgs:
                    await status_msg.delete()
                    cached_msgs.sort(key=lambda x: x.id)
                    
                    session_id = str(message.id)
                    if chat_id not in extracted_sessions:
                        extracted_sessions[chat_id] = {}
                        
                    extracted_sessions[chat_id][session_id] = {
                        "cached": True,
                        "files": cached_msgs
                    }
                    
                    keyboard = []
                    keyboard.append([InlineKeyboardButton("📤 Send All Files", callback_data=f"sendall_{session_id}")])
                    
                    for idx, msg in enumerate(cached_msgs[:50]):
                        filename = "Unknown"
                        if msg.document: filename = msg.document.file_name
                        elif msg.audio: filename = msg.audio.file_name
                        elif msg.video: filename = msg.video.file_name
                        keyboard.append([InlineKeyboardButton(filename, callback_data=f"sendfile_{session_id}_{idx}")])
                        
                    if len(cached_msgs) > 50:
                        keyboard.append([InlineKeyboardButton(f"...and {len(cached_msgs)-50} more", callback_data="ignore")])
                        
                    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data=f"cancelzip_{session_id}")])
                    
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await message.reply_text(
                        f"⚡️ **CACHE HIT!**\n✅ Found {len(cached_msgs)} files instantly.\n\n"
                        f"Select what to download:", 
                        reply_markup=reply_markup
                    )
                    return
                else:
                    await status_msg.edit_text("📥 Downloading zip file (this might take a while for large files)...")
            else:
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
                        ext_file = os.path.join(root, file)
                        base_name = os.path.basename(ext_file)
                        if not base_name.startswith("@QualityPixels - "):
                            new_name = f"@QualityPixels - {base_name}"
                            new_path = os.path.join(root, new_name)
                            os.rename(ext_file, new_path)
                            ext_file = new_path
                        extracted_files.append(ext_file)
                
                if not extracted_files:
                    await status_msg.delete()
                    await message.reply_text("⚠️ The zip file was empty.")
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
                    await status_msg.delete()
                    new_msg = await message.reply_text(
                        f"✅ Extracted {len(extracted_files)} files.\n\n"
                        f"⚠️ **Note:** Files will be automatically deleted after 2 minutes of inactivity.\n\n"
                        f"Select what to download:", 
                        reply_markup=reply_markup
                    )
                    
                    # Schedule 2-minute cleanup
                    asyncio.create_task(cleanup_session(client, chat_id, session_id, new_msg.id, user_temp_dir))
                    
                    # Background cache
                    if LOG_CHANNEL_ID and file_unique_id:
                        asyncio.create_task(cache_zip_files(client, LOG_CHANNEL_ID, extracted_files, file_unique_id))
                            
            except zipfile.BadZipFile:
                await status_msg.delete()
                await message.reply_text("❌ The file provided is not a valid zip archive.")
                shutil.rmtree(user_temp_dir, ignore_errors=True)
            except Exception as e:
                await status_msg.delete()
                await message.reply_text(f"❌ Error unzipping: {e}")
                shutil.rmtree(user_temp_dir, ignore_errors=True)
                
        else:
            is_split = re.search(r'\.\d{3}$', file_name.lower()) is not None
            if is_split:
                status_msg = await message.reply_text(f"📥 Downloading split file `{file_name}`...")
                
                user_dir = os.path.join(TEMP_DIR, f"user_{chat_id}")
                os.makedirs(user_dir, exist_ok=True)
                
                save_path = os.path.join(user_dir, file_name)
                
                base, ext = os.path.splitext(save_path)
                counter = 1
                while os.path.exists(save_path):
                    save_path = f"{base}_{counter}{ext}"
                    counter += 1
                    
                await client.download_media(
                    message, 
                    file_name=save_path,
                    progress=progress,
                    progress_args=(status_msg, f"📥 Downloading `{os.path.basename(save_path)}`...")
                )
                    
                if chat_id not in user_files:
                    user_files[chat_id] = []
                
                user_files[chat_id].append(save_path)
                
                await status_msg.delete()
                await message.reply_text(
                    f"✅ Saved `{os.path.basename(save_path)}`.\n"
                    f"Total split files queued: {len(user_files[chat_id])}.\n"
                    f"Send more parts, then type `/unzip` to extract them!"
                )
            else:
                await message.reply_text(f"❌ I only accept `.zip` or `.001` split archives. Please send a valid archive file!")
            
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
            await query.message.delete()
            await client.send_message(chat_id, "🗑 Files deleted and session cancelled.")
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
                await query.message.delete()
                status_msg = await client.send_message(chat_id, f"📤 Uploading all {len(extracted_files)} files...")
                for item in extracted_files:
                    if session.get("cached"):
                        msg = item
                        deep_link = f"https://t.me/{client.me.username}?start=file_{msg.id}"
                        keyboard = [
                            [InlineKeyboardButton("📢 Share QualityPixels", url="https://t.me/share/url?url=https://t.me/QualityPixels&text=Join%20QualityPixels%20for%20more%20awesome%20content!")],
                            [InlineKeyboardButton("📥 Permanent Link", url=deep_link)]
                        ]
                        try:
                            await client.copy_message(chat_id, LOG_CHANNEL_ID, msg.id, reply_markup=InlineKeyboardMarkup(keyboard))
                            await asyncio.sleep(0.5)
                        except PeerIdInvalid:
                            await force_resolve_peer(LOG_CHANNEL_ID)
                            try:
                                await client.copy_message(chat_id, LOG_CHANNEL_ID, msg.id, reply_markup=InlineKeyboardMarkup(keyboard))
                                await asyncio.sleep(0.5)
                            except Exception: pass
                        except Exception: pass
                    else:
                        ext_file = item
                        if not os.path.exists(ext_file):
                            continue
                            
                        base, ext = os.path.splitext(os.path.basename(ext_file))
                        ext_clean = ext.replace('.', '').upper()
                        caption = f"**👉🏽 {base}**\n**👉🏽 File Type: {ext_clean}**"
                        
                        import urllib.parse
                        share_text = urllib.parse.quote(f"Listen to {base} on @QualityPixels!")
                        file_share_url = f"https://t.me/share/url?url=https://t.me/QualityPixels&text={share_text}"
                        
                        keyboard = [
                            [InlineKeyboardButton("📢 Share QualityPixels", url="https://t.me/share/url?url=https://t.me/QualityPixels&text=Join%20QualityPixels%20for%20more%20awesome%20content!")],
                            [InlineKeyboardButton("🔗 Share this file", url=file_share_url)]
                        ]
                        
                        up_msg = await query.message.reply_text(f"📤 Uploading {os.path.basename(ext_file)}...")
                        try:
                            await client.send_document(
                                chat_id, 
                                ext_file,
                                caption=caption,
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                progress=progress,
                                progress_args=(up_msg, f"📤 Uploading {os.path.basename(ext_file)}...")
                            )
                        except Exception as e:
                            print(f"Upload error: {e}")
                        await up_msg.delete()
                
                await status_msg.delete()
                await client.send_message(chat_id, "✅ All files processed!")
                if user_temp_dir:
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
                    if session.get("cached"):
                        msg = extracted_files[idx]
                        deep_link = f"https://t.me/{client.me.username}?start=file_{msg.id}"
                        keyboard = [
                            [InlineKeyboardButton("📢 Share QualityPixels", url="https://t.me/share/url?url=https://t.me/QualityPixels&text=Join%20QualityPixels%20for%20more%20awesome%20content!")],
                            [InlineKeyboardButton("📥 Permanent Link", url=deep_link)]
                        ]
                        try:
                            await client.copy_message(chat_id, LOG_CHANNEL_ID, msg.id, reply_markup=InlineKeyboardMarkup(keyboard))
                            await query.answer("✅ File forwarded from cache!")
                        except PeerIdInvalid:
                            await force_resolve_peer(LOG_CHANNEL_ID)
                            try:
                                await client.copy_message(chat_id, LOG_CHANNEL_ID, msg.id, reply_markup=InlineKeyboardMarkup(keyboard))
                                await query.answer("✅ File forwarded from cache!")
                            except Exception:
                                await query.answer("⚠️ Error fetching from cache.", show_alert=True)
                        except Exception:
                            await query.answer("⚠️ Error fetching from cache.", show_alert=True)
                    else:
                        ext_file = extracted_files[idx]
                        if os.path.exists(ext_file):
                            base, ext = os.path.splitext(os.path.basename(ext_file))
                            ext_clean = ext.replace('.', '').upper()
                            caption = f"**👉🏽 {base}**\n**👉🏽 File Type: {ext_clean}**"
                            
                            import urllib.parse
                            share_text = urllib.parse.quote(f"Listen to {base} on @QualityPixels!")
                            file_share_url = f"https://t.me/share/url?url=https://t.me/QualityPixels&text={share_text}"
                            
                            keyboard = [
                                [InlineKeyboardButton("📢 Share QualityPixels", url="https://t.me/share/url?url=https://t.me/QualityPixels&text=Join%20QualityPixels%20for%20more%20awesome%20content!")],
                                [InlineKeyboardButton("🔗 Share this file", url=file_share_url)]
                            ]
                            
                            up_msg = await query.message.reply_text(f"📤 Uploading {os.path.basename(ext_file)}...")
                            try:
                                await client.send_document(
                                    chat_id, 
                                    ext_file,
                                    caption=caption,
                                    reply_markup=InlineKeyboardMarkup(keyboard),
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
