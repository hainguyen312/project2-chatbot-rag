"""
Đánh giá retrieval KHÔNG cần ground truth gán tay.

Hai phép đo bổ trợ nhau:

1) Pooled LLM-judge (kiểu TREC pooling)
   - Mỗi pipeline lấy top-K điều cho từng câu hỏi; gộp (pool) tất cả, khử trùng.
   - LLM chấm MÙ từng cặp (câu hỏi, điều luật) thang 0/1/2, không biết điều đến từ pipeline nào.
     2 = trả lời trực tiếp, 1 = liên quan/hữu ích một phần, 0 = không liên quan.
   - Tính P@5, P@10 (rel ≥ 1), nDCG@10 (graded, ideal = các điều tốt nhất trong pool),
     MRR@10 (điều rel = 2 đầu tiên), Top1 (rel ≥ 1). So từng cặp pipeline vs baseline:
     win/tie/loss theo nDCG@10 + sign test.
   - Kết quả chấm được cache (evaluation/results/judgments_cache.json) → chạy lại rẻ.

2) Known-item (tuỳ chọn --synthetic N)
   - Sinh câu hỏi đời thường từ N điều ngẫu nhiên (dùng lại compare_title_vs_context),
     "gold" = chính điều đó. Đo Hit@1/5/10, MRR@10. Thiên vị nhẹ về nội dung điều,
     nên đọc cùng với (1).

Pipelines:
   old             : Milvus(tên điều) top-30 → base 0.8 cho mọi hit → overlap cũ (+0.05/token)
   vector_new      : Milvus giữ điểm (RRF) → _rerank mới (base + coverage có chặn)
   hybrid_norerank : Milvus + BM25 (RRF), không rerank lexical
   hybrid_new      : Milvus + BM25 (RRF) → _rerank mới
   hybrid3_*       : Milvus tên điều + Milvus ctx_chunks (header + nội dung) + BM25 (RRF),
                     có / không _rerank — chỉ chạy khi đã seed phapdien_ctx_chunks
   hybrid3_w05     : như hybrid3_new, trọng số RRF của ctx = 0.5
   bm25_only / ctx_only : từng nguồn chạy riêng (không rerank)
   *_llmrr         : *_new + LLM rerank top-12 (gpt-4o-mini, như RAG_AGENT_LLM_RERANK=1);
                     tắt bằng EVAL_LLM_RERANK=0

Judge: Claude (mặc định claude-opus-5-5), structured output JSON {reason, score}.

Chạy (cần OPENAI_API_KEY cho embedding truy vấn + ANTHROPIC_API_KEY cho judge,
Milvus + MySQL đang chạy):
    python evaluation/eval_retrieval_nogt.py
    python evaluation/eval_retrieval_nogt.py --synthetic 100
    python evaluation/eval_retrieval_nogt.py --judge-model claude-sonnet-5-5  # judge rẻ hơn
    python evaluation/eval_retrieval_nogt.py --judge-effort high              # judge Claude kỹ hơn
    python evaluation/eval_retrieval_nogt.py --dry-run                        # chỉ ước tính chi phí
Mặc định chấm qua Batch API (-50%) và dừng nếu ước tính vượt --budget (mặc định $3).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import mean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "evaluation")
sys.path.insert(0, ROOT)
sys.path.insert(0, BASE)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import pymysql  # noqa: E402
from openai import OpenAI  # noqa: E402

client = OpenAI()  # chỉ dùng cho embedding truy vấn (Milvus) + sinh câu synthetic
CACHE_PATH = os.path.join(BASE, "results", "judgments_cache.json")
POOL_K = 30

# Câu hỏi viết tay: trộn văn nói đời thường và thuật ngữ, trải nhiều lĩnh vực.
HANDWRITTEN_QUERIES = [
    "Công ty nợ lương 3 tháng thì tôi phải làm gì",
    "Lao động nữ đang mang thai có bị sa thải không",
    "Nghỉ thai sản được bao nhiêu tháng",
    "Đơn phương nghỉ việc phải báo trước bao nhiêu ngày",
    "Trợ cấp thôi việc được tính như thế nào",
    "Làm thêm giờ tối đa bao nhiêu giờ một tháng",
    "Uống rượu bia rồi lái xe máy bị phạt bao nhiêu tiền",
    "Không đội mũ bảo hiểm bị phạt thế nào",
    "Vượt đèn đỏ bằng ô tô bị tước bằng lái bao lâu",
    "Bao nhiêu tuổi thì được thi bằng lái xe ô tô",
    "Điều kiện để được kết hôn là gì",
    "Ly hôn đơn phương cần thủ tục gì",
    "Con dưới 36 tháng tuổi khi ly hôn thì ai nuôi",
    "Tài sản riêng của vợ chồng gồm những gì",
    "Bố mẹ mất không để lại di chúc thì chia tài sản thế nào",
    "Di chúc miệng có hợp pháp không",
    "Hàng xóm xây nhà lấn sang đất nhà tôi thì xử lý sao",
    "Mua bán đất bằng giấy viết tay có giá trị không",
    "Thời hạn sử dụng đất nông nghiệp là bao lâu",
    "Cho vay tiền không trả thì có kiện được không",
    "Lãi suất cho vay tối đa theo luật là bao nhiêu",
    "Trộm cắp tài sản bao nhiêu tiền thì bị truy cứu trách nhiệm hình sự",
    "Đánh người gây thương tích 11% bị xử lý thế nào",
    "Người dưới 18 tuổi phạm tội có bị phạt tù không",
    "Điều kiện được hưởng án treo",
    "Tội lừa đảo chiếm đoạt tài sản qua mạng",
    "Đánh bạc bao nhiêu tiền thì bị truy cứu hình sự",
    "Tàng trữ trái phép chất ma tuý",
    "Thủ tục thành lập công ty trách nhiệm hữu hạn một thành viên",
    "Hộ kinh doanh có phải nộp thuế thu nhập cá nhân không",
    "Mức giảm trừ gia cảnh thuế thu nhập cá nhân",
    "Đóng bảo hiểm xã hội bao nhiêu năm thì được hưởng lương hưu",
    "Rút bảo hiểm xã hội một lần cần điều kiện gì",
    "Đăng ký thường trú cần giấy tờ gì",
    "Làm lại căn cước công dân bị mất",
    "Nghĩa vụ quân sự bao nhiêu tuổi thì phải đi",
    "Bán hàng online không đăng ký kinh doanh có bị phạt không",
    "Đăng thông tin sai sự thật lên Facebook bị xử phạt thế nào",
    "Quyền của người tiêu dùng khi mua phải hàng giả",
    "Khiếu nại quyết định hành chính trong thời hạn bao lâu",
    "Chống người thi hành công vụ bị xử lý như thế nào",
    "Xả rác ra nơi công cộng bị phạt bao nhiêu",
]


# ------------------------------------------------------------------ data
def mysql():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "localhost"), port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER", "root"), password=os.getenv("MYSQL_PASSWORD", "123456789"),
        database=os.getenv("MYSQL_DATABASE", "law"), charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


_ART: dict[str, dict] = {}


def fetch_articles(mapcs: list[str]) -> dict[str, dict]:
    need = [m for m in set(mapcs) if m not in _ART]
    if need:
        conn = mysql()
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT d.mapc, d.ten, d.noidung, ch.ten AS tenchuong, dm.ten AS tendemuc, cd.ten AS tenchude
                    FROM pddieu d
                    LEFT JOIN pdchuong ch ON d.chuong_id = ch.mapc
                    LEFT JOIN pddemuc dm ON d.demuc_id = dm.id
                    LEFT JOIN pdchude cd ON d.chude_id = cd.id
                    WHERE d.mapc IN ({",".join(["%s"] * len(need))})""",
                need,
            )
            for r in cur.fetchall():
                _ART[r["mapc"]] = r
        conn.close()
    return {m: _ART[m] for m in mapcs if m in _ART}


