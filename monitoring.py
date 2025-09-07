# monitoring.py
import asyncio, json, os
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# نستخدم نفس API_ID/API_HASH من telegram_client
from telegram_client import API_ID, API_HASH

SESSIONS_DIR = "sessions"

def monitoring_task(user_id: str, USERS: dict, USERS_LOCK, socketio):
    """
    دالة خلفية تعمل لكل مستخدم عند بدء المراقبة.
    ستنشئ حلقات asyncio خاصة بها وتتحكم بالـ client.
    """
    # إنشاء حلقة جديدة ليعمل داخل thread/background task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run_monitor():
        # جلب session_string من إعدادات
        settings_path = os.path.join(SESSIONS_DIR, f"{user_id}.json")
        if not os.path.exists(settings_path):
            socketio.emit('log_update', {"message": "❌ لا توجد إعدادات محفوظة."}, to=user_id)
            return

        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)

        session_string = settings.get("session_string")
        if not session_string:
            socketio.emit('log_update', {"message": "❌ لا توجد جلسة (session). الرجاء التحقق."}, to=user_id)
            return

        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

        @client.on(events.NewMessage())
        async def handler(event):
            try:
                msg = event.message.message or ""
                # مراقبة كلمات
                for word in settings.get("watch_words", []):
                    if word and word in msg:
                        # إرسال إشعار إلى المحادثة الخاصة (Saved Messages)
                        try:
                            await client.send_message('me', f"🔔 رصدت كلمة '{word}' في {event.chat_id}\n{msg[:200]}")
                            socketio.emit('log_update', {"message": f"🔔 رصدت كلمة '{word}'"}, to=user_id)
                        except Exception as e:
                            socketio.emit('log_update', {"message": f"❌ خطأ بإرسال التنبيه: {str(e)}"}, to=user_id)
            except Exception:
                pass

        try:
            await client.start()
            socketio.emit('log_update', {"message": "✅ تم تشغيل جلسة التليجرام للمراقبة"}, to=user_id)
            # دوري الإرسال والمراقبة
            while True:
                # تحقق إن المستخدم أوقف المهمة
                with USERS_LOCK:
                    u = USERS.get(user_id)
                    running = u.get('is_running', False) if u else False
                if not running:
                    socketio.emit('log_update', {"message": "⏹ تم إيقاف المراقبة"}, to=user_id)
                    break

                # الإرسال التلقائي إذا مفعل
                if settings.get("send_type") == "automatic":
                    groups = settings.get("groups", [])
                    message = settings.get("message", "")
                    for g in groups:
                        try:
                            await client.send_message(g, message)
                            socketio.emit('log_update', {"message": f"🚀 أرسلت رسالة إلى {g}"}, to=user_id)
                            with USERS_LOCK:
                                if user_id in USERS:
                                    USERS[user_id]['stats']['sent'] += 1
                        except Exception as e:
                            socketio.emit('log_update', {"message": f"❌ فشل الإرسال إلى {g}: {str(e)}"}, to=user_id)
                            with USERS_LOCK:
                                if user_id in USERS:
                                    USERS[user_id]['stats']['errors'] += 1

                # انتظر الفاصل المحدد
                await asyncio.sleep(max(1, int(settings.get("interval_seconds", 60))))
        except Exception as e:
            socketio.emit('log_update', {"message": f"❌ خطأ في مهمة المراقبة: {str(e)}"}, to=user_id)
        finally:
            try:
                await client.disconnect()
            except:
                pass

    # تشغيل ال Coroutine في loop هذا الخيط
    loop.run_until_complete(run_monitor())
    loop.close()
