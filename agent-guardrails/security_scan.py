# -*- coding: utf-8 -*-
"""
security_scan.py - a pre-commit / pre-merge security guard for a repo that AI coding agents commit to.

Each rule closes a risk class found while running NEONIX_TEC's production SaaS (Flask + PostgreSQL on
Windows) with several AI coding agents committing to one repo. Design notes: README.md.

    python security_scan.py --staged                 # pre-commit hook (githooks/pre-commit)
    python security_scan.py --range master...staging # pre-merge gate (also catches `git commit --no-verify`)
    python security_scan.py --all                    # whole tree, report hits not in the baseline
    python security_scan.py --all --write-baseline   # human-approved reset of the baseline only

Design:
    - Output is ASCII only (Korean Windows consoles are cp949) and never prints the matched secret/PII text itself.
    - HIGH has no escape hatch - fix it. MED clears with `security-ok: <reason>` on that line or the line above.
    - Only new lines are judged; existing hits are fingerprints in .security_baseline.json (no text, sha1 only).
      Do not regenerate the baseline to get a commit through - that is the same as --no-verify.

Configuration (env, all optional):
    SECSCAN_STATIC_DIR      web-served static folder, repo-relative   (default: static/)
    SECSCAN_PUBLIC_LIST     allow-list of public static files        (default: .security_public_static.txt)
    SECSCAN_BASELINE        baseline file                            (default: .security_baseline.json)
    SECSCAN_ALLOW_DOMAINS   extra e-mail domains that are fine to publish, comma separated (your own domain)
    SECSCAN_ALLOW_PHONES    public business phone numbers, comma separated (digits only is fine)

Rules (one risk class each):
  S1 HIGH  new .html/.json/.txt under the static dir not in the public allow-list, or an e-mail /
           mobile number / resident number added to a static file
           (the whole static dir is served to anyone - a sample or mockup page dropped there is public)
  S2 HIGH  private keys, provider tokens, KEY=<high-entropy value>
  S3 HIGH  resident registration number, or a full mobile number in docs/scripts/code
           (one-off migration scripts and notes are where test data tends to get pasted)
  S4 HIGH  new public POST route with no login check and no @limiter.limit; MED for a public route that
           still lacks one of them (public endpoints that cost money or ping ops need a cap)
  S5 HIGH  session[...] = True with no time stamp next to it (a bare "verified" flag lives as long as the cookie)
  S6 HIGH  offsite backup write in a file that never goes through age, HighestAvailable in a task XML
           or #Requires -RunAsAdministrator (one elevated process makes the daily restart skip every process)
  S7 HIGH  a git remote URL that carries a credential (https://user:pass@ / token@ / ghp_ / github_pat_ / glpat-)
           - checked on every run, not just new lines: .git/config is never committed, so a diff can't see it,
           and `git remote -v` would print the token into any log or agent transcript.
           Fix = `git remote set-url <name> https://host/owner/repo.git` + the OS credential manager.
"""
import argparse
import difflib
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

def _repo_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else Path.cwd()


ROOT = _repo_root()
STATIC_DIR = os.environ.get("SECSCAN_STATIC_DIR", "static/").replace("\\", "/").rstrip("/") + "/"
BASELINE = ROOT / os.environ.get("SECSCAN_BASELINE", ".security_baseline.json")
PUBLIC_LIST = ROOT / os.environ.get("SECSCAN_PUBLIC_LIST", ".security_public_static.txt")

SKIP_DIRS = ("build", "dist", "node_modules", "__pycache__", ".gradle")   # whole path segments only
SKIP_FILES = (BASELINE.name,)
SKIP_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svgz", ".pdf", ".zip", ".gz", ".7z", ".apk",
               ".aab", ".jar", ".keystore", ".jks", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".wav",
               ".ogg", ".db", ".sqlite", ".pyc", ".exe", ".dll", ".so", ".psd", ".xlsx", ".xls", ".docx", ".hwp",
               ".age", ".dump", ".bin", ".lock")
