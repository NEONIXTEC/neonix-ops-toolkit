# -*- coding: utf-8 -*-
"""
review_scan.py - mechanical first pass of an incident-based review (see SKILL.md).

    I: the current repo's diff = merge-base(master, HEAD) -> working tree (committed + uncommitted + untracked)
    O: one ASCII line per hit: SEVERITY  CHECK  file:line  source-doc  (exit 1 if any HIGH)

    - Read-only: never edits, stages or commits. Output is ASCII only (cp949 console).
    - The hookify rule patterns are read from .claude/hookify.*.local.md, never copied here - one source of truth
      for "what the agent is warned about while editing" and "what the reviewer flags afterwards".
    - The judgment items (GET side effects, permission scope, ...) are in SKILL.md 2 and need a reader.

    python review_scan.py               # vs master
    python review_scan.py --base HEAD   # uncommitted only
    env PROD_CHECKOUT=<path>            # optional: also flag uncommitted edits in the production checkout
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# hookify rules: block -> HIGH, warn -> MED, unless listed here
SEV_OVERRIDE = {"warn-client-ip": "HIGH", "warn-secret-in-session": "HIGH", "warn-template-safe": "HIGH"}
# event:bash block rules that also make sense inside committed scripts (a script that kills the web server is the same act)
SCRIPT_RULES = ("block-prod-restart", "block-dev-mode-on-prod")
SCRIPT_EXT = (".ps1", ".bat", ".cmd", ".sh", ".py")
NAIVE_NOW = re.compile(r"\bdatetime\.(?:now\(\s*\)|utcnow\(|today\()")
JINJA_IN_COMMENT = [re.compile(r"<!--(?:(?!-->).)*?(\{\{|\{%)", re.S),
                    re.compile(r"/\*(?:(?!\*/).)*?(\{\{|\{%)", re.S),
                    re.compile(r"(?m)^\s*//[^\n]*(\{\{|\{%)")]
SPEC_ROOTS = tuple(r.strip().rstrip("/") + "/" for r in os.environ.get("DOCMETA_CODE_ROOTS", "app/,scripts/").split(",")
                   if r.strip())
TEMPLATES, STATIC, BUILD = "app/templates/", "app/static/", "app/build/"
SPEC_SKIP = ("/tests/", "/build/", "/node_modules/", "/vendor/", "/migrations/", "/__pycache__/")


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True)
    if check and r.returncode != 0:
        raise SystemExit("ERROR: git %s: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace")[:300]))
    return r.stdout.decode("utf-8", "replace")


def locate():
    wt = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    prod = os.getenv("PROD_CHECKOUT")
    return (Path(prod) if prod else None), wt


def load_rules(wt):
    """Parse .claude/hookify.*.local.md front matter (enabled rules only)."""
    rules = []
    for p in sorted((wt / ".claude").glob("hookify.*.local.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
        if not m:
            continue
        head = m.group(1)
        def get(k):
            km = re.search(r"(?m)^%s:\s*(.+?)\s*$" % k, head)
            return km.group(1) if km else ""
        if get("enabled").lower() != "true":
            continue
        conds = []
        for cm in re.finditer(r"-\s*field:\s*(\S+)\s*\r?\n\s*operator:\s*(\S+)\s*\r?\n\s*pattern:\s*(.+?)\s*$", head, re.M):
            conds.append((cm.group(1), cm.group(2), cm.group(3).strip("'\"") if cm.group(3)[:1] in "'\"" and cm.group(3)[-1:] == cm.group(3)[:1] else cm.group(3)))
        if not conds and get("pattern"):
            conds.append(("content" if get("event") == "file" else "command", "regex_match", get("pattern")))
        rules.append({"name": get("name"), "event": get("event"), "action": get("action") or "warn",
                      "conds": conds, "src": ".claude/" + p.name})
    return rules


def changed(wt, base):
    """{path: status} for base -> working tree, plus untracked files."""
    out = {}
    for line in git(wt, "diff", "--name-status", "-M", base).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out[parts[-1]] = parts[0][0]
    for f in git(wt, "ls-files", "--others", "--exclude-standard").splitlines():
        if f:
            out[f] = "A"
    return out


def added_lines(wt, base, path, status):
    """[(lineno, text)] of lines added in the working tree."""
    p = wt / path
    if status == "A" and not git(wt, "ls-files", "--", path).strip():
        try:
            return list(enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1))
        except OSError:
            return []
    res, n = [], 0
    for line in git(wt, "diff", "-U0", base, "--", path).splitlines():
        h = re.match(r"@@ -\S+ \+(\d+)", line)
        if h:
            n = int(h.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            res.append((n, line[1:]))
            n += 1
    return res


def match_cond(op, pattern, value):
    if op == "regex_match":
        return re.search(pattern, value) is not None
    if op == "contains":
        return pattern in value
    if op == "not_contains":
        return pattern not in value
    if op == "equals":
        return value == pattern
    return False


def first_line(pattern, adds):
    text = "\n".join(t for _, t in adds)
    m = re.search(pattern, text)
    if not m:
        return None
    idx = text.count("\n", 0, m.start())
    return adds[idx][0] if idx < len(adds) else adds[-1][0]


def eol_style(b):
    crlf, lf = b.count(b"\r\n"), b.count(b"\n")
    if lf == 0:
        return None
    return "CRLF" if crlf == lf else ("LF" if crlf == 0 else "MIXED")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="master", help="compare from merge-base(BASE, HEAD); HEAD = uncommitted only")
    a = ap.parse_args()
    main_repo, wt = locate()
    base = a.base if a.base == "HEAD" else git(wt, "merge-base", a.base, "HEAD").strip()
    files = changed(wt, base)
    rules = load_rules(wt)
    hits = []

    def hit(sev, check, where, src):
        hits.append((sev, check, where, src))

    dirty = [l for l in git(main_repo, "status", "--porcelain").splitlines() if l.strip()] if main_repo else []
    if dirty:
        hit("HIGH", "edits-in-production-checkout", "%s (%d files)" % (main_repo, len(dirty)),
            "edit in the staging worktree, never in the production checkout")

    for path, st in sorted(files.items()):
        if st == "D":
            continue
        p = wt / path
        abs_path = str(p).replace("/", "\\")
        adds = added_lines(wt, base, path, st)
        try:
            raw = p.read_bytes()
        except OSError:
            raw = b""
        full = raw.decode("utf-8", "replace")

        for r in rules:
            if r["event"] == "file":
                ok, line = True, None
                for field, op, pat in r["conds"]:
                    if field == "file_path":
                        ok = ok and match_cond(op, pat, abs_path)
                    elif field in ("content", "new_text", "new_string"):
                        if pat.startswith("(?s)\\A"):
                            ok = ok and match_cond(op, pat, full)
                        elif op == "regex_match":
                            line = first_line(pat, adds) if adds else None
                            ok = ok and line is not None
                        else:
                            ok = ok and match_cond(op, pat, "\n".join(t for _, t in adds))
                if ok:
                    sev = SEV_OVERRIDE.get(r["name"], "HIGH" if r["action"] == "block" else "MED")
                    hit(sev, r["name"], "%s:%s" % (path, line or "-"), r["src"])
            elif r["name"] in SCRIPT_RULES and path.endswith(SCRIPT_EXT) and adds:
                for field, op, pat in r["conds"]:
                    if field == "command" and op == "regex_match":
                        line = first_line(pat, adds)
                        if line:
                            hit("HIGH", r["name"] + " (in script)", "%s:%d" % (path, line), r["src"])

        if path.startswith(BUILD):
            hit("HIGH", "build-output-edited", path, "edit the source, the build is generated")
        if path.endswith(".bat") and raw and eol_style(raw) != "CRLF":
            hit("HIGH", "bat-not-crlf", path, "a LF .bat silently fails to start on Windows")
        if path.endswith(".ps1") and raw and any(c > 127 for c in raw) and not raw.startswith(b"\xef\xbb\xbf"):
            hit("HIGH", "ps1-nonascii-without-bom", path, "Windows PowerShell 5 reads BOM-less files as the ANSI code page")
        if st != "A" and raw:
            old = subprocess.run(["git", "-C", str(wt), "show", "%s:%s" % (base, path)], capture_output=True).stdout
            o, n = eol_style(old), eol_style(raw)
            if o in ("CRLF", "LF") and n in ("CRLF", "LF") and o != n:
                if not (path.endswith(".bat") and n == "CRLF"):
                    hit("HIGH", "eol-flip %s->%s" % (o, n), path, "sed -i / Write tools flip CRLF files - lint_eol.py")
        for n_, t in adds:
            if "\ufffd" in t:
                hit("HIGH", "broken-text (U+FFFD)", "%s:%d" % (path, n_), "text broken by a PowerShell/CP949 rewrite")
                break
        if path.endswith(".py"):
            for n_, t in adds:
                if NAIVE_NOW.search(t):
                    hit("MED", "naive-datetime", "%s:%d" % (path, n_), "use a timezone-aware now() helper")
                    break
            if st == "A" and path.startswith(SPEC_ROOTS) and not any(s in "/" + path for s in SPEC_SKIP) \
                    and os.path.basename(path) not in ("__init__.py", "conftest.py") and "[Module Spec]" not in full[:4000]:
                hit("HIGH", "new-py-without-module-spec", path, "lint_doc_meta.py")
        if path.startswith(TEMPLATES) and path.endswith(".html"):
            for rx in JINJA_IN_COMMENT:
                m = rx.search(full)
                if m:
                    hit("HIGH", "jinja-inside-comment", "%s:%d" % (path, full.count("\n", 0, m.start()) + 1),
                        "Jinja inside a comment still renders")
                    break
        if path.startswith(STATIC) and path.endswith((".js", ".css")) and "{%" in full:
            hit("HIGH", "jinja-in-static-file", path, "static files are served raw")

    order = {"HIGH": 0, "MED": 1, "LOW": 2, "INFO": 3}
    hits.sort(key=lambda h: (order.get(h[0], 9), h[1], h[2]))
    print("[review] worktree=%s base=%s files=%d" % (wt, base[:10], len(files)))
    for sev, check, where, src in hits:
        line = "  %-4s  %-34s %-58s %s" % (sev, check, where, src)
        print(line.replace(chr(0xa7), "#").replace(chr(0xb7), "/").encode("ascii", "replace").decode())
    n_high = sum(1 for h in hits if h[0] == "HIGH")
    print("[review] HIGH=%d MED=%d INFO=%d  (mechanical pass only - do SKILL.md 2 by reading the diff)"
          % (n_high, sum(1 for h in hits if h[0] == "MED"), sum(1 for h in hits if h[0] == "INFO")))
    return 1 if n_high else 0


if __name__ == "__main__":
    sys.exit(main())
