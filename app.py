# app.py
import eventlet
eventlet.monkey_patch()

import os, json, uuid, time, asyncio, threading
from threading import Lock
import logging
from flask import Flask, session, request, render_template, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room

# الوحدات المخصصة
from telegram_client import send_code_request, sign_in_with_code, cleanup_auth_data, get_auth_status
from monitoring import monitoring_task

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(24))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=60, ping_interval=25)

SESSIONS_DIR = "sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

USERS = {}
USERS_LOCK = Lock()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# ---------- Helper: save/load settings ----------
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

# ---------- Load sessions on startup (non-blocking) ----------
def load_all_sessions():
    # لا نريد حجب التشغيل لفترة طويلة، لكن نحاول تحميل ملفات الإعداد فقط
    for filename in os.listdir(SESSIONS_DIR):
        if filename.endswith('.json'):
            user_id = filename.split('.')[0]
            try:
                settings = load_settings(user_id)
                # لو كان هناك session_string نعلم أن الجلسة محفوظة (حالة الاتصال سنتحقق لاحقاً عند start)
                USERS[user_id] = {
                    'client': None,
                    'settings': settings,
                    'thread': None,
                    'is_running': False,
                    'stats': settings.get('stats', {"sent": 0, "errors": 0}),
                    'connected': bool(settings.get('session_string'))
                }
            except Exception as e:
                logger.error(f"Failed to load session file {filename}: {str(e)}")

# ---------- Socket events ----------
@socketio.on('join')
def on_join(data):
    if 'user_id' in session:
        join_room(session['user_id'])

@socketio.on('connect')
def on_connect():
    if 'user_id' in session:
        user_id = session['user_id']
        join_room(user_id)
        logger.info(f"Socket connected: {user_id}")
        # ارسال حالة للمستخدم
        with USERS_LOCK:
            st = USERS.get(user_id)
            connected = False
            if st:
                connected = st.get('connected', False) or st.get('is_running', False)
        emit('connection_status', {"status": "connected" if connected else "disconnected"})

# ---------- Routes / UI ----------
@app.route("/")
def index():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
        session.permanent = True
    user_id = session['user_id']
    settings = load_settings(user_id)
    connection_status = "disconnected"
    with USERS_LOCK:
        if user_id in USERS:
            connection_status = "connected" if (USERS[user_id].get('connected') or USERS[user_id].get('is_running')) else "disconnected"
    return render_template("index.html", settings=settings, connection_status=connection_status)

@app.route("/api/save_login", methods=["POST"])
def api_save_login():
    user_id = session['user_id']
    data = request.json or {}
    if not data:
        return {"success": False, "message": "❌ لم يتم إرسال البيانات"}

    # قم بتنظيف أي auth مؤقت سابق
    cleanup_auth_data(user_id)

    phone = data.get('phone')
    password = data.get('password')  # قد تكون None

    if not phone:
        return {"success": False, "message": "❌ أدخل رقم الهاتف"}

    # احفظ الإعدادات الأولية
    settings = load_settings(user_id)
    settings.update({
        'phone': phone,
        'password': password,
        'login_time': time.time(),
        # لا تضيف api_id/api_hash للواجهة
    })
    save_settings(user_id, settings)

    # أرسل كود
    try:
        # send_code_request هو coroutine
        result = asyncio.run(send_code_request(user_id, phone))
        if result.get('status') == 'code_sent':
            return {"success": True, "message": result.get('message', 'تم إرسال الكود'), "code_required": True}
        else:
            return {"success": False, "message": result.get('message', 'خطأ عند إرسال الكود')}
    except Exception as e:
        logger.exception("Failed send_code")
        return {"success": False, "message": f"❌ خطأ: {str(e)}"}

@app.route("/api/verify_code", methods=["POST"])
def api_verify_code():
    user_id = session.get('user_id')
    data = request.json or {}
    code = data.get('code')
    password = data.get('password')

    if not code and not password:
        return {"success": False, "message": "❌ أدخل الكود أو كلمة المرور"}

    settings = load_settings(user_id)
    if not settings:
        return {"success": False, "message": "❌ لم يتم حفظ بيانات الدخول"}

    phone = settings.get('phone')

    # التحقق من وجود حالة تحقق مؤقتة
    auth_status = get_auth_status(user_id)
    if not auth_status:
        # قد تكون الجلسة منتهية أو لم يُطلب الكود
        return {"success": False, "message": "❌ لم يُطلب كود تحقق؛ أعد العملية"}

    try:
        if code:
            res = asyncio.run(sign_in_with_code(user_id, phone, code=code.strip()))
        else:
            res = asyncio.run(sign_in_with_code(user_id, phone, password=password))

        if res.get('status') == 'success':
            session_string = res.get('session_string')
            # حفظ session_string في إعدادات المستخدم
            settings['session_string'] = session_string
            settings.setdefault('stats', {"sent": 0, "errors": 0})
            save_settings(user_id, settings)

            # أنشئ مدخلاً في USERS (سيتم إنشاء client في المهام عند الحاجة)
            with USERS_LOCK:
                USERS[user_id] = {
                    'client': None,
                    'settings': settings,
                    'thread': None,
                    'is_running': False,
                    'stats': settings.get('stats', {"sent": 0, "errors": 0}),
                    'connected': True
                }

            socketio.emit('log_update', {"message": "✅ تم التحقق من الكود وتسجيل الجلسة"}, to=user_id)
            socketio.emit('connection_status', {"status": "connected"}, to=user_id)
            return {"success": True, "message": "✅ تم التحقق من الكود وتسجيل الجلسة"}
        elif res.get('status') == 'password_required':
            return {"success": True, "message": "🔒 مطلوب إدخال كلمة المرور", "password_required": True}
        else:
            return {"success": False, "message": res.get('message', 'فشل التحقق')}
    except Exception as e:
        logger.exception("verify_code error")
        return {"success": False, "message": f"❌ خطأ: {str(e)}"}

