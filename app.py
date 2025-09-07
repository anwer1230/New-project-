import os, time, uuid, sqlite3, asyncio, threading
from flask import Flask, render_template_string, request, session, jsonify
from flask_socketio import SocketIO, emit
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ---------- إعداد Flask + SocketIO ----------
app = Flask(__name__)
app.secret_key = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# ---------- إعداد قاعدة البيانات SQLite ----------
DB_FILE = 'users.db'
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, phone TEXT, session_string TEXT, message TEXT, groups TEXT, interval_sec INTEGER, watch_words TEXT, stats_sent INTEGER, stats_errors INTEGER)''')
conn.commit()

# ---------- القوالب HTML ----------
HTML_PAGE = '''
<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="UTF-8">
<title>لوحة تحكم Telegram</title>
<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<style>
body { font-family: Tahoma, sans-serif; background:#f4f4f9; color:#333; padding:20px; }
h1 { color:#5C3D99; }
input, textarea { width:100%; padding:8px; margin:5px 0; border-radius:5px; border:1px solid #ccc; }
button { padding:10px 20px; background:#5C3D99; color:white; border:none; border-radius:5px; cursor:pointer; margin:5px 0; }
button:hover { background:#7A57D1; }
.log { background:#fff; padding:10px; border-radius:5px; max-height:200px; overflow-y:auto; margin-top:10px; border:1px solid #ccc; }
</style>
</head>
<body>
<h1>لوحة تحكم Telegram</h1>
<h2>تسجيل الدخول</h2>
<input id="phone" placeholder="أدخل رقم الهاتف +967...">
<button onclick="savePhone()">حفظ وارسال الكود</button>
<input id="code" placeholder="كود التحقق">
<button onclick="verifyCode()">تحقق من الكود</button>
<h2>إرسال الرسائل</h2>
<textarea id="groups" placeholder="أدخل معرفات القنوات أو المجموعات (سطر لكل مجموعة)"></textarea>
<textarea id="message" placeholder="الرسالة هنا"></textarea>
<input type="number" id="interval" placeholder="وقت الإرسال بالثواني">
<label><input type="checkbox" id="auto_send"> ارسال تلقائي</label>
<button onclick="sendNow()">إرسال الآن</button>
<h2>المراقبة</h2>
<textarea id="watch_words" placeholder="كلمات المراقبة (سطر لكل كلمة)"></textarea>
<button onclick="startMonitoring()">تشغيل المراقبة</button>
<button onclick="stopMonitoring()">إيقاف المراقبة</button>
<h2>سجل الأحداث</h2>
<div class="log" id="log"></div>
<h2>الإحصائيات</h2>
<div id="stats">الرسائل المرسلة: 0<br>الأخطاء: 0</div>
<script>
var socket = io();
socket.on('log_update', data => { let log = document.getElementById('log'); log.innerHTML += data.message+'<br>'; log.scrollTop = log.scrollHeight; });
socket.on('stats_update', data => { document.getElementById('stats').innerHTML = `الرسائل المرسلة: ${data.sent}<br>الأخطاء: ${data.errors}`; });
function savePhone(){ fetch('/api/save_login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:document.getElementById('phone').value})}).then(r=>r.json()).then(j=>alert(j.message));}
function verifyCode(){ fetch('/api/verify_code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:document.getElementById('code').value})}).then(r=>r.json()).then(j=>alert(j.message));}
function sendNow(){ fetch('/api/send_now',{method:'POST'}).then(r=>r.json()).then(j=>alert(j.message));}
function startMonitoring(){ fetch('/api/start_monitoring',{method:'POST'}).then(r=>r.json()).then(j=>alert(j.message));}
function stopMonitoring(){ fetch('/api/stop_monitoring',{method:'POST'}).then(r=>r.json()).then(j=>alert(j.message));}
</script>
</body>
</html>
'''

# ---------- بيانات المستخدم في الذاكرة ----------
USERS = {}
USERS_LOCK = threading.Lock()

# ---------- واجهة الصفحة الرئيسية ----------
@app.route('/')
def index():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return render_template_string(HTML_PAGE)

# ---------- وظائف Telegram ----------
async def send_message_task(user_id):
    with USERS_LOCK:
        user = USERS[user_id]
    client = user['client']
    settings = user['settings']
    groups = settings['groups'].split('\n')
    msg = settings['message']
    interval = settings['interval_sec']
    while user['running'] and settings['auto_send']:
        for g in groups:
            try:
                await client.send_message(g, msg)
                settings['stats_sent'] += 1
                socketio.emit('log_update', {'message': f'✅ أرسلت إلى {g}'}, to=user_id)
            except Exception as e:
                settings['stats_errors'] += 1
                socketio.emit('log_update', {'message': f'❌ فشل الإرسال إلى {g}: {str(e)}'}, to=user_id)
            socketio.emit('stats_update', {'sent': settings['stats_sent'], 'errors': settings['stats_errors']}, to=user_id)
        await asyncio.sleep(interval)

async def monitor_task(user_id):
    with USERS_LOCK:
        user = USERS[user_id]
    client = user['client']
    words = [w.lower() for w in user['settings']['watch_words'].split('\n') if w.strip()]
    @client.on(events.NewMessage)
    async def handler(event):
        text = event.raw_text.lower()
        chat = await event.get_chat()
        sender = await event.get_sender()
        for w in words:
            if w in text:
                msg = f'🔔 كلمة مراقبة: {w}\nمرسل: {sender.id}\nالمجموعة: {chat.title}\nالنص: {event.raw_text}'
                socketio.emit('log_update', {'message': msg}, to=user_id)
                await client.send_message(user['settings']['phone'], msg)
    await client.run_until_disconnected()

# ---------- API Endpoints ----------
@app.route('/api/save_login', methods=['POST'])
def api_save_login():
    user_id = session['user_id']
    data = request.json
    phone = data.get('phone')
    if not phone:
        return jsonify({'success': False, 'message':'أدخل الرقم'})
    session['phone'] = phone
    # إنشاء أو تحميل client
    client = TelegramClient(StringSession(), 22043994, '56f64582b363d367280db96586b97801')
    USERS[user_id] = {'client': client, 'settings': {'phone':phone,'message':'','groups':'','interval_sec':60,'watch_words':'','stats_sent':0,'stats_errors':0,'auto_send':False}, 'running': False}
    return jsonify({'success': True, 'message':'تم حفظ الرقم'})

@app.route('/api/verify_code', methods=['POST'])
def api_verify_code():
    user_id = session['user_id']
    code = request.json.get('code')
    user = USERS[user_id]
    client = user['client']
    async def verify():
        await client.connect()
        await client.sign_in(phone=user['settings']['phone'], code=code)
        user['running'] = True
    threading.Thread(target=lambda: asyncio.run(verify())).start()
    return jsonify({'success': True, 'message':'تم التحقق'})

@app.route('/api/send_now', methods=['POST'])
def api_send_now():
    user_id = session['user_id']
    user = USERS[user_id]
    asyncio.run(send_message_task(user_id))
    return jsonify({'success': True, 'message':'تم الإرسال'})

@app.route('/api/start_monitoring', methods=['POST'])
def api_start_monitoring():
    user_id = session['user_id']
    user = USERS[user_id]
    user['running'] = True
    threading.Thread(target=lambda: asyncio.run(monitor_task(user_id))).start()
    return jsonify({'success': True, 'message':'بدأت المراقبة'})

@app.route('/api/stop_monitoring', methods=['POST'])
def api_stop_monitoring():
    user_id = session['user_id']
    USERS[user_id]['running'] = False
    return jsonify({'success': True, 'message':'توقفت المراقبة'})

# ---------- تشغيل السيرفر ----------
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
