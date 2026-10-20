#!/usr/bin/env python3
"""Job application tracker (stored in tracker.json at the project root).

Usage:
  python3 tools/tracker.py add --company C --role R [--location L] [--url U]
                               [--status S] [--source tailored|tracked]
                               [--jd-file F] [--notes-file F]
                               [--resume-from .work/<run>/<First>_<Last>_Resume.pdf | --resume base]
                               [--clean-work .work/<run>]
  python3 tools/tracker.py update ID [--status S] [--notes-file F]
  python3 tools/tracker.py add-combined --manifest WORK/jobs.json --resume-from WORK/<First>_<Last>_Resume.pdf [--clean-work WORK]
  python3 tools/tracker.py import-gmail --file WORK/import.json [--clean-work WORK]
  python3 tools/tracker.py list
  python3 tools/tracker.py base-pdf
  python3 tools/tracker.py show ID
  python3 tools/tracker.py qa-add ID --file answers.json   # [{"q","a"}] new, [{"id","a"}] revise

add:
  * If a record with the same URL (or the same company + role) exists, it is updated
    instead of duplicated, so a job tracked earlier can later get a tailored resume.
  * --resume-from verifies the resume.tex next to that PDF with check_resume.py first and refuses to
    save a resume that fails. The PDF is copied to resumes/ under a job-specific name.
  * --resume base links the base resume PDF (built into resumes/ if missing or stale).
  * --clean-work DIR deletes that run's scratch folder (must be inside .work/) after a successful add.
"""
import argparse
import contextlib
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import me  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tracker.json"
RESUMES = ROOT / "resumes"
WORK = ROOT / ".work"
BASE_PDF_NAME = f"{me.name_slug()}_Resume.pdf"   # name from base/profile.json

STATUSES = ["Saved", "Resume ready", "Applied", "Interviewing", "Offer", "Rejected", "Withdrawn"]
EDITABLE = {"company", "role", "location", "url", "status", "notes", "date_applied"}


# ---------- storage ----------

@contextlib.contextmanager
def locked():
    """Exclusive lock around read-modify-write so the CLI and the web server never clash."""
    lock_path = ROOT / ".tracker.lock"
    with open(lock_path, "w") as fh:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:  # Windows: best effort
            pass
        yield


def load():
    if not DATA.exists():
        return []
    try:
        return json.loads(DATA.read_text() or "[]")
    except json.JSONDecodeError:
        sys.exit(f"tracker.json is not valid JSON; fix or restore it before continuing ({DATA})")


def save(records):
    tmp = DATA.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, DATA)


def today():
    return dt.date.today().isoformat()


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


TRACKING_PARAMS = re.compile(r"^(utm_.*|gclid|fbclid|msclkid|trk|trkInfo|refId|trackingId|src|source|ref|lipi)$", re.I)


