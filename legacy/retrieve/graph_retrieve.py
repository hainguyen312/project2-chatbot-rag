"""
GraphRAGRetriever.retrieve() — đường truy hồi của giao diện Streamlit cũ (legacy/app.py, qua
services.utils.retrieve_parallel): decompose query → vector tên điều → mở rộng đồ thị → rerank → Tavily fallback
/ realtime. Chatbot hiện tại (rag_api) dùng agent + _hybrid_search, không dùng method này.
Nguyên văn method lúc gỡ (từ retrieve/build_graph.py); cần các hàm trong legacy/retrieve/tavily_fallback.py.
"""
from legacy.retrieve.tavily_fallback import (  # noqa: F401
    decompose_query,
    is_context_sufficient,
    search_tavily,
    search_tavily_realtime,
)


class _GraphRAGRetrieverLegacyMixin:
    def retrieve(self, query, top_k=20, seed_k=5, hop=2) -> list[dict]:
        # Decompose trước khi search
        decomposed     = decompose_query(query)
        legal_query    = decomposed.get("legal_query") or query
        realtime_query = decomposed.get("realtime_query")
        calc_hint      = decomposed.get("calc_hint")

        if realtime_query:
            print(f"[GraphRAG] Phát hiện query thực tế: '{realtime_query}'")

        # Stage 1–3: Graph RAG với legal_query
        seed_mapcs = self._vector_search(legal_query, top_k=seed_k)
        print(f"[GraphRAG] Seeds: {len(seed_mapcs)}")

        candidates = self._graph_expand(seed_mapcs, hop=hop)
        print(f"[GraphRAG] Candidates: {len(candidates)}")

        final = self._rerank(candidates, legal_query, top_k=top_k)

        for r in final:
            r.setdefault("source", "phapdien")
            r.setdefault("source_label", "[Pháp Điển]")

        # Stage 4a: Fallback pháp lý — TRUYỀN query VÀO để check relevance + thời sự
        if not is_context_sufficient(final, query=query):
            print(f"[GraphRAG] Context không đủ/không liên quan → Tavily legal fallback")
            web_legal = search_tavily(legal_query)
            final = final + web_legal
            print(f"[GraphRAG] Sau legal fallback: {len(final)} kết quả")

        # Stage 4b: Realtime data
        if realtime_query:
            realtime_results = search_tavily_realtime(realtime_query)
            final = realtime_results + final
            print(f"[GraphRAG] Sau realtime: {len(final)} kết quả")

        # Stage 4c: Gắn calc hint nếu có
        if calc_hint:
            final.insert(0, {
                "mapc": None, "ten": "Yêu cầu tính toán",
                "noidung": calc_hint,
                "passage": f"[Hướng dẫn tính toán]\n{calc_hint}",
                "score": 1.0, "source": "system",
                "source_label": "[Hệ thống]",
            })

        print(f"[GraphRAG] Tổng kết quả trả về: {len(final)}")
        return final
