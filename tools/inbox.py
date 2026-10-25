#!/usr/bin/env python3
"""Count job applications per day from application-confirmation emails in Gmail (read-only).

Usage:
  python3 tools/inbox.py                 # counts for the last 31 days
  python3 tools/inbox.py --days 14
  python3 tools/inbox.py --list 2026-10-17   # the confirmations counted on one day
  python3 tools/inbox.py --totals            # applications and rejections since SEARCH_START
  python3 tools/inbox.py pending --ids WORK/ids.json --out WORK/emails.json   # confirmations to import into the tracker
  python3 tools/inbox.py pending --limit 50 --out WORK/emails.json            # (same, the oldest 50 not yet imported)
  python3 tools/inbox.py statuses            # rejection / interview emails and the tracker jobs they'd update (no changes)

Uses the Gmail app password from tools/outreach.py (IMAP, read-only: nothing is marked read or moved).
Gmail's own search finds candidate emails (and drops ones whose text reads like a rejection); a subject check
then keeps only confirmations. Every confirmation counts once, so 3 applications to one company count as 3.
Each confirmation is identified by its Message-ID; tracker records list the ones linked to them in `gmail_ids`.
"""
import argparse
import datetime as dt
import email
import html
import imaplib
import json
import re
import sys
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import me  # noqa: E402
import outreach  # noqa: E402

ALL_MAIL = '"[Gmail]/All Mail"'
SEARCH_START = me.search_start()   # profile "searchStart": totals count from here
PHRASES = ['"thank you for applying"', '"thanks for applying"', '"thank you for your application"', '"received your application"',
           '"have your application"', '"application received"', '"application has been received"', '"received your"', '"application confirmation"', '"application submitted"',
           '"your application was sent"', '"your application for"', '"your application to"', '"your recent job application"',
           '"thank you for your interest in joining"']
# phrases only rejections use; loose words like "unfortunately" or "other candidates" also appear in confirmation boilerplate
REJECTION = ['"not to move forward"', '"not be moving forward"', '"not moving forward"', '"won\'t be moving forward"',
             '"decided to move forward with other"', '"decided to pursue other"', '"position has been filled"',
             '"no longer under consideration"', '"move forward with other candidates"', '"moving forward with other candidates"',
             '"pursue other candidates"', '"proceed with other candidates"', '"will not be moving forward"', '"not be proceeding"',
             '"after careful consideration"', '"not move forward"', '"unable to move forward"', '"decided not to proceed"',
             '"not proceed with your application"', '"wasn\'t selected"', '"was not selected"', '"have not been selected"',
             '"has not been selected"']
CONFIRM = re.compile(r"thank(s| you)\s+for\s+(applying|your application|your interest in joining)|received your( \S+)? application|"
                     r"have your application|application\s+(received|confirmation|submitted)|application (was )?sent|"
                     r"application has been received|"
                     r"^your (recent job )?application (for|to)\b|^(\w+, )?your application was sent|thank you for applying", re.I)
NOT_CONFIRM = re.compile(r"\bupdates?\b|application status|regarding|important information|next steps?\b|\binterview|"
                         r"\bassessment|unfortunately|\ba decision\b|decision on", re.I)


def query(since):
    return (f"after:{since:%Y/%m/%d} -in:sent -in:drafts -in:chats {{{' '.join(PHRASES)}}} "
            + " ".join(f"-{r}" for r in REJECTION))


def rejection_query(since):
    return f"after:{since:%Y/%m/%d} -in:sent -in:drafts -in:chats {{{' '.join(REJECTION)}}}"


def text(h):
    return re.sub(r"\s+", " ", str(make_header(decode_header(h or "")))).strip()


