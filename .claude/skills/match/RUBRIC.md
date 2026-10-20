# Fit score rubric (0-100)

One rubric for every fit score: Match my profile, /tailor, /tailor-combined, /track, and /score-jobs.
Score the user **as they are on paper today**, from `base/resume.tex` and `base/facts.md` only. Never assume skills, years, or experience those files don't support. Score against the role itself, never against a tailored resume.

| Part | Points | What earns them |
|---|---|---|
| Must-have skills and technologies | 40 | Share of the JD's required skills that facts.md backs up with real work. Directly used at a job counts fully; projects-only counts half; a closely related technology counts half. |
| Core work and domain | 25 | How closely the day-to-day work (backend services, distributed systems, data platforms, LLM services, migrations, and so on) matches what they have actually built. |
| Level | 20 | The JD's seniority vs. about 4 years of experience with senior-level signals (design docs, design reviews, on-call ownership, cross-team migrations, mentoring). Senior / II-III or similar: full points. Staff+ or 7+ years required: 5-8. Entry-level or new-grad: 8-12 (over-qualified). |
| Location and logistics | 15 | Remote, in the home city or state from `base/profile.json`, or US-wide: full points. Hybrid elsewhere with occasional travel: 8. On-site outside the home state: 3. Unknown: 10. |

Bands: **85-100 Strong**, **70-84 Good**, **55-69 Moderate**, **under 55 Stretch**.

Hard caps:
- A required degree, clearance, or work-authorization condition they can't be confirmed to meet: cap at 60.
- The role is in a different discipline (sales engineering, hardware, people management, data science research): cap at 40.

The reason is one short line naming the biggest plus and the biggest gap, for example "Java/Kafka backend at scale matches; no Go or GitOps".
Scores are permanent once saved, so be calibrated rather than generous.
