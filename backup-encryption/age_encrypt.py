# -*- coding: utf-8 -*-
"""
age_encrypt.py — 오프사이트 백업 age 공개키 암호화 공용 모듈.

    I: 평문 백업 파일 + 공개키 파일(recipients, age1...) + age 실행 파일
    O: <이름>.age 암호문 (헤더·크기 확인이 끝난 것만 최종 이름으로)
    ⛔ 개인키(AGE-SECRET-KEY-)는 서버·저장소 어디에도 두지 않는다 — recipients 파일에 섞여 있으면 거부
    ⛔ 실패는 전부 AgeError — 호출부는 평문을 오프사이트로 내보내지 않는다 (fail-closed)

왜: 풀백업에는 .env·마스터 키·외부 연동 키·DB 덤프가 다 들어간다. 오프사이트 폴더를 동기화 클라이언트가
    클라우드로 올리는 구조에서는 클라우드 계정 하나가 곧 운영 비밀 전체다 — 그래서 클라우드엔 암호문만 간다.
    서버는 **잠그기만** 한다 — 공개키만 두고, 여는 개인키는 오너 PC 에 오너 비밀번호로 잠가 둔다.
    그래서 서버(또는 서버를 턴 사람)는 자기가 만든 백업도 못 연다. 복호화 검증은
    오너 PC 의 decrypt_backup.py 가 한다.

설정 (없으면 기본값):
    AGE_EXE                 age 실행 파일 경로  (기본: PATH 의 age)
    BACKUP_AGE_RECIPIENTS   공개키 파일 경로    (기본: <이 폴더>/backup_age_recipients.txt)

사용 예 (백업 스크립트 쪽):
    import age_encrypt
    age_encrypt.preflight()                                   # 덤프 만들기 전에 age·공개키 확인
    age_encrypt.encrypt_file(dump_path, offsite_dir / (dump_path.name + ".age"))
    # AgeError 면 평문을 오프사이트로 복사하지 말고 실패로 끝낸다
"""
import contextlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import List

DEFAULT_RECIPIENTS = Path(__file__).resolve().parent / "backup_age_recipients.txt"

AGE_HEADER = b"age-encryption.org/v1\n"
_ENCRYPT_TIMEOUT_SEC = 3600


class AgeError(RuntimeError):
    """암호화를 못 했다 — 호출부는 평문을 내보내지 말고 실패로 끝낸다."""


def find_age() -> str:
    """# I: (env AGE_EXE) -> O: age 실행 파일 경로. 없으면 AgeError."""
    cfg = (os.getenv("AGE_EXE") or "").strip().strip('"')
    if cfg:
        if not Path(cfg).is_file():
            raise AgeError("AGE_EXE not found: %s" % cfg)
        return cfg
    found = shutil.which("age")
    if not found:
        raise AgeError("age not installed (set AGE_EXE or put age on PATH)")
    return found


def recipients_path() -> Path:
    """# I: (env BACKUP_AGE_RECIPIENTS) -> O: 공개키 파일 경로 (존재 여부는 안 본다)"""
    cfg = (os.getenv("BACKUP_AGE_RECIPIENTS") or "").strip().strip('"')
    return Path(cfg) if cfg else DEFAULT_RECIPIENTS


def read_recipients(path: Path) -> List[str]:
    """# I: 공개키 파일 -> O: age1... 목록. 없음·빈 파일·개인키 섞임이면 AgeError.

    ⛔ 개인키가 들어 있으면 거부한다 — 그 파일이 서버에 있다는 것 자체가 사고다
       (서버가 열 수 있으면 서버를 턴 사람도 연다).
    """
    if not path.is_file():
        raise AgeError("age recipients file missing: %s" % path)
    keys: List[str] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if "AGE-SECRET-KEY-" in line.upper():
            raise AgeError("PRIVATE key found in recipients file %s - remove it from this machine" % path)
        if not line or line.startswith("#"):
            continue
        if not line.startswith("age1"):
            raise AgeError("not an age public key in %s: %s..." % (path, line[:12]))
        keys.append(line)
    if not keys:
        raise AgeError("no age public key in %s" % path)
    return keys


def preflight() -> None:
    """# I: - -> O: None. 백업을 만들기 전에 age·공개키를 확인한다 (10분짜리 tar 를 만들고 나서 실패하지 않게)."""
    find_age()
    read_recipients(recipients_path())


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    """테스트가 갈아끼우는 지점 — age 프로세스 실행 하나만 한다."""
    return subprocess.run(cmd, capture_output=True, timeout=_ENCRYPT_TIMEOUT_SEC)


def encrypt_file(src: Path, dst: Path) -> int:
    """# I: 평문 파일, 최종 암호문 경로 -> O: 암호문 바이트 수. 실패면 AgeError (dst 는 안 생긴다).

    <dst>.part 에 쓰고 → age 헤더 + 크기(평문보다 커야 함) 확인 → 그때만 dst 로 이름을 바꾼다.
    동기화 클라이언트가 절반짜리를 최종 이름으로 올리는 일이 없다.
    """
    age = find_age()
    rcp = recipients_path()
    read_recipients(rcp)
    src_size = src.stat().st_size
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    try:
        try:
            r = _run([age, "-R", str(rcp), "-o", str(part), str(src)])
        except (OSError, subprocess.SubprocessError) as e:
            raise AgeError("age run failed: %s: %s" % (type(e).__name__, e)) from e
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()[:300]
            raise AgeError("age rc=%s: %s" % (r.returncode, err))
        if not part.is_file():
            raise AgeError("age wrote no output")
        with open(part, "rb") as f:
            head = f.read(len(AGE_HEADER))
        if head != AGE_HEADER:
            raise AgeError("output is not an age file (header %r)" % head[:24])
        enc_size = part.stat().st_size
        if enc_size <= src_size:
            raise AgeError("ciphertext %d bytes <= plaintext %d bytes" % (enc_size, src_size))
        os.replace(part, dst)
        return enc_size
    finally:
        # 남은 .part 는 반쪽 암호문이라 새도 무해하고 prune·워치독이 세지도 않는다 — 지우기 실패로 원래 예외를 가리지 않는다
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)
