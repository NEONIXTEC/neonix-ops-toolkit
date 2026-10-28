# -*- coding: utf-8 -*-
"""
caddy_ctl.py — 앞문 Caddy 켜기·확인 (Caddyfile 과 짝)

왜: app.py 재시작은 단일 인스턴스 뮤텍스 때문에 "죽이고 다시 켜기" 밖에 안 된다 → 5~9초 동안 :80 이 비어
    현장 이용자들이 튕겼다. Caddy 가 :80 을 잡고 있으면 그 몇 초 동안 요청을 붙잡았다가 새 app.py 로 넘긴다
    (스테이징 실측 오류 18→0).

켜지는 조건: config/.env 에 APP_PORT(80 이 아닌 값)가 있을 때만. 없으면 전부 아무것도 안 한다 — 전환 전 운영은 그대로.
누가 부르나: 부팅 bat(부팅·05:51, app.py 보다 먼저) · 워치독(5분마다, 죽었으면 다시 켬).
⚠️ Caddy 는 부팅 bat 의 프로세스 목록에 넣지 않는다 — 05:51 재시작이 그 목록을 죽이는데,
   Caddy 는 앱이 재시작하는 동안 **살아 있어야** 하는 쪽이다.

배치: <repo>/scripts/caddy_ctl.py, <repo>/deploy/caddy/{Caddyfile, caddy.exe}, <repo>/config/.env, 로그 <repo>/logs/

    python scripts/caddy_ctl.py ensure    # 꺼져 있으면 켠다 (전환 전이면 no-op)
    python scripts/caddy_ctl.py status
    python scripts/caddy_ctl.py validate  # Caddyfile 문법 확인
출력은 ASCII (CP949 콘솔).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Tuple

REPO = Path(__file__).resolve().parents[1]
CADDY_DIR = REPO / "deploy" / "caddy"
CADDY_EXE = CADDY_DIR / "caddy.exe"          # git 미추적 — 서버에 따로 둔다
ENV_FILE = REPO / "config" / ".env"
LOG_DIR = REPO / "logs"
CADDYFILE = CADDY_DIR / "Caddyfile"
FRONT_PORT = 80


def app_port() -> int:
    """# O: app.py 가 여는 포트 (config/.env APP_PORT, 없으면 80 = Caddy 없이 예전 방식)"""
    val = os.getenv("APP_PORT", "")
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == "APP_PORT":
                val = v.strip().strip("\"'")   # 같은 키가 두 줄이면 아래 줄 (python-dotenv override 와 같다)
    except OSError:
        pass   # .env 를 못 읽으면 예전 방식으로 본다(아무것도 안 켠다)
    try:
        return int(val or "80")
    except ValueError:
        return 80


def enabled() -> bool:
    return app_port() != FRONT_PORT


def listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _env(front_port: int = FRONT_PORT) -> Dict[str, str]:
    env = dict(os.environ)
    env.update({"APP_PORT": str(app_port()), "FRONT_PORT": str(front_port),
                "LOG_DIR": str(LOG_DIR).replace("\\", "/")})
    return env


def validate() -> Tuple[bool, str]:
    if not CADDY_EXE.is_file():
        return False, f"caddy.exe missing: {CADDY_EXE}"
    r = subprocess.run([str(CADDY_EXE), "validate", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
                       env=_env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    last = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
    return r.returncode == 0, last[0][:200]


def _elevated() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def start(front_port: int = FRONT_PORT, wait_sec: float = 8.0) -> Tuple[bool, str]:
    """# O: (떴나, 사유). 콘솔 없이 분리 실행 — 부른 쪽(워치독·bat)이 끝나도 Caddy 는 산다.
    ⛔ 관리자 셸(윈도 OpenSSH·에이전트 셸은 늘 관리자, 실측)에서는 켜지 않는다 — High 로 뜨고 SSH 가 끊길 때
       같이 죽을 수 있다. 거기선 일반 권한 예약작업으로 다시 실행한다 (restart_one_medium.ps1 과 같은 방식)."""
    if not CADDY_EXE.is_file():
        return False, f"caddy.exe missing: {CADDY_EXE}"
    if _elevated():
        return False, "elevated shell - refusing; re-run it from a Medium (non-elevated) scheduled task"
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([str(CADDY_EXE), "run", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
                     cwd=str(CADDY_DIR), env=_env(front_port), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if listening(front_port):
            return True, f"listening on 127.0.0.1:{front_port} after {time.time() - t0:.1f}s"
        time.sleep(0.2)
    return False, f"not listening on :{front_port} after {wait_sec:.0f}s - see logs/caddy.log"


def ensure() -> Tuple[str, str]:
    """# O: (상태, 사유) 상태 = disabled | ok | started | failed. 이미 떠 있으면 아무것도 안 한다."""
    if not enabled():
        return "disabled", "APP_PORT not set (app.py serves :80 itself)"
    if listening(FRONT_PORT):
        return "ok", f":{FRONT_PORT} is up"
    ok, why = start()
    return ("started" if ok else "failed"), why


def status() -> Dict[str, object]:
    p = app_port()
    return {"enabled": p != FRONT_PORT, "app_port": p, "front_up": listening(FRONT_PORT),
            "app_up": listening(p), "caddy_exe": CADDY_EXE.is_file()}


def _cli(argv) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "ensure":
        st, why = ensure()
        print(f"[caddy] {st}: {why}")
        return 1 if st == "failed" else 0
    if cmd == "validate":
        ok, why = validate()
        print(f"[caddy] validate {'OK' if ok else 'FAILED'}: {why}")
        return 0 if ok else 1
    print("[caddy] " + " ".join(f"{k}={v}" for k, v in status().items()))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
