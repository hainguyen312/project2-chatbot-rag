"use client";
import { useEffect, useMemo, useState } from "react";
import { Calculator, Check, Plus, Save, Trash2 } from "lucide-react";
import { useAdminFetch } from "../api";
import { Badge, Button, Card, Notice, inputStyle } from "../ui";
import {
  COST_SOURCE, CostInfo, Estimate, Price, RunSummary, Suite, fmtValue, fmtWhen, selectCls,
} from "./types";

const usd = (v: number | null | undefined) => fmtValue(v, "usd");
const ROLE: Record<string, string> = {
  agent: "Agent", judge: "Judge", memory: "Bộ nhớ", embedding: "Embedding", "gen+judge": "Sinh case + chấm",
};

// ---------------------------------------------------------------- chi phí của một lần chạy
export function CostCard({ cost }: { cost: CostInfo }) {
  const roles = [...new Set(cost.what_if.map((w) => `${w.role}|${w.from_model}`))];
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="text-sm font-semibold">Chi phí</h3>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>{COST_SOURCE[cost.source]}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["Tổng", usd(cost.total_usd)],
          ["Mỗi case", usd(cost.per_case_usd)],
          ["Token vào", fmtValue(cost.tokens_in, "int")],
          ["Token ra", fmtValue(cost.tokens_out, "int")],
        ].map(([l, v]) => (
          <div key={l} className="rounded-lg px-3 py-2" style={{ background: "var(--bg-base)", border: "1px solid var(--border)" }}>
            <div className="text-xs" style={{ color: "var(--text-secondary)" }}>{l}</div>
            <div className="mt-0.5 text-lg font-semibold tabular-nums">{v}</div>
          </div>
        ))}
      </div>
      {cost.note && <p className="text-xs" style={{ color: "var(--text-muted)" }}>{cost.note}</p>}

      {roles.length > 0 && (
        <div className="space-y-1">
          <h4 className="text-sm font-medium">Nếu đổi model <span className="font-normal" style={{ color: "var(--text-muted)" }}>— tính lại từ lần chạy này, không gọi API</span></h4>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs" style={{ color: "var(--text-secondary)" }}>
                  <th className="py-1.5 pr-3 font-medium">Vai trò</th>
                  <th className="py-1.5 pr-3 font-medium">Model</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Chi phí</th>
                  <th className="py-1.5 pr-3 text-right font-medium">So với hiện tại</th>
                  <th className="py-1.5 font-medium">Độ tin cậy</th>
                </tr>
              </thead>
              <tbody>
                {cost.what_if.slice().sort((a, b) => a.role.localeCompare(b.role) || a.usd - b.usd).map((w) => (
                  <tr key={`${w.role}-${w.model}`} style={{ borderTop: "1px solid var(--border)" }}>
                    <td className="py-1.5 pr-3">{ROLE[w.role] ?? w.role}</td>
                    <td className="py-1.5 pr-3 font-mono text-xs">
                      {w.model} {w.current && <Badge tone="accent">đang dùng</Badge>}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{usd(w.usd)}</td>
                    <td className="py-1.5 pr-3 text-right text-xs tabular-nums"
                      style={{ color: w.current ? "var(--text-muted)" : w.delta_usd < 0 ? "var(--ok-text)" : "var(--danger-text)" }}>
                      {w.current ? "—" : `${w.delta_usd < 0 ? "rẻ hơn" : "đắt hơn"} ${usd(Math.abs(w.delta_usd))}`
                        + (w.usd > 0 && !w.current ? ` (×${(w.usd / (w.usd - w.delta_usd)).toFixed(2)})` : "")}
                    </td>
                    <td className="py-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                      {!w.exact ? "xấp xỉ (tỉ lệ giá in/out khác nhau)" : cost.source === "tokens" ? "chính xác theo token" : "chính xác (cùng tỉ lệ giá in/out)"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            Chỉ đổi giá, giữ nguyên số token — model khác có thể gọi tool / trả lời dài ngắn khác, chất lượng cũng khác. Muốn biết chắc thì chạy lại với model đó.
          </p>
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- ước tính trước khi chạy
export function EstimateBox({ suite, values }: { suite: Suite; values: Record<string, unknown> }) {
  const api = useAdminFetch();
  const [est, setEst] = useState<Estimate | null>(null);
  const [err, setErr] = useState("");
  const key = JSON.stringify(values);
  useEffect(() => {
    let cancelled = false;
    const t = setTimeout(() => {
      api<Estimate>("eval/estimate", { method: "POST", body: JSON.stringify({ suite: suite.id, params: JSON.parse(key) }) })
        .then((r) => { if (!cancelled) { setEst(r); setErr(""); } })
        .catch((e) => { if (!cancelled) { setEst(null); setErr((e as Error).message); } });
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
  }, [api, suite.id, key]);

  return (
    <div className="rounded-lg px-3 py-2 text-sm" style={{ background: "var(--bg-base)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2">
        <Calculator size={15} aria-hidden style={{ color: "var(--text-secondary)" }} />
        <span className="font-medium">Ước tính chi phí:</span>
        {err ? <span style={{ color: "var(--danger-text)" }}>{err}</span>
          : !est ? <span style={{ color: "var(--text-muted)" }}>đang tính…</span>
          : est.usd === null ? <span style={{ color: "var(--text-muted)" }}>chưa ước tính được</span>
          : <span className="font-semibold tabular-nums">≈ {usd(est.usd)}</span>}
        {est && est.usd !== null && !est.same_model && suite.model_params && Object.keys(suite.model_params).length > 0 && (
          <Badge>quy đổi từ model khác</Badge>
        )}
      </div>
      {est?.basis && (
        <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
          Mốc: lần chạy {fmtWhen(est.basis.created_at)} ({est.basis.models.join(", ")}, {usd(est.basis.usd)}). Không gọi API khi ước tính.
        </p>
      )}
      {est?.note && <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>{est.note}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- tổng quan chi phí
export function CostOverview({ suites, runs }: { suites: Suite[]; runs: RunSummary[] }) {
  const done = runs.filter((r) => r.status === "done");
  const [since] = useState(() => Date.now() - 30 * 864e5);  // mốc 30 ngày, cố định trong phiên xem
  const rows = suites.map((s) => {
    const rs = done.filter((r) => r.suite === s.id);
    const withCost = rs.filter((r) => typeof r.metrics["cost.total_usd"] === "number");
    const sum = (xs: RunSummary[]) => xs.reduce((a, r) => a + (r.metrics["cost.total_usd"] ?? 0), 0);
    const last = withCost[0];
    return {
      suite: s, total: sum(withCost),
      month: sum(withCost.filter((r) => r.created_at && new Date(r.created_at).getTime() >= since)),
      last: last?.metrics["cost.total_usd"], perCase: last?.metrics["cost.per_case_usd"],
      lastVariant: last?.variant, counted: withCost.length, all: rs.length,
    };
  });
  const total = rows.reduce((a, r) => a + r.total, 0);
  const month = rows.reduce((a, r) => a + r.month, 0);
  const missing = rows.reduce((a, r) => a + (r.all - r.counted), 0);

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <h3 className="text-sm font-semibold">Chi phí đánh giá</h3>
        <span className="text-sm">Tổng: <b className="tabular-nums">{usd(total)}</b></span>
        <span className="text-sm">30 ngày qua: <b className="tabular-nums">{usd(month)}</b></span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs" style={{ color: "var(--text-secondary)" }}>
              <th className="py-1.5 pr-3 font-medium">Bộ</th>
              <th className="py-1.5 pr-3 text-right font-medium">Lần gần nhất</th>
              <th className="py-1.5 pr-3 text-right font-medium">Mỗi case</th>
              <th className="py-1.5 pr-3 text-right font-medium">30 ngày</th>
              <th className="py-1.5 pr-3 text-right font-medium">Tổng</th>
              <th className="py-1.5 text-right font-medium">Lần chạy có số liệu</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.suite.id} style={{ borderTop: "1px solid var(--border)" }}>
                <td className="py-1.5 pr-3">{r.suite.name}{r.lastVariant && <span className="ml-1 text-xs" style={{ color: "var(--text-muted)" }}>({r.lastVariant})</span>}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{usd(r.last)}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{usd(r.perCase)}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{r.counted ? usd(r.month) : "—"}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{r.counted ? usd(r.total) : "—"}</td>
                <td className="py-1.5 text-right text-xs tabular-nums" style={{ color: "var(--text-muted)" }}>{r.counted}/{r.all}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {missing > 0 && (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          {missing} lần chạy cũ không ghi chi phí (không tính vào tổng). Từ giờ các script ghi số token thật cho mỗi lần chạy.
        </p>
      )}
      <PricingEditor />
    </Card>
  );
}

// ---------------------------------------------------------------- bảng giá
type Row = Price & { model: string };

function PricingEditor() {
  const api = useAdminFetch();
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [orig, setOrig] = useState<string[]>([]);
  const [msg, setMsg] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = (models: Record<string, Price>) => {
    setRows(Object.entries(models).map(([model, p]) => ({ model, ...p })));
    setOrig(Object.keys(models));
  };
  useEffect(() => {
    if (!open || rows) return;
    api<{ models: Record<string, Price> }>("eval/pricing").then((r) => load(r.models)).catch((e) => setMsg({ tone: "warn", text: (e as Error).message }));
  }, [api, open, rows]);

  const providers = useMemo(() => [...new Set((rows ?? []).map((r) => r.provider))], [rows]);
  const set = (i: number, patch: Partial<Row>) => setRows((rs) => rs!.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const save = async () => {
    if (!rows) return;
    setBusy(true); setMsg(null);
    const models: Record<string, Price | null> = {};
    for (const r of rows) if (r.model.trim()) models[r.model.trim()] = { provider: r.provider, in: Number(r.in), out: Number(r.out), source: r.source };
    for (const m of orig) if (!(m in models)) models[m] = null;
    try {
      const r = await api<{ models: Record<string, Price> }>("eval/pricing", { method: "PUT", body: JSON.stringify({ models }) });
      load(r.models);
      setMsg({ tone: "ok", text: "Đã lưu. Chi phí \"nếu đổi model\" và ước tính dùng giá mới ngay." });
    } catch (e) {
      setMsg({ tone: "warn", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer text-sm" style={{ color: "var(--text-secondary)" }}>Bảng giá model ($ / 1M token)</summary>
      {rows && (
        <div className="mt-2 space-y-2">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs" style={{ color: "var(--text-secondary)" }}>
                  <th className="py-1 pr-2 font-medium">Model</th>
                  <th className="py-1 pr-2 font-medium">Nhà cung cấp</th>
                  <th className="py-1 pr-2 font-medium">Input</th>
                  <th className="py-1 pr-2 font-medium">Output</th>
                  <th className="py-1 pr-2 font-medium">Nguồn giá</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td className="py-1 pr-2"><input aria-label="Model" className={`${selectCls} w-44 font-mono text-xs`} style={inputStyle} value={r.model} onChange={(e) => set(i, { model: e.target.value })} /></td>
                    <td className="py-1 pr-2">
                      <input aria-label="Nhà cung cấp" list="eval-providers" className={`${selectCls} w-32`} style={inputStyle} value={r.provider} onChange={(e) => set(i, { provider: e.target.value })} />
                    </td>
                    <td className="py-1 pr-2"><input aria-label="Giá input" type="number" step="any" min={0} className={`${selectCls} w-20`} style={inputStyle} value={r.in} onChange={(e) => set(i, { in: e.target.value as unknown as number })} /></td>
                    <td className="py-1 pr-2"><input aria-label="Giá output" type="number" step="any" min={0} className={`${selectCls} w-20`} style={inputStyle} value={r.out} onChange={(e) => set(i, { out: e.target.value as unknown as number })} /></td>
                    <td className="py-1 pr-2 text-xs" style={{ color: "var(--text-muted)" }}>{r.source}</td>
                    <td className="py-1">
                      <button type="button" aria-label={`Xoá ${r.model}`} className="rounded p-1 hover:bg-[var(--bg-hover)]" onClick={() => setRows((rs) => rs!.filter((_, j) => j !== i))}>
                        <Trash2 size={14} aria-hidden />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <datalist id="eval-providers">{providers.map((p) => <option key={p} value={p} />)}</datalist>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => setRows((rs) => [...(rs ?? []), { model: "", provider: providers[0] ?? "openai", in: 0, out: 0, source: "admin" }])}>
              <Plus size={14} aria-hidden />Thêm model
            </Button>
            <Button variant="primary" disabled={busy} onClick={save}><Save size={14} aria-hidden />Lưu bảng giá</Button>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              Chỉ so sánh &quot;đổi model&quot; giữa các model cùng nhà cung cấp. Batch API của Anthropic được giảm 50%.
            </span>
          </div>
          {msg && <Notice tone={msg.tone}>{msg.tone === "ok" && <Check size={13} className="mr-1 inline" aria-hidden />}{msg.text}</Notice>}
        </div>
      )}
    </details>
  );
}
