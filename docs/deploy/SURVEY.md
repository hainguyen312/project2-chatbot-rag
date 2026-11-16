# Khảo sát & quyết định deploy production (16/11/2026)

Tài liệu này ghi lại kết quả khảo sát (máy Mac + máy Windows này) và các quyết định
đã chốt, để phiên làm việc sau (Claude Code chạy trong WSL) đọc lại, không khảo sát lại.

## 1. Kiến trúc chốt

```
Internet → Cloudflare (HTTPS) → cloudflared (container, kết nối đi ra)
        → frontend Next.js :3000 → backend FastAPI :8001
        → MySQL, Milvus (+etcd, minio), Neo4j  (container)
        → MongoDB Atlas (ngoài, giữ như hiện tại)
```

- Tất cả chạy bằng `docker compose` trong WSL2 Ubuntu 24.04, **không publish cổng ra host**.
  Debug: `docker-compose.debug.yml` bind `127.0.0.1` khi cần. Không dùng Caddy.
- ~~Chưa có domain: chạy trước bằng quick tunnel trycloudflare~~ → **Cập nhật 16/11/2026: đã có
  domain `haichatbot.id.vn`**, nameserver đã chuyển sang Cloudflare. Dùng **named tunnel**
  `chatbot-prod`, public hostname `haichatbot.id.vn` → `http://frontend:3000`.
  - Token trong `.env.prod` (gốc repo, chmod 600, đã gitignore), tên biến
    `CLOUDFLARE_TUNNEL_TOKEN`. Không đọc/in nội dung file.
  - Chủ repo đã thử bằng container tạm `cloudflare/cloudflared` với `TUNNEL_TOKEN`: 4 kết nối
    "Registered tunnel connection", protocol quic, location hkg.
  - **Yêu cầu cho `docker-compose.prod.yml`:** service `cloudflared` dùng
    `TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}`, lệnh `tunnel --no-autoupdate run`, cùng network
    với `frontend`, **ghim phiên bản image cụ thể** (không `latest`).
  - Health gate của `deploy.sh` và nghiệm thu reboot dùng `https://haichatbot.id.vn`.
  - Quick tunnel trycloudflare không còn là đường chính (chỉ dự phòng khi debug).
  - **Trước nghiệm thu (bước 7): nhắc chủ repo thêm `haichatbot.id.vn` vào Firebase →
    Authentication → Settings → Authorized domains.**

## 2. Máy chủ (Windows 11 Pro 10.0.26200)

| Mục | Đo ngày 16/11/2026 |
|---|---|
| GPU | RTX 5060 Ti 16 GB (Blackwell, sm_120), driver Windows **591.86**, CUDA 13.1. `nvidia-smi` trong WSL chạy được (`/usr/lib/wsl/lib/nvidia-smi`). ~1.3 GB VRAM bị app desktop chiếm. |
| RAM | 32 GB vật lý. WSL lúc khảo sát: 15 GB + swap 4 GB (**chưa có** `%UserProfile%\.wslconfig`) → cần tạo: memory=24GB, swap=8GB. |
| CPU | WSL thấy 12 nhân. |
| Đĩa | Ổ WSL (ext4.vhdx) 1007 GB, trống 915 GB. Ổ C trống ~155 GB. |
| WSL | Distro mặc định `Ubuntu` = Ubuntu 24.04.1, `systemd=true` trong `/etc/wsl.conf`. Còn `Ubuntu-20.04` và `docker-desktop` (không dùng). |
| Docker | Lúc khảo sát: Docker Desktop đã cài nhưng tắt; `docker` trong WSL trỏ sang `/mnt/c/Program Files/Docker/...` (tích hợp WSL của Desktop). |
| Mạng | Mạng gia đình, không IP công khai, không mở cổng router. |

### User / thư mục trong WSL
- Đăng nhập WSL mặc định là `root`. Đã tạo user thường **`deploy`** (uid 1000), thuộc group
  `docker` (gid 1000, tạo trước khi cài Docker Engine).
- Code: **`/home/deploy/apps/chatbot`** (clone từ GitHub, branch `deploy/prod` tạo từ `main`
  @ `2fe63fd`). Không đặt gì dưới `/root` hay `/mnt/c`.
- Bí mật đã copy từ repo Windows (không đọc nội dung, chmod 600, chủ `deploy`):
  `.env`, `frontend/.env.local`, `firebase-credentials.json` — đều đã được gitignore.
