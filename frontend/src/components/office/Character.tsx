"use client";

import { Html } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { memo, useRef, useState } from "react";
import * as THREE from "three";
import { AWAY_ACTIVITY, director, resolveTarget, useCharView, useSelected, useViewMode, usePovId, type Anim, type Step, type Target } from "./director";
import { toon } from "./materials";
import { SEAT_HEIGHT, type CharacterDef, type Vec2 } from "./officeData";
import { findPath } from "./pathfinding";
import { characterEyes, characterHeadings, characterPositions, player } from "./runtime";

const WALK_SPEED = 1.9;
const RUN_SPEED = 3.1;
const TURN_SPEED = 9;
const HIP = 0.62;
const EYE = 1.45;

function angleLerp(a: number, b: number, t: number) {
  let d = ((b - a + Math.PI) % (Math.PI * 2)) - Math.PI;
  if (d < -Math.PI) d += Math.PI * 2;
  return a + d * Math.min(1, t);
}
type G = THREE.Group | null;
const lerpRot = (g: G, x: number, z: number, k: number) => {
  if (!g) return;
  g.rotation.x += (x - g.rotation.x) * k;
  g.rotation.z += (z - g.rotation.z) * k;
};

// ── Hình học dùng chung ──────────────────────────────────────────────────────
const geo = {
  thigh: new THREE.CapsuleGeometry(0.078, 0.18, 4, 10),
  shin: new THREE.CapsuleGeometry(0.07, 0.16, 4, 10),
  shoe: new THREE.SphereGeometry(0.1, 12, 8),
  torso: new THREE.CapsuleGeometry(0.21, 0.24, 6, 16),
  arm: new THREE.CapsuleGeometry(0.062, 0.22, 4, 10),
  hand: new THREE.SphereGeometry(0.066, 12, 8),
  head: new THREE.SphereGeometry(0.3, 28, 20),
  hairCap: new THREE.SphereGeometry(0.318, 28, 18, 0, Math.PI * 2, 0, Math.PI * 0.56),
  eye: new THREE.SphereGeometry(0.045, 14, 10),
  shine: new THREE.SphereGeometry(0.014, 8, 6),
  ear: new THREE.SphereGeometry(0.06, 10, 8),
  cheek: new THREE.CircleGeometry(0.045, 16),
  small: new THREE.SphereGeometry(0.1, 14, 10),
  paper: new THREE.BoxGeometry(0.34, 0.035, 0.44),
  ring: new THREE.RingGeometry(0.45, 0.56, 40),
  blob: new THREE.CircleGeometry(0.42, 24),
  cup: new THREE.CylinderGeometry(0.045, 0.035, 0.1, 12),
};
const mat = {
  dark: toon("#1b1f2a"),
  white: toon("#ffffff"),
  mouth: toon("#7a2d2d"),
  cheek: new THREE.MeshBasicMaterial({ color: "#ff9aa2", transparent: true, opacity: 0.55 }),
  blob: new THREE.MeshBasicMaterial({ color: "#000000", transparent: true, opacity: 0.22, depthWrite: false }),
  paperA: toon("#fbfaf5"),
  paperB: toon("#fde68a"),
  ringSel: new THREE.MeshBasicMaterial({ color: "#facc15", transparent: true, opacity: 0.95 }),
  ringHover: new THREE.MeshBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.5 }),
  ringFocus: new THREE.MeshBasicMaterial({ color: "#38bdf8", transparent: true, opacity: 0.9 }),
  cup: toon("#e0f2fe"),
};

