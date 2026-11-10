import * as THREE from "three";
import type { CharacterId } from "./officeData";

/**
 * State realtime dùng chung giữa các component trong vòng render (useFrame).
 * Không đi qua React state để tránh re-render mỗi frame.
 */

/** Vị trí (x, 0, z) của từng nhân vật */
export const characterPositions = new Map<CharacterId, THREE.Vector3>();
/** Hướng nhìn (heading) hiện tại */
export const characterHeadings = new Map<CharacterId, number>();
/** Độ cao mắt (thấp hơn khi ngồi) */
export const characterEyes = new Map<CharacterId, number>();

/** Người chơi ở chế độ Nhập vai (điều khiển nhân vật "client") */
export const player = {
  active: false,
  pos: new THREE.Vector2(0, 2.2),
  heading: Math.PI, // nhìn về -z (phía bàn luật sư)
  pitch: -0.08,
  speed: 0,
};

/** Input từ bàn phím / joystick / vuốt màn hình */
export const playerInput = {
  forward: 0, // -1..1
  right: 0, // -1..1
  run: false,
  lookDX: 0, // tích luỹ pixel, FP controller tiêu thụ mỗi frame
  lookDY: 0,
};

export function resetPlayerInput() {
  playerInput.forward = 0;
  playerInput.right = 0;
  playerInput.run = false;
  playerInput.lookDX = 0;
  playerInput.lookDY = 0;
}
