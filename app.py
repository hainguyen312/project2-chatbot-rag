import os
import streamlit as st
import streamlit.components.v1 as components
from urllib.parse import urlencode
import time
from retrieve.elastic_search import retrieve_top_20_results
from services.utils import rewrite_query_with_history, rewrite_query, rewrite_query_v2, generate_response, generate_chat_title, detect_intent, analyze_complex_situation, retrieve_parallel, generate_structured_response
from services.history import save_chat, load_chats, rename_chat, delete_chat, create_new_chat, cleanup_empty_chats
from agents.pipeline import run_pre_retrieve, Action

from retrieve.two_stage_search import collection, engine, client
from retrieve.build_graph import GraphRAGRetriever, get_neo4j
import asyncio
import nest_asyncio
import markdown as md 

neo4j_driver = get_neo4j()
two_stage_retriever = GraphRAGRetriever(
    neo4j_driver=neo4j_driver,
    milvus_collection=collection,
    openai_client=client,
)

# =====================================================================
# PAGE CONFIG — phải gọi trước mọi st.* khác
# =====================================================================
st.set_page_config(
    page_title="Hỏi đáp Pháp luật",
    layout="wide",
    page_icon="⚖️",
)

# =====================================================================
# SESSION STATE
# =====================================================================
if "query" not in st.session_state:
    st.session_state.query = ""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "chat_id" not in st.session_state:
    st.session_state.chat_id = None
if "chats" not in st.session_state:
    st.session_state.chats = load_chats()
if "query_mode" not in st.session_state:
    st.session_state.query_mode = "normal"
st.session_state.setdefault("pending_action", None)
st.session_state.setdefault("new_name", "")
st.session_state.setdefault("last_action", None)

# =====================================================================
# GLOBAL CSS — chat bubbles + layout + sidebar + responsive
# =====================================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@300;400;500;600;700&display=swap');

/* ── Root & base ─────────────────────────────────────────── */
html, body, [class*="css"] {
    font-family: 'Be Vietnam Pro', sans-serif !important;
}

.block-container {
    padding-top: 40px !important;
    padding-bottom: 0rem !important;
    max-width: 100% !important;
}

/* ── Topbar title ─────────────────────────────────────────── */
.topbar-title {
    font-weight: 700;
    font-size: 26px;
    line-height: 32px;
    color: #1a2744;
    letter-spacing: -0.3px;
}

/* ── Divider ──────────────────────────────────────────────── */
hr {
    margin: 8px 0 12px 0 !important;
    border-color: #e2e8f0 !important;
}

/* ══════════════════════════════════════════════════════════
   SIDEBAR
══════════════════════════════════════════════════════════ */
[data-testid="stSidebar"] {
    background: #f7f8fc !important;
    border-right: 1px solid #e2e8f0;
}

[data-testid="stSidebar"] .stButton > button {
    background-color: transparent;
    color: #374151;
    border: none !important;
    border-radius: 10px;
    font-size: 14px;
    font-family: 'Be Vietnam Pro', sans-serif !important;
    width: 100%;
    justify-content: flex-start !important;
    align-items: center !important;
    text-align: left !important;
    padding: 8px 14px !important;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    transition: background 0.15s ease;
}

[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #edf0f7 !important;
    color: #1a2744 !important;
}

[data-testid="stSidebar"] button[data-testid="baseButton-primary"] {
    background-color: #1a2744 !important;
    color: #ffffff !important;
    font-weight: 600;
}

/* ══════════════════════════════════════════════════════════
   MODE TOGGLE BUTTONS
══════════════════════════════════════════════════════════ */
div[data-testid="column"] .stButton > button {
    border-radius: 20px !important;
    font-size: 13.5px !important;
    font-weight: 500 !important;
    padding: 6px 18px !important;
    border: 1.5px solid #d1d5db !important;
    background: #fff !important;
    color: #374151 !important;
    transition: all 0.18s ease;
}

div[data-testid="column"] button[data-testid="baseButton-primary"] {
    background: #1a2744 !important;
    color: #fff !important;
    border-color: #1a2744 !important;
    font-weight: 600 !important;
}

.chat-bubble p {
    margin: 0 0 12px 0 !important;
    padding: 0 !important;
}

.chat-bubble p:last-child {
    margin-bottom: 0 !important;
}

