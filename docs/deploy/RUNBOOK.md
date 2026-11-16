# RUNBOOK — vận hành production

Trang web: **https://haichatbot.id.vn** (Cloudflare named tunnel `chatbot-prod`).
Máy chủ: Windows 11 + WSL2 Ubuntu 24.04, Docker Engine (không Docker Desktop), user `deploy`.

Tài liệu liên quan: `SURVEY.md` (quyết định) · `SETUP_WINDOWS.md` (chuẩn bị máy) ·
`CD.md` (CI/CD, deploy, rollback) · `SECURITY.md` (bảo mật, trần chi tiêu) ·
`BACKUP.md` (backup/khôi phục) · `MIGRATE_DATA.md` (chuyển/dựng lại dữ liệu).

## 0. Vào máy

- Từ Windows: mở Terminal → `wsl -d Ubuntu` (user mặc định là root nếu chưa đổi) →
  `su - deploy`, hoặc `wsl -d Ubuntu -u deploy`. Việc cần root: `wsl -d Ubuntu -u root`.
- Root có conda tự bật → lệnh hệ thống dùng `/usr/bin/python3`; tắt hẳn:
  `conda config --set auto_activate_base false`.

## 1. Vị trí

| Gì | Ở đâu |
|---|---|
| Repo làm việc (branch dev) | `/home/deploy/apps/chatbot` |
| Repo đang chạy (CD, sau khi cài) | `/home/deploy/deploy/repo` (detached ở sha đang chạy) |
| Bí mật | `/home/deploy/apps/chatbot/.env.prod`, `firebase-credentials.json` (symlink từ `/home/deploy/deploy/secrets/`) |
| Trạng thái/log deploy | `/home/deploy/deploy/state/`, `/home/deploy/deploy/logs/deploy.log` |
| Backup | `/home/deploy/backups/` (+ `backup.log`) |
| Dữ liệu (volume Docker, project `chatbot`) | `chatbot_mysql_data`, `chatbot_neo4j_data`, `chatbot_milvus_data`, `chatbot_minio_data`, `chatbot_etcd_data`, `chatbot_hf_cache` (model 2.1 GB), `chatbot_bm25_cache`, `chatbot_lawdata` |

Viết tắt dùng trong tài liệu (chạy trong thư mục repo — làm việc hoặc deploy):

```bash
C="docker compose -f docker-compose.prod.yml --env-file .env.prod"   # repo deploy: --env-file ../secrets/.env.prod
```

**Luôn** có `--env-file`: thiếu nó compose đọc `.env` (bản dev). Project luôn tên `chatbot`.

## 2. Kiểm tra nhanh

```bash
$C ps                                   # mọi service Up; backend/frontend/mysql… (healthy)
docker exec chatbot-backend-1 python -c "import urllib.request,json;print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8001/health')),ensure_ascii=False,indent=1))"
curl -sI https://haichatbot.id.vn | head -1
$C logs --tail 100 backend              # [Sentiment] device: cuda …, [UsageCap] …
docker stats --no-stream
/home/deploy/apps/chatbot/scripts/windows/check_host.sh   # máy chủ (Docker, GPU, RAM…)
```

`/health` (chỉ gọi được từ trong container — shared secret): `mysql`, `milvus`, `neo4j`
critical (lỗi → 503); `mongodb`, `bm25`, `daily_cap` chỉ báo.

## 3. Bật / tắt / khởi động lại

```bash
$C up -d                                # cả stack (image phải có sẵn — IMAGE_TAG, xem CD.md)
$C restart backend                      # nạp lại model ~1–2 phút
$C stop                                 # dừng (giữ dữ liệu)
$C up -d mysql neo4j etcd minio milvus  # chỉ dữ liệu (không đưa site lên Internet)
$C stop cloudflared                     # gỡ site khỏi Internet ngay, stack vẫn chạy
```

Đổi biến trong `.env.prod` → `$C up -d <service>` (compose tạo lại container). **Không**
`down -v` (xoá volume = mất dữ liệu).

Debug cục bộ (cổng chỉ ở 127.0.0.1):
`docker compose -f docker-compose.prod.yml -f docker-compose.debug.yml --env-file .env.prod up -d`
— tắt bằng `up -d` chỉ với file prod.

## 4. Deploy / rollback

Xem `CD.md`. Tóm tắt: push lên `main` → CI xanh → timer 5' tự deploy + health gate + tự
rollback. Tay: `/home/deploy/deploy/bin/deploy.sh`, `…/rollback.sh [tag]`.
Tạm dừng CD: `systemctl stop chatbot-deploy.timer` (root).

## 5. Giới hạn sử dụng & công tắc khẩn cấp

| Tình huống | Làm |
|---|---|
| Chi phí OpenAI tăng bất thường / bị lạm dụng | `.env.prod`: `DAILY_QUESTION_CAP=0` → `$C up -d backend` (mọi câu hỏi nhận thông báo, không gọi LLM) |
| Cần gỡ site ngay | `$C stop cloudflared` |
| Đổi trần ngày / rate limit | `DAILY_QUESTION_CAP`, `RATE_LIMIT_*` trong `.env.prod` → `$C up -d backend` (SECURITY.md mục 1) |
| Xem đã dùng bao nhiêu hôm nay | `/health` mục `daily_cap`, hoặc `SELECT * FROM usage_daily ORDER BY day DESC LIMIT 7;` |

## 6. Dựng lại dữ liệu phái sinh

Chi tiết + số kiểm: `MIGRATE_DATA.md`.

