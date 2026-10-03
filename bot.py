import os
import re
import json
import time
import sqlite3
import threading
import logging
import requests
import yt_dlp

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8609529978:AAGLWxFRa3UPn_Ly14ovC75sYcxLRZx3naI")
ADMIN_ID  = str(os.environ.get("ADMIN_ID", ""))
DB_PATH   = "bot.db"
DOWNLOAD_DIR = "downloads"
MAX_TG_SIZE  = 49 * 1024 * 1024
TG_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bot")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ─── قاعدة البيانات ───
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

# ─── الجلسات ───
sessions = {}
session_lock = threading.Lock()
admin_state = {}  # حالة الأدمن (بانتظار رسالة الإذاعة...)

stats = {"success": 0, "fail": 0}
stats_lock = threading.Lock()
OFFSET = 0

def is_admin(uid): return str(uid) == str(ADMIN_ID)

# ═══════════ وظائف تيليجرام ═══════════
def tg_call(method, data=None, files=None, timeout=60):
    try:
        r = requests.post(f"{TG_BASE}/{method}", data=data, files=files, timeout=timeout)
        return r.json()
    except Exception as e:
        log.warning(f"tg_call({method}): {e}")
        return {"ok": False}

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text[:4000],
               "parse_mode": "Markdown", "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg_call("sendMessage", data=payload)

def edit_message(chat_id, mid, text, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": mid, "text": text[:4000],
               "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg_call("editMessageText", data=payload)

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
                    "supports_streaming": True, "parse_mode": "Markdown"}
            if duration: data["duration"] = int(duration)
            return tg_call("sendVideo", data=data, files={"video": f}, timeout=900).get("ok", False)
    except Exception as e:
        log.warning(f"send_video: {e}"); return False

def send_audio(chat_id, path, caption, title="", performer=""):
    try:
        with open(path, "rb") as f:
            data = {"chat_id": chat_id, "caption": caption[:1000],
                    "parse_mode": "Markdown", "title": title[:64], "performer": performer[:64]}
            return tg_call("sendAudio", data=data, files={"audio": f}, timeout=900).get("ok", False)
    except Exception as e:
        log.warning(f"send_audio: {e}"); return False

def send_document(chat_id, path, caption):
    try:
        with open(path, "rb") as f:
            return tg_call("sendDocument",
                data={"chat_id": chat_id, "caption": caption[:1000], "parse_mode": "Markdown"},
                files={"document": f}, timeout=900).get("ok", False)
    except Exception:
        return False

# ═══════════ أدوات ═══════════
def human_size(b):
    if not b: return "?"
    for u in ("B","KB","MB","GB"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def human_duration(sec):
    if not sec: return "0:00"
    m, s = divmod(int(sec), 60); h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def safe_filename(name):
    return (re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name)[:60].strip() or "video")

def progress_bar(done, total):
    pct = done * 100 // max(total, 1)
    return f"`[{ '█' * (pct//5)}{'░' * (20 - pct//5)}] {pct}%`", pct

# ═══════════ الأزرار الاحترافية ═══════════
def kb_main():
    return {"inline_keyboard": [
        [{"text": " 🎬 تحميل فيديو (HD + صوت)", "callback_data": "dl:video"}],
        [{"text": " 🎵 تحميل صوت فقط (MP3)", "callback_data": "dl:audio"}],
        [{"text": " ℹ️ صيغ إضافية", "callback_data": "dl:formats"}],
    ]}

def kb_formats(formats):
    rows, row = [], []
    for i, f in enumerate(formats[:8]):
        row.append({"text": f" {f.get('height','?')}p", "callback_data": f"fmt:{i}"})
        if len(row) == 3: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([{"text": " 🔙 رجوع", "callback_data": "dl:back"}])
    return {"inline_keyboard": rows}

def kb_admin():
    return {"inline_keyboard": [
        [{"text": " 📊 الإحصائيات", "callback_data": "adm:stats"}],
        [{"text": " 📢 إذاعة للجميع", "callback_data": "adm:broadcast"},
         {"text": " 👥 المستخدمون", "callback_data": "adm:users"}],
        [{"text": " 🔴 إيقاف البوت", "callback_data": "adm:ping"}],
    ]}

