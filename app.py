"""
PyHost Cloud — Telegram Bot Hosting Bot  (single-file edition)
================================================================
ইউজাররা নিজের Python বট/স্ক্রিপ্ট এই বটে আপলোড করে হোস্ট করতে পারে।

ফাইল মাত্র ৩টি:  app.py  •  .env  •  requirements.txt

সেটআপ (Ubuntu/Debian, root হিসেবে — ইউজার আইসোলেশনের জন্য):
    apt install -y python3 python3-venv python3-pip git unzip
    mkdir -p /opt/pyhost && cd /opt/pyhost          # তিনটা ফাইল এখানে রাখুন
    python3 -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt
    chmod -R a+rX /opt/venv /opt/pyhost && chmod 600 .env
    nano .env                                       # BOT_TOKEN, ADMIN_IDS দিন
    /opt/venv/bin/python app.py
venv অবশ্যই /opt এর মতো সবার-পড়ার-যোগ্য জায়গায় রাখুন (/root এর ভেতরে নয়)।

নোট:
  • চ্যানেল ভেরিফিকেশন: বটকে চ্যানেলে Admin বানাতে হবে। চ্যানেল/bKash/Nagad/রেফার রিওয়ার্ড সব
    Admin Panel → সেটিংস থেকে বদলানো যায়।
  • পেমেন্ট ম্যানুয়াল: ইউজার TrxID দেয় → অ্যাডমিনের কাছে Approve/Reject বাটন যায়।
  • root হিসেবে চালালে (ISOLATE_USERS=1) প্রতিটি ইউজারের কোড আলাদা Linux uid-এ চলে। root ছাড়া চালালে
    এই সুরক্ষা বন্ধ থাকে — তখন ইউজার বটের .env/ডাটাবেস পড়তে পারবে।
  • ফাইল-অপারেশন সাব-প্রসেস এই ফাইলই চালায়:  python app.py --fsworker <op> ...
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile


# ==============================================================================
# 1. SANDBOXED FILE WORKER  (runs as the project owner's OS user — keep it stdlib-only)
# ==============================================================================
_FS_RAW = False


def _fs_out(**kw):
    sys.stdout.write(json.dumps(kw))
    sys.stdout.flush()
    sys.exit(0)


def _fs_fail(msg):
    if _FS_RAW:
        sys.stderr.write(msg)
        sys.exit(2)
    _fs_out(ok=False, error=msg)


def _fs_jail(base, rel):
    b = os.path.realpath(base)
    t = os.path.realpath(os.path.join(b, rel.strip("/")))
    if t != b and not t.startswith(b + os.sep):
        _fs_fail("পাথ অনুমোদিত নয়")
    return b, t


def _fs_op_ls(base, rel):
    _, t = _fs_jail(base, rel)
    if not os.path.isdir(t):
        _fs_fail("ফোল্ডার পাওয়া যায়নি")
    items = []
    for e in os.scandir(t):
        try:
            d = e.is_dir(follow_symlinks=False)
            s = 0 if d else e.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        items.append({"n": e.name, "d": d, "s": s})
    items.sort(key=lambda i: (not i["d"], i["n"].lower()))
    _fs_out(ok=True, items=items)


def _fs_op_exists(base, rel):
    _, t = _fs_jail(base, rel)
    if not os.path.isfile(t):
        _fs_fail(f"ফাইল পাওয়া যায়নি: {rel}")
    _fs_out(ok=True)


def _fs_op_read(base, rel, maxb):
    _, t = _fs_jail(base, rel)
    if not os.path.isfile(t):
        _fs_fail("ফাইল পাওয়া যায়নি")
    with open(t, "rb") as f:
        sys.stdout.buffer.write(f.read(int(maxb) + 1))
    sys.exit(0)


def _fs_op_write(base, rel, maxb):
    _, t = _fs_jail(base, rel)
    data = sys.stdin.buffer.read()
    if len(data) > int(maxb):
        _fs_fail("ফাইল অনেক বড়")
    os.makedirs(os.path.dirname(t), exist_ok=True)
    with open(t, "wb") as f:
        f.write(data)
    _fs_out(ok=True, size=len(data))


def _fs_op_mkdir(base, rel):
    _, t = _fs_jail(base, rel)
    os.makedirs(t, exist_ok=True)
    _fs_out(ok=True)


def _fs_op_rm(base, rel):
    if not rel.strip("/"):
        _fs_fail("রুট ফোল্ডার ডিলিট করা যাবে না")
    _, parent_real = _fs_jail(base, os.path.dirname(rel.strip("/")))
    t = os.path.join(parent_real, os.path.basename(rel.strip("/")))
    if os.path.islink(t) or os.path.isfile(t):
        os.unlink(t)
    elif os.path.isdir(t):
        shutil.rmtree(t)
    else:
        _fs_fail("পাওয়া যায়নি")
    _fs_out(ok=True)


def _fs_op_mv(base, rel, rel2):
    _, a = _fs_jail(base, rel)
    _, b = _fs_jail(base, rel2)
    if not os.path.exists(a):
        _fs_fail("পাওয়া যায়নি")
    os.makedirs(os.path.dirname(b), exist_ok=True)
    os.rename(a, b)
    _fs_out(ok=True)


def _fs_op_du(base, rel):
    total = 0
    for root, _dirs, files in os.walk(base):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    _fs_out(ok=True, size=total)


def _fs_op_find_py(base, rel):
    found = []
    base = os.path.realpath(base)
    for root, dirs, files in os.walk(base):
        depth = os.path.relpath(root, base).count(os.sep)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", "site-packages")
                   and depth < 2]
        for f in files:
            if f.endswith(".py"):
                found.append(os.path.relpath(os.path.join(root, f), base))
    found.sort(key=lambda p: (p.count(os.sep), p))
    _fs_out(ok=True, files=found[:40])


def _fs_extract(zf, dest, maxb):
    infos = [i for i in zf.infolist()]
    if sum(i.file_size for i in infos) > int(maxb):
        _fs_fail("আনজিপ সাইজ লিমিট ছাড়িয়ে গেছে")
    names = [i.filename for i in infos if not i.is_dir()]
    if not names:
        _fs_fail("জিপ ফাইল খালি")
    tops = {n.split("/")[0] for n in names}
    strip = len(tops) == 1 and all("/" in n for n in names)  # GitHub-style single root folder
    dest = os.path.realpath(dest)
    os.makedirs(dest, exist_ok=True)
    count = 0
    for i in infos:
        n = i.filename
        if strip:
            n = n.split("/", 1)[1] if "/" in n else ""
        if not n or i.is_dir():
            continue
        if (i.external_attr >> 16) & 0o170000 == 0o120000:  # symlink entry
            continue
        t = os.path.normpath(os.path.join(dest, n))
        if not t.startswith(dest + os.sep):
            continue
        os.makedirs(os.path.dirname(t), exist_ok=True)
        with zf.open(i) as s, open(t, "wb") as d:
            shutil.copyfileobj(s, d)
        count += 1
    _fs_out(ok=True, count=count)


def _fs_op_unzip(base, rel, maxb):
    """zip bytes come from stdin"""
    _, t = _fs_jail(base, rel)
    with tempfile.TemporaryFile() as tmp:
        shutil.copyfileobj(sys.stdin.buffer, tmp)
        tmp.seek(0)
        try:
            zf = zipfile.ZipFile(tmp)
        except zipfile.BadZipFile:
            _fs_fail("এটা বৈধ জিপ ফাইল নয়")
        _fs_extract(zf, t, maxb)


def _fs_op_unzipfile(base, rel, maxb, reldest):
    _, z = _fs_jail(base, rel)
    _, d = _fs_jail(base, reldest)
    try:
        zf = zipfile.ZipFile(z)
    except Exception:
        _fs_fail("এটা বৈধ জিপ ফাইল নয়")
    _fs_extract(zf, d, maxb)


def _fs_op_zip(base, rel, fd):
    skip = {"__pycache__", ".git", "node_modules"}
    base = os.path.realpath(base)
    with zipfile.ZipFile(os.fdopen(int(fd), "w+b"), "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip]
            for f in files:
                p = os.path.join(root, f)
                if os.path.islink(p):
                    continue
                z.write(p, os.path.relpath(p, base))
    _fs_out(ok=True)


_FS_OPS = {"ls": _fs_op_ls, "exists": _fs_op_exists, "read": _fs_op_read, "write": _fs_op_write,
           "mkdir": _fs_op_mkdir, "rm": _fs_op_rm, "mv": _fs_op_mv, "du": _fs_op_du, "find_py": _fs_op_find_py,
           "unzip": _fs_op_unzip, "unzipfile": _fs_op_unzipfile, "zip": _fs_op_zip}


def _fs_main(argv):
    global _FS_RAW
    op = argv[0]
    _FS_RAW = op == "read"
    try:
        _FS_OPS[op](*argv[1:])
    except SystemExit:
        raise
    except Exception as e:  # noqa
        _fs_fail(f"{type(e).__name__}: {e}")


# Sub-process mode:  python app.py --fsworker <op> <base> <rel> [extra...]
# Handled right here — before .env / database / telegram are touched — because the bot launches this
# as the project owner's unprivileged OS user, which must never read the bot's secrets.
if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--fsworker":
    _fs_main(sys.argv[2:])
    sys.exit(0)

# ---- everything below needs more than the standard file-ops (only loaded in normal bot mode) ----
import functools
import hashlib
import html
import logging
import posixpath
import re
import signal
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException


# ==============================================================================
# 2. CONFIG  (values come from .env)
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent


def _load_env():
    # ".env" (or a file simply named "env") next to app.py
    p = next((BASE_DIR / n for n in (".env", "env") if (BASE_DIR / n).is_file()), None)
    if p:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8848890598:AAH94DegiEf4nNg4qzWF0XuZLVhuYaqPrKI")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "7205386493").replace(" ", "").split(",") if x.isdigit()}
TZ = os.getenv("TZ_NAME", "Asia/Dhaka")

# Per-user OS isolation (needs the bot to run as root on Linux). Strongly recommended.
ISOLATE = os.getenv("ISOLATE_USERS", "1") == "1"
ISO_BASE_UID = int(os.getenv("ISO_BASE_UID", "100000"))

DATA_DIR = BASE_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
LOGS_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "host.db"
for _d in (DATA_DIR, PROJECTS_DIR, LOGS_DIR, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = 20          # Telegram Bot API download limit is 20 MB
MAX_UNZIP_MB = 300
MAX_FILE_MB = 500           # RLIMIT_FSIZE for user processes
MONITOR_INTERVAL = 4        # seconds
MAX_AUTO_RESTARTS = 5       # per 10 minutes
CPU_STRIKES = 10            # consecutive monitor ticks above the plan CPU limit before kill

# Defaults — ALL of these are editable later from the Admin Panel (Settings / Welcome message)
DEFAULT_WELCOME = """এটা একটা Python বট হোস্টিং সার্ভিস — পুরোটাই Telegram থেকে চালানো যায়। আপনার বট/স্ক্রিপ্টের ফাইল আপলোড করুন, ▶️ চালু করুন — ব্যস! কোনো সার্ভার বা টার্মিনাল জানার দরকার নেই।

✨ কেন আমাদের থেকে নেবেন?

✅ ঝামেলাহীন — .py বা পুরো .zip আপলোড করুন, requirements.txt অটো ইন্সটল হবে
✅ ২৪/৭ অনলাইন — ক্র্যাশ করলে সাথে সাথে লগ ও সমাধান জানানো হয়, পেইড প্ল্যানে অটো-রিস্টার্ট
✅ সব এক জায়গায় — ফাইল ম্যানেজার, লাইভ লগ, Package Manager, গিটহাব সিঙ্ক, ব্যাকআপ
✅ নিরাপদ — প্রতিটি প্রজেক্ট আলাদা ফোল্ডারে ও আলাদা প্রসেসে চলে
✅ সাশ্রয়ী — ফ্রি ট্রায়াল, কম দামের প্ল্যান, bKash/Nagad-এ সহজ পেমেন্ট
✅ রেফার করে ফ্রিতে ব্যালেন্স আর্ন করুন"""

DEFAULT_SETTINGS = {
    "welcome_title": "WELCOME AHAD HOSTING BOT",
    "welcome_text": DEFAULT_WELCOME,
    "channels": "@kdjddjksjsjd",
    "bkash": "01327392182",
    "nagad": "01767610021",
    "bkash_on": "1",          # "0" = bKash payment hidden
    "nagad_on": "1",          # "0" = Nagad payment hidden
    "payment_note": "",       # extra line shown on the payment screen
    "referral_reward": "5",
    "support": "@Abdulahad1234577",
    "admins": "",             # extra admin IDs (comma separated) — managed from Admin Panel
}

# id, name, price, days, max_projects, ram_mb, cpu_pct, disk_mb, auto_restart, active, sort
SEED_PLANS = [
    ("free", "🆓 FREE", 0, 7, 1, 256, 50, 100, 0, 1, 0),
    ("basic", "🥉 BASIC", 49, 30, 3, 512, 80, 500, 1, 1, 1),
    ("pro", "🥈 PRO", 99, 30, 10, 1024, 150, 2000, 1, 1, 2),
    ("premium", "👑 PREMIUM", 199, 30, 50, 2048, 200, 5000, 1, 1, 3),
    ("student", "🎓 STUDENT", 29, 30, 2, 256, 50, 300, 1, 1, 0),   # cheap plan — editable from Admin Panel
]


# ==============================================================================
# 3. UTILS
# ==============================================================================
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(TZ)
except Exception:  # tzdata missing
    _TZ = None


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def fmt_dur(sec) -> str:
    sec = int(max(0, sec))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def fmt_ts(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=_TZ).strftime("%d %b %Y, %I:%M %p")


def human_size(n) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def days_left(ts) -> int:
    return max(0, int((ts - time.time()) // 86400))


# ==============================================================================
# 4. DATABASE  (SQLite, thread-safe)
# ==============================================================================
_db_lock = threading.RLock()
_db_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, isolation_level=None)
_db_conn.row_factory = sqlite3.Row
_db_conn.execute("PRAGMA journal_mode=WAL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at REAL,
  balance REAL DEFAULT 0, referred_by INTEGER, ref_count INTEGER DEFAULT 0, ref_rewarded INTEGER DEFAULT 0,
  verified INTEGER DEFAULT 0, banned INTEGER DEFAULT 0,
  plan_id TEXT DEFAULT 'free', plan_expires REAL, trial_started REAL,
  exp_notified REAL DEFAULT 0, trial_notified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS plans(
  id TEXT PRIMARY KEY, name TEXT, price REAL, days INTEGER, max_projects INTEGER, ram_mb INTEGER,
  cpu_pct INTEGER, disk_mb INTEGER, auto_restart INTEGER DEFAULT 1, active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, entry TEXT, status TEXT DEFAULT 'stopped',
  pid INTEGER, started_at REAL, auto_restart INTEGER DEFAULT 1, req_hash TEXT, github_url TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS envs(project_id INTEGER, k TEXT, v TEXT, PRIMARY KEY(project_id, k));
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan_id TEXT, method TEXT, amount REAL,
  trx_id TEXT, status TEXT DEFAULT 'pending', created_at REAL, decided_by INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS ux_trx ON payments(trx_id) WHERE status != 'rejected';
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
"""

USER_COLS = {"username", "first_name", "balance", "referred_by", "ref_count", "ref_rewarded", "verified",
             "banned", "plan_id", "plan_expires", "trial_started", "exp_notified", "trial_notified"}
PROJECT_COLS = {"name", "entry", "status", "pid", "started_at", "auto_restart", "req_hash", "github_url"}
PLAN_COLS = {"name", "price", "days", "max_projects", "ram_mb", "cpu_pct", "disk_mb", "auto_restart", "active", "sort"}


# ---------------------------------------------------------------- primitives
def db_q(sql, args=()):
    with _db_lock:
        return [dict(r) for r in _db_conn.execute(sql, args).fetchall()]


def db_q1(sql, args=()):
    r = db_q(sql, args)
    return r[0] if r else None


def db_val(sql, args=()):
    r = db_q1(sql, args)
    return list(r.values())[0] if r else 0


def db_x(sql, args=()):
    with _db_lock:
        return _db_conn.execute(sql, args)


def _db_upd(table, key, id_, allowed, kw):
    kw = {k: v for k, v in kw.items() if k in allowed}
    if kw:
        sets = ",".join(f"{k}=?" for k in kw)
        db_x(f"UPDATE {table} SET {sets} WHERE {key}=?", (*kw.values(), id_))


