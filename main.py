# ============================================================
# AI POWERED COMPLAINT MANAGEMENT SYSTEM
# Problem Code: P-004
# PART 1: Core Setup, DB, Auth, Agents, FAQ
# ============================================================

# -------------------- IMPORTS --------------------
import streamlit as st
import sqlite3
import os
import uuid
import hashlib
import pandas as pd
import google.generativeai as genai
from datetime import datetime, timedelta

# -------------------- STREAMLIT CONFIG --------------------
st.set_page_config(
    page_title="AI Complaint Management System",
    page_icon="🤖",
    layout="wide"
)

# -------------------- GEMINI API CONFIG --------------------
# Safe, single-time initialization (prevents crashes on rerun)

if "GEMINI_READY" not in st.session_state:
    try:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        st.session_state["GEMINI_READY"] = True
    except Exception:
        st.session_state["GEMINI_READY"] = False

# -------------------- DATABASE CONFIG --------------------
DB_FILE = os.path.join(os.getcwd(), "complaints.db")

def get_db():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # USERS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY,
        password_hash BLOB NOT NULL,
        salt BLOB NOT NULL,
        role TEXT DEFAULT 'user'
    )
    """)

    # AGENTS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS agents (
        agent_name TEXT PRIMARY KEY,
        category TEXT,
        workload INTEGER DEFAULT 0,
        available INTEGER DEFAULT 1
    )
    """)

    # TICKETS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id TEXT PRIMARY KEY,
        query TEXT,
        category TEXT,
        priority TEXT,
        status TEXT,
        assigned_agent TEXT,
        created_at TEXT,
        user_email TEXT,
        feedback TEXT
    )
    """)

    # CHAT LOG TABLE (for chatbot + interview credibility)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chat_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT,
        user_message TEXT,
        bot_response TEXT,
        escalated INTEGER,
        timestamp TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()

# -------------------- INITIAL AGENT DATA --------------------
def init_agents():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM agents")

    if cur.fetchone()[0] == 0:
        agents = [
            ("Agent_Shipping", "Shipping", 0, 1),
            ("Agent_Refund", "Refund", 0, 1),
            ("Agent_Login", "Login", 0, 1),
            ("Agent_Cancel", "Cancellation", 0, 1),
            ("Agent_General", "General", 0, 1)
        ]
        cur.executemany(
            "INSERT INTO agents VALUES (?,?,?,?)",
            agents
        )

    conn.commit()
    conn.close()

init_agents()

# -------------------- PASSWORD SECURITY --------------------
def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        100000
    )

# -------------------- USER AUTHENTICATION --------------------
def register_user(email, password, role="user"):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT email FROM users WHERE email=?", (email,))
    if cur.fetchone():
        conn.close()
        return False, "User already exists"

    salt = os.urandom(16)
    pwd_hash = hash_password(password, salt)

    cur.execute(
        "INSERT INTO users VALUES (?,?,?,?)",
        (email, pwd_hash, salt, role)
    )

    conn.commit()
    conn.close()
    return True, "Registration successful"

def authenticate_user(email, password):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT password_hash, salt, role FROM users WHERE email=?",
        (email,)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, None

    if hash_password(password, row[1]) == row[0]:
        return True, row[2]

    return False, None

# -------------------- DEFAULT ADMIN --------------------
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "admin123"

def ensure_admin():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email FROM users WHERE email=?", (ADMIN_EMAIL,))
    if not cur.fetchone():
        register_user(ADMIN_EMAIL, ADMIN_PASSWORD, role="admin")
    conn.close()

ensure_admin()

# -------------------- FAQ KNOWLEDGE BASE --------------------
# Used by Chatbot before escalating to ticket

FAQS = [
    ("When will my order be shipped?",
     "Orders are shipped within 3–5 business days."),

    ("How can I track my order?",
     "You can track your order using the tracking link sent to your email."),

    ("What is your return policy?",
     "Returns are allowed within 30 days of delivery."),

    ("I forgot my password",
     "Use the 'Forgot Password' option on the login page."),

    ("How can I cancel my order?",
     "Orders can be cancelled within 2 hours of placing them.")
]

def search_faq(user_query):
    for q, a in FAQS:
        if q.lower() in user_query.lower():
            return a
    return None

# -------------------- SESSION STATE --------------------
if "page" not in st.session_state:
    st.session_state["page"] = "login"

if "user_email" not in st.session_state:
    st.session_state["user_email"] = None

if "user_role" not in st.session_state:
    st.session_state["user_role"] = None

# ============================================================
# PART 2: AI CHATBOT, FAQ FLOW, TICKET CREATION
# ============================================================

# -------------------- GEMINI RESPONSE --------------------
def ask_gemini(prompt):
    if not st.session_state.get("GEMINI_READY"):
        return None
    try:
        model = genai.GenerativeModel("gemini-2.5-pro")
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception:
        return None

# -------------------- PRIORITY (SEVERITY) DETECTION --------------------
def determine_priority(query):
    prompt = f"""
    You are an AI system for complaint management.
    Classify urgency as High, Medium, or Low.

    Complaint: {query}
    """
    result = ask_gemini(prompt)
    if not result:
        return "Low"

    result = result.lower()
    if "high" in result:
        return "High"
    elif "medium" in result:
        return "Medium"
    return "Low"

# -------------------- CATEGORY DETECTION --------------------
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

# -------------------- AGENT ASSIGNMENT --------------------
def assign_agent(category):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT agent_name FROM agents
        WHERE available=1 AND (category=? OR category='General')
        ORDER BY workload ASC
        LIMIT 1
    """, (category,))

    agent = cur.fetchone()
    if agent:
        cur.execute(
            "UPDATE agents SET workload = workload + 1 WHERE agent_name=?",
            (agent[0],)
        )
        conn.commit()
        conn.close()
        return agent[0]

    conn.close()
    return "Unassigned"

