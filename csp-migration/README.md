# CSP `'unsafe-inline'` 제거

## 순서 (운영에서 실제로 밟은 것)

1. **Report-Only** 헤더부터: `Content-Security-Policy-Report-Only` 로 걸고, 위반 보고를 받는 라우트가 한 줄 로그로 남긴다(분당 상한·크기 상한·확장 프로그램 출처 버림). 일일 점검이 위반 수를 센다.
2. 인라인 `<script>` 는 `nonce="{{ csp_nonce }}"` 를 단다. 서버가 문자열로 돌려주는 작은 `<script>alert(...)</script>` 도 빠뜨리면 안 된다(실제로 두 곳이 남아 위반이 찍혔다).
3. 인라인 `onclick="코드"` 는 `data-nx-click="코드"` 로 바꾸고, 그 화면 JS 가 `window.NX_H["코드"] = function (event) { 코드 }` 로 **같은 글자의 키**를 등록한다. 디스패처가 [`nx_on.js`](nx_on.js) 30줄이다.
   - document **캡처** 단계에서 받는다: 중간 요소의 `stopPropagation()` 이 있어도 옛 인라인 핸들러처럼 반드시 돈다.
   - 옛 의미 보존: `this` = 요소, `return false` → `preventDefault`.
   - `eval`·`new Function` 을 안 쓴다(CSP 가 막는다). 키가 없으면 `console.warn` 한 줄.
   - 28개 화면, 410개 `data-nx` 키, 246개 등록을 교차 대조했다.
4. JS 가 그리는 마크업의 `onerror=` 같은 것도 남는다(`error` 는 버블하지 않아 위임으로 못 받는다) → 별도 캡처 리스너.
5. 위반 0 이 며칠 이어진 뒤 헤더 이름을 `Content-Security-Policy` 로.

## 주의: 위임은 "등록된 함수"만 부르지만, 아무 요소에서나 부른다

`eval` 이 없어서 임의 코드는 못 돈다. 하지만 HTML 주입이 하나라도 있으면 공격자가 `data-nx-click="삭제"` 를 단 요소를 만들어
**이미 등록된 핸들러**를 원하는 요소·속성으로 부를 수 있다(script gadget). 그래서:
- 사용자 입력을 HTML 로 그리는 경로(sanitizer, `innerHTML`)는 `data-nx-*` 속성을 지운다.
- 핸들러는 `this` 의 다른 속성(`data-url`, `data-id`)을 그대로 행동 인자로 쓰지 않는다 — 서버가 권한을 다시 본다.
- 더 조이려면 nonce 를 확인한 `data-nx-root` 조상 안의 요소만 디스패치한다.

## 캐시 함정 → [`cache_bust.py`](cache_bust.py)

바뀐 JS 를 배포했는데 이용자 브라우저엔 옛 파일(인라인 onclick 판)이 7일 동안 남았다. 다른 JS 가 그 파일을 **옛 `?v=`** 로 동적 로드하고 있었고, Cloudflare 는 같은 URL 이면 캐시를 준다.
손으로 올리는 `?v=` 대신 빌드 사본의 **내용 해시**를 URL 에 붙이면, 내용이 바뀔 때마다 URL 이 바뀌어 CDN·브라우저 캐시를 저절로 비켜 간다.