def load_queries(limit: int | None) -> list[str]:
    qs: list[str] = []
    for f in ("data.json", "data_v2.json", "data_v3.json"):
        p = os.path.join(BASE, f)
        if os.path.exists(p):
            for d in json.load(open(p, encoding="utf-8")):
                if d.get("query"):
                    qs.append(d["query"])
    db = os.path.join(ROOT, "chat_history", "conversations.sqlite")
    if os.path.exists(db):
        for (c,) in sqlite3.connect(db).execute("SELECT content FROM messages WHERE role='user'"):
            if c and len(c) > 15:
                qs.append(c)
    qs += HANDWRITTEN_QUERIES
    seen, out = set(), []
    for q in qs:
        key = re.sub(r"\W+", " ", q.lower()).strip()
        if key not in seen:
            seen.add(key)
            out.append(q.strip())
    return out[:limit] if limit else out


# ------------------------------------------------------------------ pipelines
def old_rerank(rows: list[dict], query: str, top_k: int) -> list[str]:
    """Bản sao logic CŨ: base 0.8 hằng + 0.05 × token trùng (split khoảng trắng, gồm hư từ)."""
    q_tokens = set(query.lower().split())
    scored = []
    for i, r in enumerate(rows):
        text = ((r.get("ten") or "") + " " + (r.get("noidung") or "")[:12000]).lower()
        scored.append((0.8 + 0.05 * sum(1 for t in q_tokens if t in text), -i, r["mapc"]))
    scored.sort(reverse=True)
    return [m for _, _, m in scored[:top_k]]


