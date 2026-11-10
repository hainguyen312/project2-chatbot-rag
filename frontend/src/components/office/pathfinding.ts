import { OBSTACLES, ROOM, type Vec2 } from "./officeData";

/** A* trên lưới 0.5m để nhân vật đi vòng qua bàn ghế. */
const CELL = 0.5;
const AGENT_RADIUS = 0.3;
const COLS = Math.round((ROOM.maxX - ROOM.minX) / CELL);
const ROWS = Math.round((ROOM.maxZ - ROOM.minZ) / CELL);

const blocked = new Uint8Array(COLS * ROWS);
(function buildGrid() {
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const x = ROOM.minX + (c + 0.5) * CELL;
      const z = ROOM.minZ + (r + 0.5) * CELL;
      const edge = x < ROOM.minX + 0.4 || x > ROOM.maxX - 0.4 || z < ROOM.minZ + 0.4 || z > ROOM.maxZ - 0.2;
      const hit = OBSTACLES.some(
        (o) =>
          Math.abs(x - o.c[0]) < o.size[0] / 2 + AGENT_RADIUS &&
          Math.abs(z - o.c[1]) < o.size[1] / 2 + AGENT_RADIUS,
      );
      blocked[r * COLS + c] = edge || hit ? 1 : 0;
    }
  }
})();

const toCell = ([x, z]: Vec2): [number, number] => [
  Math.min(COLS - 1, Math.max(0, Math.floor((x - ROOM.minX) / CELL))),
  Math.min(ROWS - 1, Math.max(0, Math.floor((z - ROOM.minZ) / CELL))),
];
const toWorld = (c: number, r: number): Vec2 => [ROOM.minX + (c + 0.5) * CELL, ROOM.minZ + (r + 0.5) * CELL];

function lineFree(a: Vec2, b: Vec2): boolean {
  const dist = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const steps = Math.ceil(dist / (CELL / 3));
  for (let i = 1; i < steps; i++) {
    const t = i / steps;
    const [c, r] = toCell([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    if (blocked[r * COLS + c]) return false;
  }
  return true;
}

export function findPath(from: Vec2, to: Vec2): Vec2[] {
  if (lineFree(from, to)) return [to];
  const [sc, sr] = toCell(from);
  const [gc, gr] = toCell(to);
  const start = sr * COLS + sc;
  const goal = gr * COLS + gc;
  const g = new Float32Array(COLS * ROWS).fill(Infinity);
  const came = new Int32Array(COLS * ROWS).fill(-1);
  const closed = new Uint8Array(COLS * ROWS);
  const open: number[] = [start];
  const f = new Float32Array(COLS * ROWS).fill(Infinity);
  g[start] = 0;
  f[start] = Math.hypot(gc - sc, gr - sr);

  while (open.length) {
    let bi = 0;
    for (let i = 1; i < open.length; i++) if (f[open[i]] < f[open[bi]]) bi = i;
    const cur = open.splice(bi, 1)[0];
    if (cur === goal) break;
    closed[cur] = 1;
    const cc = cur % COLS;
    const cr = (cur / COLS) | 0;
    for (let dr = -1; dr <= 1; dr++) {
      for (let dc = -1; dc <= 1; dc++) {
        if (!dr && !dc) continue;
        const nc = cc + dc;
        const nr = cr + dr;
        if (nc < 0 || nr < 0 || nc >= COLS || nr >= ROWS) continue;
        const n = nr * COLS + nc;
        if (closed[n]) continue;
        if (blocked[n] && n !== goal) continue;
        // không cắt góc vật cản
        if (dr && dc && (blocked[cr * COLS + nc] || blocked[nr * COLS + cc])) continue;
        const ng = g[cur] + (dr && dc ? Math.SQRT2 : 1);
        if (ng < g[n]) {
          g[n] = ng;
          came[n] = cur;
          f[n] = ng + Math.hypot(gc - nc, gr - nr);
          if (!open.includes(n)) open.push(n);
        }
      }
    }
  }

  if (came[goal] === -1) return [to]; // không tìm được → đi thẳng
  const cells: Vec2[] = [];
  for (let n = goal; n !== start && n !== -1; n = came[n]) cells.push(toWorld(n % COLS, (n / COLS) | 0));
  cells.reverse();
  cells[cells.length - 1] = to;

  // làm mượt: bỏ các điểm trung gian khi có đường thẳng thông
  const smooth: Vec2[] = [];
  let anchor = from;
  let i = 0;
  while (i < cells.length) {
    let j = cells.length - 1;
    while (j > i && !lineFree(anchor, cells[j])) j--;
    smooth.push(cells[j]);
    anchor = cells[j];
    i = j + 1;
  }
  return smooth;
}

/** Điểm (x, z) có đi được không (dùng cho va chạm của người chơi) */
export function isFree(x: number, z: number): boolean {
  if (x < ROOM.minX + 0.35 || x > ROOM.maxX - 0.35 || z < ROOM.minZ + 0.35 || z > ROOM.maxZ - 0.35) return false;
  return !OBSTACLES.some(
    (o) => Math.abs(x - o.c[0]) < o.size[0] / 2 + 0.28 && Math.abs(z - o.c[1]) < o.size[1] / 2 + 0.28,
  );
}
