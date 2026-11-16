"""
Ví dụ cho phần thảo luận tìm kiếm lai của đồ án: thứ hạng của một điều ở BM25, vector tên điều, sau RRF và sau
xếp hạng lại, với hai trọng số vector. Chỉ gọi embedding cho truy vấn (không gọi LLM); pipeline giống
evaluation/eval_retrieval_nogt.py (hybrid_new).

    python scripts/thesis_rrf_example.py   → evaluation/results/thesis_rrf_example.json
"""
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import retrieve.build_graph as BG  # noqa: E402
from evaluation.eval_retrieval_nogt import POOL_K, fetch_articles  # noqa: E402
from retrieve.lexical_bm25 import get_bm25  # noqa: E402
from retrieve.two_stage_search import client, collection  # noqa: E402

QUERY = "Lao động nữ đang mang thai có bị sa thải không?"
SO_HIEU, SO_DIEU = "45/2019/QH14", "137"
WIDE = 200
WIDE_VEC = 128  # Milvus HNSW: ef=128 nên top_k vector tối đa 128


def rank_of(mapcs, m):
    return next((i + 1 for i, x in enumerate(mapcs) if x == m), None)


def main():
    drv = BG.get_neo4j()
    r = BG.GraphRAGRetriever(drv, collection, client)
    with drv.session() as s:
        row = s.run("MATCH (d:Dieu {so_hieu:$sh, so_dieu:$sd}) RETURN d.mapc AS m, d.ten AS t",
                    sh=SO_HIEU, sd=SO_DIEU).single()
    target = row["m"]
    emb = r._embed(QUERY)
    r._embed = lambda t: emb  # một lần embedding cho mọi phép đo
    bm = [m for m, _ in get_bm25().search(QUERY, top_k=WIDE)]
    vec = [m for m, _ in r._vector_search_scored(QUERY, top_k=WIDE_VEC)]
    out = {"ngay": date.today().isoformat(), "truy_van": QUERY, "dieu": f"Điều {SO_DIEU} {SO_HIEU}",
           "mapc": target, "ten": row["t"], "ket_qua_moi_nguon_khi_hop_nhat": 50,
           "so_ung_vien_xep_hang_lai": POOL_K, "pham_vi_do_hang": {"bm25": WIDE, "vector": WIDE_VEC},
           "hang_bm25": rank_of(bm, target), "hang_vector_ten_dieu": rank_of(vec, target), "theo_trong_so": []}
    for w in (1.0, 0.5):
        BG.RRF_W_VECTOR = w
        fused = r._hybrid_search(QUERY, top_k=POOL_K)
        arts = fetch_articles([d["mapc"] for d in fused])
        rows = [dict(arts[d["mapc"]], base_score=d["score"]) for d in fused if d["mapc"] in arts]
        rr = [c["mapc"] for c in r._rerank(rows, QUERY, top_k=POOL_K)]
        out["theo_trong_so"].append({"w_vector": w, "hang_sau_rrf": rank_of([d["mapc"] for d in fused], target),
                                     "hang_sau_xep_hang_lai": rank_of(rr, target),
                                     "top5_sau_xep_hang_lai": [arts[m]["ten"] for m in rr[:5] if m in arts]})
    path = os.path.join(ROOT, "evaluation", "results", "thesis_rrf_example.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
