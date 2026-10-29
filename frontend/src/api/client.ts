/**
 * The only place the dashboard talks to the network. Every call goes to our
 * FastAPI middleware (never to the vendor directly). In dev, Vite proxies
 * /api and /health to it; see vite.config.ts.
 *
 * The signed-in user's vendor token rides along as a Bearer header on every
 * call. A 401 on an authenticated call means the token is dead (expired or
 * revoked; there is no refresh), so the session is cleared and the route
 * guards send the user back to sign in.
 */

import { clearSession, getSession } from "@/auth/session";

/** The middleware's context path (its CONTEXT_PATH), e.g. "/las-api"; "" = root. */
const apiPath = (import.meta.env.VITE_API_BASE_PATH ?? "").trim().replace(/^\/+|\/+$/g, "");
const API_ROOT = apiPath ? `/${apiPath}` : "";

export const API_BASE = `${API_ROOT}/api/v1`;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body: unknown,
    /** The vendor's machine code, e.g. INVALID_CREDENTIALS_TO_AUTHENTICATE_USER. */
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** A Blob/File body is sent raw with its own type (uploads); anything else as JSON. */
type RequestOptions = Omit<RequestInit, "body"> & { body?: unknown };

export async function request<T>(path: string, { body, headers, ...init }: RequestOptions = {}): Promise<T> {
  const token = getSession()?.token;
  const raw = body instanceof Blob;
  const contentType = raw ? body.type || "application/octet-stream" : "application/json";
  const res = await fetch(path.startsWith("/") ? `${API_ROOT}${path}` : `${API_BASE}/${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(body !== undefined ? { "Content-Type": contentType } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: raw ? body : body !== undefined ? JSON.stringify(body) : undefined,
  });

  const text = await res.text();
  const data: unknown = text ? safeJson(text) : null;

  if (!res.ok) {
    if (res.status === 401 && token) clearSession();
    throw new ApiError(res.status, errorMessage(data, res), data, errorCode(data));
  }
  return data as T;
}

/** Vendor errors arrive as { error: { code, message } }; validation errors as { detail }. */
function errorMessage(data: unknown, res: Response): string {
  const body = data as { error?: { message?: unknown }; detail?: unknown } | null;
  if (typeof body?.error?.message === "string") return body.error.message;
  if (typeof body?.detail === "string") return body.detail;
  return res.statusText || `Request failed (${res.status})`;
}

function errorCode(data: unknown): string | undefined {
  const code = (data as { error?: { code?: unknown } } | null)?.error?.code;
  return typeof code === "string" ? code : undefined;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export const api = {
  get: <T>(path: string, init?: RequestOptions) => request<T>(path, { ...init, method: "GET" }),
  post: <T>(path: string, body?: unknown, init?: RequestOptions) => request<T>(path, { ...init, method: "POST", body }),
  put: <T>(path: string, body?: unknown, init?: RequestOptions) => request<T>(path, { ...init, method: "PUT", body }),
  patch: <T>(path: string, body?: unknown, init?: RequestOptions) => request<T>(path, { ...init, method: "PATCH", body }),
  delete: <T>(path: string, init?: RequestOptions) => request<T>(path, { ...init, method: "DELETE" }),
};

/**
 * Fetches a file from an authenticated endpoint (a plain link would not
 * carry the bearer token). `name` comes from Content-Disposition, if sent.
 */
export async function fetchFile(path: string): Promise<{ blob: Blob; name: string | null }> {
  const token = getSession()?.token;
  const res = await fetch(`${API_BASE}/${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) {
    const text = await res.text();
    const data = text ? safeJson(text) : null;
    if (res.status === 401 && token) clearSession();
    throw new ApiError(res.status, errorMessage(data, res), data, errorCode(data));
  }
  const name = /filename\*=UTF-8''([^;]+)/i.exec(res.headers.get("content-disposition") ?? "")?.[1];
  return { blob: await res.blob(), name: name ? decodeURIComponent(name) : null };
}

/** Hands a file to the browser to save. */
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Downloads a file from an authenticated endpoint and hands it to the browser to save. */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const { blob, name } = await fetchFile(path);
  saveBlob(blob, name ?? fallbackName);
}
