/**
 * Bố cục văn phòng luật 3D + ánh xạ thực thể backend → nhân vật.
 *
 * Toạ độ: mặt sàn XZ, x ∈ [-11, 11], z ∈ [-8, 8]. Camera toàn cảnh nhìn từ phía +z.
 * Hướng (heading) θ: nhân vật nhìn theo vector (sin θ, cos θ); 0 = nhìn về +z.
 */

export type Vec2 = [number, number]; // [x, z]

export type CharacterId =
  | "client"
  | "receptionist"
  | "secretary"
  | "lead"
  | "librarian"
  | "archivist"
  | "researcher"
  | "clerk"
  | "memory";

export type HairStyle = "short" | "side" | "bun" | "long" | "spiky" | "bob";
export type Outfit = "suit" | "shirt" | "vest" | "cardigan" | "casual";
export type WorkAnim = "type" | "read" | "think" | "search";

export type CharacterDef = {
  id: CharacterId;
  name: string;
  role: string;
  /** Thành phần backend mà nhân vật đại diện */
  backend: string;
  description: string;
  greet: string;
  /** Vị trí làm việc mặc định khi rảnh */
  work: { pos: Vec2; facing: number; anim: WorkAnim; seated?: boolean };
  top: string; // áo ngoài
  inner: string; // áo trong / cổ áo
  pants: string;
  hair: string;
  skin: string;
  hairStyle: HairStyle;
  outfit: Outfit;
  accessory?: "tie" | "glasses" | "headset" | "badge" | "bowtie" | "pen";
  /** Tỉ lệ cơ thể (nhân vật cao/thấp khác nhau cho tự nhiên) */
  scale?: number;
};

export const ROOM = { minX: -11, maxX: 11, minZ: -8, maxZ: 8 };
export const SEAT_HEIGHT = 0.42;

export const STATIONS = {
  leadDesk: [0, -1.2] as Vec2,
  deliver: [2.3, -0.9] as Vec2, // chỗ trợ lý đứng nộp tài liệu cho luật sư chính
  secretaryReport: [-2.25, -0.7] as Vec2,
  memoryReport: [-2.25, -2.45] as Vec2,
  library: [-9.0, -2.0] as Vec2, // giá sách điều luật (Milvus)
  graph: [6.0, -6.2] as Vec2, // tủ hồ sơ liên kết (Neo4j)
  vault: [-5.5, -6.3] as Vec2, // kho lưu trữ nguyên văn (get_full_article)
  web: [8.9, 0.6] as Vec2, // máy tính tra cứu web (Tavily)
  memory: [-8.6, 5.2] as Vec2, // tủ hồ sơ khách hàng (long-term memory)
  board: [0, -6.6] as Vec2, // bảng ghi nhớ (semantic cache)
  cooler: [2.7, -6.55] as Vec2, // bình nước — chỗ tán gẫu
  sofa: [2.1, 5.9] as Vec2,
  clientChair: [0, 1.15] as Vec2,
} as const;