def clean_url(url):
    """Drop tracking parameters (utm_*, gclid, LinkedIn's trk, ...) so the same posting is one record."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", urlencode(query), ""))


def find_existing(records, url, company, role, source="tracked"):
    """Same posting = same link. Same company + role counts as the same job only when one of
    the two has no link (e.g. tracked as "Company - Role" first, tailored from the link later);
    two different links for the same title are different postings and stay separate records."""
    url = clean_url(url)
    if url:
        for r in records:
            if r.get("url") and clean_url(r["url"]) == url:
                return r
    for r in records:
        if norm(r.get("company")) == norm(company) and norm(r.get("role")) == norm(role):
            if url and r.get("url"):
                continue  # two different links: two different postings
            if source == "tailored" and r.get("source") == "tailored":
                continue  # never overwrite another job's tailored resume, notes and answers
            return r
    return None


# ---------- resumes ----------

def slug(s, n=40):
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")
    return s[:n].rstrip("-") or "Job"


def base_pdf():
    """Path to the base resume PDF in resumes/, rebuilt when base/resume.tex is newer."""
    sys.path.insert(0, str(ROOT / "tools"))
    import check_resume
    RESUMES.mkdir(exist_ok=True)
    pdf = RESUMES / BASE_PDF_NAME
    if not pdf.exists() or pdf.stat().st_mtime < check_resume.BASE_TEX.stat().st_mtime:
        out, _, errs = check_resume.compile_tex(check_resume.BASE_TEX, RESUMES)
        if errs:
            sys.exit("Could not build the base resume PDF: " + "; ".join(errs))
    return pdf


def publish_tailored(pdf_from, company, role, record_id, name=None):
    """Verify the tailored .tex, then copy its PDF into resumes/ with a job-specific name."""
    sys.path.insert(0, str(ROOT / "tools"))
    import check_resume
    tex = Path(pdf_from).resolve().parent / "resume.tex"
    if not tex.exists():
        sys.exit(f"Expected the tailored source next to the PDF: {tex}")
    fails, warns, pdf = check_resume.run_checks(tex)
    if fails:
        sys.exit("Refusing to save: the tailored resume fails its checks.\n"
                 + check_resume.report(tex, fails, warns, pdf))
    RESUMES.mkdir(exist_ok=True)
    name = name or f"{me.name_slug()}_Resume_{slug(company, 30)}_{slug(role)}.pdf"
    dest = RESUMES / name
    owner = next((r for r in load() if r.get("resume") == f"resumes/{name}"), None)
    if dest.exists() and owner and owner.get("id") != record_id:
        base, n = f"{dest.stem}_{today()}", 2
        dest = RESUMES / f"{base}.pdf"
        while dest.exists():  # never overwrite another job's PDF
            dest, n = RESUMES / f"{base}_{n}.pdf", n + 1
    shutil.copy(pdf, dest)
    return dest, warns


def rel(p):
    return str(Path(p).resolve().relative_to(ROOT))


def delatex(s):
    s = s.replace("$\\sim$", "~").replace("--", "–").replace("\\\\", "")
    s = re.sub(r"\\([%&#_$])", r"\1", s)
    s = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", s)
    s = s.replace("\\hfill", " |")
    s = re.sub(r"\\[a-zA-Z]+\*?", "", s)
    return re.sub(r"\s+", " ", s.replace("{", "").replace("}", "")).strip()


def resume_text(tex_path):
    """Readable text of a resume .tex: sections, role lines, bullets, skills."""
    body = Path(tex_path).read_text().split("\\begin{document}", 1)[-1]
    out = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("%") or s.startswith(("\\vspace", "\\begin", "\\end")):
            continue
        sec = re.match(r"\\section\*\{(.*)\}", s)
        if sec:
            out.append(f"\n## {delatex(sec.group(1))}")
        elif s.startswith("\\item"):
            out.append("- " + delatex(s[len("\\item"):]))
        else:
            out.append(delatex(s))
    return "\n".join(out).strip()


# ---------- application questions ----------

def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def get(rec_id):
    return next((r for r in load() if r["id"] == rec_id), None)


def qa_add(rec_id, items):
    """Add answers ({"q", "a"}) or revise existing ones ({"id", "a"}); old answers go to history."""
    with locked():
        records = load()
        rec = next((r for r in records if r["id"] == rec_id), None)
        if not rec:
            raise ValueError(f"No record #{rec_id}")
        qa = rec.setdefault("qa", [])
        changed = []
        for it in items:
            answer = (it.get("a") or "").strip()
            if not answer:
                continue
            if it.get("id"):
                cur = next((x for x in qa if x["id"] == int(it["id"])), None)
                if not cur:
                    raise ValueError(f"No question #{it['id']} on record #{rec_id}")
                cur.setdefault("history", []).append({"a": cur["a"], "at": cur.get("updated", "")})
                cur.update({"a": answer, "updated": now()})
                changed.append(cur)
            elif (it.get("q") or "").strip():
                new = {"id": max((x["id"] for x in qa), default=0) + 1, "q": it["q"].strip(), "a": answer,
                       "created": now(), "updated": now(), "history": []}
                qa.append(new)
                changed.append(new)
        rec["updated"] = now()
        save(records)
        return changed


def qa_edit(rec_id, qa_id, answer):
    return qa_add(rec_id, [{"id": qa_id, "a": answer}])


def qa_delete(rec_id, qa_id):
    with locked():
        records = load()
        rec = next((r for r in records if r["id"] == rec_id), None)
        if not rec or not any(x["id"] == qa_id for x in rec.get("qa", [])):
            return False
        rec["qa"] = [x for x in rec["qa"] if x["id"] != qa_id]
        save(records)
        return True


# ---------- fit score (set once, never changed) ----------

FIT_BANDS = [(85, "Strong"), (70, "Good"), (55, "Moderate"), (0, "Stretch")]


def fit_label(score):
    return next(label for floor, label in FIT_BANDS if score >= floor)


def make_fit(score, reason="", source="tailor"):
    try:
        score = int(round(float(score)))
    except (TypeError, ValueError):
        raise ValueError(f"fit score must be a number from 0 to 100, got {score!r}")
    if not 0 <= score <= 100:
        raise ValueError(f"fit score must be 0-100, got {score}")
    return {"score": score, "label": fit_label(score), "reason": (reason or "").strip()[:300],
            "source": source, "set": now()}


def apply_fit(rec, score, reason="", source="tailor"):
    """Attach a fit score unless the record already has one (fit scores are immutable)."""
    if score is None or score == "" or rec.get("fit"):
        return False
    rec["fit"] = make_fit(score, reason, source)
    return True


def set_fits(items, source="backfill"):
    """items: [{"id", "score", "reason"}]. Records that already have a fit are left untouched."""
    with locked():
        records = load()
        by_id = {r["id"]: r for r in records}
        done, kept = [], []
        for it in items:
            rec = by_id.get(int(it["id"]))
            if not rec:
                raise ValueError(f"No record #{it['id']}")
            (done if apply_fit(rec, it.get("score"), it.get("reason", ""), it.get("source", source)) else kept).append(rec)
        save(records)
        return done, kept


# ---------- commands ----------

def add(company, role, location="", url="", status=None, source="tracked",
        jd="", notes="", resume_from=None, resume_base=False, clean_work=False,
        fit=None, fit_reason="", fit_source=None):
    company, role, url = company.strip(), role.strip(), clean_url(url)
    if not company or not role:
        sys.exit("company and role are required")
    status = status or ("Resume ready" if source == "tailored" else "Applied")
    if status not in STATUSES:
        sys.exit(f"status must be one of: {', '.join(STATUSES)}")

    with locked():
        records = load()
        rec = find_existing(records, url, company, role, source)
        created = rec is None
        if created:
            rec = {"id": max((r["id"] for r in records), default=0) + 1, "date_added": today(),
                   "date_applied": "", "resume": "", "notes": "", "jd": ""}
            records.append(rec)

        warns = []
        if resume_from:
            dest, warns = publish_tailored(resume_from, company, role, rec["id"])
            rec["resume"] = rel(dest)
            # plain-text copy of exactly what this resume says, for answering application questions later
            rec["resume_text"] = resume_text(Path(resume_from).resolve().parent / "resume.tex")
        elif resume_base and not rec.get("resume"):
            rec["resume"] = rel(base_pdf())

        rec.update({"company": company, "role": role,
                    "location": location or rec.get("location", ""),
                    "url": url or rec.get("url", ""),
                    "source": "tailored" if (source == "tailored" or rec.get("source") == "tailored") else "tracked"})
        # don't move a job backwards (e.g. re-tailoring something already marked Interviewing)
        if created or STATUSES.index(status) > STATUSES.index(rec.get("status", "Saved")):
            rec["status"] = status
        if rec["status"] == "Applied" and not rec.get("date_applied"):
            rec["date_applied"] = today()
        if jd:
            rec["jd"] = jd
        if notes:
            rec["notes"] = notes
        apply_fit(rec, fit, fit_reason, fit_source or ("tailor" if source == "tailored" else "track"))
        rec["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        save(records)

    if clean_work:
        d = Path(clean_work).resolve()
        if WORK.resolve() in d.parents or d == WORK.resolve():
            shutil.rmtree(d, ignore_errors=True)
    return rec, created, warns


MAX_COMBINED = 5


def add_combined(manifest_path, resume_from, clean_work=None):
    """One verified PDF tailored to several jobs; each job gets (or updates) its own record linked to it.

    manifest: [{"company", "role", "location", "url", "jd_file", "notes_file", "fit", "fit_reason", "fit_source"}, ...]
    (2 to MAX_COMBINED)
    """
    jobs = json.loads(Path(manifest_path).read_text())
    if not isinstance(jobs, list) or not 2 <= len(jobs) <= MAX_COMBINED:
        sys.exit(f"The manifest must list 2 to {MAX_COMBINED} jobs.")
    for j in jobs:
        if not (j.get("company") or "").strip() or not (j.get("role") or "").strip():
            sys.exit("Every job in the manifest needs a company and a role.")
    companies = list(dict.fromkeys(slug(j["company"], 20) for j in jobs))  # each company once, in order
    label = "-".join(companies)[:60].rstrip("-")
    tex = Path(resume_from).resolve().parent / "resume.tex"
    names = [f"{j['company'].strip()} - {j['role'].strip()}" for j in jobs]

    with locked():
        records = load()
        dest, warns = publish_tailored(resume_from, "", "", None, name=f"{me.name_slug()}-{label}.pdf")
        text = resume_text(tex)
        out = []
        for j, me in zip(jobs, names):
            company, role, url = j["company"].strip(), j["role"].strip(), clean_url(j.get("url", ""))
            rec = find_existing(records, url, company, role)
            created = rec is None
            if created:
                rec = {"id": max((r["id"] for r in records), default=0) + 1, "date_added": today(),
                       "date_applied": "", "resume": "", "notes": "", "jd": ""}
                records.append(rec)
            rec.update({"company": company, "role": role, "location": j.get("location") or rec.get("location", ""),
                        "url": url or rec.get("url", ""), "source": "tailored", "resume": rel(dest),
                        "resume_text": text, "combined_with": [n for n in names if n != me]})
            if created or STATUSES.index("Resume ready") > STATUSES.index(rec.get("status", "Saved")):
                rec["status"] = "Resume ready"
            if j.get("jd_file") and Path(j["jd_file"]).exists():
                rec["jd"] = Path(j["jd_file"]).read_text()
            if j.get("notes_file") and Path(j["notes_file"]).exists():
                rec["notes"] = Path(j["notes_file"]).read_text()
            apply_fit(rec, j.get("fit"), j.get("fit_reason", ""), j.get("fit_source", "tailor"))
            rec["updated"] = now()
            out.append((rec, created))
        save(records)

    if clean_work:
        d = Path(clean_work).resolve()
        if WORK.resolve() in d.parents:
            shutil.rmtree(d, ignore_errors=True)
    return dest, out, warns


def import_gmail(file, clean_work=None):
    """Link Gmail application confirmations to tracker jobs, adding the jobs that aren't tracked yet.

    file: [{"gmail_id", "company", "role", "location", "match": <tracker id or null>}], next to the emails.json
    written by `inbox.py pending` (each gmail_id must be one of those emails; its date becomes the applied date).
    """
    items = json.loads(Path(file).read_text())
    emails = {e["id"]: e for e in json.loads((Path(file).parent / "emails.json").read_text())}
    out, problems = [], []
    with locked():
        records = load()
        linked = {g for r in records for g in r.get("gmail_ids", [])}
        for it in items:
            gid, company = it.get("gmail_id"), (it.get("company") or "").strip()
            role = (it.get("role") or "").strip() or "Unknown role"
            if gid not in emails:
                problems.append(f"{gid}: not one of the emails in emails.json")
                continue
            if gid in linked:
                continue
            if not company:
                problems.append(f"{gid}: no company")
                continue
            day = emails[gid]["date"]
            rec = next((r for r in records if r["id"] == it.get("match")), None) if it.get("match") else None
            rec = rec or find_existing(records, "", company, role)
            created = rec is None
            if created:
                rec = {"id": max((r["id"] for r in records), default=0) + 1, "date_added": day, "date_applied": day,
                       "company": company, "role": role, "location": (it.get("location") or "").strip(), "url": "",
                       "status": "Applied", "source": "gmail", "resume": "", "notes": "", "jd": ""}
                records.append(rec)
            else:
                if STATUSES.index(rec.get("status", "Saved")) < STATUSES.index("Applied"):
                    rec["status"] = "Applied"
                if not rec.get("date_applied"):
                    rec["date_applied"] = day
                if not rec.get("location") and it.get("location"):
                    rec["location"] = it["location"].strip()
            rec.setdefault("gmail_ids", []).append(gid)
            rec["updated"] = now()
            linked.add(gid)
            out.append((rec, created))
        save(records)
    if clean_work:
        d = Path(clean_work).resolve()
        if WORK.resolve() in d.parents:
            shutil.rmtree(d, ignore_errors=True)
    return out, problems


def update(rec_id, **fields):
    with locked():
        records = load()
        rec = next((r for r in records if r["id"] == rec_id), None)
        if not rec:
            return None
        for k, v in fields.items():
            if k not in EDITABLE or v is None:
                continue
            if k == "status":
                if v not in STATUSES:
                    raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
                if v == "Applied" and not rec.get("date_applied"):
                    rec["date_applied"] = today()
            rec[k] = v
        rec["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        save(records)
        return rec


def delete(rec_id):
    with locked():
        records = load()
        keep = [r for r in records if r["id"] != rec_id]
        if len(keep) == len(records):
            return False
        save(keep)
        return True


def read_opt(path):
    return Path(path).read_text() if path else ""


def main():
    p = argparse.ArgumentParser(description="Job application tracker")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("--company", required=True)
    a.add_argument("--role", required=True)
    a.add_argument("--location", default="")
    a.add_argument("--url", default="")
    a.add_argument("--status", choices=STATUSES)
    a.add_argument("--source", choices=["tailored", "tracked"], default="tracked")
    a.add_argument("--jd-file")
    a.add_argument("--notes-file")
    g = a.add_mutually_exclusive_group()
    g.add_argument("--resume-from", help="tailored PDF in .work/<run>/ (its resume.tex is verified first)")
    g.add_argument("--resume", choices=["base"], help="link the base resume PDF")
    a.add_argument("--clean-work", metavar="DIR", help="delete this run's scratch folder (inside .work/) afterwards")
    a.add_argument("--fit", type=int, help="fit score 0-100 (only set if the job has none yet)")
    a.add_argument("--fit-reason", default="")
    a.add_argument("--fit-source", choices=["match", "tailor", "track"])

    u = sub.add_parser("update")
    u.add_argument("id", type=int)
    u.add_argument("--status", choices=STATUSES)
    u.add_argument("--notes-file")

    c = sub.add_parser("add-combined", help="save one resume tailored to 2-5 jobs and track each job")
    c.add_argument("--manifest", required=True)
    c.add_argument("--resume-from", required=True)
    c.add_argument("--clean-work", metavar="DIR")

    sf = sub.add_parser("set-fit", help='set fit scores from a JSON file [{"id","score","reason"}]; existing scores never change')
    sf.add_argument("--file", required=True)
    sf.add_argument("--source", default="backfill")

    us = sub.add_parser("unscored", help="print jobs without a fit score as JSON (id, company, role, location, url, jd)")
    us.add_argument("--ids", help="comma-separated job ids to include (one scoring batch)")
    us.add_argument("--jd-chars", type=int, default=4000, help="truncate each saved job description")
    rb = sub.add_parser("backfill-random", help="give unscored jobs a RANDOM fit score (marked 'random'; permanent)")
    rb.add_argument("--yes", action="store_true")

    ig = sub.add_parser("import-gmail", help="link Gmail confirmations to jobs (adding untracked ones) from WORK/import.json")
    ig.add_argument("--file", required=True)
    ig.add_argument("--clean-work", metavar="DIR")

    sub.add_parser("list")
    sub.add_parser("base-pdf")

    sh = sub.add_parser("show", help="print one record as JSON (for answering questions)")
    sh.add_argument("id", type=int)

    qa = sub.add_parser("qa-add", help='save answers from a JSON file: [{"q","a"}] or [{"id","a"}] to revise')
    qa.add_argument("id", type=int)
    qa.add_argument("--file", required=True)
    args = p.parse_args()

    if args.cmd == "add":
        try:
            if args.fit is not None:
                make_fit(args.fit)  # validate before saving anything
        except ValueError as exc:
            sys.exit(str(exc))
        rec, created, warns = add(args.company, args.role, args.location, args.url, args.status,
                                  args.source, read_opt(args.jd_file), read_opt(args.notes_file),
                                  args.resume_from, args.resume == "base", args.clean_work,
                                  args.fit, args.fit_reason, args.fit_source)
        fit = f" fit: {rec['fit']['score']}" if rec.get("fit") else ""
        print(f"{'Added' if created else 'Updated'} #{rec['id']}: {rec['company']} - {rec['role']} "
              f"[{rec['status']}] resume: {rec['resume'] or 'none'}{fit}")
        for w in warns:
            print(f"  WARN  {w}")
    elif args.cmd == "add-combined":
        dest, out, warns = add_combined(args.manifest, args.resume_from, args.clean_work)
        print(f"Saved {rel(dest)}")
        for rec, created in out:
            print(f"  {'Added' if created else 'Updated'} #{rec['id']}: {rec['company']} - {rec['role']} [{rec['status']}]")
        for w in warns:
            print(f"  WARN  {w}")
    elif args.cmd == "update":
        rec = update(args.id, status=args.status, notes=read_opt(args.notes_file) or None)
        if not rec:
            sys.exit(f"No record #{args.id}")
        print(f"Updated #{rec['id']}: {rec['company']} - {rec['role']} [{rec['status']}]")
    elif args.cmd == "set-fit":
        try:
            done, kept = set_fits(json.loads(Path(args.file).read_text()), args.source)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            sys.exit(f"set-fit failed: {exc}")
        for r in done:
            print(f"Set fit #{r['id']}: {r['fit']['score']} ({r['fit']['label']}) {r['company']} - {r['role']}")
        for r in kept:
            print(f"Kept fit #{r['id']}: {r['fit']['score']} (already set; fit scores never change)")
    elif args.cmd == "unscored":
        ids = {int(x) for x in args.ids.split(",") if x.strip()} if args.ids else None
        print(json.dumps([dict({k: r.get(k, "") for k in ("id", "company", "role", "location", "url", "source")},
                               jd=(r.get("jd") or "")[:args.jd_chars]) for r in load()
                          if not r.get("fit") and (ids is None or r["id"] in ids)],
                         indent=1, ensure_ascii=False))
    elif args.cmd == "backfill-random":
        if not args.yes:
            sys.exit("Random scores are permanent and carry no information. Re-run with --yes if you really want them.")
        import random
        todo = [{"id": r["id"], "score": random.randint(40, 95), "reason": "Random placeholder score.",
                 "source": "random"} for r in load() if not r.get("fit")]
        done, _ = set_fits(todo, "random")
        print(f"Gave {len(done)} job(s) a random fit score (marked 'random').")
    elif args.cmd == "import-gmail":
        out, problems = import_gmail(args.file, args.clean_work)
        for rec, created in out:
            print(f"{'Added' if created else 'Updated'} #{rec['id']}: {rec['company']} - {rec['role']} [{rec['status']}] from Gmail")
        print(f"Imported {len(out)} confirmation(s): {sum(c for _, c in out)} new job(s), {sum(not c for _, c in out)} linked to tracked jobs.")
        for x in problems:
            print(f"  SKIPPED {x}")
    elif args.cmd == "list":
        for r in load():
            print(f"#{r['id']:<4}{r['date_added']}  {r['status']:<13}{r['company']} - {r['role']}  "
                  f"{r.get('resume') or ''}")
    elif args.cmd == "base-pdf":
        print(rel(base_pdf()))
    elif args.cmd == "show":
        rec = get(args.id)
        if not rec:
            sys.exit(f"No record #{args.id}")
        print(json.dumps(rec, indent=2, ensure_ascii=False))
    elif args.cmd == "qa-add":
        try:
            items = json.loads(Path(args.file).read_text())
            changed = qa_add(args.id, items if isinstance(items, list) else [items])
        except (ValueError, json.JSONDecodeError) as exc:
            sys.exit(f"qa-add failed: {exc}")
        for x in changed:
            print(f"Saved Q#{x['id']} on record #{args.id}: {x['q'][:70]}")


if __name__ == "__main__":
    main()
