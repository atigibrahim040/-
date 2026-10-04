import os
import re
import json
import time
import html
import shutil
import sqlite3
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
BOT_USERNAME = ""

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

# ═══════════ الجلسات ═══════════
sessions = {}
session_lock = threading.Lock()
admin_state = {}

stats = {"success": 0, "fail": 0}
stats_lock = threading.Lock()
OFFSET = 0

def is_admin(uid): return str(uid) == str(ADMIN_ID)

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

def edit_message(chat_id, mid, text, reply_markup=None, photo=False):
    # الرسالة اللي فيها صورة تتعدّل بـ editMessageCaption مو editMessageText
    method = "editMessageCaption" if photo else "editMessageText"
    key = "caption" if photo else "text"
    payload = {"chat_id": chat_id, "message_id": mid, key: text[:1000 if photo else 4000],
               "parse_mode": "HTML"}
    if not photo:
        payload["disable_web_page_preview"] = True
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg_call(method, data=payload)

def delete_message(chat_id, mid):
    tg_call("deleteMessage", data={"chat_id": chat_id, "message_id": mid})

def answer_callback(cb_id, text=""):
    tg_call("answerCallbackQuery", data={"callback_query_id": cb_id, "text": text})

def send_action(chat_id, action):
    tg_call("sendChatAction", data={"chat_id": chat_id, "action": action})

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
def human_size(b):
    if not b: return "—"
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def human_duration(sec):
    if not sec: return "0:00"
    m, s = divmod(int(sec), 60); h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def human_count(n):
    if not n: return "—"
    for div, suf in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suf
    return str(n)

def safe_filename(name):
    return (re.sub(r'[\\/:*?"<>|\n\r\t%]', "_", name)[:60].strip() or "file")

def progress_bar(done, total):
    pct = min(done * 100 // max(total, 1), 100)
    n = pct // 10
    return f"{'▰' * n}{'▱' * (10 - n)}  {pct}%"

def cleanup(tag):
    for f in os.listdir(DOWNLOAD_DIR):
        if f.startswith(tag + "."):
            try: os.remove(os.path.join(DOWNLOAD_DIR, f))
            except OSError: pass

# ═══════════ الأزرار ═══════════
def kb_main():
    return {"inline_keyboard": [
        [{"text": "فيديو HD", "callback_data": "dl:video"},
         {"text": "صوت MP3", "callback_data": "dl:audio"}],
        [{"text": "اختيار الجودة", "callback_data": "dl:formats"}],
    ]}

def kb_formats(formats):
    rows, row = [], []
    for i, f in enumerate(formats[:6]):
        row.append({"text": f"{f.get('height', '?')}p", "callback_data": f"fmt:{i}"})
        if len(row) == 3: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([{"text": "رجوع", "callback_data": "dl:back"}])
    return {"inline_keyboard": rows}

def kb_admin():
    return {"inline_keyboard": [
        [{"text": "الإحصائيات", "callback_data": "adm:stats"},
         {"text": "المستخدمون", "callback_data": "adm:users"}],
        [{"text": "إذاعة", "callback_data": "adm:broadcast"},
         {"text": "الحالة", "callback_data": "adm:ping"}],
    ]}

# ═══════════ yt-dlp ═══════════
def extract_info(url):
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'noplaylist': True}) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        log.warning(f"extract: {e}")
        return None

def fmt_qualities(info):
    out, seen = [], set()
    for f in info.get('formats', []):
        if f.get('vcodec') != 'none' and f.get('height'):
            h = f['height']
            if h not in seen and f.get('url'):
                seen.add(h)
                out.append({'height': h, 'ext': f.get('ext', 'mp4'),
                            'url': info.get('webpage_url'), 'format_id': f['format_id']})
    return sorted(out, key=lambda x: -x['height'])[:6]