MAX_BYTES = 1_500_000
SOURCES = {
    "S1": "public static file / sample page served to anyone",
    "S2": "no hardcoded secrets",
    "S3": "personal data in code or docs",
    "S4": "public endpoints need login or a rate limit",
    "S5": "a verified flag needs a time",
    "S6": "offsite = encrypted only / never elevated",
    "S7": "no token in a git remote URL",
}

# ---------------------------------------------------------------- patterns
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\d-])01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?![\d-])")
RRN_RE = re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-[1-8]\d{6}(?!\d)")
ALLOW_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "test", "localhost", "anthropic.com",
                       "users.noreply.github.com", "sentry.io", "domain.com", "email.com", "company.com") + tuple(
    d.strip().lower() for d in os.environ.get("SECSCAN_ALLOW_DOMAINS", "").split(",") if d.strip())
ALLOW_EMAILS = set()   # add your public support address here if it is not on an allowed domain
ALLOW_LOCALPARTS = {"user", "test", "example", "name", "id", "abc", "foo", "sample", "yourname", "you", "me",
                    "email", "mail", "hello", "admin", "your_id", "myid"}

TOKEN_RES = [
    ("age private key", re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{50,}")),
    ("PEM private key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI-style key", re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("GitHub token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}")),
    ("AWS access key", re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])")),
    ("Discord bot token", re.compile(r"(?<![\w-])[MNO][A-Za-z\d_-]{23,27}\.[\w-]{6}\.[\w-]{27,40}(?![\w-])")),
    ("Discord webhook", re.compile(r"discord(?:app)?\.com/api/webhooks/\d{15,}/[\w-]{40,}")),
    ("Telegram bot token", re.compile(r"(?<!\d)\d{8,10}:AA[\w-]{33}")),
    ("Fernet key literal", re.compile(r"(?<![\w-])[A-Za-z0-9_-]{43}=(?![\w=])(?=.*(?:fernet|master\.key|FERNET))", re.I)),
]
KV_RE = re.compile(r"""(?ix)
    (?:^|[\s{,(])['"]?
    ([A-Z0-9_.-]*(?:secret|token|passwd|password|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|webhook|dsn|auth[_-]?key)[A-Z0-9_]*)
    ['"]?\s*(?:=|:)\s*['"]?([^\s'"`#,;)}]{16,})""")
DUMMY_RE = re.compile(r"(?i)dummy|test|fake|example|sample|placeholder|changeme|change[_-]?me|your[_-]|xxxx|\*\*\*|"
                      r"premerge|staging|redacted|replace|<|>|\$\{|\{\{|%\(|os\.getenv|environ|getenv|config\[|"
                      r"\.get\(|localhost|127\.0\.0\.1|^[a-z_.]+$|^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$|[()\[\]]|\?\.|\.value\b|"
                      r"^https?://(?![^/\s]*:[^/\s]*@)[^?]*$")

ROUTE_RE = re.compile(r"^\s*@(\w+)\.(route|get|post|put|delete|patch)\(")
WRITE_METHOD_RE = re.compile(r"""methods\s*=\s*[\[(][^\])]*['"](POST|PUT|DELETE|PATCH)['"]""", re.I)
GUARD_RE = re.compile(r"session\.get\(|session\[|in session\b|_require_\w*\(|@_require_\w+\b|_guard\(|_master_self\(|abort\(40[13]\)|"
                      r"login_required|master_required|_uid\(\)|is_local_request\(|compare_digest\(|verify_signature|"
                      r"_check_key\(|_auth\w*\(|current_user|_me\w*\(|X-Internal-Key|_verify\w*\(")
