"""
LLM rerank (pointwise) — đã gỡ khỏi chatbot (cờ RAG_AGENT_LLM_RERANK, mặc định tắt, chưa được đo trên hybrid_new).

Giữ nguyên văn để Bước 6 (LLM rerank trên nền hybrid_new: top 20 → gpt-4o-mini chấm 0–1 → top 10, cache theo
(query, doc_id)) tham khảo prompt chấm điểm, không phải viết lại từ đầu. Lưu ý khi tái dùng: bản cũ gọi LLM
TUẦN TỰ cho từng passage và chỉ chấm top 12 — nên chạy song song và chấm đủ top 20.

Trước đây được gọi trong services/agentic_rag.rerank_phapdien_hits:
    if RAG_AGENT_LLM_RERANK and ranked:
        ranked = _apply_llm_rerank_head(retriever, q, ranked, head_n=12)
"""
from __future__ import annotations

import os
import re
from typing import Any

RAG_AGENT_LLM_RERANK = os.getenv("RAG_AGENT_LLM_RERANK", "0").strip().lower() in ("1", "true", "yes")


def _llm_score_passage(client: Any, query: str, passage: str, *, model: str | None = None) -> float:
    """Chấm liên quan query–passage (0–1), cùng logic TwoStageRetriever._score_pair."""
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    passage = (passage or "")[:8000]
    if not passage.strip():
        return 0.0
    system_prompt = (
        "Bạn là một hệ thống chấm điểm truy hồi văn bản phục vụ cho trả lời truy vấn pháp luật. "
        "Nhiệm vụ của bạn là đánh giá mức độ đoạn văn (passage) trả lời được câu hỏi (query). "
        "Đoạn văn nào trực tiếp cần dùng để trả lời câu hỏi thì điểm cao."
        "Hãy trả về MỘT số thực trong khoảng từ 0 đến 1:\n"
        "- 0: hoàn toàn không liên quan\n"
        "- 1: rất liên quan, trả lời trực tiếp câu hỏi\n"
        "Chỉ in ra đúng MỘT số, không giải thích gì thêm."
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Query:\n{query}\n\nPassage:\n{passage}"},
            ],
            temperature=0.0,
            max_tokens=8,
        )
        text = (resp.choices[0].message.content or "").strip()
        m = re.search(r"([01](?:\.\d+)?)", text)
        if not m:
            return 0.0
        return max(0.0, min(1.0, float(m.group(1))))
    except Exception:
        return 0.0


def _apply_llm_rerank_head(
    retriever: Any,
    query: str,
    ranked: list[dict[str, Any]],
    *,
    head_n: int = 12,
) -> list[dict[str, Any]]:
    """Chấm LLM lại phần đầu danh sách đã overlap-rerank, rồi gộp đuôi."""
    client = getattr(retriever, "client", None)
    if not client or not ranked:
        return ranked
    n = min(head_n, len(ranked))
    head = [dict(x) for x in ranked[:n]]
    tail = ranked[n:]
    for c in head:
        passage = (c.get("passage") or "").strip()
        if not passage:
            passage = "\n".join(
                filter(None, [c.get("ten"), c.get("noidung")])
            )
        c["score"] = _llm_score_passage(client, query, passage)
    head.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    return head + tail
