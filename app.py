import os
import streamlit as st
import streamlit.components.v1 as components
from urllib.parse import urlencode
import time
from retrieve.elastic_search import retrieve_top_20_results
from services.utils import rewrite_query_with_history, rewrite_query, rewrite_query_v2, generate_response, generate_chat_title, detect_intent, analyze_complex_situation, retrieve_parallel, generate_structured_response
from services.history import save_chat, load_chats, rename_chat, delete_chat, create_new_chat, cleanup_empty_chats
# from services.history_sqlite import save_chat, load_chats, rename_chat, delete_chat, create_new_chat, cleanup_empty_chats
from agents.pipeline import run_pre_retrieve, Action

# from retrieve.search import SearchService
# from retrieve.hybrid_rerank import HybridRerankRetriever, collection, engine, client

# Khởi tạo service 1 lần, tái dùng
# search_service = SearchService(collection=collection, engine=engine, client=client)

# rerank_retriever = HybridRerankRetriever(
#         # hybrid_retriever=hybrid_retriever,
#         client=client,
#         rerank_model="gpt-4o-mini",  # đổi model nếu muốn
#     )

from retrieve.two_stage_search import collection, engine, client
from retrieve.build_graph import GraphRAGRetriever, get_neo4j
import asyncio
import nest_asyncio

neo4j_driver = get_neo4j()
two_stage_retriever = GraphRAGRetriever(
    neo4j_driver=neo4j_driver,
    milvus_collection=collection,
    openai_client=client,
)

# === Giao diện ===
if "query" not in st.session_state:
    st.session_state.query = ""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
# if "chat_id" not in st.session_state or st.session_state.chat_id not in st.session_state.chats:
#     # Khởi tạo cuộc trò chuyện mặc định khi mở app
#     new_id = create_new_chat()
#     st.session_state.chat_id = new_id
#     st.session_state.chat_history = []
#     st.session_state.chats = load_chats()
if "chat_id" not in st.session_state:
    st.session_state.chat_id = None
if "chats" not in st.session_state:
    st.session_state.chats = load_chats()
if "query_mode" not in st.session_state:
    st.session_state.query_mode = "normal"  # "normal" | "situation"


# === Hàm tiện ích ===
def clear_input():
    st.session_state.query = ""

def add_to_history(role, content):
    st.session_state.chat_history.append({"role": role, "content": content})

# === Giao diện chính ===
st.markdown("""
            <style>
            /*
            div.block-container {
                padding: 0 !important;
                margin: 0 !important;
            }
            */
            .block-container {
                padding-top: 45px !important;
                padding-bottom: 0rem !important;
            }

            .button {
                border: none !important;
            }

            .topbar-title {
                font-weight: 600;
                font-size: 28px;
                line-height: 30px; 
            }
                        
            /* Tất cả nút trong sidebar */
            [data-testid="stSidebar"] .stButton > button {
                background-color: rgb(240, 242, 246);
                color: black;
                border: none !important; 
                border-radius: 12px;
                font-size: 15px;
                width: 100%;
                    
                justify-content: flex-start !important;
                align-items: center !important;
                text-align: left !important;
                    
                padding-inline: 16px !important; 
                    
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            /* Hover */
            [data-testid="stSidebar"] .stButton > button:hover {
                background-color: white;
            }

            /* Nút 'active' dùng type='primary' để dễ bắt selector */
            [data-testid="stSidebar"] button[data-testid="baseButton-primary"] {
                background-color: white !important;
                color: black !important;
                font-weight: 700;
            }
            </style>
            """, unsafe_allow_html=True)


st.set_page_config(page_title="Hỏi đáp Pháp luật", layout="wide", page_icon="🤖")

# Thêm vào giao diện, ngay trên st.chat_input
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

# Thay đổi placeholder theo mode
placeholder_text = (
    "Mô tả tình huống: ai làm gì · với ai · hoàn cảnh · hậu quả..."
    if st.session_state.query_mode == "situation"
    else "Nhập câu hỏi pháp luật của bạn..."
)
prompt = st.chat_input(placeholder_text)

