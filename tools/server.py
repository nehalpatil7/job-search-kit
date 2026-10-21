#!/usr/bin/env python3
"""Local web page for the job tracker, and for running /tailor, /tailor-combined, /track, /answer and /outreach through Claude Code.

  python3 tools/server.py                 # then open http://localhost:8765
  python3 tools/server.py --parallel 3    # run up to 3 Claude Code jobs at once (default 2)
  python3 tools/server.py --port 9000

Only listens on 127.0.0.1. Uses only the Python standard library.
The tracker works without Claude Code; the Tailor / Track / Answer buttons need the `claude` CLI on PATH.
"""
import argparse
import csv
import io
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import tracker  # noqa: E402
import careers  # noqa: E402
import outreach  # noqa: E402
import inbox  # noqa: E402

UI = TOOLS / "ui.html"
WORK = ROOT / ".work"                    # each run gets its own .work/<run id>/ folder
RUN_TIMEOUT = 20 * 60                    # seconds
KEEP_FINISHED = 30                       # finished runs kept in the list
CLEAR_DONE_AFTER = 30                    # seconds a successful run stays in the list (failures stay until cleared)
MAX_COMBINED = tracker.MAX_COMBINED      # jobs per combined resume
ALLOWED_TOOLS = ",".join([
    "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch",
    "Bash(mkdir *)", "Bash(cp *)", "Bash(rm -rf .work/*)",
    "Bash(python3 tools/check_resume.py *)", "Bash(python3 tools/tracker.py *)", "Bash(python3 tools/careers.py *)",
    # runs may find recruiters and save drafts, never send: sending is a button the user presses
    "Bash(python3 tools/outreach.py contacts *)", "Bash(python3 tools/outreach.py find *)", "Bash(python3 tools/outreach.py add *)",
    "Bash(python3 tools/outreach.py import-contacts *)", "Bash(python3 tools/inbox.py pending *)",
    # Apollo fallback for /outreach: free people search + email reveal (1 credit each, capped by the skill and outreach.py)
    "mcp__claude_ai_Apollo_io__apollo_mixed_people_api_search", "mcp__claude_ai_Apollo_io__apollo_people_bulk_match",
])

SAVED_JOB = re.compile(r"(Added|Updated) #(\d+): (.+?) \[")
SAVED_QA = re.compile(r"Saved Q#(\d+) on record #(\d+)")
SAVED_MATCH = re.compile(r"Saved match #(\d+): (.+)")
SAVED_FIT = re.compile(r"Set fit #(\d+): (\d+) \((\w+)\) (.+)")
SAVED_OUTREACH = re.compile(r"Saved outreach #(\d+): (.+)")

CLAUDE = shutil.which("claude")
JOBS = {}                                # id -> Job, in creation order
JOBS_LOCK = threading.Lock()
PARALLEL = 2
PORT = 8765


# ---------- Claude Code runs ----------

class Job:
    def __init__(self, mode, preview, record_id=None):
        self.id = uuid.uuid4().hex[:8]
        self.mode = mode
        self.record_id = record_id
        self.preview = preview.strip().splitlines()[0][:140] if preview.strip() else ""
        self.work = WORK / self.id
        self.arg = ""
        self.status = "queued"   # queued | running | done | failed | cancelled
        self.log = []
        self.result = ""
        self.cost = None
        self.created = time.time()
        self.started = None
        self.finished = None
        self.proc = None
        self.saved = []          # what the run saved to the tracker, parsed from the tracker CLI output

    def add(self, kind, text):
        self.log.append({"kind": kind, "text": text})

    def view(self):
        return {"id": self.id, "mode": self.mode, "record_id": self.record_id, "preview": self.preview,
                "status": self.status, "log": self.log, "result": self.result, "cost": self.cost, "saved": self.saved,
                "created": self.created, "started": self.started, "finished": self.finished}