def make_pipelines(retriever, k: int):
    def rows_for(mapcs: list[str], scores: dict[str, float]) -> list[dict]:
        arts = fetch_articles(mapcs)
        return [dict(arts[m], base_score=scores.get(m, 0.3)) for m in mapcs if m in arts]

    def p_old(q):
        mapcs = retriever._vector_search(q, top_k=POOL_K)
        return old_rerank(rows_for(mapcs, {}), q, k)

    def _fused(q, mode, weights=None):
        fused = retriever._hybrid_search(q, top_k=POOL_K, mode=mode, weights=weights)
        return [d["mapc"] for d in fused], {d["mapc"]: d["score"] for d in fused}

    def _llm_rerank(q, ranked: list[dict], head_n: int = 12) -> list[str]:
        """Giống RAG_AGENT_LLM_RERANK=1 (services/agentic_rag._apply_llm_rerank_head): LLM chấm
        lại top-N (pointwise 0–1) rồi nối phần đuôi; chạy song song cho nhanh."""
        from services.agentic_rag import _llm_score_passage

        head, tail = ranked[:head_n], ranked[head_n:]
        def score(c):
            passage = "\n".join(filter(None, [c.get("ten"), (c.get("noidung") or "")[:8000]]))
            return _llm_score_passage(retriever.client, q, passage)
        with ThreadPoolExecutor(max_workers=8) as ex:
            scores = list(ex.map(score, head))
        order = sorted(range(len(head)), key=lambda i: -scores[i])  # sort ổn định: hoà giữ thứ tự cũ
        return [head[i]["mapc"] for i in order] + [c["mapc"] for c in tail]

    def p_bm25_only(q):
        mapcs, _ = _fused(q, "bm25")
        return mapcs[:k]

    def p_ctx_only(q):
        mapcs, _ = _fused(q, "ctx")
        return mapcs[:k]

    def p_hybrid_llmrr(q):
        mapcs, sc = _fused(q, "hybrid")
        return _llm_rerank(q, retriever._rerank(rows_for(mapcs, sc), q, top_k=POOL_K))[:k]

    def p_hybrid3_llmrr(q):
        mapcs, sc = _fused(q, "hybrid3")
        return _llm_rerank(q, retriever._rerank(rows_for(mapcs, sc), q, top_k=POOL_K))[:k]

    def p_hybrid3_w05(q):
        mapcs, sc = _fused(q, "hybrid3", {"ctx": 0.5})
        return [c["mapc"] for c in retriever._rerank(rows_for(mapcs, sc), q, top_k=k)]

    def p_vector_new(q):
        mapcs, sc = _fused(q, "vector")
        return [c["mapc"] for c in retriever._rerank(rows_for(mapcs, sc), q, top_k=k)]

    def p_hybrid_norerank(q):
        mapcs, _ = _fused(q, "hybrid")
        return mapcs[:k]

    def p_hybrid_new(q):
        mapcs, sc = _fused(q, "hybrid")
        return [c["mapc"] for c in retriever._rerank(rows_for(mapcs, sc), q, top_k=k)]

    def p_hybrid3_norerank(q):
        mapcs, _ = _fused(q, "hybrid3")
        return mapcs[:k]

    def p_hybrid3_new(q):
        mapcs, sc = _fused(q, "hybrid3")
        return [c["mapc"] for c in retriever._rerank(rows_for(mapcs, sc), q, top_k=k)]

    pipes = {
        "old": p_old,
        "vector_new": p_vector_new,
        "hybrid_norerank": p_hybrid_norerank,
        "hybrid_new": p_hybrid_new,
    }
    if retriever.ctx_collection is not None:
        pipes["hybrid3_norerank"] = p_hybrid3_norerank
        pipes["hybrid3_new"] = p_hybrid3_new
        pipes["hybrid3_w05"] = p_hybrid3_w05
        pipes["ctx_only"] = p_ctx_only
    pipes["bm25_only"] = p_bm25_only
    if os.getenv("EVAL_LLM_RERANK", "1") == "1":
        pipes["hybrid_llmrr"] = p_hybrid_llmrr
        if retriever.ctx_collection is not None:
            pipes["hybrid3_llmrr"] = p_hybrid3_llmrr
    return pipes


