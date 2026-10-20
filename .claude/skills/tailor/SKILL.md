---
name: tailor
description: Tailor the user's base LaTeX resume to one job description, save the verified one-page PDF in resumes/, and add the job to the tracker.
argument-hint: [JD url | path/to/jd.txt | pasted JD text]
disable-model-invocation: true
allowed-tools: Read Write Edit Glob Grep WebFetch Bash(mkdir *) Bash(cp *) Bash(rm -rf .work/*) Bash(python3 tools/check_resume.py *) Bash(python3 tools/tracker.py *)
---

# Tailor the resume to a job description

Job description input: $ARGUMENTS

**Fit score.** If the input contains `--fit <n>`, that score already came from Match my profile: use it as is (source `match`) and remove it from the JD input. Otherwise score the job yourself with `.claude/skills/match/RUBRIC.md` after analyzing the JD (step 3).

**Scratch folder (`WORK`).** If the input ends with `--work <folder>`, that folder is `WORK` and the rest is the JD input. Otherwise `WORK` is `.work/cli`. Several runs can happen at the same time, each with its own `WORK`, so use only your own `WORK` folder and never touch other folders in `.work/`. Below, `WORK` always means your folder (for example `.work/3fa9c2d1`).

If the JD input is empty, reply `FAILED: no job description given.`

**Failure.** Whenever you stop without saving to the tracker, your final reply must start with `FAILED:` followed by the reason, so the web page marks the run as failed.

Work only from `base/resume.tex` and `base/facts.md`. Never ask the user questions, never ask them to confirm or supply new facts, and never stop mid-run, except when the JD itself is missing or unreadable. If the JD wants something those two files don't support, it is a gap: leave it off the resume and list it in the notes. Nothing more.

## Ground rules (never break these)

1. **Truth.** Every claim must trace to `base/resume.tex` or `base/facts.md`. Numbers are copied exactly. No new metrics, technologies, scope, titles, or years of experience. Respect the "Do not claim" list in facts.md.
2. **Layout is locked.** Work on a copy of `base/resume.tex`. You may change only:
   - the text of `\item` lines (rephrase, reorder within a role, drop, or swap in another true fact for that same role), and
   - the comma-separated items in the Skills rows (reorder, drop, or add from facts.md "Allowed skills"), and
   - the location at the start of the header contact line (`City, ST | \href{tel:...`), which becomes the job's location (see step 5).
   Everything else stays byte-for-byte: preamble, the rest of the header, section titles, role/company/location/date lines, Certifications, Competitions, Education, every `\vspace` and `itemize` option. Never move a fact to a different role.
3. **One full page.** Exactly one page, filled to the bottom like the base. No bullet may wrap with only one or two words on its last line.
4. **LaTeX safety.** One `\item` per line. Escape `\%`, `\&`, `\#`, `\_`. Use `$\sim$` for a tilde and `--` for ranges.
5. **Files.** Work only inside `WORK`. Do not create any other folders or files. Never edit `base/resume.tex` (a hook blocks it).
6. **Tools.** Read files with the Read tool and create them with the Write tool. Never use `cat`, `echo >`, or heredocs in Bash; unattended runs can't approve them. Bash is only for the `rm`/`mkdir`/`cp` steps and the two `python3 tools/...` scripts below.

## Steps

### 1. Capture the JD
- `mkdir -p WORK` (it may already exist and hold the pasted JD as `input.md`; don't delete it).
- URL: fetch it with WebFetch and keep the full JD text (title, company, location, responsibilities, requirements, nice-to-haves). If the page will not load or doesn't contain the job description (login wall, JS-only page, expired posting), stop and reply `FAILED: couldn't read the job description from <url>; paste the text instead.`
- File path: read it. Pasted text: use it as is.
- Save the JD to `WORK/jd.md`, with the source URL on the first line if there is one.
- Note the company, the exact role title, and the location.

### 2. Analyze the JD
Extract, in the JD's own words:
- **Must-haves**: required skills, technologies, years, domains.
- **Nice-to-haves**.
- **What the role actually does** (the 3 to 5 core responsibilities).
- **Seniority signals** the reader will look for (ownership, design, mentoring, on-call, cross-team work).
- **Exact keywords and phrases** an ATS or recruiter will scan for.

### 3. Map the JD to the facts
For each must-have and core responsibility, find the strongest true evidence in facts.md. Mark it **covered**, **partial** (true but not yet worded in the JD's terms), or **gap** (no true evidence).

### 4. Plan the edits
- **Order**: within each role, put the bullets that best match the JD first. The most recent role matters most.
- **Wording**: use the JD's terms where they accurately describe the work (for example "event streaming" for the Kafka work). Do not stuff keywords; every bullet must read naturally.
- **Swaps**: replace a low-relevance bullet with a more relevant true fact from the same role in facts.md.
- **Drops**: keep at least 5 bullets for the most recent role and at least 2 for every other role, and keep the page full.
- **Skills**: put the JD's must-have skills first in each row; drop irrelevant items if space is needed; add only from "Allowed skills".
- **Gaps**: never cover a gap by inventing. List it in the notes.
- Bullets within a role start with different verbs.

### 5. Write the tailored copy
- `cp base/resume.tex WORK/resume.tex`, then edit only the allowed parts.
- **Header location**: replace the base location at the start of the contact line with the JD's location as plain `City, ST` (e.g. `San Francisco, CA`; drop "(Hybrid)", "On-site", office names and countries). If the JD lists several cities, use the home city from `base/profile.json` if it is one of them, otherwise the first. Keep the base location when the job is remote or names no city. If the checker says the contact line wraps, use the city alone (`Mountain View`); if that still wraps, keep the base location. Role lines keep their own locations.
- After every edit a hook runs `tools/check_resume.py`. Fix any FAIL or WARN lines it reports.
- Finish with `python3 tools/check_resume.py WORK/resume.tex`. It must end in PASS with no WARN lines (it also builds `WORK/<First>_<Last>_Resume.pdf`). Fixes:
  - 2 pages: shorten bullets that wrap by a few words, or drop the least relevant bullet.
  - Page ends high: lengthen a relevant bullet with true detail from facts.md, or restore a dropped bullet.
  - Short last line: trim or add a few words to that bullet.

### 6. Write `WORK/notes.md`
This is what the user sees under "Details" in the tracker. Bullets only, one short line each, plain language, no tables, no questions:

```
## Match
- Overall: Strong | Moderate | Stretch (one-line reason)
- Angle: the one-line story the resume now tells for this job

## Covered
- <JD requirement> → <where it shows on the resume>      (up to 6)

## Changes from base
- Header location: <location used>
- <what changed, in a few words>                         (up to 6)

## Gaps
- <JD requirement> (closest true experience to mention, or "none")   (up to 4)
```

### 7. Save the PDF and track the job
Run (quote every value):

```
python3 tools/tracker.py add --company "<Company>" --role "<Role title>" --location "<Location>" --url "<JD url, or omit>" --fit <score> --fit-reason "<one line>" --fit-source <match|tailor> --source tailored --jd-file WORK/jd.md --notes-file WORK/notes.md --resume-from WORK/<First>_<Last>_Resume.pdf --clean-work WORK
```

This re-verifies the resume, copies the PDF into `resumes/`, adds or updates the tracker record (status "Resume ready"), and deletes `WORK`. If it refuses, fix the reported problem in `WORK/resume.tex` and run it again.

### 8. Report
Reply in exactly this shape. Bullets only, one line each, no questions, no paragraphs, and no reassurances like "numbers unchanged" or "nothing was invented" (those are guaranteed by the checks):

```
**Resume:** resumes/<file>.pdf
**Match:** Strong | Moderate | Stretch (one-line reason)

**What changed**
- <change>            (3 to 4 bullets, most important first)

**Gaps**
- <requirement>       (up to 3, a few words each; omit the section if none)
```