CUSTOM_LIMIT_RE = re.compile(r"\w+_allowed\(|_budget_ok\(|code_guard\.|rate_?limit")
SIDE_EFFECT_RE = re.compile(r"request\.|db\.|\.query\b|open\(|requests\.|httpx\.|subprocess|send_\w+\(")
SESSION_TRUE_RE = re.compile(r"""session\[\s*['"]([A-Za-z0-9_]+)['"]\s*\]\s*=\s*True\b""")
SESSION_TIME_RE = re.compile(r"""session\[\s*['"][A-Za-z0-9_]*(?:_at|_ts|_time)['"]\s*\]\s*=|=\s*(?:int\()?\s*time\.time\(|kst_now\(""")
OFFSITE_RE = re.compile(r"(?i)offsite|\\MEGA\\|/MEGA/|GoogleDrive|My Drive|OneDrive|Dropbox|내 드라이브")
WRITE_OP_RE = re.compile(r"(?i)shutil\.(?:copy\w*|move|make_archive)|\.write_bytes\(|\.write_text\(|open\([^)]*['\"][wa]b?['\"]|"
                         r"zipfile\.ZipFile\([^)]*['\"]w|tarfile\.open\([^)]*['\"]w|Copy-Item|robocopy|Compress-Archive|"
                         r"Move-Item|Out-File|Set-Content")
AGE_USE_RE = re.compile(r"_age\b|age_encrypt|encrypt_file|\.age\b|age\.exe|AGE_EXE")
OK_MARK_RE = re.compile(r"security-ok:\s*\S.{2,}")


def _git_b(args, cwd=ROOT) -> bytes:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True)
    return r.stdout if r.returncode == 0 else b""


def _git(args, cwd=ROOT) -> str:
    return _git_b(args, cwd).decode("utf-8", "replace")


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(ch) for ch in set(s)))


BIZ_PHONES = {re.sub(r"\D", "", p) for p in os.environ.get("SECSCAN_ALLOW_PHONES", "").split(",") if p.strip()}


def dummy_phone(raw: str) -> bool:
    """# I: matched phone text -> O: True for obvious placeholders (0000, 1234-5678, all one digit) or the public biz tel."""
    d = re.sub(r"\D", "", raw)
    tail = d[3:]
    if d in BIZ_PHONES:
        return True
    if len(set(tail)) <= 1 or tail in ("12345678", "1234567", "87654321", "01234567"):
        return True
    mid, last = tail[:-4], tail[-4:]
    return len(set(mid)) <= 1 or len(set(last)) <= 1 or last in ("1234", "5678") and mid in ("1234", "123", "0000")


def dummy_rrn(raw: str) -> bool:
    """# I: YYMMDD-NNNNNNN -> O: True for sample numbers (900101-1234567: the digits after the sex digit ascend)."""
    tail = raw.split("-")[1]
    return tail[1:] in "0123456789" or len(set(tail)) <= 2


def allowed_email(e: str) -> bool:
    low = e.lower()
    if low in ALLOW_EMAILS or low.split("@", 1)[0] in ALLOW_LOCALPARTS:
        return True
    dom = low.rsplit("@", 1)[-1]
    if re.search(r"\.(png|jpe?g|gif|webp|svg|css|js)$", low) or "@2x" in low or "@3x" in low:
        return True   # retina asset names, npm scopes
    return any(dom == a or dom.endswith("." + a) for a in ALLOW_EMAIL_DOMAINS)


def public_list():
    try:
        lines = PUBLIC_LIST.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {l.split("#", 1)[0].strip() for l in lines if l.split("#", 1)[0].strip()}


def is_static_public(path: str) -> bool:
    return path.startswith(STATIC_DIR) and path.lower().endswith((".html", ".htm", ".json", ".txt"))


def skip_path(path: str) -> bool:
    # segment match: "build/x.js" and "app/build/x.js" are skipped, "scripts/rebuild/x.py" is not
    parts = path.split("/")
    return (any(d in parts[:-1] for d in SKIP_DIRS) or parts[-1] in SKIP_FILES
            or path.lower().endswith(SKIP_SUFFIX))


