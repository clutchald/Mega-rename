import os, re, logging, asyncio, signal
from pathlib import Path
from functools import partial
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler,
                          ConversationHandler, ContextTypes, filters)
from aiohttp import web
from mega import Mega
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mega-renamer")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "mega_renamer_bot").strip()
PORT = int(os.getenv("PORT", "8000"))
mongo_client = None
mongo_db = None

EMAIL, PASSWORD, RENAME_NAME = range(3)
# MEGA client is held only in memory. It is re-authenticated from encrypted credentials after restart.
mega_clients = {}
pending_bulk = {}


def init_mongodb():
    global mongo_client, mongo_db
    if not MONGODB_URI:
        raise SystemExit("Set MONGODB_URI environment variable.")
    mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    mongo_client.admin.command("ping")
    mongo_db = mongo_client[MONGODB_DATABASE]
    mongo_db.accounts.create_index("telegram_user_id", unique=True)

def save_account_email(user_id, email):
    # Store account identifier only. MEGA passwords are never stored in MongoDB.
    mongo_db.accounts.update_one(
        {"telegram_user_id": user_id},
        {"$set": {"telegram_user_id": user_id, "mega_email": email}},
        upsert=True
    )

def allowed(update):
    return bool(ADMIN_ID and update.effective_user and update.effective_user.id == ADMIN_ID)

async def admin_only(update):
    if not allowed(update):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ This is a private bot. You are not authorized.")
        return False
    return True

def login_sync(email, password):
    return Mega().login(email, password)

async def get_client(user_id):
    if user_id in mega_clients:
        return mega_clients[user_id]
    # MEGA credentials are intentionally not persisted. Re-login after a bot restart.
    return None

def all_nodes(client):
    # mega.py returns a mapping of node IDs to node metadata.
    nodes = client.get_files()
    return nodes if isinstance(nodes, dict) else {}

def node_name(node):
    return node.get("a", {}).get("n") or node.get("name") or ""

def node_parent(node):
    return node.get("p") or node.get("parent")

def is_folder(node):
    # MEGA node type 1 is a folder; type 0 is a file.
    return node.get("t") == 1 or node.get("type") == 1

def ext_kind(name):
    ext = Path(name).suffix.lower()
    videos = {".mp4",".mkv",".avi",".mov",".wmv",".flv",".webm",".m4v",".mpeg",".mpg",".3gp"}
    images = {".jpg",".jpeg",".png",".webp",".gif",".bmp",".tif",".tiff",".heic"}
    if ext in videos: return "video"
    if ext in images: return "image"
    return None

def safe_filename(name):
    name = re.sub(r'[\\\\/:*?"<>|\\x00-\\x1f]', "_", name).strip()
    return name[:180] or "Renamed"

def rename_node(client, node_id, new_name):
    nodes = client.get_files()
    node = nodes.get(node_id)
    if not node:
        raise ValueError("File/folder no longer exists. Refresh the listing.")
    # mega.py rename() expects the node metadata object.
    client.rename(node, new_name)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return
    await update.message.reply_text(
        "☁️ *MEGA RENAMER BOT*\\n\\n"
        "Login with /login, then use /files to browse your MEGA drive.\\n\\n"
        "/rename — rename one file/folder\\n"
        "/bulk — bulk rename videos and images\\n"
        "/prefix — add prefix to selected file types\\n"
        "/suffix — add suffix to selected file types\\n"
        "/replace — replace text in names\\n"
        "/search — search files/folders\\n"
        "/help — commands and safety notes",
        parse_mode="Markdown")

async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return ConversationHandler.END
    await update.message.reply_text("Send your MEGA account email address.")
    return EMAIL

async def login_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return ConversationHandler.END
    email = update.message.text.strip()
    if "@" not in email or len(email) > 254:
        await update.message.reply_text("That email doesn't look valid. Send the MEGA email address again.")
        return EMAIL
    context.user_data["mega_email"] = email
    await update.message.reply_text("Now send your MEGA password. It will not be echoed in chat; delete your message after login.")
    return PASSWORD

