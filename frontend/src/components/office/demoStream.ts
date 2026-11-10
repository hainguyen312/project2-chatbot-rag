import type { Passage, StreamEvent } from "./director";
import { ANGRY, GREETING, OUT_OF_SCOPE, SCENARIOS, SPAM, genericScenario } from "./mockData";

/**
 * MOCK backend: giả lập chuỗi SSE của /rag/stream (rag_api.rag_stream) với
 * đúng thứ tự & định dạng event, để chạy văn phòng 3D không cần
 * Milvus / MySQL / Neo4j / OpenAI.
 */
const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const id = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(id);
      reject(new DOMException("Aborted", "AbortError"));
    });
  });

const MAX_IT = 6;
const answeredInSession = new Map<string, { answer: string; passages: Passage[] }>();
const normalize = (s: string) => s.toLowerCase().replace(/[?？!.\s]+/g, " ").trim();

function* tokens(text: string): Generator<StreamEvent> {
  for (const part of text.match(/[\s\S]{1,32}/g) ?? []) yield { type: "token", text: part };
}

export async function* demoStream(
  prompt: string,
  signal?: AbortSignal,
  opts: { turn?: number } = {},
): AsyncGenerator<StreamEvent> {
  const q = prompt.trim();

  // ── Lễ tân: pre-retrieve ──────────────────────────────────────────────────
  yield { type: "status", stage: "reception", text: "Lễ tân đang tiếp nhận yêu cầu..." };
  await sleep(1300, signal);

  const quick = GREETING.test(q) && q.length < 40;
  const action = SPAM.test(q) ? "spam" : ANGRY.test(q) ? "escalate" : quick ? "quick" : "proceed";
  yield { type: "status", stage: "reception_done", action, text: "Đã phân loại yêu cầu" };

  if (action !== "proceed") {
    await sleep(action === "spam" ? 800 : 2600, signal);
    const answer =
      action === "quick"
        ? /cảm ơn|thank/i.test(q)
          ? "Không có gì ạ! Nếu cần tư vấn thêm vấn đề pháp lý nào, bạn cứ hỏi nhé."
          : "Xin chào! Mình là trợ lý pháp luật của văn phòng. Bạn cần tư vấn vấn đề gì ạ?"
        : action === "spam"
          ? "Xin lỗi, yêu cầu có dấu hiệu spam. Vui lòng cung cấp thông tin cụ thể hơn."
          : "Mình rất tiếc. Bạn có muốn mình chuyển tiếp cho cán bộ xử lý không?";
    yield { type: "meta", action, passages: [] };
    yield* tokens(answer);
    yield { type: "done" };
    return;
  }

  // ── Thư ký: rewrite + intent ──────────────────────────────────────────────
  yield { type: "status", stage: "secretary", text: "Thư ký đang chuẩn hoá câu hỏi..." };
  await sleep(3200, signal);
  const nq = q.replace(/[?？]+$/, "").trim();
  const inScope = !OUT_OF_SCOPE.test(q);
  yield { type: "status", stage: "secretary_done", text: "Đã chuẩn hoá câu hỏi", normalized_query: nq, in_scope: inScope };

  if (!inScope) {
    await sleep(2500, signal);
    yield { type: "meta", action: "proceed", normalized_query: nq, passages: [] };
    yield* tokens("Yêu cầu chưa rõ ràng hoặc ngoài phạm vi. Mình hỗ trợ tìm kiếm và giải thích pháp luật Việt Nam.");
    yield { type: "done" };
    return;
  }

  // ── Hồ sơ khách hàng (memory) + sổ tay (cache) ───────────────────────────
  await sleep(500, signal);
  yield { type: "status", stage: "memory", text: "Đã tra hồ sơ khách hàng", count: Math.min(opts.turn ?? 0, 3) };
  const cached = answeredInSession.get(normalize(nq));
  yield { type: "status", stage: "cache", text: "Kiểm tra sổ tay đã trả lời", hit: !!cached };
  await sleep(cached ? 5500 : 2800, signal);

  if (cached) {
    yield {
      type: "meta", action: "proceed", normalized_query: nq, passages: cached.passages,
      cache_hit: true, confidence_score: 0.9,
    };
    yield* tokens(cached.answer);
    yield { type: "done" };
    return;
  }

  // ── Luật sư chính: agentic tool loop ──────────────────────────────────────
  const sc = SCENARIOS.find((s) => s.match.test(q)) ?? genericScenario(nq);
  let callN = 0;
  for (let it = 0; it < sc.rounds.length; it++) {
    const round = sc.rounds[it];
    const calls = round.map((c) => ({ ...c, id: `mock_${Date.now()}_${callN++}` }));
    // backend gọi tuần tự từng tool trong một vòng; mock phát start cho cả vòng
    // để các trợ lý cùng xuất phát cho sinh động
    for (const c of calls) {
      yield {
        type: "status", stage: "tool", tool: c.tool, call_id: c.id, args: c.args,
        iteration: it + 1, max: MAX_IT, text: `🔍 Đang gọi ${c.tool}...`,
      };
    }
    await sleep(Math.max(...calls.map((c) => c.ms)), signal);
    for (const c of calls) {
      const hits = c.resultIdx.map((i) => sc.passages[i]).filter(Boolean);
      yield {
        type: "status", stage: "tool_done", tool: c.tool, call_id: c.id,
        count: hits.length, titles: hits.map((h) => h.ten),
        iteration: it + 1, max: MAX_IT, text: `✅ ${c.tool} xong`,
      };
    }
    await sleep(2500, signal); // luật sư đọc tài liệu trước khi quyết định vòng tiếp
  }

  yield { type: "status", stage: "compose", text: "Luật sư đang soạn câu trả lời...", iteration: sc.rounds.length + 1, max: MAX_IT };
  await sleep(3500, signal);

  const answer = "🧪 *Chế độ Mock — dữ liệu mẫu*\n\n" + sc.answer;
  yield {
    type: "meta", action: "proceed", normalized_query: nq, passages: sc.passages,
    cache_hit: false, confidence_score: 0.82,
  };
  yield* tokens(answer);
  answeredInSession.set(normalize(nq), { answer, passages: sc.passages });
  yield { type: "done" };
}
