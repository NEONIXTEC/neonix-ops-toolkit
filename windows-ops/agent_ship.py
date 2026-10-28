# -*- coding: utf-8 -*-
"""
agent_ship.py - 에이전트가 오너 승인 후 배포·재시작을 대신 누르는 손.

    python agent_ship.py deploy     # staging == master · 미커밋 0 확인 → 오너가 쓰는 [배포] bat 을 새 콘솔로
    python agent_ship.py restart    # 서버 Caddy front_up=True 확인 → [재시작] bat → 바깥 /healthz 200
    python agent_ship.py restart-proc worker.py collector.py   # 하나씩, 일반 권한 (restart_one_medium.ps1) → 끝에 audit
    python agent_ship.py audit      # 읽기 전용 권한 점검 (승인 불필요)

설계 이유:
    - 관리자 셸에서 띄운 프로세스가 하나라도 있으면 일반 권한 일일 재시작이 18개 프로세스를 통째로 건너뛴다.
      → 이 스크립트는 관리자 셸이면 아예 거부하고, 서버 쪽 재시작은 일반 권한 예약작업으로만 탄다.
    - 에이전트가 배포 스크립트를 자기 셸 안에서 직접 돌리면 SSH 세션 핸들·프로세스 그룹이 얽힌다.
      → 오너가 바탕화면에서 누르는 것과 **같은 bat** 을 새 콘솔·새 프로세스 그룹으로 한 번 띄우고 끝날 때까지 기다린다.
    - 재시작 직전에 앞문(Caddy)이 죽어 있으면 그 몇 초 동안 이용자가 튕긴다 → front_up=True 가 아니면 멈춘다.

사람 쪽 규칙 (코드가 아니라 운영 약속):
    ⛔ 오너가 그 배포·재시작을 **그 턴에** 채팅에서 승인한 뒤에만 부른다 (지난 승인 재사용 금지).
       승인이 없으면 에이전트는 머지까지만 하고 오너에게 넘긴다.
    마지막 줄 = 판정 (`[agent_ship] OK ...` | `[agent_ship] STOP ...`)

설정 (env):
    OPS_SERVER        ssh 대상, 예: deploy@203.0.113.10 (필수: restart / restart-proc / audit)
    OPS_SERVER_ROOT   서버의 레포 경로 (기본 C:\\app)
    OPS_HEALTHZ_URL   바깥에서 보는 헬스체크 주소, 예: https://example.com/healthz
    OPS_BAT_DIR       [배포]·[재시작] bat 이 있는 로컬 폴더 (기본 <레포>/scripts/dev)
"""
import argparse
import ctypes
import datetime
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or os.getcwd()
SERVER = os.environ.get("OPS_SERVER", "")
SERVER_ROOT = os.environ.get("OPS_SERVER_ROOT", "C:\\app")
HEALTHZ_URL = os.environ.get("OPS_HEALTHZ_URL", "")
BAT_DIR = os.environ.get("OPS_BAT_DIR") or os.path.join(ROOT, "scripts", "dev")
BATS = {"deploy": os.path.join(BAT_DIR, "deploy.bat"),
        "restart": os.path.join(BAT_DIR, "restart_app.bat")}
LOG_DIR = os.path.join(ROOT, "cache", "agent_ship")


def _say(msg):
    print("[agent_ship] " + msg, flush=True)


def _stop(msg):
    _say("STOP " + msg)
    sys.exit(2)


def _elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:   # noqa: BLE001 - 윈도가 아니면 판단 못 함 = 막는다
        return True


def _run(cmd, timeout=120):
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=timeout)
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")


def _need_server():
    if not SERVER:
        _stop("OPS_SERVER 가 비어 있다 (예: deploy@203.0.113.10)")


def _precheck_deploy():
    _c, ahead = _run(["git", "rev-list", "--count", "master..staging"])
    _c, behind = _run(["git", "rev-list", "--count", "staging..master"])
    _c, dirty = _run(["git", "status", "--porcelain"])
    if ahead.strip() != "0" or behind.strip() != "0" or dirty.strip():
        _stop("staging == master, 미커밋 0 이어야 한다 (ahead=%s behind=%s dirty=%d)"
              % (ahead.strip(), behind.strip(), len(dirty.splitlines())))
    _say("git OK (staging == master, 미커밋 0)")


def _precheck_restart():
    _need_server()
    _code, out = _run(["ssh", SERVER, "C:\\Windows\\py.exe %s\\scripts\\caddy_ctl.py status" % SERVER_ROOT], timeout=60)
    if "front_up=True" not in out:
        _stop("Caddy front_up=True 가 아니다 - 지금 재시작하면 사용자가 튕긴다:\n" + out[-600:])
    _say("Caddy front_up=True")


def _launch(kind):
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, "%s_%s.log" % (kind, stamp))
    feed_path = os.path.join(LOG_DIR, "_enter.txt")
    with open(feed_path, "w", encoding="ascii") as f:
        f.write("\r\n" * 4)          # bat 의 pause 를 넘긴다 (재시작 bat 중간 pause = 위 front_up 확인이 대신한다)
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    _say("%s 시작 - 로그 %s" % (kind, os.path.relpath(log_path, ROOT)))
    with open(feed_path, "rb") as fin, open(log_path, "wb") as fout:
        p = subprocess.Popen(["cmd.exe", "/d", "/c", BATS[kind]], cwd=os.path.dirname(BATS[kind]),
                             stdin=fin, stdout=fout, stderr=subprocess.STDOUT, creationflags=flags)
        try:
            rc = p.wait(timeout=900)
        except subprocess.TimeoutExpired:
            _stop("15분 안에 안 끝났다 - 창/로그를 오너가 확인 (%s)" % log_path)
    raw = open(log_path, "rb").read()
    try:                               # 재시작 bat 은 chcp 65001, 배포 bat 의 git/ssh 출력은 CP949 일 수 있다
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp949", "replace")
    tail = "\n".join(text.strip().splitlines()[-25:])
    print(tail, flush=True)
    return rc, text


