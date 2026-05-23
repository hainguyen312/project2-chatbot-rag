# Chatbot RAG — Hỏi đáp pháp luật (Pháp Điển)

Hệ thống hỏi–đáp pháp luật tiếng Việt dựa trên **Retrieval-Augmented Generation (RAG)** và **agentic tool-calling**: truy xuất vector (Milvus), mở rộng đồ thị (Neo4j), tùy chọn web (Tavily), rồi tổng hợp câu trả lời bằng LLM.

## Kiến trúc tổng quan

```
┌─────────────────┐     SSE / REST      ┌──────────────────┐
│  Next.js UI     │ ◄──────────────────►│  rag_api.py      │
│  (frontend/)    │                     │  FastAPI :8001   │
└────────┬────────┘                     └────────┬─────────┘
         │ MongoDB (lịch sử chat)                │
         │                                       ├── agents (pre-retrieve)
         │                                       ├── services/agentic_rag.py
         │                                       └── retrieve/GraphRAGRetriever
         │                                              ├── Milvus (vector)
         │                                              ├── Neo4j (graph)
         │                                              └── Tavily (web, tùy chọn)
         │
┌────────┴────────┐
│ MySQL `law`     │  ← nguồn gốc từ law-crawler
└─────────────────┘
```

| Thành phần | Vai trò |
|-----------|---------|
| **MySQL** | Lưu Pháp Điển đã crawl (`pddieu`, chủ đề, chương, đề mục) |
| **Milvus** | Embedding + tìm kiếm semantic (`phapdien_simple_tendieu`) |
| **Neo4j** | Đồ thị điều luật, quan hệ `LIEN_QUAN` / `THAM_CHIEU`, nội dung full cho agent |
| **rag_api** | API RAG, stream SSE, TTS/STT (Firebase tùy chọn) |
| **frontend** | Giao diện chat, markdown, passages, TTS, STT |
| **app.py** | Giao diện Streamlit (luồng cũ, tùy chọn) |

> Docker dùng cho **MySQL** (`law-crawler/`), **Milvus + Neo4j** (`test_raptor/`). Code Python và Next.js chạy trên host (venv + `npm`).

---

## Yêu cầu

- Python ≥ 3.10
- Node.js ≥ 20 (frontend Next.js 16)
- Docker & Docker Compose
- `OPENAI_API_KEY` (embedding, chat, rerank LLM tùy chọn)
- Tuỳ chọn: `TAVILY_API_KEY`, MongoDB (lịch sử chat), Firebase (TTS lưu file)

---

## 1. Cài đặt Python

```bash
cd project2-chatbot-rag
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Tạo file `.env` ở thư mục gốc (tham khảo các biến bên dưới).

---

## 2. Chuẩn bị dữ liệu MySQL

### 2.1 Crawl Pháp Điển

Tải dữ liệu từ [Pháp Điển](https://phapdien.moj.gov.vn/), giải nén vào `law-crawler/phap-dien/` (`chude.json`, `demuc.json`, `treeNode.json`, thư mục `demuc/`).

```bash
cd law-crawler
docker compose up -d          # MySQL + phpMyAdmin
pip install -r requirements.txt
# Lần đầu: bỏ comment db.create_tables trong main.py nếu DB trống
python main.py
```

MySQL mặc định: `localhost:3306`, DB `law`, user `root`, password `123456789`, phpMyAdmin `http://localhost:8081`.

### 2.2 Index Milvus

```bash
cd test_raptor
docker compose up -d          # etcd, minio, milvus-standalone, neo4j
```

Đợi healthy: `curl http://localhost:9091/healthz` → `OK`.

```bash
cd ..
source .venv/bin/activate
cd data_processing
python seed_data_batch.py              # ingest mới (drop collection cũ)
# python seed_data_batch.py --resume   # tiếp tục nếu bị gián đoạn
```

Collection: `phapdien_simple_tendieu` — embed theo **tên điều** (`tendieu`), metadata JSON (có `mapc`, `noidung` đã cắt nếu quá dài giới hạn Milvus).

### 2.3 Build đồ thị Neo4j

```bash
cd ..
python retrieve/build_graph.py
# hoặc: python -m retrieve.build_graph
```

Neo4j Browser: `http://localhost:7474` — `neo4j` / `password123`.

---

## 3. Chạy backend RAG (FastAPI)

```bash
source .venv/bin/activate
uvicorn rag_api:app --host 0.0.0.0 --port 8001 --reload
```

| Endpoint | Mô tả |
|----------|--------|
| `GET /health` | Health check |
| `POST /rag/chat` | Chat đồng bộ (JSON) |
| `POST /rag/stream` | Chat SSE (`meta`, `status`, `token`, `done`) |
| `POST /tts` | Text-to-speech |
| `POST /stt` | Speech-to-text |

### Luồng agentic (`services/agentic_rag.py`)

