#!/usr/bin/env bash
# CD pull-based — chạy bởi systemd timer (chatbot-deploy.timer) mỗi 5 phút, user deploy.
#
#   1. fetch origin/$DEPLOY_BRANCH; sha mới ≠ sha đang chạy, chưa từng FAIL
#   2. CI (ci.yml) đã success trên đúng sha (GitHub API công khai)
#   3. docker compose build (IMAGE_TAG = 12 ký tự đầu của sha) → up -d
#   4. health gate: backend + frontend healthy, URL công khai trả 2xx/3xx
#   5. FAIL → đánh dấu sha lỗi, tự quay về tag trước (rollback)
#
# Không có thay đổi → thoát im lặng. Cấu hình + biến môi trường: scripts/lib/deploy_common.sh.
#   FORCE=1 scripts/deploy.sh   deploy lại kể cả sha từng FAIL / đang chạy
#
# Toàn bộ logic nằm trong hàm, `main` gọi ở dòng cuối: git checkout có thể thay file này khi
# đang chạy, bash đã đọc xong các hàm trước đó.

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/deploy_common.sh
. "$SCRIPT_DIR/lib/deploy_common.sh"

deploy() {
  check_secrets
  ensure_repo
  git -C "$REPO" fetch --quiet --force origin "$DEPLOY_BRANCH"
  local sha tag current failed
  sha="$(git -C "$REPO" rev-parse FETCH_HEAD)"
  tag="${sha:0:12}"
  current="$(state_get current_tag)"
  failed="$(state_get failed_tag)"

  if [ "${FORCE:-0}" != 1 ]; then
    [ "$tag" = "$current" ] && return 0
    [ "$tag" = "$failed" ] && return 0
  fi
  if ! ci_success "$sha"; then
    [ "$(state_get waiting_ci)" = "$tag" ] || log "Có $tag mới nhưng CI chưa success — chờ"
    state_set waiting_ci "$tag"
    return 0
  fi

  log "=== Deploy ${current:-(chưa có)} → $tag"
  checkout "$sha"
  if ! compose "$tag" build >>"$LOG" 2>&1; then
    log "Build $tag thất bại — giữ nguyên $current"
    state_set failed_tag "$tag"
    checkout "${current:-$sha}" 2>/dev/null || true
    return 1
  fi
  if activate "$tag"; then
    record_success "$tag"
    rm -f "$STATE/failed_tag" "$STATE/waiting_ci"
    prune_images
    log "=== Deploy $tag OK"
    return 0
  fi

  state_set failed_tag "$tag"
  if [ -n "$current" ] && images_exist "$current"; then
    log "Rollback về $current"
    if activate "$current"; then
      log "=== Deploy $tag FAIL — đã rollback về $current (OK)"
    else
      log "=== Deploy $tag FAIL — rollback về $current cũng FAIL, cần xử lý tay (RUNBOOK)"
    fi
  else
    log "=== Deploy $tag FAIL — không có bản trước để rollback, cần xử lý tay"
  fi
  return 1
}

main() {
  init_dirs
  with_lock deploy
}

{ main "$@"; exit $?; }