def describe_tool(name, inp):
    inp = inp or {}
    if name == "Bash":
        return f"$ {inp.get('command', '')}"[:200]
    for key in ("file_path", "url", "pattern", "path"):
        if inp.get(key):
            val = str(inp[key]).replace(str(ROOT) + "/", "")
            return f"{name}: {val}"[:200]
    return name


def handle_event(job, line):
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        if line.strip():
            job.add("raw", line.strip()[:300])
        return
    t = ev.get("type")
    if t == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                job.add("text", block["text"].strip())
            elif block.get("type") == "tool_use":
                job.add("tool", describe_tool(block.get("name", "tool"), block.get("input")))
    elif t == "user":
        for block in (ev.get("message", {}).get("content") or []):
            if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                continue
            content = block.get("content")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            content = str(content or "")
            if block.get("is_error"):
                job.add("error", content.strip()[:400] or "tool error")
            for m in SAVED_JOB.finditer(content):
                job.saved.append(f"{m.group(1)} #{m.group(2)}: {m.group(3).strip()}")
            for m in SAVED_QA.finditer(content):
                job.saved.append(f"Saved answer Q#{m.group(1)} on #{m.group(2)}")
            for m in SAVED_MATCH.finditer(content):
                job.saved.append(f"Match #{m.group(1)}: {m.group(2).strip()}")
            for m in SAVED_FIT.finditer(content):
                job.saved.append(f"Fit {m.group(2)} for #{m.group(1)}: {m.group(4).strip()}")
            for m in SAVED_OUTREACH.finditer(content):
                job.saved.append(f"Outreach #{m.group(1)}: {m.group(2).strip()}")
    elif t == "system" and ev.get("subtype") == "permission_denied":
        job.add("error", f"Blocked (needs permission): {json.dumps(ev)[:300]}")
    elif t == "result":
        job.result = ev.get("result") or ""
        job.cost = ev.get("total_cost_usd")
        for d in ev.get("permission_denials") or []:
            job.add("error", f"Blocked (needs permission): {d.get('tool_name', '')} {json.dumps(d.get('tool_input', ''))[:240]}")
        if ev.get("is_error"):
            job.status = "failed"


def run_job(job):
    prompt = f"/{job.mode} {job.arg} --work .work/{job.id}"
    cmd = [CLAUDE, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits", "--allowedTools", ALLOWED_TOOLS]
    try:
        job.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1, start_new_session=True)
        timer = threading.Timer(RUN_TIMEOUT, lambda: cancel(job, "Timed out after 20 minutes."))
        timer.start()
        for line in job.proc.stdout:
            handle_event(job, line)
        job.proc.wait()
        timer.cancel()
        if job.status == "running":
            job.status = "done" if job.proc.returncode == 0 else "failed"
        if job.status == "done" and job.result.lstrip().startswith("FAILED"):
            job.status = "failed"   # the skill stopped on purpose, e.g. the job page couldn't be read
        elif job.status == "done" and not job.saved:
            job.status = "failed"
            job.add("error", "Claude Code finished, but nothing was saved to the tracker. "
                             "See the result and the log above for the reason.")
    except Exception as exc:  # keep the server alive whatever happens
        job.add("error", f"Could not run Claude Code: {exc}")
        job.status = "failed"
    finally:
        job.finished = time.time()
        shutil.rmtree(job.work, ignore_errors=True)
        pump()


AUTOFILL = [shutil.which("node") or "node", str(TOOLS / "apply" / "autofill.js")]


