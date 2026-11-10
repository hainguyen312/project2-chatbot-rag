import * as THREE from "three";

/** Toon shading 3 bậc cho phong cách hoạt hình */
let gradient: THREE.DataTexture | null = null;
function gradientMap() {
  if (!gradient) {
    gradient = new THREE.DataTexture(new Uint8Array([110, 185, 255]), 3, 1, THREE.RedFormat);
    gradient.minFilter = THREE.NearestFilter;
    gradient.magFilter = THREE.NearestFilter;
    gradient.generateMipmaps = false;
    gradient.needsUpdate = true;
  }
  return gradient;
}

const toonCache = new Map<string, THREE.MeshToonMaterial>();
export function toon(color: string) {
  let m = toonCache.get(color);
  if (!m) {
    m = new THREE.MeshToonMaterial({ color, gradientMap: gradientMap() });
    toonCache.set(color, m);
  }
  return m;
}

const stdCache = new Map<string, THREE.MeshStandardMaterial>();
/** Vật liệu PBR có cache (đồ đạc) */
export function std(color: string, roughness = 0.75, metalness = 0) {
  const key = `${color}|${roughness}|${metalness}`;
  let m = stdCache.get(key);
  if (!m) {
    m = new THREE.MeshStandardMaterial({ color, roughness, metalness });
    stdCache.set(key, m);
  }
  return m;
}

/** Texture sàn gỗ ghép (vẽ bằng canvas, không cần file ảnh) */
export function makeParquetTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 512;
  const g = c.getContext("2d")!;
  const tones = ["#c99a6b", "#be8f60", "#d3a676", "#b98a5b", "#c5966a"];
  const plankW = 64;
  const plankH = 256;
  for (let col = 0; col < 512 / plankW; col++) {
    const offset = (col % 2) * (plankH / 2);
    for (let y = -plankH; y < 512; y += plankH) {
      g.fillStyle = tones[(col * 3 + Math.floor((y + offset) / plankH) + 7) % tones.length];
      g.fillRect(col * plankW, y + offset, plankW, plankH);
      // vân gỗ
      g.strokeStyle = "rgba(90,55,25,0.12)";
      g.lineWidth = 1;
      for (let i = 0; i < 5; i++) {
        const x = col * plankW + 8 + i * 11 + ((col * 7) % 5);
        g.beginPath();
        g.moveTo(x, y + offset);
        g.bezierCurveTo(x + 4, y + offset + 80, x - 4, y + offset + 170, x + 2, y + offset + plankH);
        g.stroke();
      }
      g.strokeStyle = "rgba(60,35,15,0.45)";
      g.lineWidth = 2;
      g.strokeRect(col * plankW + 1, y + offset + 1, plankW - 2, plankH - 2);
    }
  }
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

/** Texture thảm hoa văn */
export function makeRugTexture() {
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 400;
  const g = c.getContext("2d")!;
  g.fillStyle = "#7a1f2b";
  g.fillRect(0, 0, 512, 400);
  g.strokeStyle = "#d9a441";
  g.lineWidth = 10;
  g.strokeRect(18, 18, 476, 364);
  g.lineWidth = 3;
  g.strokeRect(36, 36, 440, 328);
  g.fillStyle = "#8f2a37";
  g.fillRect(40, 40, 432, 320);
  g.strokeStyle = "rgba(217,164,65,0.8)";
  g.lineWidth = 4;
  g.beginPath();
  g.moveTo(256, 80);
  g.lineTo(420, 200);
  g.lineTo(256, 320);
  g.lineTo(92, 200);
  g.closePath();
  g.stroke();
  g.fillStyle = "rgba(217,164,65,0.9)";
  g.beginPath();
  g.arc(256, 200, 22, 0, Math.PI * 2);
  g.fill();
  for (const [x, y] of [[70, 70], [442, 70], [70, 330], [442, 330]]) {
    g.beginPath();
    g.arc(x, y, 10, 0, Math.PI * 2);
    g.fill();
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}
