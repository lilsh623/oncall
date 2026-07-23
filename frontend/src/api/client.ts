export type UserRole = "viewer" | "operator" | "approver" | "admin";

export type AuthUser = {
  id: string;
  username: string;
  role: UserRole;
};

type RefreshHandler = () => Promise<boolean>;

function csrfToken(): string | undefined {
  return document.cookie
    .split("; ")
    .find((entry) => entry.startsWith("csrf_token="))
    ?.split("=")[1];
}

export class ApiClient {
  constructor(
    private readonly getAccessToken: () => string | null,
    private readonly refresh: RefreshHandler,
    private readonly onUnauthenticated: () => void,
  ) {}

  async request<T>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
    const headers = new Headers(init.headers);
    const token = this.getAccessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...init, headers, credentials: "include" });
    if (response.status === 401 && !retried && (await this.refresh())) {
      return this.request<T>(path, init, true);
    }
    if (response.status === 401) this.onUnauthenticated();
    if (!response.ok) {
      const message = await response.json().catch(() => ({ detail: "请求失败" }));
      throw new Error(String(message.detail ?? `请求失败：${response.status}`));
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  async refreshSession(): Promise<{ access_token: string; user: AuthUser } | null> {
    const csrf = csrfToken();
    const response = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
      headers: csrf ? { "X-CSRF-Token": csrf } : {},
    });
    return response.ok ? (response.json() as Promise<{ access_token: string; user: AuthUser }>) : null;
  }

  async openStream(path: string): Promise<Response> {
    const headers = new Headers({ Accept: "text/event-stream" });
    const token = this.getAccessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    let response = await fetch(path, { headers, credentials: "include" });
    if (response.status === 401 && (await this.refresh())) {
      const retryHeaders = new Headers({ Accept: "text/event-stream" });
      const refreshed = this.getAccessToken();
      if (refreshed) retryHeaders.set("Authorization", `Bearer ${refreshed}`);
      response = await fetch(path, { headers: retryHeaders, credentials: "include" });
    }
    if (!response.ok || !response.body) throw new Error("无法连接 Incident 事件流");
    return response;
  }
}
