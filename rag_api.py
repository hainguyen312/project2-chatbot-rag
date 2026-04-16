import asyncio
from typing import Any, Dict, List, Literal, Optional

import nest_asyncio
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel, Field

# Cần nạp .env trước khi import các module khởi tạo client (Tavily/OpenAI/ES)
load_dotenv()

from agents.pipeline import Action, run_pre_retrieve
from retrieve.build_graph import GraphRAGRetriever, get_neo4j
from retrieve.two_stage_search import client, collection
from services.utils import (
    analyze_complex_situation,
    detect_intent,
    generate_response,
    generate_structured_response,
    retrieve_parallel,
    rewrite_query_v2,
)

app = FastAPI(title="RAG Backend API", version="1.0.0")

neo4j_driver = get_neo4j()
two_stage_retriever = GraphRAGRetriever(
    neo4j_driver=neo4j_driver,
    milvus_collection=collection,
    openai_client=client,
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class RagChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    history: List[ChatMessage] = Field(default_factory=list)
    query_mode: Literal["normal", "situation"] = "normal"


class RagChatResponse(BaseModel):
    answer: str
    action: str
    normalized_query: Optional[str] = None
    sources: List[Dict[str, str]] = Field(default_factory=list)


def _run_retrieve_parallel(all_queries: List[str]) -> List[Dict[str, Any]]:
    nest_asyncio.apply()
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            retrieve_parallel(two_stage_retriever, all_queries, top_k_each=8)
        )
    finally:
        loop.close()


def _web_sources_markdown(web_sources: List[Dict[str, str]]) -> str:
    if not web_sources:
        return ""
    lines = ["\n\n---\n**Nguồn tham khảo từ web:**"]
    for src in web_sources:
        icon = "✅" if src.get("level") == "high" else "⚠️"
        lines.append(f"{icon} {src.get('label', '[Web]')} [{src.get('title', '')}]({src.get('url', '')})")
    return "\n".join(lines)


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.post("/rag/chat", response_model=RagChatResponse)
def rag_chat(req: RagChatRequest) -> RagChatResponse:
    prompt = req.prompt.strip()
    history_for_processing = [m.model_dump() for m in req.history]
    decision = run_pre_retrieve(prompt, history_for_processing)

    if decision.action == Action.QUICK_ANSWER:
        return RagChatResponse(answer=decision.answer_text or "", action="quick")

    if decision.action == Action.SPAM:
        return RagChatResponse(
            action="spam",
            answer=(
                "Xin lỗi, tôi không thể xử lý yêu cầu của bạn vì nó có dấu hiệu spam. "
                "Bạn hãy cung cấp thông tin cụ thể hơn để tôi có thể hỗ trợ tốt nhất. Cảm ơn bạn!"
            ),
        )

    if decision.action == Action.ESCALATE:
        return RagChatResponse(
            action="escalate",
            answer=(
                "Mình rất tiếc khi đem lại trải nghiệm không tốt cho bạn. "
                "Bạn có muốn mình chuyển tiếp vấn đề cho cán bộ trực tiếp xử lý không?"
            ),
        )

    # Action.PROCEED
    base = (
        history_for_processing[:-1]
        if history_for_processing and history_for_processing[-1].get("role") == "user"
        else history_for_processing
    )
    latest_history = base[-10:]
    normalized_query = rewrite_query_v2(prompt, latest_history) or prompt

    intent_result = detect_intent(normalized_query) or "Có"
    if "không" in intent_result.lower():
        return RagChatResponse(
            action="proceed",
            normalized_query=normalized_query,
            answer=(
                "Có vẻ yêu cầu của bạn chưa rõ ràng hoặc không nằm trong phạm vi mình xử lý. "
                "Mình là trợ lý pháp lý hỗ trợ tìm kiếm và giải thích luật. "
                "Bạn có thể hỏi về các quy định, điều luật cụ thể, hoặc quy trình pháp lý mà bạn đang thắc mắc."
            ),
        )

    is_situation = req.query_mode == "situation"
    if is_situation:
        situation = analyze_complex_situation(prompt, latest_history)
        violations = situation.get("cac_vi_pham", []) if isinstance(situation, dict) else []
        all_queries: List[str] = []
        for violation in violations:
            all_queries.extend(violation.get("queries", []))
        if not all_queries:
            all_queries = [normalized_query]
    else:
        situation = {}
        violations = []
        all_queries = [normalized_query]

    raw_results = _run_retrieve_parallel(all_queries)

    seen_keys = set()
    web_results: List[Dict[str, Any]] = []
    pd_results: List[Dict[str, Any]] = []
    for hit in raw_results:
        key = hit.get("mapc") or hit.get("url") or (hit.get("passage", "")[:100])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if hit.get("source") in ("web", "web_realtime"):
            web_results.append(hit)
        else:
            pd_results.append(hit)

    results = (web_results + pd_results)[:20]
    if not results:
        return RagChatResponse(
            action="proceed",
            normalized_query=normalized_query,
            answer=(
                "Mình rất tiếc vì chưa đủ thông tin để trả lời câu hỏi này. "
                "Bạn hãy cung cấp rõ tình huống và vấn đề pháp lý bạn gặp phải nhé. "
                "Nếu vấn đề nằm ngoài khả năng xử lý, mình sẽ hỗ trợ bạn chuyển tiếp cho cán bộ xử lý!"
            ),
        )

    context_parts = []
    web_sources: List[Dict[str, str]] = []
    for hit in results:
        label = (
            hit.get("source_label", "[Web]")
            if hit.get("source") in ("web", "web_realtime")
            else "[Pháp Điển]"
        )
        context_parts.append(f"{label}\n{hit.get('passage', '')}")
        if hit.get("source") in ("web", "web_realtime") and hit.get("url"):
            web_sources.append(
                {
                    "title": str(hit.get("ten", "")),
                    "url": str(hit.get("url", "")),
                    "level": str(hit.get("trust_level", "medium")),
                    "label": str(label),
                }
            )

    if is_situation and violations:
        raw_response = generate_structured_response(
            context_parts, prompt, situation, decision.sentiment, latest_history
        )
    else:
        raw_response = generate_response(
            context_parts, normalized_query, decision.sentiment, latest_history
        )

    raw_response = (raw_response or "").strip()
    if not raw_response:
        raw_response = (
            "Mình chưa thể tạo câu trả lời lúc này do lỗi dịch vụ AI. "
            "Bạn vui lòng thử lại sau ít phút."
        )
    raw_response += _web_sources_markdown(web_sources)

    return RagChatResponse(
        action="proceed",
        normalized_query=normalized_query,
        answer=raw_response,
        sources=web_sources,
    )

