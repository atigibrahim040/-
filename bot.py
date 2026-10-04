import os
import re
import json
import time
import html
import uuid
import shutil
import sqlite3
import subprocess
import threading
import logging
import requests
import yt_dlp

# ═══════════ الإعدادات ═══════════
BOT_NAME     = os.environ.get("BOT_NAME", "Onyx")
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
ADMIN_ID     = str(os.environ.get("ADMIN_ID", ""))
DB_PATH      = "bot.db"
DOWNLOAD_DIR = "downloads"
MAX_TG_SIZE  = 49 * 1024 * 1024
TG_BASE      = f"https://api.telegram.org/bot{BOT_TOKEN}"
FFMPEG       = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
USE_COLORS   = True      # ألوان الأزرار (تحتاج تحديث تيليجرام). خليها False لإيقافها
BOT_USERNAME = ""

PLATFORMS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram",
             "facebook": "Facebook", "twitter": "X"}

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bot")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
E = html.escape

# ═══════════ قاعدة البيانات ═══════════
db_lock = threading.Lock()
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
    joined_at TEXT, downloads INTEGER DEFAULT 0)""")
conn.commit()

def add_user(uid, username, first_name):
    with db_lock:
        conn.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?,0)",
                     (uid, username or "", first_name or "", time.strftime("%Y-%m-%d")))
        conn.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?",
                     (username or "", first_name or "", uid))
        conn.commit()

def get_users_count():
    with db_lock:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

def get_all_users():
    with db_lock:
        return [r[0] for r in conn.execute("SELECT user_id FROM users")]

def add_download(uid):
    with db_lock:
        conn.execute("UPDATE users SET downloads = downloads + 1 WHERE user_id=?", (uid,))
        conn.commit()

# ═══════════ الجلسات (كل كارت له جلسته الخاصة) ═══════════
sessions = {}
session_lock = threading.Lock()
admin_state = {}

stats = {"success": 0, "fail": 0}
stats_lock = threading.Lock()
OFFSET = 0

def is_admin(uid): return str(uid) == str(ADMIN_ID)

def new_session(user_id, data):
    tok = uuid.uuid4().hex[:8]
    with session_lock:
        sessions[tok] = {**data, "user_id": user_id, "ts": time.time(), "busy": False}
        if len(sessions) > 300:
            for k in sorted(sessions, key=lambda k: sessions[k]["ts"])[:100]:
                sessions.pop(k, None)
    return tok

# ═══════════ تيليجرام ═══════════
def tg_call(method, data=None, files=None, timeout=60):
    try:
        r = requests.post(f"{TG_BASE}/{method}", data=data, files=files, timeout=timeout)
        return r.json()
    except Exception as e:
        log.warning(f"tg_call({method}): {e}")
        return {"ok": False}

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text[:4000],
               "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg_call("sendMessage", data=payload)

def edit_message(chat_id, mid, text, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": mid, "text": text[:4000],
               "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg_call("editMessageText", data=payload)

def edit_markup(chat_id, mid, reply_markup):
    # يغيّر الأزرار فقط ويترك الكارت (صورة + نص) كما هو
    tg_call("editMessageReplyMarkup", data={"chat_id": chat_id, "message_id": mid,
            "reply_markup": json.dumps(reply_markup)})

def delete_message(chat_id, mid):
    if mid:
        tg_call("deleteMessage", data={"chat_id": chat_id, "message_id": mid})

def answer_callback(cb_id, text=""):
    tg_call("answerCallbackQuery", data={"callback_query_id": cb_id, "text": text})

def send_action(chat_id, action):
    tg_call("sendChatAction", data={"chat_id": chat_id, "action": action})

def send_card(chat_id, thumb, text, kb):
    """كارت: صورة + نص + أزرار. إذا فشلت الصورة يرسل نص فقط."""
    if thumb:
        data = {"chat_id": chat_id, "caption": text[:1000], "parse_mode": "HTML",
                "reply_markup": json.dumps(kb)}
        r = tg_call("sendPhoto", data={**data, "photo": thumb})
        if r.get("ok"): return r
        try:  # بعض الروابط ما يقدر تيليجرام يجلبها، نجلبها نحن ونرفعها
            img = requests.get(thumb, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if img.ok and img.content:
                r = tg_call("sendPhoto", data=data, files={"photo": ("t.jpg", img.content)})
                if r.get("ok"): return r
        except Exception as e:
            log.warning(f"thumb: {e}")
    return send_message(chat_id, text, kb)

def send_video(chat_id, path, caption, duration=0):
    try:
        with open(path, "rb") as f:
            data = {"chat_id": chat_id, "caption": caption[:1000],
                    "supports_streaming": True, "parse_mode": "HTML"}
            if duration: data["duration"] = int(duration)
            return tg_call("sendVideo", data=data, files={"video": f}, timeout=900).get("ok", False)
    except Exception as e:
        log.warning(f"send_video: {e}"); return False

def send_audio(chat_id, path, caption, title="", performer=""):
    try:
        with open(path, "rb") as f:
            data = {"chat_id": chat_id, "caption": caption[:1000],
                    "parse_mode": "HTML", "title": title[:64], "performer": performer[:64]}
            return tg_call("sendAudio", data=data, files={"audio": f}, timeout=900).get("ok", False)
    except Exception as e:
        log.warning(f"send_audio: {e}"); return False

def send_document(chat_id, path, caption, name):
    try:
        with open(path, "rb") as f:
            return tg_call("sendDocument",
                data={"chat_id": chat_id, "caption": caption[:1000], "parse_mode": "HTML"},
                files={"document": (name, f)}, timeout=900).get("ok", False)
    except Exception:
        return False

# ═══════════ أدوات ═══════════
def human_duration(sec):
    if not sec: return ""
    m, s = divmod(int(sec), 60); h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def human_size(b):
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def short(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"

def safe_filename(name):
    return (re.sub(r'[\\/:*?"<>|\n\r\t%]', "_", name)[:60].strip() or "file")

def progress_bar(pct):
    n = max(0, min(int(pct) // 10, 10))
    return f"{'▰' * n}{'▱' * (10 - n)}  {int(pct)}%"

def platform_name(info):
    raw = info.get("extractor_key") or info.get("extractor") or ""
    low = raw.lower()
    for k, v in PLATFORMS.items():
        if low.startswith(k): return v
    return "" if low.startswith("generic") else raw

def cleanup(tag):
    for f in os.listdir(DOWNLOAD_DIR):
        if f.startswith(tag + "."):
            try: os.remove(os.path.join(DOWNLOAD_DIR, f))
            except OSError: pass

def find_output(tag, prefer):
    files = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR)
             if f.startswith(tag + ".") and not f.endswith((".part", ".ytdl", ".temp"))]
    files = [p for p in files if os.path.getsize(p) > 10000]
    if not files: return None
    for ext in prefer:
        for p in files:
            if p.endswith("." + ext): return p
    return max(files, key=os.path.getsize)

def to_mp3(src, dst):
    """تحويل أي ملف (فيديو أو صوت) إلى MP3 صافي بدون فيديو."""
    try:
        r = subprocess.run([FFMPEG, "-y", "-i", src, "-vn", "-c:a", "libmp3lame",
                            "-b:a", "192k", dst], capture_output=True, timeout=600)
        return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 5000
    except Exception as e:
        log.warning(f"to_mp3: {e}")
        return False

def prepare_audio(tag):
    src = find_output(tag, ["mp3"])
    if not src: return None
    if src.endswith(".mp3"): return src
    dst = os.path.join(DOWNLOAD_DIR, f"{tag}.mp3")
    return dst if to_mp3(src, dst) else None

# ═══════════ الأزرار ═══════════
def btn(text, data, style=None):
    b = {"text": text, "callback_data": data}
    if style and USE_COLORS: b["style"] = style
    return b

def kb_main(tok, has_q):
    rows = [[btn("فيديو", f"v:{tok}", "primary"), btn("صوت", f"a:{tok}", "success")]]
    if has_q: rows.append([btn("جودة أخرى", f"q:{tok}")])
    return {"inline_keyboard": rows}

def kb_formats(tok, formats):
    rows, row = [], []
    for i, f in enumerate(formats[:6]):
        row.append(btn(f"{f['height']}p", f"f:{tok}:{i}"))
        if len(row) == 3: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([btn("رجوع", f"b:{tok}")])
    return {"inline_keyboard": rows}

def kb_admin():
    return {"inline_keyboard": [
        [btn("الإحصائيات", "adm:stats"), btn("المستخدمون", "adm:users")],
        [btn("إذاعة", "adm:broadcast"), btn("الحالة", "adm:ping")],
    ]}

# ═══════════ yt-dlp ═══════════
def extract_info(url):
    opts = {'quiet': True, 'no_warnings': True, 'noplaylist': True, 'playlist_items': '1'}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries: return None
                first = entries[0]
                first.setdefault("webpage_url", info.get("webpage_url") or url)
                return first
            return info
    except Exception as e:
        log.warning(f"extract: {e}")
        return None

def fmt_qualities(info):
    out, seen = [], set()
    for f in info.get('formats') or []:
        h = f.get('height')
        if h and f.get('vcodec') != 'none' and h not in seen:
            seen.add(h)
            out.append({'height': h, 'format_id': f['format_id']})
    return sorted(out, key=lambda x: -x['height'])[:6]

def ytdlp_download(url, out_tpl, mode, fmt_id=None, progress_cb=None):
    last = [0.0]
    def hook(d):
        if d['status'] != 'downloading' or not progress_cb: return
        if time.time() - last[0] < 3: return
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        if total:
            pct = (d.get('downloaded_bytes') or 0) * 100 / total
        elif d.get('fragment_count'):
            pct = (d.get('fragment_index') or 0) * 100 / d['fragment_count']
        else:
            return
        last[0] = time.time()
        progress_cb(min(pct, 100))
    opts = {
        'outtmpl': out_tpl,
        'quiet': True, 'no_warnings': True,
        'noplaylist': True, 'playlist_items': '1',
        'progress_hooks': [hook],
        'merge_output_format': 'mp4',
        'ffmpeg_location': FFMPEG,
    }
    if mode == 'audio':
        opts['format'] = "bestaudio/best"   # التحويل لـ MP3 يتم بعدها عبر ffmpeg
    elif fmt_id:
        opts['format'] = f"{fmt_id}+bestaudio/{fmt_id}/best"
    else:
        opts['format'] = "bestvideo*+bestaudio/best"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        log.warning(f"ytdlp_download: {e}")
        return False

# ═══════════ معالجة الرابط ═══════════
def card_text(s):
    lines = [f"<b>{E(short(s['title'], 90))}</b>"]
    sub = [x for x in (s["platform"], human_duration(s["duration"])) if x]
    if sub: lines += ["", E("  ·  ".join(sub))]
    return "\n".join(lines)

def process_link(chat_id, user_id, url):
    msg = send_message(chat_id, "<i>جارٍ قراءة الرابط…</i>")
    mid = msg.get("result", {}).get("message_id")

    info = extract_info(url)
    if not info:
        edit_message(chat_id, mid, "<b>تعذّر قراءة الرابط</b>\nتأكد أنه صحيح وأن المحتوى عام.")
        with stats_lock: stats["fail"] += 1
        return

    sess = {
        "url": url,
        "title": info.get('title') or "بدون عنوان",
        "author": info.get('uploader') or info.get('channel') or "",
        "duration": info.get('duration') or 0,
        "platform": platform_name(info),
        "page": info.get('webpage_url') or url,
        "qualities": fmt_qualities(info),
    }
    tok = new_session(user_id, sess)
    send_card(chat_id, info.get('thumbnail'), card_text(sess), kb_main(tok, bool(sess["qualities"])))
    delete_message(chat_id, mid)

# ═══════════ التحميل والإرسال ═══════════
def do_download(chat_id, user_id, tok, mode, fmt_id=None):
    with session_lock:
        sess = sessions.get(tok)
    if not sess:
        send_message(chat_id, "انتهت الجلسة. أرسل الرابط من جديد.")
        return

    url, title, author, dur = sess["url"], sess["title"], sess["author"], sess["duration"]
    label = "الصوت" if mode == "audio" else "الفيديو"

    status = send_message(chat_id, f"<b>جارٍ تحميل {label}…</b>")
    sid = status.get("result", {}).get("message_id")
    send_action(chat_id, "upload_voice" if mode == "audio" else "upload_video")

    def on_progress(pct):
        if sid:
            edit_message(chat_id, sid,
                f"<b>جارٍ تحميل {label}</b>\n<code>{progress_bar(pct)}</code>")

    tag = f"{user_id}_{int(time.time())}"
    ok = ytdlp_download(url, os.path.join(DOWNLOAD_DIR, tag + ".%(ext)s"), mode, fmt_id, on_progress)

    if not ok:
        path = None
    elif mode == "audio":
        path = prepare_audio(tag)
    else:
        path = find_output(tag, ["mp4", "mkv", "webm"])

    try:
        if not path:
            if sid: edit_message(chat_id, sid, "<b>تعذّر التحميل</b>\nأرسل الرابط وجرّب مرة ثانية.")
            with stats_lock: stats["fail"] += 1
            return

        size = os.path.getsize(path)
        if size > MAX_TG_SIZE:
            delete_message(chat_id, sid)
            send_message(chat_id,
                f"<b>الملف أكبر من حد تيليجرام</b>\n"
                f"الحجم <code>{human_size(size)}</code> · الحد <code>50 MB</code>\n\n"
                f"جرّب جودة أقل، أو افتح <a href=\"{E(sess['page'], quote=True)}\">الرابط الأصلي</a>.")
            return

        if sid: edit_message(chat_id, sid, "<b>جارٍ الإرسال…</b>")
        tail = f"\n\n@{BOT_USERNAME}" if BOT_USERNAME else ""
        caption = f"<b>{E(short(title, 100))}</b>{tail}"

        if mode == "audio":
            sent = send_audio(chat_id, path, caption, title=title, performer=author)
        else:
            sent = send_video(chat_id, path, caption, duration=dur)
        if not sent:
            sent = send_document(chat_id, path, caption,
                                 safe_filename(title) + os.path.splitext(path)[1])

        delete_message(chat_id, sid)
        if sent:
            with stats_lock: stats["success"] += 1
            add_download(user_id)
        else:
            send_message(chat_id, "<b>تعذّر الإرسال</b>\nجرّب مرة ثانية.")
            with stats_lock: stats["fail"] += 1
    finally:
        cleanup(tag)
        with session_lock:
            sessions.pop(tok, None)

# ═══════════ لوحة الأدمن ═══════════
def admin_panel(chat_id):
    send_message(chat_id, "<b>لوحة التحكم</b>", kb_admin())

def admin_stats(chat_id):
    with db_lock:
        total_dl = conn.execute("SELECT SUM(downloads) FROM users").fetchone()[0] or 0
    with stats_lock: s = dict(stats)
    send_message(chat_id,
        f"<b>الإحصائيات</b>\n\n"
        f"المستخدمون  <code>{get_users_count()}</code>\n"
        f"التحميلات  <code>{total_dl}</code>\n"
        f"ناجحة  <code>{s['success']}</code>\n"
        f"فاشلة  <code>{s['fail']}</code>",
        kb_admin())

def do_broadcast(text):
    ok, fail = 0, 0
    body = f"<b>إعلان</b>\n\n{E(text[:3500])}"
    for uid in get_all_users():
        try:
            r = send_message(uid, body)
            if r.get("ok"): ok += 1
            else: fail += 1
        except Exception:
            fail += 1
        time.sleep(0.05)
    return ok, fail

# ═══════════ النصوص ═══════════
def welcome_text():
    return (f"<b>{E(BOT_NAME)}</b>\n\n"
            f"أرسل رابط أي فيديو.\n"
            f"واختر فيديو أو صوت.\n\n"
            f"<i>YouTube · Instagram · TikTok · Facebook · X</i>")

HELP = ("<b>طريقة الاستخدام</b>\n\n"
        "1 — أرسل رابط الفيديو\n"
        "2 — اختر فيديو أو صوت\n"
        "3 — استلم الملف مباشرة\n\n"
        "<i>الحد الأقصى للإرسال 50 MB</i>")

# ═══════════ المعالجات ═══════════
def handle_message(msg):
    chat_id  = msg["chat"]["id"]
    user_id  = msg["from"]["id"]
    username = msg["from"].get("username", "")
    fname    = msg["from"].get("first_name", "")
    text     = (msg.get("text") or "").strip()

    add_user(user_id, username, fname)

    if is_admin(user_id) and admin_state.get(user_id) == "await_broadcast":
        admin_state.pop(user_id, None)
        status = send_message(chat_id, "<i>جارٍ الإرسال…</i>")
        ok, fail = do_broadcast(text)
        edit_message(chat_id, status.get("result", {}).get("message_id"),
            f"<b>تمت الإذاعة</b>\n\nوصلت  <code>{ok}</code>\nفشلت  <code>{fail}</code>")
        return

    if not text: return

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/start":
            send_message(chat_id, welcome_text())
        elif cmd == "/help":
            send_message(chat_id, HELP)
        elif cmd == "/id":
            who = f"\n@{E(username)}" if username else ""
            send_message(chat_id, f"<b>معرّفك</b>\n<code>{user_id}</code>{who}")
        elif cmd in ("/admin", "/panel") and is_admin(user_id):
            admin_panel(chat_id)
        elif cmd == "/broadcast" and is_admin(user_id):
            parts = text.partition(" ")
            if parts[2].strip():
                ok, fail = do_broadcast(parts[2].strip())
                send_message(chat_id, f"<b>تمت الإذاعة</b>\n\nوصلت  <code>{ok}</code>\nفشلت  <code>{fail}</code>")
            else:
                admin_state[user_id] = "await_broadcast"
                send_message(chat_id, "أرسل الآن نص الإذاعة.")
        return

    url = re.search(r"https?://[^\s<>\"']+", text)
    if url:
        threading.Thread(target=process_link,
            args=(chat_id, user_id, url.group(0)), daemon=True).start()
    else:
        send_message(chat_id, "أرسل <b>رابط فيديو</b> صالح، أو اكتب /help")

def start_download(chat_id, mid, user_id, tok, sess, mode, fmt_id=None):
    with session_lock:
        if sess["busy"]: return
        sess["busy"] = True
    delete_message(chat_id, mid)          # يحذف الكارت أول ما تضغط
    threading.Thread(target=do_download,
        args=(chat_id, user_id, tok, mode, fmt_id), daemon=True).start()

def handle_callback(cb):
    user_id = cb["from"]["id"]
    msg     = cb["message"]
    chat_id = msg["chat"]["id"]
    mid     = msg["message_id"]
    data    = cb.get("data", "")
    parts   = data.split(":")
    action  = parts[0]
    alert   = ""

    if action == "adm":
        if not is_admin(user_id):
            alert = "غير مصرّح"
        elif data == "adm:stats":
            admin_stats(chat_id)
        elif data == "adm:users":
            send_message(chat_id, f"<b>المستخدمون</b>\n<code>{get_users_count()}</code>", kb_admin())
        elif data == "adm:broadcast":
            admin_state[user_id] = "await_broadcast"
            send_message(chat_id, "أرسل الآن نص الإذاعة.\n<i>أو استخدم /broadcast مع النص مباشرة</i>")
        elif data == "adm:ping":
            send_message(chat_id, "<b>البوت يعمل</b>", kb_admin())

    elif action in ("v", "a", "q", "b", "f") and len(parts) >= 2:
        tok = parts[1]
        with session_lock:
            sess = sessions.get(tok)
        if not sess or sess["user_id"] != user_id:
            alert = "انتهت الجلسة، أرسل الرابط من جديد"
        elif action == "v":
            start_download(chat_id, mid, user_id, tok, sess, "video")
        elif action == "a":
            start_download(chat_id, mid, user_id, tok, sess, "audio")
        elif action == "q":
            edit_markup(chat_id, mid, kb_formats(tok, sess["qualities"]))
        elif action == "b":
            edit_markup(chat_id, mid, kb_main(tok, bool(sess["qualities"])))
        elif action == "f" and len(parts) == 3:
            try: idx = int(parts[2])
            except ValueError: idx = -1
            if 0 <= idx < len(sess["qualities"]):
                start_download(chat_id, mid, user_id, tok, sess, "video",
                               sess["qualities"][idx]["format_id"])

    answer_callback(cb["id"], alert)

def get_updates(offset):
    try:
        r = requests.get(f"{TG_BASE}/getUpdates",
            params={"offset": offset, "timeout": 30,
                    "allowed_updates": json.dumps(["message", "callback_query"])},
            timeout=40)
        return r.json()
    except Exception as e:
        log.warning(f"getUpdates: {e}")
        return {"ok": False, "result": []}

def main():
    global OFFSET, BOT_USERNAME
    if not BOT_TOKEN:
        log.error("BOT_TOKEN غير موجود. اضبطه كمتغير بيئة.")
        return
    me = requests.get(f"{TG_BASE}/getMe", timeout=15).json()
    if not me.get("ok"):
        log.error("توكن غير صالح"); return
    BOT_USERNAME = me["result"].get("username", "")

    tg_call("setMyCommands", data={"commands": json.dumps([
        {"command": "start", "description": "البداية"},
        {"command": "help",  "description": "طريقة الاستخدام"},
        {"command": "id",    "description": "معرّفك"},
    ])})
    tg_call("setMyShortDescription", data={"short_description": "تحميل الفيديو والصوت من أي رابط"})
    tg_call("setMyDescription", data={"description":
        "أرسل رابط أي فيديو، واختر فيديو أو صوت.\nYouTube · Instagram · TikTok · Facebook · X"})

    log.info(f"@{BOT_USERNAME} | المستخدمون: {get_users_count()}")
    while True:
        try:
            ups = get_updates(OFFSET)
            if not ups.get("ok"): time.sleep(2); continue
            for up in ups.get("result", []):
                OFFSET = up["update_id"] + 1
                if "message" in up: handle_message(up["message"])
                elif "callback_query" in up: handle_callback(up["callback_query"])
        except KeyboardInterrupt: break
        except Exception as e:
            log.exception(f"loop: {e}"); time.sleep(3)

if __name__ == "__main__":
    main()
