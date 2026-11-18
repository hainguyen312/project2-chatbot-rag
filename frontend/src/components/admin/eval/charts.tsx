"use client";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Fmt, fmtValue, fmtWhen } from "./types";

export type Point = { x: string; y: number; id: string; note?: string };

/** Sparkline trong thẻ tổng quan: một chuỗi, không trục; điểm cuối có chấm. */
export function Sparkline({ points, fmt, width = 120, height = 32 }: { points: Point[]; fmt: Fmt; width?: number; height?: number }) {
  if (points.length < 2) return <div style={{ width, height }} />;
  const ys = points.map((p) => p.y);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = 4;
  const sx = (i: number) => pad + (i * (width - 2 * pad)) / (points.length - 1);
  const sy = (v: number) => (hi === lo ? height / 2 : height - pad - ((v - lo) * (height - 2 * pad)) / (hi - lo));
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.y).toFixed(1)}`).join("");
  const last = points[points.length - 1];
  return (
    <svg width={width} height={height} role="img" aria-label={`Xu hướng ${points.length} lần chạy, gần nhất ${fmtValue(last.y, fmt)}`}>
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {points.map((p, i) => (
        <circle key={p.id} cx={sx(i)} cy={sy(p.y)} r={i === points.length - 1 ? 3 : 6} fill={i === points.length - 1 ? "var(--accent)" : "transparent"}
          stroke={i === points.length - 1 ? "var(--bg-surface)" : "none"} strokeWidth={i === points.length - 1 ? 2 : 0}>
          <title>{`${fmtWhen(p.x)}: ${fmtValue(p.y, fmt)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

function niceTicks(lo: number, hi: number, n = 4): number[] {
  if (hi === lo) { const d = Math.abs(lo) * 0.1 || 1; lo -= d; hi += d; }
  const raw = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toFixed(10));
  if (out[out.length - 1] < hi) out.push(out[out.length - 1] + step);
  return out;
}

/** Biểu đồ xu hướng một chỉ số qua các lần chạy: đường 2px, crosshair + tooltip, bấm điểm để mở lần chạy. */
export function TrendChart({ points, fmt, label, onPick }: {
  points: Point[]; fmt: Fmt; label: string; onPick?: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const h = 220, ml = 56, mr = 16, mt = 12, mb = 28;
  if (points.length === 0) {
    return <div ref={ref} className="flex h-[220px] items-center justify-center text-sm" style={{ color: "var(--text-muted)" }}>
      Chưa có lần chạy nào có chỉ số này.</div>;
  }
  const ys = points.map((p) => p.y);
  let ticks = niceTicks(Math.min(...ys), Math.max(...ys));
  if (fmt === "pct") {  // tỉ lệ không vượt [0, 100%]
    const clipped = ticks.filter((t) => t >= 0 && t <= 1 + 1e-9);
    if (clipped.length >= 2) ticks = clipped;
  }
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const iw = w - ml - mr, ih = h - mt - mb;
  const sx = (i: number) => ml + (points.length === 1 ? iw / 2 : (i * iw) / (points.length - 1));
  const sy = (v: number) => mt + ih - ((v - lo) * ih) / (hi - lo || 1);
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.y).toFixed(1)}`).join("");
  // Số nhãn trục x theo chiều rộng (~120px / nhãn) để không chồng chữ ở màn hẹp.
  const nLab = Math.min(points.length, Math.max(2, Math.floor(iw / 120)));
  const xLabels = points.length === 1 ? [0]
    : [...new Set(Array.from({ length: nLab }, (_, k) => Math.round((k * (points.length - 1)) / (nLab - 1))))];

  const onMove = (e: ReactPointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - box.left;
    const i = points.length === 1 ? 0 : Math.round(((x) / box.width) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };
  const hp = hover !== null ? points[hover] : null;

  return (
    <div ref={ref} className="relative w-full min-w-0 overflow-hidden">
      <svg width={w} height={h} role="img" aria-label={`${label}: ${points.length} lần chạy`} className="block">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={ml} x2={w - mr} y1={sy(t)} y2={sy(t)} stroke="var(--border)" strokeWidth={1} />
            <text x={ml - 8} y={sy(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--text-muted)">{fmtValue(t, fmt)}</text>
          </g>
        ))}
        {xLabels.map((i) => (
          <text key={i} x={sx(i)} y={h - 8} fontSize={11} fill="var(--text-muted)"
            textAnchor={points.length === 1 ? "middle" : i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}>
            {fmtWhen(points[i].x)}
          </text>
        ))}
        <path d={d} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => (
          <circle key={p.id} cx={sx(i)} cy={sy(p.y)} r={hover === i ? 5 : 4} fill="var(--accent)" stroke="var(--bg-surface)" strokeWidth={2} />
        ))}
        {hp && hover !== null && (
          <line x1={sx(hover)} x2={sx(hover)} y1={mt} y2={mt + ih} stroke="var(--text-muted)" strokeWidth={1} strokeDasharray="3 3" />
        )}
        <rect x={ml - 8} y={mt} width={iw + 16} height={ih} fill="transparent" style={{ cursor: onPick ? "pointer" : "default" }}
          onPointerMove={onMove} onPointerLeave={() => setHover(null)}
          onClick={() => { if (hp && onPick) onPick(hp.id); }} />
      </svg>
      {hp && hover !== null && (
        <div className="pointer-events-none absolute z-10 rounded-lg px-2.5 py-1.5 text-xs shadow-sm"
          style={{ left: Math.min(Math.max(sx(hover) - 80, 0), w - 170), top: Math.max(sy(hp.y) - 64, 0), width: 170,
            background: "var(--bg-surface)", border: "1px solid var(--border-strong)", color: "var(--text-primary)" }}>
          <div style={{ color: "var(--text-muted)" }}>{fmtWhen(hp.x)}</div>
          <div className="font-semibold">{label}: {fmtValue(hp.y, fmt)}</div>
          {hp.note && <div className="truncate" style={{ color: "var(--text-secondary)" }}>{hp.note}</div>}
          {onPick && <div style={{ color: "var(--text-muted)" }}>Bấm để mở lần chạy</div>}
        </div>
      )}
    </div>
  );
}
