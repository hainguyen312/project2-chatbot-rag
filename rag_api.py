import asyncio
import os
import re
import tempfile
import uuid as _uuid
from typing import Any, Dict, List, Literal, Optional

import firebase_admin
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from fastapi import FastAPI, UploadFile, File
from firebase_admin import auth as fb_auth, credentials, storage as fb_storage
from openai import OpenAI
from pydantic import BaseModel, Field
import json as _json

# Nạp .env trước khi import các module khác
load_dotenv()

_FIREBASE_CRED   = os.getenv("FIREBASE_CRED_PATH", "firebase-credentials.json")
_FIREBASE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET", "")

# ── Init Firebase ─────────────────────────────────────────────────────────────
if not firebase_admin._apps:
    if _FIREBASE_CRED and os.path.exists(_FIREBASE_CRED) and _FIREBASE_BUCKET:
        cred = credentials.Certificate(_FIREBASE_CRED)
        firebase_admin.initialize_app(cred, {"storageBucket": _FIREBASE_BUCKET})
        print(f"[Firebase] Initialized — bucket: {_FIREBASE_BUCKET}")
    else:
        print("[Firebase] Bỏ qua init — thiếu credentials hoặc bucket")

from agents.pipeline import Action, run_pre_retrieve
from agents.quick_agent import is_meta_conversation
from retrieve.build_graph import GraphRAGRetriever, get_neo4j
from retrieve.two_stage_search import client, collection
from services.agentic_rag import (
    RAG_AGENT_TOOLS,
    agent_chat_model,
    agent_system_prompt,
    apply_agent_tool_result,
    assistant_message_to_dict,
    execute_agent_tool,
    run_agentic_rag_sync,
)
from services.utils import (
    client as oai_client,
    detect_intent,
    embed_batch,
    rewrite_query_v2,
)
from services.semantic_cache import semantic_cache
from services.memory_manager import MemoryManager

app = FastAPI(title="RAG Backend API", version="1.0.0")

# ── Memory Manager (long-term memory cho user) ────────────────────────────────
MEMORY_ENABLED = os.getenv("MEMORY_ENABLED", "1").strip() in ("1", "true", "True", "yes")
MEMORY_TOP_K_API = int(os.getenv("MEMORY_TOP_K", "5"))
memory_manager: Optional[MemoryManager] = None
if MEMORY_ENABLED:
    try:
        memory_manager = MemoryManager(
            openai_client=client,
            mongo_uri=os.getenv("MONGODB_URI", ""),
            milvus_host=os.getenv("MILVUS_HOST", "localhost"),
            milvus_port=os.getenv("MILVUS_PORT", "19530"),
        )
        print("[Memory] MemoryManager initialized")
    except Exception as e:
        print(f"[Memory] Khởi tạo thất bại — tắt memory: {e}")
        memory_manager = None
else:
    print("[Memory] MEMORY_ENABLED=0 — bỏ qua khởi tạo MemoryManager")

neo4j_driver = get_neo4j()
two_stage_retriever = GraphRAGRetriever(
    neo4j_driver=neo4j_driver,
    milvus_collection=collection,
    openai_client=client,
)

# Agentic RAG — OpenAI function tools (bản đầy đủ trong services/agentic_rag.py)
TOOLS = RAG_AGENT_TOOLS


# ── Pydantic models ───────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class RagChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    history: List[ChatMessage] = Field(default_factory=list)
    query_mode: Literal["normal", "situation"] = "normal"
    user_id: Optional[str] = None   # định danh người dùng để cá nhân hóa memory
    chat_id: Optional[str] = None   # để audit nguồn fact


class RagChatResponse(BaseModel):
    answer: str
    action: str
    normalized_query: Optional[str] = None
    sources: List[Dict[str, str]] = Field(default_factory=list)
    passages: List[Dict[str, Any]] = Field(default_factory=list)
    confidence_score: float = 0.0
    hallucination_warning: bool = False
    cache_hit: bool = False
    memories_used: List[Dict[str, Any]] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    chat_id: str
    msg_idx: int
    rating: Literal["up", "down"]


