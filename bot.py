from keep_alive import keep_alive
keep_alive()

from custom_caption import generate_custom_caption

import os
import json
import re
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# --- Load config and environment ---
load_dotenv()

with open("config.json", "r") as f:
    config = json.load(f)

ADMIN_ID = config["admin_id"]
SOURCE_CHANNEL = config["source_channel"]
MAIN_CHANNEL = config["main_channel"]

MOVIES_FILE = "movieFiles.json"
PENDING_FILE = "pending.json"

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

def load_json(file):
    return json.load(open(file)) if os.path.exists(file) else {}

movie_data = load_json(MOVIES_FILE)
pending_data = load_json(PENDING_FILE)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    user_id = update.effective_user.id

    if not args:
        return await update.message.reply_text("❌ Usage: /start <moviecode>")

    code = args[0].lower()
    context.user_data["start_code"] = code

    try:
        member = await context.bot.get_chat_member(MAIN_CHANNEL, user_id)
        if member.status not in ["member", "administrator", "creator"]:
            raise Exception("Not joined")
    except:
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Join Channel", url=f"https://t.me/{MAIN_CHANNEL.lstrip('@')}")],
            [InlineKeyboardButton("✅ I've Joined", callback_data=f"retry_{code}")]
        ])
        return await update.message.reply_text(
            "🎥 Please join our main channel first to access this movie.",
            reply_markup=btn,
            parse_mode="Markdown"
        )

    if code not in movie_data:
        return await update.message.reply_text("❌ Invalid movie code.")

    for file_info in movie_data[code]["files"]:
        try:
            if file_info.get("file_id"):
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=file_info["file_id"],
                    caption=file_info.get("custom_caption") or "",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=file_info.get("original_caption", "📄 Info"),
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Failed to send: {e}")

async def retry_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    code = query.data.split("_", 1)[1]

    try:
        member = await context.bot.get_chat_member(MAIN_CHANNEL, user_id)
        if member.status not in ["member", "administrator", "creator"]:
            raise Exception()
    except:
        return await query.edit_message_text("❌ You still haven't joined the channel.")

    if code not in movie_data:
        return await query.edit_message_text("❌ Invalid movie code.")

    await query.edit_message_text("✅ Access granted. Sending files...")

    for file_info in movie_data[code]["files"]:
        try:
            if file_info.get("file_id"):
                await context.bot.send_document(
                    chat_id=query.message.chat.id,
                    document=file_info["file_id"],
                    caption=file_info.get("custom_caption") or "",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat.id,
                    text=file_info.get("original_caption", "📄 Info"),
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text=f"⚠️ Failed to send: {e}"
            )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("✅ Bot is alive.")

async def delete_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(context.args) != 1:
        return await update.message.reply_text("❌ Usage: /delete <moviecode>")
    code = context.args[0].lower()
    if code not in movie_data:
        return await update.message.reply_text("❌ No such movie found.")
    del movie_data[code]
    save_json(MOVIES_FILE, movie_data)
    await update.message.reply_text(f"🗑 Deleted movie `{code}`", parse_mode="Markdown")

async def handle_forwarded_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    message = update.message
    original_caption = message.caption or message.text or ""

    if not message.forward_from_chat:
        return await update.message.reply_text("⚠️ Please forward directly from the source channel.")

    user_key = str(update.effective_user.id)
    pending = pending_data.setdefault(user_key, {"files": [], "stage": None})

    chat_id = message.forward_from_chat.id
    message_id = message.forward_from_message_id

    if message_id is None:
        return await update.message.reply_text("⚠️ Cannot detect forwarded message ID.")

    file_entry = {
        "chat_id": chat_id,
        "message_id": message_id,
        "original_caption": original_caption,
        "custom_caption": generate_custom_caption(original_caption, channel_username="mollywooddiariesreloaded"),
        "file_id": message.document.file_id if message.document else None
    }

    if file_entry not in pending["files"]:
        pending["files"].append(file_entry)

    if pending["stage"] != "poster":
        pending["stage"] = "poster"
        save_json(PENDING_FILE, pending_data)
        await update.message.reply_text("✅ Files received.\n📌 Now send the poster (image, text, or forwarded message).")
    else:
        save_json(PENDING_FILE, pending_data)

