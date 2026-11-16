# CI/CD

Quyết định gốc: `docs/deploy/SURVEY.md` mục 3 (repo PUBLIC → không self-hosted runner, không
`deploy.yml`; CD kéo về từ máy chủ).

```
push/PR ──► GitHub Actions: .github/workflows/ci.yml
             backend pytest (torch CPU) · frontend lint+build · build thử 2 image · gitleaks
                                   │ (success trên sha X của main)
máy chủ: systemd timer 5' ──► scripts/deploy.sh
             fetch origin/main = X ≠ đang chạy, chưa từng FAIL
             GitHub API: ci.yml success trên X?  (công khai, không token)
             docker compose build (IMAGE_TAG = X[:12]) → up -d --no-build
             health gate: backend + frontend healthy, https://haichatbot.id.vn trả 2xx/3xx
             FAIL → ghi failed_tag, tự quay về tag trước
```

## CI — `.github/workflows/ci.yml`

| Job | Nội dung |
|---|---|
| backend-test | Python 3.12, torch 2.11.0 CPU, `pip install -r requirements.txt -c requirements.lock.txt`, `pytest` (`HF_HUB_OFFLINE=1`, không gọi OpenAI/Tavily) |
| frontend | Node 20, `npm ci`, `npm run lint`, `npm run build` |
| docker-build | `docker build --build-arg TORCH_VARIANT=cpu .` + smoke test, `docker build frontend` |
| gitleaks | gitleaks 8.30.1 (kiểm sha256), **chỉ quét commit của lần push/PR** — lịch sử cũ có khoá đã lộ (SURVEY mục 9) nên quét toàn bộ sẽ luôn đỏ |

Action ghim theo commit SHA. Chạy khi: push lên `main`, mọi PR, chạy tay.

## CD — thư mục trên máy chủ

```
/home/deploy/deploy/
├── repo/        bản clone GitHub (partial clone), checkout detached đúng sha đang chạy
├── bin -> repo/scripts
├── secrets/
│   ├── .env.prod                 -> /home/deploy/apps/chatbot/.env.prod
│   └── firebase-credentials.json -> /home/deploy/apps/chatbot/firebase-credentials.json
├── state/       current_tag, previous_tag, failed_tag, waiting_ci, history, deploy.lock
└── logs/deploy.log
```

Thư mục làm việc `/home/deploy/apps/chatbot` (branch `deploy/prod`) **không** bị deploy đụng tới.
Compose project luôn tên `chatbot` → volume dữ liệu không đổi giữa các lần deploy.

## Cài đặt (một lần, sau khi các thay đổi deploy đã nằm trên `main`)

`deploy.sh` theo dõi `origin/main`. Hiện các file Docker/compose/CD mới chỉ có trên branch
`deploy/prod` (chưa push/merge) → **chỉ bật timer sau khi merge vào `main` và CI xanh**, và sau
bước 5 (bảo mật) + bước 6 (dữ liệu). Trước đó có thể thử tay (mục "Chạy tay").

**User `deploy`:**

```bash
D=/home/deploy/deploy
mkdir -p $D/secrets
ln -s /home/deploy/apps/chatbot/.env.prod               $D/secrets/.env.prod
ln -s /home/deploy/apps/chatbot/firebase-credentials.json $D/secrets/firebase-credentials.json
git clone --filter=blob:none https://github.com/hainguyen312/project2-chatbot-rag.git $D/repo
git -C $D/repo checkout --detach origin/main
ln -s $D/repo/scripts $D/bin
$D/bin/deploy.sh          # lần đầu: build + up + health gate (xem log)
```

**Root** (`wsl -d Ubuntu -u root`):

```bash
cp /home/deploy/deploy/repo/scripts/systemd/chatbot-deploy.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now chatbot-deploy.timer
systemctl list-timers chatbot-deploy.timer
```

Đổi file unit sau này → copy lại + `systemctl daemon-reload`.

## Vận hành

| Việc | Lệnh |
|---|---|
| Xem log | `tail -f /home/deploy/deploy/logs/deploy.log` · `journalctl -u chatbot-deploy -n 100` |
| Đang chạy tag nào | `cat /home/deploy/deploy/state/current_tag` |
| Deploy ngay (không chờ timer) | `/home/deploy/deploy/bin/deploy.sh` |
| Deploy lại sha từng FAIL | `FORCE=1 /home/deploy/deploy/bin/deploy.sh` |
| Rollback về bản trước | `/home/deploy/deploy/bin/rollback.sh` |
| Rollback về tag cụ thể | `/home/deploy/deploy/bin/rollback.sh <tag>` (tag còn image: `docker image ls chatbot-backend`) |
| Tạm dừng CD | `systemctl stop chatbot-deploy.timer` (root) |

- Rollback đánh dấu tag bị bỏ là `failed_tag` → timer không tự deploy lại sha đó; commit mới trên
  `main` vẫn deploy bình thường.
- Giữ image của 3 tag gần nhất (`KEEP_IMAGES`); image GPU ~12 GB/tag.
- Build cache (gồm wheel torch ~3 GB) không bị xoá tự động. Dọn tay khi cần:
  `docker builder prune` (lần build sau tải lại torch).
- Deploy backend có thời gian gián đoạn ~1–2 phút (nạp model, BM25) — một máy, một bản chạy.

## Chạy tay / thử nghiệm

Mọi cấu hình là biến môi trường (đầu `scripts/lib/deploy_common.sh`). Ví dụ thử từ branch đang
làm, project tách riêng, không kiểm CI, không kiểm URL công khai:

```bash
DEPLOY_DIR=/tmp/cdtest DEPLOY_REPO_URL=/home/deploy/apps/chatbot DEPLOY_BRANCH=deploy/prod \
COMPOSE_PROJECT=chatbot-cdtest SKIP_CI_CHECK=1 PUBLIC_URL= \
ENV_FILE=… FIREBASE_CRED_FILE=… scripts/deploy.sh
```

Test tự động: `tests/test_deploy_script.py` (docker giả + GitHub API giả).
