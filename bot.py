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

user_files = {}
extracted_sessions = {}

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
        "1. Zip multiple files into one ZIP\n"
        "2. Unzip archives easily\n"
        "3. Support for `.001`, `.002`, `.003` split files\n"
        "4. Multiple files can be queued\n"
        "5. Fast and simple processing\n\n"
        "**How to use:**\n"
        "👉 Send a `.zip` file to **Unzip** it automatically.\n"
        "👉 Send multiple files and type `/zip` to **Zip** them.\n"
        "👉 Send `.001`, `.002` split files and type `/merge` to **Merge** them.\n"
        "👉 Type `/clear` if you want to cancel queued files."
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
        await message.reply_text("🗑 Cleared your pending files.")
    else:
        await message.reply_text("You don't have any pending files.")


@app.on_message(filters.command("zip"))
async def zip_files_command(client: Client, message: Message):
    if not await check_force_sub(client, message):
        return
    chat_id = message.chat.id
    if chat_id not in user_files or not user_files[chat_id]:
        await message.reply_text("⚠️ You haven't sent any files to zip yet.")
        return

    status_msg = await message.reply_text("🔄 Zipping your files, please wait...")
    
    zip_filename = os.path.join(TEMP_DIR, f"@QualityPixels - archive_{chat_id}.zip")
    try:
        # allowZip64=True is necessary for files > 2GB (in total) or > 4GB etc
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zipf:
            for file_path in user_files[chat_id]:
                if os.path.exists(file_path):
                    zipf.write(file_path, os.path.basename(file_path))
        
        await status_msg.edit_text("📤 Uploading your Zip archive. This might take a while for large files...")
        
        caption = f"**@QualityPixels - archive.zip**\nFile Type: .zip"
        await client.send_document(
            chat_id, 
            zip_filename,
            caption=caption,
            progress=progress,
            progress_args=(status_msg, "📤 Uploading Zip Archive...")
        )
        await status_msg.delete()
            
    except Exception as e:
        await status_msg.edit_text(f"❌ Error creating zip: {e}")
    finally:
        # Cleanup
        for file_path in user_files.get(chat_id, []):
            if os.path.exists(file_path):
                os.remove(file_path)
        user_files[chat_id] = []
        if os.path.exists(zip_filename):
            os.remove(zip_filename)


@app.on_message(filters.command("merge"))
async def merge_files_command(client: Client, message: Message):
    if not await check_force_sub(client, message):
        return
    chat_id = message.chat.id
    if chat_id not in user_files or len(user_files[chat_id]) < 2:
        await message.reply_text("⚠️ You need to queue at least 2 files to merge.")
        return

    status_msg = await message.reply_text("🔄 Merging your files, please wait...")
    
    # Sort files alphabetically to ensure .001, .002, etc. are ordered
    files_to_merge = sorted(user_files[chat_id])
    
    # Determine base name from the first file
    first_file = files_to_merge[0]
    base_name = os.path.basename(first_file)
    if base_name.endswith('.001'):
        base_name = base_name[:-4]
    else:
        base_name = "merged_file"
        
    merged_filename = os.path.join(TEMP_DIR, f"@QualityPixels - {base_name}")
    
    try:
        with open(merged_filename, 'wb') as outfile:
            for file_path in files_to_merge:
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as infile:
                        shutil.copyfileobj(infile, outfile)
                        
        await status_msg.edit_text("📤 Uploading your merged file. This might take a while...")
        
        _, ext = os.path.splitext(merged_filename)
        caption = f"**@QualityPixels - {base_name}**\nFile Type: {ext}"
        await client.send_document(
            chat_id, 
            merged_filename,
            caption=caption,
            progress=progress,
            progress_args=(status_msg, "📤 Uploading Merged File...")
        )
        await status_msg.delete()
            
    except Exception as e:
        await status_msg.edit_text(f"❌ Error merging files: {e}")
    finally:
        for file_path in user_files.get(chat_id, []):
            if os.path.exists(file_path):
                os.remove(file_path)
        user_files[chat_id] = []
        if os.path.exists(merged_filename):
            os.remove(merged_filename)


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


@app.on_message(filters.document | filters.photo | filters.audio | filters.video)
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
                    await status_msg.edit_text(f"✅ Extracted {len(extracted_files)} files. Select what to download:", reply_markup=reply_markup)
                            
            except zipfile.BadZipFile:
                await status_msg.edit_text("❌ The file provided is not a valid zip archive.")
                shutil.rmtree(user_temp_dir, ignore_errors=True)
            except Exception as e:
                await status_msg.edit_text(f"❌ Error unzipping: {e}")
                shutil.rmtree(user_temp_dir, ignore_errors=True)
                
        else:
            status_msg = await message.reply_text(f"📥 Downloading `{file_name}`...")
            
            user_dir = os.path.join(TEMP_DIR, f"user_{chat_id}")
            os.makedirs(user_dir, exist_ok=True)
            
            save_path = os.path.join(user_dir, file_name)
            
            base, ext = os.path.splitext(save_path)
            counter = 1
            while os.path.exists(save_path):
                save_path = f"{base}_{counter}{ext}"
                counter += 1
                
            # Download to path
            await client.download_media(
                message, 
                file_name=save_path,
                progress=progress,
                progress_args=(status_msg, f"📥 Downloading `{os.path.basename(save_path)}`...")
            )
                
            if chat_id not in user_files:
                user_files[chat_id] = []
            
            user_files[chat_id].append(save_path)
            
            await status_msg.edit_text(
                f"✅ Saved `{os.path.basename(save_path)}`.\n"
                f"Total files queued: {len(user_files[chat_id])}.\n"
                f"Send more files, then type `/zip` to zip them, or `/merge` to combine split files."
            )
            
    except Exception as e:
        await message.reply_text(f"❌ An error occurred: {e}")


@app.on_callback_query()
async def handle_callbacks(client: Client, query):
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
                    
                _, ext = os.path.splitext(ext_file)
                caption = f"**{os.path.basename(ext_file)}**\nFile Type: {ext}"
                
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
                        
                    _, ext = os.path.splitext(ext_file)
                    caption = f"**{os.path.basename(ext_file)}**\nFile Type: {ext}"
                    
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
