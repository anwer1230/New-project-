# telegram_client.py
import os, json, asyncio
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError

# ====== API مخفية داخل هذا الملف ======
API_ID = 22043994
API_HASH = "56f64582b363d367280db96586b97801"

SESSIONS_DIR = "sessions"
AUTH_TEMP_SUFFIX = "_auth.json"   # يخزن info مؤقت بعد طلب الكود

if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

def _auth_temp_path(user_id):
    return os.path.join(SESSIONS_DIR, f"{user_id}{AUTH_TEMP_SUFFIX}")

async def send_code_request(user_id: str, phone: str) -> dict:
    """
    يرسل كود إلى الهاتف ويحتفظ بحالة مؤقتة حتى يدخل المستخدم الكود.
    """
    client = TelegramClient(None, API_ID, API_HASH)  # no persistent session yet
    try:
        await client.connect()
        await client.send_code_request(phone)
        # احفظ حالة مؤقتة حتى يرسل المستخدم الكود
        with open(_auth_temp_path(user_id), "w", encoding="utf-8") as f:
            json.dump({"phone": phone, "time": int(asyncio.get_event_loop().time())}, f, ensure_ascii=False)
        return {"status": "code_sent", "message": "تم إرسال كود التحقق إلى رقم الهاتف."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        try:
            await client.disconnect()
        except:
            pass

async def sign_in_with_code(user_id: str, phone: str, code: str = None, password: str = None) -> dict:
    """
    يحاول تسجيل الدخول باستخدام الكود أو كلمة المرور. عند النجاح يعيد session_string.
    """
    # session path per user so Telethon will save auth info there
    session_path = os.path.join(SESSIONS_DIR, f"{user_id}_session")
    client = TelegramClient(session_path, API_ID, API_HASH)
    try:
        await client.connect()
        # إذا الكود موجود -> حاول sign_in
        if code:
            try:
                await client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                # الحساب فيه 2FA ويحتاج كلمة مرور
                return {"status": "password_required", "message": "الحساب مفعّل عليه 2FA، مطلوب كلمة مرور."}
            except PhoneCodeInvalidError:
                return {"status": "error", "message": "كود التحقق غير صحيح."}
            except PhoneCodeExpiredError:
                return {"status": "error", "message": "كود التحقق منتهي الصلاحية."}
        elif password:
            # sign in by password (after SessionPasswordNeededError)
            try:
                await client.sign_in(password=password)
            except Exception as e:
                return {"status": "error", "message": f"خطأ في كلمة المرور: {str(e)}"}
        else:
            # لا كود ولا كلمة مرور: سنفترض هذه الدالة يجب أن تتلقى واحداً منهما
            return {"status": "error", "message": "لم يتم توفير الكود أو كلمة المرور."}

        # إذا وصلنا هنا فالمصادقة تمت
        session_str = client.session.save()  # StringSession
        # احذف الحالة المؤقتة
        try:
            os.remove(_auth_temp_path(user_id))
        except:
            pass
        return {"status": "success", "session_string": session_str}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        try:
            await client.disconnect()
        except:
            pass

def cleanup_auth_data(user_id: str):
    """يحذف الملفات المؤقتة المتعلقة بعملية المصادقة لجلسة المستخدم"""
    try:
        path = _auth_temp_path(user_id)
        if os.path.exists(path):
            os.remove(path)
    except:
        pass
    # لا نحذف ملفات الجلسة المحفوظة نهائياً هنا، لأن ذلك يُستخدم عند logout من الAPI.

def get_auth_status(user_id: str) -> dict:
    """يرجع True إذا توجد حالة تحقق مؤقتة (تم طلب كود)"""
    path = _auth_temp_path(user_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return None
    return None
