# utils.py
import os, re, unicodedata
from dotenv import load_dotenv
from elasticsearch import Elasticsearch
from datetime import datetime
from openai import OpenAI

load_dotenv()

ES_ENDPOINT = os.getenv("ES_ENDPOINT")
ES_API_KEY  = os.getenv("ES_API_KEY")
OPENAI_MODEL  = os.getenv("OPENAI_MODEL")

client = OpenAI()

def get_es() -> Elasticsearch:
    if not ES_ENDPOINT or not ES_API_KEY:
        raise RuntimeError("Thiếu ES_ENDPOINT / ES_API_KEY trong .env")
    return Elasticsearch(ES_ENDPOINT, api_key=ES_API_KEY)

def join_noidung(noidung) -> str:
    if isinstance(noidung, list):
        parts = [str(x).strip() for x in noidung if str(x).strip()]
        return " ".join(parts)
    return str(noidung or "")

def simp(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii","ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+","_", s).strip("_") or "NA"

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


def generate_chat_title(messages):
    """
    Dùng OpenAI (SDK mới) để tạo tiêu đề cho cuộc trò chuyện
    dựa trên 2-3 đoạn hội thoại đầu tiên của user.
    """
    try:
        # Lấy 2-3 tin nhắn đầu tiên từ user
        initial_messages = []
        for msg in messages:
            if msg["role"] == "user" and msg["content"].strip():
                initial_messages.append(msg["content"].strip())
                if len(initial_messages) >= 3:
                    break

        if not initial_messages:
            return "Cuộc trò chuyện mới"

        # Tạo prompt cho OpenAI
        joined = "\n".join(f"- {m}" for m in initial_messages)
        prompt = f"""
        Dựa vào các đoạn hội thoại sau:
        {joined}

        Hãy tạo một tiêu đề ngắn gọn (tối đa 10 từ) phản ánh nội dung chính của cuộc trò chuyện.
        Tiêu đề phải:
        1. Súc tích và dễ hiểu, thường bằng cụm động từ ([Động từ chính] + [tân ngữ hoặc bổ ngữ]) hoặc cụm danh từ ([Danh từ/chủ đề chính] + [bổ ngữ mô tả thêm nếu cần]) hoặc Câu rút gọn không chủ ngữ hoặc Cụm danh từ chuyên môn
        2. Mô tả chủ đề chính của cuộc trò chuyện, liên quan trực tiếp đến nội dung các câu hỏi
        3. Không quá dài (tối đa 8 từ)
        4. Bằng tiếng Việt
        5. Không chứa dấu câu đặc biệt

        Chỉ trả về tiêu đề, không cần giải thích.
        """

        # Gọi API theo chuẩn openai>=1.0.0
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # hoặc "gpt-4-turbo" nếu bạn có quyền truy cập
            messages=[
                {"role": "system", "content": "Bạn là một trợ lý ngôn ngữ chuyên đặt tiêu đề hội thoại."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=40
        )

        title = (response.choices[0].message.content or "").strip()

        # Giới hạn độ dài tiêu đề
        words = title.split()
        if len(words) > 10:
            title = " ".join(words[:10])
        return title

    except Exception as e:
        print(f"[Lỗi tạo tiêu đề]: {e}")
        return f"Cuộc trò chuyện {datetime.now().strftime('%H:%M:%S')}"

# def chuan_hoa_truy_van(query: str) -> str:
#     try:
#         if not os.getenv("OPENAI_API_KEY"):
#             return query
#         msgs = [
#             {"role": "system", "content": "Bạn là trợ lý IR. Hãy chuẩn hóa truy vấn pháp luật ngắn gọn và đúng trọng tâm."},
#             {"role": "user", "content": f"Chuẩn hóa truy vấn: {query}. Chỉ trả về một truy vấn duy nhất."}
#         ]
#         return _openai_chat(msgs, model=OPENAI_MODEL, temperature=0.0, max_tokens=120) or query
#     except Exception:
#         return query

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

def rewrite_query_with_history(prompt, history, max_history_turns=3):
    """
    Viết lại câu truy vấn hiện tại (prompt) dựa trên ngữ cảnh hội thoại gần nhất.

    Args:
        prompt (str): Câu hỏi hiện tại của người dùng.
        history (list[dict]): Lịch sử hội thoại, dạng [{"role": "user"|"assistant", "content": "..."}]
        max_history_turns (int): Số lượt hội thoại gần nhất sẽ dùng để rewrite.

    Returns:
        str: Truy vấn đã viết lại đầy đủ ngữ cảnh.
    """
    try:
        if not os.getenv("OPENAI_API_KEY"):
            return "Chưa cấu hình OPENAI_API_KEY nên chỉ hiển thị danh sách trích đoạn."
        
        # Giới hạn số lượt hội thoại để tiết kiệm token
        trimmed_history = history[-max_history_turns*2:]  # mỗi lượt gồm user + assistant

        # Xây prompt hội thoại dạng rõ ràng
        context_text = "\n".join([
            f"{turn['role'].capitalize()}: {turn['content']}" for turn in trimmed_history
        ])

        system_prompt = (
            "Bạn là trợ lý chuyên rewrite câu hỏi cho hệ thống truy xuất văn bản pháp luật Việt Nam. "
            "Hãy viết lại câu hỏi người dùng (prompt) sao cho đầy đủ ngữ cảnh hội thoại, rõ nghĩa, "
            "và có thể được dùng để tìm kiếm chính xác trong cơ sở dữ liệu pháp luật. "
            "Không thêm các cụm như \“theo quy định pháp luật Việt Nam\”, \“theo luật Việt Nam\", \“căn cứ pháp luật hiện hành\”, hoặc những đoạn mang tính khái quát không cần thiết."
            "Không được thêm thông tin mới, không được suy đoán, không diễn giải mở rộng. "
            "Không được thay đổi ý định gốc của người dùng."
            "Chỉ trả về đúng một prompt đã viết lại, không giải thích thêm."
            "Chỉ trả về truy vấn pháp luật khách quan, không kèm đại từ xưng hô, không đi kèm cảm xúc cá nhân."
            "Nếu prompt không mang yêu cầu hay thắc mắc về thông tin, mà chỉ đơn thuần phàn nàn hoặc bày tỏ cảm xúc hoặc feedback cho câu trả lời nhận được trước đó thì giữ nguyên prompt ban đầu."
        )

        user_prompt = (
            f"Lịch sử hội thoại:\n{context_text}\n\n"
            f"Prompt: {prompt}\n\n"
            f"Hãy viết lại prompt này ngắn gọn, trọng tâm nhưng phải đầy đủ ngữ cảnh để tôi tìm kiếm tài liệu liên quan hiệu quả hơn."
            f"Không kèm các cụm từ khái quát như \"theo quy định pháp luật Việt Nam\" hay \"theo luật Việt Nam\" hay \"theo quy định của pháp luật\"..."
            f"Nếu prompt không mang yêu cầu hay thắc mắc về thông tin, mà chỉ đơn thuần phàn nàn hoặc bày tỏ cảm xúc hoặc feedback cho câu trả lời nhận được trước đó thì giữ nguyên prompt ban đầu."
        )

        messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

        return _openai_chat(messages, temperature=0.2, max_tokens=800)
    
    except Exception as e:
        return f"Lỗi rewrite câu hỏi người dùng: {e}"

def rewrite_query(prompt, history, max_history_turns=3):
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
            Nhiệm vụ của bạn:

            1) Viết lại câu hỏi sao cho:
            - rõ ràng, cụ thể, tối ưu cho việc truy vấn dữ liệu pháp luật.
            - bổ sung các từ khóa cần thiết để tìm được điều luật cụ thể (phạt tiền, mức phạt, chế tài...)
                khi người dùng hỏi theo dạng mơ hồ như "xử lý", "bị gì", "bị sao".
            - Nếu câu hỏi đang yêu cầu về chế tài hoặc vi phạm nhưng dùng từ mơ hồ
                → hãy chuyển sang dạng tập trung vào hành vi + chế tài cụ thể
                (ví dụ: thêm "mức phạt", "phạt tiền", "xử phạt vi phạm hành chính", "truy cứu trách nhiệm hình sự").

            2) Tuyệt đối:
            - Không được thêm thông tin pháp lý không xuất hiện trong prompt hoặc trong history.
            - Không được suy đoán sự kiện không có.
            - Không được đưa ra câu trả lời pháp lý, chỉ rewrite truy vấn.
            - Không thêm các cụm khái quát như "theo pháp luật Việt Nam", "theo quy định hiện hành".

            3) Nếu prompt là phàn nàn, cảm xúc, phản hồi… và không chứa câu hỏi cần thông tin → trả về nguyên văn.

            4) Luôn trả về DUY NHẤT một truy vấn đã viết lại.
            """

        user_prompt = f"""
            Lịch sử hội thoại:
            {context_text}

            Prompt hiện tại của người dùng:
            "{prompt}"

            Hãy rewrite câu hỏi này thành một truy vấn rõ nghĩa, đầy đủ ngữ cảnh, 
            và thiên về tìm kiếm điều luật cụ thể liên quan đến chế tài, mức phạt, xử phạt, 
            nếu câu hỏi gốc thuộc nhóm "xử lý hành vi vi phạm".

            Không giải thích. Không thêm thông tin mới.
            Chỉ trả về truy vấn đã rewrite.
            """

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        return _openai_chat(messages, temperature=0.0, max_tokens=200)

    except Exception as e:
        return f"Lỗi rewrite câu hỏi người dùng: {e}"
    
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

SENTIMENT_TONE_MAP = {
    "neutral_info": "Giữ giọng điệu chuyên nghiệp, khách quan và dễ hiểu, tập trung vào cung cấp thông tin pháp luật chính xác.",
    "satisfied": "Giữ giọng điệu thân thiện, nhẹ nhàng và cảm ơn người dùng vì phản hồi tích cực.",
    "confused": "Giữ giọng điệu kiên nhẫn, giải thích kỹ hơn và làm rõ các khái niệm pháp lý dễ gây nhầm lẫn.",
    "anxious": "Giữ giọng điệu trấn an, rõ ràng và hướng dẫn từng bước, tránh dùng từ ngữ gây lo lắng.",
    "urgent": "Giữ giọng điệu nhanh gọn, trọng tâm, chỉ rõ hành động hoặc bước cần làm ngay.",
    "sad": "Giữ giọng điệu cảm thông, lịch sự, trấn an nhẹ nhàng hoặc khích lệ nếu cần và mang tính hỗ trợ, khích lệ người dùng.",
    "dissatisfied": "Giữ giọng điệu chuyên nghiệp, mở đầu trấn an nhẹ nhàng nếu cần, và tập trung giúp người dùng đạt được kết quả mong muốn.",
    "angry": "Giữ giọng điệu bình tĩnh, không đối đầu, mở đầu xin lỗi nếu cần, thể hiện sự tôn trọng và cố gắng giải quyết vấn đề một cách khách quan.",
    "grateful_polite": "Đáp lại với giọng điệu thân thiện và cảm ơn người dùng, thể hiện sự lịch sự tương xứng.",
    "hostile_abusive": "Giữ giọng điệu trung lập, tránh tranh luận, nhắc nhở nhẹ nhàng về việc trao đổi tôn trọng và chỉ tập trung vào yêu cầu pháp lý."
}  

def resolve_sentiment(raw_sentiment: str) -> str:
    """
    Luôn có 'neutral_info' + 1 label khác.
    Trả về label KHÔNG PHẢI 'neutral_info'.
    Fallback: nếu không tìm thấy thì trả về 'neutral_info'.
    """
    if not raw_sentiment:
        return "neutral_info"
    tokens = [t.strip() for t in raw_sentiment.split(",") if t.strip()]
    # Lấy nhãn hợp lệ nằm trong map và khác neutral_info
    for t in tokens:
        if t in SENTIMENT_TONE_MAP and t != "neutral_info":
            return t
    return "neutral_info"

def generate_response(noidung_texts, current_query, sentiment, chat_history):
    try:
        if not os.getenv("OPENAI_API_KEY"):
            return "Chưa cấu hình OPENAI_API_KEY nên chỉ hiển thị danh sách trích đoạn."

        context = "\n\n---\n\n".join(noidung_texts[:20])

        history_formatted = "\n".join(
            f"{m['role'].capitalize()}: {m['content']}" for m in chat_history
        )

        system_prompt = (
            "Bạn là trợ lý pháp luật Việt Nam. "
            "Bạn cần trả lời chính xác, ngắn gọn, đúng luật và có thể trích dẫn điều luật nếu cần. "
        )

        resolved_sentiment = resolve_sentiment(sentiment)
        tone_instruction = SENTIMENT_TONE_MAP.get(
            resolved_sentiment, SENTIMENT_TONE_MAP["neutral_info"]
        )

        user_prompt = (
            f"Truy vấn hiện tại: {current_query}\n\n"
            f"Lịch sử hội thoại gần đây:\n{history_formatted}\n\n"
            f"Các văn bản pháp luật liên quan:\n{context}\n\n"
            f"Yêu cầu về giọng điệu khi trả lời: {tone_instruction}\n\n"
            f"Hãy trả lời truy vấn dựa trên thông tin trên, trả lời trực tiếp, không cần nhắc lại câu hỏi, trình bày logic, cấu trúc rõ ràng, chia theo từng ý và in đậm các thông tin hoặc số liệu quan trọng, \n\n"
            f"nên giữ nguyên nội dung các đoạn văn bản pháp luật nào đề cập tới trong câu trả lời\n\n"
            f"Chốt lại các căn cứ pháp lý với mỗi tuyên bố nêu ra trong câu trả lời ở cuối câu trả lời\n\n" 
            f"Nếu câu hỏi là hành vi có hay không điều gì đó thì dòng đầu cần khẳng định có hoặc không hoặc như thế nào trước (in đậm khẳng định) rồi mới giải thích.\n\n"
            f"Nếu không biết thì trả lời là chưa đủ dữ liệu để xử lý yêu cầu trên, định hướng người dùng mô tả yêu cầu rõ hơn, không được bịa thông tin không có căn cứ"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        return _openai_chat(messages, temperature=0.2, max_tokens=800)
    except Exception as e:
        return f"Lỗi tạo câu trả lời: {e}"

