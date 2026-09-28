# Big Interview ResumeAI MCP — Build Plan

> **Purpose of this document:** a complete, professional-grade specification for building a custom
> MCP (Model Context Protocol) server that gives an AI agent programmatic access to Big Interview's
> ResumeAI resume scanner. Hand this file to the implementing AI agent as the single source of truth.
> It covers architecture, tool contracts, data schemas, the target site's workflow, auth strategy,
> the tailor-and-rescan loop, safety rules, build phases, testing, risks, and definition of done.

**Document version: 1.1 — 2026-09-24.**

### Changelog v1.0 → v1.1
- Per-category badges confirmed visible on the summary page (user screenshot 2026-09-24); badge-based
  loop target restored as primary, `badge=null` demoted to rare edge case.
- Allowance "recycling" via deletion removed from required design; `delete_scan` is now optional
  history management (backup-first); the loop stops at the displayed allowance.
- One consistent success/error envelope for all six tools; complete input/output schemas; all
  parameters declared (no phantom inputs); state enums cover the reuse path; `partial` flag in schema.
- Scan identity: reuse key is now the full tuple (resume hash, JD hash, scoring guide, role, company);
  hashes are ledger-only (null for site-created scans); pagination defined; lost-ID recovery procedure added.
- Claims hedged to evidence: "no documented public ResumeAI API found"; Playwright is the chosen
  approach pending a Phase 0/1 proof of concept; first-party source links added.
- Security wording fixed: session cookies acknowledged as bearer tokens; explicit permissions and
  retention rules for `~/.resumeai-mcp/` (a `.gitignore` entry cannot protect files outside the repo).
- Editorial: consistent "Big Interview" naming; unmistakably illustrative placeholder values; precise
  definitions (stagnation, unknown vs. failed scan states).

---

## 1. Objective

Build a local MCP server (`resumeai-mcp`) that lets the user's existing Claude Code resume-tailoring
agent drive Big Interview ResumeAI end-to-end:

1. Upload a resume (PDF/DOCX) + a job description to ResumeAI.
2. Trigger a scan and wait for completion.
3. Extract the site's structured feedback (overall medal, four per-category badges, per-criterion
   results, action items, matched/unmatched ATS keywords) as strict JSON.
4. Support an automated **tailor → rescan loop** that stops when **ATS Fit badge = Gold AND
   Credibility badge = Gold** (Readability and Format are extracted and reported but are not loop
   targets). The loop stops at the displayed daily allowance — it never depends on regaining scans.
5. Keep a **local-first history** (ledger + feedback backups) so nothing is lost when scans are
   tidied up.
6. Offer `delete_scan` strictly as **optional history management** (backup-first, explicit
   confirmation for the latest result). Deletion is not part of the loop's fuel supply.

