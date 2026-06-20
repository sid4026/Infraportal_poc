import os, sqlite3, secrets, io, random, re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, abort, send_file
from flask_cors import CORS
import pandas as pd
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import landscape, A3
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'infra_portal.db')
KEY_PATH = os.path.join(DATA_DIR, 'server.key')

if not os.path.exists(KEY_PATH):
    with open(KEY_PATH, 'wb') as f: f.write(Fernet.generate_key())
with open(KEY_PATH, 'rb') as f: cipher_suite = Fernet(f.read())

app = Flask(__name__, static_folder='static')
CORS(app)

TOKEN_TO_USER = {}
USER_TO_TOKEN = {}

COLUMNS = ["Report ID","Date Performed","Technician","Manufacturer","Model Number","Serial Number","CPU Tier","CPU Generation","CPU Architecture","RAM Brand","RAM Size (GB)","RAM Freq (MHz)","RAM Type","RAM Config","Storage Brand","Storage Size","Storage Type","GPU Type","GPU Model","OS Version","OS Release","OS Build ID","CPU Test","RAM Test","Storage Test","Video Test","2D Graphics","3D Graphics","Test Combination","CPU Load %","RAM Load %","Storage Load %","GPU Load %","CPU Max °C","RAM Max °C","SSD Max °C","GPU Max °C","Duration (hrs)","Result","Is Retest","Retest Attempt","Retest Reason","Notes"]

# Columns that are formula-derived — never imported from Excel, always recalculated
FORMULA_COLS = {"Report ID", "OS Build ID", "Test Combination",
                "CPU Load %", "RAM Load %", "Storage Load %", "GPU Load %",
                "CPU Max °C", "RAM Max °C", "SSD Max °C", "GPU Max °C"}

# ─────────────────────────────────────────────
# REPORT ID MIGRATION HELPER
# ─────────────────────────────────────────────
_REPORT_ID_PATTERN = re.compile(r'^([A-Z]+)\d{2}S(\d+)$')

def parse_legacy_report_id(report_id_str):
    """Parse a stored Report ID string into (prefix, sequence).
    Returns ('UNKNOWN', 0) for malformed records with a warning log."""
    if not report_id_str:
        return ('UNKNOWN', 0)
    m = _REPORT_ID_PATTERN.match(str(report_id_str).strip())
    if m:
        return (m.group(1), int(m.group(2)))
    print(f"WARNING: Could not parse report_id '{report_id_str}' — setting prefix=UNKNOWN, sequence=0")
    return ('UNKNOWN', 0)

def reconstruct_report_id(prefix, sequence, date_str):
    """Reconstruct the display Report ID from stored parts at read time."""
    try:
        yy = date_str[:4][-2:] if date_str and len(date_str) >= 4 else datetime.now().strftime('%y')
    except Exception:
        yy = datetime.now().strftime('%y')
    return f"{prefix}{yy}S{sequence}"

# ─────────────────────────────────────────────
# DB CONNECTION
# ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def log_activity(username, action):
    conn = get_db()
    conn.execute("INSERT INTO activity_logs (username, action, timestamp) VALUES (?,?,?)",
                 (username, action, datetime.now().isoformat()))
    conn.commit(); conn.close()

