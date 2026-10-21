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
    # status column add kiya hai active/blocked track karne ke liye
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        status TEXT DEFAULT 'active'
    )''')
    # Reject kiye gaye users ki ID store karne ke liye table
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

def get_all_users():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE status = 'active'")
    users = c.fetchall()
    conn.close()
    return [user[0] for user in users]

def mark_user_blocked(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("UPDATE users SET status = 'blocked' WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# Banned / Rejected User ID functions
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

# ============ SELF-PING BACKGROUND WORKER ============
def self_ping():
    while True:
        try:
            time.sleep(300)  # Har 5 minute baad khud ko ping karega
            response = requests.get(APP_URL)
            print(f"Self-ping sent successfully! Status Code: {response.status_code}")
        except Exception as e:
            print(f"Self-ping error: {e}")

def run_self_ping_in_thread():
    ping_thread = threading.Thread(target=self_ping, daemon=True)
    ping_thread.start()

# ============ AUTO-REJECT JOIN REQUEST HANDLER ============
# Yahan apne target names / keywords dalein jise match karke reject karwana hai
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
        
        # 1. Agar user ki ID pehle se rejected/banned list me saved hai (naam chahe change kar liya ho)
        if is_user_banned(user_id):
            bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            print(f"Banned user ID matched, auto-dismissed request for ID: {user_id}")
            return

        # 2. Naam match karna
        match_found = any(name.lower() in full_name for name in TARGET_NAMES)

        # 3. Agar naam match ho jaye, ya aapko SABHI ki request reject karni hai toh match_found ki jagah True kar sakte hain
        if match_found:
            # Dismiss karne se pehle database me ID save kar lo taaki agli baar naam badalne par bhi pakda jaye
            ban_user_id(user_id)
            
            # Join request reject (dismiss) kar do
            bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            print(f"Target name matched ({full_name}), dismissed request for user {user_id}")
            
    except Exception as e:
        print(f"Error handling join request: {e}")

# ============ START COMMAND ============
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "User"
    add_user(user_id, first_name)
    
    if user_id == ADMIN_ID:
        bot.reply_to(message, "Hello Admin! ✅\n\nCommands available:\n👉 `/broadcast` - Sabhi ko message bhejne ke liye\n👉 `/status` - Database & Storage stats dekhne ke liye", parse_mode='Markdown')
    else:
        bot.reply_to(message, "👋 Bot is active and filtering channel join requests!")

# ============ STATUS / STORAGE COMMAND (Admin Only) ============
@bot.message_handler(commands=['status', 'stats'])
def status_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        if os.path.exists('users.db'):
            file_size_bytes = os.path.getsize('users.db')
            file_size_kb = file_size_bytes / 1024
        else:
            file_size_kb = 0
        
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE status = 'active'")
        active_users = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE status = 'blocked'")
        blocked_users = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM banned_requests")
        total_rejected = c.fetchone()[0]
        
        conn.close()
        
        status_text = (
            f"📊 **Bot & Database Status:**\n\n"
            f"👥 Total Users: `{total_users}`\n"
            f"🟢 Active Users: `{active_users}`\n"
            f"❌ Blocked Bot: `{blocked_users}`\n"
            f"🚫 Total Auto-Rejected Requests: `{total_rejected}`\n"
            f"💾 Database Size: `{file_size_kb:.2f} KB`"
        )
        bot.reply_to(message, status_text, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"Error getting status: {e}")

# ============ ADVANCED BROADCAST SYSTEM (Admin Only) ============
@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    msg = bot.reply_to(message, "📢 **Broadcast Mode On!**\n\nAb aap jo bhi bhejna chahte hain (Text, Image + Caption, Video, Link), wo yahan forward ya send karein:")
    bot.register_next_step_handler(msg, process_broadcast)

def process_broadcast(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    users = get_all_users()
    success = 0
    failed = 0
    
    status_msg = bot.reply_to(message, f"📢 Broadcasting to {len(users)} active users... Please wait ⏳")
    
    for user_id in users:
        try:
            bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
            success += 1
        except Exception as e:
            failed += 1
            mark_user_blocked(user_id)
            print(f"Failed to send broadcast to {user_id}: {e}")
            
    bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        text=f"✅ **Broadcast Completed!**\n\nSuccessfully sent: `{success}`\nFailed / Blocked: `{failed}`",
        parse_mode='Markdown'
    )

if __name__ == "__main__":
    init_db()
    run_keepalive_in_thread()
    run_self_ping_in_thread()
    print("Bot is starting with Auto-Reject Join Requests, Self-Ping & Broadcast system...")
    bot.infinity_polling()
