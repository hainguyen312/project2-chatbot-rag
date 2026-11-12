"""
So sánh 2 cách index cho retrieval Pháp điển (đơn vị đánh giá = Điều, khóa = mapc):

  A. title  : collection "phapdien_simple_tendieu" — embed CHỈ tên điều, lấy ngữ cảnh từ MySQL sau.
  B. ctx    : collection "phapdien_ctx_chunks"     — embed header ngữ cảnh + chunk nội dung,
              gộp chunk về Điều bằng max score.
  C. rrf    : Reciprocal Rank Fusion của A và B (tham khảo, không tốn thêm embed).

Tùy chọn --rerank: cùng một LLM reranker (gpt-4o-mini, prompt giống two_stage_search) chấm
top-N của MỖI pipeline trên full nội dung Điều lấy từ MySQL -> so sánh công bằng end-to-end.

Dataset:
  - evaluation/data_v3.json   (gold gán thủ công theo mapc)
  - --synthetic N             : sinh N câu hỏi kiểu người dân từ N điều ngẫu nhiên (gold = điều đó),
                                cache vào evaluation/synthetic_queries.json.
    Lưu ý: câu hỏi sinh từ nội dung có xu hướng ưu ái B; prompt đã yêu cầu không lặp tên điều,
    nhưng vẫn nên đọc kết quả 2 dataset riêng.

Chạy:
    python evaluation/compare_title_vs_context.py
    python evaluation/compare_title_vs_context.py --synthetic 100 --rerank
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import mean

import pymysql
from dotenv import load_dotenv
from openai import OpenAI
from pymilvus import Collection, connections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics.retrieval_metrics import hit_at_k, mrr_at_k, ndcg_at_k, recall_at_k  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
TITLE_COL = "phapdien_simple_tendieu"
CTX_COL = "phapdien_ctx_chunks"
EMBED_MODEL = "text-embedding-3-small"
RERANK_MODEL = "gpt-4o-mini"
GEN_MODEL = "gpt-4o-mini"

POOL = 50            # số Điều ứng viên mỗi pipeline
CTX_CHUNK_POOL = 300 # số chunk lấy từ collection B trước khi gộp về Điều
RERANK_N = 20        # số Điều đưa vào LLM rerank
KS = [1, 3, 5, 10, 20, 50]
RRF_K = 60

load_dotenv()
client = OpenAI()


# ------------------------------------------------------------------ IO
def mysql():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456789"),
        database=os.getenv("MYSQL_DATABASE", "law"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


_ARTICLE_CACHE: dict[str, dict] = {}


def fetch_articles(mapcs: list[str]) -> dict[str, dict]:
    """Lấy full Điều theo mapc (khóa ổn định, không phụ thuộc thứ tự dòng)."""
    need = [m for m in set(mapcs) if m not in _ARTICLE_CACHE]
    if need:
        conn = mysql()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d.mapc, d.ten, d.noidung, d.vbqppl,
                       cd.ten AS chude, dm.ten AS demuc, ch.ten AS chuong
                FROM pddieu d
                LEFT JOIN pdchude  cd ON cd.id = d.chude_id
                LEFT JOIN pddemuc  dm ON dm.id = d.demuc_id
                LEFT JOIN pdchuong ch ON ch.mapc = d.chuong_id
                WHERE d.mapc IN ({",".join(["%s"] * len(need))})
                """,
                need,
            )
            for r in cur.fetchall():
                _ARTICLE_CACHE[r["mapc"]] = r
        conn.close()
    return {m: _ARTICLE_CACHE[m] for m in mapcs if m in _ARTICLE_CACHE}


def passage_of(a: dict, max_chars: int = 6000) -> str:
    return (
        f"Chủ đề: {a.get('chude') or ''}\nĐề mục: {a.get('demuc') or ''}\n"
        f"Chương: {a.get('chuong') or ''}\nĐiều: {a.get('ten') or ''}\n"
        f"{(a.get('noidung') or '')[:max_chars]}\nCăn cứ theo {a.get('vbqppl') or ''}"
    )