def ytdlp_download(url, out_tpl, mode, fmt_id=None, progress_cb=None):
    last = [0.0]
    def hook(d):
        if d['status'] == 'downloading' and progress_cb:
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            if total and time.time() - last[0] >= 3:
                last[0] = time.time()
                progress_cb(d.get('downloaded_bytes', 0), total)
    opts = {
        'outtmpl': out_tpl,
        'quiet': True, 'no_warnings': True, 'noplaylist': True,
        'progress_hooks': [hook],
        'merge_output_format': 'mp4',
        'ffmpeg_location': FFMPEG,
    }
    if mode == 'video':
        opts['format'] = f"{fmt_id}+bestaudio/best" if fmt_id else "bestvideo+bestaudio/best"
    elif mode == 'audio':
        opts['format'] = "bestaudio/best"
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        log.warning(f"ytdlp_download: {e}")
        return False

def find_file(tag, exts):
    for ext in exts:
        p = os.path.join(DOWNLOAD_DIR, f"{tag}.{ext}")
        if os.path.exists(p) and os.path.getsize(p) > 10000:
            return p
    return None

# ═══════════ معالجة الرابط ═══════════
def process_link(chat_id, user_id, url):
    msg = send_message(chat_id, "<i>جارٍ قراءة الرابط…</i>")
    mid = msg.get("result", {}).get("message_id")

    info = extract_info(url)
    if not info:
        edit_message(chat_id, mid,
            "<b>تعذّر قراءة الرابط</b>\nتأكد أنه صحيح وأن الفيديو عام.")
        with stats_lock: stats["fail"] += 1
        return

    title  = info.get('title') or "بدون عنوان"
    author = info.get('uploader') or info.get('channel') or ""
    thumb  = info.get('thumbnail')
    dur    = info.get('duration') or 0
    views  = info.get('view_count') or 0

    with session_lock:
        sessions[user_id] = {
            "url": url, "info": info, "title": title,
            "qualities": fmt_qualities(info),
        }

    lines = [f"<b>{E(title[:150])}</b>"]
    if author: lines.append(E(author[:60]))
    meta = []
    if dur:   meta.append(f"المدة  <code>{human_duration(dur)}</code>")
    if views: meta.append(f"المشاهدات  <code>{human_count(views)}</code>")
    if meta: lines += ["", *meta]
    lines += ["", "<i>اختر الصيغة</i>"]
    text = "\n".join(lines)

    if thumb:
        delete_message(chat_id, mid)
        r = tg_call("sendPhoto", data={"chat_id": chat_id, "photo": thumb,
                    "caption": text, "parse_mode": "HTML",
                    "reply_markup": json.dumps(kb_main())})
        if not r.get("ok"):
            send_message(chat_id, text, kb_main())
    else:
        edit_message(chat_id, mid, text, kb_main())

