import { describe, expect, it } from "vitest";
import { chatReducer, initialState } from "./chatReducer";

describe("chatReducer", () => {
  it("send -> reply keeps order, ids and the sending flag", () => {
    const s1 = chatReducer(initialState, { type: "send", text: "질문" });
    expect(s1.sending).toBe(true);
    const s2 = chatReducer(s1, { type: "reply", text: "답", route: "ai" });
    expect(s2.sending).toBe(false);
    expect(s2.messages.map((m) => [m.id, m.role, m.text])).toEqual([[1, "user", "질문"], [2, "bot", "답"]]);
  });

  it("an error route from the bot becomes an error message", () => {
    const s = chatReducer(initialState, { type: "reply", text: "x", route: "error:key_rejected" });
    expect(s.messages[0].role).toBe("error");
  });
});
