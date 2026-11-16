"""
Đánh giá "KIỂM TRA NGOẠI LỆ" bằng đồ thị: điều chính cho phép, nhưng một điều liên quan CẤM / đặt NGOẠI LỆ
(vd. đủ tuổi kết hôn ↔ cấm kết hôn trong phạm vi ba đời; được sa thải ↔ không được kỷ luật người đang mang thai).

1) Sinh bộ case từ chính đồ thị (cache evaluation/exception_cases.json):
   - Lấy cặp (A = điều thường, B = điều cấm/ngoại lệ) nối nhau bằng THAM_CHIEU (hai chiều) hoặc LIEN_QUAN.
   - Claude đọc A + B: chỉ giữ cặp mà B THỰC SỰ giới hạn điều A cho phép, rồi viết một câu hỏi đời thường
     mà A trả lời, KHÔNG nhắc tới điều kiện ngoại lệ (người hỏi không biết có ngoại lệ) + tóm tắt ngoại lệ.
2) Retrieval: câu hỏi → rewrite_query_v2 (như pipeline) → vector_search; B có trong kết quả tool không?
   So sánh AGENT_EXCEPTION_CHECK tắt / bật.
3) (--answers) Câu trả lời: chạy agent đầy đủ (OPENAI_AGENT_MODEL) tắt / bật; Claude chấm câu trả lời có nêu đúng
   ngoại lệ không (0/1). Đây là phép đo đóng góp của Neo4j ở mức câu trả lời.

Chạy (Milvus + MySQL + Neo4j đang chạy, OPENAI_API_KEY + ANTHROPIC_API_KEY):
    python evaluation/eval_exceptions.py --n 30
    python evaluation/eval_exceptions.py --n 30 --answers
"""
from __future__ import annotations

import argparse
import json
import os
import random
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

CASES_PATH = os.path.join(BASE, "exception_cases.json")
GEN_MODEL = os.getenv("EXC_GEN_MODEL", "claude-sonnet-5-5")
JUDGE_MODEL = os.getenv("EXC_JUDGE_MODEL", "claude-sonnet-5-5")
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}

GEN_SYS = (
    "Bạn là chuyên gia pháp luật Việt Nam, đang soạn bộ câu hỏi kiểm thử cho chatbot pháp luật.\n"
    "Cho điều A (quy định chính) và điều B (có khoản CẤM / NGOẠI LỆ). Hãy xác định B có THỰC SỰ giới hạn, "
    "loại trừ hoặc cấm một điều mà A cho phép/quy định hay không (B làm ĐỔI kết luận nếu chỉ đọc A).\n"
    "Nếu có: viết MỘT câu hỏi đời thường của người dân (ngôi thứ nhất, 1–2 câu) mà A trả lời, đặt trong tình "
    "huống mà ngoại lệ ở B CÓ THỂ áp dụng nhưng người hỏi KHÔNG biết và KHÔNG nhắc tới điều kiện ngoại lệ "
    "(không dùng từ ngữ của B). Ví dụ: A = điều kiện kết hôn, B = cấm kết hôn người có họ trong phạm vi ba đời "
    "→ 'Tôi 25 tuổi, muốn cưới con gái của chú ruột tôi thì có được không?'. Kèm 'ngoai_le': một câu tóm tắt "
    "ngoại lệ mà câu trả lời đúng PHẢI nêu.\n"
    "Nếu B không giới hạn A (chỉ cùng chủ đề, quy định thủ tục, hoặc ngoại lệ không ảnh hưởng ai hỏi theo A): "
    "hop_le = false."
)
GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "hop_le": {"type": "boolean"},
        "cau_hoi": {"type": "string"},
        "ngoai_le": {"type": "string"},
    },
    "required": ["hop_le", "cau_hoi", "ngoai_le"],
    "additionalProperties": False,
}
JUDGE_SYS = (
    "Bạn chấm câu trả lời của chatbot pháp luật. Cho câu hỏi, NGOẠI LỆ mà câu trả lời đúng phải nêu, và câu "
    "trả lời. Chấm 1 nếu câu trả lời nêu rõ ngoại lệ/điều cấm đó (hoặc kết luận theo nó, hoặc kết luận có điều "
    "kiện 'được, trừ khi …' đúng ngoại lệ đó); 0 nếu bỏ qua, nêu sai, hoặc kết luận 'được' mà không nhắc tới. "
    'Trả về JSON {"reason": một câu ngắn tiếng Việt, "score": 0 hoặc 1}.'
)
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"reason": {"type": "string"}, "score": {"type": "integer", "enum": [0, 1]}},
    "required": ["reason", "score"],
    "additionalProperties": False,
}

_usage = {"in": 0, "out": 0}
_anth = None


