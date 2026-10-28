# -*- coding: utf-8 -*-
"""
lint_eol.py - staged files must not mix line endings or flip them wholesale

    I: staged text files (pre-commit) or files given on the command line
    O: MIXED / FLIP / CRCR lines and BAD=<n>; exit 1 when BAD > 0
    Pairs with `.gitattributes: * -text` - git keeps every file's bytes, this keeps every file's style.

Why: .gitattributes keeps `* -text` (byte-preserving, 2026-08-11 lesson: forcing eol on the
whole repo rewrote thousands of lines). The price: nothing stops ONE file from collecting both
CRLF and LF lines when different tools/agents append to it. The next editor (Claude Edit,
Python text mode, Gemini/Codex) saves the whole file in one style and the commit shows every
minority line as changed (one index doc had 682 CRLF + 77 LF lines -> a 154-line diff for a
3-line change, over and over). Korean text had nothing to do with it.

Checks the STAGED blob (what will be committed), per staged text file:
  MIXED  both CRLF and LF lines                       -> block (fix: --fix PATH, then git add)
  FLIP   HEAD was all-CRLF and staged is all-LF, or the reverse -> block (whole-file rewrite;
         keep the file's existing style)
  CRCR   a "\\r\\r\\n" line ending -> block. Comes from reading with newline="" and writing
         with the Windows default (\\n -> \\r\\n): every CRLF line gets one more CR. Nothing else caught it
         (one sat in a doc for weeks).
HEAD-mixed -> staged-uniform is allowed (that is the fix).

Usage:
  python lint_eol.py            # check staged files (pre-commit)
  python lint_eol.py A B        # check these worktree files (same rules, FLIP against HEAD)
  python lint_eol.py --fix A B  # rewrite files in the worktree to their majority style (+ CRCR -> CRLF)
Output is ASCII only (cp949 consoles).
"""
import subprocess
import sys


def _style(b: bytes):
    """# I: file bytes -> O: (crlf_lines, lf_only_lines)"""
    crlf = b.count(b"\r\n")
    return crlf, b.count(b"\n") - crlf


def _blob(spec: str):
    r = subprocess.run(["git", "show", spec], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def _is_binary(b: bytes) -> bool:
    return b"\0" in b[:8000]


def _read(p: str):
    try:
        with open(p, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _git_normalizes(path: str) -> bool:
    """# I: 워크트리 경로 -> O: .gitattributes 가 text(=set|auto) 라 git 이 커밋 때 줄바꿈을 고쳐 넣는 파일인가
    (`text=auto eol=lf` 등). 그런 파일은 워크트리가 CRLF·섞임이어도 커밋은 LF 라 MIXED·FLIP 대상이 아니다."""
    r = subprocess.run(["git", "check-attr", "text", "--", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout.strip().endswith((": set", ": auto"))


def check(paths=None) -> int:
    """# I: 없음(스테이징된 파일) | 워크트리 경로들 -> O: 0 OK / 1 BAD"""
    if paths:
        items = [(p, _read(p)) for p in paths]
    else:
        names = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                               capture_output=True).stdout.decode("utf-8", "replace").split("\0")
        items = [(f, _blob(":" + f)) for f in filter(None, names)]
    bad = 0
    for f, new in items:
        if new is None or _is_binary(new):
            continue
        crlf, lf = _style(new)
        shown = f.encode("ascii", "backslashreplace").decode("ascii")
        crcr = new.count(b"\r\r\n")
        if crcr:
            print(f"[lint_eol] CRCR  {shown}: {crcr} line(s) end in CR CR LF. fix: python lint_eol.py --fix \"{shown}\"")
            bad += 1
            continue
        if paths and _git_normalizes(f):
            continue                    # 워크트리 모드: git 이 커밋 때 줄바꿈을 맞춰 넣는 파일
        if crlf and lf:
            print(f"[lint_eol] MIXED {shown}: {crlf} CRLF + {lf} LF lines. fix: python lint_eol.py --fix \"{shown}\" && git add it")
            bad += 1
            continue
        old = _blob("HEAD:" + f.replace("\\", "/"))
        if old is None or _is_binary(old):
            continue
        ocrlf, olf = _style(old)
        if (ocrlf and not olf and lf and not crlf) or (olf and not ocrlf and crlf and not lf):
            was, now = ("CRLF", "LF") if ocrlf else ("LF", "CRLF")
            if not (f.endswith(".bat") and now == "CRLF"):
                print(f"[lint_eol] FLIP  {shown}: whole file {was} -> {now} (every line shows as changed). keep {was}.")
                bad += 1
    print(f"[lint_eol] BAD={bad}")
    return 1 if bad else 0


def fix(paths) -> int:
    for p in paths:
        with open(p, "rb") as fh:
            b = fh.read()
        crcr = 0
        while b"\r\r\n" in b:          # CR CR LF -> CR LF (반만 고친 newline 이 남긴 것)
            crcr += b.count(b"\r\r\n")
            b = b.replace(b"\r\r\n", b"\r\n")
        crlf, lf = _style(b)
        if not (crlf and lf):
            if crcr:
                with open(p, "wb") as fh:
                    fh.write(b)
                print(f"[lint_eol] fixed {p} -> CR CR LF x{crcr} collapsed")
            else:
                print(f"[lint_eol] ok    {p}")
            continue
        body = b.replace(b"\r\n", b"\n")
        if crlf >= lf:
            body = body.replace(b"\n", b"\r\n")
        with open(p, "wb") as fh:
            fh.write(body)
        print(f"[lint_eol] fixed {p} -> {'CRLF' if crlf >= lf else 'LF'} ({min(crlf, lf)} lines changed)")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--fix"]:
        sys.exit(fix(sys.argv[2:]))
    sys.exit(check(sys.argv[1:] or None))