```bash
# Neo4j (miễn phí, ~1 phút) — sau khi đổi dữ liệu MySQL / khôi phục backup
$C run --rm --no-deps -T backend python -m retrieve.build_graph 2>&1 | grep -v "Neo4j Browser"
# Milvus (~4 phút, ~0,05 USD — đếm token trước nếu dữ liệu đổi nhiều; ngưỡng 3M token)
$C run --rm --no-deps -T -v "$PWD/data_processing:/app/data_processing:ro" -w /tmp -e PYTHONPATH=/app \
   backend python -m data_processing.seed_data_batch
# BM25: tự dựng lại khi dữ liệu MySQL đổi (khởi động backend); ép dựng lại:
$C stop backend && docker volume rm chatbot_bm25_cache && $C up -d backend
```

Sau khi dựng lại: `$C restart backend` (collection Milvus, thống kê đồ thị nạp lúc khởi động).

## 7. Đổi bí mật

| Bí mật | Đổi |
|---|---|
| `OPENAI_API_KEY`, `TAVILY_API_KEY` | sửa `.env.prod` → `$C up -d backend` |
| `BACKEND_SHARED_SECRET` | `openssl rand -base64 48 \| tr -d '/+=\n'` → `.env.prod` → `$C up -d backend frontend` |
| `CLOUDFLARE_TUNNEL_TOKEN` | Cloudflare Zero Trust → tunnel → Refresh token → `.env.prod` → `$C up -d cloudflared` |
| `MYSQL_PASSWORD` (user `law_app`) | `ALTER USER 'law_app'@'%' IDENTIFIED BY '…';` (root) → `.env.prod` → `$C up -d backend` |
| `MYSQL_ROOT_PASSWORD`, `NEO4J_PASSWORD`, `MINIO_ROOT_*` | Image chỉ dùng các biến này khi **tạo volume lần đầu** — sửa `.env.prod` thôi KHÔNG đổi mật khẩu thật. Đổi trong dịch vụ trước, rồi mới sửa `.env.prod` + `$C up -d`: MySQL root `ALTER USER 'root'@'%' IDENTIFIED BY '…'; ALTER USER 'root'@'localhost' IDENTIFIED BY '…';` · Neo4j `cypher-shell -u neo4j -p <cũ> -d system "ALTER CURRENT USER SET PASSWORD FROM '<cũ>' TO '<mới>'"` · MinIO: đổi `MINIO_ROOT_*` rồi `$C up -d minio milvus` (Bitnami đặt lại tài khoản root khi khởi động) |
| Firebase service account | Firebase console → Project settings → Service accounts → tạo khoá mới → thay `firebase-credentials.json` (giữ mode 640) → `$C up -d backend` → xoá khoá cũ |

## 8. Sự cố thường gặp

| Triệu chứng | Nguyên nhân / xử lý |
|---|---|
| `docker`: *failed to connect … /var/run/docker.sock: no such file* | Docker Desktop từng bật + WSL integration đã đè rồi xoá socket. Root: `systemctl restart docker.socket docker`. Tắt hẳn Desktop: bỏ "Start when you sign in" + bỏ WSL integration (SETUP_WINDOWS A3). Kiểm: `docker info --format '{{.OperatingSystem}}'` phải là Ubuntu, **không** "Docker Desktop". |
| Site 530 / 1033 (Cloudflare) | cloudflared không chạy hoặc token sai: `$C ps cloudflared`, `$C logs --tail 50 cloudflared` (phải có "Registered tunnel connection"). |
| Site 502 | frontend chết/đang khởi động: `$C ps frontend`, logs. |
| Người dùng nhận "lỗi kết nối" | backend unhealthy: `/health` (mục 2) xem dịch vụ nào lỗi; `$C logs backend`. |
| Mọi câu hỏi báo "đã đủ số câu hỏi hôm nay" | chạm `DAILY_QUESTION_CAP` — đúng thiết kế; đổi trần nếu cần (mục 5). |
| Báo "tạm thời không nhận câu hỏi mới" (503) | MySQL không đếm được trần → kiểm mysql. |
| `[Sentiment] device: cpu` dù có GPU | `docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`; lỗi → NVIDIA Container Toolkit / driver Windows (SETUP_WINDOWS B3, A6). |
| Backend khởi động rất lâu lần đầu | tải model 2.1 GB vào `chatbot_hf_cache` + dựng BM25 (~2 phút). Lần sau dùng cache. |
| Sau khi khởi động lại Windows, site không lên | Task `WSL-Ubuntu-KeepAlive` (SETUP_WINDOWS A4): `Get-ScheduledTask WSL-Ubuntu-KeepAlive \| Get-ScheduledTaskInfo`; đổi mật khẩu Windows → cập nhật task. Trong WSL: `systemctl status docker`. |
| Đĩa đầy | `docker system df`; image GPU ~12 GB/tag (giữ 3); `docker image prune`; build cache: `docker builder prune` (lần build sau tải lại torch ~3 GB). |
| Build image treo ở `pip` tải gói | tuyến tới CDN PyPI (Fastly) chập chờn — dừng và build lại; torch đã lấy từ index PyTorch (Dockerfile). |

## 9. Nghiệm thu khởi động lại (bước 7)

1. Đã thêm `haichatbot.id.vn` vào Firebase Authorized domains (đã làm 16/11).
2. Restart Windows, **không đăng nhập**, chờ 5 phút.
3. Từ điện thoại (4G): mở https://haichatbot.id.vn, đăng nhập, hỏi một câu.
4. Sau đó đăng nhập máy: `uptime -s`, `$C ps` (thời điểm Up trước lúc đăng nhập),
   `tail /home/deploy/deploy/logs/deploy.log`.
