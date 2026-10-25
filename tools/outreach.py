#!/usr/bin/env python3
"""Cold email to recruiters: find them (Hunter.io), store Claude-written emails, push Gmail drafts, send.

Stored in outreach.json at the project root. Uses only the Python standard library.

Usage:
  python3 tools/outreach.py status                                  # what is set up (Gmail, Hunter, resume, today's sends)
  python3 tools/outreach.py find --company "Stripe" [--domain stripe.com] --work .work/<run> [--apollo]
        # Hunter domain search (HR department), ranks recruiters, writes the top 5 to WORK/contacts.json.
        # With --apollo, prints APOLLO_NEEDED instead of failing when Hunter is out of searches or finds nobody.
  python3 tools/outreach.py contacts --company "Stripe" --file WORK/recipients.txt --work WORK
        # recipients you give (one per line: "Name, Title <email>", "email, Name, Title" or just the email) -> WORK/contacts.json
  python3 tools/outreach.py import-contacts --work WORK --file WORK/apollo.json
        # merge Apollo-enriched recruiters ([{email, first_name, last_name, title, linkedin, email_status}]) into contacts.json
  python3 tools/outreach.py add --file WORK/emails.json [--clean-work WORK]
        # save the written emails as a campaign (status "draft"); every address must come from WORK/contacts.json
  python3 tools/outreach.py list
  python3 tools/outreach.py eml CID EID OUT.eml                 # write one email as a .eml file to preview it
  python3 tools/outreach.py draft CID EID                       # put one email in Gmail Drafts (IMAP)
  python3 tools/outreach.py send CID EID [EID ...]              # send now (SMTP); refuses repeats and the daily cap

emails.json: {"company", "domain", "angle", "emails": [{"email", "subject", "body"}]}. Bodies are plain text: paragraphs separated by blank lines, "- " for bullets, no greeting
signature: the signature (from base/profile.json) is appended when the message is built.

Secrets come from environment variables or the macOS Keychain:
  GMAIL_APP_PASSWORD  or  security add-generic-password -s resume-tailor-gmail -a "$USER" -w
  HUNTER_API_KEY      or  security add-generic-password -s resume-tailor-hunter -a "$USER" -w
"""
import argparse
import contextlib
import datetime as dt
import email
import html
import imaplib
import json
import os
import random
import re
import shutil
import smtplib
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, make_msgid, parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "outreach.json"
PROFILE = ROOT / "base" / "profile.json"
TRACKER = ROOT / "tracker.json"
WORK = ROOT / ".work"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import me  # noqa: E402

# the resume attached to every email: profile "coldEmailResume" (a path, relative to the project or absolute), else the base PDF
RESUME_PDF = ROOT / (me.profile().get("coldEmailResume") or f"resumes/{me.name_slug()}_Resume.pdf")
ATTACH_NAME = f"{me.name_slug()}_Resume.pdf"   # the name recruiters see
TOP_N = 5
DAILY_CAP = 25                                 # cold emails per day; keeps the Gmail account's reputation safe
MAX_WORDS = 150                                # body without signature
SEND_GAP = (20, 45)                            # seconds between sends in one batch
DRAFTS = '"[Gmail]/Drafts"'
TRASH = '"[Gmail]/Trash"'
ALL_MAIL = '"[Gmail]/All Mail"'
HUNTER = "https://api.hunter.io/v2/domain-search"
MAX_RECIPIENTS = 20                            # per campaign when you give the addresses yourself
APOLLO_MAX = 5                                 # Apollo credits one run may spend (fallback only)
STATUSES = ["draft", "gmail_draft", "sent", "failed"]
FOLLOWUP_AFTER_DAYS = 3                        # earliest follow-up after the last email to someone
MAX_FOLLOWUPS = 2                              # follow-ups per person; none once they reply


# ---------- storage ----------

@contextlib.contextmanager
def locked():
    with open(ROOT / ".outreach.lock", "w") as fh:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:
            pass
        yield


def load():
    if not DATA.exists():
        return []
    try:
        return json.loads(DATA.read_text() or "[]")
    except json.JSONDecodeError:
        sys.exit(f"outreach.json is not valid JSON; fix or restore it ({DATA})")


def save(campaigns):
    tmp = DATA.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(campaigns, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, DATA)


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def find_email(campaigns, cid, eid):
    camp = next((c for c in campaigns if c["id"] == cid), None)
    item = camp and next((e for e in camp["emails"] if e["id"] == eid), None)
    if not item:
        raise ValueError(f"No email #{cid}.{eid}")
    return camp, item


def contacted():
    """Addresses already emailed (sent) in any campaign: never email anyone twice."""
    return {e["email"].lower() for c in load() for e in c["emails"] if e["status"] == "sent"}


def sent_today():
    """Emails and follow-ups sent today (they share the daily cap)."""
    day = dt.date.today().isoformat()
    return sum(1 for c in load() for e in c["emails"] if e["status"] == "sent" and (e.get("sent_at") or "").startswith(day)) + \
        sum(1 for c in load() for e in c["emails"] for f in e.get("followups", [])
            if f["status"] == "sent" and (f.get("sent_at") or "").startswith(day))


# ---------- secrets and setup ----------

def secret(env, service):
    if os.environ.get(env):
        return os.environ[env].strip()
    if shutil.which("security"):
        out = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return None


def gmail_password():
    return (secret("GMAIL_APP_PASSWORD", "resume-tailor-gmail") or "").replace(" ", "") or None


def hunter_key():
    return secret("HUNTER_API_KEY", "resume-tailor-hunter")


def profile():
    return json.loads(PROFILE.read_text())


