# bot.py
from keep_alive import keep_alive
keep_alive()

from custom_caption import generate_custom_caption

import os, json
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# — Load config & data —
load_dotenv()
with open("config.json") as f: cfg = json.load(f)
ADMIN_ID    = cfg["admin_id"]
MAIN_CHANNEL= cfg["main_channel"]
MOVIES_FILE = "movieFiles.json"
PENDING_FILE= "pending.json"

def load(fn):
    return json.load(open(fn)) if os.path.exists(fn) else {}

def save(fn, data):
    with open(fn,"w") as f:
        json.dump(data, f, indent=2)

movies  = load(MOVIES_FILE)
pending = load(PENDING_FILE)

# — Admin: /upload starts an upload session —
async def cmd_upload(u,ctx):
    if u.effective_user.id != ADMIN_ID: return
    pending[str(ADMIN_ID)] = {
        "stage":"files",
        "items":[]   # will hold {"type":"text","text":...} and {"type":"file","file_id":...}
    }
    save(PENDING_FILE, pending)
    await u.message.reply_text("📥 Now forward your sections (text) and files in the order you want them delivered.")

# — Admin: receive forwarded text or files —
async def on_admin_item(u,ctx):
    if u.effective_user.id != ADMIN_ID: return
    d = pending.get(str(ADMIN_ID))
    if not d or d["stage"]!="files":
        return

    msg = u.message
    if msg.text and not msg.document:
        # a section header
        d["items"].append({"type":"text","text": msg.text.strip()})
    elif msg.document:
        # a file
        d["items"].append({"type":"file","file_id": msg.document.file_id})
    else:
        # ignore other things
        return
    save(PENDING_FILE, pending)

# — Admin: poster step —
async def on_poster(u,ctx):
    if u.effective_user.id != ADMIN_ID: return
    d = pending.get(str(ADMIN_ID))
    if not d or d["stage"]!="files": return

    msg = u.message
    d["poster"] = msg.caption or msg.text or ""
    if msg.photo:
        d["photo"] = msg.photo[-1].file_id

    d["stage"] = "code"
    save(PENDING_FILE, pending)
    await u.message.reply_text("🔢 Now send the unique movie code (e.g. `kgf2`).")

# — Admin: code entry finishes upload —
async def on_code(u,ctx):
    if u.effective_user.id != ADMIN_ID: return
    d = pending.get(str(ADMIN_ID))
    if not d or d["stage"]!="code": return

    code = u.message.text.strip().lower()
    if not code or code in movies:
        return await u.message.reply_text("❌ Invalid or duplicate code, try another.")

    # Commit to movies
    movies[code] = {
        "poster": d.get("poster",""),
        "photo":  d.get("photo"),
        "items":  d["items"]
    }
    save(MOVIES_FILE, movies)
    del pending[str(ADMIN_ID)]
    save(PENDING_FILE, pending)

    # Send poster + start button to admin
    kb = [[ InlineKeyboardButton("▶️ Forward Preview", 
            callback_data=f"preview_{code}") ]]
    markup = InlineKeyboardMarkup(kb)
    if movies[code].get("photo"):
        await ctx.bot.send_photo(
            chat_id=u.effective_chat.id,
            photo=movies[code]["photo"],
            caption=movies[code]["poster"],
            reply_markup=markup
        )
    else:
        await ctx.bot.send_message(
            chat_id=u.effective_chat.id,
            text=movies[code]["poster"],
            reply_markup=markup
        )
    await u.message.reply_text("✅ Movie added! Forward the above preview to your channel/group.")

# — Admin: preview callback simply forwards preview message —
async def on_preview(u,ctx):
    await u.answer()
    code = u.data.split("_",1)[1]
    msg = await ctx.bot.send_message(
        chat_id=u.message.chat_id,
        text="📤 Preview — forward this message to your target group."
    )
    # nothing more

# — User: /start handler shows section buttons —
async def start(u, ctx):
    user = u.effective_user.id
    args = ctx.args or []
    if not args:
        return await u.message.reply_text("❌ Usage: /start <moviecode>")

    code = args[0].lower()
    # … join‑check omitted for brevity …

    mdata = movies.get(code)
    if not mdata:
        return await u.message.reply_text("❌ Invalid movie code.")

    items = mdata["items"]

    # 1) Extract all the section indices
    section_indices = [i for i, it in enumerate(items) if it["type"] == "text"]

    # 2) If no sections, just send all files straight away:
    if not section_indices:
        files = [it["file_id"] for it in items if it["type"] == "file"]
        if not files:
            return await u.message.reply_text("ℹ️ No files available.")
        for fid in files:
            await ctx.bot.send_document(u.effective_chat.id, fid)
        return

    # 3) Otherwise, show poster + section buttons as before
    if mdata.get("photo"):
        await ctx.bot.send_photo(u.effective_chat.id,
            photo=mdata["photo"],
            caption=mdata["poster"]
        )
    else:
        await ctx.bot.send_message(u.effective_chat.id, text=mdata["poster"])

    kb = [[ InlineKeyboardButton(
        items[i]["text"][:30],
        callback_data=f"pick_{code}_{i}"
    )] for i in section_indices]

    await u.message.reply_text(
        "📂 Select a section to receive its files:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def retry_join(u,ctx):
    await u.answer()
    return await start(u,ctx)

# — User: section button handler —
async def on_pick(u,ctx):
    await u.answer()
    data = u.data.split("_",2)
    code, idx = data[1], int(data[2])
    mdata = movies.get(code)
    if not mdata:
        return await u.message.reply_text("❌ Invalid code.")
    items = mdata["items"]
    # collect all file_ids from idx+1 until next text or end
    files_to_send = []
    for it in items[idx+1:]:
        if it["type"]=="text":
            break
        files_to_send.append(it["file_id"])

    if not files_to_send:
        return await u.message.reply_text("ℹ️ No files in this section.")
    # send each
    for fid in files_to_send:
        await ctx.bot.send_document(u.effective_chat.id, fid)

# — Status/Delete —
async def status(u,ctx):
    if u.effective_user.id==ADMIN_ID:
        await u.message.reply_text("✅ Bot is alive.")

async def delete_movie(u,ctx):
    if u.effective_user.id != ADMIN_ID: return
    args = ctx.args or []
    if not args or args[0].lower() not in movies:
        return await u.message.reply_text("❌ Usage: /delete <code>")
    code = args[0].lower()
    del movies[code]
    save(MOVIES_FILE, movies)
    await u.message.reply_text(f"🗑 Deleted `{code}`", parse_mode=ParseMode.MARKDOWN)

# — Entrypoint —
def main():
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    # Admin flow
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND, on_admin_item))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.PHOTO, on_poster))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.Regex(r"^[A-Za-z0-9_-]+$"), on_code))
    app.add_handler(CallbackQueryHandler(on_preview, pattern="^preview_"))

    # User flow
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(retry_join, pattern="^retry_"))
    app.add_handler(CallbackQueryHandler(on_pick,   pattern="^pick_"))

    # Utility
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("delete", delete_movie))

    print("✅ Bot is running...")
    app.run_polling()

if __name__=="__main__":
    main()
