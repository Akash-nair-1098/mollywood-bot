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

    try:
        save(PENDING, pending)
        logger.info(f"Started upload for user {uid}, type: {data['type']}")
    except Exception as e:
        logger.error(f"Failed to save pending.json in on_type: {e}")
        await u.callback_query.edit_message_text("❌ Error saving state. Please try /upload again.")
        return

    message = (
        "📥 Send all movie files now. When finished, use /done." if data["type"] == "single"
        else "📥 Send language names followed by files for each language. When finished, use /done."
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
            language = msg.text.strip()
            data["files"][language] = data["files"].get(language, [])
            data["current"] = language
            await msg.reply_text(f"✅ Language '{language}' set. Now send files for this language.")
        elif msg.document:
            if "current" not in data: 
                await msg.reply_text("⚠️ Send a language name first.")
                return
            data["files"][data["current"]].append({"file_id": msg.document.file_id})
            await msg.reply_text(f"✅ File received for {data['current']}. Send more files for this language, switch to another language, or use /done when finished.")
        else:
            await msg.reply_text("⚠️ Please send a language name or a file.")
            return
    else:
        if msg.document:
            data["files"].append({"file_id": msg.document.file_id})
            await msg.reply_text("✅ File received. Send more files or use /done when finished.")
        else:
            await msg.reply_text("⚠️ Please send a file.")
            return

    try:
        save(PENDING, pending)
        logger.info(f"Saved file for user {uid}, files: {data['files']}")
    except Exception as e:
        logger.error(f"Failed to save pending.json in on_file_or_text: {e}")
        await msg.reply_text("❌ Error saving files. Please try /upload again.")
        return

# — STEP 2.5: Admin Signals Completion with /done
async def cmd_done(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Only admins can use this command.")
        return
    uid = str(u.effective_user.id)
    if uid not in pending or pending[uid]["stage"] != "files":
        await u.message.reply_text("❌ No active file upload in progress.")
        return
    data = pending[uid]
    if (data["type"] == "single" and len(data["files"]) == 0) or \
       (data["type"] == "multi" and not any(data["files"].values())):
        await u.message.reply_text("❌ No files received yet. Send files first.")
        return
    data["stage"] = "poster"
    try:
        save(PENDING, pending)
        logger.info(f"Transitioned to poster stage for user {uid} via /done")
    except Exception as e:
        logger.error(f"Failed to save pending.json in cmd_done: {e}")
        await u.message.reply_text("❌ Error saving state. Please try /upload again.")
        return
    await u.message.reply_text("✅ Files received. Now send or forward the movie poster (photo with optional caption or text only).")

# — STEP 3: Poster Upload or Forward
async def on_poster(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        logger.warning(f"Invalid poster attempt by user {uid}")
        await u.message.reply_text("❌ Unauthorized or no active upload.")
        return
    data = pending[uid]
    if data["stage"] != "poster":
        logger.warning(f"Wrong stage for poster: {data['stage']}")
        await u.message.reply_text("❌ Wrong stage. Use /upload to start over or /cancel to reset.")
        return
    msg = u.message

    try:
        if msg.forward_from_message_id:  # Handle forwarded message
            data["poster"] = msg.caption or msg.text or ""
            data["photo"] = msg.photo[-1].file_id if msg.photo else None
            data["forwarded_message_id"] = msg.message_id
            data["forwarded_chat_id"] = msg.chat_id
            data["poster_mode"] = "forward"
            logger.info(f"Processed forwarded poster for user {uid}")
        elif msg.photo or msg.text:  # Handle direct photo or text
            data["poster"] = msg.caption or msg.text or ""
            data["photo"] = msg.photo[-1].file_id if msg.photo else None
            data["poster_mode"] = "send"
            logger.info(f"Processed direct poster for user {uid}")
        else:
            logger.warning(f"Invalid poster input from user {uid}: {msg}")
            await msg.reply_text("⚠️ Please send a photo with optional caption, text only, or forward a message.")
            return

        data["stage"] = "code"
        try:
            save(PENDING, pending)
            logger.info(f"Saved poster for user {uid}, transitioning to code stage")
        except Exception as e:
            logger.error(f"Failed to save pending.json in on_poster: {e}")
            await msg.reply_text("❌ Error saving poster. Please try /upload again.")
            return
        await msg.reply_text("🔢 Now send a unique movie-code (alphanumeric, e.g., Unitedkingdomofkerala2025):")
    except Exception as e:
        logger.error(f"Error in on_poster: {e}")
        kb = [[InlineKeyboardButton("🔄 Retry Poster", callback_data="poster_retry")],
              [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        await msg.reply_text(
            "❌ Error processing poster. Please retry or cancel.",
            reply_markup=InlineKeyboardMarkup(kb)
        )

# — STEP 4: Receive Movie Code
async def on_code(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        logger.warning(f"Invalid code attempt by user {uid}")
        await u.message.reply_text("❌ Unauthorized or no active upload.")
        return
    data = pending[uid]
    if data["stage"] != "code":
        logger.warning(f"Wrong stage for code: {data['stage']}")
        await u.message.reply_text("❌ Wrong stage. Use /upload to start over or /cancel to reset.")
        return
    code = u.message.text.strip().lower()
    logger.info(f"Received movie code: '{code}' from user {uid}")
    
    if not code:
        logger.warning(f"Empty code received from user {uid}")
        await u.message.reply_text("❌ Code cannot be empty. Try again (e.g., Unitedkingdomofkerala2025).")
        return
    if not code.isalnum():
        logger.warning(f"Invalid code format: '{code}'")
        await u.message.reply_text("❌ Code must be alphanumeric (letters and numbers only, e.g., Unitedkingdomofkerala2025). Try again.")
        return
    if code in movies:
        logger.warning(f"Duplicate code: '{code}'")
        await u.message.reply_text("❌ Code already exists. Choose a different code.")
        return
    
    data["code"] = code
    data["stage"] = "altlink"
    try:
        save(PENDING, pending)
        logger.info(f"Saved movie code: '{code}', transitioning to altlink stage for user {uid}")
    except Exception as e:
        logger.error(f"Failed to save pending.json in on_code: {e}")
        await u.message.reply_text("❌ Error saving code. Please try /upload again.")
        return
    
    kb = [[InlineKeyboardButton("➕ Add Alternate Link", callback_data="alt_provide")],
          [InlineKeyboardButton("⏭ Skip", callback_data="alt_skip")],
          [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
    await u.message.reply_text(f"✅ Code '{code}' received. Provide an alternate link (optional):", reply_markup=InlineKeyboardMarkup(kb))

# — STEP 5: Optional Alternate Link Buttons
async def on_alt_btn(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        logger.warning(f"Invalid alt_btn attempt by user {uid}")
        await u.callback_query.edit_message_text("❌ Invalid action.")
        return
    data = pending[uid]
    if u.callback_query.data == "alt_skip":
        data["alt_link"] = None
        logger.info(f"Skipped alternate link for user {uid}")
        await finalize(u, ctx)
    elif u.callback_query.data == "alt_provide":
        data["stage"] = "altwait"
        try:
            save(PENDING, pending)
            logger.info(f"Transitioned to altwait stage for user {uid}")
        except Exception as e:
            logger.error(f"Failed to save pending.json in on_alt_btn: {e}")
            await u.callback_query.edit_message_text("❌ Error saving state. Please try /upload again.")
            return
        await u.callback_query.edit_message_text("🔗 Send the alternate link now (must start with http:// or https://):")
    elif u.callback_query.data == "cancel":
        del pending[uid]
        try:
            save(PENDING, pending)
            logger.info(f"Cancelled upload for user {uid}")
        except Exception as e:
            logger.error(f"Failed to save pending.json in on_alt_btn cancel: {e}")
        await u.callback_query.edit_message_text("✅ Upload cancelled.")
    elif u.callback_query.data == "poster_retry":
        data["stage"] = "poster"
        try:
            save(PENDING, pending)
            logger.info(f"Retrying poster for user {uid}")
        except Exception as e:
            logger.error(f"Failed to save pending.json in on_alt'}</xaiArtifact_btn poster_retry: {e}")
            await u.callback_query.edit_message_text("❌ Error saving state. Please try /upload again.")
            return
        await u.callback_query.edit_message_text("✅ Retrying. Send or forward the movie poster (photo with optional caption or text only).")
    try:
        save(PENDING, pending)
    except Exception as e:
        logger.error(f"Failed to save pending.json in on_alt_btn: {e}")
        await u.callback_query.edit_message_text("❌ Error saving state. Please try /upload again.")

# — STEP 6: Receive Alternate Link Input
async def on_alt_input(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        logger.warning(f"Invalid alt_input attempt by user {uid}")
        await u.message.reply_text("❌ Unauthorized or no active upload.")
        return
    data = pending[uid]
    if data["stage"] != "altwait":
        logger.warning(f"Wrong stage for alt_input: {data['stage']}")
        await u.message.reply_text("❌ Wrong stage. Use /upload to start over or /cancel to reset.")
        return
    link = u.message.text.strip()
    if not link.startswith(("http://", "https://")):
        logger.warning(f"Invalid URL: '{link}' from user {uid}")
        await u.message.reply_text("⚠️ Please send a valid URL starting with http:// or https://")
        return
    data["alt_link"] = link
    logger.info(f"Received alternate link: '{link}' for user {uid}")
    try:
        save(PENDING, pending)
        logger.info(f"Saved alternate link for user {uid}")
    except Exception as e:
        logger.error(f"Failed to save pending.json in on_alt_input: {e}")
        await u.message.reply_text("❌ Error saving link. Please try /upload again.")
        return
    await finalize(u, ctx)

# — STEP 7: Finalize and Post
async def finalize(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if u.effective_user.id != ADMIN_ID or uid not in pending:
        logger.warning(f"Invalid finalize attempt by user {uid}")
        await u.effective_chat.send_message("❌ Invalid action.")
        return
    d = pending.pop(uid)
    if not d.get("code"):
        logger.error(f"No code found in pending data for user {uid}")
        await u.effective_chat.send_message("❌ No movie code found. Please try /upload again.")
        return
    movies[d["code"]] = d
    try:
        save(MOVIES, movies)
        save(PENDING, pending)
        logger.info(f"Saved movie data for code: '{d['code']}' for user {uid}")
    except Exception as e:
        logger.error(f"Failed to save movies.json or pending.json in finalize: {e}")
        await u.effective_chat.send_message("❌ Error saving movie data. Please try /upload again.")
        return

    kb = [[InlineKeyboardButton("▶️ Get Movie", url=f"https://t.me/{ctx.bot.username}?start={d['code']}")]]
    if d.get("alt_link"):
        kb.append([InlineKeyboardButton("📥 Alternate Link", url=d["alt_link"])])

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
            logger.info(f"Forwarded poster to admin chat for user {uid}")
        else:
            if d.get("photo"):
                msg = await ctx.bot.send_photo(
                    chat_id=u.effective_chat.id,
                    photo=d["photo"],
                    caption=d["poster"],
                    reply_markup=markup
                )
                logger.info(f"Sent photo poster to admin chat for user {uid}")
            else:
                msg = await ctx.bot.send_message(
                    chat_id=u.effective_chat.id,
                    text=d["poster"],
                    reply_markup=markup
                )
                logger.info(f"Sent text poster to admin chat for user {uid}")
        # Forward to main channel
        await ctx.bot.forward_message(
            chat_id=MAIN_CHANNEL,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id
        )
        logger.info(f"Forwarded poster to main channel for user {uid}, code: '{d['code']}'")
        await u.effective_chat.send_message("✅ Movie posted successfully to the main channel!")
    except Exception as e:
        logger.error(f"Error in finalize: {e}")
        movies.pop(d["code"], None)  # Revert movie save on failure
        try:
            save(MOVIES, movies)
        except Exception as e:
            logger.error(f"Failed to save movies.json after revert in finalize: {e}")
        await u.effective_chat.send_message(
            "❌ Failed to post movie. Use /upload to try again or /cancel to reset."
        )

# — /cancel Command
async def cmd_cancel(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        await u.message.reply_text("❌ Only admins can use this command.")
        return
    uid = str(u.effective_user.id)
    if uid in pending:
        del pending[uid]
        try:
            save(PENDING, pending)
            logger.info(f"Cancelled upload for user {uid}")
        except Exception as e:
            logger.error(f"Failed to save pending.json in cmd_cancel: {e}")
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
                text=f"📥 Alternate Link: {d['alt_link']}"
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
            text=f"📥 Alternate Link: {movies[code]['alt_link']}"
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
    try:
        save(MOVIES, movies)
        logger.info(f"Deleted movie code: '{c}'")
    except Exception as e:
        logger.error(f"Failed to save movies.json in cmd_delete: {e}")
    await u.message.reply_text(f"✅ Deleted `{c}`")

async def cmd_status(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id == ADMIN_ID:
        await u.message.reply_text("✅ Bot is alive.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Command handlers (prioritized first)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("done", cmd_done))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(on_type, pattern="^t_"))
    app.add_handler(CallbackQueryHandler(on_alt_btn, pattern="^(alt_|cancel|poster_retry)"))
    app.add_handler(CallbackQueryHandler(on_retry, pattern="^retry_"))
    app.add_handler(CallbackQueryHandler(on_getlang, pattern="^getlang_"))

    # Message handlers (ordered and filtered carefully)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Regex(r"^https?://"), on_alt_input))

    # Admin-only code entry (avoid catching regular user input)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        on_code
    ))

    # Poster and file upload
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.TEXT | filters.FORWARDED), on_poster))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND & (filters.Document.ALL | filters.TEXT), on_file_or_text))

    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    main()