def db_init():
    with _db_lock:
        _db_conn.executescript(SCHEMA)
    if not db_q1("SELECT 1 FROM plans LIMIT 1"):
        for r in SEED_PLANS:
            db_x("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?,?)", r)
    for k, v in DEFAULT_SETTINGS.items():
        db_x("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
    # one-time upgrades for databases created by an older version (never re-applied, so admin edits stick)
    if not db_q1("SELECT 1 FROM settings WHERE k='_m_student'"):
        if not db_q1("SELECT 1 FROM plans WHERE id='student'"):
            db_x("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?,?)", [r for r in SEED_PLANS if r[0] == "student"][0])
        db_x("INSERT INTO settings VALUES('_m_student','1')")
    if not db_q1("SELECT 1 FROM settings WHERE k='_m_support'"):
        if not db_get_setting("support").strip():
            db_set_setting("support", DEFAULT_SETTINGS["support"])
        db_x("INSERT INTO settings VALUES('_m_support','1')")
    if not db_q1("SELECT 1 FROM settings WHERE k='_m_support2'"):
        if db_get_setting("support").strip() in ("", "@Abdulahad1234577"):
            db_set_setting("support", DEFAULT_SETTINGS["support"])
        db_x("INSERT INTO settings VALUES('_m_support2','1')")


# ---------------------------------------------------------------- settings
def db_get_setting(k, default=""):
    r = db_q1("SELECT v FROM settings WHERE k=?", (k,))
    return r["v"] if r else DEFAULT_SETTINGS.get(k, default)


def db_set_setting(k, v):
    db_x("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, str(v)))


# ---------------------------------------------------------------- users
def db_upsert_user(tu, referred_by=None) -> bool:
    """Returns True if the user was newly created."""
    if db_q1("SELECT id FROM users WHERE id=?", (tu.id,)):
        db_x("UPDATE users SET username=?, first_name=? WHERE id=?", (tu.username, tu.first_name, tu.id))
        return False
    if referred_by == tu.id or (referred_by and not db_q1("SELECT 1 FROM users WHERE id=?", (referred_by,))):
        referred_by = None
    cur = db_x("INSERT OR IGNORE INTO users(id,username,first_name,joined_at,referred_by) VALUES(?,?,?,?,?)",
            (tu.id, tu.username, tu.first_name, time.time(), referred_by))
    return cur.rowcount == 1


def db_get_user(uid):
    return db_q1("SELECT * FROM users WHERE id=?", (uid,))


def db_update_user(user_id, /, **kw):
    _db_upd("users", "id", user_id, USER_COLS, kw)


def db_add_balance(uid, amount):
    db_x("UPDATE users SET balance=balance+? WHERE id=?", (amount, uid))


def db_charge_balance(uid, amount) -> bool:
    return db_x("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?", (amount, uid, amount)).rowcount == 1


def db_claim_referral(uid, amount):
    """Pays the referrer once. Returns referrer id or None."""
    with _db_lock:
        u = db_get_user(uid)
        if not u or not u["referred_by"] or u["ref_rewarded"]:
            return None
        _db_conn.execute("BEGIN")
        try:
            cur = _db_conn.execute("UPDATE users SET ref_rewarded=1 WHERE id=? AND ref_rewarded=0", (uid,))
            if cur.rowcount != 1:
                _db_conn.execute("ROLLBACK")
                return None
            _db_conn.execute("UPDATE users SET balance=balance+?, ref_count=ref_count+1 WHERE id=?",
                          (amount, u["referred_by"]))
            _db_conn.execute("COMMIT")
        except Exception:
            _db_conn.execute("ROLLBACK")
            raise
        return u["referred_by"]


def db_all_user_ids():
    return [r["id"] for r in db_q("SELECT id FROM users WHERE banned=0")]


# ---------------------------------------------------------------- plans
def db_get_plan(pid):
    return db_q1("SELECT * FROM plans WHERE id=?", (pid,))


def db_list_plans(active_only=False, paid_only=False):
    sql = "SELECT * FROM plans WHERE 1=1"
    if active_only:
        sql += " AND active=1"
    if paid_only:
        sql += " AND id!='free'"
    return db_q(sql + " ORDER BY sort, price")


def db_update_plan(plan_id, /, **kw):
    _db_upd("plans", "id", plan_id, PLAN_COLS, kw)


def db_add_plan(pid, name, price, days, max_projects, ram, cpu, disk):
    db_x("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,1,1,?)",
      (pid, name, price, days, max_projects, ram, cpu, disk, int(time.time()) % 100000))


def db_delete_plan(pid):
    db_x("DELETE FROM plans WHERE id=? AND id!='free'", (pid,))


def db_plan_state(u):
    """Effective plan of a user: paid plan if valid, otherwise FREE (7-day trial that starts on first run)."""
    now = time.time()
    if u["plan_id"] != "free" and u["plan_expires"] and u["plan_expires"] > now:
        p = db_get_plan(u["plan_id"])
        if p:
            return {"plan": p, "paid": True, "active": True, "expires": u["plan_expires"]}
    free = db_get_plan("free")
    ts = u["trial_started"]
    if ts is None:
        return {"plan": free, "paid": False, "active": True, "expires": None}
    exp = ts + free["days"] * 86400
    return {"plan": free, "paid": False, "active": exp > now, "expires": exp}


def db_activate_plan(uid, plan_id):
    plan, u, now = db_get_plan(plan_id), db_get_user(uid), time.time()
    same = u["plan_id"] == plan_id and u["plan_expires"] and u["plan_expires"] > now
    exp = (u["plan_expires"] if same else now) + plan["days"] * 86400
    db_update_user(uid, plan_id=plan_id, plan_expires=exp, exp_notified=0)
    return exp


# ---------------------------------------------------------------- projects
def db_create_project(uid, name):
    cur = db_x("INSERT INTO projects(user_id,name,created_at) VALUES(?,?,?)", (uid, name, time.time()))
    return cur.lastrowid


def db_get_project(pid):
    return db_q1("SELECT * FROM projects WHERE id=?", (pid,))


def db_user_projects(uid):
    return db_q("SELECT * FROM projects WHERE user_id=? ORDER BY id", (uid,))


def db_update_project(project_id, /, **kw):
    _db_upd("projects", "id", project_id, PROJECT_COLS, kw)


def db_delete_project(pid):
    db_x("DELETE FROM envs WHERE project_id=?", (pid,))
    db_x("DELETE FROM projects WHERE id=?", (pid,))


def db_running_count(uid):
    return db_val("SELECT COUNT(*) FROM projects WHERE user_id=? AND status='running'", (uid,))


def db_projects_by_status(status):
    return db_q("SELECT * FROM projects WHERE status=?", (status,))


def db_get_envs(pid):
    return db_q("SELECT k, v FROM envs WHERE project_id=? ORDER BY k", (pid,))


def db_set_env(pid, k, v):
    db_x("INSERT OR REPLACE INTO envs VALUES(?,?,?)", (pid, k, v))


def db_del_env(pid, k):
    db_x("DELETE FROM envs WHERE project_id=? AND k=?", (pid, k))


# ---------------------------------------------------------------- payments
def db_trx_exists(trx):
    return db_q1("SELECT 1 FROM payments WHERE trx_id=? AND status!='rejected'", (trx,)) is not None


def db_add_payment(uid, plan_id, method, amount, trx):
    return db_x("INSERT INTO payments(user_id,plan_id,method,amount,trx_id,created_at) VALUES(?,?,?,?,?,?)",
             (uid, plan_id, method, amount, trx, time.time())).lastrowid


def db_get_payment(pid):
    return db_q1("SELECT * FROM payments WHERE id=?", (pid,))


def db_pending_payments():
    return db_q("SELECT * FROM payments WHERE status='pending' ORDER BY id")


def db_user_pending_count(uid):
    return db_val("SELECT COUNT(*) FROM payments WHERE user_id=? AND status='pending'", (uid,))


# ---------------------------------------------------------------- stats / backup
def db_stats():
    now = time.time()
    return {
        "users": db_val("SELECT COUNT(*) FROM users"),
        "verified": db_val("SELECT COUNT(*) FROM users WHERE verified=1"),
        "banned": db_val("SELECT COUNT(*) FROM users WHERE banned=1"),
        "today": db_val("SELECT COUNT(*) FROM users WHERE joined_at>?", (now - 86400,)),
        "paid": db_val("SELECT COUNT(*) FROM users WHERE plan_id!='free' AND plan_expires>?", (now,)),
        "projects": db_val("SELECT COUNT(*) FROM projects"),
        "running": db_val("SELECT COUNT(*) FROM projects WHERE status='running'"),
        "pending": db_val("SELECT COUNT(*) FROM payments WHERE status='pending'"),
        "revenue": db_val("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'"),
    }


def db_backup_to(path):
    with _db_lock:
        dst = sqlite3.connect(str(path))
        _db_conn.backup(dst)
        dst.close()


db_init()


# ==============================================================================
# 5. CORE  (bot instance, routers, guards, UI helpers, channel check)
# ==============================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pyhost")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN সেট করা নেই। .env ফাইলে BOT_TOKEN=... দিন।")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=24)

M_PANEL = "🎛 কন্ট্রোল প্যানেল"
M_FILES = "📁 ফাইল ম্যানেজার"
M_UPGRADE = "💎 প্ল্যান আপগ্রেড"
M_ACCOUNT = "👤 আমার অ্যাকাউন্ট"
M_REFER = "🎁 রেফার ও আর্ন"
M_DOCS = "📖 ডকুমেন্টেশন"
M_SUPPORT = "🛰 সাপোর্ট হাব"
M_ADMIN = "🛠 অ্যাডমিন প্যানেল"

STATE = {}   # uid -> {"action": str, ...}
CACHE = {}   # misc per-user ephemeral data (file manager, entry list ...)
MENU = {}    # reply-keyboard text -> handler
STATES = {}  # state action -> handler
CB = {}      # callback head -> handler


def extra_admins():
    """Admins added from the Admin Panel (stored in DB)."""
    return {int(x) for x in db_get_setting("admins").replace(" ", "").split(",") if x.isdigit()}


def is_owner(uid):
    """Main admins = ADMIN_IDS in .env. Only they can manage admins and take DB backups."""
    return uid in ADMIN_IDS


def is_admin(uid):
    return uid in ADMIN_IDS or uid in extra_admins()


def set_state(uid, action, **data):
    STATE[uid] = {"action": action, **data}


def get_state(uid):
    return STATE.get(uid)


def clear_state(uid):
    STATE.pop(uid, None)


# ---------------------------------------------------------------- UI helpers
def kb(*rows):
    mk = types.InlineKeyboardMarkup()
    for row in rows:
        mk.row(*[types.InlineKeyboardButton(t, url=d) if d.startswith("http")
                 else types.InlineKeyboardButton(t, callback_data=d) for t, d in row])
    return mk


def main_menu(uid):
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    mk.add(M_PANEL, M_FILES)
    mk.add(M_UPGRADE, M_ACCOUNT)
    mk.add(M_REFER, M_DOCS)
    mk.add(M_SUPPORT)
    if is_admin(uid):
        mk.add(M_ADMIN)
    return mk


def ack(call, text=None, alert=False):
    try:
        bot.answer_callback_query(call.id, text, show_alert=alert)
    except Exception:  # already answered / expired
        pass


def show(obj, text, markup=None):
    """Edit the message when triggered from a button, otherwise send a new one."""
    if isinstance(obj, types.CallbackQuery):
        try:
            bot.edit_message_text(text, obj.message.chat.id, obj.message.message_id, reply_markup=markup,
                                  disable_web_page_preview=True)
            return
        except ApiTelegramException as e:
            if "message is not modified" in str(e):
                return
        except Exception:  # noqa
            pass
        bot.send_message(obj.message.chat.id, text, reply_markup=markup, disable_web_page_preview=True)
    else:
        bot.send_message(obj.chat.id, text, reply_markup=markup, disable_web_page_preview=True)


def notify(uid, text, markup=None):
    try:
        bot.send_message(uid, text, reply_markup=markup, disable_web_page_preview=True)
        return True
    except Exception as e:  # noqa
        log.info("notify %s failed: %s", uid, e)
        return False


def notify_admins(text, markup=None):
    for a in ADMIN_IDS | extra_admins():
        notify(a, text, markup)


_bot_username = {}


def bot_username():
    if "u" not in _bot_username:
        _bot_username["u"] = bot.get_me().username
    return _bot_username["u"]


def plan_card(p):
    bots = str(p["max_projects"]).translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))
    s = (f"{p['name']} — <b>৳{p['price']:g}</b> / {p['days']} দিন\n"
         f"   🤖 {bots}টি বট চালাতে পারবেন • 💾 RAM: {p['ram_mb']}MB • 🧠 CPU: {p['cpu_pct']}% • "
         f"💽 ডিস্ক: {p['disk_mb']}MB")
    return s + ("\n   ♻️ অটো-রিস্টার্ট ✔" if p["auto_restart"] else "")


def plan_left(st):
    if st["paid"]:
        return f"{days_left(st['expires'])} দিন"
    if st["expires"] is None:
        return f"ট্রায়াল শুরু হয়নি (প্রথম চালুর পর {st['plan']['days']} দিন)"
    if st["active"]:
        return f"ট্রায়াল: {fmt_dur(st['expires'] - time.time())} বাকি"
    return "❌ মেয়াদ শেষ — প্ল্যান কিনুন"


# ---------------------------------------------------------------- channel verification
def channels():
    return [c.strip() if c.strip().startswith("@") else "@" + c.strip()
            for c in db_get_setting("channels").split(",") if c.strip()]


_member_cache = {}


def is_member(uid):
    chs = channels()
    if not chs:
        return True
    t = _member_cache.get(uid)
    if t and time.time() - t < 180:
        return True
    for ch in chs:
        try:
            m = bot.get_chat_member(ch, uid)
            if m.status not in ("member", "administrator", "creator"):
                _member_cache.pop(uid, None)
                return False
        except Exception as e:  # bot not admin in channel, wrong username...
            log.warning("membership check failed for %s: %s", ch, e)
            return False
    _member_cache[uid] = time.time()
    return True


def send_join(chat_id):
    rows = [[(f"📢 জয়েন করুন {c}", f"https://t.me/{c.lstrip('@')}")] for c in channels()]
    rows.append([("✅ ভেরিফাই করুন", "chk")])
    bot.send_message(chat_id, "🔒 <b>বট ব্যবহার করতে আগে আমাদের চ্যানেলে জয়েন করুন</b>\n\n"
                              "জয়েন করে নিচের ✅ বাটনে চাপ দিন।", reply_markup=kb(*rows))


# ---------------------------------------------------------------- guard + routers
def guarded(fn, gate=True):
    @functools.wraps(fn)
    def wrapper(obj, *a):
        is_cb = isinstance(obj, types.CallbackQuery)
        u = obj.from_user
        chat_id = obj.message.chat.id if is_cb else obj.chat.id
        try:
            db_upsert_user(u)
            row = db_get_user(u.id)
            if row["banned"]:
                if is_cb:
                    ack(obj, "🚫 আপনাকে ব্যান করা হয়েছে", True)
                else:
                    bot.send_message(chat_id, "🚫 আপনাকে ব্যান করা হয়েছে।")
                return
            if gate and not is_admin(u.id) and not is_member(u.id):
                clear_state(u.id)
                send_join(chat_id)
                return
            return fn(obj, *a)
        except ApiTelegramException as e:
            if "message is not modified" not in str(e):
                log.warning("Telegram API error: %s", e)
        except Exception:  # noqa
            log.exception("handler error")
            try:
                bot.send_message(chat_id, "⚠️ একটি ত্রুটি হয়েছে, আবার চেষ্টা করুন।")
            except Exception:  # noqa
                pass
        finally:
            if is_cb:
                ack(obj)
    return wrapper


def cb(head, gate=True):
    def deco(fn):
        CB[head] = guarded(fn, gate)
        return fn
    return deco


def menu(text):
    def deco(fn):
        MENU[text] = guarded(fn)
        return fn
    return deco


def state(action):
    def deco(fn):
        STATES[action] = guarded(fn)
        return fn
    return deco


@bot.callback_query_handler(func=lambda c: True)
def _on_callback(call):
    parts = (call.data or "").split(":")
    fn = CB.get(parts[0])
    if fn:
        fn(call, parts[1:])
    else:
        ack(call)


@bot.message_handler(content_types=["text"], func=lambda m: not (m.text or "").startswith("/"))
def _on_text(m):
    uid = m.from_user.id
    t = m.text.strip()
    if t in MENU:
        clear_state(uid)
        return MENU[t](m)
    st = get_state(uid)
    if st and st["action"] in STATES:
        return STATES[st["action"]](m, st)
    guarded(lambda mm: bot.send_message(mm.chat.id, "👇 মেনু থেকে একটি অপশন বেছে নিন।",
                                        reply_markup=main_menu(mm.from_user.id)))(m)


@bot.message_handler(content_types=["document", "photo", "video", "audio", "voice", "animation", "sticker"])
def _on_media(m):
    st = get_state(m.from_user.id)
    if st and st["action"] in STATES:
        return STATES[st["action"]](m, st)
    guarded(lambda mm: bot.send_message(
        mm.chat.id, "📤 ফাইল আপলোড করতে: 📁 <b>ফাইল ম্যানেজার</b> → প্রজেক্ট → 📤 আপলোড।"))(m)


# ==============================================================================
# 6. PROCESSES  (run / monitor / limits / backups for user projects)
# ==============================================================================
try:
    import resource
except ImportError:  # non-posix
    resource = None


def notify_owner(uid, text, pid=None):
    """Message a project owner; with 📋 লগ / 🎛 প্যানেল buttons when a project id is given."""
    markup = kb([("📋 লগ", f"pl:{pid}"), ("🎛 প্যানেল", f"p:{pid}")]) if pid else None
    notify(uid, text, markup)


RUNNING = {}          # project_id -> Popen
STARTING = {}         # project_id -> time the start began
START_TTL = 1200      # a start older than 20 min is considered stuck and may be replaced