def search(m, q, since, keep=lambda subject: True):
    """[{id, uid, date, from, subject}] for messages matching Gmail query q since `since`, kept by subject (local dates)."""
    out = []
    typ, data = m.uid("SEARCH", "X-GM-RAW", '"' + q.replace('"', '\\"') + '"')
    uids = data[0].split() if typ == "OK" else []
    for i in range(0, len(uids), 200):
        typ, res = m.uid("FETCH", b",".join(uids[i:i + 200]), "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
        for part in res:
            if not isinstance(part, tuple):
                continue
            h = email.message_from_bytes(part[1])
            subject = text(h["Subject"])
            if not keep(subject):
                continue
            try:
                when = parsedate_to_datetime(h["Date"]).astimezone()
            except (TypeError, ValueError):
                internal = imaplib.Internaldate2tuple(part[0])
                when = dt.datetime(*internal[:6]) if internal else None
            if when and when.date() >= since:
                uid = re.search(rb"UID (\d+)", part[0])
                mid = (h["Message-ID"] or "").strip() or f"<{when.isoformat()}|{subject[:60]}>"
                out.append({"id": mid, "uid": uid.group(1).decode() if uid else "", "date": when.date().isoformat(),
                            "from": text(h["From"]), "subject": subject})
    return sorted(out, key=lambda x: x["date"])


def is_confirmation(subject):
    return bool(CONFIRM.search(subject)) and not NOT_CONFIRM.search(subject)


def daily(days=31):
    """Per-day confirmations for the last `days` days, plus application and rejection totals since SEARCH_START."""
    start = min(SEARCH_START, dt.date.today() - dt.timedelta(days=days - 1))
    user, pw = outreach.login_args()
    with imaplib.IMAP4_SSL("imap.gmail.com") as m:
        m.login(user, pw)
        m.select(ALL_MAIL, readonly=True)
        confirmed = search(m, query(start), start, is_confirmation)
        rejected = search(m, rejection_query(SEARCH_START), SEARCH_START)
    recent = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    items = [x for x in confirmed if x["date"] >= recent]
    counts = {}
    for x in items:
        counts[x["date"]] = counts.get(x["date"], 0) + 1
    since = SEARCH_START.isoformat()
    return {"days": counts, "items": items, "checked": dt.datetime.now().isoformat(timespec="seconds"),
            "total": {"since": since, "applied": sum(1 for x in confirmed if x["date"] >= since),
                      "rejected": len(rejected)}, "rejections": rejected,
            "since_start": [{"id": x["id"], "date": x["date"]} for x in confirmed if x["date"] >= since]}


# ---------- importing into the tracker ----------

GREETING = re.compile(r"\b(hi|hello|dear|hey)\b", re.I)


def body_text(msg):
    """Plain text of an email: the text/plain part, else the HTML part with tags stripped."""
    for want in ("text/plain", "text/html"):
        for part in msg.walk():
            if part.get_content_type() == want and not part.get_filename():
                t = part.get_payload(decode=True) or b""
                t = t.decode(part.get_content_charset() or "utf-8", "replace")
                if want == "text/html":
                    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t)
                    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
                return t
    return ""


def snippet(msg, n=700):
    t = re.sub(r"\(?\s*https?://\S+\s*\)?|\[https?://\S+\]", " ", body_text(msg))
    t = re.sub(r"\s+", " ", t).strip()
    g = GREETING.search(t[:1500])
    return t[g.start() if g else 0:][:n]


def tracker_records():
    path = Path(__file__).resolve().parent.parent / "tracker.json"
    return json.loads(path.read_text() or "[]") if path.exists() else []


def imported_ids(records=None):
    """Message-IDs already linked to tracker records."""
    return {i for r in (records if records is not None else tracker_records()) for i in r.get("gmail_ids", [])}


def company_key(name):
    words = [w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in {"the", "inc", "llc", "co", "corp"}]
    return words[0] if words and len(words[0]) >= 3 else ""


