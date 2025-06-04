import os
import ffmpeg
import shlex
import subprocess
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import *
from pymongo import MongoClient
from pyrogram.errors import FloodWait, PeerIdInvalid
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

API_ID = os.environ.get("API_ID", "")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_Token", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", ""))
MONGODB_URI=os.environ.get("MONGODB_URI", "")
LOG_CHANNEL=os.environ.get("LOG_CHANNEL", "")
SUPPORTED_EXTENSIONS = (".mp4", ".mkv")

bot = Client("screenshot_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
mongo = MongoClient(MONGODB_URI)
db = mongo["screenshot_bot"]
users = db["users"]
stats = db["stats"]
pending_files = {}

def is_supported(filename: str) -> bool:
    return filename.lower().endswith(SUPPORTED_EXTENSIONS)

def build_keyboard():
    keyboard, row = [], []
    for i in range(1, 21):
        row.append(InlineKeyboardButton(str(i), callback_data=f"ss_{i}"))
        if i % 5 == 0:
            keyboard.append(row)
            row = []
    return InlineKeyboardMarkup(keyboard)

async def get_video_duration(file_path):
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{file_path}"'
        result = subprocess.run(shlex.split(cmd), capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"[ERROR] Duration fetch failed: {e}")
        return 0

@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message: Message):
    user = message.from_user
    is_new = users.count_documents({"_id": user.id}) == 0

    users.update_one({"_id": user.id}, {"$set": {
        "name": user.first_name,
        "username": user.username,
        "joined": datetime.utcnow()
    }}, upsert=True)

    await message.reply(
        "👋 Send me an `.mp4` or `.mkv` video (as video or document), and I'll take evenly spaced screenshots for you!"
    )

    if is_new:
        await bot.send_message(
            chat_id=LOG_CHANNEL,
            text=(
                f"👤 <b>User started bot</b>\n\n"
                f"🆔 <code>{user.id}</code>\n"
                f"👤 {user.mention()}\n"
                f"🔗 @{user.username or 'N/A'}"
            ),
        )

@bot.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message: Message):
    await message.reply(
        "🛠 <b>Bot Instructions</b>\n\n"
        "1. Send a .mp4 or .mkv file as video or document.\n"
        "2. Select how many screenshots (1–20).\n"
        "3. Receive evenly spaced screenshots from the video.\n\n"
        "Use /cancel to cancel current processing."
    )

@bot.on_message(filters.command("stats") & filters.private)
async def stats_cmd(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.reply("🚫 You are not authorized.")

    total_users = users.count_documents({})
    total_files = stats.find_one({"_id": "summary"}) or {}
    file_count = total_files.get("total_files", 0)
    await message.reply(f"📊 <b>Bot Stats</b>\n\n👥 Users: {total_users}\n📁 Files Processed: {file_count}")

@bot.on_message(filters.command("broadcast") & filters.private)
async def broadcast_cmd(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.reply("🚫 You are not authorized to use this command.")

    if not message.reply_to_message:
        return await message.reply("⚠️ Reply to a message to broadcast it.")

    sent_count, fail_count = 0, 0
    text = message.reply_to_message.text or message.reply_to_message.caption
    media = message.reply_to_message

    await message.reply("📣 Starting broadcast...")

    async for user in users.find({}, {"_id": 1}):
        user_id = user["_id"]
        try:
            if media.media:
                await media.copy(chat_id=user_id)
            else:
                await bot.send_message(chat_id=user_id, text=text)
            sent_count += 1
            await asyncio.sleep(0.1)  # To avoid hitting flood limits
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except PeerIdInvalid:
            users.delete_one({"_id": user_id})  # Remove inactive user
            fail_count += 1
        except Exception:
            fail_count += 1

    await message.reply(f"✅ Broadcast complete!\n\n📬 Sent: {sent_count}\n❌ Failed: {fail_count}")

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message: Message):
    user_id = message.from_user.id
    if user_id in pending_files:
        try:
            os.remove(pending_files[user_id])
        except Exception:
            pass
        pending_files.pop(user_id)
        await message.reply("❌ Your pending upload was cancelled.")
    else:
        await message.reply("ℹ️ No pending uploads found.")

@bot.on_message((filters.video | filters.document) & filters.private)
async def handle_video(client, message: Message):
    file = message.video or message.document
    if not file or not is_supported(file.file_name):
        return await message.reply("❌ Only `.mp4` and `.mkv` formats are supported.")

    msg = await message.reply("📥 Downloading file...")
    file_path = await message.download()
    await msg.edit("✅ File downloaded.\n\n📸 How many screenshots to generate?")
    pending_files[message.from_user.id] = file_path

    await message.reply("Choose how many screenshots (1–20):", reply_markup=build_keyboard())

    # log file send
    stats.update_one({"_id": "summary"}, {"$inc": {"total_files": 1}}, upsert=True)
    await bot.send_message(
        chat_id=LOG_CHANNEL,
        text=f"📁 <b>File Received</b>\n\n🆔 <code>{message.from_user.id}</code>\n👤 {message.from_user.mention()}\n📝 File: <code>{file.file_name}</code>"
    )

@bot.on_callback_query(filters.regex(r"^ss_\d+$"))
async def handle_screenshot_selection(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    count = int(callback_query.data.split("_")[1])

    file_path = pending_files.pop(user_id, None)
    if not file_path:
        return await callback_query.answer("No file to process. Try uploading again.", show_alert=True)

    await callback_query.message.edit_text(f"🛠 Generating {count} screenshots...")

    duration = await get_video_duration(file_path)
    if duration <= 0:
        await callback_query.message.reply("❌ Unable to get video duration.")
        os.remove(file_path)
        return

    screenshots, temp_files = [], []
    for i in range(1, count + 1):
        timestamp = int(duration * i / (count + 1))
        screenshot_path = f"screenshot_{user_id}_{i}_{datetime.now().timestamp()}.jpg"
        try:
            ffmpeg.input(file_path, ss=timestamp).output(screenshot_path, vframes=1).run(overwrite_output=True, quiet=True)
            screenshots.append(InputMediaPhoto(media=screenshot_path, caption=f"🕒 {timestamp}s" if i == 1 else ""))
            temp_files.append(screenshot_path)
        except Exception as e:
            await callback_query.message.reply(f"⚠️ Failed at {timestamp}s: {e}")

    if screenshots:
        await callback_query.message.reply_media_group(screenshots)
    else:
        await callback_query.message.reply("❌ No screenshots were generated.")

    os.remove(file_path)
    for f in temp_files:
        if os.path.exists(f):
            os.remove(f)

bot.run()