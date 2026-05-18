from flask import Flask, request, redirect, session, url_for, render_template_string, flash
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import requests
import datetime
import os

app = Flask(__name__)
app.secret_key = "change_this_secret_key"

VERIFY_TOKEN = "my_verify_token_123"

WHATSAPP_ACCESS_TOKEN = "PASTE_YOUR_ACCESS_TOKEN_HERE"
WHATSAPP_PHONE_NUMBER_ID = "1140876945770564"
GRAPH_API_VERSION = "v25.0"

DB_PATH = "crm.db"


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password_hash TEXT,
        role TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        phone TEXT UNIQUE,
        assigned_sa_id INTEGER,
        source TEXT,
        status TEXT DEFAULT 'new',
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        direction TEXT,
        message TEXT,
        created_at TEXT
    )
    """)

    conn.commit()

    if cur.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0:
        cur.execute("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)",
                    ("Admin", "admin@test.com", generate_password_hash("123456"), "admin"))
        cur.execute("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)",
                    ("Annelie", "annelie@test.com", generate_password_hash("123456"), "sa"))
        cur.execute("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)",
                    ("SA Test", "sa@test.com", generate_password_hash("123456"), "sa"))
        conn.commit()

    conn.close()


def current_user():
    if "user_id" not in session:
        return None
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()
    return user


def save_message(customer_id, direction, message):
    conn = db()
    conn.execute(
        "INSERT INTO messages (customer_id,direction,message,created_at) VALUES (?,?,?,?)",
        (customer_id, direction, message, now())
    )
    conn.commit()
    conn.close()


def get_or_create_customer(phone, name=None):
    conn = db()
    customer = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()

    if customer:
        conn.close()
        return customer

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO customers (name,phone,source,created_at) VALUES (?,?,?,?)",
        (name or phone, phone, "WhatsApp Incoming", now())
    )
    conn.commit()

    customer = conn.execute("SELECT * FROM customers WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return customer


def send_whatsapp(to_phone, text):
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text}
    }

    r = requests.post(url, headers=headers, json=payload, timeout=20)

    if r.status_code >= 400:
        raise Exception(r.text)

    return r.json()


@app.route("/")
def home():
    if "user_id" not in session:
        return redirect("/login")
    return redirect("/customers")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect("/customers")

        flash("Email 或密码错误")

    return render_template_string(LOGIN_HTML)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/customers")
def customers():
    user = current_user()
    if not user:
        return redirect("/login")

    conn = db()

    if user["role"] == "admin":
        rows = conn.execute("""
            SELECT c.*, u.name AS sa_name
            FROM customers c
            LEFT JOIN users u ON c.assigned_sa_id = u.id
            ORDER BY c.id DESC
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT c.*, u.name AS sa_name
            FROM customers c
            LEFT JOIN users u ON c.assigned_sa_id = u.id
            WHERE c.assigned_sa_id = ?
            ORDER BY c.id DESC
        """, (user["id"],)).fetchall()

    conn.close()
    return render_template_string(CUSTOMERS_HTML, user=user, customers=rows)


@app.route("/chat/<int:customer_id>", methods=["GET", "POST"])
def chat(customer_id):
    user = current_user()
    if not user:
        return redirect("/login")

    conn = db()
    customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()

    if not customer:
        conn.close()
        return "Customer not found", 404

    if user["role"] != "admin" and customer["assigned_sa_id"] != user["id"]:
        conn.close()
        return "你没有权限查看这个客户", 403

    if request.method == "POST":
        text = request.form.get("message", "").strip()

        if text:
            try:
                send_whatsapp(customer["phone"], text)
                save_message(customer_id, "out", text)
                flash("已发送")
            except Exception as e:
                flash("发送失败：" + str(e))

        conn.close()
        return redirect(url_for("chat", customer_id=customer_id))

    messages = conn.execute(
        "SELECT * FROM messages WHERE customer_id=? ORDER BY id ASC",
        (customer_id,)
    ).fetchall()

    conn.close()
    return render_template_string(CHAT_HTML, user=user, customer=customer, messages=messages)


@app.route("/admin/assign", methods=["GET", "POST"])
def assign():
    user = current_user()
    if not user:
        return redirect("/login")

    if user["role"] != "admin":
        return "Only admin can assign customers", 403

    conn = db()

    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        sa_id = request.form.get("sa_id")

        conn.execute(
            "UPDATE customers SET assigned_sa_id=? WHERE id=?",
            (sa_id, customer_id)
        )
        conn.commit()
        conn.close()

        flash("已分配")
        return redirect("/admin/assign")

    customers = conn.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()
    sas = conn.execute("SELECT * FROM users WHERE role='sa' ORDER BY name ASC").fetchall()

    conn.close()
    return render_template_string(ASSIGN_HTML, user=user, customers=customers, sas=sas)


@app.route("/webhook", methods=["GET", "POST"])
def webhook():

    # VERIFY WEBHOOK
    if request.method == "GET":
        verify_token = "my_verify_token_123"

        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode and token:
            if mode == "subscribe" and token == verify_token:
                return challenge, 200
            else:
                return "Verification token mismatch", 403

    # RECEIVE MESSAGE
    if request.method == "POST":

        data = request.get_json()

        print("Webhook received:")
        print(data)

        try:
            entry = data["entry"][0]
            changes = entry["changes"][0]
            value = changes["value"]

            if "messages" in value:

                message = value["messages"][0]
                sender = message["from"]

                text = ""

                if "text" in message:
                    text = message["text"]["body"]

                customers.append({
                    "name": sender,
                    "phone": sender,
                    "sa": "Unassigned",
                    "source": "WhatsApp",
                    "status": text
                })

                print("Customer added:", sender)

        except Exception as e:
            print("Webhook error:", e)

        return "EVENT_RECEIVED", 200

BASE_CSS = """
<style>
body { font-family: Arial; background:#f5f6f7; margin:0; }
.header { background:#075e54; color:white; padding:15px 25px; display:flex; justify-content:space-between; }
.header a { color:white; margin-left:15px; text-decoration:none; }
.container { max-width:1000px; margin:25px auto; background:white; padding:25px; border-radius:12px; }
input, select, textarea { width:100%; padding:12px; margin:8px 0 15px; border:1px solid #ddd; border-radius:8px; box-sizing:border-box; }
button, .btn { background:#25d366; border:0; padding:10px 16px; border-radius:8px; color:#073b31; font-weight:bold; cursor:pointer; text-decoration:none; }
table { width:100%; border-collapse:collapse; }
td, th { padding:12px; border-bottom:1px solid #eee; text-align:left; }
.msg { padding:10px 14px; border-radius:12px; margin:8px 0; max-width:70%; }
.in { background:#eee; }
.out { background:#dcf8c6; margin-left:auto; }
.small { color:#777; font-size:12px; }
.flash { background:#fff3cd; padding:10px; border-radius:8px; margin-bottom:12px; }
</style>
"""

LOGIN_HTML = BASE_CSS + """
<div class="container" style="max-width:420px;margin-top:80px;">
<h2>SA WhatsApp CRM 登录</h2>
{% with messages = get_flashed_messages() %}
{% for m in messages %}<div class="flash">{{m}}</div>{% endfor %}
{% endwith %}
<form method="post">
<label>Email</label>
<input name="email" placeholder="admin@test.com">
<label>Password</label>
<input name="password" type="password" placeholder="123456">
<button>登入</button>
</form>
<p class="small">
Admin: admin@test.com / 123456<br>
SA: annelie@test.com / 123456
</p>
</div>
"""

CUSTOMERS_HTML = BASE_CSS + """
<div class="header">
<div><b>SA WhatsApp CRM</b></div>
<div>
{{user['name']}} | {{user['role']}}
<a href="/customers">客户</a>
{% if user['role']=='admin' %}<a href="/admin/assign">分配</a>{% endif %}
<a href="/logout">登出</a>
</div>
</div>

<div class="container">
<h2>客户列表</h2>
<table>
<tr><th>客户</th><th>电话</th><th>SA</th><th>来源</th><th>状态</th><th></th></tr>
{% for c in customers %}
<tr>
<td>{{c['name'] or '-'}}</td>
<td>{{c['phone']}}</td>
<td>{{c['sa_name'] or '未分配'}}</td>
<td>{{c['source'] or '-'}}</td>
<td>{{c['status']}}</td>
<td><a class="btn" href="/chat/{{c['id']}}">聊天</a></td>
</tr>
{% endfor %}
</table>
</div>
"""

CHAT_HTML = BASE_CSS + """
<div class="header">
<div><b>SA WhatsApp CRM</b></div>
<div><a href="/customers">返回</a><a href="/logout">登出</a></div>
</div>

<div class="container">
<h2>{{customer['name'] or customer['phone']}}</h2>
<p class="small">{{customer['phone']}}</p>

{% with messages_flash = get_flashed_messages() %}
{% for m in messages_flash %}<div class="flash">{{m}}</div>{% endfor %}
{% endwith %}

<div style="background:#efeae2;padding:15px;border-radius:12px;min-height:350px;">
{% for m in messages %}
<div class="msg {{m['direction']}}">
<div>{{m['message']}}</div>
<div class="small">{{m['created_at']}}</div>
</div>
{% endfor %}
</div>

<form method="post" style="margin-top:15px;">
<textarea name="message" rows="3" placeholder="输入回复..."></textarea>
<button>用公司WhatsApp发送</button>
</form>
</div>
"""

ASSIGN_HTML = BASE_CSS + """
<div class="header">
<div><b>SA WhatsApp CRM</b></div>
<div><a href="/customers">客户</a><a href="/logout">登出</a></div>
</div>

<div class="container">
<h2>分配客户给SA</h2>

{% with messages = get_flashed_messages() %}
{% for m in messages %}<div class="flash">{{m}}</div>{% endfor %}
{% endwith %}

<form method="post">
<label>客户</label>
<select name="customer_id">
{% for c in customers %}
<option value="{{c['id']}}">{{c['name'] or '-'}} - {{c['phone']}}</option>
{% endfor %}
</select>

<label>SA</label>
<select name="sa_id">
{% for sa in sas %}
<option value="{{sa['id']}}">{{sa['name']}}</option>
{% endfor %}
</select>

<button>确认分配</button>
</form>
</div>
"""


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
else:
    init_db()