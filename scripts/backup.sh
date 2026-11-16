#!/usr/bin/env bash
# Backup hằng ngày (cron user deploy, xem docs/deploy/RUNBOOK.md):
#   - MySQL `law` (dữ liệu pháp điển + usage_daily)  → mysql-law.sql.gz      (mysqldump trong container)
#   - MongoDB Atlas (chat, memory, vai trò, rà soát) → mongo.archive.gz      (mongodump, image mongo)
#   - sha256sums + manifest.txt (số dòng pddieu, kích thước)
# Neo4j và Milvus KHÔNG backup: dựng lại từ MySQL (RUNBOOK "Dựng lại Neo4j / Milvus").
#
# Giữ KEEP bản gần nhất (mặc định 7). BACKUP_MIRROR_DIR (tuỳ chọn) → chép thêm sang thư mục khác
# (vd ổ Windows /mnt/c/Backups/chatbot), cũng giữ KEEP bản.
#
# Biến: BACKUP_DIR (/home/deploy/backups), ENV_FILE (lấy MONGODB_URI/MONGODB_DB),
#       COMPOSE_PROJECT (chatbot), KEEP (7), BACKUP_MIRROR_DIR, MONGO_IMAGE, SKIP_MONGO (0)
# Mã thoát ≠ 0 nếu phần nào lỗi; bản backup lỗi bị xoá (không tính vào KEEP).

set -Eeuo pipefail

: "${BACKUP_DIR:=/home/deploy/backups}"
: "${ENV_FILE:=/home/deploy/deploy/secrets/.env.prod}"
: "${COMPOSE_PROJECT:=chatbot}"
: "${KEEP:=7}"
: "${BACKUP_MIRROR_DIR:=}"
: "${MONGO_IMAGE:=mongo:8.0.34}"
: "${SKIP_MONGO:=0}"

log() { printf '%s [backup] %s\n' "$(date '+%F %T')" "$*"; }

container() {  # container của service trong project compose (không cần file compose)
  docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
    --filter "label=com.docker.compose.service=$1" | head -n1
}

# Đọc 1 biến từ ENV_FILE mà không source cả file (không in giá trị).
env_get() {
  [ -r "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1 | sed -E 's/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'
}

# Lưu ý: hàm được gọi dạng `hàm || rc=1` → bash TẮT set -e bên trong; mọi lệnh phải tự `|| return 1`.
backup_mysql() {
  local out="$1/mysql-law.sql.gz" cid n
  cid="$(container mysql)"
  [ -n "$cid" ] || { log "LỖI: không thấy container mysql của project $COMPOSE_PROJECT"; return 1; }
  docker exec "$cid" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --routines --triggers --events --hex-blob --set-gtid-purged=OFF --default-character-set=utf8mb4 --databases law' \
    | gzip -6 >"$out" || { log "LỖI: mysqldump thất bại"; return 1; }
  gzip -t "$out" || { log "LỖI: file dump MySQL hỏng"; return 1; }
  zcat "$out" | tail -n1 | grep -q '^-- Dump completed' || { log "LỖI: dump MySQL không có dòng kết thúc"; return 1; }
  n="$(docker exec "$cid" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B law -e "SELECT COUNT(*) FROM pddieu"')" \
    || { log "LỖI: không đếm được pddieu"; return 1; }
  printf 'mysql pddieu=%s\n' "$n" >>"$1/manifest.txt"
  log "MySQL OK ($(du -h "$out" | cut -f1), pddieu=$n)"
}

backup_mongo() {
  local dir="$1" uri db
  if [ "$SKIP_MONGO" = 1 ]; then log "SKIP_MONGO=1 — bỏ qua MongoDB"; return 0; fi
  uri="$(env_get MONGODB_URI)"; db="$(env_get MONGODB_DB)"
  [ -n "$uri" ] || { log "LỖI: không có MONGODB_URI trong $ENV_FILE"; return 1; }
  docker image inspect "$MONGO_IMAGE" >/dev/null 2>&1 || docker pull -q "$MONGO_IMAGE" >/dev/null \
    || { log "LỖI: không tải được $MONGO_IMAGE"; return 1; }
  # URI qua biến môi trường (không lộ trên dòng lệnh / ps); ghi thẳng vào thư mục backup.
  MONGO_URI="$uri" docker run --rm -e MONGO_URI -v "$dir:/out" --user "$(id -u):$(id -g)" \
    "$MONGO_IMAGE" sh -c 'exec mongodump --uri="$MONGO_URI" '"${db:+--db=$db}"' --archive=/out/mongo.archive.gz --gzip --quiet' \
    || { log "LỖI: mongodump thất bại"; return 1; }
  gzip -t "$dir/mongo.archive.gz" || { log "LỖI: file mongo.archive.gz hỏng/thiếu"; return 1; }
  printf 'mongo db=%s bytes=%s\n' "${db:-(tất cả)}" "$(stat -c %s "$dir/mongo.archive.gz")" >>"$dir/manifest.txt"
  log "MongoDB OK ($(du -h "$dir/mongo.archive.gz" | cut -f1))"
}

prune() {  # giữ $KEEP thư mục ngày mới nhất trong $1
  local root="$1"
  [ -d "$root" ] || return 0
  find "$root" -mindepth 1 -maxdepth 1 -type d -name '20??-??-??_*' | sort -r | tail -n +"$((KEEP + 1))" |
    while read -r old; do log "Xoá bản cũ $old"; rm -rf -- "$old"; done
}

main() {
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  exec 9>"$BACKUP_DIR/.lock"
  flock -n 9 || { log "Đang có backup khác chạy — bỏ qua"; exit 0; }

  local name dir tmp rc=0
  name="$(date +%F_%H%M%S)"
  dir="$BACKUP_DIR/$name"
  tmp="$BACKUP_DIR/.tmp-$name"
  mkdir -m 700 "$tmp"
  printf 'backup %s host=%s\n' "$name" "$(hostname)" >"$tmp/manifest.txt"

  backup_mysql "$tmp" || rc=1
  backup_mongo "$tmp" || rc=1

  if [ "$rc" -ne 0 ]; then
    log "Backup $name THẤT BẠI — xoá bản dở"
    rm -rf -- "$tmp"
    exit 1
  fi
  (cd "$tmp" && sha256sum -- *.gz >sha256sums)
  chmod 600 "$tmp"/*
  mv "$tmp" "$dir"
  log "Backup OK → $dir"
  prune "$BACKUP_DIR"

  if [ -n "$BACKUP_MIRROR_DIR" ]; then
    if mkdir -p "$BACKUP_MIRROR_DIR" && cp -r "$dir" "$BACKUP_MIRROR_DIR/" &&
       (cd "$BACKUP_MIRROR_DIR/$name" && sha256sum -c --quiet sha256sums); then
      log "Đã chép sang $BACKUP_MIRROR_DIR/$name"
      prune "$BACKUP_MIRROR_DIR"
    else
      log "LỖI: chép sang BACKUP_MIRROR_DIR=$BACKUP_MIRROR_DIR thất bại"
      exit 1
    fi
  fi
}

{ main "$@"; exit $?; }