def status():
    return {"gmail": bool(gmail_password()), "hunter": bool(hunter_key()), "resume": RESUME_PDF.exists(),
            "resume_path": str(RESUME_PDF), "attach_name": ATTACH_NAME, "sender": profile().get("email"), "sent_today": sent_today(),
            "daily_cap": DAILY_CAP}


# ---------- finding recruiters ----------

def hunter_left():
    """Domain searches left this month (the account call itself is free); None if unknown."""
    try:
        url = "https://api.hunter.io/v2/account?" + urllib.parse.urlencode({"api_key": hunter_key()})
        with urllib.request.urlopen(url, timeout=20) as res:
            return ((json.load(res).get("data") or {}).get("requests") or {}).get("searches", {}).get("remaining")
    except Exception:
        return None


class HunterOut(Exception):
    pass


def hunter_search(company, domain=None):
    key = hunter_key()
    if not key:
        sys.exit("No Hunter API key. Run: security add-generic-password -s resume-tailor-hunter -a \"$USER\" -w")
    params = {"department": "hr", "type": "personal", "limit": 10, "api_key": key}
    params["domain" if domain else "company"] = domain or company
    try:
        with urllib.request.urlopen(f"{HUNTER}?{urllib.parse.urlencode(params)}", timeout=30) as res:
            return json.load(res).get("data") or {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        if exc.code in (402, 429) or re.search(r"usage|credit|limit", detail, re.I):
            raise HunterOut(detail)
        sys.exit(f"Hunter search failed ({exc.code}): {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"Could not reach Hunter: {exc.reason}")


TECH = re.compile(r"technical|engineering|software|\btech\b|r&d|product|developer")
NON_TECH = re.compile(r"executive|sales|\bgtm\b|go-to-market|finance|legal|marketing|g&a|corporate|retail store|hourly")
# only hands-on recruiters (recruiter, senior/technical recruiter, TA partner, sourcer); leaders and ops never reply to cold email
NOT_IC = re.compile(r"director|\bhead\b|\bvp\b|vice president|chief|manager|\bmgr\b|operations|\bops\b|coordinator|"
                    r"business partner|people partner|hrbp|enablement|program|analyst|systems|brand|marketing|scheduler")


def rank(c):
    """Score a Hunter contact as a target for a software engineer; None means skip."""
    title = (c.get("position") or "").lower()
    ver = ((c.get("verification") or {}).get("status") or "unknown").lower()
    if ver == "invalid" or not c.get("value"):
        return None
    if not re.search(r"recruit|talent acquisition|sourc", title) or NOT_IC.search(title):
        return None            # no title, not a recruiter, or a leader / ops role
    if NON_TECH.search(title) and not TECH.search(title):
        return None            # sales, executive, finance... recruiters don't hire engineers
    s = 40 if TECH.search(title) else 0
    s += 30 if "recruit" in title else 25 if "talent" in title else 15
    s += 5 if re.search(r"\bsenior\b|\bsr\b|\blead\b|principal|staff", title) else 0
    s += 10 if re.search(r"university|campus|early.career|new grad|emerging", title) else 0
    s += {"valid": 15, "accept_all": 0}.get(ver, -5)
    return s + (c.get("confidence") or 0) / 10


def find(company, domain, work):
    hunter = "ok"
    if hunter_left() == 0:
        data, hunter = {}, "out_of_searches"
    else:
        try:
            data = hunter_search(company, domain)
        except HunterOut:
            data, hunter = {}, "out_of_searches"
    seen = contacted()
    ranked = []
    for c in data.get("emails") or []:
        score = rank(c)
        if score is None or c["value"].lower() in seen:
            continue
        ranked.append({"email": c["value"], "first_name": c.get("first_name") or "", "last_name": c.get("last_name") or "",
                       "title": c.get("position") or "", "linkedin": c.get("linkedin") or "",
                       "verification": (c.get("verification") or {}).get("status") or "unknown",
                       "confidence": c.get("confidence"), "score": round(score, 1)})
    ranked.sort(key=lambda x: -x["score"])
    for c in ranked:
        c["source"] = "hunter"
    out = {"company": data.get("organization") or company, "domain": data.get("domain") or domain or "",
           "contacts": ranked[:TOP_N], "found": len(data.get("emails") or []), "already_contacted": len(seen),
           "hunter": hunter}
    Path(work).mkdir(parents=True, exist_ok=True)
    Path(work, "contacts.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return out


EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
TITLE_WORDS = re.compile(r"recruit|talent|sourc|\bhr\b|people|hiring|manager|director|engineer|partner|lead|head|coordinator", re.I)


def parse_recipients(text):
    """One recipient per line: an email plus, optionally, a name and a title in any order around it.
    Returns (contacts, problems)."""
    out, problems, seen = [], [], set()
    for line in text.splitlines():
        line = re.sub(r"https?://\S+", " ", line).strip()
        if not line:
            continue
        m = EMAIL.search(line)
        if not m:
            problems.append(f"no email address in: {line[:60]}")
            continue
        addr = m.group(0)
        if addr.lower() in seen:
            continue
        seen.add(addr.lower())
        rest = re.sub(r"[<>()\[\]\"]", " ", line[:m.start()] + " , " + line[m.end():])
        parts = [x.strip(" -–|:;.") for x in re.split(r"[,\t|;]| - | – ", rest)]
        parts = [x for x in parts if x and not x.lower().startswith("mailto")]
        name = next((x for x in parts if not TITLE_WORDS.search(x)), "")
        title = ", ".join(x for x in parts if x != name)
        if not name:   # "jane.doe@" or "jane_doe@" -> Jane Doe; anything else stays nameless rather than guessed
            local = addr.split("@")[0]
            if re.fullmatch(r"[A-Za-z]{2,}[._][A-Za-z]{2,}", local):
                name = " ".join(w.capitalize() for w in re.split(r"[._]", local))
        first, _, last = name.partition(" ")
        out.append({"email": addr, "first_name": first, "last_name": last, "title": title, "linkedin": "",
                    "verification": "provided", "confidence": None, "score": 0, "source": "manual"})
    return out, problems


def manual_contacts(company, file, work):
    """Recipients given by the user, written to WORK/contacts.json in the same shape `find` writes."""
    contacts, problems = parse_recipients(Path(file).read_text())
    seen = contacted()
    already = [c["email"] for c in contacts if c["email"].lower() in seen]
    contacts = [c for c in contacts if c["email"].lower() not in seen]
    if len(contacts) > MAX_RECIPIENTS:
        sys.exit(f"FAILED: {len(contacts)} recipients; at most {MAX_RECIPIENTS} per company.")
    domains = [c["email"].split("@")[1].lower() for c in contacts]
    out = {"company": company, "domain": max(set(domains), key=domains.count) if domains else "",
           "contacts": contacts, "found": len(contacts), "already_contacted": len(already), "hunter": "not used"}
    Path(work).mkdir(parents=True, exist_ok=True)
    Path(work, "contacts.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return out, problems, already


APOLLO_STATUS = {"verified": "valid", "likely to engage": "accept_all", "extrapolated": "unknown"}


def import_contacts(work, file):
    """Merge Apollo-enriched recruiters into WORK/contacts.json, ranked like Hunter's, top TOP_N kept."""
    cfile = Path(work, "contacts.json")
    out = json.loads(cfile.read_text())
    have = {c["email"].lower() for c in out["contacts"]}
    seen = contacted()
    domain = (out.get("domain") or "").lower().removeprefix("www.")
    people = json.loads(Path(file).read_text())
    if len(people) > APOLLO_MAX:
        sys.exit(f"At most {APOLLO_MAX} Apollo contacts per run.")
    added, skipped = [], []
    for p in people:
        addr = (p.get("email") or "").strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr):
            skipped.append(f"{p.get('first_name', '?')}: no work email")
            continue
        if domain and not addr.lower().endswith("@" + domain) and not addr.lower().endswith("." + domain):
            skipped.append(f"{addr}: not an @{domain} address")
            continue
        if addr.lower() in have or addr.lower() in seen:
            skipped.append(f"{addr}: already a contact or already emailed")
            continue
        ver = APOLLO_STATUS.get((p.get("email_status") or "").lower(), "unknown")
        score = rank({"value": addr, "position": p.get("title"), "verification": {"status": ver}, "confidence": 80 if ver == "valid" else 50})
        if score is None:
            skipped.append(f"{addr}: not a recruiter or invalid")
            continue
        added.append({"email": addr, "first_name": p.get("first_name") or "", "last_name": p.get("last_name") or "",
                      "title": p.get("title") or "", "linkedin": p.get("linkedin") or "", "verification": ver,
                      "confidence": None, "score": round(score, 1), "source": "apollo"})
    out["contacts"] = sorted(out["contacts"] + added, key=lambda x: -x["score"])[:TOP_N]
    cfile.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return out, added, skipped


def tracked_jobs(company):
    """Jobs at this company in the tracker (best hook for the email: roles already applied to)."""
    if not TRACKER.exists():
        return []
    key = re.sub(r"[^a-z0-9]", "", company.lower().split(" ")[0])
    return [r for r in json.loads(TRACKER.read_text())
            if key and re.sub(r"[^a-z0-9]", "", (r.get("company") or "").lower()).startswith(key)]


# ---------- campaigns ----------

def words(text):
    return len(re.findall(r"\S+", text))


def check_email(item):
    errs = []
    if not (item.get("subject") or "").strip() or len(item["subject"]) > 80:
        errs.append("subject must be 1-80 characters")
    employer = (profile().get("currentCompany") or "").strip()
    if employer and employer.lower() in (item.get("subject") or "").lower():
        errs.append(f"keep {employer} out of the subject; keep it simple")
    body = (item.get("body") or "").strip()
    if not body:
        errs.append("body is empty")
    elif words(body) > MAX_WORDS:
        errs.append(f"body is {words(body)} words; keep it under {MAX_WORDS}")
    if re.search(r"\[[^\]]+\]|\{[^}]+\}|<[^>]+>", body + item.get("subject", "")):
        errs.append("leftover placeholder like [..], {..} or <..>")
    first = re.escape((profile().get("firstName") or "").lower()) or r"\w+"
    if re.search(rf"(?im)^(best|thanks|thank you|regards|cheers|sincerely)[,!]?\s*\n+\s*{first}", body):
        errs.append("leave out the sign-off and name; the signature is added automatically")
    return errs


def add(file, clean_work=None):
    spec = json.loads(Path(file).read_text())
    cfile = Path(file).parent / "contacts.json"
    if not cfile.exists():
        sys.exit(f"Expected {cfile} from `find` next to the emails file.")
    contacts = {c["email"].lower(): c for c in json.loads(cfile.read_text())["contacts"]}
    seen = contacted()
    emails, problems = [], []
    for n, it in enumerate(spec.get("emails") or [], 1):
        addr = (it.get("email") or "").strip()
        c = contacts.get(addr.lower())
        if not c:
            problems.append(f"{addr or f'email {n}'}: not one of the contacts found by `find`")
            continue
        if addr.lower() in seen:
            problems.append(f"{addr}: already emailed in an earlier campaign")
            continue
        problems += [f"{addr}: {e}" for e in check_email(it)]
        emails.append({"id": n, "email": addr, "name": f"{c['first_name']} {c['last_name']}".strip(), "title": c["title"],
                       "verification": c.get("verification"), "linkedin": c.get("linkedin", ""),
                       "subject": it["subject"].strip(), "body": it["body"].strip(), "status": "draft",
                       "created": now(), "sent_at": "", "error": ""})
    if problems:
        sys.exit("Refusing to save; fix these and run again:\n  " + "\n  ".join(problems))
    if not emails:
        sys.exit("No emails to save.")
    with locked():
        campaigns = load()
        camp = {"id": max((c["id"] for c in campaigns), default=0) + 1, "company": spec.get("company", "").strip(),
                "domain": spec.get("domain", ""), "angle": spec.get("angle", ""), "date": now(),
                "emails": emails}
        campaigns.append(camp)
        save(campaigns)
    if clean_work:
        d = Path(clean_work).resolve()
        if WORK.resolve() in d.parents:
            shutil.rmtree(d, ignore_errors=True)
    return camp


def edit(cid, eid, subject=None, body=None):
    with locked():
        campaigns = load()
        _, item = find_email(campaigns, cid, eid)
        if item["status"] == "sent":
            raise ValueError("Already sent; it can't be edited.")
        new = dict(item, subject=subject if subject is not None else item["subject"],
                   body=body if body is not None else item["body"])
        errs = check_email(new)
        if errs:
            raise ValueError("; ".join(errs))
        item.update(subject=new["subject"].strip(), body=new["body"].strip(), updated=now())
        save(campaigns)
    if item["status"] == "gmail_draft":   # keep the Gmail draft in step: replace it with the edited version
        try:
            return gmail_draft(cid, eid)
        except Exception as exc:
            return set_status(cid, eid, error=f"Saved here, but the Gmail draft wasn't updated: {exc}"[:300])
    return item


def delete(cid, eid=None):
    with locked():
        campaigns = load()
        if eid is None:
            keep = [c for c in campaigns if c["id"] != cid]
        else:
            camp, _ = find_email(campaigns, cid, eid)
            camp["emails"] = [e for e in camp["emails"] if e["id"] != eid]
            keep = [c for c in campaigns if c["emails"]]
        save(keep)


def set_status(cid, eid, **fields):
    with locked():
        campaigns = load()
        _, item = find_email(campaigns, cid, eid)
        item.update(fields)
        save(campaigns)
        return item


# ---------- building the message ----------

def signature():
    p = profile()
    name = f"{p.get('preferredName') or p['firstName']} {p['lastName']}"
    links = [(p.get("linkedin") or ""), (p.get("github") or ""), (p.get("portfolio") or "")]
    return name, p.get("phone") or "", [l for l in links if l]


def short(url):
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/")


BULLET = re.compile(r"^\s*[-•*]\s+")


def tight(body):
    """The body without the blank line before a bullet list: the list follows its lead-in line directly."""
    return re.sub(r"\n\s*\n(?=\s*[-•*]\s)", "\n", body.strip())


def to_html(body):
    """Paragraphs as <div>s with a blank line between them; runs of "- " lines as one list, right under its lead-in."""
    out = []
    for block in re.split(r"\n\s*\n", body.strip()):
        runs = []   # consecutive bullet lines or text lines within the block
        for line in block.splitlines():
            kind = bool(BULLET.match(line))
            if runs and runs[-1][0] == kind:
                runs[-1][1].append(line)
            else:
                runs.append((kind, [line]))
        for kind, lines in runs:
            if kind:
                if out and out[-1] == "<div><br></div>":
                    out.pop()   # no blank line between the lead-in paragraph and its bullets
                out.append('<ul style="margin-top:0;margin-bottom:0">'
                           + "".join(f"<li>{html.escape(BULLET.sub('', l))}</li>" for l in lines) + "</ul>")
            else:
                out.append("<div>" + "<br>".join(html.escape(l) for l in lines) + "</div>")
        out.append("<div><br></div>")
    return "".join(out)


def sig_text():
    name, phone, links = signature()
    return f"Best,\n{name}\n" + "\n".join([phone] + [short(l) for l in links]) + "\n"


def sig_html():
    name, phone, links = signature()
    return (f"<div>Best,<br>{html.escape(name)}<br>{html.escape(phone)}<br>"
            + "<br>".join(f'<a href="{html.escape(l)}">{html.escape(short(l))}</a>' for l in links) + "</div>")


def build(camp, item, to=None):
    p = profile()
    name = signature()[0]
    text = f"{tight(item['body'])}\n\n{sig_text()}"
    sig_html_ = sig_html()
    msg = EmailMessage()
    msg["From"] = formataddr((name, p["email"]))
    msg["To"] = formataddr((item.get("name") or "", to or item["email"]))
    msg["Subject"] = item["subject"]
    msg["Message-ID"] = make_msgid(domain=p["email"].split("@")[-1])
    msg.set_content(text)
    msg.add_alternative(f'<div dir="ltr">{to_html(item["body"])}{sig_html_}</div>', subtype="html")
    if not RESUME_PDF.exists():
        raise ValueError(f"Resume not found: {RESUME_PDF}")
    msg.add_attachment(RESUME_PDF.read_bytes(), maintype="application", subtype="pdf", filename=ATTACH_NAME)
    return msg


def login_args():
    pw = gmail_password()
    if not pw:
        raise ValueError("No Gmail app password. Run: security add-generic-password -s resume-tailor-gmail -a \"$USER\" -w")
    return profile()["email"], pw


# ---------- Gmail drafts: once an email is in Gmail Drafts, the Gmail draft is the version that counts ----------

def imap_login():
    user, pw = login_args()
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(user, pw)
    return imap


def find_draft(imap, item):
    """UID (in Drafts) of this email's Gmail draft, or None. Matched by Gmail thread id, then by recipient address
    (Gmail may save an edited draft as a new message); the newest match wins."""
    imap.select(DRAFTS)
    uids = []
    if item.get("gmail_thread"):
        typ, data = imap.uid("SEARCH", "X-GM-THRID", item["gmail_thread"])
        uids = data[0].split() if typ == "OK" else []
    if not uids:
        typ, data = imap.uid("SEARCH", None, "TO", f'"{item["email"]}"')
        uids = data[0].split() if typ == "OK" else []
    return max(uids, key=int) if uids else None


def fetch(imap, uid):
    typ, res = imap.uid("FETCH", uid, "(X-GM-THRID BODY.PEEK[])")
    part = next(r for r in res if isinstance(r, tuple))
    thread = re.search(rb"X-GM-THRID (\d+)", part[0])
    return email.message_from_bytes(part[1], policy=policy.default), thread.group(1).decode() if thread else ""


def trash(imap, uid):
    """Remove a draft from Drafts (it goes to Trash, recoverable for 30 days)."""
    imap.uid("COPY", uid, TRASH)
    imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
    imap.expunge()


def app_copy(msg):
    """(subject, body) of a Gmail draft as the app shows them: plain text without the signature block."""
    part = msg.get_body(preferencelist=("plain", "html"))
    text = part.get_content() if part else ""
    if part is not None and part.get_content_type() == "text/html":
        text = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"(?i)<br\s*/?>", "\n", text)))
    name = re.escape(signature()[0])
    text = re.split(rf"\n\s*(best|thanks|regards|cheers|sincerely),?\s*\n\s*{name}\b", text, flags=re.I)[0]
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()
    return str(msg["Subject"] or ""), text


