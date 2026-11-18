"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, GitCompare, Play, RefreshCw } from "lucide-react";
import { useAdminFetch } from "../api";
import { Badge, Button, Card, Notice, inputStyle } from "../ui";
import { selectCls } from "./types";
import { Sparkline, TrendChart } from "./charts";
import { CancelButton, Delta, LaunchDialog, LogPanel, MetricTile, RunStatusBadge } from "./parts";
import { CompareView, RunDetailView } from "./RunDetail";
import { CostOverview } from "./cost";
import {
  ACTIVE, KIND_LABEL, RunSummary, Suite, duration, fmtValue, fmtWhen, metricKeys, previousDone, specFor,
} from "./types";

type View = { kind: "list" } | { kind: "run"; id: string } | { kind: "compare"; a: string; b: string };

export function EvalTab({ initialSuite = "", initialRun, initialCompare }: {
  initialSuite?: string; initialRun?: string; initialCompare?: [string, string];
} = {}) {
  const api = useAdminFetch();
  const [suites, setSuites] = useState<Suite[] | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<string>(initialSuite);          // "" = tổng quan
  const [view, setView] = useState<View>(
    initialCompare ? { kind: "compare", a: initialCompare[0], b: initialCompare[1] }
      : initialRun ? { kind: "run", id: initialRun } : { kind: "list" });
  const [launch, setLaunch] = useState<Suite | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    api<{ suites: Suite[] }>("eval/suites").then((r) => setSuites(r.suites)).catch((e) => setError((e as Error).message));
  }, [api]);

  useEffect(() => {
    let cancelled = false;
    api<{ runs: RunSummary[] }>("eval/runs?limit=500")
      .then((r) => { if (!cancelled) { setRuns(r.runs); setError(""); } })
      .catch((e) => { if (!cancelled) setError((e as Error).message); });
    return () => { cancelled = true; };
  }, [api, tick]);

  const anyActive = runs.some((r) => ACTIVE.has(r.status));
  useEffect(() => {
    if (!anyActive) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [anyActive, reload]);

  const byId = useMemo(() => Object.fromEntries((suites ?? []).map((s) => [s.id, s])), [suites]);

  if (view.kind === "run") {
    return <RunDetailView id={view.id} suite={byId[runs.find((r) => r.id === view.id)?.suite ?? ""]} suites={byId}
      allRuns={runs} onBack={() => { setView({ kind: "list" }); reload(); }}
      onOpen={(id) => setView({ kind: "run", id })} />;
  }
  if (view.kind === "compare") {
    return <CompareView a={view.a} b={view.b} suites={byId} onBack={() => setView({ kind: "list" })}
      onOpen={(id) => setView({ kind: "run", id })} />;
  }

  const active = runs.filter((r) => ACTIVE.has(r.status));
  const suite = filter ? byId[filter] : undefined;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {[{ id: "", name: "Tổng quan" }, ...(suites ?? [])].map((s) => (
          <button key={s.id} type="button" onClick={() => setFilter(s.id)} aria-pressed={filter === s.id}
            className="rounded-full px-3 py-1 text-sm transition-colors hover:bg-[var(--bg-hover)]"
            style={{ background: filter === s.id ? "var(--bg-hover)" : undefined, fontWeight: filter === s.id ? 600 : 400,
              color: filter === s.id ? "var(--text-primary)" : "var(--text-secondary)" }}>
            {s.name}
          </button>
        ))}
        <span className="ml-auto" />
        <Button onClick={reload}><RefreshCw size={14} aria-hidden />Làm mới</Button>
      </div>

      {error && <Notice tone="warn">{error}</Notice>}

      {active.length > 0 && (
        <Card className="space-y-3">
          <h3 className="text-sm font-semibold">Đang chạy / chờ ({active.length})</h3>
          {active.map((r) => (
            <div key={r.id} className="space-y-2 rounded-lg p-3" style={{ background: "var(--bg-base)", border: "1px solid var(--border)" }}>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <RunStatusBadge status={r.status} />
                <span className="font-medium">{byId[r.suite]?.name ?? r.suite}</span>
                {r.variant && <Badge>{r.variant}</Badge>}
                <span style={{ color: "var(--text-muted)" }}>
                  {r.started_at ? `chạy được ${duration(r.started_at)}` : `tạo lúc ${fmtWhen(r.created_at)}`} · {r.started_by}
                </span>
                <span className="ml-auto" />
                <CancelButton runId={r.id} onDone={reload} />
              </div>
              <LogPanel runId={r.id} status={r.status} onStatus={reload} />
            </div>
          ))}
        </Card>
      )}

      {!suites ? (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>Đang tải…</p>
      ) : !suite ? (
        <div className="space-y-4">
        <CostOverview suites={suites} runs={runs} />
        <div className="grid gap-3 md:grid-cols-2">
          {suites.map((s) => (
            <SuiteCard key={s.id} suite={s} runs={runs.filter((r) => r.suite === s.id)}
              onOpen={() => setFilter(s.id)} onRun={() => setLaunch(s)}
              onOpenRun={(id) => setView({ kind: "run", id })} />
          ))}
        </div>
        </div>
      ) : (
        <SuiteView key={suite.id} suite={suite} runs={runs.filter((r) => r.suite === suite.id)} onRun={() => setLaunch(suite)}
          onOpenRun={(id) => setView({ kind: "run", id })}
          onCompare={(a, b) => setView({ kind: "compare", a, b })} />
      )}

      {launch && (
        <LaunchDialog suite={launch} onClose={() => setLaunch(null)}
          onStarted={() => { setLaunch(null); setFilter(launch.id); reload(); }} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- thẻ tổng quan
function SuiteCard({ suite, runs, onOpen, onRun, onOpenRun }: {
  suite: Suite; runs: RunSummary[]; onOpen: () => void; onRun: () => void; onOpenRun: (id: string) => void;
}) {
  const done = runs.filter((r) => r.status === "done");
  const latest = done[0];
  const prev = latest ? previousDone(runs, latest) : undefined;
  const heads = suite.metrics.filter((m) => m.headline && latest?.metrics[m.key] !== undefined).slice(0, 4);
  const main = heads[0];
  const spark = main
    ? done.filter((r) => r.metrics[main.key] !== undefined && (r.variant ?? null) === (latest?.variant ?? null))
      .slice(0, 20).reverse().map((r) => ({ x: r.created_at ?? "", y: r.metrics[main.key], id: r.id }))
    : [];
  const lastAny = runs[0];

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">{suite.name}</h3>
            <Badge>{KIND_LABEL[suite.kind]}</Badge>
            {latest?.variant && <Badge tone="accent">{latest.variant}</Badge>}
          </div>
          <p className="mt-0.5 line-clamp-2 text-xs" style={{ color: "var(--text-secondary)" }}>{suite.description}</p>
        </div>
        {main && spark.length > 1 && <Sparkline points={spark} fmt={main.fmt} />}
      </div>

      {latest ? (
        <div className="grid grid-cols-2 gap-2">
          {heads.map((m) => <MetricTile key={m.key} spec={m} value={latest.metrics[m.key]} prev={prev?.metrics[m.key]} />)}
        </div>
      ) : (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>Chưa có lần chạy nào.</p>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
        {lastAny && (
          <button type="button" className="inline-flex items-center gap-1.5 hover:underline" onClick={() => onOpenRun(lastAny.id)}>
            <RunStatusBadge status={lastAny.status} />{fmtWhen(lastAny.created_at)}
          </button>
        )}
        <span>· {runs.length} lần chạy</span>
        <span className="ml-auto" />
        <Button onClick={onOpen}>Chi tiết</Button>
        <Button variant="primary" onClick={onRun}><Play size={13} aria-hidden />Chạy</Button>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- một suite: xu hướng + danh sách
function SuiteView({ suite, runs, onRun, onOpenRun, onCompare }: {
  suite: Suite; runs: RunSummary[]; onRun: () => void; onOpenRun: (id: string) => void; onCompare: (a: string, b: string) => void;
}) {
  const done = runs.filter((r) => r.status === "done");
  const keys = metricKeys(suite, done.map((r) => r.metrics));
  const variants = [...new Set(done.map((r) => r.variant).filter(Boolean))] as string[];
  const firstHead = suite.metrics.find((m) => m.headline && keys.includes(m.key))?.key ?? keys[0] ?? "";
  const [metric, setMetric] = useState(firstHead);
  const [variant, setVariant] = useState<string>(done[0]?.variant ?? "");
  const [picked, setPicked] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<"all" | "done" | "problem">("all");

  // Dữ liệu có thể về sau lần mount đầu → state rỗng/không hợp lệ thì lấy mặc định từ dữ liệu hiện tại.
  const m = metric && keys.includes(metric) ? metric : firstHead;
  const v = variant && variants.includes(variant) ? variant : (done[0]?.variant ?? "");
  const spec = specFor(suite, m);
  const points = done
    .filter((r) => r.metrics[m] !== undefined && (!variants.length || !v || r.variant === v))
    .slice().reverse()
    .map((r) => ({ x: r.created_at ?? "", y: r.metrics[m], id: r.id, note: [r.variant, r.label].filter(Boolean).join(" · ") }));

  const cols = suite.metrics.filter((x) => x.headline).slice(0, 4);
  const shown = runs.filter((r) => statusFilter === "all" || (statusFilter === "done" ? r.status === "done"
    : ["failed", "interrupted", "cancelled"].includes(r.status)));
  const toggle = (id: string) => setPicked((p) => p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]);

  return (
    <div className="space-y-4">
      <Card className="space-y-2">
        <div className="flex flex-wrap items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold">{suite.name}</h3><Badge>{KIND_LABEL[suite.kind]}</Badge>
            </div>
            <p className="mt-0.5 text-sm" style={{ color: "var(--text-secondary)" }}>{suite.description}</p>
            {suite.requires.length > 0 && <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>Cần: {suite.requires.join(", ")}</p>}
          </div>
          <Button variant="primary" onClick={onRun}><Play size={14} aria-hidden />Chạy</Button>
        </div>
      </Card>

      <Card className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="mr-auto text-sm font-semibold">Xu hướng</h3>
          {variants.length > 1 && (
            <select aria-label={suite.variant_label || "Biến thể"} className={selectCls} style={inputStyle}
              value={v} onChange={(e) => setVariant(e.target.value)}>
              {variants.map((x) => <option key={x} value={x}>{suite.variant_label ? `${suite.variant_label}: ` : ""}{x}</option>)}
            </select>
          )}
          <select aria-label="Chỉ số" className={`${selectCls} max-w-full`} style={inputStyle} value={m}
            onChange={(e) => setMetric(e.target.value)}>
            {keys.map((k) => <option key={k} value={k}>{specFor(suite, k).label}</option>)}
          </select>
        </div>
        <TrendChart points={points} fmt={spec.fmt} label={spec.label} onPick={onOpenRun} />
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          {spec.better === "up" ? "Cao hơn là tốt hơn." : spec.better === "down" ? "Thấp hơn là tốt hơn." : ""}
          {variants.length > 1 && " Chỉ so các lần chạy cùng biến thể."}
        </p>
      </Card>

      <Card className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold">Các lần chạy ({runs.length})</h3>
          <select aria-label="Lọc trạng thái" className={selectCls} style={inputStyle} value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}>
            <option value="all">Tất cả</option><option value="done">Xong</option><option value="problem">Lỗi / huỷ</option>
          </select>
          <span className="ml-auto text-xs" style={{ color: "var(--text-muted)" }}>Chọn 2 lần chạy để so sánh</span>
          <Button disabled={picked.length !== 2} onClick={() => {
            const [a, b] = [...picked].sort((x, y) => (runs.find((r) => r.id === x)?.created_at ?? "").localeCompare(runs.find((r) => r.id === y)?.created_at ?? ""));
            onCompare(a, b);
          }}><GitCompare size={14} aria-hidden />So sánh</Button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs" style={{ color: "var(--text-secondary)" }}>
                <th className="w-8 py-2" />
                <th className="py-2 pr-3 font-medium">Thời gian</th>
                <th className="py-2 pr-3 font-medium">Trạng thái</th>
                {variants.length > 0 && <th className="py-2 pr-3 font-medium">{suite.variant_label || "Biến thể"}</th>}
                {cols.map((c) => <th key={c.key} className="py-2 pr-3 text-right font-medium">{c.label}</th>)}
                <th className="py-2 pr-3 font-medium">Nguồn</th>
                <th className="w-6" />
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const prev = previousDone(runs, r);
                return (
                  <tr key={r.id} className="cursor-pointer hover:bg-[var(--bg-hover)]" style={{ borderTop: "1px solid var(--border)" }}
                    onClick={() => onOpenRun(r.id)}>
                    <td className="py-2" onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" aria-label={`Chọn ${r.id} để so sánh`} disabled={r.status !== "done"}
                        checked={picked.includes(r.id)} onChange={() => toggle(r.id)} />
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap">
                      {fmtWhen(r.created_at)}
                      {r.label && <div className="text-xs" style={{ color: "var(--text-muted)" }}>{r.label}</div>}
                    </td>
                    <td className="py-2 pr-3"><RunStatusBadge status={r.status} /></td>
                    {variants.length > 0 && <td className="py-2 pr-3">{r.variant ?? "—"}</td>}
                    {cols.map((c) => (
                      <td key={c.key} className="py-2 pr-3 text-right tabular-nums whitespace-nowrap">
                        {fmtValue(r.metrics[c.key], c.fmt)}
                        <div><Delta cur={r.metrics[c.key]} prev={prev?.metrics[c.key]} fmt={c.fmt} better={c.better} /></div>
                      </td>
                    ))}
                    <td className="py-2 pr-3 text-xs" style={{ color: "var(--text-muted)" }}>
                      {r.source === "ui" ? r.started_by ?? "UI" : "CLI"}
                    </td>
                    <td><ChevronRight size={15} style={{ color: "var(--text-muted)" }} aria-hidden /></td>
                  </tr>
                );
              })}
              {shown.length === 0 && (
                <tr><td colSpan={8} className="py-6 text-center text-sm" style={{ color: "var(--text-muted)" }}>Không có lần chạy.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