# ---------------------------------------------------------------- per-file scan
def scan_file(path, added, content, new_file=False):
    """# I: repo path, [(lineno, text)] of added lines, full new content, new_file flag
       # O: [(sev, rule, path, lineno, why, line_text)]  (line_text only for the fingerprint, never printed)"""
    hits = []
    if skip_path(path):
        return hits
    lines = content.splitlines()
    low = path.lower()
    minified = ".min." in low or low.endswith(("package-lock.json", "yarn.lock"))

    def hit(sev, rule, ln, why, text):
        hits.append((sev, rule, path, ln, why, text))

    # S1 file level
    if new_file and is_static_public(path) and path not in public_list():
        hit("HIGH", "S1", 0, "new public file under %s (served to anyone) - not in %s; put samples/mockups "
            "in docs/" % (STATIC_DIR, PUBLIC_LIST.name), "FILE " + path)

    is_test = "/tests/" in "/" + path or "_test." in low or "test_" in os.path.basename(low)

    for ln, text in added:
        # S2 provider tokens run on every line, minified and very long ones too (a key pasted into a bundle
        # is still a leaked key); only the costlier checks below skip them
        token = next((label for label, rx in TOKEN_RES if rx.search(text)), None)
        if token:
            hit("HIGH", "S2", ln, token + " literal - move it to .env (never commit secrets)", text)
        if minified or len(text) > 4000:
            continue
        if not token:
            if not is_test:
                for m in KV_RE.finditer(text):
                    val = m.group(2)
                    if DUMMY_RE.search(val) or re.search(r"(?i)test|dummy|example|fake|sample", m.group(1)):
                        continue
                    if len(set(val)) < 8 or _entropy(val) < 3.5:
                        continue
                    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{7,12}", val) and "secret" not in m.group(1).lower():
                        continue   # git hashes in docs
                    hit("HIGH", "S2", ln, "high-entropy value for '%s' - secrets live in .env only"
                        % m.group(1).encode("ascii", "replace").decode()[:40], text)
                    break
        # PII
        if any(not dummy_rrn(r) for r in RRN_RE.findall(text)):
            hit("HIGH", "S1" if path.startswith(STATIC_DIR) else "S3", ln,
                "resident registration number pattern (YYMMDD-NNNNNNN)", text)
        phones = [p for p in PHONE_RE.findall(text) if not dummy_phone(p)]
        if path.startswith(STATIC_DIR):
            mails = [e for e in EMAIL_RE.findall(text) if not allowed_email(e)]
            if phones or mails:
                hit("HIGH", "S1", ln, "e-mail or mobile number in a public static file", text)
        elif phones and not is_test:
            # a full mobile number in this repo is a person's (the public biz tel is allowed in dummy_phone);
            # no name match needed - Korean text has a name-shaped word on almost every line
            hit("HIGH", "S3", ln, "full mobile number (a real person's) - mask it (010-****-1234) or pass it as "
                "an argument; never in docs/memo/scripts/code", text)

    if low.endswith(".py") and not is_test:
        hits += _routes(path, added, lines)
        hits += _session_flags(path, added, lines)
    if not is_test:
        hits += _infra(path, added, lines, content)
    return hits


