"use client";

import { Html, Line, RoundedBox } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { useDeskDocs, usePassages, useViewMode } from "./director";
import { makeParquetTexture, makeRugTexture, std } from "./materials";
import { CHAIRS, ROOM, STATIONS } from "./officeData";

const WALL_H = 3.2;
const WOOD = "#9a6a3f";
const WOOD_DARK = "#5f3d22";
const WALL = "#efe6d8";
const WAINSCOT = "#7b5537";

function rng(seed: number) {
  return () => {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  };
}

type V3 = [number, number, number];

function RBox({ p, s, c, r, radius = 0.04, rough = 0.7, metal = 0 }: { p: V3; s: V3; c: string; r?: V3; radius?: number; rough?: number; metal?: number }) {
  return (
    <RoundedBox args={s} radius={Math.min(radius, Math.min(...s) / 2 - 0.001)} smoothness={3} position={p} rotation={r} castShadow receiveShadow material={std(c, rough, metal)} />
  );
}
function Box({ p, s, c, r, emissive, intensity = 0.8 }: { p: V3; s: V3; c: string; r?: V3; emissive?: string; intensity?: number }) {
  return (
    <mesh position={p} rotation={r} castShadow receiveShadow>
      <boxGeometry args={s} />
      {emissive ? (
        <meshStandardMaterial color={c} emissive={emissive} emissiveIntensity={intensity} />
      ) : (
        <primitive object={std(c)} attach="material" />
      )}
    </mesh>
  );
}

// ── Nội thất ─────────────────────────────────────────────────────────────────
function Desk({ x, z, w, d, rot = 0, top = WOOD }: { x: number; z: number; w: number; d: number; rot?: number; top?: string }) {
  return (
    <group position={[x, 0, z]} rotation-y={rot}>
      <RBox p={[0, 0.76, 0]} s={[w, 0.07, d]} c={top} radius={0.03} rough={0.45} />
      <RBox p={[0, 0.4, -d / 2 + 0.06]} s={[w - 0.12, 0.66, 0.05]} c={WOOD_DARK} radius={0.02} />
      {[-1, 1].map((sx) => (
        <RBox key={sx} p={[(sx * (w - 0.14)) / 2, 0.38, 0]} s={[0.07, 0.76, d - 0.06]} c={WOOD_DARK} radius={0.02} />
      ))}
    </group>
  );
}

function Chair({ x, z, facing, color }: { x: number; z: number; facing: number; color: string }) {
  // ghế văn phòng: lưng ghế ở phía sau người ngồi
  return (
    <group position={[x, 0, z]} rotation-y={facing}>
      <RBox p={[0, 0.4, 0]} s={[0.55, 0.09, 0.52]} c={color} radius={0.04} rough={0.9} />
      <RBox p={[0, 0.82, -0.27]} s={[0.52, 0.66, 0.08]} c={color} radius={0.05} rough={0.9} />
      <mesh position={[0, 0.2, 0]} castShadow>
        <cylinderGeometry args={[0.03, 0.03, 0.36, 8]} />
        <primitive object={std("#9ca3af", 0.3, 0.8)} attach="material" />
      </mesh>
      {[0, 1, 2, 3, 4].map((i) => (
        <mesh key={i} position={[Math.sin((i / 5) * Math.PI * 2) * 0.2, 0.04, Math.cos((i / 5) * Math.PI * 2) * 0.2]} rotation-y={(i / 5) * Math.PI * 2}>
          <boxGeometry args={[0.04, 0.03, 0.42]} />
          <primitive object={std("#4b5563", 0.4, 0.6)} attach="material" />
        </mesh>
      ))}
    </group>
  );
}

