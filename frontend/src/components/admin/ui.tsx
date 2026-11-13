"use client";
import type { ReactNode } from "react";
import { STATUS } from "./api";

const TONE = {
  muted: { bg: "var(--status-bg)", fg: "var(--status-text)", bd: "var(--status-border)" },
  ok: { bg: "var(--ok-bg)", fg: "var(--ok-text)", bd: "transparent" },
  warn: { bg: "var(--danger-bg)", fg: "var(--danger-text)", bd: "var(--danger-border)" },
  accent: { bg: "var(--accent-light)", fg: "var(--accent-text)", bd: "transparent" },
} as const;

export function Badge({ tone = "muted", children }: { tone?: keyof typeof TONE; children: ReactNode }) {
  const t = TONE[tone];
  return (
    <span className="inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[11px] font-medium"
      style={{ background: t.bg, color: t.fg, border: `1px solid ${t.bd}` }}>
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "muted" as const };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

export function Button({
  children, onClick, variant = "ghost", disabled, type = "button", title,
}: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "ghost" | "danger";
  disabled?: boolean; type?: "button" | "submit"; title?: string;
}) {
  const style =
    variant === "primary"
      ? { background: "var(--accent)", color: "var(--text-on-accent)", border: "1px solid var(--accent)" }
      : variant === "danger"
        ? { background: "transparent", color: "var(--danger-text)", border: "1px solid var(--danger-border)" }
        : { background: "var(--bg-surface)", color: "var(--text-primary)", border: "1px solid var(--border-strong)" };
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title}
      className="inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
      style={style}>
      {children}
    </button>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>{label}</span>
      {children}
      {hint && <span className="text-xs" style={{ color: "var(--text-muted)" }}>{hint}</span>}
    </label>
  );
}

export const inputCls = "w-full rounded-lg px-2.5 py-1.5 text-sm outline-none focus:shadow-[var(--focus-ring)]";
export const inputStyle = { background: "var(--bg-input)", border: "1px solid var(--border-strong)", color: "var(--text-primary)" };

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl p-4 ${className}`} style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
      {children}
    </div>
  );
}

export function Notice({ tone, children }: { tone: "warn" | "ok" | "muted"; children: ReactNode }) {
  const t = TONE[tone];
  return (
    <div className="rounded-lg px-3 py-2 text-sm" style={{ background: t.bg, color: t.fg, border: `1px solid ${t.bd}` }}>
      {children}
    </div>
  );
}

export function fmtTime(iso?: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
}