# SIDEBAR
with st.sidebar:
    if st.button("➕ Tạo hội thoại mới", use_container_width=True, type='primary'):
        cleanup_empty_chats() 
        # st.session_state.chat_id = create_new_chat()
        st.session_state.chat_id = None
        st.session_state.chat_history = []
        # st.session_state.chats = load_chats()
        st.session_state.query = ""
        st.rerun()

    # st.markdown("---")

    st.header("Chats ")

    # st.header(f"Chat_id: {st.session_state.get('chat_id', '')}")

    # Lấy danh sách hội thoại và sắp xếp theo updated_at (từ cũ đến mới)
    sorted_chats = sorted(
        st.session_state.chats.items(),
        key=lambda x: x[1].get("updated_at", ""),
        reverse=True
    )
    for cid, chat in sorted_chats:
        title = chat.get("title", "(Không tên)")
        # title = f"{cid == 'dd6f0f9f-04ce-4702-8749-466b06db2b92'} {chat.get('title', '(Không tên)')}"

        is_active = cid == 'dd6f0f9f-04ce-4702-8749-466b06db2b92'
        # Dùng type='primary' cho nút đang active để CSS trên bắt được
        btn_type = "primary" if is_active else "secondary"
        div_class = "active-btn" if is_active else "normal-btn"

        if st.button(title, key=f"load_{cid}", use_container_width=True, type=btn_type):
            cleanup_empty_chats()
            st.session_state.chat_id = cid
            st.session_state.chat_history = chat["messages"]
            st.rerun()


# TOPBAR
st.session_state.setdefault("pending_action", None)
st.session_state.setdefault("new_name", "")
st.session_state.setdefault("last_action", None)

col_left, col_right = st.columns([1, 0.17]) #0.15
with col_left:
    st.markdown(
        f'<div class="topbar-title">{st.session_state.chats.get(st.session_state.chat_id, {}).get("title", "Hệ thống hỏi đáp pháp luật Việt Nam")}</div>',
        unsafe_allow_html=True
    )

if st.session_state.chat_id:
    with col_right:
        # with st.popover("⋮"):
        #     st.markdown("**Tùy chọn**")
        #     if st.button("Đổi tên", key="rename_btn", use_container_width=True):
        #         st.session_state.pending_action = "rename"
        #     if st.button("Xoá", key="delete_btn", use_container_width=True):
        #         st.session_state.pending_action = "delete"
        c1, c2 = st.columns([2,1.5])
        with c1:
            if st.button("Đổi tên", key="rename_btn", use_container_width=True):
                st.session_state.pending_action = "rename"
        with c2:
            if st.button("Xoá", key="delete_btn", use_container_width=True):
                st.session_state.pending_action = "delete"

st.markdown("---")

# Xử lý kết quả
@st.dialog("Xác nhận thao tác")
def confirm_dialog():
    action = st.session_state.pending_action

    if action == "rename":
        chat_id = st.session_state.chat_id
        current_name= st.session_state.chats.get(st.session_state.chat_id, {}).get("title", "")
        if "prefilled_chat" not in st.session_state or st.session_state.prefilled_chat != chat_id:
            st.session_state.rename_value = current_name
            st.session_state.prefilled_chat = chat_id

        st.write("Nhập **tên mới** cho tài liệu:")
        st.session_state.new_name = st.text_input(
            "Tên mới", 
            key="rename_value", 
            value=st.session_state.get("new_name", current_name), 
            label_visibility="collapsed"
        )

        c1, c2 = st.columns([1, 0.3])
        with c1:
            if st.button("Huỷ"):
                st.session_state.pending_action = None
                st.rerun()
        with c2:
            if st.button("Xác nhận"):
                chat_id = st.session_state.chat_id
                new_name = st.session_state.new_name
                rename_chat(chat_id, new_name)
                st.session_state.chats = load_chats()
                st.session_state.pending_action = None
                st.session_state.last_action = "rename"
                st.rerun()

    elif action == "delete":
        st.warning("Bạn chắc chắn muốn **xoá hội thoại** này?\n \nHành động này **không thể hoàn tác**.")
        c1, c2 = st.columns([1, 0.4])
        with c1:
            if st.button("Huỷ"):
                st.session_state.pending_action = None
                st.rerun()
        with c2:
            if st.button("Xoá vĩnh viễn"):
                chat_id = st.session_state.chat_id
                delete_chat(chat_id)
                st.session_state.chat_id = None
                st.session_state.chat_history = []
                st.session_state.chats = load_chats()
                st.session_state.query = ""
                st.session_state.pending_action = None
                st.session_state.last_action = "delete"
                st.rerun()