def gmail_draft(cid, eid):
    """Put this email in Gmail Drafts, replacing its earlier Gmail draft if there is one."""
    camp, item = find_email(load(), cid, eid)
    if item["status"] == "sent":
        raise ValueError("Already sent.")
    msg = build(camp, item)
    imap = imap_login()
    try:
        old = find_draft(imap, item) if item["status"] == "gmail_draft" else None
        typ, data = imap.append(DRAFTS, r"(\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            raise RuntimeError(f"Gmail refused the draft: {data}")
        if old:
            trash(imap, old)
        imap.select(DRAFTS)
        uid = re.search(rb"APPENDUID \d+ (\d+)", data[0] or b"")
        thread = fetch(imap, uid.group(1).decode())[1] if uid else ""
    finally:
        imap.logout()
    return set_status(cid, eid, status="gmail_draft", drafted_at=now(), error="", gmail_thread=thread)


def sent_from_gmail(imap, item):
    """Date of a message to this recipient in Sent since the draft was made (sent from Gmail itself), or None."""
    imap.select(ALL_MAIL, readonly=True)
    since = (item.get("drafted_at") or now())[:10].replace("-", "/")
    q = f'in:sent to:{item["email"]} after:{since}'
    typ, data = imap.uid("SEARCH", "X-GM-RAW", '"' + q + '"')
    uids = data[0].split() if typ == "OK" else []
    if not uids:
        return None
    typ, res = imap.uid("FETCH", max(uids, key=int), "(BODY.PEEK[HEADER.FIELDS (DATE)])")
    part = next(r for r in res if isinstance(r, tuple))
    try:
        return parsedate_to_datetime(email.message_from_bytes(part[1])["Date"]).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return now()


def addrs(msg, *fields):
    return [a.lower() for _, a in getaddresses(sum((msg.get_all(f, []) for f in fields), [])) if a]


def when(msg):
    try:
        return parsedate_to_datetime(msg["Date"]).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return now()


def search_headers(imap, query, fields):
    """[(headers message, gmail thread id)] for All Mail messages matching a Gmail search, oldest first."""
    imap.select(ALL_MAIL, readonly=True)
    typ, data = imap.uid("SEARCH", "X-GM-RAW", '"' + query.replace('"', '\\"') + '"')
    uids = data[0].split() if typ == "OK" else []
    out = []
    for i in range(0, len(uids), 200):
        typ, res = imap.uid("FETCH", b",".join(uids[i:i + 200]), f"(X-GM-THRID BODY.PEEK[HEADER.FIELDS ({fields})])")
        for part in res:
            if isinstance(part, tuple):
                thread = re.search(rb"X-GM-THRID (\d+)", part[0])
                out.append((email.message_from_bytes(part[1]), thread.group(1).decode() if thread else ""))
    return out


def sent_index(imap, since):
    """Sent mail since `since` (YYYY-MM-DD) by recipient: {address: [{date, msgid, thread, subject}]}, oldest first."""
    index = {}
    for msg, thread in search_headers(imap, f"in:sent after:{since.replace('-', '/')}", "TO CC DATE SUBJECT MESSAGE-ID"):
        info = {"date": when(msg), "msgid": (msg["Message-ID"] or "").strip(), "thread": thread,
                "subject": re.sub(r"\s+", " ", str(msg["Subject"] or "")).strip()}
        for a in addrs(msg, "To", "Cc"):
            index.setdefault(a, []).append(info)
    for v in index.values():
        v.sort(key=lambda x: x["date"])
    return index


def reply_index(imap, people, since):
    """Earliest message from each of these addresses since `since`: {address: date}."""
    found = {}
    people = sorted(people)
    for i in range(0, len(people), 30):
        q = f"-in:sent -in:drafts after:{since.replace('-', '/')} {{{' '.join('from:' + a for a in people[i:i + 30])}}}"
        for msg, _ in search_headers(imap, q, "FROM DATE"):
            for a in addrs(msg, "From"):
                if a in people and (a not in found or when(msg) < found[a]):
                    found[a] = when(msg)
    return found


def is_reply(subject):
    return bool(re.match(r"\s*(re|fwd?)\s*:", subject, re.I))


def sync_gmail():
    """Bring every email in line with Gmail, in one pass: edits made to a Gmail draft show up here, a draft deleted
    there falls back to the app's copy, and anything sent from Gmail (by hand, by a script, or from the app) is
    marked sent with its thread, so follow-ups can reply in it. Replies from recipients and sent follow-up drafts
    are noted too. Returns what changed."""
    campaigns = load()
    items = [(c, e) for c in campaigns for e in c["emails"]]
    if not items:
        return []
    since = min(c["date"][:10] for c, _ in items)
    changes, updates = [], []   # updates: (cid, eid, fields) applied under the lock at the end
    imap = imap_login()
    try:
        sent = sent_index(imap, since)
        waiting = {e["email"].lower() for _, e in items if e["status"] == "sent" and not e.get("replied_at")}
        replies = reply_index(imap, waiting, since) if waiting else {}
        for c, e in items:
            addr, fields = e["email"].lower(), {}
            firsts = [x for x in sent.get(addr, []) if not is_reply(x["subject"])]
            if e["status"] == "gmail_draft":
                uid = find_draft(imap, e)
                if uid:
                    msg, thread = fetch(imap, uid)
                    subject, body = app_copy(msg)
                    if subject != e["subject"] or tight(body) != tight(e["body"]):   # spacing before bullets isn't an edit
                        fields.update(subject=subject or e["subject"], body=body or e["body"], synced_at=now(), error="")
                        changes.append(f"{e['email']}: updated from Gmail")
                    if thread != e.get("gmail_thread"):
                        fields["gmail_thread"] = thread
                    updates.append((c["id"], e["id"], fields))
                    continue
                after = [x for x in firsts if x["date"][:10] >= (e.get("drafted_at") or c["date"])[:10]]
                if after:
                    x = after[0]
                    fields.update(status="sent", sent_at=x["date"], sent_via="gmail", error="",
                                  sent_msgid=x["msgid"], sent_thread=x["thread"], sent_subject=x["subject"])
                    changes.append(f"{e['email']}: sent from Gmail")
                else:
                    fields.update(status="draft", gmail_thread="",
                                  error="The Gmail draft was deleted; this is the app's copy. Save to Gmail Drafts to recreate it.")
                    changes.append(f"{e['email']}: Gmail draft deleted")
            elif e["status"] in ("draft", "failed"):
                after = [x for x in firsts if x["date"][:10] >= c["date"][:10]]
                if after:   # written in the app, sent by hand from Gmail
                    x = after[0]
                    fields.update(status="sent", sent_at=x["date"], sent_via="gmail (outside the app)", error="",
                                  sent_msgid=x["msgid"], sent_thread=x["thread"], sent_subject=x["subject"])
                    changes.append(f"{e['email']}: sent from Gmail")
            elif e["status"] == "sent":
                if not e.get("sent_msgid"):
                    after = [x for x in firsts if x["date"][:10] >= (e.get("sent_at") or c["date"])[:10]] or firsts
                    if after:
                        x = after[0]
                        fields.update(sent_msgid=x["msgid"], sent_thread=x["thread"], sent_subject=x["subject"])
                if addr in replies and replies[addr] >= (e.get("sent_at") or ""):
                    fields["replied_at"] = replies[addr]
                    changes.append(f"{e['email']}: replied")
                followups = [dict(f) for f in e.get("followups", [])]
                for f in followups:
                    if f["status"] == "gmail_draft":
                        later = [x for x in sent.get(addr, []) if is_reply(x["subject"]) and x["date"] >= f["created"]]
                        if later:
                            f.update(status="sent", sent_at=later[0]["date"])
                            changes.append(f"{e['email']}: follow-up sent from Gmail")
                if followups != e.get("followups", []):
                    fields["followups"] = followups
            if fields:
                updates.append((c["id"], e["id"], fields))
    finally:
        imap.logout()
    if updates:
        with locked():
            campaigns = load()
            for cid, eid, fields in updates:
                if fields:
                    find_email(campaigns, cid, eid)[1].update(fields)
            save(campaigns)
    return changes


sync_drafts = sync_gmail   # older name


def send_one(cid, eid):
    """Send one email. If it's in Gmail Drafts, the Gmail draft itself is sent (with any edits made in Gmail) and
    removed from Drafts; otherwise the app's copy is built and sent."""
    camp, item = find_email(load(), cid, eid)
    if item["status"] == "sent" or item["email"].lower() in contacted():
        raise ValueError(f"{item['email']} was already emailed.")
    if sent_today() >= DAILY_CAP:
        raise ValueError(f"Daily cap reached ({DAILY_CAP} sent today). Send the rest tomorrow.")
    imap, uid = None, None
    if item["status"] == "gmail_draft":
        imap = imap_login()
        uid = find_draft(imap, item)
        if not uid:
            imap.logout()
            raise ValueError(f"The Gmail draft to {item['email']} is gone (deleted or already sent from Gmail). "
                             "Reopen the Cold email tab to sync, then try again.")
        msg = fetch(imap, uid)[0]
        rcpts = [a for _, a in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", [])) if a]
        if item["email"].lower() not in {a.lower() for a in rcpts}:
            imap.logout()
            raise ValueError(f"The Gmail draft's recipients no longer include {item['email']}; send it from Gmail instead.")
        del msg["Bcc"]
    else:
        msg, rcpts = build(camp, item), None
    user, pw = login_args()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=60) as smtp:
            smtp.login(user, pw)
            smtp.send_message(msg, to_addrs=rcpts)
    except Exception as exc:
        if imap:
            imap.logout()
        set_status(cid, eid, status="failed" if not imap else "gmail_draft", error=str(exc)[:300])
        raise
    fields = {"status": "sent", "sent_at": now(), "error": "", "message_id": msg["Message-ID"] or ""}
    if imap:   # the draft is sent: take it out of Drafts and keep what was actually sent
        try:
            trash(imap, uid)
        finally:
            imap.logout()
        subject, body = app_copy(msg)
        fields.update(subject=subject or item["subject"], body=body or item["body"], sent_via="gmail draft")
    return set_status(cid, eid, **fields)


# ---------- follow-ups: a reply in the original thread, not a new email ----------

def followup_state(item):
    """(can follow up now, why not)."""
    if item["status"] != "sent":
        return False, "not sent yet"
    if item.get("replied_at"):
        return False, "they replied"
    done = [f for f in item.get("followups", [])]
    if len(done) >= MAX_FOLLOWUPS:
        return False, f"{MAX_FOLLOWUPS} follow-ups already"
    last = max([item.get("sent_at") or ""] + [f.get("sent_at") or f["created"] for f in done])
    try:
        days = (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(last).astimezone()).days
    except ValueError:
        days = FOLLOWUP_AFTER_DAYS
    if days < FOLLOWUP_AFTER_DAYS:
        return False, f"wait {FOLLOWUP_AFTER_DAYS - days} more day(s)"
    if not item.get("sent_msgid"):
        return False, "original not found in Gmail yet (Sync with Gmail)"
    return True, ""


def followup_template(camp, item):
    first = (item.get("name") or "").split(" ")[0]
    n = len(item.get("followups", []))
    hi = f"Hi {first}," if first else "Hello,"
    if n == 0:
        return (f"{hi}\n\nJust following up on my note below about engineering roles at {camp['company']}. I'm still very "
                f"interested and would welcome a quick chat, or I'd be grateful if you could share my resume with the hiring "
                f"manager.\n\nThanks for your time.")
    return (f"{hi}\n\nI know inboxes get busy, so I'll keep this short: I'm still very interested in engineering roles at "
            f"{camp['company']} and would be glad to connect whenever it suits you.\n\nThanks again.")


def build_followup(camp, item, body):
    p = profile()
    name = signature()[0]
    first = item.get("sent_subject") or item["subject"]
    subject = first if is_reply(first) else f"Re: {first}"
    try:
        on = dt.datetime.fromisoformat(item["sent_at"]).strftime("%a, %b %-d, %Y")
    except (ValueError, KeyError):
        on = "earlier"
    quote = f"{tight(item['body'])}\n\n{sig_text()}"
    text = (f"{tight(body)}\n\n{sig_text()}\nOn {on}, {name} <{p['email']}> wrote:\n"
            + "\n".join("> " + l for l in quote.splitlines()) + "\n")
    refs = " ".join([item["sent_msgid"]] + [f["message_id"] for f in item.get("followups", []) if f.get("message_id")])
    msg = EmailMessage()
    msg["From"] = formataddr((name, p["email"]))
    msg["To"] = formataddr((item.get("name") or "", item["email"]))
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=p["email"].split("@")[-1])
    msg["In-Reply-To"] = refs.split()[-1]
    msg["References"] = refs
    msg.set_content(text)
    msg.add_alternative(
        f'<div dir="ltr">{to_html(body)}{sig_html()}<br><div class="gmail_quote"><div dir="ltr">On {html.escape(on)}, '
        f'{html.escape(name)} &lt;{html.escape(p["email"])}&gt; wrote:<br></div>'
        f'<blockquote class="gmail_quote" style="margin:0 0 0 .8ex;border-left:1px #ccc solid;padding-left:1ex">'
        f'{to_html(item["body"])}{sig_html()}</blockquote></div></div>', subtype="html")
    return msg


