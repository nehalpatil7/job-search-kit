#!/usr/bin/env python3
"""Compile and verify a tailored resume against the base template.

Usage:
  python3 tools/check_resume.py .work/<run>/resume.tex   # full check; builds the PDF next to it
  python3 tools/check_resume.py --hook-post   # Claude Code PostToolUse hook (reads JSON on stdin)
  python3 tools/check_resume.py --hook-pre    # Claude Code PreToolUse hook: keeps base/resume.tex read-only

Checks (FAIL = must fix, WARN = should fix):
  FAIL  compiles with pdflatex, exactly 1 page
  FAIL  locked structure identical to base: preamble, header, sections, role lines, dates,
        education, skill-row labels, spacing commands. Only \\item text, skill-row items and the
        header location (set to the job's location) may change.
  FAIL  header location is plain text and the contact line still fits on one line
  FAIL  every number in the bullets exists in base/resume.tex or base/facts.md
  FAIL  every skill-row item is allowed (base row items + "Allowed skills" in facts.md)
  FAIL  no unescaped LaTeX special characters (% & # _) in bullets or skill rows
  WARN  page fill well below the base resume (too much white space at the bottom)
  WARN  bullets whose last line holds only a word or two
  WARN  overfull lines reported by LaTeX
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import me  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE_TEX = ROOT / "base" / "resume.tex"
FACTS = ROOT / "base" / "facts.md"
PDF_NAME = f"{me.name_slug()}_Resume"   # from base/profile.json
PAGE_H = 792.0           # letterpaper, points
BOTTOM_MARGIN = 0.3 * 72  # from the geometry line in the template
LINE_PT = 12.0           # one 10pt line plus leading
FILL_TOLERANCE = 1.2 * LINE_PT / (PAGE_H - BOTTOM_MARGIN)  # allow ~1 line less than the base

SKILL_ROW = re.compile(r"^\\textbf\{(?P<label>[^}]*):\}\s*(?P<items>.*?)(\\\\)?\s*$")
NUM = re.compile(r"\d+(?:\.\d+)?")
UNESCAPED = re.compile(r"(?<!\\)[%&#_]")
LOCKED_ROWS = {"Certifications", "Competitions"}
# header contact line: "<Location> | \href{tel:...}..."; the location is the only free part
HEADER_LINE = re.compile(r"^(?P<loc>[^|\\]*?)\s*\|\s*(?P<rest>\\href\{tel:.*)$")
LOCATION_OK = re.compile(r"^[A-Za-z][A-Za-z .,'()/-]{1,39}$")


# ---------- parsing ----------

def strip_comment(line):
    """Drop a trailing LaTeX comment (an unescaped %)."""
    m = re.search(r"(?<!\\)%", line)
    return line[: m.start()] if m else line


def in_skills(lines, i):
    """True when line i sits inside the SKILLS section."""
    for j in range(i, -1, -1):
        if lines[j].lstrip().startswith("\\section*"):
            return "SKILLS" in lines[j]
    return False


def header_location(tex):
    """The location at the start of the header contact line, or None if there is no such line."""
    for line in tex.splitlines():
        m = HEADER_LINE.match(line.strip())
        if m:
            return m.group("loc").strip()
    return None


def parse(tex):
    """Return (skeleton, bullets, skill_rows).

    skeleton: the document with bullet runs collapsed to <BULLETS>, skill-row items and the
    header location blanked, comments and blank lines removed. Must match the base exactly.
    """
    raw = tex.splitlines()
    skeleton, bullets, rows = [], [], {}
    for i, line in enumerate(raw):
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        h = HEADER_LINE.match(s)
        if h:
            skeleton.append("<LOCATION> | " + strip_comment(h.group("rest")).strip())
            continue
        if s.startswith("\\item"):
            bullets.append(s[len("\\item"):].strip())
            if not skeleton or skeleton[-1] != "<BULLETS>":
                skeleton.append("<BULLETS>")
            continue
        m = SKILL_ROW.match(s)
        if m and in_skills(raw, i):
            label = m.group("label")
            items = [x.strip() for x in m.group("items").split(",") if x.strip()]
            rows[label] = items
            skeleton.append(f"\\textbf{{{label}:}} <ITEMS>")
            continue
        skeleton.append(strip_comment(s).strip())
    return skeleton, bullets, rows


def facts_allowed_skills():
    text = FACTS.read_text()
    m = re.search(r"## Allowed skills\s*(.*?)(\n---|\Z)", text, re.S)
    if not m:
        return set()
    body = "\n".join(l for l in m.group(1).splitlines() if "," in l)
    return {x.strip() for x in body.split(",") if x.strip()}


def norm(item):
    return item.replace("\\&", "&").strip().lower()


# ---------- checks ----------

def check_structure(base_skel, skel):
    if base_skel == skel:
        return []
    import difflib
    diff = list(difflib.unified_diff(base_skel, skel, "base", "tailored", lineterm="", n=0))
    shown = "\n    ".join(diff[2:14])
    return [f"Locked structure changed (only \\item text, skill items and the header location may change):\n    {shown}"]


def check_location(loc):
    if loc is None:
        return ["Header contact line not found (it must start with \"<Location> | \\href{tel:...\")."]
    if not LOCATION_OK.match(loc):
        return [f"Header location must be plain text like \"City, ST\" (letters, spaces, commas; no LaTeX), got \"{loc}\"."]
    return []


def header_wraps(pdf, tex):
    """True when the header contact line no longer fits on one line (needs pdftotext)."""
    links = re.findall(r"\\href\{[^}]*\}\{([^}]*)\}", next(
        (l for l in tex.splitlines() if HEADER_LINE.match(l.strip())), ""))
    if len(links) < 2 or not shutil.which("pdftotext"):
        return False
    out = subprocess.run(["pdftotext", "-layout", "-f", "1", "-l", "1", str(pdf), "-"],
                         capture_output=True, text=True).stdout
    first = next((l for l in out.splitlines() if links[0] in l), None)
    return first is not None and links[-1] not in first


def check_numbers(bullets):
    known = set(NUM.findall(BASE_TEX.read_text())) | set(NUM.findall(FACTS.read_text()))
    errors = []
    for b in bullets:
        new = [n for n in NUM.findall(b) if n not in known]
        if new:
            errors.append(f"Number(s) {', '.join(new)} not found in base or facts.md: \"{b[:90]}...\"")
    return errors


def check_skills(base_rows, rows):
    extra_ok = {norm(x) for x in facts_allowed_skills()}
    errors = []
    for label, items in rows.items():
        allowed = {norm(x) for x in base_rows.get(label, [])}
        if label not in LOCKED_ROWS:
            allowed |= extra_ok
        bad = [x for x in items if norm(x) not in allowed]
        if bad:
            errors.append(f"Skills row '{label}' has items not in base/facts.md: {', '.join(bad)}")
        if not items:
            errors.append(f"Skills row '{label}' is empty")
    return errors


def check_escaping(bullets, rows):
    errors = []
    for text in bullets + [", ".join(v) for v in rows.values()]:
        if UNESCAPED.search(text):
            errors.append(f"Unescaped LaTeX character (use \\% \\& \\# \\_): \"{text[:90]}...\"")
    return errors


# ---------- build ----------

def compile_tex(tex_path, out_dir):
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
               f"-jobname={PDF_NAME}", f"-output-directory={tmp}", str(tex_path)]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            return None, None, ["pdflatex not found. Install a TeX distribution (see README)."]
        log = Path(tmp, PDF_NAME + ".log")
        log_text = log.read_text(errors="replace") if log.exists() else ""
        pdf = Path(tmp, PDF_NAME + ".pdf")
        if not pdf.exists():
            err = [l for l in log_text.splitlines() if l.startswith("!")][:3]
            return None, log_text, ["LaTeX failed to compile: " + " | ".join(err or ["see log"])]
        dest = Path(out_dir, PDF_NAME + ".pdf")
        shutil.copy(pdf, dest)
        return dest, log_text, []


def pages_from_log(log_text):
    m = re.search(r"Output written on .*?\((\d+) pages?", log_text, re.S)
    return int(m.group(1)) if m else None


def bottom_of_text(pdf):
    """Lowest text position as a fraction of the usable page height (needs poppler's pdftotext)."""
    if not shutil.which("pdftotext"):
        return None
    out = subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-bbox", str(pdf), "-"],
                         capture_output=True, text=True).stdout
    ys = [float(y) for y in re.findall(r'yMax="([\d.]+)"', out)]
    return max(ys) / (PAGE_H - BOTTOM_MARGIN) if ys else None


