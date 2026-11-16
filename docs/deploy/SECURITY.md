# Bảo mật & trần chi tiêu

Quyết định gốc: `docs/deploy/SURVEY.md` mục 3 (khách vãng lai bị chặn, trần chung/ngày, rate
limit, shared secret, `/api/chats`). Tài liệu này mô tả **những gì code đã làm** (bước 5a–5e) và
**những việc chủ repo phải tự làm** trên Cloudflare / Firebase / Google Cloud / OpenAI / Tavily.

## 1. Các lớp bảo vệ trong code

```
Trình duyệt ──HTTPS──► Cloudflare ──tunnel──► frontend (Next.js) ──X-Internal-Secret──► backend (FastAPI) ──► OpenAI
                       (CF-Connecting-IP)      route /api/*                               require_login
                                               gửi Authorization + X-Client-IP            rate limit user + IP
                                                                                          trần chung/ngày (MySQL)
```

| Lớp | Ở đâu | Hành vi |
|---|---|---|
| Không cổng ra ngoài | `docker-compose.prod.yml` | Không service nào publish cổng; chỉ vào qua Cloudflare Tunnel. DB ở mạng `data` internal (không ra Internet). |
| Shared secret | `services/internal_secret.py`, `frontend/src/lib/backend.ts` | Backend trả **403** nếu thiếu/sai `X-Internal-Secret` (`BACKEND_SHARED_SECRET`). Ngoại lệ: `/health` từ 127.0.0.1 (Docker healthcheck). |
| Đăng nhập bắt buộc | `rag_api.require_login`, `services/auth_guard.py` | Mọi endpoint gọi LLM cần Firebase ID token hợp lệ → **401**. uid luôn lấy từ token; `user_id` client gửi và id ẩn danh `demo_…` bị bỏ. |
| Lịch sử chat | `frontend/src/lib/server-auth.ts`, `/api/chats*` | uid từ token đã verify (qua backend `/auth/verify`); không tin `x-user-id`. A đọc/sửa/xoá chat của B → **404**. |
| Rate limit | `services/rate_limit.py` | Theo user và theo IP (IP từ `CF-Connecting-IP`). Vượt → **429** + `Retry-After`. |
| Trần chung/ngày | `services/usage_cap.py` | Tổng câu hỏi (chat + stream + rà soát) toàn hệ thống/ngày (giờ VN). Vượt → **429** + thông báo, **không gọi LLM**. MySQL lỗi → **503** (đóng an toàn). |

Thứ tự trong backend cho endpoint đặt câu hỏi: secret → token → rate limit → trần ngày → LLM
(rate limit trước để người spam không tiêu hết trần chung).

Test giữ các lớp này: `tests/test_llm_auth_wiring.py` (mọi endpoint gọi LLM có đúng guard),
`tests/test_internal_secret.py`, `tests/test_rate_limit.py`, `tests/test_usage_cap.py`,
`frontend/tests/*.test.ts` (A/B chats, proxy chuyển tiếp token, mọi route dùng helper backend).

### Cấu hình (`.env.prod`, đổi xong: `docker compose … up -d backend`)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `DAILY_QUESTION_CAP` | `300` | Trần câu hỏi/ngày toàn hệ thống. **`0` = công tắc khẩn cấp: dừng mọi câu hỏi tới LLM.** |
| `RATE_LIMIT_QUESTION_USER` / `_IP` | `6/60,60/3600` / `12/60,120/3600` | Câu hỏi: số lần/giây-cửa-sổ, nhiều cửa sổ cách bằng dấu phẩy; `off` = tắt |
| `RATE_LIMIT_TTS_USER` / `_IP` | `20/60` / `40/60` | Đọc câu trả lời |
| `RATE_LIMIT_STT_USER` / `_IP` | `10/60` / `20/60` | Nhận giọng nói (Whisper) |
| `RATE_LIMIT_TITLE_USER` / `_IP` | `10/60` / `20/60` | LLM đặt tên đoạn chat |
| `BACKEND_SHARED_SECRET` | — (bắt buộc) | ≥ 32 ký tự ngẫu nhiên; đổi: sửa `.env.prod` rồi `up -d backend frontend` |

Xem mức dùng hôm nay: `/health` của backend có mục `daily_cap` ("x/300 câu hỏi hôm nay").
Rate limit đếm trong bộ nhớ → khởi động lại backend là đếm lại; trần ngày nằm trong MySQL
(bảng `usage_daily`) nên không mất.

## 2. Việc chủ repo phải làm

### 2.1. Đổi khoá đã lộ (SURVEY mục 9) — **làm trước khi mở công khai**

- [ ] OpenAI: thu hồi khoá cũ (lộ trong lịch sử git, nhánh `win`), tạo khoá mới (xem 2.4),
      cập nhật `OPENAI_API_KEY` trong `.env.prod`.
