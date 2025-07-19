# created by Akash Kiran T 

import os, json
import asyncio
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, Document
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MAIN_CHANNEL = os.getenv("MAIN_CHANNEL")
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL")

MOVIES = "movieFiles.json"
PENDING = "pending.json"

def load(fn): return json.load(open(fn)) if os.path.exists(fn) else {}
def save(fn,data): open(fn,"w").write(json.dumps(data, indent=2))

movies = load(MOVIES)
pending = load(PENDING)

# — STEP 0: Admin Initiates Upload with /upload
async def cmd_upload(u,ctx):
    if u.effective_user.id!=ADMIN_ID: return
    kb=[[InlineKeyboardButton("🎞 Single",callback_data="t_single")],
        [InlineKeyboardButton("🌐 Multi‑Language",callback_data="t_multi")]]
    await u.message.reply_text("Choose mode:",reply_markup=InlineKeyboardMarkup(kb))

# — STEP 1: Handle Single vs Multi
async def on_type(u,ctx):
    data = pending[str(u.from_user.id)] = {"type":None,"files":{}, "stage":"files"}
    data["type"] = "single" if u.data=="t_single" else "multi"
    if data["type"]=="single": data["files"]=[]
    await u.edit_message_text("📥 Send all movie files now." if data["type"]=="single"
                              else "📥 Send as:\n<LanguageName>\n[file1]\n[file2]\n...")

    save(PENDING,pending)

# — STEP 2: Admin Sends Content
async def on_file_or_text(u,ctx):
    uid=str(u.effective_user.id)
    if u.effective_user.id!=ADMIN_ID or uid not in pending: return
    data=pending[uid]
    if data["stage"]!="files": return

    msg=u.message
    if data["type"]=="multi":
        if msg.text:
            data.setdefault("files",{})[msg.text.strip()]=[]
            data["current"]=msg.text.strip()
        elif msg.document:
            if "current" not in data: return await msg.reply_text("⚠️ Send a language name first.")
            data["files"][data["current"]].append({"file_id":msg.document.file_id})
    else:
        if msg.document:
            data["files"].append({"file_id":msg.document.file_id})
    save(PENDING,pending)

# — STEP 3: Poster Upload
async def on_poster(u,ctx):
    uid=str(u.effective_user.id)
    if u.effective_user.id!=ADMIN_ID or uid not in pending: return
    data=pending[uid]
    if data["stage"]!="files": return
    msg=u.message
    data["poster"]=msg.caption or msg.text or ""
    if msg.photo: data["photo"]=msg.photo[-1].file_id
    data["stage"]="code"
    save(PENDING,pending)
    await msg.reply_text("🔢 Now send a unique movie-code:")

# — STEP 4: Receive movie-code
async def on_code(u,ctx):
    uid=str(u.effective_user.id)
    if u.effective_user.id!=ADMIN_ID or uid not in pending: return
    data=pending[uid]
    if data["stage"]!="code": return
    code=u.message.text.strip().lower()
    if code in movies: return await u.message.reply_text("❌ Code already exists.")
    data["code"]=code
    data["stage"]="altlink"
    save(PENDING,pending)
    kb=[[InlineKeyboardButton("➕ Add Link",callback_data="alt_provide")],
        [InlineKeyboardButton("⏭ Skip",callback_data="alt_skip")]]
    await u.message.reply_text("🎥 Alternate link?",reply_markup=InlineKeyboardMarkup(kb))

# — STEP 5: Optional Alt Link Buttons
async def on_alt_btn(u,ctx):
    uid=str(u.from_user.id)
    data=pending[uid]
    await u.answer()
    if u.data=="alt_skip":
        data["alt_link"]=None
        await finalize(u,ctx)
    else:
        data["stage"]="altwait"
        await u.edit_message_text("🔗 Send alternate link now.")
    save(PENDING,pending)

# — STEP 6: Receive Alt Link Input
async def on_alt_input(u,ctx):
    uid=str(u.effective_user.id)
    if u.effective_user.id!=ADMIN_ID or uid not in pending: return
    data=pending[uid]
    if data["stage"]!="altwait": return
    data["alt_link"]=u.message.text.strip()
    await finalize(u,ctx); save(PENDING,pending)

