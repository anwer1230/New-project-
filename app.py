# app.py
import os, uuid, json, time, asyncio
from threading import Lock
from flask import Flask, session, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

# ---------- إعدادات ----------
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(24))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

SESSIONS_DIR = "sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

USERS = {}
USERS_LOCK = Lock()

# API_ID و API_HASH مخفيين داخليًا
API_ID = int(os.environ.get("API_ID", 22043994))
API_HASH = os.environ.get("API_HASH", "56f64582b363d367280db96586b97801")

# ---------- HTML + CSS + JS ----------
HTML_PAGE = """ ... نفس واجهة السابقة ... """ # يمكنك نسخ واجهة HTML من الكود السابق بدون تغيير

# ---------- Helpers ----------
def save_settings(user_id, settings):
    path = os.path.join(SESSIONS_DIR, f"{user_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)

def load_settings(user_id):
    path = os.path.join(SESSIONS_DIR, f"{user_id}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

async def send_messages_task(user_id):
    user_data = USERS[user_id]
    settings = user_data['settings']
    interval = settings.get('interval_seconds', 60)
    groups = settings.get('groups', [])
    message = settings.get('message', '')
    session_string = settings.get('session_string')

    async with TelegramClient(StringSession(session_string), API_ID, API_HASH) as client:
        while user_data['is_running']:
            for g in groups:
                try:
                    await client.send_message(g, message)
                    socketio.emit('log_update', {"message": f"✅ أرسلت إلى {g}"}, to=user_id)
                    user_data['stats']['sent'] += 1
                except Exception as e:
                    socketio.emit('log_update', {"message": f"❌ فشل الإرسال إلى {g}: {str(e)}"}, to=user_id)
                    user_data['stats']['errors'] += 1
            await asyncio.sleep(interval)

async def monitor_task(user_id):
    user_data = USERS[user_id]
    settings = user_data['settings']
    watch_words = settings.get('watch_words', [])
    session_string = settings.get('session_string')

    async with TelegramClient(StringSession(session_string), API_ID, API_HASH) as client:
        while user_data['is_running']:
            try:
                async for message in client.iter_messages('me', limit=10):
                    for word in watch_words:
                        if word.lower() in message.message.lower():
                            socketio.emit('log_update', {"message": f"🔍 كلمة '{word}' تم رصدها: {message.text}"}, to=user_id)
                await asyncio.sleep(5)
            except Exception as e:
                socketio.emit('log_update', {"message": f"❌ خطأ بالمراقبة: {str(e)}"}, to=user_id)

# ---------- Routes ----------
@app.route("/")
def index():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return render_template_string(HTML_PAGE)

@app.route("/api/save_login", methods=["POST"])
def api_save_login():
    data = request.json
    phone = data.get('phone')
    if not phone:
        return jsonify({"success": False, "message": "❌ أدخل رقم الهاتف"})
    user_id = session['user_id']
    settings = load_settings(user_id)
    settings.update({'phone': phone})
    save_settings(user_id, settings)
    return jsonify({"success": True, "message": f"✅ تم حفظ الرقم: {phone} (الكود سيتم إرساله لاحقًا)"})

@app.route("/api/save_settings", methods=["POST"])
def api_save_settings():
    data = request.json
    user_id = session['user_id']
    settings = load_settings(user_id)
    settings.update({
        'message': data.get('message', ''),
        'groups': [g.strip() for g in data.get('groups','').split('\n') if g.strip()],
        'interval_seconds': int(data.get('interval_seconds', 60)),
        'watch_words': [w.strip() for w in data.get('watch_words','').split('\n') if w.strip()],
        'send_type': data.get('send_type','manual')
    })
    save_settings(user_id, settings)
    with USERS_LOCK:
        if user_id not in USERS:
            USERS[user_id] = {'settings': settings, 'is_running': False, 'stats': {'sent':0,'errors':0}}
        else:
            USERS[user_id]['settings'] = settings
    return jsonify({"success": True, "message": "✅ تم حفظ الإعدادات"})

@app.route("/api/send_now", methods=["POST"])
def api_send_now():
    user_id = session['user_id']
    with USERS_LOCK:
        if user_id not in USERS or not USERS[user_id]['settings'].get('session_string'):
            return jsonify({"success": False, "message": "❌ لم يتم تسجيل الجلسة بعد"})
        USERS[user_id]['is_running'] = True
    socketio.start_background_task(send_messages_task, user_id)
    return jsonify({"success": True, "message": "🚀 بدأ الإرسال الآن"})

@app.route("/api/start_monitoring", methods=["POST"])
def api_start_monitoring():
    user_id = session['user_id']
    with USERS_LOCK:
        if user_id not in USERS or not USERS[user_id]['settings'].get('session_string'):
            return jsonify({"success": False, "message": "❌ لم يتم تسجيل الجلسة بعد"})
        USERS[user_id]['is_running'] = True
    socketio.start_background_task(monitor_task, user_id)
    return jsonify({"success": True, "message": "🚀 بدأت المراقبة"})

@app.route("/api/stop_monitoring", methods=["POST"])
def api_stop_monitoring():
    user_id = session['user_id']
    with USERS_LOCK:
        if user_id in USERS:
            USERS[user_id]['is_running'] = False
            return jsonify({"success": True, "message": "⏹ تم إيقاف المراقبة"})
    return jsonify({"success": False, "message": "❌ النظام لم يكن يعمل"})

# ---------- Run ----------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