@app.route("/api/save_settings", methods=["POST"])
def api_save_settings():
    user_id = session['user_id']
    data = request.json or {}
    if not data:
        return {"success": False, "message": "❌ لم يتم إرسال البيانات"}

    current = load_settings(user_id)
    current.update({
        'message': data.get('message',''),
        'groups': [g.strip() for g in data.get('groups','').split('\n') if g.strip()],
        'interval_seconds': int(data.get('interval_seconds', 3600)),
        'watch_words': [w.strip() for w in data.get('watch_words','').split('\n') if w.strip()],
        'send_type': data.get('send_type','manual'),
        'max_retries': int(data.get('max_retries',5)),
        'auto_reconnect': bool(data.get('auto_reconnect', False))
    })
    save_settings(user_id, current)
    with USERS_LOCK:
        if user_id in USERS:
            USERS[user_id]['settings'] = current
    socketio.emit('log_update', {"message": "✅ تم حفظ الإعدادات"}, to=user_id)
    return {"success": True, "message": "✅ تم حفظ الإعدادات"}

@app.route("/api/start_monitoring", methods=["POST"])
def api_start_monitoring():
    user_id = session.get('user_id')
    with USERS_LOCK:
        if user_id not in USERS:
            return {"success": False, "message": "❌ لم يتم تسجيل الجلسة بعد"}
        if USERS[user_id]['is_running']:
            return {"success": False, "message": "✅ النظام يعمل بالفعل"}
        USERS[user_id]['is_running'] = True

    # أطلق المهمة كخلفية (eventlet-friendly)
    socketio.start_background_task(monitoring_task, user_id, USERS, USERS_LOCK, socketio)
    socketio.emit('log_update', {"message": "🚀 بدأت المراقبة"}, to=user_id)
    return {"success": True, "message": "🚀 بدأت المراقبة"}

@app.route("/api/stop_monitoring", methods=["POST"])
def api_stop_monitoring():
    user_id = session.get('user_id')
    with USERS_LOCK:
        if user_id in USERS:
            USERS[user_id]['is_running'] = False
            return {"success": True, "message": "⏹ تم إيقاف المراقبة"}
    return {"success": False, "message": "❌ لم يتم تشغيل النظام"}

@app.route("/api/send_now", methods=["POST"])
def api_send_now():
    user_id = session.get('user_id')
    with USERS_LOCK:
        if user_id not in USERS:
            return {"success": False, "message": "❌ لم يتم تسجيل الجلسة بعد"}
        settings = USERS[user_id]['settings']
        session_string = settings.get('session_string')
    if not session_string:
        return {"success": False, "message": "❌ لا توجد جلسة محفوظة"}

    async def do_send():
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(session_string), 22043994, "56f64582b363d367280db96586b97801")
        try:
            await client.start()
            for g in settings.get('groups', []):
                try:
                    await client.send_message(g, settings.get('message',''))
                    socketio.emit('log_update', {"message": f"✅ أرسلت إلى {g}"}, to=user_id)
                except Exception as e:
                    socketio.emit('log_update', {"message": f"❌ فشل الإرسال إلى {g}: {str(e)}"}, to=user_id)
            await client.disconnect()
        except Exception as e:
            socketio.emit('log_update', {"message": f"❌ خطأ الإرسال: {str(e)}"}, to=user_id)

    # run in new loop
    try:
        asyncio.run(do_send())
        return {"success": True, "message": "✅ تم الإرسال الفوري"}
    except Exception as e:
        return {"success": False, "message": f"❌ خطأ: {str(e)}"}

@app.route("/api/get_login_status", methods=["GET"])
def api_get_login_status():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"logged_in": False, "connected": False})
    with USERS_LOCK:
        if user_id in USERS:
            return jsonify({
                "logged_in": bool(USERS[user_id]['settings'].get('session_string')),
                "connected": USERS[user_id].get('connected', False) or USERS[user_id].get('is_running', False)
            })
    return jsonify({"logged_in": False, "connected": False})

@app.route("/api/reset_login", methods=["POST"])
def api_reset_login():
    user_id = session.get('user_id')
    cleanup_auth_data(user_id)
    with USERS_LOCK:
        if user_id in USERS:
            USERS[user_id]['is_running'] = False
            USERS.pop(user_id, None)
    # remove saved files
    f = os.path.join(SESSIONS_DIR, f"{user_id}.json")
    if os.path.exists(f):
        os.remove(f)
    p = os.path.join(SESSIONS_DIR, f"{user_id}_session.session")
    if os.path.exists(p):
        os.remove(p)
    socketio.emit('log_update', {"message": "🔄 تم إعادة تعيين الجلسة"}, to=user_id)
    return {"success": True, "message": "✅ تم إعادة التعيين"}

@app.route("/api/logout", methods=["POST"])
def api_logout():
    return api_reset_login()

# ---------- Admin endpoints omitted for brevity (can be added similarly) ----------

# ---------- Startup ----------
if __name__ == "__main__":
    load_all_sessions()
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, allow_unsafe_werkzeug=True)
