# Biểu đồ UML — Chatbot RAG hỏi đáp pháp luật

Các biểu đồ viết bằng PlantUML (`*.puml`, dùng chung style trong `_style.iuml`).

| File | Loại | Nội dung |
|------|------|----------|
| `01_usecase_tong_quat.puml` | Use case | Tổng quát toàn hệ thống (UC01–UC15) |
| `02_usecase_hoi_dap.puml` | Use case | Phân rã UC01 Hỏi đáp pháp luật |
| `03_usecase_tai_khoan_hoi_thoai.puml` | Use case | Tài khoản, hội thoại, bộ nhớ cá nhân |
| `04_usecase_quan_tri.puml` | Use case | Quản trị dữ liệu và đánh giá |
| `10_seq_hoi_dap_phap_luat.puml` | Tuần tự | UC01 Hỏi đáp (SSE, pre-retrieve, cache, agent loop) |
| `11_seq_agent_tools.puml` | Tuần tự | Thực thi tool: vector / graph / full article / web |
| `12_seq_cap_nhat_bo_nho.puml` | Tuần tự | Cập nhật bộ nhớ dài hạn (ADD/UPDATE/DELETE/NOOP) |
| `13_seq_dang_nhap.puml` | Tuần tự | UC10 Đăng nhập + đồng bộ dữ liệu ẩn danh |
| `14_seq_quan_ly_hoi_thoai.puml` | Tuần tự | UC07 Quản lý hội thoại (CRUD) |
| `15_seq_giong_noi.puml` | Tuần tự | UC04 STT và UC05 TTS |
| `16_seq_bo_nho_ca_nhan.puml` | Tuần tự | UC08 Xem / xoá bộ nhớ cá nhân |
| `17_seq_danh_gia_cau_tra_loi.puml` | Tuần tự | UC06 Đánh giá câu trả lời |
| `18_seq_xay_dung_du_lieu.puml` | Tuần tự | UC12–UC14 Crawl → MySQL → Milvus → Neo4j |
| `20_activity_xu_ly_cau_hoi.puml` | Hoạt động | Luồng xử lý một câu hỏi |
| `30_component_kien_truc.puml` | Thành phần | Kiến trúc tổng thể |

## Xuất ảnh

```bash
# cần Java; không cần Graphviz nhờ layout smetana
java -jar plantuml.jar -charset UTF-8 -Playout=smetana -tpng -o png docs/uml/[0-9]*.puml
```

Hoặc mở bằng extension **PlantUML** trong VS Code (Alt+D để xem trước).
