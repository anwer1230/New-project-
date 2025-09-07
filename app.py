# telegram_dashboard_full.py
import os, time, uuid
from threading import Lock
from flask import Flask, render_template_string, request, session, jsonify
from flask_socketio import SocketIO, emit
from telethon import TelegramClient, events
from telethon.sessions import StringSession
import eventlet
eventlet.monkey_patch()

# ================== إعدادات ==================
API_ID = int(os.environ.get("API_ID", "22043994"))
API_HASH = os.environ.get("API_HASH", "56f64582b363d367280db96586b97801")
SECRET_KEY = os.environ.get("SESSION_SECRET", os.urandom(24))
SESSION_DIR = "sessions"
if not os.path.exists(SESSION_DIR):
    os.makedirs(SESSION_DIR)

app = Flask(__name__)
app.secret_key = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ================== بيانات المستخدم ==================
USERS = {}
USERS_LOCK = Lock()

# ================== واجهة المستخدم ==================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="UTF-8">
<title>لوحة التحكم - Telegram</title>
<style>
body{font-family:tahoma,sans-serif;background:#f2f2f2;color:#333;margin:0;padding:0;}
header{background:#5C3D99;color:#fff;padding:15px;text-align:center;font-size:24px;}
.container{padding:20px;max-width:900px;margin:auto;}
.card{background:#fff;padding:20px;margin-bottom:20px;border-radius:10px;box-shadow:0 2px 6px rgba(0,0,0,0.2);}
input,textarea,button{width:100%;padding:10px;margin:5px 0;border-radius:5px;border:1px solid #ccc;}
button{background:#5C3D99;color:#fff;border:none;cursor:pointer;}
button:hover{background:#452b7a;}
.status{padding:10px;margin-top:10px;background:#e2e2e2;border-radius:5px;height:150px;overflow:auto;}
.flex{display:flex;gap:10px;}
.flex > *{flex:1;}
</style>
<script src="https://cdn.socket.io/4.6.1/socket.io.min.js"></script>
</head>
<body>
<header>لوحة التحكم - Telegram</header>
<div class="container">

<div class="card">
<h3>تسجيل الدخول</h3>
<input type="text" id="phone" placeholder="+967774523876">
<button onclick="saveLogin()">حفظ & ارسال الكود</button>
<input type="text" id="code" placeholder="كود التحقق">
<button onclick="verifyCode()">تحقق الكود</button>
<div id="login_status" class="status"></div>
</div>

<div class="card">
<h3>إرسال الرسائل</h3>
<textarea id="groups" placeholder="أدخل معرفات القنوات أو المجموعات (سطر لكل مجموعة)"></textarea>
<textarea id="message" placeholder="الرسالة هنا"></textarea>
<div class="flex">
<input type="number" id="interval" placeholder="الفترة بالثواني" value="30">
<button onclick="sendNow()">إرسال الآن</button>
<button onclick="toggleAutoSend()">تفعيل الإرسال التلقائي</button>
</div>
<div id="send_status" class="status"></div>
</div>

<div class="card">
<h3>المراقبة</h3>
<textarea id="watch_words" placeholder="كلمات المراقبة (سطر لكل كلمة)"></textarea>
<button onclick="startMonitoring()">ابدأ المراقبة</button>
<button onclick="stopMonitoring()">أوقف المراقبة</button>
<div id="monitor_status" class="status"></div>
</div>

<div class="card">
<h3>الإحصائيات</h3>
<div id="stats" class="status">الرسائل المرسلة: 0<br>الأخطاء: 0</div>
</div>

</div>
<script>
var socket = io();
var autoSendInterval = null;

socket.on('connect',()=>{socket.emit('join',{user_id:'{{user_id}}'});});
socket.on('log_update',data=>{
    let el=document.getElementById('send_status');
    let m=document.createElement('div'); m.textContent=data.message;
    el.appendChild(m); el.scrollTop=el.scrollHeight;
});
socket.on('monitor_update',data=>{
    let el=document.getElementById('monitor_status');
    let m=document.createElement('div'); m.textContent=data.message;
    el.appendChild(m); el.scrollTop=el.scrollHeight;
});
socket.on('stats_update',data=>{
    document.getElementById('stats').innerHTML='الرسائل المرسلة: '+data.sent+'<br>الأخطاء: '+data.errors;
});

function saveLogin(){
    fetch('/api/save_login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:document.getElementById('phone').value})})
    .then(res=>res.json()).then(data=>{document.getElementById('login_status').textContent=data.message;});
}
function verifyCode(){
    fetch('/api/verify_code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:document.getElementById('code').value})})
    .then(res=>res.json()).then(data=>{document.getElementById('login_status').textContent=data.message;});
}
function sendNow(){
    fetch('/api/send_now',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        groups:document.getElementById('groups').value,
        message:document.getElementById('message').value
    })}).then(res=>res.json()).then(data=>{
        let el=document.getElementById('send_status');
        let m=document.createElement('div'); m.textContent=data.message; el.appendChild(m);
    });
}
function toggleAutoSend(){
    let intervalSec = parseInt(document.getElementById('interval').value)||30;
    if(autoSendInterval){
        clearInterval(autoSendInterval); autoSendInterval=null; alert('تم إيقاف الإرسال التلقائي');
    }else{
        autoSendInterval=setInterval(sendNow,intervalSec*1000); alert('تم تفعيل الإرسال التلقائي');
    }
}
function startMonitoring(){
    fetch('/api/start_monitoring',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        watch_words:document.getElementById('watch_words').value
    })}).then(res=>res.json()).then(data=>{
        document.getElementById('monitor_status').textContent=data.message;
    });
}
function stopMonitoring(){
    fetch('/api/stop_monitoring',{method:'POST'}).then(res=>res.json()).then(data=>{
        document.getElementById('monitor_status').textContent=data.message;
    });
}
</script>
</body>
</html>
"""

# ================== Routes ==================
@app.route("/")
def index():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return render_template_string(HTML_TEMPLATE, user_id=session['user_id'])

# ================== API ==================
@app.route("/api/save_login", methods=["POST"])
def save_login():
    data = request.json
    phone = data.get("phone")
    if not phone: return jsonify({"success":False,"message":"❌ أدخل رقم الهاتف"})
    user_id = session['user_id']
    session_file = os.path.join(SESSION_DIR, f"{user_id}.session")
    with USERS_LOCK:
        USERS[user_id] = {"phone":phone, "session_file":session_file, "client":None, "running":False, "stats":{"sent":0,"errors":0}}
    return jsonify({"success":True,"message":"✅ تم حفظ الرقم (الكود تم إرساله)"})


@app.route("/api/verify_code", methods=["POST"])
def verify_code():
    data = request.json
    code = data.get("code")
    user_id = session['user_id']
    with USERS_LOCK:
        u = USERS.get(user_id)
        if not u: return jsonify({"success":False,"message":"❌ لم يتم حفظ الرقم"})
        client = TelegramClient(u["session_file"], API_ID, API_HASH)
        client.start(phone=u["phone"], code_callback=lambda: code)
        USERS[user_id]["client"] = client
    return jsonify({"success":True,"message":"✅ تم التحقق من الكود وتسجيل الجلسة"})


@app.route("/api/send_now", methods=["POST"])
def send_now():
    data = request.json
    groups = [g.strip() for g in data.get("groups","").split('\n') if g.strip()]
    message = data.get("message","")
    user_id = session['user_id']

    def task():
        u = USERS.get(user_id)
        if not u or not u.get("client"): return
        client = u["client"]
        for g in groups:
            try:
                client.loop.run_until_complete(client.send_message(g, message))
                with USERS_LOCK:
                    u["stats"]["sent"] +=1
                socketio.emit("log_update", {"message":f"✅ أرسلت إلى {g}"}, to=user_id)
            except Exception as e:
                with USERS_LOCK:
                    u["stats"]["errors"] +=1
                socketio.emit("log_update", {"message":f"❌ فشل الإرسال إلى {g}: {str(e)}"}, to=user_id)
        socketio.emit("stats_update", u["stats"], to=user_id)

    socketio.start_background_task(task)
    return jsonify({"success":True,"message":"🚀 تم بدء الإرسال"})

@app.route("/api/start_monitoring", methods=["POST"])
def start_monitoring():
    data = request.json
    watch_words = [w.strip() for w in data.get("watch_words","").split('\n') if w.strip()]
    user_id = session['user_id']

    def monitor_task():
        u = USERS.get(user_id)
        if not u or not u.get("client"): return
        client = u["client"]
        with USERS_LOCK:
            u["running"] = True

        @client.on(events.NewMessage())
        async def handler(event):
            if not u["running"]: return
            text = event.message.message
            for w in watch_words:
                if w in text:
                    alert = f"🔔 تم رصد الكلمة '{w}' من {event.sender_id} في {event.chat_id}: {text}"
                    await client.send_message("me", alert)
                    socketio.emit("monitor_update", {"message":alert}, to=user_id)

        client.run_until_disconnected()

    socketio.start_background_task(monitor_task)
    return jsonify({"success":True,"message":"🚀 بدأت المراقبة"})


@app.route("/api/stop_monitoring", methods=["POST"])
def stop_monitoring():
    user_id = session['user_id']
    u = USERS.get(user_id)
    if u: u["running"] = False
    return jsonify({"success":True,"message":"⏹ تم إيقاف المراقبة"})


# ================== تشغيل النظام ==================
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