# — Create Poster + Buttons + Auto-post
async def finalize(u,ctx):
    uid=str(u.effective_user.id)
    d=pending.pop(uid)
    movies[d["code"]]=d
    save(MOVIES,movies)
    save(PENDING,pending)

    kb=[[InlineKeyboardButton("▶️ Get Movie",url=f"https://t.me/{ctx.bot.username}?start={d['code']}")]]
    if d.get("alt_link"): kb.append([InlineKeyboardButton("📥 If bot not responding...",url=d["alt_link"])])

    markup=InlineKeyboardMarkup(kb)
    if d.get("photo"):
        msg=await ctx.bot.send_photo(chat_id=u.effective_chat.id,photo=d["photo"],caption=d["poster"],reply_markup=markup)
    else:
        msg=await ctx.bot.send_message(chat_id=u.effective_chat.id,text=d["poster"],reply_markup=markup)
    # Auto post to main channel
    await ctx.bot.forward_message(chat_id=MAIN_CH,from_chat_id=msg.chat_id,message_id=msg.message_id)

# — /start for Users (with join-check)
async def cmd_start(u,ctx):
    usr=u.effective_user.id; args=ctx.args or []
    if not args: return await u.message.reply_text("❌ Usage: /start <moviecode>")
    code=args[0].lower()
    try:
        mem=await ctx.bot.get_chat_member(MAIN_CH,usr)
        if mem.status not in ["member","administrator","creator"]: raise
    except:
        kb=[[InlineKeyboardButton("🎬 Join Channel",url=f"https://t.me/{MAIN_CH.lstrip('@')}")],
            [InlineKeyboardButton("🔄 Retry",callback_data=f"retry_{code}")]]
        return await u.message.reply_text("Join our channel first.",reply_markup=InlineKeyboardMarkup(kb))
    if code not in movies: return await u.message.reply_text("❌ Invalid code.")
    d=movies[code]

    if d["type"]=="multi":
        kb=[[InlineKeyboardButton(lang,callback_data=f"getlang_{code}_{lang}")] for lang in d["files"]]
        return await u.message.reply_text("Choose language:",reply_markup=InlineKeyboardMarkup(kb))
    else:
        for file in d["files"]:
            await ctx.bot.send_document(u.effective_chat.id, file["file_id"])
        if d.get("alt_link"):
            await ctx.bot.send_message(chat_id=u.effective_chat.id,
                text=f"📥 If bot not responding, click here: {d['alt_link']}")

# — Join Retry
async def on_retry(u,ctx):
    await u.answer()
    return await cmd_start(u,ctx)

# — Language buttons for Users
async def on_getlang(u,ctx):
    await u.answer()
    _,code,lang=u.data.split("_",2)
    for file in movies[code]["files"][lang]:
        await ctx.bot.send_document(u.message.chat.id, file["file_id"])
    if movies[code].get("alt_link"):
        await ctx.bot.send_message(chat_id=u.message.chat.id,
            text=f"📥 If bot not responding, click here: {movies[code]['alt_link']}")

# — Deletion, Status
async def cmd_delete(u,ctx):
    if u.effective_user.id!=ADMIN_ID: return
    c=ctx.args and ctx.args[0].lower()
    if not c or c not in movies: return await u.message.reply_text("❌ Usage: /delete <code>")
    del movies[c]; save(MOVIES,movies)
    await u.message.reply_text(f"✅ Deleted `{c}`")

async def cmd_status(u,ctx):
    if u.effective_user.id==ADMIN_ID:
        await u.message.reply_text("✅ Bot is alive.")


async def main():
    print("🤖 Bot starting...")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CallbackQueryHandler(on_type, pattern="^t_"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_file_or_text))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.PHOTO, on_poster))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Regex(r"^[a-zA-Z0-9_-]+$"), on_code))
    app.add_handler(CallbackQueryHandler(on_alt_btn, pattern="^alt_"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Regex(r"^https?://"), on_alt_input))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_retry, pattern="^retry_"))
    app.add_handler(CallbackQueryHandler(on_getlang, pattern="^getlang_"))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("status", cmd_status))

    await asyncio.gather(
        keep_alive(),           # Start the web server (for Render/UptimeRobot)
        app.run_polling()       # Start the Telegram bot
    )

if __name__ == "__main__":
    asyncio.run(main())