# ── Memory helpers ────────────────────────────────────────────────────────────
def _safe_normalized_query(prompt: str, rewritten: Optional[str]) -> str:
    """Bảo vệ rewrite_query: nếu LLM rewrite ra quá ngắn / vô nghĩa, fallback prompt gốc.

    Trường hợp gặp khi user gửi câu giới thiệu + hỏi capability (vd: "tôi là X, bạn
    làm gì được"), LLM rewrite cố ép thành topic pháp lý và trả về 1 từ rỗng nghĩa.
    """
    r = (rewritten or "").strip()
    if not r:
        return prompt
    # Bắt error message từ rewrite_query_v2 khi exception
    if r.startswith("Lỗi") or r.startswith("Error") or "Chưa cấu hình" in r:
        return prompt
    # < 2 từ hoặc < 6 ký tự được coi là quá ngắn để search hữu ích
    if len(r) < 6 or len(r.split()) < 2:
        return prompt
    return r


def _retrieve_user_memories(user_id: Optional[str], query: str) -> tuple[List[Dict[str, Any]], str]:
    """Lấy memory của user + format thành đoạn text gắn vào system prompt.

    Bọc try/except: lỗi Milvus/Mongo KHÔNG được làm chết request chính.
    """
    if not memory_manager or not user_id or not query:
        return [], ""
    try:
        mems = memory_manager.retrieve_memories(user_id, query, top_k=MEMORY_TOP_K_API)
        return mems, memory_manager.format_for_context(mems)
    except Exception as e:
        print(f"[Memory] retrieve error: {e}")
        return [], ""


def _inject_memory_into_system(system_prompt: str, memory_block: str) -> str:
    if not memory_block:
        return system_prompt
    return system_prompt + "\n\n" + memory_block


def _schedule_memory_update(
    user_id: Optional[str],
    user_msg: str,
    bot_msg: str,
    chat_id: Optional[str],
) -> None:
    """Chạy extraction + update memory ở background, không chặn response.

    Dùng asyncio.create_task khi có event loop, fallback threading nếu không.
    """
    if not memory_manager or not user_id or not user_msg or not bot_msg:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(memory_manager.process_turn_async(
                user_id, user_msg, bot_msg, chat_id=chat_id
            ))
            return
    except RuntimeError:
        pass
    # Fallback: chạy trong thread riêng
    import threading
    def _run():
        try:
            asyncio.run(memory_manager.process_turn_async(
                user_id, user_msg, bot_msg, chat_id=chat_id
            ))
        except Exception as e:
            print(f"[Memory] background update error: {e}")
    threading.Thread(target=_run, daemon=True).start()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _web_sources_markdown(web_sources: List[Dict[str, str]]) -> str:
    if not web_sources:
        return ""
    lines = ["\n\n---\n**Nguồn tham khảo từ web:**"]
    for src in web_sources:
        icon = "✅" if src.get("level") == "high" else "⚠️"
        lines.append(
            f"{icon} {src.get('label', '[Web]')} [{src.get('title', '')}]({src.get('url', '')})"
        )
    return "\n".join(lines)