def is_starting(pid):
    t = STARTING.get(pid)
    return bool(t) and time.time() - t < START_TTL
_ps_cache = {}
_high_cpu = {}
_restarts = {}
_lock = threading.RLock()

APP_FILE = str(Path(__file__).resolve())   # this very file — also acts as the sandboxed file worker
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
RESERVED_ENV = {"PATH", "PYTHONPATH", "HOME", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONHOME"}
ENTRY_CANDIDATES = ["main.py", "bot.py", "app.py", "index.py", "run.py", "start.py", "__main__.py"]


# ------------------------------------------------------------------ paths / isolation
def iso_uid(user_id):
    if ISOLATE and hasattr(os, "geteuid") and os.geteuid() == 0:
        return ISO_BASE_UID + (int(user_id) % 2_000_000_000)
    return None


def project_dir(p):
    return PROJECTS_DIR / str(p["user_id"]) / str(p["id"])


def files_dir(p):
    return project_dir(p) / "files"


def libs_dir(p):
    return project_dir(p) / ".pylibs"


def log_path(p):
    return LOGS_DIR / f"{p['id']}.log"


def init_permissions():
    if hasattr(os, "geteuid") and os.geteuid() == 0 and ISOLATE:
        for d in (DATA_DIR, PROJECTS_DIR):
            os.chmod(d, 0o711)
        for d in (LOGS_DIR, BACKUP_DIR):
            os.chmod(d, 0o700)
        if DB_PATH.exists():
            os.chmod(DB_PATH, 0o600)
        log.info("User isolation ENABLED (per-user uid)")
    else:
        log.warning("User isolation DISABLED — user code can read the bot's files. Run as root or use Docker.")


def prepare_dirs(p):
    dirs = (project_dir(p).parent, project_dir(p), files_dir(p), libs_dir(p))
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    uid = iso_uid(p["user_id"])
    if uid is not None:
        for d in dirs:
            os.chown(d, uid, uid)
            os.chmod(d, 0o700)


def _make_preexec(user_id):
    uid = iso_uid(user_id)

    def fn():
        if resource:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            fsize = MAX_FILE_MB * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        if uid is not None:
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
            if resource:
                resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
    return fn


def _env(p, extra=None):
    e = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(project_dir(p)),
         "LANG": "C.UTF-8"}
    e.update(extra or {})
    return e


def run_as(p, cmd, cwd=None, env=None, timeout=60, stdin=None, pass_fds=()):
    """Run a command as the project owner's unprivileged OS user (sanitized env)."""
    return subprocess.run(cmd, cwd=cwd, env=env or _env(p), input=stdin, capture_output=True, timeout=timeout,
                          preexec_fn=_make_preexec(p["user_id"]), pass_fds=pass_fds)


def fs(p, op, rel="", *extra, stdin=None, base=None, timeout=120, pass_fds=()):
    """Sandboxed file op. Returns (True, dict|bytes) or (False, error_text)."""
    base = str(base or files_dir(p))
    cmd = [sys.executable, APP_FILE, "--fsworker", op, base, rel, *map(str, extra)]
    try:
        r = run_as(p, cmd, cwd=base, stdin=stdin, timeout=timeout, pass_fds=pass_fds)
    except subprocess.TimeoutExpired:
        return False, "টাইমআউট"
    except Exception as e:  # noqa
        return False, str(e)
    if op == "read":
        return (True, r.stdout) if r.returncode == 0 else (False, r.stderr.decode(errors="replace").strip())
    try:
        d = json.loads(r.stdout.decode() or "{}")
    except Exception:  # noqa
        return False, (r.stderr.decode(errors="replace") or "internal error")[-300:]
    return (True, d) if d.get("ok") else (False, d.get("error", "error"))


def disk_usage(p) -> int:
    ok, d = fs(p, "du", "", base=project_dir(p))
    return d["size"] if ok else 0


def detect_entry(p):
    ok, d = fs(p, "find_py")
    if not ok:
        return None
    files = d["files"]
    for c in ENTRY_CANDIDATES:
        if c in files:
            return c
    for f in files:
        if os.path.basename(f) in ENTRY_CANDIDATES:
            return f
    return files[0] if len(files) == 1 else None


# ------------------------------------------------------------------ pip / git
def pip_install(p, args, timeout=900):
    cmd = [sys.executable, "-m", "pip", "install", "--target", str(libs_dir(p)), "--no-cache-dir",
           "--disable-pip-version-check", "--upgrade", *args]
    try:
        r = run_as(p, cmd, cwd=str(files_dir(p)), env=_env(p, {"PIP_NO_INPUT": "1"}), timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "Timeout (15 মিনিট)"
    out = ANSI.sub("", (r.stdout + r.stderr).decode(errors="replace"))
    return r.returncode == 0, out[-3000:]


def git_sync(p, url):
    fdir = str(files_dir(p))
    env = _env(p, {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"})
    g = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never"]
    steps = []
    if not (files_dir(p) / ".git").exists():
        steps += [g + ["init", "-q"], g + ["remote", "add", "origin", url]]
    else:
        steps += [g + ["remote", "set-url", "origin", url]]
    steps += [g + ["fetch", "--depth", "1", "origin", "HEAD"], g + ["reset", "--hard", "FETCH_HEAD"]]
    out = ""
    for cmd in steps:
        try:
            r = run_as(p, cmd, cwd=fdir, env=env, timeout=180)
        except FileNotFoundError:
            return False, "সার্ভারে git ইন্সটল নেই"
        except subprocess.TimeoutExpired:
            return False, "Timeout"
        out = (r.stdout + r.stderr).decode(errors="replace")
        if r.returncode != 0:
            return False, re.sub(r"https://[^@\s]+@", "https://***@", out)[-1500:]
    return True, out[-500:]


# ------------------------------------------------------------------ start / stop
def is_running(pid) -> bool:
    pr = RUNNING.get(pid)
    return pr is not None and pr.poll() is None


def start_project(p):
    """Blocking. Returns (True, os_pid) or (False, error_text)."""
    pid = p["id"]
    with _lock:
        if is_starting(pid):
            return False, "⏳ আগের স্টার্ট এখনো চলছে (requirements ইন্সটল হতে সময় লাগে)। শেষ হলে নিজে থেকেই চালু হবে — একটু পরে 🔄 রিফ্রেশ দিন।"
        if is_running(pid):
            return False, "এটি ইতোমধ্যে চালু আছে।"
        STARTING[pid] = t0 = time.time()
    try:
        return _start(pid, t0)
    except Exception as e:  # noqa
        log.exception("start failed")
        return False, f"{type(e).__name__}: {e}"
    finally:
        with _lock:
            if STARTING.get(pid) == t0:
                STARTING.pop(pid, None)


def _start(pid, t0):
    p = db_get_project(pid)
    prepare_dirs(p)
    entry = p["entry"] or detect_entry(p)
    if not entry:
        return False, "কোনো এন্ট্রি ফাইল (যেমন main.py) পাওয়া যায়নি। ফাইল আপলোড করুন অথবা '📝 এন্ট্রি ফাইল' থেকে বেছে নিন।"
    ok, err = fs(p, "exists", entry)
    if not ok:
        return False, err
    db_update_project(pid, entry=entry)

    ok, data = fs(p, "read", "requirements.txt", 1_000_000)
    if ok and data.strip():
        h = hashlib.md5(data).hexdigest()
        if h != p["req_hash"]:
            notify(p["user_id"], f"📦 <b>{esc(p['name'])}</b>: requirements.txt ইন্সটল হচ্ছে… ১-২ মিনিট লাগতে পারে, শেষ হলে অটো চালু হবে।")
            ok2, out = pip_install(p, ["-r", "requirements.txt"])
            if not ok2:
                return False, "❌ requirements.txt ইন্সটল ব্যর্থ:\n" + out[-1500:]
            db_update_project(pid, req_hash=h)

    env = {r["k"]: r["v"] for r in db_get_envs(pid) if r["k"] not in RESERVED_ENV}
    env.update(_env(p, {"PYTHONPATH": str(libs_dir(p)), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}))

    if STARTING.get(pid) != t0:      # ⏹ pressed (or replaced) while installing
        return False, "স্টার্ট বাতিল করা হয়েছে।"
    lp = log_path(p)
    if lp.exists() and lp.stat().st_size > 5 * 1024 * 1024:
        lp.write_bytes(lp.read_bytes()[-1024 * 1024:])
    with open(lp, "ab", buffering=0) as lf:
        lf.write(f"\n===== START {time.strftime('%F %T')} | {entry} =====\n".encode())
        proc = subprocess.Popen([sys.executable, "-u", entry], cwd=str(files_dir(p)), env=env,
                                stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                                start_new_session=True, preexec_fn=_make_preexec(p["user_id"]))
    with _lock:
        RUNNING[pid] = proc
    db_update_project(pid, status="running", pid=proc.pid, started_at=time.time())
    return True, proc.pid


def _kill_tree(proc_or_pid):
    pid = proc_or_pid.pid if hasattr(proc_or_pid, "pid") else proc_or_pid
    try:
        kids = psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        kids = []
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 3)):
        try:
            if pgid:
                os.killpg(pgid, sig)
            else:
                os.kill(pid, sig)
        except OSError:
            pass
        if hasattr(proc_or_pid, "wait"):
            try:
                proc_or_pid.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        else:
            time.sleep(1)
            break
    for k in kids:
        try:
            k.kill()
        except psutil.Error:
            pass


def stop_project(pid, status="stopped"):
    with _lock:
        proc = RUNNING.pop(pid, None)
        STARTING.pop(pid, None)       # also cancels a start that is still installing
    if proc is not None:
        _kill_tree(proc)
    _high_cpu.pop(pid, None)
    db_update_project(pid, status=status, pid=None)


def delete_files(p):
    stop_project(p["id"])
    shutil.rmtree(project_dir(p), ignore_errors=True)
    try:
        log_path(p).unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ stats / logs
def _get_ps(pid):
    pr = _ps_cache.get(pid)
    if pr is None or not pr.is_running():
        pr = psutil.Process(pid)
        _ps_cache[pid] = pr
        if len(_ps_cache) > 3000:
            for k in [k for k, v in _ps_cache.items() if not v.is_running()]:
                _ps_cache.pop(k, None)
    return pr


def _measure(os_pid):
    ram, cpu = 0.0, 0.0
    try:
        root = _get_ps(os_pid)
        for pr in [root] + root.children(recursive=True):
            try:
                pr = _get_ps(pr.pid)
                ram += pr.memory_info().rss
                cpu += pr.cpu_percent(None)
            except psutil.Error:
                pass
    except psutil.Error:
        pass
    return ram / 1048576, cpu


def proc_stats(p):
    proc = RUNNING.get(p["id"])
    if proc is not None and proc.poll() is None:
        ram, cpu = _measure(proc.pid)
        return {"running": True, "ram": ram, "cpu": cpu, "uptime": time.time() - (p["started_at"] or time.time())}
    return {"running": False, "ram": 0.0, "cpu": 0.0, "uptime": 0}


def tail_log(p, lines=40, maxchars=3000):
    lp = log_path(p)
    if not lp.exists():
        return ""
    with open(lp, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 65536))
        txt = f.read().decode(errors="replace")
    txt = ANSI.sub("", txt).replace("\r", "")
    return "\n".join(txt.splitlines()[-lines:])[-maxchars:]


def full_log(p) -> bytes:
    lp = log_path(p)
    if not lp.exists():
        return b""
    with open(lp, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 5 * 1024 * 1024))
        return ANSI.sub("", f.read().decode(errors="replace")).encode()


PIPMAP = {"telebot": "pyTelegramBotAPI", "cv2": "opencv-python-headless", "PIL": "pillow", "bs4": "beautifulsoup4",
          "yaml": "pyyaml", "dotenv": "python-dotenv", "telegram": "python-telegram-bot", "sklearn": "scikit-learn",
          "Crypto": "pycryptodome", "dns": "dnspython", "jwt": "pyjwt", "pyrogram": "pyrogram tgcrypto"}
HINTS = [
    (r"InvalidToken|Unauthorized|401", "বট টোকেন ভুল বা বাতিল হয়েছে — টোকেন চেক করুন।"),
    (r"terminated by other getUpdates|Conflict", "একই টোকেনে আরেকটি বট চলছে — অন্য জায়গায় বন্ধ করুন।"),
    (r"SyntaxError|IndentationError|TabError", "কোডে সিনট্যাক্স/ইন্ডেন্টেশন ত্রুটি আছে।"),
    (r"FileNotFoundError", "কোডে যে ফাইলটা খুঁজছে সেটা আপলোড করা হয়নি বা পাথ ভুল।"),
    (r"PermissionError", "ফাইল/ফোল্ডারে পারমিশন নেই — প্রজেক্ট ফোল্ডারের ভেতরে লিখুন।"),
    (r"MemoryError", "র‍্যাম শেষ — প্ল্যান আপগ্রেড করুন বা কোড অপ্টিমাইজ করুন।"),
    (r"Address already in use", "পোর্ট আগে থেকেই ব্যবহৃত হচ্ছে।"),
    (r"KeyError: '[A-Z_]+'", "সম্ভবত এনভায়রনমেন্ট ভেরিয়েবল সেট করা হয়নি — 🔐 এনভায়রনমেন্ট ভেরিয়েবল দেখুন।"),
]


def diagnose(text):
    m = re.search(r"No module named '([\w\.]+)'", text)
    if m:
        mod = m.group(1).split(".")[0]
        pkg = PIPMAP.get(mod, mod)
        return f"মডিউল <code>{esc(mod)}</code> নেই — 📦 Package Manager থেকে <code>{esc(pkg)}</code> ইন্সটল করুন।"
    for pat, hint in HINTS:
        if re.search(pat, text):
            return hint
    return None


# ------------------------------------------------------------------ backup
def make_backup(p):
    """Returns (Path, None) or (None, error)."""
    d = BACKUP_DIR / str(p["user_id"])
    d.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w\-]", "_", p["name"])[:30] or "project"
    path = d / f"{p['id']}_{slug}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    with open(path, "w+b") as f:
        ok, res = fs(p, "zip", "", f.fileno(), pass_fds=(f.fileno(),), timeout=600)
    if not ok:
        path.unlink(missing_ok=True)
        return None, res
    olds = sorted(d.glob(f"{p['id']}_*.zip"))
    for o in olds[:-3]:
        o.unlink(missing_ok=True)
    return path, None


# ------------------------------------------------------------------ monitor
def _handle_exit(p, code):
    pid = p["id"]
    with _lock:
        if RUNNING.pop(pid, None) is None:
            return                    # already handled by another thread
    _high_cpu.pop(pid, None)
    tail = tail_log(p, 200, 6000)
    if "===== START" in tail:
        tail = tail.rsplit("===== START", 1)[1].split("\n", 1)[-1]
    tail = "\n".join(tail.splitlines()[-25:])[-2500:]
    if code == 0:
        db_update_project(pid, status="stopped", pid=None)
        notify_owner(p["user_id"], f"ℹ️ <b>{esc(p['name'])}</b> স্বাভাবিকভাবে শেষ হয়েছে (exit 0)।\n"
                             "বট হলে সম্ভবত কোডে polling/loop নেই।", pid)
        return
    db_update_project(pid, status="crashed", pid=None)
    hint = diagnose(tail)
    msg = (f"❌ <b>প্রজেক্ট ক্র্যাশ করেছে:</b> {esc(p['name'])}\n🔢 Exit code: <code>{code}</code>\n\n"
           f"<pre>{esc(tail) or '(লগ খালি)'}</pre>")
    if hint:
        msg += f"\n💡 <b>সমাধান:</b> {hint}"
    plan = db_plan_state(db_get_user(p["user_id"]))
    if p["auto_restart"] and plan["plan"]["auto_restart"] and plan["active"]:
        now = time.time()
        hist = [t for t in _restarts.get(pid, []) if now - t < 600]
        if len(hist) < MAX_AUTO_RESTARTS:
            hist.append(now)
            _restarts[pid] = hist
            notify_owner(p["user_id"], msg + "\n\n♻️ ৫ সেকেন্ড পর অটো-রিস্টার্ট হবে…", pid)
            threading.Timer(5, lambda: start_project(db_get_project(pid)) if db_get_project(pid) else None).start()
            return
        msg += "\n\n⛔ বারবার ক্র্যাশ করায় অটো-রিস্টার্ট বন্ধ। কোড ঠিক করে নিজে চালু করুন।"
    notify_owner(p["user_id"], msg, pid)