def anthropic_json(model: str, system: str, user: str, schema: dict, effort: str = "low") -> dict:
    global _anth
    import anthropic

    if _anth is None:
        _anth = anthropic.Anthropic(max_retries=5)
    msg = _anth.messages.create(
        model=model, max_tokens=3000, system=system,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user}],
    )
    _usage["in"] += msg.usage.input_tokens
    _usage["out"] += msg.usage.output_tokens
    text = next(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return json.loads(text)


def cost() -> float:
    pin, pout = PRICES.get(GEN_MODEL, PRICES["claude-sonnet-5-5"])
    return (_usage["in"] * pin + _usage["out"] * pout) / 1e6


def article(driver, mapc: str) -> dict:
    with driver.session() as s:
        r = s.run("MATCH (d:Dieu {mapc: $m}) RETURN d.mapc AS mapc, d.ten AS ten, d.noidung AS noidung, "
                  "d.vbqppl AS vbqppl", m=mapc).single()
    return dict(r) if r else {}


# ------------------------------------------------------------------ 1. sinh case
def candidate_pairs(driver, seed: int) -> list[tuple[str, str, str]]:
    with driver.session() as s:
        rows = [dict(r) for r in s.run("""
            MATCH (a:Dieu)-[r:THAM_CHIEU|LIEN_QUAN]-(b:Dieu)
            WHERE NOT coalesce(a.ngoai_le, false) AND coalesce(b.ngoai_le, false)
                  AND size(coalesce(a.noidung, '')) > 200
            RETURN DISTINCT a.mapc AS a, b.mapc AS b,
                   CASE WHEN type(r) = 'THAM_CHIEU' AND coalesce(r.ngoai_le, false) THEN 'tham_chieu_ngoai_le'
                        ELSE toLower(type(r)) END AS loai
        """)]
    rng = random.Random(seed)
    rng.shuffle(rows)
    # Trộn đều các loại cạnh để không chỉ đo một kiểu quan hệ
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["loai"], []).append((r["a"], r["b"], r["loai"]))
    out, seen_a = [], set()
    while any(by.values()):
        for k in sorted(by):
            while by[k]:
                a, b, loai = by[k].pop()
                if a not in seen_a:
                    seen_a.add(a)
                    out.append((a, b, loai))
                    break
    return out


