---
name: warn-heredoc-file-patch
enabled: true
event: bash
conditions:
  - field: command
    operator: regex_match
    pattern: python[^\n]*<<
  - field: command
    operator: regex_match
    pattern: \.replace\(|\.write\(|\.write_text\(|\.write_bytes\(
---
⚠️ 셸 heredoc 파이썬으로 파일을 고치려 함 — 문자열 속 `\` 가 한 번 더 풀려 `C:\N…`·`\n` 이 깨진다(하루에 6번 깨진 날이 있었다). 텍스트 치환은 Edit 도구, 긴 코드는 Write 로 파일을 만든 뒤 실행, 경로는 `r"..."`.
