import os
import threading
import asyncio
from flask import Flask, request, render_template_string, jsonify
from telethon import TelegramClient, events

# =========================
# إعدادات عامة
# =========================
API_ID = int(os.getenv("API_ID", 123456))   # ضع API_ID
API_HASH = os.getenv("API_HASH", "YOUR_API_HASH")
SESSION_NAME = "session"

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
keywords = set()
is_running = False
phone_number = None
loop = asyncio.get_event_loop()

# =========================
# HTML Template
# =========================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar">
<head>
  <meta charset="UTF-8">
  <title>📡 مراقبة تليجرام</title>
  <style>
    body { font-family: Tahoma, sans-serif; background: #f4f4f4; padding: 20px; }
    h1 { color: #333; text-align:center; }
    input, button { padding: 10px; margin: 5px; border-radius: 8px; }
    .section { background:#fff; padding:20px; margin:20px auto; border-radius:10px; width:90%; max-width:600px; }
    ul { list-style:none; padding:0; }
    li { background:#eee; margin:5px; padding:8px; border-radius:6px; }
    .status { font-weight:bold; margin:10px 0; }
  </style>
</head>
<body>
  <h1>📡 مراقبة تليجرام</h1>

  <div class="section">
    <h2>🔑 تسجيل الدخول</h2>
    <form id="loginForm">
      <input type="text" id="phone" placeholder="أدخل رقم الهاتف" required><br>
      <button type="submit">إرسال الكود</button>
    </form>
    <form id="codeForm" style="display:none;">
      <input type="text" id="code" placeholder="أدخل كود التحقق"><br>
      <button type="submit">تأكيد الكود</button>
    </form>
  </div>

  <div class="section">
    <h2>📝 كلمات المراقبة</h2>
    <input type="text" id="wordInput" placeholder="أدخل كلمة">
    <button onclick="addWord()">➕ إضافة</button>
    <ul id="keywords"></ul>
  </div>

  <div class="section">
    <h2>⚙️ التحكم</h2>
    <button onclick="toggleMonitor()">▶️ بدء / ⏹️ إيقاف</button>
    <div class="status" id="status">🔴 المراقبة متوقفة</div>
  </div>

  <script>
    // تسجيل الدخول
    document.getElementById("loginForm").onsubmit = async function(e){
      e.preventDefault();
      let phone = document.getElementById("phone").value;
      let res = await fetch("/send_code", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({phone})
      });
      let data = await res.json();
      if(data.status=="ok"){
        alert("✅ تم إرسال الكود إلى تليجرام.");
        document.getElementById("loginForm").style.display="none";
        document.getElementById("codeForm").style.display="block";
      }
    };

    document.getElementById("codeForm").onsubmit = async function(e){
      e.preventDefault();
      let code = document.getElementById("code").value;
      let res = await fetch("/confirm_code", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({code})
      });
      let data = await res.json();
      alert(data.message);
    };

    // إضافة كلمات
    async function addWord(){
      let word = document.getElementById("wordInput").value;
      if(word){
        let res = await fetch("/add_keyword", {
          method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({word})
        });
        let data = await res.json();
        document.getElementById("keywords").innerHTML = data.keywords.map(w=>"<li>"+w+"</li>").join("");
        document.getElementById("wordInput").value="";
      }
    }

    // تشغيل/إيقاف المراقبة
    async function toggleMonitor(){
      let res = await fetch("/toggle_monitor", {method:"POST"});
      let data = await res.json();
      document.getElementById("status").innerText = data.status;
    }
  </script>
</body>
</html>
"""

# =========================
# Flask
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route("/send_code", methods=["POST"])
def send_code():
    global phone_number
    phone_number = request.json.get("phone")
    asyncio.run_coroutine_threadsafe(client.send_code_request(phone_number), loop)
    return jsonify({"status":"ok"})

@app.route("/confirm_code", methods=["POST"])
def confirm_code():
    code = request.json.get("code")
    try:
        asyncio.run_coroutine_threadsafe(client.sign_in(phone_number, code), loop).result()
        return jsonify({"status":"ok", "message":"✅ تم تسجيل الدخول بنجاح"})
    except Exception as e:
        return jsonify({"status":"error", "message":str(e)})

@app.route("/add_keyword", methods=["POST"])
def add_keyword():
    word = request.json.get("word")
    if word: keywords.add(word)
    return jsonify({"keywords":list(keywords)})

@app.route("/toggle_monitor", methods=["POST"])
def toggle_monitor():
    global is_running
    is_running = not is_running
    status = "🟢 المراقبة تعمل" if is_running else "🔴 المراقبة متوقفة"
    return jsonify({"status":status})

# =========================
# Telethon event
# =========================
@client.on(events.NewMessage())
async def handler(event):
    if not is_running: return
    text = event.raw_text.lower()
    for word in keywords:
        if word.lower() in text:
            sender = await event.get_sender()
            chat = await event.get_chat()
            msg = (
                f"🚨 كلمة: **{word}**\n\n"
                f"👤 المرسل: {getattr(sender, 'username', sender.id)}\n"
                f"💬 الرسالة: {event.raw_text}\n"
                f"🔗 الرابط: https://t.me/c/{chat.id}/{event.id}" if event.is_group else ""
            )
            await client.send_message("me", msg)
            break

# =========================
# تشغيل Flask + Telethon
# =========================
def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

def run_telethon():
    loop.run_until_complete(client.start())
    loop.run_until_complete(client.run_until_disconnected())

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    run_telethon()
