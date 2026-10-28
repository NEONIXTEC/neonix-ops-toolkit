# -*- coding: utf-8 -*-
r"""
decrypt_backup.py — 오프사이트 age 암호문 백업을 오너 PC 에서 열어 보고 검증한다.

    I: 오프사이트 암호문(.tar.gz.age / .dump.age / .zip.age) + 오너 개인키 파일
    O: 복호화 → 끝까지 읽기 검증 결과 (기본은 평문을 지운다, --keep-plain 이면 남긴다)
    ⛔ 오너 PC 전용 — 서버엔 개인키가 없어서 못 돈다(그게 정상)
    ⛔ 개인키·비밀번호를 인자·로그·화면에 찍지 않는다 (비밀번호는 age 가 직접 묻는다)
    ⛔ print 는 ASCII 만 (cp949 콘솔)

서버는 공개키로 잠그기만 해서 자기가 만든 백업을 못 연다. 그래서 "복호화가 된다"는
오너 PC 에서만 확인할 수 있다 — 월 1회 복원 연습에 쓴다.

    python decrypt_backup.py -i <개인키 파일> <x.tar.gz.age>      # 복원 연습: 풀고 검증하고 지운다
    python decrypt_backup.py -i <개인키 파일> --latest <폴더>      # 폴더의 최신 풀백업 + DB 덤프 + zip
    python decrypt_backup.py -i <개인키 파일> <x.age> --keep-plain --out <폴더>   # 실제 복구용

개인키 파일이 `age -p` 로 잠겨 있으면 age 가 비밀번호를 직접 묻는다 (이 스크립트는 비밀번호를 안 본다).
개인키 만들기:  age-keygen | age -p > owner.key.age   (공개키 줄 age1... 만 서버의 recipients 파일로)

검증:
    .tar.gz → tarfile 로 끝까지 읽기 (모든 멤버를 실제로 읽는다)
    .dump   → pg_restore --list 가 목차를 끝까지 읽는지 (PATH 에 pg_restore 필요)
    .zip    → zipfile.testzip (멤버 CRC 까지)
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import age_encrypt as _age  # noqa: E402


def _latest(d: Path, pattern: str) -> Optional[Path]:
    files = sorted(d.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def decrypt(age_exe: str, identity: Path, src: Path, dst: Path) -> None:
    """# I: age 경로, 개인키 파일, 암호문, 평문 출력 경로 -> O: None. 실패면 AgeError.
    stdin·stderr 를 그대로 물려준다 — 잠긴 개인키면 age 가 콘솔에서 비밀번호를 묻는다."""
    r = subprocess.run([age_exe, "-d", "-i", str(identity), "-o", str(dst), str(src)])
    if r.returncode != 0 or not dst.is_file():
        if dst.exists():
            dst.unlink()
        raise _age.AgeError("age -d rc=%s for %s" % (r.returncode, src.name))


def verify_plain(plain: Path) -> Tuple[bool, str]:
    """# I: 복호화된 평문 -> O: (통과?, 한 줄 요약)"""
    if plain.name.endswith(".tar.gz"):
        import tarfile
        try:
            n = 0
            with tarfile.open(plain, "r:gz") as tf:
                for m in tf:                 # 압축을 끝까지 풀어 읽는다 (잘린 파일이면 여기서 예외)
                    if m.isfile():
                        f = tf.extractfile(m)
                        while f and f.read(1 << 20):
                            pass
                    n += 1
        except Exception as e:  # noqa: BLE001 - 끝까지 못 읽음 = 실패
            return False, "archive unreadable %s: %s" % (plain.name, e)
        return (n > 0), "archive OK %s members=%d" % (plain.name, n)
    if plain.name.endswith(".dump"):
        try:
            r = subprocess.run(["pg_restore", "--list", str(plain)], capture_output=True, timeout=600)
        except (OSError, subprocess.SubprocessError) as e:
            return False, "pg_restore not runnable: %s" % e
        tables = sum(1 for ln in r.stdout.decode("utf-8", "replace").splitlines() if " TABLE DATA " in ln)
        if r.returncode != 0 or not tables:
            return False, "dump unreadable %s (rc=%s tables=%d)" % (plain.name, r.returncode, tables)
        return True, "dump OK %s tables=%d" % (plain.name, tables)
    if plain.name.endswith(".zip"):
        import zipfile
        try:
            with zipfile.ZipFile(plain) as zf:
                bad = zf.testzip()           # 모든 멤버 CRC 까지 읽는다
                n = len(zf.namelist())
        except Exception as e:  # noqa: BLE001
            return False, "zip unreadable %s: %s" % (plain.name, e)
        if bad or not n:
            return False, "zip broken %s: %s" % (plain.name, bad or "empty")
        return True, "zip OK %s members=%d" % (plain.name, n)
    return False, "unknown backup type %s" % plain.name


def main() -> int:
    ap = argparse.ArgumentParser(description="Decrypt and verify offsite .age backups (owner PC only)")
    ap.add_argument("-i", "--identity", required=True, help="age private key file (may be passphrase-protected with age -p)")
    ap.add_argument("files", nargs="*", help=".tar.gz.age / .dump.age / .zip.age files")
    ap.add_argument("--latest", help="dir: newest full archive, DB dump and zip (.age)")
    ap.add_argument("--out", help="output dir for the plaintext (default: a temp dir)")
    ap.add_argument("--keep-plain", action="store_true", help="keep the decrypted file (real restore)")
    args = ap.parse_args()

    targets: List[Path] = [Path(f) for f in args.files]
    if args.latest:
        d = Path(args.latest)
        for pat in ("*.tar.gz.age", "*.dump.age", "*.zip.age"):
            p = _latest(d, pat)
            if p:
                targets.append(p)
    if not targets:
        ap.error("nothing to decrypt")
    if args.keep_plain and not args.out:
        ap.error("--keep-plain needs --out (the temp dir is deleted)")

    identity = Path(args.identity)
    if not identity.is_file():
        print("[DECRYPT] FAIL identity file not found: %s" % identity)
        return 1
    try:
        age_exe = _age.find_age()
    except _age.AgeError as e:
        print("[DECRYPT] FAIL %s" % e)
        return 1

    ok_all = True
    with tempfile.TemporaryDirectory(prefix="decrypt-") as tmp:
        out_dir = Path(args.out) if args.out else Path(tmp)
        out_dir.mkdir(parents=True, exist_ok=True)
        for src in targets:
            if not src.name.endswith(".age"):
                print("[DECRYPT] FAIL not an .age file: %s" % src.name)
                ok_all = False
                continue
            plain = out_dir / src.name[:-len(".age")]
            try:
                decrypt(age_exe, identity, src, plain)
            except _age.AgeError as e:
                print("[DECRYPT] FAIL %s" % e)
                ok_all = False
                continue
            ok, msg = verify_plain(plain)
            print("[DECRYPT] %s %s" % ("OK  " if ok else "FAIL", msg))
            ok_all = ok_all and ok
            if args.keep_plain:
                print("[DECRYPT] kept plaintext: %s  (may contain secrets - delete after restore)" % plain)
            else:
                plain.unlink()
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
