# Job search kit: resume tailor + job tracker

Tailors your base LaTeX resume to a job description with Claude Code, keeps every final PDF in `resumes/`, tracks every job you apply to, cold-emails recruiters, and counts your applications from Gmail. Use it from Claude Code directly or from a small local web page.

## Make it yours (do this first)

The `base/` folder ships with a fictional person (Jordan Lee). Replace it with your own:

1. **`base/resume.tex`**: your resume. Keep the preamble and the overall shape (header, EXPERIENCE with `\textbf{Title - Company}` role lines and `\item` bullets, SKILLS rows as `\textbf{Label:} a, b, c\\`, EDUCATION), and keep the contact line starting with `City, ST | \href{tel:...}`, since tailoring swaps that location for the job's. It must compile to one full page.
2. **`base/facts.md`**: every true fact about your work, with more detail than fits on the resume, under the same headings (one section per role, Links, Projects, Allowed skills, Do not claim). Tailoring, cold emails and application answers can only say what's in here or in the resume.
3. **`base/profile.json`**: your name (used in PDF file names and the email signature), contact details, `city` / `state` / `stateCode` (job-location filters and fit scores), `searchStart` (the date your job search began, for the Gmail totals) and `coldEmailResume` (a PDF to attach to cold emails; null means the base resume).
4. Run `python3 tools/tracker.py base-pdf`; it should print `resumes/<First>_<Last>_Resume.pdf`.

Nothing personal lives in the code. Your data files (`tracker.json`, `matches.json`, `outreach.json`, `resumes/`) are in `.gitignore`, so they stay out of git; if you push your own copy, decide whether `base/` should be public too.

## What's in the folder