def _tick():
    for pid, proc in list(RUNNING.items()):
        p = db_get_project(pid)
        if not p:
            with _lock:
                RUNNING.pop(pid, None)
            _kill_tree(proc)
            continue
        code = proc.poll()
        if code is not None:
            _handle_exit(p, code)
            continue
        st = db_plan_state(db_get_user(p["user_id"]))
        plan = st["plan"]
        if not st["active"]:
            stop_project(pid)
            notify_owner(p["user_id"], f"⏰ আপনার প্ল্যান/ট্রায়াল শেষ, তাই <b>{esc(p['name'])}</b> বন্ধ করা হয়েছে।\n"
                                 "💎 প্ল্যান আপগ্রেড করে আবার চালু করুন।", pid)
            continue
        ram, cpu = _measure(proc.pid)
        if ram > plan["ram_mb"]:
            stop_project(pid, "crashed")
            notify_owner(p["user_id"], f"💥 <b>{esc(p['name'])}</b> র‍্যাম লিমিট ({plan['ram_mb']} MB) ছাড়িয়েছে "
                                 f"({ram:.0f} MB), তাই বন্ধ করা হয়েছে।\nকোড অপ্টিমাইজ করুন বা প্ল্যান আপগ্রেড করুন।", pid)
            continue
        if cpu > plan["cpu_pct"]:
            _high_cpu[pid] = _high_cpu.get(pid, 0) + 1
            if _high_cpu[pid] >= CPU_STRIKES:
                stop_project(pid, "crashed")
                notify_owner(p["user_id"], f"🔥 <b>{esc(p['name'])}</b> দীর্ঘ সময় CPU লিমিট ({plan['cpu_pct']}%) "
                                     f"ছাড়িয়েছে ({cpu:.0f}%), তাই বন্ধ করা হয়েছে।", pid)
        else:
            _high_cpu[pid] = 0


def _monitor_loop():
    while True:
        try:
            _tick()
        except Exception:  # noqa
            log.exception("monitor error")
        time.sleep(MONITOR_INTERVAL)


def start_monitor():
    threading.Thread(target=_monitor_loop, daemon=True, name="monitor").start()


def restore_projects():
    """After a bot/server restart: kill orphans and bring 'running' projects back up."""
    for p in db_projects_by_status("running"):
        if p["pid"] and psutil.pid_exists(p["pid"]):
            try:
                pr = psutil.Process(p["pid"])
                if pr.cwd() == str(files_dir(p)):
                    _kill_tree(p["pid"])
            except psutil.Error:
                pass
        st = db_plan_state(db_get_user(p["user_id"]))
        if st["active"] and p["entry"]:
            threading.Thread(target=start_project, args=(p,), daemon=True).start()
        else:
            db_update_project(p["id"], status="stopped", pid=None)


# ==============================================================================
# 7. HANDLERS — user (/start, account, referral, docs, support)
# ==============================================================================
def welcome_message():
    """Title + body come from Admin Panel -> 👋 ওয়েলকাম মেসেজ (empty = default)."""
    title = (db_get_setting("welcome_title") or "").strip() or DEFAULT_SETTINGS["welcome_title"]
    body = (db_get_setting("welcome_text") or "").strip() or DEFAULT_SETTINGS["welcome_text"]
    free = db_get_plan("free")
    return (f"🚀 <b>{esc(title)}</b>\n\n{esc(body)}\n\n━━━━━━━━━━━━━━\n"
            f"🎁 ফ্রি প্ল্যান: <b>{free['max_projects']}টি বট</b>, প্রথম চালুর পর <b>{free['days']} দিন</b>।\n"
            f"💎 তারপর প্ল্যান কিনতে হবে — অথবা রেফার করে ব্যালেন্স আর্ন করুন!\n\n"
            f"শুরু করতে 🎛 <b>কন্ট্রোল প্যানেল</b> থেকে নতুন প্রজেক্ট বানান।")


def complete_verification(uid, chat_id):
    u = db_get_user(uid)
    if not u["verified"]:
        db_update_user(uid, verified=1)
        reward = float(db_get_setting("referral_reward") or 0)
        ref = db_claim_referral(uid, reward)
        if ref:
            notify(ref, f"🎉 <b>নতুন রেফার!</b> আপনার লিংকে একজন জয়েন করেছে।\n💰 +৳{reward:g} ব্যালেন্সে যোগ হয়েছে।")
    bot.send_message(chat_id, welcome_message(), reply_markup=main_menu(uid))


@bot.message_handler(commands=["start"])
def cmd_start(m):
    uid = m.from_user.id
    ref = None
    parts = m.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref_") and parts[1][4:].isdigit():
        ref = int(parts[1][4:])
    db_upsert_user(m.from_user, referred_by=ref)
    if db_get_user(uid)["banned"]:
        return bot.send_message(m.chat.id, "🚫 আপনাকে ব্যান করা হয়েছে।")
    clear_state(uid)
    if not is_admin(uid) and not is_member(uid):
        return send_join(m.chat.id)
    complete_verification(uid, m.chat.id)


@bot.message_handler(commands=["cancel", "help", "menu"])
def cmd_misc(m):
    clear_state(m.from_user.id)
    guarded(lambda mm: bot.send_message(mm.chat.id, "✅ ঠিক আছে। মেনু থেকে বেছে নিন।",
                                        reply_markup=main_menu(mm.from_user.id)))(m)


@cb("chk", gate=False)
def c_check(call, a):
    uid = call.from_user.id
    if is_admin(uid) or is_member(uid):
        ack(call, "✅ ভেরিফাই সফল!")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:  # noqa
            pass
        complete_verification(uid, call.message.chat.id)
    else:
        ack(call, "❌ আপনি এখনো চ্যানেলে জয়েন করেননি!", True)


@menu(M_ACCOUNT)
def m_account(m):
    u = db_get_user(m.from_user.id)
    st = db_plan_state(u)
    cnt = len(db_user_projects(u["id"]))
    show(m, (f"👤 <b>আমার অ্যাকাউন্ট</b>\n━━━━━━━━━━━━━━\n"
             f"🆔 ID: <code>{u['id']}</code>\n"
             f"👑 প্ল্যান: <b>{st['plan']['name']}</b>\n"
             f"⏳ বাকি: {plan_left(st)}\n"
             f"📦 প্রজেক্ট: {cnt}/{st['plan']['max_projects']}\n"
             f"💾 RAM লিমিট: {st['plan']['ram_mb']} MB • 🧠 CPU: {st['plan']['cpu_pct']}%\n"
             f"💰 ব্যালেন্স: <b>৳{u['balance']:g}</b>\n"
             f"👥 রেফার: {u['ref_count']} জন\n"
             f"📅 জয়েন: {fmt_ts(u['joined_at'])}"),
         kb([("💎 প্ল্যান আপগ্রেড", "up"), ("🎁 রেফার", "ref")]))


@cb("ref")
def c_ref(call, a):
    m_refer(call)


@menu(M_REFER)
def m_refer(obj):
    uid = obj.from_user.id
    u = db_get_user(uid)
    reward = float(db_get_setting("referral_reward") or 0)
    link = f"https://t.me/{bot_username()}?start=ref_{uid}"
    show(obj, (f"🎁 <b>রেফার ও আর্ন</b>\n━━━━━━━━━━━━━━\n"
               f"প্রতিটি সফল রেফারে পাবেন <b>৳{reward:g}</b>!\n"
               f"(রেফার করা ব্যক্তি চ্যানেল ভেরিফাই করলে ব্যালেন্স যোগ হবে)\n\n"
               f"🔗 আপনার লিংক:\n<code>{link}</code>\n\n"
               f"👥 মোট রেফার: <b>{u['ref_count']}</b>\n"
               f"💰 ব্যালেন্স: <b>৳{u['balance']:g}</b>\n\n"
               f"ব্যালেন্স দিয়ে 💎 প্ল্যান কিনতে পারবেন।"),
         kb([("📤 শেয়ার করুন", f"https://t.me/share/url?url={link}&text=ফ্রি বট হোস্টিং!")],
            [("💎 প্ল্যান কিনুন", "up")]))


@menu(M_DOCS)
def m_docs(m):
    show(m, ("📖 <b>ডকুমেন্টেশন</b>\n━━━━━━━━━━━━━━\n"
             "<b>1️⃣ প্রজেক্ট বানান</b>\n🎛 কন্ট্রোল প্যানেল → ➕ নতুন প্রজেক্ট\n\n"
             "<b>2️⃣ ফাইল আপলোড</b>\n.py ফাইল বা পুরো প্রজেক্টের .zip পাঠান (সর্বোচ্চ 20MB)। "
             "<code>requirements.txt</code> থাকলে চালুর সময় অটো ইন্সটল হবে।\n\n"
             "<b>3️⃣ টোকেন/সিক্রেট</b>\n🔐 এনভায়রনমেন্ট ভেরিয়েবলে সেট করুন, কোডে "
             "<code>os.getenv('BOT_TOKEN')</code> দিয়ে নিন।\n\n"
             "<b>4️⃣ চালু করুন</b>\n▶️ চালু করুন চাপুন। এরর হলে আমি সাথে সাথে লগ + সমাধান পাঠাব।\n\n"
             "<b>5️⃣ আরও টুল</b>\n• 📦 Package Manager — pip প্যাকেজ ইন্সটল\n• 🐙 গিটহাব সিঙ্ক\n"
             "• 💾 ব্যাকআপ — ZIP ডাউনলোড\n• ♻️ অটো-রিস্টার্ট (পেইড প্ল্যান)\n\n"
             "<b>⚠️ নিয়ম:</b> লিমিটের বেশি RAM/CPU ব্যবহার করলে প্রজেক্ট বন্ধ হবে। "
             "অবৈধ/ক্ষতিকর কাজে ব্যবহার নিষিদ্ধ।"))


@menu(M_SUPPORT)
def m_support(m):
    sup = db_get_setting("support").strip()
    rows = [[("📢 অফিসিয়াল চ্যানেল", f"https://t.me/{channels()[0].lstrip('@')}")]] if channels() else []
    if sup:
        rows.insert(0, [("💬 সাপোর্টে মেসেজ", f"https://t.me/{sup.lstrip('@')}")])
    show(m, "🛰 <b>সাপোর্ট হাব</b>\n\nসমস্যা হলে নিচের বাটনে যোগাযোগ করুন। "
            "পেমেন্ট সমস্যায় TrxID সহ জানাবেন।" + (f"\n\n💬 সাপোর্ট: <b>{esc(sup)}</b>" if sup else ""),
         kb(*rows) if rows else None)


# ==============================================================================
# 8. HANDLERS — control panel (projects, start/stop, logs, backup, delete)
# ==============================================================================
def own(call, pid):
    try:
        p = db_get_project(int(pid))
    except (TypeError, ValueError):
        p = None
    if not p or (p["user_id"] != call.from_user.id and not is_admin(call.from_user.id)):
        ack(call, "❌ প্রজেক্ট পাওয়া যায়নি", True)
        return None
    return p


def owned_by(uid, pid):
    p = db_get_project(pid)
    return p if p and (p["user_id"] == uid or is_admin(uid)) else None


def panel_text(p):
    u = db_get_user(p["user_id"])
    st = db_plan_state(u)
    plan = st["plan"]
    s = proc_stats(p)
    status = ("🟢 অনলাইন" if s["running"] else "🟡 চালু হচ্ছে…" if is_starting(p["id"])
              else "🟠 ক্র্যাশড" if p["status"] == "crashed" else "🔴 অফলাইন")
    ram = f"{s['ram']:.1f} / {plan['ram_mb']} MB" if s["running"] else f"— / {plan['ram_mb']} MB"
    return (f"⚙️ <b>প্রজেক্ট:</b> <code>{esc(p['name'])}</code>\n"
            f"<blockquote>📡 স্ট্যাটাস: {status}\n💾 র‍্যাম: {ram}\n"
            f"🧠 সিপিউ: {s['cpu']:.1f}% / {plan['cpu_pct']}%\n"
            f"⬆️ আপটাইম: {fmt_dur(s['uptime']) if s['running'] else '0m'}\n"
            f"📝 এন্ট্রি: {esc(p['entry'] or 'সেট করা হয়নি')}</blockquote>\n"
            f"<blockquote>👑 প্ল্যান: <b>{plan['name']}</b>\n⏳ বাকি সময়: {plan_left(st)}</blockquote>")


def panel_kb(p):
    pid = p["id"]
    rows = [[("🔄 রিফ্রেশ", f"p:{pid}"), ("📋 লগ দেখুন", f"pl:{pid}")]]
    if is_running(pid):
        rows.append([("🔁 রিস্টার্ট", f"prs:{pid}"), ("⏹ বন্ধ করুন", f"px:{pid}")])
    elif is_starting(pid):
        rows.append([("⏳ চালু হচ্ছে… রিফ্রেশ", f"p:{pid}"), ("⏹ বাতিল", f"px:{pid}")])
    else:
        rows.append([("▶️ চালু করুন", f"ps:{pid}")])
    rows += [[("🐙 গিটহাব সিঙ্ক", f"pg:{pid}"), ("📝 এন্ট্রি ফাইল", f"pen:{pid}")],
             [("📁 ফাইলস", f"fm:{pid}"), ("💾 ব্যাকআপ", f"pbk:{pid}")],
             [("📦 Package Manager", f"ppm:{pid}")],
             [("🔐 এনভায়রনমেন্ট ভেরিয়েবল", f"pev:{pid}")],
             [(f"♻️ অটো-রিস্টার্ট: {'ON' if p['auto_restart'] else 'OFF'}", f"par:{pid}"),
              ("🗑 ডিলিট", f"pdl:{pid}")],
             [("◀️ পেছনে যান", "cp")]]
    return kb(*rows)


def show_panel(obj, p, extra=""):
    p = db_get_project(p["id"])
    show(obj, panel_text(p) + extra, panel_kb(p))


# ------------------------------------------------------------------ list / create
@menu(M_PANEL)
def m_panel(m):
    show_projects(m)


@cb("cp")
def c_cp(call, a):
    clear_state(call.from_user.id)
    show_projects(call)


def show_projects(obj):
    uid = obj.from_user.id
    u = db_get_user(uid)
    st = db_plan_state(u)
    projs = db_user_projects(uid)
    rows = []
    for p in projs:
        dot = "🟢" if is_running(p["id"]) else "🟡" if is_starting(p["id"]) else "🟠" if p["status"] == "crashed" else "🔴"
        rows.append([(f"{dot} {p['name']}", f"p:{p['id']}")])
    rows.append([("➕ নতুন প্রজেক্ট", "np")])
    show(obj, (f"🎛 <b>কন্ট্রোল প্যানেল</b>\n━━━━━━━━━━━━━━\n"
               f"👑 প্ল্যান: <b>{st['plan']['name']}</b> • ⏳ {plan_left(st)}\n"
               f"📦 প্রজেক্ট: {len(projs)}/{st['plan']['max_projects']}\n\n"
               + ("আপনার প্রজেক্ট বেছে নিন 👇" if projs else "কোনো প্রজেক্ট নেই। ➕ নতুন প্রজেক্ট বানান।")),
         kb(*rows))


@cb("np")
def c_new(call, a):
    uid = call.from_user.id
    st = db_plan_state(db_get_user(uid))
    if len(db_user_projects(uid)) >= st["plan"]["max_projects"]:
        return ack(call, f"⛔ আপনার প্ল্যানে সর্বোচ্চ {st['plan']['max_projects']}টি প্রজেক্ট। প্ল্যান আপগ্রেড করুন।", True)
    set_state(uid, "new_name")
    show(call, "📝 <b>নতুন প্রজেক্টের নাম লিখুন</b>\n(২-৩০ অক্ষর; ইংরেজি/সংখ্যা/স্পেস/-/_)\n\nবাতিল: /cancel")


@state("new_name")
def s_new_name(m, st):
    uid = m.from_user.id
    name = (m.text or "").strip() if m.content_type == "text" else ""
    if not re.fullmatch(r"[\w \-]{2,30}", name, flags=re.ASCII):
        return bot.send_message(m.chat.id, "❌ নাম ঠিক নয়। ২-৩০ অক্ষর, শুধু ইংরেজি/সংখ্যা/স্পেস/-/_ ব্যবহার করুন।")
    plan = db_plan_state(db_get_user(uid))["plan"]
    if len(db_user_projects(uid)) >= plan["max_projects"]:
        clear_state(uid)
        return bot.send_message(m.chat.id, "⛔ প্রজেক্ট লিমিট শেষ।")
    pid = db_create_project(uid, name)
    prepare_dirs(db_get_project(pid))
    set_state(uid, "upload", pid=pid, rel="")
    bot.send_message(m.chat.id, f"✅ প্রজেক্ট <b>{esc(name)}</b> তৈরি হয়েছে!\n\n"
                                f"📤 এখন আপনার <code>.py</code> ফাইল বা পুরো প্রজেক্টের <code>.zip</code> পাঠান।\n"
                                f"(requirements.txt থাকলে অটো ইন্সটল হবে। একাধিক ফাইল পাঠাতে পারেন।)",
                     reply_markup=kb([("✅ আপলোড শেষ", f"fupd:{pid}")]))


@cb("p")
def c_panel(call, a):
    p = own(call, a[0])
    if p:
        clear_state(call.from_user.id)
        show_panel(call, p)


# ------------------------------------------------------------------ start / stop / restart
def _start_job(chat_id, pid, prefix=""):
    p = db_get_project(pid)
    if not p:
        return
    ok, info = start_project(p)
    if ok:
        time.sleep(2)     # catch an instant crash (missing module, wrong token, syntax error...) right here
        pr = RUNNING.get(pid)
        if pr is None or pr.poll() is not None:
            if pr is not None:
                _handle_exit(db_get_project(pid), pr.poll())   # sends the crash log + fix hint
            return
        p = db_get_project(pid)
        bot.send_message(chat_id, panel_text(p) + f"\n{prefix}✅ <b>প্রজেক্ট চালু হয়েছে!</b>\n🆔 PID: <code>{info}</code>",
                         reply_markup=panel_kb(p))
    else:
        bot.send_message(chat_id, f"❌ <b>চালু করা যায়নি:</b>\n<pre>{esc(str(info)[-2500:])}</pre>",
                         reply_markup=panel_kb(p))


