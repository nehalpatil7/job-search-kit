#!/usr/bin/env python3
"""Count job applications per day from application-confirmation emails in Gmail (read-only).

Usage:
  python3 tools/inbox.py                 # counts for the last 8 days
  python3 tools/inbox.py --days 14
  python3 tools/inbox.py --list 2026-10-17   # the confirmations counted on one day
  python3 tools/inbox.py --totals            # applications and rejections since SEARCH_START

Uses the Gmail app password from tools/outreach.py (IMAP, read-only: nothing is marked read or moved).
Gmail's own search finds candidate emails (and drops ones whose text reads like a rejection); a subject check
then keeps only confirmations. Every confirmation counts once, so 3 applications to one company count as 3.
"""
import argparse
import datetime as dt
import email
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
             '"after careful consideration"']
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
    """[{date, from, subject}] for messages matching Gmail query q since `since`, kept by subject (local dates)."""
    out = []
    typ, data = m.uid("SEARCH", "X-GM-RAW", '"' + q.replace('"', '\\"') + '"')
    uids = data[0].split() if typ == "OK" else []
    for i in range(0, len(uids), 200):
        typ, res = m.uid("FETCH", b",".join(uids[i:i + 200]), "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
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
                out.append({"date": when.date().isoformat(), "from": text(h["From"]), "subject": subject})
    return sorted(out, key=lambda x: x["date"])


def is_confirmation(subject):
    return bool(CONFIRM.search(subject)) and not NOT_CONFIRM.search(subject)


def daily(days=8):
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
                      "rejected": len(rejected)}, "rejections": rejected}


def main():
    p = argparse.ArgumentParser(description="Applications per day from Gmail confirmations")
    p.add_argument("--days", type=int, default=8)
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


if __name__ == "__main__":
    main()