# ═══════════ التحميل والإرسال ═══════════
def do_download(chat_id, user_id, mode, fmt_id=None):
    with session_lock:
        sess = sessions.get(user_id)
    if not sess:
        send_message(chat_id, "انتهت الجلسة. أرسل الرابط من جديد.")
        return

    url    = sess["url"]
    info   = sess["info"]
    title  = sess["title"]
    dur    = info.get('duration') or 0
    author = info.get('uploader') or info.get('channel') or ""

    label = "الصوت" if mode == "audio" else "الفيديو"
    status = send_message(chat_id, f"<b>جارٍ تحميل {label}</b>")
    sid = status.get("result", {}).get("message_id")
    send_action(chat_id, "upload_voice" if mode == "audio" else "upload_video")

    def on_progress(done, total):
        if sid:
            edit_message(chat_id, sid,
                f"<b>جارٍ تحميل {label}</b>\n"
                f"<code>{progress_bar(done, total)}</code>\n"
                f"{human_size(done)} / {human_size(total)}")

    tag  = f"{user_id}_{int(time.time())}"
    ok   = ytdlp_download(url, os.path.join(DOWNLOAD_DIR, tag + ".%(ext)s"),
                          mode, fmt_id, on_progress)
    exts = ['mp3', 'm4a'] if mode == 'audio' else ['mp4', 'mkv', 'webm']
    path = find_file(tag, exts) if ok else None

    if not path:
        if sid: edit_message(chat_id, sid, "<b>فشل التحميل</b>\nجرّب جودة أخرى.")
        with stats_lock: stats["fail"] += 1
        cleanup(tag)
        return

    size = os.path.getsize(path)
    if sid: delete_message(chat_id, sid)

    if size > MAX_TG_SIZE:
        send_message(chat_id,
            f"<b>الملف أكبر من حد تيليجرام</b>\n"
            f"الحجم <code>{human_size(size)}</code> · الحد <code>50 MB</code>\n\n"
            f"<a href=\"{E(info.get('webpage_url', url), quote=True)}\">فتح الرابط الأصلي</a>")
    else:
        footer = f"\n\n@{BOT_USERNAME}" if BOT_USERNAME else ""
        caption = (f"<b>{E(title[:100])}</b>\n"
                   f"{E(author[:50])}{' · ' if author else ''}{human_size(size)}{footer}")
        if mode == 'audio':
            sent = send_audio(chat_id, path, caption, title=title, performer=author)
        else:
            sent = send_video(chat_id, path, caption, duration=dur)
        if not sent:
            name = safe_filename(title) + os.path.splitext(path)[1]
            sent = send_document(chat_id, path, caption, name)
        if sent:
            with stats_lock: stats["success"] += 1
            add_download(user_id)
        else:
            with stats_lock: stats["fail"] += 1

    cleanup(tag)

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
        f"فاشلة  <code>{s['fail']}</code>\n\n"
        f"<i>التشغيل كل 5 ساعات تلقائياً</i>",
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
    return (f"<b>{E(BOT_NAME)}</b>\n"
            f"──────────\n"
            f"حمّل الفيديو والصوت من أكثر من 1000 موقع.\n"
            f"<i>YouTube · TikTok · Instagram · X · Facebook</i>\n\n"
            f"أرسل الرابط للبدء.\n\n"
            f"/help  ·  /id")

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

def handle_callback(cb):
    user_id  = cb["from"]["id"]
    msg      = cb["message"]
    chat_id  = msg["chat"]["id"]
    mid      = msg["message_id"]
    is_photo = bool(msg.get("photo"))
    data     = cb.get("data", "")
    alert    = ""

    with session_lock:
        sess = sessions.get(user_id)

    if data in ("dl:video", "dl:audio"):
        mode = data.split(":")[1]
        threading.Thread(target=do_download, args=(chat_id, user_id, mode), daemon=True).start()

    elif data == "dl:formats":
        q = sess.get("qualities", []) if sess else []
        if not sess:
            alert = "انتهت الجلسة"
        elif not q:
            alert = "لا توجد جودات إضافية"
        else:
            edit_message(chat_id, mid, "<i>اختر الجودة</i>", kb_formats(q), photo=is_photo)

    elif data == "dl:back":
        if sess:
            edit_message(chat_id, mid, "<i>اختر الصيغة</i>", kb_main(), photo=is_photo)

    elif data.startswith("fmt:"):
        try: idx = int(data.split(":")[1])
        except ValueError: idx = -1
        if sess and 0 <= idx < len(sess["qualities"]):
            fid = sess["qualities"][idx]["format_id"]
            threading.Thread(target=do_download,
                args=(chat_id, user_id, "video", fid), daemon=True).start()
        else:
            alert = "انتهت الجلسة"

    elif data.startswith("adm:"):
        if not is_admin(user_id):
            alert = "غير مصرّح"
        elif data == "adm:stats":
            admin_stats(chat_id)
        elif data == "adm:users":
            send_message(chat_id,
                f"<b>المستخدمون</b>\n<code>{get_users_count()}</code>", kb_admin())
        elif data == "adm:broadcast":
            admin_state[user_id] = "await_broadcast"
            send_message(chat_id, "أرسل الآن نص الإذاعة.\n<i>أو استخدم /broadcast مع النص مباشرة</i>")
        elif data == "adm:ping":
            send_message(chat_id, "<b>البوت يعمل</b>", kb_admin())

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
