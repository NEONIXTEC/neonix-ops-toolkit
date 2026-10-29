// Calls POST /api/chat. Every failure becomes one cause-specific sentence: a message that says
// "network problem or server error" hides which of two different fixes the user needs.

export type ChatReply = { text: string; route: string; model: string };

export type FailureKind = "network" | "timeout" | "rate" | "invalid" | "server";

export const FAILURE_TEXT: Record<FailureKind, string> = {
  network: "서버에 연결하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 보내 주세요.",
  timeout: "답변이 60초 안에 오지 않았습니다. 잠시 후 다시 보내 주세요.",
  rate: "짧은 시간에 질문을 많이 보냈습니다. 1분 뒤에 다시 보내 주세요.",
  invalid: "질문이 비었거나 너무 깁니다. 2,000자 안으로 보내 주세요.",
  server: "서버 오류로 답변을 받지 못했습니다. 잠시 후 다시 보내 주세요.",
};

export class ChatError extends Error {
  readonly kind: FailureKind;
  constructor(kind: FailureKind) {
    super(FAILURE_TEXT[kind]);
    this.kind = kind;
  }
}

export type SendChat = (message: string, sessionId: string) => Promise<ChatReply>;

export function makeSendChat(fetchImpl: typeof fetch = fetch, timeoutMs = 60_000): SendChat {
  return async (message, sessionId) => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    let res: Response;
    try {
      res = await fetchImpl("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId }),
        signal: ctrl.signal,
      });
    } catch (e) {
      throw new ChatError(ctrl.signal.aborted ? "timeout" : "network");
    } finally {
      clearTimeout(timer);
    }
    if (res.status === 429) throw new ChatError("rate");
    if (res.status === 422) throw new ChatError("invalid");
    if (!res.ok) throw new ChatError("server");
    return (await res.json()) as ChatReply;
  };
}

export const sendChat: SendChat = makeSendChat();