def _build_passages(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    passages = []
    for hit in results:
        passages.append({
            "mapc":      hit.get("mapc", ""),
            "ten":       hit.get("ten", ""),
            "tenchuong": hit.get("tenchuong", ""),
            "tendemuc":  hit.get("tendemuc", ""),
            "tenchude":  hit.get("tenchude", ""),
            "noidung":   str(hit.get("noidung") or hit.get("passage", "")),
            "score":     round(float(hit.get("score", 0)), 4),
            "source":    hit.get("source", "phapdien"),
            "url":       hit.get("url", ""),
            "trust_level": hit.get("trust_level", "medium"),
            "source_label": hit.get("source_label", "[Web]"),
        })
    return passages


def _web_sources_from_passages(passages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    web_sources: List[Dict[str, str]] = []
    for p in passages:
        if p.get("source") in ("web", "web_realtime") and p.get("url"):
            web_sources.append({
                "title": str(p.get("ten", "")),
                "url":   str(p.get("url", "")),
                "level": str(p.get("trust_level", "medium")),
                "label": str(p.get("source_label", "[Web]")),
            })
    return web_sources


def _dedup_results(raw_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen_keys: set = set()
    web_r, pd_r = [], []
    for hit in raw_results:
        key = hit.get("mapc") or hit.get("url") or hit.get("passage", "")[:100]
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if hit.get("source") in ("web", "web_realtime"):
            web_r.append(hit)
        else:
            pd_r.append(hit)
    return (web_r + pd_r)[:20]


def _compute_confidence(passages: List[Dict[str, Any]], iterations: int) -> tuple:
    """
    Tính confidence score (0.0–1.0) và hallucination_warning dựa trên:
    - Điểm trung bình của top-5 passages
    - Phạt nếu toàn nguồn web, ít passages, hoặc agent hết vòng lặp
    """
    if not passages:
        return 0.0, True

    phapdien = [p for p in passages if p.get("source", "") not in ("web", "web_realtime")]
    top_scores = [p.get("score", 0.5) for p in passages[:5]]
    confidence = sum(top_scores) / len(top_scores) if top_scores else 0.0

    if not phapdien:
        confidence *= 0.70   # phạt nếu toàn nguồn web
    if len(passages) < 2:
        confidence *= 0.80   # phạt nếu ít nguồn
    if iterations >= 6:
        confidence *= 0.90   # phạt nếu agent đã dùng hết vòng lặp

    confidence = round(min(1.0, max(0.0, confidence)), 3)
    hallucination = confidence < 0.40 or not phapdien
    return confidence, hallucination


def _chunk_stream_text(text: str, max_chars: int = 32):
    """
    Tách văn bản thành nhiều SSE nhỏ (theo từ / dòng) để client hiển thị mượt,
    thay vì gửi một lần ~400 ký tự.
    """
    rest = text or ""
    if not rest:
        return
    while rest:
        if rest[0] == "\n":
            n = 1
            while n < len(rest) and rest[n] == "\n":
                n += 1
            yield rest[:n]
            rest = rest[n:]
            continue
        m = re.match(r"(\S+)(\s*)", rest)
        if m:
            piece = m.group(0)
            if len(piece) > max_chars:
                yield piece[:max_chars]
                rest = rest[max_chars:]
            else:
                yield piece
                rest = rest[len(piece) :]
        else:
            yield rest[0]
            rest = rest[1:]


def _strip_markdown(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*',     r'\1', text)
    text = re.sub(r'#{1,6}\s',       '',   text)
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
    text = re.sub(r'`{1,3}[^`]*`{1,3}', '', text)
    text = re.sub(r'---+', '', text)
    text = re.sub(r'\n+', '. ', text).strip()
    return text[:4096]


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.post("/rag/feedback")
def post_feedback(req: FeedbackRequest) -> Dict[str, bool]:
    """Lưu đánh giá thumbs up/down của người dùng cho một câu trả lời."""
    from services.history import update_feedback
    update_feedback(req.chat_id, req.msg_idx, req.rating)
    return {"ok": True}


@app.post("/rag/chat", response_model=RagChatResponse)
def rag_chat(req: RagChatRequest, authorization: Optional[str] = Header(default=None)) -> RagChatResponse:
    req.user_id = resolve_user_id(req.user_id, authorization)
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
    normalized_query = _safe_normalized_query(prompt, rewrite_query_v2(prompt, latest_history))

    intent_result = detect_intent(normalized_query) or "Có"
    # Câu chào/giới thiệu/hỏi capability không nên bị reject (đáng lẽ pre_retrieve
    # bắt rồi, nhưng giữ guard này để chống lọt lưới khi prompt mix meta + intro)
    if "không" in intent_result.lower() and not is_meta_conversation(prompt):
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
    user_for_agent = prompt if is_situation else normalized_query

    # ── Lấy long-term memory để cá nhân hóa câu trả lời ───────────────────
    # Dùng prompt gốc (giữ "tôi/mình") cho memory retrieve — không dùng normalized_query
    # vì normalized đã strip ngôi xưng, làm giảm độ chính xác khi tìm fact về user.
    memories_used, memory_block = _retrieve_user_memories(req.user_id, prompt)

    # ── Semantic cache lookup (bỏ qua câu chào hỏi / meta) ─────────────────
    q_embedding: list = []
    if not is_meta_conversation(prompt):
        try:
            q_embedding = embed_batch([normalized_query])[0]
            cached = semantic_cache.get(q_embedding)
            if cached:
                meta = cached.get("meta", {})
                cached_passages = meta.get("passages", [])
                cached_sources  = meta.get("sources", [])
                confidence, hallucination = _compute_confidence(cached_passages, meta.get("iterations", 1))
                # Vẫn schedule memory update cho cache hit (vì user vẫn vừa hội thoại 1 lượt)
                _schedule_memory_update(req.user_id, prompt, cached.get("answer", ""), req.chat_id)
                return RagChatResponse(
                    action="proceed",
                    normalized_query=normalized_query,
                    answer=cached.get("answer", ""),
                    sources=cached_sources,
                    passages=cached_passages,
                    confidence_score=confidence,
                    hallucination_warning=hallucination,
                    cache_hit=True,
                )
        except Exception as e:
            print(f"[Cache lookup error] {e}")

    try:
        agent_out = run_agentic_rag_sync(
            openai_client=oai_client,
            retriever=two_stage_retriever,
            neo4j_driver=neo4j_driver,
            user_prompt=user_for_agent,
            history=latest_history,
            situation_mode=is_situation,
            rerank_query=normalized_query,
            system_prompt_extra=memory_block,
        )
    except Exception as e:
        return RagChatResponse(
            action="proceed",
            normalized_query=normalized_query,
            answer=f"Mình gặp lỗi khi chạy tác tử tìm kiếm: {e}",
            passages=[],
        )

    hits = _dedup_results(agent_out.get("passage_hits") or [])
    passages = _build_passages(hits)
    web_sources = _web_sources_from_passages(passages)
    raw_response = (agent_out.get("answer") or "").strip() or (
        "Mình chưa thể tạo câu trả lời lúc này do lỗi dịch vụ AI. "
        "Bạn vui lòng thử lại sau ít phút."
    )
    raw_response += _web_sources_markdown(web_sources)
    confidence, hallucination = _compute_confidence(passages, agent_out.get("iterations", 1))

    # ── Lưu vào semantic cache (không cache câu chào hỏi / meta) ──────────
    if q_embedding and not is_meta_conversation(prompt):
        try:
            semantic_cache.set(q_embedding, {
                "meta":   {"passages": passages, "sources": web_sources, "iterations": agent_out.get("iterations", 1)},
                "answer": raw_response,
            })
        except Exception as e:
            print(f"[Cache set error] {e}")

    # ── Schedule extraction + update memory ở background ───────────────────
    _schedule_memory_update(req.user_id, prompt, raw_response, req.chat_id)

    return RagChatResponse(
        action="proceed",
        normalized_query=normalized_query,
        answer=raw_response,
        sources=web_sources,
        passages=passages,
        confidence_score=confidence,
        hallucination_warning=hallucination,
        cache_hit=False,
        memories_used=memories_used,
    )


@app.post("/rag/stream")
def rag_stream(req: RagChatRequest, authorization: Optional[str] = Header(default=None)):
    req.user_id = resolve_user_id(req.user_id, authorization)
    """
    Server-Sent Events stream.
      data: {"type":"status", "text":"...", "iteration"?: n, "max"?: m}
      data: {"type":"meta",  "action":"...", "normalized_query":"...", "passages":[...], "iterations"?: n}
      data: {"type":"token", "text":"..."}
      data: {"type":"done"}
      data: {"type":"error", "message":"..."}
    """
    def event_stream():
        prompt = req.prompt.strip()
        history_for_processing = [m.model_dump() for m in req.history]
        decision = run_pre_retrieve(prompt, history_for_processing)

        # ── QUICK / SPAM / ESCALATE ───────────────────────────────────────────
        if decision.action in (Action.QUICK_ANSWER, Action.SPAM, Action.ESCALATE):
            if decision.action == Action.QUICK_ANSWER:
                answer = decision.answer_text or ""
            elif decision.action == Action.SPAM:
                answer = "Xin lỗi, yêu cầu có dấu hiệu spam. Vui lòng cung cấp thông tin cụ thể hơn."
            else:
                answer = "Mình rất tiếc. Bạn có muốn mình chuyển tiếp cho cán bộ xử lý không?"
            yield f"data: {_json.dumps({'type':'meta','action':decision.action.value,'passages':[]}, ensure_ascii=False)}\n\n"
            for part in _chunk_stream_text(answer):
                yield f"data: {_json.dumps({'type':'token','text':part}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"
            # QUICK_ANSWER thường là chỗ user tự giới thiệu ("Tôi là Hải, …") →
            # vẫn extract memory (LLM sẽ tự bỏ qua các turn không có info)
            if decision.action == Action.QUICK_ANSWER:
                _schedule_memory_update(req.user_id, prompt, answer, req.chat_id)
            return

        # ── PROCEED ───────────────────────────────────────────────────────────
        base = (
            history_for_processing[:-1]
            if history_for_processing and history_for_processing[-1].get("role") == "user"
            else history_for_processing
        )
        latest_history   = base[-10:]
        normalized_query = _safe_normalized_query(prompt, rewrite_query_v2(prompt, latest_history))

        intent_result = detect_intent(normalized_query) or "Có"
        if "không" in intent_result.lower() and not is_meta_conversation(prompt):
            answer = (
                "Yêu cầu chưa rõ ràng hoặc ngoài phạm vi. "
                "Mình hỗ trợ tìm kiếm và giải thích pháp luật Việt Nam."
            )
            yield f"data: {_json.dumps({'type':'meta','action':'proceed','normalized_query':normalized_query,'passages':[]}, ensure_ascii=False)}\n\n"
            for part in _chunk_stream_text(answer):
                yield f"data: {_json.dumps({'type':'token','text':part}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"
            return

        is_situation = req.query_mode == "situation"
        user_for_agent = prompt if is_situation else normalized_query

        # ── Lấy long-term memory để cá nhân hóa ─────────────────────────────
        # Dùng prompt gốc (giữ "tôi/mình") cho memory retrieve — không dùng normalized_query
        # vì normalized đã strip ngôi xưng, làm giảm độ chính xác khi tìm fact về user.
        memories_used, memory_block = _retrieve_user_memories(req.user_id, prompt)

        # ── Semantic cache lookup (bỏ qua câu chào hỏi / meta) ───────────────
        q_embedding: list = []
        if not is_meta_conversation(prompt):
            try:
                q_embedding = embed_batch([normalized_query])[0]
                cached = semantic_cache.get(q_embedding)
                if cached:
                    meta = cached.get("meta", {})
                    cached_passages = meta.get("passages", [])
                    cached_sources  = meta.get("sources", [])
                    cc, hw = _compute_confidence(cached_passages, meta.get("iterations", 1))
                    yield f"data: {_json.dumps({'type':'meta','action':'proceed','normalized_query':normalized_query,'passages':cached_passages,'sources':cached_sources,'iterations':meta.get('iterations',1),'agentic':True,'confidence_score':cc,'hallucination_warning':hw,'cache_hit':True,'memories_used':memories_used}, ensure_ascii=False)}\n\n"
                    for part in _chunk_stream_text(cached.get("answer", "")):
                        yield f"data: {_json.dumps({'type':'token','text':part}, ensure_ascii=False)}\n\n"
                    yield f"data: {_json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"
                    _schedule_memory_update(req.user_id, prompt, cached.get("answer", ""), req.chat_id)
                    return
            except Exception as e:
                print(f"[Stream cache lookup error] {e}")

        system_prompt = _inject_memory_into_system(
            agent_system_prompt(situation_mode=is_situation),
            memory_block,
        )
        messages: List[Any] = [
            {"role": "system", "content": system_prompt},
            *[{"role": m["role"], "content": m["content"]} for m in latest_history[-8:]],
            {"role": "user", "content": user_for_agent},
        ]
        bucket: List[Dict[str, Any]] = []
        max_it = 6
        model = agent_chat_model()
        stream_full_answer = ""

        try:
            for iteration in range(max_it):
                response = oai_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=RAG_AGENT_TOOLS,
                    tool_choice="auto",
                    temperature=0.1,
                )
                msg = response.choices[0].message
                messages.append(assistant_message_to_dict(msg))

                if not msg.tool_calls:
                    ded = _dedup_results(bucket)
                    passages = _build_passages(ded)
                    web_sources = _web_sources_from_passages(passages)
                    cc, hw = _compute_confidence(passages, iteration + 1)
                    yield f"data: {_json.dumps({'type':'meta','action':'proceed','normalized_query':normalized_query,'passages':passages,'sources':web_sources,'iterations':iteration + 1,'agentic':True,'confidence_score':cc,'hallucination_warning':hw,'cache_hit':False,'memories_used':memories_used}, ensure_ascii=False)}\n\n"

                    content = (msg.content or "").strip()
                    if content:
                        stream_full_answer = content
                        for part in _chunk_stream_text(content):
                            yield f"data: {_json.dumps({'type':'token','text':part}, ensure_ascii=False)}\n\n"
                    else:
                        stream = oai_client.chat.completions.create(
                            model=model,
                            messages=messages,
                            temperature=0.2,
                            max_tokens=1200,
                            stream=True,
                        )
                        for chunk in stream:
                            delta = chunk.choices[0].delta.content
                            if delta:
                                stream_full_answer += delta
                                yield f"data: {_json.dumps({'type':'token','text':delta}, ensure_ascii=False)}\n\n"

                    if web_sources:
                        footer = "\n\n---\n**Nguồn tham khảo từ web:**\n" + "\n".join(
                            f"{'✅' if s['level'] == 'high' else '⚠️'} {s['label']} [{s['title']}]({s['url']})"
                            for s in web_sources
                        )
                        stream_full_answer += footer
                        yield f"data: {_json.dumps({'type':'token','text':footer}, ensure_ascii=False)}\n\n"

                    # Lưu vào cache
                    if q_embedding and not is_meta_conversation(prompt):
                        try:
                            semantic_cache.set(q_embedding, {
                                "meta":   {"passages": passages, "sources": web_sources, "iterations": iteration + 1},
                                "answer": stream_full_answer,
                            })
                        except Exception:
                            pass

                    yield f"data: {_json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"
                    _schedule_memory_update(req.user_id, prompt, stream_full_answer, req.chat_id)
                    return

                for tc in msg.tool_calls:
                    tname = tc.function.name
                    yield f"data: {_json.dumps({'type':'status','text':f'🔍 Đang gọi {tname}...','iteration':iteration + 1,'max':max_it}, ensure_ascii=False)}\n\n"
                    try:
                        args = _json.loads(tc.function.arguments or "{}")
                    except Exception:
                        args = {}
                    tool_content, full_hits = execute_agent_tool(
                        tname,
                        args,
                        retriever=two_stage_retriever,
                        neo4j_driver=neo4j_driver,
                        rerank_query=normalized_query,
                    )
                    apply_agent_tool_result(tname, tool_content, full_hits, bucket)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_content,
                    })

            ded = _dedup_results(bucket)
            passages = _build_passages(ded)
            web_sources = _web_sources_from_passages(passages)
            cc, hw = _compute_confidence(passages, max_it)
            yield f"data: {_json.dumps({'type':'meta','action':'proceed','normalized_query':normalized_query,'passages':passages,'sources':web_sources,'iterations':max_it,'agentic':True,'confidence_score':cc,'hallucination_warning':hw,'cache_hit':False,'memories_used':memories_used}, ensure_ascii=False)}\n\n"

            stream = oai_client.chat.completions.create(
                model=model,
                messages=messages
                + [{
                    "role": "user",
                    "content": "Hãy tổng hợp câu trả lời dựa trên thông tin đã thu thập (tiếng Việt).",
                }],
                temperature=0.2,
                max_tokens=1200,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    stream_full_answer += delta
                    yield f"data: {_json.dumps({'type':'token','text':delta}, ensure_ascii=False)}\n\n"

            if web_sources:
                footer = "\n\n---\n**Nguồn tham khảo từ web:**\n" + "\n".join(
                    f"{'✅' if s['level'] == 'high' else '⚠️'} {s['label']} [{s['title']}]({s['url']})"
                    for s in web_sources
                )
                stream_full_answer += footer
                yield f"data: {_json.dumps({'type':'token','text':footer}, ensure_ascii=False)}\n\n"

            # Lưu vào cache
            if q_embedding and not is_meta_conversation(prompt):
                try:
                    semantic_cache.set(q_embedding, {
                        "meta":   {"passages": passages, "sources": web_sources, "iterations": max_it},
                        "answer": stream_full_answer,
                    })
                except Exception:
                    pass

        except Exception as e:
            yield f"data: {_json.dumps({'type':'error','message':str(e)}, ensure_ascii=False)}\n\n"

        yield f"data: {_json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"
        _schedule_memory_update(req.user_id, prompt, stream_full_answer, req.chat_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Auth (Firebase ID token) ─────────────────────────────────────────────────
class AuthVerifyRequest(BaseModel):
    id_token: str


class ClaimAnonymousRequest(BaseModel):
    id_token: str               # Firebase ID token để xác thực
    anonymous_id: str           # demo_xxx đang gắn dữ liệu cũ


def _verify_firebase_token(id_token: str) -> Dict[str, Any]:
    """Verify Firebase ID token, raise 401 nếu sai."""
    if not firebase_admin._apps:
        raise HTTPException(status_code=503, detail="Firebase Admin chưa khởi tạo")
    try:
        decoded = fb_auth.verify_id_token(id_token)
        return decoded
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token không hợp lệ: {e}")


def resolve_user_id(
    body_user_id: Optional[str],
    authorization: Optional[str],
) -> Optional[str]:
    """Ưu tiên uid từ Firebase ID token (Authorization: Bearer …), fallback body."""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(None, 1)[1].strip()
        try:
            decoded = fb_auth.verify_id_token(token)
            return decoded.get("uid")
        except Exception as e:
            print(f"[Auth] Token verify fail, fallback body user_id: {e}")
    return body_user_id


@app.post("/auth/verify")
def auth_verify(req: AuthVerifyRequest) -> Dict[str, Any]:
    """Verify Firebase ID token, trả về uid + email + name."""
    decoded = _verify_firebase_token(req.id_token)
    return {
        "uid":   decoded.get("uid"),
        "email": decoded.get("email"),
        "name":  decoded.get("name") or decoded.get("email"),
        "picture": decoded.get("picture"),
    }


@app.post("/auth/claim_anonymous")
def auth_claim_anonymous(req: ClaimAnonymousRequest) -> Dict[str, Any]:
    """Sau khi user login lần đầu, re-tag toàn bộ chat + memory từ anonymous_id
    sang uid Firebase. Không xoá dữ liệu — chỉ update khoá user_id.
    """
    decoded = _verify_firebase_token(req.id_token)
    uid = decoded.get("uid")
    anon = (req.anonymous_id or "").strip()
    if not uid or not anon or not anon.startswith("demo_"):
        return {"ok": False, "reason": "invalid_ids"}

    moved_chats = 0
    moved_memories = 0

    # Chats: re-tag conversations.user_id từ anon → uid.
    if memory_manager and getattr(memory_manager, "_mongo_client", None) is not None:
        try:
            chats_col = memory_manager._mongo_client[memory_manager.mongo_db_name]["conversations"]
            res = chats_col.update_many(
                {"user_id": anon},
                {"$set": {"user_id": uid}},
            )
            moved_chats = res.modified_count
        except Exception as e:
            print(f"[Auth] Mongo chat re-tag fail: {e}")

    # Memories
    if memory_manager and memory_manager.mongo_col is not None:
        try:
            res = memory_manager.mongo_col.update_many(
                {"user_id": anon},
                {"$set": {"user_id": uid}},
            )
            moved_memories = res.modified_count
            # Milvus: phải tải lại records, xoá rồi insert vì VARCHAR không update in-place
            if memory_manager.collection is not None:
                try:
                    expr = f'user_id == "{anon}"'
                    rows = memory_manager.collection.query(
                        expr=expr,
                        output_fields=["id", "mem_type", "fact", "embedding", "timestamp"],
                    )
                    if rows:
                        ids = [int(r["id"]) for r in rows]
                        memory_manager.collection.delete(
                            expr="id in [" + ",".join(str(i) for i in ids) + "]"
                        )
                        # Re-insert với user_id mới
                        memory_manager.collection.insert([
                            [uid] * len(rows),
                            [r["mem_type"] for r in rows],
                            [r["fact"] for r in rows],
                            [r["embedding"] for r in rows],
                            [r["timestamp"] for r in rows],
                        ])
                        memory_manager.collection.flush()
                        # Đồng bộ lại milvus_id mới trong Mongo (best-effort: ghi đè theo
                        # thứ tự nếu cần). Vì insert auto_id, ta phải truy vấn lại.
                        new_rows = memory_manager.collection.query(
                            expr=f'user_id == "{uid}"',
                            output_fields=["id", "fact"],
                        )
                        by_fact = {r["fact"]: int(r["id"]) for r in new_rows}
                        for old in rows:
                            new_id = by_fact.get(old["fact"])
                            if new_id is not None:
                                memory_manager.mongo_col.update_one(
                                    {"user_id": uid, "milvus_id": int(old["id"])},
                                    {"$set": {"milvus_id": new_id}},
                                )
                except Exception as e:
                    print(f"[Auth] Milvus re-tag fail: {e}")
        except Exception as e:
            print(f"[Auth] Mongo memory re-tag fail: {e}")

    return {
        "ok": True,
        "uid": uid,
        "anonymous_id": anon,
        "moved_chats": moved_chats,
        "moved_memories": moved_memories,
    }


# ── Memory CRUD endpoints ────────────────────────────────────────────────────
def _enforce_user_match(path_user_id: str, authorization: Optional[str]) -> str:
    """Người dùng đã login chỉ được thao tác trên memory của chính mình.

    Anonymous user (demo_xxx) không cần token nhưng giữ nguyên path id.
    Logged-in user phải gửi token; uid phải khớp path id.
    """
    resolved = resolve_user_id(path_user_id, authorization)
    if authorization and resolved and resolved != path_user_id:
        raise HTTPException(status_code=403,
                            detail="Không được phép thao tác memory của người dùng khác")
    return resolved or path_user_id


@app.get("/memory/{user_id}")
def list_memories(user_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Liệt kê toàn bộ memory chưa xoá của user_id (sắp xếp mới → cũ)."""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory service không khả dụng")
    uid = _enforce_user_match(user_id, authorization)
    items = memory_manager.list_user_memories(uid)
    return {"user_id": uid, "count": len(items), "memories": items}


@app.delete("/memory/{user_id}/{memory_id}")
def delete_memory(user_id: str, memory_id: int,
                  authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Xoá 1 memory (cả Milvus + Mongo). memory_id = milvus_id."""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory service không khả dụng")
    uid = _enforce_user_match(user_id, authorization)
    ok = memory_manager.delete_memory(uid, memory_id)
    return {"ok": ok, "user_id": uid, "memory_id": memory_id}


@app.delete("/memory/{user_id}")
def delete_all_memories(user_id: str,
                        authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Xoá toàn bộ memory của user (quyền được quên)."""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory service không khả dụng")
    uid = _enforce_user_match(user_id, authorization)
    n = memory_manager.delete_all_for_user(uid)
    return {"ok": True, "user_id": uid, "deleted": n}


@app.post("/tts")
async def text_to_speech(req: dict):
    from services.history import save_tts_url

    text    = (req.get("text") or "").strip()
    voice   = req.get("voice", "nova")
    chat_id = req.get("chat_id", "")
    msg_idx = req.get("msg_idx")

    if not text:
        return {"error": "text is required"}

    text = _strip_markdown(text)

    # ── Tạo audio bytes ───────────────────────────────────────────────────────
    tts_client  = OpenAI()
    audio_bytes = b""
    with tts_client.audio.speech.with_streaming_response.create(
        model="tts-1",
        voice=voice,
        input=text,
        response_format="mp3",
    ) as response:
        for chunk in response.iter_bytes(chunk_size=4096):
            audio_bytes += chunk

    # ── Upload lên Firebase Storage ───────────────────────────────────────────
    firebase_url = None
    if firebase_admin._apps and _FIREBASE_BUCKET:
        try:
            file_name = f"tts/{chat_id or 'unknown'}/{_uuid.uuid4().hex}.mp3"
            bucket    = fb_storage.bucket(_FIREBASE_BUCKET)   # ← explicit bucket name
            blob      = bucket.blob(file_name)

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            blob.upload_from_filename(tmp_path, content_type="audio/mpeg")
            os.unlink(tmp_path)

            blob.make_public()
            firebase_url = blob.public_url
            print(f"[TTS] Uploaded: {firebase_url}")

            if chat_id and msg_idx is not None:
                save_tts_url(chat_id, int(msg_idx), firebase_url)

        except Exception as e:
            print(f"[TTS Firebase] Lỗi upload: {e}")
    else:
        print("[TTS] Firebase chưa init — fallback stream trực tiếp")

    # ── Trả về ────────────────────────────────────────────────────────────────
    if firebase_url:
        return {"url": firebase_url, "chat_id": chat_id, "msg_idx": msg_idx}

    # Fallback: stream audio trực tiếp
    def generate():
        yield audio_bytes

    return StreamingResponse(
        generate(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache"},
    )

@app.post("/stt")
async def speech_to_text(file: UploadFile = File(...)):
    from fastapi import UploadFile, File
    
    audio_bytes = await file.read()
    
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    
    try:
        stt_client = OpenAI()
        with open(tmp_path, "rb") as audio_file:
            transcription = stt_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="vi",
            )
        return {"text": transcription.text}
    finally:
        os.unlink(tmp_path)