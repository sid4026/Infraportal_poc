#!/usr/bin/env python3
"""
migrate_v7.py — ACGI-IT-Infra Portal: V6.x → V7.0 Database Migration
----------------------------------------------------------------------
Run ONCE on the host before or after upgrading to V7 server.py.
Safe to re-run — uses INSERT OR IGNORE throughout.

What it does:
  1. Creates the new `os_builds` table if it doesn't exist.
  2. Creates the new `fiscal_years` table if it doesn't exist.
  3. Migrates old OS data from `lookups` (category='OS Version_OS Release')
     into the new `os_builds` table.
  4. Seeds the default fiscal year FY-2627 if no fiscal years exist.
  5. Does NOT delete old lookup rows — they are left in place so a rollback
     to V6.x is still possible. You can clean them up manually later.

Usage:
  python3 migrate_v7.py [path/to/infra_portal.db]

  Default DB path: /opt/acgi-infra-portal/data/infra_portal.db
"""

import sqlite3
import sys
import os

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/acgi-infra-portal/data/infra_portal.db"

if not os.path.exists(DB_PATH):
    print(f"ERROR: Database not found at: {DB_PATH}")
    print("Usage: python3 migrate_v7.py [path/to/infra_portal.db]")
    sys.exit(1)

print(f"Connecting to: {DB_PATH}")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# ── Step 1: Create os_builds table ──────────────────────────────────────────
conn.execute('''
    CREATE TABLE IF NOT EXISTS os_builds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        os_name TEXT NOT NULL,
        os_release TEXT NOT NULL,
        build_id TEXT NOT NULL,
        UNIQUE(os_name, os_release))
''')
print("✓ os_builds table ready.")

# ── Step 2: Create fiscal_years table ───────────────────────────────────────
conn.execute('''
    CREATE TABLE IF NOT EXISTS fiscal_years (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fy_key TEXT UNIQUE NOT NULL,
        fy_label TEXT NOT NULL,
        is_active INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)
''')
print("✓ fiscal_years table ready.")

# ── Step 3: Migrate OS data from old lookups table ───────────────────────────
old_os_rows = conn.execute(
    "SELECT lookup_key, lookup_value FROM lookups WHERE category='OS Version_OS Release'"
).fetchall()

migrated = 0
skipped = 0
errors = 0

for row in old_os_rows:
    key = (row['lookup_key'] or '').strip()       # e.g. "Windows 10_21H2"
    build_id = (row['lookup_value'] or '').strip() # e.g. "Win10-Build74"

    if '_' not in key:
        print(f"  SKIP (bad format, no underscore): '{key}'")
        skipped += 1
        continue

    # Split on FIRST underscore only — OS names don't have underscores,
    # but releases might (e.g. "23H2-R" is fine; "Windows_11_23H2" would split wrong).
    # Convention from old system: "OS Name_OS Release"
    os_name, os_release = key.split('_', 1)
    os_name = os_name.strip()
    os_release = os_release.strip()

    if not os_name or not os_release or not build_id:
        print(f"  SKIP (empty field): os_name='{os_name}' release='{os_release}' build='{build_id}'")
        skipped += 1
        continue

    try:
        conn.execute(
            "INSERT OR IGNORE INTO os_builds (os_name, os_release, build_id) VALUES (?,?,?)",
            (os_name, os_release, build_id)
        )
        migrated += 1
        print(f"  Migrated: {os_name} | {os_release} → {build_id}")
    except Exception as e:
        print(f"  ERROR inserting '{os_name}' / '{os_release}': {e}")
        errors += 1

if not old_os_rows:
    print("  No old OS lookup rows found — nothing to migrate.")
    # Seed the three default entries so the system isn't empty
    defaults = [
        ('Windows 10', '21H2',   'Win10-Build74'),
        ('Windows 11', '23H2',   'Win11-Build42'),
        ('Windows 11', '23H2-R', 'Win11-Build42R'),
    ]
    for d in defaults:
        conn.execute("INSERT OR IGNORE INTO os_builds (os_name, os_release, build_id) VALUES (?,?,?)", d)
        print(f"  Seeded default: {d[0]} | {d[1]} → {d[2]}")

print(f"\n  OS migration: {migrated} migrated, {skipped} skipped, {errors} errors.")

# ── Step 4: Seed default FY if none exist ───────────────────────────────────
fy_count = conn.execute("SELECT COUNT(*) FROM fiscal_years").fetchone()[0]
if fy_count == 0:
    conn.execute(
        "INSERT OR IGNORE INTO fiscal_years (fy_key, fy_label, is_active) VALUES (?,?,1)",
        ('FY-2627', 'FY-2627')
    )
    print("✓ Seeded default fiscal year: FY-2627")
else:
    print(f"✓ fiscal_years already has {fy_count} row(s) — skipped seed.")

conn.commit()
conn.close()

print("\n✅ Migration complete. Start the V7 container now.")
print("   Old OS rows in 'lookups' table were NOT deleted.")
print("   Once V7 is confirmed working, you can delete them manually:")
print("   DELETE FROM lookups WHERE category='OS Version_OS Release';")