.chat-bubble ul, .chat-bubble ol {
    margin: 4px 0 4px 20px !important;
    padding: 0 !important;
}

.chat-bubble li {
    margin-bottom: 2px !important;
}

.chat-bubble strong {
    font-weight: 600 !important;
}

/* ══════════════════════════════════════════════════════════
   RESPONSIVE
══════════════════════════════════════════════════════════ */
@media (max-width: 1024px) {
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) .stMarkdown,
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) .stMarkdown {
        max-width: 75% !important;
    }
}

@media (max-width: 768px) {
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) .stMarkdown,
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) .stMarkdown {
        max-width: 85% !important;
        font-size: 14px !important;
    }
}

@media (max-width: 480px) {
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) .stMarkdown,
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) .stMarkdown {
        max-width: 92% !important;
        font-size: 13.5px !important;
        padding: 8px 12px !important;
    }
}

/* Topbar title nhỏ lại trên mobile */
@media (max-width: 768px) {
    .topbar-title {
        font-size: 18px !important;
        line-height: 24px !important;
    }
}

@media (max-width: 480px) {
    .topbar-title {
        font-size: 15px !important;
        line-height: 20px !important;
        letter-spacing: 0 !important;
    }
}

/* Nút Đổi tên / Xoá nhỏ lại */
@media (max-width: 768px) {
    [data-testid="stSidebar"] ~ div [data-testid="column"]:last-child .stButton > button {
        font-size: 11px !important;
        padding: 4px 8px !important;
    }
}

/* Mode toggle — thu nhỏ chữ và padding */
@media (max-width: 768px) {
    div[data-testid="column"] .stButton > button {
        font-size: 12px !important;
        padding: 5px 10px !important;
    }
}

@media (max-width: 480px) {
    div[data-testid="column"] .stButton > button {
        font-size: 11px !important;
        padding: 4px 8px !important;
        border-radius: 14px !important;
    }
}

