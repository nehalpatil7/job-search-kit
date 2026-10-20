---
name: tailor-combined
description: Build ONE resume tailored to 2-5 job descriptions at once, save the verified PDF in resumes/, and add every job to the tracker linked to it.
argument-hint: [WORK/inputs.json | 2-5 JD urls separated by spaces]
disable-model-invocation: true
allowed-tools: Read Write Edit Glob Grep WebFetch Bash(mkdir *) Bash(cp *) Bash(rm -rf .work/*) Bash(python3 tools/check_resume.py *) Bash(python3 tools/tracker.py *)
---

# One resume for several jobs

Input: $ARGUMENTS

**Scratch folder (`WORK`).** If the input ends with `--work <folder>`, that folder is `WORK` and the rest is the job input. Otherwise `WORK` is `.work/cli`. Other runs may be working in `.work/` at the same time; touch only your own `WORK`.

The job input is either a path to `WORK/inputs.json`, a list like `[{"url": "...", "fit": 82}, {"file": "WORK/input-2.md"}]`, or 2 to 5 JD URLs separated by spaces. An item's `fit` came from Match my profile: keep it (source `match`). Score every other job yourself with `.claude/skills/match/RUBRIC.md`.

**Limit: 2 to 5 jobs.** One page can only speak convincingly to a handful of roles, and every JD adds reading and planning. If more than 5 are given, use the first 5 and say so in the report.

## Rules
Read `.claude/skills/tailor/SKILL.md` first and follow its **Ground rules** (truth, locked layout, one full page, LaTeX safety, files, tools) and its edit and fix guidance exactly. The only difference is that you optimize for several JDs at once, as described below. Work only from `base/resume.tex` and `base/facts.md`, and never ask the user anything.

**Failure.** If you stop without saving, your final reply must start with `FAILED:` followed by the reason.

## Steps

### 1. Capture every JD
- `mkdir -p WORK`. For each job: fetch the URL with WebFetch, or Read the file. Save it to `WORK/jd-<n>.md` (source URL on the first line), and note the company, exact role title, and location.
- If a JD can't be read (login wall, JS-only page), drop that job and note it. If fewer than 2 readable jobs remain, stop: `FAILED: could only read <k> of the job descriptions`.

### 2. Build one combined target
- Analyze each JD the way `/tailor` does (must-haves, nice-to-haves, core responsibilities, seniority signals, keywords).
- Merge them into a single ranked list. A requirement shared by more of the jobs ranks higher; each job's top 2 or 3 must-haves are kept even if they're unique to it.
- If the roles pull in different directions (for example backend infra vs. frontend-heavy full stack), center the resume on the shared core and say in the notes which job is the stretch.

### 3. Plan and write the resume
- `cp base/resume.tex WORK/resume.tex`, then edit only the allowed parts, using the combined ranking for bullet order, wording, swaps, and drops.
- Wording should use terms that several of the JDs use. Never stuff one job's jargon into a bullet at the expense of the others.
- In the Skills rows, order items by how many of the JDs ask for them.
- Header location: follow `/tailor`'s header-location rule using the city most of the jobs share; if none is shared, use the first job's city.
- Finish with `python3 tools/check_resume.py WORK/resume.tex`: PASS with no WARN lines.

### 4. Write one notes file per job
`WORK/notes-<n>.md`, in the same bullet format as `/tailor` (Match, Covered, Changes from base, Gaps), written for that specific job. Under Match, add the line `- Combined resume: also used for <other companies>`.

### 5. Save and track
Write `WORK/jobs.json`:

```
[{"company": "...", "role": "...", "location": "...", "url": "...", "jd_file": "WORK/jd-1.md", "notes_file": "WORK/notes-1.md", "fit": 78, "fit_reason": "<one line>", "fit_source": "tailor"}, ...]
```

Then run:

```
python3 tools/tracker.py add-combined --manifest WORK/jobs.json --resume-from WORK/<First>_<Last>_Resume.pdf --clean-work WORK
```

This re-verifies the resume, saves one PDF as `resumes/<First>_<Last>-<Company>.pdf` (each company once, e.g. `Jordan_Lee-Stripe-Plaid.pdf`), and adds or updates one tracker record per job (status "Resume ready"), all linked to that PDF. If it refuses, fix the problem and run it again.

### 6. Report
Bullets only, one line each, no questions:

```
**Resume:** resumes/<file>.pdf
**Jobs:** <Company – Role> (Strong|Moderate|Stretch), ...

**What changed**
- <change>            (3 to 4 bullets)

**Gaps**
- <requirement> (<which jobs>)    (up to 3; omit if none)
```

Add `**Skipped:** <job> (<reason>)` if any job was dropped or over the limit.