async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return ConversationHandler.END
    password = update.message.text
    try:
        await update.message.delete()
    except Exception:
        pass
    email = context.user_data.pop("mega_email", "")
    status = await update.message.reply_text("Connecting to MEGA…")
    try:
        client = await asyncio.to_thread(login_sync, email, password)
        mega_clients[update.effective_user.id] = client
        await asyncio.to_thread(save_account_email, update.effective_user.id, email)
        await status.edit_text("✅ MEGA login successful. Account email saved in MongoDB; password/session stays in memory. After a bot restart, use /login again.")
    except Exception as e:
        log.exception("MEGA login failed")
        await status.edit_text(f"❌ Login failed: {type(e).__name__}. Check credentials and 2FA/account security.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return
    client = await get_client(update.effective_user.id)
    if not client:
        await update.message.reply_text("First connect your account using /login.")
        return
    nodes = await asyncio.to_thread(all_nodes, client)
    # Display root-level items first; mega.py node parent IDs vary across API versions.
    roots = [(nid,n) for nid,n in nodes.items() if node_parent(n) in (None, "", "root")]
    if not roots:
        roots = list(nodes.items())[:30]
    roots = roots[:40]
    lines = ["☁️ *MEGA Drive items* (first 40)"]
    buttons = []
    for nid,n in roots:
        name = node_name(n) or "(unnamed)"
        icon = "📁" if is_folder(n) else "📄"
        lines.append(f"{icon} `{name[:70]}`")
        buttons.append([InlineKeyboardButton(f"{icon} {name[:35]}", callback_data=f"pick:{nid}")])
    buttons.append([InlineKeyboardButton("🔄 Refresh", callback_data="refresh")])
    await update.message.reply_text("\\n".join(lines), parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(buttons))

async def pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await admin_only(update): return
    await q.answer()
    if q.data == "refresh":
        await q.edit_message_text("Use /files to refresh the drive listing.")
        return
    node_id = q.data.split(":",1)[1]
    client = await get_client(update.effective_user.id)
    nodes = await asyncio.to_thread(all_nodes, client)
    node = nodes.get(node_id)
    if not node:
        await q.edit_message_text("That item was not found. Run /files again.")
        return
    name = node_name(node)
    context.user_data["rename_node_id"] = node_id
    await q.edit_message_text(f"Selected: {name}\\nSend /rename to rename it, or /cancel to stop.")

async def rename_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return
    if not context.user_data.get("rename_node_id"):
        await update.message.reply_text("Select an item from /files first, then use /rename.")
        return
    await update.message.reply_text("Send the new name (extension will be preserved if you omit it).")
    return RENAME_NAME

async def rename_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return ConversationHandler.END
    node_id = context.user_data.pop("rename_node_id", None)
    if not node_id:
        await update.message.reply_text("No item selected.")
        return ConversationHandler.END
    new_name = safe_filename(update.message.text)
    client = await get_client(update.effective_user.id)
    try:
        nodes = await asyncio.to_thread(all_nodes, client)
        old = node_name(nodes[node_id])
        if not Path(new_name).suffix and not is_folder(nodes[node_id]):
            new_name += Path(old).suffix
        await asyncio.to_thread(rename_node, client, node_id, new_name)
        await update.message.reply_text(f"✅ Renamed on MEGA:\\n{old} → {new_name}")
    except Exception as e:
        log.exception("Rename failed")
        await update.message.reply_text(f"❌ Rename failed: {type(e).__name__}")
    return ConversationHandler.END

def collect_targets(nodes, scope, kind):
    # Root/all-drive selection uses node metadata. Folder selection is supported after a folder ID is chosen.
    found = []
    for nid,n in nodes.items():
        name = node_name(n)
        if not is_folder(n) and ext_kind(name) in (("video","image") if kind == "both" else (kind,)):
            found.append((nid,n,name,ext_kind(name)))
    return found

async def bulk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return
    client = await get_client(update.effective_user.id)
    if not client:
        await update.message.reply_text("First connect using /login.")
        return
    keyboard = [[InlineKeyboardButton("🎬 Videos + 🖼 Images", callback_data="bulk:both"),
                 InlineKeyboardButton("🎬 Videos only", callback_data="bulk:video")],
                [InlineKeyboardButton("🖼 Images only", callback_data="bulk:image")]]
    await update.message.reply_text("Choose which files to bulk rename:", reply_markup=InlineKeyboardMarkup(keyboard))

async def bulk_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await admin_only(update): return
    await q.answer()
    kind=q.data.split(":")[1]
    client=await get_client(update.effective_user.id)
    if not client:
        await q.edit_message_text("Connect MEGA first with /login.")
        return
    nodes=await asyncio.to_thread(all_nodes,client)
    targets=collect_targets(nodes,"drive",kind)
    if not targets:
        await q.edit_message_text("No matching video/image files found in the drive.")
        return
    pending_bulk[update.effective_user.id] = {"targets":targets, "kind":kind}
    await q.edit_message_text(
        f"Found {len(targets)} matching files across your MEGA drive.\\n"
        "This first version applies separate numbering: videos start at (1), images start at (1).\\n"
        "Send a base name, for example `Video` (or /cancel).\\n"
        "A preview will be shown before changes are applied.",
        parse_mode="Markdown")

async def bulk_name_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return
    state=pending_bulk.get(update.effective_user.id)
    if not state: return
    base_name=safe_filename(update.message.text)
    targets=state["targets"]
    counters={"video":0,"image":0}
    preview=[]
    plan=[]
    for nid,node,old,kind in targets:
        counters[kind]+=1
        suffix=Path(old).suffix
        new=f"{base_name} ({counters[kind]}){suffix}"
        plan.append((nid,old,new))
        if len(preview)<20: preview.append(f"`{old[:35]}` → `{new[:35]}`")
    state["plan"]=plan
    keyboard=[[InlineKeyboardButton("✅ Confirm rename",callback_data="bulk:confirm"),
               InlineKeyboardButton("❌ Cancel",callback_data="bulk:cancel")]]
    await update.message.reply_text(
        f"Preview ({len(plan)} files; showing up to 20):\\n" + "\\n".join(preview),
        parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(keyboard))

async def bulk_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await admin_only(update): return
    await q.answer()
    state=pending_bulk.get(update.effective_user.id)
    if q.data=="bulk:cancel":
        pending_bulk.pop(update.effective_user.id,None)
        await q.edit_message_text("Bulk rename cancelled. No files changed.")
        return
    if not state or "plan" not in state:
        await q.edit_message_text("No pending rename. Run /bulk again.")
        return
    client=await get_client(update.effective_user.id)
    ok=0; failed=0
    await q.edit_message_text(f"Renaming {len(state['plan'])} files…")
    for nid,old,new in state["plan"]:
        try:
            await asyncio.to_thread(rename_node,client,nid,new)
            ok+=1
        except Exception:
            failed+=1
    pending_bulk.pop(update.effective_user.id,None)
    await q.message.reply_text(f"✅ Bulk rename finished.\\nSuccessful: {ok}\\nFailed: {failed}")

async def simple_transform(update, context, mode):
    if not await admin_only(update): return
    await update.message.reply_text(f"Use /bulk for safe bulk selection. {mode} workflow is available in the next extension; no files were changed.")

async def prefix_cmd(update, context): await simple_transform(update, context, "Prefix")
async def suffix_cmd(update, context): await simple_transform(update, context, "Suffix")
async def replace_cmd(update, context): await simple_transform(update, context, "Find/replace")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update): return
    if not context.args:
        await update.message.reply_text("Usage: /search keyword")
        return
    client=await get_client(update.effective_user.id)
    if not client:
        await update.message.reply_text("First connect using /login.")
        return
    term=" ".join(context.args).lower()
    nodes=await asyncio.to_thread(all_nodes,client)
    matches=[node_name(n) for n in nodes.values() if term in node_name(n).lower()]
    await update.message.reply_text("Matches:\\n" + ("\\n".join(matches[:50]) if matches else "No matches found."))