const _m = new THREE.Matrix4();
const _c = new THREE.Color();
function Bookshelf({ x, z, seed }: { x: number; z: number; seed: number }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const books = useMemo(() => {
    const r = rng(seed);
    const palette = ["#7f1d1d", "#1e3a8a", "#14532d", "#78350f", "#4c1d95", "#0f766e", "#9a3412", "#334155", "#a16207"];
    const out: { y: number; z: number; h: number; w: number; tilt: number; c: string }[] = [];
    for (let shelf = 0; shelf < 5; shelf++) {
      let zz = -1.15;
      while (zz < 1.08) {
        const w = 0.06 + r() * 0.07;
        const h = 0.3 + r() * 0.14;
        const tilt = r() < 0.06 ? 0.25 : 0;
        out.push({ y: 0.13 + shelf * 0.5 + h / 2, z: zz + w / 2, h, w, tilt, c: palette[Math.floor(r() * palette.length)] });
        zz += w + 0.008 + (r() < 0.08 ? 0.12 : 0);
      }
    }
    return out;
  }, [seed]);
  useLayoutEffect(() => {
    const m = ref.current;
    if (!m) return;
    books.forEach((b, i) => {
      _m.compose(
        new THREE.Vector3(0.04, b.y, b.z),
        new THREE.Quaternion().setFromEuler(new THREE.Euler(b.tilt, 0, 0)),
        new THREE.Vector3(0.48, b.h, b.w),
      );
      m.setMatrixAt(i, _m);
      m.setColorAt(i, _c.set(b.c));
    });
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [books]);
  return (
    <group position={[x, 0, z]}>
      <RBox p={[-0.32, 1.32, 0]} s={[0.06, 2.64, 2.5]} c={WOOD_DARK} radius={0.02} />
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <RBox key={i} p={[0, 0.09 + i * 0.5, 0]} s={[0.7, 0.05, 2.5]} c={WOOD} radius={0.02} />
      ))}
      {[-1, 1].map((s) => (
        <RBox key={s} p={[0, 1.32, s * 1.24]} s={[0.7, 2.64, 0.05]} c={WOOD} radius={0.02} />
      ))}
      <instancedMesh ref={ref} args={[undefined, undefined, books.length]} castShadow>
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial roughness={0.8} />
      </instancedMesh>
    </group>
  );
}

function Cabinet({ x, z, color = "#94a3b8" }: { x: number; z: number; color?: string }) {
  return (
    <group position={[x, 0, z]}>
      <RBox p={[0, 0.7, 0]} s={[0.9, 1.4, 0.7]} c={color} radius={0.03} rough={0.4} metal={0.3} />
      {[0, 1, 2].map((i) => (
        <group key={i}>
          <RBox p={[0, 0.27 + i * 0.45, 0.35]} s={[0.8, 0.38, 0.03]} c="#cbd5e1" radius={0.012} rough={0.35} metal={0.3} />
          <Box p={[0, 0.37 + i * 0.45, 0.375]} s={[0.24, 0.035, 0.03]} c="#475569" />
        </group>
      ))}
    </group>
  );
}

function Plant({ x, z, big }: { x: number; z: number; big?: boolean }) {
  const s = big ? 1.25 : 1;
  return (
    <group position={[x, 0, z]} scale={s}>
      <mesh position-y={0.25} castShadow>
        <cylinderGeometry args={[0.26, 0.2, 0.5, 18]} />
        <primitive object={std("#e7e1d6", 0.6)} attach="material" />
      </mesh>
      {Array.from({ length: 9 }, (_, i) => {
        const a = i * 2.4;
        return (
          <mesh key={i} position={[Math.sin(a) * 0.18, 0.75 + (i % 3) * 0.22, Math.cos(a) * 0.18]} rotation={[Math.sin(a) * 0.5, a, Math.cos(a) * 0.5]} castShadow>
            <sphereGeometry args={[0.22, 8, 6]} />
            <meshStandardMaterial color={i % 2 ? "#2f8f46" : "#3fae5a"} roughness={0.8} flatShading />
          </mesh>
        );
      })}
    </group>
  );
}

function Monitor({ x, z, rot = 0, glow = "#38bdf8" }: { x: number; z: number; rot?: number; glow?: string }) {
  const screen = useRef<THREE.MeshStandardMaterial>(null);
  useFrame(({ clock }) => {
    if (screen.current) screen.current.emissiveIntensity = 0.75 + Math.sin(clock.elapsedTime * 2 + x) * 0.08;
  });
  return (
    <group position={[x, 0.8, z]} rotation-y={rot}>
      <RBox p={[0, 0.02, 0]} s={[0.26, 0.03, 0.18]} c="#1f2937" radius={0.01} />
      <Box p={[0, 0.17, -0.02]} s={[0.05, 0.28, 0.04]} c="#1f2937" />
      <RBox p={[0, 0.44, 0]} s={[0.74, 0.46, 0.05]} c="#111827" radius={0.02} rough={0.3} />
      <mesh position={[0, 0.44, 0.027]}>
        <planeGeometry args={[0.68, 0.4]} />
        <meshStandardMaterial ref={screen} color="#0b2942" emissive={glow} emissiveIntensity={0.75} />
      </mesh>
      <RBox p={[0, 0.02, 0.3]} s={[0.5, 0.025, 0.16]} c="#e5e7eb" radius={0.01} />
    </group>
  );
}

