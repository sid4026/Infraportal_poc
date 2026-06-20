# Infraportal_poc
This is for the internal infra porta;
================================================================================
PROJECT: ACGI-IT-Infra Premium Enterprise Portal
DOCUMENT: Master Architectural Blueprint & Agent Handover
VERSION: 4.4 (Comprehensive Bug Tracking & Gated Navigation)
================================================================================

1. SYSTEM OVERVIEW
--------------------------------------------------------------------------------
This is a premium-themed, Single Page Application (SPA) corporate IT portal 
powered by Python (Flask) and SQLite. 

Key Directive for Future Agents (Dual-Engine Logic): 
The Python backend dynamically calculates and injects raw Excel formulas 
(Report ID, Lookups, Combinations, Load/Temp NA toggles) ONLY during the XLSX export. 
Upon bulk import, the backend discards imported formula columns and recomputes them.
The Frontend JavaScript explicitly mimics these formulas in real-time during inline editing.

2. TERMINOLOGY & ABBREVIATIONS
--------------------------------------------------------------------------------
FFF (Feature For Future): Upcoming secure modules (Server Inventory, SSL Expiry, SOPs)
FFFM (Feature For Future on Main Page): Upcoming public modules (Achievements)
POC: Proof of Concept

3. DEPLOYMENT STRUCTURE & FILE PATHS
--------------------------------------------------------------------------------
/opt/acgi-infra-portal/                 
├── docker-compose.yml                  
├── Dockerfile                          
├── requirements.txt                    
├── server.py                           # CORE BACKEND API
│
├── data/                               # PERSISTENT VOLUME
│   ├── infra_portal.db                 # Master SQLite Database
│   └── server.key                      # AES encryption key for Vault
│
└── static/                             
    └── index.html                      # All-in-one SPA Frontend

4. ENCRYPTION & SECURITY (App Key)
--------------------------------------------------------------------------------
The Credential Vault uses symmetric AES encryption (Fernet). The key is stored in 
`/data/server.key`. If lost, passwords are unrecoverable. BACK UP THIS FILE.

================================================================================
5. VERSION HISTORY, BUG TRACKING & ARCHITECTURAL DECISIONS
================================================================================

[V1.0] Initial Scope & Architecture
- Concern: Using raw `.xlsx` files as a database risks data corruption on concurrent edits.
- Solution: Transitioned to an SQLite backend with a "Dual-Engine Logic" (SQLite for UI speed, Python openpyxl for accurate file exports).
- Clarification: Burn-In testing applies to Client Machines, not servers.

[V2.0] Role-Based Access Control (RBAC) & Theming
- Concern: IT Admin dashboard UI looked too utilitarian. 
- Solution: Wrapped the application in a premium corporate website wrapper (Warm Beige/Coral).
- Architecture: Established 4 roles: Super Admin, Admin, Viewer/Observer, and Public.

[V3.0] Formula Retention & Offline Fallback
- Concern: Openpyxl writes static values, destroying 4 complex Excel formulas (Report ID generation, Lookups, Test Combinations, and Conditional Load/Temp NA toggles).
- Solution: Python backend is designated the "Source of Truth". Uploaded files have their formula columns ignored/recalculated by Python to prevent offline data collisions.

[V4.0] Initial POC Deployment & User QA
- Bug 1: Placeholder text ("Precision Technology Solutions") present on UI. (Fixed).
- Bug 2: Login prompt openly hinted the superadmin username/password. (Fixed).
- Bug 3: "Upload XLSX" button was completely missing from the Burn-In UI. (Fixed).
- Bug 4: Date input popups were clunky and failed to append rows correctly. (Fixed).
- Bug 5: Missing ADO-style inline row editor; Report ID wasn't auto-generating in UI. (Fixed).
- Bug 6: Credential Vault "Add Credentials" button was non-functional. (Fixed).
- Bug 7: User Management tab was missing for Super Admin. (Fixed).
- Bug 8: Button read "Admin Login", which is misleading since Viewers log in too. (Changed to "Login").
- Bug 9: Clicking "Cancel" on the JS browser prompt triggered the OTP Forgot Password flow. (Eradicated JS prompts; replaced with pure HTML Modals).
- Bug 10: "Technician" and "OS Build ID" fields were free-text, allowing typos. (Added SQLite `lookups` table and UI Dropdowns).
- Bug 11: `Dockerfile` had a `.txt` extension preventing Docker execution. (Corrected).

