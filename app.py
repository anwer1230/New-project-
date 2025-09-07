# app.py
import os, json, uuid, time, asyncio, logging
from threading import Lock
from flask import Flask, session, request, jsonify, render_template_string
from flask_socketio import SocketIO, emit
from telethon import TelegramClient
from telethon.sessions import StringSession

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask + SocketIO
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(24))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=60, ping_interval=25)

# بيانات الجلسات
SESSIONS_DIR = "sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

USERS = {}
USERS_LOCK = Lock()

# ---------- Helper: حفظ وتحميل الإعدادات ----------
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

# ---------- HTML الواجهة ----------
HTML_PAGE = """
<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>لوحة التحكم - Telegram</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
<style>
body { background-color: #f8f9fa; font-family: Arial, sans-serif; }
.card { margin-top: 20px; }
textarea { resize: none; }
.log { height: 200px; overflow-y: scroll; background: #212529; color: #fff; padding: 10px; border-radius: 5px; }
</style>
</head>
<body>
<div class="container">
<h2 class="mt-4 text-center">لوحة التحكم - Telegram</h2>

<div class="row">
  <div class="col-md-6">
    <div class="card p-3">
      <h5>تسجيل الدخول</h5>
      <input id="phone" class="form-control mb-2" placeholder="رقم الهاتف">
      <button class="btn btn-primary mb-2" onclick="sendCode()">إرسال الكود</button>
      <input id="code" class="form-control mb-2" placeholder="كود التحقق">
      <button class="btn btn-success" onclick="verifyCode()">تأكيد الكود</button>
    </div>

    <div class="card p-3 mt-3">
      <h5>إرسال الرسائل</h5>
      <textarea id="groups" class="form-control mb-2" placeholder="أدخل معرفات القنوات أو المجموعات (سطر لكل مجموعة)" rows="3"></textarea>
      <textarea id="message" class="form-control mb-2" placeholder="الرسالة هنا" rows="3"></textarea>
      <button class="btn btn-warning" onclick="sendNow()">إرسال فوري</button>
    </div>

    <div class="card p-3 mt-3">
      <h5>المراقبة</h5>
      <button class="btn btn-info mb-2" onclick="startMonitoring()">بدء المراقبة</button>
      <button class="btn btn-secondary" onclick="stopMonitoring()">إيقاف المراقبة</button>
    </div>
  </div>

  <div class="col-md-6">
    <div class="card p-3">
      <h5>سجل الأحداث</h5>
      <div class="log" id="log"></div>
    </div>
    <div class="card p-3 mt-3">
      <h5>الإحصائيات</h5>
      <p>الرسائل المرسلة: <span id="sent">0</span></p>
      <p>الأخطاء: <span id="errors">0</span></p>
    </div>
  </div>
</div>
</div>

<script>
var socket = io();
socket.on('connect', () => { appendLog("✅ متصل بالخادم"); });
socket.on('log_update', data => { appendLog(data.message); });
socket.on('stats_update', data => {
    document.getElementById('sent').innerText = data.sent;
    document.getElementById('errors').innerText = data.errors;
});

function appendLog(msg){
    var logDiv = document.getElementById('log');
    logDiv.innerHTML += msg + "<br>";
    logDiv.scrollTop = logDiv.scrollHeight;
}

function sendCode(){
    fetch('/api/save_login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({phone: document.getElementById('phone').value})})
    .then(res=>res.json()).then(r=>appendLog(r.message));
}

function verifyCode(){
    fetch('/api/verify_code', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code: document.getElementById('code').value})})
    .then(res=>res.json()).then(r=>appendLog(r.message));
}

function sendNow(){
    fetch('/api/send_now', {method:'POST'}).then(res=>res.json()).then(r=>appendLog(r.message));
}

function startMonitoring(){
    fetch('/api/start_monitoring', {method:'POST'}).then(res=>res.json()).then(r=>appendLog(r.message));
}

function stopMonitoring(){
    fetch('/api/stop_monitoring', {method:'POST'}).then(res=>res.json()).then(r=>appendLog(r.message));
}
</script>
</body>
</html>
"""

# ---------- Routes ----------
@app.route("/")
def index():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return render_template_string(HTML_PAGE)

# ---------- Login & verification ----------
@app.route("/api/save_login", methods=["POST"])
def api_save_login():
    user_id = session['user_id']
    data = request.json
    phone = data.get('phone')
    if not phone:
        return {"success": False, "message": "❌ أدخل رقم الهاتف"}
    settings = {"phone": phone, "stats": {"sent":0, "errors":0}}
    save_settings(user_id, settings)
    USERS[user_id] = {"settings": settings, "is_running": False}
    return {"success": True, "message": f"✅ تم حفظ الرقم: {phone} (الكود يفترض أنه تم إرساله) "}

@app.route("/api/verify_code", methods=["POST"])
def api_verify_code():
    user_id = session['user_id']
    data = request.json
    code = data.get('code')
    if code == "1234":  # مجرد محاكاة
        USERS[user_id]['settings']['session_string'] = "dummy_session"
        return {"success": True, "message": "✅ تم التحقق من الكود"}
    return {"success": False, "message": "❌ كود غير صحيح"}

# ---------- Send message ----------
@app.route("/api/send_now", methods=["POST"])
def api_send_now():
    user_id = session['user_id']
    user = USERS.get(user_id)
    if not user or 'session_string' not in user['settings']:
        return {"success": False, "message": "❌ لم يتم تسجيل الجلسة بعد"}
    groups = request.json.get('groups', []) if request.json else []
    message = request.json.get('message','') if request.json else ''
    # محاكاة إرسال
    user['settings']['stats']['sent'] += 1
    socketio.emit('stats_update', user['settings']['stats'], to=user_id)
    return {"success": True, "message": "✅ تم الإرسال (محاكاة)"}

# ---------- Monitoring ----------
def monitoring_task(user_id):
    import time
    while USERS[user_id]['is_running']:
        # كل 5 ثواني نرسل رسالة محاكاة
        USERS[user_id]['settings']['stats']['sent'] += 1
        socketio.emit('log_update', {"message": f"🚀 تم إرسال رسالة مراقبة"}, to=user_id)
        socketio.emit('stats_update', USERS[user_id]['settings']['stats'], to=user_id)
        time.sleep(5)

@app.route("/api/start_monitoring", methods=["POST"])
def api_start_monitoring():
    user_id = session['user_id']
    USERS[user_id]['is_running'] = True
    socketio.start_background_task(monitoring_task, user_id)
    return {"success": True, "message": "🚀 بدأت المراقبة"}

@app.route("/api/stop_monitoring", methods=["POST"])
def api_stop_monitoring():
    user_id = session['user_id']
    USERS[user_id]['is_running'] = False
    return {"success": True, "message": "⏹ تم إيقاف المراقبة"}

# ---------- Startup ----------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
