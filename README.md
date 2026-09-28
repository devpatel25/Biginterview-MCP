# resumeai-mcp

A local MCP server (stdio) that lets a Claude Code agent use **Big Interview ResumeAI** on your own
Northeastern account: scan a resume against a job description, poll the result, read the structured
feedback, and tidy up old scans. It drives the website with Playwright in a real Chrome profile; there is no
public API. Spec: [PLAN.md](PLAN.md). Agent loop: [RUNBOOK.md](RUNBOOK.md).

No password is ever stored. You log in once by hand (SSO + Duo); the server reuses that browser session.

## 1. Requirements

- macOS or Linux, Python **3.11+**, [`uv`](https://docs.astral.sh/uv/), Google Chrome, Claude Code.
- A Big Interview account (NEU: `https://neu.biginterview.com`).

## 2. Install

```bash
git clone https://github.com/devpatel25/Biginterview-MCP.git
cd Biginterview-MCP
uv sync --frozen          # exact locked versions (fastmcp 2.14.7 + mcp 1.30.0); don't pip-install instead
cp .env.example .env      # defaults work for NEU; edit only if you need to
```

The default `BROWSER_CHANNEL=chrome` uses your installed Google Chrome. If Chrome is missing, run
`uv run playwright install chrome`, or use bundled Chromium: `uv run playwright install chromium` and set
`BROWSER_CHANNEL=` (empty) in `.env`.

| `.env` key | default | meaning |
|---|---|---|
| `BASE_URL` | `https://neu.biginterview.com` | your school's Big Interview portal |
| `DATA_DIR` | `~/.resumeai-mcp` | ledger, feedback backups, snapshots (created `0700`) |
| `PROFILE_DIR` | `~/.resumeai-mcp/resumeai-profile` | the browser profile = your login session (`0700`) |
| `HEADLESS` | `true` | run the browser invisibly |
| `BROWSER_CHANNEL` | `chrome` | `chrome`, or empty for bundled Chromium |
| `NAV_TIMEOUT_MS` | `30000` | page-load timeout |
| `HUMAN_DELAY_MIN_S` / `_MAX_S` | `2` / `5` | random pause between UI actions |

## 3. Log in (once, and whenever the session expires)

```bash
uv run python scripts/login.py          # opens Chrome: log in with SSO + Duo, then press ENTER in the terminal
uv run python scripts/login.py --check  # headless check → prints logged_in=true
```

The session lives in `PROFILE_DIR`. Your school's SSO usually re-authenticates silently, so you rarely need to
repeat this. **Never run `login.py` while Claude Code is using the server**: the two can't share the profile
(you'll get `profile_in_use`).

## 4. Add to Claude Code

```bash
claude mcp add --scope user resumeai -- uv --directory /absolute/path/to/Biginterview-MCP run resumeai-mcp
claude mcp get resumeai   # Status: ✔ Connected
```

Start a new Claude Code session so it picks up the tools.

## 5. First scan

In Claude Code, ask for example:

> Check my Big Interview status, then scan `/Users/me/resumes/Resume.pdf` for "Data Science Co-op" at
> "Acme" with this job description: … Poll until it's done and summarize the feedback.

The agent will call `auth_status` → `start_scan` → `get_scan_status` (every ≥ 20 s) → `get_scan_feedback`.
For the full tailor-and-rescan loop, point the agent at [RUNBOOK.md](RUNBOOK.md).

| tool | what it does |
|---|---|
| `auth_status()` | logged in? scans left today? |
| `list_scans(limit=20, cursor=null)` | past scans, newest first, paged |
| `start_scan(resume_path, job_title, company, job_description, scoring_guide="Graduate - STEM Focus")` | uploads and scans (uses 1 of 5 daily scans); returns the existing scan instead if the exact same inputs were already scanned |
| `get_scan_status(scan_id)` | queued / scanning / complete / failed / unknown |
| `get_scan_feedback(scan_id)` | medal, 4 category badges, every criterion, action items, ATS keywords; saves a local backup |
| `delete_scan(scan_id, confirm=false)` | backs up feedback, then deletes the scan from My Scans |

Every call returns `{"ok": true, "data": …}` or `{"ok": false, "error": {"code", "message", "hint"}}`.

**Limits built in:** 5 scans/day (the site's), at most 12 scan operations (starts + deletes) per day, one scan
in flight, 2–5 s pauses between clicks, one tool at a time. `resume_path` must be an absolute path to a
text-based PDF/DOCX (not a scanned image), 5 MB max.

## 6. Troubleshooting

| you see | why | fix |
|---|---|---|
| `auth_expired` | the SSO session ended | `uv run python scripts/login.py`, then retry |
| `profile_in_use` | `login.py` (or another Chrome) holds the profile | close it and retry |
| `internal_error` on every tool | usually Chrome isn't installed / can't launch (details in the server's stderr log) | see §2 (install Chrome or use Chromium); otherwise report the log |
| `limit_reached` | 0 scans left today, or 12 operations done today | wait until local midnight |
| `invalid_input` "not found" for the scoring guide | guide name doesn't match the site | use the name exactly as shown on the scan page |
| `invalid_input` "no extractable text" | image-only / scanned PDF | export a text-based PDF or DOCX |
| `invalid_input` "still in flight" | a scan is running | poll `get_scan_status` until it completes |
| `unknown_state` | a submission couldn't be confirmed, or a status couldn't be read | **don't rescan**; follow the hint (the next `start_scan` recovers pending submissions) |
| `site_changed` | the website's layout changed | see §7 |
| `network_error` | portal unreachable | read tools already retried twice: check your connection; after a scan/delete, check My Scans before retrying |
| `storage_error` | `DATA_DIR` not writable / disk full | fix permissions/space; nothing is deleted without a backup |
| `claude mcp get resumeai` not connected | wrong path or venv | re-run §2 and the `claude mcp add` line with the absolute repo path |

The server logs to stderr (Claude Code shows it in the MCP logs); page contents are never logged.

## 7. When the site changes

The server never clicks blindly: every step checks for a known landmark and stops with `site_changed`, saving
the page to `~/.resumeai-mcp/snapshots/<step>-<time>-<random>.html`. To fix:

1. Open the snapshot named in the error's `hint` and find what moved.
2. Update the locator or parser: scan form → `src/resumeai_mcp/scans.py` (`_submit`); My Scans list →
   `scans.py` (`parse_my_scans`, which reads the `UserResumeAssignmentScansApp` page data); feedback →
   `src/resumeai_mcp/feedback.py` (`ResumeAssignmentReviewSummaryApp` page data); login → `auth.py`.
3. Re-capture a fixture: keep only the parser-relevant element, redact names/emails/filenames/ids, and save
   it as `tests/fixtures/<name>.fixture.html` (the only HTML that may be committed).
4. `uv run pytest -q`, then record the change as a dated amendment in PLAN.md.

## 8. Your data

Everything stays on your machine under `DATA_DIR` (`0700`): `ledger.jsonl` (the scans this server created, with
file hashes, never resume text), `feedback/<scan_id>.json` backups (kept), and `snapshots/` (full page HTML,
which is personal data: deleted automatically after 30 days, checked at every server start and every new snapshot). `PROFILE_DIR` holds your session cookies: treat
it like a password; never copy, share or commit it.

## 9. Development

```bash
uv run pytest -q          # offline: no browser, no site, no scans
```

Live changes are checked by hand (they use real scans): `login.py --check` → `auth_status` → `list_scans` →
`start_scan` → poll → `get_scan_feedback` → the same `start_scan` again returns `reused=true` →
`delete_scan` backs up first and reports `allowance_restored` truthfully.