def send_followup(cid, eid, body, draft=False):
    """Reply to the sent email in its thread: send now, or put the reply in Gmail Drafts."""
    camp, item = find_email(load(), cid, eid)
    ok, why = followup_state(item)
    if not ok:
        raise ValueError(f"Can't follow up with {item['email']}: {why}.")
    body = (body or "").strip()
    if not body or words(body) > MAX_WORDS:
        raise ValueError(f"Write the follow-up first (at most {MAX_WORDS} words).")
    msg = build_followup(camp, item, body)
    rec = {"created": now(), "body": body, "message_id": msg["Message-ID"]}
    if draft:
        imap = imap_login()
        try:
            typ, data = imap.append(DRAFTS, r"(\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            if typ != "OK":
                raise RuntimeError(f"Gmail refused the draft: {data}")
        finally:
            imap.logout()
        rec["status"] = "gmail_draft"
    else:
        if sent_today() >= DAILY_CAP:
            raise ValueError(f"Daily cap reached ({DAILY_CAP} sent today). Send it tomorrow, or save it to Gmail Drafts.")
        user, pw = login_args()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=60) as smtp:
            smtp.login(user, pw)
            smtp.send_message(msg)
        rec.update(status="sent", sent_at=now())
    with locked():
        campaigns = load()
        it = find_email(campaigns, cid, eid)[1]
        it.setdefault("followups", []).append(rec)
        save(campaigns)
        return it