function GraphBoard() {
  const pins = useMemo(() => {
    const r = rng(42);
    return Array.from({ length: 12 }, () => new THREE.Vector3(3.95 + r() * 4.5, 1.3 + r() * 1.25, ROOM.minZ + 0.07));
  }, []);
  const edges = useMemo(() => {
    const r = rng(7);
    const e: [THREE.Vector3, THREE.Vector3][] = [];
    for (let i = 0; i < pins.length; i++) {
      const j = Math.floor(r() * pins.length);
      if (j !== i) e.push([pins[i], pins[j]]);
      if (r() > 0.5) e.push([pins[i], pins[(i + 1) % pins.length]]);
    }
    return e;
  }, [pins]);
  return (
    <group>
      <RBox p={[6.2, 1.95, ROOM.minZ + 0.04]} s={[5.3, 1.75, 0.06]} c="#5f3d22" radius={0.02} />
      <Box p={[6.2, 1.95, ROOM.minZ + 0.075]} s={[5.1, 1.55, 0.01]} c="#c49a6c" />
      {pins.map((p, i) => (
        <group key={i}>
          <Box p={[p.x, p.y - 0.1, p.z]} s={[0.3, 0.22, 0.005]} c={i % 3 ? "#fefce8" : "#e0f2fe"} />
          <mesh position={[p.x, p.y, p.z + 0.01]}>
            <sphereGeometry args={[0.035, 8, 6]} />
            <meshStandardMaterial color="#dc2626" />
          </mesh>
        </group>
      ))}
      {edges.map(([a, b], i) => (
        <Line key={i} points={[a.clone().setZ(a.z + 0.012), b.clone().setZ(b.z + 0.012)]} color="#dc2626" lineWidth={1.6} />
      ))}
    </group>
  );
}

function CacheBoard() {
  const notes = useMemo(() => {
    const r = rng(99);
    const colors = ["#fde047", "#f9a8d4", "#86efac", "#7dd3fc", "#fdba74"];
    return Array.from({ length: 16 }, () => ({ x: -1.25 + r() * 2.5, y: 1.3 + r() * 1.05, rot: (r() - 0.5) * 0.4, c: colors[Math.floor(r() * colors.length)] }));
  }, []);
  return (
    <group>
      <RBox p={[0, 1.85, ROOM.minZ + 0.03]} s={[3.1, 1.5, 0.05]} c="#64748b" radius={0.02} />
      <Box p={[0, 1.85, ROOM.minZ + 0.06]} s={[2.95, 1.35, 0.01]} c="#f8fafc" />
      {notes.map((n, i) => (
        <mesh key={i} position={[n.x, n.y, ROOM.minZ + 0.07]} rotation-z={n.rot}>
          <planeGeometry args={[0.26, 0.26]} />
          <meshStandardMaterial color={n.c} />
        </mesh>
      ))}
    </group>
  );
}

function WaterCooler() {
  const [x, z] = [2.7, -7.35];
  return (
    <group position={[x, 0, z]}>
      <RBox p={[0, 0.5, 0]} s={[0.5, 1.0, 0.5]} c="#e5e7eb" radius={0.05} rough={0.3} />
      <mesh position={[0, 1.25, 0]} castShadow>
        <cylinderGeometry args={[0.2, 0.2, 0.5, 20]} />
        <meshPhysicalMaterial color="#7dd3fc" transmission={0.4} roughness={0.1} transparent opacity={0.75} />
      </mesh>
      <Box p={[0.1, 0.7, 0.26]} s={[0.06, 0.05, 0.04]} c="#3b82f6" />
      <Box p={[-0.1, 0.7, 0.26]} s={[0.06, 0.05, 0.04]} c="#ef4444" />
    </group>
  );
}

