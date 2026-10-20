# Resume tailoring and job tracking workspace

Tailors one LaTeX resume to job descriptions and tracks applications.
- `/tailor <JD>`: tailored resume PDF + tracker record.
- `/tailor-combined <2-5 JDs>`: one resume PDF tailored to several jobs, one tracker record per job.
- `/track <JD>`: tracker record only.
- `/match <careers page url>`: find and rank the best-fit open roles on a company's careers page (saved in matches.json).
- `/score-jobs`: give tracker jobs without a fit score a real one.
- `/answer <record id> <question>`: answer a job-application question for any tracked job (saved in the tracker).
- `/sync-gmail [ids file]`: import Gmail application-confirmation emails into the tracker (links tracked jobs, adds untracked ones).
- `/outreach <company>`: find the top 5 recruiters at a company and write a short cold email to each (saved in outreach.json for review; never sent by Claude).

## Layout
- `base/resume.tex`: the master resume. Read-only (a hook blocks edits). Its styling and one-page format must never change.
- `base/facts.md`: the only allowed source of claims, numbers, and extra skills. `base/resume.tex` wins if they disagree.
- `resumes/`: final PDFs only (`<First>_<Last>_Resume.pdf` is the base, named from `base/profile.json`; tailored ones are named after the job). Nothing else goes here.
- `matches.json`: saved Match my profile searches. Change it only through `python3 tools/careers.py`.
- `.claude/skills/match/RUBRIC.md`: the one rubric for every fit score.
- `tracker.json`: every tracked job. Change it only through `python3 tools/tracker.py`, never by hand-editing.
- `.work/<run>/`: scratch space, one folder per run (JD, resume.tex, notes), so several runs can work in parallel. Each run touches only its own folder, which is deleted when the job is saved. Never create other folders.
- `tools/check_resume.py`: compiles and verifies a run's `resume.tex` against the base; runs automatically after each edit.
- `base/profile.json`: name, contact details, home location, job-search start date (`searchStart`) and application defaults, used for PDF names, job-location filters, the Gmail counters, the prefill script (`tools/apply/autofill.js`) and for the cold-email signature; null means the user answers it themselves.
- `outreach.json`: cold-email campaigns. Change it only through `python3 tools/outreach.py`. Emails attach the resume set by `coldEmailResume` in profile.json (default: the base PDF).
- `tools/inbox.py`: counts applications per day from Gmail confirmation emails (read-only IMAP, same app password); feeds the daily counter, and lists confirmations not yet in the tracker (`pending`). Tracker jobs keep the Message-IDs of their confirmations in `gmail_ids`; import them only via `tracker.py import-gmail`.
- `tools/server.py`: optional local web page for the tracker; queues /tailor, /tailor-combined, /track and /answer runs and runs 2 at a time (`--parallel N`).

## Rules
- Never invent facts, metrics, technologies, titles, or years of experience. Numbers are copied exactly from base or facts.md.
- In a tailored copy, change only `\item` text, Skills-row items, and the header location (set to the job's location). Everything else stays identical to the base.
- The result must be exactly one page, filled to the bottom, and pass `python3 tools/check_resume.py <run folder>/resume.tex` with no FAIL or WARN lines.
- Fit scores (`fit` on a job) are set once and never changed: always pass them via `tracker.py add --fit` / `set-fit`, scored with the rubric.
- Application answers are saved per job in tracker.json (`qa`, with earlier versions in `history`). Save them only via `python3 tools/tracker.py qa-add`.
- Status changes: `python3 tools/tracker.py update <id> --status "<Status>"`. Statuses: Saved, Resume ready, Applied, Interviewing, Offer, Rejected, Withdrawn.
- When the user confirms a new true fact, add it to `base/facts.md` under the right role before using it. Editing facts.md is allowed; editing base/resume.tex is not.
- Cold emails are sent only when the user presses Send in the Cold email tab. Claude never runs `outreach.py send` or `draft`.
- To change base/resume.tex, the user must ask explicitly. Then run `touch base/.unlock`, make the edit, check a copy with the checker, and `rm base/.unlock`.
