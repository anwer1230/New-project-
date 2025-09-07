# app.py
import os, time, json, uuid, asyncio, logging
from threading import Lock
from flask import Flask, render_template_string, session, request, jsonify
from flask_socketio import SocketIO, emit
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ===================== إعدادات =====================
API_ID = int(os.environ.get("API_ID", "22043994"))  # ضع قيمتك في env
API_HASH = os.environ.get("API_HASH", "56f64582b363d367280db96586b97801")  # ضع قيمتك في env

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(24))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

USERS = {}  # حفظ جلسات المستخدمين
USERS_LOCK = Lock()

# ===================== واجهة ويب =====================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="UTF-8">
<title>لوحة تحكم Telegram</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<style>
body{font-family:sans-serif;background:#f0f4f8;color:#333;margin:0;padding:0;}
header{background:#5C3D99;color:white;padding:15px;text-align:center;font-size:20px;}
.container{padding:20px;}
input,textarea,button{padding:10px;margin:5px 0;width:100%;border-radius:5px;border:1px solid #ccc;}
button{background:#5C3D99;color:white;border:none;cursor:pointer;}
button:hover{background:#432c70;}
.section{background:white;padding:15px;margin-bottom:20px;border-radius:10px;box-shadow:0 2px 5px rgba(0,0,0,0.1);}
h2{margin-top:0;color:#5C3D99;}
.log{height:150px;overflow:auto;background:#eef;padding:10px;border-radius:5px;}
.flex{display:flex;gap:10px;}
.flex input{flex:1;}
</style>
</head>
<body>
<header>لوحة تحكم Telegram</header>
<div class="container">

<div class="section">
<h2>تسجيل الدخول</h2>
<input id="phone" placeholder="+967774523876">
<button onclick="saveLogin()">حفظ وإرسال الكود</button>
<input id="code" placeholder="كود التحقق">
<button onclick="verifyCode()">تحقق الكود</button>
<div id="login_status"></div>
</div>

<div class="section">
<h2>إرسال الرسائل</h2>
<textarea id="groups" placeholder="أدخل معرفات القنوات أو المجموعات (سطر لكل مجموعة)"></textarea>
<textarea id="message" placeholder="الرسالة هنا"></textarea>
<div class="flex">
<input id="interval" placeholder="الفترة بالثواني" type="number">
<label><input type="checkbox" id="auto_send"> إرسال تلقائي</label>
</div>
<button onclick="sendNow()">إرسال الآن</button>
<button onclick="startAutoSend()">ابدأ الإرسال التلقائي</button>
<button onclick="stopAutoSend()">إيقاف الإرسال التلقائي</button>
</div>

<div class="section">
<h2>المراقبة</h2>
<textarea id="watch_words" placeholder="كلمات المراقبة (سطر لكل كلمة)"></textarea>
<button onclick="startMonitoring()">ابدأ المراقبة</button>
<button onclick="stopMonitoring()">إيقاف المراقبة</button>
</div>

<div class="section">
<h2>سجل الأحداث</h2>
<div id="log" class="log"></div>
</div>

<div class="section">
<h2>الإحصائيات</h2>
<div>الرسائل المرسلة: <span id="sent">0</span></div>
<div>الأخطاء: <span id="errors">0</span></div>
</div>

<script>
var socket = io();
socket.on('log_update', data => {
    let log = document.getElementById('log');
    log.innerHTML += data.message + "<br>";
    log.scrollTop = log.scrollHeight;
});
socket.on('stats_update', data => {
    document.getElementById('sent').innerText = data.sent;
    document.getElementById('errors').innerText = data.errors;
});
socket.on('connection_status', data => {
    document.getElementById('login_status').innerText = data.status=="connected"?"✅ متصل بالخادم":"❌ غير متصل";
});

function saveLogin(){
    fetch("/api/save_login", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({phone: document.getElementById("phone").value})
    }).then(r=>r.json()).then(alert);
}
function verifyCode(){
    fetch("/api/verify_code", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({code: document.getElementById("code").value})
    }).then(r=>r.json()).then(alert);
}
function sendNow(){
    fetch("/api/send_now",{method:"POST"}).then(r=>r.json()).then(alert);
}
function startAutoSend(){
    fetch("/api/start_auto_send",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({interval: parseInt(document.getElementById("interval").value)||60})
    }).then(r=>r.json()).then(alert);
}
function stopAutoSend(){
    fetch("/api/stop_auto_send",{method:"POST"}).then(r=>r.json()).then(alert);
}
function startMonitoring(){
    fetch("/api/start_monitoring",{method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({words: document.getElementById("watch_words").value})}).then(r=>r.json()).then(alert);
}
function stopMonitoring(){
    fetch("/api/stop_monitoring",{method:"POST"}).then(r=>r.json()).then(alert);
}
</script>

</div>
</body>
</html>
"""

# ===================== وظائف أساسية =====================
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

def get_user_client(user_id):
    settings = load_settings(user_id)
    session_string = settings.get('session_string')
    if not session_string:
        return None
    if USERS.get(user_id) and USERS[user_id].get('client'):
        return USERS[user_id]['client']
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    USERS[user_id]['client'] = client
    return client

# ===================== Routes =====================
@app.route("/")
def index():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return render_template_string(HTML_PAGE)

@app.route("/api/save_login", methods=["POST"])
def api_save_login():
    user_id = session['user_id']
    data = request.json
    phone = data.get('phone')
    if not phone:
        return {"success": False, "message": "أدخل الرقم"}
    settings = load_settings(user_id)
    settings['phone'] = phone
    save_settings(user_id, settings)
    return {"success": True, "message": "✅ تم حفظ الرقم (الكود سيُرسل لاحقًا)"}

@app.route("/api/verify_code", methods=["POST"])
def api_verify_code():
    user_id = session['user_id']
    data = request.json
    code = data.get('code')
    if not code:
        return {"success": False, "message":"أدخل الكود"}
    settings = load_settings(user_id)
    phone = settings.get('phone')
    if not phone:
        return {"success": False, "message":"الرقم غير محفوظ"}
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    asyncio.run(client.start(phone=phone, code_callback=lambda: code))
    settings['session_string'] = client.session.save()
    save_settings(user_id, settings)
    USERS[user_id] = {"client": client, "auto_send": False, "monitoring": False, "stats":{"sent":0,"errors":0}}
    return {"success": True, "message":"✅ تم تسجيل الدخول بنجاح"}

@app.route("/api/send_now", methods=["POST"])
def api_send_now():
    user_id = session['user_id']
    client = get_user_client(user_id)
    if not client:
        return {"success": False, "message":"❌ لم يتم تسجيل الجلسة بعد"}
    settings = load_settings(user_id)
    groups = [g.strip() for g in settings.get('groups',[])]
    message = settings.get('message','')
    async def send_task():
        await client.start()
        for g in groups:
            try:
                await client.send_message(g,message)
                USERS[user_id]['stats']['sent'] += 1
                socketio.emit('log_update', {"message":f"✅ أرسلت إلى {g}"}, to=user_id)
            except Exception as e:
                USERS[user_id]['stats']['errors'] += 1
                socketio.emit('log_update', {"message":f"❌ فشل الإرسال إلى {g}: {e}"}, to=user_id)
        socketio.emit('stats_update', USERS[user_id]['stats'], to=user_id)
    asyncio.run(send_task())
    return {"success": True, "message":"✅ تم الإرسال"}

# ===================== المراقبة اللحظية =====================
async def monitor_task(user_id, words):
    client = get_user_client(user_id)
    if not client:
        return
    @client.on(events.NewMessage)
    async def handler(event):
        msg = event.message.message
        chat = await event.get_chat()
        sender = await event.get_sender()
        for w in words:
            if w in msg:
                alert_msg = f"🔔 كلمة مراقبة: {w}\nمن: {sender.id}\nالمجموعة: {chat.title}\nالنص: {msg}"
                await client.send_message('me', alert_msg)
                socketio.emit('log_update', {"message":alert_msg}, to=user_id)
    await client.start()
    await client.run_until_disconnected()

@app.route("/api/start_monitoring", methods=["POST"])
def start_monitoring():
    user_id = session['user_id']
    data = request.json
    words = [w.strip() for w in data.get('words','').split("\n") if w.strip()]
    if not words:
        return {"success": False, "message":"أدخل كلمات المراقبة"}
    if USERS.get(user_id):
        USERS[user_id]['monitoring'] = True
        asyncio.create_task(monitor_task(user_id, words))
        return {"success": True, "message":"🚀 بدأت المراقبة"}
    return {"success": False, "message":"❌ لم يتم تسجيل الجلسة"}

@app.route("/api/stop_monitoring", methods=["POST"])
def stop_monitoring():
    user_id = session['user_id']
    if USERS.get(user_id):
        USERS[user_id]['monitoring'] = False
        return {"success": True, "message":"⏹ تم إيقاف المراقبة"}
    return {"success": False, "message":"❌ لم يتم تسجيل الجلسة"}

# ===================== تشغيل السيرفر =====================
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