/* Divider bớt margin trên mobile */
@media (max-width: 480px) {
    hr {
        margin: 4px 0 8px 0 !important;
    }

    .block-container {
        padding-top: 16px !important;
    }


/* ══════════════════════════════════════════════════════════
   CHAT INPUT
══════════════════════════════════════════════════════════ */
[data-testid="stChatInput"] > div {
    border-radius: 24px !important;
    border: 1.5px solid #d1d5db !important;
    background: #fff !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06) !important;
    transition: border-color 0.2s ease;
}

[data-testid="stChatInput"] > div:focus-within {
    border-color: #1a2744 !important;
    box-shadow: 0 2px 12px rgba(26,39,68,0.12) !important;
}

/* ══════════════════════════════════════════════════════════
   TOPBAR ACTIONS (Đổi tên / Xoá)
══════════════════════════════════════════════════════════ */
.topbar-actions .stButton > button {
    border-radius: 8px !important;
    font-size: 13px !important;
    padding: 5px 12px !important;
    border: 1px solid #d1d5db !important;
    background: #fff !important;
    color: #374151 !important;
}

.topbar-actions .stButton > button:hover {
    background: #f3f4f6 !important;
}
</style>
""", unsafe_allow_html=True)


# =====================================================================
# UTILITY
# =====================================================================
def clear_input():
    st.session_state.query = ""

def add_to_history(role, content):
    st.session_state.chat_history.append({"role": role, "content": content})

def to_md(text: str) -> str:
    return text.replace("\n", "  \n")


# =====================================================================
# MODE TOGGLE
# =====================================================================
col_m1, col_m2, col_spacer = st.columns([1, 1.4, 5])
with col_m1:
    if st.button(
        "Hỏi đáp thường",
        type="primary" if st.session_state.query_mode == "normal" else "secondary",
        use_container_width=True,
    ):
        st.session_state.query_mode = "normal"
        st.rerun()
with col_m2:
    if st.button(
        "Phân tích tình huống",
        type="primary" if st.session_state.query_mode == "situation" else "secondary",
        use_container_width=True,
    ):
        st.session_state.query_mode = "situation"
        st.rerun()

placeholder_text = (
    "Mô tả tình huống: ai làm gì · với ai · hoàn cảnh · hậu quả..."
    if st.session_state.query_mode == "situation"
    else "Nhập câu hỏi pháp luật của bạn..."
)
prompt = st.chat_input(placeholder_text)


# =====================================================================
# SIDEBAR
# =====================================================================
with st.sidebar:
    if st.button("➕  Tạo hội thoại mới", use_container_width=True, type="primary"):
        cleanup_empty_chats()
        st.session_state.chat_id = None
        st.session_state.chat_history = []
        st.session_state.query = ""
        st.rerun()

    st.markdown("### 💬 Hội thoại")

    sorted_chats = sorted(
        st.session_state.chats.items(),
        key=lambda x: x[1].get("updated_at", ""),
        reverse=True,
    )
    for cid, chat in sorted_chats:
        title = chat.get("title", "(Không tên)")
        is_active = cid == st.session_state.chat_id
        btn_type = "primary" if is_active else "secondary"

        if st.button(title, key=f"load_{cid}", use_container_width=True, type=btn_type):
            cleanup_empty_chats()
            st.session_state.chat_id = cid
            st.session_state.chat_history = chat["messages"]
            st.rerun()


# =====================================================================
# TOPBAR
# =====================================================================
col_left, col_right = st.columns([1, 0.22])
with col_left:
    title_text = (
        st.session_state.chats.get(st.session_state.chat_id, {}).get("title", "")
        or "⚖️ Hệ thống hỏi đáp pháp luật Việt Nam"
    )
    st.markdown(f'<div class="topbar-title">{title_text}</div>', unsafe_allow_html=True)

if st.session_state.chat_id:
    with col_right:
        with st.container():
            c1, c2 = st.columns([2, 1.4])
            with c1:
                if st.button("✏️ Đổi tên", key="rename_btn", use_container_width=True):
                    st.session_state.pending_action = "rename"
            with c2:
                if st.button("🗑️ Xoá", key="delete_btn", use_container_width=True):
                    st.session_state.pending_action = "delete"

st.markdown("---")


# =====================================================================
# DIALOG XÁC NHẬN
# =====================================================================
@st.dialog("Xác nhận thao tác")
def confirm_dialog():
    action = st.session_state.pending_action

    if action == "rename":
        chat_id = st.session_state.chat_id
        current_name = st.session_state.chats.get(chat_id, {}).get("title", "")
        if "prefilled_chat" not in st.session_state or st.session_state.prefilled_chat != chat_id:
            st.session_state.rename_value = current_name
            st.session_state.prefilled_chat = chat_id

        st.write("Nhập **tên mới** cho hội thoại:")
        st.session_state.new_name = st.text_input(
            "Tên mới",
            key="rename_value",
            value=st.session_state.get("new_name", current_name),
            label_visibility="collapsed",
        )
        c1, c2 = st.columns([1, 0.35])
        with c1:
            if st.button("Huỷ"):
                st.session_state.pending_action = None
                st.rerun()
        with c2:
            if st.button("Xác nhận", type="primary"):
                rename_chat(chat_id, st.session_state.new_name)
                st.session_state.chats = load_chats()
                st.session_state.pending_action = None
                st.session_state.last_action = "rename"
                st.rerun()

    elif action == "delete":
        st.warning("Bạn chắc chắn muốn **xoá hội thoại** này?\n\nHành động này **không thể hoàn tác**.")
        c1, c2 = st.columns([1, 0.5])
        with c1:
            if st.button("Huỷ"):
                st.session_state.pending_action = None
                st.rerun()
        with c2:
            if st.button("🗑️ Xoá", type="primary"):
                delete_chat(st.session_state.chat_id)
                st.session_state.chat_id = None
                st.session_state.chat_history = []
                st.session_state.chats = load_chats()
                st.session_state.query = ""
                st.session_state.pending_action = None
                st.session_state.last_action = "delete"
                st.rerun()


if st.session_state.pending_action:
    confirm_dialog()

if st.session_state.last_action:
    action = st.session_state.last_action
    if action == "rename":
        st.toast("Đổi tên hội thoại thành công ✅")
    if action == "delete":
        st.toast("Đã xoá hội thoại 🗑️")
    st.session_state.last_action = None


# =====================================================================
# LỊCH SỬ HỘI THOẠI
# =====================================================================

for msg in st.session_state.chat_history:
    role = msg["role"]
    content_html = md.markdown(msg["content"], extensions=["extra", "nl2br"])
    
    if role == "user":
        st.markdown(f"""
        <div style="display:flex; justify-content:flex-end; align-items:flex-end; gap:8px; margin:6px 0;">
            <div class="chat-bubble" style="
                background:#1a2744;
                color:#fff;
                border-radius:18px 4px 18px 18px;
                padding:10px 16px;
                max-width:75%;
                font-size:14.5px;
                line-height:1.6;
                box-shadow:0 2px 8px rgba(26,39,68,0.15);
                font-family:'Be Vietnam Pro',sans-serif;
            ">
            {content_html}</div>
            <div style="
                width:36px; height:36px; border-radius:50%;
                background:#1a2744; color:#fff;
                display:flex; align-items:center; justify-content:center;
                font-size:16px; flex-shrink:0;
            ">👤</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div style="display:flex; justify-content:flex-start; align-items:flex-end; gap:8px; margin:6px 0;">
            <div style="
                width:36px; height:36px; border-radius:50%;
                background:#e8edf5; color:#1a2744;
                display:flex; align-items:center; justify-content:center;
                font-size:16px; flex-shrink:0;
            ">⚖️</div>
            <div class="chat-bubble" style="
                background:#ffffff;
                color:#1a2744;
                border-radius:4px 18px 18px 18px;
                padding:10px 16px;
                max-width:75%;
                font-size:14.5px;
                line-height:1.7;
                box-shadow:0 1px 4px rgba(0,0,0,0.08);
                border:1px solid #e8edf5;
                font-family:'Be Vietnam Pro',sans-serif;
            ">
            {content_html}</div>
        </div>
        """, unsafe_allow_html=True)

# =====================================================================
# XỬ LÝ PROMPT MỚI
# =====================================================================
if prompt:
    # Tạo chat_id nếu chưa có
    if not st.session_state.chat_id:
        chat_id = create_new_chat()
        st.session_state.chat_id = chat_id
        st.session_state.chats = load_chats()
    else:
        chat_id = st.session_state.chat_id

    st.session_state.chats = load_chats()

    add_to_history("user", prompt)
    st.chat_message("user").write(prompt)

    with st.chat_message("assistant"):
        status_ph = st.empty()
        message_ph = st.empty()

        status_ph.caption("⏳ Đang phân tích yêu cầu…")
        decision = run_pre_retrieve(prompt, st.session_state.chat_history)
        status_ph.empty()

        # ── Helper: streaming effect ──────────────────────────────
        def stream_text(text: str, chunk: int = 5, delay: float = 0.05):
            full = ""
            ph = st.empty()
            for i in range(0, len(text), chunk):
                full += text[i:i + chunk]
                ph.markdown(full + "▌")
                time.sleep(delay)
            ph.markdown(full)
            return full

        # ── QUICK_ANSWER ──────────────────────────────────────────
        if decision.action == Action.QUICK_ANSWER:
            response = stream_text(decision.answer_text)

        # ── SPAM ─────────────────────────────────────────────────
        elif decision.action == Action.SPAM:
            response = stream_text(
                "Xin lỗi, tôi không thể xử lý yêu cầu của bạn vì nó có dấu hiệu spam. "
                "Bạn hãy cung cấp thông tin cụ thể hơn để tôi có thể hỗ trợ tốt nhất. Cảm ơn bạn!"
            )

        # ── ESCALATE ─────────────────────────────────────────────
        elif decision.action == Action.ESCALATE:
            response = stream_text(
                "Mình rất tiếc khi đem lại trải nghiệm không tốt cho bạn. "
                "Bạn có muốn mình chuyển tiếp vấn đề cho cán bộ trực tiếp xử lý không?"
            )

        # ── PROCEED ──────────────────────────────────────────────
        elif decision.action == Action.PROCEED:
            status_ph.caption("🔍 Đang tìm kiếm thông tin…")

            hist = st.session_state.get("chat_history", [])
            base = hist[:-1] if hist and hist[-1].get("role") == "user" else hist
            lastest_history = base[-10:]

            query_chuanhoa = rewrite_query_v2(prompt, lastest_history)
            if query_chuanhoa:
                status_ph.caption(f"🔍 {query_chuanhoa}")

            intent_result = detect_intent(query_chuanhoa) or "Có"

            if "không" in intent_result.lower():
                response = stream_text(
                    "Có vẻ yêu cầu của bạn chưa rõ ràng hoặc không nằm trong phạm vi mình xử lý. "
                    "Mình là trợ lý pháp lý hỗ trợ tìm kiếm và giải thích luật. "
                    "Bạn có thể hỏi về các quy định, điều luật cụ thể, hoặc quy trình pháp lý mà bạn đang thắc mắc."
                )
            else:
                is_situation = st.session_state.query_mode == "situation"

                if is_situation:
                    status_ph.caption("📋 Đang phân tích tình huống…")
                    situation = analyze_complex_situation(prompt, lastest_history)
                    cac_vi_pham = situation.get("cac_vi_pham", [])
                    all_queries = []
                    for vp in cac_vi_pham:
                        all_queries.extend(vp.get("queries", []))
                    if not all_queries:
                        all_queries = [query_chuanhoa]
                    if cac_vi_pham:
                        status_ph.caption(f"⚠️ Phát hiện {len(cac_vi_pham)} vi phạm — đang tìm kiếm…")
                else:
                    situation = {}
                    cac_vi_pham = []
                    all_queries = [query_chuanhoa]

                # Search song song
                nest_asyncio.apply()
                loop = asyncio.new_event_loop()
                try:
                    raw_results = loop.run_until_complete(
                        retrieve_parallel(two_stage_retriever, all_queries, top_k_each=8)
                    )
                finally:
                    loop.close()

                # Deduplicate + ưu tiên web
                seen_keys = set()
                web_results = []
                pd_results = []

                for hit in raw_results:
                    key = (
                        hit.get("mapc")
                        or hit.get("url")
                        or hit.get("passage", "")[:100]
                    )
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    if hit.get("source") in ("web", "web_realtime"):
                        web_results.append(hit)
                    else:
                        pd_results.append(hit)

                results = (web_results + pd_results)[:20]
                print(f"[PROCEED] Tổng sau dedup: {len(results)} passages")

                if results:
                    context_parts = []
                    web_sources = []

                    for hit in results:
                        label = (
                            hit.get("source_label", "[Web]")
                            if hit.get("source") in ("web", "web_realtime")
                            else "[Pháp Điển]"
                        )
                        context_parts.append(f"{label}\n{hit['passage']}")
                        if hit.get("source") in ("web", "web_realtime") and hit.get("url"):
                            web_sources.append({
                                "title": hit.get("ten", ""),
                                "url": hit["url"],
                                "level": hit.get("trust_level", "medium"),
                                "label": label,
                            })

                    print("Văn bản liên quan:")
                    print(context_parts[:3])

                    if is_situation and cac_vi_pham:
                        raw_response = generate_structured_response(
                            context_parts, prompt, situation, decision.sentiment, lastest_history
                        )
                    else:
                        raw_response = generate_response(
                            context_parts, query_chuanhoa, decision.sentiment, lastest_history
                        )

                    if web_sources:
                        source_lines = ["\n\n---\n**Nguồn tham khảo từ web:**"]
                        for s in web_sources:
                            icon = "✅" if s["level"] == "high" else "⚠️"
                            source_lines.append(
                                f"{icon} {s['label']} [{s['title']}]({s['url']})"
                            )
                        raw_response += "\n".join(source_lines)

                else:
                    raw_response = (
                        "Mình rất tiếc vì chưa đủ thông tin để trả lời câu hỏi này. "
                        "Bạn hãy cung cấp rõ tình huống và vấn đề pháp lý bạn gặp phải nhé. "
                        "Nếu vấn đề nằm ngoài khả năng xử lý, mình sẽ hỗ trợ bạn "
                        "chuyển tiếp cho cán bộ xử lý!"
                    )

                print("Query đã chuẩn hoá:", query_chuanhoa)
                print("Sentiment:", decision.sentiment)

                status_ph.empty()
                response = stream_text(raw_response)

    add_to_history("assistant", response)

    # Tự động đặt tên nếu chưa có
    if st.session_state.chat_id:
        chat_id = st.session_state.chat_id
        if chat_id not in st.session_state.chats:
            st.session_state.chats = load_chats()
        current_title = st.session_state.chats.get(chat_id, {}).get("title", "")

        new_title = (
            generate_chat_title(st.session_state.chat_history)
            if not current_title or current_title.startswith("Cuộc trò chuyện")
            else current_title
        )
        save_chat(chat_id, new_title, st.session_state.chat_history)
        st.session_state.chats = load_chats()
        st.rerun()

    st.session_state.chats = load_chats()