export const CHARACTERS: CharacterDef[] = [
  {
    id: "client",
    name: "Bạn",
    role: "Khách hàng",
    backend: "Người dùng",
    description: "Đại diện cho bạn. Chuyển sang chế độ Nhập vai để tự đi lại trong văn phòng và nói chuyện với từng người.",
    greet: "",
    work: { pos: [0, 1.15], facing: Math.PI, anim: "think", seated: true },
    top: "#6b7280",
    inner: "#f3f4f6",
    pants: "#374151",
    hair: "#1f2937",
    skin: "#f2c9a6",
    hairStyle: "short",
    outfit: "casual",
  },
  {
    id: "receptionist",
    name: "Lan",
    role: "Lễ tân",
    backend: "agents/pipeline · quick / spam / sentiment",
    description:
      "Tiếp nhận mọi yêu cầu: trả lời nhanh câu chào hỏi/FAQ, chặn spam, nhận diện khách đang bức xúc để chuyển cán bộ.",
    greet: "Chào mừng anh/chị đến văn phòng luật VNLaw! Tôi có thể giúp gì ạ?",
    work: { pos: [7, 6.25], facing: Math.PI, anim: "type", seated: true },
    top: "#e85d75",
    inner: "#fff1f2",
    pants: "#334155",
    hair: "#4a2c1a",
    skin: "#f7d4b8",
    hairStyle: "bun",
    outfit: "cardigan",
    accessory: "badge",
    scale: 0.96,
  },
  {
    id: "secretary",
    name: "Minh",
    role: "Thư ký",
    backend: "rewrite_query_v2 · detect_intent",
    description:
      "Ghi chép lại câu hỏi cho rõ nghĩa (dựa trên lịch sử hội thoại) và kiểm tra yêu cầu có thuộc phạm vi pháp luật không.",
    greet: "Chào anh/chị, tôi là thư ký. Anh/chị cứ trình bày, tôi sẽ ghi lại cẩn thận.",
    work: { pos: [-4, 4.25], facing: Math.PI, anim: "type", seated: true },
    top: "#3b9ae1",
    inner: "#ffffff",
    pants: "#475569",
    hair: "#1c1917",
    skin: "#eec4a0",
    hairStyle: "side",
    outfit: "vest",
    accessory: "glasses",
  },
  {
    id: "lead",
    name: "LS. Hải",
    role: "Luật sư chính",
    backend: "Agentic LLM (tool-calling loop)",
    description:
      "Điều phối toàn bộ vụ việc: quyết định cần tra cứu gì, giao việc cho các trợ lý, đọc tài liệu và soạn câu trả lời cuối.",
    greet: "Mời anh/chị ngồi. Anh/chị đang gặp vấn đề pháp lý gì?",
    work: { pos: [0, -2.55], facing: 0, anim: "read", seated: true },
    top: "#1e2a44",
    inner: "#ffffff",
    pants: "#151c2e",
    hair: "#0c0a09",
    skin: "#e9bb96",
    hairStyle: "short",
    outfit: "suit",
    accessory: "tie",
    scale: 1.05,
  },
  {
    id: "librarian",
    name: "Thư",
    role: "Thủ thư điều luật",
    backend: "vector_search · Milvus",
    description: "Tìm các điều luật có nội dung gần nghĩa với câu hỏi trên giá sách Pháp Điển (tìm kiếm ngữ nghĩa).",
    greet: "Xin chào! Cả giá sách Pháp Điển này tôi thuộc lòng đấy.",
    work: { pos: [-8.9, -3.4], facing: -Math.PI / 2, anim: "read" },
    top: "#3fa66b",
    inner: "#f0fdf4",
    pants: "#44403c",
    hair: "#8a3b12",
    skin: "#f5d2b5",
    hairStyle: "long",
    outfit: "cardigan",
    accessory: "glasses",
    scale: 0.95,
  },
  {
    id: "archivist",
    name: "Quân",
    role: "Chuyên viên liên kết",
    backend: "graph_traverse · Neo4j",
    description:
      "Lần theo sợi chỉ đỏ trên bảng liên kết: các điều luật LIÊN QUAN và THAM CHIẾU tới điều đã tìm được.",
    greet: "Mọi điều luật đều có sợi dây liên kết — việc của tôi là lần theo chúng.",
    work: { pos: [6.2, -6.1], facing: Math.PI, anim: "think" },
    top: "#d64545",
    inner: "#fef2f2",
    pants: "#27272a",
    hair: "#18181b",
    skin: "#e2b08a",
    hairStyle: "spiky",
    outfit: "shirt",
    accessory: "pen",
  },
  {
    id: "researcher",
    name: "Vy",
    role: "Trợ lý nghiên cứu web",
    backend: "web_search · Tavily",
    description: "Tra cứu văn bản mới nhất trên Internet khi Pháp Điển chưa có hoặc cần cập nhật.",
    greet: "Hi! Có văn bản nào mới ban hành là tôi tìm ra liền.",
    work: { pos: [8.95, 0.6], facing: Math.PI / 2, anim: "type", seated: true },
    top: "#8b5cf6",
    inner: "#f5f3ff",
    pants: "#1f2937",
    hair: "#2b2523",
    skin: "#f6d6bd",
    hairStyle: "bob",
    outfit: "casual",
    accessory: "headset",
    scale: 0.94,
  },
  {
    id: "clerk",
    name: "Tùng",
    role: "Nhân viên lưu trữ",
    backend: "get_full_article · Neo4j/MySQL",
    description: "Vào kho lưu trữ lấy nguyên văn đầy đủ của một điều luật theo mã (mapc).",
    greet: "Cần nguyên văn điều nào, cứ đọc mã cho tôi.",
    work: { pos: [-2.6, -5.3], facing: 0, anim: "read", seated: true },
    top: "#e08a2c",
    inner: "#fff7ed",
    pants: "#3f3f46",
    hair: "#3f3a36",
    skin: "#ddb089",
    hairStyle: "short",
    outfit: "vest",
    accessory: "bowtie",
    scale: 1.02,
  },
  {
    id: "memory",
    name: "Hạnh",
    role: "Quản lý hồ sơ khách hàng",
    backend: "MemoryManager · long-term memory",
    description: "Giữ hồ sơ những gì văn phòng biết về bạn (đã chia sẻ trước đó) để tư vấn sát hoàn cảnh hơn.",
    greet: "Chào anh/chị! Hồ sơ của khách hàng nào tôi cũng giữ cẩn thận.",
    work: { pos: [-7.4, 3.25], facing: Math.PI, anim: "type", seated: true },
    top: "#14a39a",
    inner: "#f0fdfa",
    pants: "#334155",
    hair: "#5b4636",
    skin: "#f1c9aa",
    hairStyle: "bun",
    outfit: "cardigan",
    scale: 0.97,
  },
];

