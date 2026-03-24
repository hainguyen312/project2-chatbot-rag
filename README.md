# README — Hướng dẫn triển khai hệ thống

## 1. Tổng quan
Đồ án xây dựng một hệ thống hỏi – đáp pháp luật tiếng Việt dựa trên kiến trúc Retrieval-Augmented Generation (RAG), nhằm hỗ trợ người dùng tra cứu, tìm hiểu và hỏi đáp thông tin pháp luật một cách chính xác và thuận tiện.

Hệ thống sử dụng nguồn dữ liệu Pháp Điển Việt Nam làm dữ liệu đầu vào, thực hiện quy trình:

-   Crawl và chuẩn hoá dữ liệu pháp luật

-   Lưu trữ dữ liệu cấu trúc trong MySQL

-   Sinh embedding và lưu trữ vector trong Milvus

-   Truy xuất ngữ nghĩa kết hợp mô hình ngôn ngữ lớn (LLM) để sinh câu trả lời

Kiến trúc hệ thống được thiết kế theo từng giai đoạn độc lập:

-   Tầng dữ liệu quan hệ (MySQL): lưu nội dung pháp luật đã crawl

-   Tầng vector (Milvus): lưu embedding phục vụ tìm kiếm ngữ nghĩa

-   Tầng xử lý (Python + RAG): truy xuất, xếp hạng và tổng hợp thông tin

-   Tầng giao diện (Streamlit): cung cấp giao diện hỏi – đáp cho người dùng

Hệ thống cho phép:

-   Truy vấn thông tin pháp luật theo ngữ nghĩa, không phụ thuộc từ khoá cứng

-   Mở rộng dữ liệu dễ dàng bằng cách crawl hoặc seed batch

-   Tách biệt rõ ràng giữa xử lý dữ liệu, lưu trữ và hiển thị

> Lưu ý:  
> - Docker **chỉ dùng cho database (MySQL, Milvus)**  
> - **Toàn bộ code Python chạy trên host bằng venv**  
> - **Chi tiết clone từ đầu:** xem [SETUP_CLONE.md](SETUP_CLONE.md)  

---

## 2. Yêu cầu môi trường
- Python >= 3.10
- Docker & Docker Compose
- Chạy trên Linux / macOS / Windows

---

### Khởi chạy venv
```bash
    python -m venv .venv
    # Windows
    source .venv\Scripts\activate
    # Linux / macOS
    source .venv/bin/activate
```

### Cấu hình các giá trị api_key
export OPENAI_API_KEY="YOUR_OPENAI_KEY"
# Windows PowerShell:
# setx OPENAI_API_KEY "YOUR_OPENAI_KEY"

## 3. Chuẩn bị dữ liệu

Quy trình này sẽ cào dữ liệu pháp luật từ [Pháp Điển Việt Nam](https://phapdien.moj.gov.vn/). Đây là bước khởi đầu để xây dựng database cho hệ thống nhằm phục vụ quy trình RAG. Nếu việc crawl và embed quá tốn thời gian và chi phí, bạn có thể thử sử dụng bộ dữ liệu nhỏ có sẵn trong thư mục /data_processing và lưu vào elasticsearch để chạy, tuy nhiên dữ liệu có sẵn chỉ là 1 phần nhỏ làm mẫu thử cho hệ thống.

Lấy dữ liệu từ [Pháp Điển Việt Nam](https://phapdien.moj.gov.vn/), tải file zip và giải nén vào thư mục này.

### Cào dữ liệu pháp điển

-   Tạo 2 file json từ file jsonData.json gốc:
    -   chude.json: chứa các chủ đề
    -   demuc.json: chứa các đề mục
    -   treeNode: chứa các node là các Phần, Chương, Mục, Tiểu mục, Điều.
-   Cuối cùng thư mục của bạn sẽ có cấu trúc như sau:

```
phap-dien
├── chude.json
├── demuc.json
├── treeNode.json
├── demuc/
│   ├── 1/...
│   ├── 2/...
```

-   Chuyển đến thư mục `law_crawler/` từ thư mục chính của dự án bằng câu lệnh: 

```bash 
    cd law_crawler
```

-   Cài đặt các thư viện cần thiết:

```bash
    pip install -r requirements.txt
```

-   Chạy MySQL và PHPMyAdmin containers từ docker-compose:
```bash
    docker compose up -d
```

Thông tin MySQL:
    Host: localhost
    Port: 3306
    Database: law
    User: root
    Password: 123456789
    phpMyAdmin: http://localhost:8081

-   Chạy crawler:

```bash
    python main.py
```

Sau khi chạy xong, dữ liệu sẽ được lưu vào DB, bạn có thể export ra bằng PHPAdmin dưới dạng .sql để dùng lại.

### 4. Embed dữ liệu để lưu vào cơ sở Milvus

-   Trở lại thư mục chính của dự án

```bash
    cd ..
```

-   Cài đặt các thư viện cần thiết (nếu chưa có):

```bash
    pip install -r requirements.txt
```

-   Khởi động Milvus (Docker)

```bash
    cd test_raptor
    docker compose up -d
    cd ..
```

Các service:
    Milvus gRPC: localhost:19530
    Milvus health: localhost:9091
    MinIO API: localhost:9000
    MinIO Console: localhost:9001

-   Embed dữ liệu từ MySQL rồi lưu Milvus

```bash
    cd data_processing
    python seed_milvus_from_mysql.py
```

Script `seed_milvus_from_mysql.py` đọc toàn bộ điều từ MySQL, embed tên điều bằng OpenAI, và lưu vào collection `phapdien_tendieu`. Cần cấu hình `OPENAI_API_KEY` trước khi chạy.

> **Lưu ý:** Nếu muốn dùng dữ liệu mẫu từ `data.json` (không qua MySQL), dùng `python seed_data_batch.py` thay thế — khi đó collection tạo ra là `phapdien_simple_tendieu` và cần chỉnh sửa code retrieve cho phù hợp.

Như vậy, data đã được embed và lưu vào Milvus sẵn sàng phục vụ cho giai đoạn truy xuất thông tin.

### 5. Chạy hệ thống Streamlit

-   Chuyển về thư mục root project:

-   Cài đặt các thư viện cần thiết:

```bash
    pip install -r requirements.txt
```

-   Chạy streamlit cho hệ thống:

```bash
    streamlit run app.py
```

Truy cập ứng dụng tại: http://localhost:8501
