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
        with open(fn, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving {fn}: {e}")
        raise

movies = load(MOVIES)
pending = load(PENDING)

# — STEP 0: Admin initiates upload
async def cmd_upload(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Only admins can use this command.")
        return
    kb = [[InlineKeyboardButton("🎞 Single", callback_data="t_single")],
          [InlineKeyboardButton("🌐 Multi‑Language", callback_data="t_multi")]]
    await u.message.reply_text("Choose mode:", reply_markup=InlineKeyboardMarkup(kb))

# — STEP 1: Handle type selection
async def on_type(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    if u.effective_user.id != ADMIN_ID:
        await u.callback_query.edit_message_text("❌ Unauthorized.")
        return
    uid = str(u.effective_user.id)
    pending[uid] = {"type": None, "files": {}, "stage": "files"}
    data = pending[uid]
    data["type"] = "single" if u.callback_query.data == "t_single" else "multi"
    if data["type"] == "single":
        data["files"] = []
    save(PENDING, pending)
    prompt = ("📥 Send all movie files now." if data["type"] == "single"
              else "📥 Send as:\n<LanguageName>\n[file1]\n...")
    await u.callback_query.edit_message_text(prompt)

# — STEP 2: Receive files (documents, videos, animations)
async def on_file_or_text(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        return
    data = pending[uid]
    if data["stage"] != "files":
        return

    msg = u.message
    # detect file_id
    file_id = None
    if msg.document:
        file_id = msg.document.file_id
    elif msg.video:
        file_id = msg.video.file_id
    elif msg.animation:
        file_id = msg.animation.file_id

    if data["type"] == "multi":
        if msg.text:
            label = msg.text.strip()
            data.setdefault("files", {})[label] = []
            data["current"] = label
            await msg.reply_text(f"✅ Language '{label}' set. Now send files for this language.")
        elif file_id:
            if "current" not in data:
                await msg.reply_text("⚠️ Send a language name first.")
                return
            data["files"][data["current"]].append({"file_id": file_id})
            await msg.reply_text("✅ File received.")
        else:
            await msg.reply_text("⚠️ Please send a language name or a supported media file.")
            return
    else:
        if file_id:
            data["files"].append({"file_id": file_id})
            await msg.reply_text("✅ File received.")
        else:
            await msg.reply_text("⚠️ Please send a supported media file.")
            return

    save(PENDING, pending)
    # auto-advance to poster stage
    got_some = (data["type"] == "single" and len(data["files"]) > 0) or \
               (data["type"] == "multi" and any(len(v) for v in data["files"].values()))
    if got_some:
        data["stage"] = "poster"
        save(PENDING, pending)
        await msg.reply_text("✅ Files received. Now send or forward the movie poster (photo with optional caption or text only).")

# — STEP 3: Receive poster
async def on_poster(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        return
    data = pending[uid]
    if data["stage"] != "poster":
        return
    msg = u.message
    # process poster
    if msg.forward_from_message_id:
        data["poster"] = msg.caption or msg.text or ""
        data["photo"] = msg.photo[-1].file_id if msg.photo else None
        data.update({"poster_mode": "forward", "forwarded_message_id": msg.message_id,
                     "forwarded_chat_id": msg.chat_id})
    elif msg.photo or msg.text:
        data["poster"] = msg.caption or msg.text or ""
        data["photo"] = msg.photo[-1].file_id if msg.photo else None
        data["poster_mode"] = "send"
    else:
        await msg.reply_text("⚠️ Please send a photo with optional caption, text only, or forward a message.")
        return

    data["stage"] = "code"
    save(PENDING, pending)
    await msg.reply_text("🔢 Now send a unique movie-code (alphanumeric, e.g., Unitedkingdomofkerala2025):")

# — STEP 4: Receive code
async def on_code(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        return
    data = pending[uid]
    if data["stage"] != "code":
        return
    code = u.message.text.strip().lower()
    if not (code and code.isalnum() and code not in movies):
        await u.message.reply_text("❌ Invalid or duplicate code. Try again.")
        return
    data.update({"code": code, "stage": "altlink"})
    save(PENDING, pending)
    kb = [[InlineKeyboardButton("➕ Add Alternate Link", callback_data="alt_provide")],
          [InlineKeyboardButton("⏭ Skip", callback_data="alt_skip")],
          [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
    await u.message.reply_text(f"✅ Code '{code}' received. Alternate link (optional):",
                               reply_markup=InlineKeyboardMarkup(kb))

# — STEP 5: Alt link buttons
async def on_alt_btn(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        return
    data = pending[uid]
    cmd = u.callback_query.data
    if cmd == "alt_skip":
        data["alt_link"] = None
        await finalize(u, ctx)
    elif cmd == "alt_provide":
        data["stage"] = "altwait"
        save(PENDING, pending)
        await u.callback_query.edit_message_text("🔗 Send the alternate link now:")
    elif cmd == "cancel":
        pending.pop(uid, None)
        save(PENDING, pending)
        await u.callback_query.edit_message_text("✅ Upload cancelled.")

# — STEP 6: Receive alt link
async def on_alt_input(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        return
    data = pending[uid]
    if data["stage"] != "altwait":
        return
    link = u.message.text.strip()
    if not link.startswith(("http://", "https://")):
        await u.message.reply_text("⚠️ Link must start with http:// or https://")
        return
    data["alt_link"] = link
    save(PENDING, pending)
    await finalize(u, ctx)

# — STEP 7: Finalize and post
async def finalize(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    d = pending.pop(uid, None)
    if not d or not d.get("code"):
        return
    movies[d["code"]] = d
    save(MOVIES, movies)
    save(PENDING, pending)

    kb = [[InlineKeyboardButton("▶️ Get Movie", url=f"https://t.me/{ctx.bot.username}?start={d['code']}")]]
    if d.get("alt_link"):
        kb.append([InlineKeyboardButton("📥 Alternate Link", url=d["alt_link"])])
    markup = InlineKeyboardMarkup(kb)

    # send poster
    if d.get("poster_mode") == "forward":
        msg = await ctx.bot.forward_message(chat_id=u.effective_chat.id,
                                            from_chat_id=d["forwarded_chat_id"],
                                            message_id=d["forwarded_message_id"])
        await ctx.bot.edit_message_reply_markup(chat_id=msg.chat_id, message_id=msg.message_id,
                                                reply_markup=markup)
    else:
        if d.get("photo"):
            msg = await ctx.bot.send_photo(chat_id=u.effective_chat.id, photo=d["photo"],
                                           caption=d["poster"], reply_markup=markup)
        else:
            msg = await ctx.bot.send_message(chat_id=u.effective_chat.id, text=d["poster"],
                                             reply_markup=markup)
    # forward to main channel
    await ctx.bot.forward_message(chat_id=MAIN_CHANNEL, from_chat_id=msg.chat_id, message_id=msg.message_id)
    await u.effective_chat.send_message("✅ Movie posted successfully!")

# — /cancel
async def cmd_cancel(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Only admins can use this command.")
        return
    uid = str(u.effective_user.id)
    if uid in pending:
        pending.pop(uid, None)
        save(PENDING, pending)
        await u.message.reply_text("✅ Upload cancelled.")
    else:
        await u.message.reply_text("❌ No active upload.")

# — /start for users
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
    except:
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
        for f in d["files"]:
            await ctx.bot.send_document(u.effective_chat.id, f["file_id"])
        if d.get("alt_link"):
            await ctx.bot.send_message(chat_id=u.effective_chat.id, text=f"📥 Alternate Link: {d['alt_link']}")

# — Retry
async def on_retry(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    code = u.callback_query.data.split("_")[1]
    ctx.args = [code]
    await cmd_start(u, ctx)

# — Language selection
async def on_getlang(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    _, code, lang = u.callback_query.data.split("_", 2)
    if code not in movies or lang not in movies[code]["files"]:
        return await u.callback_query.edit_message_text("❌ Invalid selection.")
    for f in movies[code]["files"][lang]:
        await ctx.bot.send_document(u.effective_chat.id, f["file_id"])
    if movies[code].get("alt_link"):
        await ctx.bot.send_message(chat_id=u.effective_chat.id,
                                   text=f"📥 Alternate Link: {movies[code]['alt_link']}" )

# — Admin delete/status
async def cmd_delete(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Only admins can use this command.")
        return
    c = ctx.args and ctx.args[0].lower()
    if not c or c not in movies:
        return await u.message.reply_text("❌ Usage: /delete <code>")
    del movies[c]
    save(MOVIES, movies)
    await u.message.reply_text(f"✅ Deleted `{c}`")

async def cmd_status(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id == ADMIN_ID:
        await u.message.reply_text("✅ Bot is alive.")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Admin commands
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("status", cmd_status))

    # User start
    app.add_handler(CommandHandler("start", cmd_start))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(on_type, pattern="^t_"))
    app.add_handler(CallbackQueryHandler(on_alt_btn, pattern="^(alt_|cancel|poster_retry)"))
    app.add_handler(CallbackQueryHandler(on_retry, pattern="^retry_"))
    app.add_handler(CallbackQueryHandler(on_getlang, pattern="^getlang_"))

    # Alt link input
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Regex(r"^https?://"), on_alt_input))

    # File uploads (docs/videos/animations)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & (
            filters.Document.ALL | filters.Video.ALL | filters.Animation.ALL
        ),
        on_file_or_text
    ))

    # Poster stage (photos/texts)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & (
            filters.PHOTO | filters.TEXT | filters.FORWARDED
        ),
        on_poster
    ))

    # Code entry (admin only)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & filters.TEXT,
        on_code
    ))

    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    main()
