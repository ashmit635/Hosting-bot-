
# ========== BOT CONFIGURATION ==========

TOKEN = "8931014087:AAFvgfi4wjrA4YGT21FN1GWuWwGsLXcOYGw"

ADMIN_ID = 8444969077  # Yahan apni numeric Telegram user ID daalo

ADMIN_USERNAME = "gamer_pro_a_s"

# ======================================


"""
Button-based Telegram Bot Hosting Manager.
Requires: pip install -U python-telegram-bot
Set MANAGER_BOT_TOKEN in the environment. Optionally set ADMIN_ID to the numeric Telegram ID
for stronger admin identity; otherwise admin is matched by Telegram username Gamer_pro_a_s.

WARNING: Running uploaded Python source executes arbitrary code. Host only trusted projects.
No manager can guarantee arbitrary third-party bots will run without code/configuration fixes.
"""
import os, re, sys, time, shutil, sqlite3, asyncio, zipfile, tempfile, compileall, subprocess
from pathlib import Path, PurePosixPath
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

TOKEN = os.environ.get("MANAGER_BOT_TOKEN", "").strip()
ADMIN_USERNAME = "gamer_pro_a_s"
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)
ROOT = Path(os.environ.get("HOSTED_BOTS_DIR", "hosted_bots")).resolve()
DB_PATH = Path(os.environ.get("HOSTING_DB_FILE", "hosting_manager.sqlite3")).resolve()
MAX_UPLOAD = 20 * 1024 * 1024
MAX_UNPACKED = 100 * 1024 * 1024
ROOT.mkdir(parents=True, exist_ok=True)
PROCS = {}
FLOWS = {}
ARCHIVE_EXTS = {".py",".txt",".json",".yaml",".yml",".toml",".ini",".cfg",".md",".html",".css",".js",".csv",".db",".sqlite",".pem",".crt",".key"}

