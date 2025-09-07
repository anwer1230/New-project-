# telegram_dashboard.py
import os, json, uuid, asyncio, time
from flask import Flask, render_template_string, session, request, jsonify
from flask_socketio import SocketIO
from telethon import TelegramClient, events
from telethon.sessions import StringSession
import eventlet
eventlet.monkey_patch()

# ===================== إعداد Flask =====================
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(24))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)
USERS = {}

API_ID = 22043994
API_HASH = "56f64582b363d367280db96586b97801"

# ===================== واجهة ويب =====================
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="UTF-8">
<title>لوحة تحكم Telegram</title>
<style>
body { font-family: Arial; background: #f0f2f5; margin:0; padding:0; }
header { background:#4b6cb7; color:white; padding:20px; text-align:center; font-size:22px; }
.container { padding:20px; max-width:900px; margin:auto; }
input, textarea, button { width:100%; padding:10px; margin:5px 0; border-radius:5px; border:1px solid #ccc; }
button { cursor:pointer; background:#4b6cb7; color:white; border:none; }
textarea { resize:none; }
h2 { margin-top:30px; color:#333; }
.log { background:#fff; border:1px solid #ccc; height:200px; overflow:auto; padding:10px; }
.stats { background:#fff; padding:10px; border:1px solid #ccc; margin-top:10px; }
.flex { display:flex; gap:10px; }
.flex > * { flex:1; }
</style>
</head>
<body>
<header>لوحة تحكم Telegram</header>
<div class="container">
<h2>تسجيل الدخول</h2>
<input id="phone" placeholder="+967..." />
<button onclick="saveLogin()">حفظ وإرسال الكود</button>
<div class="flex">
<input id="code" placeholder="كود التحقق" />
<button onclick="verifyCode()">تحقق</button>
</div>

<h2>إرسال الرسائل</h2>
<textarea id="groups" placeholder="أدخل معرفات القنوات أو المجموعات (سطر لكل مجموعة)"></textarea>
<textarea id="message" placeholder="الرسالة هنا"></textarea>
<div class="flex">
<input type="number" id="interval" placeholder="الفترة بالثواني" />
<button onclick="sendNow()">إرسال فوري</button>
<button onclick="startAutoSend()">إرسال تلقائي</button>
<button onclick="stopAutoSend()">إيقاف الإرسال</button>
</div>

<h2>مراقبة الكلمات</h2>
<textarea id="watch_words" placeholder="أدخل كلمات المراقبة (سطر لكل كلمة)"></textarea>
<button onclick="startMonitor()">بدء المراقبة</button>
<button onclick="stopMonitor()">إيقاف المراقبة</button>

<h2>سجل الأحداث</h2>
<div class="log" id="log"></div>

<h2>الإحصائيات</h2>
<div class="stats" id="stats">الرسائل المرسلة: 0<br>الأخطاء: 0</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
let socket = io();
socket.on("log_update", data => {
    let log = document.getElementById("log");
    log.innerHTML += data.message + "<br>";
    log.scrollTop = log.scrollHeight;
});
socket.on("stats_update", data => {
    document.getElementById("stats").innerHTML = "الرسائل المرسلة: "+data.sent+"<br>الأخطاء: "+data.errors;
});

function saveLogin(){
    fetch("/api/save_login", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({phone: document.getElementById("phone").value})})
    .then(res=>res.json()).then(r=>logMessage(r.message));
}
function verifyCode(){
    fetch("/api/verify_code", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({code: document.getElementById("code").value})})
    .then(res=>res.json()).then(r=>logMessage(r.message));
}

function sendNow(){
    fetch("/api/send_now", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({groups:document.getElementById("groups").value,message:document.getElementById("message").value})})
    .then(res=>res.json()).then(r=>logMessage(r.message));
}
function startAutoSend(){
    fetch("/api/start_autosend", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({groups:document.getElementById("groups").value,message:document.getElementById("message").value,interval:document.getElementById("interval").value})})
    .then(res=>res.json()).then(r=>logMessage(r.message));
}
function stopAutoSend(){
    fetch("/api/stop_autosend",{method:"POST"}).then(res=>res.json()).then(r=>logMessage(r.message));
}
function startMonitor(){
    fetch("/api/start_monitor",{method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({watch_words:document.getElementById("watch_words").value})})
    .then(res=>res.json()).then(r=>logMessage(r.message));
}
function stopMonitor(){
    fetch("/api/stop_monitor",{method:"POST"}).then(res=>res.json()).then(r=>logMessage(r.message));
}
function logMessage(msg){ let log = document.getElementById("log"); log.innerHTML += msg+"<br>"; log.scrollTop = log.scrollHeight; }
</script>
</div>
</body>
</html>
"""

# ===================== الوظائف =====================
async def create_client(user_id):
    session_str = USERS[user_id].get("session_str")
    client = TelegramClient(StringSession(session_str) if session_str else None, API_ID, API_HASH)
    await client.connect()
    USERS[user_id]["client"] = client
    return client

async def send_message(user_id, groups, message):
    client = USERS[user_id]["client"]
    stats = USERS[user_id].setdefault("stats", {"sent":0,"errors":0})
    for g in groups:
        try:
            await client.send_message(g, message)
            stats["sent"] += 1
            socketio.emit("log_update", {"message":f"✅ أرسلت إلى {g}"}, to=user_id)
        except Exception as e:
            stats["errors"] += 1
            socketio.emit("log_update", {"message":f"❌ فشل الإرسال إلى {g}: {str(e)}"}, to=user_id)
        socketio.emit("stats_update", stats, to=user_id)

# ===================== Routes API =====================
@app.route("/api/save_login", methods=["POST"])
def api_save_login():
    user_id = session.get("user_id") or str(uuid.uuid4())
    session["user_id"] = user_id
    data = request.json
    phone = data.get("phone")
    if not phone: return jsonify({"success":False,"message":"❌ أدخل رقم الهاتف"})
    USERS[user_id] = {"phone":phone, "session_str":load_session(user_id)}
    client = asyncio.get_event_loop().run_until_complete(create_client(user_id))
    try:
        asyncio.get_event_loop().run_until_complete(client.send_code_request(phone))
        return jsonify({"success":True,"message":"📩 تم إرسال الكود"})
    except Exception as e:
        return jsonify({"success":False,"message":f"❌ خطأ: {str(e)}"})

@app.route("/api/verify_code", methods=["POST"])
def api_verify_code():
    user_id = session.get("user_id")
    data = request.json
    code = data.get("code")
    client = USERS[user_id]["client"]
    try:
        asyncio.get_event_loop().run_until_complete(client.sign_in(USERS[user_id]["phone"], code))
        save_session(user_id, client.session.save())
        USERS[user_id]["session_str"] = client.session.save()
        return jsonify({"success":True,"message":"✅ تم التحقق بنجاح"})
    except SessionPasswordNeededError:
        return jsonify({"success":False,"message":"🔒 مطلوب كلمة مرور (2FA)"})
    except Exception as e:
        return jsonify({"success":False,"message":f"❌ خطأ: {str(e)}"})

@app.route("/api/send_now", methods=["POST"])
def api_send_now():
    user_id = session.get("user_id")
    data = request.json
    groups = [g.strip() for g in data.get("groups","").split("\n") if g.strip()]
    message = data.get("message","")
    asyncio.get_event_loop().create_task(send_message(user_id, groups, message))
    return jsonify({"success":True,"message":"🚀 إرسال جاري..."})

AUTO_TASKS = {}

@app.route("/api/start_autosend", methods=["POST"])
def start_autosend():
    user_id = session.get("user_id")
    data = request.json
    groups = [g.strip() for g in data.get("groups","").split("\n") if g.strip()]
    message = data.get("message","")
    interval = int(data.get("interval",30))
    if user_id in AUTO_TASKS: return jsonify({"success":False,"message":"⏹ الإرسال التلقائي يعمل بالفعل"})
    async def task():
        while True:
            await send_message(user_id, groups, message)
            await asyncio.sleep(interval)
    AUTO_TASKS[user_id] = asyncio.get_event_loop().create_task(task())
    return jsonify({"success":True,"message":"🚀 بدأ الإرسال التلقائي"})

@app.route("/api/stop_autosend", methods=["POST"])
def stop_autosend():
    user_id = session.get("user_id")
    task = AUTO_TASKS.pop(user_id, None)
    if task:
        task.cancel()
        return jsonify({"success":True,"message":"⏹ تم إيقاف الإرسال التلقائي"})
    return jsonify({"success":False,"message":"❌ لم يكن هناك إرسال تلقائي"})

MONITOR_TASKS = {}

@app.route("/api/start_monitor", methods=["POST"])
def start_monitor():
    user_id = session.get("user_id")
    data = request.json
    watch_words = [w.strip() for w in data.get("watch_words","").split("\n") if w.strip()]
    client = USERS[user_id]["client"]
    async def monitor():
        @client.on(events.NewMessage)
        async def handler(event):
            for word in watch_words:
                if word in event.raw_text:
                    await client.send_message(USERS[user_id]["phone"], f"⚠️ {word} اكتشفت في {event.chat_id}\n{event.raw_text}")
                    socketio.emit("log_update", {"message":f"⚠️ كلمة {word} تم اكتشافها"}, to=user_id)
        await client.run_until_disconnected()
    MONITOR_TASKS[user_id] = asyncio.get_event_loop().create_task(monitor())
    return jsonify({"success":True,"message":"🚀 بدأ المراقبة"})

@app.route("/api/stop_monitor", methods=["POST"])
def stop_monitor():
    user_id = session.get("user_id")
    task = MONITOR_TASKS.pop(user_id,None)
    if task:
        task.cancel()
        return jsonify({"success":True,"message":"⏹ تم إيقاف المراقبة"})
    return jsonify({"success":False,"message":"❌ لم تكن هناك مراقبة"})

@app.route("/")
def index():
    if "user_id" not in session:
        session["user_id"] = str(uuid.uuid4())
    return render_template_string(INDEX_HTML)

# ===================== تشغيل التطبيق =====================
if __name__=="__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=True)