def candidates(x, records, n=8):
    """Tracker jobs whose company name appears in this email (to link instead of duplicating)."""
    hay = f"{x['from']} {x['subject']} {x.get('snippet', '')}".lower()
    hits = [r for r in records if company_key(r.get("company")) and re.search(rf"\b{company_key(r['company'])}", hay)]
    hits.sort(key=lambda r: abs((dt.date.fromisoformat(r.get("date_applied") or r["date_added"]) - dt.date.fromisoformat(x["date"])).days))
    return [{k: r.get(k, "") for k in ("id", "company", "role", "status", "date_applied")} for r in hits[:n]]


def pending(ids=None, limit=None):
    """Confirmations since SEARCH_START not yet in the tracker (or exactly `ids`), oldest first, with a body snippet
    and the tracker jobs at the same company."""
    user, pw = outreach.login_args()
    records = tracker_records()
    done = imported_ids(records)
    with imaplib.IMAP4_SSL("imap.gmail.com") as m:
        m.login(user, pw)
        m.select(ALL_MAIL, readonly=True)
        found = search(m, query(SEARCH_START), SEARCH_START, is_confirmation)
        todo = [x for x in found if (x["id"] in ids if ids is not None else x["id"] not in done)]
        todo = todo[:limit] if limit else todo
        by_uid = {x["uid"]: x for x in todo}
        uids = list(by_uid)
        for i in range(0, len(uids), 25):
            typ, res = m.uid("FETCH", ",".join(uids[i:i + 25]), "(BODY.PEEK[])")
            for part in res:
                uid = isinstance(part, tuple) and re.search(rb"UID (\d+)", part[0])
                if uid and uid.group(1).decode() in by_uid:
                    by_uid[uid.group(1).decode()]["snippet"] = snippet(email.message_from_bytes(part[1]))
    for x in todo:
        x.pop("uid")
        x.setdefault("snippet", "")
        x["candidates"] = candidates(x, records)
    return todo


def main():
    if sys.argv[1:2] == ["statuses"]:
        try:
            changes, unmatched = statuses()
        except (ValueError, OSError, imaplib.IMAP4.error) as exc:
            sys.exit(f"FAILED: {exc}")
        for rid, st, x in changes:
            print(f"#{rid} -> {st}: {x['date']} {x['subject'][:70]} ({x['how']})")
        for x, why in unmatched:
            print(f"   unmatched {x['status']}: {x['date']} {x['from'][:30]} | {x['subject'][:60]} ({why})")
        return
    if sys.argv[1:2] == ["pending"]:
        p = argparse.ArgumentParser(prog="inbox.py pending")
        p.add_argument("--ids", help="JSON list of Message-IDs to fetch (one import batch)")
        p.add_argument("--limit", type=int)
        p.add_argument("--out", required=True)
        a = p.parse_args(sys.argv[2:])
        try:
            items = pending(set(json.loads(Path(a.ids).read_text())) if a.ids else None, a.limit)
        except (ValueError, OSError, imaplib.IMAP4.error) as exc:
            sys.exit(f"FAILED: {exc}")
        Path(a.out).write_text(json.dumps(items, indent=1, ensure_ascii=False))
        print(f"{len(items)} confirmation email(s) to import -> {a.out}")
        return
    p = argparse.ArgumentParser(description="Applications per day from Gmail confirmations")
    p.add_argument("--days", type=int, default=31)
    p.add_argument("--list", metavar="YYYY-MM-DD")
    p.add_argument("--totals", action="store_true")
    args = p.parse_args()
    try:
        d = daily(max(args.days, 1))
    except (ValueError, OSError, imaplib.IMAP4.error) as exc:
        sys.exit(f"FAILED: {exc}")
    if args.totals:
        print(json.dumps(d["total"], indent=1))
        return
    if args.list:
        for x in d["items"]:
            if x["date"] == args.list:
                print(f"{x['from'][:40]:<40}  {x['subject'][:90]}")
        return
    print(json.dumps(d["days"], indent=1, sort_keys=True))