function Hair({ def }: { def: CharacterDef }) {
  const m = toon(def.hair);
  const cap = (
    <mesh geometry={geo.hairCap} material={m} rotation-x={-0.32} position={[0, 0.02, -0.01]} castShadow />
  );
  switch (def.hairStyle) {
    case "side":
      return (
        <>
          {cap}
          <mesh geometry={geo.small} material={m} position={[0.1, 0.2, 0.2]} scale={[1.9, 0.55, 0.9]} rotation-z={-0.35} />
        </>
      );
    case "bun":
      return (
        <>
          {cap}
          <mesh geometry={geo.small} material={m} position={[0, 0.3, -0.15]} scale={1.15} castShadow />
          <mesh geometry={geo.small} material={m} position={[-0.12, 0.17, 0.2]} scale={[1.3, 0.5, 0.8]} rotation-z={0.4} />
        </>
      );
    case "long":
      return (
        <>
          {cap}
          <mesh geometry={geo.small} material={m} position={[0, -0.12, -0.17]} scale={[2.9, 3.0, 1.2]} castShadow />
          <mesh geometry={geo.small} material={m} position={[0, 0.2, 0.2]} scale={[2.2, 0.55, 0.8]} />
        </>
      );
    case "bob":
      return (
        <>
          {cap}
          <mesh geometry={geo.small} material={m} position={[0, -0.02, -0.08]} scale={[3.25, 2.2, 2.6]} castShadow />
          <mesh geometry={geo.small} material={m} position={[0, 0.19, 0.21]} scale={[2.3, 0.6, 0.8]} />
        </>
      );
    case "spiky":
      return (
        <>
          {cap}
          {[-0.16, -0.05, 0.06, 0.17].map((x, i) => (
            <mesh key={i} material={m} position={[x, 0.3 - Math.abs(x) * 0.3, 0.1 - i * 0.02]} rotation={[0.5, 0, -x * 2]}>
              <coneGeometry args={[0.07, 0.18, 6]} />
            </mesh>
          ))}
        </>
      );
    default:
      return (
        <>
          {cap}
          <mesh geometry={geo.small} material={m} position={[0, 0.2, 0.2]} scale={[2.3, 0.45, 0.8]} />
        </>
      );
  }
}

