/** Kiểu dữ liệu + định dạng cho tab "Đánh giá". Khớp services/evalhub (backend). */

export type Fmt = "pct" | "ratio" | "int" | "num" | "sec" | "usd" | "rank" | "bool" | "text" | "auto";
export type Better = "up" | "down" | "none";

export type Param = {
  name: string; label: string; type: "int" | "float" | "bool" | "str" | "select";
  default: unknown; flag: string; help: string; options: string[]; min: number | null; max: number | null;
};
export type MetricSpec = { key: string; label: string; fmt: Fmt; better: Better; headline: boolean; group: string };

export type Suite = {
  id: string; name: string; description: string; kind: "eval" | "benchmark" | "test";
  file_prefix: string; params: Param[]; metrics: MetricSpec[]; cost_hint: string; requires: string[];
  variant_label: string; variant_param: string; model_params: Record<string, string>; scale_params: string[];
  runnable: boolean; run_disabled_reason: string;
};

export type RunStatus = "queued" | "running" | "done" | "failed" | "cancelled" | "interrupted" | "cancelling";

export type RunSummary = {
  id: string; suite: string; status: RunStatus; source: "ui" | "cli";
  created_at: string | null; started_at: string | null; finished_at: string | null;
  started_by: string | null; params: Record<string, unknown> | null; error: string | null;
  label: string | null; variant: string | null; metrics: Record<string, number>;
};

export type Column = { key: string; label: string; fmt: Fmt };
export type CaseRow = {
  id: string; label: string; category: string | null; pass: boolean | null;
  cells: Record<string, unknown>; detail: Record<string, unknown>;
};
export type Group = { id: string; title: string; columns: Column[]; rows: CaseRow[] };
export type Table = { title: string; columns: string[]; rows: { label: string; values: (number | null)[] }[] };

export type RunDetail = RunSummary & { config: Record<string, unknown>; tables: Table[]; groups: Group[]; cost: CostInfo | null };

export const KIND_LABEL: Record<Suite["kind"], string> = { eval: "Eval", benchmark: "Benchmark", test: "Test" };

export const RUN_STATUS: Record<RunStatus, { label: string; tone: "muted" | "ok" | "warn" | "accent" }> = {
  queued: { label: "Đang chờ", tone: "muted" },
  running: { label: "Đang chạy", tone: "accent" },
  cancelling: { label: "Đang huỷ", tone: "muted" },
  done: { label: "Xong", tone: "ok" },
  failed: { label: "Lỗi", tone: "warn" },
  cancelled: { label: "Đã huỷ", tone: "muted" },
  interrupted: { label: "Bị ngắt", tone: "warn" },
};
export const ACTIVE = new Set<RunStatus>(["queued", "running", "cancelling"]);

export function specFor(suite: Suite | undefined, key: string): MetricSpec {
  return suite?.metrics.find((m) => m.key === key) ?? { key, label: key, fmt: "auto", better: "none", headline: false, group: "" };
}

/** Thứ tự hiển thị: khai báo trong suite trước, key lạ (vd. pipeline cũ) sau. */
export function metricKeys(suite: Suite | undefined, metrics: Record<string, number>[]): string[] {
  const present = new Set(metrics.flatMap((m) => Object.keys(m)));
  const declared = (suite?.metrics ?? []).map((m) => m.key).filter((k) => present.has(k));
  const extra = [...present].filter((k) => !declared.includes(k)).sort();
  return [...declared, ...extra];
}

export function fmtValue(v: unknown, fmt: Fmt = "auto"): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "có" : "không";
  if (typeof v !== "number") return String(v);
  if (!Number.isFinite(v)) return "—";
  switch (fmt) {
    case "pct": return `${(v * 100).toFixed(1)}%`;
    case "ratio": return v.toFixed(3);
    case "int": case "rank": return String(Math.round(v));
    case "num": return v.toFixed(2);
    case "sec": return v < 10 ? `${v.toFixed(2)} s` : `${v.toFixed(1)} s`;
    case "usd": return `$${v < 1 ? v.toFixed(4) : v.toFixed(2)}`;
    default: return Number.isInteger(v) ? String(v) : v.toFixed(3);
  }
}

export function fmtDelta(d: number, fmt: Fmt): string {
  const sign = d > 0 ? "+" : d < 0 ? "−" : "±";
  const a = Math.abs(d);
  if (fmt === "pct") return `${sign}${(a * 100).toFixed(1)} điểm %`;
  return `${sign}${fmtValue(a, fmt)}`;
}

/** 1 = tốt hơn, -1 = kém hơn, 0 = không đổi / không có chiều. */
export function judge(d: number, better: Better): -1 | 0 | 1 {
  if (better === "none" || Math.abs(d) < 1e-9) return 0;
  return (d > 0) === (better === "up") ? 1 : -1;
}

export function fmtWhen(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
}

export function duration(a?: string | null, b?: string | null): string {
  if (!a) return "";
  const ms = (b ? new Date(b) : new Date()).getTime() - new Date(a).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "";
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} giây` : s < 3600 ? `${Math.floor(s / 60)} phút ${s % 60} giây` : `${(s / 3600).toFixed(1)} giờ`;
}

/** Lần chạy "xong" trước đó cùng suite (và cùng variant nếu có) — mốc tính chênh lệch. */
export function previousDone(runs: RunSummary[], run: RunSummary): RunSummary | undefined {
  const sorted = runs.filter((r) => r.suite === run.suite && r.status === "done" && (r.variant ?? null) === (run.variant ?? null));
  const i = sorted.findIndex((r) => r.id === run.id);
  return i >= 0 ? sorted[i + 1] : undefined;
}

/** Như inputCls của admin nhưng không ép full chiều ngang (dùng cho select / ô lọc nằm cùng hàng). */
export const selectCls = "rounded-lg px-2.5 py-1.5 text-sm outline-none focus:shadow-[var(--focus-ring)]";

// ---------------------------------------------------------------- chi phí
export type CostItem = {
  role: string; model: string | null; tokens_in: number | null; tokens_out: number | null; usd: number | null; batch: boolean;
};
export type WhatIf = { role: string; from_model: string; model: string; usd: number; delta_usd: number; exact: boolean; current: boolean };
export type CostInfo = {
  total_usd: number | null; per_case_usd: number | null; tokens_in: number | null; tokens_out: number | null;
  n_cases: number | null; items: CostItem[]; source: "tokens" | "cost_only" | "free" | "none"; note: string;
  what_if: WhatIf[];
};
export type Estimate = {
  usd: number | null; exact: boolean; same_model?: boolean; note: string;
  basis: { id: string; created_at: string | null; usd: number | null; models: string[] } | null;
  items: { role: string; from_model: string; model: string; usd: number; exact: boolean }[];
};
export type Price = { provider: string; in: number; out: number; source: string };
export const COST_SOURCE: Record<CostInfo["source"], string> = {
  tokens: "Tính từ số token thật",
  cost_only: "Chỉ có số tiền (lần chạy cũ, không ghi token)",
  free: "Không gọi API có phí",
  none: "Không ghi nhận chi phí",
};
