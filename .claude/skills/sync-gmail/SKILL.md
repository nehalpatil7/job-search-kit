---
name: sync-gmail
description: Import application-confirmation emails from Gmail into the tracker. Reads each confirmation's company, role and location, links it to the job if it's already tracked, and adds the job otherwise.
argument-hint: [WORK/ids.json] [--work <folder>]
disable-model-invocation: true
allowed-tools: Read Write Bash(mkdir *) Bash(rm -rf .work/*) Bash(python3 tools/inbox.py pending *) Bash(python3 tools/tracker.py *)
---

# Import Gmail application confirmations into the tracker

Input: $ARGUMENTS

**Scratch folder (`WORK`).** If the input ends with `--work <folder>`, that folder is `WORK`. Otherwise `WORK` is `.work/cli`. Touch only your own `WORK`.

This runs unattended. Never ask the user anything. Whenever you stop without importing, your final reply must start with `FAILED:` and the reason. Read files with the Read tool and write them with the Write tool; Bash is only for the commands below.

## Steps
1. `mkdir -p WORK`. If the input names an ids file (`.work/<run>/ids.json`), run `python3 tools/inbox.py pending --ids <that file> --out WORK/emails.json`; otherwise run `python3 tools/inbox.py pending --limit 100 --out WORK/emails.json`. If it reports 0 emails, reply `FAILED: nothing to import; every confirmation is already in the tracker.`
2. Read `WORK/emails.json`. Each email has `id`, `date`, `from`, `subject`, `snippet` (the start of the body) and `candidates` (tracked jobs whose company name appears in the email).
3. For every email, decide:
   - **company**: the hiring company, as people write it ("Stripe", "Acme Robotics", "Example.io"). Not the ATS or sender platform (Greenhouse, Lever, Ashby, Workday, Workable, Rippling, iCIMS, Gem) and not a recruiting agency's mailbox name. Workday senders like `acme@myworkday.com` mean Acme.
   - **role**: the job title exactly as the email states it, without requisition numbers or IDs ("R123456 Software Engineer 3" becomes "Software Engineer 3"; "Software Engineer II (Backend) - Payments - 100234" becomes "Software Engineer II (Backend) - Payments"). If the email names no role, use `""` (it is saved as "Unknown role"). Never guess a role.
   - **location**: only if the email states one, else `""`.
   - **match**: the `id` of the candidate that is the same job: same company and the same role (allow small wording differences such as "OSS" vs "Open Source Server", or "Sr" vs "Senior"). If the email names no role, it is still one of the applications at that company: match the candidate at the same company that has no email linked yet and whose applied date is closest to the email's date (within 3 days). Only when no such candidate exists use `null` (a new job is added, as "Unknown role"). With a role, no same-role candidate means `null`. Candidates from a different company that only share a word with the email are never a match.
   - Two emails for the same company and role are the same application only when they arrive within a day of each other; give both the same values and the tracker merges them.
4. Write `WORK/import.json`: one entry per email, `[{"gmail_id": "<id exactly as in emails.json>", "company": "...", "role": "...", "location": "...", "match": 12 or null}]`.
5. Run `python3 tools/tracker.py import-gmail --file WORK/import.json --clean-work WORK`. If it lists SKIPPED lines, fix those entries in `WORK/import.json` and run it again (already imported emails are skipped automatically).

## Report
```
**Imported:** <n> confirmations: <a> new jobs, <b> linked to tracked jobs
**Unknown role:** <k> (emails that name no role)
```