def embed(texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


# ------------------------------------------------------------------ datasets
def load_gold_dataset() -> list[dict]:
    """data_v3.json: gold gán theo mapc (gold_docs id cũ trong data_v2 không còn khớp DB hiện tại)."""
    data = json.load(open(os.path.join(BASE, "data_v3.json"), encoding="utf-8"))
    return [{"query": d["query"], "gold": d["gold_mapcs"]} for d in data if d.get("gold_mapcs")]


SYNTH_PROMPT = (
    "Bạn là người dân không có chuyên môn pháp luật. Đọc điều luật dưới đây và viết MỘT câu hỏi "
    "thực tế mà người dân có thể hỏi, mà điều luật này trả lời được. Yêu cầu:\n"
    "- Mô tả tình huống/nhu cầu cụ thể, dùng ngôn ngữ đời thường.\n"
    "- KHÔNG chép lại tên điều, KHÔNG nhắc số hiệu điều/văn bản.\n"
    "- Nếu điều luật chỉ là định nghĩa/phạm vi/hiệu lực chung chung, trả về đúng chữ SKIP.\n"
    "Chỉ in ra câu hỏi."
)


def load_synthetic(n: int, seed: int = 42) -> list[dict]:
    path = os.path.join(BASE, "synthetic_queries.json")
    if os.path.exists(path):
        cached = json.load(open(path, encoding="utf-8"))
        if len(cached) >= n:
            return cached[:n]
    conn = mysql()
    with conn.cursor() as cur:
        cur.execute("SELECT mapc, ten, noidung FROM pddieu WHERE CHAR_LENGTH(noidung) > 200 ORDER BY mapc")
        rows = cur.fetchall()
    conn.close()
    rng = random.Random(seed)
    rng.shuffle(rows)

    out: list[dict] = []
    for r in rows:
        if len(out) >= n:
            break
        resp = client.chat.completions.create(
            model=GEN_MODEL,
            temperature=0.7,
            messages=[
                {"role": "system", "content": SYNTH_PROMPT},
                {"role": "user", "content": f"{r['ten']}\n{r['noidung'][:4000]}"},
            ],
        )
        q = resp.choices[0].message.content.strip()
        if q.upper().startswith("SKIP") or len(q) < 10:
            continue
        out.append({"query": q, "gold": [r["mapc"]], "source_title": r["ten"]})
        print(f"  synth {len(out)}/{n}: {q[:90]}")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return out


# ------------------------------------------------------------------ retrievers
def search_title(col: Collection, qvec) -> list[tuple[str, float]]:
    res = col.search([qvec], "embedding", {"metric_type": "COSINE", "params": {"ef": 128}},
                     limit=POOL, output_fields=["metadata"])
    out, seen = [], set()
    for h in res[0]:
        meta = h.entity.get("metadata") or {}
        m = meta.get("mapc")
        if m and m not in seen:
            seen.add(m)
            out.append((m, float(h.distance)))
    return out


def search_ctx(col: Collection, qvec) -> list[tuple[str, float]]:
    res = col.search([qvec], "embedding", {"metric_type": "COSINE", "params": {"ef": max(CTX_CHUNK_POOL, 128)}},
                     limit=CTX_CHUNK_POOL, output_fields=["mapc"])
    best: dict[str, float] = {}
    for h in res[0]:
        m = h.entity.get("mapc")
        s = float(h.distance)
        if m and s > best.get(m, -1.0):
            best[m] = s
    return sorted(best.items(), key=lambda x: x[1], reverse=True)[:POOL]


def rrf(*lists: list[tuple[str, float]]) -> list[tuple[str, float]]:
    score: dict[str, float] = {}
    for lst in lists:
        for rank, (m, _) in enumerate(lst):
            score[m] = score.get(m, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(score.items(), key=lambda x: x[1], reverse=True)[:POOL]


RERANK_SYS = (
    "Bạn là một hệ thống chấm điểm truy hồi văn bản phục vụ cho trả lời truy vấn pháp luật. "
    "Nhiệm vụ của bạn là đánh giá mức độ đoạn văn (passage) trả lời được câu hỏi (query). "
    "Đoạn văn nào trực tiếp cần dùng để trả lời câu hỏi thì điểm cao."
    "Hãy trả về MỘT số thực trong khoảng từ 0 đến 1:\n"
    "- 0: hoàn toàn không liên quan\n- 1: rất liên quan, trả lời trực tiếp câu hỏi\n"
    "Chỉ in ra đúng MỘT số, không giải thích gì thêm."
)
_SCORE_CACHE: dict[tuple[str, str], float] = {}


def _llm_score(query: str, mapc: str, passage: str) -> float:
    key = (query, mapc)
    if key in _SCORE_CACHE:
        return _SCORE_CACHE[key]
    try:
        resp = client.chat.completions.create(
            model=RERANK_MODEL, temperature=0.0, max_tokens=8,
            messages=[{"role": "system", "content": RERANK_SYS},
                      {"role": "user", "content": f"Query:\n{query}\n\nPassage:\n{passage}"}],
        )
        m = re.search(r"([01](?:\.\d+)?)", resp.choices[0].message.content or "")
        s = max(0.0, min(1.0, float(m.group(1)))) if m else 0.0
    except Exception as e:
        print(f"  [rerank err] {e}")
        s = 0.0
    _SCORE_CACHE[key] = s
    return s


def llm_rerank(query: str, ranked: list[tuple[str, float]]) -> list[tuple[str, float]]:
    head = [m for m, _ in ranked[:RERANK_N]]
    arts = fetch_articles(head)
    with ThreadPoolExecutor(max_workers=8) as ex:
        scores = list(ex.map(lambda m: _llm_score(query, m, passage_of(arts.get(m, {}))), head))
    # tie-break theo thứ hạng ban đầu
    order = sorted(range(len(head)), key=lambda i: (-scores[i], i))
    return [(head[i], scores[i]) for i in order] + ranked[RERANK_N:]


# ------------------------------------------------------------------ eval
def metrics_for(ranked: list[str], gold: list[str]) -> dict:
    out = {}
    for k in KS:
        out[f"hit@{k}"] = hit_at_k(ranked, gold, k)
        out[f"recall@{k}"] = recall_at_k(ranked, gold, k)
        out[f"mrr@{k}"] = mrr_at_k(ranked, gold, k)
        out[f"ndcg@{k}"] = ndcg_at_k(ranked, gold, k)
    first = next((i + 1 for i, m in enumerate(ranked) if m in set(gold)), None)
    out["first_gold_rank"] = first
    return out


def evaluate(name: str, dataset: list[dict], title_col, ctx_col, do_rerank: bool) -> dict:
    print(f"\n=== Dataset: {name} ({len(dataset)} query) ===")
    qvecs = []
    for i in range(0, len(dataset), 100):
        qvecs.extend(embed([d["query"] for d in dataset[i:i + 100]]))

    systems = ["title", "ctx", "rrf"] + (["title+rerank", "ctx+rerank", "rrf+rerank"] if do_rerank else [])
    per_q: dict[str, list[dict]] = {s: [] for s in systems}
    lat: dict[str, list[float]] = {"title": [], "ctx": []}

    for d, qv in zip(dataset, qvecs):
        t0 = time.perf_counter(); a = search_title(title_col, qv); lat["title"].append(time.perf_counter() - t0)
        t0 = time.perf_counter(); b = search_ctx(ctx_col, qv); lat["ctx"].append(time.perf_counter() - t0)
        c = rrf(a, b)
        runs = {"title": a, "ctx": b, "rrf": c}
        if do_rerank:
            runs["title+rerank"] = llm_rerank(d["query"], a)
            runs["ctx+rerank"] = llm_rerank(d["query"], b)
            runs["rrf+rerank"] = llm_rerank(d["query"], c)
        for s, r in runs.items():
            m = metrics_for([x for x, _ in r], d["gold"])
            m["query"] = d["query"]
            per_q[s].append(m)

    summary = {}
    for s in systems:
        rows = per_q[s]
        avg = {k: mean(r[k] for r in rows) for k in rows[0] if k not in ("query", "first_gold_rank")}
        avg["miss@50"] = sum(1 for r in rows if r["first_gold_rank"] is None) / len(rows)
        summary[s] = avg

    # thắng/thua theo từng query (MRR@10) giữa title và ctx
    def wins(x, y):
        w = l = 0
        for rx, ry in zip(per_q[x], per_q[y]):
            if rx["mrr@10"] > ry["mrr@10"]: w += 1
            elif rx["mrr@10"] < ry["mrr@10"]: l += 1
        return {"win": w, "loss": l, "tie": len(per_q[x]) - w - l}

    h2h = {"ctx_vs_title": wins("ctx", "title")}
    if do_rerank:
        h2h["ctx+rerank_vs_title+rerank"] = wins("ctx+rerank", "title+rerank")

    print_table(summary)
    print(f"Head-to-head (MRR@10): {h2h}")
    print(f"Latency search TB: title={mean(lat['title'])*1000:.1f}ms ctx={mean(lat['ctx'])*1000:.1f}ms")
    return {"summary": summary, "head_to_head": h2h,
            "latency_ms": {k: mean(v) * 1000 for k, v in lat.items()}, "per_query": per_q}


def print_table(summary: dict):
    cols = ["hit@1", "hit@5", "recall@5", "recall@10", "recall@20", "recall@50", "mrr@10", "ndcg@10", "miss@50"]
    print(f"{'system':<14}" + "".join(f"{c:>11}" for c in cols))
    for s, m in summary.items():
        print(f"{s:<14}" + "".join(f"{m[c]:>11.3f}" for c in cols))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", type=int, default=0, help="Sinh thêm N câu hỏi tổng hợp")
    ap.add_argument("--rerank", action="store_true", help="Thêm LLM rerank (tốn API)")
    ap.add_argument("--out", default=os.path.join(BASE, "results", "compare_title_vs_context.json"))
    args = ap.parse_args()

    connections.connect("default", host=os.getenv("MILVUS_HOST", "localhost"),
                        port=os.getenv("MILVUS_PORT", "19530"))
    title_col = Collection(TITLE_COL); title_col.load()
    ctx_col = Collection(CTX_COL); ctx_col.load()
    print(f"{TITLE_COL}: {title_col.num_entities:,} entity | {CTX_COL}: {ctx_col.num_entities:,} entity")

    report = {"config": {"POOL": POOL, "CTX_CHUNK_POOL": CTX_CHUNK_POOL, "RERANK_N": RERANK_N,
                         "embed_model": EMBED_MODEL, "rerank_model": RERANK_MODEL}}
    report["gold_v3"] = evaluate("data_v3 (gold thủ công, theo mapc)", load_gold_dataset(),
                                 title_col, ctx_col, args.rerank)
    if args.synthetic:
        report["synthetic"] = evaluate(f"synthetic ({args.synthetic})", load_synthetic(args.synthetic),
                                       title_col, ctx_col, args.rerank)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(report, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nĐã lưu: {args.out}")


if __name__ == "__main__":
    main()
