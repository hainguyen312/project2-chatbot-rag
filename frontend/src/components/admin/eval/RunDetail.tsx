"use client";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Check, ChevronDown, ChevronRight, Minus, Pencil, X } from "lucide-react";
import { useAdminFetch } from "../api";
import { Badge, Button, Card, Notice, inputCls, inputStyle } from "../ui";
import { selectCls } from "./types";
import { CancelButton, Delta, LogPanel, MetricTile, RunStatusBadge } from "./parts";
import { CostCard } from "./cost";
import {
  ACTIVE, CaseRow, Column, Group, RunDetail, RunSummary, Suite, Table, duration, fmtDelta, fmtValue, fmtWhen, judge,
  metricKeys, previousDone, specFor,
} from "./types";

function useRun(id: string) {
  const api = useAdminFetch();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    let cancelled = false;
    api<RunDetail>(`eval/runs/${encodeURIComponent(id)}`)
      .then((r) => { if (!cancelled) { setRun(r); setError(""); } })
      .catch((e) => { if (!cancelled) setError((e as Error).message); });
    return () => { cancelled = true; };
  }, [api, id, tick]);
  useEffect(() => {
    if (!run || !ACTIVE.has(run.status)) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [run, reload]);
  return { run, error, reload };
}

function PassMark({ pass }: { pass: boolean | null | undefined }) {
  if (pass === null || pass === undefined) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  return pass
    ? <span className="inline-flex items-center gap-1 text-xs font-medium" style={{ color: "var(--ok-text)" }}><Check size={13} aria-hidden />Đạt</span>
    : <span className="inline-flex items-center gap-1 text-xs font-medium" style={{ color: "var(--danger-text)" }}><X size={13} aria-hidden />Trượt</span>;
}

function Cell({ v, fmt }: { v: unknown; fmt: Column["fmt"] }) {
  if (fmt === "bool" || typeof v === "boolean") {
    if (v === null || v === undefined) return <>—</>;
    return v ? <Check size={14} aria-label="có" style={{ color: "var(--ok-text)" }} /> : <Minus size={14} aria-label="không" style={{ color: "var(--text-muted)" }} />;
  }
  return <>{fmtValue(v, fmt)}</>;
}