[V4.1] Lookup & Modal Fixes
- Addressed all V4.0 bugs. Implemented a "Manage Lookups" admin modal to add predefined values, syncing perfectly with the exported Excel `Lookups` sheet.

[V4.2] Strict Column Logic & Public Security Leaks
- Bug 1: Without login, public users could see "Add Entry" and "Upload XLSX". (Root cause: CSS `display: flex` was overriding `.admin-only`. Fixed via `!important` capsuling).
- Bug 2: Public users clicking "Upload" opened file explorer. (Fixed by locking down UI).
- Bug 3: Public users got a 401 Unauthorized error when downloading XLSX. (Fixed by removing the authentication requirement from the `/api/fy/<fy>/download/xlsx` endpoint).
- Bug 4: The 43-column inline editor vanished after an upload. (Fixed DOM rendering loop).
- Enhancement: Hand-coded the entire 43-column inline HTML editor to actively mirror Excel conditional logic in real-time JS.

[V4.3] Missing HTML Pages
- Bug: FFF Tabs and About pages crashed or showed blank screens. (Root cause: Physical HTML `<div>` containers were omitted during the massive V4.2 code rewrite. Containers restored).

[V4.4] Gated FFF Navigation (Current Architecture)
- Concern/Bug: FFF tabs (Server Inventory, SSL Expiry, SOPs) completely disappeared for public users. User requested they remain visible to show portal capability, but restrict access.
- Architectural Shift: Moved from a "Stealth" model to a "Gated" model. Public users now see FFF dropdowns. 
- Solution: Built a JS `secureNav()` interceptor. Clicking an FFF tab blocks access, alerts the user, and forces the Login Modal open.

[V4.5] Modal CSS Patch (Current)
- Bug: Clicking the "Login" button (or triggering the `secureNav` interceptor) 
  failed to display the login modal on screen.
- Root Cause: A CSS targeting mismatch. The JavaScript `openModal()` function 
  correctly appended the `.active` class to the `.modal-overlay` container. 
  However, the CSS rule was erroneously targeting `.modal.active`, leaving the 
  overlay perpetually stuck in `display: none`.
- Fix: Corrected the stylesheet so `.modal-overlay.active` applies `display: flex`, 
  restoring functionality to the Login, Add User, Add Credential, and Lookups modals.

[V4.6] SuperAdmin CSS Visibility Patch (Current)
- Bug: SuperAdmin logged in successfully ("My Profile" and "Vault" became visible), 
  but the "User Management" and "Manage Lookups" buttons remained hidden.
- Root Cause: A string mismatch in the DOM security enforcement. The JavaScript 
  correctly applied the class `is-superadmin` (dynamically pulled from the DB role), 
  but the CSS rules were hardcoded to unlock features for `is-super`. 
- Fix: Corrected the CSS mapping to explicitly target `.is-superadmin`, successfully 
  unhiding the highest-tier UI elements for the default master account.

[V5.0] Enterprise Security & Full CRUD Architecture (Current)
- Bug 1-7 (Dropdowns): Added missing pre-defined dropdown arrays for CPU Architecture, 
  RAM Size, RAM Type, RAM Config, Storage Size/Type, and GPU Type.
- Bug 8 (CRUD): Added "Delete" functionality for Burn-In records.
- Bug 9 (Empty Rows): Implemented JavaScript validation preventing blank row submission.
- Bug 10-12 (User Schema & Admin): Completely rebuilt the `users` table. Added First Name, 
  Last Name, Email, and Phone. Added Edit/Delete User buttons for SuperAdmins.
- Bug 13 (Form UX & Autocomplete): Cleared password auto-filling by forcing `autocomplete="new-password"`. 
  Added lowercase enforcement for usernames to prevent symmetry breaks.
- Bug 14 (Session Persistence): Built an `/api/auth/me` endpoint. The UI now checks local 
  storage on refresh and restores the session instead of logging the user out. Added 30-min idle timeout.
- Bug 15 (Authorization): Hardened backend endpoints so ONLY SuperAdmins can delete/edit users.
- Bug 16-18 (Profile & Passwords): Added a "My Profile" modal for users to update their own details 
  and Profile Picture (Base64). Added 72-hour temporary passwords and a Forced Reset flow for new users.

