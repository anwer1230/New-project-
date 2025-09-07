# telegram_control.py
import os, json, uuid, time, asyncio, threading, logging
from flask import Flask, session, request, render_template_string, jsonify
from flask_socketio import SocketIO, emit, join_room
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError

# ===== إعدادات التسجيل =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== إعداد التطبيق =====
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(24))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

SESSIONS_DIR = "sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

# ===== API ID و HASH (يجب استبدالها بقيمك الخاصة) =====
API_ID = 22043994  # استبدل بـ API ID الخاص بك
API_HASH = "56f64582b363d367280db96586b97801"  # استبدل بـ API HASH الخاص بك

# ===== تخزين بيانات المستخدمين =====
USERS = {}
ACTIVE_CLIENTS = {}

# ===== حفظ/تحميل الجلسة =====
def save_session(user_id, data):
    try:
        with open(os.path.join(SESSIONS_DIR, f"{user_id}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving session for {user_id}: {e}")

def load_session(user_id):
    try:
        path = os.path.join(SESSIONS_DIR, f"{user_id}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading session for {user_id}: {e}")
    return {}

# ===== إدارة اتصالات Telegram =====
async def setup_telegram_client(user_id, phone=None, code=None, password=None):
    try:
        user_data = USERS.get(user_id, {})
        session_string = user_data.get('session_string')
        
        if session_string:
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        else:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
        
        await client.connect()
        
        if not await client.is_user_authorized():
            if phone and not code:
                # إرسال كود التحقق
                await client.send_code_request(phone)
                return {"status": "code_sent", "message": "✅ تم إرسال كود التحقق"}
            
            elif phone and code:
                # التحقق بالكود
                try:
                    await client.sign_in(phone=phone, code=code)
                    user_data['session_string'] = client.session.save()
                    user_data['phone'] = phone
                    USERS[user_id] = user_data
                    save_session(user_id, user_data)
                    return {"status": "success", "message": "✅ تم تسجيل الدخول بنجاح"}
                except SessionPasswordNeededError:
                    return {"status": "password_needed", "message": "🔒 يلزم إدخال كلمة المرور الثانية"}
            
            elif password:
                # إدخال كلمة المرور الثانية
                await client.sign_in(password=password)
                user_data['session_string'] = client.session.save()
                USERS[user_id] = user_data
                save_session(user_id, user_data)
                return {"status": "success", "message": "✅ تم تسجيل الدخول بنجاح"}
        
        # إذا كانت الجلسة صالحة بالفعل
        return {"status": "success", "message": "✅ الجلسة نشطة بالفعل", "client": client}
        
    except PhoneNumberInvalidError:
        return {"status": "error", "message": "❌ رقم الهاتف غير صحيح"}
    except Exception as e:
        return {"status": "error", "message": f"❌ خطأ: {str(e)}"}

# ===== واجهة الويب (HTML داخل الكود) =====
HTML_PAGE = """
<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>لوحة تحكم Telegram</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
body { background-color:#f8f9fc; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
.container { max-width: 1000px; margin-top:30px; }
.card { margin-bottom:20px; border-radius:10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
.card-header { background: linear-gradient(135deg, #4e73df 0%, #6f42c1 100%); color: white; }
textarea { resize:vertical; min-height:100px; }
.btn { border-radius:5px; }
.connection-status { padding:5px 10px; border-radius:20px; font-size:12px; }
.connected { background:#28a745; color:white; }
.disconnected { background:#dc3545; color:white; }
.log-container { height:300px; overflow-y:auto; background:#2e3440; color:#d8dee9; font-family:monospace; padding:15px; }
.log-entry { margin-bottom:5px; padding:5px; border-bottom:1px solid #4c566a; }
</style>
</head>
<body>
<div class="container">
<div class="text-center mb-4">
    <h2 class="text-primary"><i class="fas fa-paper-plane"></i> لوحة تحكم Telegram</h2>
    <div class="connection-status disconnected" id="connectionStatus">
        <i class="fas fa-times-circle"></i> غير متصل
    </div>
</div>

<div class="row">
    <div class="col-md-6">
        <div class="card">
            <div class="card-header">
                <h5><i class="fas fa-sign-in-alt"></i> تسجيل الدخول</h5>
            </div>
            <div class="card-body">
                <div class="form-group mb-3">
                    <label>رقم الهاتف:</label>
                    <input type="text" id="phone" class="form-control" placeholder="+967xxxxxxxxx">
                </div>
                <button id="send_code" class="btn btn-primary w-100 mb-2">
                    <i class="fas fa-sms"></i> إرسال الكود
                </button>
                
                <div id="codeSection" style="display:none;">
                    <div class="form-group mb-3">
                        <label>كود التحقق:</label>
                        <input type="text" id="code" class="form-control" placeholder="أدخل كود التحقق">
                    </div>
                    <div class="form-group mb-3" id="passwordSection" style="display:none;">
                        <label>كلمة المرور الثانية:</label>
                        <input type="password" id="password" class="form-control" placeholder="كلمة المرور الثانية">
                    </div>
                    <button id="verify_code" class="btn btn-success w-100">
                        <i class="fas fa-check"></i> تحقق
                    </button>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <h5><i class="fas fa-cog"></i> الإعدادات</h5>
            </div>
            <div class="card-body">
                <div class="form-group mb-3">
                    <label>كلمات المراقبة (سطر لكل كلمة):</label>
                    <textarea id="watch_words" class="form-control" placeholder="كلمة1&#10;كلمة2"></textarea>
                </div>
                <div class="d-grid gap-2">
                    <button id="start_monitor" class="btn btn-success">
                        <i class="fas fa-play"></i> تشغيل المراقبة
                    </button>
                    <button id="stop_monitor" class="btn btn-danger">
                        <i class="fas fa-stop"></i> إيقاف المراقبة
                    </button>
                </div>
            </div>
        </div>
    </div>

    <div class="col-md-6">
        <div class="card">
            <div class="card-header">
                <h5><i class="fas fa-paper-plane"></i> إرسال الرسائل</h5>
            </div>
            <div class="card-body">
                <div class="form-group mb-3">
                    <label>المجموعات/القنوات (سطر لكل مجموعة):</label>
                    <textarea id="groups" class="form-control" placeholder="@group1&#10;@group2"></textarea>
                </div>
                <div class="form-group mb-3">
                    <label>الرسالة:</label>
                    <textarea id="message" class="form-control" placeholder="نص الرسالة هنا..."></textarea>
                </div>
                <div class="input-group mb-3">
                    <input type="number" id="interval" class="form-control" placeholder="الفترة بالثواني" value="60">
                    <button class="btn btn-warning" id="auto_send">
                        <i class="fas fa-robot"></i> تلقائي
                    </button>
                </div>
                <button class="btn btn-primary w-100" id="send_now">
                    <i class="fas fa-bolt"></i> إرسال فوري
                </button>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <h5><i class="fas fa-chart-bar"></i> الإحصائيات</h5>
            </div>
            <div class="card-body">
                <div class="row text-center">
                    <div class="col-6">
                        <h5 id="sent">0</h5>
                        <small>الرسائل المرسلة</small>
                    </div>
                    <div class="col-6">
                        <h5 id="errors">0</h5>
                        <small>الأخطاء</small>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<div class="card mt-3">
    <div class="card-header">
        <h5><i class="fas fa-history"></i> سجل الأحداث</h5>
    </div>
    <div class="card-body p-0">
        <div class="log-container" id="log"></div>
    </div>
</div>
</div>

<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script>
const socket = io();
let auto_interval = null;
let isConnected = false;

function log(msg) {
    let logDiv = document.getElementById('log');
    const now = new Date().toLocaleTimeString();
    logDiv.innerHTML += `<div class="log-entry">${now} - ${msg}</div>`;
    logDiv.scrollTop = logDiv.scrollHeight;
}

function updateConnectionStatus(connected) {
    const statusElement = document.getElementById('connectionStatus');
    isConnected = connected;
    if (connected) {
        statusElement.innerHTML = '<i class="fas fa-check-circle"></i> متصل';
        statusElement.className = 'connection-status connected';
    } else {
        statusElement.innerHTML = '<i class="fas fa-times-circle"></i> غير متصل';
        statusElement.className = 'connection-status disconnected';
    }
}

// إرسال كود التحقق
document.getElementById('send_code').onclick = async () => {
    const phone = document.getElementById('phone').value;
    if (!phone) {
        log('❌ يرجى إدخال رقم الهاتف');
        return;
    }
    
    const response = await fetch("/api/send_code", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({phone})
    });
    
    const data = await response.json();
    log(data.message);
    
    if (data.success) {
        document.getElementById('codeSection').style.display = 'block';
    }
};

// التحقق من الكود
document.getElementById('verify_code').onclick = async () => {
    const code = document.getElementById('code').value;
    const password = document.getElementById('password').value;
    
    if (!code && !password) {
        log('❌ يرجى إدخال كود التحقق أو كلمة المرور');
        return;
    }
    
    const payload = {code};
    if (password) payload.password = password;
    
    const response = await fetch("/api/verify_code", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
    });
    
    const data = await response.json();
    log(data.message);
    
    if (data.success) {
        updateConnectionStatus(true);
        document.getElementById('codeSection').style.display = 'none';
        document.getElementById('passwordSection
        # أكمل الكود من هنا...

# ===== إضافة دوال جديدة للتحكم المتقدم =====

@app.route("/api/get_chats", methods=["GET"])
def api_get_chats():
    """الحصول على قائمة الدردشات والمجموعات"""
    try:
        user_id = session['user_id']
        user_data = USERS.get(user_id, load_session(user_id))
        session_string = user_data.get('session_string')
        
        if not session_string:
            return jsonify({"success": False, "message": "❌ يرجى تسجيل الدخول أولاً"})
        
        async def fetch_chats():
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await client.start()
            chats = []
            async for dialog in client.iter_dialogs():
                if dialog.is_group or dialog.is_channel:
                    chats.append({
                        'id': dialog.id,
                        'name': dialog.name,
                        'type': 'قناة' if dialog.is_channel else 'مجموعة',
                        'participants_count': getattr(dialog.entity, 'participants_count', 0)
                    })
            await client.disconnect()
            return chats
        
        chats = asyncio.run(fetch_chats())
        return jsonify({"success": True, "chats": chats})
        
    except Exception as e:
        logger.error(f"Error in get_chats: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ في جلب الدردشات: {str(e)}"})

@app.route("/api/export_session", methods=["GET"])
def api_export_session():
    """تصدير جلسة المستخدم"""
    try:
        user_id = session['user_id']
        user_data = USERS.get(user_id, load_session(user_id))
        session_string = user_data.get('session_string')
        
        if not session_string:
            return jsonify({"success": False, "message": "❌ لا توجد جلسة لتصديرها"})
        
        return jsonify({"success": True, "session_string": session_string})
        
    except Exception as e:
        logger.error(f"Error in export_session: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ في تصدير الجلسة: {str(e)}"})

@app.route("/api/import_session", methods=["POST"])
def api_import_session():
    """استيراد جلسة مستخدم"""
    try:
        user_id = session['user_id']
        data = request.json
        session_string = data.get('session_string')
        
        if not session_string:
            return jsonify({"success": False, "message": "❌ يرجى تقديم سلسلة الجلسة"})
        
        # التحقق من صحة الجلسة
        async def verify_session():
            try:
                client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
                await client.connect()
                
                if await client.is_user_authorized():
                    user_data = {
                        'session_string': session_string,
                        'phone': await client.get_me()
                    }
                    USERS[user_id] = user_data
                    save_session(user_id, user_data)
                    await client.disconnect()
                    return True
                await client.disconnect()
                return False
            except:
                return False
        
        if asyncio.run(verify_session()):
            return jsonify({"success": True, "message": "✅ تم استيراد الجلسة بنجاح"})
        else:
            return jsonify({"success": False, "message": "❌ جلسة غير صالحة"})
            
    except Exception as e:
        logger.error(f"Error in import_session: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ في استيراد الجلسة: {str(e)}"})

@app.route("/api/delete_session", methods=["POST"])
def api_delete_session():
    """حذف جلسة المستخدم"""
    try:
        user_id = session['user_id']
        
        # حذف من الذاكرة
        if user_id in USERS:
            del USERS[user_id]
        
        # حذف من التخزين
        session_path = os.path.join(SESSIONS_DIR, f"{user_id}.json")
        if os.path.exists(session_path):
            os.remove(session_path)
        
        # إيقاف أي مراقبة نشطة
        if user_id in MONITOR_THREADS:
            del MONITOR_THREADS[user_id]
        
        socketio.emit('connection_status', {"status": "disconnected"}, to=user_id)
        return jsonify({"success": True, "message": "✅ تم حذف الجلسة بنجاح"})
        
    except Exception as e:
        logger.error(f"Error in delete_session: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ في حذف الجلسة: {str(e)}"})

@app.route("/api/get_info", methods=["GET"])
def api_get_info():
    """الحصول على معلومات الحساب"""
    try:
        user_id = session['user_id']
        user_data = USERS.get(user_id, load_session(user_id))
        session_string = user_data.get('session_string')
        
        if not session_string:
            return jsonify({"success": False, "message": "❌ يرجى تسجيل الدخول أولاً"})
        
        async def fetch_info():
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await client.start()
            me = await client.get_me()
            info = {
                'id': me.id,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'username': me.username,
                'phone': me.phone
            }
            await client.disconnect()
            return info
        
        user_info = asyncio.run(fetch_info())
        return jsonify({"success": True, "user_info": user_info})
        
    except Exception as e:
        logger.error(f"Error in get_info: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ في جلب المعلومات: {str(e)}"})

# ===== إضافة دوال للنسخ الاحتياطي والاستعادة =====

@app.route("/api/backup", methods=["GET"])
def api_backup():
    """إنشاء نسخة احتياطية من الإعدادات"""
    try:
        user_id = session['user_id']
        user_data = USERS.get(user_id, load_session(user_id))
        
        # إزالة البيانات الحساسة من النسخة الاحتياطية
        backup_data = user_data.copy()
        if 'session_string' in backup_data:
            del backup_data['session_string']
        
        return jsonify({"success": True, "backup": backup_data})
        
    except Exception as e:
        logger.error(f"Error in backup: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ في إنشاء النسخة الاحتياطية: {str(e)}"})

@app.route("/api/restore", methods=["POST"])
def api_restore():
    """استعادة الإعدادات من نسخة احتياطية"""
    try:
        user_id = session['user_id']
        data = request.json
        backup_data = data.get('backup', {})
        
        if not backup_data:
            return jsonify({"success": False, "message": "❌ لا توجد بيانات للاستعادة"})
        
        user_data = USERS.get(user_id, load_session(user_id))
        user_data.update(backup_data)
        USERS[user_id] = user_data
        save_session(user_id, user_data)
        
        return jsonify({"success": True, "message": "✅ تم استعادة الإعدادات بنجاح"})
        
    except Exception as e:
        logger.error(f"Error in restore: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ في استعادة الإعدادات: {str(e)}"})

# ===== إضافة دالة للبث الجماعي مع التقدم =====

@app.route("/api/broadcast", methods=["POST"])
def api_broadcast():
    """بث رسالة إلى عدة مجموعات مع متابعة التقدم"""
    try:
        user_id = session['user_id']
        data = request.json
        groups = data.get('groups', [])
        message = data.get('message', '')
        
        user_data = USERS.get(user_id, load_session(user_id))
        session_string = user_data.get('session_string')
        
        if not session_string:
            return jsonify({"success": False, "message": "❌ يرجى تسجيل الدخول أولاً"})
        
        if not groups or not message:
            return jsonify({"success": False, "message": "❌ يرجى إدخال الرسالة والمجموعات"})
        
        # تخزين حالة البث
        if 'broadcast_status' not in user_data:
            user_data['broadcast_status'] = {
                'total': len(groups),
                'sent': 0,
                'failed': 0,
                'current': 0
            }
        
        # البث في thread منفصل
        def broadcast_messages():
            async def broadcast_task():
                try:
                    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
                    await client.start()
                    
                    status = user_data['broadcast_status']
                    status['current'] = 0
                    
                    for i, group in enumerate(groups):
                        status['current'] = i + 1
                        try:
                            await client.send_message(group, message)
                            status['sent'] += 1
                            socketio.emit('log_update', {"message": f"✅ تم البث إلى {group} ({i+1}/{len(groups)})"}, to=user_id)
                        except Exception as e:
                            status['failed'] += 1
                            socketio.emit('log_update', {"message": f"❌ فشل البث إلى {group}: {str(e)}"}, to=user_id)
                        
                        # إرسال تحديث التقدم
                        socketio.emit('broadcast_progress', {
                            'current': status['current'],
                            'total': status['total'],
                            'sent': status['sent'],
                            'failed': status['failed']
                        }, to=user_id)
                    
                    # تحديث الإحصائيات النهائية
                    if 'stats' not in user_data:
                        user_data['stats'] = {"sent": 0, "errors": 0}
                    user_data['stats']['sent'] += status['sent']
                    user_data['stats']['errors'] += status['failed']
                    USERS[user_id] = user_data
                    save_session(user_id, user_data)
                    
                    socketio.emit('stats_update', user_data['stats'], to=user_id)
                    socketio.emit('log_update', {"message": f"✅ اكتمل البث: {status['sent']} نجاح, {status['failed']} فشل"}, to=user_id)
                    
                    await client.disconnect()
                    
                except Exception as e:
                    socketio.emit('log_update', {"message": f"❌ خطأ في البث: {str(e)}"}, to=user_id)
            
            asyncio.run(broadcast_task())
        
        threading.Thread(target=broadcast_messages, daemon=True).start()
        return jsonify({"success": True, "message": "🚀 بدأ البث الجماعي"})
        
    except Exception as e:
        logger.error(f"Error in broadcast: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ في البث: {str(e)}"})

# ===== إضافة دالة لإدارة القنوات =====

@app.route("/api/join_channel", methods=["POST"])
def api_join_channel():
    """الانضمام إلى قناة أو مجموعة"""
    try:
        user_id = session['user_id']
        data = request.json
        channel = data.get('channel')
        
        user_data = USERS.get(user_id, load_session(user_id))
        session_string = user_data.get('session_string')
        
        if not session_string:
            return jsonify({"success": False, "message": "❌ يرجى تسجيل الدخول أولاً"})
        
        if not channel:
            return jsonify({"success": False, "message": "❌ يرجى إدخال معرف القناة"})
        
        async def join_channel_task():
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await client.start()
            
            try:
                await client.join_channel(channel)
                await client.disconnect()
                return True
            except Exception as e:
                await client.disconnect()
                raise e
        
        success = asyncio.run(join_channel_task())
        if success:
            return jsonify({"success": True, "message": f"✅ تم الانضمام إلى {channel}"})
        else:
            return jsonify({"success": False, "message": f"❌ فشل الانضمام إلى {channel}"})
            
    except Exception as e:
        logger.error(f"Error in join_channel: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ في الانضمام: {str(e)}"})

# ===== تحسين دالة المراقبة =====

def enhanced_monitor_task(user_id, words):
    """نسخة محسنة من دالة المراقبة"""
    user_data = USERS.get(user_id, load_session(user_id))
    session_string = user_data.get('session_string')
    
    if not session_string:
        return
    
    async def run_enhanced_monitor():
        try:
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await client.start()
            
            # إرسال رسالة بدء المراقبة
            await client.send_message('me', "🚀 بدأت مراقبة الرسائل")
            
            @client.on(events.NewMessage)
            async def handler(event):
                try:
                    msg_text = event.message.text or event.message.message or ""
                    sender = await event.get_sender()
                    chat = await event.get_chat()
                    
                    for word in words:
                        if word.lower() in msg_text.lower():
                            # معلومات المرسل
                            sender_name = getattr(sender, 'first_name', '') 
                            if getattr(sender, 'last_name', ''):
                                sender_name += f" {sender.last_name}"
                            if getattr(sender, 'username', ''):
                                sender_name += f" (@{sender.username})"
                            
                            # معلومات الدردشة
                            chat_name = getattr(chat, 'title', getattr(chat, 'username', 'unknown'))
                            
                            # إنشاء التنبيه
                            alert_msg = (
                                f"🔔 كلمة '{word}' تم رصدها\n\n"
                                f"في: {chat_name}\n"
                                f"من: {sender_name}\n"
                                f"المحتوى: {msg_text[:200]}..."
                            )
                            
                            socketio.emit('log_update', {"message": alert_msg}, to=user_id)
                            
                            # إرسال التنبيه إلى المحادثة الخاصة
                            try:
                                await client.send_message('me', alert_msg)
                            except:
                                pass
                except Exception as e:
                    logger.error(f"Error in enhanced monitor handler: {e}")
            
            # البقاء في حلقة المراقبة
            await client.run_until_disconnected()
            
        except Exception as e:
            socketio.emit('log_update', {"message": f"❌ خطأ في المراقبة المحسنة: {str(e)}"}, to=user_id)
    
    # تشغيل المراقبة المحسنة
    asyncio.run(run_enhanced_monitor())

# ===== إضافة route للمراقبة المحسنة =====

@app.route("/api/enhanced_monitor", methods=["POST"])
def api_enhanced_monitor():
    """بدء المراقبة المحسنة"""
    try:
        user_id = session['user_id']
        data = request.json
        words = data.get('words', [])
        
        user_data = USERS.get(user_id, load_session(user_id))
        session_string = user_data.get('session_string')
        
        if not session_string:
            return jsonify({"success": False, "message": "❌ يرجى تسجيل الدخول أولاً"})
        
        # إيقاف المراقبة الحالية إذا كانت تعمل
        if user_id in MONITOR_THREADS:
            return jsonify({"success": False, "message": "✅ المراقبة تعمل بالفعل"})
        
        # بدء المراقبة المحسنة في thread منفصل
        thread = threading.Thread(target=enhanced_monitor_task, args=(user_id, words), daemon=True)
        thread.start()
        MONITOR_THREADS[user_id] = thread
        
        return jsonify({"success": True, "message": "🚀 بدأت المراقبة المحسنة"})
        
    except Exception as e:
        logger.error(f"Error in enhanced_monitor: {e}")
        return jsonify({"success": False, "message": f"❌ خطأ في بدء المراقبة المحسنة: {str(e)}"})

# ===== التشغيل الرئيسي مع خيارات إضافية =====

if __name__ == "__main__":
    # تحميل الجلسات الموجودة عند البدء
    for filename in os.listdir(SESSIONS_DIR):
        if filename.endswith('.json'):
            user_id = filename.split('.')[0]
            USERS[user_id] = load_session(user_id)
            logger.info(f"تم تحميل جلسة المستخدم: {user_id}")
    
    # إعداد خيارات السيرفر
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    logger.info(f"بدء تشغيل السيرفر على {host}:{port}")
    
    # تشغيل التطبيق
    socketio.run(app, host=host, port=port, debug=debug)