def short_last_lines(pdf):
    """Bullet lines that wrap with only one or two words on the last line."""
    if not shutil.which("pdftotext"):
        return []
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    hits, in_bullet = [], False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("•"):
            in_bullet = True
        elif in_bullet and s and line.startswith(" "):
            if len(s.split()) <= 2:   # a continuation this short is always a bullet's last line
                hits.append(s)
        else:
            in_bullet = False
    return hits


_BASE_FILL = None

def base_fill():
    global _BASE_FILL
    if _BASE_FILL is None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf, _, errs = compile_tex(BASE_TEX, tmp)
            _BASE_FILL = bottom_of_text(pdf) if pdf else None
    return _BASE_FILL


# ---------- main ----------

def run_checks(tex_path):
    tex_path = Path(tex_path).resolve()
    base_skel, _, base_rows = parse(BASE_TEX.read_text())
    tex = tex_path.read_text()
    skel, bullets, rows = parse(tex)

    fails = []
    fails += check_structure(base_skel, skel)
    fails += check_location(header_location(tex))
    fails += check_numbers(bullets)
    fails += check_skills(base_rows, rows)
    fails += check_escaping(bullets, rows)

    warns = []
    pdf, log_text, errs = compile_tex(tex_path, tex_path.parent)
    fails += errs
    if pdf:
        pages = pages_from_log(log_text)
        if pages != 1:
            fails.append(f"Resume is {pages} pages; it must be exactly 1.")
        if header_wraps(pdf, tex):
            fails.append(f"Header location \"{header_location(tex)}\" is too long: the contact line wraps. "
                         "Use the city alone (drop the state), or keep the base location.")
        overfull = log_text.count("Overfull \\hbox")
        if overfull:
            warns.append(f"{overfull} overfull line(s) reported by LaTeX.")
        fill, bfill = bottom_of_text(pdf), base_fill()
        if fill is not None and bfill is not None and pages == 1 and fill < bfill - FILL_TOLERANCE:
            gap = (bfill - fill) * (PAGE_H - BOTTOM_MARGIN) / LINE_PT
            warns.append(f"Page ends ~{gap:.0f} line(s) higher than the base; add or lengthen bullets to fill it.")
        for s in short_last_lines(pdf):
            warns.append(f"Bullet wraps with only a word or two on its last line: \"{s}\"")
        if not shutil.which("pdftotext"):
            warns.append("pdftotext not installed: page-fill and short-line checks skipped (see README).")

    return fails, warns, pdf