1. **Pre-retrieve** (`agents/`): quick / spam / sentiment → có thể trả lời ngay hoặc `proceed`.
2. **Rewrite** (`rewrite_query_v2`) + **intent**.
3. **Agent** gọi tool:
   - `vector_search` — Milvus → `mapc` → Neo4j (full nội dung) → **rerank** (`GraphRAGRetriever._rerank`, overlap từ khóa).
   - `graph_traverse` — mở rộng Neo4j theo `mapcs` → rerank.
   - `web_search` — Tavily.
   - `get_full_article` — full một điều theo `mapc`.
4. LLM tổng hợp câu trả lời; passages đưa vào panel UI (full text từ Neo4j, không phụ thuộc snippet Milvus).

**Rerank:** mặc định overlap + thứ tự Milvus; bật LLM rerank top-12: `RAG_AGENT_LLM_RERANK=1`.

### Biến môi trường (backend)

```bash
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
OPENAI_AGENT_MODEL=gpt-4o-mini

# MySQL (fallback khi Neo4j thiếu node)
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=123456789
MYSQL_DATABASE=law

# Agent / RAG
AGENT_LLM_MAX_NOIDUNG_CHARS=4000
AGENT_LLM_MAX_PASSAGE_CHARS=4000
AGENT_VECTOR_POOL_MULT=3
RAG_AGENT_LLM_RERANK=0

# Tavily (web_search)
TAVILY_API_KEY=tvly-...

# Lịch sử chat Python/Streamlit (tùy chọn)
MONGODB_URI=
MONGODB_DB=chatbot_rag
MONGODB_COLLECTION=conversations

# Firebase TTS (tùy chọn)
FIREBASE_CRED_PATH=firebase-credentials.json
FIREBASE_STORAGE_BUCKET=your-bucket
```

---

## 4. Chạy frontend (Next.js)

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Mở `http://localhost:3000`.

### `.env.local` (frontend)

```bash
RAG_BACKEND_URL=http://127.0.0.1:8001
MONGODB_URI=                    # tùy chọn; không có thì lưu chat trong RAM
MONGODB_DB=chatbot_rag
MONGODB_COLLECTION=conversations
```

- Chat gọi `POST /api/stream` → proxy tới `RAG_BACKEND_URL/rag/stream`.
- Sau khi nhận **đủ** câu trả lời, UI **gõ dần từng ký tự** (markdown qua `react-markdown`).
- Passages: panel bên phải, full `noidung` từ bucket agent.

---

## 5. Streamlit (tùy chọn)

```bash
source .venv/bin/activate
pip install streamlit
streamlit run app.py
```

`http://localhost:8501` — luồng RAG/graph tương tự nhưng giao diện Streamlit, không dùng agentic API mới.

---

## Cấu trúc thư mục chính

```
project2-chatbot-rag/
├── rag_api.py                 # FastAPI entry
├── app.py                     # Streamlit UI
├── services/
│   ├── agentic_rag.py         # Tool-calling + rerank passages
│   ├── utils.py               # rewrite, intent, generate
│   └── history.py             # Mongo / SQLite / JSON chat
├── retrieve/
│   ├── build_graph.py         # MySQL → Neo4j + GraphRAGRetriever
│   ├── two_stage_search.py    # Milvus module (import lúc start API)
│   └── tavily_fallback.py
├── agents/                    # Pre-retrieve agents
├── data_processing/
│   └── seed_data_batch.py     # MySQL → Milvus
├── law-crawler/               # Crawl + MySQL docker
├── test_raptor/               # Milvus + Neo4j docker-compose
└── frontend/                  # Next.js 16
```

---

## Xử lý sự cố

| Triệu chứng | Gợi ý |
|-------------|--------|
| `Collection 'phapdien_simple_tendieu' not exist` | Chạy `seed_data_batch.py` sau khi Milvus healthy |
| Milvus `Connection refused` | `docker compose up -d` trong `test_raptor`, đợi `healthz` |
| Neo4j crash / transaction log | Reset volume: stop neo4j, xóa `test_raptor/volumes/neo4j/databases` + `transactions`, `up -d`, chạy lại `build_graph.py` |
| `etcd` exited | `docker compose up -d` lại stack Milvus; có thể `docker rm` container etcd kẹt rồi tạo mới |
| API import lỗi `retrieve` | Chạy từ root repo; `build_graph.py` đã thêm `sys.path` khi chạy trực tiếp |
| Seed lỗi JSON > 65536 byte | Script tự cắt `noidung` trong metadata; dùng `--resume` nếu ingest gián đoạn |
| Frontend không stream chữ | Kiểm tra `RAG_BACKEND_URL` và `uvicorn` port 8001 |

---

## Đánh giá / thử nghiệm

```bash
cd evaluation
python run_evaluation.py
```

Các chiến lược retrieval cũ nằm trong `retrieve/retrieval_strategies.py` (dùng cho benchmark, không phải luồng agentic mặc định).

---

## License

Dự án học thuật / đồ án — tuỳ chính sách nhóm phát triển.
