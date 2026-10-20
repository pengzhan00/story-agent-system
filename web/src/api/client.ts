export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly remediation: string,
    readonly detail: unknown,
  ) {
    super(message);
  }
}

let locale = "en";
export function setApiLocale(next: string): void {
  locale = next;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept-Language", locale);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let env: { error?: { code?: string; message?: string; remediation?: string; detail?: unknown } } = {};
    try {
      env = await res.json();
    } catch {
      /* not an envelope (proxy down, HTML error page...) */
    }
    const e = env.error ?? {};
    throw new ApiError(res.status, e.code ?? "internal", e.message ?? res.statusText, e.remediation ?? "", e.detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const del = (path: string) => api<void>(path, { method: "DELETE" });