def _can_start(call, p):
    u = db_get_user(p["user_id"])
    st = db_plan_state(u)
    if not st["active"]:
        ack(call, "⛔ ফ্রি ট্রায়াল/প্ল্যান শেষ। 💎 প্ল্যান কিনুন।", True)
        return None
    if db_running_count(p["user_id"]) >= st["plan"]["max_projects"] and not is_running(p["id"]):
        ack(call, "⛔ প্ল্যানের লিমিটের বেশি প্রজেক্ট একসাথে চালানো যাবে না।", True)
        return None
    prefix = ""
    if not st["paid"] and u["trial_started"] is None:
        db_update_user(u["id"], trial_started=time.time())
        prefix = f"🎁 ফ্রি ট্রায়াল শুরু! শেষ হবে: <b>{fmt_ts(time.time() + st['plan']['days'] * 86400)}</b>\n"
    return prefix


@cb("ps")
def c_start(call, a):
    p = own(call, a[0])
    if not p:
        return
    if is_running(p["id"]):
        return ack(call, "ইতোমধ্যে চলছে")
    if is_starting(p["id"]):
        return ack(call, "⏳ চালু হচ্ছে — requirements ইন্সটল হতে সময় লাগে। একটু পরে 🔄 রিফ্রেশ দিন।", True)
    prefix = _can_start(call, p)
    if prefix is None:
        return
    ack(call, "⏳ চালু হচ্ছে…")
    threading.Thread(target=_start_job, args=(call.message.chat.id, p["id"], prefix), daemon=True).start()


@cb("px")
def c_stop(call, a):
    p = own(call, a[0])
    if p:
        stop_project(p["id"])
        ack(call, "⏹ বন্ধ করা হয়েছে")
        show_panel(call, p)


@cb("prs")
def c_restart(call, a):
    p = own(call, a[0])
    if not p:
        return
    prefix = _can_start(call, p)
    if prefix is None:
        return
    ack(call, "🔁 রিস্টার্ট হচ্ছে…")

    def job():
        stop_project(p["id"])
        _start_job(call.message.chat.id, p["id"], prefix)
    threading.Thread(target=job, daemon=True).start()


@cb("par")
def c_autorestart(call, a):
    p = own(call, a[0])
    if not p:
        return
    plan = db_plan_state(db_get_user(p["user_id"]))["plan"]
    if not plan["auto_restart"]:
        return ack(call, "♻️ অটো-রিস্টার্ট শুধু পেইড প্ল্যানে পাওয়া যায়। 💎 আপগ্রেড করুন।", True)
    db_update_project(p["id"], auto_restart=0 if p["auto_restart"] else 1)
    show_panel(call, p)


# ------------------------------------------------------------------ logs
@cb("pl")
def c_logs(call, a):
    p = own(call, a[0])
    if not p:
        return
    txt = tail_log(p, 40, 3000)
    show(call, f"📋 <b>লগ — {esc(p['name'])}</b>\n<pre>{esc(txt) if txt.strip() else '(লগ খালি)'}</pre>",
         kb([("🔄 রিফ্রেশ", f"pl:{p['id']}"), ("📥 ফুল লগ", f"pld:{p['id']}")], [("◀️ পেছনে", f"p:{p['id']}")]))


@cb("pld")
def c_logfile(call, a):
    p = own(call, a[0])
    if not p:
        return
    data = full_log(p)
    if not data:
        return ack(call, "লগ খালি", True)
    bot.send_document(call.message.chat.id, (f"{p['name']}_log.txt", data))


# ------------------------------------------------------------------ backup
@cb("pbk")
def c_backup(call, a):
    p = own(call, a[0])
    if not p:
        return
    ack(call, "💾 ব্যাকআপ তৈরি হচ্ছে…")

    def job():
        path, err = make_backup(p)
        if not path:
            return bot.send_message(call.message.chat.id, f"❌ ব্যাকআপ ব্যর্থ: {esc(err)}")
        size = path.stat().st_size
        if size > 49 * 1024 * 1024:
            return bot.send_message(call.message.chat.id, "⚠️ ব্যাকআপ ৫০MB এর বেশি, টেলিগ্রামে পাঠানো যাচ্ছে না।")
        with open(path, "rb") as f:
            bot.send_document(call.message.chat.id, f, caption=f"💾 <b>{esc(p['name'])}</b> ব্যাকআপ • {fmt_ts(time.time())}\n"
                              "রিস্টোর করতে: ফাইল ম্যানেজারে এই ZIP আপলোড করুন।")
    threading.Thread(target=job, daemon=True).start()


# ------------------------------------------------------------------ delete
@cb("pdl")
def c_delete(call, a):
    p = own(call, a[0])
    if p:
        show(call, f"⚠️ <b>{esc(p['name'])}</b> সম্পূর্ণ ডিলিট করবেন? ফাইল, লগ, এনভায়রনমেন্ট সব মুছে যাবে!",
             kb([("✅ হ্যাঁ, ডিলিট", f"pdy:{p['id']}"), ("❌ না", f"p:{p['id']}")]))


@cb("pdy")
def c_delete_yes(call, a):
    p = own(call, a[0])
    if p:
        delete_files(p)
        db_delete_project(p["id"])
        ack(call, "🗑 ডিলিট হয়েছে")
        show_projects(call)


# ==============================================================================
# 9. HANDLERS — tools (pip, env vars, GitHub, entry file)
# ==============================================================================
def _quota_ok(p, extra=0):
    plan = db_plan_state(db_get_user(p["user_id"]))["plan"]
    return disk_usage(p) + extra < plan["disk_mb"] * 1024 * 1024


PKG_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-\[\],=<>!~]*")
ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")
GIT_URL = re.compile(r"https://(?:[\w.\-]+(?::[\w.\-%]+)?@)?(?:github\.com|gitlab\.com|bitbucket\.org)/"
                     r"[\w.\-]+/[\w.\-]+?(?:\.git)?/?")


# ------------------------------------------------------------------ package manager
@cb("ppm")
def c_pm(call, a):
    p = own(call, a[0])
    if not p:
        return
    pid = p["id"]
    show(call, f"📦 <b>Package Manager</b> — {esc(p['name'])}\n\npip প্যাকেজ আপনার প্রজেক্টে আলাদাভাবে ইন্সটল হয়।",
         kb([("➕ প্যাকেজ ইন্সটল", f"ppi:{pid}"), ("📋 ইন্সটল করা", f"ppl:{pid}")],
            [("📥 requirements.txt ইন্সটল", f"ppr:{pid}")],
            [("🧹 সব লাইব্রেরি মুছুন", f"ppc:{pid}")],
            [("◀️ পেছনে", f"p:{pid}")]))


@cb("ppi")
def c_pip_ask(call, a):
    p = own(call, a[0])
    if p:
        set_state(call.from_user.id, "pip_in", pid=p["id"])
        show(call, "📦 প্যাকেজের নাম লিখুন (স্পেস দিয়ে একাধিক)\nযেমন: <code>requests aiohttp==3.9.5 pyTelegramBotAPI</code>\n\nবাতিল: /cancel")


def _pip_job(chat_id, msg_id, p, args):
    ok, out = pip_install(p, args)
    # drop cached requirements hash so the next start re-checks nothing unexpected
    txt = (f"{'✅ ইন্সটল সফল' if ok else '❌ ইন্সটল ব্যর্থ'}\n<pre>{esc(out[-1800:])}</pre>")
    try:
        bot.edit_message_text(txt, chat_id, msg_id, reply_markup=kb([("📦 Package Manager", f"ppm:{p['id']}")]))
    except Exception:  # noqa
        bot.send_message(chat_id, txt, reply_markup=kb([("📦 Package Manager", f"ppm:{p['id']}")]))