# ─────────────────────────────────────────────
# DB INIT — Phase 2
# ─────────────────────────────────────────────
def init_db():
    conn = get_db()

    # ── EXISTING TABLES (unchanged) ──
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, role TEXT,
        first_name TEXT, last_name TEXT, email TEXT, phone TEXT, avatar TEXT,
        force_reset BOOLEAN DEFAULT 0, pwd_expiry DATETIME, can_delete BOOLEAN DEFAULT 0)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS user_creds (
        id INTEGER PRIMARY KEY, user_id INTEGER, app_name TEXT,
        portal_url TEXT, username TEXT, enc_password TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS lookups (
        id INTEGER PRIMARY KEY, category TEXT, lookup_key TEXT, lookup_value TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY, username TEXT, action TEXT, timestamp DATETIME)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS os_builds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        os_name TEXT NOT NULL,
        os_release TEXT NOT NULL,
        build_id TEXT NOT NULL,
        UNIQUE(os_name, os_release))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS fiscal_years (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fy_key TEXT UNIQUE NOT NULL,
        fy_label TEXT NOT NULL,
        is_active INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    # ── PHASE 2: sequence_counter (single-row global counter) ──
    conn.execute('''CREATE TABLE IF NOT EXISTS sequence_counter (
        id INTEGER PRIMARY KEY,
        next_val INTEGER NOT NULL DEFAULT 1
    )''')

    # ── PHASE 2: Template registry ──
    conn.execute('''CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_name TEXT UNIQUE NOT NULL,
        created_by TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    # ── PHASE 2: Project registry ──
    conn.execute('''CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT UNIQUE NOT NULL,
        project_prefix TEXT NOT NULL,
        db_path TEXT UNIQUE NOT NULL,
        template_id INTEGER REFERENCES templates(id),
        created_by TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    # ── PHASE 2: Preset values (replaces BASE_ARRAYS) ──
    conn.execute('''CREATE TABLE IF NOT EXISTS preset_values (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER REFERENCES templates(id) ON DELETE CASCADE,
        column_name TEXT NOT NULL,
        value TEXT NOT NULL,
        display_order INTEGER DEFAULT 0,
        UNIQUE(template_id, column_name, value)
    )''')

    # ── PHASE 2: Linked column rules (data engine — equals/not_equals/contains only) ──
    conn.execute('''CREATE TABLE IF NOT EXISTS linked_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER REFERENCES templates(id) ON DELETE CASCADE,
        rule_name TEXT,
        if_column TEXT NOT NULL,
        if_value TEXT NOT NULL,
        operator TEXT NOT NULL DEFAULT 'equals',
        then_column TEXT NOT NULL,
        then_value TEXT NOT NULL,
        display_order INTEGER DEFAULT 0
    )''')

    # ── PHASE 2: Display rules (appearance engine — supports gte/lte for numeric) ──
    conn.execute('''CREATE TABLE IF NOT EXISTS display_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER REFERENCES templates(id) ON DELETE CASCADE,
        column_name TEXT NOT NULL,
        operator TEXT NOT NULL,
        condition_value TEXT NOT NULL,
        highlight_color TEXT,
        font_style TEXT,
        display_order INTEGER DEFAULT 0
    )''')

    # ── LEGACY: burn_in_records — migrate report_id if still present ──
    # Check columns of burn_in_records
    existing_bir_cols = {row[1] for row in conn.execute("PRAGMA table_info(burn_in_records)").fetchall()}

    if not existing_bir_cols:
        # Fresh install — create with new schema (report_prefix + report_sequence)
        cols_def = ", ".join([f'"{col}" TEXT' for col in COLUMNS if col != "Report ID"])
        conn.execute(f'''CREATE TABLE IF NOT EXISTS burn_in_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fy TEXT NOT NULL,
            report_prefix TEXT NOT NULL DEFAULT "BIT",
            report_sequence INTEGER NOT NULL DEFAULT 0,
            {cols_def})''')
    elif '"Report ID"' in existing_bir_cols or 'Report ID' in existing_bir_cols:
        # Old schema: migrate report_id → report_prefix + report_sequence
        _migrate_report_id_column(conn)
    # else: already migrated — nothing to do

    # ── LEGACY V7.1 rule engine tables (kept for backward compat, not used in Phase 2 flow) ──
    conn.execute('''CREATE TABLE IF NOT EXISTS rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_name TEXT NOT NULL,
        priority INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        created_by TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS rule_conditions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id INTEGER REFERENCES rules(id) ON DELETE CASCADE,
        condition_col TEXT NOT NULL,
        operator TEXT NOT NULL,
        condition_value TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS rule_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id INTEGER REFERENCES rules(id) ON DELETE CASCADE,
        action_col TEXT NOT NULL,
        action_value TEXT NOT NULL)''')

    # ── SEEDS ──
    if not conn.execute("SELECT * FROM users WHERE username='superadmin'").fetchone():
        conn.execute("INSERT INTO users (username, password_hash, role, first_name, last_name, force_reset, can_delete) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     ("superadmin", generate_password_hash("acgi2026"), "superadmin", "Master", "Admin", 0, 1))

    if not conn.execute("SELECT * FROM os_builds").fetchone():
        conn.executemany("INSERT OR IGNORE INTO os_builds (os_name, os_release, build_id) VALUES (?,?,?)", [
            ('Windows 10', '21H2',   'Win10-Build74'),
            ('Windows 11', '23H2',   'Win11-Build42'),
            ('Windows 11', '23H2-R', 'Win11-Build42R'),
        ])

    if not conn.execute("SELECT * FROM lookups WHERE category='Technician'").fetchone():
        conn.executemany("INSERT INTO lookups (category, lookup_key, lookup_value) VALUES (?, ?, ?)", [
            ('Technician', 'Siddhesh P', ''),
            ('Technician', 'Sunil K', ''),
        ])

    if not conn.execute("SELECT * FROM fiscal_years").fetchone():
        conn.execute("INSERT INTO fiscal_years (fy_key, fy_label, is_active) VALUES (?, ?, 1)",
                     ('FY-2627', 'FY-2627'))

    # ── SEED default BIT template + project (Phase 2) ──
    if not conn.execute("SELECT * FROM templates WHERE template_name='BIT-Managed'").fetchone():
        conn.execute("INSERT INTO templates (template_name, created_by) VALUES ('BIT-Managed', 'system')")

    bit_template = conn.execute("SELECT id FROM templates WHERE template_name='BIT-Managed'").fetchone()
    if bit_template and not conn.execute("SELECT * FROM projects WHERE project_name='BIT'").fetchone():
        conn.execute(
            "INSERT INTO projects (project_name, project_prefix, db_path, template_id, created_by) VALUES (?,?,?,?,?)",
            ('BIT', 'BIT', DB_PATH, bit_template['id'], 'system')
        )

    # ── INIT sequence_counter if empty ──
    if not conn.execute("SELECT * FROM sequence_counter WHERE id=1").fetchone():
        # Find the current MAX sequence from existing records
        max_seq_row = conn.execute("SELECT MAX(report_sequence) as m FROM burn_in_records").fetchone()
        max_seq = max_seq_row['m'] if max_seq_row and max_seq_row['m'] is not None else 0
        conn.execute("INSERT INTO sequence_counter (id, next_val) VALUES (1, ?)", (max_seq + 1,))

    conn.commit(); conn.close()


def _migrate_report_id_column(conn):
    """Migrate burn_in_records from 'Report ID' TEXT column to report_prefix + report_sequence."""
    print("INFO: Migrating burn_in_records Report ID column to prefix + sequence split...")

    existing_bir_cols = {row[1] for row in conn.execute("PRAGMA table_info(burn_in_records)").fetchall()}

    # Add new columns if they don't exist yet
    if 'report_prefix' not in existing_bir_cols:
        conn.execute('ALTER TABLE burn_in_records ADD COLUMN report_prefix TEXT NOT NULL DEFAULT "BIT"')
    if 'report_sequence' not in existing_bir_cols:
        conn.execute('ALTER TABLE burn_in_records ADD COLUMN report_sequence INTEGER NOT NULL DEFAULT 0')

    # Parse and populate from existing Report ID strings
    rows = conn.execute('SELECT id, "Report ID" FROM burn_in_records').fetchall()
    max_seq = 0
    for row in rows:
        prefix, seq = parse_legacy_report_id(row['Report ID'])
        conn.execute(
            'UPDATE burn_in_records SET report_prefix=?, report_sequence=? WHERE id=?',
            (prefix, seq, row['id'])
        )
        if seq > max_seq:
            max_seq = seq

    # Drop old Report ID column — SQLite requires table rebuild for this
    # Get all current columns except "Report ID"
    all_cols = conn.execute("PRAGMA table_info(burn_in_records)").fetchall()
    keep_cols = [c[1] for c in all_cols if c[1] not in ('"Report ID"', 'Report ID')]
    keep_cols_quoted = [f'"{c}"' for c in keep_cols]

    conn.execute(f'''CREATE TABLE burn_in_records_new AS
        SELECT {", ".join(keep_cols_quoted)} FROM burn_in_records''')
    conn.execute("DROP TABLE burn_in_records")
    conn.execute("ALTER TABLE burn_in_records_new RENAME TO burn_in_records")

    # Ensure sequence_counter is populated
    if not conn.execute("SELECT * FROM sequence_counter WHERE id=1").fetchone():
        conn.execute("INSERT INTO sequence_counter (id, next_val) VALUES (1, ?)", (max_seq + 1,))
    else:
        # Only update if current next_val is less than what we found
        current = conn.execute("SELECT next_val FROM sequence_counter WHERE id=1").fetchone()
        if current and current['next_val'] <= max_seq:
            conn.execute("UPDATE sequence_counter SET next_val=? WHERE id=1", (max_seq + 1,))

    print(f"INFO: Migration complete. {len(rows)} records migrated. next_val set to {max_seq + 1}.")
    conn.commit()


init_db()

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_current_user():
    token = request.headers.get("X-Auth-Token")
    if not token or token not in TOKEN_TO_USER: abort(401)
    return TOKEN_TO_USER[token]

def get_next_sequence(conn):
    """Atomically get-and-increment the global sequence counter."""
    row = conn.execute("SELECT next_val FROM sequence_counter WHERE id=1").fetchone()
    if row is None:
        conn.execute("INSERT INTO sequence_counter (id, next_val) VALUES (1, 2)")
        return 1
    next_val = row['next_val']
    conn.execute("UPDATE sequence_counter SET next_val=? WHERE id=1", (next_val + 1,))
    return next_val

def assign_report_id(conn, fy, row_id, date_str, prefix='BIT'):
    """Assign permanent report_prefix and report_sequence to a newly inserted row."""
    sequence = get_next_sequence(conn)
    conn.execute(
        'UPDATE burn_in_records SET report_prefix=?, report_sequence=? WHERE id=?',
        (prefix, sequence, row_id)
    )
    return reconstruct_report_id(prefix, sequence, date_str)

def rows_to_dicts_with_report_id(rows):
    """Convert DB rows to dicts, reconstructing Report ID at read time."""
    result = []
    for row in rows:
        d = dict(row)
        prefix = d.pop('report_prefix', 'BIT')
        sequence = d.pop('report_sequence', 0)
        date_str = d.get('Date Performed', '') or ''
        d['Report ID'] = reconstruct_report_id(prefix, sequence, date_str)
        result.append(d)
    return result

def resolve_os_build(conn, os_name, os_release):
    row = conn.execute(
        "SELECT build_id FROM os_builds WHERE os_name=? AND os_release=?",
        (os_name, os_release)).fetchone()
    return row['build_id'] if row else 'Manual Entry'

def get_template_id_for_project(conn, project_name='BIT'):
    """Get the template_id for a named project."""
    row = conn.execute("SELECT template_id FROM projects WHERE project_name=?", (project_name,)).fetchone()
    return row['template_id'] if row else None

def evaluate_linked_rules(conn, template_id, data):
    """Evaluate linked_rules from DB against current row data. Returns dict of {col: value} to set."""
    if not template_id:
        return {}
    rules = conn.execute(
        "SELECT * FROM linked_rules WHERE template_id=? ORDER BY display_order, id",
        (template_id,)
    ).fetchall()

    applied = {}
    for rule in rules:
        field_val = str(data.get(rule['if_column'], '') or '').strip()
        cond_val = str(rule['if_value'] or '').strip()
        op = rule['operator']

        match = False
        if op == 'equals':     match = field_val.lower() == cond_val.lower()
        elif op == 'not_equals': match = field_val.lower() != cond_val.lower()
        elif op == 'contains':   match = cond_val.lower() in field_val.lower()

        if match and rule['then_column'] not in applied:
            applied[rule['then_column']] = rule['then_value']

    return applied

def recalc_formula_cols(conn, row_id, data, template_id=None):
    """Recalculate formula columns server-side and update the record."""
    updates = {}

    # OS Build ID
    os_name = data.get('OS Version', '')
    os_release = data.get('OS Release', '')
    if os_name and os_release:
        updates['OS Build ID'] = resolve_os_build(conn, os_name, os_release)

    # Test Combination
    combo_map = [('CPU Test','CPU'), ('RAM Test','RAM'), ('Storage Test','Storage'),
                 ('Video Test','Video'), ('2D Graphics','2D'), ('3D Graphics','3D')]
    combo = ' '.join(label for col, label in combo_map if data.get(col) == 'Yes')
    updates['Test Combination'] = combo

    # Linked column rules from DB (replaces hardcoded NA overrides for POC)
    # For BIT template, also apply hardcoded NA logic for Load/Temp columns
    na_map = [
        ('CPU Test',     ['CPU Load %', 'CPU Max °C']),
        ('RAM Test',     ['RAM Load %', 'RAM Max °C']),
        ('Storage Test', ['Storage Load %', 'SSD Max °C']),
        ('Video Test',   ['GPU Load %', 'GPU Max °C']),
    ]
    for test_col, derived_cols in na_map:
        if data.get(test_col) == 'No':
            for dc in derived_cols:
                updates[dc] = 'NA'

    # DB-driven linked rules (template-aware)
    if template_id:
        db_rule_updates = evaluate_linked_rules(conn, template_id, data)
        # DB rules do NOT override the built-in NA logic above — NA logic takes precedence
        for col, val in db_rule_updates.items():
            if col not in updates:
                updates[col] = val

    if updates:
        set_clause = ', '.join([f'"{c}"=?' for c in updates])
        conn.execute(f'UPDATE burn_in_records SET {set_clause} WHERE id=?',
                     (*updates.values(), row_id))
    return updates

# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

@app.post("/api/auth/login")
def login():
    data = request.json
    username = data.get('username', '').lower().strip()
    if not username: return jsonify({"ok": False, "error": "Username required."}), 400
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if user and check_password_hash(user['password_hash'], data.get('password')):
        if user['force_reset']:
            return jsonify({"ok": True, "requires_reset": True, "username": user['username']})
        old_token = USER_TO_TOKEN.get(username)
        if old_token and old_token in TOKEN_TO_USER: del TOKEN_TO_USER[old_token]
        token = secrets.token_hex(32)
        TOKEN_TO_USER[token] = dict(user)
        USER_TO_TOKEN[username] = token
        log_activity(username, "System Login")
        return jsonify({"ok": True, "token": token, "username": user['username'],
                        "role": user['role'], "avatar": user['avatar'], "can_delete": user['can_delete']})
    return jsonify({"ok": False, "error": "Invalid credentials"}), 401

@app.post("/api/auth/force_reset")
def force_reset():
    data = request.json
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=?, force_reset=0, pwd_expiry=NULL WHERE username=?",
                 (generate_password_hash(data['new_password']), data['username'].lower()))
    conn.commit(); conn.close()
    log_activity(data['username'].lower(), "Forced Password Reset")
    return jsonify({"ok": True})

@app.get("/api/auth/me")
def check_session():
    user = get_current_user()
    return jsonify({"ok": True, "id": user['id'], "role": user['role'],
                    "avatar": user['avatar'], "username": user['username'],
                    "can_delete": user['can_delete']})

@app.post("/api/auth/update_profile")
def update_profile():
    user = get_current_user()
    data = request.json
    conn = get_db()
    if data.get('new_password'):
        conn.execute("UPDATE users SET first_name=?, last_name=?, email=?, phone=?, avatar=?, password_hash=? WHERE id=?",
                     (data.get('first_name'), data.get('last_name'), data.get('email'),
                      data.get('phone'), data.get('avatar'), generate_password_hash(data['new_password']), user['id']))
    else:
        conn.execute("UPDATE users SET first_name=?, last_name=?, email=?, phone=?, avatar=? WHERE id=?",
                     (data.get('first_name'), data.get('last_name'), data.get('email'),
                      data.get('phone'), data.get('avatar'), user['id']))
    conn.commit(); conn.close()
    log_activity(user['username'], "Updated profile")
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# SUPERADMIN — Users
# ─────────────────────────────────────────────

@app.get("/api/superadmin/users")
def get_users():
    if get_current_user()['role'] != 'superadmin': abort(403)
    conn = get_db()
    users = conn.execute("SELECT id, username, role, first_name, last_name, email, phone, can_delete FROM users").fetchall()
    conn.close()
    out = []
    for u in users:
        d = dict(u)
        d['is_active'] = d['username'] in USER_TO_TOKEN and USER_TO_TOKEN[d['username']] in TOKEN_TO_USER
        out.append(d)
    return jsonify(out)

@app.post("/api/superadmin/users")
def create_user():
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    data = request.json
    username = data.get('username', '').lower().strip()
    if not username: return jsonify({"ok": False, "error": "Username is required."}), 400
    expiry = (datetime.now() + timedelta(hours=72)).isoformat()
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username, password_hash, role, first_name, last_name, email, phone, force_reset, pwd_expiry, can_delete) VALUES (?,?,?,?,?,?,?,1,?,?)",
                     (username, generate_password_hash(data['password']), data['role'],
                      data['first_name'], data['last_name'], data['email'], data['phone'], expiry, data['can_delete']))
        conn.commit()
        log_activity(user['username'], f"Created user: {username}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": "Username already exists"}), 400
    finally:
        conn.close()