export const CHARACTER_BY_ID = Object.fromEntries(CHARACTERS.map((c) => [c.id, c])) as Record<
  CharacterId,
  CharacterDef
>;

export const STAFF = CHARACTERS.filter((c) => c.id !== "client");

/** Tool của agent → nhân vật + trạm làm việc */
export const TOOL_ROUTES: Record<
  string,
  { who: CharacterId; station: Vec2; facing: number; verb: string; place: string }
> = {
  vector_search: { who: "librarian", station: STATIONS.library, facing: -Math.PI / 2, verb: "Tìm trên giá sách", place: "Thư viện Pháp Điển" },
  graph_traverse: { who: "archivist", station: STATIONS.graph, facing: Math.PI, verb: "Lần theo liên kết", place: "Bảng liên kết" },
  web_search: { who: "researcher", station: STATIONS.web, facing: Math.PI / 2, verb: "Tra cứu Internet", place: "Máy tra cứu" },
  get_full_article: { who: "clerk", station: STATIONS.vault, facing: Math.PI, verb: "Lấy nguyên văn", place: "Kho lưu trữ" },
};

/** Ghế: vị trí + hướng ngồi (render & cho nhân vật ngồi) */
export const CHAIRS: { pos: Vec2; facing: number; color: string }[] = [
  ...CHARACTERS.filter((c) => c.work.seated).map((c) => ({
    pos: c.work.pos,
    facing: c.work.facing,
    color: c.id === "lead" ? "#7a3b22" : c.id === "client" ? "#3f4a5c" : "#2f3646",
  })),
];

/** Vật cản trên sàn (AABB: tâm, kích thước) — dùng cho cả render lẫn tìm đường & va chạm */
export type Obstacle = { id: string; c: Vec2; size: Vec2 };

export const OBSTACLES: Obstacle[] = [
  { id: "leadDesk", c: [0, -1.2], size: [3.4, 1.4] },
  { id: "reception", c: [7, 5.3], size: [3.0, 1.0] },
  { id: "secretaryDesk", c: [-4, 3.4], size: [2.2, 1.0] },
  { id: "shelf1", c: [-10.4, -5], size: [0.8, 2.6] },
  { id: "shelf2", c: [-10.4, -2], size: [0.8, 2.6] },
  { id: "shelf3", c: [-10.4, 1], size: [0.8, 2.6] },
  { id: "cab1", c: [4.5, -7.4], size: [1.0, 0.8] },
  { id: "cab2", c: [5.7, -7.4], size: [1.0, 0.8] },
  { id: "cab3", c: [6.9, -7.4], size: [1.0, 0.8] },
  { id: "cab4", c: [8.1, -7.4], size: [1.0, 0.8] },
  { id: "vault", c: [-5.5, -7.4], size: [3.2, 0.9] },
  { id: "webDesk", c: [10, 0.6], size: [1.2, 2.4] },
  { id: "memoryCab", c: [-10.2, 5.2], size: [0.9, 2.2] },
  { id: "memoryDesk", c: [-7.4, 2.4], size: [1.8, 0.9] },
  { id: "clerkDesk", c: [-2.6, -4.45], size: [1.8, 0.9] },
  { id: "cooler", c: [2.7, -7.35], size: [0.6, 0.6] },
  { id: "plantA", c: [-10.2, 7.2], size: [0.7, 0.7] },
  { id: "plantB", c: [10.2, -7.2], size: [0.7, 0.7] },
  { id: "plantC", c: [-2.4, -7.3], size: [0.7, 0.7] },
  { id: "plantD", c: [10.2, 7.2], size: [0.7, 0.7] },
  { id: "sofa", c: [3.6, 6.8], size: [2.6, 0.9] },
  { id: "coffeeTable", c: [3.6, 5.9], size: [1.4, 0.6] },
];

/** Câu tán gẫu lúc rảnh */
export const SMALL_TALK: [string, string][] = [
  ["Hôm nay hồ sơ nhiều ghê!", "Ừ, toàn vụ đất đai với lao động."],
  ["Cà phê không?", "Để tôi xong bản này đã!"],
  ["Nghe nói sắp có luật mới sửa đổi.", "Tôi cập nhật vào Pháp Điển rồi."],
  ["Vụ hôm qua xử lý xong chưa?", "Xong rồi, khách hài lòng lắm."],
  ["Trưa nay ăn gì nhỉ?", "Phở đầu ngõ đi!"],
  ["Bảng liên kết rối như tơ vò…", "Rối nhưng mà logic đấy!"],
];
