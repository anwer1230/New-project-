# telegram_dashboard.py
import os
import json
import uuid
import time
import asyncio
import threading
from threading import Lock
from flask import Flask, session, request, render_template_string, jsonify
from flask_socketio import SocketIO, emit
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ===== إعداد Flask + SocketIO =====
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(24))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=60, ping_interval=25)

# ===== بيانات التليجرام المخفية =====
API_ID = int(os.environ.get("TG_API_ID", "22043994"))
API_HASH = os.environ.get("TG_API_HASH", "56f64582b363d367280db96586b97801")

# ===== إعداد التخزين لكل مستخدم =====
SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)
USERS = {}
USERS_LOCK = Lock()

# ===== HTML/CSS داخل نفس الملف =====
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="UTF-8">
<title>لوحة تحكم Telegram</title>
<style>
body {font-family: Arial, sans-serif; background:#1e1e2f; color:#eee; margin:0; padding:0;}
.container {max-width:900px; margin:20px auto; padding:20px; background:#2c2c3e; border-radius:10px;}
h1,h2 {text-align:center;}
input,textarea,button {padding:8px; margin:5px 0; width:100%; border-radius:5px; border:none;}
button {background:#5C3D99; color:white; cursor:pointer;}
button:hover {background:#7a55c9;}
textarea {height:80px;}
.log-box {background:#111; padding:10px; height:150px; overflow:auto; border-radius:5px;}
.stat-box {background:#222; padding:10px; margin-top:10px; border-radius:5px;}
.toggle {margin:5px 0; display:flex; justify-content:space-between; align-items:center;}
</style>
</head>
<body>
<div class="container">
<h1>لوحة تحكم Telegram</h1>

<h2>تسجيل الدخول</h2>
<input type="text" id="phone" placeholder="+967xxxxxxxx">
<input type="text" id="code" placeholder="كود التحقق">
<button onclick="saveLogin()">حفظ وإرسال الكود</button>

<h2>إرسال الرسائل</h2>
<textarea id="groups" placeholder="أدخل معرفات القنوات أو المجموعات (سطر لكل مجموعة)"></textarea>
<textarea id="message" placeholder="الرسالة هنا"></textarea>
<div class="toggle">
<label>الارسال التلقائي:</label>
<input type="checkbox" id="auto_send">
</div>
<input type="number" id="interval" placeholder="الفترة بالثواني (مثلاً 30)">
<button onclick="sendNow()">الإرسال الفوري</button>
<button onclick="saveSettings()">حفظ الإعدادات</button>

<h2>المراقبة</h2>
<textarea id="watch_words" placeholder="أدخل كلمات المراقبة (سطر لكل كلمة)"></textarea>
<button onclick="startMonitoring()">بدء المراقبة</button>
<button onclick="stopMonitoring()">إيقاف المراقبة</button>

<h2>سجل الأحداث</h2>
<div class="log-box" id="log"></div>

<h2>الإحصائيات</h2>
<div class="stat-box" id="stats">
الرسائل المرسلة: <span id="sent_count">0</span><br>
الأخطاء: <span id="error_count">0</span>
</div>
</div>

<script src="https://cdn.socket.io/4.6.1/socket.io.min.js"></script>
<script>
var socket = io();
socket.on('connect', () => {logUpdate("✅ متصل بالخادم");});
socket.on('log_update', data => {logUpdate(data.message);});
socket.on('stats_update', data => {document.getElementById("sent_count").innerText = data.sent; document.getElementById("error_count").innerText = data.errors;});

function logUpdate(msg) { var lb = document.getElementById("log"); lb.innerHTML += msg+"<br>"; lb.scrollTop = lb.scrollHeight; }

function saveLogin() {
    fetch("/api/save_login", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({phone: document.getElementById("phone").value})
    }).then(res=>res.json()).then(r=>logUpdate(r.message));
}

function saveSettings() {
    fetch("/api/save_settings", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
            groups: document.getElementById("groups").value,
            message: document.getElementById("message").value,
            auto_send: document.getElementById("auto_send").checked,
            interval_seconds: parseInt(document.getElementById("interval").value)||60,
            watch_words: document.getElementById("watch_words").value
        })
    }).then(res=>res.json()).then(r=>logUpdate(r.message));
}

function sendNow() { fetch("/api/send_now",{method:"POST"}).then(res=>res.json()).then(r=>logUpdate(r.message)); }
function startMonitoring() { fetch("/api/start_monitoring",{method:"POST"}).then(res=>res.json()).then(r=>logUpdate(r.message)); }
function stopMonitoring() { fetch("/api/stop_monitoring",{method:"POST"}).then(res=>res.json()).then(r=>logUpdate(r.message)); }
</script>
</body>
</html>
"""

# ======== وظائف التخزين ========
def save_settings(user_id, settings):
    path = os.path.join(SESSIONS_DIR, f"{user_id}.json")
    with open(path,"w",encoding="utf-8") as f:
        json.dump(settings,f,ensure_ascii=False,indent=4)

def load_settings(user_id):
    path = os.path.join(SESSIONS_DIR, f"{user_id}.json")
    if os.path.exists(path):
        with open(path,"r",encoding="utf-8") as f:
            return json.load(f)
    return {}

# ======== إدارة المستخدمين ========
def get_user_id():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return session['user_id']

# ======== Routes ========
@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/save_login", methods=["POST"])
def api_save_login():
    user_id = get_user_id()
    data = request.json or {}
    phone = data.get("phone")
    if not phone:
        return {"success":False,"message":"❌ أدخل رقم الهاتف"}
    settings = load_settings(user_id)
    settings['phone'] = phone
    save_settings(user_id, settings)
    USERS[user_id] = {"client":None, "settings":settings, "is_running":False, "stats":{"sent":0,"errors":0}}
    return {"success":True,"message":"✅ تم حفظ الرقم (ستصل كود التحقق عند التنفيذ الفعلي)"}

@app.route("/api/save_settings", methods=["POST"])
def api_save_settings():
    user_id = get_user_id()
    data = request.json or {}
    settings = load_settings(user_id)
    settings.update({
        "groups": [g.strip() for g in data.get("groups","").split("\n") if g.strip()],
        "message": data.get("message",""),
        "auto_send": bool(data.get("auto_send",False)),
        "interval_seconds": int(data.get("interval_seconds",60)),
        "watch_words": [w.strip() for w in data.get("watch_words","").split("\n") if w.strip()]
    })
    save_settings(user_id, settings)
    with USERS_LOCK:
        if user_id in USERS:
            USERS[user_id]['settings'] = settings
    return {"success":True,"message":"✅ تم حفظ الإعدادات"}

# ======== إرسال الرسائل ========
def start_sending(user_id):
    settings = USERS[user_id]['settings']
    client = TelegramClient(StringSession(settings.get('session_string','')), API_ID, API_HASH)
    asyncio.run(client.connect())
    try:
        async def send_loop():
            while USERS[user_id]['settings'].get("auto_send",False):
                for g in settings.get("groups",[]):
                    try:
                        await client.send_message(g, settings.get("message",""))
                        USERS[user_id]['stats']['sent'] +=1
                        socketio.emit('stats_update', USERS[user_id]['stats'], to=user_id)
                        socketio.emit('log_update', {"message":f"✅ أرسلت إلى {g}"}, to=user_id)
                    except Exception as e:
                        USERS[user_id]['stats']['errors'] +=1
                        socketio.emit('stats_update', USERS[user_id]['stats'], to=user_id)
                        socketio.emit('log_update', {"message":f"❌ خطأ إرسال إلى {g}: {str(e)}"}, to=user_id)
                await asyncio.sleep(settings.get("interval_seconds",60))
        asyncio.run(send_loop())
    finally:
        asyncio.run(client.disconnect())

@app.route("/api/send_now", methods=["POST"])
def api_send_now():
    user_id = get_user_id()
    threading.Thread(target=start_sending, args=(user_id,)).start()
    return {"success":True,"message":"🚀 بدأ الإرسال الفوري"}

# ======== مراقبة الرسائل ========
def start_monitoring_task(user_id):
    settings = USERS[user_id]['settings']
    client = TelegramClient(StringSession(settings.get('session_string','')), API_ID, API_HASH)
    asyncio.run(client.connect())
    async def monitor():
        @client.on(events.NewMessage)
        async def handler(event):
            text = event.raw_text
            sender = await event.get_sender()
            for word in settings.get("watch_words",[]):
                if word in text:
                    # أرسل تنبيه إلى محادثة خاصة مع نفسك
                    try:
                        await client.send_message(settings.get("phone"), f"🚨 الكلمة: {word}\nمن: {sender.id}\nالنص: {text}")
                        socketio.emit('log_update', {"message":f"🚨 كلمة مراقبة '{word}' من {sender.id}'"}, to=user_id)
                    except: pass
        await client.run_until_disconnected()
    threading.Thread(target=lambda: asyncio.run(monitor())).start()

@app.route("/api/start_monitoring", methods=["POST"])
def api_start_monitoring():
    user_id = get_user_id()
    start_monitoring_task(user_id)
    return {"success":True,"message":"🚀 بدأت المراقبة"}

@app.route("/api/stop_monitoring", methods=["POST"])
def api_stop_monitoring():
    user_id = get_user_id()
    with USERS_LOCK:
        if user_id in USERS:
            USERS[user_id]['is_running'] = False
    return {"success":True,"message":"⏹ تم إيقاف المراقبة"}

# ======== تشغيل التطبيق ========
if __name__=="__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
