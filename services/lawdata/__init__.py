"""
Công cụ xây dựng dữ liệu pháp luật (trang /admin).

  pdf_extract  PDF → chữ (lọc chú thích theo cỡ chữ, phát hiện trang scan)
  parser       chữ → metadata + cấu trúc theo ĐÚNG văn bản (Phần/Chương/Mục/Điều/Khoản/Điểm)
  sources      lấy PDF từ link (trang văn bản Công báo hoặc link PDF trực tiếp)
  store        bảng phụ MySQL, so sánh với dữ liệu hiện có, ghi khi duyệt (+ Milvus, BM25)
  staging      khu chờ duyệt (bảng MySQL law_staging) + hàng đợi xử lý nền
  status       gắn tình trạng hiệu lực vào kết quả tra cứu
  phapdien     cập nhật dữ liệu pháp điển từ bản xuất mới
"""