| Path | What it is |
|---|---|
| `base/resume.tex` | Your master resume (Claude can't edit it) |
| `base/facts.md` | Everything tailoring is allowed to say; review and extend it |
| `resumes/` | Final PDFs only: `<First>_<Last>_Resume.pdf` (base) and `<First>_<Last>_Resume_<Company>_<Role>.pdf` (tailored) |
| `tracker.json` | Your application tracker (created on first use; git-ignored) |
| `tools/` | The checker, tracker, and web server |
| `.work/` | Temporary scratch, one folder per run; deleted automatically |

Only the PDFs are kept. The tailored `.tex` is discarded after its PDF is saved; to change a tailored resume, run `/tailor` again for that job.

## One-time setup

1. **Claude Code**: installed and signed in.
2. **Python 3**: already on macOS and most Linux systems.
3. **LaTeX** (`pdflatex` with `titlesec`, `enumitem`, `hyperref`, `mathptmx`):
   - macOS, full (simplest, ~5 GB): `brew install --cask mactex-no-gui`
   - macOS, small: `brew install --cask basictex`, open a new terminal, then
     `sudo tlmgr update --self && sudo tlmgr install titlesec enumitem rsfs symbol times psnfss`
   - Ubuntu/Debian: `sudo apt install texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended`
4. **Poppler** (optional, recommended; turns on the page-fill and short-line checks):
   macOS `brew install poppler` · Ubuntu `sudo apt install poppler-utils`
5. Check it works: `python3 tools/tracker.py base-pdf` should print `resumes/<First>_<Last>_Resume.pdf`.

## Option A: the web page

```
python3 tools/server.py
```

To run more (or fewer) jobs at once: `python3 tools/server.py --parallel 3`. Each run is a full Claude Code session, so more in parallel uses your plan's allowance faster and can hit rate limits; 2–3 is a sensible ceiling.

Open http://localhost:8765. From there you can:
- **Tailor resume + track**: paste a job link, several links (one per line), or the full JD. Claude Code tailors the resume, saves the PDF to `resumes/`, and adds the job with status "Resume ready". Each link becomes its own run.
- **One resume for all (2–5 jobs)**: paste 2–5 links (one per line), or full job descriptions separated by a line with `---`. Claude builds one resume aimed at what the jobs have in common, plus each one's top must-haves, and saves it as `resumes/<First>_<Last>-<Company>.pdf` (each company once, e.g. `Jordan_Lee-Stripe-Plaid.pdf`). Every job gets its own tracker record and notes, linked to that PDF, and application questions work on each of them. The limit is 5 because one page can't convincingly target more; for best results, group similar roles.
- **Track only**: same input. It just adds the job as "Applied", linked to the base resume.
- **Runs**: everything you start (tailoring, tracking, answering questions) goes into one queue, and **2 run at the same time** by default. Each run shows its status, elapsed time and cost; open its log or result, or cancel it. You can keep adding jobs and questions while others run. Successful runs disappear from the list 30 seconds after finishing, since their results are in the tracker. Failed runs stay until you clear them, including ones where the job page couldn't be read.
- **Match my profile**: paste a company's careers page link (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, or a custom page; custom pages need the Prefill setup, `cd tools/apply && npm install`). It pulls every open role, keeps engineering roles at your level near home (city/state in `base/profile.json`), remote or US-wide (tick **All levels** or **All locations** to widen that), and Claude scores the best 25 against your resume and facts. Each result shows its fit score, why it fits, the main gaps, whether it's **new** since your last search of that company, and whether it's already in your tracker. Click **Tailor** or **Track** on a role, or tick 2–5 and choose **One resume for the picked roles**. Searches are saved in `matches.json`; use **Search again** to recheck a company later.
- **Fit score**: every job gets a 0–100 fit score from one shared rubric (`.claude/skills/match/RUBRIC.md`): skills 40, core work 25, level 20, location 15. Bands are 85+ Strong, 70–84 Good, 55–69 Moderate, below 55 Stretch. A score is set once and never changes: a role tailored or tracked from Match keeps its Match score, and other runs score the job as they add it. Jobs without one show "—"; **Score N unscored** gives them a real score from their saved job description (jobs with no description stay "—"). Sort the tracker by **Best fit first**. (`python3 tools/tracker.py backfill-random --yes` exists for random placeholder scores, marked "random", but it's not recommended: the numbers are permanent and meaningless.)
- **Add manually**: type the company and role yourself. Instant, and Claude isn't involved.
- **Application questions** (every job; track-only jobs use your base resume): open a job's Details and paste one question, or several at once (one per line). Claude writes short, specific answers from that job's resume, your facts file and the job description, plus a detail or two from the company's own site for "why us" questions. Each answer has Copy, Edit, Revise (tell it what to change) and Delete. Everything is saved in the tracker, including earlier versions. Anything only you know (salary, start date, work authorization) comes back as a `[placeholder]` to fill in.
- **Tracker table**: change a job's status, open its PDF or posting, see the notes (match, what is covered, changes, gaps) and the saved JD, search and filter, and export to CSV.

The server runs Claude Code in the background (`claude -p`), using your normal Claude Code login and plan usage. Those runs may only read and edit files in this folder, fetch web pages, and run the checker and tracker scripts. The server listens only on your own machine (127.0.0.1).

If `claude` isn't installed, the page still works as a tracker. Only the two Claude buttons are disabled.

## Option B: Claude Code directly

```
cd job-search-kit
claude
/tailor https://company.com/careers/senior-backend-engineer
/track https://company.com/careers/platform-engineer
/track Stripe - Senior Software Engineer
/answer 3 What about Stripe's mission interests you?
```

`/tailor` and `/track` also accept a file path or pasted JD text. The first time, Claude Code asks whether to trust the folder. Say yes so the checks can run.

Tracker from the terminal:

```
python3 tools/tracker.py list
python3 tools/tracker.py update 3 --status Interviewing
```

Statuses: Saved, Resume ready, Applied, Interviewing, Offer, Rejected, Withdrawn. The applied date is filled in automatically the first time a job becomes Applied. A job is never added twice: tracking or tailoring a job that is already in the tracker (same link, or same company and role) updates its existing record.

## How it keeps your resume intact

`tools/check_resume.py` runs after every edit Claude makes, and the tracker refuses to save a PDF that fails it:

- Compiles with `pdflatex` to exactly **1 page**.
- **Locked layout**: preamble, header, section titles, job titles, companies and dates, Certifications, Competitions, Education, and spacing must match the base exactly. Only bullet text and Skills-row items may change.
- **No invented numbers**: every number in a bullet must appear in `base/resume.tex` or `base/facts.md`.
- **No invented skills**: Skills items must come from the base rows or facts.md's "Allowed skills".
- **LaTeX safety**: no unescaped `% & # _`.
- **Page fill and wrapping**: warns if the page ends noticeably higher than the base, or if a bullet wraps with only a word or two on its last line.

## Prefill applications (optional)

Opens a job's application page in Chrome on your Mac, attaches that job's resume PDF, fills the form using Gemini, and leaves the browser open. **You review everything and click Submit yourself; it never submits.**

What it fills: your contact details and links from `base/profile.json`; open-ended questions only when you've saved a matching answer for that job under Application questions; facts from that job's resume. Anything it has no data for is left empty and listed as **NEEDS YOU**. Demographic and EEO questions (gender, race, veteran, disability, and similar) are always left to you and never sent to Gemini. Consent boxes, legal attestations, salary, start date and sponsorship are also left to you, unless you fill those values in `profile.json`.

**It learns from you.** When you fill in a field it left empty, or correct one it filled, your answer is saved once you stop typing for a few seconds, and reused next time:
- general answers ("How did you hear about us?", notice period, work authorization, relocation…) go to `base/profile.json` under `"learned"`; they apply to every future application, and the newest answer wins;
- job-specific open-ended answers ("Why do you want to work at Acme?", long textareas) go to that job's saved Application questions, where you can also see and edit them.
Demographic/EEO answers, consent boxes, attestations and signatures are never saved. To change or forget something it learned, edit or delete that entry in `profile.json`.

It keeps watching while the window is open, so after you click "Apply" or "Next" on multi-step forms (Workday, Greenhouse, Lever, forms inside iframes), new fields get filled too. When the page says the application was submitted, the job is marked Applied automatically.

One-time setup:
1. Node.js 18 or newer (`node --version`), then `cd tools/apply && npm install` (downloads Puppeteer and its Chrome, about 170 MB).
2. Fill in `base/profile.json`. Leave anything as `null` that you'd rather answer yourself each time.
3. Get a Gemini API key from Google AI Studio and set it in the terminal you start things from: `export GEMINI_API_KEY="..."` (or add that line to `~/.zshrc`). Never paste the key into a file. Optional: `export GEMINI_MODEL=...` to change the model.

Use it from the web page (job → Details → **Prefill application**; start the server from a terminal where the key is set), or directly with `node tools/apply/autofill.js <job #>` (add `--url <link>` if the application form is at a different link than the posting). Logins you make in that Chrome window, such as Workday accounts, are remembered in `tools/apply/.chrome-profile`.

Use it on company career sites and their ATS pages. Don't point it at LinkedIn Easy Apply or other sites whose terms forbid automation. Some sites detect automated browsers; if one blocks it, apply by hand.

## Moving to a new computer

Copy these from the old folder into the new one; everything else comes with the download:
- `tracker.json`: every job, its notes, saved JD, and application answers (with earlier versions)
- `matches.json`: your saved Match my profile searches
- `resumes/`: the PDFs the tracker links to (paths are relative, so the folder can live anywhere)
- `base/facts.md`, `base/resume.tex` and `base/profile.json`, only if you edited them yourself

Then check with `python3 tools/tracker.py list`.

## Keeping it up to date

- **Base resume**: edit `base/resume.tex` in your own editor any time; the base PDF rebuilds itself the next time it's needed. To have Claude make the change, ask explicitly; it unlocks, edits, and relocks.
- **New facts**: add true details (tools, metrics, project specifics) to `base/facts.md` under the right role, or tell Claude to. Tailoring can only use what is there or in the base resume.
- **"Do not claim"** at the bottom of facts.md lists things that were removed or never confirmed.

## Cold email (second tab)

Type a company in the **Cold email** tab. Claude finds the top 5 recruiters there (Hunter.io domain search, HR department, ranked toward technical and early-career recruiters, verified addresses first), reads what the company builds and any jobs you've tracked there, and writes a 60–110 word email to each: why you're writing and how you fit in the first sentence, two proof bullets with numbers, one ask. Your signature (from `base/profile.json`) and resume (`coldEmailResume` in the profile, or the base PDF; attached as `<First>_<Last>_Resume.pdf`) are added automatically.

Review and edit each email, press **Save to Gmail Drafts** to see it exactly as Gmail shows it, then tick the ones to send and press **Send selected**. Guards: nobody is emailed twice, at most 25 sends a day, 20–45 s between sends.

**Apollo fallback (needs a paid Apollo plan; off by default):** Apollo's free plan blocks people search and email reveal, both through the claude.ai connector and with an API key. On a paid plan, with **Use Apollo as a fallback** ticked, a run that finds Hunter out of searches, or finds no recruiters there, searches Apollo through your claude.ai Apollo connector (free) and reveals at most 5 work emails (1 Apollo credit each). Imported addresses must be at the company's domain.

One-time setup (secrets go in the macOS Keychain, never in the project):
1. Gmail: turn on 2-Step Verification, create an app password at https://myaccount.google.com/apppasswords, then run `security add-generic-password -s resume-tailor-gmail -a "$USER" -w` and paste it.
2. Hunter.io: create a free account, copy the API key from https://hunter.io/api-keys, then run `security add-generic-password -s resume-tailor-hunter -a "$USER" -w` and paste it.

CLI: `python3 tools/outreach.py status | list | eml | draft | send` (see the top of the file).

## Daily target (top right)

Today's applications against a target of 50, with the last 7 days below. The count comes from application-confirmation emails in Gmail ("Thank you for applying…", "We've received your application…"), so it covers every site you apply on, not just the tracker. Gmail search drops emails that read like rejections, and subjects like "Application Update" or "Important information about your application" are not counted. It reads Gmail read-only (nothing is marked read), rechecks every 5 minutes, and rechecks at once when you click the number. Hover it to see the tracker's count too. Without Gmail access it falls back to jobs marked Applied in the tracker.

The totals row starts with **Gmail since <date>**: applications (confirmation emails) and rejections (emails saying you're not moving forward), counted since `searchStart` in `base/profile.json`. Change that date when a new search starts. The tracker's own pipeline counts (tracked, Saved, Resume ready, Applied…) follow, smaller.

**Import from Gmail.** The Applications toolbar shows **Import N from Gmail** when confirmation emails since the search start aren't in the tracker yet. It runs Claude in batches of 100: each email's company, role and location are read from the email, and it's linked to the matching tracked job (marking it Applied with the email's date if needed) or added as a new job ("Added from Gmail" under Details). An email that names no role is linked to the closest-dated job at that company that doesn't have an email yet, and only becomes an "Unknown role" job when there's none. Each email is imported once (its Message-ID is stored on the job as `gmail_ids`). From the terminal: `/sync-gmail` in Claude Code.

The counter's column shows the last 30 days, back to the search start at most.

Check what was counted for a day: `python3 tools/inbox.py --list YYYY-MM-DD`. Totals: `python3 tools/inbox.py --totals`.