- Dump MySQL: `/root/law.sql` (~150 MB, sha256 đã kiểm, kết thúc `-- Dump completed`,
  tiếng Việt đúng), dự phòng `/root/law.sql.gz` (~19 MB). Khi dùng: copy sang thư mục của
  `deploy` (vd `/home/deploy/data/law.sql`).
- Conda (base) tự bật khi mở shell root → script trên host phải gọi `/usr/bin/python3`
  hoặc chạy trong container. RUNBOOK ghi cách tắt: `conda config --set auto_activate_base false`.

## 3. Quyết định đã chốt

| # | Quyết định |
|---|---|
| Docker | **Docker Engine trong WSL + NVIDIA Container Toolkit**, tắt Docker Desktop (gỡ tích hợp WSL, tắt tự khởi động). Lý do: Desktop chỉ chạy khi có người đăng nhập Windows; Engine là service systemd. |
| Nghiệm thu bắt buộc | Khởi động lại máy **không đăng nhập Windows**, chờ 5 phút, `https://haichatbot.id.vn` truy cập được (đã thêm domain vào Firebase Authorized domains). |
| Repo | **PUBLIC** → **không dùng self-hosted GitHub runner**, **không có `deploy.yml`**. |
| CD | **Pull-based**: `scripts/deploy.sh` chạy bằng **systemd timer mỗi 5 phút** dưới user `deploy`: fetch `origin/main`; nếu sha mới ≠ sha đang chạy **và** CI (`ci.yml`) trên đúng sha đó đã `success` (GitHub API công khai, không token) → `docker compose build` (IMAGE_TAG=sha, build tại máy, không registry) → `up -d` → health gate (backend + frontend + URL công khai) → fail trong N giây thì tự quay về tag trước. `flock` chống chạy chồng, ghi log. Lưu tag đang chạy vào file; giữ 3 image gần nhất. `scripts/rollback.sh <tag>`. |
| CI | `.github/workflows/ci.yml` trên `ubuntu-latest` (PR + push): backend pytest với torch CPU (không gọi OpenAI/Tavily), frontend `npm ci` + lint + build, build thử 2 Dockerfile (backend bản CPU). |
| Bí mật trong lịch sử git | Quét toàn bộ lịch sử bằng gitleaks, báo kết quả; **không** viết lại lịch sử — chủ repo tự đổi khoá. Mật khẩu mặc định trong compose cũ coi như đã lộ. |
| Khách vãng lai | **Chặn.** Mọi route gọi LLM bắt đăng nhập Firebase (verify ID token ở server). Bỏ chấp nhận id `demo_…` cho route LLM. |
| Trần chung | Tổng số câu hỏi/ngày toàn hệ thống, cấu hình được, **mặc định 300**; vượt thì trả thông báo lịch sự, **không gọi LLM**. Cộng thêm rate limit theo user + IP (chat, stream, review, tts, stt, chats/title). |
| `/api/chats` | Verify Firebase token, chỉ đọc/xoá chat của chính mình; test A truy cập chat của B → 403/404. **Làm đầu tiên ở bước 5.** |
| Lint | Sửa luôn 2 lỗi (`useAuth.tsx:75`, `useSTT.tsx:13` — setState trong effect). |
| MySQL | **Giữ `mysql:9.6`** đúng như máy Mac (dump lấy từ 9.6). *(Ghi chú cũ nói dữ liệu là 8.0.45 — đã được chủ repo sửa lại: dùng 9.6.)* |
| Neo4j | Ghim `neo4j:5.26.24` community (compose cũ ghi `neo4j:5` không ghim). |
| Milvus | v2.4.14 + etcd `quay.io/coreos/etcd:v3.5.5` + minio `RELEASE.2023-03-20T20-16-18Z`. |
| Embedding Milvus | Nhúng lại tên điều bằng `text-embedding-3-small`. **Đếm token thật trước**; chạy nếu **< 3M token (≈ $0.06)**, vượt thì hỏi lại. |
| GPU sentiment | Chọn `cuda` nếu có, fallback `cpu`, log `device: …` khi khởi động. Có test. |
| Shared secret | Header bí mật giữa Next.js và FastAPI; backend từ chối request thiếu header. |

## 4. Kết quả khảo sát code (xác nhận lại trên repo ngày 16/11)

