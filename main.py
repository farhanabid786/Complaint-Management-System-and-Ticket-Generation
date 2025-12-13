# ============================================================
# AI POWERED COMPLAINT MANAGEMENT & TICKET GENERATION SYSTEM
# Problem Code: P-004
# Technology: Python, Streamlit, Gemini AI, SQLite
# ============================================================

import streamlit as st
import sqlite3
import os
import uuid
import hashlib
import pandas as pd
import google.generativeai as genai
from datetime import datetime, timedelta
import smtplib
import ssl

# ============================================================
# STREAMLIT CONFIG
# ============================================================
st.set_page_config(
    page_title="AI Complaint Management System",
    page_icon="🤖",
    layout="wide"
)

# ============================================================
# GEMINI API CONFIG (SAFE INITIALIZATION)
# ============================================================
if "GEMINI_READY" not in st.session_state:
    try:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        st.session_state.GEMINI_READY = True
    except Exception:
        st.session_state.GEMINI_READY = False

# ============================================================
# DATABASE CONFIG
# ============================================================
DB_FILE = os.path.join(os.getcwd(), "complaints.db")

def get_db():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY,
        password_hash BLOB,
        salt BLOB
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id TEXT PRIMARY KEY,
        query TEXT,
        category TEXT,
        priority TEXT,
        status TEXT,
        assigned_to TEXT,
        timestamp TEXT,
        user_email TEXT,
        feedback TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS agents (
        name TEXT PRIMARY KEY,
        category TEXT,
        workload INTEGER
    )
    """)

    conn.commit()
    conn.close()

init_db()

# ============================================================
# INITIAL AGENTS
# ============================================================
def init_agents():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM agents")

    if cur.fetchone()[0] == 0:
        agents = [
            ("Agent_A", "Shipping", 0),
            ("Agent_B", "Refund", 0),
            ("Agent_C", "Login", 0),
            ("Agent_D", "Cancellation", 0),
            ("Agent_X", "General", 0)
        ]
        cur.executemany("INSERT INTO agents VALUES (?,?,?)", agents)

    conn.commit()
    conn.close()

init_agents()

# ============================================================
# AUTHENTICATION
# ============================================================
def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)

def register_user(email, password):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email FROM users WHERE email=?", (email,))
    if cur.fetchone():
        conn.close()
        return False, "User already exists"

    salt = os.urandom(16)
    hashed = hash_password(password, salt)
    cur.execute("INSERT INTO users VALUES (?,?,?)", (email, hashed, salt))
    conn.commit()
    conn.close()
    return True, "Registration successful"

def authenticate_user(email, password):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT password_hash, salt FROM users WHERE email=?", (email,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    return hash_password(password, row[1]) == row[0]

# ============================================================
# AI FUNCTIONS
# ============================================================
def ask_gemini(prompt):
    if not st.session_state.GEMINI_READY:
        return "NO_ANSWER"
    try:
        model = genai.GenerativeModel("gemini-2.5-pro")
        response = model.generate_content(prompt)
        return response.text
    except:
        return "NO_ANSWER"

def categorize_query(query):
    q = query.lower()
    if "refund" in q or "return" in q:
        return "Refund"
    if "login" in q or "password" in q:
        return "Login"
    if "ship" in q or "delivery" in q:
        return "Shipping"
    if "cancel" in q:
        return "Cancellation"
    return "General"

def determine_priority(query):
    prompt = f"""
    Determine urgency: High, Medium, or Low.
    Complaint: {query}
    """
    res = ask_gemini(prompt).lower()
    if "high" in res:
        return "High"
    elif "medium" in res:
        return "Medium"
    return "Low"

# ============================================================
# AGENT ASSIGNMENT
# ============================================================
def assign_agent(category):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT name FROM agents
        WHERE category=? OR category='General'
        ORDER BY workload ASC
        LIMIT 1
    """, (category,))
    agent = cur.fetchone()

    if agent:
        cur.execute(
            "UPDATE agents SET workload = workload + 1 WHERE name=?",
            (agent[0],)
        )
        conn.commit()
        conn.close()
        return agent[0]

    conn.close()
    return "Unassigned"

