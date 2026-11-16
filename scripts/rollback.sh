#!/usr/bin/env bash
# Quay stack về một tag đã build trước đó (image còn trên máy).
#
#   scripts/rollback.sh            về previous_tag
#   scripts/rollback.sh <tag>      về tag cụ thể (12 ký tự sha; xem: docker image ls chatbot-backend)
#
# Tag đang chạy (bị bỏ) được ghi vào state/failed_tag → deploy.sh không tự deploy lại sha đó;
# commit mới trên main sẽ được deploy bình thường. Muốn deploy lại sha đó: FORCE=1 deploy.sh.

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/deploy_common.sh
. "$SCRIPT_DIR/lib/deploy_common.sh"

rollback() {
  check_secrets
  ensure_repo
  local target="${1:-$(state_get previous_tag)}" current
  current="$(state_get current_tag)"
  [ -n "$target" ] || die "không có previous_tag — chỉ rõ tag: rollback.sh <tag>"
  [ "$target" != "$current" ] || die "$target đang chạy rồi"
  images_exist "$target" || die "không còn image chatbot-backend/frontend:$target trên máy"
  git -C "$REPO" cat-file -e "$target^{commit}" 2>/dev/null \
    || git -C "$REPO" fetch --quiet origin "$DEPLOY_BRANCH" \
    || true
  git -C "$REPO" cat-file -e "$target^{commit}" 2>/dev/null || die "repo không có commit $target"

  log "=== Rollback ${current:-(không rõ)} → $target"
  if activate "$target"; then
    [ -n "$current" ] && state_set failed_tag "$current"
    record_success "$target"
    log "=== Rollback về $target OK"
  else
    die "Rollback về $target FAIL — xem log, RUNBOOK"
  fi
}

main() {
  init_dirs
  with_lock rollback "$@"
}

{ main "$@"; exit $?; }