# -------------------- TICKET CREATION --------------------
def create_ticket(query, user_email):
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
        user_email,
        None
    ))
    conn.commit()
    conn.close()
    return ticket_id, priority, agent

# -------------------- CHAT LOGGING --------------------
def log_chat(user_email, user_msg, bot_msg, escalated):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO chat_logs (user_email, user_message, bot_response, escalated, timestamp)
        VALUES (?,?,?,?,?)
    """, (
        user_email,
        user_msg,
        bot_msg,
        escalated,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()
    conn.close()

# ============================================================
# CHATBOT UI (USER SIDE)
# ============================================================
def chatbot_ui():
    st.subheader("🤖 AI Support Chatbot")

    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    user_input = st.text_input("Ask your question or describe your issue:")

    if st.button("Send") and user_input.strip():
        # Step 1: FAQ check
        faq_answer = search_faq(user_input)

        if faq_answer:
            bot_reply = faq_answer
            escalated = 0

        else:
            # Step 2: Gemini AI
            prompt = f"""
            You are a customer support assistant.
            Answer the user's query clearly.
            If unsure, say you cannot resolve confidently.

            Query: {user_input}
            """
            ai_reply = ask_gemini(prompt)

            # Step 3: Confidence check
            if not ai_reply or "cannot" in ai_reply.lower():
                # Escalate to ticket
                ticket_id, priority, agent = create_ticket(
                    user_input,
                    st.session_state["user_email"]
                )
                bot_reply = (
                    f"I have created a support ticket for you.\n\n"
                    f"🎫 Ticket ID: {ticket_id}\n"
                    f"⚠ Priority: {priority}\n"
                    f"👨‍💼 Assigned Agent: {agent}"
                )
                escalated = 1
            else:
                bot_reply = ai_reply
                escalated = 0

        # Save history
        st.session_state["chat_history"].append(
            ("You", user_input)
        )
        st.session_state["chat_history"].append(
            ("Bot", bot_reply)
        )

        log_chat(
            st.session_state["user_email"],
            user_input,
            bot_reply,
            escalated
        )

    # Display chat
    for sender, msg in st.session_state["chat_history"]:
        if sender == "You":
            st.markdown(f"**🧑 You:** {msg}")
        else:
            st.markdown(f"**🤖 Bot:** {msg}")

# ============================================================
# PART 3: USER DASHBOARD, ADMIN DASHBOARD, ESCALATION, ANALYTICS
# ============================================================

# -------------------- ESCALATION LOGIC --------------------
ESCALATION_HOURS = 24

def escalate_tickets():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT ticket_id, created_at
        FROM tickets
        WHERE priority='High' AND status NOT IN ('Resolved','Escalated')
    """)
    rows = cur.fetchall()

    for ticket_id, created_at in rows:
        created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
        if datetime.now() - created > timedelta(hours=ESCALATION_HOURS):
            cur.execute(
                "UPDATE tickets SET status='Escalated' WHERE ticket_id=?",
                (ticket_id,)
            )

    conn.commit()
    conn.close()

# -------------------- USER DASHBOARD --------------------
def user_dashboard():
    st.title("👤 User Dashboard")
    st.write(f"Logged in as **{st.session_state['user_email']}**")

    st.divider()
    chatbot_ui()

    st.divider()
    st.subheader("📋 My Tickets")

    conn = get_db()
    df = pd.read_sql(
        "SELECT * FROM tickets WHERE user_email=?",
        conn,
        params=(st.session_state["user_email"],)
    )
    conn.close()

    if df.empty:
        st.info("No tickets created yet.")
    else:
        st.dataframe(df)

    if st.button("🚪 Logout"):
        logout()