- `agents/sentiment_agent.py` nạp `joeddav/xlm-roberta-large-xnli` (~2.1 GB) ngay trong
  `__init__`; `agents/pipeline.py:7` tạo `_manager = AgentsManager()` ở cấp module → nạp lúc
  import `rag_api`. Chạy mỗi request chat/stream, luôn CPU, không chọn device.
- `agents/spam_agent.py`: rule-based, `import torch` thừa (dòng 5), code model bị comment.
- `requirements.txt`: `torch`, `transformers` không ghim; có `setuptools<81` (pymilvus 2.4),
  `pymilvus==2.4.*`, `firebase-admin`, `bm25s>=0.2`, `pytest`. Mac: Python 3.12, torch 2.15.0,
  transformers 5.22.2.
- Model HF duy nhất cần: `joeddav/xlm-roberta-large-xnli`. Cache cần volume: `HF_HOME`,
  `BM25_CACHE_DIR` (`.cache/bm25_phapdien` ~104 MB), `LAWDATA_DIR` (`.cache/lawdata`).
- `rag_api.py`: `/health` (dòng ~368) chỉ trả `{"ok": true}`. SSE: `/rag/stream` (~533) và
  `/review/analyze` (~945) trả `StreamingResponse(text/event-stream)` **không heartbeat**
  (Cloudflare cắt sau ~100 s không có byte). Route ẩn danh `demo_…`: `/auth/claim_anonymous`,
  `services/auth_guard.py` (`enforce_user_match`).
- Code backend đọc ~66 biến môi trường (`os.getenv`) → liệt kê đủ trong `.env.prod.example`.
  Bắt buộc: `OPENAI_API_KEY, OPENAI_MODEL, OPENAI_AGENT_MODEL, TAVILY_API_KEY,
  MONGODB_URI/DB/COLLECTION, MYSQL_*, NEO4J_*, MILVUS_HOST/PORT, FIREBASE_CRED_PATH,
  FIREBASE_STORAGE_BUCKET`. Không cần cho prod: `ES_*`, `ANTHROPIC_API_KEY`.
- Frontend: Next 16.2.3, React 19.2.4. Runtime: `RAG_BACKEND_URL`, `MONGODB_*`. Build-time:
  `NEXT_PUBLIC_FIREBASE_*` (7 biến), `NEXT_PUBLIC_OFFICE_DATA`.
  `next.config.ts` vừa `module.exports = { allowedDevOrigins }` vừa `export default` → gộp
  thành một khi bật `output: 'standalone'`.
- 16 route API Next.js (`frontend/src/app/api/**/route.ts`):
  - `stream`, `review`: chỉ chuyển tiếp Authorization; backend chấp nhận `demo_…`.
  - `rag`, `tts`, `stt` (OpenAI tts + whisper-1), `chats/title`, `feedback`: không xác thực.
  - `memory/*`, `review/history/*`: backend `enforce_user_match` (cho phép `demo_`).
  - `admin/[...path]`: token + role admin (backend).
  - `chats`, `chats/[id]`: Mongo trực tiếp, **tin header `x-user-id`** không verify → đọc/xoá
    chat người khác được.
- Test: 298 hàm `def test_` (372 test kể cả parametrize — số từ máy Mac; đếm lại trong container).
  Python Windows chưa có pytest.
- Compose cũ: `law-crawler/docker-compose.yml` (mysql:9.6 + phpmyadmin, mật khẩu `123456789`),
  `test_raptor/docker-compose.yml` (etcd, minio `minioadmin`, milvus, `neo4j:5` `password123`),
  publish `0.0.0.0` nhiều cổng → **không dùng cho prod**, mật khẩu coi như đã lộ.

## 5. RAM đo trên Mac (đo lại trên máy này khi chạy đủ)

| Service | Image | RAM Mac | mem_limit đề xuất |
|---|---|---|---|
| milvus | milvusdb/milvus:v2.4.14 | 2.89 GiB | 4G |
| etcd | quay.io/coreos/etcd:v3.5.5 | 57 MiB | 256M |
| minio | minio/minio:RELEASE.2023-03-20T20-16-18Z | 232 MiB | 512M |
| neo4j | neo4j:5.26.24 community (dữ liệu ~1.5 GB) | 1.54 GiB | 4.5G (heap 2G, pagecache 1.5G) |
| mysql | mysql:9.6 | 526 MiB | 1.5G |
| backend | (tự build) | 961 MB + 2.1 GB trọng số mmap ≈ 3.1 GB CPU; GPU thêm CUDA context | 6G |
| frontend | (tự build, `next start`) | 80–190 MB | 768M |
| cloudflared | cloudflare/cloudflared | — | 256M |

