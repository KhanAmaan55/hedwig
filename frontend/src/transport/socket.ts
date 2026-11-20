/**
 * The live channel (docs/16 §5, §13): one WebSocket, replies token by token.
 *
 * Isolated in `transport/` like the REST client (docs/18 §4). Connects lazily, on the first send.
 * The API token travels as a subprotocol — browsers cannot set headers on a WebSocket, and a
 * query string ends up in logs (docs/16 §9). In browser development the page holds no token and
 * the Vite proxy authenticates the upgrade instead.
 */

import { ApiError, resolveBaseUrl, resolveTokenSource, type TokenSource } from "./client";
import type { TurnReply } from "./types";

export interface StreamHandlers {
  readonly onStarted?: (sessionId: string, turnId: string) => void;
  readonly onStage?: (stage: string, detail: string | null) => void;
  readonly onToken?: (text: string) => void;
}

export type StreamApi = Pick<HedwigStream, "send">;

interface Envelope {
  readonly type: string;
  readonly corr: string | null;
  readonly payload: Record<string, unknown>;
}

interface Pending {
  readonly resolve: (reply: TurnReply) => void;
  readonly reject: (error: Error) => void;
  readonly handlers: StreamHandlers;
  started: boolean;
}

type SocketFactory = (url: string, protocols: string[]) => WebSocket;

/** The socket could not be opened at all: nothing was sent, so REST can carry the message. */
export const UNREACHABLE = "stream_unreachable";
/** The socket closed after the turn began: the reply is saved server-side (docs/16 §9). */
export const DISCONNECTED = "stream_disconnected";
const UNAUTHORISED = 4401;
const KEEPALIVE_MS = 25_000;

export function streamUrl(baseUrl: string = resolveBaseUrl()): string {
  if (baseUrl) return `${baseUrl.replace(/^http/, "ws")}/v1/stream`;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/v1/stream`;
}

export class HedwigStream {
  private socket: WebSocket | null = null;
  private opening: Promise<WebSocket> | null = null;
  private readonly pending = new Map<string, Pending>();
  private readonly turns = new Map<string, string>();
  private keepalive = 0;
  private counter = 0;

  constructor(
    private readonly url: string = streamUrl(),
    private readonly tokenSource: TokenSource = resolveTokenSource(),
    private readonly factory: SocketFactory = (url, protocols) => new WebSocket(url, protocols),
  ) {}

  /** Send a message; resolves with the same reply REST returns, after the tokens. */
  async send(
    sessionId: string | null,
    text: string,
    handlers: StreamHandlers = {},
  ): Promise<TurnReply> {
    const socket = await this.open();
    const clientId = `c${Math.random().toString(36).slice(2, 8)}-${++this.counter}`;
    return new Promise<TurnReply>((resolve, reject) => {
      this.pending.set(clientId, { resolve, reject, handlers, started: false });
      socket.send(
        JSON.stringify({
          type: "message.send",
          payload: {
            text,
            client_message_id: clientId,
            ...(sessionId ? { session_id: sessionId } : {}),
          },
        }),
      );
    });
  }

  close(): void {
    this.socket?.close();
  }

  private open(): Promise<WebSocket> {
    if (this.socket?.readyState === WebSocket.OPEN) return Promise.resolve(this.socket);
    this.opening ??= this.connect(false).finally(() => {
      this.opening = null;
    });
    return this.opening;
  }

  private async connect(refreshToken: boolean): Promise<WebSocket> {
    const token = await this.tokenSource(refreshToken);
    const protocols = token ? ["hedwig.v1", `hedwig.token.${token}`] : ["hedwig.v1"];
    const socket = this.factory(this.url, protocols);

    const opened = await new Promise<boolean | "unauthorised">((resolve) => {
      socket.onopen = () => {
        resolve(true);
      };
      socket.onclose = (event) => {
        resolve(event.code === UNAUTHORISED ? "unauthorised" : false);
      };
      socket.onerror = () => {
        resolve(false);
      };
    });
    if (opened === "unauthorised" && !refreshToken) return this.connect(true);
    if (opened !== true) {
      throw new ApiError(UNREACHABLE, "The live connection could not be opened.", 0, null, true);
    }

    this.socket = socket;
    socket.onmessage = (event: MessageEvent<string>) => {
      this.receive(JSON.parse(event.data) as Envelope);
    };
    socket.onclose = () => {
      this.closed();
    };
    window.clearInterval(this.keepalive);
    this.keepalive = window.setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping" }));
    }, KEEPALIVE_MS);
    return socket;
  }

  private receive(envelope: Envelope): void {
    const { type, payload, corr } = envelope;
    const clientId =
      typeof payload.client_message_id === "string"
        ? payload.client_message_id
        : corr
          ? this.turns.get(corr)
          : undefined;
    const pending = clientId ? this.pending.get(clientId) : undefined;
    if (!pending || !clientId) return;

    switch (type) {
      case "turn.started": {
        const turnId = String(payload.turn_id);
        this.turns.set(turnId, clientId);
        pending.started = true;
        pending.handlers.onStarted?.(String(payload.session_id), turnId);
        break;
      }
      case "turn.stage":
        pending.handlers.onStage?.(
          String(payload.stage),
          typeof payload.detail === "string" ? payload.detail : null,
        );
        break;
      case "token":
        pending.handlers.onToken?.(String(payload.text));
        break;
      case "message.complete":
        this.pending.delete(clientId);
        pending.resolve(payload as unknown as TurnReply);
        break;
      case "error":
        this.pending.delete(clientId);
        pending.reject(
          new ApiError(
            String(payload.code),
            String(payload.message),
            0,
            null,
            Boolean(payload.retryable),
            (payload.detail as Record<string, unknown> | undefined) ?? {},
          ),
        );
        break;
      case "turn.completed":
        if (corr) this.turns.delete(corr);
        break;
      default:
        break;
    }
  }

  private closed(): void {
    window.clearInterval(this.keepalive);
    this.socket = null;
    for (const [clientId, pending] of this.pending) {
      pending.reject(
        new ApiError(
          pending.started ? DISCONNECTED : UNREACHABLE,
          pending.started
            ? "The connection dropped. HEDWIG's reply is saved; it will be here when you reload."
            : "The live connection closed before the message was sent.",
          0,
          null,
          true,
        ),
      );
      this.pending.delete(clientId);
    }
    this.turns.clear();
  }
}

export const stream = new HedwigStream();