# ---------- job status from Gmail: rejections and interview invitations (no Claude, no tokens) ----------

INTERVIEW = ['"schedule an interview"', '"schedule your interview"', '"interview invitation"', '"invite you to interview"',
             '"invitation to interview"', '"phone screen"', '"technical screen"', '"recruiter screen"', '"next round"']
INTERVIEW_SUBJECT = re.compile(r"\binterview|phone screen|technical screen|recruiter (call|screen)", re.I)
INTERVIEW_TEXT = re.compile(r"(schedule (an|your) interview|interview invitation|invit\w+ (you )?to (an )?interview|"
                            r"phone screen|technical screen|recruiter screen|next round)", re.I)
CONDITIONAL = re.compile(r"\b(if|once|should|when|whether|may)\b[^.!?]{0,80}$", re.I)   # "if selected, we'll schedule an interview"
GENERIC = {"senior", "sr", "software", "engineer", "engineering", "developer", "staff", "the", "and", "for", "with", "ii", "iii",
           "level", "full", "time", "remote", "hybrid", "role", "position", "team"}


def role_words(role):
    return {w for w in re.findall(r"[a-z0-9+#]+", (role or "").lower()) if len(w) >= 2 and w not in GENERIC}


def status_emails(since, skip=frozenset()):
    """Rejection and interview emails since `since` not in `skip`: [{id, date, from, subject, snippet, refs, status}]."""
    user, pw = outreach.login_args()
    found = []
    with imaplib.IMAP4_SSL("imap.gmail.com") as m:
        m.login(user, pw)
        m.select(ALL_MAIL, readonly=True)
        queries = [("Rejected", rejection_query(since)),
                   ("Interviewing", f"after:{since:%Y/%m/%d} -in:sent -in:drafts -in:chats {{{' '.join(INTERVIEW)}}} "
                                    + " ".join(f"-{r}" for r in REJECTION))]
        for status, q in queries:
            for x in search(m, q, since):
                if x["id"] in skip or any(f["id"] == x["id"] for f in found):
                    continue
                if status == "Interviewing" and is_confirmation(x["subject"]):
                    continue   # "thanks for applying ... we'll reach out to schedule an interview"
                x["status"] = status
                found.append(x)
        by_uid = {x["uid"]: x for x in found}
        uids = list(by_uid)
        for i in range(0, len(uids), 25):
            typ, res = m.uid("FETCH", ",".join(uids[i:i + 25]), "(BODY.PEEK[])")
            for part in res:
                uid = isinstance(part, tuple) and re.search(rb"UID (\d+)", part[0])
                if uid and uid.group(1).decode() in by_uid:
                    msg = email.message_from_bytes(part[1])
                    x = by_uid[uid.group(1).decode()]
                    x["snippet"] = snippet(msg, 1200)
                    x["refs"] = re.findall(r"<[^>]+>", f"{msg['In-Reply-To'] or ''} {msg['References'] or ''}")
    out = []
    for x in found:
        x.pop("uid", None)
        x.setdefault("snippet", "")
        x.setdefault("refs", [])
        if x["status"] == "Interviewing":
            if re.search(r"unfortunately|regret", x["snippet"], re.I):
                continue
            hit = INTERVIEW_TEXT.search(x["snippet"])
            strong = INTERVIEW_SUBJECT.search(x["subject"]) or (hit and not CONDITIONAL.search(x["snippet"][:hit.start()]))
            if not strong:
                continue
        out.append(x)
    return out


ACTIVE = {"Rejected": {"Saved", "Resume ready", "Applied", "Interviewing"}, "Interviewing": {"Saved", "Resume ready", "Applied"}}


PLATFORMS = re.compile(r"linkedin|greenhouse|ashby|lever\.co|workday|icims|workable|rippling|smartrecruiters|jobvite|indeed|"
                       r"ziprecruiter|tsenta|calendly|goodtime|gem\.com|recruitee|bamboohr|myworkday|candid", re.I)
