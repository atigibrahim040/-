import telebot
from telebot import types
import feedparser
import requests
import time
import threading
import os

TOKEN = "8178057085:AAEhCSYjlVY9CjhZBs-GjzbmhkHWSccU2sw"
bot = telebot.TeleBot(TOKEN)

USERS_FILE = "users.txt"
WEATHER_FILE = "weather.txt"
LANG_FILE = "lang.txt"

def load_users():
    if not os.path.exists(USERS_FILE):
        return set()
    with open(USERS_FILE) as f:
        return set(int(x.strip()) for x in f if x.strip().isdigit())

def save_user(user_id):
    users = load_users()
    if user_id not in users:
        with open(USERS_FILE, "a") as f:
            f.write(str(user_id) + "\n")

def remove_user(user_id):
    users = load_users()
    users.discard(user_id)
    with open(USERS_FILE, "w") as f:
        for u in users:
            f.write(str(u) + "\n")

def set_city(user_id, city):
    data = {}
    if os.path.exists(WEATHER_FILE):
        with open(WEATHER_FILE) as f:
            for line in f:
                if ":" in line:
                    uid, c = line.strip().split(":", 1)
                    data[int(uid)] = c
    data[user_id] = city
    with open(WEATHER_FILE, "w") as f:
        for uid, c in data.items():
            f.write(f"{uid}:{c}\n")

def get_city(user_id):
    if not os.path.exists(WEATHER_FILE): return None
    with open(WEATHER_FILE) as f:
        for line in f:
            if ":" in line:
                uid, city = line.strip().split(":", 1)
                if int(uid) == user_id:
                    return city
    return None

def set_lang(user_id, lang):
    with open(LANG_FILE, "a") as f:
        f.write(f"{user_id}:{lang}\n")

def get_lang(user_id):
    if not os.path.exists(LANG_FILE): return "ar"
    lines = open(LANG_FILE).readlines()
    for line in lines:
        if str(user_id) in line:
            return line.strip().split(":")[1]
    return "ar"

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.chat.id
    save_user(user_id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📰 آخر الأخبار", "🌦️ الطقس الآن", "🌐 تغيير اللغة")
    bot.send_message(user_id, "👋 أهلاً بك في بوت الأخبار والطقس!\nاختر خدمة:", reply_markup=markup)

@bot.message_handler(commands=['subscribe'])
def subscribe(message):
    save_user(message.chat.id)
    bot.reply_to(message, "✅ تم الاشتراك في النشرة اليومية.")

@bot.message_handler(commands=['unsubscribe'])
def unsubscribe(message):
    remove_user(message.chat.id)
    bot.reply_to(message, "❌ تم إلغاء الاشتراك من النشرة اليومية.")

@bot.message_handler(commands=['news'])
def send_news_cmd(message):
    send_news(message.chat.id)

@bot.message_handler(commands=['weather'])
def send_weather_cmd(message):
    args = message.text.split(" ", 1)
    if len(args) < 2:
        bot.reply_to(message, "❗ استخدم: /weather <المدينة>")
        return
    city = args[1].strip()
    set_city(message.chat.id, city)
    send_weather(message.chat.id, city)

@bot.message_handler(func=lambda msg: True)
def handle(message):
    user_id = message.chat.id
    text = message.text.lower()
    if text == "📰 آخر الأخبار":
        send_news(user_id)
    elif text == "🌦️ الطقس الآن":
        city = get_city(user_id)
        if city:
            send_weather(user_id, city)
        else:
            bot.send_message(user_id, "📍 من فضلك أرسل المدينة أولًا عبر الأمر:\n/weather <المدينة>")
    elif text == "🌐 تغيير اللغة":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🇸🇦 عربي", callback_data="lang_ar"))
        markup.add(types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"))
        bot.send_message(user_id, "اختر اللغة:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("lang_"))
def set_language(c):
    lang = c.data.split("_")[1]
    set_lang(c.message.chat.id, lang)
    bot.answer_callback_query(c.id, "✅ تم تغيير اللغة.")
    bot.send_message(c.message.chat.id, f"✅ اللغة الحالية: {'العربية' if lang == 'ar' else 'English'}")

def send_news(user_id):
    lang = get_lang(user_id)
    url = "http://feeds.bbci.co.uk/news/world/rss.xml" if lang == "en" else "http://feeds.bbci.co.uk/arabic/rss.xml"
    feed = feedparser.parse(url)
    for entry in feed.entries[:5]:
        title = entry.title
        link = entry.link
        summary = getattr(entry, 'summary', '')
        text = f"<b>{title}</b>\n\n{summary}\n<a href='{link}'>اقرأ المزيد</a>"
        try:
            bot.send_message(user_id, text, parse_mode="HTML")
        except:
            continue

def send_weather(user_id, city):
    try:
        url = f"https://wttr.in/{city}?format=j1"
        data = requests.get(url).json()
        curr = data["current_condition"][0]
        temp = curr["temp_C"]
        desc = curr["weatherDesc"][0]["value"]
        hum = curr["humidity"]
        wind = curr["windspeedKmph"]
        msg = (
            f"📍 الطقس في <b>{city.title()}</b>:\n"
            f"🌡️ الحرارة: <b>{temp}°C</b>\n"
            f"💧 الرطوبة: <b>{hum}%</b>\n"
            f"💨 الرياح: <b>{wind} km/h</b>\n"
            f"🌥️ الحالة: <b>{desc}</b>"
        )
    except:
        msg = "⚠️ تعذر جلب الطقس. تأكد من المدينة."
    bot.send_message(user_id, msg, parse_mode="HTML")

# إرسال تلقائي صباحاً
def daily_broadcast():
    while True:
        now = time.strftime("%H:%M")
        if now == "08:00":
            for user_id in load_users():
                try:
                    bot.send_message(user_id, "☀️ صباح الخير! إليك آخر الأخبار:")
                    send_news(user_id)
                    city = get_city(user_id)
                    if city:
                        send_weather(user_id, city)
                except:
                    continue
            time.sleep(60)
        time.sleep(20)

threading.Thread(target=daily_broadcast, daemon=True).start()

print("✅ البوت يعمل الآن...")
bot.infinity_polling()