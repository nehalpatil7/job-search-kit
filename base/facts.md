# Facts bank: the only source material for tailoring

Everything on a tailored resume, cold email or application answer must trace back to `base/resume.tex` or this file.
Where the two differ, `base/resume.tex` wins (it holds the latest edits).
Numbers are used exactly as written here. Never round, combine, or extrapolate them.

When the user confirms a new fact, add it here under the right role before using it.

This is an EXAMPLE for a fictional person (Jordan Lee). Replace every section with your own facts, keeping the
headings: one `## Company: Title (Location, Start to End)` section per role, then Links, Projects,
Allowed skills and Do not claim. Write more detail here than fits on the resume: the tailoring skill swaps
these facts in when a job asks for them.

---

## Example Corp: Software Engineer (Denver, CO, Jan 2023 to Present)

Stack: Java, Kafka, PostgreSQL, Kubernetes, AWS.

**Order event pipeline**
- Event-driven order pipeline processing 2M+ daily events with Kafka consumers and idempotent handlers.
- P95 end-to-end latency under 200ms during seasonal peaks.

**Billing service migration**
- Led the migration of a monolithic billing service to 4 independently deployable Java services.
- Coordinated zero-downtime cutovers across 12 dependent teams.

**Routing engine**
- Rules-based routing engine with priority-ordered configuration and a safe default; replaced hard-coded logic.
- Routing errors cut by 30%.

**Delivery**
- Contract tests and canary deployments in CI/CD; production rollbacks down 40% over two quarters.

**Senior signals**
- Wrote design docs; ran design reviews for the platform team; on-call for customer-facing services.

---

## Sample Labs: Software Engineer (Seattle, WA, Jun 2020 to Dec 2022)

Stack: Node.js, PostgreSQL, Redis, Terraform, Docker, AWS.

- REST APIs in Node.js and PostgreSQL for a marketplace serving 500K+ monthly users; Redis caching cut median response time by 35%.
- Terraform and Docker on AWS; new-environment setup from days to under an hour.
- Mentored 3 junior engineers; set the code-review checklist; first-pass review approval up 25%.
- Structured logging and dashboards; incident diagnosis from hours to minutes for the payments team.

---

## Placeholder Inc: Software Engineering Intern (Remote, May 2019 to Aug 2019)

Stack: Python, SQL.

- Python data-validation tool flagging schema drift in nightly ETL jobs; caught 15+ data issues before they reached reports.
- Integration tests for the internal analytics API; coverage from 52% to 81%.

---

## Links

- GitHub: https://github.com/example
- LinkedIn: https://linkedin.com/in/example

---

## Projects

Personal projects may be cited in application answers (with their link), never as resume bullets or as work done at an employer.

**Example project** (https://github.com/example/project). Personal project
- One or two lines on what it does and the stack. No invented user or star counts.

---

## Allowed skills

Extra skills the tailoring may add to the resume's Skills rows (comma-separated, only things you can really back up):

Python, Go, Terraform, gRPC, GraphQL, Spark, Airflow, GCP, Prometheus, Grafana

---

## Do not claim

- Anything not written in this file or in base/resume.tex.
- Years of experience beyond what the dates show.
