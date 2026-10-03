import os
import re
import json
import time
import threading
import logging
import requests

BOT_TOKEN    = os.environ.get("BOT_TOKEN", "8609529978:AAGLWxFRa3UPn_Ly14ovC75sYcxLRZx3naI")
ADMIN_ID     = os.environ.get("ADMIN_ID", "")
DOWNLOAD_DIR = "downloads"
MAX_TG_SIZE  = 50 * 1024 * 1024
API_EXTRACT  = os.environ.get("API_EXTRACT", "https://cobalt.tools/api/json")
TG_BASE      = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

sessions     = {}
session_lock = threading.Lock()

stats      = {"total": 0, "success": 0, "fail": 0}
stats_lock = threading.Lock()

OFFSET = 0


def human_size(b):
    if not b:
        return "?"
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def human_duration(sec):
    if not sec:
        return "?"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def safe_filename(name):
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name)
    return (name[:80].strip() or "video")


def tg_call(method, data=None, files=None, timeout=30):
    try:
        r = requests.post(f"{TG_BASE}/{method}", data=data, files=files, timeout=timeout)
        return r.json()
    except Exception as e:
        log.warning(f"tg_call({method}): {e}")
        return {"ok": False}


def send_message(chat_id, text, parse_mode="Markdown", reply_markup=None, reply_to=None):
    payload = {
        "chat_id": chat_id,
        "text": text[:4000],
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = (
            json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
        )
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    return tg_call("sendMessage", data=payload)


def edit_message(chat_id, message_id, text, parse_mode="Markdown"):
    return tg_call("editMessageText", data={
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:4000],
        "parse_mode": parse_mode,
    })


def delete_message(chat_id, message_id):
    return tg_call("deleteMessage", data={
        "chat_id": chat_id,
        "message_id": message_id,
    })


def send_chat_action(chat_id, action="typing"):
    tg_call("sendChatAction", data={"chat_id": chat_id, "action": action})


def answer_callback(cb_id, text=""):
    tg_call("answerCallbackQuery", data={"callback_query_id": cb_id, "text": text})


def send_photo(chat_id, photo_url, caption=""):
    return tg_call("sendPhoto", data={
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption[:1000],
        "parse_mode": "Markdown",
    })


def send_video(chat_id, path, caption="", duration=None):
    try:
        with open(path, "rb") as f:
            data = {
                "chat_id": chat_id,
                "caption": caption[:1000],
                "supports_streaming": True,
            }
            if duration:
                data["duration"] = int(duration)
            r = tg_call("sendVideo", data=data, files={"video": f}, timeout=600)
            return r.get("ok", False)
    except Exception as e:
        log.warning(f"send_video: {e}")
        return False


def send_audio(chat_id, path, caption="", title="", performer=""):
    try:
        with open(path, "rb") as f:
            data = {"chat_id": chat_id, "caption": caption[:1000]}
            if title:
                data["title"] = title[:64]
            if performer:
                data["performer"] = performer[:64]
            r = tg_call("sendAudio", data=data, files={"audio": f}, timeout=600)
            return r.get("ok", False)
    except Exception as e:
        log.warning(f"send_audio: {e}")
        return False


def send_document(chat_id, path, caption=""):
    try:
        with open(path, "rb") as f:
            r = tg_call(
                "sendDocument",
                data={"chat_id": chat_id, "caption": caption[:1000]},
                files={"document": f},
                timeout=600,
            )
            return r.get("ok", False)
    except Exception as e:
        log.warning(f"send_document: {e}")
        return False


