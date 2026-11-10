"use client";

import { useRef, useState } from "react";
import { playerInput } from "./runtime";

/** Joystick ảo (trái) + vùng vuốt để nhìn (phải) cho điện thoại / máy tính bảng */
export function TouchControls() {
  const base = useRef<HTMLDivElement>(null);
  const [knob, setKnob] = useState<{ x: number; y: number } | null>(null);
  const stickId = useRef<number | null>(null);
  const lookId = useRef<number | null>(null);
  const last = useRef({ x: 0, y: 0 });
  const R = 48;

  const updateStick = (clientX: number, clientY: number) => {
    const el = base.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    let dx = clientX - (rect.left + rect.width / 2);
    let dy = clientY - (rect.top + rect.height / 2);
    const d = Math.hypot(dx, dy);
    if (d > R) {
      dx = (dx / d) * R;
      dy = (dy / d) * R;
    }
    setKnob({ x: dx, y: dy });
    playerInput.right = dx / R;
    playerInput.forward = -dy / R;
    playerInput.run = d > R * 0.95;
  };
  const releaseStick = () => {
    stickId.current = null;
    setKnob(null);
    playerInput.forward = 0;
    playerInput.right = 0;
    playerInput.run = false;
  };

  return (
    <>
      {/* vùng nhìn: nửa phải màn hình */}
      <div
        className="absolute inset-y-0 right-0 z-10 w-1/2 touch-none"
        onPointerDown={(e) => {
          if (e.pointerType === "mouse") return;
          lookId.current = e.pointerId;
          last.current = { x: e.clientX, y: e.clientY };
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (e.pointerId !== lookId.current) return;
          playerInput.lookDX += (e.clientX - last.current.x) * 1.4;
          playerInput.lookDY += (e.clientY - last.current.y) * 1.4;
          last.current = { x: e.clientX, y: e.clientY };
        }}
        onPointerUp={(e) => {
          if (e.pointerId === lookId.current) lookId.current = null;
        }}
        onPointerCancel={() => (lookId.current = null)}
      />
      {/* joystick */}
      <div
        ref={base}
        className="absolute bottom-24 left-6 z-20 flex h-28 w-28 touch-none items-center justify-center rounded-full border border-white/25 bg-white/10 backdrop-blur-sm"
        onPointerDown={(e) => {
          stickId.current = e.pointerId;
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
          updateStick(e.clientX, e.clientY);
        }}
        onPointerMove={(e) => {
          if (e.pointerId === stickId.current) updateStick(e.clientX, e.clientY);
        }}
        onPointerUp={(e) => {
          if (e.pointerId === stickId.current) releaseStick();
        }}
        onPointerCancel={releaseStick}
      >
        <div
          className="pointer-events-none h-12 w-12 rounded-full bg-white/70 shadow-lg"
          style={{ transform: `translate(${knob?.x ?? 0}px, ${knob?.y ?? 0}px)` }}
        />
      </div>
    </>
  );
}
