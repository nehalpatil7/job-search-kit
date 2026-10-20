---
name: match
description: Match my profile. Read a company's careers page, shortlist the open roles at the user's level and location, and rank them by fit to their profile.
argument-hint: <careers page url> [--all-levels] [--all-locations]
disable-model-invocation: true
allowed-tools: Read Write WebFetch Bash(mkdir *) Bash(rm -rf .work/*) Bash(python3 tools/careers.py *)
---

# Match my profile against a careers page

Input: $ARGUMENTS

**Scratch folder (`WORK`).** If the input ends with `--work <folder>`, that folder is `WORK`. Otherwise `WORK` is `.work/cli`. Touch only your own `WORK`.
Read files with the Read tool and create them with the Write tool; never use `cat`, `echo >`, or heredocs in Bash. This often runs unattended: never ask questions. If you stop without saving, your final reply must start with `FAILED:` and the reason.

## 1. Fetch the roles
`mkdir -p WORK`, then run (pass through `--all-levels` / `--all-locations` if the input has them):

```
python3 tools/careers.py fetch "<careers url>" --out WORK/jobs.json
```

It detects the platform (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, or a custom page), filters to engineering roles at the user's level near home (city/state in `base/profile.json`), remote or US-wide, and keeps the most promising 25 with their descriptions.
- If it prints `FETCH FAILED`, stop: `FAILED: <its message>`.
- If it kept 0 roles, stop: `FAILED: <company> has <n> open roles but none match the filters; try All levels / All locations.`

## 2. Fill in missing descriptions
Read `WORK/jobs.json`. For any job without a `description` (custom pages), WebFetch its `url` and keep the role's requirements and responsibilities. If a page won't load, score that job from its title and location only, and say so in its reasons.

## 3. Score every job
Read `base/resume.tex`, `base/facts.md`, and `.claude/skills/match/RUBRIC.md`. Score each job 0-100 strictly by the rubric, using only what those files support. Be calibrated: these scores are saved permanently on any job the user later tracks.

Write `WORK/ranked.json`, a list sorted best first:

```
[{"title": "...", "url": "...", "location": "...", "score": 84,
  "reasons": ["<biggest plus>", "<second plus>"], "gaps": ["<main gap>"], "posted": "..."}]
```

- `reasons`: 2-3 short, specific lines tied to their real work (for example "event pipelines at scale match their streaming platform").
- `gaps`: 0-2 short lines naming what the JD asks for that facts.md doesn't back.
- Keep `url` exactly as in jobs.json.

## 4. Save

```
python3 tools/careers.py save --url "<careers url>" --file WORK/ranked.json --meta WORK/jobs.json --clean-work WORK
```

## 5. Report
Bullets only, one line each:

```
**<Company>** (<platform>): <found> open roles, <passed> fit the filters, <k> scored
- <score> <label>: <title> (<location>)    (the top 5)
```

Add one line if many good roles were cut by the 25 limit or the filters.
