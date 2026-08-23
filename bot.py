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
        "3. Support for .001, .002, .003 split files\n"
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
    
    zip_filename = os.path.join(TEMP_DIR, f"archive_{chat_id}.zip")
    try:
        # allowZip64=True is necessary for files > 2GB (in total) or > 4GB etc
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zipf:
            for file_path in user_files[chat_id]:
                if os.path.exists(file_path):
                    zipf.write(file_path, os.path.basename(file_path))
        
        await status_msg.edit_text("📤 Uploading your Zip archive. This might take a while for large files...")
        
        await client.send_document(
            chat_id, 
            zip_filename,
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
        
    merged_filename = os.path.join(TEMP_DIR, f"{chat_id}_{base_name}")
    
    try:
        with open(merged_filename, 'wb') as outfile:
            for file_path in files_to_merge:
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as infile:
                        shutil.copyfileobj(infile, outfile)
                        
        await status_msg.edit_text("📤 Uploading your merged file. This might take a while...")
        
        await client.send_document(
            chat_id, 
            merged_filename,
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
                else:
                    await status_msg.edit_text(f"📤 Uploading {len(extracted_files)} extracted files...")
                    for ext_file in extracted_files:
                        up_msg = await message.reply_text(f"📤 Uploading {os.path.basename(ext_file)}...")
                        await client.send_document(
                            chat_id, 
                            ext_file,
                            progress=progress,
                            progress_args=(up_msg, f"📤 Uploading {os.path.basename(ext_file)}...")
                        )
                        await up_msg.delete()
                    await status_msg.edit_text("✅ All files extracted and uploaded!")
                            
            except zipfile.BadZipFile:
                await status_msg.edit_text("❌ The file provided is not a valid zip archive.")
            except Exception as e:
                await status_msg.edit_text(f"❌ Error unzipping: {e}")
            finally:
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