def build_cases(driver, n: int, seed: int, budget: float) -> list[dict]:
    cases = json.load(open(CASES_PATH, encoding="utf-8")) if os.path.exists(CASES_PATH) else []
    if len(cases) >= n:
        return cases[:n]
    done = {(c["a"], c["b"]) for c in cases}
    tried = 0
    for a, b, loai in candidate_pairs(driver, seed):
        if len(cases) >= n or cost() > budget:
            break
        if (a, b) in done:
            continue
        A, B = article(driver, a), article(driver, b)
        tried += 1
        try:
            g = anthropic_json(GEN_MODEL, GEN_SYS,
                               f"ĐIỀU A:\n{A['ten']}\n{(A['noidung'] or '')[:3500]}\n\n"
                               f"ĐIỀU B:\n{B['ten']}\n{(B['noidung'] or '')[:3500]}", GEN_SCHEMA)
        except Exception as e:
            print(f"  [gen] lỗi {e}")
            continue
        if g["hop_le"] and g["cau_hoi"].strip():
            cases.append({"a": a, "b": b, "loai_canh": loai, "ten_a": A["ten"], "ten_b": B["ten"],
                          "cau_hoi": g["cau_hoi"].strip(), "ngoai_le": g["ngoai_le"].strip()})
            print(f"  [{len(cases)}/{n}] {g['cau_hoi'][:90]}")
            json.dump(cases, open(CASES_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[gen] thử {tried} cặp → {len(cases)} case hợp lệ (chi phí ~${cost():.2f})")
    return cases


# ------------------------------------------------------------------ 2. retrieval
def retrieval_eval(cases, retriever, driver) -> dict:
    import services.agentic_rag as A
    from services.utils import rewrite_query_v2

    rows = []
    for c in cases:
        q = rewrite_query_v2(c["cau_hoi"], []) or c["cau_hoi"]
        r = {"cau_hoi": c["cau_hoi"], "query": q}
        for flag in (False, True):
            A.AGENT_EXCEPTION_CHECK = flag
            _, hits = A.execute_agent_tool("vector_search", {"query": q, "top_k": 10}, retriever=retriever,
                                           neo4j_driver=driver, rerank_query=q)
            ids = [h.get("mapc") for h in hits]
            key = "on" if flag else "off"
            r[f"a_{key}"] = c["a"] in ids
            r[f"b_{key}"] = c["b"] in ids
            r[f"n_{key}"] = len(ids)
        rows.append(r)
    A.AGENT_EXCEPTION_CHECK = True
    summ = {
        "A (điều chính) có trong kết quả": mean(x["a_on"] for x in rows),
        "B (ngoại lệ) có — TẮT kiểm tra ngoại lệ": mean(x["b_off"] for x in rows),
        "B (ngoại lệ) có — BẬT kiểm tra ngoại lệ": mean(x["b_on"] for x in rows),
        "B có | A có — BẬT": (mean(x["b_on"] for x in rows if x["a_on"]) if any(x["a_on"] for x in rows) else 0.0),
        "số kết quả TB — TẮT": mean(x["n_off"] for x in rows),
        "số kết quả TB — BẬT": mean(x["n_on"] for x in rows),
    }
    return {"summary": summ, "rows": rows}


# ------------------------------------------------------------------ 3. câu trả lời
def answer_eval(cases, retriever, driver, workers: int) -> dict:
    import services.agentic_rag as A
    from services.utils import client as oai, rewrite_query_v2

    def run(c):
        q = rewrite_query_v2(c["cau_hoi"], []) or c["cau_hoi"]
        for attempt in range(4):
            try:
                out = A.run_agentic_rag_sync(openai_client=oai, retriever=retriever, neo4j_driver=driver,
                                             user_prompt=c["cau_hoi"], history=[], rerank_query=q)
                break
            except Exception as e:  # rate limit… → thử lại; vẫn lỗi thì ghi nhận, không dừng cả lượt
                if attempt == 3:
                    return {"answer": "", "score": None, "reason": f"lỗi: {e}", "has_b": False}
                time.sleep(15 * (attempt + 1))
        j = anthropic_json(JUDGE_MODEL, JUDGE_SYS,
                           f"Câu hỏi: {c['cau_hoi']}\n\nNGOẠI LỆ phải nêu: {c['ngoai_le']}\n\n"
                           f"Câu trả lời:\n{out['answer']}", JUDGE_SCHEMA)
        return {"answer": out["answer"], "score": j["score"], "reason": j["reason"],
                "has_b": c["b"] in [h.get("mapc") for h in out["passage_hits"]]}

    res = {}
    try:
        for flag in (False, True):
            A.AGENT_EXCEPTION_CHECK = flag  # cờ module: đặt một lần cho cả pha, các luồng không tự đổi
            with ThreadPoolExecutor(max_workers=workers) as ex:
                res["on" if flag else "off"] = list(ex.map(run, cases))
    finally:
        A.AGENT_EXCEPTION_CHECK = True
    ok = [i for i in range(len(cases)) if res["off"][i]["score"] is not None and res["on"][i]["score"] is not None]
    n = len(ok)
    w = sum(res["on"][i]["score"] > res["off"][i]["score"] for i in ok)
    l = sum(res["on"][i]["score"] < res["off"][i]["score"] for i in ok)
    return {
        "summary": {
            "agent model": A.agent_chat_model(),
            "số case chấm được": n,
            "nêu đúng ngoại lệ — TẮT": mean(res["off"][i]["score"] for i in ok) if ok else 0.0,
            "nêu đúng ngoại lệ — BẬT": mean(res["on"][i]["score"] for i in ok) if ok else 0.0,
            "B trong ngữ cảnh agent — TẮT": mean(x["has_b"] for x in res["off"]),
            "B trong ngữ cảnh agent — BẬT": mean(x["has_b"] for x in res["on"]),
            "BẬT tốt hơn / kém hơn / hoà": f"{w} / {l} / {n - w - l}",
        },
        "rows": [{"cau_hoi": c["cau_hoi"], "ngoai_le": c["ngoai_le"], "off": res["off"][i], "on": res["on"][i]}
                 for i, c in enumerate(cases)],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--answers", action="store_true", help="đo cả câu trả lời (chạy agent + judge)")
    ap.add_argument("--budget", type=float, default=1.5, help="trần chi phí Claude (sinh case + judge), USD")
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    from pymilvus import Collection, connections
    from retrieve.build_graph import GraphRAGRetriever, get_neo4j
    from retrieve.lexical_bm25 import get_bm25
    from services.utils import client as oai

    driver = get_neo4j()
    cases = build_cases(driver, args.n, args.seed, args.budget * 0.5)
    connections.connect("default", host=os.getenv("MILVUS_HOST", "localhost"), port=os.getenv("MILVUS_PORT", "19530"))
    col = Collection("phapdien_simple_tendieu")
    col.load()
    get_bm25()
    retriever = GraphRAGRetriever(driver, col, oai)

    out = {"config": vars(args), "n_cases": len(cases)}
    print(f"\n=== Retrieval ({len(cases)} case) ===")
    out["retrieval"] = retrieval_eval(cases, retriever, driver)
    for k, v in out["retrieval"]["summary"].items():
        print(f"  {k:<42} {v:.3f}" if isinstance(v, float) else f"  {k:<42} {v}")
    if args.answers:
        from services.agentic_rag import agent_chat_model
        print(f"\n=== Câu trả lời (agent {agent_chat_model()}, judge {JUDGE_MODEL}) ===")
        out["answers"] = answer_eval(cases, retriever, driver, args.workers)
        for k, v in out["answers"]["summary"].items():
            print(f"  {k:<42} {v:.3f}" if isinstance(v, float) else f"  {k:<42} {v}")
    out["claude_cost_usd"] = round(cost(), 3)
    print(f"\nChi phí Claude (sinh case + judge): ${cost():.2f}")
    path = os.path.join(BASE, "results", f"exception_eval_{time.strftime('%Y%m%d_%H%M%S')}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✓ Lưu chi tiết: {path}")


if __name__ == "__main__":
    main()