def report(tex_path, fails, warns, pdf):
    lines = [f"Resume check: {tex_path}"]
    lines += [f"  FAIL  {f}" for f in fails]
    lines += [f"  WARN  {w}" for w in warns]
    if not fails:
        lines.append(f"  PASS  1 page, structure intact, facts and skills verified -> {pdf}")
    return "\n".join(lines)


def hook_input():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return None
    path = (data.get("tool_input") or {}).get("file_path")
    return Path(path).resolve() if path else None


def main():
    args = sys.argv[1:]
    if args == ["--hook-pre"]:
        path = hook_input()
        base_dir = (ROOT / "base").resolve()
        if path == BASE_TEX.resolve() and not (base_dir / ".unlock").exists():
            print("base/resume.tex is read-only. Copy it into your run's .work/<run>/ folder and edit the copy. "
                  "To change the base itself, the user must ask explicitly; then run `touch base/.unlock`, "
                  "edit, and `rm base/.unlock` afterwards.", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    if args == ["--hook-post"]:
        path = hook_input()
        # each run works in its own .work/<run>/ folder
        if not path or path.name != "resume.tex" or path.parent.parent != (ROOT / ".work").resolve():
            sys.exit(0)
        fails, warns, pdf = run_checks(path)
        if fails or warns:
            print(report(path, fails, warns, pdf), file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    if len(args) != 1:
        print(__doc__)
        sys.exit(64)
    fails, warns, pdf = run_checks(args[0])
    print(report(args[0], fails, warns, pdf))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