# ---------- CLI ----------

def main():
    p = argparse.ArgumentParser(description="Cold email to recruiters")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    f = sub.add_parser("find")
    f.add_argument("--company", required=True)
    f.add_argument("--domain")
    f.add_argument("--work", required=True)
    f.add_argument("--apollo", action="store_true", help="Apollo fallback approved: don't fail when Hunter can't help")
    mc = sub.add_parser("contacts")
    mc.add_argument("--company", required=True)
    mc.add_argument("--file", required=True)
    mc.add_argument("--work", required=True)
    ic = sub.add_parser("import-contacts")
    ic.add_argument("--work", required=True)
    ic.add_argument("--file", required=True)
    a = sub.add_parser("add")
    a.add_argument("--file", required=True)
    a.add_argument("--clean-work", metavar="DIR")
    sub.add_parser("list")
    e = sub.add_parser("eml")
    e.add_argument("cid", type=int)
    e.add_argument("eid", type=int)
    e.add_argument("out")
    sub.add_parser("sync", help="bring emails in Gmail Drafts in line with Gmail")
    d = sub.add_parser("draft")
    d.add_argument("cid", type=int)
    d.add_argument("eid", type=int)
    s = sub.add_parser("send")
    s.add_argument("cid", type=int)
    s.add_argument("eids", type=int, nargs="+")
    args = p.parse_args()

    try:
        if args.cmd == "status":
            print(json.dumps(status(), indent=2))
        elif args.cmd == "find":
            company = args.company.strip()
            domain = args.domain or (company if re.fullmatch(r"[\w-]+(\.[\w-]+)+", company) else None)
            out = find(company, domain, args.work)
            print(f"{out['company']} ({out['domain'] or 'domain unknown'}): Hunter {out['hunter']}, {out['found']} HR contacts found, "
                  f"top {len(out['contacts'])} kept -> {args.work}/contacts.json")
            for c in out["contacts"]:
                print(f"  {c['score']:>5}  {c['first_name']} {c['last_name']}, {c['title'] or '(no title)'} <{c['email']}> [{c['verification']}]")
            jobs = tracked_jobs(out["company"])
            if jobs:
                print("Tracked jobs at this company (status, fit):")
                for r in jobs[:10]:
                    print(f"  #{r['id']} {r['role']} [{r['status']}] fit {(r.get('fit') or {}).get('score', '-')}")
            if not out["contacts"]:
                why = "Hunter is out of searches this month" if out["hunter"] != "ok" else "Hunter found no recruiters"
                if args.apollo:
                    print(f"APOLLO_NEEDED: {why}. Use Apollo for up to {APOLLO_MAX} contacts.")
                else:
                    sys.exit(f"FAILED: no recruiters found at {out['company']} ({why}"
                             + ("; the rest were already contacted" if out["found"] else "") + "). Tick 'Use Apollo' to try Apollo.")
        elif args.cmd == "contacts":
            out, problems, already = manual_contacts(args.company.strip(), args.file, args.work)
            print(f"{out['company']} ({out['domain']}): {len(out['contacts'])} recipient(s) -> {args.work}/contacts.json")
            for c in out["contacts"]:
                print(f"  {(c['first_name'] + ' ' + c['last_name']).strip() or '(no name)'}, {c['title'] or '(no title)'} <{c['email']}>")
            for a in already:
                print(f"  SKIPPED {a}: already emailed in an earlier campaign")
            for x in problems:
                print(f"  SKIPPED {x}")
            jobs = tracked_jobs(out["company"])
            if jobs:
                print("Tracked jobs at this company (status, fit):")
                for r in jobs[:10]:
                    print(f"  #{r['id']} {r['role']} [{r['status']}] fit {(r.get('fit') or {}).get('score', '-')}")
            if not out["contacts"]:
                sys.exit("FAILED: no new recipients to write to" + (" (all were already emailed)" if already else "") + ".")
        elif args.cmd == "import-contacts":
            out, added, skipped = import_contacts(args.work, args.file)
            print(f"Imported {len(added)} Apollo contact(s); {len(out['contacts'])} contacts in {args.work}/contacts.json")
            for x in skipped:
                print(f"  skipped {x}")
            if not out["contacts"]:
                sys.exit(f"FAILED: no usable recruiter emails at {out['company']} from Hunter or Apollo.")
        elif args.cmd == "add":
            camp = add(args.file, args.clean_work)
            print(f"Saved outreach #{camp['id']}: {camp['company']} ({len(camp['emails'])} emails)")
        elif args.cmd == "list":
            for c in load():
                print(f"#{c['id']} {c['date'][:10]} {c['company']}")
                for e in c["emails"]:
                    print(f"   .{e['id']} {e['status']:<12}{e['name']} <{e['email']}>  {e['subject']}")
        elif args.cmd == "eml":
            camp, item = find_email(load(), args.cid, args.eid)
            Path(args.out).write_bytes(bytes(build(camp, item)))
            print(f"Wrote {args.out}")
        elif args.cmd == "sync":
            for c in sync_gmail() or ["Nothing to sync."]:
                print(c)
        elif args.cmd == "draft":
            item = gmail_draft(args.cid, args.eid)
            print(f"Gmail draft created for {item['email']}")
        elif args.cmd == "send":
            for n, eid in enumerate(args.eids):
                if n:
                    time.sleep(random.uniform(*SEND_GAP))
                item = send_one(args.cid, eid)
                print(f"Sent to {item['email']}")
    except (ValueError, RuntimeError, OSError, smtplib.SMTPException, imaplib.IMAP4.error) as exc:
        sys.exit(f"FAILED: {exc}")


if __name__ == "__main__":
    main()
