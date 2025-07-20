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
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
MAIN_CHANNEL = os.getenv("MAIN_CHANNEL")
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL")

# File paths
MOVIES = "movieFiles.json"
PENDING = "pending.json"

# Helper to load/save JSON
def load(fn):
    try:
        return json.load(open(fn)) if os.path.exists(fn) else {}
    except Exception as e:
        logger.error(f"Error loading {fn}: {e}")
        return {}

def save(fn, data):
    try:
        with open(fn, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving {fn}: {e}")
        raise

# In-memory state
movies = load(MOVIES)
pending = load(PENDING)

# — STEP 0: /upload command
async def cmd_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    kb = [
        [InlineKeyboardButton("🎞 Single", callback_data="t_single")],
        [InlineKeyboardButton("🌐 Multi‑Language", callback_data="t_multi")]
    ]
    await update.message.reply_text("Choose upload mode:", reply_markup=InlineKeyboardMarkup(kb))

# — STEP 1: Type selection
async def on_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.edit_message_text("❌ Unauthorized.")
        return
    uid = str(update.effective_user.id)
    pending[uid] = {"type": None, "files": {}, "stage": "files"}
    data = pending[uid]
    data["type"] = "single" if update.callback_query.data == "t_single" else "multi"
    if data["type"] == "single":
        data["files"] = []
    save(PENDING, pending)
    prompt = (
        "📥 Send all movie files now." if data["type"] == "single"
        else "📥 Send files labeled by language: send a language name text, then files."
    )
    await update.callback_query.edit_message_text(prompt)

# — STEP 2: Receive files
async def on_file_or_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = str(update.effective_user.id)
    if update.effective_user.id != ADMIN_ID or uid not in pending:
        return
    data = pending[uid]
    if data["stage"] != "files":
        return

    # Detect file types
    file_id = None
    if msg.document:
        file_id = msg.document.file_id
    elif msg.video:
        file_id = msg.video.file_id
    elif msg.animation:
        file_id = msg.animation.file_id

    # Multi-language
    if data["type"] == "multi":
        if msg.text and not file_id:
            label = msg.text.strip()
            data.setdefault("files", {})[label] = []
            data["current"] = label
            await msg.reply_text(f"✅ Language '{label}' selected. Now send files for this language.")
        elif file_id:
            if "current" not in data:
                await msg.reply_text("⚠️ Please send a language name first.")
                return
            data["files"][data["current"]].append({"file_id": file_id})
            await msg.reply_text("✅ File received.")
        else:
            await msg.reply_text("⚠️ Send a language name or a supported media file.")
            return
    # Single-language
    else:
        if file_id:
            data["files"].append({"file_id": file_id})
            await msg.reply_text("✅ File received.")
        else:
            await msg.reply_text("⚠️ Please send a supported media file.")
            return

    save(PENDING, pending)
    # Check and advance to poster
    has_files = (
        (data["type"] == "single" and len(data["files"]) > 0) or
        (data["type"] == "multi" and any(len(v) > 0 for v in data["files"].values()))
    )
    if has_files:
        data["stage"] = "poster"
        save(PENDING, pending)
        await msg.reply_text(
            "✅ All files received. Now send or forward the movie poster (photo with optional caption)."
        )

# — STEP 3: Receive poster
async def on_poster(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = str(update.effective_user.id)
    if update.effective_user.id != ADMIN_ID or uid not in pending:
        return
    data = pending[uid]
    if data["stage"] != "poster":
        return

    # Capture poster
    if msg.forward_from_message_id:
        data["poster_mode"] = "forward"
        data["forwarded_chat_id"] = msg.chat_id
        data["forwarded_message_id"] = msg.message_id
        data["poster"] = msg.caption or msg.text or ""
        data["photo"] = msg.photo[-1].file_id if msg.photo else None
    elif msg.photo or msg.text:
        data["poster_mode"] = "send"
        data["poster"] = msg.caption or msg.text or ""
        data["photo"] = msg.photo[-1].file_id if msg.photo else None
    else:
        await msg.reply_text("⚠️ Send a photo (with optional caption) or text poster.")
        return

    data["stage"] = "code"
    save(PENDING, pending)
    await msg.reply_text("🔢 Now send a unique movie code (alphanumeric):")

# — STEP 4: Receive code
async def on_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = str(update.effective_user.id)
    if update.effective_user.id != ADMIN_ID or uid not in pending:
        return
    data = pending[uid]
    if data["stage"] != "code":
        return

    code = msg.text.strip().lower()
    if not code.isalnum() or code in movies:
        await msg.reply_text("❌ Code invalid or exists. Please send another.")
        return

    data["code"] = code
    data["stage"] = "altlink"
    save(PENDING, pending)
    kb = [
        [InlineKeyboardButton("➕ Add Alternate Link", callback_data="alt_provide")],
        [InlineKeyboardButton("⏭ Skip", callback_data="alt_skip")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ]
    await msg.reply_text(
        f"✅ Code '{code}' registered. Provide alternate link (optional):",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# — STEP 5: Alt link buttons
async def on_alt_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = str(update.effective_user.id)
    if update.effective_user.id != ADMIN_ID or uid not in pending:
        return
    data = pending[uid]

    action = update.callback_query.data
    if action == "alt_skip":
        data["alt_link"] = None
        await finalize(update, ctx)
    elif action == "alt_provide":
        data["stage"] = "altwait"
        save(PENDING, pending)
        await update.callback_query.edit_message_text("🔗 Send the alternate link now:")
    elif action == "cancel":
        pending.pop(uid, None)
        save(PENDING, pending)
        await update.callback_query.edit_message_text("✅ Upload cancelled.")

# — STEP 6: Receive alternate link
async def on_alt_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = str(update.effective_user.id)
    if update.effective_user.id != ADMIN_ID or uid not in pending:
        return
    data = pending[uid]
    if data["stage"] != "altwait":
        return

    link = msg.text.strip()
    if not link.startswith(("http://", "https://")):
        await msg.reply_text("⚠️ Link must start with http:// or https://")
        return
    data["alt_link"] = link
    save(PENDING, pending)
    await finalize(update, ctx)

# — STEP 7: Finalize upload
async def finalize(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    d = pending.pop(uid, None)
    if not d or not d.get("code"):
        return
    movies[d["code"]] = d
    save(MOVIES, movies)
    save(PENDING, pending)

    # Build buttons
    kb = [[InlineKeyboardButton(
        "▶️ Get Movie",
        url=f"https://t.me/{ctx.bot.username}?start={d['code']}"
    )]]
    if d.get("alt_link"):
        kb.append([InlineKeyboardButton("📥 Alternate Link", url=d["alt_link"])])
    markup = InlineKeyboardMarkup(kb)

    # Send poster to admin
    if d.get("poster_mode") == "forward":
        msg = await ctx.bot.forward_message(
            chat_id=update.effective_chat.id,
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
                chat_id=update.effective_chat.id,
                photo=d["photo"],
                caption=d["poster"],
                reply_markup=markup
            )
        else:
            msg = await ctx.bot.send_message(
                chat_id=update.effective_chat.id,
                text=d["poster"],
                reply_markup=markup
            )

    # Forward poster to main channel
    await ctx.bot.forward_message(
        chat_id=MAIN_CHANNEL,
        from_chat_id=msg.chat_id,
        message_id=msg.message_id
    )
    await update.effective_chat.send_message("✅ Movie posted successfully!")

# — /cancel command
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    uid = str(update.effective_user.id)
    if uid in pending:
        pending.pop(uid)
        save(PENDING, pending)
        await update.message.reply_text("✅ Upload cancelled.")
    else:
        await update.message.reply_text("❌ No active upload to cancel.")

# — /start command for users
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usr = update.effective_user.id
    args = ctx.args or []
    if not args:
        await update.message.reply_text("❌ Usage: /start <moviecode>")
        return
    code = args[0].lower()
    # Check join
    try:
        mem = await ctx.bot.get_chat_member(MAIN_CHANNEL, usr)
        if mem.status not in ["member", "administrator", "creator"]:
            raise Exception
    except:
        kb = [
            [InlineKeyboardButton("🎬 Join Channel", url=f"https://t.me/{MAIN_CHANNEL.lstrip('@')}\
")],
            [InlineKeyboardButton("🔄 Retry", callback_data=f"retry_{code}")]
        ]
        await update.message.reply_text("Join our channel first.", reply_markup=InlineKeyboardMarkup(kb))
        return

    if code not in movies:
        await update.message.reply_text("❌ Invalid code.")
        return
    d = movies[code]

    if d["type"] == "multi":
        kb = [[InlineKeyboardButton(lang, callback_data=f"getlang_{code}_{lang}")] for lang in d["files"]]
        await update.message.reply_text("Choose language:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        for f in d["files"]:
            await ctx.bot.send_document(update.effective_chat.id, f["file_id"])
        if d.get("alt_link"):
            await ctx.bot.send_message(update.effective_chat.id, f"📥 Alternate Link: {d['alt_link']}")

# — Retry join
async def on_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    code = update.callback_query.data.split("_")[1]
    ctx.args = [code]
    await cmd_start(update, ctx)

# — Language selection
async def on_getlang(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, code, lang = update.callback_query.data.split("_", 2)
    if code not in movies or lang not in movies[code]["files"]:
        return await update.callback_query.edit_message_text("❌ Invalid selection.")
    for f in movies[code]["files"][lang]:
        await ctx.bot.send_document(update.effective_chat.id, f["file_id"])
    if movies[code].get("alt_link"):
        await ctx.bot.send_message(update.effective_chat.id, f"📥 Alternate Link: {movies[code]['alt_link']}")

# — /delete and /status
async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    c = ctx.args and ctx.args[0].lower()
    if not c or c not in movies:
        return await update.message.reply_text("❌ Usage: /delete <code>")
    del movies[c]
    save(MOVIES, movies)
    await update.message.reply_text(f"✅ Deleted `{c}`")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("✅ Bot is alive.")

# — Main setup
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Admin
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("status", cmd_status))

    # User
    app.add_handler(CommandHandler("start", cmd_start))

    # Callbacks
    app.add_handler(CallbackQueryHandler(on_type, pattern="^t_"))
    app.add_handler(CallbackQueryHandler(on_alt_btn, pattern="^(alt_provide|alt_skip|cancel)"))
    app.add_handler(CallbackQueryHandler(on_retry, pattern="^retry_"))
    app.add_handler(CallbackQueryHandler(on_getlang, pattern="^getlang_"))

    # Alt link input
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.Regex(r"^https?://"),
        on_alt_input
    ))

    # File uploads
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & (
            filters.DOCUMENT | filters.VIDEO | filters.ANIMATION
        ),
        on_file_or_text
    ))

    # Poster stage
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & (
            filters.PHOTO | filters.TEXT & ~filters.Regex(r"^https?://")
        ),
        on_poster
    ))

    # Code stage
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & filters.TEXT,
        on_code
    ))

    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    main()
