# shellcheck shell=bash
# Hàm dùng chung cho scripts/deploy.sh và scripts/rollback.sh. Không chạy trực tiếp.
#
# Cấu hình qua biến môi trường (mặc định cho máy chủ production):
#   DEPLOY_DIR          /home/deploy/deploy        (repo/, state/, logs/, secrets/)
#   DEPLOY_REPO_URL     origin của repo chứa script (GitHub, public)
#   DEPLOY_BRANCH       main
#   ENV_FILE            $DEPLOY_DIR/secrets/.env.prod
#   FIREBASE_CRED_FILE  $DEPLOY_DIR/secrets/firebase-credentials.json
#   COMPOSE_PROJECT     chatbot
#   PUBLIC_URL          https://haichatbot.id.vn   (rỗng = bỏ kiểm URL công khai)
#   HEALTH_TIMEOUT      600   giây chờ backend + frontend healthy (lần đầu tải model 2.1 GB)
#   PUBLIC_TIMEOUT      120   giây chờ URL công khai trả 2xx/3xx
#   HEALTH_INTERVAL     5
#   KEEP_IMAGES         3     số tag image giữ lại
#   GITHUB_API          https://api.github.com
#   CI_WORKFLOW         ci.yml
#   GITHUB_REPO         owner/repo (mặc định suy từ DEPLOY_REPO_URL)
#   SKIP_CI_CHECK       0     (1 = bỏ kiểm CI — chỉ dùng khi thử nghiệm)

: "${DEPLOY_DIR:=/home/deploy/deploy}"
: "${DEPLOY_BRANCH:=main}"
: "${ENV_FILE:=$DEPLOY_DIR/secrets/.env.prod}"
: "${FIREBASE_CRED_FILE:=$DEPLOY_DIR/secrets/firebase-credentials.json}"
: "${COMPOSE_PROJECT:=chatbot}"
: "${PUBLIC_URL=https://haichatbot.id.vn}"   # rỗng = bỏ kiểm (dùng "=" không phải ":=")
: "${HEALTH_TIMEOUT:=600}"
: "${PUBLIC_TIMEOUT:=120}"
: "${HEALTH_INTERVAL:=5}"
: "${KEEP_IMAGES:=3}"
: "${GITHUB_API:=https://api.github.com}"
: "${CI_WORKFLOW:=ci.yml}"
: "${SKIP_CI_CHECK:=0}"

REPO="$DEPLOY_DIR/repo"
STATE="$DEPLOY_DIR/state"
LOGDIR="$DEPLOY_DIR/logs"
LOG="$LOGDIR/deploy.log"
LOCK="$STATE/deploy.lock"
export FIREBASE_CRED_FILE

log() {
  local line
  line="$(date '+%F %T') [$(basename "$0" .sh)] $*"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >>"$LOG"
}

die() { log "LỖI: $*"; exit 1; }

init_dirs() {
  mkdir -p "$STATE" "$LOGDIR" "$DEPLOY_DIR/secrets"
  # Giữ log gọn: tối đa ~5000 dòng.
  if [ -f "$LOG" ] && [ "$(wc -l <"$LOG")" -gt 6000 ]; then
    tail -n 5000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  fi
}

# Chạy hàm với khoá flock; đang có tiến trình khác giữ khoá → thoát 0 (timer chạy lại sau).
with_lock() {
  exec 9>"$LOCK"
  if ! flock -n 9; then
    log "Đang có deploy/rollback khác chạy — bỏ qua lượt này"
    exit 0
  fi
  "$@"
}

check_secrets() {
  [ -r "$ENV_FILE" ] || die "không đọc được ENV_FILE=$ENV_FILE"
  [ -r "$FIREBASE_CRED_FILE" ] || die "không đọc được FIREBASE_CRED_FILE=$FIREBASE_CRED_FILE"
}

repo_url() {
  if [ -n "${DEPLOY_REPO_URL:-}" ]; then printf '%s\n' "$DEPLOY_REPO_URL"; return; fi
  git -C "$(dirname "${BASH_SOURCE[0]}")/../.." remote get-url origin
}

# owner/repo từ URL GitHub (https://github.com/o/r(.git) hoặc git@github.com:o/r(.git)).
github_repo() {
  if [ -n "${GITHUB_REPO:-}" ]; then printf '%s\n' "$GITHUB_REPO"; return; fi
  repo_url | sed -nE 's#^(https://github\.com/|git@github\.com:)([^/]+/[^/]+)$#\2#p' | sed -E 's/\.git$//'
}

ensure_repo() {
  if [ ! -d "$REPO/.git" ]; then
    log "Clone $(repo_url) → $REPO"
    git clone --quiet --filter=blob:none --no-checkout "$(repo_url)" "$REPO"
  fi
}

