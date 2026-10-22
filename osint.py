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
APP_URL = os.environ.get("APP_URL", "https://osint-kblt.onrender.com")  # Render App URL

bot = telebot.TeleBot(BOT_TOKEN)

# ============ DATABASE SETUP ============
def init_db():
    try:
        conn = sqlite3.connect('users.db', timeout=10)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            status TEXT DEFAULT 'active'
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS banned_requests (
            user_id INTEGER PRIMARY KEY
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS target_names (
            name TEXT UNIQUE
        )''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database Init Error: {e}")

def add_user(user_id, first_name):
    try:
        conn = sqlite3.connect('users.db', timeout=10)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (user_id, first_name, status) VALUES (?, ?, 'active')", (user_id, first_name))
        c.execute("UPDATE users SET status = 'active', first_name = ? WHERE user_id = ?", (first_name, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Add User Error: {e}")

def is_user_banned(user_id):
    try:
        conn = sqlite3.connect('users.db', timeout=10)
        c = conn.cursor()
        c.execute("SELECT user_id FROM banned_requests WHERE user_id = ?", (user_id,))
        res = c.fetchone()
        conn.close()
        return res is not None
    except Exception as e:
        print(f"Is User Banned Error: {e}")
        return False

def ban_user_id(user_id):
    try:
        conn = sqlite3.connect('users.db', timeout=10)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO banned_requests (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Ban User ID Error: {e}")

def get_target_names():
    try:
        conn = sqlite3.connect('users.db', timeout=10)
        c = conn.cursor()
        c.execute("SELECT name FROM target_names")
        rows = c.fetchall()
        conn.close()
        return [row[0] for row in rows]
    except Exception as e:
        print(f"Get Target Names Error: {e}")
        return []

# ============ KEEPALIVE SERVER (Render 24/7 ke liye) ============
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running 24/7 successfully!")
    def log_message(self, format, *args):
        pass

def start_keepalive_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        print(f"Keepalive server started on port {port}")
        server.serve_forever()
    except Exception as e:
        print(f"Keepalive Server Error: {e}")

def run_keepalive_in_thread():
    server_thread = threading.Thread(target=start_keepalive_server, daemon=True)
    server_thread.start()

def self_ping():
    time.sleep(10) # Server start hone ka wait karein
    while True:
        try:
            time.sleep(120)  # Har 2 minute baad self-ping karega taaki render sleep na ho
            if APP_URL:
                response = requests.get(APP_URL, timeout=15)
                print(f"Self-ping sent! Status Code: {response.status_code}")
        except Exception as e:
            print(f"Self-ping error: {e}")

def run_self_ping_in_thread():
    ping_thread = threading.Thread(target=self_ping, daemon=True)
    ping_thread.start()

# ============ JOIN REQUEST HANDLER ============
@bot.chat_join_request_handler()
def handle_join_request(message):
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        last_name = message.from_user.last_name or ""
        
        full_name = f"{first_name} {last_name}".strip().lower()
        clean_first = first_name.strip().lower()
        
        chat_id = message.chat.id
        
        if is_user_banned(user_id):
            bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            print(f"Banned user ID matched, auto-dismissed request for ID: {user_id}")
            return

        target_names = get_target_names()
        
        match_found = False
        for t_name in target_names:
            t_clean = t_name.strip().lower()
            if t_clean == clean_first or t_clean == full_name or t_clean in full_name.split():
                match_found = True
                break

        if match_found:
            ban_user_id(user_id)
            bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            print(f"Target name matched exactly ({full_name}), dismissed request for user {user_id}")
            
    except Exception as e:
        print(f"Error handling join request: {e}")

# ============ START COMMAND ============
@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or "User"
        add_user(user_id, first_name)
        
        if user_id == ADMIN_ID:
            bot.reply_to(message, 
                "🤖 Bot is working 24/7!\n\n"
                "👑 **Admin Commands:**\n"
                "👉 `/addlist <name>` - Naya naam list me add karne ke liye\n"
                "👉 `/unlist <name>` - List se naam hatane ke liye\n"
                "👉 `/listnames` - Sabhi saved names dekhne ke liye\n"
                "👉 `/removeid <user_id>` - Banned ID hatane ke liye\n"
                "👉 `/removeid all` - Sabhi banned IDs saaf karne ke liye",
                parse_mode='Markdown'
            )
        else:
            bot.reply_to(message, "🤖 Bot is working perfectly and online! Join requests filter is active.")
    except Exception as e:
        print(f"Start Command Error: {e}")

# ============ ADD TARGET NAME COMMAND (Admin Only) ============
@bot.message_handler(commands=['addlist'])
def add_list_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ **Galat tareeqa!**\nUse aise karein: `/addlist haseri`", parse_mode='Markdown')
        return
    
    new_name = parts[1].strip().lower()
    try:
        conn = sqlite3.connect('users.db', timeout=10)
        c = conn.cursor()
        c.execute("INSERT INTO target_names (name) VALUES (?)", (new_name,))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ **Success!** `{new_name}` ko target list me add kar diya gaya hai.", parse_mode='Markdown')
    except sqlite3.IntegrityError:
        bot.reply_to(message, f"⚠️ Yeh naam (`{new_name}`) pehle se hi list me maujud hai!", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# ============ REMOVE TARGET NAME COMMAND (Admin Only) ============
@bot.message_handler(commands=['unlist'])
def unlist_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ **Galat tareeqa!**\nUse aise karein: `/unlist haseri`", parse_mode='Markdown')
        return
    
    name_to_remove = parts[1].strip().lower()
    try:
        conn = sqlite3.connect('users.db', timeout=10)
        c = conn.cursor()
        c.execute("DELETE FROM target_names WHERE name = ?", (name_to_remove,))
        deleted_rows = c.rowcount
        conn.commit()
        conn.close()
        
        if deleted_rows > 0:
            bot.reply_to(message, f"✅ **Success!** `{name_to_remove}` ko target list se hata diya gaya hai.", parse_mode='Markdown')
        else:
            bot.reply_to(message, f"⚠️ Yeh naam (`{name_to_remove}`) list me nahi mila.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# ============ VIEW TARGET NAMES COMMAND (Admin Only) ============
@bot.message_handler(commands=['listnames'])
def list_names_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        names = get_target_names()
        if not names:
            bot.reply_to(message, "📋 Target list abhi khali hai! Koi bhi naam add nahi hai.")
            return
        
        names_text = "\n".join([f"• `{name}`" for name in names])
        bot.reply_to(message, f"📋 **Total Target Names: ({len(names)})**\n\n{names_text}", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# ============ REMOVE / DELETE BANNED ID COMMAND (Admin Only) ============
@bot.message_handler(commands=['removeid'])
def remove_banned_id_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ **Galat tareeqa!**\n\nUse aise karein:\n👉 `/removeid 123456789` (Ek specific ID hatane ke liye)\n👉 `/removeid all` (Sabhi saved IDs delete karne ke liye)", parse_mode='Markdown')
        return
    
    target = parts[1].strip()
    try:
        conn = sqlite3.connect('users.db', timeout=10)
        c = conn.cursor()
        
        if target.lower() == 'all':
            c.execute("DELETE FROM banned_requests")
            conn.commit()
            conn.close()
            bot.reply_to(message, "✅ **Success!** Database se sabhi saved banned/rejected user IDs ko delete kar diya gaya hai.")
        else:
            user_id_to_remove = int(target)
            c.execute("DELETE FROM banned_requests WHERE user_id = ?", (user_id_to_remove,))
            conn.commit()
            conn.close()
            bot.reply_to(message, f"✅ **Success!** User ID `{user_id_to_remove}` ko banned list se hata diya gaya hai ab wo dobara request bhej payega.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

if __name__ == "__main__":
    init_db()
    run_keepalive_in_thread()
    run_self_ping_in_thread()
    print("Bot is starting 24/7 with error handling & auto-recovery...")
    
    while True:
        try:
            bot.infinity_polling(allowed_updates=['message', 'chat_join_request'], timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"Polling connection error: {e}")
            time.sleep(5)
sers.db', timeout=10)
        c = conn.cursor()
        
        if target.lower() == 'all':
            c.execute("DELETE FROM banned_requests")
            conn.commit()
            conn.close()
            bot.reply_to(message, "✅ **Success!** Database se sabhi saved banned/rejected user IDs ko delete kar diya gaya hai.")
        else:
            user_id_to_remove = int(target)
            c.execute("DELETE FROM banned_requests WHERE user_id = ?", (user_id_to_remove,))
            conn.commit()
            conn.close()
            bot.reply_to(message, f"✅ **Success!** User ID `{user_id_to_remove}` ko banned list se hata diya gaya hai ab wo dobara request bhej payega.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

if __name__ == "__main__":
    init_db()
    run_keepalive_in_thread()
    run_self_ping_in_thread()
    print("Bot is starting 24/7 with error handling & auto-recovery...")
    
    # Infinite loop polling taaki agar network drop ho toh bot crash na ho, balki reconnect kar le
    while True:
        try:
            bot.infinity_polling(allowed_updates=['message', 'chat_join_request'], timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"Polling connection error: {e}")
            time.sleep(5)
sers.db', timeout=10)
        c = conn.cursor()
        
        if target.lower() == 'all':
            c.execute("DELETE FROM banned_requests")
            conn.commit()
            conn.close()
            bot.reply_to(message, "✅ **Success!** Database se sabhi saved banned/rejected user IDs ko delete kar diya gaya hai.")
        else:
            user_id_to_remove = int(target)
            c.execute("DELETE FROM banned_requests WHERE user_id = ?", (user_id_to_remove,))
            conn.commit()
            conn.close()
            bot.reply_to(message, f"✅ **Success!** User ID `{user_id_to_remove}` ko banned list se hata diya gaya hai ab wo dobara request bhej payega.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

if __name__ == "__main__":
    init_db()
    run_keepalive_in_thread()
    run_self_ping_in_thread()
    print("Bot is starting 24/7 with error handling & auto-recovery...")
    
    # Infinite loop polling taaki agar network drop ho toh bot crash na ho, balki reconnect kar le
    while True:
        try:
            bot.infinity_polling(allowed_updates=['message', 'chat_join_request'], timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"Polling connection error: {e}")
            time.sleep(5)
db', timeout=10)
        c = conn.cursor()
        
        if target.lower() == 'all':
            c.execute("DELETE FROM banned_requests")
            conn.commit()
            conn.close()
            bot.reply_to(message, "✅ **Success!** Database se sabhi saved banned/rejected user IDs ko delete kar diya gaya hai.")
        else:
            user_id_to_remove = int(target)
            c.execute("DELETE FROM banned_requests WHERE user_id = ?", (user_id_to_remove,))
            conn.commit()
            conn.close()
            bot.reply_to(message, f"✅ **Success!** User ID `{user_id_to_remove}` ko banned list se hata diya gaya hai ab wo dobara request bhej payega.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

if __name__ == "__main__":
    init_db()
    run_keepalive_in_thread()
    run_self_ping_in_thread()
    print("Bot is starting 24/7 with error handling & auto-recovery...")
    
    # Infinite loop polling taaki agar network drop ho toh bot crash na ho, balki reconnect kar le
    while True:
        try:
            bot.infinity_polling(allowed_updates=['message', 'chat_join_request'], timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"Polling connection error: {e}")
            time.sleep(5)
            
