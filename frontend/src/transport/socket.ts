/**
 * The live channel (docs/16 §5, §13): one WebSocket, replies token by token.
 *
 * Isolated in `transport/` like the REST client (docs/18 §4). Connects lazily, on the first send.
 * The API token travels as a subprotocol — browsers cannot set headers on a WebSocket, and a
 * query string ends up in logs (docs/16 §9). In browser development the page holds no token and
 * the Vite proxy authenticates the upgrade instead.
 */

import { ApiError, resolveBaseUrl, resolveTokenSource, type TokenSource } from "./client";
import type { ExpressionIntent, InitiativeMessage, TurnReply } from "./types";

export interface StreamHandlers {
  readonly onStarted?: (sessionId: string, turnId: string) => void;
  readonly onStage?: (stage: string, detail: string | null) => void;
  readonly onToken?: (text: string) => void;
}

export type StreamApi = Pick<HedwigStream, "send"> &
  Partial<Pick<HedwigStream, "interrupt" | "watchInitiative" | "showing">>;
export type MindStream = Pick<HedwigStream, "watchMind">;

type MindHandler = (intent: ExpressionIntent) => void;
type InitiativeHandler = (message: InitiativeMessage) => void;

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
  private readonly mindHandlers = new Set<MindHandler>();
  private readonly mindLost = new Set<() => void>();
  private mindSubscribed = false;
  private readonly initiativeHandlers = new Set<InitiativeHandler>();
  private readonly initiativeLost = new Set<() => void>();
  private showingSession: string | null = null;

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

  /** Stop a reply mid-sentence (VOI-03). The turn still completes, saved as far as it got. */
  interrupt(turnId: string): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "turn.interrupt", payload: { turn_id: turnId } }));
    }
  }

  /**
   * Have the avatar's expression pushed as it changes (EMO-02), instead of polling for it.
   * Resolves once subscribed; `onLost` is called if the socket closes, so the caller can fall
   * back to polling. Returns an unsubscribe function.
   */
  async watchMind(handler: MindHandler, onLost: () => void = () => undefined): Promise<() => void> {
    const socket = await this.open();
    this.mindHandlers.add(handler);
    this.mindLost.add(onLost);
    if (!this.mindSubscribed) {
      socket.send(JSON.stringify({ type: "mind.subscribe" }));
      this.mindSubscribed = true;
    }
    return () => {
      this.mindHandlers.delete(handler);
      this.mindLost.delete(onLost);
    };
  }

  /**
   * Say the app is open and which conversation it shows, so HEDWIG may speak first into it
   * (ADR-0026). `onLost` is called if the socket closes. Returns an unsubscribe function.
   */
  async watchInitiative(
    sessionId: string | null,
    handler: InitiativeHandler,
    onLost: () => void = () => undefined,
  ): Promise<() => void> {
    const socket = await this.open();
    this.initiativeHandlers.add(handler);
    this.initiativeLost.add(onLost);
    this.showingSession = sessionId;
    this.sendShowing(socket);
    return () => {
      this.initiativeHandlers.delete(handler);
      this.initiativeLost.delete(onLost);
    };
  }

  /** The conversation on screen changed (or there is none: "New conversation"). */
  showing(sessionId: string | null): void {
    this.showingSession = sessionId;
    if (this.socket?.readyState === WebSocket.OPEN && this.initiativeHandlers.size > 0) {
      this.sendShowing(this.socket);
    }
  }

  private sendShowing(socket: WebSocket): void {
    socket.send(
      JSON.stringify({
        type: "initiative.subscribe",
        payload: { session_id: this.showingSession },
      }),
    );
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
    if (type === "mind.update") {
      for (const handler of this.mindHandlers) handler(payload as unknown as ExpressionIntent);
      return;
    }
    if (type === "initiative.message") {
      for (const handler of this.initiativeHandlers) {
        handler(payload as unknown as InitiativeMessage);
      }
      return;
    }
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
    this.mindSubscribed = false;
    for (const lost of this.mindLost) lost();
    this.mindLost.clear();
    this.mindHandlers.clear();
    for (const lost of this.initiativeLost) lost();
    this.initiativeLost.clear();
    this.initiativeHandlers.clear();
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
