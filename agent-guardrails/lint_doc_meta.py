# -*- coding: utf-8 -*-
"""
lint_doc_meta.py - code commits must carry their metadata docs

    I: staged files (pre-commit) / commit message file (commit-msg) / a range (pre-merge gate)
    O: exit 0 pass / exit 1 blocked + what to fix (ASCII only, cp949 consoles)
    No env bypass - if there is really nothing to document, put `Docs-skip: <reason>` in the commit message.

Why: the owner had to say "update the docs too" after every task. Agents (Claude, Gemini, Codex) forget;
a git hook does not. Three rules:

  HEADER  a NEW module (.py under CODE_ROOTS) needs a `[Module Spec]` block whose `SSOT:` line names at
          least one .md doc; a NEW .js/.css needs an `SSOT: <doc>.md` line in its first 15 lines.
          The block is what lets the next agent find the rules before it edits the file.
  ANCHOR  a NEW app module must be findable from the anchor index doc (its file stem appears there);
          scripts from any doc.
  DOCS    a commit that changes code but stages no .md is blocked, unless the message has a
          `Docs-skip: <reason>` line (typo fix, pure refactor, ...). Merge/revert commits are exempt.

Configuration (env, optional):
    DOCMETA_CODE_ROOTS   comma separated code folders   (default: app/,scripts/)
    DOCMETA_ANCHORS      anchor index doc               (default: docs/ANCHORS.md)

Usage:
  python lint_doc_meta.py --staged              # pre-commit: HEADER + ANCHOR on staged new files
  python lint_doc_meta.py --commit-msg FILE     # commit-msg: DOCS
  python lint_doc_meta.py --range master...staging   # pre-merge gate: all three over the range

Example header:
    \"\"\"
    [Module Spec]
    - SSOT: docs/BILLING.md
    - Anchor: docs/ANCHORS.md "Invoice export"
    - Pipeline I/O: I: month, tenant id -> O: CSV bytes
    - Constraints: never reads another tenant's rows
    \"\"\"
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or os.getcwd()

CODE_ROOTS = tuple(r.strip().rstrip("/") + "/" for r in os.environ.get("DOCMETA_CODE_ROOTS", "app/,scripts/").split(",")
                   if r.strip())
CODE_EXT = (".py", ".js", ".css", ".html", ".ps1", ".bat")
HEADER_EXT = (".py", ".js", ".css")
SKIP_PARTS = ("/tests/", "/build/", "/node_modules/", "/vendor/", "/migrations/", "/__pycache__/")
SKIP_NAMES = ("__init__.py", "conftest.py")
ANCHORS = os.environ.get("DOCMETA_ANCHORS", "docs/ANCHORS.md")
SKIP_TRAILER = re.compile(r"^Docs-skip:[ \t]*\S", re.M)
SPEC_LINES = 60
JS_HEAD_LINES = 15


def _git(*args, check=True) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if check and r.returncode != 0:
        raise SystemExit("[doc-meta] git %s failed: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace")[:200]))
    return r.stdout.decode("utf-8", "replace")


def is_code(path: str) -> bool:
    # I: repo-relative path -> O: production code that should carry docs
    p = "/" + path
    return (path.startswith(CODE_ROOTS) and path.endswith(CODE_EXT) and ".min." not in path
            and not any(s in p for s in SKIP_PARTS) and os.path.basename(path) not in SKIP_NAMES
            and not os.path.basename(path).endswith("_test.py"))


def needs_header(path: str) -> bool:
    return is_code(path) and path.endswith(HEADER_EXT)


def header_problem(path: str, text: str):
    # I: path, file text -> O: None | short reason
    lines = text.splitlines()
    if path.endswith(".py"):
        head = lines[:SPEC_LINES]
        if not any("[Module Spec]" in l for l in head):
            return "no [Module Spec] block in the first %d lines" % SPEC_LINES
        if not any("SSOT" in l and ".md" in l for l in head):
            return "[Module Spec] has no 'SSOT: <doc>.md' line"
        return None
    head = lines[:JS_HEAD_LINES]
    if not any("SSOT" in l and ".md" in l for l in head):
        return "no 'SSOT: <doc>.md' comment in the first %d lines" % JS_HEAD_LINES
    return None


def anchor_scope(path: str):
    # I: path -> O: (label, predicate on doc path) - where this module must be mentioned
    if "scripts/" in path:
        return "any doc", lambda d: d.endswith(".md")
    return ANCHORS, lambda d: d == ANCHORS


def stem_in(stem: str, text: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(stem), text) is not None


class Source:
    """Where file contents come from: the index (pre-commit) or a commit (range)."""

    def __init__(self, rev=None):
        self.rev = rev   # None = staged index
        self._docs = None

    def read(self, path: str) -> str:
        spec = (":" + path) if self.rev is None else ("%s:%s" % (self.rev, path))
        r = subprocess.run(["git", "show", spec], cwd=ROOT, capture_output=True)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", "replace")
        try:
            with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def docs(self):
        if self._docs is None:
            out = _git("ls-files") if self.rev is None else _git("ls-tree", "-r", "--name-only", self.rev)
            self._docs = [d for d in out.splitlines() if d.endswith(".md")]
        return self._docs


def check_new_files(new_files, src: Source):
    # I: added paths, Source -> O: [problem lines]
    probs = []
    cache = {}
    for path in new_files:
        if not needs_header(path):
            continue
        why = header_problem(path, src.read(path))
        if why:
            probs.append("HEADER %s: %s" % (path, why))
        stem = os.path.splitext(os.path.basename(path))[0]
        label, pred = anchor_scope(path)
        found = False
        for d in src.docs():
            if not pred(d):
                continue
            if d not in cache:
                cache[d] = src.read(d)
            if stem_in(stem, cache[d]):
                found = True
                break
        if not found:
            probs.append("ANCHOR %s: '%s' is not mentioned in %s" % (path, stem, label))
    return probs


def docs_problem(names, message: str):
    # I: changed paths, commit message -> O: None | reason
    code = [n for n in names if is_code(n)]
    if not code or any(n.endswith(".md") for n in names):
        return None
    if SKIP_TRAILER.search(message or ""):
        return None
    return "code changed (%s%s) but no .md is in this commit" % (", ".join(code[:3]), " ..." if len(code) > 3 else "")


HELP = """
  What to update: the ANCHORS section . INVARIANTS/PITFALLS if a rule or an incident . README map if
  files/ports/processes moved . both CLAUDE.md and AGENTS.md if an agent rule changed.
  New module header: [Module Spec] with SSOT / Anchor / Pipeline I/O / Constraints (example in this file's docstring).
  Nothing to document? add a line to the commit message:  Docs-skip: <reason>"""


def _report(probs) -> int:
    if not probs:
        print("[doc-meta] OK")
        return 0
    for p in probs:
        print("[doc-meta] " + p)
    print(HELP)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true")
    g.add_argument("--commit-msg", metavar="FILE")
    g.add_argument("--range", metavar="A...B")
    a = ap.parse_args()

    if a.staged:
        new = _git("diff", "--cached", "--name-only", "--diff-filter=A").splitlines()
        return _report(check_new_files(new, Source()))

    if a.commit_msg:
        if os.path.exists(os.path.join(ROOT, _git("rev-parse", "--git-path", "MERGE_HEAD").strip())):
            return 0
        with open(a.commit_msg, encoding="utf-8", errors="replace") as f:
            msg = "\n".join(l for l in f.read().splitlines() if not l.startswith("#"))
        if msg.lstrip().startswith(("Revert ", "Merge ")):
            return 0
        names = _git("diff", "--cached", "--name-only", "--diff-filter=ACMRD").splitlines()
        why = docs_problem(names, msg)
        return _report(["DOCS " + why] if why else [])

    head = a.range.partition("...")[2]
    head = head or "HEAD"
    new = _git("diff", "--name-only", "--diff-filter=A", a.range).splitlines()
    probs = check_new_files(new, Source(head))
    names = _git("diff", "--name-only", "--diff-filter=ACMRD", a.range).splitlines()
    msgs = _git("log", "--format=%B", a.range.replace("...", ".."))
    why = docs_problem(names, msgs)
    if why:
        probs.append("DOCS " + why)
    return _report(probs)


if __name__ == "__main__":
    sys.exit(main())
