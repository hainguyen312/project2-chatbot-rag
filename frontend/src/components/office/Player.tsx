"use client";

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { director, useDialog } from "./director";
import { STAFF, type CharacterId } from "./officeData";
import { isFree } from "./pathfinding";
import { characterEyes, characterHeadings, characterPositions, player, playerInput, resetPlayerInput } from "./runtime";

const EYE_H = 1.42;
const WALK = 2.2;
const RUN = 3.8;
const LOOK_SENS = 0.0024;
const TALK_RANGE = 3.6;

/**
 * Chế độ Nhập vai: bạn là khách hàng.
 *  - Desktop: WASD / mũi tên để đi, Shift chạy, click để khoá chuột rồi di chuột để nhìn, E để nói chuyện.
 *  - Mobile: joystick + vuốt (TouchControls ghi vào playerInput).
 */
export function FirstPersonController() {
  const { camera, gl } = useThree();
  const get = useThree((s) => s.get);
  const dialog = useDialog();
  const bobT = useRef(0);

  // vào chế độ: đứng dậy khỏi ghế khách, nhìn về bàn luật sư
  useEffect(() => {
    const p = characterPositions.get("client");
    player.pos.set(p ? p.x : 0, p ? p.z + 0.75 : 1.9);
    if (!isFree(player.pos.x, player.pos.y)) player.pos.set(0, 2.2);
    player.heading = Math.PI;
    player.pitch = -0.08;
    player.active = true;
    get().camera.rotation.order = "YXZ";
    return () => {
      player.active = false;
      player.speed = 0;
      resetPlayerInput();
      if (document.pointerLockElement) document.exitPointerLock();
    };
  }, [get]);

  // bàn phím + chuột
  useEffect(() => {
    const keys = new Set<string>();
    const sync = () => {
      playerInput.forward = (keys.has("KeyW") || keys.has("ArrowUp") ? 1 : 0) - (keys.has("KeyS") || keys.has("ArrowDown") ? 1 : 0);
      playerInput.right = (keys.has("KeyD") || keys.has("ArrowRight") ? 1 : 0) - (keys.has("KeyA") || keys.has("ArrowLeft") ? 1 : 0);
      playerInput.run = keys.has("ShiftLeft") || keys.has("ShiftRight");
    };
    const typing = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
    };
    const down = (e: KeyboardEvent) => {
      if (typing(e)) return;
      if (e.code === "KeyE") {
        const f = director.getSnapshot().focus;
        if (f && !director.getSnapshot().dialog) {
          director.openDialog(f);
          if (document.pointerLockElement) document.exitPointerLock();
        }
        return;
      }
      keys.add(e.code);
      sync();
      if (e.code.startsWith("Arrow")) e.preventDefault();
    };
    const up = (e: KeyboardEvent) => {
      keys.delete(e.code);
      sync();
    };
    const blur = () => {
      keys.clear();
      sync();
    };
    const move = (e: MouseEvent) => {
      if (document.pointerLockElement !== gl.domElement) return;
      playerInput.lookDX += e.movementX;
      playerInput.lookDY += e.movementY;
    };
    const click = () => {
      if (director.getSnapshot().dialog) return;
      if (window.matchMedia("(pointer: coarse)").matches) return;
      gl.domElement.requestPointerLock?.();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    document.addEventListener("mousemove", move);
    gl.domElement.addEventListener("click", click);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
      document.removeEventListener("mousemove", move);
      gl.domElement.removeEventListener("click", click);
    };
  }, [gl]);

  // đang hội thoại → nhả chuột
  useEffect(() => {
    if (dialog && document.pointerLockElement) document.exitPointerLock();
  }, [dialog]);

  useFrame((_, rawDt) => {
    const dt = Math.min(rawDt, 0.05);
    const talking = !!director.getSnapshot().dialog;

    // nhìn
    player.heading -= playerInput.lookDX * LOOK_SENS;
    player.pitch = THREE.MathUtils.clamp(player.pitch - playerInput.lookDY * LOOK_SENS, -1.2, 1.1);
    playerInput.lookDX = 0;
    playerInput.lookDY = 0;

    // đi
    let f = talking ? 0 : playerInput.forward;
    let r = talking ? 0 : playerInput.right;
    const len = Math.hypot(f, r);
    if (len > 1) {
      f /= len;
      r /= len;
    }
    const speed = (playerInput.run ? RUN : WALK) * Math.min(1, len);
    const h = player.heading;
    const fx = Math.sin(h), fz = Math.cos(h);
    const rx = -Math.cos(h), rz = Math.sin(h);
    const vx = (fx * f + rx * r) * speed;
    const vz = (fz * f + rz * r) * speed;
    const nx = player.pos.x + vx * dt;
    const nz = player.pos.y + vz * dt;
    // trượt dọc vật cản: thử từng trục
    if (isFree(nx, player.pos.y)) player.pos.x = nx;
    if (isFree(player.pos.x, nz)) player.pos.y = nz;
    // không đi xuyên nhân vật
    for (const s of STAFF) {
      const p = characterPositions.get(s.id);
      if (!p) continue;
      const dx = player.pos.x - p.x;
      const dz = player.pos.y - p.z;
      const d = Math.hypot(dx, dz);
      if (d > 0.001 && d < 0.55) {
        const px = p.x + (dx / d) * 0.55;
        const pz = p.z + (dz / d) * 0.55;
        if (isFree(px, pz)) player.pos.set(px, pz);
      }
    }
    player.speed = speed;

    // camera
    bobT.current += dt * speed * 3.2;
    const bob = speed > 0.1 ? Math.sin(bobT.current) * 0.035 : 0;
    camera.position.set(player.pos.x, EYE_H + bob, player.pos.y);
    camera.rotation.set(player.pitch, h + Math.PI, 0, "YXZ");

    // ai đang ở trước mặt?
    let best: CharacterId | null = null;
    let bestScore = Infinity;
    for (const s of STAFF) {
      const p = characterPositions.get(s.id);
      if (!p) continue;
      const dx = p.x - player.pos.x;
      const dz = p.z - player.pos.y;
      const d = Math.hypot(dx, dz);
      if (d > TALK_RANGE) continue;
      let ang = Math.atan2(dx, dz) - h;
      ang = Math.atan2(Math.sin(ang), Math.cos(ang));
      if (Math.abs(ang) > 0.5) continue;
      const score = d + Math.abs(ang) * 2;
      if (score < bestScore) {
        bestScore = score;
        best = s.id;
      }
    }
    director.setFocus(best);
  });

  return null;
}

