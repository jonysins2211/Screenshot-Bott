import os
import ffmpeg
import shlex
import subprocess
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InputMediaPhoto,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
)

API_ID = os.environ.get("API_ID", "10446021")
API_HASH = os.environ.get("API_HASH", "da82f2cdb1ae8d752cbd91bbbb15e579")
BOT_TOKEN = os.environ.get("BOT_Token", "6158880228:AAGQ4ZS0i5U3yZpdsUfJUt4GNJhZkdYnTkE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1087141176"))
SUPPORTED_EXTENSIONS = (".mp4", ".mkv")

bot = Client("screenshot_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
pending_files = {}  # user_id: file_path

def is_supported(filename: str) -> bool:
    return filename.lower().endswith(SUPPORTED_EXTENSIONS)

async def get_video_duration(file_path):
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{file_path}"'
        result = subprocess.run(shlex.split(cmd), capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"[ERROR] Duration fetch failed: {e}")
        return 0

def build_keyboard():
    keyboard = []
    row = []
    for i in range(1, 21):
        row.append(InlineKeyboardButton(str(i), callback_data=f"ss_{i}"))
        if i % 5 == 0:
            keyboard.append(row)
            row = []
    return InlineKeyboardMarkup(keyboard)

@bot.on_message((filters.video | filters.document) & filters.private)
async def handle_video(client, message: Message):
    file = message.video or message.document
    if not file or not is_supported(file.file_name):
        await message.reply("❌ Only `.mp4` and `.mkv` formats are supported.")
        return

    msg = await message.reply("📥 Downloading file...")
    file_path = await message.download()
    await msg.edit("✅ File downloaded.\n\n📸 How many screenshots do you want?")
    pending_files[message.from_user.id] = file_path

    await message.reply("Please select how many screenshots to generate (1–20):", reply_markup=build_keyboard())

@bot.on_callback_query(filters.regex(r"^ss_\d+$"))
async def handle_screenshot_selection(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    count = int(callback_query.data.split("_")[1])

    file_path = pending_files.pop(user_id, None)
    if not file_path:
        await callback_query.answer("No video found. Please upload a new one.", show_alert=True)
        return

    await callback_query.message.edit_text(f"🛠 Generating {count} screenshots...")

    duration = await get_video_duration(file_path)
    if duration <= 0:
        await callback_query.message.reply("❌ Unable to get video duration.")
        os.remove(file_path)
        return

    screenshots = []
    temp_files = []

    for i in range(1, count + 1):
        timestamp = int(duration * i / (count + 1))
        screenshot_path = f"screenshot_{user_id}_{i}_{datetime.now().timestamp()}.jpg"
        try:
            ffmpeg.input(file_path, ss=timestamp).output(screenshot_path, vframes=1).run(overwrite_output=True, quiet=True)
            screenshots.append(InputMediaPhoto(media=screenshot_path, caption=f"🕒 {timestamp}s" if i == 1 else ""))
            temp_files.append(screenshot_path)
        except Exception as e:
            await callback_query.message.reply(f"⚠️ Failed at {timestamp}s: {e}")
            continue

    if screenshots:
        await callback_query.message.reply_media_group(screenshots)
    else:
        await callback_query.message.reply("❌ No screenshots were generated.")

    os.remove(file_path)
    for path in temp_files:
        if os.path.exists(path):
            os.remove(path)

@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    await message.reply("👋 Send me an `.mp4` or `.mkv` video (as video or document), and I'll take evenly spaced screenshots for you!")

bot.run()