async def handle_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    user_key = str(update.effective_user.id)
    pending = pending_data.get(user_key)
    if not pending or pending["stage"] != "poster":
        return

    message = update.message
    caption = message.caption or message.text or ""
    if message.forward_from_chat:
        caption = message.caption or message.text or ""

    pending["poster"] = caption
    if message.photo:
        pending["photo"] = message.photo[-1].file_id

    pending["stage"] = "alternate_link"
    save_json(PENDING_FILE, pending_data)
    
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 Send Alternate Link", callback_data="send_alternate_link")],
        [InlineKeyboardButton("⏭ Skip", callback_data="skip_alternate_link")]
    ])
    await update.message.reply_text("🔗 Please send an alternate link or skip.", reply_markup=btn)

async def handle_alternate_link_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_key = str(query.from_user.id)
    pending = pending_data.get(user_key)
    if not pending or pending["stage"] != "alternate_link":
        return

    if query.data == "send_alternate_link":
        pending["stage"] = "waiting_for_link"
        save_json(PENDING_FILE, pending_data)
        await query.edit_message_text("🔗 Please send the alternate link.")
    elif query.data == "skip_alternate_link":
        pending["stage"] = "code"
        save_json(PENDING_FILE, pending_data)
        await query.edit_message_text("🔢 Now send the unique movie code for /start command.")

async def handle_alternate_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    user_key = str(update.effective_user.id)
    pending = pending_data.get(user_key)
    if not pending or pending["stage"] != "waiting_for_link":
        return

    link = update.message.text.strip()
    if not re.match(r'^https?://', link):
        return await update.message.reply_text("❌ Invalid link format. Please send a valid URL starting with http:// or https://.")

    pending["alternate_link"] = link
    pending["stage"] = "code"
    save_json(PENDING_FILE, pending_data)
    await update.message.reply_text("🔢 Now send the unique movie code for /start command.")

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    user_key = str(update.effective_user.id)
    pending = pending_data.get(user_key)
    if not pending or pending["stage"] != "code":
        return

    code = update.message.text.strip().lower()
    if code in movie_data:
        return await update.message.reply_text("❌ Code already exists. Try another.")

    movie_data[code] = {
        "files": pending["files"],
        "poster": pending.get("poster", ""),
        "photo": pending.get("photo"),
        "alternate_link": pending.get("alternate_link")
    }
    save_json(MOVIES_FILE, movie_data)
    del pending_data[user_key]
    save_json(PENDING_FILE, pending_data)

    buttons = [
        [InlineKeyboardButton("▶️ Get Movie", url=f"https://t.me/{context.bot.username}?start={code}")]
    ]
    if movie_data[code].get("alternate_link"):
        buttons.append([InlineKeyboardButton("🔗 If bot not responding, click here", url=movie_data[code]["alternate_link"])])

    btn = InlineKeyboardMarkup(buttons)

    if "photo" in movie_data[code]:
        await context.bot.send_photo(
            update.effective_chat.id,
            photo=movie_data[code]["photo"],
            caption=movie_data[code]["poster"],
            reply_markup=btn
        )
    else:
        await context.bot.send_message(
            update.effective_chat.id,
            text=movie_data[code]["poster"],
            reply_markup=btn
        )

    await update.message.reply_text("✅ Movie added. Forward the above message to your group!")

def main():
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(retry_join, pattern=r"^retry_"))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("delete", delete_movie))
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, handle_forwarded_file))
    app.add_handler(MessageHandler((filters.PHOTO | filters.FORWARDED | filters.TEXT) & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_poster))
    app.add_handler(CallbackQueryHandler(handle_alternate_link_choice, pattern=r"^(send_alternate_link|skip_alternate_link)$"))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND & filters.Regex(r'^https?://'), handle_alternate_link))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.Regex(r'^https?://'), handle_code))

    print("✅ Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()