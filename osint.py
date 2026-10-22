import os
import time
import sqlite3
import threading
import logging
import requests
import telebot

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============ CONFIGURATION ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID = 5989928539
APP_URL = os.environ.get(
    "APP_URL", "https://osint-s7db.onrender.com"
).strip().rstrip("/")
PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = os.environ.get("DB_PATH", "users.db")

if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
    raise RuntimeError(
        "BOT_TOKEN missing. Render Dashboard > Environment mein BOT_TOKEN set karein."
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("osint-bot")

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=4)


# ============ DATABASE HELPERS ============
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db():
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                status TEXT DEFAULT 'active'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_requests (
                user_id INTEGER PRIMARY KEY
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS target_names (
                name TEXT UNIQUE
            )
        """)
        conn.commit()
        logger.info("Database initialized: %s", DB_PATH)
    finally:
        conn.close()


def add_user(user_id, first_name):
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO users (user_id, first_name, status)
            VALUES (?, ?, 'active')
            ON CONFLICT(user_id) DO UPDATE SET
                first_name = excluded.first_name,
                status = 'active'
        """, (user_id, first_name))
        conn.commit()
    finally:
        conn.close()


def is_user_banned(user_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM banned_requests WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def ban_user_id(user_id):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO banned_requests (user_id) VALUES (?)",
            (user_id,)
        )
        conn.commit()
    finally:
        conn.close()


def get_target_names():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT name FROM target_names ORDER BY name"
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


# ============ HEALTH-CHECK WEB SERVER ============
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            return

        body = b"Bot service is running"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def start_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.daemon_threads = True
    logger.info("Health server listening on port %s", PORT)
    server.serve_forever()


# ============ SELF-PING EVERY 3 MINUTES ============
def start_self_ping():
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))

    def worker():
        # Allow the web server time to start.
        time.sleep(15)
        while True:
            try:
                response = session.get(APP_URL + "/health", timeout=15)
                logger.info("Self-ping: HTTP %s", response.status_code)
            except requests.RequestException:
                logger.exception("Self-ping failed")
            time.sleep(180)

    threading.Thread(
        target=worker,
        daemon=True,
        name="self-ping"
    ).start()


# ============ JOIN REQUEST HANDLER ============
@bot.chat_join_request_handler()
def handle_join_request(request):
    try:
        user_id = request.from_user.id
        chat_id = request.chat.id

        first_name = (request.from_user.first_name or "").strip().lower()
        last_name = (request.from_user.last_name or "").strip().lower()
        full_name = f"{first_name} {last_name}".strip()

        if is_user_banned(user_id):
            bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            logger.info("Declined previously banned ID: %s", user_id)
            return

        target_names = get_target_names()
        matched = any(
            (name.strip().lower() == first_name)
            or (name.strip().lower() == full_name)
            or (name.strip().lower() in full_name.split())
            for name in target_names
        )

        if matched:
            # Only save the ID after Telegram successfully declines the request.
            bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            ban_user_id(user_id)
            logger.info("Target name matched; request declined for ID %s", user_id)

    except Exception:
        logger.exception("Join-request handler failed")


# ============ START COMMAND ============
@bot.message_handler(commands=["start"])
def send_welcome(message):
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name or "User"
        add_user(user_id, first_name)

        if user_id == ADMIN_ID:
            text = (
                "🤖 Bot is running!\n\n"
                "👑 Admin Commands:\n"
                "/addlist <name> - Add a target name\n"
                "/unlist <name> - Remove a target name\n"
                "/listnames - Show saved names\n"
                "/removeid <user_id> - Remove one banned ID\n"
                "/removeid all - Clear all banned IDs"
            )
        else:
            text = (
                "🤖 Bot is online!\n"
                "Join-request filtering is active."
            )

        bot.reply_to(message, text)
    except Exception:
        logger.exception("Start command failed")


# ============ ADD TARGET NAME ============
@bot.message_handler(commands=["addlist"])
def add_list_command(message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "Usage: /addlist name")
        return

    name = parts[1].strip().lower()
    conn = get_db()
    try:
        conn.execute("INSERT INTO target_names (name) VALUES (?)", (name,))
        conn.commit()
        bot.reply_to(message, f"✅ Added target name: {name}")
    except sqlite3.IntegrityError:
        bot.reply_to(message, "⚠️ This name is already in the target list.")
    except Exception:
        logger.exception("Add-list command failed")
        bot.reply_to(message, "❌ Database error. Check Render logs.")
    finally:
        conn.close()


# ============ REMOVE TARGET NAME ============
@bot.message_handler(commands=["unlist"])
def unlist_command(message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "Usage: /unlist name")
        return

    name = parts[1].strip().lower()
    conn = get_db()
    try:
        cursor = conn.execute(
            "DELETE FROM target_names WHERE name = ?", (name,)
        )
        conn.commit()
        if cursor.rowcount:
            bot.reply_to(message, f"✅ Removed target name: {name}")
        else:
            bot.reply_to(message, "⚠️ Name not found in target list.")
    except Exception:
        logger.exception("Unlist command failed")
        bot.reply_to(message, "❌ Database error. Check Render logs.")
    finally:
        conn.close()


# ============ VIEW TARGET NAMES ============
@bot.message_handler(commands=["listnames"])
def list_names_command(message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        names = get_target_names()
        if not names:
            bot.reply_to(message, "📋 Target list is empty.")
            return

        text = f"📋 Target Names ({len(names)}):\n\n" + "\n".join(
            f"• {name}" for name in names
        )
        # Telegram messages have a maximum length; split long lists safely.
        for start in range(0, len(text), 3500):
            bot.reply_to(message, text[start:start + 3500])
    except Exception:
        logger.exception("List-names command failed")
        bot.reply_to(message, "❌ Could not read the target list. Check logs.")


# ============ REMOVE / CLEAR BANNED IDs ============
@bot.message_handler(commands=["removeid"])
def remove_banned_id_command(message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(
            message,
            "Usage:\n/removeid 123456789\n/removeid all"
        )
        return

    target = parts[1].strip()
    conn = get_db()
    try:
        if target.lower() == "all":
            cursor = conn.execute("DELETE FROM banned_requests")
            conn.commit()
            bot.reply_to(
                message,
                f"✅ Cleared all banned IDs. Removed: {cursor.rowcount}"
            )
        else:
            user_id = int(target)
            cursor = conn.execute(
                "DELETE FROM banned_requests WHERE user_id = ?",
                (user_id,)
            )
            conn.commit()
            if cursor.rowcount:
                bot.reply_to(message, f"✅ Removed banned ID: {user_id}")
            else:
                bot.reply_to(message, "⚠️ ID was not found in the banned list.")
    except ValueError:
        bot.reply_to(message, "⚠️ ID must be a number or the word 'all'.")
    except Exception:
        logger.exception("Remove-ID command failed")
        bot.reply_to(message, "❌ Database error. Check Render logs.")
    finally:
        conn.close()


# ============ MAIN ============
def main():
    init_db()

    # Start health server in a separate daemon thread.
    threading.Thread(
        target=start_health_server,
        daemon=True,
        name="health-server"
    ).start()

    start_self_ping()
    logger.info("Starting Telegram polling")

    # Retry polling after network/API failures.
    while True:
        try:
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=25,
                allowed_updates=["message", "chat_join_request"],
                skip_pending=False
            )
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            break
        except Exception:
            logger.exception("Telegram polling failed; retrying in 5 seconds")
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    