/** Nhìn qua mắt một nhân vật (camera gắn vào đầu, tự xoay theo hướng nhân vật) */
export function PovCamera({ id }: { id: CharacterId }) {
  const { camera } = useThree();
  const heading = useRef<number | null>(null);
  const look = useRef({ yaw: 0, pitch: -0.12 });
  const { gl } = useThree();
  const get = useThree((s) => s.get);

  useEffect(() => {
    get().camera.rotation.order = "YXZ";
    heading.current = null;
    look.current = { yaw: 0, pitch: -0.12 };
    // kéo chuột / vuốt để liếc nhìn quanh, thả ra tự trở về
    let dragging = false;
    let lx = 0, ly = 0;
    const down = (e: PointerEvent) => {
      dragging = true;
      lx = e.clientX;
      ly = e.clientY;
    };
    const move = (e: PointerEvent) => {
      if (!dragging) return;
      look.current.yaw = THREE.MathUtils.clamp(look.current.yaw - (e.clientX - lx) * 0.004, -1.6, 1.6);
      look.current.pitch = THREE.MathUtils.clamp(look.current.pitch - (e.clientY - ly) * 0.004, -1.0, 0.8);
      lx = e.clientX;
      ly = e.clientY;
    };
    const up = () => (dragging = false);
    gl.domElement.addEventListener("pointerdown", down);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      gl.domElement.removeEventListener("pointerdown", down);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [get, id, gl]);

  useFrame((_, dt) => {
    const p = characterPositions.get(id);
    const h = characterHeadings.get(id);
    if (!p || h == null) return;
    if (heading.current == null) heading.current = h;
    // làm mượt hướng quay đầu để đỡ chóng mặt
    let d = h - heading.current;
    d = Math.atan2(Math.sin(d), Math.cos(d));
    heading.current += d * Math.min(1, dt * 5);
    const eye = characterEyes.get(id) ?? 1.45;
    const fx = Math.sin(heading.current), fz = Math.cos(heading.current);
    camera.position.set(p.x + fx * 0.22, eye, p.z + fz * 0.22);
    camera.rotation.set(look.current.pitch, heading.current + Math.PI + look.current.yaw, 0, "YXZ");
  });
  return null;
}
