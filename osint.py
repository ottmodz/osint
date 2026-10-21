import os
import sqlite3
import threading
import time
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot

# ============ CONFIGURATION ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_ID = 5989928539  # Aapki Admin ID
APP_URL = "https://osint-kblt.onrender.com"  # Aapka Render App URL

bot = telebot.TeleBot(BOT_TOKEN)

# ============ DATABASE SETUP ============
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        status TEXT DEFAULT 'active'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS banned_requests (
        user_id INTEGER PRIMARY KEY
    )''')
    conn.commit()
    conn.close()

def add_user(user_id, first_name):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, first_name, status) VALUES (?, ?, 'active')", (user_id, first_name))
    c.execute("UPDATE users SET status = 'active', first_name = ? WHERE user_id = ?", (first_name, user_id))
    conn.commit()
    conn.close()

def is_user_banned(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM banned_requests WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

def ban_user_id(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO banned_requests (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

# ============ KEEPALIVE SERVER (Render ke liye) ============
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")
    def log_message(self, format, *args):
        pass

def start_keepalive_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

def run_keepalive_in_thread():
    server_thread = threading.Thread(target=start_keepalive_server, daemon=True)
    server_thread.start()

def self_ping():
    while True:
        try:
            time.sleep(300)
            response = requests.get(APP_URL)
            print(f"Self-ping sent successfully! Status Code: {response.status_code}")
        except Exception as e:
            print(f"Self-ping error: {e}")

def run_self_ping_in_thread():
    ping_thread = threading.Thread(target=self_ping, daemon=True)
    ping_thread.start()

# ============ TARGET NAMES LIST ============
TARGET_NAMES = [
    "sahajan", 
    "kudkii", 
    "banner"
]

@bot.chat_join_request_handler()
def handle_join_request(message):
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or "User"
        last_name = message.from_user.last_name or ""
        full_name = f"{first_name} {last_name}".strip().lower()
        
        chat_id = message.chat.id
        
        # Agar user ki ID pehle se banned list me saved hai
        if is_user_banned(user_id):
            bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            print(f"Banned user ID matched, auto-dismissed request for ID: {user_id}")
            return

        # Naam match karna
        match_found = any(name.lower() in full_name for name in TARGET_NAMES)

        if match_found:
            ban_user_id(user_id)
            bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            print(f"Target name matched ({full_name}), dismissed request for user {user_id}")
            
    except Exception as e:
        print(f"Error handling join request: {e}")

# ============ START COMMAND (Checking if bot is working) ============
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "User"
    add_user(user_id, first_name)
    
    bot.reply_to(message, "🤖 Bot is working perfectly and online! Join requests filter is active.")

if __name__ == "__main__":
    init_db()
    run_keepalive_in_thread()
    run_self_ping_in_thread()
    print("Bot is starting with allowed_updates for join requests...")
    # SABSE MAIN FIX: Yeh allowed_updates dena zaroori hai tabhi join request aayegi!
    bot.infinity_polling(allowed_updates=['message', 'chat_join_request'])
