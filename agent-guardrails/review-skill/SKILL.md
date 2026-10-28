---
name: incident-review
description: >-
  Use to review a change before commit/merge ("review this", "is this safe to merge", "check my diff").
  Checks the staging diff against the incidents this repo actually had - INVARIANTS, PITFALLS and the hookify
  rules - and reports a severity-ordered table where every finding cites its source doc.
---

# Incident-based review (structure)

이 스킬은 "일반론 코드 리뷰" 대신 **이 레포가 실제로 겪은 사고 기록**을 기준으로 리뷰하게 만드는 틀이다.
운영 레포의 원본은 표가 60줄이 넘는다. 여기엔 뼈대와 예시 몇 줄만 남겼다.

SSOT: `docs/INVARIANTS.md` (지켜야 할 것), `docs/PITFALLS.md` (사고 기록), `CLAUDE.md`, `.claude/hookify.*.local.md`.
Tool: `review_scan.py` (이 폴더).
**근거 문서에 없는 지적은 의견이다** - `opinion` 으로 표시하고 절대 `HIGH` 로 올리지 않는다.

## 1. Mechanical pass

```bash
python review_scan.py                 # merge-base(master) -> working tree + untracked
python review_scan.py --base HEAD     # uncommitted only
python security_scan.py --range master...staging
```
`review_scan` 은 `.claude/hookify.*.local.md` 의 **file** 규칙 패턴을 그 파일에서 직접 읽어(복사하지 않는다)
추가된 줄에만 적용하고, 그 밖에 줄바꿈 뒤집힘, `.bat` 비 CRLF, BOM 없는 비ASCII `.ps1`, U+FFFD(인코딩 깨짐),
naive `datetime.now()`, `[Module Spec]` 없는 새 `.py` 를 잡는다. HIGH 가 하나라도 있으면 exit 1.

## 2. Judgment pass - 정규식이 못 잡는 것

Severity: **HIGH** = 돈, 보안, 데이터 유실, 운영 중단 · **MED** = 기록된 사고의 재발 · **LOW** = 관례.

| Sev | Check | Source |
|---|---|---|
| HIGH | 웹서버를 무중단 재시작 스크립트가 아닌 방법으로 죽이거나, 관리자 셸에서 프로세스를 띄움 | INVARIANTS "never elevated" · PITFALLS "05:51 restart skipped 18" |
| HIGH | 세션에 비밀값(인증번호·토큰) 저장, 시간 없는 "인증됨" 플래그 | INVARIANTS "session" |
| HIGH | 요청의 id 를 바꿔 넣으면 남의 데이터가 열림 (읽기·쓰기 둘 다 소유자 확인) | INVARIANTS "permission scope" |
| HIGH | 외부로 나가는 새 경로(SMS·메일·외부 API)가 스테이징 차단 스위치를 안 거침 | INVARIANTS "staging blocks outbound" |
| MED | GET 핸들러가 디스크를 훑거나 커밋함 | INVARIANTS "GET is read-only" |
| MED | 중복 방지 상태를 메모리에만 둬서 재시작마다 0 으로 돌아감 | PITFALLS "memory cap reset on restart" |
| MED | `except: pass` | hookify `warn-except-pass` |
| LOW | 경로 하드코딩 (`paths.py` 를 거치지 않음) | CLAUDE.md "paths" |

## 2-1. Security judgment - 스캐너가 못 보는 것

| Sev | Area | Ask |
|---|---|---|
| HIGH | Auth flow | 새 로그인·코드·토큰 경로: 세션 발급 시각, 서버 쪽 코드 보관, OAuth state nonce, 같은 사이트로만 리다이렉트 |
| HIGH | Public writes | 공개 POST 에 IP 당 제한 + 돈이 들거나 운영자를 부르는 경우 전체 상한 |
| HIGH | 개인정보 목적·최소화 | 새 개인정보 필드마다 왜 필요한지, 누가 보는지, 마스킹, 로그·에러 응답에 안 나가는지 |
| HIGH | 위탁·국외 이전 고지 = 실제 | 개인정보를 받는 새 외부 서비스는 **같은 커밋에서** 개인정보처리방침에 들어간다 |

## 3. Report

한 장의 표, HIGH 먼저. 모든 줄에 `file:line` 과 위 표의 근거.

```text
[결론] 머지 가능 / HIGH N건 먼저 고쳐야 함
| 심각도 | 항목 | 위치 | 근거 | 고칠 것 |
[기계 검사] review_scan HIGH=.. MED=.. (명령 그대로)
[못 본 것] 화면 캡처 안 봄 / 운영 데이터 필요 등 (없으면 '없음')
```
리뷰는 스스로 커밋·머지하지 않는다.
