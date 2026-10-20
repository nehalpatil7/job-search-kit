#!/usr/bin/env python3
"""Find the open roles on a company's careers page and shortlist the ones worth scoring.

  python3 tools/careers.py fetch <careers url> --out WORK/jobs.json [--all-levels] [--all-locations] [--limit 25]
  python3 tools/careers.py save --url <careers url> --file WORK/ranked.json [--clean-work WORK]
  python3 tools/careers.py list                      # saved searches

fetch: detects the hiring platform and uses its public job feed:
  Greenhouse, Lever, Ashby, SmartRecruiters, Workday (*.myworkdayjobs.com).
  Custom careers pages: looks for an embedded Greenhouse/Lever/Ashby/Workday board first; otherwise renders
  the page in Puppeteer (tools/apply) and collects job links.
Then filters to engineering roles at the user's level near home (city/state in base/profile.json), remote or US-wide
(switch off with the flags), and keeps the best `--limit` by keyword overlap with their skills. Writes {company, platform, found, kept, jobs}.

save: stores Claude's ranked results in matches.json, marking roles not seen in earlier searches as new.
Uses only the Python standard library.
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import me  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MATCHES = ROOT / "matches.json"
WORK = ROOT / ".work"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36"
TIMEOUT = 30

# ---------- filters ----------

ENGINEERING = re.compile(r"engineer|developer|\bswe\b|\bsde\b|programmer|software", re.I)
OTHER_DISCIPLINE = re.compile(
    r"sales|solutions? engineer|pre-?sales|support engineer|customer|field engineer|mechanical|electrical|hardware|"
    r"civil|manufactur|process engineer|chemical|test technician|recruit|designer|analyst|account|"
    r"silicon|asic|fpga|rf engineer|firmware|embedded|validation engineer|construction|facilities", re.I)
OFF_LEVEL = re.compile(
    r"\b(staff|principal|distinguished|fellow|director|head of|vp|vice president|manager|lead manager|architect|"
    r"intern|internship|co-?op|new grad|graduate|university|apprentice|junior|entry[- ]level|associate)\b"
    r"|engineer,?\s+(i|1)\b(?!\s*i)|\bsde\s*i\b(?!i)|\bl[78]\b", re.I)
HOME = "".join(rf"\b{re.escape(x.lower())}\b|" for x in me.home_places())   # city, state, state code
GOOD_LOCATION = re.compile(HOME + r"remote|anywhere|united states|\bus\b|\busa\b|\bu\.s\.|north america|americas", re.I)


def skill_terms():
    """Skills the user can back up: base Skills rows + facts.md 'Allowed skills'."""
    terms = set()
    tex = (ROOT / "base" / "resume.tex").read_text(errors="ignore")
    for m in re.finditer(r"\\textbf\{[^}]*:\}\s*(.*?)(\\\\)?$", tex, re.M):
        terms.update(t.strip() for t in m.group(1).split(","))
    facts = (ROOT / "base" / "facts.md").read_text(errors="ignore")
    m = re.search(r"## Allowed skills\s*(.*?)(\n---|\Z)", facts, re.S)
    if m:
        for line in m.group(1).splitlines():
            if "," in line:
                terms.update(t.strip() for t in line.split(","))
    terms |= {"backend", "distributed", "microservices", "event-driven", "kafka", "java", "python", "llm",
              "platform", "infrastructure", "data pipeline", "api", "scalab"}
    return {t.lower().replace("\\&", "&") for t in terms if 1 < len(t) < 40}


def shortlist(jobs, all_levels=False, all_locations=False, limit=25):
    kept = []
    for j in jobs:
        title = j.get("title", "")
        if not ENGINEERING.search(title) or OTHER_DISCIPLINE.search(title):
            continue
        if not all_levels and OFF_LEVEL.search(title):
            continue
        loc = f"{j.get('location', '')} {'remote' if j.get('remote') else ''}".strip()
        multi = re.fullmatch(r"\d+\s+locations?", loc.strip(), re.I)  # Workday hides multi-city lists
        if not all_locations and loc and not multi and not GOOD_LOCATION.search(loc):
            continue
        kept.append(j)
    terms = skill_terms()
    for j in kept:
        text = f"{j.get('title', '')} {j.get('title', '')} {j.get('description', '')}".lower()
        j["prescore"] = sum(1 for t in terms if t in text) + (3 if re.search(r"senior|\bii\b|\biii\b", j.get("title", ""), re.I) else 0)
    kept.sort(key=lambda j: -j["prescore"])
    return kept[:limit], len(kept)


# ---------- http ----------

def get(url, data=None, headers=None, as_json=True):
    hdrs = {"User-Agent": UA, "Accept": "application/json" if as_json else "text/html,*/*"}
    hdrs.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read().decode("utf-8", errors="replace")
    return json.loads(raw) if as_json else raw


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


# ---------- platforms ----------

def detect(url):
    u = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    q = urllib.parse.parse_qs(u.query)
    if "greenhouse.io" in host:
        token = (q.get("for") or [None])[0] or (parts[0] if parts and parts[0] not in ("embed", "v1") else None)
        if token:
            return "greenhouse", {"token": token}
    if host.endswith("lever.co") and parts:
        return "lever", {"company": parts[0], "eu": ".eu." in host}
    if "ashbyhq.com" in host and parts:
        return "ashby", {"org": parts[0]}
    if "smartrecruiters.com" in host and parts:
        return "smartrecruiters", {"company": parts[0]}
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        site_parts = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        if site_parts and site_parts[0] == "recruiting" and len(site_parts) >= 3:  # myworkdaysite.com/recruiting/<tenant>/<site>
            return "workday", {"host": host, "tenant": site_parts[1], "site": site_parts[2]}
        if site_parts:
            return "workday", {"host": host, "tenant": host.split(".")[0], "site": site_parts[0]}
    return "custom", {"url": u.geturl()}


def greenhouse(token, **_):
    data = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    return [{"title": j.get("title", ""), "location": (j.get("location") or {}).get("name", ""),
             "url": j.get("absolute_url", ""), "description": strip_html(j.get("content", ""))[:12000],
             "posted": (j.get("updated_at") or "")[:10]} for j in data.get("jobs", [])]


def lever(company, eu=False, **_):
    base = "https://api.eu.lever.co" if eu else "https://api.lever.co"
    data = get(f"{base}/v0/postings/{company}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        lists = "\n".join(f"{l.get('text', '')}\n{strip_html(l.get('content', ''))}" for l in j.get("lists") or [])
        out.append({"title": j.get("text", ""), "location": cats.get("location", "") or ", ".join(cats.get("allLocations") or []),
                    "remote": (j.get("workplaceType") or "").lower() == "remote",
                    "url": j.get("hostedUrl", ""), "description": f"{j.get('descriptionPlain', '')}\n{lists}"[:12000]})
    return out


def ashby(org, **_):
    data = get(f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=false")
    return [{"title": j.get("title", ""), "location": j.get("location", ""), "remote": bool(j.get("isRemote")),
             "url": j.get("jobUrl", ""), "description": (j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", "")))[:12000],
             "posted": (j.get("publishedAt") or "")[:10]}
            for j in data.get("jobs", []) if j.get("isListed", True)]


def smartrecruiters(company, **_):
    out, offset = [], 0
    while offset < 1000:
        data = get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset={offset}")
        for j in data.get("content", []):
            loc = j.get("location") or {}
            out.append({"title": j.get("name", ""), "remote": bool(loc.get("remote")),
                        "location": ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
                        "url": f"https://jobs.smartrecruiters.com/{company}/{j.get('id')}", "detail": j.get("ref", ""),
                        "posted": (j.get("releasedDate") or "")[:10]})
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return out


def smartrecruiters_detail(job):
    d = get(job["detail"])
    secs = ((d.get("jobAd") or {}).get("sections") or {})
    return "\n".join(strip_html((secs.get(k) or {}).get("text", "")) for k in
                     ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))[:12000]


WORKDAY_QUERIES = ["software engineer", "backend engineer", "platform engineer", "developer"]


def workday(host, tenant, site, max_pages=25, **_):
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    seen, out = set(), []
    for q in WORKDAY_QUERIES:
        for page in range(max_pages):
            data = get(api, data={"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": q})
            posts = data.get("jobPostings") or []
            for j in posts:
                path = j.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                out.append({"title": j.get("title", ""), "location": j.get("locationsText", ""),
                            "url": f"https://{host}/{site}{path}", "detail": f"https://{host}/wday/cxs/{tenant}/{site}{path}",
                            "posted": j.get("postedOn", "")})
            if len(posts) < 20 or (page + 1) * 20 >= (data.get("total") or 0):
                break
    return out


def workday_detail(job):
    d = get(job["detail"]).get("jobPostingInfo") or {}
    if d.get("remoteType"):
        job["location"] = f"{job.get('location', '')} {d['remoteType']}".strip()
    return strip_html(d.get("jobDescription", ""))[:12000]


EMBED_PATTERNS = [
    r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?[A-Za-z0-9_-]+",
    r"https?://jobs(?:\.eu)?\.lever\.co/[A-Za-z0-9_.-]+",
    r"https?://jobs\.ashbyhq\.com/[A-Za-z0-9_.%-]+",
    r"https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+",
    r"https?://(?:careers|jobs)\.smartrecruiters\.com/[A-Za-z0-9_-]+",
]
GH_SCRIPT = re.compile(r"greenhouse\.io/embed/job_board/js\?for=([A-Za-z0-9_-]+)")


def find_embedded(page_html):
    m = GH_SCRIPT.search(page_html)
    if m:
        return f"https://boards.greenhouse.io/{m.group(1)}"
    for pat in EMBED_PATTERNS:
        m = re.search(pat, page_html)
        if m and "/embed/job_board/js" not in m.group(0):
            return m.group(0)
    return None


def custom(url, **_):
    """Embedded ATS first (cheap and complete), then a rendered-page link scan."""
    try:
        page = get(url, as_json=False)
        emb = find_embedded(page)
        if emb:
            platform, args = detect(emb)
            if platform != "custom":
                return platform, FETCHERS[platform](**args)
    except (urllib.error.URLError, OSError, ValueError):
        page = ""
    script = ROOT / "tools" / "apply" / "listjobs.js"
    if not (ROOT / "tools" / "apply" / "node_modules").exists() or not shutil.which("node"):
        raise RuntimeError("This careers page needs a real browser to read. Run `cd tools/apply && npm install` once "
                           "(the same setup as Prefill), then try again.")
    res = subprocess.run(["node", str(script), url], capture_output=True, text=True, timeout=180)
    if res.returncode != 0:
        raise RuntimeError(f"Couldn't render the careers page: {(res.stdout + res.stderr).strip()[-300:]}")
    data = json.loads(res.stdout or "{}")
    if data.get("embedded"):
        platform, args = detect(data["embedded"])
        if platform != "custom":
            return platform, FETCHERS[platform](**args)
    return "custom", [{"title": j["title"], "location": j.get("location", ""), "url": j["url"]} for j in data.get("jobs", [])]


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters, "workday": workday}
DETAIL = {"workday": workday_detail, "smartrecruiters": smartrecruiters_detail}


def company_name(platform, args, url):
    raw = args.get("token") or args.get("company") or args.get("org") or args.get("tenant") or \
        urllib.parse.urlparse(url).netloc.replace("www.", "").replace("careers.", "").replace("jobs.", "").split(".")[0]
    return re.sub(r"[-_]+", " ", raw).strip().title()


def fetch(url, all_levels=False, all_locations=False, limit=25):
    platform, args = detect(url)
    if platform == "custom":
        platform, jobs = custom(**args)
    else:
        jobs = FETCHERS[platform](**args)
    jobs = [j for j in jobs if j.get("title") and j.get("url")]
    if not jobs:
        raise RuntimeError("No open roles were found on that page.")
    short, passed = shortlist(jobs, all_levels, all_locations, limit)
    for j in short:  # descriptions for the shortlist only
        if not j.get("description") and platform in DETAIL and j.get("detail"):
            try:
                j["description"] = DETAIL[platform](j)
            except Exception as exc:  # one bad posting shouldn't sink the search
                j["description"] = ""
                j["detail_error"] = str(exc)[:120]
        j.pop("detail", None)
    return {"careers_url": url, "company": company_name(platform, args, url), "platform": platform,
            "found": len(jobs), "passed_filters": passed, "kept": len(short),
            "filters": {"all_levels": all_levels, "all_locations": all_locations, "limit": limit}, "jobs": short}


# ---------- saved searches ----------

def load_matches():
    return json.loads(MATCHES.read_text()) if MATCHES.exists() else {"searches": []}


def clean(u):
    u = urllib.parse.urlsplit(u or "")
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query) if not re.match(r"utm_|gh_src|source|ref", k, re.I)]
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), u.path.rstrip("/"), urllib.parse.urlencode(q), ""))


def save(careers_url, ranked_path, meta_path=None, clean_work=None):
    ranked = json.loads(Path(ranked_path).read_text())
    meta = json.loads(Path(meta_path).read_text()) if meta_path and Path(meta_path).exists() else {}
    results = ranked.get("results", ranked) if isinstance(ranked, dict) else ranked
    if not isinstance(results, list):
        sys.exit("ranked file must be a list of results")
    for r in results:
        r["score"] = max(0, min(100, int(round(float(r.get("score", 0))))))
    results.sort(key=lambda r: -r["score"])
    data = load_matches()
    key = clean(careers_url)
    seen = {clean(r["url"]) for s in data["searches"] if clean(s["url"]) == key for r in s.get("results", [])}
    for r in results:
        r["new"] = clean(r.get("url", "")) not in seen
        r["label"] = next(l for f, l in [(85, "Strong"), (70, "Good"), (55, "Moderate"), (0, "Stretch")] if r["score"] >= f)
    sid = max((s["id"] for s in data["searches"]), default=0) + 1
    data["searches"].append({"id": sid, "url": careers_url, "company": meta.get("company", ""),
                             "platform": meta.get("platform", ""), "date": dt.datetime.now().isoformat(timespec="minutes"),
                             "found": meta.get("found"), "passed_filters": meta.get("passed_filters"),
                             "filters": meta.get("filters", {}), "results": results})
    tmp = MATCHES.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, MATCHES)
    if clean_work:
        d = Path(clean_work).resolve()
        if WORK.resolve() in d.parents:
            shutil.rmtree(d, ignore_errors=True)
    new = sum(1 for r in results if r["new"])
    print(f"Saved match #{sid}: {len(results)} roles ranked ({new} new) for {meta.get('company') or careers_url}")


def delete_search(sid):
    data = load_matches()
    keep = [s for s in data["searches"] if s["id"] != sid]
    if len(keep) == len(data["searches"]):
        return False
    data["searches"] = keep
    tmp = MATCHES.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, MATCHES)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("url")
    f.add_argument("--out", required=True)
    f.add_argument("--all-levels", action="store_true")
    f.add_argument("--all-locations", action="store_true")
    f.add_argument("--limit", type=int, default=25)
    s = sub.add_parser("save")
    s.add_argument("--url", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--meta", help="the jobs.json written by fetch (company, platform, counts)")
    s.add_argument("--clean-work", metavar="DIR")
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "fetch":
        try:
            res = fetch(a.url, a.all_levels, a.all_locations, max(1, min(a.limit, 40)))
        except urllib.error.HTTPError as exc:
            sys.exit(f"FETCH FAILED: the careers site answered {exc.code} ({a.url}).")
        except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
            sys.exit(f"FETCH FAILED: {exc}")
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
        missing = sum(1 for j in res["jobs"] if not j.get("description"))
        print(f"{res['company']} ({res['platform']}): {res['found']} open roles, {res['passed_filters']} match the filters, "
              f"kept the top {res['kept']} for scoring" + (f"; {missing} still need their description fetched" if missing else ""))
    elif a.cmd == "save":
        save(a.url, a.file, a.meta, a.clean_work)
    else:
        for s_ in load_matches()["searches"]:
            print(f"#{s_['id']:<3} {s_['date']}  {s_.get('company') or s_['url']}: {len(s_['results'])} ranked")


if __name__ == "__main__":
    main()