function WallClock({ x, z }: { x: number; z: number }) {
  const hour = useRef<THREE.Mesh>(null);
  const min = useRef<THREE.Mesh>(null);
  useFrame(() => {
    const d = new Date();
    if (min.current) min.current.rotation.z = -(d.getMinutes() / 60) * Math.PI * 2;
    if (hour.current) hour.current.rotation.z = -(((d.getHours() % 12) + d.getMinutes() / 60) / 12) * Math.PI * 2;
  });
  return (
    <group position={[x, 2.6, z]}>
      <mesh rotation-x={Math.PI / 2}>
        <cylinderGeometry args={[0.3, 0.3, 0.05, 32]} />
        <primitive object={std("#1f2937", 0.4)} attach="material" />
      </mesh>
      <mesh position-z={0.03}>
        <circleGeometry args={[0.26, 32]} />
        <meshStandardMaterial color="#fffdf7" />
      </mesh>
      <mesh ref={hour} position-z={0.04}>
        <boxGeometry args={[0.025, 0.14, 0.01]} />
        <meshBasicMaterial color="#111" />
      </mesh>
      <mesh ref={min} position-z={0.045}>
        <boxGeometry args={[0.018, 0.21, 0.01]} />
        <meshBasicMaterial color="#111" />
      </mesh>
    </group>
  );
}

function Frame({ p, rot = 0, w = 0.7, h = 0.5, art = "#c7d2fe" }: { p: V3; rot?: number; w?: number; h?: number; art?: string }) {
  return (
    <group position={p} rotation-y={rot}>
      <RBox p={[0, 0, 0]} s={[w, h, 0.04]} c="#b08d57" radius={0.01} rough={0.4} metal={0.4} />
      <Box p={[0, 0, 0.022]} s={[w - 0.1, h - 0.1, 0.005]} c={art} />
    </group>
  );
}

function PendantLamp({ x, z }: { x: number; z: number }) {
  return (
    <group position={[x, 0, z]}>
      <mesh position-y={3.0}>
        <cylinderGeometry args={[0.006, 0.006, 0.6, 4]} />
        <meshBasicMaterial color="#222" />
      </mesh>
      <mesh position-y={2.62}>
        <coneGeometry args={[0.3, 0.25, 24, 1, true]} />
        <meshStandardMaterial color="#1f2937" side={THREE.DoubleSide} roughness={0.4} metalness={0.5} />
      </mesh>
      <mesh position-y={2.55}>
        <sphereGeometry args={[0.09, 12, 8]} />
        <meshBasicMaterial color="#fff4d6" />
      </mesh>
    </group>
  );
}

/** Chồng hồ sơ trên bàn luật sư — cao dần theo số tài liệu; bấm để mở */
function DeskDocs({ onOpen }: { onOpen: () => void }) {
  const deskDocs = useDeskDocs();
  const passages = usePassages();
  const [hover, setHover] = useState(false);
  const n = Math.min(deskDocs, 12);
  return (
    <group
      position={[0.95, 0.8, -1.1]}
      onClick={(e) => {
        e.stopPropagation();
        if (passages.length) onOpen();
      }}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHover(true);
        if (passages.length) document.body.style.cursor = "pointer";
      }}
      onPointerOut={() => {
        setHover(false);
        document.body.style.cursor = "";
      }}
    >
      {Array.from({ length: n }, (_, i) => (
        <mesh key={i} position={[Math.sin(i * 2.1) * 0.03, 0.02 + i * 0.035, Math.cos(i * 1.3) * 0.03]} rotation-y={Math.sin(i) * 0.25} castShadow>
          <boxGeometry args={[0.42, 0.03, 0.56]} />
          <meshStandardMaterial color={i % 3 === 0 ? "#fef3c7" : "#ffffff"} emissive={hover ? "#facc15" : "#000"} emissiveIntensity={hover ? 0.3 : 0} />
        </mesh>
      ))}
      {n > 0 && hover && (
        <Html position={[0, 0.25 + n * 0.035, 0]} center style={{ pointerEvents: "none" }}>
          <div className="whitespace-nowrap rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-900 shadow">
            📁 {passages.length || n} tài liệu{passages.length ? " · bấm để xem" : ""}
          </div>
        </Html>
      )}
    </group>
  );
}

