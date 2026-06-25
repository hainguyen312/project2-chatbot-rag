# Prompt cho Claude Code — Thêm hệ thống Memory cho Agentic RAG chatbot pháp luật

Copy toàn bộ nội dung dưới đây và paste vào Claude Code (chạy từ thư mục gốc `project2-chatbot-rag`).

---

## NGỮ CẢNH DỰ ÁN

Tôi có một chatbot hỏi đáp pháp luật Việt Nam (Pháp Điển) dùng Agentic RAG:
- Backend: FastAPI (`rag_api.py`, port 8001), endpoint `/rag/stream` (SSE), `/rag/chat`
- Agentic loop trong `services/agentic_rag.py` với tool-calling (vector_search, graph_traverse, web_search, get_full_article)
- Vector store: Milvus (`retrieve/two_stage_search.py` — biến `client` là OpenAI client, `collection` là Milvus collection)
- Graph: Neo4j (`retrieve/build_graph.py`)
- Lịch sử chat: MongoDB (`services/history.py`, có sẵn `MONGODB_URI`, `MONGODB_DB`)
- LLM: OpenAI gpt-4o-mini (`OPENAI_MODEL` trong .env), embedding text-embedding-3-small (dim 1536)
- Frontend Next.js gửi `prompt`, `history`, `query_mode` và đã có sẵn khái niệm hội thoại với `chat_id`

## MỤC TIÊU

Thêm một **hệ thống quản lý bộ nhớ dài hạn (long-term memory)** cho agent, theo kiến trúc **Mem0** (Extraction → Update với 4 thao tác ADD/UPDATE/DELETE/NOOP) kết hợp phân loại 4 loại memory cho domain pháp lý. Agent phải nhớ được context xuyên nhiều phiên hội thoại của cùng một người dùng.

### 4 loại memory cần phân loại:
1. **Core** — hồ sơ người dùng ổn định: nghề nghiệp, địa phương, vai trò (vd: "người dùng là chủ doanh nghiệp nhỏ ở Quảng Ninh")
2. **Episodic** — tình huống/vụ việc cụ thể đã hỏi (vd: "đang tranh chấp hợp đồng thuê nhà với chủ trọ")
3. **Semantic** — chủ đề pháp lý / điều luật người dùng quan tâm (vd: "quan tâm Luật Lao động về thai sản")
4. **Procedural** — sở thích cách trả lời (vd: "thích câu trả lời ngắn gọn, có trích dẫn điều luật")

## YÊU CẦU TRIỂN KHAI

### 1. Tạo file `services/memory_manager.py`

Class `MemoryManager` với các method:

```
class MemoryManager:
    def __init__(self, openai_client, mongo_uri, milvus_host="localhost", milvus_port="19530")

    # PHA EXTRACTION — gọi async sau mỗi lượt hội thoại
    def extract_memories(self, user_msg: str, bot_msg: str, recent_summary: str = "") -> list[dict]
        # Dùng gpt-4o-mini với prompt trích xuất fact pháp lý quan trọng
        # Trả về list[{"fact": str, "type": "core|episodic|semantic|procedural"}]
        # Nếu lượt hội thoại không có thông tin đáng nhớ → trả về []

    # PHA UPDATE — so khớp với memory đã có, LLM quyết định thao tác
    def update_memory(self, user_id: str, candidate_fact: dict) -> str
        # 1. Embed candidate fact
        # 2. Search top-5 memory tương đồng của user_id trong Milvus
        # 3. Gọi LLM (tool-calling hoặc structured output) quyết định: ADD/UPDATE/DELETE/NOOP
        # 4. Thực thi thao tác trên cả Milvus + MongoDB
        # Trả về tên thao tác đã thực hiện

    # PHA RETRIEVE — lấy memory liên quan để đưa vào context
    def retrieve_memories(self, user_id: str, query: str, top_k: int = 5) -> list[dict]
        # Embed query, search Milvus filter theo user_id
        # Trả về list[{"fact", "type", "score", "timestamp"}]

    # Helper: format memory thành đoạn text đưa vào system prompt
    def format_for_context(self, memories: list[dict]) -> str

    # Async wrapper: extract + update chạy nền, không chặn response
    async def process_turn_async(self, user_id: str, user_msg: str, bot_msg: str)
```

### 2. Milvus collection mới: `user_memory`

Schema:
- `id` (INT64, primary, auto_id=True)
- `user_id` (VARCHAR, max_length=128) — để filter theo người dùng
- `mem_type` (VARCHAR, max_length=32) — core/episodic/semantic/procedural
- `fact` (VARCHAR, max_length=2000) — nội dung fact
- `embedding` (FLOAT_VECTOR, dim=1536)
- `timestamp` (VARCHAR, max_length=64)

Index: HNSW, COSINE, M=16, efConstruction=200.
Khi search luôn dùng `expr=f'user_id == "{user_id}"'` để cô lập theo người dùng.

### 3. MongoDB collection `memories`

Mỗi document:
```json
{
  "_id": "...",
  "user_id": "...",
  "milvus_id": 123,
  "fact": "...",
  "mem_type": "core",
  "timestamp": "ISO8601",
  "version": 1,
  "source_chat_id": "...",
  "history": [{"action": "ADD", "fact": "...", "at": "..."}]
}
```
MongoDB lưu raw fact + lịch sử thay đổi (audit). Milvus chỉ lo vector search.

### 4. Prompt cho LLM extraction (viết trong code, tiếng Việt)

