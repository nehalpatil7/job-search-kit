---
name: score-jobs
description: Give every tracker job that has no fit score yet a real one, using the shared rubric. Existing scores are never changed.
argument-hint: [--ids 3,7,12]
disable-model-invocation: true
allowed-tools: Read Write WebFetch Bash(mkdir *) Bash(rm -rf .work/*) Bash(python3 tools/tracker.py *)
---

# Score unscored jobs

Input: $ARGUMENTS

**Scratch folder (`WORK`).** If the input ends with `--work <folder>`, that folder is `WORK`; otherwise `.work/cli`. Never ask questions. If you stop without saving, start your final reply with `FAILED:`.

1. If the input has `--ids <list>` (one batch from the web page), run `python3 tools/tracker.py unscored --ids <list>`; otherwise `python3 tools/tracker.py unscored` and take only the first 20 jobs that have a `jd` or a `url` (say how many remain). It prints the jobs without a fit score (id, company, role, location, url, jd). If there are none, reply `FAILED: every job already has a fit score.`
2. Read `base/resume.tex`, `base/facts.md`, and `.claude/skills/match/RUBRIC.md`.
3. For each job, score it 0-100 by the rubric from its saved `jd`.
   - If `jd` is empty but there is a `url`, WebFetch it (at most 10 fetches per run; skip the rest).
   - If there's still no description, skip the job; it stays unscored (shown as "—"). Never guess a score from the title alone.
4. `mkdir -p WORK` and write `WORK/fits.json`: `[{"id": 3, "score": 74, "reason": "<one line: biggest plus; biggest gap>"}]`
5. `python3 tools/tracker.py set-fit --file WORK/fits.json --source backfill`, then `rm -rf WORK`.
6. Report: how many jobs were scored and skipped, then one line per scored job: `#<id> <score> <company> – <role>`.
