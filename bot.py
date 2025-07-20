import os
import json
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from keep_alive import keep_alive
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MAIN_CHANNEL = os.getenv("MAIN_CHANNEL")
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL")

MOVIES = "movieFiles.json"
PENDING = "pending.json"

def load(fn): 
    try:
        return json.load(open(fn)) if os.path.exists(fn) else {}
    except Exception as e:
        logger.error(f"Error loading {fn}: {e}")
        return {}

def save(fn, data): 
    try:
        open(fn, "w").write(json.dumps(data, indent=2))
    except Exception as e:
        logger.error(f"Error saving {fn}: {e}")

movies = load(MOVIES)
pending = load(PENDING)

# — STEP 0: Admin Initiates Upload with /upload
async def cmd_upload(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID: 
        await u.message.reply_text("❌ Only admins can use this command.")
        return
    kb = [[InlineKeyboardButton("🎞 Single", callback_data="t_single")],
          [InlineKeyboardButton("🌐 Multi‑Language", callback_data="t_multi")]]
    await u.message.reply_text("Choose mode:", reply_markup=InlineKeyboardMarkup(kb))

# — STEP 1: Handle Single vs Multi
async def on_type(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID:
        await u.callback_query.edit_message_text("❌ Unauthorized.")
        return
    pending[uid] = {"type": None, "files": {}, "stage": "files"}
    data = pending[uid]

    data["type"] = "single" if u.callback_query.data == "t_single" else "multi"
    if data["type"] == "single":
        data["files"] = []

    save(PENDING, pending)

    message = (
        "📥 Send all movie files now." if data["type"] == "single"
        else "📥 Send as:\n<LanguageName>\n[file1]\n[file2]\n..."
    )
    await u.callback_query.edit_message_text(message)

# — STEP 2: Admin Sends Content
async def on_file_or_text(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending: 
        return
    data = pending[uid]
    if data["stage"] != "files": 
        return

    msg = u.message
    if data["type"] == "multi":
        if msg.text:
            data.setdefault("files", {})[msg.text.strip()] = []
            data["current"] = msg.text.strip()
            await msg.reply_text(f"✅ Language '{msg.text.strip()}' set. Now send files for this language.")
        elif msg.document:
            if "current" not in data: 
                await msg.reply_text("⚠️ Send a language name first.")
                return
            data["files"][data["current"]].append({"file_id": msg.document.file_id})
            await msg.reply_text("✅ File received.")
        else:
            await msg.reply_text("⚠️ Please send a language name or a file.")
            return
    else:
        if msg.document:
            data["files"].append({"file_id": msg.document.file_id})
            await msg.reply_text("✅ File received.")
        else:
            await msg.reply_text("⚠️ Please send a file.")
            return

    save(PENDING, pending)

    # Automatically transition to poster stage if files received
    if (data["type"] == "single" and len(data["files"]) > 0) or \
       (data["type"] == "multi" and len(data["files"]) > 0 and any(len(files) > 0 for files in data["files"].values())):
        data["stage"] = "poster"
        save(PENDING, pending)
        kb = [[InlineKeyboardButton("📌 Send Poster", callback_data="poster_send")],
              [InlineKeyboardButton("➡️ Forward Poster", callback_data="poster_forward")],
              [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        await msg.reply_text("✅ Files received. Choose poster option:", reply_markup=InlineKeyboardMarkup(kb))

# — STEP 3: Poster Option Selection
async def on_poster_option(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending or pending[uid]["stage"] != "poster":
        await u.callback_query.edit_message_text("❌ Invalid action.")
        return
    data = pending[uid]
    if u.callback_query.data == "poster_send":
        data["poster_mode"] = "send"
        await u.callback_query.edit_message_text(
            "📌 Send the movie poster as a **photo** with caption or as **text only**."
        )
    elif u.callback_query.data == "poster_forward":
        data["poster_mode"] = "forward"
        await u.callback_query.edit_message_text(
            "➡️ Forward the poster message now."
        )
    elif u.callback_query.data == "cancel":
        del pending[uid]
        save(PENDING, pending)
        await u.callback_query.edit_message_text("✅ Upload cancelled.")
    save(PENDING, pending)

# — STEP 4: Poster Upload or Forward (Fixed)
async def on_poster(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        logger.warning(f"Invalid poster attempt by user {uid}")
        return
    data = pending[uid]
    if data["stage"] != "poster":
        logger.warning(f"Wrong stage for poster: {data['stage']}")
        return
    msg = u.message

    try:
        if data.get("poster_mode") == "send":
            if msg.photo:
                data["poster"] = msg.caption or ""
                data["photo"] = msg.photo[-1].file_id
            elif msg.text:
                data["poster"] = msg.text
                data["photo"] = None
            else:
                await msg.reply_text("⚠️ Please send a photo with caption or text only.")
                return
        elif data.get("poster_mode") == "forward":
            if not msg.forward_from_message_id:
                await msg.reply_text("⚠️ Please forward a message containing a photo or text.")
                return
            data["poster"] = msg.caption or msg.text or ""
            data["photo"] = msg.photo[-1].file_id if msg.photo else None
            data["forwarded_message_id"] = msg.message_id
            data["forwarded_chat_id"] = msg.chat_id
        else:
            await msg.reply_text("⚠️ Invalid poster mode. Please start over with /upload.")
            return

        data["stage"] = "code"
        save(PENDING, pending)
        await msg.reply_text("🔢 Now send a unique movie-code:")
    except Exception as e:
        logger.error(f"Error in on_poster: {e}")
        kb = [[InlineKeyboardButton("🔄 Retry Poster", callback_data="poster_retry")],
              [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        await msg.reply_text(
            "❌ An error occurred while processing the poster. Please retry or cancel.",
            reply_markup=InlineKeyboardMarkup(kb)
        )

# — STEP 5: Receive movie-code
async def on_code(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending: 
        return
    data = pending[uid]
    if data["stage"] != "code": 
        return
    code = u.message.text.strip().lower()
    if not code.isalnum():  # Ensure code is alphanumeric
        await u.message.reply_text("❌ Code must be alphanumeric.")
        return
    if code in movies: 
        await u.message.reply_text("❌ Code already exists.")
        return
    data["code"] = code
    data["stage"] = "altlink"
    save(PENDING, pending)
    kb = [[InlineKeyboardButton("➕ Add Link", callback_data="alt_provide")],
          [InlineKeyboardButton("⏭ Skip", callback_data="alt_skip")],
          [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
    await u.message.reply_text("🎥 Alternate link?", reply_markup=InlineKeyboardMarkup(kb))

# — STEP 6: Optional Alt Link Buttons
async def on_alt_btn(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        await u.callback_query.edit_message_text("❌ Invalid action.")
        return
    data = pending[uid]
    if u.callback_query.data == "alt_skip":
        data["alt_link"] = None
        await finalize(u, ctx)
    elif u.callback_query.data == "alt_provide":
        data["stage"] = "altwait"
        await u.callback_query.edit_message_text("🔗 Send alternate link now.")
    elif u.callback_query.data == "cancel":
        del pending[uid]
        save(PENDING, pending)
        await u.callback_query.edit_message_text("✅ Upload cancelled.")
    elif u.callback_query.data == "poster_retry":
        data["stage"] = "poster"
        save(PENDING, pending)
        kb = [[InlineKeyboardButton("📌 Send Poster", callback_data="poster_send")],
              [InlineKeyboardButton("➡️ Forward Poster", callback_data="poster_forward")],
              [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        await u.callback_query.edit_message_text("✅ Retrying. Choose poster option:", reply_markup=InlineKeyboardMarkup(kb))
    save(PENDING, pending)

# — STEP 7: Receive Alt Link Input
async def on_alt_input(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending: 
        return
    data = pending[uid]
    if data["stage"] != "altwait": 
        return
    if not u.message.text.startswith(("http://", "https://")):
        await u.message.reply_text("⚠️ Please send a valid URL starting with http:// or https://")
        return
    data["alt_link"] = u.message.text.strip()
    await finalize(u, ctx)
    save(PENDING, pending)

# — Finalize and Post
async def finalize(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        await u.effective_chat.send_message("❌ Invalid action.")
        return
    d = pending.pop(uid)
    movies[d["code"]] = d
    save(MOVIES, movies)
    save(PENDING, pending)

    kb = [[InlineKeyboardButton("▶️ Get Movie", url=f"https://t.me/{ctx.bot.username}?start={d['code']}")]]
    if d.get("alt_link"):
        kb.append([InlineKeyboardButton("📥 If bot not responding...", url=d["alt_link"])])

    markup = InlineKeyboardMarkup(kb)
    try:
        if d.get("poster_mode") == "forward" and d.get("forwarded_message_id"):
            msg = await ctx.bot.forward_message(
                chat_id=u.effective_chat.id,
                from_chat_id=d["forwarded_chat_id"],
                message_id=d["forwarded_message_id"]
            )
            await ctx.bot.edit_message_reply_markup(
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                reply_markup=markup
            )
        else:
            if d.get("photo"):
                msg = await ctx.bot.send_photo(
                    chat_id=u.effective_chat.id, 
                    photo=d["photo"], 
                    caption=d["poster"], 
                    reply_markup=markup
                )
            else:
                msg = await ctx.bot.send_message(
                    chat_id=u.effective_chat.id, 
                    text=d["poster"], 
                    reply_markup=markup
                )
        await ctx.bot.forward_message(
            chat_id=MAIN_CHANNEL, 
            from_chat_id=msg.chat_id, 
            message_id=msg.message_id
        )
        await u.effective_chat.send_message("✅ Movie posted successfully!")
    except Exception as e:
        logger.error(f"Error in finalize: {e}")
        await u.effective_chat.send_message("❌ Failed to post movie. Please try again with /upload.")

# — /cancel Command
async def cmd_cancel(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Only admins can use this command.")
        return
    uid = str(u.effective_user.id)
    if uid in pending:
        del pending[uid]
        save(PENDING, pending)
        await u.message.reply_text("✅ Upload process cancelled.")
    else:
        await u.message.reply_text("❌ No active upload process to cancel.")

# — /start for Users (with join-check)
async def cmd_start(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usr = u.effective_user.id
    args = ctx.args or []
    if not args: 
        await u.message.reply_text("❌ Usage: /start <moviecode>")
        return
    code = args[0].lower()
    try:
        mem = await ctx.bot.get_chat_member(MAIN_CHANNEL, usr)
        if mem.status not in ["member", "administrator", "creator"]: 
            raise Exception
    except Exception as e:
        logger.warning(f"User {usr} not in channel: {e}")
        kb = [[InlineKeyboardButton("🎬 Join Channel", url=f"https://t.me/{MAIN_CHANNEL.lstrip('@')}")],
              [InlineKeyboardButton("🔄 Retry", callback_data=f"retry_{code}")]]
        await u.message.reply_text("Join our channel first.", reply_markup=InlineKeyboardMarkup(kb))
        return
    if code not in movies: 
        await u.message.reply_text("❌ Invalid code.")
        return
    d = movies[code]

    if d["type"] == "multi":
        kb = [[InlineKeyboardButton(lang, callback_data=f"getlang_{code}_{lang}")] for lang in d["files"]]
        await u.message.reply_text("Choose language:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        for file in d["files"]:
            await ctx.bot.send_document(u.effective_chat.id, file["file_id"])
        if d.get("alt_link"):
            await ctx.bot.send_message(
                chat_id=u.effective_chat.id,
                text=f"📥 If bot not responding, click here: {d['alt_link']}"
            )

# — Retry After Join
async def on_retry(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    code = u.callback_query.data.split("_")[1]
    ctx.args = [code]
    await cmd_start(u, ctx)

# — Language Selection
async def on_getlang(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    _, code, lang = u.callback_query.data.split("_", 2)
    if code not in movies or lang not in movies[code]["files"]:
        await u.effective_chat.send_message("❌ Invalid movie or language.")
        return
    for file in movies[code]["files"][lang]:
        await ctx.bot.send_document(u.effective_chat.id, file["file_id"])
    if movies[code].get("alt_link"):
        await ctx.bot.send_message(
            chat_id=u.effective_chat.id,
            text=f"📥 If bot not responding, click here: {movies[code]['alt_link']}"
        )

# — Admin Commands
async def cmd_delete(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID: 
        await u.message.reply_text("❌ Only admins can use this command.")
        return
    c = ctx.args and ctx.args[0].lower()
    if not c or c not in movies: 
        await u.message.reply_text("❌ Usage: /delete <code>")
        return
    del movies[c]
    save(MOVIES, movies)
    await u.message.reply_text(f"✅ Deleted `{c}`")

async def cmd_status(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id == ADMIN_ID:
        await u.message.reply_text("✅ Bot is alive.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_type, pattern="^t_"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_file_or_text))
    app.add_handler(CallbackQueryHandler(on_poster_option, pattern="^(poster_|cancel)"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.TEXT | filters.FORWARDED), on_poster))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Regex(r"^[a-zA-Z0-9_-]+$"), on_code))
    app.add_handler(CallbackQueryHandler(on_alt_btn, pattern="^(alt_|cancel|poster_retry)"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Regex(r"^https?://"), on_alt_input))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_retry, pattern="^retry_"))
    app.add_handler(CallbackQueryHandler(on_getlang, pattern="^getlang_"))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("status", cmd_status))

    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    main()