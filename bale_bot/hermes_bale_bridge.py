import time
import urllib.request
import json
import urllib.parse
import sqlite3
import os
from openai import OpenAI

TOKEN = "2119722542:***"
BASE_URL = f"https://tapi.bale.ai/bot{TOKEN}"
DB_PATH = "/data/workspace/bale_bot.db"

# ---------------------------------------------------------
# Database Setup
# ---------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Projects table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT UNIQUE,
            description TEXT,
            github_repo TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Sessions / Conversations table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            project_id INTEGER,
            title TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    ''')
    
    # Messages History
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            role TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    ''')
    
    # User Active Context State
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_state (
            user_id INTEGER PRIMARY KEY,
            active_project_id INTEGER,
            active_session_id INTEGER
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# ---------------------------------------------------------
# OpenAI Client (9router)
# ---------------------------------------------------------
client = OpenAI(
    base_url='https://9router-production-d6aa.up.railway.app/v1',
    api_key='«redacted:sk-…»',
)

# ---------------------------------------------------------
# Helper DB functions
# ---------------------------------------------------------
def get_user_state(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT active_project_id, active_session_id FROM user_state WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"project_id": row[0], "session_id": row[1]}
    return {"project_id": None, "session_id": None}

def set_user_state(user_id, project_id=None, session_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_state (user_id, active_project_id, active_session_id)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            active_project_id = COALESCE(excluded.active_project_id, user_state.active_project_id),
            active_session_id = COALESCE(excluded.active_session_id, user_state.active_session_id)
    ''', (user_id, project_id, session_id))
    conn.commit()
    conn.close()

def create_project(user_id, name, description="", github_repo=""):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO projects (user_id, name, description, github_repo) VALUES (?, ?, ?, ?)",
                       (user_id, name, description, github_repo))
        project_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return project_id
    except sqlite3.IntegrityError:
        conn.close()
        return None

def get_projects(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, github_repo FROM projects WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def create_session(user_id, project_id, title="گفتگوی جدید"):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO sessions (user_id, project_id, title) VALUES (?, ?, ?)", (user_id, project_id, title))
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return session_id

def get_sessions(user_id, project_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if project_id:
        cursor.execute("SELECT id, title, created_at FROM sessions WHERE user_id = ? AND project_id = ? ORDER BY id DESC", (user_id, project_id))
    else:
        cursor.execute("SELECT id, title, created_at FROM sessions WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_message(session_id, role, content):
    if not session_id:
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
    conn.commit()
    conn.close()

def get_chat_history(session_id, limit=10):
    if not session_id:
        return []
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?", (session_id, limit))
    rows = cursor.fetchall()
    conn.close()
    history = []
    for role, content in reversed(rows):
        history.append({"role": role, "content": content})
    return history

# ---------------------------------------------------------
# Bale API Communication
# ---------------------------------------------------------
def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/sendMessage", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"Error sending message: {e}")
        return None

def get_9router_response(user_text, user_name, session_id, project_info=None):
    try:
        history = get_chat_history(session_id, limit=10)
        
        system_prompt = f"You are Hermes Agent, talking to Justin (حمیدرضا) in Bale messenger. He is a Next.js developer and manager of NoFap and ice-center.ir. Be concise, friendly, helpful, and reply in Persian."
        if project_info:
            system_prompt += f"\nActive Project Context: Name='{project_info['name']}', GitHub='{project_info['github_repo']}'."

        messages = [{"role": "system", "content": system_prompt}] + history
        if not history or history[-1]["content"] != user_text:
            messages.append({"role": "user", "content": user_text})

        response = client.chat.completions.create(
            model="my-combo",
            messages=messages,
            timeout=25
        )
        bot_reply = response.choices[0].message.content
        
        # Save to history
        add_message(session_id, "user", user_text)
        add_message(session_id, "assistant", bot_reply)
        
        return bot_reply
    except Exception as e:
        print(f"9router API Error: {e}")
        return f"سلام {user_name} عزیز! پیام شما دریافت شد. (خطا در 9router: {str(e)[:60]})"

def get_updates(offset=None):
    url = f"{BASE_URL}/getUpdates?timeout=30"
    if offset:
        url += f"&offset={offset}"
    try:
        with urllib.request.urlopen(url, timeout=35) as resp:
            data = json.loads(resp.read().decode())
            return data.get("result", [])
    except Exception as e:
        return []

# ---------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------
def handle_command(chat_id, user_id, user_name, text):
    parts = text.split(" ", 2)
    cmd = parts[0].lower()

    if cmd == "/start" or cmd == "/help":
        menu = (
            f"سلام {user_name} عزیز! 🚀\n\n"
            "ربات پیشرفته هرمس با قابلیت مدیریت پروژه و گفتگوها آماده است.\n\n"
            "📋 **دستورات مدیریت پروژه:**\n"
            "🔹 `/newproject <نام_پروژه> [لینک_گیتهاب]` - تعریف پروژه جدید\n"
            "🔹 `/projects` - لیست پروژه‌های شما\n"
            "🔹 `/useproject <id>` - انتخاب پروژه فعال\n\n"
            "💬 **دستورات مدیریت گفتگو:**\n"
            "🔹 `/newchat <عنوان>` - شروع یک گفتگوی جدید در پروژه فعال\n"
            "🔹 `/chats` - لیست گفتگوهای پروژه فعال\n"
            "🔹 `/usechat <id>` - سوییچ به یک گفتگوی خاص\n"
            "🔹 `/status` - مشاهده وضعیت پروژه و گفتگوی فعال\n"
        )
        send_message(chat_id, menu)
        return True

    elif cmd == "/newproject":
        if len(parts) < 2:
            send_message(chat_id, "⚠️ لطفا نام پروژه را وارد کنید:\nمثال: `/newproject ice-center https://github.com/user/ice-center`")
            return True
        p_name = parts[1]
        p_repo = parts[2] if len(parts) > 2 else ""
        p_id = create_project(user_id, p_name, github_repo=p_repo)
        if p_id:
            s_id = create_session(user_id, p_id, f"گفتگوی اصلی {p_name}")
            set_user_state(user_id, project_id=p_id, session_id=s_id)
            send_message(chat_id, f"✅ پروژه **{p_name}** با موفقیت ساخته شد و به عنوان پروژه فعال انتخاب گردید!\nگفتگوی فعال جدید ایجاد شد (ID: {s_id}).")
        else:
            send_message(chat_id, f"❌ پروژه‌ای با نام {p_name} قبلاً ساخته شده است.")
        return True

    elif cmd == "/projects":
        projs = get_projects(user_id)
        if not projs:
            send_message(chat_id, "هیچ پروژه‌ای تعریف نشده است. با `/newproject` یکی بسازید.")
            return True
        msg = "📁 **پروژه‌های شما:**\n\n"
        for pid, pname, prepo in projs:
            repo_str = f" ({prepo})" if prepo else ""
            msg += f"• ID: `{pid}` | **{pname}**{repo_str}\n  دستور انتخاب: `/useproject {pid}`\n\n"
        send_message(chat_id, msg)
        return True

    elif cmd == "/useproject":
        if len(parts) < 2 or not parts[1].isdigit():
            send_message(chat_id, "⚠️ لطفا ID پروژه را وارد کنید. مثال: `/useproject 1`")
            return True
        pid = int(parts[1])
        # get project sessions
        sessions = get_sessions(user_id, pid)
        if not sessions:
            sid = create_session(user_id, pid, "گفتگوی عمومی")
        else:
            sid = sessions[0][0]
        set_user_state(user_id, project_id=pid, session_id=sid)
        send_message(chat_id, f"🔄 پروژه فعال روی ID `{pid}` تنظیم شد. گفتگوی فعال: `{sid}`.")
        return True

    elif cmd == "/newchat":
        state = get_user_state(user_id)
        pid = state["project_id"]
        title = parts[1] if len(parts) > 1 else "گفتگوی جدید"
        sid = create_session(user_id, pid, title)
        set_user_state(user_id, session_id=sid)
        send_message(chat_id, f"💬 گفتگوی جدید **{title}** (ID: `{sid}`) ایجاد شد و فعال گردید.")
        return True

    elif cmd == "/chats":
        state = get_user_state(user_id)
        pid = state["project_id"]
        sessions = get_sessions(user_id, pid)
        if not sessions:
            send_message(chat_id, "هیچ گفتگویی ثبت نشده است.")
            return True
        msg = f"💬 **گفتگوهای پروژه جاری:**\n\n"
        for sid, stitle, sdate in sessions:
            msg += f"• ID: `{sid}` | **{stitle}** ({sdate[:10]})\n  انتخاب: `/usechat {sid}`\n\n"
        send_message(chat_id, msg)
        return True

    elif cmd == "/usechat":
        if len(parts) < 2 or not parts[1].isdigit():
            send_message(chat_id, "⚠️ لطفا ID گفتگو را وارد کنید. مثال: `/usechat 2`")
            return True
        sid = int(parts[1])
        set_user_state(user_id, session_id=sid)
        send_message(chat_id, f"💬 گفتگوی فعال به ID `{sid}` تغییر یافت.")
        return True

    elif cmd == "/status":
        state = get_user_state(user_id)
        send_message(chat_id, f"📊 **وضعیت کنونی شما:**\n\n• پروژه فعال ID: `{state['project_id'] or 'انتخاب نشده'}`\n• گفتگوی فعال ID: `{state['session_id'] or 'انتخاب نشده'}`")
        return True

    return False

# ---------------------------------------------------------
# Main Loop
# ---------------------------------------------------------
def main():
    print("Hermes Advanced 9router Bale Bridge started...", flush=True)
    offset = None
    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                update_id = update["update_id"]
                offset = update_id + 1
                
                message = update.get("message")
                if not message:
                    continue
                
                chat_id = message["chat"]["id"]
                user_id = message["from"]["id"]
                text = message.get("text", "")
                user_name = message["from"].get("first_name", "جاستین")
                
                print(f"Received from {user_name} ({chat_id}): {text}", flush=True)
                
                # Check commands
                if text.startswith("/"):
                    if handle_command(chat_id, user_id, user_name, text):
                        continue
                
                # Normal AI Chat with History and Project Context
                state = get_user_state(user_id)
                session_id = state["session_id"]
                
                if not session_id:
                    # Auto create default session
                    pid = state["project_id"] or create_project(user_id, "General", "General Project")
                    session_id = create_session(user_id, pid, "گفتگوی عمومی")
                    set_user_state(user_id, project_id=pid, session_id=session_id)

                # Fetch project details for prompt context
                project_info = None
                if state["project_id"]:
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("SELECT name, github_repo FROM projects WHERE id = ?", (state["project_id"],))
                    p_row = c.fetchone()
                    conn.close()
                    if p_row:
                        project_info = {"name": p_row[0], "github_repo": p_row[1]}

                reply = get_9router_response(text, user_name, session_id, project_info)
                send_message(chat_id, reply)

        except Exception as e:
            print(f"Polling loop error: {e}", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