# compose <IMAGE_TAG> <lệnh compose…> — luôn --env-file, project cố định.
compose() {
  local tag="$1"; shift
  IMAGE_TAG="$tag" docker compose -p "$COMPOSE_PROJECT" \
    -f "$REPO/docker-compose.prod.yml" --env-file "$ENV_FILE" "$@"
}

checkout() { git -C "$REPO" checkout --quiet --force --detach "$1"; }

state_get() { cat "$STATE/$1" 2>/dev/null || true; }
state_set() { printf '%s\n' "$2" >"$STATE/$1.tmp" && mv "$STATE/$1.tmp" "$STATE/$1"; }

# CI (workflow ci.yml) đã success trên đúng sha? GitHub API công khai, không token.
ci_success() {
  local sha="$1" gh n
  [ "$SKIP_CI_CHECK" = 1 ] && { log "SKIP_CI_CHECK=1 — bỏ kiểm CI"; return 0; }
  gh="$(github_repo)"
  [ -n "$gh" ] || { log "Không suy ra được owner/repo GitHub từ $(repo_url)"; return 1; }
  n="$(curl -fsS --max-time 20 -H 'Accept: application/vnd.github+json' \
        "$GITHUB_API/repos/$gh/actions/workflows/$CI_WORKFLOW/runs?head_sha=$sha&status=success&per_page=1" \
        | jq -r '.total_count // 0')" || { log "Gọi GitHub API thất bại"; return 1; }
  [ "${n:-0}" -gt 0 ]
}

container_health() {
  local tag="$1" svc="$2" cid
  cid="$(compose "$tag" ps -q "$svc" 2>/dev/null | head -n1)"
  [ -n "$cid" ] || { echo missing; return; }
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo missing
}

# Health gate: backend + frontend healthy (HEALTHCHECK → /health thật) rồi URL công khai.
health_gate() {
  local tag="$1" deadline b f code
  deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  while :; do
    b="$(container_health "$tag" backend)"; f="$(container_health "$tag" frontend)"
    [ "$b" = healthy ] && [ "$f" = healthy ] && break
    if [ "$b" = unhealthy ] || [ "$f" = unhealthy ] || [ "$(date +%s)" -ge "$deadline" ]; then
      log "Health gate FAIL: backend=$b frontend=$f"
      return 1
    fi
    sleep "$HEALTH_INTERVAL"
  done
  log "backend + frontend healthy"
  [ -z "$PUBLIC_URL" ] && return 0
  deadline=$(( $(date +%s) + PUBLIC_TIMEOUT ))
  while :; do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$PUBLIC_URL" 2>/dev/null || true)"
    case "$code" in 2??|3??) log "URL công khai $PUBLIC_URL → $code"; return 0 ;; esac
    if [ "$(date +%s)" -ge "$deadline" ]; then
      log "Health gate FAIL: $PUBLIC_URL → ${code:-không kết nối}"
      return 1
    fi
    sleep "$HEALTH_INTERVAL"
  done
}

images_exist() {
  docker image inspect "chatbot-backend:$1" "chatbot-frontend:$1" >/dev/null 2>&1
}

# Đưa stack về tag (image phải còn). Trả 0 nếu qua health gate.
activate() {
  local tag="$1"
  checkout "$tag" || { log "Không checkout được $tag"; return 1; }
  compose "$tag" up -d --no-build --remove-orphans || { log "compose up thất bại ($tag)"; return 1; }
  health_gate "$tag"
}

record_success() {
  local tag="$1" prev
  prev="$(state_get current_tag)"
  [ -n "$prev" ] && [ "$prev" != "$tag" ] && state_set previous_tag "$prev"
  state_set current_tag "$tag"
  printf '%s %s\n' "$(date '+%F %T')" "$tag" >>"$STATE/history"
}

# Giữ KEEP_IMAGES tag gần nhất (theo state/history) + tag đang chạy; xoá image cũ hơn.
prune_images() {
  local keep repo t
  keep="$( { tac "$STATE/history" 2>/dev/null | awk '{print $NF}' | awk '!seen[$0]++' | head -n "$KEEP_IMAGES"; state_get current_tag; } | sort -u)"
  for repo in chatbot-backend chatbot-frontend; do
    docker image ls "$repo" --format '{{.Tag}}' 2>/dev/null | while read -r t; do
      [ -z "$t" ] && continue
      printf '%s\n' "$keep" | grep -qxF "$t" && continue
      log "Xoá image cũ $repo:$t"
      docker image rm "$repo:$t" >/dev/null 2>&1 || true
    done
  done
  docker image prune -f >/dev/null 2>&1 || true
}
