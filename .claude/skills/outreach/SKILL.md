---
name: outreach
description: Cold email recruiters at one company. Find the top 5 recruiters (Hunter.io), write a short personalized email to each, and save them for the user to review and send from the Cold email tab.
argument-hint: <company name or domain> [--apollo]
disable-model-invocation: true
allowed-tools: Read Write Glob Grep WebSearch WebFetch Bash(mkdir *) Bash(rm -rf .work/*) Bash(python3 tools/outreach.py find *) Bash(python3 tools/outreach.py import-contacts *) Bash(python3 tools/outreach.py add *) mcp__claude_ai_Apollo_io__apollo_mixed_people_api_search mcp__claude_ai_Apollo_io__apollo_people_bulk_match
---

# Cold emails to recruiters at one company

Input: $ARGUMENTS

**Apollo fallback (`--apollo`).** If the input contains `--apollo`, the user ticked "Use Apollo as a fallback (up to 5 Apollo credits)" when they started this run: that is their advance approval for this run to spend up to 5 Apollo credits, so don't ask again. Without `--apollo`, never call an Apollo tool.

**Scratch folder (`WORK`).** If the input ends with `--work <folder>`, that folder is `WORK` and the rest is the company. Otherwise `WORK` is `.work/cli`. Touch only your own `WORK`.

If the company is empty, reply `FAILED: no company given.` Whenever you stop without saving, your final reply must start with `FAILED:` and the reason. Never ask the user questions.

**You never send anything.** This skill only writes drafts. The user reviews them and presses Send themselves in the web page. Never run `outreach.py send`, `draft` or `eml`.

## Rules for every email
1. **Truth.** Every claim traces to `base/resume.tex` or `base/facts.md`, numbers copied exactly. No years of experience, no titles they never held. Describe the work ("At <employer> I built...") rather than current status. Never mention visa, salary or relocation.
2. **Short.** 60 to 110 words in the body. No greeting fluff ("I hope this finds you well"), no "My name is", no "I am writing to".
3. **Purpose and fit at the same time.** The first sentence states why they're writing *and* what they do, aimed at this company. Example: "I applied for Acme's Backend Engineer, Payments role, and I build the kind of high-volume backend systems Acme's payments platform runs on."
4. **Proof: one technical, one human.** Then exactly 2 bullets ("- " lines), each with a number:
   - one technical achievement picked for what this company builds (its product, scale, stack, open roles), in plain words a recruiter understands (no jargon pile-ups like "pub/sub", "p99", "RPC");
   - one soft-skill achievement: leading or mentoring a team, cross-functional work, ownership (design reviews, on-call), or delivery (e.g. "mentored 3 junior engineers and set the team's code-review standards", "coordinated zero-downtime cutovers across 12 dependent teams").
   The opening sentence can carry a light soft-skill note too (ownership, working with people), but stays one sentence.
5. **One ask.** End with one line that treats the recipient as the right person: a quick chat with them about <specific roles or team>, or, as the easy alternative, passing the resume to a hiring manager on one of those teams. Example: "My resume is attached. Would you be open to a quick chat about Payments or Platform roles, or to sharing it with a hiring manager on one of those teams?" Never ask to be pointed to "the right person" or "the recruiter who handles" the roles: it implies they aren't. Mention the resume is attached.
6. **Personal, not templated.** Greet by first name ("Hi Priya,"). Fit the email to the recipient's title: a technical recruiter gets the engineering angle, a university/early-career recruiter gets "early-career backend roles", a talent acquisition partner or sourcer is asked to share the resume with the hiring team. The 5 emails share the core facts but must not be identical: vary the first sentence and the ask.
7. **Plain text.** Paragraphs separated by a blank line. No sign-off and no name (the signature with phone and links is added automatically). No markdown, links, emoji or placeholders.
8. **Subject** (30 to 60 characters): simple, just the role and the company, e.g. `Software Engineer interested in Acme Payments roles` or `Backend Engineer, Payments at Acme`. No current or past employers, no metrics, no "Quick question", no clickbait.

## Steps
1. `mkdir -p WORK`, then run `python3 tools/outreach.py find --company "<company>" --work WORK` (add `--apollo` if the input has it). It searches Hunter.io for the company's recruiters, ranks them, writes the top 5 to `WORK/contacts.json`, and lists any tracked jobs at that company. If it fails, stop with its `FAILED:` message.
   **Only if it printed `APOLLO_NEEDED`** (Hunter is out of searches or found no one):
   - Find the company's website domain if `contacts.json` has none (one WebSearch).
   - `apollo_mixed_people_api_search` (free, no credits) with `q_organization_domains_list: ["<domain>"]`, `person_titles: ["technical recruiter", "recruiter", "talent acquisition partner", "university recruiter", "sourcer"]`, `person_locations: ["United States"]`, `contact_email_status: ["verified"]`, `per_page: 25`.
   - Pick at most 5 hands-on recruiters (recruiter, senior recruiter, technical recruiter, senior technical recruiter, talent acquisition partner, university recruiter, sourcer), technical and early-career ones first. Never pick directors, heads, VPs, managers, coordinators or recruiting operations: they rarely answer cold email. Enrich them with ONE `apollo_people_bulk_match` call, passing only each person's `id` (1 credit per match). Never set `reveal_personal_emails`, `reveal_phone_number` or any waterfall option. Never call it a second time.
   - Write `WORK/apollo.json`: `[{"email", "first_name", "last_name", "title", "linkedin", "email_status"}]`, copied from the match results; leave out anyone without a work email. Then run `python3 tools/outreach.py import-contacts --work WORK --file WORK/apollo.json`. If it fails, stop with its `FAILED:` message.
   - Say in the report how many Apollo credits were used.
2. Read `WORK/contacts.json`, `base/resume.tex` and `base/facts.md`.
3. **Learn the company** (2 to 3 searches or fetches at most): what it builds, its engineering scale and stack, and 1 or 2 open engineering roles that fit the user (or the tracked jobs listed by `find`: a role they already applied to is the strongest hook, "I applied for ...").
4. Pick the angle: the one line that connects their strongest evidence to this company. Choose the 2 proof bullets from it.
5. Write `WORK/emails.json`:
   ```
   {"company": "<Company>", "domain": "<domain from contacts.json>", "angle": "<one line>",
    "emails": [{"email": "<address exactly as in contacts.json>", "subject": "...", "body": "Hi <First>,\n\n...\n\n- ...\n- ...\n\n..."}]}
   ```
   One email per contact in `contacts.json`, using only those addresses.
6. Run `python3 tools/outreach.py add --file WORK/emails.json --clean-work WORK`. If it refuses (too long, placeholder, unknown address), fix `WORK/emails.json` and run it again.

## Report
```
**Outreach:** <Company>, <n> emails ready to review in the Cold email tab
**Source:** Hunter | Apollo (<k> credits used)
**Angle:** <one line>
**Recipients**
- <Name>, <title>
```
