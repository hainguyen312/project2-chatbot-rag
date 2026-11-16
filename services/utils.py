"""
Tiện ích LLM dùng chung cho rag_api: chuẩn hoá truy vấn (rewrite_query_v2), kiểm tra phạm vi (detect_intent),
embedding (embed_batch) và client OpenAI.

Các hàm của giao diện Streamlit cũ (app.py: generate_response*, retrieve_parallel, analyze_complex_situation,
rewrite_query*, Elasticsearch…) đã chuyển sang legacy/services/utils.py.
"""
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL")

client = OpenAI()


def embed_batch(texts):
    resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts
    )
    return [item.embedding for item in resp.data]


def _openai_chat(messages, model=OPENAI_MODEL, temperature=0.2, max_tokens=600):
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[Lỗi _openai_chat]: {e}")
        return None


def detect_intent(prompt):
    """
    Kiểm tra xem câu hỏi có đúng về lĩnh vực pháp lý không
    Tránh trả lời các câu hỏi không liên quan
    """
    try:
        system_prompt = (
            "Bạn là trợ lý đánh giá câu hỏi người dùng cho hệ thống hỏi đáp pháp lý hoặc văn bản pháp luật Việt Nam. "
            "Bạn có nhiệm cụ phân loại mục đích của người dùng có đúng mục đích tìm kiếm thông tin về pháp lý hoặc pháp luật và liên quan hay không"
            "Chỉ trả về Có hoặc không, không giải thích gì thêm."
            "Nếu không rõ, trả về Có"
            )

        user_prompt = (
            f"Câu hỏi: {prompt}\n\n"
            f"Câu hỏi trên có đúng phạm vi hỏi đáp về kiến thức liên quan đến pháp luật hoặc pháp lý Việt Nam hoặc lĩnh vực liên quan hay không"
            f"Chỉ trả về kết quả \"Có\" hoặc \"Không\", không cần giải thích gì thêm."
        )

        messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

        return _openai_chat(messages, temperature=0.2, max_tokens=800)
    except Exception as e:
        print(f"[Lỗi phân tích yêu cầu người dùng]: {e}")


def rewrite_query_v2(prompt, history, max_history_turns=3):
    """
    Viết lại câu truy vấn hiện tại dựa trên hội thoại + làm truy vấn cụ thể hơn
    (đặc biệt với các câu hỏi 'xử lý' → ưu tiên dạng truy vấn có tính chế tài cụ thể).

    Args:
        prompt (str): Câu hỏi hiện tại của người dùng.
        history (list[dict]): [{"role": "user"|"assistant", "content": "..."}]
        max_history_turns (int): Số lượt hội thoại gần nhất sẽ dùng để rewrite.

    Returns:
        str: Truy vấn đã viết lại.
    """
    try:
        if not os.getenv("OPENAI_API_KEY"):
            return "Chưa cấu hình OPENAI_API_KEY nên chỉ hiển thị danh sách trích đoạn."
        
        # Giới hạn context hội thoại (user + assistant)
        trimmed_history = history[-max_history_turns*2:]

        # Ghép hội thoại thành dạng readable
        context_text = "\n".join([
            f"{turn['role'].capitalize()}: {turn['content']}"
            for turn in trimmed_history
        ])

        system_prompt = """
            Bạn là trợ lý chuyên rewrite truy vấn cho hệ thống tìm kiếm văn bản pháp luật Việt Nam.

            Nhiệm vụ: Từ prompt hiện tại và lịch sử hội thoại, tạo ra MỘT cụm từ truy vấn NGẮN GỌN mô tả đúng trọng tâm vấn đề.

            Quy tắc bắt buộc về hình thức:
            - Chỉ trả về DUY NHẤT 1 dòng truy vấn.
            - Truy vấn phải súc tích, dễ hiểu, thường ở một trong các dạng:
            (1) Cụm động từ: [Động từ chính] + [tân ngữ/bổ ngữ]
            (2) Cụm danh từ: [Chủ đề/danh từ chính] + [bổ ngữ cần thiết]
            (3) Câu rút gọn không chủ ngữ
            (4) Cụm danh từ chuyên môn
            - KHÔNG chứa dấu câu đặc biệt: không dùng ?, !, ., ,, :, ;, ", ', (, ), [, ], {, }, -, _, /, \, +, =, @, #, %, &…
            - Chỉ dùng chữ, số và khoảng trắng. Không xuống dòng. Không bullet.
            - Độ dài ưu tiên 2–10 từ (nếu cần có thể dài hơn, nhưng vẫn phải gọn).

            Quy tắc bắt buộc về nội dung:
            - Chỉ mô tả CHỦ ĐỀ/TRỌNG TÂM cần tra cứu, không viết lại thành câu hỏi đầy đủ.
            - Loại bỏ các phần rườm rà như: "là gì", "như thế nào", "được quy định", "theo quy định", "ra sao", "xử lý", "bị gì", "bị sao".
            - Nếu câu hỏi thuộc nhóm chế tài/xử phạt nhưng diễn đạt mơ hồ:
            -> Ưu tiên giữ lại HÀNH VI hoặc CHỦ ĐỀ VI PHẠM (không cần thêm các cụm như mức phạt, xử phạt, truy cứu... trừ khi người dùng đã nêu rõ).
            - Nếu câu hỏi thuộc nhóm quyền lợi, nghĩa vụ, điều kiện, thủ tục:
            -> Ưu tiên giữ lại CHỦ ĐỀ chính (ví dụ "Quyền lợi lao động nữ mang thai").

            Ràng buộc an toàn:
            - Tuyệt đối KHÔNG thêm thông tin pháp lý hoặc tình tiết không có trong prompt hoặc history.
            - KHÔNG suy đoán.
            - KHÔNG trả lời pháp lý, chỉ tạo cụm truy vấn.
            - KHÔNG thêm các cụm khái quát như "theo pháp luật Việt Nam", "theo quy định hiện hành".
            """

        user_prompt = f"""
            Lịch sử hội thoại:
            {context_text}

            Prompt hiện tại của người dùng:
            {prompt}

            Hãy tạo 1 truy vấn dạng cụm từ ngắn gọn mô tả đúng trọng tâm cần tra cứu.
            Yêu cầu bắt buộc:
            - Chỉ trả về 1 dòng
            - Không dấu câu đặc biệt, chỉ chữ số và khoảng trắng
            - Không viết thành câu hỏi, không thêm "được quy định như thế nào", "là gì", "ra sao"
            - Không thêm thông tin mới ngoài lịch sử và prompt
            """

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        return _openai_chat(messages, temperature=0.0, max_tokens=200)

    except Exception as e:
        return f"Lỗi rewrite câu hỏi người dùng: {e}"
