"""Who the user is, from base/profile.json: the name used in PDF file names, the home location for job filters,
and the start of the current job search. Everything personal lives in base/, never in the code."""
import datetime as dt
import json
import re
from pathlib import Path

PROFILE = Path(__file__).resolve().parent.parent / "base" / "profile.json"


def profile():
    try:
        return json.loads(PROFILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def name_slug():
    """"First_Last" for file names (e.g. First_Last_Resume.pdf)."""
    p = profile()
    parts = [re.sub(r"[^A-Za-z0-9]+", "", p.get(k) or "") for k in ("firstName", "lastName")]
    return "_".join(x for x in parts if x) or "My"


def home_places():
    """City, state and state code from the profile, for "is this job near me" filters."""
    p = profile()
    return [x for x in (p.get("city"), p.get("state"), p.get("stateCode")) if x]


def search_start():
    """First day of the current job search (profile "searchStart"); defaults to 30 days ago."""
    try:
        return dt.date.fromisoformat(profile().get("searchStart") or "")
    except ValueError:
        return dt.date.today() - dt.timedelta(days=30)
