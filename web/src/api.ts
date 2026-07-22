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
