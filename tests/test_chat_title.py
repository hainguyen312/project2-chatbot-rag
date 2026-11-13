"""Đặt tên đoạn chat bằng LLM (services/chat_title.py)."""
import pytest

from services.chat_title import clean_title, fallback_title, generate_title
from tests.conftest import ScriptedChatClient, assistant


@pytest.mark.parametrize("raw, expected", [
    ('"Lao động nữ mang thai bị sa thải."', "Lao động nữ mang thai bị sa thải"),
    ("Câu hỏi về điều kiện kết hôn", "Điều kiện kết hôn"),
    ("Tiêu đề: mức phạt nồng độ cồn", "Mức phạt nồng độ cồn"),
    ("**Chia di sản thừa kế**\nGiải thích thêm…", "Chia di sản thừa kế"),
    ("", ""),
])
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


def test_clean_title_length_cut_on_word_boundary():
    t = clean_title("Tranh chấp " + "hợp đồng thuê nhà " * 10)
    assert len(t) <= 60 and not t.endswith(" ")


def test_fallback_is_old_behavior():
    assert fallback_title("Tôi bị công ty nợ lương 3 tháng thì phải làm sao ạ") == "Tôi bị công ty nợ lương 3 tháng thì phải…"
    assert fallback_title("  ") == "Cuộc trò chuyện mới"


def test_generate_uses_question_and_answer():
    c = ScriptedChatClient([assistant("Công ty nợ lương 3 tháng")])
    assert generate_title(c, "Công ty nợ lương tôi 3 tháng rồi", "Theo Điều 97 BLLĐ…") == "Công ty nợ lương 3 tháng"
    user_msg = c.requests[0]["messages"][1]["content"]
    assert "nợ lương" in user_msg and "Điều 97" in user_msg


@pytest.mark.parametrize("llm_out", ["Lương", "", "a " * 20])
def test_unusable_output_falls_back(llm_out):
    c = ScriptedChatClient([assistant(llm_out)])
    assert generate_title(c, "Điều kiện kết hôn là gì") == "Điều kiện kết hôn là gì"


def test_llm_error_falls_back():
    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**k):
                    raise RuntimeError("429")
    assert generate_title(Boom, "Mức phạt vượt đèn đỏ") == "Mức phạt vượt đèn đỏ"


def test_empty_question_no_llm_call():
    c = ScriptedChatClient([])
    assert generate_title(c, "  ") == "Cuộc trò chuyện mới" and c.requests == []


def test_short_question_skips_llm():
    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    raise AssertionError("không được gọi LLM")
    assert generate_title(Boom(), "bạn có thể làm gì") == "Bạn có thể làm gì"
