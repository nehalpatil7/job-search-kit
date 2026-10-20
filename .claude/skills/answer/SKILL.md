---
name: answer
description: Write (or revise) answers to job-application questions for one job in the tracker, grounded in the resume used for that job and the user's facts.
argument-hint: [<record id> <question> | path/to/qa-input.json]
disable-model-invocation: true
allowed-tools: Read Write WebFetch WebSearch Bash(mkdir *) Bash(rm -rf .work/*) Bash(python3 tools/tracker.py *)
---

# Answer application questions

Input: $ARGUMENTS

**Scratch folder (`WORK`).** If the input ends with `--work <folder>`, that folder is `WORK` and the rest is the input. Otherwise `WORK` is `.work/cli`. Other runs may be working in `.work/` at the same time, so touch only your own `WORK`.

The input is one of:
- a path to a JSON file (from the web page it is `WORK/qa-input.json`), shaped like either
  `{"record_id": 7, "questions": ["...", "..."]}` (new questions), or
  `{"record_id": 7, "revise": {"id": 3, "instruction": "shorter, mention Kafka"}}` (rewrite one saved answer);
- or `<record id> <question text>` typed directly, which means one new question.

This can run unattended from the web page. Never ask the user anything. Read files with the Read tool and create them with the Write tool; never use `cat`, `echo >`, or heredocs in Bash.

## 1. Gather the material
- `python3 tools/tracker.py show <record id>` prints the job record. Use:
  - `jd`: the job description;
  - `resume_text`: the exact tailored resume sent for this job. Track-only jobs (`source` "tracked") used the base resume: Read `base/resume.tex` instead. If a tailored job has no `resume_text`, Read the PDF at the record's `resume` path;
  - `notes`: the match and gaps;
  - `qa`: answers already saved for this job (so new answers stay consistent and don't repeat the same story).
- If the record has no `jd` (tracked by name only), answer from the role title and company, plus a quick look at the company's site when the question needs it.
- If the record doesn't exist, stop and reply `FAILED: no job #<id>.` Whenever you stop without saving answers, your final reply must start with `FAILED:`.
- Read `base/facts.md` for detail behind the resume bullets (the "why" and "how" of each project) and its "Do not claim" list.

## 2. Read the question before anything else
Decide what the question is actually asking. That decides how much of the user's experience belongs in the answer, which is often very little.

| Question type | Examples | What the answer is about | Experience |
|---|---|---|---|
| Motivation / company | "Why us?", "What about our mission interests you?" | The company and why it matters to them | At most one short clause, only if it's the real reason |
| Values / culture / working style | "How do you handle disagreement?", "What team culture do you thrive in?", "How do you give feedback?" | Their approach, beliefs, and habits, stated directly | None by default. One brief, relevant moment only if it makes the point concrete |
| Behavioral ("Tell us about a time…") | conflict, failure, ambiguity, ownership | One situation, told well | Exactly one story: the single most relevant one |
| Technical concept | "How would you design X?", "SQL vs NoSQL?" | The technical answer itself, as an expert would give it | Optional single "in practice, I…" line, only if it's directly on topic |
| Experience / skills | "Your strongest technology?", "A project you're proud of?" | Their work | Yes: pick the one best example and go deep instead of listing several |
| Logistics | salary, start date, authorization, relocation | The fact | None. Use `[placeholders]` for anything not in the files |

**The relevance test.** Before including any piece of the user's experience, ask: does the question need this, and is it the single most relevant thing they have done for this question? If not, leave it out. Never add projects, metrics, or company names just to show more of their background, and never recap the resume. One well-chosen example woven into the sentence ("…which is how I ran the billing migration at Example Corp: …") beats three listed ones.

## 3. Sources
- **About the user**: use only `resume_text`, `base/facts.md` (including its Projects and Links sections), `base/resume.tex`, and the job's `jd`. Never invent experience, numbers, technologies, titles, team sizes, or years. When an example is needed, prefer one that appears on this job's resume.
- **About the company** (mission, products, values, engineering culture), only when the question needs it: one or two quick lookups on the company's own site (WebSearch, then WebFetch on their About, mission, careers, or engineering page). Use one or two specific, accurate details.
- **Values and working style**: The user's real habits show in facts.md (for example design docs and reviews, on-call ownership, code-review standards and mentoring, careful migrations). Express these as how they work, not as a list of achievements.
- **Personal facts not in the files** (salary, work authorization or sponsorship, start date, notice period, relocation specifics): never guess. Put a short bracketed placeholder, e.g. `[expected base salary]`, inside an otherwise complete answer.

## 4. How to write each answer
- **Answer first.** The first sentence directly answers the question. No warm-up, no restating it.
- **Crisp.** Default 50–100 words. Motivation and culture questions: 40–80. Behavioral stories and technical deep dives: up to 150. If the question states a word or character limit, stay under it.
- **Sound like a person.** A natural first-person voice, with a clear point of view: what they believe, how they work, and why. Make it interesting enough that a recruiter keeps reading, but don't pad.
- **Behavioral stories:** situation in one line, what they did and why, the result, and what they took from it. One story only.
- **Technical answers:** answer the way a strong engineer would. State the tradeoffs and give a recommendation, using correct terminology and no buzzwords.
- **Avoid:** "I am passionate", "I am excited", "thrilled", "leverage", "synergy", "fast-paced", "dynamic", "cutting-edge", exclamation marks, and repeating resume lines word for word.
- **Format:** plain-text paragraphs ready to paste into a form. No markdown, headings, or bullets unless the question asks for a list. One or two short paragraphs at most.
- **Batches:** across the questions in one run, don't reuse the same example, and don't force an example into answers that don't need one.

For a **revision**, rewrite only that one saved answer, following the instruction while keeping every rule above. The instruction wins on length and emphasis, but never on truthfulness.

## 5. Save
- `mkdir -p WORK`, then Write `WORK/answers.json`:
  - new questions: `[{"q": "<question exactly as given>", "a": "<answer>"}, ...]`
  - revision: `[{"id": <answer id>, "a": "<new answer>"}]`
- `python3 tools/tracker.py qa-add <record id> --file WORK/answers.json`
- `rm -rf WORK`

## 6. Report
Reply with each question in bold, followed by its answer, and nothing else.