@app.put("/api/superadmin/users/<int:id>")
def update_user(id):
    active_admin = get_current_user()
    if active_admin['role'] != 'superadmin': abort(403)
    data = request.json
    conn = get_db()
    conn.execute("UPDATE users SET role=?, first_name=?, last_name=?, email=?, phone=?, can_delete=? WHERE id=?",
                 (data['role'], data['first_name'], data['last_name'],
                  data['email'], data['phone'], data['can_delete'], id))
    conn.commit(); conn.close()
    log_activity(active_admin['username'], f"Updated user profile ID: {id}")
    return jsonify({"ok": True})

@app.post("/api/superadmin/users/<username>/kill")
def kill_session(username):
    active_admin = get_current_user()
    if active_admin['role'] != 'superadmin': abort(403)
    token = USER_TO_TOKEN.get(username)
    if token and token in TOKEN_TO_USER:
        del TOKEN_TO_USER[token]
        del USER_TO_TOKEN[username]
    log_activity(active_admin['username'], f"Force-logged out: {username}")
    return jsonify({"ok": True})

@app.delete("/api/superadmin/users/<int:id>")
def delete_user(id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    if user['id'] == id: return jsonify({"ok": False, "error": "Self-deletion prohibited."}), 400
    conn = get_db()
    target = conn.execute("SELECT username FROM users WHERE id=?", (id,)).fetchone()
    conn.execute("DELETE FROM users WHERE id=?", (id,))
    conn.commit(); conn.close()
    if target: log_activity(user['username'], f"Deleted user: {target['username']}")
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# SUPERADMIN — Logs
# ─────────────────────────────────────────────

@app.get("/api/superadmin/logs")
def get_logs():
    if get_current_user()['role'] != 'superadmin': abort(403)
    conn = get_db()
    rows = conn.execute("SELECT * FROM activity_logs ORDER BY timestamp DESC LIMIT 500").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.delete("/api/superadmin/logs/purge/<int:days>")
def purge_logs(days):
    if get_current_user()['role'] != 'superadmin': abort(403)
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db()
    conn.execute("DELETE FROM activity_logs WHERE timestamp < ?", (cutoff,))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# FISCAL YEARS
# ─────────────────────────────────────────────

@app.get("/api/fiscal_years")
def get_fiscal_years():
    conn = get_db()
    rows = conn.execute("SELECT * FROM fiscal_years ORDER BY created_at").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/superadmin/fiscal_years")
def create_fiscal_year():
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    data = request.json
    fy_key = data.get('fy_key', '').strip()
    fy_label = data.get('fy_label', fy_key).strip()
    if not fy_key: return jsonify({"ok": False, "error": "FY key required"}), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO fiscal_years (fy_key, fy_label) VALUES (?,?)", (fy_key, fy_label))
        conn.commit()
        log_activity(user['username'], f"Created fiscal year: {fy_key}")
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "FY already exists"}), 400
    finally:
        conn.close()