# ------------------------------------------------------------------ judge
JUDGE_SYS = (
    "Bạn là chuyên gia pháp luật Việt Nam, đánh giá mức độ một điều luật giúp trả lời câu hỏi của người dân.\n"
    "Thang điểm:\n"
    "2 = điều luật trả lời TRỰC TIẾP câu hỏi (chứa quy định/chế tài/điều kiện mà câu hỏi cần).\n"
    "1 = liên quan, hữu ích một phần hoặc là căn cứ bổ trợ, nhưng chưa trả lời trực tiếp.\n"
    "0 = không liên quan, hoặc chỉ trùng từ khoá mà khác vấn đề.\n"
    "Đọc kỹ toàn bộ điều luật trước khi chấm. Trùng từ khoá không đủ để được điểm 1 hoặc 2: "
    "điều luật phải đúng vấn đề, đúng đối tượng, đúng loại quy định mà câu hỏi cần. "
    'Trả về JSON gồm "reason" (một câu ngắn, tiếng Việt) và "score".'
)
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "score": {"type": "integer", "enum": [0, 1, 2]},
    },
    "required": ["reason", "score"],
    "additionalProperties": False,
}
_anthropic_client = None


def _jkey(model: str, q: str, mapc: str) -> str:
    return hashlib.sha1(f"{model}|{q}|{mapc}".encode()).hexdigest()


def _score_of(v) -> int | None:
    """Cache value: {"s": int|None, "r": str}; giá trị khác (vd. cache judge cũ) bị bỏ qua."""
    return v.get("s") if isinstance(v, dict) else None


