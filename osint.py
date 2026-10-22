
import os
import time
import sqlite3
import threading
import logging
import requests
import telebot
import html

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============ CONFIGURATION ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID = 5989928539
APP_URL = os.environ.get(
    "APP_URL",
    "https://osint-s7db.onrender.com"
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

bot = telebot.TeleBot(
    BOT_TOKEN,
    threaded=True,
    num_threads=4
)


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

        # Stores the name observed when the matching request was declined.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_user_details (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                full_name TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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


def save_banned_user_details(user_id, first_name, last_name):
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    full_name = f"{first_name} {last_name}".strip()

    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO banned_user_details (
                user_id,
                first_name,
                last_name,
                full_name,
                updated_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                full_name = excluded.full_name,
                updated_at = CURRENT_TIMESTAMP
        """, (
            user_id,
            first_name,
            last_name,
            full_name
        ))
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
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def start_health_server():
    try:
        server = ThreadingHTTPServer(
            ("0.0.0.0", PORT),
            HealthHandler
        )
        server.daemon_threads = True
        logger.info("Health server listening on port %s", PORT)
        server.serve_forever()
    except Exception:
        logger.exception("Health server failed to start")


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

    session.mount(
        "https://",
        HTTPAdapter(max_retries=retry)
    )

    def worker():
        time.sleep(20)

        while True:
            try:
                response = session.get(
                    APP_URL + "/health",
                    timeout=15
                )
                logger.info(
                    "Self-ping: HTTP %s",
                    response.status_code
                )
                response.close()
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

        # Capture the name currently visible in this join request.
        first_name = (
            request.from_user.first_name or ""
        ).strip()

        last_name = (
            request.from_user.last_name or ""
        ).strip()

        full_name = f"{first_name} {last_name}".strip()

        # Previously recorded ID: reject regardless of current name.
        if is_user_banned(user_id):
            bot.decline_chat_join_request(
                chat_id=chat_id,
                user_id=user_id
            )

            # Refresh details with the latest name observed.
            save_banned_user_details(
                user_id,
                first_name,
                last_name
            )

            logger.info(
                "Previously banned ID declined again: %s (%s)",
                user_id,
                full_name
            )
            return

        # Match against the configured target names.
        target_names = get_target_names()

        matched = any(
            name.strip().casefold() == first_name.casefold()
            or name.strip().casefold() == full_name.casefold()
            or name.strip().casefold()
                in [part.casefold() for part in full_name.split()]
            for name in target_names
        )

        if matched:
            # Save only after Telegram successfully declines the request.
            bot.decline_chat_join_request(
                chat_id=chat_id,
                user_id=user_id
            )

            ban_user_id(user_id)

            save_banned_user_details(
                user_id,
                first_name,
                last_name
            )

            logger.info(
                "Name matched; declined and saved ID %s (%s)",
                user_id,
                full_name
            )

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
                "/listnames - Show saved target names\n"
                "/removeid <user_id> - Remove one banned ID\n"
                "/removeid all - Clear all banned IDs\n"
                "/userlist - Show saved IDs and names\n"
                "/userdetails - Show all saved user details"
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

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "Usage: /addlist name")
        return

    name = parts[1].strip().lower()
    conn = get_db()

    try:
        conn.execute(
            "INSERT INTO target_names (name) VALUES (?)",
            (name,)
        )
        conn.commit()

        bot.reply_to(
            message,
            f"✅ Added target name: {html.escape(name)}",
            parse_mode="HTML"
        )

    except sqlite3.IntegrityError:
        bot.reply_to(
            message,
            "⚠️ This name is already in the target list."
        )

    except Exception:
        logger.exception("Add-list command failed")
        bot.reply_to(
            message,
            "❌ Database error. Check Render logs."
        )

    finally:
        conn.close()


# ============ REMOVE TARGET NAME ============
@bot.message_handler(commands=["unlist"])
def unlist_command(message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "Usage: /unlist name")
        return

    name = parts[1].strip().lower()
    conn = get_db()

    try:
        cursor = conn.execute(
            "DELETE FROM target_names WHERE name = ?",
            (name,)
        )
        conn.commit()

        if cursor.rowcount:
            bot.reply_to(
                message,
                f"✅ Removed target name: {html.escape(name)}",
                parse_mode="HTML"
            )
        else:
            bot.reply_to(
                message,
                "⚠️ Name not found in target list."
            )

    except Exception:
        logger.exception("Unlist command failed")
        bot.reply_to(
            message,
            "❌ Database error. Check Render logs."
        )

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

        text = (
            f"📋 Target Names ({len(names)}):\n\n"
            + "\n".join(f"• {name}" for name in names)
        )

        # Split safely under Telegram's message-size limit.
        for start in range(0, len(text), 3500):
            bot.reply_to(
                message,
                text[start:start + 3500]
            )

    except Exception:
        logger.exception("List-names command failed")
        bot.reply_to(
            message,
            "❌ Could not read the target list. Check logs."
        )


# ============ REMOVE / CLEAR BANNED IDs ============
@bot.message_handler(commands=["removeid"])
def remove_banned_id_command(message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = (message.text or "").split(maxsplit=1)

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
            cursor = conn.execute(
                "DELETE FROM banned_requests"
            )
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
                bot.reply_to(
                    message,
                    f"✅ Removed banned ID: {user_id}"
                )
            else:
                bot.reply_to(
                    message,
                    "⚠️ ID was not found in the banned list."
                )

    except ValueError:
        bot.reply_to(
            message,
            "⚠️ ID must be a number or the word 'all'."
        )

    except Exception:
        logger.exception("Remove-ID command failed")
        bot.reply_to(
            message,
            "❌ Database error. Check Render logs."
        )

    finally:
        conn.close()


# ============ USER LIST / USER DETAILS ============
def send_saved_users(message, detailed=False):
    if message.from_user.id != ADMIN_ID:
        return

    conn = get_db()

    try:
        rows = conn.execute("""
            SELECT
                b.user_id,
                d.first_name,
                d.last_name,
                d.full_name,
                d.updated_at
            FROM banned_requests b
            LEFT JOIN banned_user_details d
                ON b.user_id = d.user_id
            ORDER BY b.user_id DESC
        """).fetchall()

        if not rows:
            bot.reply_to(
                message,
                "📋 No saved user IDs found."
            )
            return

        title = (
            "👥 <b>All User Details</b>"
            if detailed
            else "📋 <b>Saved User List</b>"
        )

        bot.reply_to(
            message,
            f"{title}\n"
            f"📊 Total Users: <b>{len(rows)}</b>",
            parse_mode="HTML"
        )

        # Send multiple smaller messages to avoid Telegram limits.
        chunks = []
        current = ""

        for user_id, first_name, last_name, full_name, updated_at in rows:
            first_name = html.escape(first_name or "Unknown")
            last_name = html.escape(last_name or "Not available")
            full_name = html.escape(full_name or "Name not recorded")
            updated_at = html.escape(updated_at or "Unknown")

            if detailed:
                item = (
                    "👤 <b>User Details</b>\n"
                    f"📝 Name: {full_name}\n"
                    f"🔹 First Name: {first_name}\n"
                    f"🔹 Last Name: {last_name}\n"
                    f"🆔 ID: <code>{user_id}</code>\n"
                    f"🕒 Updated: {updated_at}\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                )
            else:
                item = (
                    f"🆔 <code>{user_id}</code>  |  "
                    f"👤 {full_name}\n"
                )

            if len(current) + len(item) > 3500:
                chunks.append(current)
                current = ""

            current += item

        if current:
            chunks.append(current)

        for chunk in chunks:
            bot.send_message(
                message.chat.id,
                chunk,
                parse_mode="HTML"
            )

    except Exception:
        logger.exception("Saved-user command failed")
        bot.reply_to(
            message,
            "❌ Could not retrieve user details. Check Render logs."
        )

    finally:
        conn.close()


@bot.message_handler(commands=["userlist"])
def user_list_command(message):
    send_saved_users(message, detailed=False)


@bot.message_handler(commands=["userdetails"])
def user_details_command(message):
    send_saved_users(message, detailed=True)


# ============ MAIN ============
def main():
    init_db()

    threading.Thread(
        target=start_health_server,
        daemon=True,
        name="health-server"
    ).start()

    start_self_ping()
    logger.info("Starting Telegram polling")

    while True:
        try:
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=25,
                allowed_updates=[
                    "message",
                    "chat_join_request"
                ],
                skip_pending=False
            )

        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            break

        except Exception:
            logger.exception(
                "Telegram polling failed; retrying in 5 seconds"
            )
            time.sleep(5)


if __name__ == "__main__":
    main()
    