- [ ] Elasticsearch: thu hồi khoá (`testz/test_connect_elasticsearch.py`, `.env` cũ).
- [ ] (Tuỳ chọn) xoá nhánh `win` trên GitHub. Khoá cũ vẫn phải coi là lộ.

### 2.2. Cloudflare (dash.cloudflare.com → domain `haichatbot.id.vn`)

Tunnel đã lo HTTPS ở biên. Nên bật thêm:

- [ ] **SSL/TLS → Edge Certificates**: Always Use HTTPS = On; Minimum TLS Version = 1.2.
- [ ] **Security → Bots**: Bot Fight Mode = On (gói Free).
- [ ] **Security → WAF → Rate limiting rules** (gói Free có 1 rule): ví dụ
      *URI Path starts with `/api/`* → 120 request / 1 phút / IP → Block 1 phút. Là lớp ngoài
      cùng, chặn trước khi tới máy chủ; giới hạn chi tiết vẫn do backend làm.
- [ ] Khi bị tấn công: **Security → Settings → Security Level = I'm Under Attack** tạm thời.
- Không cần cấu hình cache: Cloudflare không cache `/api/*` (phản hồi động). SSE không bị cắt
  sau 100 s vì backend gửi `: ping` mỗi 15 s.

### 2.3. Firebase / Google Cloud

- [x] Authentication → Settings → **Authorized domains**: có `haichatbot.id.vn` (đã làm 16/11).
      Xoá các domain không còn dùng (vd IP LAN cũ) nếu có.
- [ ] Authentication → Sign-in method: chỉ bật phương thức đang dùng (Email/Password, Google).
- [ ] Authentication → Settings → **User actions**: bật *Email enumeration protection*.
- [ ] **Giới hạn Web API key** (khoá `NEXT_PUBLIC_FIREBASE_API_KEY` công khai trong bundle — bình
      thường, nhưng nên khoá phạm vi): console.cloud.google.com → APIs & Services →
      Credentials → khoá "Browser key" →
      *Application restrictions* = Websites: `https://haichatbot.id.vn/*`,
      `https://<project>.firebaseapp.com/*`; *API restrictions*: chỉ các API Firebase Auth cần
      (Identity Toolkit API, Token Service API).
- [ ] **Storage rules**: backend ghi file TTS bằng Admin SDK (bỏ qua rules) và `make_public()`
      từng file mp3. Rules nên **cấm client ghi**: `allow write: if false;`.
- Tài khoản quản trị: backend yêu cầu `email_verified` + vai trò `admin` trong MongoDB
  (`scripts/set_role.py`).

### 2.4. OpenAI (platform.openai.com) — trần chi tiêu

- [ ] Tạo **Project riêng** cho production (vd `chatbot-prod`) và tạo API key **trong project
      đó** (khoá mới — thay khoá đã lộ).
- [ ] Project → Limits: đặt **Monthly budget** (hard limit) và **email alert** ở ~50–80 %.
      Khi chạm budget, OpenAI từ chối request → backend trả lỗi, không phát sinh thêm phí.
- [ ] Project → Limits → **Model usage**: chỉ cho phép các model thực dùng (`OPENAI_MODEL`,
      `OPENAI_AGENT_MODEL`, model đặt tên/memory nếu khác, `whisper-1`, model TTS,
      `text-embedding-3-small`).
- Ước lượng trần: chi phí/ngày tối đa ≈ `DAILY_QUESTION_CAP` × (chi phí trung bình một câu
  hỏi agent) + TTS/STT/đặt tên (bị rate limit, không tính vào trần). Đo chi phí trung bình
  thật trên dashboard Usage sau vài ngày rồi chỉnh `DAILY_QUESTION_CAP` và budget cho khớp.

### 2.5. Tavily

- [ ] app.tavily.com: xem hạn mức gói; tạo khoá riêng cho production nếu khoá hiện tại dùng
      chung với máy dev.

## 3. Rủi ro còn lại (chưa xử lý — ngoài phạm vi đã chốt)

| Điểm | Mức | Ghi chú |
|---|---|---|
| `/api/feedback` → `/rag/feedback` không xác thực | Thấp | Ai cũng đặt 👍/👎 cho `chat_id` bất kỳ nếu biết id (UUID khó đoán). Không gọi LLM. Sửa: dùng `require_login` + kiểm chat thuộc uid. |
| File TTS công khai (`make_public`) | Thấp | URL chứa tên file khó đoán; nội dung là câu trả lời pháp luật chung, có thể kèm tình huống người dùng. Sửa: signed URL có hạn. |
| Rate limit trong bộ nhớ | Thấp | Khởi động lại backend → đếm lại. Trần ngày (MySQL) vẫn giữ. |
| Không kiểm token bị thu hồi | Thấp | `verify_id_token` không `check_revoked`; token Firebase hết hạn sau ~1 giờ. |
| `/auth/claim_anonymous` | Thấp | Còn để gắn dữ liệu ẩn danh cũ sang tài khoản khi đăng nhập (cần token hợp lệ). Gỡ khi không còn dữ liệu `demo_`. |
