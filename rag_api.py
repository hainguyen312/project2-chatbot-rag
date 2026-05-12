import asyncio
import os
import re
import tempfile
import uuid as _uuid
from typing import Any, Dict, List, Literal, Optional

import firebase_admin
import nest_asyncio
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi import FastAPI, UploadFile, File
from firebase_admin import credentials, storage as fb_storage
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


# ── Pydantic models ───────────────────────────────────────────────────────────
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
    passages: List[Dict[str, Any]] = Field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────
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
        })
    return passages


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
        situation  = analyze_complex_situation(prompt, latest_history)
        violations = situation.get("cac_vi_pham", []) if isinstance(situation, dict) else []
        all_queries: List[str] = [q for v in violations for q in v.get("queries", [])]
        if not all_queries:
            all_queries = [normalized_query]
    else:
        situation, violations, all_queries = {}, [], [normalized_query]

    raw_results = _run_retrieve_parallel(all_queries)
    results     = _dedup_results(raw_results)

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

    context_parts: List[str]       = []
    web_sources:   List[Dict[str, str]] = []

    for hit in results:
        label = (
            hit.get("source_label", "[Web]")
            if hit.get("source") in ("web", "web_realtime")
            else "[Pháp Điển]"
        )
        context_parts.append(f"{label}\n{hit.get('passage', '')}")
        if hit.get("source") in ("web", "web_realtime") and hit.get("url"):
            web_sources.append({
                "title": str(hit.get("ten", "")),
                "url":   str(hit.get("url", "")),
                "level": str(hit.get("trust_level", "medium")),
                "label": str(label),
            })

    if is_situation and violations:
        raw_response = generate_structured_response(
            context_parts, prompt, situation, decision.sentiment, latest_history
        )
    else:
        raw_response = generate_response(
            context_parts, normalized_query, decision.sentiment, latest_history
        )

    raw_response = (raw_response or "").strip() or (
        "Mình chưa thể tạo câu trả lời lúc này do lỗi dịch vụ AI. "
        "Bạn vui lòng thử lại sau ít phút."
    )
    raw_response += _web_sources_markdown(web_sources)

    return RagChatResponse(
        action="proceed",
        normalized_query=normalized_query,
        answer=raw_response,
        sources=web_sources,
        passages=_build_passages(results),
    )


@app.post("/rag/stream")
def rag_stream(req: RagChatRequest):
    """
    Server-Sent Events stream.
      data: {"type":"meta",  "action":"...", "normalized_query":"...", "passages":[...]}
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
            yield f"data: {_json.dumps({'type':'token','text':answer}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"
            return

        # ── PROCEED ───────────────────────────────────────────────────────────
        base = (
            history_for_processing[:-1]
            if history_for_processing and history_for_processing[-1].get("role") == "user"
            else history_for_processing
        )
        latest_history   = base[-10:]
        normalized_query = rewrite_query_v2(prompt, latest_history) or prompt

        intent_result = detect_intent(normalized_query) or "Có"
        if "không" in intent_result.lower():
            answer = (
                "Yêu cầu chưa rõ ràng hoặc ngoài phạm vi. "
                "Mình hỗ trợ tìm kiếm và giải thích pháp luật Việt Nam."
            )
            yield f"data: {_json.dumps({'type':'meta','action':'proceed','normalized_query':normalized_query,'passages':[]}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type':'token','text':answer}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"
            return

        is_situation = req.query_mode == "situation"
        if is_situation:
            situation   = analyze_complex_situation(prompt, latest_history)
            violations  = situation.get("cac_vi_pham", []) if isinstance(situation, dict) else []
            all_queries = [q for v in violations for q in v.get("queries", [])] or [normalized_query]
        else:
            situation, violations, all_queries = {}, [], [normalized_query]

        raw_results = _run_retrieve_parallel(all_queries)
        results     = _dedup_results(raw_results)

        if not results:
            yield f"data: {_json.dumps({'type':'meta','action':'proceed','passages':[]}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type':'token','text':'Chưa đủ thông tin để trả lời. Bạn hãy mô tả rõ hơn vấn đề pháp lý.'}, ensure_ascii=False)}\n\n"
            yield f"data: {_json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"
            return

        context_parts: List[str]            = []
        web_sources:   List[Dict[str, str]] = []

        for hit in results:
            label = (
                hit.get("source_label", "[Web]")
                if hit.get("source") in ("web", "web_realtime") else "[Pháp Điển]"
            )
            context_parts.append(f"{label}\n{hit.get('passage', '')}")
            if hit.get("source") in ("web", "web_realtime") and hit.get("url"):
                web_sources.append({
                    "title": str(hit.get("ten", "")),
                    "url":   str(hit.get("url", "")),
                    "level": str(hit.get("trust_level", "medium")),
                    "label": str(label),
                })

        passages = _build_passages(results)

        # Gửi metadata trước
        yield f"data: {_json.dumps({'type':'meta','action':'proceed','normalized_query':normalized_query,'passages':passages,'sources':web_sources}, ensure_ascii=False)}\n\n"

        # Stream tokens từ LLM
        try:
            from services.utils import (
                generate_response_stream,
                generate_structured_response_stream,
            )
            gen = (
                generate_structured_response_stream(
                    context_parts, prompt, situation, decision.sentiment, latest_history
                )
                if is_situation and violations
                else generate_response_stream(
                    context_parts, normalized_query, decision.sentiment, latest_history
                )
            )
            for token in gen:
                yield f"data: {_json.dumps({'type':'token','text':token}, ensure_ascii=False)}\n\n"

            if web_sources:
                footer = "\n\n---\n**Nguồn tham khảo từ web:**\n" + "\n".join(
                    f"{'✅' if s['level'] == 'high' else '⚠️'} {s['label']} [{s['title']}]({s['url']})"
                    for s in web_sources
                )
                yield f"data: {_json.dumps({'type':'token','text':footer}, ensure_ascii=False)}\n\n"

        except Exception as e:
            yield f"data: {_json.dumps({'type':'error','message':str(e)}, ensure_ascii=False)}\n\n"

        yield f"data: {_json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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