def _routes(path, added, lines):
    hits = []
    for ln, text in added:
        m = ROUTE_RE.match(text)
        if not m:
            continue
        # decorator block: walk down to the def, then the body until the next top-level statement
        i = ln - 1
        decos = []
        while i < len(lines) and not re.match(r"\s*(async\s+)?def\s", lines[i]):
            decos.append(lines[i])
            i += 1
        body = []
        j = i + 1
        while j < len(lines) and j < i + 60:
            if lines[j].strip() and not lines[j].startswith((" ", "\t")):
                break
            body.append(lines[j])
            j += 1
        deco_txt = "\n".join(decos)
        body_txt = "\n".join(l for l in body if not l.strip().startswith("#"))
        limited = ("@limiter.limit" in deco_txt or "@limiter.shared_limit" in deco_txt
                   or bool(CUSTOM_LIMIT_RE.search(body_txt)))
        guarded = bool(GUARD_RE.search(body_txt) or GUARD_RE.search(deco_txt))
        write = m.group(2) in ("post", "put", "delete", "patch") or bool(WRITE_METHOD_RE.search(deco_txt))
        if guarded:
            continue   # logged-in route: the login check is the gate (INVARIANTS s4 asks @limiter of PUBLIC ones)
        if write and not limited:
            hits.append(("HIGH", "S4", path, ln, "public write route with no login check and no @limiter.limit", text))
        elif write:
            hits.append(("MED", "S4", path, ln, "public write route (rate-limited) - confirm it may run without login", text))
        elif not limited and SIDE_EFFECT_RE.search(body_txt):
            hits.append(("MED", "S4", path, ln, "public route that reads input/DB with no @limiter.limit", text))
    return hits


def _session_flags(path, added, lines):
    hits = []
    for ln, text in added:
        m = SESSION_TRUE_RE.search(text)
        if not m:
            continue
        lo, hi = max(0, ln - 7), min(len(lines), ln + 6)
        near = "\n".join(lines[lo:hi])
        if SESSION_TIME_RE.search(near):
            continue
        hits.append(("HIGH", "S5", path, ln, "session['%s'] = True with no time stamp - store the pass time and check "
                     "its age on read (a bare flag lives as long as the session cookie)" % m.group(1)[:40], text))
    return hits


def _infra(path, added, lines, content):
    hits = []
    low = path.lower()
    for ln, text in added:
        if low.endswith(".xml") and "HighestAvailable" in text:
            hits.append(("HIGH", "S6", path, ln, "scheduled task RunLevel HighestAvailable - use LeastPrivilege "
                         "(an elevated task skipped 18 processes at the 05:51 restart)", text))
        if low.endswith((".ps1", ".psm1")) and re.search(r"(?i)^\s*#requires\s+-runasadministrator", text):
            hits.append(("HIGH", "S6", path, ln, "#Requires -RunAsAdministrator - service processes run non-elevated", text))
    if low.endswith((".py", ".ps1", ".bat", ".cmd")) and not path.startswith(("docs/", "memo/")):
        refs = [(n, t) for n, t in added if OFFSITE_RE.search(t) and not re.match(r"\s*(#|//|::|rem\s)", t, re.I)]
        if refs and WRITE_OP_RE.search(content) and not AGE_USE_RE.search(content):
            ln = refs[0][0]
            hits.append(("HIGH", "S6", path, ln, "writes toward the offsite folder without age - offsite gets *.age only "
                         "(backup-encryption/age_encrypt.py encrypt_file)", dict(added).get(ln, "")))
    return hits


# ---------------------------------------------------------------- git plumbing
REMOTE_CRED_RE = re.compile(r"://[^/@\s]+@|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{16,}")


# I: repo root -> O: [(HIGH, S7, '.git/config', 0, why, fingerprint text)]   ⛔ never returns the URL itself
def remote_credential_hits(cwd=ROOT):
    try:
        out = _git(["config", "--get-regexp", r"^remote\..*\.(url|pushurl)$"], cwd=cwd)
    except Exception:   # noqa: BLE001 — not a repo / git missing: nothing to judge
        return []
    hits = []
    for line in out.splitlines():
        key, _, url = line.partition(" ")
        # ssh form user@host:path is fine (no secret); only scheme://<something>@ or a token-shaped string counts
        if REMOTE_CRED_RE.search(url):
            name = key.split(".")[1] if key.count(".") >= 2 else key
            hits.append(("HIGH", "S7", ".git/config", 0,
                         "git remote '%s' URL carries a credential - use a plain https URL + credential manager" % name,
                         key))
    return hits