# -------------------- ADMIN DASHBOARD --------------------
def admin_dashboard():
    st.title("🛠 Admin Dashboard")

    escalate_tickets()

    conn = get_db()
    tickets_df = pd.read_sql("SELECT * FROM tickets", conn)
    agents_df = pd.read_sql("SELECT * FROM agents", conn)
    conn.close()

    st.subheader("📊 System Analytics")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Tickets", len(tickets_df))
    col2.metric("Resolved", len(tickets_df[tickets_df.status == "Resolved"]))
    col3.metric("Escalated", len(tickets_df[tickets_df.status == "Escalated"]))
    col4.metric("High Priority", len(tickets_df[tickets_df.priority == "High"]))

    st.bar_chart(tickets_df["category"].value_counts())
    st.bar_chart(tickets_df["priority"].value_counts())

    st.divider()
    st.subheader("🎫 Ticket Management")

    for _, row in tickets_df.iterrows():
        with st.expander(f"Ticket {row.ticket_id} | {row.priority} | {row.status}"):
            st.write(f"**User:** {row.user_email}")
            st.write(f"**Category:** {row.category}")
            st.write(f"**Assigned Agent:** {row.assigned_agent}")
            st.write(f"**Query:** {row.query}")

            new_status = st.selectbox(
                "Update Status",
                ["Registered", "In Progress", "Resolved"],
                index=["Registered", "In Progress", "Resolved"].index(row.status)
                if row.status in ["Registered", "In Progress", "Resolved"] else 0,
                key=row.ticket_id
            )

            if st.button(f"Update {row.ticket_id}", key=f"btn_{row.ticket_id}"):
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE tickets SET status=? WHERE ticket_id=?",
                    (new_status, row.ticket_id)
                )
                conn.commit()
                conn.close()
                st.success("Status updated")
                st.experimental_rerun()

    st.divider()
    st.subheader("👨‍💼 Agent Management")

    st.dataframe(agents_df)

    st.divider()
    st.subheader("📤 Export Reports")
    csv = tickets_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download Ticket Report (CSV)",
        csv,
        "tickets_report.csv",
        "text/csv"
    )

    if st.button("🚪 Logout"):
        logout()

# -------------------- LOGOUT --------------------
def logout():
    for key in ["user_email", "user_role", "page", "chat_history"]:
        if key in st.session_state:
            del st.session_state[key]
    st.session_state["page"] = "login"
    st.experimental_rerun()

# ============================================================
# ROUTING & LOGIN UI
# ============================================================
def login_page():
    st.title("🔐 Login")

    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        ok, role = authenticate_user(email, password)
        if ok:
            st.session_state["user_email"] = email
            st.session_state["user_role"] = role
            st.session_state["page"] = "admin" if role == "admin" else "user"
            st.experimental_rerun()
        else:
            st.error("Invalid credentials")

    if st.button("Register"):
        st.session_state["page"] = "register"
        st.experimental_rerun()

def register_page():
    st.title("📝 Register")

    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Create Account"):
        ok, msg = register_user(email, password)
        if ok:
            st.success(msg)
            st.session_state["page"] = "login"
            st.experimental_rerun()
        else:
            st.error(msg)

# -------------------- MAIN ROUTER --------------------
if st.session_state["page"] == "login":
    login_page()
elif st.session_state["page"] == "register":
    register_page()
elif st.session_state["page"] == "user":
    user_dashboard()
elif st.session_state["page"] == "admin":
    admin_dashboard()

# ============================================================
# PART 4: ADMIN OVERRIDES, FAQ MGMT, NOTIFICATIONS, FINAL HARDENING
# ============================================================

import smtplib
import ssl

# ============================================================
# OPTIONAL EMAIL NOTIFICATIONS (SAFE / FAIL-SILENT)
# ============================================================
def send_email_notification(to_email, subject, message):
    """
    Optional email notification.
    If secrets are missing, function fails silently (safe for Streamlit Cloud).
    """
    try:
        sender = st.secrets["SENDER_EMAIL"]
        password = st.secrets["SENDER_EMAIL_PASSWORD"]
    except Exception:
        return  # Secrets not configured

    email_text = f"""\
From: {sender}
To: {to_email}
Subject: {subject}

{message}
"""

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls(context=context)
            server.login(sender, password)
            server.sendmail(sender, to_email, email_text)
    except Exception:
        pass  # Never crash app due to email failure