System prompt phải:
- Chỉ trích xuất fact BỀN VỮNG, hữu ích cho cá nhân hóa pháp lý lâu dài
- KHÔNG lưu nội dung điều luật chung chung (cái đó đã có trong RAG)
- KHÔNG lưu thông tin nhạy cảm quá mức (số CMND, số tài khoản) — bỏ qua các fact chứa thông tin định danh nhạy cảm
- Trả về JSON array hợp lệ, mỗi item có "fact" và "type"
- Nếu không có gì đáng nhớ → trả về `[]`

Ví dụ vào: user "Tôi mở quán cà phê ở Hạ Long, muốn biết thủ tục đăng ký kinh doanh" / bot "..."
Ví dụ ra: `[{"fact": "Người dùng mở quán cà phê ở Hạ Long, Quảng Ninh", "type": "core"}, {"fact": "Đang tìm hiểu thủ tục đăng ký kinh doanh hộ cá thể", "type": "episodic"}]`

### 5. Prompt cho LLM update decision (viết trong code, tiếng Việt)

Đưa candidate fact + danh sách memory tương đồng, LLM quyết định:
- **ADD**: fact hoàn toàn mới
- **UPDATE**: bổ sung/sửa memory cũ (vd địa chỉ thay đổi)
- **DELETE**: fact mới phủ định fact cũ (vd "không còn kinh doanh nữa")
- **NOOP**: đã có thông tin tương đương, không cần thay đổi

### 6. Tích hợp vào `rag_api.py`

#### 6a. Khởi tạo MemoryManager (sau khi đã có `client`, dùng lại OpenAI client):
```python
from services.memory_manager import MemoryManager
memory_manager = MemoryManager(openai_client=client, mongo_uri=os.getenv("MONGODB_URI"))
```

#### 6b. RagChatRequest thêm field `user_id`:
```python
class RagChatRequest(BaseModel):
    prompt: str
    history: List[ChatMessage] = []
    query_mode: Literal["normal", "situation"] = "normal"
    user_id: Optional[str] = None      # ← THÊM
    chat_id: Optional[str] = None      # ← THÊM (để audit source)
```

#### 6c. Trong `/rag/stream` (và `/rag/chat`):
- TRƯỚC khi chạy agentic loop: nếu có `user_id`, gọi `memory_manager.retrieve_memories(user_id, prompt)` → format → chèn vào đầu system prompt của agent dưới dạng:
  ```
  [Bộ nhớ về người dùng — dùng để cá nhân hóa câu trả lời]
  - (core) Người dùng mở quán cà phê ở Hạ Long
  - (episodic) Đang tìm hiểu thủ tục đăng ký kinh doanh
  ...
  ```
- SAU khi stream xong câu trả lời (sau event `done`): gọi `memory_manager.process_turn_async(user_id, prompt, full_answer)` bằng `asyncio.create_task` hoặc background task — KHÔNG chặn response.
- Gửi thêm SSE event `meta` field `memories_used` (list fact đã dùng) để frontend hiển thị nếu muốn.

### 7. Endpoint quản lý memory (CRUD cho user xem/xóa)

Thêm vào `rag_api.py`:
```
GET    /memory/{user_id}              → list tất cả memory của user
DELETE /memory/{user_id}/{memory_id}  → xóa 1 memory (cả Milvus + Mongo)
DELETE /memory/{user_id}              → xóa toàn bộ memory của user (quyền được quên)
```

### 8. Async + non-blocking

- Extraction + update PHẢI chạy nền (background task), không làm chậm response.
- Nếu MemoryManager lỗi (Milvus/Mongo down) → log warning, KHÔNG crash request chính. Bọc try/except toàn bộ memory operations.

### 9. Frontend (tùy chọn, làm nếu còn thời gian)

Trong `frontend/src/`:
- Gửi `user_id` (lấy từ localStorage hoặc auth, tạm thời hardcode 1 user_id demo) và `chat_id` trong body khi gọi `/api/stream`
- Thêm 1 nút "Bộ nhớ của tôi" mở panel hiển thị memory (gọi `GET /memory/{user_id}`), cho phép xóa từng memory hoặc xóa hết
- Hiển thị badge nhỏ "đã dùng N ký ức" trên message bot nếu `memories_used` không rỗng

## RÀNG BUỘC KỸ THUẬT

- Dùng lại OpenAI `client` đã có trong `two_stage_search.py`, KHÔNG tạo client mới
- Dùng lại pattern kết nối Milvus đã có (`connections.connect`, `Collection`)
- Dùng lại `MONGODB_URI` từ .env
- Embedding: text-embedding-3-small, dim 1536 (giống collection chính)
- Tất cả prompt LLM viết bằng tiếng Việt
- Code có docstring tiếng Việt, log rõ ràng mỗi thao tác memory (ADD/UPDATE/DELETE/NOOP)
- Thêm biến .env mới nếu cần: `MEMORY_ENABLED=1`, `MEMORY_TOP_K=5`, `MEMORY_EXTRACT_MODEL=gpt-4o-mini`

## THỨ TỰ LÀM

1. Tạo `services/memory_manager.py` (class đầy đủ + 2 prompt extraction/update)
2. Script `scripts/init_memory_collection.py` tạo Milvus collection `user_memory`
3. Tích hợp vào `rag_api.py` (init + retrieve trước loop + process async sau done)
4. Thêm 3 endpoint CRUD memory
5. Test: chạy thử 1 hội thoại, kiểm tra memory được tạo trong Milvus + Mongo, hội thoại sau retrieve được
6. (Tùy chọn) Frontend panel quản lý memory

Hãy bắt đầu bằng việc đọc các file hiện có: `rag_api.py`, `services/agentic_rag.py`, `retrieve/two_stage_search.py`, `services/history.py` để hiểu pattern code, RỒI mới triển khai. Làm từng bước, hỏi tôi xác nhận ở các điểm quyết định quan trọng (vd: schema, cách inject vào system prompt).