@app.put("/api/superadmin/fiscal_years/<fy_key>")
def rename_fiscal_year(fy_key):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    data = request.json
    conn = get_db()
    conn.execute("UPDATE fiscal_years SET fy_label=? WHERE fy_key=?",
                 (data.get('fy_label', fy_key), fy_key))
    conn.commit(); conn.close()
    log_activity(user['username'], f"Renamed fiscal year: {fy_key}")
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# OS BUILDS
# ─────────────────────────────────────────────

@app.get("/api/os_builds")
def get_os_builds():
    conn = get_db()
    rows = conn.execute("SELECT * FROM os_builds ORDER BY os_name, os_release").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/superadmin/os_builds")
def add_os_build():
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    data = request.json
    os_name = data.get('os_name', '').strip()
    os_release = data.get('os_release', '').strip()
    build_id = data.get('build_id', '').strip()
    if not all([os_name, os_release, build_id]):
        return jsonify({"ok": False, "error": "All 3 fields required"}), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO os_builds (os_name, os_release, build_id) VALUES (?,?,?)",
                     (os_name, os_release, build_id))
        conn.commit()
        log_activity(user['username'], f"Added OS build: {os_name} {os_release} → {build_id}")
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "This OS + Release combination already exists"}), 400
    finally:
        conn.close()