[V5.1] Explicit Formulas & UI Standardization (Current)
- Bug 1 (Self-Deletion Lockout): SuperAdmins could delete their own account, causing 
  system lockout. Added a hard validation block in Python backend and removed the 
  delete button for the active session user in the UI.
- Bug 2 (Action Icons): Replaced text-based "Delete" button with industry-standard 
  Tabler Icons (Pencil & Trash). Re-engineered the editor to allow "Edit Row" functionality 
  via a PUT request.
- Bug 3 & 4 (Formula Truncation): The Python `chr(ord())` loop used to dynamically 
  generate Load/Temp formulas in V5.0 was broken and mapped incorrect columns. Ripped 
  out the loop and explicitly defined formulas for columns AD-AK, AO, and AP to 
  perfectly match the master XLSX template. 
- Bug 5 (Conditional Formatting): Frontend `loadBurnInData` logic now maps Excel 
  conditional formatting rules (Pass=Green, Temp>=90=Red, NA=Grey/Italic) directly 
  into CSS classes upon render. Added all missing specific dropdown arrays (e.g. 
  Storage Size: 120, 250, 500, 1024, 2048).

[V6.0] Enterprise Session Control & UX Polish (Current)
- Bug 1 (Icons): Third-party CDN failed to load Tabler icons. Replaced with 
  hardcoded, unblockable inline SVGs (Pencil, Trash, Power).
- Bug 2 & 3 (Export 500 Error): Pandas crashed when attempting to export a 
  completely empty database. Implemented safe-drop validation logic.
- Bug 4 (Validation & Edit): Hardened backend to reject empty/whitespace usernames. 
  Added a full "Edit User" UI for SuperAdmins.
- Bug 5 (Nav UX): Removed placeholder text from the About page. Implemented active 
  nav-state highlighters so users know which tab they are on.
- Bug 6 (Granular Deletion): Added a `can_delete` flag. SuperAdmins can now 
  restrict standard Admins from deleting Burn-In records.
- Bug 7 & 8 (Session Engine): Built a 1-to-1 Token Registry. Logging in from a 
  new device instantly invalidates the old token. SuperAdmins have a "Kill Session" 
  button to force-logout users globally.
- Bug 9 (Activity Logs): Implemented a persistent `activity_logs` table. Added a 
  dedicated SuperAdmin dashboard to view logs and purge old records (7, 30, 90 days).
- Bug 10 (Universal Lookups): Overhauled the Lookups Engine. SuperAdmins can now 
  inject predefined dropdown values for ANY of the 43 columns, not just OS/Tech.

[V6.1] Auth Payload & Pandas Parameter Patches (Current)
- Bug 1 (Silent Login Failure): The V6.0 login endpoint failed to return the `username` 
  in the JSON payload. The frontend `applyAuth()` function crashed silently when 
  attempting to run `.charAt(0)` on an undefined string to build the avatar circle, 
  leaving the user stuck on the login modal. Fixed by ensuring `username` is returned.
- Bug 2 (Public Export 500 Error): The `pd.read_sql_query` function lacked the 
  `params=(fy,)` tuple argument. This caused an SQLite binding error when attempting 
  to fetch records, resulting in a 500 Internal Server Error. Added the correct 
  parameter binding to safely generate the XLSX file.

[V7.0] Phase 1 — Architecture Overhaul & UX Upgrade (Current)
--------------------------------------------------------------------------------
ARCHITECTURAL CHANGES:

- Feature 1 (OS Builds — Single Source of Truth): Replaced flat `lookups` table OS entries
  with a dedicated `os_builds` table (os_name, os_release, build_id, UNIQUE constraint on
  os_name+os_release). All OS Version/Release dropdowns portal-wide are now driven from this
  table, not from hardcoded JS arrays. The Excel Lookups sheet is also populated from this
  table. SuperAdmins manage OS entries via a dedicated "OS Version Manager" page
  (Admin Control dropdown). When a new OS arrives (e.g. Windows 12), SuperAdmin adds one row.
  No code changes ever required.

- Feature 2 (Report ID — Permanent Server-Side Assignment): Report ID is now calculated and
  permanently stored in SQLite at insert time (both manual add and bulk upload). It is never
  recalculated after assignment. Formula: BIT + last 2 digits of year + S + zero-padded
  sequence count for that FY. Formula columns (Report ID, OS Build ID, Test Combination,
  Load/Temp NA columns) are explicitly stripped from all XLSX imports and never accepted from
  the frontend payload — the Python backend is the sole authority.