# ═══════════ استخراج المعلومات ═══════════
def extract_info(url):
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        log.warning(f"extract: {e}")
        return None

def fmt_qualities(info):
    out = []
    seen = set()
    for f in info.get('formats', []):
        if f.get('vcodec') != 'none' and f.get('height'):
            h = f['height']
            if h not in seen and f.get('url'):
                seen.add(h)
                out.append({'height': h, 'ext': f.get('ext','mp4'), 'url': info.get('webpage_url'), 'format_id': f['format_id']})
    return sorted(out, key=lambda x: -x['height'])[:6]

# ═══════════ التحميل بـ yt-dlp (فيديو + صوت مدموج!) ═══════════
def ytdlp_download(url, out_path, mode, fmt_id=None, progress_cb=None):
    last = [0.0]
    def hook(d):
        if d['status'] == 'downloading' and progress_cb:
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            if total and time.time() - last[0] >= 3:
                last[0] = time.time()
                progress_cb(d.get('downloaded_bytes', 0), total)
    opts = {
        'outtmpl': out_path,
        'quiet': True, 'no_warnings': True,
        'progress_hooks': [hook],
        'merge_output_format': 'mp4',
        'ffmpeg_location': '/usr/bin/ffmpeg',
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

def find_file(title, exts):
    base = safe_filename(title)
    for ext in exts:
        p = os.path.join(DOWNLOAD_DIR, f"{base}.{ext}")
        if os.path.exists(p) and os.path.getsize(p) > 10000:
            return p
    # yt-dlp أحياناً يضيف رقم
    for f in os.listdir(DOWNLOAD_DIR):
        if f.startswith(base[:30]) and f.rsplit('.',1)[-1] in exts:
            p = os.path.join(DOWNLOAD_DIR, f)
            if os.path.getsize(p) > 10000: return p
    return None

# ═══════════ معالجة الرابط ═══════════
def process_link(chat_id, user_id, url):
    msg = send_message(chat_id, " ⏳ *جارٍ تحليل الرابط...*")
    mid = msg.get("result", {}).get("message_id")

    info = extract_info(url)
    if not info:
        edit_message(chat_id, mid, " ❌ *تعذر جلب الفيديو!*\nتأكد أن الرابط صحيح والفيديو عام.")
        with stats_lock: stats["fail"] += 1
        return

    title   = info.get('title', 'فيديو')
    author  = info.get('uploader') or info.get('channel') or "-"
    thumb   = info.get('thumbnail')
    dur     = info.get('duration') or 0
    views   = info.get('view_count') or 0
    likes   = info.get('like_count') or 0

    with session_lock:
        sessions[user_id] = {
            "url": url, "info": info, "title": title,
            "qualities": fmt_qualities(info),
        }

    text = (f"🎬 *{title}*\n\n"
            f"👤 `{author}`\n"
            f"⏱ المدة: `{human_duration(dur)}`  |  👁 `{views:,}`\n\n"
            f"❓ *كيف تريد التحميل؟*")

    if thumb:
        delete_message(chat_id, mid)
        r = tg_call("sendPhoto", data={"chat_id": chat_id, "photo": thumb,
                    "caption": text[:1000], "parse_mode": "Markdown",
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
        send_message(chat_id, " ⌛ انتهت الجلسة، أرسل الرابط من جديد.")
        return

    url   = sess["url"]
    info  = sess["info"]
    title = sess["title"]
    dur   = info.get('duration') or 0
    author = info.get('uploader') or info.get('channel') or ""

    labels = {"video": "🎬 فيديو HD بالصوت", "audio": "🎵 صوت MP3", "fmt": "🎬 فيديو"}
    label = labels.get(mode, "ملف")

    status = send_message(chat_id, f" ⬇️ *جارٍ تحميل {label}...*")
    sid = status.get("result", {}).get("message_id")
    send_action(chat_id, "upload_video" if mode != "audio" else "upload_voice")

    def on_progress(done, total):
        bar, _ = progress_bar(done, total)
        if sid:
            edit_message(chat_id, sid,
                f" ⬇️ *{label}*\n{bar}\n{human_size(done)} / {human_size(total)}")

    base = safe_filename(title)
    ok = ytdlp_download(url, os.path.join(DOWNLOAD_DIR, base), mode, fmt_id, on_progress)

    exts = ['mp4', 'mkv', 'webm'] if mode != 'audio' else ['mp3', 'm4a']
    path = find_file(base, exts) if ok else None

    if not path:
        if sid: edit_message(chat_id, sid, " ❌ *فشل التحميل!* حاول صيغة أخرى.")
        with stats_lock: stats["fail"] += 1
        return

    size = os.path.getsize(path)
    if sid: delete_message(chat_id, sid)

    if size > MAX_TG_SIZE:
        send_message(chat_id,
            f" ⚠️ *الملف كبير جداً!* ({human_size(size)})\n"
            f"حد تيليجرام: 50MB\n\n🔗 [تحميل مباشر]({info.get('webpage_url', url)})")
    else:
        caption = f"🎬 *{title}*\n👤 `{author}`\n📦 `{human_size(size)}`"
        sent = False
        if mode == 'audio':
            sent = send_audio(chat_id, path, caption, title=title, performer=author)
        else:
            sent = send_video(chat_id, path, caption, duration=dur)
        if not sent:
            sent = send_document(chat_id, path, caption)
        if sent:
            with stats_lock: stats["success"] += 1
            add_download(user_id)

    try: os.remove(path)
    except OSError: pass

# ═══════════ لوحة تحكم الأدمن ═══════════
def admin_panel(chat_id):
    send_message(chat_id,
        " 👑 *لوحة تحكم المالك*\n\nاختر من القائمة:",
        kb_admin())

def admin_stats(chat_id):
    total_dl = 0
    with db_lock:
        total_dl = conn.execute("SELECT SUM(downloads) FROM users").fetchone()[0] or 0
    with stats_lock: s = dict(stats)
    send_message(chat_id,
        f" 📊 *إحصائيات البوت*\n\n"
        f"👥 المستخدمون: `{get_users_count()}`\n"
        f"📥 إجمالي التحميلات: `{total_dl}`\n"
        f"✅ ناجحة: `{s['success']}`\n"
        f"❌ فاشلة: `{s['fail']}`\n"
        f"⏰ التشغيل: كل 5 ساعات تلقائياً",
        kb_admin())

def do_broadcast(text):
    users = get_all_users()
    ok, fail = 0, 0
    for uid in users:
        try:
            r = send_message(uid, f" 📢 *إذاعة*\n\n{text}")
            if r.get("ok"): ok += 1
            else: fail += 1
        except Exception:
            fail += 1
        time.sleep(0.05)
    return ok, fail

# ═══════════ الرسائل ═══════════
WELCOME = """👑 *أهلاً بك في البوت الذهبي*

📥 حمّل من *1000+ موقع*:
YouTube • TikTok • Instagram • X • Facebook • والمزيد

⚡ أرسل الرابط مباشرة واختر: فيديو 🎬 أو صوت 🎵

📌 /start - البداية
📖 /help - المساعدة
🆔 /id - معرفك"""

HELP = """📖 *طريقة الاستخدام*

1️⃣ أرسل رابط أي فيديو
2️⃣ البوت يعرض معلومات الفيديو
3️⃣ اختر: 🎬 فيديو بالصوت أو 🎵 MP3
4️⃣ استلم الملف مباشرة!

✨ مميزات: جودة HD، صوت نقي، سرعة فائقة"""

# ═══════════ المعالجات ═══════════
def handle_message(msg):
    chat_id  = msg["chat"]["id"]
    user_id  = msg["from"]["id"]
    username = msg["from"].get("username", "")
    fname    = msg["from"].get("first_name", "")
    text     = (msg.get("text") or "").strip()

    add_user(user_id, username, fname)

    # وضع إذاعة الأدمن
    if is_admin(user_id) and admin_state.get(user_id) == "await_broadcast":
        admin_state.pop(user_id, None)
        status = send_message(chat_id, " 📢 جارٍ الإذاعة...")
        ok, fail = do_broadcast(text)
        edit_message(chat_id, status.get("result",{}).get("message_id"),
            f" ✅ *تمت الإذاعة!*\n\nوصلت: `{ok}`\nفشلت: `{fail}`")
        return

    if not text: return

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/start":
            send_message(chat_id, WELCOME)
        elif cmd == "/help":
            send_message(chat_id, HELP)
        elif cmd == "/id":
            send_message(chat_id, f" 🆔 `{user_id}`\n👤 @{username}")
        elif cmd in ("/admin", "/panel") and is_admin(user_id):
            admin_panel(chat_id)
        elif cmd == "/broadcast" and is_admin(user_id):
            parts = text.partition(" ")
            if parts[2].strip():
                ok, fail = do_broadcast(parts[2].strip())
                send_message(chat_id, f" ✅ وصلت: `{ok}` | فشلت: `{fail}`")
            else:
                admin_state[user_id] = "await_broadcast"
                send_message(chat_id, " 📢 أرسل الآن رسالة الإذاعة:")
        return

    url = re.search(r"https?://[^\s<>\"']+", text)
    if url:
        threading.Thread(target=process_link,
            args=(chat_id, user_id, url.group(0)), daemon=True).start()
    else:
        send_message(chat_id, " ❓ أرسل *رابط فيديو* صالح، أو اكتب /help")

def handle_callback(cb):
    user_id = cb["from"]["id"]
    chat_id = cb["message"]["chat"]["id"]
    mid     = cb["message"]["message_id"]
    data    = cb.get("data", "")
    answer_callback(cb["id"])

    with session_lock:
        sess = sessions.get(user_id)

    if data == "dl:video":
        threading.Thread(target=do_download, args=(chat_id, user_id, "video"), daemon=True).start()
    elif data == "dl:audio":
        threading.Thread(target=do_download, args=(chat_id, user_id, "audio"), daemon=True).start()
    elif data == "dl:formats":
        if not sess: return
        q = sess.get("qualities", [])
        if q:
            edit_message(chat_id, mid, " 🎞 *اختر الجودة:*", kb_formats(q))
        else:
            answer_callback(cb["id"], "لا توجد صيغ إضافية")
    elif data == "dl:back":
        if sess:
            edit_message(chat_id, mid, " ❓ *كيف تريد التحميل؟*", kb_main())
    elif data.startswith("fmt:"):
        idx = int(data.split(":")[1])
        if sess and idx < len(sess["qualities"]):
            fid = sess["qualities"][idx]["format_id"]
            threading.Thread(target=do_download,
                args=(chat_id, user_id, "video", fid), daemon=True).start()
    elif data.startswith("adm:"):
        if not is_admin(user_id): return
        if data == "adm:stats":
            admin_stats(chat_id)
        elif data == "adm:users":
            send_message(chat_id,
                f" 👥 عدد المستخدمين: `{get_users_count()}`",
                kb_admin())
        elif data == "adm:broadcast":
            admin_state[user_id] = "await_broadcast"
            send_message(chat_id, " 📢 أرسل الآن رسالة الإذاعة:\n(أو /broadcast نص مباشرة)")
        elif data == "adm:ping":
            send_message(chat_id, " 🟢 *البوت يعمل بشكل ممتاز!*", kb_admin())

def get_updates(offset):
    try:
        r = requests.get(f"{TG_BASE}/getUpdates",
            params={"offset": offset, "timeout": 30,
                    "allowed_updates": json.dumps(["message","callback_query"])},
            timeout=40)
        return r.json()
    except Exception as e:
        log.warning(f"getUpdates: {e}")
        return {"ok": False, "result": []}

def main():
    global OFFSET
    me = requests.get(f"{TG_BASE}/getMe", timeout=15).json()
    if not me.get("ok"):
        log.error("توكن غير صالح"); return
    log.info(f"البوت: @{me['result']['username']} | المستخدمون: {get_users_count()}")
    log.info("👑 البوت الفخم يعمل الآن!")
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
