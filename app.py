# app.py
import os, json, uuid, time, asyncio
from threading import Lock
from flask import Flask, session, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# --------- إعدادات Flask & SocketIO ---------
app = Flask(__name__)
app.secret_key = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=60, ping_interval=25)

# --------- إعدادات Telegram ---------
API_ID = 22043994      # استخدم نفس الـID لكل المستخدمين
API_HASH = "56f64582b363d367280db96586b97801"

# --------- إدارة الجلسات ---------
SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)
USERS = {}
USERS_LOCK = Lock()

# --------- واجهة HTML باستخدام Bootstrap ---------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>لوحة تحكم Telegram</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.1/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.socket.io/4.6.1/socket.io.min.js"></script>
<style>
body {background:#f8f9fa; direction: rtl;}
.card {margin-top:20px;}
textarea {resize:none;}
#log {height:200px; overflow:auto; background:#e9ecef; padding:10px;}
</style>
</head>
<body>
<div class="container">
  <h2 class="mt-3">لوحة تحكم Telegram</h2>
  <div class="card p-3">
    <h5>تسجيل الدخول</h5>
    <input type="text" id="phone" class="form-control mb-2" placeholder="رقم الهاتف +967...">
    <button class="btn btn-primary mb-2" onclick="sendCode()">إرسال كود التحقق</button>
    <input type="text" id="code" class="form-control mb-2" placeholder="كود التحقق">
    <button class="btn btn-success mb-2" onclick="verifyCode()">تحقق من الكود</button>
    <div id="login_status"></div>
  </div>

  <div class="card p-3">
    <h5>إرسال الرسائل</h5>
    <textarea id="groups" class="form-control mb-2" placeholder="أدخل معرفات القنوات أو المجموعات (سطر لكل مجموعة)"></textarea>
    <textarea id="message" class="form-control mb-2" placeholder="الرسالة هنا"></textarea>
    <input type="number" id="interval" class="form-control mb-2" placeholder="المدة بين الرسائل بالثواني">
    <div class="form-check mb-2">
      <input type="checkbox" class="form-check-input" id="auto_send">
      <label class="form-check-label">إرسال تلقائي</label>
    </div>
    <button class="btn btn-primary mb-2" onclick="sendNow()">إرسال فوري</button>
    <button class="btn btn-success mb-2" onclick="startAutoSend()">بدء الإرسال التلقائي</button>
    <button class="btn btn-danger mb-2" onclick="stopAutoSend()">إيقاف الإرسال</button>
  </div>

  <div class="card p-3">
    <h5>المراقبة</h5>
    <textarea id="watch_words" class="form-control mb-2" placeholder="كلمات المراقبة (سطر لكل كلمة)"></textarea>
    <button class="btn btn-primary mb-2" onclick="startMonitoring()">بدء المراقبة</button>
    <button class="btn btn-danger mb-2" onclick="stopMonitoring()">إيقاف المراقبة</button>
  </div>

  <div class="card p-3">
    <h5>سجل الأحداث</h5>
    <div id="log"></div>
  </div>

  <div class="card p-3">
    <h5>الإحصائيات</h5>
    <p>الرسائل المرسلة: <span id="sent_count">0</span></p>
    <p>الأخطاء: <span id="error_count">0</span></p>
  </div>
</div>

<script>
var socket = io();
socket.on('log_update', function(data){ 
    var log = document.getElementById('log');
    log.innerHTML += data.message + "<br>"; 
    log.scrollTop = log.scrollHeight;
});
socket.on('stats_update', function(data){
    document.getElementById('sent_count').innerText = data.sent;
    document.getElementById('error_count').innerText = data.errors;
});

function sendCode(){
    fetch("/api/save_login", {
        method:"POST",
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({phone:document.getElementById('phone').value})
    }).then(r=>r.json()).then(d=>alert(d.message));
}

function verifyCode(){
    fetch("/api/verify_code", {
        method:"POST",
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({code:document.getElementById('code').value})
    }).then(r=>r.json()).then(d=>alert(d.message));
}

function sendNow(){
    fetch("/api/send_now",{method:"POST"}).then(r=>r.json()).then(d=>alert(d.message));
}

function startAutoSend(){
    fetch("/api/start_auto_send",{method:"POST"}).then(r=>r.json()).then(d=>alert(d.message));
}

function stopAutoSend(){
    fetch("/api/stop_auto_send",{method:"POST"}).then(r=>r.json()).then(d=>alert(d.message));
}

function startMonitoring(){
    fetch("/api/start_monitoring",{method:"POST"}).then(r=>r.json()).then(d=>alert(d.message));
}

function stopMonitoring(){
    fetch("/api/stop_monitoring",{method:"POST"}).then(r=>r.json()).then(d=>alert(d.message));
}
</script>
</body>
</html>
"""

# --------- وظائف المساعدة ---------
def save_user_settings(user_id, settings):
    path = os.path.join(SESSIONS_DIR, f"{user_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)

def load_user_settings(user_id):
    path = os.path.join(SESSIONS_DIR, f"{user_id}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# --------- Routes ---------
@app.route("/")
def index():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return render_template_string(HTML_TEMPLATE)

# --------- API Routes ---------
@app.route("/api/save_login", methods=["POST"])
def api_save_login():
    data = request.json or {}
    phone = data.get('phone')
    if not phone:
        return jsonify({"success":False,"message":"أدخل رقم الهاتف"})
    user_id = session['user_id']
    settings = load_user_settings(user_id)
    settings['phone'] = phone
    save_user_settings(user_id, settings)
    # إنشاء عميل Telegram مؤقت لإرسال الكود
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    async def send_code():
        await client.connect()
        try:
            await client.send_code_request(phone)
            return {"success":True,"message":"تم إرسال الكود"}
        except Exception as e:
            return {"success":False,"message":str(e)}
        finally:
            await client.disconnect()
    result = asyncio.run(send_code())
    return jsonify(result)

@app.route("/api/verify_code", methods=["POST"])
def api_verify_code():
    data = request.json or {}
    code = data.get('code')
    if not code:
        return jsonify({"success":False,"message":"أدخل كود التحقق"})
    user_id = session['user_id']
    settings = load_user_settings(user_id)
    phone = settings.get('phone')
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    async def sign_in():
        await client.connect()
        try:
            result = await client.sign_in(phone, code)
            session_string = client.session.save()
            settings['session_string'] = session_string
            settings.setdefault('stats', {"sent":0,"errors":0})
            save_user_settings(user_id, settings)
            USERS[user_id] = {'client': client, 'settings':settings, 'auto_send':False, 'monitoring':False}
            return {"success":True,"message":"تم التحقق من الكود"}
        except Exception as e:
            return {"success":False,"message":str(e)}
    result = asyncio.run(sign_in())
    return jsonify(result)

# --------- بدء التطبيق ---------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
