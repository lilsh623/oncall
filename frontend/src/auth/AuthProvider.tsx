import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiClient, type AuthUser } from "../api/client";

type AuthContextValue = {
  user: AuthUser | null;
  client: ApiClient;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const tokenRef = useRef<string | null>(null);
  const clear = useCallback(() => {
    tokenRef.current = null;
    setUser(null);
  }, []);
  const refresh = useCallback(async () => {
    const csrf = document.cookie.split("; ").find((item) => item.startsWith("csrf_token="))?.split("=")[1];
    const response = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
      headers: csrf ? { "X-CSRF-Token": csrf } : {},
    });
    if (!response.ok) {
      clear();
      return false;
    }
    const payload = (await response.json()) as { access_token: string; user: AuthUser };
    tokenRef.current = payload.access_token;
    setUser(payload.user);
    return true;
  }, [clear]);
  const client = useMemo(() => new ApiClient(() => tokenRef.current, refresh, clear), [clear, refresh]);
  const login = useCallback(async (username: string, password: string) => {
    const payload = await client.request<{ access_token: string; user: AuthUser }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    tokenRef.current = payload.access_token;
    setUser(payload.user);
  }, [client]);
  const logout = useCallback(async () => {
    const csrf = document.cookie.split("; ").find((item) => item.startsWith("csrf_token="))?.split("=")[1];
    await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include", headers: csrf ? { "X-CSRF-Token": csrf } : {} });
    clear();
  }, [clear]);
  return <AuthContext.Provider value={{ user, client, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("AuthProvider is required");
  return context;
}