def _ssh_ps(script_rel, args="", timeout=300):
    """# I: 서버 스크립트 상대경로, 인자 -> O: (rc, 출력) — ssh 출력은 그대로 보여 준다 (post-quantum 경고 줄은 뺀다)"""
    _need_server()
    cmd = 'powershell -NoProfile -ExecutionPolicy Bypass -File %s\\%s %s' % (SERVER_ROOT, script_rel, args)
    p = subprocess.run(["ssh", SERVER, cmd], cwd=ROOT, capture_output=True, timeout=timeout)
    out = (p.stdout or b"") + (p.stderr or b"")
    try:
        text = out.decode("utf-8")
    except UnicodeDecodeError:
        text = out.decode("cp949", "replace")
    keep = [ln for ln in text.splitlines()
            if not any(w in ln for w in ("post-quantum", "store now, decrypt later", "openssh.com/pq", "CLIXML", "<Objs "))]
    print("\n".join(keep).strip(), flush=True)
    return p.returncode, text


def _audit():
    rc, text = _ssh_ps("windows-ops\\integrity_audit.ps1", timeout=120)
    m = re.search(r"total=(\d+) bad=(\d+)", text)
    if rc != 0 or not m or m.group(2) != "0" or int(m.group(1)) == 0:
        _stop("관리자 권한(또는 세션 0) 프로세스가 있거나 점검이 아무것도 못 봤다 - '!!' 줄과 total 을 본다")
    _say("OK audit - 전 프로세스 일반 권한(Medium), 바탕화면 세션 1")


def _restart_proc(names):
    if not names:
        _stop("재시작할 스크립트 이름이 없다 (예: restart-proc worker.py)")
    for n in names:
        if not re.fullmatch(r"[\w\-]+\.py", n, re.ASCII):   # 원격 셸 명령줄에 그대로 들어간다 - 이름 모양만 통과
            _stop("스크립트 이름 모양이 아니다: %r" % n)
        if n.lower() == "app.py":
            _stop("app.py 는 restart-proc 이 아니라 `agent_ship.py restart` (대기 인계 재시작)")
    for n in names:
        _say("restart-proc %s" % n)
        rc, text = _ssh_ps("windows-ops\\restart_one_medium.ps1", "-Script %s" % n, timeout=240)
        if rc != 0 or "[restart_one] OK" not in text:
            _stop("%s 재시작 실패 - 남은 것은 건드리지 않았다" % n)
    _audit()


def _healthz():
    if not HEALTHZ_URL:
        _stop("OPS_HEALTHZ_URL 이 비어 있다")
    try:
        # 기본 Python-urllib UA 는 Cloudflare 가 403 으로 막는다 (첫 실행 때 실측) — curl 과 같은 UA
        req = urllib.request.Request(HEALTHZ_URL, headers={"User-Agent": "curl/8.9.1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except Exception as e:   # noqa: BLE001
        _say("healthz 실패: %s" % e)
        return 0


# 서버 배포 스크립트가 마지막에 찍는 판정 줄. 줄 **머리**로만 본다:
# 배포 bat 이 매번 "[deploy] if it said RESTART REQUIRED, ..." 안내를 찍어서, 본문 아무 데서나
# 찾으면 다른 프로세스만 바뀐 배포도 RESTART REQUIRED 로 나왔다 (두 번 연속 오탐).
_DEPLOY_VERDICTS = (
    ("[deploy] RESTART REQUIRED", "RESTART REQUIRED (app.py 재시작 필요 - 오너 승인 뒤 agent_ship.py restart)"),
    ("[deploy] app.py: no restart needed", "OTHER PROCESS (app.py 재시작 불필요 - 바뀐 프로세스만 restart-proc)"),
    ("[deploy] LIVE RELOADED", "LIVE RELOADED (재시작 불필요)"),
)


def _deploy_verdict(text: str) -> str:
    """deploy 로그 -> 서버 판정 한 줄. 못 찾으면 '?'."""
    for line in text.splitlines():
        s = line.strip()
        for head, verdict in _DEPLOY_VERDICTS:
            if s.startswith(head):
                return verdict
    return "?"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 진입점이 콘솔 인코딩을 책임진다
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=sorted(list(BATS) + ["restart-proc", "audit"]))
    ap.add_argument("names", nargs="*", help="restart-proc: 재시작할 런처 스크립트 이름")
    a = ap.parse_args()
    if _elevated():
        _stop("관리자 권한 셸이다 - 일반 권한 셸에서 다시")
    if a.kind == "audit":
        _audit()
        return
    if a.kind == "restart-proc":
        _restart_proc(a.names)
        _say("OK restart-proc 끝 - %s" % ", ".join(a.names))
        return
    if not os.path.isfile(BATS[a.kind]):
        _stop("bat 이 없다: %s" % BATS[a.kind])
    if a.kind == "deploy":
        _precheck_deploy()
    else:
        _precheck_restart()
    rc, text = _launch(a.kind)
    if "FAILED" in text or rc not in (0, None):
        _stop("%s 실패 (rc=%s) - 위 로그 끝을 본다" % (a.kind, rc))
    if a.kind == "deploy":
        _say("OK deploy 끝 - 서버 판정: %s" % _deploy_verdict(text))
    else:
        time.sleep(3)
        code = _healthz()
        if code != 200:
            _stop("재시작 뒤 바깥 /healthz = %s" % code)
        _say("OK restart 끝 - 바깥 /healthz 200")


if __name__ == "__main__":
    main()
