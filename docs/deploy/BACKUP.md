# Backup & khôi phục

## Cái gì được backup

| Dữ liệu | Cách | File |
|---|---|---|
| MySQL `law` (pháp điển, `usage_daily`) | `mysqldump --single-transaction --set-gtid-purged=OFF` trong container | `mysql-law.sql.gz` |
| MongoDB Atlas `chatbot_rag` (chat, memory, vai trò, rà soát) | `mongodump --archive --gzip` (image `mongo:8.0.34`) | `mongo.archive.gz` |
| Neo4j | **không** — dựng lại từ MySQL (~1 phút, miễn phí) | — |
| Milvus | **không** — nhúng lại từ MySQL (~4 phút, ~0,05 USD) | — |
| Bí mật (`.env.prod`, `firebase-credentials.json`) | **không** — tự giữ trong trình quản lý mật khẩu | — |

Mỗi bản: `/home/deploy/backups/<YYYY-MM-DD_HHMMSS>/` gồm 2 file trên + `sha256sums` +
`manifest.txt` (số điều `pddieu`, db Mongo). Thư mục 700, file 600. Giữ **7 bản** mới nhất
(`KEEP`). Bản lỗi bị xoá, không tính vào 7 bản; script thoát ≠ 0.

## Lịch chạy

Crontab user `deploy` (`crontab -l`): **03:15 hằng ngày** (giờ VN), log
`/home/deploy/backups/backup.log`.

```
15 3 * * * ENV_FILE=/home/deploy/apps/chatbot/.env.prod /home/deploy/apps/chatbot/scripts/backup.sh >> /home/deploy/backups/backup.log 2>&1
```

Sau khi cài CD (`docs/deploy/CD.md`) đổi đường dẫn script sang `/home/deploy/deploy/bin/backup.sh`
(`crontab -e`). Chạy tay bất kỳ lúc nào: cùng lệnh trên (không cần `>>`).

**Nên** chép ra ngoài ổ WSL: đặt `BACKUP_MIRROR_DIR` trong dòng cron, vd
`BACKUP_MIRROR_DIR=/mnt/c/Backups/chatbot` (ổ C:) — tốt hơn nữa là thư mục được OneDrive/Google
Drive đồng bộ để có bản ngoài máy. Script kiểm sha256 sau khi chép và cũng giữ 7 bản ở đó.

## Kiểm tra bản backup

```bash
B=$(ls -d /home/deploy/backups/20* | tail -1)
(cd "$B" && sha256sum -c sha256sums) && cat "$B/manifest.txt"
grep -E "OK|LỖI|THẤT BẠI" /home/deploy/backups/backup.log | tail
```

Đã thử khôi phục ngày 16/11 vào container tạm: MySQL khớp từng bảng (pddieu 66.706,
pdmuclienquan 38.435, pdchuong 1.583, pdfile 5.774, tiếng Việt đúng); Mongo 8 collection khớp số
document với Atlas. **Nên thử khôi phục lại mỗi tháng** (mục "Thử khôi phục" dưới).

## Khôi phục MySQL (production)

> Ghi đè dữ liệu `law` đang chạy. Dừng backend trước để không có ghi giữa chừng.

```bash
cd /home/deploy/apps/chatbot            # hoặc /home/deploy/deploy/repo
C="docker compose -f docker-compose.prod.yml --env-file .env.prod"
B=/home/deploy/backups/<bản>
(cd "$B" && sha256sum -c sha256sums)
$C stop backend
zcat "$B/mysql-law.sql.gz" \
  | $C exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --default-character-set=utf8mb4'
$C exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N law -e "SELECT COUNT(*) FROM pddieu"'
$C start backend
```

Dump chứa `DROP TABLE IF EXISTS` cho từng bảng → nạp đè sạch. Dữ liệu điều thay đổi so với lúc
dựng đồ thị/vector → dựng lại Neo4j và Milvus (RUNBOOK).

## Khôi phục MongoDB Atlas

> `--drop` xoá collection trên Atlas trước khi nạp. Cân nhắc khôi phục vào DB khác trước để
> xem (`--nsFrom 'chatbot_rag.*' --nsTo 'chatbot_rag_restore.*'`).

```bash
B=/home/deploy/backups/<bản>
U=$(sed -n 's/^MONGODB_URI=//p' /home/deploy/apps/chatbot/.env.prod | tail -1)
MONGO_URI="$U" docker run --rm -e MONGO_URI -v "$B:/in:ro" mongo:8.0.34 \
  sh -c 'mongorestore --uri="$MONGO_URI" --archive=/in/mongo.archive.gz --gzip --drop'
unset U
```

## Thử khôi phục (không đụng production)

```bash
B=$(ls -d /home/deploy/backups/20* | tail -1); P=$(openssl rand -hex 12)
docker network create rst
docker run -d --rm --name rst-mysql --network rst -e MYSQL_ROOT_PASSWORD=$P mysql:9.6.0
docker run -d --rm --name rst-mongo --network rst mongo:8.0.34
# chờ ~30 s cho mysql
zcat "$B/mysql-law.sql.gz" | docker exec -i rst-mysql sh -c "MYSQL_PWD=$P mysql -uroot --default-character-set=utf8mb4"
docker exec rst-mysql sh -c "MYSQL_PWD=$P mysql -uroot -N law -e 'SELECT COUNT(*) FROM pddieu'"   # 66706
docker cp "$B/mongo.archive.gz" rst-mongo:/tmp/m.gz
docker exec rst-mongo sh -c 'mongorestore --archive=/tmp/m.gz --gzip --quiet && mongosh --quiet --eval "db.getSiblingDB(\"chatbot_rag\").getCollectionNames()"'
docker rm -f rst-mysql rst-mongo; docker network rm rst
```

Test tự động của script: `tests/test_backup_script.py`.
