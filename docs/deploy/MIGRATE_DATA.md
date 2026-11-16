# Chuyển dữ liệu sang máy chủ production

Làm ngày **16/11/2026** trên máy chủ WSL, vào volume của compose project `chatbot` (volume
`chatbot_*`). Chỉ bật các service dữ liệu (mysql, neo4j, etcd, minio, milvus) — **không** bật
backend/frontend/cloudflared trong lúc chuyển. Dùng lại quy trình này khi cần dựng lại từ đầu.

Viết tắt dùng bên dưới (user `deploy`, thư mục repo chứa `docker-compose.prod.yml`):

```bash
C="docker compose -f docker-compose.prod.yml --env-file .env.prod"
```

## 1. MySQL — nhập dump

Nguồn: `law.sql.gz` (~19 MB nén, ~150 MB SQL). Bản dùng ngày 16/11:
sha256 `797bc2829289f50ff96dbc37fbd4ffdb998a3c991258abc388be6d4473ee98a1`, kết thúc
`-- Dump completed on 2026-11-16 10:32:01`, đầu file ghi **`Server version 8.0.45`** (máy Mac,
aarch64). Nạp vào `mysql:9.6.0` không lỗi (dump logic; collation `utf8mb4_0900_ai_ci`).

```bash
sha256sum -c law.sql.gz.sha256 && gzip -t law.sql.gz
$C up -d mysql                       # chờ healthy
gunzip -c /home/deploy/data/law.sql.gz \
  | $C exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --default-character-set=utf8mb4'
```

Dump tự `CREATE DATABASE IF NOT EXISTS law` + `USE law`; không có `DEFINER`. User ứng dụng
`law_app` (tạo bởi image mysql từ `MYSQL_USER`/`MYSQL_PASSWORD`) có toàn quyền trên `law`.
Thời gian: ~30 s.

## 2. Neo4j — dựng lại đồ thị từ MySQL (không tốn API)

```bash
$C up -d neo4j
$C build backend
$C run --rm --no-deps -T backend python -m retrieve.build_graph 2>&1 | grep -v "Neo4j Browser"
```

`grep -v` vì dòng cuối của script **in mật khẩu Neo4j**. Thời gian: ~60 s. Xoá & dựng lại toàn
bộ (an toàn khi chạy lại).

## 3. Milvus — nhúng lại tên điều (`text-embedding-3-small`)

Đếm token trước (không gọi API) — tiktoken `cl100k_base` trên `SELECT ten FROM pddieu`:
**2.494.985 token** (66.706 tên, TB 37, dài nhất 348) ≈ **0,05 USD** — dưới ngưỡng 3M
(SURVEY mục 8) nên chạy luôn.

```bash
$C up -d etcd minio milvus
$C run --rm --no-deps -T -v "$PWD/data_processing:/app/data_processing:ro" -w /tmp \
   -e PYTHONPATH=/app backend python -m data_processing.seed_data_batch
```

`data_processing/` không nằm trong image (mount vào). Script **xoá rồi tạo lại** collection
`phapdien_simple_tendieu` (HNSW, COSINE, dim 1536), 334 batch × 200. Thời gian: ~4 phút.
Đã sửa để đọc `MYSQL_*` / `MILVUS_*` từ môi trường (trước hardcode localhost + mật khẩu cũ).

## 4. Kiểm tra (kết quả 16/11/2026)

| Mục | Kỳ vọng (SURVEY mục 6) | Thực tế |
|---|---|---|
| MySQL `pddieu` | 66.706 | **66.706** (MySQL 9.6.0; tiếng Việt đúng) |
| MySQL `pdmuclienquan` / `pdchuong` / `pdfile` | — | 38.435 / 1.583 / 5.774 |
| Neo4j Dieu / Khoan / THAM_CHIEU / LIEN_QUAN | 66.706 / 195.658 / 24.718 / 45.250 | **66.706 / 195.658 / 24.718 / 45.250** (+ VanBan 4.469) |
| Milvus vector | ~66.751 (Mac) | **66.706** — đúng số điều hiện có; collection trên Mac dựng từ dữ liệu cũ hơn |
| BM25 | 68.086 tài liệu | **68.086** (66.706 điều) |
| Known-item 100 câu, hybrid_new Hit@10 | 0,69 | **0,70** (Hit@1 0,28 · Hit@5 0,57 · MRR 0,402) |
| Known-item bm25_only Hit@10 | 0,74 | **0,74** (giống hệt mọi chỉ số → dữ liệu MySQL khớp) |

Kết quả eval: `evaluation/results/nogt_eval_20261116_161621.json`
(`eval_retrieval_nogt.py --synthetic 100 --seed 7 --skip-judge`; không gọi Claude, câu hỏi đã
cache, chỉ nhúng 100 câu truy vấn).

Kiểm nhanh Milvus không tốn API: lấy vector của một điều làm truy vấn → top-1 phải chính là
điều đó (đã làm: "Điều 8.4.LQ.8. Điều kiện kết hôn" → 1,0000; kế tiếp "Đăng ký kết hôn" 0,85).

## 5. MongoDB Atlas

Giữ nguyên (ngoài máy chủ, không chuyển). Database `chatbot_rag`: conversations, memories,
memory_settings, semantic_cache, user_roles, doc_reviews, doc_review_files.* — được
`scripts/backup.sh` dump hằng ngày (gói M0 không có backup tự động).
