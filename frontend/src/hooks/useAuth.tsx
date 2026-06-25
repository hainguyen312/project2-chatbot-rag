"use client";

/**
 * Auth context cho Firebase. Cung cấp:
 * - user (Firebase User | null)
 * - loading (đang init)
 * - signInEmail / signUpEmail / signInGoogle / signOut
 * - getIdToken() → token string|null để gửi qua Authorization header
 *
 * Khi user login lần đầu, hook tự gọi backend /auth/claim_anonymous để
 * re-tag dữ liệu anonymous (demo_xxx) sang uid mới.
 */

import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  GoogleAuthProvider,
  User,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut as fbSignOut,
} from "firebase/auth";
import { getFirebaseAuth, isFirebaseConfigured } from "@/lib/firebase";

type AuthCtx = {
  user: User | null;
  loading: boolean;
  configured: boolean;
  signInEmail: (email: string, password: string) => Promise<void>;
  signUpEmail: (email: string, password: string) => Promise<void>;
  signInGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
  getIdToken: () => Promise<string | null>;
};

const Ctx = createContext<AuthCtx | null>(null);

const ANON_KEY = "rag_user_id";
const CLAIM_FLAG = "rag_anon_claimed";

async function claimAnonymousIfNeeded(token: string) {
  if (typeof window === "undefined") return;
  if (window.localStorage.getItem(CLAIM_FLAG)) return;
  const anon = window.localStorage.getItem(ANON_KEY);
  if (!anon || !anon.startsWith("demo_")) return;
  try {
    const res = await fetch("/api/auth/claim_anonymous", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: token, anonymous_id: anon }),
    });
    if (res.ok) {
      window.localStorage.setItem(CLAIM_FLAG, "1");
    }
  } catch (e) {
    console.warn("[Auth] claim_anonymous failed:", e);
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const configured = isFirebaseConfigured();

  useEffect(() => {
    if (!configured) {
      setLoading(false);
      return;
    }
    const auth = getFirebaseAuth();
    const unsub = onAuthStateChanged(auth, async (u) => {
      setUser(u);
      setLoading(false);
      if (u) {
        try {
          const token = await u.getIdToken();
          await claimAnonymousIfNeeded(token);
        } catch (e) {
          console.warn("[Auth] getIdToken error:", e);
        }
      }
    });
    return () => unsub();
  }, [configured]);

  const signInEmail = useCallback(async (email: string, password: string) => {
    await signInWithEmailAndPassword(getFirebaseAuth(), email, password);
  }, []);

  const signUpEmail = useCallback(async (email: string, password: string) => {
    await createUserWithEmailAndPassword(getFirebaseAuth(), email, password);
  }, []);

  const signInGoogle = useCallback(async () => {
    await signInWithPopup(getFirebaseAuth(), new GoogleAuthProvider());
  }, []);

  const signOut = useCallback(async () => {
    await fbSignOut(getFirebaseAuth());
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(CLAIM_FLAG);
    }
  }, []);

  const getIdToken = useCallback(async () => {
    if (!user) return null;
    try {
      return await user.getIdToken();
    } catch {
      return null;
    }
  }, [user]);

  return (
    <Ctx.Provider value={{
      user, loading, configured,
      signInEmail, signUpEmail, signInGoogle, signOut, getIdToken,
    }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside <AuthProvider>");
  return v;
}