def api_extract(url):
    """استخراج روابط التحميل"""
    try:
        # Cobalt API - مجاني وبدون حقوق
        r = requests.post(
            API_EXTRACT,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            json={
                "url": url,
                "isAudioOnly": False,
                "aFormat": "mp3"
            },
            timeout=60,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "stream":
                return {
                    "title": data.get("filename", "video"),
                    "author": "Unknown",
                    "source": "web",
                    "duration": 0,
                    "views": 0,
                    "likes": 0,
                    "thumbnail": None,
                    "formats": [
                        {
                            "label": "HD",
                            "ext": "mp4",
                            "type": "video",
                            "url": data.get("url")
                        }
                    ]
                }
        log.warning(f"api_extract status: {r.status_code}")
        return None
    except Exception as e:
        log.warning(f"api_extract: {e}")
        return None


def api_health():
    try:
        return requests.get("https://cobalt.tools", timeout=10).status_code == 200
    except Exception:
        return False


def download_file(url, filepath, progress_cb=None):
    try:
        with requests.get(url, stream=True, timeout=180) as r:
            total = int(r.headers.get("content-length", 0))
            done = 0
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(done, total)
        return os.path.getsize(filepath) > 1000
    except Exception as e:
        log.warning(f"download_file: {e}")
        return False


def build_caption(data):
    title  = data.get("title", "بدون عنوان")
    author = data.get("author", "-")
    dur    = human_duration(data.get("duration"))
    views  = data.get("views", 0) or 0
    likes  = data.get("likes", 0) or 0
    source = data.get("source", "-")

    return (
        f"*{title}*\n\n"
        f"القناة: `{author}`\n"
        f"المصدر: `{source}`\n"
        f"المدة: `{dur}`\n"
        f"المشاهدات: `{views:,}`\n"
        f"اللايكات: `{likes:,}`"
    )


def build_keyboard(formats):
    buttons, row = [], []
    for i, fmt in enumerate(formats):
        label = fmt.get("label", "?")
        ext   = fmt.get("ext", "?")
        row.append({"text": f"{label} - {ext}", "callback_data": f"fmt:{i}"})
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([{"text": " فيديو + صوتيه ", "callback_data": "both"}])
    return {"inline_keyboard": buttons}


def process_link(chat_id, url, user_id):
    send_message(chat_id, " جاري جلب المعلومات...")

    data = api_extract(url)
    if not data:
        send_message(chat_id, " فشل جلب المعلومات. تأكد من الرابط.")
        with stats_lock:
            stats["fail"] += 1
        return

    with stats_lock:
        stats["total"] += 1

    title = data.get("title", "video")
    thumb = data.get("thumbnail")

    with session_lock:
        sessions[user_id] = {"data": data, "title": title}

    caption = build_caption(data)

    if thumb:
        res = send_photo(chat_id, thumb, caption)
        if not res.get("ok"):
            send_message(chat_id, caption)
    else:
        send_message(chat_id, caption)

    formats = data.get("formats", [])
    if formats:
        send_message(chat_id, " اختر الصيغة:",
                     reply_markup=build_keyboard(formats))
    else:
        send_message(chat_id, " لا توجد صيغ تحميل متاحة.")


def download_and_send(chat_id, user_id, fmt_index):
    with session_lock:
        sess = sessions.get(user_id)
    if not sess:
        send_message(chat_id, " انتهت الجلسة. أرسل الرابط مرة أخرى.")
        return

    data    = sess["data"]
    title   = sess["title"]
    formats = data.get("formats", [])

    if not (0 <= fmt_index < len(formats)):
        send_message(chat_id, " صيغة غير صحيحة.")
        return

    fmt   = formats[fmt_index]
    label = fmt.get("label", "?")
    ext   = fmt.get("ext", "mp4")
    kind  = fmt.get("type", "video")
    url   = fmt.get("url")

    filename = f"{safe_filename(title)}.{ext}"
    filepath = os.path.join(DOWNLOAD_DIR, filename)

    status    = send_message(chat_id, f" جاري تحميل {label}...")
    status_id = status.get("result", {}).get("message_id") if status.get("ok") else None

    send_chat_action(chat_id, "upload_video" if kind == "video" else "upload_voice")

    last = [0.0]

    def on_progress(done, total):
        if time.time() - last[0] < 3:
            return
        last[0] = time.time()
        pct = done * 100 // max(total, 1)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        if status_id:
            edit_message(
                chat_id, status_id,
                f" *تحميل {label}*\n`[{bar}] {pct}%`\n"
                f"{human_size(done)} / {human_size(total)}"
            )

    if not download_file(url, filepath, on_progress):
        send_message(chat_id, " فشل التحميل.")
        with stats_lock:
            stats["fail"] += 1
        return

    size = os.path.getsize(filepath)

    if status_id:
        delete_message(chat_id, status_id)

    if size > MAX_TG_SIZE:
        send_message(
            chat_id,
            f" الملف كبير ({human_size(size)})\n"
            f"الحد الأقصى لتليكرام هو 50MB.\n\n🔗 الرابط المباشر:\n`{url}`"
        )
    else:
        caption = f"*{title}*\n {label} - {human_size(size)}"
        sent = False
        if kind == "video":
            sent = send_video(chat_id, filepath, caption, duration=data.get("duration"))
        elif kind == "audio":
            sent = send_audio(chat_id, filepath, caption,
                              title=title, performer=data.get("author", ""))
        else:
            sent = send_document(chat_id, filepath, caption)

        if sent:
            with stats_lock:
                stats["success"] += 1

    try:
        os.remove(filepath)
    except OSError:
        pass


def download_both(chat_id, user_id):
    with session_lock:
        sess = sessions.get(user_id)
    if not sess:
        send_message(chat_id, " انتهت الجلسة.")
        return

    formats = sess["data"].get("formats", [])
    picks   = []

    video = next((f for f in formats if f.get("type") == "video"), None)
    audio = next((f for f in formats if f.get("type") == "audio"), None)

    if video:
        picks.append(formats.index(video))
    if audio:
        picks.append(formats.index(audio))

    for idx in picks:
        download_and_send(chat_id, user_id, idx)


WELCOME = """🎬 *مرحباً بك في بوت تحميل الفيديوهات*

 أرسل أي رابط فيديو من:
YouTube | TikTok | Instagram | X | 1600+ موقع

⚡ *مثال:*
`https://youtu.be/l08Zw-RY__Q`

📌 *الأوامر:*
/start - البداية
/help - المساعدة
/stats - الإحصائيات
/id - ايديك
"""

HELP = """📖 *طريقة الاستخدام*

1️⃣ أرسل رابط فيديو
2️⃣ البوت يعرض العنوان والقناة والصيغ
3️⃣ اختر الصيغة للتحميل

"""


def handle_command(chat_id, cmd, user_id, username):
    if cmd == "/start":
        send_message(chat_id, WELCOME)
    elif cmd == "/help":
        send_message(chat_id, HELP)
    elif cmd == "/id":
        send_message(chat_id, f" `{user_id}`\n @{username}")
    elif cmd == "/stats":
        with stats_lock:
            s = dict(stats)
        send_message(chat_id,
            f"*إحصائيات البوت*\n\n"
            f" الإجمالي: `{s['total']}`\n"
            f" نجحت: `{s['success']}`\n"
            f" فشلت: `{s['fail']}`"
        )


URL_REGEX = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_url(text):
    m = URL_REGEX.search(text)
    return m.group(0) if m else None


def handle_message(msg):
    chat_id  = msg["chat"]["id"]
    user_id  = msg["from"]["id"]
    username = msg["from"].get("username", "unknown")
    text     = (msg.get("text") or "").strip()

    if not text:
        return

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        handle_command(chat_id, cmd, user_id, username)
        return

    url = extract_url(text)
    if url:
        threading.Thread(
            target=process_link,
            args=(chat_id, url, user_id),
            daemon=True,
        ).start()
    else:
        send_message(chat_id, "❓ أرسل رابط فيديو، أو اكتب /help")


def handle_callback(cb):
    user_id = cb["from"]["id"]
    chat_id = cb["message"]["chat"]["id"]
    data    = cb.get("data", "")
    cb_id   = cb["id"]

    answer_callback(cb_id)

    if data.startswith("fmt:"):
        try:
            idx = int(data.split(":", 1)[1])
        except ValueError:
            return
        threading.Thread(
            target=download_and_send,
            args=(chat_id, user_id, idx),
            daemon=True,
        ).start()

    elif data == "both":
        threading.Thread(
            target=download_both,
            args=(chat_id, user_id),
            daemon=True,
        ).start()


def get_updates(offset):
    try:
        r = requests.get(
            f"{TG_BASE}/getUpdates",
            params={
                "offset": offset,
                "timeout": 30,
                "allowed_updates": json.dumps(
                    ["message", "callback_query", "edited_message"]
                ),
            },
            timeout=40,
        )
        return r.json()
    except Exception as e:
        log.warning(f"getUpdates: {e}")
        return {"ok": False, "result": []}


def main():
    global OFFSET

    me = requests.get(f"{TG_BASE}/getMe", timeout=15).json()
    if not me.get("ok"):
        log.error("  توكن البوت غير صالح")
        return
    log.info(f" البوت: @{me['result']['username']}")

    log.info(" فحص API ...")
    if api_health():
        log.info(" API يعمل")
    else:
        log.warning(" API لا يستجيب - سنحاول على أي حال")

    log.info(" البوت يعمل الآن. اضغط Ctrl+C للإيقاف.")
    print()

    while True:
        try:
            updates = get_updates(OFFSET)
            if not updates.get("ok"):
                time.sleep(2)
                continue

            for up in updates.get("result", []):
                OFFSET = up["update_id"] + 1

                if "message" in up:
                    handle_message(up["message"])
                elif "edited_message" in up:
                    handle_message(up["edited_message"])
                elif "callback_query" in up:
                    handle_callback(up["callback_query"])

        except KeyboardInterrupt:
            log.info(" تم الإيقاف")
            break
        except Exception as e:
            log.exception(f"loop error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