# ============================================================
# TICKET MANAGEMENT
# ============================================================
def raise_ticket(query, email):
    ticket_id = str(uuid.uuid4())[:8]
    category = categorize_query(query)
    priority = determine_priority(query)
    agent = assign_agent(category)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?)
    """, (
        ticket_id,
        query,
        category,
        priority,
        "Registered",
        agent,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        email,
        None
    ))
    conn.commit()
    conn.close()
    return ticket_id

def get_user_tickets(email):
    conn = get_db()
    df = pd.read_sql("SELECT * FROM tickets WHERE user_email=?", conn, params=(email,))
    conn.close()
    return df

def get_all_tickets():
    conn = get_db()
    df = pd.read_sql("SELECT * FROM tickets", conn)
    conn.close()
    return df

def update_ticket_status(ticket_id, status):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE tickets SET status=? WHERE ticket_id=?", (status, ticket_id))
    conn.commit()
    conn.close()

# ============================================================
# ESCALATION MECHANISM
# ============================================================
def escalate_tickets():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ticket_id, timestamp FROM tickets
        WHERE priority='High' AND status!='Resolved'
    """)
    rows = cur.fetchall()
    for r in rows:
        created = datetime.strptime(r[1], "%Y-%m-%d %H:%M:%S")
        if datetime.now() - created > timedelta(hours=24):
            cur.execute(
                "UPDATE tickets SET status='Escalated' WHERE ticket_id=?",
                (r[0],)
            )
    conn.commit()
    conn.close()

# ============================================================
# EMAIL NOTIFICATION (OPTIONAL)
# ============================================================
def send_email(to_email, message):
    try:
        sender = st.secrets["SENDER_EMAIL"]
        password = st.secrets["SENDER_EMAIL_PASSWORD"]
    except:
        return

    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls(context=context)
        server.login(sender, password)
        server.sendmail(sender, to_email, message)

# ============================================================
# SESSION STATE
# ============================================================
if "page" not in st.session_state:
    st.session_state.page = "login"
if "user" not in st.session_state:
    st.session_state.user = None

ADMIN_EMAIL = "admin@test.com"

# ============================================================
# UI PAGES
# ============================================================
def login_page():
    st.title("🔐 Login")
    email = st.text_input("Email")
    pwd = st.text_input("Password", type="password")

    if st.button("Login"):
        if authenticate_user(email, pwd):
            st.session_state.user = email
            st.session_state.page = "admin" if email == ADMIN_EMAIL else "user"
            st.rerun()
        else:
            st.error("Invalid credentials")

    if st.button("Register"):
        st.session_state.page = "register"
        st.rerun()

def register_page():
    st.title("📝 Register")
    email = st.text_input("Email")
    pwd = st.text_input("Password", type="password")

    if st.button("Create Account"):
        ok, msg = register_user(email, pwd)
        if ok:
            st.success(msg)
            st.session_state.page = "login"
            st.rerun()
        else:
            st.error(msg)

def user_page():
    st.title("👤 User Dashboard")
    st.write(f"Logged in as **{st.session_state.user}**")

    query = st.text_area("Enter your complaint")
    if st.button("Submit Complaint"):
        ticket = raise_ticket(query, st.session_state.user)
        st.success(f"Ticket Generated: {ticket}")

    st.subheader("📋 My Tickets")
    st.dataframe(get_user_tickets(st.session_state.user))

    if st.button("Logout"):
        st.session_state.page = "login"
        st.session_state.user = None
        st.rerun()

def admin_page():
    st.title("🛠 Admin Dashboard")
    escalate_tickets()

    df = get_all_tickets()
    st.dataframe(df)

    st.subheader("📊 Analytics")
    st.metric("Total Tickets", len(df))
    st.metric("Resolved", len(df[df.status == "Resolved"]))
    st.metric("Escalated", len(df[df.status == "Escalated"]))

    for _, row in df.iterrows():
        status = st.selectbox(
            f"Update status for {row.ticket_id}",
            ["Registered", "In Progress", "Resolved"],
            key=row.ticket_id
        )
        if st.button(f"Update {row.ticket_id}"):
            update_ticket_status(row.ticket_id, status)
            st.rerun()

    if st.button("Logout"):
        st.session_state.page = "login"
        st.session_state.user = None
        st.rerun()

# ============================================================
# ROUTING
# ============================================================
if st.session_state.page == "login":
    login_page()
elif st.session_state.page == "register":
    register_page()
elif st.session_state.page == "user":
    user_page()
elif st.session_state.page == "admin":
    admin_page()