def conn():
    c=sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, started_at REAL, access_until REAL, lifetime INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS bots(id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, username TEXT NOT NULL, token TEXT NOT NULL, folder TEXT NOT NULL, entry TEXT NOT NULL, disabled INTEGER DEFAULT 0, created_at REAL);
        CREATE INDEX IF NOT EXISTS idx_bots_owner ON bots(owner_id);
        """)

def admin(update):
    u=update.effective_user
    if not u: return False
    if ADMIN_ID: return u.id == ADMIN_ID
    return (u.username or "").lower() == ADMIN_USERNAME

def user_row(uid):
    with conn() as c: return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

def access_ok(uid):
    if ADMIN_ID and uid == ADMIN_ID: return True
    with conn() as c: r=c.execute("SELECT access_until,lifetime,username FROM users WHERE user_id=?", (uid,)).fetchone()
    if r and (r["username"] or "").lower() == ADMIN_USERNAME: return True
    return bool(r and (r["lifetime"] or (r["access_until"] and r["access_until"] > time.time())))

def touch_user(update):
    u=update.effective_user
    if not u: return
    with conn() as c:
        c.execute("""INSERT INTO users(user_id,username,first_name,started_at) VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name""",
        (u.id,u.username or "",u.first_name or "",time.time()))

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add bot", callback_data="add")],
        [InlineKeyboardButton("🤖 My bots", callback_data="mybots"), InlineKeyboardButton("❓ Help", callback_data="help")]
    ])

def home_text():
    return "🚀 **Bot Hosting Manager**\n\nButtons se bots add aur manage karo. Har user ko sirf apne bots dikhte hain."

async def say(update, text, reply_markup=None, **kwargs):
    m=update.effective_message
    if m: await m.reply_text(text, reply_markup=reply_markup, **kwargs)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update)
    if not access_ok(update.effective_user.id) and not admin(update):
        await say(update, "🔒 **Access required**\n\nContact admin to get access: @Gamer_pro_a_s", parse_mode="Markdown")
        return
    await say(update, home_text(), main_menu(), parse_mode="Markdown")

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update)
    if not admin(update):
        if not access_ok(update.effective_user.id):
            return await say(update, "🔒 Contact admin to get access: @Gamer_pro_a_s")
        return await say(update, "⛔ Admin only.")
    await say(update, "👑 **ADMIN PANEL**", InlineKeyboardMarkup([
        [InlineKeyboardButton("All hosted bots 🔥", callback_data="adm_bots")],
        [InlineKeyboardButton("Started users 👽", callback_data="adm_users")]
    ]), parse_mode="Markdown")

async def show_my_bots(q, uid):
    with conn() as c: rows=c.execute("SELECT id,username,disabled FROM bots WHERE owner_id=? ORDER BY id DESC",(uid,)).fetchall()
    if not rows:
        await q.edit_message_text("📭 Abhi tumne koi bot host nahi kiya.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add bot",callback_data="add")],[InlineKeyboardButton("⬅️ Menu",callback_data="home")]]))
        return
    kb=[]
    for r in rows:
        state="⛔" if r["disabled"] else "🤖"
        kb.append([InlineKeyboardButton(f"{state} @{r['username']}",callback_data=f"bot:{r['id']}")])
    kb.append([InlineKeyboardButton("➕ Add bot",callback_data="add"),InlineKeyboardButton("⬅️ Menu",callback_data="home")])
    await q.edit_message_text("🤖 **Your hosted bots**\nSelect a bot to manage it:",reply_markup=InlineKeyboardMarkup(kb),parse_mode="Markdown")

async def start_add(q, uid):
    FLOWS[uid]={"step":"bot_username"}
    await q.edit_message_text("➕ **Add bot — 1/3**\nBot ka @username bhejo. Example: `my_sample_bot`",parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="cancel")]]))

def get_bot(bot_id):
    with conn() as c: return c.execute("SELECT * FROM bots WHERE id=?",(bot_id,)).fetchone()

async def start_proc(bot_id):
    r=get_bot(bot_id)
    if not r: return False,"Bot not found."
    if r["disabled"]: return False,"This bot has been disabled by admin."
    old=PROCS.get(bot_id)
    if old and old.poll() is None: return True,"Already running."
    folder=Path(r["folder"]).resolve(); entry=(folder/r["entry"]).resolve()
    if folder not in entry.parents or not entry.is_file(): return False,"Entry file missing/unsafe."
    py=folder/".venv"/("Scripts/python.exe" if os.name=="nt" else "bin/python")
    if not py.exists(): return False,"Virtual environment missing. Delete and re-add this project."
    log=open(folder/"output.log","a",encoding="utf-8",buffering=1)
    env=os.environ.copy()
    env.update({"BOT_TOKEN":r["token"],"TELEGRAM_BOT_TOKEN":r["token"],"API_TOKEN":r["token"],"PYTHONUNBUFFERED":"1"})
    try:
        p=subprocess.Popen([str(py),str(entry)],cwd=folder,env=env,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
        PROCS[bot_id]=p
        return True,f"Started (PID {p.pid})."
    except Exception as e:
        log.close(); return False,f"Start failed: {e}"

async def stop_proc(bot_id):
    p=PROCS.pop(bot_id,None)
    if not p or p.poll() is not None: return True,"Already stopped."
    try:
        p.terminate()
        try: await asyncio.to_thread(p.wait,5)
        except subprocess.TimeoutExpired: p.kill(); await asyncio.to_thread(p.wait,5)
        return True,"Stopped."
    except Exception as e: return False,str(e)

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    uid=q.from_user.id
    touch_user(update)
    data=q.data or ""
    if data=="cancel":
        FLOWS.pop(uid,None); await q.edit_message_text("Cancelled."); return
    if data=="home":
        if not access_ok(uid) and not admin(update): return await q.edit_message_text("🔒 Contact admin to get access: @Gamer_pro_a_s")
        return await q.edit_message_text(home_text(),reply_markup=main_menu(),parse_mode="Markdown")
    if data=="help":
        return await q.edit_message_text("Use buttons: ➕ Add bot → username → token → upload .py/.zip. ZIP projects should include requirements.txt when needed.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu",callback_data="home")]]))
    if data=="add" or data=="mybots" or data.startswith("bot:"):
        if not access_ok(uid) and not admin(update): return await q.edit_message_text("🔒 Contact admin to get access: @Gamer_pro_a_s")
    if data=="add": return await start_add(q,uid)
    if data=="mybots": return await show_my_bots(q,uid)
    if data.startswith("bot:"):
        bid=int(data.split(":")[1]); r=get_bot(bid)
        if not r or (r["owner_id"]!=uid and not admin(update)): return await q.edit_message_text("⛔ You cannot access this bot.")
        running=bool(PROCS.get(bid) and PROCS[bid].poll() is None)
        kb=[[InlineKeyboardButton("⏹ Stop" if running else "▶️ Start",callback_data=f"toggle:{bid}")],
            [InlineKeyboardButton("🔄 Restart",callback_data=f"restart:{bid}"),InlineKeyboardButton("📜 Logs",callback_data=f"logs:{bid}")],
            [InlineKeyboardButton("🗑 Delete",callback_data=f"delete:{bid}")],[InlineKeyboardButton("⬅️ My bots",callback_data="mybots")]]
        return await q.edit_message_text(f"🤖 @{r['username']}\nStatus: {'🟢 Running' if running else '🔴 Stopped'}\nAdmin disabled: {'Yes' if r['disabled'] else 'No'}",reply_markup=InlineKeyboardMarkup(kb))
    if data.startswith(("toggle:","restart:","logs:","delete:")):
        kind,bs=data.split(":"); bid=int(bs); r=get_bot(bid)
        if not access_ok(uid) and not admin(update): return await q.edit_message_text("🔒 Access expired. Contact admin: @Gamer_pro_a_s")
        if not r or (r["owner_id"]!=uid and not admin(update)): return await q.edit_message_text("⛔ You cannot access this bot.")
        if kind=="toggle":
            if PROCS.get(bid) and PROCS[bid].poll() is None: ok,msg=await stop_proc(bid)
            else: ok,msg=await start_proc(bid)
            await q.edit_message_text(msg,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data=f"bot:{bid}")]])); return
        if kind=="restart":
            await stop_proc(bid); ok,msg=await start_proc(bid)
            await q.edit_message_text(msg,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data=f"bot:{bid}")]])); return
        if kind=="logs":
            p=Path(r["folder"])/"output.log"
            text=p.read_text(errors="replace")[-3500:] if p.exists() else "No logs yet."
            return await q.edit_message_text(text[-3900:],reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data=f"bot:{bid}")]]))
        if kind=="delete":
            await stop_proc(bid); folder=Path(r["folder"]).resolve()
            with conn() as c: c.execute("DELETE FROM bots WHERE id=?",(bid,))
            if ROOT in folder.parents: shutil.rmtree(folder,ignore_errors=True)
            return await q.edit_message_text("🗑️ Bot deleted.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ My bots",callback_data="mybots")]]))
    if not admin(update):
        return
    if data=="adm_bots":
        with conn() as c: rows=c.execute("SELECT b.id,b.username,b.owner_id,b.disabled,u.username AS owner_name,u.first_name FROM bots b LEFT JOIN users u ON b.owner_id=u.user_id ORDER BY b.id DESC").fetchall()
        kb=[[InlineKeyboardButton(f"{'⛔' if r['disabled'] else '🔥'} @{r['username']} — {('@'+r['owner_name']) if r['owner_name'] else r['first_name'] or r['owner_id']}",callback_data=f"adm_bot:{r['id']}")] for r in rows]
        kb.append([InlineKeyboardButton("⬅️ Admin panel",callback_data="adm_home")])
        return await q.edit_message_text("All hosted bots 🔥\nChoose a bot:",reply_markup=InlineKeyboardMarkup(kb or [[InlineKeyboardButton("No bots yet",callback_data="adm_home")]]))
    if data=="adm_users":
        with conn() as c: rows=c.execute("SELECT * FROM users ORDER BY started_at DESC").fetchall()
        kb=[]
        for r in rows:
            label=("@" + r["username"]) if r["username"] else (r["first_name"] or str(r["user_id"]))
            kb.append([InlineKeyboardButton(f"👤 {label} ({r['user_id']})",callback_data=f"grant_user:{r['user_id']}")])
        kb.append([InlineKeyboardButton("⬅️ Admin panel",callback_data="adm_home")])
        return await q.edit_message_text("Started users 👽\nSelect a user to grant access:",reply_markup=InlineKeyboardMarkup(kb or [[InlineKeyboardButton("No users yet",callback_data="adm_home")]]))
    if data=="adm_home":
        return await q.edit_message_text("👑 ADMIN PANEL",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("All hosted bots 🔥",callback_data="adm_bots")],[InlineKeyboardButton("Started users 👽",callback_data="adm_users")]]))
    if data.startswith("adm_bot:"):
        bid=int(data.split(":")[1]); r=get_bot(bid)
        if not r: return await q.edit_message_text("Bot not found.")
        with conn() as c: u=c.execute("SELECT username,first_name FROM users WHERE user_id=?",(r["owner_id"],)).fetchone()
        owner=("@"+u["username"]) if u and u["username"] else (u["first_name"] if u else "Unknown")
        kb=[[InlineKeyboardButton("✅ Enable bot" if r["disabled"] else "⛔ Disable bot",callback_data=f"disable:{bid}")],[InlineKeyboardButton("⬅️ All hosted bots",callback_data="adm_bots")]]
        return await q.edit_message_text(f"🔥 @{r['username']}\nHosted by: {owner}\nTelegram user ID: {r['owner_id']}\nStatus: {'DISABLED' if r['disabled'] else 'Enabled'}",reply_markup=InlineKeyboardMarkup(kb))
    if data.startswith("disable:"):
        bid=int(data.split(":")[1]); r=get_bot(bid)
        if not r: return await q.edit_message_text("Bot not found.")
        new=0 if r["disabled"] else 1
        with conn() as c: c.execute("UPDATE bots SET disabled=? WHERE id=?",(new,bid))
        if new: await stop_proc(bid)
        return await q.edit_message_text(f"@{r['username']} is now {'disabled' if new else 'enabled'}.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data=f"adm_bot:{bid}")]]))
    if data.startswith("grant_user:"):
        target=int(data.split(":")[1])
        with conn() as c: c.execute("INSERT OR IGNORE INTO users(user_id,started_at) VALUES(?,?)",(target,time.time()))
        return await q.edit_message_text(f"Grant access to user ID {target} for how long?",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("1 day",callback_data=f"grant:{target}:1"),InlineKeyboardButton("7 days",callback_data=f"grant:{target}:7")],
            [InlineKeyboardButton("30 days",callback_data=f"grant:{target}:30"),InlineKeyboardButton("Lifetime",callback_data=f"grant:{target}:life")],
            [InlineKeyboardButton("Custom days",callback_data=f"custom:{target}")],
            [InlineKeyboardButton("⬅️ Users",callback_data="adm_users")]]))
    if data.startswith("custom:"):
        target=int(data.split(":")[1]); FLOWS[uid]={"step":"custom_days","target":target}
        return await q.edit_message_text("Custom access: ab jitne din access dena hai woh number message mein bhejo (1–36500).")
    if data.startswith("grant:"):
        _,target_s,duration=data.split(":"); target=int(target_s)
        with conn() as c:
            if duration=="life": c.execute("UPDATE users SET lifetime=1,access_until=NULL WHERE user_id=?",(target,)); msg="Lifetime access granted."
            else:
                days=int(duration); c.execute("UPDATE users SET lifetime=0,access_until=? WHERE user_id=?",(time.time()+days*86400,target)); msg=f"Access granted for {days} day(s)."
        return await q.edit_message_text(msg,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Users",callback_data="adm_users")]]))

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    if not u: return
    touch_user(update); uid=u.id; flow=FLOWS.get(uid)
    if not flow:
        if not access_ok(uid) and not admin(update):
            await say(update,"🔒 Contact admin to get access: @Gamer_pro_a_s")
        return
    text=(update.effective_message.text or "").strip()
    if flow["step"]=="bot_username":
        username=text.lstrip("@").lower()
        if not re.fullmatch(r"[a-zA-Z0-9_]{5,64}",username):
            return await say(update,"❌ Invalid bot username. Try again or press /cancel.")
        with conn() as c:
            exists=c.execute("SELECT 1 FROM bots WHERE owner_id=? AND lower(username)=?",(uid,username)).fetchone()
        if exists: return await say(update,"You already host a bot with that username. Use My bots.")
        flow.update(step="token",username=username)
        return await say(update,"Step 2/3: Send the bot's BotFather token.")
    if flow["step"]=="token":
        if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}",text):
            return await say(update,"❌ Token format looks invalid. Send the complete token.")
        flow.update(step="file",token=text)
        return await say(update,"Step 3/3: Upload one .py file or a .zip project. For multi-file projects, include requirements.txt.")
    if flow["step"]=="custom_days":
        if not text.isdigit() or not 1<=int(text)<=36500: return await say(update,"Send a whole number from 1 to 36500.")
        target=flow["target"]; days=int(text); FLOWS.pop(uid,None)
        with conn() as c: c.execute("UPDATE users SET lifetime=0,access_until=? WHERE user_id=?",(time.time()+days*86400,target))
        return await say(update,f"✅ Access granted for {days} day(s) to user ID {target}.")
    if flow["step"]=="file":
        return await say(update,"Please upload the .py or .zip file now.")

def safe_extract(zpath, dest):
    total=0
    with zipfile.ZipFile(zpath) as z:
        if len(z.infolist())>500: raise ValueError("ZIP has too many files (max 500).")
        for info in z.infolist():
            name=info.filename.replace("\\","/"); p=PurePosixPath(name)
            if p.is_absolute() or ".." in p.parts: raise ValueError("Unsafe ZIP path.")
            if info.is_dir(): continue
            if Path(name).suffix.lower() not in ARCHIVE_EXTS: raise ValueError(f"Unsupported file type: {name}")
            total+=info.file_size
            if total>MAX_UNPACKED: raise ValueError("ZIP expands beyond 100 MB.")
            target=(dest/Path(*p.parts)).resolve()
            if dest.resolve() not in target.parents: raise ValueError("Unsafe ZIP destination.")
            target.parent.mkdir(parents=True,exist_ok=True)
            with z.open(info) as src,open(target,"wb") as dst: shutil.copyfileobj(src,dst)

def choose_entry(folder, uploaded_name=""):
    py=[p for p in folder.rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts]
    if uploaded_name:
        found=[p for p in py if p.name==uploaded_name]
        if found:return found[0]
    for name in ("main.py","bot.py","app.py","run.py","__main__.py"):
        found=[p for p in py if p.name.lower()==name]
        if found:return found[0]
    if len(py)==1:return py[0]
    if not py: raise ValueError("No Python files found.")
    raise ValueError("Multiple Python files found; name the entry file main.py or bot.py.")

def cmd(args,cwd,timeout=900):
    r=subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
    if r.returncode: raise RuntimeError((r.stdout or "Command failed")[-3000:])
    return r.stdout

async def document_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; touch_user(update)
    if not access_ok(uid) and not admin(update): return await say(update,"🔒 Contact admin to get access: @Gamer_pro_a_s")
    flow=FLOWS.get(uid)
    if not flow or flow.get("step")!="file": return
    doc=update.effective_message.document
    if not doc: return
    name=Path(doc.file_name or "upload").name; ext=Path(name).suffix.lower()
    if ext not in (".py",".zip"): return await say(update,"Upload a .py or .zip file.")
    if doc.file_size and doc.file_size>MAX_UPLOAD: return await say(update,"File too large (20 MB max).")
    username,token=flow["username"],flow["token"]
    temp=Path(tempfile.mkdtemp(prefix="hosting_")); folder=ROOT/f"user_{uid}"/f"{username}_{int(time.time())}"
    try:
        src=temp/name; tgfile=await doc.get_file(); await tgfile.download_to_drive(custom_path=str(src))
        test=Application.builder().token(token).build()
        try: me=await test.bot.get_me()
        except Exception as e: return await say(update,f"❌ Token verification failed: {str(e)[:400]}")
        if (me.username or "").lower()!=username: return await say(update,f"❌ Token belongs to @{me.username}, not @{username}.")
        folder.mkdir(parents=True,exist_ok=False)
        if ext==".zip": safe_extract(src,folder); uploaded=""
        else: shutil.copy2(src,folder/name); uploaded=name
        entry=choose_entry(folder,uploaded)
        if not compileall.compile_dir(str(folder),quiet=1,force=True,rx=r"(^|/)(\.venv|__pycache__)(/|$)"):
            raise ValueError("Python syntax check failed. Fix syntax errors and upload again.")
        venv=folder/".venv"; cmd([sys.executable,"-m","venv",str(venv)],str(folder),120)
        pip=venv/("Scripts/pip.exe" if os.name=="nt" else "bin/pip")
        req=folder/"requirements.txt"
        if req.exists(): cmd([str(pip),"install","--disable-pip-version-check","-r",str(req)],str(folder),900)
        with conn() as c:
            c.execute("INSERT INTO bots(owner_id,username,token,folder,entry,created_at) VALUES(?,?,?,?,?,?)",
                (uid,username,token,str(folder),str(entry.relative_to(folder)),time.time()))
            bid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        FLOWS.pop(uid,None)
        ok,msg=await start_proc(bid)
        await say(update,f"✅ @{username} added.\n{msg}",main_menu())
    except Exception as e:
        if folder.exists(): shutil.rmtree(folder,ignore_errors=True)
        await say(update,f"❌ Setup failed: {str(e)[:1800]}\nCheck requirements.txt and source files, then try again.")
    finally: shutil.rmtree(temp,ignore_errors=True)

async def cancel(update,context):
    FLOWS.pop(update.effective_user.id,None)
    await say(update,"Cancelled.",main_menu() if access_ok(update.effective_user.id) or admin(update) else None)

async def monitor(app):
    while True:
        await asyncio.sleep(15)
        with conn() as c: rows=c.execute("SELECT id,owner_id,disabled FROM bots").fetchall()
        for r in rows:
            if r["disabled"] or not access_ok(r["owner_id"]):
                await stop_proc(r["id"])
        # No blind auto-restart: repeatedly restarting broken code can hide errors and waste resources.

async def post_init(app):
    app.create_task(monitor(app))

def main():
    if not TOKEN: raise SystemExit("Set MANAGER_BOT_TOKEN environment variable first.")
    init_db()
    app=Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("admin",admin_cmd))
    app.add_handler(CommandHandler("cancel",cancel))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.Document.ALL,document_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_message))
    print("Hosting manager is running.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__": main()
