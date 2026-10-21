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
    # Dynamic Target Names store karne ke liye table
    c.execute('''CREATE TABLE IF NOT EXISTS target_names (
        name TEXT UNIQUE
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

# Database se dynamic target names fetch karne ke liye function
def get_target_names():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT name FROM target_names")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

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

# ============ JOIN REQUEST HANDLER ============
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

        # Database se target names ki list nikal kar check karna
        target_names = get_target_names()
        match_found = any(name.lower() in full_name for name in target_names)

        if match_found:
            ban_user_id(user_id)
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
        bot.reply_to(message, 
            "🤖 Bot is working!\n\n"
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

# ============ ADD TARGET NAME COMMAND (Admin Only) ============
@bot.message_handler(commands=['addlist'])
def add_list_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ **Galat tareeqa!**\nUse aise karein: `/addlist raju`", parse_mode='Markdown')
        return
    
    new_name = parts[1].strip().lower()
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO target_names (name) VALUES (?)", (new_name,))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ **Success!** `{new_name}` ko target list me add kar diya gaya hai.", parse_mode='Markdown')
    except sqlite3.IntegrityError:
        conn.close()
        bot.reply_to(message, f"⚠️ Yeh naam (`{new_name}`) pehle se hi list me maujud hai!", parse_mode='Markdown')
    except Exception as e:
        conn.close()
        bot.reply_to(message, f"❌ Error: {e}")

# ============ REMOVE TARGET NAME COMMAND (Admin Only) ============
@bot.message_handler(commands=['unlist'])
def unlist_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ **Galat tareeqa!**\nUse aise karein: `/unlist raju`", parse_mode='Markdown')
        return
    
    name_to_remove = parts[1].strip().lower()
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute("DELETE FROM target_names WHERE name = ?", (name_to_remove,))
    deleted_rows = c.rowcount
    conn.commit()
    conn.close()
    
    if deleted_rows > 0:
        bot.reply_to(message, f"✅ **Success!** `{name_to_remove}` ko target list se hata diya gaya hai.", parse_mode='Markdown')
    else:
        bot.reply_to(message, f"⚠️ Yeh naam (`{name_to_remove}`) list me nahi mila.", parse_mode='Markdown')

# ============ VIEW TARGET NAMES COMMAND (Admin Only) ============
@bot.message_handler(commands=['listnames'])
def list_names_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    names = get_target_names()
    if not names:
        bot.reply_to(message, "📋 Target list abhi khali hai! Koi bhi naam add nahi hai.")
        return
    
    names_text = "\n".join([f"• `{name}`" for name in names])
    bot.reply_to(message, f"📋 **Total Target Names: ({len(names)})**\n\n{names_text}", parse_mode='Markdown')

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
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    try:
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
        conn.close()
        bot.reply_to(message, f"❌ Error: {e}")

if __name__ == "__main__":
    init_db()
    run_keepalive_in_thread()
    run_self_ping_in_thread()
    print("Bot is starting with dynamic target list commands...")
    bot.infinity_polling(allowed_updates=['message', 'chat_join_request'])