function CharacterImpl({ def }: { def: CharacterDef }) {
  const view = useCharView(def.id);
  const selected = useSelected();
  const mode = useViewMode();
  const povId = usePovId();
  const [hovered, setHovered] = useState(false);
  const isPlayer = def.id === "client" && mode === "fp";
  const hidden = isPlayer || (mode === "pov" && povId === def.id);

  const root = useRef<THREE.Group>(null);
  const body = useRef<THREE.Group>(null);
  const torso = useRef<THREE.Group>(null);
  const head = useRef<THREE.Group>(null);
  const thighL = useRef<THREE.Group>(null);
  const thighR = useRef<THREE.Group>(null);
  const kneeL = useRef<THREE.Group>(null);
  const kneeR = useRef<THREE.Group>(null);
  const armL = useRef<THREE.Group>(null);
  const armR = useRef<THREE.Group>(null);
  const docs = useRef<THREE.Group>(null);
  const cup = useRef<THREE.Mesh>(null);
  const eyes = useRef<THREE.Group>(null);
  const mouth = useRef<THREE.Mesh>(null);

  const pos = useRef(new THREE.Vector2(def.work.pos[0], def.work.pos[1]));
  const heading = useRef(def.work.facing);
  const targetHeading = useRef(def.work.facing);
  const stepRef = useRef<Step | undefined>(undefined);
  const stepStart = useRef(0);
  const path = useRef<Vec2[]>([]);
  const pathGoal = useRef<Vec2>([0, 0]);
  const lastRepath = useRef(0);
  const idleSince = useRef(-1);
  const idleDelay = useRef(0);
  const sitAmt = useRef(def.work.seated ? 1 : 0);
  const blinkAt = useRef(0);

  useFrame((state, rawDt) => {
    const dt = Math.min(rawDt, 0.05);
    const now = performance.now();
    const t = state.clock.elapsedTime;
    if (idleSince.current < 0) {
      idleSince.current = now;
      idleDelay.current = 300 + Math.random() * 2500;
      blinkAt.current = now + 1000 + Math.random() * 3000;
      // vào chỗ ngồi ngay từ đầu
      director.idle(def.id);
    }

    let moving = false;
    let running = false;
    let anim: Anim = "idle";
    let seated = false;
    let away = false;

    if (isPlayer) {
      // người chơi điều khiển trực tiếp
      pos.current.copy(player.pos);
      heading.current = targetHeading.current = player.heading;
      moving = player.speed > 0.1;
      running = player.speed > 2.5;
    } else {
      const step = director.current(def.id);
      if (step !== stepRef.current) {
        stepRef.current = step;
        stepStart.current = now;
        if (step?.kind === "walk") {
          pathGoal.current = resolveTarget(step.to, [pos.current.x, pos.current.y]);
          path.current = findPath([pos.current.x, pos.current.y], pathGoal.current);
          lastRepath.current = now;
        }
        if (!step) {
          idleSince.current = now;
          idleDelay.current = 400 + Math.random() * 2600;
        }
      }
      const elapsed = now - stepStart.current;

      if (step) {
        switch (step.kind) {
          case "walk": {
            // đích là nhân vật đang di chuyển → tính lại đường định kỳ
            if (!Array.isArray(step.to) && now - lastRepath.current > 600) {
              const g = resolveTarget(step.to, [pos.current.x, pos.current.y]);
              if (Math.hypot(g[0] - pathGoal.current[0], g[1] - pathGoal.current[1]) > 0.5) {
                pathGoal.current = g;
                path.current = findPath([pos.current.x, pos.current.y], g);
              }
              lastRepath.current = now;
            }
            const wp = path.current[0];
            if (!wp) {
              director.advance(def.id);
              break;
            }
            const dx = wp[0] - pos.current.x;
            const dz = wp[1] - pos.current.y;
            const dist = Math.hypot(dx, dz);
            // đứng dậy trước khi đi
            if (sitAmt.current > 0.15) break;
            const speed = step.run ? RUN_SPEED : WALK_SPEED;
            const move = speed * dt;
            if (dist <= move) {
              pos.current.set(wp[0], wp[1]);
              path.current.shift();
              if (!path.current.length) director.advance(def.id);
            } else {
              pos.current.x += (dx / dist) * move;
              pos.current.y += (dz / dist) * move;
            }
            if (dist > 0.01) targetHeading.current = Math.atan2(dx, dz);
            moving = true;
            running = !!step.run;
            break;
          }
          case "face": {
            if (typeof step.to === "number") targetHeading.current = step.to;
            else {
              const g = resolveTarget(step.to, [pos.current.x, pos.current.y]);
              targetHeading.current = Math.atan2(g[0] - pos.current.x, g[1] - pos.current.y);
            }
            if (elapsed > 300) director.advance(def.id);
            break;
          }
          case "work": {
            anim = step.anim;
            seated = !!step.seated;
            away = !!step.away;
            if (step.facing != null) targetHeading.current = step.facing;
            if ((elapsed >= step.minMs && director.isResolved(step.until)) || elapsed > 120_000) director.advance(def.id);
            break;
          }
          case "say": {
            anim = "talk";
            seated = !!step.seated;
            if (elapsed >= Math.min(step.ms, 1700)) director.advance(def.id);
            break;
          }
          default:
            director.advance(def.id);
        }
      } else if (now - idleSince.current > idleDelay.current) {
        // nhờ director giao một hoạt động; nếu không có gì (đang hội thoại) thì thử lại sau
        idleSince.current = now;
        idleDelay.current = 2500;
        director.idle(def.id);
      }
    }

    heading.current = angleLerp(heading.current, targetHeading.current, dt * TURN_SPEED);
    sitAmt.current += ((seated ? 1 : 0) - sitAmt.current) * Math.min(1, dt * 7);
    const sit = sitAmt.current;

    const g = root.current;
    if (!g) return;
    g.position.set(pos.current.x, 0, pos.current.y);
    g.rotation.y = heading.current;
    g.visible = !away;
    let p = characterPositions.get(def.id);
    if (!p) characterPositions.set(def.id, (p = new THREE.Vector3()));
    p.set(pos.current.x, 0, pos.current.y);
    characterHeadings.set(def.id, heading.current);
    const scale = def.scale ?? 1;
    characterEyes.set(def.id, (EYE - sit * (HIP - SEAT_HEIGHT - 0.05)) * scale);

    if (hidden) return;

    // ── Pose ────────────────────────────────────────────────────────────────
    const carrying = view.carrying && !isPlayer;
    let tL = 0, tR = 0, kL = 0, kR = 0;
    let aL = 0.05, aR = 0.05, aLz = 0.12, aRz = -0.12;
    let headX = 0, headY = 0, headZ = 0, bob = 0, lean = 0, talk = 0;

    if (moving) {
      const f = running ? 13 : 8.5;
      const amp = running ? 0.85 : 0.55;
      const s = Math.sin(t * f);
      tL = s * amp;
      tR = -s * amp;
      kL = Math.max(0, -Math.cos(t * f)) * amp * 1.1;
      kR = Math.max(0, Math.cos(t * f)) * amp * 1.1;
      aL = -s * amp * 0.9;
      aR = s * amp * 0.9;
      bob = Math.abs(Math.cos(t * f)) * (running ? 0.08 : 0.045);
      lean = running ? 0.18 : 0.05;
    } else {
      bob = Math.sin(t * 2.1) * 0.01;
      switch (anim) {
        case "search":
          aL = -2.5 + Math.sin(t * 5) * 0.45;
          aR = -2.5 - Math.sin(t * 5) * 0.45;
          headX = -0.3;
          headY = Math.sin(t * 1.6) * 0.5;
          break;
        case "type":
          aL = -1.25 + Math.sin(t * 24) * 0.07;
          aR = -1.25 + Math.cos(t * 24) * 0.07;
          aLz = 0.25;
          aRz = -0.25;
          headX = 0.18;
          headY = Math.sin(t * 0.7) * 0.12;
          break;
        case "think":
          aR = -2.5;
          aRz = 0.55;
          aL = -0.4;
          aLz = -0.3;
          headZ = Math.sin(t * 0.9) * 0.12;
          headY = Math.sin(t * 0.6) * 0.25;
          break;
        case "read":
          aL = aR = -1.2;
          aLz = 0.42;
          aRz = -0.42;
          headX = 0.32;
          headY = Math.sin(t * 0.5) * 0.15;
          break;
        case "talk":
          aR = -0.8 + Math.sin(t * 4) * 0.4;
          aRz = -0.35;
          aL = -0.25 + Math.cos(t * 3) * 0.2;
          headY = Math.sin(t * 2.2) * 0.15;
          headZ = Math.sin(t * 1.3) * 0.06;
          talk = 1;
          break;
        case "drink":
          aR = -2.2;
          aRz = 0.5;
          headX = -0.25 + Math.sin(t * 1.5) * 0.05;
          break;
        case "wave":
          aR = -2.8;
          aRz = -0.4 + Math.sin(t * 9) * 0.35;
          break;
        default:
          headY = Math.sin(t * 0.4 + scale * 10) * 0.25;
      }
    }
    if (view.bubble) talk = 1;
    if (sit > 0.01) {
      tL = tL * (1 - sit) - (Math.PI / 2) * sit;
      tR = tR * (1 - sit) - (Math.PI / 2) * sit;
      kL = kL * (1 - sit) + (Math.PI / 2) * sit;
      kR = kR * (1 - sit) + (Math.PI / 2) * sit;
    }
    if (carrying) {
      aL = aR = -1.2;
      aLz = 0.45;
      aRz = -0.45;
    }

    const k = Math.min(1, dt * 12);
    lerpRot(thighL.current, tL, 0, k);
    lerpRot(thighR.current, tR, 0, k);
    lerpRot(kneeL.current, kL, 0, k);
    lerpRot(kneeR.current, kR, 0, k);
    lerpRot(armL.current, aL, aLz, k);
    lerpRot(armR.current, aR, aRz, k);
    if (head.current) {
      head.current.rotation.x += (headX - head.current.rotation.x) * k;
      head.current.rotation.y += (headY - head.current.rotation.y) * k;
      head.current.rotation.z += (headZ - head.current.rotation.z) * k;
    }
    if (body.current) body.current.position.y = -sit * (HIP - SEAT_HEIGHT - 0.05) + bob;
    if (torso.current) torso.current.rotation.x = lean;
    if (docs.current) docs.current.visible = carrying;
    if (cup.current) cup.current.visible = anim === "drink" && !moving;
    // chớp mắt
    if (eyes.current) {
      const closing = now > blinkAt.current;
      eyes.current.scale.y = closing ? 0.12 : 1;
      if (now > blinkAt.current + 120) blinkAt.current = now + 1800 + Math.random() * 3500;
    }
    if (mouth.current) {
      const open = talk ? 0.6 + Math.abs(Math.sin(t * 14)) * 1.6 : 0.6;
      mouth.current.scale.y += (open - mouth.current.scale.y) * k;
    }
  });

  const isSel = selected === def.id;
  const interactive = mode === "orbit";
  const onClick = (e: ThreeEvent<MouseEvent>) => {
    if (!interactive) return;
    e.stopPropagation();
    director.select(isSel ? null : def.id);
  };
  const scale = def.scale ?? 1;
  const top = toon(def.top);
  const inner = toon(def.inner);
  const skin = toon(def.skin);
  const pants = toon(def.pants);
  const sleeve = def.outfit === "vest" ? inner : top;

  return (
    <group ref={root}>
      <group visible={!hidden}>
        <mesh geometry={geo.blob} material={mat.blob} rotation-x={-Math.PI / 2} position-y={0.012} scale={scale} />
        <mesh
          geometry={geo.ring}
          material={isSel ? mat.ringSel : mat.ringHover}
          rotation-x={-Math.PI / 2}
          position-y={0.02}
          visible={isSel || (hovered && interactive)}
        />
        <group
          ref={body}
          scale={scale}
          onClick={onClick}
          onPointerOver={(e) => {
            if (!interactive) return;
            e.stopPropagation();
            setHovered(true);
            document.body.style.cursor = "pointer";
          }}
          onPointerOut={() => {
            setHovered(false);
            document.body.style.cursor = "";
          }}
        >
          {/* chân: đùi → gối → ống chân → giày */}
          {([
            [thighL, kneeL, -0.1],
            [thighR, kneeR, 0.1],
          ] as const).map(([thigh, knee, x], i) => (
            <group key={i} ref={thigh} position={[x, HIP, 0]}>
              <mesh geometry={geo.thigh} material={pants} position-y={-0.15} castShadow />
              <group ref={knee} position-y={-0.3}>
                <mesh geometry={geo.shin} material={pants} position-y={-0.12} castShadow />
                <mesh geometry={geo.shoe} material={mat.dark} position={[0, -0.27, 0.04]} scale={[0.85, 0.55, 1.3]} castShadow />
              </group>
            </group>
          ))}

          <group ref={torso} position-y={HIP}>
            {/* thân */}
            <mesh geometry={geo.torso} material={top} position-y={0.27} scale={[1, 1, 0.82]} castShadow />
            {def.outfit === "suit" && (
              <>
                <mesh material={inner} position={[0, 0.42, 0.16]} rotation-x={Math.PI} scale={[1, 1, 0.35]}>
                  <coneGeometry args={[0.1, 0.24, 3]} />
                </mesh>
              </>
            )}
            {(def.outfit === "vest" || def.outfit === "cardigan") && (
              <mesh material={inner} position={[0, 0.3, 0.165]} scale={[0.35, 1, 0.2]}>
                <capsuleGeometry args={[0.1, 0.3, 4, 8]} />
              </mesh>
            )}
            {def.outfit === "shirt" && (
              <mesh material={inner} position={[0, 0.5, 0.12]} rotation-x={0.5}>
                <torusGeometry args={[0.1, 0.025, 6, 16, Math.PI]} />
              </mesh>
            )}
            {def.accessory === "tie" && (
              <mesh material={toon("#c0262d")} position={[0, 0.32, 0.18]} rotation-x={-0.1}>
                <boxGeometry args={[0.06, 0.24, 0.02]} />
              </mesh>
            )}
            {def.accessory === "bowtie" &&
              [-1, 1].map((s) => (
                <mesh key={s} material={toon("#7c1d2a")} position={[s * 0.045, 0.47, 0.155]} rotation-z={(s * Math.PI) / 2}>
                  <coneGeometry args={[0.04, 0.08, 4]} />
                </mesh>
              ))}
            {def.accessory === "badge" && (
              <mesh material={mat.white} position={[0.1, 0.36, 0.175]}>
                <boxGeometry args={[0.07, 0.09, 0.01]} />
              </mesh>
            )}
            {def.accessory === "pen" && (
              <mesh material={toon("#2563eb")} position={[-0.1, 0.4, 0.17]}>
                <cylinderGeometry args={[0.01, 0.01, 0.12, 6]} />
              </mesh>
            )}

            {/* tay */}
            {([
              [armL, -0.27],
              [armR, 0.27],
            ] as const).map(([arm, x], i) => (
              <group key={i} ref={arm} position={[x, 0.46, 0]}>
                <mesh geometry={geo.arm} material={sleeve} position-y={-0.17} castShadow />
                <mesh geometry={geo.hand} material={skin} position-y={-0.36} />
                {i === 1 && <mesh ref={cup} geometry={geo.cup} material={mat.cup} position={[0, -0.4, 0.06]} visible={false} />}
              </group>
            ))}

            {/* tài liệu ôm trước ngực */}
            <group ref={docs} position={[0, 0.25, 0.36]} visible={false}>
              {[0, 1, 2].map((i) => (
                <mesh key={i} geometry={geo.paper} material={i === 1 ? mat.paperB : mat.paperA} position={[(i % 2) * 0.03, i * 0.04, 0]} rotation-y={i * 0.12} castShadow />
              ))}
            </group>

            {/* đầu */}
            <group ref={head} position-y={0.82}>
              <mesh geometry={geo.head} material={skin} scale={[1, 0.94, 0.94]} castShadow />
              {[-1, 1].map((s) => (
                <mesh key={s} geometry={geo.ear} material={skin} position={[s * 0.29, -0.02, 0]} />
              ))}
              <group ref={eyes} position={[0, 0.0, 0.266]}>
                {[-0.105, 0.105].map((x) => (
                  <group key={x} position={[x, 0, 0]}>
                    <mesh geometry={geo.eye} material={mat.dark} scale={[0.95, 1.25, 0.6]} />
                    <mesh geometry={geo.shine} material={mat.white} position={[0.016, 0.02, 0.03]} />
                  </group>
                ))}
              </group>
              {[-0.105, 0.105].map((x) => (
                <mesh key={x} material={toon(def.hair)} position={[x, 0.1, 0.27]} rotation-z={x > 0 ? -0.12 : 0.12}>
                  <boxGeometry args={[0.08, 0.018, 0.02]} />
                </mesh>
              ))}
              <mesh ref={mouth} material={mat.mouth} position={[0, -0.115, 0.268]} scale={[1, 0.6, 1]}>
                <sphereGeometry args={[0.032, 12, 8]} />
              </mesh>
              {[-0.17, 0.17].map((x) => (
                <mesh key={x} geometry={geo.cheek} material={mat.cheek} position={[x, -0.07, 0.23]} rotation-y={x * 1.6} />
              ))}
              <Hair def={def} />
              {def.accessory === "glasses" && (
                <group position={[0, 0.01, 0.285]}>
                  {[-0.105, 0.105].map((x) => (
                    <mesh key={x} material={mat.dark} position={[x, 0, 0]}>
                      <torusGeometry args={[0.068, 0.011, 6, 20]} />
                    </mesh>
                  ))}
                  <mesh material={mat.dark}>
                    <boxGeometry args={[0.07, 0.012, 0.012]} />
                  </mesh>
                </group>
              )}
              {def.accessory === "headset" && (
                <>
                  <mesh material={mat.dark} rotation-z={Math.PI / 2} rotation-y={Math.PI / 2}>
                    <torusGeometry args={[0.33, 0.022, 8, 24, Math.PI]} />
                  </mesh>
                  {[-1, 1].map((s) => (
                    <mesh key={s} material={toon("#7c3aed")} position={[s * 0.32, -0.02, 0]} rotation-z={Math.PI / 2}>
                      <cylinderGeometry args={[0.07, 0.07, 0.05, 14]} />
                    </mesh>
                  ))}
                  <mesh material={mat.dark} position={[0.22, -0.13, 0.18]} rotation-y={0.9}>
                    <boxGeometry args={[0.02, 0.02, 0.2]} />
                  </mesh>
                </>
              )}
            </group>
          </group>
        </group>
      </group>

      {/* nhãn tên + bong bóng thoại */}
      {!hidden && view.activity !== AWAY_ACTIVITY && (
        <Html position={[0, 2.0 * scale, 0]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
          <div className="flex flex-col items-center gap-1 select-none" style={{ transform: "translateY(-50%)" }}>
            {view.bubble && (
              <div className="office-bubble w-max max-w-[150px] rounded-2xl border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-1.5 text-[12px] leading-snug text-[var(--text-primary)] shadow-lg sm:max-w-[220px]">
                {view.bubble}
              </div>
            )}
            <div
              className={`whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-semibold shadow ${
                isSel
                  ? "bg-[var(--accent)] text-[var(--text-on-accent)]"
                  : "border border-[var(--border)] bg-[var(--bg-surface)] text-[var(--text-primary)]"
              }`}
            >
              {def.name}
              <span className="ml-1 hidden font-normal opacity-75 sm:inline">· {def.role}</span>
              {view.busy && def.id !== "client" && (
                <span className="ml-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--ok-text)] align-middle" />
              )}
            </div>
          </div>
        </Html>
      )}
    </group>
  );
}

export const Character = memo(CharacterImpl);
export type { Target };
