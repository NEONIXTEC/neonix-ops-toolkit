import { describe, expect, it } from "vitest";
import { ChatError, FAILURE_TEXT, makeSendChat, type FailureKind } from "./api";

function respond(status: number, body: unknown = {}): typeof fetch {
  return (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;
}

async function kindOf(p: Promise<unknown>): Promise<FailureKind | "ok"> {
  try {
    await p;
    return "ok";
  } catch (e) {
    return (e as ChatError).kind;
  }
}

describe("sendChat", () => {
  it("returns the server reply", async () => {
    const send = makeSendChat(respond(200, { text: "답", route: "ai", model: "demo" }));
    await expect(send("q", "s")).resolves.toEqual({ text: "답", route: "ai", model: "demo" });
  });

  it("maps each failure to its own cause", async () => {
    expect(await kindOf(makeSendChat(respond(429))("q", "s"))).toBe("rate");
    expect(await kindOf(makeSendChat(respond(422))("q", "s"))).toBe("invalid");
    expect(await kindOf(makeSendChat(respond(500))("q", "s"))).toBe("server");
    const offline = (async () => { throw new TypeError("Failed to fetch"); }) as typeof fetch;
    expect(await kindOf(makeSendChat(offline)("q", "s"))).toBe("network");
  });

  it("times out instead of waiting forever", async () => {
    const hang = ((_url: RequestInfo | URL, init?: RequestInit) =>
      new Promise((_res, rej) => init?.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError"))))) as typeof fetch;
    expect(await kindOf(makeSendChat(hang, 20)("q", "s"))).toBe("timeout");
  });

  it("never joins two causes in one sentence", () => {
    for (const text of Object.values(FAILURE_TEXT)) expect(text).not.toContain("또는");
  });
});