def run_autofill(job):
    """Run the Puppeteer prefill script; it ends when the user closes the browser window."""
    try:
        job.proc = subprocess.Popen(AUTOFILL + [str(job.record_id)], cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        for line in job.proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            kind = ("error" if line.startswith(("FAILED:", "WARN:")) else
                    "needs" if line.startswith("NEEDS YOU:") else
                    "tool" if line.startswith(("FILLED:", "ATTACHED:", "SUBMITTED:", "SKIPPED:", "LEARNED:")) else "text")
            job.add(kind, line)
            if line.startswith("SUBMITTED:"):
                job.saved.append(line)
        job.proc.wait()
        if job.status == "running":
            job.status = "done" if job.proc.returncode == 0 else "failed"
        job.result = next((e["text"] for e in reversed(job.log) if e["kind"] == "error" and e["text"].startswith("FAILED")),
                          job.log[-1]["text"] if job.log else "")
    except Exception as exc:
        job.add("error", f"Could not start the autofill script: {exc}")
        job.status = "failed"
    finally:
        job.finished = time.time()


def start_autofill(rec_id):
    rec = tracker.get(rec_id)
    if not rec:
        raise ValueError("No such job in the tracker.")
    if not rec.get("url"):
        raise ValueError("This job has no link to open.")
    if not (TOOLS / "apply" / "node_modules").exists():
        raise ValueError("Autofill isn't set up yet: run `cd tools/apply && npm install` once.")
    if not os.environ.get("GEMINI_API_KEY"):
        raise ValueError("Set GEMINI_API_KEY in the terminal before starting the server "
                         "(export GEMINI_API_KEY=...), then restart it.")
    with JOBS_LOCK:
        if any(j.mode == "autofill" and j.status == "running" for j in JOBS.values()):
            raise RuntimeError("A prefill browser is already open. Close it first.")
        job = Job("autofill", f"{rec['company']} - {rec['role']}", record_id=rec_id)
        job.status, job.started = "running", time.time()
        JOBS[job.id] = job
    threading.Thread(target=run_autofill, args=(job,), daemon=True).start()
    return job


def pump():
    """Start queued runs, oldest first, while fewer than PARALLEL are running."""
    with JOBS_LOCK:
        running = sum(1 for j in JOBS.values() if j.status == "running" and j.mode != "autofill")
        for job in JOBS.values():
            if running >= PARALLEL:
                break
            if job.status == "queued":
                job.status, job.started = "running", time.time()
                running += 1
                threading.Thread(target=run_job, args=(job,), daemon=True).start()
        prune()
        # forget old finished runs
        finished = [j for j in JOBS.values() if j.status in ("done", "failed", "cancelled")]
        for j in finished[:-KEEP_FINISHED] if len(finished) > KEEP_FINISHED else []:
            JOBS.pop(j.id, None)


def prune():
    """Drop successful runs a little while after they finish (call with JOBS_LOCK held)."""
    cutoff = time.time() - CLEAR_DONE_AFTER
    for jid in [j.id for j in JOBS.values() if j.status == "done" and j.finished and j.finished < cutoff]:
        JOBS.pop(jid, None)


def enqueue(job, input_name=None, input_text=None):
    """Prepare the run's own scratch folder (with its input file, if any) and queue it."""
    job.work.mkdir(parents=True, exist_ok=True)
    if input_name:
        (job.work / input_name).write_text(input_text)
        job.arg = f".work/{job.id}/{input_name}"
    with JOBS_LOCK:
        JOBS[job.id] = job
    pump()
    return job


def require_claude():
    if not CLAUDE:
        raise ValueError("Claude Code (the `claude` command) was not found on PATH.")


def start_jobs(mode, text, fits=None):
    """One run per pasted link when the input is a list of links; otherwise one run for the text."""
    require_claude()
    text = text.strip()
    if not text:
        raise ValueError("Paste a job link or the job description first.")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if all(re.fullmatch(r"https?://\S+", l) for l in lines):
        jobs = []
        for url in dict.fromkeys(lines):  # skip duplicates, keep order
            job = Job(mode, url)
            fit = (fits or {}).get(url)
            job.arg = f"{url} --fit {int(fit)}" if isinstance(fit, (int, float)) else url
            jobs.append(enqueue(job))
        return jobs
    if mode == "track" and len(lines) == 1 and len(text) < 120:
        job = Job(mode, text)
        job.arg = json.dumps(text)  # a short "Company - Role" string
        return [enqueue(job)]
    return [enqueue(Job(mode, text), "input.md", text)]


def start_combined(text, fits=None):
    """One resume for 2-5 jobs: links one per line, or pasted JDs separated by a line of ---."""
    require_claude()
    text = text.strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and all(re.fullmatch(r"https?://\S+", l) for l in lines):
        urls, texts = list(dict.fromkeys(lines)), []
    else:
        urls, texts = [], [t.strip() for t in re.split(r"(?m)^\s*-{3,}\s*$", text) if t.strip()]
    count = len(urls) or len(texts)
    if count < 2:
        raise ValueError("A combined resume needs 2 to 5 jobs: paste links one per line, "
                         "or full job descriptions separated by a line with ---.")
    if count > MAX_COMBINED:
        raise ValueError(f"A combined resume works best for at most {MAX_COMBINED} jobs; you gave {count}. "
                         "Split them into groups of similar roles.")
    names = [urlparse(u).netloc.replace("www.", "") for u in urls] or [t.splitlines()[0][:30] for t in texts]
    job = Job("tailor-combined", f"{count} jobs: " + ", ".join(names))
    job.work.mkdir(parents=True, exist_ok=True)
    items = [dict({"url": u}, **({"fit": int(fits[u])} if isinstance((fits or {}).get(u), (int, float)) else {}))
             for u in urls]
    for n, t in enumerate(texts, 1):
        (job.work / f"input-{n}.md").write_text(t)
        items.append({"file": f".work/{job.id}/input-{n}.md"})
    return enqueue(job, "inputs.json", json.dumps(items))


def start_match(url, all_levels=False, all_locations=False):
    require_claude()
    url = (url or "").strip()
    if not re.fullmatch(r"https?://\S+", url):
        raise ValueError("Paste the link to a company's careers page (it starts with https://).")
    job = Job("match", url)
    flags = (" --all-levels" if all_levels else "") + (" --all-locations" if all_locations else "")
    job.arg = f"{url}{flags}"
    return enqueue(job)


SCORE_BATCH = 20   # jobs per scoring run, so each run's job descriptions fit comfortably


def start_score():
    """Queue one run per batch of unscored jobs that have something to score from."""
    require_claude()
    todo = [r["id"] for r in tracker.load() if not r.get("fit") and (r.get("jd") or r.get("url"))]
    if not todo:
        raise ValueError("No unscored job has a saved description or link to score from.")
    with JOBS_LOCK:
        if any(j.mode == "score-jobs" and j.status in ("queued", "running") for j in JOBS.values()):
            raise ValueError("Scoring is already running.")
    batches = [todo[i:i + SCORE_BATCH] for i in range(0, len(todo), SCORE_BATCH)]
    jobs = []
    for n, ids in enumerate(batches, 1):
        job = Job("score-jobs", f"Batch {n} of {len(batches)}: jobs #{ids[0]}–#{ids[-1]}")
        job.arg = "--ids " + ",".join(map(str, ids))
        jobs.append(enqueue(job))
    return jobs


def start_answer(rec_id, questions=None, revise=None):
    require_claude()
    rec = tracker.get(rec_id)
    if not rec:
        raise ValueError("No such job in the tracker.")
    if revise:
        payload = {"record_id": rec_id, "revise": revise}
        q = next((x["q"] for x in rec.get("qa", []) if x["id"] == revise["id"]), "")
        preview = f"{rec['company']}, revising: {q}"
    else:
        questions = [q.strip() for q in (questions or []) if q and q.strip()]
        if not questions:
            raise ValueError("Type at least one question.")
        payload = {"record_id": rec_id, "questions": questions}
        preview = f"{rec['company']}: " + (questions[0] if len(questions) == 1 else f"{len(questions)} questions")
    job = Job("answer", preview, record_id=rec_id)
    return enqueue(job, "qa-input.json", json.dumps(payload, ensure_ascii=False))


def start_outreach(company, recipients="", apollo=False):
    """Write cold emails to the recipients given (one per line); without recipients, the skill sources them on Hunter."""
    require_claude()
    company = (company or "").strip()
    if not company or len(company) > 100:
        raise ValueError("Type the company name (or its domain, like stripe.com).")
    recipients = (recipients or "").strip()
    if recipients:
        found, problems = outreach.parse_recipients(recipients)
        if not found:
            raise ValueError("No email addresses found. Put one recipient per line, e.g. Jane Doe, Technical Recruiter <jane@acme.com>.")
        if len(found) > outreach.MAX_RECIPIENTS:
            raise ValueError(f"{len(found)} recipients; at most {outreach.MAX_RECIPIENTS} per company.")
    job = Job("outreach", f"{company}: {len(found)} recipient(s)" if recipients else company)
    job.work.mkdir(parents=True, exist_ok=True)
    job.arg = json.dumps(company)
    if recipients:
        (job.work / "recipients.txt").write_text(recipients)
        job.arg += f" --recipients .work/{job.id}/recipients.txt"
    elif apollo:
        job.arg += " --apollo"
    return enqueue(job)


def run_send(job, cid, eids):
    """Send a batch of reviewed emails one by one, with a pause between them."""
    try:
        for n, eid in enumerate(eids):
            if job.status != "running":
                break
            if n:
                time.sleep(random.uniform(*outreach.SEND_GAP))
            try:
                item = outreach.send_one(cid, eid)
                job.add("tool", f"Sent to {item['name']} <{item['email']}>")
                job.saved.append(f"Sent to {item['email']}")
            except Exception as exc:
                job.add("error", f"#{cid}.{eid}: {exc}")
        if job.status == "running":
            job.status = "done" if job.saved else "failed"
        job.result = f"Sent {len(job.saved)} of {len(eids)} emails."
    finally:
        job.finished = time.time()


def start_send(cid, eids):
    camp = next((c for c in outreach.load() if c["id"] == cid), None)
    if not camp:
        raise ValueError("No such outreach campaign.")
    todo = [e["id"] for e in camp["emails"] if e["id"] in set(eids) and e["status"] != "sent"]
    if not todo:
        raise ValueError("Nothing to send: pick emails that haven't been sent.")
    left = outreach.DAILY_CAP - outreach.sent_today()
    if len(todo) > left:
        raise ValueError(f"That would pass today's cap of {outreach.DAILY_CAP} ({max(left, 0)} left).")
    outreach.login_args()  # fail now, not mid-batch, when Gmail isn't set up
    with JOBS_LOCK:
        if any(j.mode == "send-emails" and j.status == "running" for j in JOBS.values()):
            raise RuntimeError("A send batch is already running.")
        job = Job("send-emails", f"{camp['company']}: {len(todo)} email(s)")
        job.status, job.started = "running", time.time()
        JOBS[job.id] = job
    threading.Thread(target=run_send, args=(job, cid, todo), daemon=True).start()
    return job


def cancel(job, reason="Cancelled."):
    with JOBS_LOCK:
        if job.status == "queued":
            job.status, job.finished = "cancelled", time.time()
            job.add("error", reason)
            shutil.rmtree(job.work, ignore_errors=True)
            return
        if job.status == "running" and job.mode == "send-emails":
            job.status = "cancelled"   # the batch stops before its next email
            job.add("error", reason)
            return
        if job.status != "running" or not job.proc:
            return
        job.status = "cancelled"
        job.add("error", reason)
    try:
        os.killpg(job.proc.pid, signal.SIGINT)
        time.sleep(5)
        if job.proc.poll() is None:
            os.killpg(job.proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, AttributeError):
        job.proc.terminate()


INBOX_TTL = 5 * 60          # seconds a Gmail check stays fresh for background requests
INBOX_MIN_AGE = 20          # a "refresh" request reuses a check younger than this (several tabs, quick reloads)
_inbox = {"at": 0, "data": None, "error": None}
_inbox_lock = threading.Lock()


def applied_daily(refresh=False):
    """Applications per day from Gmail confirmations, cached; None data with an error when Gmail isn't reachable.
    `pending` lists confirmations since the search start that aren't linked to a tracker job yet."""
    with _inbox_lock:
        age = time.time() - _inbox["at"]
        if age > INBOX_TTL or (refresh and age > INBOX_MIN_AGE):
            try:
                _inbox.update(data=inbox.daily(31), error=None)
            except Exception as exc:  # no app password, offline, IMAP error: the page falls back to the tracker
                _inbox.update(error=str(exc)[:200])
            _inbox["at"] = time.time()
        data = _inbox["data"]
    if not data:
        return {"data": None, "error": _inbox["error"], "pending": []}
    done = inbox.imported_ids(tracker.load())
    todo = [x["id"] for x in data["since_start"] if x["id"] not in done]
    view = {k: v for k, v in data.items() if k not in ("since_start", "rejections")}
    return {"data": view, "error": _inbox["error"], "pending": todo}


SYNC_BATCH = 100   # emails per import run


def start_sync():
    """Queue Gmail-import runs, one per batch of confirmations not yet in the tracker."""
    require_claude()
    with JOBS_LOCK:
        if any(j.mode == "sync-gmail" and j.status in ("queued", "running") for j in JOBS.values()):
            raise ValueError("A Gmail import is already running.")
    todo = applied_daily(refresh=True)["pending"]
    if not todo:
        raise ValueError("Nothing to import: every confirmation since the search start is already in the tracker.")
    batches = [todo[i:i + SYNC_BATCH] for i in range(0, len(todo), SYNC_BATCH)]
    return [enqueue(Job("sync-gmail", f"Batch {n} of {len(batches)}: {len(ids)} emails"), "ids.json", json.dumps(ids))
            for n, ids in enumerate(batches, 1)]


def jobs_view():
    with JOBS_LOCK:
        prune()
        return {"jobs": [j.view() for j in JOBS.values()], "parallel": PARALLEL}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "ResumeTracker/1.1"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # -- helpers
    def send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the page was reloaded or closed before the answer arrived

    def error(self, code, msg):
        self.send(code, {"error": msg})

    def host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("localhost", "127.0.0.1")

    def write_ok(self):
        """Block cross-site requests: our page always sends this header, other sites can't."""
        return self.host_ok() and self.headers.get("X-Tracker") == "1"

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes
    def do_GET(self):
        if not self.host_ok():
            return self.error(403, "forbidden")
        url = urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            return self.send(200, UI.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/applications":
            return self.send(200, {"applications": tracker.load(), "statuses": tracker.STATUSES,
                                   "claude": bool(CLAUDE), "base_pdf": tracker.BASE_PDF_NAME})
        if path == "/api/matches":
            return self.send(200, careers.load_matches())
        if path == "/api/jobs":
            return self.send(200, jobs_view())
        if path == "/api/applied-daily":
            fresh = parse_qs(url.query).get("refresh") == ["1"]
            return self.send(200, applied_daily(fresh))
        if path == "/api/outreach":
            synced, sync_error = [], ""
            if parse_qs(url.query).get("sync") == ["1"]:   # pull edits made in Gmail Drafts, drafts sent or deleted there
                try:
                    synced = outreach.sync_drafts()
                except Exception as exc:
                    sync_error = f"Couldn't sync with Gmail: {exc}"[:300]
            return self.send(200, {"campaigns": outreach.load(), "setup": outreach.status(), "claude": bool(CLAUDE),
                                   "synced": synced, "sync_error": sync_error})
        if path == "/api/export.csv":
            out = io.StringIO()
            cols = ["id", "date_added", "company", "role", "location", "status", "date_applied", "resume", "url"]
            w = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(tracker.load())
            return self.send(200, out.getvalue(), "text/csv; charset=utf-8",
                             {"Content-Disposition": 'attachment; filename="applications.csv"'})
        m = re.fullmatch(r"/resumes/([^/]+\.pdf)", path)
        if m:
            f = (tracker.RESUMES / m.group(1)).resolve()
            if f.parent != tracker.RESUMES.resolve() or not f.is_file():
                return self.error(404, "not found")
            return self.send(200, f.read_bytes(), "application/pdf",
                             {"Content-Disposition": f'inline; filename="{f.name}"'})
        return self.error(404, "not found")

    def do_POST(self):
        if not self.write_ok():
            return self.error(403, "forbidden")
        path = urlparse(self.path).path
        data = self.body()
        if path == "/api/run":
            mode = data.get("mode")
            if mode not in ("tailor", "track", "combined"):
                return self.error(400, "mode must be tailor, track or combined")
            fits = data.get("fits") or {}
            try:
                jobs = ([start_combined(data.get("input", ""), fits)] if mode == "combined"
                        else start_jobs(mode, data.get("input", ""), fits))
            except ValueError as exc:
                return self.error(400, str(exc))
            return self.send(200, {"jobs": [j.view() for j in jobs]})
        if path == "/api/match":
            try:
                job = start_match(data.get("url"), bool(data.get("all_levels")), bool(data.get("all_locations")))
            except ValueError as exc:
                return self.error(400, str(exc))
            return self.send(200, job.view())
        if path == "/api/sync-gmail":
            try:
                return self.send(200, {"jobs": [j.view() for j in start_sync()]})
            except ValueError as exc:
                return self.error(400, str(exc))
        if path == "/api/score-unscored":
            try:
                jobs = start_score()
            except ValueError as exc:
                return self.error(400, str(exc))
            return self.send(200, {"jobs": [j.view() for j in jobs]})
        if path == "/api/outreach":
            try:
                return self.send(200, start_outreach(data.get("company"), data.get("recipients") or "", bool(data.get("apollo"))).view())
            except ValueError as exc:
                return self.error(400, str(exc))
        od = re.fullmatch(r"/api/outreach/(\d+)/(\d+)/draft", path)
        if od:
            try:
                return self.send(200, outreach.gmail_draft(int(od.group(1)), int(od.group(2))))
            except Exception as exc:
                return self.error(400, f"Gmail draft failed: {exc}")
        osend = re.fullmatch(r"/api/outreach/(\d+)/send", path)
        if osend:
            try:
                ids = [int(x) for x in data.get("ids") or []]
                return self.send(200, start_send(int(osend.group(1)), ids).view())
            except RuntimeError as exc:
                return self.error(409, str(exc))
            except (ValueError, TypeError) as exc:
                return self.error(400, str(exc))
        m = re.fullmatch(r"/api/jobs/(\w+)/cancel", path)
        if m:
            job = JOBS.get(m.group(1))
            if job:
                threading.Thread(target=cancel, args=(job,), daemon=True).start()
            return self.send(200, {"ok": True})
        if path == "/api/jobs/clear":
            with JOBS_LOCK:
                for jid in [j.id for j in JOBS.values() if j.status in ("done", "failed", "cancelled")]:
                    JOBS.pop(jid, None)
            return self.send(200, {"ok": True})
        m = re.fullmatch(r"/api/applications/(\d+)/qa", path)
        a = re.fullmatch(r"/api/applications/(\d+)/autofill", path)
        if a:
            try:
                job = start_autofill(int(a.group(1)))
            except (ValueError, RuntimeError) as exc:
                return self.error(409 if isinstance(exc, RuntimeError) else 400, str(exc))
            return self.send(200, job.view())
        m2 = re.fullmatch(r"/api/applications/(\d+)/qa/(\d+)/revise", path)
        if m or m2:
            try:
                if m:
                    job = start_answer(int(m.group(1)), questions=data.get("questions"))
                else:
                    job = start_answer(int(m2.group(1)), revise={"id": int(m2.group(2)),
                                       "instruction": (data.get("instruction") or "").strip() or "Improve it"})
            except ValueError as exc:
                return self.error(400, str(exc))
            return self.send(200, job.view())
        if path == "/api/applications":
            try:
                rec, created, _ = tracker.add(data.get("company", ""), data.get("role", ""),
                                              data.get("location", ""), data.get("url", ""),
                                              data.get("status") or "Applied", "tracked",
                                              resume_base=True)
            except SystemExit as exc:
                return self.error(400, str(exc))
            return self.send(200, rec)
        return self.error(404, "not found")

    def do_PATCH(self):
        if not self.write_ok():
            return self.error(403, "forbidden")
        oe = re.fullmatch(r"/api/outreach/(\d+)/(\d+)", urlparse(self.path).path)
        if oe:
            data = self.body()
            try:
                return self.send(200, outreach.edit(int(oe.group(1)), int(oe.group(2)), data.get("subject"), data.get("body")))
            except ValueError as exc:
                return self.error(400, str(exc))
        q = re.fullmatch(r"/api/applications/(\d+)/qa/(\d+)", urlparse(self.path).path)
        if q:
            answer = (self.body().get("answer") or "").strip()
            if not answer:
                return self.error(400, "answer is empty")
            try:
                return self.send(200, tracker.qa_edit(int(q.group(1)), int(q.group(2)), answer)[0])
            except ValueError as exc:
                return self.error(404, str(exc))
        m = re.fullmatch(r"/api/applications/(\d+)", urlparse(self.path).path)
        if not m:
            return self.error(404, "not found")
        try:
            rec = tracker.update(int(m.group(1)), **self.body())
        except ValueError as exc:
            return self.error(400, str(exc))
        return self.send(200, rec) if rec else self.error(404, "no such record")

    def do_DELETE(self):
        if not self.write_ok():
            return self.error(403, "forbidden")
        mm = re.fullmatch(r"/api/matches/(\d+)", urlparse(self.path).path)
        if mm:
            return self.send(200, {"ok": careers.delete_search(int(mm.group(1)))})
        od = re.fullmatch(r"/api/outreach/(\d+)(?:/(\d+))?", urlparse(self.path).path)
        if od:
            try:
                outreach.delete(int(od.group(1)), int(od.group(2)) if od.group(2) else None)
            except ValueError as exc:
                return self.error(404, str(exc))
            return self.send(200, {"ok": True})
        q = re.fullmatch(r"/api/applications/(\d+)/qa/(\d+)", urlparse(self.path).path)
        if q:
            ok = tracker.qa_delete(int(q.group(1)), int(q.group(2)))
            return self.send(200, {"ok": True}) if ok else self.error(404, "no such question")
        m = re.fullmatch(r"/api/applications/(\d+)", urlparse(self.path).path)
        if not m:
            return self.error(404, "not found")
        return self.send(200, {"ok": True}) if tracker.delete(int(m.group(1))) else self.error(404, "no such record")


def main():
    global PORT, PARALLEL
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--parallel", type=int, default=PARALLEL, help="Claude Code runs at once (default 2)")
    args = ap.parse_args()
    PORT, PARALLEL = args.port, max(1, args.parallel)
    # leftovers from a previous server session; folders younger than a run's timeout may belong to a live run
    for d in WORK.glob("*") if WORK.exists() else []:
        if time.time() - d.stat().st_mtime > RUN_TIMEOUT:
            shutil.rmtree(d, ignore_errors=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Job tracker running at http://localhost:{PORT}  ({PARALLEL} runs at a time; Ctrl+C to stop)")
    if not CLAUDE:
        print("Note: `claude` not found on PATH; the tracker works, but Tailor/Track/Answer runs are disabled.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