@app.delete("/api/superadmin/os_builds/<int:id>")
def delete_os_build(id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    conn = get_db()
    conn.execute("DELETE FROM os_builds WHERE id=?", (id,))
    conn.commit(); conn.close()
    log_activity(user['username'], f"Deleted OS build ID: {id}")
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# LOOKUPS (Technician)
# ─────────────────────────────────────────────

@app.get("/api/lookups")
def get_lookups():
    conn = get_db()
    rows = conn.execute("SELECT * FROM lookups").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/admin/lookups")
def add_lookup():
    user = get_current_user()
    if user['role'] not in ['admin', 'superadmin']: abort(403)
    data = request.json
    conn = get_db()
    conn.execute("INSERT INTO lookups (category, lookup_key, lookup_value) VALUES (?,?,?)",
                 (data['category'], data['key'], data.get('value', '')))
    conn.commit(); conn.close()
    log_activity(user['username'], f"Added lookup to {data['category']}")
    return jsonify({"ok": True})

@app.delete("/api/admin/lookups/<int:id>")
def delete_lookup(id):
    user = get_current_user()
    if user['role'] not in ['admin', 'superadmin']: abort(403)
    conn = get_db()
    conn.execute("DELETE FROM lookups WHERE id=?", (id,))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# VAULT
# ─────────────────────────────────────────────

@app.get("/api/vault")
def get_vault():
    user = get_current_user(); conn = get_db()
    creds = conn.execute("SELECT * FROM user_creds WHERE user_id=?", (user['id'],)).fetchall()
    conn.close()
    return jsonify([{"id": c['id'], "app_name": c['app_name'], "portal_url": c['portal_url'],
                     "username": c['username'],
                     "password": cipher_suite.decrypt(c['enc_password'].encode()).decode()} for c in creds])

@app.post("/api/vault")
def add_vault_entry():
    user = get_current_user(); data = request.json
    enc_pw = cipher_suite.encrypt(data['password'].encode()).decode()
    conn = get_db()
    conn.execute("INSERT INTO user_creds (user_id, app_name, portal_url, username, enc_password) VALUES (?,?,?,?,?)",
                 (user['id'], data['app_name'], data['portal_url'], data['username'], enc_pw))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# PHASE 2: TEMPLATES
# ─────────────────────────────────────────────

@app.get("/api/templates")
def get_templates():
    conn = get_db()
    rows = conn.execute("SELECT * FROM templates ORDER BY template_name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/superadmin/templates")
def create_template():
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    data = request.json
    name = data.get('template_name', '').strip()
    if not name: return jsonify({"ok": False, "error": "Template name required"}), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO templates (template_name, created_by) VALUES (?,?)", (name, user['username']))
        conn.commit()
        log_activity(user['username'], f"Created template: {name}")
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "Template name already exists"}), 400
    finally:
        conn.close()