function Sign({ p, text, sub }: { p: V3; text: string; sub?: string }) {
  return (
    <Html position={p} center distanceFactor={20} style={{ pointerEvents: "none" }} zIndexRange={[10, 0]}>
      <div className="hidden whitespace-nowrap rounded-lg border border-white/20 bg-slate-900/70 px-2 py-1 text-center text-white shadow backdrop-blur-sm sm:block">
        <div className="text-[13px] font-semibold">{text}</div>
        {sub && <div className="text-[10px] opacity-70">{sub}</div>}
      </div>
    </Html>
  );
}

function Window({ z }: { z: number }) {
  return (
    <group position={[ROOM.minX + 0.02, 1.85, z]} rotation-y={Math.PI / 2}>
      <mesh>
        <planeGeometry args={[2.2, 1.5]} />
        <meshStandardMaterial color="#bfe3ff" emissive="#9fd3ff" emissiveIntensity={0.9} />
      </mesh>
      {[-1.1, 0, 1.1].map((x) => (
        <Box key={x} p={[x, 0, 0.02]} s={[0.07, 1.6, 0.06]} c="#f8fafc" />
      ))}
      {[-0.75, 0.75].map((y) => (
        <Box key={y} p={[0, y, 0.02]} s={[2.3, 0.07, 0.06]} c="#f8fafc" />
      ))}
      <Box p={[0, -0.8, 0.08]} s={[2.4, 0.06, 0.18]} c="#f8fafc" />
    </group>
  );
}