@state("pip_in")
def s_pip(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    toks = (m.text or "").split() if m.content_type == "text" else []
    if not p or not toks or len(toks) > 15 or not all(PKG_TOKEN.fullmatch(t) for t in toks):
        return bot.send_message(m.chat.id, "❌ প্যাকেজ নাম ঠিক নয়। শুধু নাম/ভার্সন লিখুন (যেমন requests==2.31.0)।")
    if not _quota_ok(p):
        clear_state(uid)
        return bot.send_message(m.chat.id, "⛔ ডিস্ক কোটা শেষ। ফাইল মুছুন বা প্ল্যান আপগ্রেড করুন।")
    clear_state(uid)
    msg = bot.send_message(m.chat.id, f"⏳ ইন্সটল হচ্ছে: <code>{esc(' '.join(toks))}</code> …")
    threading.Thread(target=_pip_job, args=(m.chat.id, msg.message_id, p, toks), daemon=True).start()


@cb("ppr")
def c_pip_req(call, a):
    p = own(call, a[0])
    if not p:
        return
    ok, _ = fs(p, "exists", "requirements.txt")
    if not ok:
        return ack(call, "❌ requirements.txt আপলোড করা নেই", True)
    if not _quota_ok(p):
        return ack(call, "⛔ ডিস্ক কোটা শেষ", True)
    ack(call, "⏳ ইন্সটল হচ্ছে…")
    msg = bot.send_message(call.message.chat.id, "⏳ requirements.txt ইন্সটল হচ্ছে…")
    db_update_project(p["id"], req_hash=None)
    threading.Thread(target=_pip_job, args=(call.message.chat.id, msg.message_id, p, ["-r", "requirements.txt"]),
                     daemon=True).start()


@cb("ppl")
def c_pip_list(call, a):
    p = own(call, a[0])
    if not p:
        return
    libs = libs_dir(p)
    ok, d = fs(p, "ls", "", base=libs)
    names = []
    if ok:
        for it in d["items"]:
            if it["n"].endswith(".dist-info"):
                names.append(it["n"][:-10].replace("-", " ", 1) if "-" in it["n"] else it["n"][:-10])
    txt = "\n".join(names[:80]) or "(কিছু ইন্সটল করা নেই)"
    show(call, f"📋 <b>ইন্সটল করা প্যাকেজ</b>\n<pre>{esc(txt)}</pre>", kb([("◀️ পেছনে", f"ppm:{p['id']}")]))


@cb("ppc")
def c_pip_clear(call, a):
    p = own(call, a[0])
    if p:
        shutil.rmtree(libs_dir(p), ignore_errors=True)
        prepare_dirs(p)
        db_update_project(p["id"], req_hash=None)
        ack(call, "🧹 লাইব্রেরি মুছে ফেলা হয়েছে", True)


# ------------------------------------------------------------------ environment variables
@cb("pev")
def c_env(call, a):
    p = own(call, a[0])
    if not p:
        return
    envs = db_get_envs(p["id"])
    rows = [[(f"🗑 {e['k']}", f"pevd:{p['id']}:{e['k']}")] for e in envs]
    rows.append([("➕ নতুন ভেরিয়েবল", f"peva:{p['id']}")])
    rows.append([("◀️ পেছনে", f"p:{p['id']}")])
    lst = "\n".join(f"• <code>{esc(e['k'])}</code> = ••••{esc(e['v'][-3:])}" for e in envs) or "কোনো ভেরিয়েবল নেই"
    show(call, f"🔐 <b>এনভায়রনমেন্ট ভেরিয়েবল</b> — {esc(p['name'])}\n\n{lst}\n\n"
               f"কোডে: <code>os.getenv('KEY')</code>\nডিলিট করতে ভেরিয়েবলের বাটনে চাপুন।", kb(*rows))


@cb("peva")
def c_env_add(call, a):
    p = own(call, a[0])
    if p:
        if len(db_get_envs(p["id"])) >= 50:
            return ack(call, "সর্বোচ্চ ৫০টি ভেরিয়েবল", True)
        set_state(call.from_user.id, "env_add", pid=p["id"])
        show(call, "🔐 <code>KEY=VALUE</code> ফরম্যাটে পাঠান\nযেমন: <code>BOT_TOKEN=123:ABC...</code>\n\n"
                   "⚠️ মেসেজটি সেভ হলে আমি ডিলিট করে দেব।\nবাতিল: /cancel")


@state("env_add")
def s_env_add(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    text = (m.text or "") if m.content_type == "text" else ""
    if "=" not in text or not p:
        return bot.send_message(m.chat.id, "❌ ফরম্যাট: KEY=VALUE")
    k, v = text.split("=", 1)
    k, v = k.strip(), v.strip()
    if not ENV_KEY.fullmatch(k) or not v or len(v) > 2000:
        return bot.send_message(m.chat.id, "❌ KEY শুধু ইংরেজি অক্ষর/সংখ্যা/_ (সর্বোচ্চ ৩২), VALUE খালি হবে না।")
    db_set_env(p["id"], k, v)
    clear_state(uid)
    try:
        bot.delete_message(m.chat.id, m.message_id)
    except Exception:  # noqa
        pass
    bot.send_message(m.chat.id, f"✅ <code>{esc(k)}</code> সেভ হয়েছে। রিস্টার্ট দিলে কার্যকর হবে।",
                     reply_markup=kb([("🔐 ভেরিয়েবল তালিকা", f"pev:{p['id']}")]))


@cb("pevd")
def c_env_del(call, a):
    p = own(call, a[0])
    if p and len(a) > 1:
        db_del_env(p["id"], a[1])
        ack(call, "🗑 মুছে ফেলা হয়েছে")
        c_env(call, [str(p["id"])])


# ------------------------------------------------------------------ GitHub sync
@cb("pg")
def c_git(call, a):
    p = own(call, a[0])
    if not p:
        return
    url = p["github_url"]
    if not url:
        set_state(call.from_user.id, "git_url", pid=p["id"])
        return show(call, "🐙 <b>গিটহাব সিঙ্ক</b>\nরিপোর URL পাঠান:\n<code>https://github.com/user/repo</code>\n"
                          "প্রাইভেট রিপোর জন্য: <code>https://user:TOKEN@github.com/user/repo</code>\n\nবাতিল: /cancel")
    shown = re.sub(r"https://[^@\s]+@", "https://***@", url)
    show(call, f"🐙 <b>গিটহাব সিঙ্ক</b>\n🔗 <code>{esc(shown)}</code>\n\nসিঙ্ক করলে রিপোর লেটেস্ট ফাইল এই প্রজেক্টে আসবে "
               f"(একই নামের ফাইল ওভাররাইট হবে)।",
         kb([("🔄 এখনই সিঙ্ক", f"pgs:{p['id']}")], [("✏️ URL পরিবর্তন", f"pgu:{p['id']}")], [("◀️ পেছনে", f"p:{p['id']}")]))


@cb("pgu")
def c_git_url(call, a):
    p = own(call, a[0])
    if p:
        set_state(call.from_user.id, "git_url", pid=p["id"])
        show(call, "🐙 নতুন রিপো URL পাঠান:\n\nবাতিল: /cancel")


@state("git_url")
def s_git_url(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    url = (m.text or "").strip() if m.content_type == "text" else ""
    if not p or not GIT_URL.fullmatch(url):
        return bot.send_message(m.chat.id, "❌ শুধু https://github.com / gitlab.com / bitbucket.org এর রিপো URL দিন।")
    db_update_project(p["id"], github_url=url)
    clear_state(uid)
    try:
        bot.delete_message(m.chat.id, m.message_id)
    except Exception:  # noqa
        pass
    bot.send_message(m.chat.id, "✅ URL সেভ হয়েছে।", reply_markup=kb([("🔄 এখনই সিঙ্ক", f"pgs:{p['id']}")]))


@cb("pgs")
def c_git_sync(call, a):
    p = own(call, a[0])
    if not p or not p["github_url"]:
        return
    if not _quota_ok(p):
        return ack(call, "⛔ ডিস্ক কোটা শেষ", True)
    ack(call, "⏳ সিঙ্ক হচ্ছে…")
    chat = call.message.chat.id

    def job():
        ok, out = git_sync(p, p["github_url"])
        extra = ""
        if ok and not db_get_project(p["id"])["entry"]:
            e = detect_entry(p)
            if e:
                db_update_project(p["id"], entry=e)
                extra = f"\n📝 এন্ট্রি ফাইল সেট: <code>{esc(e)}</code>"
        bot.send_message(chat, ("✅ সিঙ্ক সম্পন্ন!" if ok else "❌ সিঙ্ক ব্যর্থ:") + f"\n<pre>{esc(out)}</pre>" + extra,
                         reply_markup=kb([("🎛 প্যানেল", f"p:{p['id']}")]))
    threading.Thread(target=job, daemon=True).start()


# ------------------------------------------------------------------ entry file
@cb("pen")
def c_entry(call, a):
    p = own(call, a[0])
    if not p:
        return
    ok, d = fs(p, "find_py")
    files = d["files"] if ok else []
    if not files:
        return ack(call, "❌ কোনো .py ফাইল নেই। আগে ফাইল আপলোড করুন।", True)
    CACHE[(call.from_user.id, "entries")] = files
    rows = [[(("✅ " if f == p["entry"] else "📄 ") + f[:40], f"pse:{p['id']}:{i}")] for i, f in enumerate(files[:30])]
    rows.append([("◀️ পেছনে", f"p:{p['id']}")])
    show(call, "📝 <b>কোন ফাইলটি চালু হবে?</b>\nবর্তমান: <code>" + esc(p["entry"] or "—") + "</code>", kb(*rows))


@cb("pse")
def c_entry_set(call, a):
    p = own(call, a[0])
    files = CACHE.get((call.from_user.id, "entries"), [])
    if p and len(a) > 1 and a[1].isdigit() and int(a[1]) < len(files):
        db_update_project(p["id"], entry=files[int(a[1])])
        ack(call, "✅ এন্ট্রি ফাইল সেট হয়েছে")
        show_panel(call, p)


# ==============================================================================
# 10. HANDLERS — file manager & uploads
# ==============================================================================
PER_PAGE = 14
TEXT_EXT = (".py", ".txt", ".json", ".md", ".env", ".cfg", ".ini", ".yml", ".yaml", ".toml", ".csv", ".log", ".html",
            ".js", ".sh", ".sql", ".xml", ".conf")


def _fm(uid):
    return CACHE.get((uid, "fm"))


def _join(rel, name):
    return posixpath.normpath(posixpath.join(rel or "", name)).lstrip("./") if rel or name else ""


def render(obj, note=""):
    uid = obj.from_user.id
    s = _fm(uid)
    p = owned_by(uid, s["pid"]) if s else None
    if not p:
        return show(obj, "❌ সেশন শেষ। আবার ফাইল ম্যানেজার খুলুন।")
    ok, d = fs(p, "ls", s["rel"])
    if not ok:
        s["rel"] = ""
        ok, d = fs(p, "ls", "")
        if not ok:
            return show(obj, f"❌ {esc(d)}", kb([("◀️ পেছনে", f"p:{p['id']}")]))
    items = d["items"]
    s["items"] = items
    pages = max(1, (len(items) + PER_PAGE - 1) // PER_PAGE)
    s["page"] = min(s.get("page", 0), pages - 1)
    chunk = items[s["page"] * PER_PAGE:(s["page"] + 1) * PER_PAGE]
    rows = []
    for idx, it in enumerate(chunk, start=s["page"] * PER_PAGE):
        label = ("📁 " + it["n"][:30]) if it["d"] else f"📄 {it['n'][:24]}  ({human_size(it['s'])})"
        rows.append([(label, f"fo:{idx}")])
    nav = []
    if s["page"] > 0:
        nav.append(("⬅️", f"fpg:{s['page'] - 1}"))
    if s["page"] < pages - 1:
        nav.append(("➡️", f"fpg:{s['page'] + 1}"))
    if nav:
        rows.append(nav)
    rows += [[("⬆️ উপরে", "fu"), ("📤 আপলোড", "fup")],
             [("➕ ফোল্ডার", "fnew"), ("🔄 রিফ্রেশ", "frf")],
             [("◀️ প্রজেক্ট", f"p:{p['id']}")]]
    used = human_size(disk_usage(p))
    show(obj, (f"📁 <b>ফাইল ম্যানেজার</b> — {esc(p['name'])}\n📍 <code>/{esc(s['rel'])}</code>\n"
               f"💽 ব্যবহৃত: {used} / {db_plan_state(db_get_user(p['user_id']))['plan']['disk_mb']} MB\n"
               f"{note}" + ("\n(ফোল্ডার খালি)" if not items else "")), kb(*rows))


@menu(M_FILES)
def m_files(m):
    projs = db_user_projects(m.from_user.id)
    if not projs:
        return show(m, "📁 আগে একটি প্রজেক্ট বানান: 🎛 কন্ট্রোল প্যানেল → ➕ নতুন প্রজেক্ট")
    show(m, "📁 <b>কোন প্রজেক্টের ফাইল দেখবেন?</b>", kb(*[[(f"📦 {p['name']}", f"fm:{p['id']}")] for p in projs]))


@cb("fm")
def c_fm(call, a):
    p = own(call, a[0])
    if p:
        clear_state(call.from_user.id)
        CACHE[(call.from_user.id, "fm")] = {"pid": p["id"], "rel": "", "items": [], "page": 0}
        render(call)


@cb("frf")
def c_refresh(call, a):
    render(call)


@cb("fpg")
def c_page(call, a):
    s = _fm(call.from_user.id)
    if s and a and a[0].isdigit():
        s["page"] = int(a[0])
        render(call)


@cb("fu")
def c_fm_up(call, a):
    s = _fm(call.from_user.id)
    if s:
        s["rel"] = posixpath.dirname(s["rel"]) if s["rel"] else ""
        s["page"] = 0
        render(call)


def _item(call, a):
    s = _fm(call.from_user.id)
    if not s or not a or not a[0].isdigit() or int(a[0]) >= len(s["items"]):
        ack(call, "সেশন শেষ, রিফ্রেশ করুন", True)
        return None, None, None
    p = owned_by(call.from_user.id, s["pid"])
    it = s["items"][int(a[0])]
    return s, p, it


@cb("fo")
def c_open(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    rel = _join(s["rel"], it["n"])
    if it["d"]:
        s["rel"], s["page"] = rel, 0
        return render(call)
    i = a[0]
    rows = [[("👁 দেখুন", f"fv:{i}"), ("📥 ডাউনলোড", f"fdl:{i}")], [("✏️ এডিট", f"fe:{i}"), ("🗑 ডিলিট", f"frm:{i}")]]
    if it["n"].endswith(".py"):
        rows.append([("📝 এন্ট্রি ফাইল করুন", f"fen:{i}")])
    if it["n"].lower().endswith(".zip"):
        rows.append([("📦 আনজিপ", f"fz:{i}")])
    rows.append([("◀️ ফোল্ডারে ফিরুন", "frf")])
    show(call, f"📄 <b>{esc(it['n'])}</b>\n📏 {human_size(it['s'])}\n📍 <code>/{esc(rel)}</code>", kb(*rows))


@cb("fv")
def c_view(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    ok, data = fs(p, "read", _join(s["rel"], it["n"]), 3000)
    if not ok:
        return ack(call, str(data)[:150], True)
    if b"\x00" in data[:500]:
        return ack(call, "বাইনারি ফাইল — ডাউনলোড করুন", True)
    txt = data[:3000].decode(errors="replace")
    show(call, f"👁 <b>{esc(it['n'])}</b>{' (প্রথম অংশ)' if len(data) > 3000 else ''}\n<pre>{esc(txt)[:3300]}</pre>",
         kb([("✏️ এডিট", f"fe:{a[0]}"), ("📥 ডাউনলোড", f"fdl:{a[0]}")], [("◀️ পেছনে", "frf")]))


@cb("fdl")
def c_download(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    if it["s"] > 49 * 1024 * 1024:
        return ack(call, "ফাইল ৫০MB এর বেশি", True)
    ok, data = fs(p, "read", _join(s["rel"], it["n"]), 49 * 1024 * 1024, timeout=180)
    if not ok:
        return ack(call, str(data)[:150], True)
    bot.send_document(call.message.chat.id, (it["n"], data))


@cb("frm")
def c_rm_ask(call, a):
    s, p, it = _item(call, a)
    if it:
        show(call, f"🗑 <b>{esc(it['n'])}</b> ডিলিট করবেন?", kb([("✅ হ্যাঁ", f"fry:{a[0]}"), ("❌ না", "frf")]))


@cb("fry")
def c_rm(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    ok, err = fs(p, "rm", _join(s["rel"], it["n"]))
    render(call, "🗑 ডিলিট হয়েছে" if ok else f"❌ {esc(err)}")


@cb("fen")
def c_file_entry(call, a):
    s, p, it = _item(call, a)
    if it:
        db_update_project(p["id"], entry=_join(s["rel"], it["n"]))
        ack(call, "✅ এন্ট্রি ফাইল সেট হয়েছে", True)


@cb("fz")
def c_unzip(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    ok, d = fs(p, "unzipfile", _join(s["rel"], it["n"]), MAX_UNZIP_MB * 1024 * 1024, s["rel"],
                         timeout=300)
    render(call, f"✅ আনজিপ হয়েছে ({d['count']}টি ফাইল)" if ok else f"❌ {esc(d)}")


@cb("fnew")
def c_newdir(call, a):
    s = _fm(call.from_user.id)
    if s:
        set_state(call.from_user.id, "fm_mkdir", rel=s["rel"], pid=s["pid"])
        show(call, "📁 নতুন ফোল্ডারের নাম লিখুন:\n\nবাতিল: /cancel")


@state("fm_mkdir")
def s_mkdir(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    name = (m.text or "").strip() if m.content_type == "text" else ""
    if not p or not re.fullmatch(r"[\w.\-]{1,40}", name):
        return bot.send_message(m.chat.id, "❌ নাম ঠিক নয় (ইংরেজি/সংখ্যা/._-)।")
    ok, err = fs(p, "mkdir", _join(st["rel"], name))
    clear_state(uid)
    bot.send_message(m.chat.id, "✅ ফোল্ডার তৈরি হয়েছে" if ok else f"❌ {esc(err)}",
                     reply_markup=kb([("📁 ফাইল ম্যানেজার", f"fm:{p['id']}")]))


# ------------------------------------------------------------------ edit text file
@cb("fe")
def c_edit(call, a):
    s, p, it = _item(call, a)
    if not it:
        return
    if it["s"] > 100_000 or not it["n"].lower().endswith(TEXT_EXT):
        return ack(call, "শুধু ১০০KB এর ছোট টেক্সট ফাইল এডিট করা যায়", True)
    set_state(call.from_user.id, "fm_edit", pid=p["id"], rel=_join(s["rel"], it["n"]))
    show(call, f"✏️ <b>{esc(it['n'])}</b>\nনতুন সম্পূর্ণ কনটেন্ট মেসেজে পাঠান (পুরনো কনটেন্ট রিপ্লেস হবে)।\n"
               f"বা একটি ফাইল পাঠান যেটা এটাকে রিপ্লেস করবে।\n\nবাতিল: /cancel")


@state("fm_edit")
def s_edit(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    if not p:
        return
    if m.content_type == "text":
        data = m.text.encode()
    elif m.content_type == "document" and m.document.file_size <= 100_000:
        data = bot.download_file(bot.get_file(m.document.file_id).file_path)
    else:
        return bot.send_message(m.chat.id, "❌ টেক্সট বা ছোট ফাইল পাঠান।")
    ok, err = fs(p, "write", st["rel"], 100_000, stdin=data)
    clear_state(uid)
    bot.send_message(m.chat.id, "✅ ফাইল আপডেট হয়েছে। রিস্টার্ট দিলে কার্যকর হবে।" if ok else f"❌ {esc(err)}",
                     reply_markup=kb([("🎛 প্যানেল", f"p:{p['id']}")]))


# ------------------------------------------------------------------ upload
@cb("fup")
def c_upload(call, a):
    s = _fm(call.from_user.id)
    if s:
        set_state(call.from_user.id, "upload", pid=s["pid"], rel=s["rel"])
        show(call, f"📤 <b>আপলোড মোড</b>\n📍 <code>/{esc(s['rel'])}</code>\n\n.py / requirements.txt / যেকোনো ফাইল বা "
                   f".zip পাঠান (সর্বোচ্চ {MAX_UPLOAD_MB}MB)। ZIP অটো আনজিপ হবে।",
             kb([("✅ আপলোড শেষ", f"fupd:{s['pid']}")]))


@cb("fupd")
def c_upload_done(call, a):
    p = own(call, a[0])
    if not p:
        return
    clear_state(call.from_user.id)
    if not db_get_project(p["id"])["entry"]:
        e = detect_entry(p)
        if e:
            db_update_project(p["id"], entry=e)
    show_panel(call, p)


@state("upload")
def s_upload(m, st):
    uid = m.from_user.id
    p = owned_by(uid, st["pid"])
    if not p:
        return clear_state(uid)
    if m.content_type != "document":
        return bot.send_message(m.chat.id, "📤 ফাইল (document) পাঠান। শেষ হলে ✅ আপলোড শেষ চাপুন।")
    doc = m.document
    if doc.file_size > MAX_UPLOAD_MB * 1024 * 1024:
        return bot.send_message(m.chat.id, f"❌ ফাইল {MAX_UPLOAD_MB}MB এর বেশি।")
    if not _quota_ok(p, doc.file_size):
        return bot.send_message(m.chat.id, "⛔ ডিস্ক কোটা শেষ। ফাইল মুছুন বা প্ল্যান আপগ্রেড করুন।")
    name = os.path.basename(doc.file_name or "file")
    name = re.sub(r"[^\w.\-]", "_", name)[:80] or "file"
    data = bot.download_file(bot.get_file(doc.file_id).file_path)
    if name.lower().endswith(".zip"):
        ok, d = fs(p, "unzip", st["rel"], MAX_UNZIP_MB * 1024 * 1024, stdin=data, timeout=300)
        msg = f"✅ <b>{esc(name)}</b> আনজিপ হয়েছে ({d['count']}টি ফাইল)" if ok else f"❌ {esc(d)}"
    else:
        ok, d = fs(p, "write", _join(st["rel"], name), MAX_UPLOAD_MB * 1024 * 1024, stdin=data)
        msg = f"✅ <b>{esc(name)}</b> সেভ হয়েছে ({human_size(len(data))})" if ok else f"❌ {esc(d)}"
    if ok and not db_get_project(p["id"])["entry"]:
        e = detect_entry(p)
        if e:
            db_update_project(p["id"], entry=e)
            msg += f"\n📝 এন্ট্রি ফাইল অটো-সেট: <code>{esc(e)}</code>"
    bot.send_message(m.chat.id, msg + "\n\nআরও পাঠান বা শেষ করুন 👇", reply_markup=kb([("✅ আপলোড শেষ", f"fupd:{p['id']}")]))


# ==============================================================================
# 11. HANDLERS — plans & payments
# ==============================================================================
METHODS = {"bkash": "bKash", "nagad": "Nagad"}


def show_plans(obj):
    uid = obj.from_user.id
    u = db_get_user(uid)
    st = db_plan_state(u)
    plans = db_list_plans(active_only=True, paid_only=True)
    txt = (f"💎 <b>প্ল্যান সমূহ</b>\n━━━━━━━━━━━━━━\n"
           f"আপনার প্ল্যান: <b>{st['plan']['name']}</b> • ⏳ {plan_left(st)}\n"
           f"💰 ব্যালেন্স: ৳{u['balance']:g}\n\n")
    txt += "\n\n".join(plan_card(p) for p in plans) if plans else "এখন কোনো প্ল্যান চালু নেই।"
    rows = [[(f"{p['name']} — ৳{p['price']:g}", f"buy:{p['id']}")] for p in plans]
    show(obj, txt, kb(*rows) if rows else None)


@menu(M_UPGRADE)
def m_upgrade(m):
    show_plans(m)


@cb("up")
def c_up(call, a):
    show_plans(call)


@cb("buy")
def c_buy(call, a):
    plan = db_get_plan(a[0]) if a else None
    if not plan or not plan["active"] or plan["id"] == "free":
        return ack(call, "প্ল্যান পাওয়া যায়নি", True)
    bal = db_get_user(call.from_user.id)["balance"]
    methods = []
    if db_get_setting("bkash_on") != "0":
        methods.append(("💗 bKash", f"pay:{plan['id']}:bkash"))
    if db_get_setting("nagad_on") != "0":
        methods.append(("🟠 Nagad", f"pay:{plan['id']}:nagad"))
    rows = ([methods] if methods else []) + [[(f"💰 ব্যালেন্স (৳{bal:g})", f"pay:{plan['id']}:bal")],
                                            [("◀️ পেছনে", "up")]]
    show(call, f"🛒 <b>ক্রয় নিশ্চিত করুন</b>\n\n{plan_card(plan)}\n\nপেমেন্ট মাধ্যম বেছে নিন 👇", kb(*rows))


@cb("pay")
def c_pay(call, a):
    uid = call.from_user.id
    plan = db_get_plan(a[0]) if len(a) > 1 else None
    if not plan or plan["id"] == "free":
        return ack(call, "প্ল্যান পাওয়া যায়নি", True)
    method = a[1]
    if method == "bal":
        bal = db_get_user(uid)["balance"]
        if bal < plan["price"]:
            return ack(call, f"❌ ব্যালেন্স কম! দরকার ৳{plan['price']:g}, আছে ৳{bal:g}।\nরেফার করে আর্ন করুন।", True)
        return show(call, f"💰 ব্যালেন্স থেকে <b>৳{plan['price']:g}</b> কেটে <b>{plan['name']}</b> কিনবেন?",
                    kb([("✅ হ্যাঁ, কিনুন", f"pbal:{plan['id']}"), ("❌ না", f"buy:{plan['id']}")]))
    if method not in METHODS:
        return
    if db_get_setting(f"{method}_on") == "0":
        return ack(call, "⛔ এই পেমেন্ট মাধ্যম এখন বন্ধ আছে। অন্যটি বেছে নিন।", True)
    if db_user_pending_count(uid) >= 3:
        return ack(call, "⏳ আপনার ৩টি পেমেন্ট এখনো যাচাই বাকি। অপেক্ষা করুন।", True)
    number = db_get_setting(method)
    note = db_get_setting("payment_note").strip()
    note_line = f"\n📝 {esc(note)}" if note else ""
    set_state(uid, "trx", plan=plan["id"], method=method)
    show(call, (f"💳 <b>{METHODS[method]} পেমেন্ট</b>\n━━━━━━━━━━━━━━\n"
                f"1️⃣ {METHODS[method]} অ্যাপ খুলে <b>Send Money</b> করুন:\n"
                f"📱 নম্বর: <code>{esc(number)}</code>\n"
                f"💵 পরিমাণ: <b>৳{plan['price']:g}</b>\n\n"
                f"2️⃣ সেন্ড করার পর <b>Transaction ID (TrxID)</b> কপি করুন।\n"
                f"3️⃣ TrxID এখানে পাঠান 👇 (অ্যাডমিন যাচাই করে অ্যাপ্রুভ করলে প্ল্যান চালু হবে)\n\n"
                f"⚠️ ঠিক ৳{plan['price']:g} পাঠাবেন। ভুল/ভুয়া TrxID দিলে একাউন্ট ব্যান হতে পারে।{note_line}\nবাতিল: /cancel"),
         kb([("◀️ পেছনে", f"buy:{plan['id']}")]))


@cb("pbal")
def c_pay_balance(call, a):
    uid = call.from_user.id
    plan = db_get_plan(a[0]) if a else None
    if not plan or plan["id"] == "free" or not plan["active"]:
        return ack(call, "প্ল্যান পাওয়া যায়নি", True)
    if not db_charge_balance(uid, plan["price"]):
        return ack(call, "❌ ব্যালেন্স কম!", True)
    exp = db_activate_plan(uid, plan["id"])
    show(call, f"🎉 <b>{plan['name']}</b> সক্রিয় হয়েছে!\n⏳ মেয়াদ: {fmt_ts(exp)}\n\n🎛 কন্ট্রোল প্যানেল থেকে প্রজেক্ট চালু করুন।")


@state("trx")
def s_trx(m, st):
    uid = m.from_user.id
    trx = (m.text or "").strip().upper() if m.content_type == "text" else ""
    plan = db_get_plan(st["plan"])
    if not plan:
        return clear_state(uid)
    if not re.fullmatch(r"[A-Z0-9]{6,20}", trx):
        return bot.send_message(m.chat.id, "❌ TrxID ঠিক নয় (৬-২০ অক্ষর/সংখ্যা)। আবার পাঠান।")
    if db_trx_exists(trx):
        return bot.send_message(m.chat.id, "❌ এই TrxID আগেই জমা দেওয়া হয়েছে।")
    if db_user_pending_count(uid) >= 3:
        clear_state(uid)
        return bot.send_message(m.chat.id, "⏳ আগের পেমেন্ট যাচাই বাকি।")
    pid = db_add_payment(uid, plan["id"], st["method"], plan["price"], trx)
    clear_state(uid)
    bot.send_message(m.chat.id, f"✅ <b>পেমেন্ট জমা হয়েছে!</b>\n🧾 TrxID: <code>{esc(trx)}</code>\n"
                                f"অ্যাডমিন যাচাই করলেই প্ল্যান সক্রিয় হবে (সাধারণত ৫-৩০ মিনিট)। আমি আপনাকে জানিয়ে দেব।")
    u = db_get_user(uid)
    notify_admins(payment_text(db_get_payment(pid), u),
                  kb([("✅ Approve", f"payok:{pid}"), ("❌ Reject", f"payno:{pid}")]))


def payment_text(pay, u):
    plan = db_get_plan(pay["plan_id"])
    uname = f"@{u['username']}" if u.get("username") else "—"
    return (f"💳 <b>নতুন পেমেন্ট #{pay['id']}</b>\n👤 {esc(u['first_name'] or '')} ({uname}) <code>{u['id']}</code>\n"
            f"📦 প্ল্যান: {plan['name'] if plan else pay['plan_id']}\n💵 পরিমাণ: ৳{pay['amount']:g}\n"
            f"📲 মাধ্যম: {METHODS.get(pay['method'], pay['method'])}\n🧾 TrxID: <code>{esc(pay['trx_id'])}</code>\n"
            f"🕒 {fmt_ts(pay['created_at'])}\n\n⚠️ {METHODS.get(pay['method'])} অ্যাপে TrxID ও পরিমাণ মিলিয়ে নিন।")


@cb("payok")
def c_approve(call, a):
    if not is_admin(call.from_user.id):
        return ack(call, "❌ অনুমতি নেই", True)
    pid = int(a[0])
    cur = db_x("UPDATE payments SET status='approved', decided_by=? WHERE id=? AND status='pending'",
               (call.from_user.id, pid))
    if cur.rowcount != 1:
        return ack(call, "ইতোমধ্যে প্রসেস করা হয়েছে", True)
    pay = db_get_payment(pid)
    exp = db_activate_plan(pay["user_id"], pay["plan_id"])
    plan = db_get_plan(pay["plan_id"])
    notify(pay["user_id"], f"🎉 <b>পেমেন্ট অ্যাপ্রুভ হয়েছে!</b>\n👑 প্ল্যান: <b>{plan['name']}</b>\n"
                           f"⏳ মেয়াদ: {fmt_ts(exp)}\n\n🎛 কন্ট্রোল প্যানেল থেকে প্রজেক্ট চালু করুন।")
    show(call, call.message.html_text + f"\n\n✅ <b>Approved</b> by {call.from_user.id}")


@cb("payno")
def c_reject(call, a):
    if not is_admin(call.from_user.id):
        return ack(call, "❌ অনুমতি নেই", True)
    pid = int(a[0])
    cur = db_x("UPDATE payments SET status='rejected', decided_by=? WHERE id=? AND status='pending'",
               (call.from_user.id, pid))
    if cur.rowcount != 1:
        return ack(call, "ইতোমধ্যে প্রসেস করা হয়েছে", True)
    pay = db_get_payment(pid)
    notify(pay["user_id"], f"❌ <b>পেমেন্ট বাতিল হয়েছে</b> (TrxID: <code>{esc(pay['trx_id'])}</code>)।\n"
                           f"ভুল মনে হলে 🛰 সাপোর্ট হাবে যোগাযোগ করুন।")
    show(call, call.message.html_text + f"\n\n❌ <b>Rejected</b> by {call.from_user.id}")


# ==============================================================================
# 12. HANDLERS — admin panel
# ==============================================================================
PLAN_FIELDS = {"name": "নাম", "price": "মূল্য (৳)", "days": "মেয়াদ (দিন)", "max_projects": "সর্বোচ্চ প্রজেক্ট",
               "ram_mb": "RAM (MB)", "cpu_pct": "CPU (%)", "disk_mb": "ডিস্ক (MB)"}
SETTING_LABELS = {"channels": "চ্যানেল (কমা দিয়ে, @username)", "bkash": "bKash নম্বর", "nagad": "Nagad নম্বর",
                  "payment_note": "পেমেন্ট নোট (পেমেন্ট স্ক্রিনে দেখাবে)", "referral_reward": "রেফার রিওয়ার্ড (৳)",
                  "support": "সাপোর্ট @username", "welcome_title": "ওয়েলকাম টাইটেল", "welcome_text": "ওয়েলকাম টেক্সট"}
SETTINGS_PAGE_KEYS = ["channels", "bkash", "nagad", "payment_note", "referral_reward", "support"]


def adm(fn):
    """admin-only wrapper for callbacks"""
    def w(call, a):
        if not is_admin(call.from_user.id):
            return ack(call, "❌ অনুমতি নেই", True)
        return fn(call, a)
    w.__name__ = fn.__name__
    return w


def admin_home_kb():
    n = db_val("SELECT COUNT(*) FROM payments WHERE status='pending'")
    return kb([("📊 স্ট্যাটস", "a:stats"), (f"💳 পেন্ডিং পেমেন্ট ({n})" if n else "💳 পেন্ডিং পেমেন্ট", "a:pay")],
              [("🧾 পেমেন্ট হিস্টরি", "a:ph"), ("📦 প্ল্যান ম্যানেজ", "a:plans")],
              [("👤 ইউজার ম্যানেজ", "a:users"), ("📢 ব্রডকাস্ট", "a:bc")],
              [("🖥 রানিং প্রজেক্ট", "a:run"), ("⚙️ সেটিংস", "a:set")],
              [("👋 ওয়েলকাম মেসেজ", "a:wl"), ("👮 অ্যাডমিন ম্যানেজ", "a:adm")],
              [("💾 DB ব্যাকআপ", "a:db")])


@bot.message_handler(commands=["admin"])
def cmd_admin(m):
    if is_admin(m.from_user.id):
        bot.send_message(m.chat.id, "🛠 <b>অ্যাডমিন প্যানেল</b>", reply_markup=admin_home_kb())


@menu(M_ADMIN)
def m_admin(m):
    if is_admin(m.from_user.id):
        bot.send_message(m.chat.id, "🛠 <b>অ্যাডমিন প্যানেল</b>", reply_markup=admin_home_kb())


@cb("a")
@adm
def c_admin(call, a):
    sub = a[0] if a else "home"
    back = kb([("◀️ অ্যাডমিন", "a:home")])
    if sub == "home":
        clear_state(call.from_user.id)
        show(call, "🛠 <b>অ্যাডমিন প্যানেল</b>", admin_home_kb())
    elif sub == "stats":
        s = db_stats()
        vm, disk = psutil.virtual_memory(), psutil.disk_usage(str(DATA_DIR))
        show(call, (f"📊 <b>স্ট্যাটস</b>\n━━━━━━━━━━━━━━\n👥 ইউজার: {s['users']} (ভেরিফাইড {s['verified']}, ব্যান {s['banned']})\n"
                    f"🆕 গত ২৪ ঘণ্টা: {s['today']}\n💎 পেইড ইউজার: {s['paid']}\n"
                    f"📦 প্রজেক্ট: {s['projects']} (🟢 {s['running']} চলছে)\n💳 পেন্ডিং পেমেন্ট: {s['pending']}\n"
                    f"💰 মোট আয়: ৳{s['revenue']:g}\n\n🖥 <b>সার্ভার</b>\n🧠 CPU: {psutil.cpu_percent(interval=0.3)}%\n"
                    f"💾 RAM: {human_size(vm.used)} / {human_size(vm.total)}\n💽 ডিস্ক: {human_size(disk.used)} / {human_size(disk.total)}"),
             kb([("🔄 রিফ্রেশ", "a:stats")], [("◀️ অ্যাডমিন", "a:home")]))
    elif sub == "pay":
        pend = db_pending_payments()
        if not pend:
            return show(call, "✅ কোনো পেন্ডিং পেমেন্ট নেই।", back)
        show(call, f"💳 পেন্ডিং: {len(pend)}টি (নিচে পাঠালাম)", back)
        for pay in pend[:15]:
            notify(call.from_user.id, payment_text(pay, db_get_user(pay["user_id"])),
                   kb([("✅ Approve", f"payok:{pay['id']}"), ("❌ Reject", f"payno:{pay['id']}")]))
    elif sub == "plans":
        rows = [[(f"{'🟢' if p['active'] else '⚪'} {p['name']} — ৳{p['price']:g}", f"ap:{p['id']}")] for p in db_list_plans()]
        rows += [[("➕ নতুন প্ল্যান", "apn")], [("◀️ অ্যাডমিন", "a:home")]]
        show(call, "📦 <b>প্ল্যান ম্যানেজ</b>\n(FREE প্ল্যানের 'মেয়াদ' = ফ্রি ট্রায়াল দিন)", kb(*rows))
    elif sub == "users":
        set_state(call.from_user.id, "a_user")
        show(call, "👤 ইউজারের Telegram ID পাঠান:\n\nবাতিল: /cancel", back)
    elif sub == "bc":
        set_state(call.from_user.id, "a_bc")
        show(call, "📢 <b>ব্রডকাস্ট</b>\nযে মেসেজ (টেক্সট/ছবি/ভিডিও/ফাইল) সবাইকে পাঠাতে চান সেটা এখন পাঠান:\n\nবাতিল: /cancel", back)
    elif sub == "run":
        rows = []
        for pid, proc in list(RUNNING.items())[:25]:
            p = db_get_project(pid)
            if p:
                s = proc_stats(p)
                rows.append([(f"⏹ {p['name'][:14]} | {p['user_id']} | {s['ram']:.0f}MB", f"ak:{pid}")])
        rows.append([("◀️ অ্যাডমিন", "a:home")])
        show(call, f"🖥 <b>রানিং প্রজেক্ট ({len(RUNNING)})</b>\nবাটনে চাপলে বন্ধ হবে।", kb(*rows))
    elif sub == "set":
        rows = [[(f"{SETTING_LABELS[k]}: {(db_get_setting(k) or '—').replace(chr(10), ' ')[:20]}", f"as:{k}")]
                for k in SETTINGS_PAGE_KEYS]
        rows.append([(f"💗 bKash: {'ON ✅' if db_get_setting('bkash_on') != '0' else 'OFF ⛔'}", "ast:bkash_on"),
                     (f"🟠 Nagad: {'ON ✅' if db_get_setting('nagad_on') != '0' else 'OFF ⛔'}", "ast:nagad_on")])
        rows.append([("◀️ অ্যাডমিন", "a:home")])
        show(call, "⚙️ <b>সেটিংস</b>\nপরিবর্তন করতে বাটনে চাপুন।\n💗/🟠 বাটনে চাপলে সেই পেমেন্ট মাধ্যম চালু/বন্ধ হবে।",
             kb(*rows))
    elif sub == "ph":
        icon = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
        lines = [f"{icon.get(r['status'], '•')} #{r['id']} • <code>{r['user_id']}</code> • {esc(r['plan_id'])} • "
                 f"৳{r['amount']:g} • {METHODS.get(r['method'], r['method'])} • <code>{esc(r['trx_id'])}</code>"
                 for r in db_q("SELECT * FROM payments ORDER BY id DESC LIMIT 15")]
        show(call, "🧾 <b>পেমেন্ট হিস্টরি (শেষ ১৫টি)</b>\n\n" + ("\n".join(lines) or "কোনো পেমেন্ট নেই।"),
             kb([("💳 পেন্ডিং দেখুন", "a:pay")], [("◀️ অ্যাডমিন", "a:home")]))
    elif sub == "wl":
        show(call, "👋 <b>ওয়েলকাম মেসেজ</b> — ইউজার নতুন এসে এটাই দেখবে:\n━━━━━━━━━━━━━━\n" + welcome_message(),
             kb([("✏️ টাইটেল বদলান", "as:welcome_title"), ("✏️ টেক্সট বদলান", "as:welcome_text")],
                [("♻️ ডিফল্টে ফেরান", "awr")], [("◀️ অ্যাডমিন", "a:home")]))
    elif sub == "adm":
        if not is_owner(call.from_user.id):
            return ack(call, "❌ শুধু মেইন অ্যাডমিন (.env-এর ADMIN_IDS) অ্যাডমিন ম্যানেজ করতে পারবে", True)
        extra = sorted(extra_admins())
        rows = [[(f"🗑 সরান: {i}", f"ard:{i}")] for i in extra]
        rows += [[("➕ অ্যাডমিন যোগ", "aad")], [("◀️ অ্যাডমিন", "a:home")]]
        show(call, ("👮 <b>অ্যাডমিন ম্যানেজ</b>\n\n👑 মেইন (.env): " + ", ".join(f"<code>{i}</code>" for i in sorted(ADMIN_IDS))
                    + "\n🛡 যোগ করা অ্যাডমিন: " + (", ".join(f"<code>{i}</code>" for i in extra) or "কেউ নেই")
                    + "\n\nযোগ করা অ্যাডমিন পেমেন্ট অ্যাপ্রুভ/ইউজার/প্ল্যান/সেটিংস ম্যানেজ করতে পারবে। "
                      "DB ব্যাকআপ ও অ্যাডমিন ম্যানেজ শুধু মেইন অ্যাডমিন করতে পারে।"), kb(*rows))
    elif sub == "db":
        if not is_owner(call.from_user.id):
            return ack(call, "❌ শুধু মেইন অ্যাডমিন (.env-এর ADMIN_IDS) ডাটাবেস ব্যাকআপ নিতে পারবে", True)
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/host_{time.strftime('%Y%m%d_%H%M')}.db"
            db_backup_to(path)
            with open(path, "rb") as f:
                bot.send_document(call.message.chat.id, f, caption="💾 ডাটাবেস ব্যাকআপ")


@cb("ak")
@adm
def c_kill(call, a):
    pid = int(a[0])
    p = db_get_project(pid)
    stop_project(pid)
    if p:
        notify(p["user_id"], f"⛔ অ্যাডমিন আপনার প্রজেক্ট <b>{esc(p['name'])}</b> বন্ধ করেছে।")
    ack(call, "⏹ বন্ধ করা হয়েছে")
    c_admin(call, ["run"])


# ------------------------------------------------------------------ plans
@cb("ap")
@adm
def c_plan(call, a):
    p = db_get_plan(a[0])
    if not p:
        return ack(call, "প্ল্যান নেই", True)
    rows = [[(f"✏️ {lbl}", f"apf:{p['id']}:{f}")] for f, lbl in PLAN_FIELDS.items()]
    rows.append([(f"{'🟢 চালু' if p['active'] else '⚪ বন্ধ'} (টগল)", f"apt:{p['id']}:active"),
                 (f"♻️ অটো-রিস্টার্ট {'ON' if p['auto_restart'] else 'OFF'}", f"apt:{p['id']}:auto_restart")])
    if p["id"] != "free":
        rows.append([("🗑 ডিলিট", f"apd:{p['id']}")])
    rows.append([("◀️ পেছনে", "a:plans")])
    show(call, f"📦 <b>প্ল্যান: {esc(p['id'])}</b>\n\n{plan_card(p)}", kb(*rows))


@cb("apf")
@adm
def c_plan_field(call, a):
    set_state(call.from_user.id, "a_planf", plan=a[0], field=a[1])
    show(call, f"✏️ নতুন <b>{PLAN_FIELDS[a[1]]}</b> লিখুন:\n\nবাতিল: /cancel")


@state("a_planf")
def s_planf(m, st):
    if not is_admin(m.from_user.id) or m.content_type != "text":
        return
    f, v = st["field"], m.text.strip()
    try:
        value = v if f == "name" else (float(v) if f == "price" else int(v))
        if f != "name" and value < 0:
            raise ValueError
    except ValueError:
        return bot.send_message(m.chat.id, "❌ সঠিক সংখ্যা দিন।")
    db_update_plan(st["plan"], **{f: value})
    clear_state(m.from_user.id)
    bot.send_message(m.chat.id, "✅ আপডেট হয়েছে।", reply_markup=kb([("📦 প্ল্যান", f"ap:{st['plan']}")]))


@cb("apt")
@adm
def c_plan_toggle(call, a):
    p = db_get_plan(a[0])
    if p and a[1] in ("active", "auto_restart"):
        db_update_plan(p["id"], **{a[1]: 0 if p[a[1]] else 1})
    c_plan(call, [a[0]])


@cb("apd")
@adm
def c_plan_del(call, a):
    show(call, f"⚠️ প্ল্যান <b>{esc(a[0])}</b> ডিলিট করবেন? (এই প্ল্যানের ইউজাররা FREE-তে নেমে যাবে)",
         kb([("✅ হ্যাঁ", f"apdy:{a[0]}"), ("❌ না", f"ap:{a[0]}")]))


@cb("apdy")
@adm
def c_plan_del_yes(call, a):
    db_x("UPDATE users SET plan_id='free', plan_expires=NULL WHERE plan_id=?", (a[0],))
    db_delete_plan(a[0])
    c_admin(call, ["plans"])


@cb("apn")
@adm
def c_plan_new(call, a):
    set_state(call.from_user.id, "a_plann")
    show(call, "➕ <b>নতুন প্ল্যান</b>\nএই ফরম্যাটে পাঠান:\n<code>id|নাম|দাম|দিন|প্রজেক্ট|ram_mb|cpu%|disk_mb</code>\n"
               "যেমন: <code>vip|💎 VIP|499|30|100|4096|300|10000</code>\n\nবাতিল: /cancel")


@state("a_plann")
def s_plann(m, st):
    if not is_admin(m.from_user.id) or m.content_type != "text":
        return
    parts = [x.strip() for x in m.text.split("|")]
    try:
        assert len(parts) == 8 and re.fullmatch(r"[a-z0-9_]{2,15}", parts[0]) and not db_get_plan(parts[0])
        db_add_plan(parts[0], parts[1], float(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]), int(parts[6]),
                    int(parts[7]))
    except Exception:  # noqa
        return bot.send_message(m.chat.id, "❌ ফরম্যাট ভুল বা id আগে থেকেই আছে (id: a-z 0-9 _)।")
    clear_state(m.from_user.id)
    bot.send_message(m.chat.id, "✅ প্ল্যান যোগ হয়েছে।", reply_markup=kb([("📦 প্ল্যান", "a:plans")]))


# ------------------------------------------------------------------ users
def user_card(u):
    st = db_plan_state(u)
    return (f"👤 <b>{esc(u['first_name'] or '')}</b> @{u['username'] or '—'}\n🆔 <code>{u['id']}</code>\n"
            f"👑 {st['plan']['name']} ({plan_left(st)})\n💰 ৳{u['balance']:g} • 👥 রেফার {u['ref_count']}\n"
            f"📦 প্রজেক্ট: {len(db_user_projects(u['id']))} • 🚫 ব্যান: {'হ্যাঁ' if u['banned'] else 'না'}\n"
            f"📅 {fmt_ts(u['joined_at'])}")


def user_kb(u):
    i = u["id"]
    return kb([("🚫 আনব্যান" if u["banned"] else "🚫 ব্যান", f"aub:{i}"), ("💰 ব্যালেন্স যোগ/কমান", f"auv:{i}")],
              [("🎁 প্ল্যান দিন", f"aug:{i}"), ("♻️ ট্রায়াল রিসেট", f"aur:{i}")],
              [("❌ প্ল্যান বাতিল", f"aux:{i}"), ("✉️ মেসেজ", f"aum:{i}")],
              [("◀️ অ্যাডমিন", "a:home")])


@state("a_user")
def s_user(m, st):
    if not is_admin(m.from_user.id) or m.content_type != "text" or not m.text.strip().isdigit():
        return bot.send_message(m.chat.id, "❌ সংখ্যায় ID দিন।")
    u = db_get_user(int(m.text.strip()))
    if not u:
        return bot.send_message(m.chat.id, "❌ ইউজার পাওয়া যায়নি।")
    clear_state(m.from_user.id)
    bot.send_message(m.chat.id, user_card(u), reply_markup=user_kb(u))


def _refresh_user(call, uid):
    u = db_get_user(int(uid))
    show(call, user_card(u), user_kb(u))


@cb("aub")
@adm
def c_ban(call, a):
    u = db_get_user(int(a[0]))
    db_update_user(u["id"], banned=0 if u["banned"] else 1)
    if not u["banned"]:
        for p in db_user_projects(u["id"]):
            stop_project(p["id"])
    _refresh_user(call, a[0])


@cb("auv")
@adm
def c_bal(call, a):
    set_state(call.from_user.id, "a_bal", uid=int(a[0]))
    show(call, "💰 পরিমাণ লিখুন (যোগ: <code>50</code>, কমানো: <code>-20</code>):\n\nবাতিল: /cancel")


@state("a_bal")
def s_bal(m, st):
    if not is_admin(m.from_user.id) or m.content_type != "text":
        return
    try:
        amt = float(m.text.strip())
    except ValueError:
        return bot.send_message(m.chat.id, "❌ সংখ্যা দিন।")
    db_add_balance(st["uid"], amt)
    clear_state(m.from_user.id)
    notify(st["uid"], f"💰 আপনার ব্যালেন্সে ৳{amt:+g} পরিবর্তন করা হয়েছে।")
    u = db_get_user(st["uid"])
    bot.send_message(m.chat.id, "✅ হয়েছে।\n\n" + user_card(u), reply_markup=user_kb(u))


@cb("aug")
@adm
def c_give(call, a):
    rows = [[(f"{p['name']} ({p['days']}দিন)", f"augp:{a[0]}:{p['id']}")] for p in db_list_plans(paid_only=True)]
    rows.append([("◀️ পেছনে", f"aus:{a[0]}")])
    show(call, "🎁 কোন প্ল্যান দেবেন?", kb(*rows))


@cb("augp")
@adm
def c_give_plan(call, a):
    exp = db_activate_plan(int(a[0]), a[1])
    notify(int(a[0]), f"🎁 অ্যাডমিন আপনাকে <b>{db_get_plan(a[1])['name']}</b> প্ল্যান দিয়েছে!\n⏳ মেয়াদ: {fmt_ts(exp)}")
    _refresh_user(call, a[0])


@cb("aus")
@adm
def c_show_user(call, a):
    _refresh_user(call, a[0])


@cb("aur")
@adm
def c_reset_trial(call, a):
    db_update_user(int(a[0]), trial_started=None, trial_notified=0)
    ack(call, "ট্রায়াল রিসেট হয়েছে")
    _refresh_user(call, a[0])


@cb("aux")
@adm
def c_cancel_plan(call, a):
    db_update_user(int(a[0]), plan_id="free", plan_expires=None)
    ack(call, "প্ল্যান বাতিল")
    _refresh_user(call, a[0])


@cb("aum")
@adm
def c_msg(call, a):
    set_state(call.from_user.id, "a_msg", uid=int(a[0]))
    show(call, "✉️ মেসেজ লিখুন (ইউজারকে পাঠানো হবে):\n\nবাতিল: /cancel")


@state("a_msg")
def s_msg(m, st):
    if is_admin(m.from_user.id) and m.content_type == "text":
        ok = notify(st["uid"], f"📩 <b>অ্যাডমিন:</b>\n{esc(m.text)}")
        clear_state(m.from_user.id)
        bot.send_message(m.chat.id, "✅ পাঠানো হয়েছে।" if ok else "❌ পাঠানো যায়নি (ইউজার বট ব্লক করেছে?)")


# ------------------------------------------------------------------ broadcast
@state("a_bc")
def s_bc(m, st):
    if not is_admin(m.from_user.id):
        return
    set_state(m.from_user.id, "a_bc_go", chat=m.chat.id, mid=m.message_id)
    bot.send_message(m.chat.id, f"📢 এই মেসেজটি <b>{len(db_all_user_ids())}</b> জনকে পাঠাবেন?",
                     reply_markup=kb([("✅ পাঠান", "abc:go"), ("❌ বাতিল", "abc:no")]))


@cb("abc")
@adm
def c_bc(call, a):
    uid = call.from_user.id
    st = get_state(uid)
    if a[0] == "no" or not st or st["action"] != "a_bc_go":
        clear_state(uid)
        return show(call, "❌ বাতিল।")
    clear_state(uid)
    show(call, "⏳ ব্রডকাস্ট শুরু হয়েছে…")

    def job():
        sent = failed = 0
        for target in db_all_user_ids():
            try:
                bot.copy_message(target, st["chat"], st["mid"])
                sent += 1
            except Exception:  # noqa
                failed += 1
            time.sleep(0.05)
        notify(uid, f"📢 ব্রডকাস্ট শেষ\n✅ সফল: {sent}\n❌ ব্যর্থ: {failed}")
    threading.Thread(target=job, daemon=True).start()


# ------------------------------------------------------------------ settings
@cb("as")
@adm
def c_setting(call, a):
    if a[0] in SETTING_LABELS:
        set_state(call.from_user.id, "a_set", key=a[0])
        show(call, f"⚙️ <b>{SETTING_LABELS[a[0]]}</b>\nবর্তমান: <code>{esc(db_get_setting(a[0]) or '—')}</code>\n\n"
                   f"নতুন মান পাঠান (খালি/ডিফল্ট করতে <code>-</code>):\n\nবাতিল: /cancel")


@state("a_set")
def s_set(m, st):
    if not is_admin(m.from_user.id) or m.content_type != "text":
        return
    v = m.text.strip()
    v = "" if v == "-" else v
    key = st["key"]
    if key == "referral_reward":
        try:
            float(v)
        except ValueError:
            return bot.send_message(m.chat.id, "❌ সংখ্যা দিন।")
    limits = {"welcome_title": 60, "welcome_text": 2000, "payment_note": 300}
    if key in limits and len(v) > limits[key]:
        return bot.send_message(m.chat.id, f"❌ সর্বোচ্চ {limits[key]} অক্ষর (আপনারটা {len(v)})। ছোট করে আবার পাঠান।")
    db_set_setting(key, v)
    clear_state(m.from_user.id)
    back, lbl = ("a:wl", "👋 ওয়েলকাম মেসেজ") if key.startswith("welcome") else ("a:set", "⚙️ সেটিংস")
    bot.send_message(m.chat.id, "✅ সেভ হয়েছে।", reply_markup=kb([(lbl, back)]))


@cb("ast")
@adm
def c_setting_toggle(call, a):
    if a and a[0] in ("bkash_on", "nagad_on"):
        db_set_setting(a[0], "1" if db_get_setting(a[0]) == "0" else "0")
    c_admin(call, ["set"])


@cb("awr")
@adm
def c_welcome_reset(call, a):
    for k in ("welcome_title", "welcome_text"):
        db_set_setting(k, DEFAULT_SETTINGS[k])
    ack(call, "♻️ ডিফল্ট ওয়েলকাম সেট হয়েছে")
    c_admin(call, ["wl"])


# ------------------------------------------------------------------ admin management (main admins only)
@cb("aad")
@adm
def c_admin_add(call, a):
    if not is_owner(call.from_user.id):
        return ack(call, "❌ শুধু মেইন অ্যাডমিন", True)
    set_state(call.from_user.id, "a_addadm")
    show(call, "👮 নতুন অ্যাডমিনের Telegram ID পাঠান (সে আগে বটে /start দিয়ে থাকতে হবে):\n\nবাতিল: /cancel")


@state("a_addadm")
def s_addadm(m, st):
    if not is_owner(m.from_user.id) or m.content_type != "text":
        return
    t = m.text.strip()
    if not t.isdigit() or not db_get_user(int(t)):
        return bot.send_message(m.chat.id, "❌ সঠিক ID দিন — ইউজারকে আগে বটে /start দিতে হবে।")
    db_set_setting("admins", ",".join(str(i) for i in sorted(extra_admins() | {int(t)})))
    clear_state(m.from_user.id)
    notify(int(t), "🛡 আপনাকে অ্যাডমিন বানানো হয়েছে। অ্যাডমিন মেনু পেতে /start দিন।")
    bot.send_message(m.chat.id, "✅ অ্যাডমিন যোগ হয়েছে।", reply_markup=kb([("👮 অ্যাডমিন ম্যানেজ", "a:adm")]))


@cb("ard")
@adm
def c_admin_remove(call, a):
    if not is_owner(call.from_user.id):
        return ack(call, "❌ শুধু মেইন অ্যাডমিন", True)
    if a and a[0].isdigit():
        db_set_setting("admins", ",".join(str(i) for i in sorted(extra_admins() - {int(a[0])})))
        ack(call, "🗑 সরানো হয়েছে")
    c_admin(call, ["adm"])


# ==============================================================================
# 13. BACKGROUND JOBS  (expiry reminders, daily DB backup)
# ==============================================================================
RENEW_KB = lambda: kb([("💎 প্ল্যান কিনুন", "up")])


def _expiry_once():
    now = time.time()
    # paid plans ending within 24h
    for u in db_q("SELECT * FROM users WHERE plan_id!='free' AND plan_expires>? AND plan_expires<? AND exp_notified!=plan_expires",
                  (now, now + 86400)):
        plan = db_get_plan(u["plan_id"])
        notify(u["id"], f"⏰ আপনার <b>{plan['name'] if plan else u['plan_id']}</b> প্ল্যান শীঘ্রই শেষ হবে!\n"
                        f"মেয়াদ: {fmt_ts(u['plan_expires'])}\nপ্রজেক্ট চালু রাখতে এখনই রিনিউ করুন।", RENEW_KB())
        db_update_user(u["id"], exp_notified=u["plan_expires"])
    # paid plans already expired -> downgrade to free
    for u in db_q("SELECT * FROM users WHERE plan_id!='free' AND plan_expires IS NOT NULL AND plan_expires<=?", (now,)):
        db_update_user(u["id"], plan_id="free", plan_expires=None)
        notify(u["id"], "⌛ আপনার পেইড প্ল্যানের মেয়াদ শেষ হয়েছে। এখন FREE প্ল্যান প্রযোজ্য। "
                        "চালু রাখতে প্ল্যান রিনিউ করুন।", RENEW_KB())
    # free trial ending within 24h
    days = db_get_plan("free")["days"]
    for u in db_q("SELECT * FROM users WHERE trial_started IS NOT NULL AND trial_notified=0 AND trial_started+?>? AND "
                  "trial_started+?<?", (days * 86400, now, days * 86400, now + 86400)):
        notify(u["id"], "⏰ আপনার ফ্রি ট্রায়াল ২৪ ঘণ্টার মধ্যে শেষ হবে! বট চালু রাখতে প্ল্যান কিনুন।", RENEW_KB())
        db_update_user(u["id"], trial_notified=1)


def _expiry_loop():
    while True:
        try:
            _expiry_once()
        except Exception:  # noqa
            log.exception("expiry job")
        time.sleep(600)


def _db_backup_loop():
    time.sleep(120)
    while True:
        try:
            with tempfile.TemporaryDirectory() as td:
                path = f"{td}/host_{time.strftime('%Y%m%d_%H%M')}.db"
                db_backup_to(path)
                for a in ADMIN_IDS:
                    try:
                        with open(path, "rb") as f:
                            bot.send_document(a, f, caption="💾 অটো ডাটাবেস ব্যাকআপ (দৈনিক)")
                    except Exception as e:  # noqa
                        log.info("db backup to %s failed: %s", a, e)
        except Exception:  # noqa
            log.exception("db backup job")
        time.sleep(86400)


def start_background():
    threading.Thread(target=_expiry_loop, daemon=True, name="expiry").start()
    threading.Thread(target=_db_backup_loop, daemon=True, name="dbbackup").start()


# ==============================================================================
# 14. MAIN
# ==============================================================================
def main():
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS সেট করা নেই — অ্যাডমিন প্যানেল কেউ ব্যবহার করতে পারবে না!")
    init_permissions()
    restore_projects()
    start_monitor()
    start_background()
    log.info("Bot started as @%s", bot_username())
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