# ============================================================
# ADMIN: AGENT REASSIGNMENT OVERRIDE
# ============================================================
def reassign_ticket(ticket_id, new_agent):
    conn = get_db()
    cur = conn.cursor()

    # Reduce workload of old agent
    cur.execute(
        "SELECT assigned_agent FROM tickets WHERE ticket_id=?",
        (ticket_id,)
    )
    old_agent = cur.fetchone()
    if old_agent and old_agent[0] != "Unassigned":
        cur.execute(
            "UPDATE agents SET workload = workload - 1 WHERE agent_name=?",
            (old_agent[0],)
        )

    # Assign new agent
    cur.execute(
        "UPDATE tickets SET assigned_agent=? WHERE ticket_id=?",
        (new_agent, ticket_id)
    )
    cur.execute(
        "UPDATE agents SET workload = workload + 1 WHERE agent_name=?",
        (new_agent,)
    )

    conn.commit()
    conn.close()

# ============================================================
# ADMIN: FAQ MANAGEMENT (INTERVIEW-LEVEL FEATURE)
# ============================================================
def admin_faq_manager():
    st.subheader("📚 FAQ Management (Admin)")

    global FAQS

    with st.expander("➕ Add New FAQ"):
        q = st.text_input("Question")
        a = st.text_area("Answer")
        if st.button("Add FAQ"):
            if q and a:
                FAQS.append((q, a))
                st.success("FAQ added (in-memory)")
            else:
                st.warning("Question and answer required")

    st.subheader("📄 Existing FAQs")
    for i, (q, a) in enumerate(FAQS):
        with st.expander(q):
            st.write(a)
            if st.button(f"Remove FAQ {i}", key=f"faq_{i}"):
                FAQS.pop(i)
                st.experimental_rerun()

# ============================================================
# ADMIN DASHBOARD EXTENSION (OVERRIDES + FAQ)
# ============================================================
def admin_dashboard_extensions():
    conn = get_db()
    tickets_df = pd.read_sql("SELECT * FROM tickets", conn)
    agents_df = pd.read_sql("SELECT * FROM agents", conn)
    conn.close()

    st.divider()
    st.subheader("🔁 Reassign Ticket (Admin Override)")

    ticket_ids = tickets_df["ticket_id"].tolist()
    agent_names = agents_df["agent_name"].tolist()

    if ticket_ids and agent_names:
        t_id = st.selectbox("Select Ticket", ticket_ids)
        new_agent = st.selectbox("Assign to Agent", agent_names)

        if st.button("Reassign Ticket"):
            reassign_ticket(t_id, new_agent)
            st.success("Ticket reassigned successfully")
            st.experimental_rerun()

    st.divider()
    admin_faq_manager()

# ============================================================
# PATCH ADMIN DASHBOARD (HOOK EXTENSION)
# ============================================================
_original_admin_dashboard = admin_dashboard

def admin_dashboard():
    _original_admin_dashboard()
    admin_dashboard_extensions()

# ============================================================
# SYSTEM SANITY CHECKS (FOR INTERVIEWS)
# ============================================================
def system_health_check():
    """
    Visible only to admin.
    Demonstrates production thinking & fault tolerance.
    """
    st.subheader("🩺 System Health")

    st.write("Gemini API:", "✅ Ready" if st.session_state.get("GEMINI_READY") else "⚠ Disabled")
    st.write("Database:", "✅ Connected" if os.path.exists(DB_FILE) else "❌ Missing")
    st.write("Email Notifications:", "✅ Configured" if "SENDER_EMAIL" in st.secrets else "⚠ Not Configured")

# ============================================================
# ADMIN DASHBOARD FINAL PATCH
# ============================================================
def admin_dashboard():
    st.title("🛠 Admin Dashboard (Extended)")

    system_health_check()
    escalate_tickets()

    conn = get_db()
    tickets_df = pd.read_sql("SELECT * FROM tickets", conn)
    agents_df = pd.read_sql("SELECT * FROM agents", conn)
    conn.close()

    # --- Analytics ---
    st.subheader("📊 System Analytics")
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Tickets", len(tickets_df))
    col2.metric("Resolved", len(tickets_df[tickets_df.status == "Resolved"]))
    col3.metric("Escalated", len(tickets_df[tickets_df.status == "Escalated"]))

    st.bar_chart(tickets_df["priority"].value_counts())
    st.bar_chart(tickets_df["category"].value_counts())

    # --- Ticket Table ---
    st.subheader("🎫 Ticket Overview")
    st.dataframe(tickets_df)

    # --- Agent Table ---
    st.subheader("👨‍💼 Agent Workload")
    st.dataframe(agents_df)

    # --- Admin Controls ---
    admin_dashboard_extensions()

    if st.button("🚪 Logout"):
        logout()
