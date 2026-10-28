"use client";

/**
 * Session state for the browser.
 *
 * The token is kept in localStorage and attached to every request by the API
 * client. This is the simplest arrangement that works without a backend session
 * cookie, and it is adequate for the demo. A production deployment behind the
 * same origin would move this to an httpOnly cookie so script cannot read it.
 *
 * The capabilities returned by /auth/me drive which nav items and dashboards
 * render. That is a convenience, not a control: every route re-checks the
 * permission on the server, so hiding a link is never the thing stopping a user.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "./api";
import type { Capabilities, MeResponse, User } from "./types";

const TOKEN_KEY = "cleanslate.token";

interface SessionState {
  token: string | null;
  user: User | null;
  permissions: string[];
  capabilities: Capabilities | null;
  ready: boolean;
  error: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
  can: (permission: string) => boolean;
}

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadMe = useCallback(async (activeToken: string) => {
    const profile = await api.me(activeToken);
    setMe(profile);
    return profile;
  }, []);

  // Restore a stored session on first paint. A stale or revoked token is
  // discarded rather than left to fail on the first real request.
  useEffect(() => {
    const stored = typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) : null;
    if (!stored) {
      setReady(true);
      return;
    }
    setToken(stored);
    loadMe(stored)
      .catch(() => {
        window.localStorage.removeItem(TOKEN_KEY);
        setToken(null);
        setMe(null);
      })
      .finally(() => setReady(true));
  }, [loadMe]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      setError(null);
      try {
        const result = await api.login(email, password);
        window.localStorage.setItem(TOKEN_KEY, result.access_token);
        setToken(result.access_token);
        await loadMe(result.access_token);
      } catch (err) {
        const message =
          err instanceof ApiError ? err.message : "Sign in failed. Check the API is running.";
        setError(message);
        throw err;
      }
    },
    [loadMe],
  );

  const signOut = useCallback(async () => {
    const active = token;
    window.localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setMe(null);
    if (active) {
      // Best effort: the token is already discarded locally, so a failure here
      // must not block the user from leaving.
      try {
        await api.logout(active);
      } catch {
        /* ignore */
      }
    }
  }, [token]);

  const refresh = useCallback(async () => {
    if (!token) return;
    await loadMe(token);
  }, [loadMe, token]);

  const value = useMemo<SessionState>(
    () => ({
      token,
      user: me?.user ?? null,
      permissions: me?.permissions ?? [],
      capabilities: me?.capabilities ?? null,
      ready,
      error,
      signIn,
      signOut,
      refresh,
      can: (permission: string) => (me?.permissions ?? []).includes(permission),
    }),
    [token, me, ready, error, signIn, signOut, refresh],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error("useSession must be used inside a SessionProvider");
  }
  return context;
}
