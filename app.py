import os
import threading
import asyncio
from flask import Flask, request, render_template_string
from telethon import TelegramClient, events

# =========================
# بيانات تليجرام
# انسخ API_ID و API_HASH من my.telegram.org
# وضع رقمك الدولي مع مفتاح الدولة
# =========================
API_ID = int(os.getenv("API_ID", 123456))   # ضع API_ID
API_HASH = os.getenv("API_HASH", "YOUR_API_HASH")
PHONE = os.getenv("PHONE", "+966500000000") # رقمك مع مفتاح الدولة

# =========================
# تهيئة التليجرام
# =========================
client = TelegramClient("session", API_ID, API_HASH)
keywords = set()

# =========================
# مراقبة كل الرسائل
# =========================
@client.on(events.NewMessage())
async def handler(event):
    text = event.raw_text.lower()
    for word in keywords:
        if word.lower() in text:
            sender = await event.get_sender()
            chat = await event.get_chat()

            msg = (
                f"🚨 كلمة مراقبة: **{word}**\n\n"
                f"👤 المرسل: {getattr(sender, 'username', sender.id)}\n"
                f"💬 الرسالة: {event.raw_text}\n"
                f"🔗 الرابط: https://t.me/c/{chat.id}/{event.id}" if event.is_group else ""
            )

            # إرسال التنبيه لمحادثة المحفوظات (Saved Messages)
            await client.send_message("me", msg)
            break

# =========================
# واجهة HTML
# =========================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar">
<head>
  <meta charset="UTF-8">
  <title>مراقبة كلمات تليجرام</title>
  <style>
    body { font-family: Tahoma, sans-serif; background: #f4f4f4; padding: 20px; text-align:center; }
    h1 { color: #333; }
    input, button { padding: 10px; margin: 5px; border-radius: 8px; }
    ul { list-style:none; padding:0; }
    li { background:#fff; margin:5px; padding:10px; border-radius:8px; }
  </style>
</head>
<body>
  <h1>📡 مراقبة كلمات تليجرام</h1>
  <input type="text" id="wordInput" placeholder="أدخل كلمة لمراقبتها">
  <button onclick="addWord()">➕ إضافة كلمة</button>
  <ul id="keywords"></ul>

  <script>
    let words = [];
    function addWord() {
      let word = document.getElementById("wordInput").value;
      if(word && !words.includes(word)) {
        words.push(word);
        document.getElementById("keywords").innerHTML += `<li>${word}</li>`;
        fetch("/add_keyword", { 
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ word })
        });
      }
      document.getElementById("wordInput").value = "";
    }
  </script>
</body>
</html>
"""

app = Flask(__name__)

@app.route("/")
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route("/add_keyword", methods=["POST"])
def add_keyword():
    data = request.get_json()
    word = data.get("word")
    if word:
        keywords.add(word)
    return {"status": "ok", "keywords": list(keywords)}

# =========================
# تشغيل Flask + Telethon مع بعض
# =========================
def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

async def run_telethon():
    await client.start(phone=PHONE)
    print("✅ البوت يراقب الآن...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    asyncio.run(run_telethon())
