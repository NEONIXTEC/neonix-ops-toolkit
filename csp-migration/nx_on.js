// nx_on.js - 인라인 on*= 핸들러의 data-nx-* 위임 디스패처 (2026-10-23). CSP 'unsafe-inline' 제거 1단계.
//   배경·이관 절차: csp-migration/README.md (28개 화면, 410개 data-nx 키를 이 30줄 하나로 옮겼다).
//   ⚠ 등록된 핸들러는 data-nx-* 를 가진 아무 요소에서나 돈다 — 사용자 HTML 을 그리는 경로는 data-nx-* 속성을 지우고,
//     핸들러는 this 의 다른 속성(data-url 등)을 그대로 행동 인자로 믿지 않는다 (README "주의"). 템플릿의 onclick="코드" 를 data-nx-click="코드" 로 바꾸고, 각 화면 JS 가
//   window.NX_H["코드"] = function (event) { 코드 } 로 같은 글자의 키를 등록한다 (몸통 = 옛 핸들러 코드 그대로).
//   - document 캡처 단계에서 받아 target 부터 위로 올라가며 data-nx-<event> 를 가진 요소마다 H[키].call(요소, event) 한다.
//     캡처인 이유: 중간 요소의 stopPropagation() 이 있어도 옛 인라인 핸들러처럼 반드시 돈다 (모달 본문의 클릭 전파 차단 등).
//   - 옛 의미 보존: this = 요소, event = 이벤트, return false → preventDefault, 핸들러 안의 stopPropagation() 은 바깥 요소로 안 올라간다.
//   - 키가 없으면 console.warn 한 줄 — 템플릿과 등록이 어긋난 것. eval·new Function 은 안 쓴다 (CSP 가 막는다).
//   ⛔ 이 파일은 nonce 가 필요 없다(외부 파일). 새 화면은 on*= 대신 data-nx-click 과 자기 페이지 JS 의 H[키] 로 쓴다.
(function () {
    var H = window.NX_H = window.NX_H || {};
    var EVENTS = ['click', 'change', 'input', 'submit', 'keydown'];
    function dispatch(ev) {
        var attr = 'data-nx-' + ev.type;
        var el = ev.target;
        while (el && el.nodeType === 1) {
            var code = el.getAttribute(attr);
            if (code !== null) {
                var fn = H[code];
                if (typeof fn === 'function') {
                    if (fn.call(el, ev) === false) ev.preventDefault();
                } else if (window.console && console.warn) {
                    console.warn('[nx_on] no handler registered for', attr, code);
                }
                if (ev.cancelBubble) return;
            }
            el = el.parentNode;
        }
    }
    for (var i = 0; i < EVENTS.length; i++) document.addEventListener(EVENTS[i], dispatch, true);
})();
