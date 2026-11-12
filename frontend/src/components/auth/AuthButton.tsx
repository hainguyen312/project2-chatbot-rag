"use client";

import { FormEvent, useState } from "react";
import { createPortal } from "react-dom";
import { LogIn, LogOut, User as UserIcon, X } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";

export function AuthButton() {
  const { user, loading, configured, signOut } = useAuth();
  const [open, setOpen] = useState(false);

  if (loading) {
    return (
      <span className="inline-flex h-9 w-24 animate-pulse rounded-md bg-[var(--bg-hover)]" aria-label="Đang tải trạng thái đăng nhập" />
    );
  }

  if (!configured) {
    return (
      <span
        title="Firebase chưa cấu hình — xem frontend/src/lib/firebase.ts"
        className="inline-flex h-9 items-center rounded-md px-2.5 text-xs text-[var(--danger-text)]"
      >
        Chưa bật đăng nhập
      </span>
    );
  }

  if (user) {
    const name = user.displayName || user.email || "User";
    return (
      <div className="flex items-center gap-2">
        <span
          className="hidden h-9 items-center gap-1.5 px-1 text-sm text-[var(--text-secondary)] md:inline-flex"
          title={user.email ?? ""}
        >
          <UserIcon size={15} aria-hidden /> {name.length > 18 ? name.slice(0, 18) + "…" : name}
        </span>
        <button
          onClick={() => signOut()}
          className="inline-flex h-9 items-center gap-1.5 rounded-md px-2.5 text-sm text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
          title="Đăng xuất"
          aria-label="Đăng xuất"
        >
          <LogOut size={16} aria-hidden />
        </button>
      </div>
    );
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-label="Đăng nhập"
        className="inline-flex h-9 items-center gap-1.5 rounded-md border border-[var(--border-strong)] px-2.5 sm:px-3 text-sm font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--bg-hover)]"
      >
        <LogIn size={16} aria-hidden /><span className="hidden sm:inline">Đăng nhập</span>
      </button>
      {open && <AuthModal onClose={() => setOpen(false)} />}
    </>
  );
}

function AuthModal({ onClose }: { onClose: () => void }) {
  const { signInEmail, signUpEmail, signInGoogle } = useAuth();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      if (mode === "signin") await signInEmail(email.trim(), password);
      else await signUpEmail(email.trim(), password);
      onClose();
    } catch (e) {
      setErr(humanFirebaseError(e));
    } finally {
      setBusy(false);
    }
  };

  const google = async () => {
    setBusy(true);
    setErr(null);
    try {
      await signInGoogle();
      onClose();
    } catch (e) {
      setErr(humanFirebaseError(e));
    } finally {
      setBusy(false);
    }
  };

  if (typeof document === "undefined") return null;
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-sm rounded-xl border border-[var(--border)] bg-[var(--bg-surface)] p-6 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-[var(--accent-text)]">
            {mode === "signin" ? "Đăng nhập" : "Tạo tài khoản"}
          </h2>
          <button onClick={onClose} className="rounded p-1 text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]">
            <X size={18} />
          </button>
        </div>
        <p className="mt-1 text-xs text-[var(--text-secondary)]">
          Đăng nhập để bộ nhớ cá nhân được đồng bộ giữa các thiết bị.
        </p>

        <button
          onClick={google}
          disabled={busy}
          className="mt-4 flex w-full items-center justify-center gap-2 rounded-md border border-[var(--border-strong)] bg-[var(--bg-surface)] px-3 py-2 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--bg-hover)] disabled:opacity-60"
        >
          <svg width="16" height="16" viewBox="0 0 48 48">
            <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.7-6.1 8-11.3 8a12 12 0 1 1 0-24 12 12 0 0 1 8.5 3.5l5.7-5.7A20 20 0 1 0 44 24c0-1.2-.1-2.4-.4-3.5z"/>
            <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 16 19 13 24 13c3 0 5.7 1.1 7.8 3l5.7-5.7A20 20 0 0 0 6.3 14.7z"/>
            <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2A11.8 11.8 0 0 1 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5A20 20 0 0 0 24 44z"/>
            <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3a12 12 0 0 1-4.1 5.6l6.2 5.2C40.2 36.6 44 30.8 44 24c0-1.2-.1-2.4-.4-3.5z"/>
          </svg>
          Tiếp tục với Google
        </button>

        <div className="my-4 flex items-center gap-2 text-xs text-[var(--text-muted)]">
          <span className="h-px flex-1 bg-[var(--bg-hover)]" /> hoặc <span className="h-px flex-1 bg-[var(--bg-hover)]" />
        </div>

        <form onSubmit={submit} className="space-y-3">
          <input
            type="email"
            required
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-[var(--border-strong)] bg-[var(--bg-input)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)] focus:outline-none placeholder:text-[var(--text-muted)]"
          />
          <input
            type="password"
            required
            minLength={6}
            placeholder="Mật khẩu (≥ 6 ký tự)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-[var(--border-strong)] bg-[var(--bg-input)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)] focus:outline-none placeholder:text-[var(--text-muted)]"
          />
          {err && <p className="text-xs text-[var(--danger-text)]">{err}</p>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-md bg-[var(--accent)] px-3 py-2 text-sm font-medium text-[var(--text-on-accent)] hover:bg-[var(--accent-hover)] disabled:opacity-60"
          >
            {busy ? "Đang xử lý…" : (mode === "signin" ? "Đăng nhập" : "Tạo tài khoản")}
          </button>
        </form>

        <p className="mt-3 text-center text-xs text-[var(--text-secondary)]">
          {mode === "signin" ? "Chưa có tài khoản? " : "Đã có tài khoản? "}
          <button
            type="button"
            onClick={() => setMode(mode === "signin" ? "signup" : "signin")}
            className="font-medium text-[var(--accent-text)] hover:underline"
          >
            {mode === "signin" ? "Đăng ký" : "Đăng nhập"}
          </button>
        </p>
      </div>
    </div>,
    document.body
  );
}

function humanFirebaseError(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  if (msg.includes("auth/invalid-credential") || msg.includes("auth/wrong-password"))
    return "Sai email hoặc mật khẩu.";
  if (msg.includes("auth/email-already-in-use")) return "Email đã được dùng.";
  if (msg.includes("auth/weak-password")) return "Mật khẩu quá yếu (≥ 6 ký tự).";
  if (msg.includes("auth/popup-closed-by-user")) return "Đã hủy đăng nhập.";
  return msg.replace(/^Firebase: /, "");
}
