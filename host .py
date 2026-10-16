
import os
import re
import sys
import time
import sqlite3
import asyncio
import shutil
import subprocess
import py_compile
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

TOKEN = os.getenv("MANAGER_BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_CONTACT = "@Gamer_pro_a_s"

BASE = Path("hosted_bots").resolve()
BASE.mkdir(exist_ok=True)
DB = "manager.db"
processes = {}
states = {}
MAX_FILE_SIZE = 20 * 1024 * 1024


def conn():
    return sqlite3.connect(DB)


def init_db():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            uid INTEGER PRIMARY KEY, name TEXT, username TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS access(
            uid INTEGER PRIMARY KEY, expires REAL DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bots(
            username TEXT PRIMARY KEY, owner INTEGER, token TEXT,
            filename TEXT, folder TEXT, enabled INTEGER DEFAULT 1)""")


def is_admin(uid):
    return uid == ADMIN_ID and ADMIN_ID != 0


def allowed(uid):
    if is_admin(uid):
        return True
    with conn() as c:
        r = c.execute(
            "SELECT expires FROM access WHERE uid=?", (uid,)
        ).fetchone()
    return bool(r and (r[0] == 0 or r[0] > time.time()))


def kb(rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=data)
         for label, data in row] for row in rows
    ])


def home(uid):
    rows = [
        [("➕ Add Bot", "add"), ("🤖 My Bots", "mine")],
    ]
    if is_admin(uid):
        rows.append([("⚙️ Admin Panel", "admin")])
    rows.append([("👤 Contact Admin", "contact")])
    return kb(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO users VALUES(?,?,?)",
            (u.id, u.full_name, u.username or "")
        )

    if not allowed(u.id):
        await update.message.reply_text(
            "🔒 ACCESS REQUIRED\n\n"
            "Contact admin to get access:\n" + ADMIN_CONTACT
        )
        return

    await update.message.reply_text(
        "👑 BOT HOSTING MANAGER\nChoose an option:",
        reply_markup=home(u.id)
    )


async def admin_cmd(update, context):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text(
            "Access denied. Contact admin: " + ADMIN_CONTACT
        )
    await update.message.reply_text(
        "👑 ADMIN PANEL",
        reply_markup=kb([
            [("🔥 All Hosted Bots", "allbots")],
            [("👽 Started Users", "users")],
            [("🏠 Home", "home")]
        ])
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    if data == "contact":
        return await q.edit_message_text(
            "Contact admin to get access:\n" + ADMIN_CONTACT
        )

    if data == "home":
        if not allowed(uid):
            return await q.edit_message_text(
                "🔒 Access expired. Contact " + ADMIN_CONTACT
            )
        return await q.edit_message_text(
            "🏠 MAIN MENU", reply_markup=home(uid)
        )

    if data == "admin":
        if not is_admin(uid):
            return await q.edit_message_text("❌ Admin only.")
        return await q.edit_message_text(
            "👑 ADMIN PANEL",
            reply_markup=kb([
                [("🔥 All Hosted Bots", "allbots")],
                [("👽 Started Users", "users")],
                [("🏠 Home", "home")]
            ])
        )

    if data == "mine":
        if not allowed(uid):
            return await q.edit_message_text(
                "🔒 Access expired. Contact " + ADMIN_CONTACT
            )
        with conn() as c:
            bots = c.execute(
                "SELECT username, enabled FROM bots WHERE owner=?",
                (uid,)
            ).fetchall()
        rows = [[(
            ("🟢 " if enabled else "🔴 ") + "@" + name,
            "mybot:" + name
        )] for name, enabled in bots]
        rows.append([("➕ Add Bot", "add")])
        rows.append([("🏠 Home", "home")])
        return await q.edit_message_text(
            "🤖 YOUR BOTS", reply_markup=kb(rows)
        )

    if data == "add":
        if not allowed(uid):
            return await q.edit_message_text(
                "🔒 Access required. Contact " + ADMIN_CONTACT
            )
        states[uid] = {"step": "username"}
        return await q.edit_message_text(
            "Step 1/3: Send your bot's @username."
        )

    if data == "allbots":
        if not is_admin(uid):
            return await q.edit_message_text("❌ Admin only.")
        with conn() as c:
            bots = c.execute(
                """SELECT b.username,b.owner,b.enabled,u.name
                   FROM bots b LEFT JOIN users u ON b.owner=u.uid"""
            ).fetchall()
        rows = [[(
            ("🟢 " if enabled else "🔴 ") + "@" + name
            + " — " + str(owner),
            "adminbot:" + name
        )] for name, owner, enabled, _ in bots]
        rows.append([("⬅️ Admin Panel", "admin")])
        return await q.edit_message_text(
            "🔥 ALL HOSTED BOTS", reply_markup=kb(rows)
        )

    if data == "users":
        if not is_admin(uid):
            return await q.edit_message_text("❌ Admin only.")
        with conn() as c:
            users = c.execute(
                "SELECT uid,name,username FROM users ORDER BY name"
            ).fetchall()
        rows = [[(
            (name or "Unknown") + " (@"+(username or "no_username")+")",
            "user:" + str(user_id)
        )] for user_id, name, username in users]
        rows.append([("⬅️ Admin Panel", "admin")])
        return await q.edit_message_text(
            "👽 STARTED USERS", reply_markup=kb(rows)
        )

    if data.startswith("mybot:"):
        name = data.split(":", 1)[1]
        with conn() as c:
            r = c.execute(
                "SELECT owner,enabled FROM bots WHERE username=?",
                (name,)
            ).fetchone()
        if not r or (r[0] != uid and not is_admin(uid)):
            return await q.edit_message_text("❌ Not your bot.")
        return await q.edit_message_text(
            "🤖 @" + name,
            reply_markup=kb([
                [("▶️ Start", "start:" + name),
                 ("⏹ Stop", "stop:" + name)],
                [("🔄 Restart", "restart:" + name)],
                [("📊 Status", "status:" + name),
                 ("📜 Logs", "logs:" + name)],
                [("⬅️ My Bots", "mine")]
            ])
        )

    if data.startswith("adminbot:"):
        if not is_admin(uid):
            return await q.edit_message_text("❌ Admin only.")
        name = data.split(":", 1)[1]
        with conn() as c:
            r = c.execute(
                "SELECT owner,enabled FROM bots WHERE username=?",
                (name,)
            ).fetchone()
        if not r:
            return await q.edit_message_text("Bot not found.")
        owner, enabled = r
        return await q.edit_message_text(
            f"🤖 @{name}\nOwner Telegram ID: {owner}\n"
            f"Enabled: {bool(enabled)}",
            reply_markup=kb([
                [("🔴 Disable" if enabled else "🟢 Enable",
                  ("disable:" if enabled else "enable:") + name)],
                [("⬅️ All Hosted Bots", "allbots")]
            ])
        )

    if data.startswith(("disable:", "enable:")):
        if not is_admin(uid):
            return await q.edit_message_text("❌ Admin only.")
        action, name = data.split(":", 1)
        enabled = 0 if action == "disable" else 1
        with conn() as c:
            c.execute("UPDATE bots SET enabled=? WHERE username=?",
                      (enabled, name))
        if not enabled:
            await stop_bot(name)
        else:
            await start_bot(name)
        return await q.edit_message_text(
            f"@{name} {'enabled' if enabled else 'disabled'}.",
            reply_markup=kb([[("⬅️ All Hosted Bots", "allbots")]])
        )

    if data.startswith("user:"):
        if not is_admin(uid):
            return await q.edit_message_text("❌ Admin only.")
        target = int(data.split(":", 1)[1])
        return await q.edit_message_text(
            f"👤 User ID: {target}\nChoose access duration:",
            reply_markup=kb([
                [("1 Day", f"grant:{target}:1"),
                 ("7 Days", f"grant:{target}:7")],
                [("30 Days", f"grant:{target}:30"),
                 ("♾ Lifetime", f"grant:{target}:0")],
                [("Custom Days", f"custom:{target}")],
                [("⬅️ Users", "users")]
            ])
        )

    if data.startswith("grant:"):
        if not is_admin(uid):
            return await q.edit_message_text("❌ Admin only.")
        _, target, days = data.split(":")
        target, days = int(target), int(days)
        expiry = 0 if days == 0 else time.time() + days * 86400
        with conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO access VALUES(?,?)",
                (target, expiry)
            )
        try:
            await context.bot.send_message(
                target, "✅ Hosting access granted: " +
                ("Lifetime" if days == 0 else f"{days} days")
            )
        except Exception:
            pass
        return await q.edit_message_text("✅ Access updated.")

    if data.startswith("custom:"):
        if not is_admin(uid):
            return await q.edit_message_text("❌ Admin only.")
        target = int(data.split(":", 1)[1])
        states[uid] = {"step": "custom_days", "target": target}
        return await q.edit_message_text(
            "Send the number of days as a whole number."
        )

    if data.startswith(("start:", "stop:", "restart:",
                        "status:", "logs:")):
        action, name = data.split(":", 1)
        with conn() as c:
            r = c.execute(
                "SELECT owner,enabled FROM bots WHERE username=?",
                (name,)
            ).fetchone()
        if not r or (r[0] != uid and not is_admin(uid)):
            return await q.edit_message_text("❌ Not your bot.")
        if action != "status" and not r[1]:
            return await q.edit_message_text("🔴 This bot is disabled by admin.")
        if action == "start":
            ok, msg = await start_bot(name)
        elif action == "stop":
            ok, msg = await stop_bot(name)
        elif action == "restart":
            await stop_bot(name)
            await asyncio.sleep(1)
            ok, msg = await start_bot(name)
        elif action == "status":
            p = processes.get(name)
            ok, msg = True, (
                "🟢 RUNNING" if p and p.poll() is None else "🔴 STOPPED"
            )
        else:
            with conn() as c:
                folder = c.execute(
                    "SELECT folder FROM bots WHERE username=?", (name,)
                ).fetchone()[0]
            log = Path(folder) / "output.log"
            ok, msg = True, (
                log.read_text(errors="replace")[-3000:]
                if log.exists() else "No logs yet."
            )
        return await q.edit_message_text(
            msg, reply_markup=kb([[("⬅️ Bot", "mybot:" + name)]])
        )


async def start_bot(name):
    with conn() as c:
        r = c.execute(
            "SELECT token,filename,folder,enabled FROM bots WHERE username=?",
            (name,)
        ).fetchone()
    if not r:
        return False, "Bot not found."
    token, filename, folder, enabled = r
    if not enabled:
        return False, "Bot disabled by admin."
    old = processes.get(name)
    if old and old.poll() is None:
        return False, "Already running."
    folder = Path(folder).resolve()
    source = (folder / filename).resolve()
    if folder not in source.parents or not source.is_file():
        return False, "Source file missing."
    log = open(folder / "output.log", "a", buffering=1)
    env = os.environ.copy()
    env.update({
        "BOT_TOKEN": token,
        "TELEGRAM_BOT_TOKEN": token,
        "API_TOKEN": token,
        "PYTHONUNBUFFERED": "1"
    })
    try:
        processes[name] = subprocess.Popen(
            [sys.executable, str(source)],
            cwd=folder, env=env, stdout=log,
            stderr=subprocess.STDOUT
        )
        return True, f"Started @{name}."
    except Exception as e:
        log.close()
        return False, str(e)


async def stop_bot(name):
    p = processes.get(name)
    if not p or p.poll() is not None:
        processes.pop(name, None)
        return False, "Already stopped."
    p.terminate()
    try:
        await asyncio.to_thread(p.wait, 5)
    except subprocess.TimeoutExpired:
        p.kill()
    processes.pop(name, None)
    return True, "Stopped."


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = states.get(uid)
    if not state:
        return
    msg = update.message
    value = (msg.text or "").strip()

    if state["step"] == "custom_days":
        if not is_admin(uid):
            states.pop(uid, None)
            return
        if not value.isdigit() or not 1 <= int(value) <= 36500:
            return await msg.reply_text("Send a number from 1 to 36500.")
        target, days = state["target"], int(value)
        with conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO access VALUES(?,?)",
                (target, time.time() + days * 86400)
            )
        states.pop(uid, None)
        return await msg.reply_text(f"✅ Access granted for {days} days.")

    if state["step"] == "username":
        name = value.lstrip("@").lower()
        if not re.fullmatch(r"[a-z0-9_]{5,32}", name):
            return await msg.reply_text("Invalid bot username. Try again.")
        with conn() as c:
            exists = c.execute(
                "SELECT 1 FROM bots WHERE username=?", (name,)
            ).fetchone()
        if exists:
            return await msg.reply_text("This bot is already registered.")
        state.update(username=name, step="token")
        return await msg.reply_text("Step 2/3: Send the bot's BotFather token.")

    if state["step"] == "token":
        if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", value):
            return await msg.reply_text("Token format is invalid. Try again.")
        state.update(token=value, step="file")
        return await msg.reply_text(
            "Step 3/3: Upload the main Python .py file as a document."
        )


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = states.get(uid)
    if not state or state.get("step") != "file":
        return
    doc = update.message.document
    if not doc.file_name.lower().endswith(".py"):
        return await update.message.reply_text("Upload a .py file only.")
    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        return await update.message.reply_text("Maximum file size is 20 MB.")

    name, token = state["username"], state["token"]
    folder = (BASE / name).resolve()
    try:
        test = context.bot
        other = await test.get_me()
        # Validate the submitted token and username.
        from telegram import Bot
        check = Bot(token)
        try:
            identity = await check.get_me()
        finally:
            await check.shutdown()
        if (identity.username or "").lower() != name:
            return await update.message.reply_text(
                f"Token belongs to @{identity.username}, not @{name}."
            )

        folder.mkdir(parents=True, exist_ok=False)
        path = folder / Path(doc.file_name).name
        tgfile = await doc.get_file()
        await tgfile.download_to_drive(custom_path=str(path))

        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as e:
            shutil.rmtree(folder, ignore_errors=True)
            return await update.message.reply_text(
                "❌ Python syntax error:\n" + str(e)[:1500]
            )

        with conn() as c:
            c.execute(
                "INSERT INTO bots(username,owner,token,filename,folder) "
                "VALUES(?,?,?,?,?)",
                (name, uid, token, path.name, str(folder))
            )
        states.pop(uid, None)
        ok, result = await start_bot(name)
        await update.message.reply_text(
            "✅ Source checked and saved.\n" + result,
            reply_markup=kb([[("🤖 My Bots", "mine")]])
        )
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)
        await update.message.reply_text(
            "❌ Setup failed:\n" + str(e)[:1500]
        )


async def monitor(app):
    while True:
        await asyncio.sleep(15)
        for name, proc in list(processes.items()):
            if proc.poll() is not None:
                processes.pop(name, None)
                with conn() as c:
                    r = c.execute(
                        "SELECT enabled FROM bots WHERE username=?", (name,)
                    ).fetchone()
                if r and r[0]:
                    await start_bot(name)


def main():
    if not TOKEN or not ADMIN_ID:
        raise RuntimeError("Set MANAGER_BOT_TOKEN and ADMIN_ID in Render.")
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    async def post_init(application):
        application.create_task(monitor(application))
    app.post_init = post_init
    app.run_polling()


if __name__ == "__main__":
    main()
    