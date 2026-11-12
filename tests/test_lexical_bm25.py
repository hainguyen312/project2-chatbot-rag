"""Tokenizer tiếng Việt + BM25 (retrieve/lexical_bm25.py)."""
import pytest

from retrieve.lexical_bm25 import BM25Index, clean_title, content_tokens, tokenize


class TestTokenize:
    def test_lowercase_and_syllables(self):
        assert "thai" in tokenize("Lao Động Nữ Mang THAI", bigrams=False)

    def test_function_words_removed(self):
        toks = tokenize("quyền của người lao động và người sử dụng", bigrams=False)
        assert "của" not in toks and "và" not in toks
        assert "quyền" in toks

    def test_bigrams_capture_compounds(self):
        toks = tokenize("nghỉ thai sản")
        assert "thai_sản" in toks

    def test_soft_stopword_kept_inside_bigram(self):
        # "điều" bỏ khỏi unigram nhưng "điều_kiện" phải còn (lỗi cũ: bỏ trước rồi mới ghép bigram)
        toks = tokenize("điều kiện kết hôn")
        assert "điều" not in toks
        assert "điều_kiện" in toks and "kết_hôn" in toks

    def test_function_word_breaks_bigram(self):
        toks = tokenize("lao động của doanh nghiệp")
        assert "động_của" not in toks and "của_doanh" not in toks

    def test_two_soft_stopwords_no_bigram(self):
        assert "quy_định" not in tokenize("quy định")

    def test_digits_dropped(self):
        assert "2019" not in tokenize("Nghị định 100 năm 2019", bigrams=False)

    def test_content_tokens_is_unigram_set(self):
        assert content_tokens("án treo án treo") == {"án", "treo"}

    def test_empty(self):
        assert tokenize("") == [] and tokenize(None) == []


@pytest.mark.parametrize("raw, expected", [
    ("Điều 1.1.LQ.1. Phạm vi điều chỉnh", "Phạm vi điều chỉnh"),
    ("Điều 39.13.NĐ.75.8. Xử phạt người điều khiển xe đạp", "Xử phạt người điều khiển xe đạp"),
    ("Không có mã", "Không có mã"),
    ("", ""),
])
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


class TestBM25Index:
    @pytest.fixture
    def index(self):
        import bm25s

        docs = {
            "A": "Bảo vệ thai sản. Người sử dụng lao động không được sa thải lao động nữ mang thai",
            "B": "Điều kiện kết hôn. Nam từ đủ 20 tuổi, nữ từ đủ 18 tuổi",
            "C": "Án treo. Người bị phạt tù không quá 03 năm được hưởng án treo",
            "D": "Thủ tục đăng ký kết hôn tại Ủy ban nhân dân cấp xã",
        }
        retr = bm25s.BM25()
        retr.index([tokenize(t) for t in docs.values()], show_progress=False)
        return BM25Index(retr, list(docs))

    def test_ranks_relevant_first(self, index):
        assert index.search("lao động nữ mang thai bị sa thải", 3)[0][0] == "A"

    def test_bigram_beats_shared_syllable(self, index):
        # "điều kiện kết hôn" khớp bigram điều_kiện ở B, D chỉ khớp "kết hôn"
        top = [m for m, _ in index.search("điều kiện kết hôn", 2)]
        assert top[0] == "B" and "D" in top

    def test_scores_descending_and_positive(self, index):
        res = index.search("kết hôn", 4)
        scores = [s for _, s in res]
        assert scores == sorted(scores, reverse=True) and all(s > 0 for s in scores)

    def test_unknown_terms_return_empty(self, index):
        assert index.search("blockchain tiền mã hoá", 5) == []

    def test_only_stopwords_return_empty(self, index):
        assert index.search("của và là", 5) == []
