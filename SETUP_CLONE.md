# Hướng dẫn chạy từ đầu đến cuối — Lên giao diện

Hướng dẫn tuần tự từ bước đầu đến khi mở được giao diện chatbot hỏi đáp pháp luật.

---

## Yêu cầu trước khi bắt đầu

| Thành phần | Yêu cầu |
|------------|---------|
| Python | >= 3.10 |
| Docker & Docker Compose | Đã cài đặt và chạy được |
| OPENAI_API_KEY | API key OpenAI (embedding + LLM) |

---

## Bước 0 — Clone và chuẩn bị môi trường

```bash
# Di chuyển vào thư mục project
cd /đường/dẫn/đến/20215315_DoThiThanhBinh_20251

# Tạo virtualenv (khuyến nghị)
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

# Cấu hình API key
export OPENAI_API_KEY="sk-proj-xxx-của-bạn"

# Tạo file .env (hoặc sử dụng export như trên)
echo "OPENAI_API_KEY=sk-proj-xxx" > .env
echo "OPENAI_MODEL=gpt-4o-mini" >> .env
```

---

## Bước 1 — Khởi động MySQL

```bash
cd law-crawler
docker compose up -d
```

**Kiểm tra:**
- `docker ps` — thấy container `law-crawler-law-mysql-1` (hoặc tương tự)
- Truy cập phpMyAdmin: http://localhost:8081 (user: `root`, pass: `123456789`)

---

## Bước 2 — Chuẩn bị dữ liệu Pháp Điển

1. Vào [Pháp Điển Việt Nam](https://phapdien.moj.gov.vn/), tải dữ liệu (file zip hoặc export).
2. Giải nén vào `law-crawler/phap-dien/`.
3. Đảm bảo cấu trúc thư mục:

```
law-crawler/phap-dien/
├── chude.json
├── demuc.json
├── treeNode.json
└── demuc/
    ├── 1.html      (hoặc 1, 2, 3... tùy cách đặt tên)
    ├── 2.html
    └── ...
```

- Nếu dữ liệu gốc có `jsonData.json`, cần tách ra thành `chude.json`, `demuc.json`, `treeNode.json` theo cấu trúc mà crawler cần.

---

## Bước 3 — Cài đặt và chạy Crawler

```bash
# Vẫn trong thư mục law-crawler
pip install -r requirements.txt
python main.py
```

**Kết quả mong đợi:** In ra log "Insert tất cả chủ đề...", "Inserted tất cả nodes pháp điển!", v.v.

**Kiểm tra:** Vào phpMyAdmin → database `law` → xem các bảng `pdchude`, `pddemuc`, `pdchuong`, `pddieu` có dữ liệu.

---

## Bước 4 — Khởi động Milvus

```bash
# Quay về thư mục gốc project
cd ..

# Vào test_raptor và khởi động Milvus
cd test_raptor
docker compose up -d
cd ..
```

**Chờ 1–2 phút** để Milvus khởi động xong.

**Kiểm tra:** `docker ps` — thấy `milvus-standalone`, `milvus-etcd`, `milvus-minio`.

---

## Bước 5 — Cài đặt dependencies và Embed dữ liệu

```bash
# Ở thư mục gốc project
pip install -r requirements.txt

# Đảm bảo OPENAI_API_KEY đã được set
# export OPENAI_API_KEY="sk-xxx"   # nếu chưa set

cd data_processing
python seed_milvus_from_mysql.py
cd ..
```

**Kết quả mong đợi:** In ra "Embedding batch...", "Inserted... records", "Done. Tổng ... bản ghi đã đưa vào Milvus."

---

## Bước 6 — Chạy giao diện Streamlit

```bash
# Ở thư mục gốc project
streamlit run app.py
```

**Kết quả:** Mở trình duyệt tự động hoặc truy cập **http://localhost:8501**

---

## Tóm tắt lệnh (chạy tuần tự)

```bash
# 0. Chuẩn bị
cd /đường/dẫn/project
python -m venv .venv && source .venv/bin/activate   # hoặc .venv\Scripts\activate trên Windows
export OPENAI_API_KEY="sk-xxx"

# 1. MySQL
cd law-crawler && docker compose up -d

# 2. Chuẩn bị phap-dien/ (làm thủ công)

# 3. Crawl
pip install -r requirements.txt && python main.py

# 4. Milvus
cd ../test_raptor && docker compose up -d && cd ..

# 5. Embed
pip install -r requirements.txt
cd data_processing && python seed_milvus_from_mysql.py && cd ..

# 6. Giao diện
streamlit run app.py
```

---

## Khắc phục sự cố

| Lỗi | Cách xử lý |
|-----|------------|
| **MySQL connection refused** | Kiểm tra `docker ps`, chạy lại `docker compose up -d` trong `law-crawler` |
| **Milvus connection refused** | Chờ 2 phút sau khi `docker compose up`; kiểm tra `docker logs milvus-standalone` |
| **Không tìm thấy phap-dien** | Đảm bảo thư mục `law-crawler/phap-dien/` tồn tại và có đủ `chude.json`, `demuc.json`, `treeNode.json`, `demuc/*` |
| **Tổng số điều từ MySQL: 0** | Chạy lại `python main.py` trong `law-crawler`; kiểm tra log và dữ liệu trong `phap-dien/` |
| **Collection không tồn tại** | Chạy lại `python seed_milvus_from_mysql.py` sau khi MySQL đã có dữ liệu |
| **Module not found** | Chạy `pip install -r requirements.txt` ở thư mục gốc |

---

## Biến môi trường (.env)

Tạo file `.env` ở thư mục gốc project:

```env
OPENAI_API_KEY=sk-proj-xxx
OPENAI_MODEL=gpt-4o-mini
```

Nếu MySQL/Milvus chạy ở host/port khác:

```env
MYSQL_URL=mysql+mysqlconnector://root:123456789@localhost:3306/law
MILVUS_HOST=localhost
MILVUS_PORT=19530
```  