# mở hộp thoại nếu có hành động chờ xác nhận
if st.session_state.pending_action:
    confirm_dialog()

if st.session_state.last_action:
    action = st.session_state.last_action
    if action == "rename":
        st.toast("Đổi tên hội thoại thành công", icon="✅")
    if action == "delete":
        st.toast("Đã xoá hội thoại", icon="✅")
    st.session_state.last_action = None


# === GIAO DIỆN CHAT ===   
# === lịch sử hội thoại ===
for msg in st.session_state.chat_history:
    role = msg["role"]
    content = msg["content"]
    st.chat_message(role).write(content)


def to_md(text: str) -> str:
    return text.replace("\n", "  \n")

if prompt:
    # Nếu chưa có chat_id thì tạo mới
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

        status_ph.caption("Đang phân tích yêu cầu…")

        decision = run_pre_retrieve(prompt, st.session_state.chat_history)

        status_ph.empty()

        if decision.action == Action.QUICK_ANSWER:
            # message_ph.markdown(to_md(decision.answer_text))
            response = decision.answer_text

            message_ph = st.empty()
            full_response = ""
                
            # Hiệu ứng gõ chữ cho quick response
            chunk_size = 5  # Hiển thị mỗi lần 5 ký tự
            for i in range(0, len(response), chunk_size):
                chunk = response[i:i + chunk_size]
                full_response += chunk
                time.sleep(0.05)  # Giảm delay xuống 0.05s cho quick response
                message_ph.markdown(full_response + "▌")
            message_ph.markdown(full_response)

        elif decision.action == Action.SPAM:
            # message_ph.caption("Tin nhắn có dấu hiệu spam nên đã bị chặn")
            response = "Xin lỗi, tôi không thể xử lý yêu cầu của bạn vì nó có dấu hiệu spam. Bạn hãy cung cấp thông tin cụ thể hơn để tôi có thể hỗ trợ bạn tốt nhất. Cảm ơn bạn!"

            message_ph = st.empty()
            full_response = ""
                
            # Hiệu ứng gõ chữ 
            chunk_size = 5  
            for i in range(0, len(response), chunk_size):
                chunk = response[i:i + chunk_size]
                full_response += chunk
                time.sleep(0.05) 
                message_ph.markdown(full_response + "▌")
            message_ph.markdown(full_response)
        

        elif decision.action == Action.ESCALATE:
            # có thể ghi log, tạo ticket, hoặc ping human ở đây
            # message_ph.markdown(
            #     to_md("Câu hỏi có tính nhạy cảm cao. Mình đã chuyển cho chuyên viên để hỗ trợ")
            # )
            response = "Mình rất tiếc khi đem lại trải nghiệm không tốt cho bạn. Bạn có muốn mình chuyển tiếp vấn đề của bạn cho cán bộ trực tiếp xử lý không?"

            message_ph = st.empty()
            full_response = ""
                
            # Hiệu ứng gõ chữ
            chunk_size = 5 
            for i in range(0, len(response), chunk_size):
                chunk = response[i:i + chunk_size]
                full_response += chunk
                time.sleep(0.05) 
                message_ph.markdown(full_response + "▌")
            message_ph.markdown(full_response)

        elif decision.action == Action.PROCEED:
            status_ph.caption("Đang tìm kiếm thông tin…")

            hist = st.session_state.get("chat_history", [])
            base = hist[:-1] if hist and hist[-1].get("role") == "user" else hist
            lastest_history = base[-10:]

            query_chuanhoa = rewrite_query_v2(prompt, lastest_history)
            if query_chuanhoa:
                status_ph.caption(query_chuanhoa)

            intent_result = detect_intent(query_chuanhoa) or "Có"

            if "không" in intent_result.lower():
                response = (
                    "Có vẻ yêu cầu của bạn chưa rõ ràng hoặc không nằm trong phạm vi "
                    "mình xử lý nên mình không thể hỗ trợ bạn được. Mình là trợ lý pháp lý "
                    "hỗ trợ tìm kiếm và giải thích luật. Bạn có thể hỏi mình về các quy định, "
                    "điều luật cụ thể, hoặc quy trình pháp lý mà bạn đang thắc mắc."
                )
            else:
                # ── Phân tích tình huống ────────────────────────────────────
                is_situation = st.session_state.query_mode == "situation"

                if is_situation:
                    status_ph.caption("Đang phân tích tình huống…")
                    situation   = analyze_complex_situation(prompt, lastest_history)
                    cac_vi_pham = situation.get("cac_vi_pham", [])
                    all_queries = []
                    for vp in cac_vi_pham:
                        all_queries.extend(vp.get("queries", []))
                    if not all_queries:
                        all_queries = [query_chuanhoa]

                    if cac_vi_pham:
                        status_ph.caption(
                            f"Phát hiện {len(cac_vi_pham)} vi phạm — đang tìm kiếm…"
                        )
                else:
                    situation   = {}
                    cac_vi_pham = []
                    all_queries = [query_chuanhoa]
                # ────────────────────────────────────────────────────────────

                # ── Search song song ────────────────────────────────────────
                import nest_asyncio
                nest_asyncio.apply()

                loop = asyncio.new_event_loop()
                try:
                    raw_results = loop.run_until_complete(
                        retrieve_parallel(two_stage_retriever, all_queries, top_k_each=8)
                    )
                finally:
                    loop.close()
                # ────────────────────────────────────────────────────────────

                # ── Deduplicate + ưu tiên web ───────────────────────────────
                seen_keys   = set()
                web_results = []
                pd_results  = []

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
                # ────────────────────────────────────────────────────────────

                if results:
                    context_parts = []
                    web_sources   = []

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
                                "url":   hit["url"],
                                "level": hit.get("trust_level", "medium"),
                                "label": label,
                            })

                    print("Văn bản liên quan:")
                    print(context_parts[:3])

                    # ── Gen câu trả lời ─────────────────────────────────────
                    if is_situation and cac_vi_pham:
                        response = generate_structured_response(
                            context_parts,
                            prompt,
                            situation,
                            decision.sentiment,
                            lastest_history,
                        )
                    else:
                        response = generate_response(
                            context_parts,
                            query_chuanhoa,
                            decision.sentiment,
                            lastest_history,
                        )
                    # ────────────────────────────────────────────────────────

                    if web_sources:
                        source_lines = ["\n\n---\n**Nguồn tham khảo từ web:**"]
                        for s in web_sources:
                            icon = "✅" if s["level"] == "high" else "⚠️"
                            source_lines.append(
                                f"{icon} {s['label']} [{s['title']}]({s['url']})"
                            )
                        response += "\n".join(source_lines)

                else:
                    response = (
                        "Mình rất tiếc vì chưa đủ thông tin để trả lời câu hỏi này, "
                        "bạn hãy cung cấp rõ tình huống và vấn đề pháp lý bạn gặp phải nhé. "
                        "Nếu vấn đề nằm ngoài khả năng xử lý, mình sẽ hỗ trợ bạn "
                        "chuyển tiếp vấn đề cho cán bộ xử lý!"
                    )

            print("Query đã chuẩn hoá:")
            print(query_chuanhoa)
            print("Sentiment:")
            print(decision.sentiment)

            message_ph = st.empty()
            full_response = ""
            status_ph.empty()

            chunk_size = 5
            for i in range(0, len(response), chunk_size):
                full_response += response[i:i + chunk_size]
                time.sleep(0.05)
                message_ph.markdown(full_response + "▌")
            message_ph.markdown(full_response)

    add_to_history("assistant", response)

    # === Tự động đặt tên nếu chưa có ===
    if st.session_state.chat_id:
        chat_id = st.session_state.chat_id
        if chat_id not in st.session_state.chats:
            st.session_state.chats = load_chats()
        current_title = st.session_state.chats.get(chat_id, {}).get("title", "")

        # Tự động đặt tên cho conversation
        if not current_title or current_title.startswith("Cuộc trò chuyện"):
            new_title = generate_chat_title(st.session_state.chat_history)
        else:
            new_title = current_title
        save_chat(chat_id, new_title, st.session_state.chat_history)
        st.session_state.chats = load_chats() 
        st.rerun()
    
    st.session_state.chats = load_chats()
