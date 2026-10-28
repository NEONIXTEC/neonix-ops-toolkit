# -*- coding: utf-8 -*-
"""
lint_commit_agent.py - every commit names who made it

    I: commit message file (githooks/commit-msg) or a range (pre-merge gate - catches --no-verify commits)
    O: exit 0 pass / exit 1 blocked + one correct example (ASCII only, cp949 consoles)
    No env bypass; only a real merge commit (MERGE_HEAD) is exempt.

Why: in a repo where several AI agents (Claude, Gemini, ...) and the human all commit, 373 of 874 commits
in two months had no Co-Authored-By line and nobody could tell an agent commit from the owner's own.
One trailer line settles it:

    Agent: Claude Opus 5.5
    Agent: Gemini 3.8 Flash
    Agent: human

The reasoning level was dropped later: 231 of 288 trailers said `(effort ?)` - agents mostly cannot see
it, so the field carried no information. Old `(<level>)` suffixes still pass.

The line must sit in the trailer block (the last paragraph, with Co-Authored-By etc.) because that is
what `git log --format='%h %(trailers:key=Agent,valueonly,separator=%x2C ) %s'` reads - git interpret-trailers decides.

Usage:
  python lint_commit_agent.py --commit-msg FILE          # githooks/commit-msg
  python lint_commit_agent.py --range master...staging   # pre-merge gate: non-merge commits in the range
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or os.getcwd()

LEGACY_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "effort ?")   # 받기만 한다 (예전 형식)
AGENT_RE = re.compile(r"^(human|[A-Za-z][A-Za-z0-9 .+_-]*[A-Za-z0-9]( \((%s)\))?)$"
                      % "|".join(re.escape(e) for e in LEGACY_EFFORTS))
EXAMPLE = "Agent: Claude Opus 5.5"
SCISSORS = re.compile(r"^# -+ >8 -+$")


def _git(args, stdin=b"") -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, input=stdin, capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def clean_message(raw: str) -> str:
    # I: message file text -> O: what git will store (comment lines and the `commit -v` diff dropped)
    out = []
    for line in raw.splitlines():
        if SCISSORS.match(line):
            break
        if not line.startswith("#"):
            out.append(line)
    return "\n".join(out).strip() + "\n"


def agent_values(message: str):
    # I: cleaned message -> O: values of every `Agent:` trailer git sees in the trailer block
    parsed = _git(["interpret-trailers", "--parse"], message.encode("utf-8"))
    vals = []
    for line in parsed.splitlines():
        key, sep, val = line.partition(":")
        if sep and key.strip().lower() == "agent":
            vals.append(val.strip())
    return vals


def problem(message: str):
    # I: cleaned message -> O: None | ASCII reason
    vals = agent_values(message)
    if not vals:
        if re.search(r"^Agent:", message, re.M | re.I):
            return "the 'Agent:' line is not in the trailer block (put it in the LAST paragraph, next to Co-Authored-By)"
        return "no 'Agent:' trailer"
    bad = [v for v in vals if not AGENT_RE.match(v)]
    if bad:
        return "bad 'Agent:' value %r" % bad[0].encode("ascii", "replace").decode()
    return None


HELP = """  Every commit ends with one trailer line naming who made it:
      %s
      Agent: Gemini 3.8 Flash
      Agent: human
  Model name only - no reasoning level.""" % EXAMPLE


def range_problems(rng: str):
    # I: "A...B" -> O: ["<sha> <reason>"] for every non-merge commit in B not in A (hook skipped with --no-verify)
    shas = _git(["rev-list", "--no-merges", rng.replace("...", "..")]).split()
    return ["%s %s" % (sha[:7], why) for sha in shas
            for why in [problem(_git(["log", "-1", "--format=%B", sha]))] if why]


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--commit-msg", metavar="FILE")
    g.add_argument("--range", metavar="A...B")
    a = ap.parse_args()

    if a.range:
        probs = range_problems(a.range)
        for p in probs:
            print("[agent] " + p)
        if probs:
            print(HELP)
            return 1
        print("[agent] OK")
        return 0

    merge_head = _git(["rev-parse", "--git-path", "MERGE_HEAD"]).strip()
    if merge_head and os.path.exists(os.path.join(ROOT, merge_head)):
        return 0  # real merge commit - the merged commits carry the trailer
    with open(a.commit_msg, encoding="utf-8", errors="replace") as f:
        msg = clean_message(f.read())
    why = problem(msg)
    if not why:
        return 0
    print("[agent] " + why)
    print(HELP)
    return 1


if __name__ == "__main__":
    sys.exit(main())