export function Room({ onOpenDocs, showSigns }: { onOpenDocs: () => void; showSigns: boolean }) {
  const mode = useViewMode();
  const immersive = mode !== "orbit";
  const W = ROOM.maxX - ROOM.minX;
  const D = ROOM.maxZ - ROOM.minZ;
  const parquet = useMemo(() => {
    const t = makeParquetTexture();
    t.repeat.set(W / 4, D / 4);
    return t;
  }, [W, D]);
  const rug = useMemo(() => makeRugTexture(), []);

  return (
    <group>
      {/* sàn + thảm */}
      <mesh rotation-x={-Math.PI / 2} receiveShadow>
        <planeGeometry args={[W, D]} />
        <meshStandardMaterial map={parquet} roughness={0.55} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.006, -0.1]} receiveShadow>
        <planeGeometry args={[7, 5.6]} />
        <meshStandardMaterial map={rug} roughness={0.95} />
      </mesh>

      {/* tường: phần trên sơn, phần dưới ốp gỗ */}
      {[
        { p: [0, 0, ROOM.minZ - 0.06] as V3, s: [W + 0.24, 0, 0.12] as V3, r: 0 },
        { p: [ROOM.minX - 0.06, 0, 0] as V3, s: [0.12, 0, D] as V3, r: 0 },
        { p: [ROOM.maxX + 0.06, 0, 0] as V3, s: [0.12, 0, D] as V3, r: 0 },
        ...(immersive ? [{ p: [0, 0, ROOM.maxZ + 0.06] as V3, s: [W + 0.24, 0, 0.12] as V3, r: 0 }] : []),
      ].map((w, i) => (
        <group key={i} position={w.p}>
          <mesh position-y={WALL_H / 2} receiveShadow>
            <boxGeometry args={[w.s[0], WALL_H, w.s[2]]} />
            <primitive object={std(WALL, 0.95)} attach="material" />
          </mesh>
          <mesh position-y={0.55}>
            <boxGeometry args={[w.s[0] + 0.02, 1.1, w.s[2] + 0.04]} />
            <primitive object={std(WAINSCOT, 0.7)} attach="material" />
          </mesh>
          <mesh position-y={1.12}>
            <boxGeometry args={[w.s[0] + 0.03, 0.05, w.s[2] + 0.07]} />
            <primitive object={std("#4a3020", 0.5)} attach="material" />
          </mesh>
        </group>
      ))}
      {immersive && (
        <>
          <mesh rotation-x={Math.PI / 2} position-y={WALL_H}>
            <planeGeometry args={[W + 0.3, D + 0.3]} />
            <meshStandardMaterial color="#f5f1ea" />
          </mesh>
          {/* cửa ra vào ở tường trước */}
          <RBox p={[7, 1.1, ROOM.maxZ - 0.02]} s={[1.4, 2.2, 0.08]} c="#6b4226" radius={0.02} />
          <mesh position={[7.45, 1.05, ROOM.maxZ - 0.08]}>
            <sphereGeometry args={[0.05, 10, 8]} />
            <primitive object={std("#d4a017", 0.3, 0.8)} attach="material" />
          </mesh>
          <Frame p={[-3, 1.9, ROOM.maxZ - 0.05]} rot={Math.PI} w={1.6} h={0.9} art="#a7c4a0" />
        </>
      )}
      <Window z={-4.5} />
      <Window z={3.5} />
      <WallClock x={-1.9} z={ROOM.minZ + 0.06} />
      <Frame p={[ROOM.maxX - 0.04, 1.95, -4.2]} rot={-Math.PI / 2} w={1.2} h={0.8} art="#f1e3c8" />
      <Frame p={[ROOM.maxX - 0.04, 1.95, 4.2]} rot={-Math.PI / 2} w={0.9} h={0.7} art="#c8d8f1" />
      <Html position={[-8.9, 2.65, ROOM.minZ + 0.09]} transform distanceFactor={6} style={{ pointerEvents: "none" }}>
        <div className="rounded-md bg-[#1f2937] px-6 py-2 text-center font-bold tracking-widest text-amber-300 shadow-xl">
          ⚖️ VĂN PHÒNG LUẬT VNLAW
        </div>
      </Html>

      {/* đèn thả */}
      {[[0, -1.2], [-6, 0], [6, 0], [0, 5], [-6, -5], [6, -5]].map(([x, z]) => (
        <PendantLamp key={`${x}${z}`} x={x} z={z} />
      ))}

      {/* bàn luật sư chính */}
      <Desk x={0} z={-1.2} w={3.4} d={1.4} rot={Math.PI} top="#7a4a2a" />
      <Monitor x={-0.9} z={-1.5} rot={Math.PI} />
      <mesh position={[0.25, 0.86, -1.0]} castShadow>
        <cylinderGeometry args={[0.05, 0.07, 0.12, 12]} />
        <primitive object={std("#d4a017", 0.3, 0.8)} attach="material" />
      </mesh>
      <group position={[-1.45, 0.8, -1.55]}>
        <mesh position-y={0.2}>
          <cylinderGeometry args={[0.012, 0.012, 0.4, 6]} />
          <primitive object={std("#b08d57", 0.3, 0.8)} attach="material" />
        </mesh>
        <mesh position-y={0.42}>
          <coneGeometry args={[0.12, 0.12, 16, 1, true]} />
          <meshStandardMaterial color="#14532d" side={THREE.DoubleSide} />
        </mesh>
      </group>
      <DeskDocs onOpen={onOpenDocs} />

      {/* ghế (khách, luật sư, nhân viên ngồi) */}
      {CHAIRS.map((c, i) => (
        <Chair key={i} x={c.pos[0]} z={c.pos[1]} facing={c.facing} color={c.color} />
      ))}

      {/* quầy lễ tân */}
      <group>
        <RBox p={[7, 0.55, 5.3]} s={[3, 1.1, 1]} c="#f8fafc" radius={0.06} rough={0.4} />
        <RBox p={[7, 1.13, 5.3]} s={[3.15, 0.06, 1.12]} c={WOOD} radius={0.03} rough={0.35} />
        <Box p={[7, 0.6, 4.79]} s={[2.2, 0.22, 0.02]} c="#1f2937" />
        <Monitor x={7.5} z={5.45} glow="#fda4af" />
        <Plant x={8.25} z={5.3} />
      </group>
      {/* khu chờ */}
      <group>
        <RBox p={[3.6, 0.28, 6.8]} s={[2.6, 0.42, 0.9]} c="#475569" radius={0.12} rough={0.95} />
        <RBox p={[3.6, 0.72, 7.18]} s={[2.6, 0.62, 0.22]} c="#475569" radius={0.1} rough={0.95} />
        {[-1, 1].map((s) => (
          <RBox key={s} p={[3.6 + s * 1.2, 0.5, 6.8]} s={[0.24, 0.5, 0.9]} c="#3f4a5c" radius={0.1} />
        ))}
        <RBox p={[3.6, 0.42, 5.9]} s={[1.4, 0.06, 0.6]} c={WOOD} radius={0.02} rough={0.35} />
        {[-0.6, 0.6].map((x) => (
          <Box key={x} p={[3.6 + x, 0.2, 5.9]} s={[0.05, 0.4, 0.5]} c={WOOD_DARK} />
        ))}
      </group>

      {/* thư ký */}
      <Desk x={-4} z={3.4} w={2.2} d={1} />
      <Monitor x={-4.45} z={3.3} />
      <RBox p={[-3.35, 0.82, 3.45]} s={[0.32, 0.05, 0.42]} c="#fef9c3" radius={0.01} />

      {/* hồ sơ khách hàng (memory) */}
      <Desk x={-7.4} z={2.4} w={1.8} d={0.9} />
      <Monitor x={-7.6} z={2.35} glow="#5eead4" />
      <group position={[-10.2, 0, 5.2]}>
        <RBox p={[0, 1.0, 0]} s={[0.9, 2.0, 2.2]} c="#0f766e" radius={0.03} rough={0.5} />
        {Array.from({ length: 12 }, (_, i) => (
          <Box key={i} p={[0.46, 0.35 + Math.floor(i / 4) * 0.55, -0.75 + (i % 4) * 0.5]} s={[0.03, 0.4, 0.35]} c={["#fbbf24", "#f87171", "#60a5fa", "#a3e635"][i % 4]} />
        ))}
      </group>

      {/* thư viện Pháp Điển (Milvus) */}
      <Bookshelf x={-10.4} z={-5} seed={3} />
      <Bookshelf x={-10.4} z={-2} seed={11} />
      <Bookshelf x={-10.4} z={1} seed={23} />

      {/* bảng liên kết (Neo4j) */}
      {[4.5, 5.7, 6.9, 8.1].map((x) => (
        <Cabinet key={x} x={x} z={-7.4} color="#64748b" />
      ))}
      <GraphBoard />

      {/* kho lưu trữ + bàn nhân viên lưu trữ */}
      <group position={[-5.5, 0, -7.4]}>
        <RBox p={[0, 1.2, 0]} s={[3.2, 2.4, 0.9]} c="#52525b" radius={0.04} rough={0.35} metal={0.5} />
        {[-1, 0, 1].map((i) => (
          <group key={i}>
            <RBox p={[i * 1.02, 1.2, 0.44]} s={[0.95, 2.2, 0.04]} c="#71717a" radius={0.015} rough={0.3} metal={0.6} />
            <mesh position={[i * 1.02, 1.2, 0.48]}>
              <torusGeometry args={[0.14, 0.025, 8, 20]} />
              <meshStandardMaterial color="#facc15" metalness={0.8} roughness={0.25} />
            </mesh>
          </group>
        ))}
      </group>
      <Desk x={-2.6} z={-4.45} w={1.8} d={0.9} rot={Math.PI} />
      <Monitor x={-2.9} z={-4.6} rot={Math.PI} glow="#fcd34d" />

      {/* máy tra cứu web */}
      <Desk x={10} z={0.6} w={2.4} d={1.2} rot={-Math.PI / 2} />
      <Monitor x={10.1} z={0.15} rot={-Math.PI / 2} glow="#a78bfa" />
      <Monitor x={10.1} z={1.05} rot={-Math.PI / 2} glow="#34d399" />

      <CacheBoard />
      <WaterCooler />
      <Plant x={-10.2} z={7.2} big />
      <Plant x={10.2} z={-7.2} big />
      <Plant x={-2.4} z={-7.3} />
      <Plant x={10.2} z={7.2} big />

      {showSigns && mode === "orbit" && (
        <>
          <Sign p={[STATIONS.library[0] - 1.2, 3.0, STATIONS.library[1]]} text="📚 Thư viện Pháp Điển" sub="Milvus · vector_search" />
          <Sign p={[6.2, 3.2, ROOM.minZ + 0.3]} text="🧵 Bảng liên kết" sub="Neo4j · graph_traverse" />
          <Sign p={[-5.5, 2.75, ROOM.minZ + 0.9]} text="🗄️ Kho lưu trữ" sub="get_full_article" />
          <Sign p={[10, 2.3, 0.6]} text="🌐 Máy tra cứu" sub="Tavily · web_search" />
          <Sign p={[-10, 2.5, 5.2]} text="🗂️ Hồ sơ khách hàng" sub="Long-term memory" />
          <Sign p={[0, 2.95, ROOM.minZ + 0.3]} text="📌 Sổ tay đã trả lời" sub="Semantic cache" />
          <Sign p={[7, 1.9, 5.3]} text="🛎️ Lễ tân" sub="quick · spam · sentiment" />
        </>
      )}
    </group>
  );
}