**Non-goals:** editing resumes inside Big Interview (the site doesn't support it — edits happen in
the user's own agent/document pipeline); a public/multi-user service; bypassing MFA; any
fabrication of resume content.

---

## 2. Background & facts (with evidence grades)

| # | Statement | Evidence grade | Source / note |
|---|-----------|----------------|---------------|
| 1 | **No documented public ResumeAI API found.** Big Interview's official developer documentation lists only SSO/SAML and LTI (Canvas) integrations. | Finding (not proof of nonexistence) | [Developer docs](http://support.biginterview.com/en/category/developer-documentation-cc3ddc/), searched 2026-09-22; GitHub search found zero API consumers |
| 2 | **Playwright browser automation is the chosen integration approach**, pending a Phase 0/1 proof of concept (login + session reuse + one feedback read). If the PoC fails, reassess before building further. | Architectural choice | — |
| 3 | Target: `https://neu.biginterview.com/members/resume_assignments/scan` (Northeastern portal). Feedback: `/members/resume_assignments/review_summary/<scan_id>`. The UI brands the surface "bigresume". | Verified | Live scan 2026-09-21 |
| 4 | ResumeAI evaluates **4 categories** (Readability, Credibility, ATS Fit, Format); result is a **medal**: Gold (76–100), Silver (57–75), Bronze (0–56). | Verified | [Feedback guide](https://support.biginterview.com/en/article/reviewing-resumeai-feedback-h6noe4/) + live scan |
| 5 | **Each category tile shows its own badge** on the summary page, separate from the overall medal. A Gold badge can coexist with red-flagged items inside that category. | Verified | User screenshot 2026-09-24 |
| 6 | **Daily limit: 5 scans**, resets at local midnight. Users who need more are directed to wait for the reset or request help (support/admins can reset the daily allowance). | Verified | [Scan guidance](https://support.biginterview.com/en/article/scanning-resumes-for-ai-feedback-ul90sh/), [reset guidance](https://support.biginterview.com/en/article/how-to-reset-the-daily-resumeai-scan-limit-raflbz/) |
| 7 | Deleting a previous scan *may* restore +1 daily allowance. | **User-observed, unverified — not depended upon** | Reported by user; treated as an optional probe, never as loop fuel |
| 8 | NEU login requires **Duo MFA** → fully automated login is not viable; a human establishes the session once and the machine reuses it. | User-confirmed | — |
| 9 | Scans take **under ~3 minutes** typically; upload accepts **PDF/DOCX ≤ 5 MB**, must be **text-based** (image-only PDFs break extraction). | Verified | [Scan guidance](https://support.biginterview.com/en/article/scanning-resumes-for-ai-feedback-ul90sh/) + live scan |
| 10 | A **Claude Code agent already exists** and handles resume tailoring. The MCP is its Big Interview interface — it must not duplicate tailoring logic. | User-confirmed | — |

### 2.1 Known site workflow (from the verified 2026-09-21 scan)

1. Open the scan page (must be signed in).
2. **Select Scoring Guide**: search box or org folders (`Career Design`, `Khoury - Undergraduate`,
   `Main Folder`). Selected guide shows as a heading with `DETAILS` / `Remove`. Default for this
   project: **`Graduate - STEM Focus`** (a parameter, not a constant — see §7).
3. **Add job description**: `Select Predefined` (saved JD) or `Add Job Description` (role title,
   company name, description text). For good ATS signal the description should focus on
   responsibilities, stack, requirements, preferred qualifications, education — strip EO/benefits/
   legal boilerplate.
4. **Upload resume** → verify the displayed filename matches the intended file.
5. **SCAN RESUME** → button becomes `SCANNING...` → result appears under **My Scans**.
6. **VIEW FEEDBACK** → summary (overall medal + four category tiles, each with its own badge) →
   **VIEW DETAILED FEEDBACK** (tabs per category + action items). Also offers `Download Resume`,
   `SCAN AGAIN`, back-to-My-Scans.
7. Criterion-level statuses observed: `Perfect`, `Needs Work`, plus counts like "N criteria meeting
   the required scoring", and red-flagged items that can appear even under a Gold category badge.
   The site offers `Dismiss` for false-positive grammar flags and an admin score-adjustment request —
   the MCP must **never** auto-dismiss or auto-request adjustments; it only reports.

---

## 3. Architecture

```
┌─────────────────────────┐        MCP (stdio)        ┌──────────────────────────────┐
│  Claude Code agent      │ ◄──────────────────────► │  resumeai-mcp (FastMCP)      │
│  (resume tailoring,     │   6 tools, JSON in/out   │  Python 3.11+                │
│   loop control, prompts)│                          │  ├─ server.py   (tool defs)  │
└─────────────────────────┘                          │  ├─ browser.py  (Playwright) │
                                                     │  ├─ auth.py     (session)   │
                    ┌────────────────────────────────┤  ├─ scans.py    (ops)       │
                    │  persistent Chromium profile   │  ├─ feedback.py (parser)    │
                    │  ~/.../resumeai-profile/       │  ├─ storage.py  (ledger)     │
                    │  (manual headed login once,    │  └─ schemas.py  (pydantic)   │
                    │   headless reuse after)        └──────────────┬───────────────┘
                    │                                              │ Playwright
                    └──────────────────────────────────────────────▼
                                                    neu.biginterview.com
```

**Component responsibilities**

| Component | Owns | Does NOT own |
|-----------|------|--------------|
| Claude Code agent | Tailoring decisions, loop control, stopping rules, honesty enforcement, final resume output | Browser automation, HTML parsing |
| `server.py` | FastMCP app, tool registration, input validation, the single success/error envelope | Any site-specific selectors |
| `browser.py` | Playwright lifecycle: launch persistent context (headless/headed), navigation helpers, human-like delays, HTML snapshots | Business logic |
| `auth.py` | Login-state detection, `auth_status` | Password storage (there is none — manual login) |
| `scans.py` | `list/start/poll/delete` operations, reuse-key computation, allowance reading | Feedback parsing |
| `feedback.py` | Extracting medal/badges/criteria/keywords from feedback pages into the schema (§8) | Scan triggering |
| `storage.py` | Local scan ledger (JSONL), feedback backups, HTML fixtures | — |
| `schemas.py` | Pydantic models for every tool input/output | — |
| `scripts/login.py` | One-time headed login helper (standalone, not an MCP tool) | — |

**Key design principles**

- **Thin tools, strict JSON.** Every tool validates inputs with pydantic and returns validated JSON
  inside the single envelope (§7). No prose in tool outputs.
- **Async scans, sync tools.** Scans take minutes; tools never block on completion. The agent polls.
- **Full-tuple idempotent `start_scan`.** Same (resume, JD, guide, role, company) → reuse the existing
  completed scan (§10).
- **Local-first history.** Everything the site knows is mirrored locally before any deletion.
- **Fail loudly, structured.** Every failure is a machine-readable error code (§13), never a hang.

---

## 4. Tech stack

| Choice | Version / detail | Rationale |
|--------|------------------|-----------|
| Python | 3.11+ | Matches user's environment; best Playwright + MCP ecosystem |
| FastMCP | `fastmcp` package (v2.x) | Fastest path to a spec-compliant MCP server; decorators for tools |
| Playwright (Python) | async API, Chromium | Persistent contexts, reliable selectors, `expect` auto-waiting |
| Pydantic | v2 | Input validation + JSON schema for every tool payload |
| Transport | **stdio** | What Claude Code expects for a local MCP server |
| Config | env vars + `.env` (paths, timeouts, pacing knobs) | No passwords needed by design (§6) |
| HTML parsing | Playwright locators first; BeautifulSoup/lxml only as fallback on snapshots | Locators survive minor DOM changes better than regex |

---

## 5. Repository layout

```
biginterview-resumeai-mcp/
├── README.md                  # setup, login flow, how to add to Claude Code
├── PLAN.md                    # this document (copy)
├── RUNBOOK.md                 # agent-side loop runbook for the Claude Code agent (§11)
├── pyproject.toml             # deps: fastmcp, playwright, pydantic, python-dotenv
├── .env.example               # PROFILE_DIR, HEADLESS, timeouts, pacing knobs
├── .gitignore                 # profile dir, .env, snapshots, __pycache__, *.log
├── src/
│   └── resumeai_mcp/
│       ├── __init__.py
│       ├── server.py          # FastMCP() app + 6 tool definitions
│       ├── config.py          # Settings dataclass from env (paths, timeouts, pacing)
│       ├── browser.py         # ensure_context(headless=...), goto, human_delay(), snapshot()
│       ├── auth.py            # is_logged_in(page), auth_status()
│       ├── scans.py           # list_scans, start_scan, get_scan_status, delete_scan
│       ├── feedback.py        # get_scan_feedback: extract + parse → ScanFeedback
│       ├── storage.py         # ledger JSONL, backup_feedback(), save_snapshot()
│       └── schemas.py         # pydantic models (see §8)
├── scripts/
│   └── login.py               # headed one-time login: opens scan page, waits for manual login
└── tests/
    ├── test_schemas.py
    ├── test_reuse_key.py
    ├── test_browser.py           # profile-lock detection, single-launch context init
    ├── test_auth.py              # login_state: redirect / stalled SSO / missing landmark (fake page)
    ├── test_storage.py           # snapshot/dir permissions, unique snapshot names
    ├── test_scans.py             # list_scans: fixture parse, ledger join, pagination (Phase 1)
    ├── fixtures/*.fixture.html   # redacted real pages, committed (§16/§17 amendment 2026-09-28)
    └── test_feedback_parser.py   # parser tests against saved HTML fixtures (no live site)
```

---

## 6. Authentication design (persistent profile, manual login)

**Rationale:** NEU login requires Duo MFA, which cannot be automated reliably. No username/password
is ever stored. The human logs in once; the machine reuses the session.

### 6.1 Mechanics

- Playwright launches with `launch_persistent_context(user_data_dir=PROFILE_DIR, ...)`.
  Per Playwright's persistent-context documentation, session storage (cookies, localStorage) persists
  in `PROFILE_DIR` across launches, in both headed and headless modes — headless vs. headed is only
  a launch flag. **Headless reuse working in this environment is an assumption to verify in the
  Phase 0 PoC, not a guarantee.**
- `scripts/login.py`:
  1. Launches **headed** (`headless=False`) with the persistent profile.
  2. Navigates to the scan page.
  3. Prints "Log in via SSO + Duo, then press ENTER here." and blocks.
  4. On ENTER: calls `auth.is_logged_in()` to verify, prints the result, closes.
- **Phase 0 PoC finding (2026-09-28, gate passed):** the portal's own session cookie is
  session-scoped and does not survive a browser restart. What persists in `PROFILE_DIR` is the
  Microsoft (NEU IdP) SSO session: each fresh launch is redirected through SAML, re-authenticates
  silently (no Duo prompt), and lands on `/members/interview_dashboard/` instead of the requested
  page. Session lifetime is therefore bounded by the IdP session, not the portal. `is_logged_in`
  waits for the redirect chain to settle on `/members/`, re-opens the target page, then checks
  the landmark.
- Normal MCP operation launches **headless** with the same `PROFILE_DIR`.
- **Never run `login.py` and the MCP server concurrently** — Playwright documents that a
  persistent context's user-data directory cannot be shared by two simultaneous launches.
  `browser.py` must detect the lock and return a clear error instead of corrupting the profile.

### 6.2 Expiry handling

- Every tool that touches the site first runs `auth.is_logged_in(page)` (heuristic: absence of
  login/SSO redirect + presence of an authenticated marker such as the My Scans list or account
  menu). If not logged in → return the structured error `auth_expired` (§13). Never attempt to
  log in automatically, never hang waiting.
- `auth_status` (§7) lets the agent check *before* starting a loop.
- Optional keep-alive: a daily scheduled task that loads the homepage headlessly to keep the
  session warm (reduces but does not eliminate expiry).

### 6.3 Headless and fingerprint notes (assumptions to test, not guarantees)

- Using the real Chrome channel with a persistent profile makes the browser *look like a normal
  user installation*; this improves fingerprint plausibility but **does not guarantee** the
  automation is undetectable. Treat as an assumption validated by the PoC, not a fact.
- If the site misbehaves headless, fallback order: (1) Playwright's new headless mode via
  `channel="chrome"`, (2) headed under `xvfb` on Linux, (3) headed (last resort — breaks
  unattended use; surface to user).

### 6.4 Security (corrected)

- `PROFILE_DIR` contains **session cookies, which are bearer tokens**: it *is* login material.
  `chmod 700`, never commit, never copy to shared machines, never upload. The earlier shorthand
  "no tokens anywhere" was inaccurate — the precise statement is: **no passwords are stored; the
  only authentication material is the OS-protected browser profile.**
- The login helper never reads keystrokes beyond ENTER and never handles the password itself.

---

## 7. MCP tool specifications

Six tools. Keep it at six — every extra tool costs the agent context on every call. Each tool
description (the `description=` string in FastMCP) must include: what it does, when to use it, the
honesty rule (for `get_scan_feedback`), and pacing notes. Descriptions are part of the contract.

### 7.0 The single envelope (normative for all six tools)

Every tool returns exactly one of these two shapes. There are no bare payloads and no exceptions.

```jsonc
// success
{ "ok": true, "data": { /* tool-specific, schema-validated */ } }

// error
{ "ok": false, "error": { "code": "auth_expired",
                          "message": "human-readable, no PII",
                          "hint": "concrete next action for the agent" } }
```

Error codes are catalogued in §13.

### 7.1 `auth_status()`

- **Purpose:** Check login state and remaining daily allowance *before* starting work.
- **Inputs:** none.
- **Success `data` (`AuthStatus`):**
  | Field | Type | Notes |
  |-------|------|-------|
  | `logged_in` | bool | — |
  | `scans_remaining` | int \| null | null when the counter isn't rendered — never guess |
  | `account_email` | string \| null | null when not visible |
  | `checked_at` | datetime | — |
- **Behavior:** Loads the scan page (or My Scans), detects login state, reads the remaining-scan
  counter if visible. `logged_in=false` is *data*, not an error.

### 7.2 `list_scans(limit: int = 20, cursor: string | null = null)`

- **Purpose:** History of scans for reuse checks and deletion candidates.
- **Inputs:**
  | Param | Type | Constraints |
  |-------|------|-------------|
  | `limit` | int | 1–50, default 20 |
  | `cursor` | string \| null | opaque pagination cursor from a previous response |
- **Success `data`:** `{ "scans": [ScanSummary], "next_cursor": string | null }`
  (`next_cursor=null` means the last page.)
- **`ScanSummary` fields:**
  | Field | Type | Notes |
  |-------|------|-------|
  | `scan_id` | string | numeric id from the feedback URL (`review_summary/<id>`) |
  | `role_title` | string | — |
  | `company` | string \| null | — |
  | `scoring_guide` | string \| null | null when not shown on the row |
  | `scanned_at` | datetime \| null | — |
  | `medal` | "gold" \| "silver" \| "bronze" \| null | — |
  | `resume_filename` | string | — |
  | `resume_sha256` | string \| null | **ledger-only**: present only for scans created by this MCP; null for site-created scans |
  | `jd_sha256` | string \| null | **ledger-only**, same rule |
- **Rule:** hashes are never scraped from the site (they aren't visible there). They are joined in
  from the local ledger by `scan_id`; scans without a ledger entry report null hashes and are
  **never eligible for reuse** (§10).
- **Amendment 2026-09-28 (Phase 1 finding):** My Scans rows show neither company nor scoring guide.
  `company` and `scoring_guide` are therefore filled from the ledger entry for MCP-created scans
  (the site value wins if the site ever provides `scoring_guide`) and are null for site-created scans.

### 7.3 `start_scan(resume_path, job_title, company, job_description, scoring_guide = "Graduate - STEM Focus")`

- **Purpose:** Upload a resume + JD and trigger a scan. Idempotent on the full reuse key.
- **Inputs:**
  | Param | Type | Notes |
  |-------|------|-------|
  | `resume_path` | string | local absolute path; PDF/DOCX; ≤ 5 MB; must contain extractable text (validated before upload) |
  | `job_title` | string | e.g. "AI Intern" |
  | `company` | string | e.g. "Welldoc" |
  | `job_description` | string | full JD text; server strips EO/benefits/legal boilerplate heuristically |
  | `scoring_guide` | string | default `"Graduate - STEM Focus"`; must match a visible guide or the call fails with `invalid_input` |
- **Success `data`:**
  | Field | Type | Notes |
  |-------|------|-------|
  | `scan_id` | string | — |
  | `reused` | bool | true when an existing completed scan matched the reuse key |
  | `state` | "queued" \| "scanning" \| "complete" | `"complete"` only on the reuse path; fresh scans report `queued`/`scanning` |
  | `scans_remaining` | int \| null | — |
- **Behavior:**
  1. Validate file (exists, extension, size, extractable text).
  2. Compute the reuse key: `(resume_sha256, jd_sha256(normalized), scoring_guide, job_title, company)`.
     JD normalization: lowercase, collapsed whitespace, stripped.
  3. Look up the ledger + `list_scans`: a **completed** scan matching the full key → return it with
     `reused=true`, `state="complete"` (zero allowance consumed).
  4. Else run the site workflow (§2.1): select scoring guide → Add Job Description (fill title,
     company, description) → upload → **verify the displayed filename equals the intended basename**
     → SCAN RESUME → capture the new scan id from My Scans (poll up to 60 s for the row).
  5. Write the ledger entry immediately after capturing the id (see §10 recovery).
  6. If the site reports the daily limit is reached → error `limit_reached`. The tool does not
     delete anything; allowance management is the agent's explicit decision.
- **Human-pacing:** random 2–5 s delays between the workflow steps (§12).
- **Amendment 2026-09-28 (Phase 3, form explored live without scanning):**
  - Landmarks: searchbox "Search for a score guide" → button "Select <guide>" (exact; absent →
    `invalid_input`) → heading "<guide>" + button "Remove" → radio "Add Job Description" → textboxes
    "Role / Position", "Company Name", "Job Description" → file input → heading "<basename>" (the
    filename check) → button "Scan Resume". Each missing landmark → `site_changed` + snapshot.
  - The id is captured from My Scans page 1 (up to 60 s): newest row **not listed before the click**
    (ids read at the start of the call), with the same filename (the site stores spaces as underscores)
    and role title, created at/after the click within a 60 s clock-skew allowance (safe only because
    pre-existing ids are excluded). A captured row the site already shows as failed → `scan_failed`
    (id kept); unreadable → `unknown_state`; already complete (fast scan) → `state="complete"`.
  - Every control is asserted visible before use, including the Scan Resume button before the attempt
    is recorded; a missing one → `site_changed` + snapshot, never a timeout.
  - All six tools run one at a time (they drive one shared browser page), so two concurrent
    `start_scan` calls cannot both pass the preflight.
  - Reuse compares guide/title/company trimmed and case-insensitively; `jd_sha256` hashes the
    normalized JD *as given* (before boilerplate stripping), so reuse survives heuristic changes.
  - §12 "one scan in flight": a ledger scan started < 15 min ago that the site still shows as
    queued/scanning → `invalid_input` ("poll get_scan_status").
  - `scans_remaining = 0` → `limit_reached` before any form interaction.

### 7.4 `get_scan_status(scan_id: string)`

- **Purpose:** Poll for completion. Cheap, fast, safe to call repeatedly.
- **Inputs:** `scan_id` (string).
- **Success `data`:**
  | Field | Type | Notes |
  |-------|------|-------|
  | `scan_id` | string | echo |
  | `state` | "queued" \| "scanning" \| "complete" \| "failed" \| "unknown" | `failed` = site explicitly shows failure; `unknown` = row missing/unreadable — **never silently mapped to `failed`** |
  | `scans_remaining` | int \| null | — |
  | `checked_at` | datetime | — |
- **Behavior:** On `unknown`, save an HTML snapshot and include a hint to inspect it; the agent
  treats `unknown` as "stop and investigate", not as failure.
- **Amendment 2026-09-28 (Phase 3):** state comes from the My Scans row `status` (page 1; older ids
  from their review_summary page, same field). Observed: `success` → `complete`. Explicit
  `failed`/`error` → `failed`; `processing`/`scanning` → `scanning`; `pending`/`queued` → `queued`
  (in-progress values to be confirmed during the first live scan); anything else → `unknown`. An id
  neither the site nor the ledger knows → `not_found`; one only the ledger knows → `unknown`. On
  `complete` the ledger entry gets `completed_at` + `medal`.
- **Pacing note in description:** "Poll no more than once every 20 seconds."

### 7.5 `get_scan_feedback(scan_id: string)`

- **Purpose:** Extract the full structured feedback for a **completed** scan.
- **Inputs:** `scan_id` (string).
- **Success `data`:** `ScanFeedback` (§8), including its `partial` flag when only the summary was readable.
- **Behavior:** Opens the feedback page, reads the overall medal + the four per-category badges from
  the summary tiles, expands `VIEW DETAILED FEEDBACK`, walks the four category tabs, parses
  criteria/action items/keyword lists into the schema. Saves the raw HTML snapshot via `storage.py`.
  If `state != complete` → error `invalid_input`.
- **Amendment 2026-09-28 (Phase 2 finding, user-approved):** the review_summary page embeds everything
  the detailed tabs render (medal, the four category badges, every criterion's gold/silver/bronze score
  and site advice, org importance, keyword data, company, scoring guide) as JSON in the
  `ResumeAssignmentReviewSummaryApp` element's `data-react-props`. The detailed tabs show one criterion
  at a time (~26 clicks per scan), so the tool loads **only the summary page** and parses that JSON —
  no clicks. Missing/malformed props → `site_changed` + snapshot; a redirect away from
  `review_summary/<id>` → `not_found` (heuristic, not yet verified with a bogus id). The full
  `ScanFeedback` is backed up to `feedback/<scan_id>.json` (0600, atomic write) on every successful read.
- **Description must contain the honesty rule**, e.g.: "Unmatched keywords are verification
  candidates, not a shopping list. Only add a keyword to the resume when it is backed by real
  experience, project, or coursework. Never fabricate skills, metrics, or employment."

### 7.6 `delete_scan(scan_id: string, confirm: bool = false)`

- **Purpose:** Optional history management — tidy up My Scans. **Not loop fuel.**
- **Inputs:**
  | Param | Type | Notes |
  |-------|------|-------|
  | `scan_id` | string | — |
  | `confirm` | bool | default false; required true to delete the latest result of the active loop iteration |
- **Success `data`:**
  | Field | Type | Notes |
  |-------|------|-------|
  | `deleted` | bool | — |
  | `backup_path` | string | local path of the feedback backup (always written before deletion) |
  | `scans_remaining` | int \| null | re-read after deletion |
  | `allowance_restored` | bool \| null | true if the counter increased vs. before; **informational only — the loop must not depend on it**; null if the counter is unreadable |
- **Behavior (order matters):**
  1. Fetch + back up feedback locally if not already present. If the backup fails → error
     `storage_error` and **abort the deletion**.
  2. Refuse to delete the latest loop result unless `confirm=true`.
  3. Delete via the site's UI (confirm dialog if present).
  4. Re-read the allowance counter and report `allowance_restored` truthfully.
- **Amendment 2026-09-28 (Phase 3):** "latest loop result" = the newest non-deleted ledger scan (by
  `started_at`). An existing backup is re-validated as `ScanFeedback` before being trusted. The row's
  menu → DELETE; a native `confirm()` or an ARIA dialog's Delete/Yes/Confirm/OK button is accepted
  (dialog shape not yet verified live). Deletion is verified by reloading My Scans; a row still
  listed → `site_changed` + snapshot. The ledger entry gets `deleted_at`.

---

## 8. Data schemas (`schemas.py`)

All models are pydantic v2. Field names below are normative — the agent parses them programmatically.
Placeholder values below are unmistakably illustrative and must never be treated as real data.

```jsonc
// ScanFeedback — data of get_scan_feedback(scan_id)
{
  "scan_id": "675751",
  "resume_filename": "Resume_AIIntern_Welldoc (1).pdf",
  "resume_sha256": "sha256:ILLUSTRATIVE_PLACEHOLDER_DO_NOT_USE",
  "role_title": "AI Intern",
  "company": "Welldoc",
  "scoring_guide": "Graduate - STEM Focus",
  "scanned_at": "2026-09-21T10:26:00-04:00",
  "medal": "gold",                        // gold | silver | bronze
  "partial": false,                       // true when only the summary was readable
  "categories": {
    "readability":  { "badge": "gold", "criteria": [ Criterion ] },
    "credibility":  { "badge": "gold", "criteria": [ Criterion ] },
    "ats_fit":      { "badge": "gold", "criteria": [ Criterion ],
                      "keywords_matched": ["Python", "PyTorch", "LLM"],
                      "keywords_unmatched": ["RAG", "LangChain", "Hugging Face"] },
    "format":       { "badge": "gold", "criteria": [ Criterion ] }
  },
  "action_items": [
    { "category": "ats_fit",
      "item": "Job Title Match flagged although badge is Gold — verify title alignment",
      "priority": "high" }
  ]
}

// Criterion
{ "name": "Contact Information",
  "status": "perfect",        // perfect | meets | needs_work | warning
  "detail": "Full name, email, city/state, phone, LinkedIn confirmed.",
  "suggestion": null }

// AuthStatus — data of auth_status()
{ "logged_in": true, "scans_remaining": 3,
  "account_email": "user@neu.edu", "checked_at": "2026-09-24T22:00:00-04:00" }

// ScanSummary — items of list_scans()
{ "scan_id": "675751", "role_title": "AI Intern", "company": "Welldoc",
  "scoring_guide": "Graduate - STEM Focus",
  "scanned_at": "2026-09-21T10:26:00-04:00", "medal": "gold",
  "resume_filename": "Resume_AIIntern_Welldoc (1).pdf",
  "resume_sha256": "sha256:ILLUSTRATIVE_PLACEHOLDER_DO_NOT_USE",
  "jd_sha256": null            // null: this scan was created on the site, not via this MCP
}
```

**Parsing rules for `feedback.py`:**

- `badge` values normalize to `gold|silver|bronze`. Per-category badges are visible on the summary
  tiles (verified via screenshot 2026-09-24) and are the primary signal. `badge=null` is permitted
  **only** as a rare edge case (e.g., the badge icon fails to render); the loop treats a null badge
  in a target category as "cannot evaluate → stop and report", never as Gold.
- A Gold badge does **not** imply zero issues: red-flagged items (e.g., "Job Title Match") can appear
  inside a Gold category. All flagged items must appear in `criteria` (with status `needs_work` or
  `warning`) and be surfaced in `action_items` regardless of the badge.
- `status` normalizes: `Perfect→perfect`, `Meets…→meets`, `Needs Work→needs_work`,
  flagged/alert items→`warning`. Unknown labels → `warning` + raw text in `detail`. The parser never
  throws on an unknown label.
- `keywords_matched/unmatched`: only for `ats_fit`, from the site's keyword lists. Preserve the
  site's exact casing; matching elsewhere is case-insensitive.
- If the detailed-feedback tabs fail to load but the summary medal/badges are visible, return the
  summary with `"partial": true` and empty `criteria` arrays rather than failing.

**Amendment 2026-09-28 (Phase 2 findings, user-approved):**
- The site shows three criterion labels, one per score type: gold "Good Work!" → `perfect`; bronze
  "Needs Work!" → `needs_work`; silver "Almost There" (not a label listed above, and flagged on the
  summary page) → `warning` per the unknown-label rule. `meets` is unused until the site shows a
  "Meets…" label. `detail` = the site label + the site's advice text for that score.
- `criteria` per category = the category's direct children (the site's per-tab Action Items list:
  4 / 9 / 7 / 6 today). Depth-2 sub-criteria (e.g. Body/Header Font Size) roll up into their parent.
- `suggestion` = the site's `improvement_advice` for that criterion when it ships one, else null (the
  other "How to improve" texts are computed client-side from raw data).
- `action_items` = every non-`perfect` criterion; `priority` = the scoring guide's importance
  (high/medium/low) for that criterion; items under a Gold badge are marked as such.
- `keywords_matched` = the site's resume-keyword list (site order and casing); `keywords_unmatched` =
  job keywords not matched case-insensitively. Verified against the rendered ATS Fit lists and the
  keyword-match score on 3 real scans.
- `partial=true` when the summary JSON yields badges but no criteria.
- `medal=null` only when the site's score type is not gold/silver/bronze (never guessed; the loop
  treats it like a null badge: cannot evaluate → stop and report). `priority` falls back to `medium`
  when the scoring guide gives no importance for a criterion.

---

## 9. Site workflow reference (for the automation implementer)

Base: `https://neu.biginterview.com`. Scan page: `/members/resume_assignments/scan`.
Feedback: `/members/resume_assignments/review_summary/<scan_id>`.
Note: the product surface brands itself **"bigresume"** in the nav; "Big Interview" is used in this
document for the platform family.

**Selector strategy (normative):** prefer Playwright's semantic locators —
`page.get_by_role("button", name="SCAN RESUME")`, `get_by_text`, `get_by_label` — over CSS/XPath.
Absolute XPaths are forbidden. After each major step, assert a visible landmark before continuing
(e.g., guide heading visible after selection; filename text matches after upload). Save an HTML
snapshot on every failure.

| Step | Landmarks / notes |
|------|-------------------|
| Load scan page | Wait for "Select Scoring Guide" heading. If redirected to SSO/login → raise `auth_expired`. |
| Select guide | Use the search box; type the guide name; click the result. Verify the guide heading + `DETAILS`/`Remove` controls are visible. If the guide isn't found → `invalid_input` (don't proceed with the wrong guide). |
| Add JD | Click "Add Job Description" (preferred for determinism over "Select Predefined"). Fill role title, company, description. |
| Upload | `Upload Resume` → `CHOOSE FILE` → `set_input_files(resume_path)`. **Verify the displayed filename equals the intended basename.** |
| Scan | Click `SCAN RESUME`; expect it to become `SCANNING...`. Capture the new scan's id from My Scans (newest row) — poll up to 60 s for the row to appear. |
| Status | My Scans row shows role, time, filename, `VIEW FEEDBACK` when done. |
| Feedback | `VIEW FEEDBACK` → summary: overall medal banner + four category tiles **each with its own badge**; `VIEW DETAILED FEEDBACK` → tabs: Readability / Credibility / ATS Fit / Format; action-items list on the side. |
| Delete | Per-row delete control in My Scans; confirm dialog if present. Re-read allowance counter after. |
| Allowance counter | The "N scans remaining / daily limit" indicator near the scan button (exact label may vary — locate by regex `/\d+\s*(scans? )?(remaining|left)/i`). |

**Amendment 2026-09-28 (Phase 1 findings, live read-only visit):**
- Allowance counter renders as the heading "N scans left today." on the scan page (regex above matches).
  `account_email` is not rendered on the scan page → null.
- My Scans lives at `/members/resume_assignments/scans`, 10 rows/page, `?page=N` selects the page
  server-side. It is a React app whose rows are embedded as JSON in the `data-react-props` attribute
  of the `[data-react-class="UserResumeAssignmentScansApp"]` element (`parsedResumes.data[]` with
  `id`, `job_title`, `document_file_name`, `highest_score`, ISO-UTC `created_at`, `status`;
  `paginationData.count/from`). The rendered row exposes the medal only as a CSS icon class and the
  date without a timezone, so `list_scans` reads the embedded JSON (a deliberate, approved deviation
  from the locator preference above). Missing/malformed props → `site_changed` + snapshot.

**Change-detection:** if a landmark from the table above is missing, do not guess — save snapshot,
return `site_changed` with the missing landmark named. This is how the system degrades instead of
clicking blindly.

---

## 10. Scan lifecycle, idempotency, pagination & recovery

```
              ┌─────────┐  start_scan   ┌─────────┐  poll    ┌──────────┐
              │  (none) ├──────────────►│ queued/ ├─────────►│ complete ├─► get_scan_feedback
              └─────────┘  (or reused)  │ scanning│  20 s    └──────────┘
                                        └────┬────┘
                                             ├─ failed ──► error (site explicitly failed; keep id)
                                             └─ unknown ─► snapshot + investigate (NOT failure)
```

### 10.1 Reuse key (normative)

A scan is reusable **iff** all five match and its state is `complete`:

```
(resume_sha256, jd_sha256_normalized, scoring_guide, job_title, company)
```

- Matching on resume+JD alone is **insufficient**: the same files under a different scoring guide,
  role, or company can legitimately score differently.
- `jd_sha256` normalization: lowercase, collapsed whitespace, stripped.
- Only scans with ledger entries (created via this MCP) carry hashes; site-created scans report
  null hashes and are never reused.

### 10.2 Ledger writes & lost-ID recovery

- `start_scan` writes the ledger entry **immediately after capturing the scan id** from My Scans,
  before returning to the agent.
- **Recovery procedure** (site accepted the scan but the MCP crashed/lost the response before
  recording the id): on next run, `list_scans` → adopt the newest row whose `resume_filename`
  matches and whose `scanned_at` is within a 10-minute window of the attempt; mark the ledger
  entry `"recovered": true`. If no row matches → report `unknown_state` and stop; never invent an id.
- **Amendment 2026-09-28 (Phase 3):** before clicking SCAN RESUME, `start_scan` appends an *attempt*
  entry (`scan_id: null`, `attempt_id`, filename, hashes, role, company, guide, `started_at`); after
  capture it appends the scan entry and marks the attempt `resolved_scan_id`. The attempt also stores
  `known_ids` (rows listed before the click), which recovery never adopts. Unresolved attempts are
  retried at every `start_scan`, which returns `unknown_state` (blocking a duplicate submission)
  while the attempt's 10-minute window is still open; once the window has passed with no matching
  row the attempt is marked `abandoned` (reported that one last time) and scanning may resume.

### 10.3 Pagination

- `list_scans` pages through My Scans with an opaque `next_cursor`; the agent follows cursors until
  `next_cursor=null` or it finds what it needs. Page size is an internal detail (default 20).

### 10.4 Polling contract (agent side)

- `get_scan_status` every **20 s**; give up after **10 min** → treat as `scan_timeout`, keep the
  scan id, report to user. Never poll faster.

### 10.5 Deletion is history management, not fuel

- The agent may call `delete_scan` to tidy My Scans (oldest, lowest-medal first; never the latest
  loop result without `confirm=true`). Feedback is always backed up first (§7.6).
- `allowance_restored` in the response is **informational only**. The loop's allowance logic reads
  `scans_remaining` and stops at zero — it must not plan around deletion restoring scans.

---

## 11. Agent loop contract (Claude Code side — not MCP code, but the MCP must enable it)

The MCP exposes the tools; the **agent owns the loop**. This is the normative loop the MCP is
designed to support. Encode it as `RUNBOOK.md` for the Claude Code agent.

```
1. s = auth_status()
   - if not s.logged_in → STOP, tell user: "Big Interview session expired. Run: python scripts/login.py"
   - note s.scans_remaining; if 0 → STOP ("daily allowance exhausted; resume tomorrow")
2. existing = list_scans()   # follow next_cursor as needed
   - if a completed scan matches the FULL reuse key (§10.1) → use its feedback, skip to step 4
3. r = start_scan(resume, title, company, jd)   # idempotent; may return reused=true
4. LOOP (iteration i = 1..MAX_ITERATIONS=8):
     a. poll get_scan_status(r.scan_id) every 20 s, timeout 10 min
        - state=unknown → STOP, snapshot saved, needs investigation (not a failure)
     b. fb = get_scan_feedback(r.scan_id)   # envelope: {ok, data|error}
     c. TARGET CHECK (primary, badge-based — badges are visible per §2 #5):
        if fb.categories.ats_fit.badge == "gold"
           and fb.categories.credibility.badge == "gold":
            DONE → report medal + per-iteration summary (see d)
        if either target badge is null → STOP ("cannot evaluate target; needs human review")
     d. REPORT flagged items even under Gold: any criterion with status needs_work/warning
        in any category goes into the iteration report (e.g. "Job Title Match flagged under
        ATS Gold — verify title alignment").
     e. STAGNATION CHECK: "no improvement" = neither target category improved vs the previous
        iteration, where improvement = badge upgrade OR fewer needs_work/warning criteria.
        If the comparisons (i-1 vs i-2) AND (i vs i-1) both show no improvement → STOP with
        "diminishing returns; manual review advised". (Two consecutive comparisons, precisely.)
     f. TAILOR the resume from fb (honesty rule below), write new file v{i+1}
     g. if scans_remaining == 0 → STOP with limit_reached ("allowance exhausted; resume next
        day or request a reset"). Optional: tidy history with delete_scan (backup-first);
        never plan on it restoring allowance.
     h. r = start_scan(new_resume, title, company, jd)
5. On any structured error → follow §13. Never retry the same failing call more than twice.
```

**Stopping rules (all must be implemented):** Gold/Gold reached · `MAX_ITERATIONS=8` ·
two consecutive stagnant comparisons · `limit_reached` (allowance exhausted) · `auth_expired` ·
`site_changed` / `unknown` state · unevaluable (null) target badge.

**Honesty rule (paste into the agent prompt verbatim):**

> You are optimizing for ATS Fit and Credibility badges. Unmatched keywords are *verification
> candidates*, not a shopping list. You may add a keyword/skill/metric ONLY when it is backed by
> the user's real experience, projects, coursework, or measurable work. NEVER invent employers,
> dates, degrees, metrics, or skills. NEVER keyword-stuff (repeating a term without genuine
> context). If a keyword cannot be truthfully supported, leave it unmatched and say so in the
> report. A truthful Silver beats a fabricated Gold.

---

## 12. Human-like pacing & account safety

The user explicitly requires cautious, human-like operation on a personal university-licensed
account. These are normative:

| Rule | Value |
|------|-------|
| Delay between UI actions (clicks, fills) | random **2–5 s** (`human_delay()` in `browser.py`) |
| Status poll interval | **≥ 20 s** (stated in `get_scan_status` description) |
| Scans in flight | **1** — never parallelize scans |
| Scan operations per calendar day (starts + deletes) | **≤ 12** |
| Operating window | daytime in the user's timezone; no 24/7 unattended marathons |
| Browser fingerprint | real Chrome channel, persistent profile — improves plausibility; **not a guarantee of undetectability** |
| Scope discipline | only the scan/JD/feedback flows in §9; never touch admin, billing, or other users' data |

What we are **not** doing: password storage, MFA bypass, CAPTCHA solving, scraping other
accounts, or exceeding what the site's own UI permits a human to do.

---

## 13. Error catalog (normative codes)

| Code | Meaning | Agent action |
|------|---------|--------------|
| `auth_expired` | Session dead (SSO redirect / login page) | STOP loop. Tell user to run `scripts/login.py`. Do not retry. |
| `limit_reached` | Daily allowance exhausted | STOP. Report; resume next day or ask user to request a reset. |
| `scan_failed` | Site explicitly reported the scan failed | Keep the scan id for manual follow-up; retry once with a fresh upload, then STOP. |
| `scan_timeout` | No completion within 10 min of polling | Keep the scan id; STOP; user may check My Scans manually. |
| `unknown_state` | Scan row missing/unreadable; state genuinely unknown (distinct from confirmed failure) | STOP. Snapshot already saved. Investigate before any retry. |
| `not_found` | Unknown `scan_id` | Re-run `list_scans`; likely a typo or a deleted scan. |
| `site_changed` | Expected landmark missing (selector drift) | STOP. Snapshot saved. Needs human/parser fix. |
| `invalid_input` | Bad file, unknown scoring guide, empty JD, feedback requested on incomplete scan, etc. | Fix inputs, retry once. |
| `storage_error` | Local ledger/backup failed | STOP — never delete a scan whose feedback isn't backed up. |
| `profile_in_use` | Browser profile locked by another process (e.g. `scripts/login.py` running, §6.1) | STOP. Tell user to close the other browser/login script, then retry. |
| `network_error` | Portal unreachable / navigation failed (connection, DNS, browser closed) — not an SSO redirect, not selector drift. *Amendment 2026-09-28.* | Retry up to 2x with the 2–5 s delay (per retry policy), then STOP and report. |

Retry policy: at most **2 retries** per failing call, with the 2–5 s human delay between them.
`auth_expired`, `site_changed`, `unknown_state`, and `profile_in_use` are never retried blindly.

---

## 14. Observability & local data (`storage.py`)

Everything the site knows must be mirrored locally — both for the agent's learning across loop
iterations and because scans may be tidied up.

- **Scan ledger** `~/.resumeai-mcp/ledger.jsonl`: one JSON line per scan — `scan_id, resume_filename,
  resume_sha256, jd_sha256, role_title, company, scoring_guide, started_at, completed_at, medal,
  ats_badge, credibility_badge, recovered, deleted_at`. Append-only.
- **Feedback backups** `~/.resumeai-mcp/feedback/<scan_id>.json`: the full `ScanFeedback` payload,
  written by `get_scan_feedback` and re-verified before any `delete_scan`.
- **HTML snapshots** `~/.resumeai-mcp/snapshots/<scan_id>-<step>.html`: saved on every failure and
  on demand; these are the fixtures for parser tests.
- **Permissions & retention (explicit — a `.gitignore` entry cannot protect files outside the
  repo):** `~/.resumeai-mcp/` and `PROFILE_DIR` are created with `0700`; snapshots contain full
  page HTML (PII) and are purged after **30 days**; ledger + feedback backups are tiny and retained
  indefinitely. Never log page HTML bodies or file contents at INFO level.
- **Ledger read-back:** on startup, `storage.py` loads the ledger so `start_scan` idempotency works
  even for scans created in previous sessions.

---

## 15. Build phases & acceptance criteria

### Phase 0 — Project setup & first login (**proof-of-concept gate**)
- Scaffold repo (§5), `pyproject.toml`, `.env.example`, `.gitignore`.
- `playwright install` (Chromium / Chrome channel).
- Implement `config.py`, `browser.py` (persistent context, `human_delay`, snapshot helper),
  `scripts/login.py`.
- **Accept:** user runs `login.py`, completes Duo manually, script prints `logged_in=true`. Then:
  close, relaunch **headless** with the same profile, `auth_status` still `logged_in=true`.
  **Go/no-go:** if headless session reuse fails, stop and reassess the approach before building
  further — do not build the toolset on an unproven session story.

### Phase 1 — Read-only tools
- Implement `auth.py` (login detection), `auth_status`, `list_scans` (+ `ScanSummary` schema,
  pagination, ledger hash-join).
- Snapshot harness: save My Scans HTML as the first parser fixture.
- **Accept:** headless `auth_status` → `logged_in=true` with a plausible `scans_remaining`;
  `list_scans` returns real rows matching what's visible on screen; site-created rows show null hashes.

### Phase 2 — Feedback parser (the hard part)
- Implement `feedback.py` + `ScanFeedback` schema (§8) against saved fixtures, including the
  summary-page category badges and red-flagged items under Gold badges.
- Cover: medal-only summary (`partial=true`), all four tabs, criterion statuses, action items,
  keyword matched/unmatched lists, `badge=null` edge case.
- **Accept:** `test_feedback_parser.py` green on ≥2 saved fixtures; parser never throws on unknown
  labels (falls back to `warning` + raw text).

### Phase 3 — Write operations
- Implement `start_scan` (full §9 workflow + full-tuple idempotency + immediate ledger write),
  `get_scan_status` (5 states incl. `unknown`), `delete_scan` (backup-first, declared `confirm`,
  truthful `allowance_restored`), `storage.py` ledger.
- Implement the §10.2 lost-ID recovery procedure.
- **Accept:** end-to-end on a real resume — start → poll → complete → feedback JSON validates
  against the schema; reuse path returns `reused=true` without consuming allowance; a repeated
  identical `start_scan` does not create a duplicate row.

### Phase 4 — MCP server wiring
- `server.py`: FastMCP app, 6 tools, stdio transport, the single envelope (§7.0), full
  `description=` texts (honesty rule, pacing notes), error catalog (§13).
- Add to Claude Code MCP config; verify tool invocation works.
- **Accept:** agent can call all 6 tools from Claude Code and receives schema-valid JSON inside the
  envelope for both success and error cases.

### Phase 5 — Loop runbook & dry run
- Write the agent-side runbook (§11 loop + honesty rule) as `RUNBOOK.md`.
- Dry run on one real role: tailor → rescan loop with `MAX_ITERATIONS=3` (capped for the dry run).
- **Accept:** loop reaches Gold/Gold or stops per a documented stopping rule (including a clean stop
  at `limit_reached`); ledger + backups complete; flagged-under-Gold items appear in the report.

### Phase 6 — Hardening & docs
- `site_changed` detection wired through every step (§9 table); retry policy; daily-budget guardrail.
- README: install, login, Claude Code config snippet, troubleshooting table, "when the site changes".
- **Accept:** README lets a fresh reader go from zero to first scan without asking questions.

---

## 16. Testing strategy

| Layer | How | Notes |
|-------|-----|-------|
| Schemas & envelope | `test_schemas.py`: valid/invalid payloads, normalization rules, envelope shape | Pure unit, no browser |
| Parser | `test_feedback_parser.py` on **saved HTML fixtures** | Fixtures captured in Phase 1/2; re-capture when the site changes. Committed fixtures are redacted copies in `tests/fixtures/*.fixture.html` (amendment 2026-09-28, see §17) |
| Reuse key & recovery | `test_reuse_key.py`: hash normalization, full-tuple matching, recovery window logic | Pure unit |
| Browser flows | **No automated live tests** — they need auth + consume real scans | Manual E2E checklist in README instead |
| MCP contract | `fastmcp` dev-mode smoke test: call each tool, validate envelope + JSON | Run against the live site sparingly (it costs scans) |

Manual E2E checklist (run once per Phase 3/4): login → auth_status → list_scans →
start_scan → poll → feedback schema-valid → reuse returns `reused=true` → delete backs up first and
reports `allowance_restored` truthfully. Tick every box.

---

## 17. Security & privacy notes

- **The profile directory holds session cookies, which are bearer tokens.** It is login material:
  `0700`, never committed, never copied to shared machines, never uploaded.
- **No passwords are stored anywhere** — in code, env files, logs, or memory files. The design
  needs none.
- **PII minimization:** the ledger stores hashes + metadata, never resume text. Snapshots contain
  full page HTML (PII) — local only, never committed, purged after 30 days (§14).
  **Amendment 2026-09-28:** test fixtures derived from snapshots *are* committed, but only as
  redacted copies in `tests/fixtures/*.fixture.html`: resume text, names, emails, filenames and scan
  ids removed or replaced; only the elements the parser reads are kept. `.gitignore` re-includes
  exactly that pattern.
- **`.gitignore` must include:** the profile dir name, `.env`, snapshot `*.html` files,
  `__pycache__/`, `*.log`. (It cannot protect `~/.resumeai-mcp/` — that's covered by §14's
  explicit permission/retention rules.)
- **Scope:** the user's own account, own resumes, own job descriptions. Nothing in this design
  accesses anyone else's data.

---

## 18. Risks & mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | Site UI/label changes break selectors | Medium | Medium | Semantic locators; `site_changed` fail-loud contract; HTML snapshots make parser fixes fast |
| 2 | Session expiry mid-loop (SSO timeouts) | Medium | Medium | `auth_expired` contract; `auth_status` pre-check; optional keep-alive task |
| 3 | Headless session reuse fails (fingerprint/behavior) | Low–Medium | High | **Phase 0 PoC gate**: prove it before building; documented fallbacks (§6.3) |
| 4 | Optimizer drifts into fabrication | Medium | High (integrity) | Honesty rule in tool description + agent prompt (§11); unmatched keywords framed as verification candidates |
| 5 | Account flagged for automation | Low | Medium | §12 pacing rules; ≤12 ops/day; human-like delays; personal-use scope |
| 6 | Delete-scan allowance behavior changes | Low | **Low** (no longer depended upon) | Informational `allowance_restored`; loop stops at displayed allowance |
| 7 | Scan failures / stuck "scanning" / unreadable rows | Low | Low | 10-min poll timeout; `unknown` distinct from `failed`; manual My Scans fallback |
| 8 | Medal/category semantics change | Low | Low | Badges are read, never assumed; thresholds not hardcoded into stopping logic |

---

## 19. Assumptions & open questions

**Assumptions (locked unless contradicted by testing):**
- A1. No *documented* public ResumeAI API found → Playwright is the chosen approach, pending the
  Phase 0/1 PoC.
- A2. Per-category badges are visible on the summary page (screenshot-verified 2026-09-24) and are
  the loop's primary target signal.
- A3. Scoring guide default `"Graduate - STEM Focus"` exists in the account's folders.
- A4. Loop target = ATS Fit Gold + Credibility Gold; Readability/Format extracted and reported but
  not loop targets. Flagged items are reported even under Gold badges.
- A5. Deletion never restores allowance *by design reliance*; any restore is incidental.
- A6. Single user, single machine (user's Mac), personal use.

**Open questions for the user (do not block Phase 0–2):**
- Q1. Should `RUNBOOK.md` live in this repo or in the Claude Code agent's own repo?
- Q2. Preferred location for `PROFILE_DIR` and `~/.resumeai-mcp/` on the Mac?
- Q3. For Phase 5's dry run: which role/JD should be the pilot?

---

## 20. Definition of done

- [ ] All 6 tools callable from Claude Code via stdio; every response validates against §7–§8
      (success *and* error envelopes).
- [ ] `scripts/login.py` establishes a session; all subsequent runs are headless (PoC gate passed).
- [ ] A real resume completes start → poll → structured feedback with zero manual steps.
- [ ] Idempotent `start_scan` on the full reuse key returns `reused=true` without consuming allowance
      or creating duplicate rows.
- [ ] `delete_scan` backs up feedback locally, honors `confirm`, and reports `allowance_restored`
      truthfully; the loop never depends on it.
- [ ] The §11 loop reaches ATS Gold + Credibility Gold on a pilot role, or stops via a documented
      stopping rule (incl. clean `limit_reached` stop) with a clear report that includes
      flagged-under-Gold items.
- [ ] Error catalog (§13) implemented; `auth_expired`, `unknown_state`, and `site_changed` paths
      manually exercised.
- [ ] Pacing rules (§12) implemented in `browser.py`; `~/.resumeai-mcp/` permissions/retention per §14.
- [ ] README + RUNBOOK.md complete; a fresh reader reaches first scan unaided.
- [ ] No passwords, profile data, or PII in the repo; `.gitignore` verified with `git status`.

---

*End of document v1.1.*