## 6. Dữ liệu cần kiểm sau migrate

- MySQL: 66.706 điều (68.086 tài liệu BM25).
- Neo4j (dựng lại bằng `python -m retrieve.build_graph`, không tốn API): Dieu 66.706,
  Khoan 195.658, THAM_CHIEU 24.718, LIEN_QUAN 45.250.
- Milvus: ~66.751 vector tên điều.
- Known-item eval (không tốn LLM, câu hỏi đã cache): Hit@10 = 0,69.

## 7. Thứ tự các bước còn lại (mỗi bước một commit trên `deploy/prod`, không push/merge)

1. Chuẩn bị máy: `docs/deploy/SETUP_WINDOWS.md`, `scripts/windows/check_host.sh`.
2. Container hoá: Dockerfile backend (Python 3.12, torch cu128, build-arg CPU, non-root,
   HF_HOME volume, uvicorn 1 worker, HEALTHCHECK) + frontend (Node 20, standalone, non-root)
   + `.dockerignore` + chọn device GPU (có test).
3. `docker-compose.prod.yml` (+ debug), `.env.prod.example`, `/health` thật (503), heartbeat
   SSE `: ping` 15 s (có test).
4. CI (`ci.yml`) + CD pull-based (`deploy.sh` + systemd timer, `rollback.sh`), gitleaks.
5. Bảo mật: `/api/chats` trước → auth Firebase cho route LLM → trần ngày → rate limit →
   shared secret → tài liệu Cloudflare/Firebase/trần chi tiêu.
6. MIGRATE_DATA, BACKUP (+`backup.sh`, cron, giữ 7 bản), RUNBOOK.
7. Kiểm tra toàn bộ + nghiệm thu reboot không đăng nhập (nhắc Firebase Authorized domains trước).

## 8. Ràng buộc

- Không push, không merge. Không commit bí mật; xem `git diff` trước mỗi commit.
- Không đổi logic retrieval/agent; chỉ sửa code ở các mục đã chốt, mỗi thay đổi có test.
- Không gọi API LLM. Embedding: báo token/chi phí trước (ngưỡng 3M token).
- Việc cần quyền Windows (PowerShell admin, BIOS, driver) → viết hướng dẫn cho chủ máy tự làm.

## 9. Quét bí mật trong lịch sử git (gitleaks 8.30.1, 16/11/2026)

Lệnh: `gitleaks git --redact --log-opts="--all"` trên toàn bộ 38 commit, mọi nhánh (~520 MB).
Báo cáo đầy đủ (đã che giá trị): `/home/deploy/reports/gitleaks.json` (ngoài repo).

| Phát hiện | File | Commit | Nhánh còn chứa | Đánh giá |
|---|---|---|---|---|
| `openai-api-key` | `.env` (dòng 4; file còn chứa `ES_ENDPOINT`, `ES_API_KEY`, `OPENAI_API_KEY`, `OPENAI_MODEL`) | `2fdae55` (04/04/2026) | `origin/win` (`.env` **vẫn còn ở đầu nhánh**) | **THẬT — phải đổi khoá OpenAI + thu hồi khoá Elasticsearch** |
| `generic-api-key` ×4 | `testz/test_connect_elasticsearch.py` (dòng 6, 9) | `71cb19a`, `95afb55` (24/03/2026) | `origin/win` | **THẬT — khoá Elasticsearch, thu hồi** |
| `square-access-token` ×2 | `law-crawler/phap-dien/jsonData.js`, `treeNode.json` | `71cb19a` | `origin/win` | Báo nhầm: GUID dữ liệu pháp điển chứa chuỗi `EAAA` |

- `main` (HEAD `2fe63fd`) **không** chứa các file trên; nhưng repo PUBLIC nên lịch sử/nhánh
  `win` ai cũng đọc được. Không viết lại lịch sử (quyết định chủ repo); chủ repo tự đổi khoá.
  Tuỳ chọn sau khi đổi khoá: xoá nhánh `win` trên GitHub để bớt lộ (khoá cũ vẫn phải coi là lộ).
- Mật khẩu mặc định trong compose cũ (`123456789`, `password123`, `minioadmin`) coi như lộ;
  production chỉ dùng mật khẩu mới trong `.env.prod`.
- CI sẽ chạy gitleaks trên mỗi PR/push để chặn tái diễn (bước 4).