// ---------------------------------------------------------------- chi tiết một lần chạy
export function RunDetailView({ id, suite: suiteHint, suites, allRuns, onBack, onOpen }: {
  id: string; suite?: Suite; suites: Record<string, Suite>; allRuns: RunSummary[];
  onBack: () => void; onOpen: (id: string) => void;
}) {
  const api = useAdminFetch();
  const { run, error, reload } = useRun(id);
  const suite = suiteHint ?? (run ? suites[run.suite] : undefined);
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState("");

  if (error) return <div className="space-y-3"><BackBtn onBack={onBack} /><Notice tone="warn">{error}</Notice></div>;
  if (!run) return <p className="text-sm" style={{ color: "var(--text-muted)" }}>Đang tải…</p>;

  const prev = previousDone(allRuns, run);
  const keys = metricKeys(suite, [run.metrics]);
  const heads = keys.filter((k) => specFor(suite, k).headline);
  const rest = keys.filter((k) => !specFor(suite, k).headline);

  const saveLabel = async () => {
    try { await api(`eval/runs/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ label }) }); } catch (e) { alert((e as Error).message); }
    setEditing(false); reload();
  };

  return (
    <div className="space-y-4">
      <BackBtn onBack={onBack} />
      <Card className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold">{suite?.name ?? run.suite}</h3>
          <RunStatusBadge status={run.status} />
          {run.variant && <Badge tone="accent">{run.variant}</Badge>}
          <span className="ml-auto" />
          {ACTIVE.has(run.status) && <CancelButton runId={run.id} onDone={reload} />}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" style={{ color: "var(--text-secondary)" }}>
          <span>{fmtWhen(run.created_at)}</span>
          {run.started_at && <span>Thời lượng: {duration(run.started_at, run.finished_at)}</span>}
          <span>Nguồn: {run.source === "ui" ? `UI · ${run.started_by ?? ""}` : "CLI"}</span>
          <span className="font-mono">{run.id}</span>
          {prev && <button type="button" className="underline" onClick={() => onOpen(prev.id)}>Lần trước: {fmtWhen(prev.created_at)}</button>}
        </div>
        <div className="flex items-center gap-2 text-sm">
          {editing ? (
            <>
              <input className={inputCls} style={inputStyle} value={label} autoFocus maxLength={120}
                placeholder="Ghi chú, vd. “sau khi giảm RRF_W_VECTOR”" onChange={(e) => setLabel(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") saveLabel(); if (e.key === "Escape") setEditing(false); }} />
              <Button variant="primary" onClick={saveLabel}>Lưu</Button>
            </>
          ) : (
            <button type="button" className="inline-flex items-center gap-1.5 hover:underline" style={{ color: "var(--text-secondary)" }}
              onClick={() => { setLabel(run.label ?? ""); setEditing(true); }}>
              <Pencil size={13} aria-hidden />{run.label || "Thêm ghi chú"}
            </button>
          )}
        </div>
        {run.error && <Notice tone="warn">{run.error}</Notice>}
        {(run.params && Object.keys(run.params).length > 0) || Object.keys(run.config).length > 0 ? (
          <details className="text-xs">
            <summary className="cursor-pointer" style={{ color: "var(--text-secondary)" }}>Tham số</summary>
            <pre className="mt-1 overflow-auto rounded-lg p-2" style={{ background: "var(--bg-base)" }}>
              {JSON.stringify(run.params ?? run.config, null, 2)}
            </pre>
          </details>
        ) : null}
        {run.source === "ui" && <LogPanel runId={run.id} status={run.status} defaultOpen={ACTIVE.has(run.status) || run.status === "failed"} onStatus={reload} />}
      </Card>

      {keys.length > 0 && (
        <Card className="space-y-3">
          <div className="flex items-baseline gap-2">
            <h3 className="text-sm font-semibold">Chỉ số</h3>
            {prev && <span className="text-xs" style={{ color: "var(--text-muted)" }}>chênh lệch so với lần trước{run.variant ? " cùng biến thể" : ""}</span>}
          </div>
          {heads.length > 0 && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {heads.map((k) => <MetricTile key={k} spec={specFor(suite, k)} value={run.metrics[k]} prev={prev?.metrics[k]} />)}
            </div>
          )}
          {rest.length > 0 && (
            <details open={heads.length === 0}>
              <summary className="cursor-pointer text-sm" style={{ color: "var(--text-secondary)" }}>Tất cả chỉ số ({rest.length})</summary>
              <MetricTable suite={suite} keys={rest} a={prev?.metrics} b={run.metrics} aLabel="Lần trước" bLabel="Lần này" />
            </details>
          )}
        </Card>
      )}

      {run.cost && <CostCard cost={run.cost} />}
      {run.tables.map((t) => <MatrixCard key={t.title} table={t} />)}
      {run.groups.map((g) => <CaseTable key={g.id} group={g} />)}
    </div>
  );
}

function BackBtn({ onBack }: { onBack: () => void }) {
  return <Button onClick={onBack}><ArrowLeft size={14} aria-hidden />Quay lại</Button>;
}

function MetricTable({ suite, keys, a, b, aLabel, bLabel }: {
  suite?: Suite; keys: string[]; a?: Record<string, number>; b: Record<string, number>; aLabel: string; bLabel: string;
}) {
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs" style={{ color: "var(--text-secondary)" }}>
            <th className="py-1.5 pr-3 font-medium">Chỉ số</th>
            {a && <th className="py-1.5 pr-3 text-right font-medium">{aLabel}</th>}
            <th className="py-1.5 pr-3 text-right font-medium">{bLabel}</th>
            {a && <th className="py-1.5 text-right font-medium">Chênh lệch</th>}
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const s = specFor(suite, k);
            return (
              <tr key={k} style={{ borderTop: "1px solid var(--border)" }}>
                <td className="py-1.5 pr-3">{s.label}</td>
                {a && <td className="py-1.5 pr-3 text-right tabular-nums">{fmtValue(a[k], s.fmt)}</td>}
                <td className="py-1.5 pr-3 text-right font-medium tabular-nums">{fmtValue(b[k], s.fmt)}</td>
                {a && <td className="py-1.5 text-right"><Delta cur={b[k]} prev={a[k]} fmt={s.fmt} better={s.better} /></td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Bảng pipeline × chỉ số; giá trị tốt nhất mỗi cột in đậm (giả định cao hơn là tốt hơn). */
function MatrixCard({ table }: { table: Table }) {
  const best = table.columns.map((_, j) => Math.max(...table.rows.map((r) => r.values[j] ?? -Infinity)));
  return (
    <Card className="space-y-2">
      <h3 className="text-sm font-semibold">{table.title}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs" style={{ color: "var(--text-secondary)" }}>
              <th className="py-1.5 pr-3 font-medium" />
              {table.columns.map((c) => <th key={c} className="py-1.5 pr-3 text-right font-medium">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r) => (
              <tr key={r.label} style={{ borderTop: "1px solid var(--border)" }}>
                <td className="py-1.5 pr-3">{r.label}</td>
                {r.values.map((v, j) => (
                  <td key={j} className="py-1.5 pr-3 text-right tabular-nums"
                    style={{ fontWeight: table.rows.length > 1 && v === best[j] ? 700 : 400 }}>{fmtValue(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.rows.length > 1 && <p className="text-xs" style={{ color: "var(--text-muted)" }}>In đậm: cao nhất mỗi cột.</p>}
    </Card>
  );
}

const PAGE = 50;

function CaseTable({ group }: { group: Group }) {
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"all" | "fail" | "pass">("all");
  const [cat, setCat] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const hasPass = group.rows.some((r) => r.pass !== null);
  const cats = [...new Set(group.rows.map((r) => r.category).filter(Boolean))] as string[];
  const nPass = group.rows.filter((r) => r.pass === true).length;
  const nFail = group.rows.filter((r) => r.pass === false).length;

  const rows = useMemo(() => group.rows.filter((r) =>
    (only === "all" || (only === "fail" ? r.pass === false : r.pass === true)) &&
    (!cat || r.category === cat) &&
    (!q || `${r.label} ${r.id}`.toLowerCase().includes(q.toLowerCase()))), [group.rows, only, cat, q]);

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-semibold">{group.title}</h3>
        {hasPass && <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{nPass} đạt · {nFail} trượt</span>}
      </div>
      <div className="flex flex-wrap gap-2">
        <input className={`${selectCls} min-w-40 flex-1`} style={inputStyle} placeholder="Tìm…" value={q} onChange={(e) => setQ(e.target.value)} />
        {hasPass && (
          <select aria-label="Lọc kết quả" className={selectCls} style={inputStyle} value={only} onChange={(e) => setOnly(e.target.value as typeof only)}>
            <option value="all">Tất cả</option><option value="fail">Chỉ trượt</option><option value="pass">Chỉ đạt</option>
          </select>
        )}
        {cats.length > 1 && (
          <select aria-label="Lọc nhóm" className={`${selectCls} max-w-[40%]`} style={inputStyle} value={cat} onChange={(e) => setCat(e.target.value)}>
            <option value="">Mọi nhóm</option>{cats.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs" style={{ color: "var(--text-secondary)" }}>
              <th className="w-6" />
              <th className="py-1.5 pr-3 font-medium">Case</th>
              {hasPass && <th className="py-1.5 pr-3 font-medium">Kết quả</th>}
              {group.columns.map((c) => <th key={c.key} className="py-1.5 pr-3 font-medium">{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, limit).map((r) => {
              const isOpen = open === r.id;
              const hasDetail = Object.keys(r.detail ?? {}).length > 0;
              return (
                <Fragment key={r.id}>
                  <tr style={{ borderTop: "1px solid var(--border)" }} className={hasDetail ? "cursor-pointer hover:bg-[var(--bg-hover)]" : ""}
                    onClick={() => hasDetail && setOpen(isOpen ? null : r.id)} aria-expanded={hasDetail ? isOpen : undefined}>
                    <td className="py-1.5 align-top">{hasDetail && (isOpen ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />)}</td>
                    <td className="max-w-md py-1.5 pr-3 align-top">
                      <div className="line-clamp-2">{r.label}</div>
                      {r.category && <div className="text-xs" style={{ color: "var(--text-muted)" }}>{r.category}</div>}
                    </td>
                    {hasPass && <td className="py-1.5 pr-3 align-top"><PassMark pass={r.pass} /></td>}
                    {group.columns.map((c) => (
                      <td key={c.key} className="max-w-xs py-1.5 pr-3 align-top tabular-nums">
                        <span className="line-clamp-2"><Cell v={r.cells[c.key]} fmt={c.fmt} /></span>
                      </td>
                    ))}
                  </tr>
                  {isOpen && (
                    <tr><td colSpan={group.columns.length + 3} className="pb-3"><DetailBox detail={r.detail} /></td></tr>
                  )}
                </Fragment>
              );
            })}
            {rows.length === 0 && (
              <tr><td colSpan={group.columns.length + 3} className="py-6 text-center text-sm" style={{ color: "var(--text-muted)" }}>Không có case phù hợp.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {rows.length > limit && <Button onClick={() => setLimit((l) => l + PAGE)}>Xem thêm ({rows.length - limit})</Button>}
    </Card>
  );
}

/** Chi tiết một case: chuỗi dài (câu trả lời, lỗi) hiển thị như văn bản, phần còn lại dạng JSON. */
function DetailBox({ detail }: { detail: Record<string, unknown> }) {
  const texts = Object.entries(detail).filter(([, v]) => typeof v === "string" && (v as string).length > 80);
  const rest = Object.fromEntries(Object.entries(detail).filter(([k, v]) => v !== null && v !== undefined && !texts.some(([t]) => t === k)));
  return (
    <div className="space-y-2 rounded-lg p-3 text-xs" style={{ background: "var(--bg-base)", border: "1px solid var(--border)" }}>
      {texts.map(([k, v]) => (
        <div key={k}>
          <div className="mb-0.5 font-medium" style={{ color: "var(--text-secondary)" }}>{k}</div>
          <div className="max-h-64 overflow-auto whitespace-pre-wrap leading-relaxed">{v as string}</div>
        </div>
      ))}
      {Object.keys(rest).length > 0 && (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap leading-relaxed">{JSON.stringify(rest, null, 2)}</pre>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- so sánh hai lần chạy
export function CompareView({ a, b, suites, onBack, onOpen }: {
  a: string; b: string; suites: Record<string, Suite>; onBack: () => void; onOpen: (id: string) => void;
}) {
  const A = useRun(a), B = useRun(b);
  const [onlyChanged, setOnlyChanged] = useState(true);
  if (A.error || B.error) return <div className="space-y-3"><BackBtn onBack={onBack} /><Notice tone="warn">{A.error || B.error}</Notice></div>;
  if (!A.run || !B.run) return <p className="text-sm" style={{ color: "var(--text-muted)" }}>Đang tải…</p>;
  const ra = A.run, rb = B.run;
  const suite = suites[rb.suite];
  const keys = metricKeys(suite, [ra.metrics, rb.metrics]);
  const regress = keys.filter((k) => ra.metrics[k] !== undefined && rb.metrics[k] !== undefined
    && judge(rb.metrics[k] - ra.metrics[k], specFor(suite, k).better) < 0);

  return (
    <div className="space-y-4">
      <BackBtn onBack={onBack} />
      <Card className="space-y-2">
        <h3 className="text-base font-semibold">So sánh · {suite?.name ?? rb.suite}</h3>
        <div className="grid gap-2 text-sm sm:grid-cols-2">
          {[["A (cũ hơn)", ra], ["B (mới hơn)", rb]].map(([t, r]) => {
            const run = r as RunDetail;
            return (
              <button key={run.id} type="button" onClick={() => onOpen(run.id)} className="rounded-lg p-2 text-left hover:bg-[var(--bg-hover)]"
                style={{ border: "1px solid var(--border)" }}>
                <div className="text-xs" style={{ color: "var(--text-muted)" }}>{t as string}</div>
                <div className="font-medium">{fmtWhen(run.created_at)} {run.variant && <Badge tone="accent">{run.variant}</Badge>}</div>
                {run.label && <div className="text-xs" style={{ color: "var(--text-secondary)" }}>{run.label}</div>}
              </button>
            );
          })}
        </div>
        {ra.variant !== rb.variant && <Notice tone="muted">Hai lần chạy khác biến thể ({ra.variant ?? "—"} / {rb.variant ?? "—"}).</Notice>}
        {regress.length > 0
          ? <Notice tone="warn">B kém hơn A ở {regress.length} chỉ số: {regress.map((k) => specFor(suite, k).label).join(", ")}.</Notice>
          : <Notice tone="ok">B không kém hơn A ở chỉ số nào có chiều tốt/xấu.</Notice>}
      </Card>

      <Card>
        <h3 className="text-sm font-semibold">Chỉ số</h3>
        <MetricTable suite={suite} keys={keys} a={ra.metrics} b={rb.metrics} aLabel="A" bLabel="B" />
      </Card>

      {rb.groups.map((gb) => {
        const ga = ra.groups.find((g) => g.id === gb.id);
        return ga ? <CaseDiff key={gb.id} a={ga} b={gb} onlyChanged={onlyChanged} setOnlyChanged={setOnlyChanged} /> : null;
      })}
    </div>
  );
}

function CaseDiff({ a, b, onlyChanged, setOnlyChanged }: {
  a: Group; b: Group; onlyChanged: boolean; setOnlyChanged: (v: boolean) => void;
}) {
  const amap = new Map(a.rows.map((r) => [r.id, r]));
  const bmap = new Map(b.rows.map((r) => [r.id, r]));
  const ids = [...new Set([...b.rows.map((r) => r.id), ...a.rows.map((r) => r.id)])];
  const numCols = b.columns.filter((c) => !["text", "bool"].includes(c.fmt));
  type D = { id: string; ra?: CaseRow; rb?: CaseRow; kind: "regress" | "fix" | "changed" | "same" | "added" | "removed" };
  const diffs: D[] = ids.map((id) => {
    const ra = amap.get(id), rb = bmap.get(id);
    if (!ra) return { id, rb, kind: "added" };
    if (!rb) return { id, ra, kind: "removed" };
    if (ra.pass === true && rb.pass === false) return { id, ra, rb, kind: "regress" };
    if (ra.pass === false && rb.pass === true) return { id, ra, rb, kind: "fix" };
    // Thời gian / chi phí lần nào cũng lệch chút ít → không tính là "thay đổi" của case.
    const changed = b.columns.filter((c) => c.fmt !== "sec" && c.fmt !== "usd")
      .some((c) => JSON.stringify(ra.cells[c.key]) !== JSON.stringify(rb.cells[c.key]));
    return { id, ra, rb, kind: changed ? "changed" : "same" };
  });
  const order = { regress: 0, fix: 1, changed: 2, added: 3, removed: 4, same: 5 };
  diffs.sort((x, y) => order[x.kind] - order[y.kind]);
  const shown = onlyChanged ? diffs.filter((d) => d.kind !== "same") : diffs;
  const count = (k: D["kind"]) => diffs.filter((d) => d.kind === k).length;
  const KIND = {
    regress: { t: "Mới trượt", c: "var(--danger-text)" }, fix: { t: "Mới đạt", c: "var(--ok-text)" },
    changed: { t: "Thay đổi", c: "var(--text-secondary)" }, same: { t: "Như cũ", c: "var(--text-muted)" },
    added: { t: "Chỉ có ở B", c: "var(--text-secondary)" }, removed: { t: "Chỉ có ở A", c: "var(--text-secondary)" },
  } as const;

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-semibold">{b.title}</h3>
        <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
          {count("regress")} mới trượt · {count("fix")} mới đạt · {count("changed")} thay đổi
        </span>
        <label className="flex items-center gap-1.5 text-xs">
          <input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} />Chỉ case khác nhau
        </label>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs" style={{ color: "var(--text-secondary)" }}>
              <th className="py-1.5 pr-3 font-medium">Case</th>
              <th className="py-1.5 pr-3 font-medium">Thay đổi</th>
              {numCols.map((c) => <th key={c.key} className="py-1.5 pr-3 text-right font-medium">{c.label} (A → B)</th>)}
            </tr>
          </thead>
          <tbody>
            {shown.slice(0, 300).map((d) => (
              <tr key={d.id} style={{ borderTop: "1px solid var(--border)" }}>
                <td className="max-w-md py-1.5 pr-3"><div className="line-clamp-2">{(d.rb ?? d.ra)?.label}</div></td>
                <td className="py-1.5 pr-3 text-xs font-medium whitespace-nowrap" style={{ color: KIND[d.kind].c }}>
                  {d.kind === "regress" ? <X size={12} className="mr-1 inline" aria-hidden /> : d.kind === "fix" ? <Check size={12} className="mr-1 inline" aria-hidden /> : null}
                  {KIND[d.kind].t}
                </td>
                {numCols.map((c) => {
                  const va = d.ra?.cells[c.key], vb = d.rb?.cells[c.key];
                  const both = typeof va === "number" && typeof vb === "number";
                  return (
                    <td key={c.key} className="py-1.5 pr-3 text-right tabular-nums whitespace-nowrap">
                      {fmtValue(va, c.fmt)} → {fmtValue(vb, c.fmt)}
                      {both && va !== vb && c.fmt !== "rank" && (
                        <span className="ml-1 text-xs" style={{ color: "var(--text-muted)" }}>({fmtDelta((vb as number) - (va as number), c.fmt)})</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
            {shown.length === 0 && (
              <tr><td colSpan={numCols.length + 2} className="py-6 text-center text-sm" style={{ color: "var(--text-muted)" }}>Không có case nào khác nhau.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {shown.length > 300 && <p className="text-xs" style={{ color: "var(--text-muted)" }}>Hiển thị 300 / {shown.length} case.</p>}
    </Card>
  );
}
