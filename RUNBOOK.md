# RUNBOOK — tailor-and-rescan loop

For the Claude Code agent that tailors resumes (PLAN.md §11). The `resumeai` MCP server only drives Big
Interview; **you** own the loop, the tailoring, and the stopping decisions. Every tool returns
`{"ok": true, "data": …}` or `{"ok": false, "error": {"code", "message", "hint"}}` — always branch on `ok`.

## Honesty rule (verbatim — non-negotiable)

> You are optimizing for ATS Fit and Credibility badges. Unmatched keywords are *verification
> candidates*, not a shopping list. You may add a keyword/skill/metric ONLY when it is backed by
> the user's real experience, projects, coursework, or measurable work. NEVER invent employers,
> dates, degrees, metrics, or skills. NEVER keyword-stuff (repeating a term without genuine
> context). If a keyword cannot be truthfully supported, leave it unmatched and say so in the
> report. A truthful Silver beats a fabricated Gold.

## Inputs

- `resume_path` — absolute path to the user's text-based PDF/DOCX. **Never edit the original**; write each
  tailored version as a new file next to it: `<name>_v2.pdf`, `<name>_v3.pdf`, …
- `job_title`, `company`, `job_description` (full text), `scoring_guide` (default `"Graduate - STEM Focus"`).
- `MAX_ITERATIONS = 8` (dry runs: 3).

## The loop

```
1. s = auth_status()
   - ok=false → follow error (§ Errors); stop.
   - s.logged_in == false → STOP: "Big Interview session expired. Run: uv run python scripts/login.py"
   - s.scans_remaining == 0 → STOP: "Daily allowance exhausted; resume tomorrow."
2. existing = list_scans()            # follow next_cursor only if you need older rows
   (informational; start_scan itself reuses a completed scan with the same full key)
3. r = start_scan(resume_path, job_title, company, job_description, scoring_guide)
   - r.reused == true → no scan was used; go straight to 4b.
4. for i in 1..MAX_ITERATIONS:
     a. POLL: get_scan_status(r.scan_id) no more often than every 20 s; give up after 10 min
        → STOP with scan_timeout ("keep scan id r.scan_id; check My Scans manually").
        - state=complete → continue to b.
        - state=failed  → the site explicitly failed it: one retry with a fresh start_scan, then STOP.
        - state=unknown → STOP. Not a failure: report data.hint (names the saved snapshot) and the scan id.
     b. fb = get_scan_feedback(r.scan_id)
        - fb.partial == true → badges are still valid for c; criteria are empty, so say so in the report
          and tailor only from what is visible.
     c. TARGET: fb.categories.ats_fit.badge == "gold" AND fb.categories.credibility.badge == "gold"
        → DONE (report, see below).
        - either target badge is null → STOP ("cannot evaluate target; needs human review").
     d. FLAGS: every criterion with status needs_work or warning, in any category, goes into this
        iteration's report — including flags under a Gold badge (their action_items say so).
     e. STAGNATION: iteration i "improved" if either target category got a better badge
        (bronze < silver < gold) OR fewer needs_work/warning criteria than iteration i-1.
        If (i-1 vs i-2) AND (i vs i-1) both show no improvement → STOP ("diminishing returns;
        manual review advised"). Two consecutive stagnant comparisons, precisely.
     f. if i == MAX_ITERATIONS → STOP ("iteration cap reached").
     g. TAILOR from fb (honesty rule!) → write v{i+1}. Work from action_items and the criterion
        advice; treat ats_fit.keywords_unmatched as verification candidates only.
     h. if the latest scans_remaining (from get_scan_status) == 0 → STOP with limit_reached
        ("allowance exhausted; resume next day or request a reset").
     i. r = start_scan(v{i+1} path, same job_title, company, job_description, scoring_guide)
```

**Stopping rules (all enforced):** Gold/Gold · MAX_ITERATIONS · two consecutive stagnant comparisons ·
`limit_reached` · `auth_expired` · `site_changed` / `unknown` state · null target badge · `scan_timeout`.

## Errors (PLAN.md §13)

| code | do |
|------|----|
| `auth_expired` | STOP. Ask the user to run `uv run python scripts/login.py`. Never retry. |
| `limit_reached` | STOP. Resume after local midnight, or the user requests a reset. |
| `scan_failed` | Keep the scan id; one retry with a fresh `start_scan`, then STOP. |
| `scan_timeout` | Agent-side (no completion after 10 min of polling): keep the scan id; STOP; the user may check My Scans. |
| `unknown_state` | STOP. Snapshot saved; the hint says what to check. **Do not rescan** (a submission may be pending recovery). |
| `not_found` | Re-run `list_scans`; likely a typo or a deleted scan. |
| `site_changed` | STOP. Snapshot saved; the site layout changed and the server needs a fix. |
| `invalid_input` | Fix the input named in the message; retry once. (A scan already in flight also lands here: poll it.) |
| `storage_error` | STOP. Local ledger/backup failed; never delete scans until fixed. |
| `profile_in_use` | STOP. Ask the user to close `scripts/login.py` / the other browser, then retry. |
| `network_error` | Retry up to 2× with a 2–5 s pause, then STOP and report. |
| `internal_error` | STOP and report; details are in the MCP server log. |

Never retry the same failing call more than twice; never retry `auth_expired`, `site_changed`,
`unknown_state`, `profile_in_use`, or `internal_error` blindly.

## Pacing & account safety (PLAN.md §12)

- One tool call at a time (the server serializes anyway); never parallel calls.
- Polls ≥ 20 s apart; one scan in flight; at most 12 scan operations (starts + deletes) per day.
- Daytime runs only; no unattended marathons.

## Deleting scans (optional)

`delete_scan` is history tidying, **not** allowance fuel: never plan on `allowance_restored`. Feedback is
backed up locally first (`~/.resumeai-mcp/feedback/<id>.json`); deleting the latest loop result needs
`confirm=true`. Prefer the oldest, lowest-medal scans.

## Report (after DONE or any STOP)

Per iteration: scan id, reused?, overall medal, the four badges, the needs_work/warning criteria (mark
those under a Gold badge), what you changed and why (each change tied to real experience), keywords
you deliberately left unmatched and why. End with: the stopping rule that fired, final resume path,
scans used, `scans_remaining`.