- Feature 3 (Dynamic Fiscal Years): FY is no longer hardcoded in 5 places. A new
  `fiscal_years` table (fy_key, fy_label, is_active) manages all FYs. SuperAdmins can
  create new FYs and rename existing ones without developer intervention. The Burn-In page
  shows FY tabs dynamically. The FY title ("Client Machine Analytics — FY-2627") has an
  inline pencil icon for renaming (admin only).

- Feature 4 (Bulk Upload — Conflict Detection): On XLSX upload, the backend compares incoming
  rows against existing records by Serial Number + Date Performed. Rows with Is Retest=Yes are
  always inserted (legitimate retests). For all others, conflicts are returned to the frontend.
  A dedicated conflict modal shows all conflicts at once with checkboxes — user selects which
  to force-insert. A `/api/admin/fy/<fy>/upload/force` endpoint handles the confirmed inserts.
  Time component is stripped from Date Performed on all uploads (stored as YYYY-MM-DD only).

- Feature 5 (Role-Gated Export): Public users see "Export PDF" only. Admin and SuperAdmin
  users see an "Export ▼" dropdown with both XLSX and PDF options. The XLSX endpoint now
  requires a valid admin/superadmin token (passed as ?token= query param to support direct
  browser navigation). PDF export uses ReportLab and shows key columns only in a
  landscape A3 layout.

UX CHANGES:

- Feature 6 ("Add" Hover Submenu): "Add Entry" button replaced with "Add" button that
  reveals a hover submenu: "Add New Row" and "Add New FY". Designed for future expansion
  (Add Column is reserved for Phase 2).

- Feature 7 (Search): Debounced search box filters across Serial Number, Manufacturer,
  Model Number, Technician, Result, and Report ID. Search is server-side via LIKE query.

- Feature 8 (Pagination): Server-side pagination with configurable per-page (10 / 50 / 100).
  Smart page number rendering with ellipsis for large page counts.

- Feature 9 (Multi-Select Bulk Delete): Checkbox column added before Report ID. Select All
  checkbox in header. A bulk action bar appears when rows are selected, showing count and
  a "Delete Selected" button (requires can_delete permission). Bulk delete via new
  DELETE `/api/admin/fy/<fy>/rows/bulk` endpoint.

- Feature 10 (OS Release Cascade): In the inline editor, selecting OS Version dynamically
  filters the OS Release dropdown to only valid releases for that OS (from os_builds table).
  OS Build ID auto-fills from the matched row — no manual typing, no VLOOKUP convention.

BUG FIXES IN V7.0:

- Bug 1 (Missing Profile Endpoint): `/api/auth/update_profile` endpoint was called by the
  frontend but did not exist in server.py. Added correctly.
- Bug 2 (Date Timestamp Pollution): Bulk-uploaded dates were stored as "2026-10-09 00:00:00".
  Backend now strips time on upload using `.dt.strftime('%Y-%m-%d')`.
- Bug 3 (Technician Manage Lookups): Overhauled "Manage Lookups" modal to be Technician-specific
  with add/remove UI. OS lookup management moved entirely to the new OS Version Manager page.
  The old flat OS Version_OS Release composite key convention is retired.

ARCHITECTURAL DEBT IDENTIFIED IN V7.0 (To be resolved in Phase 2 — see HANDOFF_V16):

- Inconsistency: OS Version data lives in a proper DB table (`os_builds`) and is runtime-
  manageable by SuperAdmin. But hardware spec dropdowns (CPU Tier, RAM Size, Storage Size,
  GPU Type, CPU Architecture, etc.) are still hardcoded arrays in `index.html` `BASE_ARRAYS`.
  This is inconsistent — both categories are "system-defined" values, but only OS gets a
  management UI. Phase 2 must resolve this with a unified `system_values` table and a
  SuperAdmin management interface. See HANDOFF_V16 for the full design proposal.

PHASE 2 RESERVED (Not Yet Implemented):
- Drag and drop column reordering
- Dynamic Add Column (ALTER TABLE at runtime)
- SuperAdmin Rule Engine (ADO-style conditional rules for computed column values)

================================================================================
END OF DOCUMENT
================================================================================