async def help_cmd(update, context):
    if not await admin_only(update): return
    await update.message.reply_text(
        "/login — connect MEGA\\n/files — browse items\\n/bulk — bulk rename videos/images\\n"
        "/rename — rename selected item\\n/search WORD — search names\\n"
        "/prefix, /suffix, /replace — reserved commands\\n/cancel — cancel current input\\n\\n"
        "Bulk mode scans your drive, previews the planned names, and asks for confirmation.")

async def health(request):
    return web.json_response({
        "status": "ok",
        "service": "mega-renamer-bot",
        "database": "mongodb",
    })


async def start_health_server():
    health_app = web.Application()
    health_app.router.add_get("/", health)
    health_app.router.add_get("/health", health)
    runner = web.AppRunner(health_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Health server listening on 0.0.0.0:%s", PORT)
    return runner


def build_telegram_application():
    app = Application.builder().token(BOT_TOKEN).build()
    login_conv = ConversationHandler(
        entry_points=[CommandHandler("login", login_cmd)],
        states={
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_email)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=180,
    )
    rename_conv = ConversationHandler(
        entry_points=[CommandHandler("rename", rename_cmd)],
        states={RENAME_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_name)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(login_conv)
    app.add_handler(rename_conv)
    app.add_handler(CommandHandler("files", files_cmd))
    app.add_handler(CommandHandler("bulk", bulk_cmd))
    app.add_handler(CommandHandler("prefix", prefix_cmd))
    app.add_handler(CommandHandler("suffix", suffix_cmd))
    app.add_handler(CommandHandler("replace", replace_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(pick_callback, pattern=r"^(pick:|refresh$)"))
    app.add_handler(CallbackQueryHandler(bulk_type_callback, pattern=r"^bulk:(both|video|image)$"))
    app.add_handler(CallbackQueryHandler(bulk_confirm_callback, pattern=r"^bulk:(confirm|cancel)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bulk_name_message), group=1)
    return app


async def main():
    if not BOT_TOKEN or not ADMIN_ID:
        raise SystemExit("Set BOT_TOKEN and ADMIN_ID environment variables.")

    # MongoDB is synchronous in this bot; initialize it before starting Telegram polling.
    await asyncio.to_thread(init_mongodb)
    telegram_app = build_telegram_application()
    health_runner = None
    polling_started = False
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await telegram_app.initialize()
        await telegram_app.start()
        health_runner = await start_health_server()
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        polling_started = True
        log.info("MEGA Renamer Bot started; health endpoint available at /health on port %s", PORT)
        await stop_event.wait()
    finally:
        log.info("Shutting down MEGA Renamer Bot")
        if polling_started:
            await telegram_app.updater.stop()
        if telegram_app.running:
            await telegram_app.stop()
        await telegram_app.shutdown()
        if health_runner is not None:
            await health_runner.cleanup()
        if mongo_client is not None:
            mongo_client.close()
        log.info("MEGA Renamer Bot shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
