import { describe, expect, it } from "vitest";

import { DISCONNECTED, HedwigStream, UNREACHABLE, streamUrl } from "./socket";

/** A WebSocket the test drives from the server side. */
class FakeSocket {
  static readonly OPEN = 1;
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(
    readonly url: string,
    readonly protocols: string[],
  ) {}

  send(data: string): void {
    this.sent.push(JSON.parse(data) as Record<string, unknown>);
  }
  close(): void {
    this.readyState = 3;
    this.onclose?.({ code: 1000 });
  }
  // -- server side --
  accept(): void {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }
  reject(code: number): void {
    this.onclose?.({ code });
  }
  push(type: string, payload: Record<string, unknown>, corr: string | null = null): void {
    this.onmessage?.({ data: JSON.stringify({ v: 1, id: "e", type, ts: 0, corr, payload }) });
  }
}

function harness(tokens: (string | null)[] = ["tok"]) {
  const sockets: FakeSocket[] = [];
  const asked: boolean[] = [];
  Object.assign(globalThis, { WebSocket: FakeSocket });
  const stream = new HedwigStream(
    "ws://h/v1/stream",
    (refresh) => {
      asked.push(refresh);
      return Promise.resolve(tokens.shift() ?? null);
    },
    (url, protocols) => {
      const socket = new FakeSocket(url, protocols);
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
  );
  return { stream, sockets, asked };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("HedwigStream", () => {
  it("asks the server to stop a reply by its turn", async () => {
    const { stream, sockets } = harness();
    void stream.send(null, "hi");
    await tick();
    const socket = sockets[0];
    if (!socket) throw new Error("no socket");
    socket.accept();
    await tick();
    stream.interrupt("turn_1");
    expect(socket.sent.at(-1)).toEqual({ type: "turn.interrupt", payload: { turn_id: "turn_1" } });
  });

  it("pushes the avatar's expression once subscribed, and says when it is lost", async () => {
    const { stream, sockets } = harness();
    const seen: string[] = [];
    let lost = false;
    const watching = stream.watchMind(
      (intent) => seen.push(intent.action),
      () => {
        lost = true;
      },
    );
    await tick();
    const socket = sockets[0];
    if (!socket) throw new Error("no socket");
    socket.accept();
    await watching;
    expect(socket.sent).toEqual([{ type: "mind.subscribe" }]);

    socket.push("mind.update", { action: "thinking" });
    socket.push("mind.update", { action: "idle" });
    expect(seen).toEqual(["thinking", "idle"]);

    socket.close();
    expect(lost).toBe(true);
  });

  it("says which conversation is on screen and hands over what HEDWIG says first", async () => {
    const { stream, sockets } = harness();
    const said: string[] = [];
    let lost = false;
    const watching = stream.watchInitiative(
      "sess_1",
      (message) => said.push(message.text),
      () => {
        lost = true;
      },
    );
    await tick();
    const socket = sockets[0];
    if (!socket) throw new Error("no socket");
    socket.accept();
    await watching;
    expect(socket.sent).toEqual([
      { type: "initiative.subscribe", payload: { session_id: "sess_1" } },
    ]);

    stream.showing(null);
    expect(socket.sent.at(-1)).toEqual({
      type: "initiative.subscribe",
      payload: { session_id: null },
    });

    socket.push("initiative.message", { text: "You asked me to find out…", offer_id: "ini_1" });
    expect(said).toEqual(["You asked me to find out…"]);

    socket.close();
    expect(lost).toBe(true);
  });

  it("offers the token as a subprotocol, never in the URL", async () => {
    const { stream, sockets } = harness();
    void stream.send(null, "hi");
    await tick();
    expect(sockets[0]?.protocols).toEqual(["hedwig.v1", "hedwig.token.tok"]);
    expect(sockets[0]?.url).toBe("ws://h/v1/stream");
  });

  it("streams a turn: started, stage, tokens, then the reply", async () => {
    const { stream, sockets } = harness();
    const seen: string[] = [];
    const reply = stream.send(null, "hi", {
      onStarted: (session) => seen.push(`started ${session}`),
      onStage: (stage) => seen.push(`stage ${stage}`),
      onToken: (text) => seen.push(`token ${text}`),
    });
    await tick();
    const socket = sockets[0];
    if (!socket) throw new Error("no socket");
    socket.accept();
    await tick();
    const clientId = (socket.sent[0]?.payload as { client_message_id: string }).client_message_id;

    socket.push("turn.started", { turn_id: "t1", session_id: "s1", client_message_id: clientId });
    socket.push("turn.stage", { stage: "composing" }, "t1");
    socket.push("token", { text: "Hel" }, "t1");
    socket.push("token", { text: "lo" }, "t1");
    socket.push("message.complete", { reply: "Hello", client_message_id: clientId }, "t1");

    expect((await reply).reply).toBe("Hello");
    expect(seen).toEqual(["started s1", "stage composing", "token Hel", "token lo"]);
  });

  it("asks for a fresh token once after a 4401, which is how a rotation reaches an open app", async () => {
    const { stream, sockets, asked } = harness(["old", "new"]);
    void stream.send(null, "hi");
    await tick();
    sockets[0]?.reject(4401);
    await tick();
    expect(asked).toEqual([false, true]);
    expect(sockets[1]?.protocols).toContain("hedwig.token.new");
  });

  it("says it is unreachable when it cannot open, so REST can carry the message", async () => {
    const { stream, sockets } = harness();
    const reply = stream.send(null, "hi");
    await tick();
    sockets[0]?.reject(1006);
    await expect(reply).rejects.toMatchObject({ code: UNREACHABLE });
  });

  it("says the reply is saved when the connection drops mid-turn", async () => {
    const { stream, sockets } = harness();
    const reply = stream.send(null, "hi");
    await tick();
    const socket = sockets[0];
    if (!socket) throw new Error("no socket");
    socket.accept();
    await tick();
    const clientId = (socket.sent[0]?.payload as { client_message_id: string }).client_message_id;
    socket.push("turn.started", { turn_id: "t1", session_id: "s1", client_message_id: clientId });
    socket.close();
    await expect(reply).rejects.toMatchObject({ code: DISCONNECTED });
  });

  it("finds the stream through the page's own host in browser development", () => {
    expect(streamUrl("")).toMatch(/^ws:\/\/.+\/v1\/stream$/);
    expect(streamUrl("http://127.0.0.1:8730")).toBe("ws://127.0.0.1:8730/v1/stream");
  });
});