NAME_NOISE = {"inc", "llc", "ltd", "corp", "co", "the", "technologies", "technology", "labs", "company", "group", "hq"}


def words_of(text):
    return " " + " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())) + " "


def company_phrase(name):
    return " ".join(w for w in words_of(name).split() if w not in NAME_NOISE)


def sender_words(frm):
    """The sender as evidence of the company: display name and domain, minus job boards and ATS platforms
    (a LinkedIn jobs email is about some other company)."""
    name, _, addr = frm.rpartition("<")
    name, addr = (name or frm).strip(' "'), addr.rstrip(">")
    if PLATFORMS.search(addr):
        return PLATFORMS.sub(" ", name)
    return f"{name} {addr.split('@')[-1].rsplit('.', 1)[0]}"


def match_job(x, records):
    """(tracker record, how) for a status email, or (None, reason with the candidates)."""
    by_ref = [r for r in records if set(r.get("gmail_ids", [])) & set(x["refs"])]
    if by_ref:
        return by_ref[0], "reply to its confirmation"
    header = words_of(f"{sender_words(x['from'])} {x['subject']}")
    body = words_of(x["snippet"][:400])
    dated = [r for r in records if company_phrase(r.get("company")) and (r.get("date_applied") or r["date_added"]) <= x["date"]]
    cands = [r for r in dated if f" {company_phrase(r['company'])} " in header]
    if not cands:   # generic subject ("Thank you"): the body may name the company, if the name is distinctive enough
        cands = [r for r in dated if len(company_phrase(r["company"])) >= 6 and not PLATFORMS.search(r["company"])
                 and f" {company_phrase(r['company'])} " in body]
    open_ = [r for r in cands if r.get("status") in ACTIVE[x["status"]]]
    if not cands:
        return None, "no tracked job at this company"
    if not open_:
        return None, "its jobs are already " + "/".join(sorted({r.get("status", "") for r in cands}))
    x["candidates"] = [{"id": r["id"], "company": r["company"], "role": r["role"], "status": r.get("status", "")} for r in open_[:8]]
    if len(open_) == 1:
        return open_[0], "only open job at the company"
    text = f"{x['subject']} {x['snippet']}".lower()
    scored = sorted(((len(role_words(r["role"]) & set(re.findall(r"[a-z0-9+#]+", text))) / max(1, len(role_words(r["role"]))), r)
                    for r in open_), key=lambda t: -t[0])
    if scored[0][0] >= 0.5 and scored[0][0] > scored[1][0]:
        return scored[0][1], "role named in the email"
    return None, "several open jobs: " + "; ".join(f"#{r['id']} {r['role']}" for r in open_[:5])


STATE = Path(__file__).resolve().parent.parent / "inbox_state.json"   # status emails dismissed in the web page


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"dismissed": []}


def dismiss(gmail_id):
    state = load_state()
    if gmail_id not in state["dismissed"]:
        state["dismissed"].append(gmail_id)
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1))
        tmp.replace(STATE)


def statuses(skip=frozenset()):
    """Status changes Gmail suggests: ([(record id, status, email)], [(email, reason)]). Emails already applied to a
    job (its `gmail_status_ids`) or dismissed are skipped."""
    records = tracker_records()
    done = {i for r in records for i in r.get("gmail_status_ids", [])} | set(load_state()["dismissed"])
    emails = status_emails(SEARCH_START, set(skip) | done)
    changes, unmatched = [], []
    for x in sorted(emails, key=lambda e: e["date"]):
        rec, how = match_job(x, records)
        if rec:
            changes.append((rec["id"], x["status"], dict(x, how=how)))
            rec["status"] = x["status"]   # later emails in this pass see the new status
        else:
            unmatched.append((x, how))
    return changes, unmatched


if __name__ == "__main__":
    main()
