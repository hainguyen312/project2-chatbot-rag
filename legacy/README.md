# legacy/

Mã và kết quả của các chiến lược / eval **thử nghiệm đã thôi dùng**. Giữ để tham khảo và
đối chiếu số liệu trong báo cáo; chatbot và eval hiện tại KHÔNG import gì từ đây.
Lý do lược bỏ từng mục: xem bảng "Đã lược bỏ" trong README gốc.

| Đường dẫn | Nội dung |
|---|---|
| `retrieve/retrieval_strategies.py`, `retrieve/test_retrieval.py`, `retrieve/README.md` | 4 chiến lược đợt 1: semantic_search, hybrid_search, two_stage_search, hybrid_rerank |
| `retrieve/two_stage_search.py` | Bản gốc có class TwoStageRetriever (rerank LLM đợt 1). Phần kết nối vẫn ở `retrieve/two_stage_search.py` |
| `evaluation/run_evaluation.py`, `metrics/`, `visualization.py`, `rebuild_data_json.py` | Eval đợt 1 (5 câu có đáp án, ROUGE-L, BLEU, semantic similarity) |
| `evaluation/results/` | Kết quả đợt 1 |
| `evaluation/compare_title_vs_context.py` | So sánh Milvus tên điều vs chunk nội dung (+ LLM rerank). Hàm sinh câu known-item đã tách sang `evaluation/synthetic.py` |
| `data_processing/seed_contextual_chunks.py` | Seed collection `phapdien_ctx_chunks` (collection vẫn còn trong Milvus, không còn trong đường truy hồi) |
| `services/llm_rerank.py` | LLM rerank pointwise (`RAG_AGENT_LLM_RERANK`, `_llm_score_passage`, `_apply_llm_rerank_head`) — Bước 6 tham khảo prompt chấm điểm ở đây |
