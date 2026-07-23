import type { Session } from "./types";

let activeSession: Session | null = null;

export function setActiveSession(session: Session | null) {
  activeSession = session;
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && activeSession) {
    headers.set("X-CSRF-Token", activeSession.csrf_token);
  }
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const code = body?.error?.code ?? "HTTP_ERROR";
    const message = body?.error?.message ?? `请求失败 (${response.status})`;
    if (response.status === 428) {
      const returnTo = `${location.pathname}${location.search}`;
      location.assign(`/api/v1/auth/reauth?return_to=${encodeURIComponent(returnTo)}`);
    }
    throw new ApiError(response.status, code, message);
  }
  return response.json() as Promise<T>;
}

export function idempotencyKey(): string {
  return crypto.randomUUID();
}

export function formatTime(value?: string): string {
  return value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "-";
}

export interface ServerEvent {
  id: number;
  event: string;
  data: Record<string, unknown>;
}

export async function streamServerEvents(
  path: string,
  after: number,
  onEvent: (event: ServerEvent) => void,
  signal: AbortSignal,
): Promise<number> {
  const separator = path.includes("?") ? "&" : "?";
  const response = await fetch(`${path}${separator}after=${after}`, {
    credentials: "include",
    headers: { Accept: "text/event-stream", "Last-Event-ID": String(after) },
    signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      body?.error?.code ?? "SSE_ERROR",
      body?.error?.message ?? `事件流连接失败 (${response.status})`,
    );
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let latest = after;
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (!block.startsWith(":")) {
        let id = latest;
        let event = "message";
        const data: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("id:")) id = Number(line.slice(3).trim());
          if (line.startsWith("event:")) event = line.slice(6).trim();
          if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
        }
        if (Number.isFinite(id) && data.length) {
          latest = id;
          onEvent({ id, event, data: JSON.parse(data.join("\n")) });
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  return latest;
}
