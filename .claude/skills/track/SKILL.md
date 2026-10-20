---
name: track
description: Add a job to the application tracker without tailoring a resume.
argument-hint: [JD url | path/to/jd.txt | pasted JD text | "Company - Role"]
disable-model-invocation: true
allowed-tools: Read Write WebFetch Bash(mkdir *) Bash(rm -rf .work/*) Bash(python3 tools/tracker.py *)
---

# Track a job (no resume tailoring)

Input: $ARGUMENTS

**Scratch folder (`WORK`).** If the input ends with `--work <folder>`, that folder is `WORK` and the rest is the job input. Otherwise `WORK` is `.work/cli`. Other runs may be working in `.work/` at the same time, so touch only your own `WORK`.

Read files with the Read tool and create them with the Write tool; never use `cat`, `echo >`, or heredocs in Bash.

**Fit score.** If the input contains `--fit <n>`, it came from Match my profile: use it (source `match`). Otherwise, when you have the JD text, score it with `.claude/skills/match/RUBRIC.md` (a quick pass, no deep analysis). With no JD text ("Company - Role" only), leave the score out.

Only add the job to the tracker. Do not tailor, edit, or build a resume, and do not create files outside `WORK`.
This can run unattended from the web page, so don't ask questions. Whenever you stop without saving to the tracker (including empty input), your final reply must start with `FAILED:` and the reason.

1. **Get the job details.**
   - URL: fetch it with WebFetch. Read off the company, exact role title, and location, and keep the JD text.
     If the page won't load, work out the company (and role, if visible) from the URL itself. If the role still can't be determined, stop and reply `FAILED: couldn't read the job from <url>.`
   - File path (for example `WORK/input.md`): read it. Pasted JD text: read it as is.
   - A short "Company - Role" string: use it directly.
2. **Save the JD text, if you have it.** `mkdir -p WORK`, then write it to `WORK/jd.md` (source URL on the first line).
3. **Add to the tracker** (quote every value; leave out `--jd-file` when there is no JD text, `--url` when there is no URL, and the three `--fit` options when there is no score):

```
python3 tools/tracker.py add --company "<Company>" --role "<Role title>" --location "<Location>" --url "<url>" --source tracked --resume base --jd-file WORK/jd.md --fit <score> --fit-reason "<one line>" --fit-source <match|track> --clean-work WORK
```

   This records the job as "Applied" with today's date and links the base resume PDF. If the job is already in the tracker, the existing record is updated instead of duplicated.
4. **Report** in one line: the record number, company, role, and status.