# Giá $/1M token (input, output) — first-party API, giá chuẩn (Batch API = 50%).
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Ước lượng token đầu ra / lần chấm (suy luận + JSON ngắn) theo effort — chỉ để dự toán,
# cố ý để rộng tay; chi phí THỰC được cộng từ usage sau khi chạy.
OUT_TOKENS_EST = {"low": 250, "medium": 600, "high": 1200, "xhigh": 2000, "max": 3000}
BATCH_STATE = os.path.join(BASE, "results", "judge_batch_pending.json")
PASSAGE_MAX_CHARS = 5000


def _passage(a: dict) -> str:
    return "\n".join(filter(None, [
        f"Chủ đề: {a.get('tenchude') or ''}", f"Chương: {a.get('tenchuong') or ''}",
        a.get("ten") or "", (a.get("noidung") or "")[:PASSAGE_MAX_CHARS],
    ]))


def _anthropic():
    global _anthropic_client
    import anthropic

    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(max_retries=5)
    return _anthropic_client


def _judge_params(model: str, effort: str, q: str, passage: str) -> dict:
    return {
        "model": model,
        "max_tokens": 4000,
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        "system": JUDGE_SYS,
        "messages": [{"role": "user", "content": f"Câu hỏi: {q}\n\nĐiều luật:\n{passage}"}],
    }


def _cost(model: str, in_tok: float, out_tok: float, batch: bool) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (in_tok * pin + out_tok * pout) / 1e6 * (0.5 if batch else 1.0)


def estimate_cost(todo, arts, model: str, effort: str, batch: bool, sample: int = 30) -> float:
    """Đếm token THẬT (count_tokens, miễn phí) trên một mẫu, ngoại suy cho cả todo."""
    if not todo:
        return 0.0
    rng = random.Random(0)
    smp = rng.sample(todo, min(sample, len(todo)))
    toks = []
    for q, m in smp:
        p = _judge_params(model, effort, q, _passage(arts.get(m) or {}))
        r = _anthropic().messages.count_tokens(
            model=model, system=p["system"], messages=p["messages"], output_config=p["output_config"],
        )
        toks.append(r.input_tokens)
    in_tok = mean(toks) * len(todo)
    out_tok = OUT_TOKENS_EST[effort] * len(todo)
    est = _cost(model, in_tok, out_tok, batch)
    print(f"[budget] {len(todo)} lượt chấm · input ≈ {in_tok / 1e6:.2f}M token (đếm thật trên {len(smp)} mẫu, "
          f"tb {mean(toks):.0f}/lượt) · output ước ≈ {out_tok / 1e6:.2f}M · "
          f"{'Batch -50%' if batch else 'giá chuẩn'} → ≈ ${est:.2f}")
    return est


def _parse_judgment(msg) -> dict:
    if msg.stop_reason == "refusal":
        return {"s": None, "r": "refusal"}
    if msg.stop_reason == "max_tokens":
        return {"s": None, "r": "max_tokens"}
    data = json.loads(next(b.text for b in msg.content if b.type == "text"))
    return {"s": int(data["score"]), "r": data.get("reason", "")}


