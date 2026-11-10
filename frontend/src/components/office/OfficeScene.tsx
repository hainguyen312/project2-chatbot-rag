"use client";

import { Environment, Lightformer, OrbitControls, SoftShadows } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { EffectComposer, N8AO, SMAA, Vignette } from "@react-three/postprocessing";
import { useEffect } from "react";
import * as THREE from "three";
import { Character } from "./Character";
import { director, usePovId, useSelected, useViewMode } from "./director";
import { CHARACTERS } from "./officeData";
import { FirstPersonController, PovCamera } from "./Player";
import { Room } from "./Room";
import { characterPositions } from "./runtime";

type OrbitControlsImpl = { target: THREE.Vector3; object: THREE.Camera; update: () => void };

function Ticker() {
  useFrame(() => director.tick(performance.now()));
  return null;
}

/** Camera bám theo nhân vật đang được chọn (toàn cảnh + bật follow) */
function CameraRig({ follow }: { follow: boolean }) {
  const selected = useSelected();
  const controls = useThree((s) => s.controls) as unknown as OrbitControlsImpl | null;
  useFrame((_, dt) => {
    if (!controls || !follow || !selected) return;
    const p = characterPositions.get(selected);
    if (!p) return;
    const delta = new THREE.Vector3(p.x, 1, p.z).sub(controls.target).multiplyScalar(Math.min(1, dt * 3));
    controls.target.add(delta);
    controls.object.position.add(delta);
    controls.update();
  });
  return null;
}

/** Đặt camera toàn cảnh vừa khung hình (màn hẹp → lùi xa hơn) */
function FitCamera({ token }: { token: number }) {
  const controls = useThree((s) => s.controls) as unknown as OrbitControlsImpl | null;
  const get = useThree((s) => s.get);
  const aspect = useThree((s) => s.size.width / Math.max(1, s.size.height));
  useEffect(() => {
    if (!controls) return;
    const camera = get().camera as THREE.PerspectiveCamera;
    camera.rotation.order = "XYZ";
    camera.fov = 42;
    camera.updateProjectionMatrix();
    const d = 14.5 * Math.max(1, Math.pow(1.75 / aspect, aspect < 1 ? 0.6 : 0.85));
    camera.position.set(0, d * 1.05, d);
    controls.target.set(0, 0, 0);
    controls.update();
  }, [token, controls, get, aspect]);
  return null;
}

/** FOV rộng hơn khi nhìn góc thứ nhất */
function ImmersiveFov() {
  const get = useThree((s) => s.get);
  useEffect(() => {
    const camera = get().camera as THREE.PerspectiveCamera;
    camera.fov = 70;
    camera.near = 0.05;
    camera.updateProjectionMatrix();
    return () => {
      camera.fov = 42;
      camera.near = 0.1;
      camera.updateProjectionMatrix();
    };
  }, [get]);
  return null;
}

export default function OfficeScene({
  onOpenDocs,
  follow,
  showSigns,
  resetToken,
  quality,
}: {
  onOpenDocs: () => void;
  follow: boolean;
  showSigns: boolean;
  resetToken: number;
  quality: "high" | "low";
}) {
  const mode = useViewMode();
  const povId = usePovId();
  const high = quality === "high";

  return (
    <Canvas
      shadows
      dpr={high ? [1, 2] : [1, 1.25]}
      gl={{ antialias: !high, toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 1.05 }}
      camera={{ position: [0, 15, 15], fov: 42, near: 0.1, far: 200 }}
      onPointerMissed={() => mode === "orbit" && director.select(null)}
    >
      <color attach="background" args={["#232a38"]} />
      {high && <SoftShadows size={18} samples={10} focus={0.6} />}
      <hemisphereLight args={["#fff3e0", "#3b3346", 0.55]} />
      <ambientLight intensity={0.15} />
      <directionalLight
        position={[-9, 14, 7]}
        intensity={1.9}
        color="#fff1dc"
        castShadow
        shadow-mapSize={high ? [2048, 2048] : [1024, 1024]}
        shadow-camera-left={-14}
        shadow-camera-right={14}
        shadow-camera-top={12}
        shadow-camera-bottom={-12}
        shadow-bias={-0.0004}
        shadow-normalBias={0.02}
      />
      {/* ánh sáng cửa sổ + đèn thả */}
      <pointLight position={[-9.5, 2.2, -4.5]} intensity={4} distance={8} color="#cfe8ff" />
      <pointLight position={[-9.5, 2.2, 3.5]} intensity={4} distance={8} color="#cfe8ff" />
      <pointLight position={[0, 2.4, -1.2]} intensity={5} distance={7} color="#ffd9a0" />
      <pointLight position={[0, 2.4, 5]} intensity={3} distance={7} color="#ffd9a0" />
      <Environment resolution={128} frames={1}>
        <Lightformer form="rect" intensity={2} position={[0, 5, -5]} scale={[10, 3, 1]} color="#fff4e0" />
        <Lightformer form="rect" intensity={1.2} position={[-6, 3, 4]} rotation-y={Math.PI / 2} scale={[6, 2, 1]} color="#dbeafe" />
        <Lightformer form="circle" intensity={1} position={[5, 6, 2]} scale={3} color="#ffe4c4" />
      </Environment>

      <Room onOpenDocs={onOpenDocs} showSigns={showSigns} />
      {CHARACTERS.map((c) => (
        <Character key={c.id} def={c} />
      ))}

      {mode === "orbit" && (
        <>
          <OrbitControls makeDefault target={[0, 0, 0]} enableDamping minDistance={4} maxDistance={45} maxPolarAngle={Math.PI / 2.2} />
          <CameraRig follow={follow} />
          <FitCamera token={resetToken} />
        </>
      )}
      {mode === "fp" && (
        <>
          <ImmersiveFov />
          <FirstPersonController />
        </>
      )}
      {mode === "pov" && (
        <>
          <ImmersiveFov />
          <PovCamera key={povId} id={povId} />
        </>
      )}
      <Ticker />

      {high && (
        <EffectComposer multisampling={0} enableNormalPass={false}>
          <N8AO halfRes aoRadius={0.6} intensity={2.2} distanceFalloff={0.6} color="#2a1d14" />
          <SMAA />
          <Vignette eskil={false} offset={0.25} darkness={0.55} />
        </EffectComposer>
      )}
    </Canvas>
  );
}
