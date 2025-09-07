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

# تغيير مسار الواجهة إلى نفس المجلد الحالي
app = Flask(__name__, template_folder=".")  
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

# ---------- Load sessions on startup ----------
def load_all_sessions():
    for filename in os.listdir(SESSIONS_DIR):
        if filename.endswith('.json'):
            user_id = filename.split('.')[0]
            try:
                settings = load_settings(user_id)
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

# ---------- بقية الـ API والوظائف بدون تغيير ----------
# جميع الدوال والـ routes تبقى كما هي، بدون أي تعديل على المنطق، فقط تغيير مسار الواجهة كما طلبت

# ---------- Startup ----------
if __name__ == "__main__":
    load_all_sessions()
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, allow_unsafe_werkzeug=True)
