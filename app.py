# telegram_control.py
import os, json, uuid, time, asyncio, threading
from flask import Flask, session, request, render_template_string, jsonify
from flask_socketio import SocketIO, emit, join_room
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ===== إعداد التطبيق =====
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(24))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

SESSIONS_DIR = "sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

# ===== API ID و HASH ثابت للمستخدمين =====
API_ID = 22043994
API_HASH = "56f64582b363d367280db96586b97801"

# ===== تخزين بيانات المستخدمين =====
USERS = {}

# ===== حفظ/تحميل الجلسة =====
def save_session(user_id, data):
    with open(os.path.join(SESSIONS_DIR, f"{user_id}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_session(user_id):
    path = os.path.join(SESSIONS_DIR, f"{user_id}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# ===== واجهة الويب (HTML داخل الكود) =====
HTML_PAGE = """
<!doctype html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>لوحة تحكم Telegram</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body { background-color:#f2f2f2; }
.container { max-width: 900px; margin-top:30px; }
.card { margin-bottom:20px; }
textarea { resize:none; }
</style>
</head>
<body>
<div class="container">
<h2 class="text-center mb-4">لوحة تحكم Telegram</h2>

<div class="card p-3">
<h4>تسجيل الدخول</h4>
<input type="text" id="phone" class="form-control mb-2" placeholder="+967xxxxxxxxx">
<button id="send_code" class="btn btn-primary mb-2">إرسال الكود</button>
<input type="text" id="code" class="form-control mb-2" placeholder="أدخل كود التحقق">
<button id="verify_code" class="btn btn-success">تحقق</button>
<div id="login_status" class="mt-2 text-success"></div>
</div>

<div class="card p-3">
<h4>إرسال الرسائل</h4>
<textarea id="groups" class="form-control mb-2" placeholder="أدخل معرفات القنوات/المجموعات (سطر لكل مجموعة)"></textarea>
<textarea id="message" class="form-control mb-2" placeholder="الرسالة هنا"></textarea>
<div class="input-group mb-2">
<input type="number" id="interval" class="form-control" placeholder="الفترة بالثواني">
<button class="btn btn-warning" id="auto_send">إرسال تلقائي</button>
<button class="btn btn-primary" id="send_now">إرسال فوري</button>
</div>
</div>

<div class="card p-3">
<h4>المراقبة</h4>
<textarea id="watch_words" class="form-control mb-2" placeholder="أدخل كلمات المراقبة (سطر لكل كلمة)"></textarea>
<button id="start_monitor" class="btn btn-success">تشغيل المراقبة</button>
<button id="stop_monitor" class="btn btn-danger">إيقاف المراقبة</button>
</div>

<div class="card p-3">
<h4>سجل الأحداث</h4>
<div id="log" style="height:200px; overflow-y:auto; background:#fff; padding:5px;"></div>
</div>

<div class="card p-3">
<h4>الإحصائيات</h4>
<p>الرسائل المرسلة: <span id="sent">0</span></p>
<p>الأخطاء: <span id="errors">0</span></p>
</div>
</div>

<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script>
const socket = io();
let auto_interval = null;

function log(msg) {
    let logDiv = document.getElementById('log');
    logDiv.innerHTML += msg + "<br>";
    logDiv.scrollTop = logDiv.scrollHeight;
}

document.getElementById('send_code').onclick = async () => {
    let phone = document.getElementById('phone').value;
    let res = await fetch("/api/send_code", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({phone})
    });
    let data = await res.json();
    log(data.message);
};

document.getElementById('verify_code').onclick = async () => {
    let code = document.getElementById('code').value;
    let res = await fetch("/api/verify_code", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({code})
    });
    let data = await res.json();
    log(data.message);
};

document.getElementById('send_now').onclick = async () => {
    let message = document.getElementById('message').value;
    let groups = document.getElementById('groups').value.split("\\n").filter(x=>x);
    let res = await fetch("/api/send_now", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({message, groups})
    });
    let data = await res.json();
    log(data.message);
};

document.getElementById('auto_send').onclick = () => {
    if(auto_interval){ clearInterval(auto_interval); auto_interval=null; log("⏹ أوقف الإرسال التلقائي"); return; }
    let interval = parseInt(document.getElementById('interval').value) || 60;
    auto_interval = setInterval(async ()=>{
        let message = document.getElementById('message').value;
        let groups = document.getElementById('groups').value.split("\\n").filter(x=>x);
        let res = await fetch("/api/send_now", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body: JSON.stringify({message, groups})
        });
        let data = await res.json();
        log(data.message);
    }, interval*1000);
    log("🚀 بدأ الإرسال التلقائي");
};

document.getElementById('start_monitor').onclick = async () => {
    let words = document.getElementById('watch_words').value.split("\\n").filter(x=>x);
    let res = await fetch("/api/start_monitoring", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({words})
    });
    let data = await res.json();
    log(data.message);
};

document.getElementById('stop_monitor').onclick = async () => {
    let res = await fetch("/api/stop_monitoring", {method:"POST"});
    let data = await res.json();
    log(data.message);
};

socket.on('log_update', data=>log(data.message));
socket.on('stats_update', data=>{
    document.getElementById('sent').innerText = data.sent;
    document.getElementById('errors').innerText = data.errors;
});
</script>
</body>
</html>
"""

# ===== الصفحة الرئيسية =====
@app.route("/")
def index():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return render_template_string(HTML_PAGE)

# ===== API إرسال الكود =====
@app.route("/api/send_code", methods=["POST"])
def send_code():
    user_id = session['user_id']
    data = request.json
    phone = data.get('phone')
    if not phone: return jsonify({"message":"❌ أدخل رقم الهاتف"})
    USERS[user_id] = load_session(user_id)
    USERS[user_id]['phone'] = phone
    USERS[user_id]['session_string'] = None
    save_session(user_id, USERS[user_id])
    return jsonify({"message":"✅ تم حفظ الرقم. أدخل الكود بعد استلامه"})

# ===== API تحقق الكود =====
@app.route("/api/verify_code", methods=["POST"])
def verify_code():
    user_id = session['user_id']
    code = request.json.get('code')
    if not code: return jsonify({"message":"❌ أدخل كود التحقق"})
    USERS[user_id] = load_session(user_id)
    phone = USERS[user_id]['phone']
    # إنشاء العميل وحفظ session_string
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    async def auth():
        await client.connect()
        try:
            await client.sign_in(phone, code)
            USERS[user_id]['session_string'] = client.session.save()
            save_session(user_id, USERS[user_id])
            socketio.emit('log_update', {"message":"✅ تم التحقق وحفظ الجلسة"}, to=user_id)
        except Exception as e:
            socketio.emit('log_update', {"message":f"❌ خطأ التحقق: {str(e)}"}, to=user_id)
        await client.disconnect()
    asyncio.run(auth())
    return jsonify({"message":"✅ العملية تمت. تحقق من سجل الأحداث"})

# ===== API إرسال الرسائل =====
@app.route("/api/send_now", methods=["POST"])
def send_now():
    user_id = session['user_id']
    data = request.json
    groups = data.get('groups', [])
    message = data.get('message', '')
    USERS[user_id] = load_session(user_id)
    session_string = USERS[user_id].get('session_string')
    if not session_string: return jsonify({"message":"❌ لم يتم تسجيل الجلسة"})
    async def send_task():
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await client.start()
        sent = 0; errors=0
        for g in groups:
            try:
                await client.send_message(g, message)
                sent += 1
                socketio.emit('log_update', {"message":f"✅ أرسلت إلى {g}"}, to=user_id)
            except Exception as e:
                errors += 1
                socketio.emit('log_update', {"message":f"❌ فشل الإرسال {g}: {str(e)}"}, to=user_id)
        socketio.emit('stats_update', {"sent":sent, "errors":errors}, to=user_id)
        await client.disconnect()
    threading.Thread(target=lambda: asyncio.run(send_task())).start()
    return jsonify({"message":"✅ الإرسال جارٍ..."})

# ===== المراقبة =====
MONITOR_THREADS = {}
@app.route("/api/start_monitoring", methods=["POST"])
def start_monitor():
    user_id = session['user_id']
    words = request.json.get('words', [])
    USERS[user_id] = load_session(user_id)
    session_string = USERS[user_id].get('session_string')
    if not session_string: return jsonify({"message":"❌ لم يتم تسجيل الجلسة"})
    def monitor_task():
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        async def run_monitor():
            await client.start()
            @client.on(events.NewMessage)
            async def handler(event):
                msg_text = event.message.message
                for w in words:
                    if w in msg_text:
                        sender = await event.get_sender()
                        chat = await event.get_chat()
                        alert = f"🔔 كلمة '{w}' من {sender.id} في {chat.title if hasattr(chat,'title') else chat.id}: {msg_text}"
                        socketio.emit('log_update', {"message":alert}, to=user_id)
                        await client.send_message(USERS[user_id]['phone'], alert)
            await client.run_until_disconnected()
        asyncio.run(run_monitor())
    t = threading.Thread(target=monitor_task, daemon=True)
    t.start()
    MONITOR_THREADS[user_id] = t
    return jsonify({"message":"🚀 بدأت المراقبة"})

@app.route("/api/stop_monitoring", methods=["POST"])
def stop_monitor():
    user_id = session['user_id']
    thread = MONITOR_THREADS.get(user_id)
    if thread and thread.is_alive():
        # لا يمكن إنهاء threads في بايثون مباشرة، يجب إعادة تشغيل النظام لاحقًا
        return jsonify({"message":"✅ لإيقاف المراقبة أغلق التطبيق حالياً"})
    return jsonify({"message":"⏹ المراقبة متوقفة"})

# ===== التشغيل =====
if __name__=="__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
