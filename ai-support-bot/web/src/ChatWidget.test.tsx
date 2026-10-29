import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatError, FAILURE_TEXT, type ChatReply, type SendChat } from "./api";
import { ChatWidget, SUGGESTIONS } from "./ChatWidget";

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("ChatWidget", () => {
  it("shows suggestion chips before the first question", () => {
    render(<ChatWidget send={vi.fn()} />);
    for (const s of SUGGESTIONS) expect(screen.getByRole("button", { name: s })).toBeInTheDocument();
  });

  it("sends the typed question, shows the typing indicator, then the answer", async () => {
    const d = deferred<ChatReply>();
    const send = vi.fn<SendChat>(() => d.promise);
    const user = userEvent.setup();
    render(<ChatWidget send={send} />);

    await user.type(screen.getByLabelText("질문 입력"), "엑셀은 어디서 받아요?{Enter}");
    expect(send).toHaveBeenCalledWith("엑셀은 어디서 받아요?", expect.any(String));
    expect(screen.getByText("엑셀은 어디서 받아요?")).toHaveAttribute("data-role", "user");
    expect(screen.getByLabelText("AI가 답변을 쓰는 중")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "보내기" })).toBeDisabled();

    d.resolve({ text: "[통계] → [월별] → [엑셀]", route: "ai", model: "demo" });
    expect(await screen.findByText("[통계] → [월별] → [엑셀]")).toHaveAttribute("data-role", "bot");
    expect(screen.queryByLabelText("AI가 답변을 쓰는 중")).not.toBeInTheDocument();
  });

  it("shows the cause-specific sentence when the request fails", async () => {
    const send = vi.fn<SendChat>(() => Promise.reject(new ChatError("rate")));
    const user = userEvent.setup();
    render(<ChatWidget send={send} />);
    await user.click(screen.getByRole("button", { name: SUGGESTIONS[0] }));
    expect(await screen.findByText(FAILURE_TEXT.rate)).toHaveAttribute("data-role", "error");
  });

  it("styles a bot-side failure route as an error", async () => {
    const send = vi.fn<SendChat>(async () => ({ text: "AI 응답이 지연되고 있습니다.", route: "error:all_failed", model: "" }));
    const user = userEvent.setup();
    render(<ChatWidget send={send} />);
    await user.type(screen.getByLabelText("질문 입력"), "질문{Enter}");
    expect(await screen.findByText("AI 응답이 지연되고 있습니다.")).toHaveAttribute("data-role", "error");
  });

  it("does not send an empty question", async () => {
    const send = vi.fn<SendChat>();
    const user = userEvent.setup();
    render(<ChatWidget send={send} />);
    expect(screen.getByRole("button", { name: "보내기" })).toBeDisabled();
    await user.type(screen.getByLabelText("질문 입력"), "   {Enter}");
    expect(send).not.toHaveBeenCalled();
  });
});