def _added_from_diff(diff_text):
    """# I: `git diff -U0` text for one file -> O: [(lineno, text)] added lines"""
    res, n = [], 0
    for line in diff_text.splitlines():
        h = re.match(r"@@ -\S+ \+(\d+)", line)
        if h:
            n = int(h.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            res.append((n, line[1:]))
            n += 1
    return res


def _decode(b: bytes):
    """# I: blob bytes -> O: text, or None for binary/huge. UTF-16 with a BOM is text: Windows task XMLs are
       UTF-16 LE and often marked `binary` in .gitattributes (schtasks.exe rejects UTF-8)."""
    if len(b) > MAX_BYTES:
        return None
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", "replace")
    if b"\x00" in b[:8192]:
        return None
    return b.decode("utf-8", "replace")


def _added(diff_text, old_blob, content):
    """# I: `git diff -U0` text, old blob bytes (b"" if new), new text -> O: [(lineno, text)] added lines.
       git prints no hunks for `binary` paths (task XMLs) - then diff the decoded texts here."""
    if "@@" in diff_text or "Binary files" not in diff_text:
        return _added_from_diff(diff_text)
    old = (_decode(old_blob) or "").splitlines()
    new = content.splitlines()
    res = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            res += [(j + 1, new[j]) for j in range(j1, j2)]
    return res


def collect_staged():
    out = []
    for line in _git(["diff", "--cached", "--name-status", "-M", "--diff-filter=ACMR"]).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st, path = parts[0][0], parts[-1]
        if skip_path(path):
            continue
        content = _decode(_git_b(["show", ":" + path]))
        if content is None:
            continue
        old_path = parts[1] if st == "R" else path
        diff = _git(["diff", "--cached", "-U0", "-M", "--"] + ([old_path, path] if st == "R" else [path]))
        old_blob = b"" if st == "A" else _git_b(["show", "HEAD:" + old_path])
        out.append((path, _added(diff, old_blob, content), content, st == "A"))
    return out


def collect_range(rng):
    left, right = (rng.split("...") if "..." in rng else rng.split(".."))
    base = _git(["merge-base", left, right]).strip() if "..." in rng else left
    out = []
    for line in _git(["diff", "--name-status", "-M", "--diff-filter=ACMR", rng]).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st, path = parts[0][0], parts[-1]
        if skip_path(path):
            continue
        content = _decode(_git_b(["show", "%s:%s" % (right, path)]))
        if content is None:
            continue
        old_path = parts[1] if st == "R" else path
        args = ["diff", "-U0", "-M", rng, "--"] + ([old_path, path] if st == "R" else [path])
        old_blob = b"" if st == "A" else _git_b(["show", "%s:%s" % (base, old_path)])
        out.append((path, _added(_git(args), old_blob, content), content, st == "A"))
    return out


def collect_all():
    out = []
    for path in _git(["ls-files"]).splitlines():
        if not path or skip_path(path):
            continue
        try:
            content = _decode((ROOT / path).read_bytes())
        except OSError:
            continue
        if content is None:
            continue
        out.append((path, list(enumerate(content.splitlines(), 1)), content, True))
    return out


# ---------------------------------------------------------------- baseline
def fingerprint(rule, path, text):
    return "%s|%s|%s" % (rule, path, hashlib.sha1(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()[:16])


WHOLE_FILE_MIN = 20   # a file with this many hits of one rule is a data dump - baselined as "rule|path"


def load_baseline():
    """# I: - -> O: set of "rule|path|sha" line fingerprints and "rule|path" whole-file entries"""
    try:
        d = json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return set(d.get("fingerprints", [])) | {"%s|%s" % (r, p) for p, rules in d.get("files", {}).items() for r in rules}


def in_baseline(baseline, rule, path, text):
    # whole-file entries ("rule|path", data dumps) never cover S2: a secret added to a baselined dump later
    # must still block - only that exact line's fingerprint can carry an old secret hit
    if rule != "S2" and "%s|%s" % (rule, path) in baseline:
        return True
    return fingerprint(rule, path, text) in baseline


def marked_ok(content_lines, ln, rule=""):
    """# I: file lines, 1-based hit line, rule -> O: True when `security-ok: <reason>` sits on the hit line or the
       line above (S4: anywhere from the line above the first decorator down to the def line)."""
    last = ln - 1
    if rule == "S4":
        while last < len(content_lines) - 1 and not re.match(r"\s*(async\s+)?def\s", content_lines[last]):
            last += 1
    for k in range(ln - 2, last + 1):
        if 0 <= k < len(content_lines) and OK_MARK_RE.search(content_lines[k]):
            return True
    return False


def run(files, baseline):
    """# I: [(path, added, content, new_file)], baseline set -> O: (new_hits, skipped_marked, skipped_baseline)"""
    new, marked, based = [], 0, 0
    for path, added, content, is_new in files:
        clines = content.splitlines()
        for h in scan_file(path, added, content, new_file=is_new):
            sev, rule, p, ln, why, text = h
            if in_baseline(baseline, rule, p, text):
                based += 1
                continue
            if sev == "MED" and marked_ok(clines, ln, rule):
                marked += 1
                continue
            new.append(h)
    return new, marked, based


def main(argv=None):
    ap = argparse.ArgumentParser(description="security guard for agent-written commits (S1-S7)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true")
    g.add_argument("--range")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--write-baseline", action="store_true", help="with --all: record every current hit (owner only)")
    a = ap.parse_args(argv)

    files = collect_staged() if a.staged else collect_range(a.range) if a.range else collect_all()
    if a.write_baseline:
        if not a.all:
            print("[security] --write-baseline needs --all")
            return 2
        fps, whole = set(), {}
        for path, added, content, is_new in files:
            per_rule = {}
            for sev, rule, p, ln, why, text in scan_file(path, added, content, new_file=is_new):
                per_rule.setdefault(rule, set()).add(fingerprint(rule, p, text))
            for rule, f in per_rule.items():
                if len(f) >= WHOLE_FILE_MIN:
                    whole.setdefault(path, []).append(rule)
                else:
                    fps |= f
        BASELINE.write_text(json.dumps({
            "note": "security_scan.py baseline - hits that existed when the guard was added. "
                    "fingerprints = rule|path|sha1(line)[:16] (no text); files = data dumps with >= %d hits of a rule. "
                    "Shrink it by fixing; never grow it to get a commit through." % WHOLE_FILE_MIN,
            "files": {k: sorted(v) for k, v in sorted(whole.items())},
            "fingerprints": sorted(fps)}, indent=0, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
        print("[security] baseline written: %d fingerprint(s), %d whole file(s)" % (len(fps), len(whole)))
        return 0

    new, marked, based = run(files, load_baseline())
    new += remote_credential_hits()   # S7 — whole state every run (no baseline, no security-ok)
    order = {"HIGH": 0, "MED": 1}
    for sev, rule, path, ln, why, _ in sorted(new, key=lambda h: (order[h[0]], h[2], h[3])):
        print("%-4s %s %s:%s - %s [%s]" % (sev, rule, path.encode("ascii", "replace").decode(), ln or "-",
                                          why, SOURCES[rule]))
    highs = sum(1 for h in new if h[0] == "HIGH")
    meds = len(new) - highs
    print("[security] files=%d HIGH=%d MED=%d (baseline-skipped=%d, security-ok=%d)"
          % (len(files), highs, meds, based, marked))
    if new:
        print("[security] BLOCKED - fix every HIGH (no exceptions). A MED may stay only with a comment "
              "'security-ok: <reason>' on that line or the line above. Rules: security_scan.py header.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
