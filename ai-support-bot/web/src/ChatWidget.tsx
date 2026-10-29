import { useEffect, useReducer, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ChatError, FAILURE_TEXT, sendChat as defaultSend, type SendChat } from "./api";
import { chatReducer, initialState } from "./chatReducer";

export const SUGGESTIONS = ["알림 문구는 어디서 바꿔요?", "엑셀은 어디서 받아요?", "직원 권한은 몇 가지예요?"];

function newSessionId(): string {
  return Math.random().toString(36).slice(2, 12);
}

export function ChatWidget({ send = defaultSend }: { send?: SendChat }) {
  const [state, dispatch] = useReducer(chatReducer, initialState);
  const [draft, setDraft] = useState("");
  const sessionId = useRef(newSessionId());
  const listEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listEnd.current?.scrollIntoView?.({ block: "end" });
  }, [state.messages.length, state.sending]);

  async function ask(text: string) {
    const q = text.trim();
    if (!q || state.sending) return;
    dispatch({ type: "send", text: q });
    setDraft("");
    try {
      const r = await send(q, sessionId.current);
      dispatch({ type: "reply", text: r.text, route: r.route });
    } catch (e) {
      dispatch({ type: "fail", text: e instanceof ChatError ? e.message : FAILURE_TEXT.server });
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(draft);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {   // isComposing: Korean IME
      e.preventDefault();
      void ask(draft);
    }
  }

  return (
    <section className="chat" aria-label="상담 도우미">
      <header className="chat-head">
        <strong>상담 도우미</strong>
        <span className="chat-sub">샘플데스크 · 데모</span>
      </header>

      <div className="chat-list" role="log" aria-live="polite">
        {state.messages.length === 0 && (
          <div className="chat-empty">
            <p>무엇이든 물어보세요. 자주 묻는 질문:</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="chip" onClick={() => void ask(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {state.messages.map((m) => (
          <div key={m.id} className={`msg msg-${m.role}`} data-role={m.role}>
            {m.text}
          </div>
        ))}
        {state.sending && (
          <div className="msg msg-bot typing" aria-label="AI가 답변을 쓰는 중">
            <span /><span /><span />
          </div>
        )}
        <div ref={listEnd} />
      </div>

      <form className="chat-form" onSubmit={onSubmit}>
        <textarea
          aria-label="질문 입력"
          placeholder="질문을 입력하세요 (Enter 보내기, Shift+Enter 줄바꿈)"
          value={draft}
          maxLength={2000}
          rows={2}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" disabled={state.sending || !draft.trim()}>
          보내기
        </button>
      </form>
    </section>
  );
}