@app.delete("/api/superadmin/templates/<int:id>")
def delete_template(id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    conn = get_db()
    conn.execute("DELETE FROM templates WHERE id=?", (id,))
    conn.commit(); conn.close()
    log_activity(user['username'], f"Deleted template ID: {id}")
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# PHASE 2: PRESET VALUES
# ─────────────────────────────────────────────

@app.get("/api/template/<int:template_id>/preset_values")
def get_preset_values(template_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM preset_values WHERE template_id=? ORDER BY column_name, display_order, value",
        (template_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/superadmin/template/<int:template_id>/preset_values")
def add_preset_value(template_id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    data = request.json
    col = data.get('column_name', '').strip()
    val = data.get('value', '').strip()
    if not col or not val: return jsonify({"ok": False, "error": "column_name and value required"}), 400
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO preset_values (template_id, column_name, value, display_order) VALUES (?,?,?,?)",
            (template_id, col, val, data.get('display_order', 0))
        )
        conn.commit()
        log_activity(user['username'], f"Added preset value: {col}={val} (template {template_id})")
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "This value already exists for this column"}), 400
    finally:
        conn.close()

@app.delete("/api/superadmin/preset_values/<int:id>")
def delete_preset_value(id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    conn = get_db()
    conn.execute("DELETE FROM preset_values WHERE id=?", (id,))
    conn.commit(); conn.close()
    log_activity(user['username'], f"Deleted preset value ID: {id}")
    return jsonify({"ok": True})

@app.get("/api/superadmin/preset_values/<int:id>/usage_count")
def preset_value_usage_count(id):
    """Returns how many burn_in_records rows currently reference this preset value."""
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    conn = get_db()
    pv = conn.execute("SELECT column_name, value FROM preset_values WHERE id=?", (id,)).fetchone()
    if not pv:
        conn.close()
        return jsonify({"count": 0})
    col = pv['column_name']
    val = pv['value']
    # Check if the column exists in burn_in_records
    bir_cols = {row[1] for row in conn.execute("PRAGMA table_info(burn_in_records)").fetchall()}
    if col not in bir_cols:
        conn.close()
        return jsonify({"count": 0})
    count = conn.execute(
        f'SELECT COUNT(*) FROM burn_in_records WHERE "{col}"=?', (val,)
    ).fetchone()[0]
    conn.close()
    return jsonify({"count": count})

# ─────────────────────────────────────────────
# PHASE 2: LINKED RULES (data engine)
# ─────────────────────────────────────────────

@app.get("/api/template/<int:template_id>/linked_rules")
def get_linked_rules(template_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM linked_rules WHERE template_id=? ORDER BY display_order, id",
        (template_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/superadmin/template/<int:template_id>/linked_rules")
def add_linked_rule(template_id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    data = request.json
    required = ['if_column', 'if_value', 'then_column', 'then_value']
    if not all(data.get(f) for f in required):
        return jsonify({"ok": False, "error": "if_column, if_value, then_column, then_value all required"}), 400
    op = data.get('operator', 'equals')
    if op not in ('equals', 'not_equals', 'contains'):
        return jsonify({"ok": False, "error": "operator must be equals, not_equals, or contains"}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO linked_rules (template_id, rule_name, if_column, if_value, operator, then_column, then_value, display_order) VALUES (?,?,?,?,?,?,?,?)",
        (template_id, data.get('rule_name', ''), data['if_column'], data['if_value'],
         op, data['then_column'], data['then_value'], data.get('display_order', 0))
    )
    conn.commit()
    log_activity(user['username'], f"Added linked rule to template {template_id}")
    conn.close()
    return jsonify({"ok": True})

@app.delete("/api/superadmin/linked_rules/<int:id>")
def delete_linked_rule(id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    conn = get_db()
    conn.execute("DELETE FROM linked_rules WHERE id=?", (id,))
    conn.commit(); conn.close()
    log_activity(user['username'], f"Deleted linked rule ID: {id}")
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# PHASE 2: DISPLAY RULES (appearance engine)
# ─────────────────────────────────────────────

@app.get("/api/template/<int:template_id>/display_rules")
def get_display_rules(template_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM display_rules WHERE template_id=? ORDER BY display_order, id",
        (template_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/superadmin/template/<int:template_id>/display_rules")
def add_display_rule(template_id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    data = request.json
    required = ['column_name', 'operator', 'condition_value']
    if not all(data.get(f) for f in required):
        return jsonify({"ok": False, "error": "column_name, operator, condition_value required"}), 400
    op = data.get('operator')
    if op not in ('equals', 'not_equals', 'gte', 'lte', 'contains'):
        return jsonify({"ok": False, "error": "operator must be equals, not_equals, gte, lte, or contains"}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO display_rules (template_id, column_name, operator, condition_value, highlight_color, font_style, display_order) VALUES (?,?,?,?,?,?,?)",
        (template_id, data['column_name'], op, data['condition_value'],
         data.get('highlight_color', ''), data.get('font_style', ''), data.get('display_order', 0))
    )
    conn.commit()
    log_activity(user['username'], f"Added display rule to template {template_id}")
    conn.close()
    return jsonify({"ok": True})

@app.delete("/api/superadmin/display_rules/<int:id>")
def delete_display_rule(id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    conn = get_db()
    conn.execute("DELETE FROM display_rules WHERE id=?", (id,))
    conn.commit(); conn.close()
    log_activity(user['username'], f"Deleted display rule ID: {id}")
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# PHASE 2: PROJECTS
# ─────────────────────────────────────────────

@app.get("/api/projects")
def get_projects():
    conn = get_db()
    rows = conn.execute(
        "SELECT p.*, t.template_name FROM projects p LEFT JOIN templates t ON p.template_id=t.id ORDER BY p.project_name"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/superadmin/projects")
def create_project():
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    data = request.json
    name = data.get('project_name', '').strip()
    prefix = data.get('project_prefix', '').strip().upper()
    template_id = data.get('template_id')
    if not name or not prefix: return jsonify({"ok": False, "error": "project_name and project_prefix required"}), 400
    db_filename = f"{name.lower().replace(' ', '_')}.db"
    db_path = os.path.join(DATA_DIR, db_filename)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO projects (project_name, project_prefix, db_path, template_id, created_by) VALUES (?,?,?,?,?)",
            (name, prefix, db_path, template_id, user['username'])
        )
        conn.commit()
        log_activity(user['username'], f"Created project: {name} (prefix={prefix})")
        return jsonify({"ok": True, "db_path": db_path})
    except Exception as e:
        return jsonify({"ok": False, "error": "Project name or prefix already exists"}), 400
    finally:
        conn.close()

# ─────────────────────────────────────────────
# BURN-IN RECORDS
# ─────────────────────────────────────────────

@app.get("/api/fy/<fy>/data")
def get_data(fy):
    conn = get_db()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    search = request.args.get('search', '').strip()
    offset = (page - 1) * per_page

    if search:
        like = f'%{search}%'
        # Search includes reconstructed Report ID via prefix+sequence match
        rows = conn.execute(
            '''SELECT * FROM burn_in_records WHERE fy=?
               AND ("Serial Number" LIKE ? OR "Manufacturer" LIKE ? OR "Model Number" LIKE ?
               OR "Technician" LIKE ? OR "Result" LIKE ?
               OR (report_prefix || '%' || report_sequence) LIKE ?)
               ORDER BY id DESC LIMIT ? OFFSET ?''',
            (fy, like, like, like, like, like, like, per_page, offset)).fetchall()
        total = conn.execute(
            '''SELECT COUNT(*) FROM burn_in_records WHERE fy=?
               AND ("Serial Number" LIKE ? OR "Manufacturer" LIKE ? OR "Model Number" LIKE ?
               OR "Technician" LIKE ? OR "Result" LIKE ?
               OR (report_prefix || '%' || report_sequence) LIKE ?)''',
            (fy, like, like, like, like, like, like)).fetchone()[0]
    else:
        rows = conn.execute(
            "SELECT * FROM burn_in_records WHERE fy=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (fy, per_page, offset)).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM burn_in_records WHERE fy=?", (fy,)).fetchone()[0]

    conn.close()
    return jsonify({
        "rows": rows_to_dicts_with_report_id(rows),
        "total": total,
        "page": page,
        "per_page": per_page
    })

@app.post("/api/admin/fy/<fy>/rows")
def add_row(fy):
    user = get_current_user(); data = request.json; conn = get_db()
    template_id = get_template_id_for_project(conn, 'BIT')
    safe_cols = [c for c in COLUMNS if c in data and c not in FORMULA_COLS]
    cols = ['fy'] + safe_cols
    vals = [fy] + [data[c] for c in safe_cols]
    placeholders = ",".join(["?"] * len(cols))
    col_str = '","'.join(cols)
    cursor = conn.execute(f'INSERT INTO burn_in_records ("{col_str}") VALUES ({placeholders})', vals)
    row_id = cursor.lastrowid
    recalc_formula_cols(conn, row_id, data, template_id)
    assign_report_id(conn, fy, row_id, data.get('Date Performed', ''), prefix='BIT')
    conn.commit(); conn.close()
    log_activity(user['username'], "Added new Burn-In record")
    return jsonify({"ok": True})

@app.put("/api/admin/fy/<fy>/rows/<int:id>")
def update_row(fy, id):
    user = get_current_user(); data = request.json; conn = get_db()
    template_id = get_template_id_for_project(conn, 'BIT')
    safe_cols = [c for c in COLUMNS if c in data and c not in FORMULA_COLS]
    vals = [data[c] for c in safe_cols]
    set_clause = ", ".join([f'"{c}" = ?' for c in safe_cols])
    conn.execute(f'UPDATE burn_in_records SET {set_clause} WHERE id=?', (*vals, id))
    existing = conn.execute("SELECT * FROM burn_in_records WHERE id=?", (id,)).fetchone()
    merged = dict(existing) if existing else {}
    merged.update(data)
    recalc_formula_cols(conn, id, merged, template_id)
    # NOTE: report_prefix and report_sequence are NEVER updated here — permanent
    conn.commit(); conn.close()
    log_activity(user['username'], f"Edited Burn-In record ID: {id}")
    return jsonify({"ok": True})

@app.delete("/api/admin/fy/<fy>/rows/<int:id>")
def delete_row(fy, id):
    user = get_current_user()
    if not user.get('can_delete'): return jsonify({"ok": False, "error": "Deletion permission denied."}), 403
    conn = get_db()
    conn.execute("DELETE FROM burn_in_records WHERE id=?", (id,))
    conn.commit(); conn.close()
    log_activity(user['username'], f"Deleted Burn-In record ID: {id}")
    return jsonify({"ok": True})

@app.delete("/api/admin/fy/<fy>/rows/bulk")
def bulk_delete_rows(fy):
    user = get_current_user()
    if not user.get('can_delete'): return jsonify({"ok": False, "error": "Deletion permission denied."}), 403
    data = request.json
    ids = data.get('ids', [])
    if not ids: return jsonify({"ok": False, "error": "No IDs provided"}), 400
    conn = get_db()
    placeholders = ','.join(['?' for _ in ids])
    conn.execute(f"DELETE FROM burn_in_records WHERE id IN ({placeholders})", ids)
    conn.commit(); conn.close()
    log_activity(user['username'], f"Bulk deleted {len(ids)} Burn-In records")
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
# BULK UPLOAD with conflict detection
# ─────────────────────────────────────────────

@app.post("/api/admin/fy/<fy>/upload")
def upload_excel(fy):
    user = get_current_user()
    file = request.files.get("file")
    if not file: return jsonify({"ok": False}), 400
    try:
        raw = pd.read_excel(file, sheet_name="Burn-In Report", header=0)
        if not any(str(c) in COLUMNS for c in raw.columns):
            raw = pd.read_excel(file, sheet_name="Burn-In Report", header=1)
        df = raw.dropna(how="all").fillna("")

        if 'Date Performed' in df.columns:
            df['Date Performed'] = pd.to_datetime(df['Date Performed'], errors='coerce').dt.strftime('%Y-%m-%d')
            df['Date Performed'] = df['Date Performed'].fillna('')

        import_cols = [c for c in COLUMNS if c in df.columns and c not in FORMULA_COLS]
        df = df[import_cols].copy()

        conn = get_db()
        template_id = get_template_id_for_project(conn, 'BIT')
        existing = conn.execute(
            'SELECT "Serial Number", "Date Performed", "Is Retest" FROM burn_in_records WHERE fy=?', (fy,)).fetchall()
        existing_set = set()
        for e in existing:
            sn = (e['Serial Number'] or '').strip()
            dt = (e['Date Performed'] or '').strip()[:10]
            retest = (e['Is Retest'] or '').strip()
            if sn and dt and retest != 'Yes':
                existing_set.add((sn, dt))

        conflicts = []
        new_rows = []

        for _, row in df.iterrows():
            sn = str(row.get('Serial Number', '')).strip()
            dt = str(row.get('Date Performed', '')).strip()[:10]
            is_retest = str(row.get('Is Retest', '')).strip()
            key = (sn, dt)
            if sn and dt and key in existing_set and is_retest != 'Yes':
                conflicts.append({
                    'serial': sn, 'date': dt,
                    'technician': str(row.get('Technician', '')),
                    'manufacturer': str(row.get('Manufacturer', '')),
                    'model': str(row.get('Model Number', '')),
                    'row_data': {c: str(row.get(c, '')) for c in import_cols}
                })
            else:
                new_rows.append({c: str(row.get(c, '')) for c in import_cols})

        for row_data in new_rows:
            safe_cols = [c for c in import_cols if c in row_data]
            cols = ['fy'] + safe_cols
            vals = [fy] + [row_data[c] for c in safe_cols]
            placeholders = ','.join(['?'] * len(cols))
            col_str = '","'.join(cols)
            cursor = conn.execute(f'INSERT INTO burn_in_records ("{col_str}") VALUES ({placeholders})', vals)
            row_id = cursor.lastrowid
            recalc_formula_cols(conn, row_id, row_data, template_id)
            assign_report_id(conn, fy, row_id, row_data.get('Date Performed', ''), prefix='BIT')

        conn.commit(); conn.close()
        log_activity(user['username'], f"Bulk uploaded {len(new_rows)} rows to {fy}, {len(conflicts)} conflicts detected")
        return jsonify({"ok": True, "inserted": len(new_rows), "conflicts": conflicts})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/admin/fy/<fy>/upload/force")
def force_insert_rows(fy):
    user = get_current_user()
    data = request.json
    rows = data.get('rows', [])
    if not rows: return jsonify({"ok": True, "inserted": 0})
    import_cols = [c for c in COLUMNS if c not in FORMULA_COLS]
    conn = get_db()
    template_id = get_template_id_for_project(conn, 'BIT')
    for row_data in rows:
        safe_cols = [c for c in import_cols if c in row_data]
        cols = ['fy'] + safe_cols
        vals = [fy] + [row_data[c] for c in safe_cols]
        placeholders = ','.join(['?'] * len(cols))
        col_str = '","'.join(cols)
        cursor = conn.execute(f'INSERT INTO burn_in_records ("{col_str}") VALUES ({placeholders})', vals)
        row_id = cursor.lastrowid
        recalc_formula_cols(conn, row_id, row_data, template_id)
        assign_report_id(conn, fy, row_id, row_data.get('Date Performed', ''), prefix='BIT')
    conn.commit(); conn.close()
    log_activity(user['username'], f"Force-inserted {len(rows)} conflict rows to {fy}")
    return jsonify({"ok": True, "inserted": len(rows)})

# ─────────────────────────────────────────────
# EXPORTS
# ─────────────────────────────────────────────

@app.get("/api/fy/<fy>/download/xlsx")
def download_xlsx(fy):
    token = request.headers.get("X-Auth-Token") or request.args.get("token")
    if not token or token not in TOKEN_TO_USER: abort(401)
    user = TOKEN_TO_USER[token]
    if user['role'] not in ['admin', 'superadmin']: abort(403)

    conn = get_db()
    try:
        raw_rows = conn.execute('SELECT * FROM burn_in_records WHERE fy = ?', (fy,)).fetchall()
        rows_as_dicts = rows_to_dicts_with_report_id(raw_rows)
        if rows_as_dicts:
            df = pd.DataFrame(rows_as_dicts)
            drop_cols = [c for c in ['id', 'fy'] if c in df.columns]
            df = df.drop(columns=drop_cols)
            # Reorder to COLUMNS order
            present_cols = [c for c in COLUMNS if c in df.columns]
            df = df[present_cols]
        else:
            df = pd.DataFrame(columns=COLUMNS)
        os_builds = conn.execute("SELECT * FROM os_builds").fetchall()
        technicians = conn.execute("SELECT * FROM lookups WHERE category='Technician'").fetchall()
    finally:
        conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name="Burn-In Report", index=False)
        worksheet = writer.sheets['Burn-In Report']
        lookup_sheet = writer.book.create_sheet('Lookups')
        lookup_sheet.cell(row=1, column=4, value="TECHNICIANS — edit here:")

        for r_idx, ob in enumerate(os_builds, 1):
            lookup_sheet.cell(row=r_idx, column=1, value=f"{ob['os_name']}_{ob['os_release']}")
            lookup_sheet.cell(row=r_idx, column=2, value=ob['build_id'])
        for r_idx, t in enumerate(technicians, 2):
            lookup_sheet.cell(row=r_idx, column=4, value=t['lookup_key'])

        for i in range(len(df)):
            r = i + 2
            worksheet[f'A{r}'] = f'=IF(B{r}="","","BIT"&RIGHT(TEXT(YEAR(B{r}),"0000"),2)&"S"&TEXT(ROW()-1,"00"))'
            worksheet[f'V{r}'] = f'=IF(OR(T{r}="",U{r}=""),"",IFERROR(VLOOKUP(T{r}&"_"&U{r},Lookups!$A:$B,2,0),"Manual Entry"))'
            worksheet[f'AC{r}'] = f'=TRIM(IF(W{r}="Yes","CPU ","")&IF(X{r}="Yes","RAM ","")&IF(Y{r}="Yes","Storage ","")&IF(Z{r}="Yes","Video ","")&IF(AA{r}="Yes","2D ","")&IF(AB{r}="Yes","3D ",""))'
            worksheet[f'AD{r}'] = f'=IF(W{r}="No","NA","")'
            worksheet[f'AE{r}'] = f'=IF(X{r}="No","NA","")'
            worksheet[f'AF{r}'] = f'=IF(Y{r}="No","NA","")'
            worksheet[f'AG{r}'] = f'=IF(Z{r}="No","NA","")'
            worksheet[f'AH{r}'] = f'=IF(W{r}="No","NA","")'
            worksheet[f'AI{r}'] = f'=IF(X{r}="No","NA","")'
            worksheet[f'AJ{r}'] = f'=IF(Y{r}="No","NA","")'
            worksheet[f'AK{r}'] = f'=IF(Z{r}="No","NA","")'
            worksheet[f'AO{r}'] = f'=IF(AN{r}="No","NA","")'
            worksheet[f'AP{r}'] = f'=IF(AN{r}="No","NA","")'

    output.seek(0)
    return send_file(output, as_attachment=True,
                     download_name=f"BurnIn_{fy}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.get("/api/fy/<fy>/download/pdf")
def download_pdf(fy):
    conn = get_db()
    try:
        raw_rows = conn.execute('SELECT * FROM burn_in_records WHERE fy = ?', (fy,)).fetchall()
        rows_as_dicts = rows_to_dicts_with_report_id(raw_rows)
    finally:
        conn.close()

    if rows_as_dicts:
        df = pd.DataFrame(rows_as_dicts)
        drop_cols = [c for c in ['id', 'fy'] if c in df.columns]
        df = df.drop(columns=drop_cols)
    else:
        df = pd.DataFrame(columns=COLUMNS)

    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A3),
                            leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    elements = [
        Paragraph(f"ACGI IT Infra — Burn-In Report ({fy})", styles['Title']),
        Spacer(1, 12)
    ]

    if df.empty:
        elements.append(Paragraph("No records found.", styles['Normal']))
    else:
        pdf_cols = ["Report ID","Date Performed","Technician","Manufacturer","Model Number",
                    "Serial Number","CPU Tier","OS Version","OS Release","OS Build ID",
                    "Test Combination","Result","Is Retest","Notes"]
        pdf_cols = [c for c in pdf_cols if c in df.columns]
        pdf_df = df[pdf_cols]
        data_rows = [pdf_cols] + pdf_df.fillna('').values.tolist()
        t = Table(data_rows, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F29C8D')),
            ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
            ('FONTSIZE',   (0, 0), (-1, -1), 7),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FDF6F0')]),
            ('GRID',       (0, 0), (-1, -1), 0.3, colors.HexColor('#E5E7EB')),
            ('ALIGN',      (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
            ('PADDING',    (0, 0), (-1, -1), 4),
        ]))
        elements.append(t)

    doc.build(elements)
    output.seek(0)
    return send_file(output, as_attachment=True,
                     download_name=f"BurnIn_{fy}.pdf",
                     mimetype="application/pdf")

# ─────────────────────────────────────────────
# STATIC SPA
# ─────────────────────────────────────────────

@app.get("/")
@app.get("/<path:path>")
def serve_spa(path=""): return send_from_directory(app.static_folder, "index.html")

# ─────────────────────────────────────────────
# LEGACY RULE ENGINE (V7.1 — kept for backward compat)
# ─────────────────────────────────────────────

@app.get("/api/superadmin/rules")
def get_rules():
    get_current_user()
    conn = get_db()
    rules = conn.execute("SELECT * FROM rules ORDER BY priority DESC, id").fetchall()
    result = []
    for r in rules:
        conditions = conn.execute("SELECT * FROM rule_conditions WHERE rule_id=?", (r['id'],)).fetchall()
        actions = conn.execute("SELECT * FROM rule_actions WHERE rule_id=?", (r['id'],)).fetchall()
        result.append({**dict(r), 'conditions': [dict(c) for c in conditions], 'actions': [dict(a) for a in actions]})
    conn.close()
    return jsonify(result)

@app.post("/api/superadmin/rules")
def create_rule():
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    data = request.json
    conn = get_db()
    cur = conn.execute("INSERT INTO rules (rule_name, priority, is_active, created_by) VALUES (?,?,1,?)",
                       (data['rule_name'], data.get('priority', 0), user['username']))
    rule_id = cur.lastrowid
    for c in data.get('conditions', []):
        conn.execute("INSERT INTO rule_conditions (rule_id, condition_col, operator, condition_value) VALUES (?,?,?,?)",
                     (rule_id, c['col'], c['operator'], c['value']))
    for a in data.get('actions', []):
        conn.execute("INSERT INTO rule_actions (rule_id, action_col, action_value) VALUES (?,?,?)",
                     (rule_id, a['col'], a['value']))
    conn.commit(); conn.close()
    log_activity(user['username'], f"Created rule: {data['rule_name']}")
    return jsonify({"ok": True, "id": rule_id})

@app.delete("/api/superadmin/rules/<int:rule_id>")
def delete_rule(rule_id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    conn = get_db()
    conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    conn.commit(); conn.close()
    log_activity(user['username'], f"Deleted rule ID: {rule_id}")
    return jsonify({"ok": True})

@app.put("/api/superadmin/rules/<int:rule_id>/toggle")
def toggle_rule(rule_id):
    user = get_current_user()
    if user['role'] != 'superadmin': abort(403)
    conn = get_db()
    conn.execute("UPDATE rules SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?", (rule_id,))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

if __name__ == "__main__": app.run(host="0.0.0.0", port=80)
