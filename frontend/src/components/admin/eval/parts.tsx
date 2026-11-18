"use client";
import { useEffect, useRef, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Minus, Play, Square, Terminal, X } from "lucide-react";
import { useAdminFetch } from "../api";
import { EstimateBox } from "./cost";
import { Badge, Button, Field, Notice, inputCls, inputStyle } from "../ui";
import {
  ACTIVE, Better, Fmt, MetricSpec, RUN_STATUS, RunStatus, RunSummary, Suite, fmtDelta, fmtValue, judge,
} from "./types";

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const s = RUN_STATUS[status] ?? { label: status, tone: "muted" as const };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

/** Chênh lệch so với mốc: mũi tên + chữ (không chỉ dựa vào màu). */
export function Delta({ cur, prev, fmt, better }: { cur?: number; prev?: number; fmt: Fmt; better: Better }) {
  if (cur === undefined || prev === undefined) return null;
  const d = cur - prev;
  const j = judge(d, better);
  const color = j > 0 ? "var(--ok-text)" : j < 0 ? "var(--danger-text)" : "var(--text-muted)";
  const Icon = Math.abs(d) < 1e-9 ? Minus : d > 0 ? ArrowUpRight : ArrowDownRight;
  const word = j > 0 ? "tốt hơn" : j < 0 ? "kém hơn" : "";
  return (
    <span className="inline-flex items-center gap-0.5 text-xs font-medium" style={{ color }}
      title={word ? `${word} so với lần trước` : "so với lần trước"}>
      <Icon size={13} aria-hidden />{Math.abs(d) < 1e-9 ? "không đổi" : fmtDelta(d, fmt)}
      {word && <span className="sr-only"> ({word})</span>}
    </span>
  );
}

export function MetricTile({ spec, value, prev }: { spec: MetricSpec; value?: number; prev?: number }) {
  return (
    <div className="min-w-0 rounded-lg px-3 py-2" style={{ background: "var(--bg-base)", border: "1px solid var(--border)" }}>
      <div className="truncate text-xs" style={{ color: "var(--text-secondary)" }} title={spec.label}>{spec.label}</div>
      <div className="mt-0.5 text-xl font-semibold tabular-nums">{fmtValue(value, spec.fmt)}</div>
      <div className="h-4"><Delta cur={value} prev={prev} fmt={spec.fmt} better={spec.better} /></div>
    </div>
  );
}

/** Hộp thoại chạy một suite: form sinh từ khai báo tham số của backend. */
export function LaunchDialog({ suite, onClose, onStarted }: {
  suite: Suite; onClose: () => void; onStarted: (run: RunSummary) => void;
}) {
  const api = useAdminFetch();
  const [values, setValues] = useState<Record<string, unknown>>(
    () => Object.fromEntries(suite.params.map((p) => [p.name, p.default ?? (p.type === "bool" ? false : "")])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = async () => {
    setBusy(true); setError("");
    try {
      const run = await api<RunSummary>("eval/runs", { method: "POST", body: JSON.stringify({ suite: suite.id, params: values }) });
      onStarted(run);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.45)" }}
      onClick={onClose} role="presentation">
      <div role="dialog" aria-modal="true" aria-labelledby="launch-title" onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl p-5"
        style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
        <div className="mb-3 flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <h3 id="launch-title" className="text-base font-semibold">Chạy: {suite.name}</h3>
            <p className="mt-0.5 text-sm" style={{ color: "var(--text-secondary)" }}>{suite.description}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Đóng" className="rounded-lg p-1 hover:bg-[var(--bg-hover)]"><X size={18} /></button>
        </div>
        {suite.requires.length > 0 && (
          <p className="mb-2 text-xs" style={{ color: "var(--text-muted)" }}>Cần đang chạy: {suite.requires.join(", ")}</p>
        )}
        {suite.cost_hint && <div className="mb-3"><Notice tone="muted">Chi phí: {suite.cost_hint}</Notice></div>}

        <div className="grid gap-3 sm:grid-cols-2">
          {suite.params.map((p) => {
            const v = values[p.name];
            const set = (nv: unknown) => setValues((s) => ({ ...s, [p.name]: nv }));
            if (p.type === "bool") {
              return (
                <label key={p.name} className="flex items-center gap-2 text-sm sm:col-span-2">
                  <input type="checkbox" checked={!!v} onChange={(e) => set(e.target.checked)} />
                  {p.label}
                </label>
              );
            }
            return (
              <Field key={p.name} label={p.label} hint={p.help || undefined}>
                {p.type === "select" ? (
                  <select className={inputCls} style={inputStyle} value={String(v ?? "")} onChange={(e) => set(e.target.value)}>
                    {p.options.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : (
                  <input className={inputCls} style={inputStyle} value={String(v ?? "")}
                    type={p.type === "str" ? "text" : "number"} step={p.type === "float" ? "any" : undefined}
                    min={p.min ?? undefined} max={p.max ?? undefined}
                    onChange={(e) => set(e.target.value)} />
                )}
              </Field>
            );
          })}
        </div>

        <div className="mt-3"><EstimateBox suite={suite} values={values} /></div>
        {error && <div className="mt-3"><Notice tone="warn">{error}</Notice></div>}
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" onClick={submit} disabled={busy}><Play size={14} aria-hidden />{busy ? "Đang gửi…" : "Chạy"}</Button>
        </div>
      </div>
    </div>
  );
}

/** Log của lần chạy từ UI; tự cập nhật khi còn đang chạy. */
export function LogPanel({ runId, status, defaultOpen = false, onStatus }: {
  runId: string; status: RunStatus; defaultOpen?: boolean; onStatus?: (s: RunStatus) => void;
}) {
  const api = useAdminFetch();
  const [open, setOpen] = useState(defaultOpen);
  const [text, setText] = useState("");
  const pre = useRef<HTMLPreElement>(null);
  const live = ACTIVE.has(status);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const load = () => api<{ text: string; status: RunStatus }>(`eval/runs/${encodeURIComponent(runId)}/log?tail=400`)
      .then((r) => {
        if (cancelled) return;
        setText(r.text);
        if (r.status !== status) onStatus?.(r.status);
        requestAnimationFrame(() => { if (pre.current) pre.current.scrollTop = pre.current.scrollHeight; });
      }).catch(() => {});
    load();
    if (!live) return () => { cancelled = true; };
    const t = setInterval(load, 2500);
    return () => { cancelled = true; clearInterval(t); };
  }, [api, runId, open, live, status, onStatus]);

  return (
    <div>
      <button type="button" onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1.5 text-sm hover:underline"
        style={{ color: "var(--text-secondary)" }} aria-expanded={open}>
        <Terminal size={14} aria-hidden />{open ? "Ẩn log" : "Xem log"}{live && " (đang cập nhật)"}
      </button>
      {open && (
        <pre ref={pre} className="mt-2 max-h-80 overflow-auto rounded-lg p-3 text-xs leading-relaxed"
          style={{ background: "var(--bg-base)", border: "1px solid var(--border)", whiteSpace: "pre-wrap" }}>
          {text || "(chưa có log)"}
        </pre>
      )}
    </div>
  );
}

export function CancelButton({ runId, onDone }: { runId: string; onDone: () => void }) {
  const api = useAdminFetch();
  const [busy, setBusy] = useState(false);
  return (
    <Button variant="danger" disabled={busy} onClick={async () => {
      if (!confirm("Huỷ lần chạy này?")) return;
      setBusy(true);
      try { await api(`eval/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }); } catch (e) { alert((e as Error).message); }
      setBusy(false); onDone();
    }}><Square size={13} aria-hidden />Huỷ</Button>
  );
}