def _run_batch(todo, arts, model: str, effort: str) -> tuple[dict, float, float]:
    """Gửi 1 batch (hoặc lấy lại batch đang dở đã lưu id), chờ xong, trả {(q,m): judgment}."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client_a = _anthropic()
    state = json.load(open(BATCH_STATE, encoding="utf-8")) if os.path.exists(BATCH_STATE) else None
    if state and state.get("model") == model and state.get("effort") == effort:
        batch_id, id_map = state["batch_id"], {k: tuple(v) for k, v in state["id_map"].items()}
        print(f"[batch] tiếp tục batch đã gửi trước đó: {batch_id} (không gửi lại, không tốn thêm)")
    else:
        id_map = {f"p{i}": qm for i, qm in enumerate(todo)}
        reqs = [
            Request(custom_id=cid, params=MessageCreateParamsNonStreaming(
                **_judge_params(model, effort, q, _passage(arts.get(m) or {}))))
            for cid, (q, m) in id_map.items()
        ]
        batch_id = client_a.messages.batches.create(requests=reqs).id
        json.dump({"batch_id": batch_id, "model": model, "effort": effort,
                   "id_map": {k: list(v) for k, v in id_map.items()}},
                  open(BATCH_STATE, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"[batch] đã gửi {len(reqs)} request → {batch_id} (id lưu ở {BATCH_STATE})")

    while True:
        b = client_a.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            break
        rc = b.request_counts
        print(f"  [batch] {b.processing_status}: xong {rc.succeeded + rc.errored + rc.canceled + rc.expired}"
              f"/{len(id_map)}")
        time.sleep(30)

    out, in_tok, out_tok = {}, 0, 0
    for res in client_a.messages.batches.results(batch_id):
        qm = id_map.get(res.custom_id)
        if qm is None:
            continue
        if res.result.type == "succeeded":
            msg = res.result.message
            in_tok += msg.usage.input_tokens
            out_tok += msg.usage.output_tokens
            try:
                out[qm] = _parse_judgment(msg)
            except Exception as e:
                print(f"  parse lỗi {res.custom_id}: {e}")
        else:
            print(f"  [batch] {res.custom_id}: {res.result.type}")
    os.remove(BATCH_STATE)
    return out, in_tok, out_tok


def judge_all(pairs: list[tuple[str, str]], model: str, workers: int, effort: str = "medium",
              batch: bool = True, budget: float = 3.0, dry_run: bool = False) -> dict[tuple[str, str], int] | None:
    cache = json.load(open(CACHE_PATH, encoding="utf-8")) if os.path.exists(CACHE_PATH) else {}
    todo = [(q, m) for q, m in pairs if _score_of(cache.get(_jkey(model, q, m))) is None]
    arts = fetch_articles([m for _, m in todo])
    print(f"[judge] {len(pairs)} cặp, cần chấm mới {len(todo)} (model={model}, effort={effort}, "
          f"{'batch' if batch else 'realtime'})")

    resuming = batch and os.path.exists(BATCH_STATE)
    if todo and not resuming:
        est = estimate_cost(todo, arts, model, effort, batch)
        if dry_run:
            return None
        if est > budget:
            raise SystemExit(f"[budget] Ước tính ${est:.2f} > ngân sách ${budget:.2f} → DỪNG, chưa gọi API chấm. "
                             f"Giảm --judge-effort, đổi --judge-model, hoặc tăng --budget.")
    elif dry_run:
        print("[budget] không còn cặp nào cần chấm (hoặc đang có batch dở)")
        return None

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    in_tok = out_tok = 0
    if todo and batch:
        results, in_tok, out_tok = _run_batch(todo, arts, model, effort)
        for (q, m), v in results.items():
            cache[_jkey(model, q, m)] = v
    elif todo:
        spent = 0.0

        def one(qm):
            q, m = qm
            for attempt in range(3):
                try:
                    msg = _anthropic().messages.create(**_judge_params(model, effort, q, _passage(arts.get(m) or {})))
                    return qm, _parse_judgment(msg), msg.usage
                except Exception as e:
                    if attempt == 2:
                        print(f"  judge lỗi ({type(e).__name__}: {e}) → bỏ qua cặp này")
                        return qm, None, None
                    time.sleep(2 * (attempt + 1))

        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for (q, m), v, usage in ex.map(one, todo):
                if usage is not None:
                    in_tok += usage.input_tokens
                    out_tok += usage.output_tokens
                    spent = _cost(model, in_tok, out_tok, False)
                if v is not None:  # lỗi mạng/API thì không cache → lần sau chấm lại
                    cache[_jkey(model, q, m)] = v
                done += 1
                if done % 50 == 0:
                    print(f"  {done}/{len(todo)} · đã tiêu ≈ ${spent:.2f}")
                    json.dump(cache, open(CACHE_PATH, "w", encoding="utf-8"))
                if spent > budget:
                    print(f"[budget] đã tiêu ${spent:.2f} > ${budget:.2f} → dừng gửi thêm")
                    ex.shutdown(wait=True, cancel_futures=True)
                    break
    json.dump(cache, open(CACHE_PATH, "w", encoding="utf-8"))
    if in_tok:
        print(f"[judge] chi phí thực: input {in_tok:,} + output {out_tok:,} token "
              f"→ ${_cost(model, in_tok, out_tok, batch):.2f}")

    out, missing = {}, 0
    for q, m in pairs:
        s = _score_of(cache.get(_jkey(model, q, m)))
        if s is None:
            missing += 1
            s = 0
        out[(q, m)] = s
    if missing:
        print(f"[judge] ⚠ {missing} cặp không có điểm (lỗi/refusal) → tính là 0")
    return out


# ------------------------------------------------------------------ metrics
def dcg(rels: list[int]) -> float:
    return sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rels))


def query_metrics(ranked: list[str], rel: dict[str, int], k: int) -> dict:
    rels = [rel.get(m, 0) for m in ranked[:k]]
    ideal = sorted(rel.values(), reverse=True)[:k]
    idcg = dcg(ideal)
    mrr = next((1 / (i + 1) for i, r in enumerate(rels) if r == 2), 0.0)
    return {
        "P@5": sum(r >= 1 for r in rels[:5]) / 5,
        f"P@{k}": sum(r >= 1 for r in rels) / k,
        f"nDCG@{k}": dcg(rels) / idcg if idcg > 0 else 0.0,
        f"MRR@{k}(rel=2)": mrr,
        "Top1": float(bool(rels) and rels[0] >= 1),
    }


def sign_test_p(wins: int, losses: int) -> float:
    """Two-sided exact binomial sign test (bỏ tie)."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def print_table(title: str, summary: dict[str, dict[str, float]]):
    names = list(summary)
    cols = list(next(iter(summary.values())))
    print(f"\n=== {title} ===")
    print(f"{'pipeline':<18}" + "".join(f"{c:>15}" for c in cols))
    for n in names:
        print(f"{n:<18}" + "".join(f"{summary[n][c]:>15.3f}" for c in cols))


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--limit", type=int, default=None, help="giới hạn số câu hỏi (debug)")
    ap.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL", "claude-opus-5-5"),
                    help="model Claude làm judge (cần ANTHROPIC_API_KEY)")
    ap.add_argument("--judge-effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"],
                    help="effort cho judge Claude")
    ap.add_argument("--budget", type=float, default=3.0,
                    help="trần chi phí judge (USD); ước tính vượt trần thì dừng trước khi gọi API")
    ap.add_argument("--no-batch", action="store_true",
                    help="chấm realtime (giá đầy đủ) thay vì Batch API (-50%%, thường xong < 1 giờ)")
    ap.add_argument("--dry-run", action="store_true",
                    help="chỉ retrieve + ước tính chi phí judge (count_tokens miễn phí), không chấm")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--baseline", default="old")
    ap.add_argument("--synthetic", type=int, default=0, help="thêm đánh giá known-item với N câu sinh tự động")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    k = args.k

    from pymilvus import Collection, connections
    from retrieve.build_graph import GraphRAGRetriever, get_neo4j
    from retrieve.lexical_bm25 import get_bm25

    connections.connect("default", host=os.getenv("MILVUS_HOST", "localhost"), port=os.getenv("MILVUS_PORT", "19530"))
    col = Collection("phapdien_simple_tendieu")
    col.load()
    retriever = GraphRAGRetriever(get_neo4j(), col, client)
    get_bm25()
    pipelines = make_pipelines(retriever, k)

    # ---------- (1) pooled LLM judge
    queries = load_queries(args.limit)
    print(f"[eval] {len(queries)} câu hỏi × {len(pipelines)} pipelines")
    runs: dict[str, dict[str, list[str]]] = {n: {} for n in pipelines}
    for i, q in enumerate(queries, 1):
        for n, fn in pipelines.items():
            runs[n][q] = fn(q)
        if i % 10 == 0:
            print(f"  retrieved {i}/{len(queries)}")

    pairs = sorted({(q, m) for n in runs for q in queries for m in runs[n][q]})
    random.Random(args.seed).shuffle(pairs)  # chấm mù, không theo thứ tự pipeline
    judged = judge_all(pairs, args.judge_model, args.workers, effort=args.judge_effort,
                       batch=not args.no_batch, budget=args.budget, dry_run=args.dry_run)
    if judged is None:
        return
    rel_by_q: dict[str, dict[str, int]] = {}
    for (q, m), s in judged.items():
        rel_by_q.setdefault(q, {})[m] = s

    per_q = {n: {q: query_metrics(runs[n][q], rel_by_q.get(q, {}), k) for q in queries} for n in pipelines}
    summary = {n: {c: mean(per_q[n][q][c] for q in queries) for c in next(iter(per_q[n].values()))}
               for n in pipelines}
    print_table(f"Pooled LLM-judge ({len(queries)} câu, pool={len(pairs)} cặp, judge={args.judge_model})", summary)

    nd = f"nDCG@{k}"
    print(f"\n=== So với baseline '{args.baseline}' theo {nd} (sign test hai phía) ===")
    for n in pipelines:
        if n == args.baseline:
            continue
        w = sum(per_q[n][q][nd] > per_q[args.baseline][q][nd] + 1e-9 for q in queries)
        l = sum(per_q[n][q][nd] < per_q[args.baseline][q][nd] - 1e-9 for q in queries)
        print(f"  {n:<18} win {w:>3}  tie {len(queries) - w - l:>3}  loss {l:>3}   p={sign_test_p(w, l):.4f}")

    # Câu tệ nhất của cấu hình mới → để soi lỗi bằng mắt
    best_name = "hybrid3_new" if "hybrid3_new" in per_q else "hybrid_new"
    worst = sorted(queries, key=lambda q: per_q[best_name][q][nd])[:5]
    arts = fetch_articles([m for q in worst for m in runs[best_name][q][:3]])
    print(f"\n=== 5 câu {best_name} kém nhất (top-3 + điểm judge) ===")
    for q in worst:
        print(f"- {q}  ({nd}={per_q[best_name][q][nd]:.2f})")
        for m in runs[best_name][q][:3]:
            print(f"    [{rel_by_q.get(q, {}).get(m, '?')}] {(arts.get(m) or {}).get('ten', m)[:100]}")

    out = {"config": vars(args), "summary": summary, "per_query": per_q,
           "runs": runs, "judgments": {q: rel_by_q.get(q, {}) for q in queries}}

    # ---------- (2) known-item synthetic
    if args.synthetic:
        from compare_title_vs_context import load_synthetic

        synth = load_synthetic(args.synthetic)
        ki: dict[str, list[dict]] = {n: [] for n in pipelines}
        for d in synth:
            for n, fn in pipelines.items():
                ranked = fn(d["query"])
                g = d["gold"][0]
                rank = ranked.index(g) + 1 if g in ranked else None
                ki[n].append({"Hit@1": float(rank == 1), "Hit@5": float(bool(rank) and rank <= 5),
                              f"Hit@{k}": float(bool(rank)), f"MRR@{k}": 1 / rank if rank else 0.0})
        ki_sum = {n: {c: mean(x[c] for x in ki[n]) for c in ki[n][0]} for n in pipelines}
        print_table(f"Known-item synthetic ({len(synth)} câu, gold = điều sinh ra câu hỏi)", ki_sum)
        out["known_item"] = ki_sum

    path = os.path.join(BASE, "results", f"nogt_eval_{time.strftime('%Y%m%d_%H%M%S')}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✓ Lưu chi tiết: {path}")


if __name__ == "__main__":
    main()
