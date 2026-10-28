# -*- coding: utf-8 -*-
"""
cache_bust.py - put a content hash into every static CSS/JS URL of a build output folder.

    python cache_bust.py <build_dir>          # rewrites files under <build_dir>/static and <build_dir>/templates
    python cache_bust.py <build_dir> --check  # only count, write nothing

    I: a build copy laid out as <build_dir>/static/... and <build_dir>/templates/... (never the source tree)
    O: references like  /static/js/app.js?v=20261001  ->  /static/js/app.js?v=20261001.m1a2b3c4d
       (the hand-written version stays, the content hash is appended; same content = same URL)

Why (first production deploy of the minified build):
    The server already sent the minified copies, but the browser dev tools still showed comments.
    Cloudflare had cached the old original for 7 days under the SAME `?v=` URL (max-age=604800) - HIT, Age 30h.
    `?v=` was a value humans bumped by hand, so the cache never learned the bytes had changed.
    With the copy's content hash in the URL, every content change is a new URL and walks past the CDN and
    browser caches by itself. The same trap bit the CSP migration: a JS file dynamically loading another JS
    file with an old `?v=` kept serving the inline-onclick version for a week.

Order matters, so it runs in three passes:
    1) CSS `@import url(x.css?v=)`   - relative paths resolved against that CSS file's folder
    2) JS strings '/static/...?v='   - after the CSS hashes are final
    3) templates                     - `url_for('static', filename='...') }}?v=` and '/static/...?v=' literals,
                                       after every static hash is final
Only references that already carry `?v=` (or end right at the quote after url_for) are touched -
`#fragment` and anything else is left alone. The production version also re-parses each rewritten
template and keeps the original when the Jinja token stream differs; that check is left out here.
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

_BUST_TPL_RE = re.compile(
    r"""(url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*['"]([^'"]+)['"][^)]*\)\s*\}\})(\?v=([\w.\-]+))?(?=(['"]))?""")
_BUST_ABS_RE = re.compile(r"""(/static/([\w./\-]+\.(?:css|js)))\?v=([\w.\-]+)""")
_BUST_CSS_RE = re.compile(r"""(url\(\s*['"]?)([^'")?\s]+\.css)\?v=([\w.\-]+)""")


def _content_ver(data: bytes) -> str:
    return "m" + hashlib.sha1(data).hexdigest()[:8]


def _bump(ver, h: str) -> str:
    return ("%s.%s" % (ver, h)) if ver else h


def cache_bust(outputs) -> int:
    """outputs = {"static/css/a.css": bytes, "templates/x.html": bytes, ...}, changed in place.
    Returns the number of rewritten references. A reference is hashed only when its target is in outputs."""
    count = 0

    def ver_of(rel):
        o = outputs.get(rel)
        return _content_ver(o) if o is not None else None

    def sub_abs(text):
        def f(m):
            nonlocal count
            h = ver_of("static/" + m.group(2))
            if not h:
                return m.group(0)
            count += 1
            return "%s?v=%s" % (m.group(1), _bump(m.group(3), h))
        return _BUST_ABS_RE.sub(f, text)

    # 1) CSS @import url(...?v=) - relative to that CSS file
    for rel in [r for r in outputs if r.startswith("static/") and r.endswith(".css")]:
        text = outputs[rel].decode("utf-8")
        base = rel.rsplit("/", 1)[0]

        def f(m, base=base, rel=rel):
            nonlocal count
            target = os.path.normpath(os.path.join(base, m.group(2))).replace("\\", "/")
            h = ver_of(target)
            if not h or target == rel:
                return m.group(0)
            count += 1
            return "%s%s?v=%s" % (m.group(1), m.group(2), _bump(m.group(3), h))
        new = _BUST_CSS_RE.sub(f, text)
        if new != text:
            outputs[rel] = new.encode("utf-8")
    # 2) '/static/...?v=' inside JS (after the CSS hashes are final)
    for rel in [r for r in outputs if r.startswith("static/") and r.endswith(".js")]:
        text = outputs[rel].decode("utf-8")
        new = sub_abs(text)
        if new != text:
            outputs[rel] = new.encode("utf-8")
    # 3) templates - only outside the Jinja tag (after `}}`), so the template logic is unchanged
    for rel in [r for r in outputs if r.startswith("templates/")]:
        text = outputs[rel].decode("utf-8")

        def g(m):
            nonlocal count
            h = ver_of("static/" + m.group(2))
            if not h or (m.group(3) is None and m.group(5) is None):
                return m.group(0)        # no ?v= and not right before the quote (#frag etc.) - leave it
            count += 1
            return "%s?v=%s" % (m.group(1), _bump(m.group(4), h))
        new = sub_abs(_BUST_TPL_RE.sub(g, text))
        if new != text:
            outputs[rel] = new.encode("utf-8")
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description="content-hash cache busting for a build copy")
    ap.add_argument("build_dir")
    ap.add_argument("--check", action="store_true", help="count only, write nothing")
    a = ap.parse_args()
    root = Path(a.build_dir)
    files = [p for sub in ("static", "templates") for p in (root / sub).rglob("*")
             if p.is_file() and p.suffix in (".css", ".js", ".html")]
    outputs = {p.relative_to(root).as_posix(): p.read_bytes() for p in files}
    before = dict(outputs)
    n = cache_bust(outputs)
    changed = [rel for rel in outputs if outputs[rel] != before[rel]]
    if not a.check:
        for rel in changed:
            tmp = root / (rel + ".tmp")
            tmp.write_bytes(outputs[rel])
            os.replace(tmp, root / rel)
    print("[cache_bust] %d reference(s) hashed in %d file(s)%s" % (n, len(changed), " (check only)" if a.check else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
