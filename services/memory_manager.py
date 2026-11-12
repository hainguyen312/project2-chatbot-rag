"""
MemoryManager — Hệ thống bộ nhớ dài hạn (Mem0-style) cho Agentic RAG pháp luật.

Kiến trúc:
- Extraction: gpt-4o-mini trích xuất các fact bền vững từ mỗi lượt hội thoại,
  phân loại theo 4 loại memory: core / episodic / semantic / procedural.
- Update: với mỗi candidate fact, tìm các memory tương đồng của user, để LLM
  quyết định ADD / UPDATE / DELETE / NOOP (giống thuật toán Mem0).
- Retrieve: cosine trên embedding lưu trong Mongo (theo user_id), có tính độ mới.

Storage (một nguồn dữ liệu duy nhất):
- MongoDB `memories`: fact + embedding + loại + cờ nhạy cảm + lịch sử thay đổi.
  Tìm kiếm ngữ nghĩa = cosine bằng numpy trên memory của MỘT user (số lượng nhỏ).
- MongoDB `memory_settings`: chế độ bộ nhớ theo user (no_sensitive | full | off).
(Trước đây vector nằm ở Milvus, fact ở Mongo → hai kho lệch nhau khi Milvus bị reset.)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Any

import uuid

import numpy as np

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except Exception:  # pragma: no cover
    MongoClient = None
    PyMongoError = Exception

logger = logging.getLogger("memory_manager")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[Memory] %(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


MEMORY_MONGO_COLLECTION = os.getenv("MEMORY_MONGO_COLLECTION", "memories")
MEMORY_TOP_K = int(os.getenv("MEMORY_TOP_K", "5"))
MEMORY_EXTRACT_MODEL = os.getenv("MEMORY_EXTRACT_MODEL", "gpt-4o-mini")
MEMORY_UPDATE_MODEL = os.getenv("MEMORY_UPDATE_MODEL", "gpt-4o-mini")
MEMORY_SIM_TOP = 5  # số memory tương đồng đưa vào prompt update

# ── Độ mới & hết hạn khi TRUY XUẤT ──────────────────────────────────────────
# Chu kỳ bán rã (ngày) theo loại: sau N ngày, trọng số độ mới còn 1/2. None = không phai.
MEMORY_HALF_LIFE_DAYS: dict[str, float | None] = {
    "episodic": float(os.getenv("MEMORY_EPISODIC_HALF_LIFE_DAYS", "30")),
    "semantic": float(os.getenv("MEMORY_SEMANTIC_HALF_LIFE_DAYS", "120")),
    "core": None,
    "procedural": None,
}
# Episodic quá hạn này thì KHÔNG đưa vào context nữa (vẫn giữ trong DB, hiện "đã cũ" ở panel)
MEMORY_EPISODIC_TTL_DAYS = float(os.getenv("MEMORY_EPISODIC_TTL_DAYS", "180"))
# Bỏ memory có độ tương đồng thấp hơn ngưỡng (trước đây luôn lấy đủ top_k dù không liên quan)
MEMORY_MIN_SIMILARITY = float(os.getenv("MEMORY_MIN_SIMILARITY", "0.2"))
MEMORY_RECENCY_WEIGHT = float(os.getenv("MEMORY_RECENCY_WEIGHT", "0.4"))

VALID_TYPES = {"core", "episodic", "semantic", "procedural"}

# Fact hồ sơ (năm sinh/giới tính/trường) — luôn pin vào context, kể cả khi
# extractor gắn nhầm type hoặc vector search không khớp "kết hôn" ↔ "sinh năm".
_PROFILE_FACT_RE = re.compile(
    r"(sinh năm|năm sinh|giới tính|\blà nam\b|\blà nữ\b|học tại)",
    re.IGNORECASE,
)
_FORCE_CORE_RE = re.compile(
    r"(sinh năm|năm sinh|giới tính|\blà nam\b|\blà nữ\b)",
    re.IGNORECASE,
)

# Cụm từ gợi ý fact MỚI có giá trị thay thế (không phải phủ định thuần tuý)
_REPLACEMENT_HINTS = re.compile(
    r"(chuyển\s+sang|chuyển\s+đến|chuyển\s+vào|chuyển\s+thành|"
    r"thành|trở thành|giờ\s+là|giờ\s+làm|hiện\s+(tại|nay)|"
    r"đổi\s+(sang|thành|qua)|"
    r"mới\s+(là|đang|chuyển|làm|học|sống|ở))",
    re.IGNORECASE,
)


def _looks_like_replacement(fact: str) -> bool:
    """True nếu fact có dấu hiệu thay thế (vd 'chuyển sang ĐH X') chứ không
    phải phủ định thuần tuý (vd 'đã nghỉ học').
    """
    return bool(_REPLACEMENT_HINTS.search(fact or ""))

# Regex thô để loại bỏ fact chứa thông tin định danh nhạy cảm
_SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{9,12}\b"),                         # CMND/CCCD/SĐT
    re.compile(r"\b\d{4,}[\s\-]?\d{4,}[\s\-]?\d{4,}\b"),  # số TK ngân hàng
    re.compile(r"\b\d{16,19}\b"),                        # số thẻ
]


# Chủ đề NHẠY CẢM (dữ liệu cá nhân nhạy cảm): chỉ lưu khi người dùng chủ động cho phép.
# LLM gắn cờ `sensitive`; regex này là lưới dự phòng khi LLM bỏ sót.
_SENSITIVE_TOPIC_RE = re.compile(
    r"(khởi tố|bị bắt|tạm giam|tạm giữ|truy nã|tiền án|tiền sự|án tích|đi tù|ngồi tù|"
    r"đang bị điều tra|bị kết án|phạm tội|"
    r"bệnh|ung thư|hiv|aids|trầm cảm|tâm thần|mang thai|sảy thai|phá thai|khuyết tật|"
    r"đồng tính|chuyển giới|xu hướng tính dục|quan hệ tình dục|"
    r"tôn giáo|theo đạo|đảng viên|chính trị|dân tộc thiểu số)",
    re.IGNORECASE,
)

MEMORY_MODES = ("no_sensitive", "full", "off")
# Mặc định: ghi nhớ nhưng KHÔNG lưu dữ liệu nhạy cảm (privacy by default)
MEMORY_DEFAULT_MODE = os.getenv("MEMORY_DEFAULT_MODE", "no_sensitive")
MEMORY_SETTINGS_COLLECTION = os.getenv("MEMORY_SETTINGS_COLLECTION", "memory_settings")


def is_sensitive_topic(fact: str) -> bool:
    return bool(_SENSITIVE_TOPIC_RE.search(fact or ""))


# ── Prompts ───────────────────────────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """Bạn là bộ trích xuất bộ nhớ dài hạn cho trợ lý pháp luật Việt Nam.

Nhiệm vụ: đọc 1 lượt hội thoại (user + bot) và trích xuất các FACT BỀN VỮNG,
hữu ích cho cá nhân hóa câu trả lời pháp lý trong các phiên trò chuyện sau.

4 LOẠI MEMORY:
- core: hồ sơ người dùng ổn định — năm sinh, tuổi, giới tính, nghề nghiệp,
  trường học, địa phương, vai trò, ngành kinh doanh.
  Ví dụ: "Người dùng sinh năm 2003", "Người dùng là nam",
  "Người dùng làm kế toán ở Hà Nội", "Người dùng đang học tại NEU".
- episodic: tình huống / vụ việc cụ thể người dùng đang gặp.
  Ví dụ: "Đang tranh chấp hợp đồng thuê nhà với chủ trọ", "Bị công ty cũ nợ lương 3 tháng".
- semantic: chủ đề pháp lý / điều luật / lĩnh vực luật mà người dùng quan tâm lâu dài.
  Ví dụ: "Quan tâm Luật Lao động phần thai sản", "Hay hỏi về thuế GTGT".
- procedural: sở thích cách trả lời (định dạng, độ dài, ngôn ngữ).
  Ví dụ: "Thích câu trả lời ngắn gọn, có trích dẫn điều luật cụ thể".

NGUYÊN TẮC BẮT BUỘC:
1. KHÔNG lưu nội dung điều luật chung chung (đã có trong RAG). Chỉ lưu thông tin
   GẮN VỚI NGƯỜI DÙNG cụ thể.
2. KHÔNG lưu thông tin định danh nhạy cảm: số CMND/CCCD, số tài khoản ngân hàng,
   số thẻ tín dụng, mật khẩu, mã OTP, địa chỉ nhà đầy đủ kèm số.
3. KHÔNG lưu nội dung câu chào hỏi, cảm ơn, hội thoại meta không có thông tin cá nhân.
4. Mỗi fact viết bằng tiếng Việt, ngắn gọn, 1 câu, ở ngôi thứ 3 ("Người dùng …").
5. Năm sinh / tuổi / giới tính / trường học LUÔN là type=core, viết RÕ, không
   ghép với chủ đề pháp lý. Ví dụ ĐÚNG: "Người dùng sinh năm 2003".
   Ví dụ SAI: "Người dùng từng năm 2005 hỏi về kết hôn sinh năm 2003".
6. Nếu lượt hội thoại không có thông tin đáng nhớ → trả về `[]`.
7. Trường "sensitive" = true nếu fact thuộc dữ liệu cá nhân NHẠY CẢM: liên quan tố tụng
   hình sự của người dùng (bị khởi tố, bị bắt, tiền án, đang bị điều tra), sức khoẻ / bệnh
   tật / thai sản, đời sống tình dục hoặc xu hướng tính dục, tôn giáo, quan điểm chính trị,
   dân tộc. Còn lại "sensitive" = false.

ĐỊNH DẠNG OUTPUT: JSON object {"facts": [...]} — mảng fact nằm trong trường "facts"
(các ví dụ dưới đây chỉ ghi phần mảng). Không có fact nào → {"facts": []}.
Mỗi phần tử là object: {"fact": "<câu tiếng Việt>", "type": "core|episodic|semantic|procedural"}

VÍ DỤ:
Input user: "Tôi mở quán cà phê ở Hạ Long, muốn biết thủ tục đăng ký kinh doanh hộ cá thể"
Input bot: "Theo Nghị định 01/2021/NĐ-CP, hộ kinh doanh đăng ký tại UBND cấp huyện..."
Output:
[
  {"fact": "Người dùng mở quán cà phê ở Hạ Long, Quảng Ninh", "type": "core"},
  {"fact": "Đang tìm hiểu thủ tục đăng ký kinh doanh hộ cá thể", "type": "episodic"}
]

Input user: "Tôi sinh năm 2003, nam, tôi có đủ tuổi kết hôn không?"
Input bot: "Theo Luật Hôn nhân và Gia đình, nam từ đủ 20 tuổi..."
Output:
[
  {"fact": "Người dùng sinh năm 2003", "type": "core"},
  {"fact": "Người dùng là nam", "type": "core"}
]

Input user: "Cảm ơn bạn"
Output: []
"""


_UPDATE_SYSTEM_PROMPT = """Bạn là bộ điều phối cập nhật bộ nhớ dài hạn.

Cho:
- 1 CANDIDATE FACT mới (vừa trích xuất từ hội thoại)
- Danh sách EXISTING MEMORIES tương đồng nhất của cùng người dùng (đã có sẵn)

Quyết định MỘT trong 4 thao tác:
- ADD: candidate là thông tin HOÀN TOÀN MỚI, không trùng / không liên quan memory cũ
  (vd: thông tin về một lĩnh vực khác mà chưa từng được lưu).
- UPDATE: candidate THAY THẾ / BỔ SUNG / ĐÍNH CHÍNH một memory cũ với GIÁ TRỊ MỚI.
  → DÙNG UPDATE BẤT KỲ KHI NÀO candidate cùng MỘT THỰC THỂ với memory cũ nhưng giá trị khác:
    + Đổi trường học (ĐH A → ĐH B)
    + Đổi ngành nghề (kế toán → giáo viên)
    + Chuyển công ty / chuyển địa phương / chuyển ngành kinh doanh
    + Đổi tình trạng hôn nhân (độc thân → kết hôn → ly hôn)
    + Đổi địa chỉ, thay đổi thông tin liên hệ
  → Khi UPDATE PHẢI điền `target_id` (id memory cũ) và `merged_fact` (câu mới thay thế).
- DELETE: candidate CHỈ phủ định / vô hiệu hoá memory cũ mà KHÔNG cung cấp giá trị thay thế.
  → CHỈ dùng DELETE khi candidate KHÔNG đưa thông tin mới đáng giữ, vd:
    + "Người dùng không còn kinh doanh" (không nói chuyển sang gì)
    + "Đã nghỉ học" (không nói học gì khác)
    + "Đã thôi việc" (không nói làm gì mới)
  → ⚠️ NẾU candidate có chứa thông tin thay thế ("chuyển sang X", "giờ làm Y") → ƯU TIÊN UPDATE, KHÔNG DELETE.
- NOOP: candidate và memory cũ tương đương về mặt ngữ nghĩa (paraphrase) → không thay đổi.

ĐỊNH DẠNG OUTPUT: CHỈ trả về JSON object hợp lệ, không markdown, không giải thích.
Schema:
{
  "action": "ADD" | "UPDATE" | "DELETE" | "NOOP",
  "target_id": <số id của memory cũ, BẮT BUỘC khi UPDATE/DELETE>,
  "merged_fact": "<câu tiếng Việt>",   // BẮT BUỘC khi UPDATE
  "reason": "<1 câu lý do ngắn>"
}

VÍ DỤ ĐÚNG:

# UPDATE — đổi ngành kinh doanh (có giá trị thay thế)
Candidate: {"fact": "Người dùng chuyển sang kinh doanh nhà hàng", "type": "core"}
Existing: [{"id": 12, "fact": "Người dùng mở quán cà phê ở Hạ Long"}]
Output: {"action":"UPDATE","target_id":12,"merged_fact":"Người dùng chuyển từ quán cà phê sang kinh doanh nhà hàng ở Hạ Long","reason":"Thay đổi ngành kinh doanh, vẫn ở Hạ Long"}

# UPDATE — chuyển trường (CÓ giá trị thay thế "Đại học X" → KHÔNG được DELETE)
Candidate: {"fact": "Người dùng vừa chuyển sang học Đại học Kinh tế Quốc dân, ngành Quản trị kinh doanh", "type": "core"}
Existing: [{"id": 30, "fact": "Người dùng đang học ngành Công nghệ thông tin tại Đại học Bách Khoa Hà Nội"}]
Output: {"action":"UPDATE","target_id":30,"merged_fact":"Người dùng chuyển từ ngành Công nghệ thông tin (ĐH Bách Khoa Hà Nội) sang ngành Quản trị kinh doanh tại Đại học Kinh tế Quốc dân","reason":"Đổi trường + ngành học, fact mới là giá trị thay thế"}

# DELETE — phủ định thuần tuý, không nêu thay thế
Candidate: {"fact": "Người dùng đã đóng cửa quán cà phê", "type": "core"}
Existing: [{"id": 12, "fact": "Người dùng mở quán cà phê ở Hạ Long"}]
Output: {"action":"DELETE","target_id":12,"reason":"Phủ định memory cũ, không có giá trị thay thế"}
"""

_BATCH_UPDATE_SYSTEM_PROMPT = _UPDATE_SYSTEM_PROMPT + """
CHẾ ĐỘ NHIỀU CANDIDATE (định dạng thực tế được dùng):
- Input gồm danh sách CANDIDATES (mỗi cái có "index") và danh sách EXISTING chung;
  mỗi memory cũ có "id" là SỐ THỨ TỰ NGẮN (1, 2, 3…).
- Trả về {"decisions": [...]} với ĐÚNG MỘT quyết định cho MỖI candidate:
  {"candidate": <index>, "action": "...", "target_id": <id trong EXISTING hoặc null>,
   "merged_fact": <câu mới khi UPDATE, ngược lại null>, "reason": "..."}
- target_id CHỈ được là một "id" có trong EXISTING; ADD/NOOP thì target_id = null.
- Hai candidate cùng lượt nói về CÙNG một thực thể → chỉ một cái ADD/UPDATE, cái kia NOOP.
- Không có EXISTING nào liên quan → ADD (trừ khi candidate rỗng nghĩa → NOOP).
"""

_EXTRACT_SCHEMA = {
    "name": "memory_facts",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"facts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "fact": {"type": "string"},
                "type": {"type": "string", "enum": sorted(VALID_TYPES)},
                "sensitive": {"type": "boolean"},
            },
            "required": ["fact", "type", "sensitive"],
            "additionalProperties": False,
        }}},
        "required": ["facts"],
        "additionalProperties": False,
    },
}

_DECISION_SCHEMA = {
    "name": "memory_decisions",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"decisions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "candidate": {"type": "integer"},
                "action": {"type": "string", "enum": ["ADD", "UPDATE", "DELETE", "NOOP"]},
                "target_id": {"type": ["integer", "null"]},
                "merged_fact": {"type": ["string", "null"]},
                "reason": {"type": "string"},
            },
            "required": ["candidate", "action", "target_id", "merged_fact", "reason"],
            "additionalProperties": False,
        }}},
        "required": ["decisions"],
        "additionalProperties": False,
    },
}

# Mỗi user chỉ một lượt ghi memory chạy tại một thời điểm (tránh 2 lượt chat liên tiếp
# cùng ADD một fact → bản trùng).
_USER_LOCKS: dict[str, threading.Lock] = {}
_USER_LOCKS_GUARD = threading.Lock()


def _user_lock(user_id: str) -> threading.Lock:
    with _USER_LOCKS_GUARD:
        return _USER_LOCKS.setdefault(user_id, threading.Lock())


def _now_iso() -> str:
    return datetime.now().isoformat()


def _age_days(timestamp: str | None, now: datetime | None = None) -> float | None:
    """Số ngày từ timestamp (ISO) tới now; None nếu không đọc được."""
    try:
        ts = datetime.fromisoformat(str(timestamp))
    except Exception:
        return None
    now = now or datetime.now()
    if ts.tzinfo is not None and now.tzinfo is None:
        ts = ts.replace(tzinfo=None)
    return max(0.0, (now - ts).total_seconds() / 86400)


def recency_factor(mem_type: str | None, timestamp: str | None, now: datetime | None = None) -> float:
    """1.0 = mới; giảm theo chu kỳ bán rã của loại memory; loại không phai → 1.0."""
    half = MEMORY_HALF_LIFE_DAYS.get((mem_type or "").lower())
    age = _age_days(timestamp, now)
    if not half or age is None:
        return 1.0
    return 0.5 ** (age / half)


def is_expired(mem_type: str | None, timestamp: str | None, now: datetime | None = None) -> bool:
    """Episodic quá MEMORY_EPISODIC_TTL_DAYS → không dùng khi trả lời."""
    if (mem_type or "").lower() != "episodic":
        return False
    age = _age_days(timestamp, now)
    return age is not None and age > MEMORY_EPISODIC_TTL_DAYS


def retrieval_score(similarity: float, mem_type: str | None, timestamp: str | None,
                    now: datetime | None = None) -> float:
    """Điểm xếp hạng = độ tương đồng × (1 − w + w × độ mới)."""
    w = MEMORY_RECENCY_WEIGHT
    return similarity * ((1 - w) + w * recency_factor(mem_type, timestamp, now))


def _is_sensitive(fact: str) -> bool:
    """Trả True nếu fact chứa thông tin định danh nhạy cảm dạng dãy số."""
    for pat in _SENSITIVE_PATTERNS:
        if pat.search(fact):
            return True
    return False


def _strip_json_fence(text: str) -> str:
    """LLM có thể wrap JSON trong ```json ... ``` — gỡ ra."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class MemoryManager:
    """Quản lý long-term memory cho người dùng theo kiến trúc Mem0."""

    def __init__(
        self,
        openai_client: Any,
        mongo_uri: str,
        milvus_host: str = "localhost",
        milvus_port: str = "19530",
        mongo_db: str | None = None,
    ):
        self.client = openai_client
        self.mongo_uri = mongo_uri
        self.mongo_db_name = (mongo_db or os.getenv("MONGODB_DB") or "chatbot_rag").strip()

        # milvus_host / milvus_port: giữ tham số để tương thích, không còn dùng.
        self._connect_mongo()

    def _connect_mongo(self) -> None:
        """Kết nối MongoDB lưu fact (gọi từ __init__, mỗi lần khởi tạo)."""
        # Khai báo sẵn để mọi nhánh lỗi đều không gây AttributeError (theo bản sửa trên main)
        self._mongo_client = None
        self.mongo_col = None
        self.settings_col = None
        if not (MongoClient and self.mongo_uri):
            logger.warning("Thiếu pymongo hoặc MONGODB_URI — memory bị tắt")
            return
        try:
            self._mongo_client = MongoClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=8000,
                connectTimeoutMS=8000,
                socketTimeoutMS=8000,
            )
            self._mongo_client.admin.command("ping")
            self.mongo_col = self._mongo_client[self.mongo_db_name][MEMORY_MONGO_COLLECTION]
            self.settings_col = self._mongo_client[self.mongo_db_name][MEMORY_SETTINGS_COLLECTION]
            # Index để query nhanh theo user_id
            try:
                self.mongo_col.create_index([("user_id", 1)])
                self.mongo_col.create_index([("mem_id", 1)], unique=True, sparse=True)
                self.settings_col.create_index([("user_id", 1)], unique=True)
            except Exception:
                pass
            logger.info("Kết nối MongoDB '%s.%s'", self.mongo_db_name, MEMORY_MONGO_COLLECTION)
        except Exception as e:
            logger.warning("Không kết nối được MongoDB: %s", e)

    # ── Helpers ────────────────────────────────────────────────────────────

    # ── Cài đặt bộ nhớ theo người dùng ─────────────────────────────────────

    def get_mode(self, user_id: str) -> str:
        """Chế độ bộ nhớ của user: no_sensitive (mặc định) | full | off."""
        col = getattr(self, "settings_col", None)
        if col is not None and user_id:
            try:
                doc = col.find_one({"user_id": user_id})
                if doc and doc.get("mode") in MEMORY_MODES:
                    return doc["mode"]
            except Exception as e:
                logger.warning("get_mode fail: %s", e)
        return MEMORY_DEFAULT_MODE if MEMORY_DEFAULT_MODE in MEMORY_MODES else "no_sensitive"

    def set_mode(self, user_id: str, mode: str) -> str:
        if mode not in MEMORY_MODES:
            raise ValueError(f"mode phải là một trong {MEMORY_MODES}")
        col = getattr(self, "settings_col", None)
        if col is None:
            raise RuntimeError("Chưa kết nối MongoDB")
        col.update_one({"user_id": user_id}, {"$set": {"mode": mode, "updated_at": _now_iso()}}, upsert=True)
        logger.info("Memory mode user=%s → %s", user_id, mode)
        return mode

    def _ready(self) -> bool:
        return self.mongo_col is not None

    def _embed(self, text: str) -> list[float]:
        """Embed 1 đoạn text bằng text-embedding-3-small (dim 1536)."""
        resp = self.client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return resp.data[0].embedding

    # ── EXTRACTION ─────────────────────────────────────────────────────────

    def extract_memories(
        self,
        user_msg: str,
        bot_msg: str,
        recent_summary: str = "",
    ) -> list[dict]:
        """Trích xuất danh sách fact đáng nhớ từ 1 lượt hội thoại.

        Trả về list[{"fact": str, "type": "core|episodic|semantic|procedural"}].
        Lỗi parse / LLM fail → trả về [].
        """
        user_msg = (user_msg or "").strip()
        bot_msg = (bot_msg or "").strip()
        if not user_msg:
            return []

        user_prompt = (
            (f"Tóm tắt các phiên trước (nếu có):\n{recent_summary}\n\n" if recent_summary else "")
            + f"USER:\n{user_msg}\n\nBOT:\n{bot_msg}\n\n"
            "Trích xuất các fact đáng nhớ theo format JSON đã quy định."
        )

        try:
            data = self._llm_json(MEMORY_EXTRACT_MODEL, _EXTRACTION_SYSTEM_PROMPT, user_prompt,
                                  _EXTRACT_SCHEMA, max_tokens=600)
        except Exception as e:
            logger.warning("Extraction fail: %s", e)
            return []
        if isinstance(data, dict):
            data = data.get("facts", [])
        if not isinstance(data, list):
            return []

        out: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            fact = (item.get("fact") or "").strip()
            mtype = (item.get("type") or "").strip().lower()
            if not fact or mtype not in VALID_TYPES:
                continue
            if _FORCE_CORE_RE.search(fact):
                mtype = "core"
            if _is_sensitive(fact):
                logger.info("Bỏ qua fact nhạy cảm: %s…", fact[:50])
                continue
            if len(fact) > 1800:
                fact = fact[:1800]
            out.append({"fact": fact, "type": mtype,
                        "sensitive": bool(item.get("sensitive")) or is_sensitive_topic(fact)})
        return out

    def _llm_json(self, model: str, system: str, user: str, schema: dict, max_tokens: int) -> Any:
        """Gọi LLM với structured outputs (JSON Schema strict) → dict đã parse."""
        resp = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0,
            max_tokens=max_tokens,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        return json.loads(_strip_json_fence(resp.choices[0].message.content or ""))

    def _embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed nhiều câu trong MỘT request."""
        if not texts:
            return []
        resp = self.client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [d.embedding for d in resp.data]

    # ── Lưu trữ: MongoDB là nguồn dữ liệu DUY NHẤT ─────────────────────────
    # Embedding nằm ngay trong document; độ tương đồng tính bằng numpy trên memory của
    # MỘT user (vài chục–vài trăm bản ghi → < 1 ms). Không còn đồng bộ 2 kho (trước đây
    # Milvus bị reset → Mongo trỏ tới vector không tồn tại, memory "mất" mà không báo lỗi).

    def _active_docs(self, user_id: str, with_embedding: bool = True) -> list[dict]:
        proj = {"history": 0} if with_embedding else {"history": 0, "embedding": 0}
        try:
            return list(self.mongo_col.find({"user_id": user_id, "deleted": {"$ne": True}}, proj))
        except Exception as e:
            logger.warning("Mongo find fail user=%s: %s", user_id, e)
            return []

    @staticmethod
    def _similarities(query_emb: list[float], docs: list[dict]) -> list[tuple[float, dict]]:
        """[(cosine, doc)] cho các doc có embedding cùng số chiều với query."""
        q = np.asarray(query_emb, dtype=np.float32)
        qn = float(np.linalg.norm(q)) or 1.0
        rows = [d for d in docs if isinstance(d.get("embedding"), list) and len(d["embedding"]) == len(q)]
        if not rows:
            return []
        m = np.asarray([d["embedding"] for d in rows], dtype=np.float32)
        norms = np.linalg.norm(m, axis=1)
        norms[norms == 0] = 1.0
        sims = (m @ q) / (norms * qn)
        return [(float(s), d) for s, d in zip(sims, rows)]

    def _search_similar(self, user_id: str, emb: list[float], k: int = MEMORY_SIM_TOP) -> list[dict]:
        """Memory cũ của user gần nhất với 1 vector: [{"mem_id","fact","mem_type","score"}]."""
        scored = sorted(self._similarities(emb, self._active_docs(user_id)), key=lambda x: -x[0])[:k]
        return [{"mem_id": d["mem_id"], "fact": d.get("fact"), "mem_type": d.get("mem_type"), "score": s}
                for s, d in scored if d.get("mem_id")]

    def _decide_actions(self, candidates: list[dict], existing: list[dict]) -> list[dict]:
        """MỘT lần gọi LLM quyết định ADD/UPDATE/DELETE/NOOP cho mọi candidate.

        LLM chỉ thấy số thứ tự ngắn (1..N) thay cho id thật; target_id trả về được ánh xạ
        lại và KIỂM TRA thuộc danh sách existing. Trả về list cùng độ dài candidates:
        {"action", "target_id" (mem_id|None), "merged_fact", "reason"}.
        """
        if not existing:
            return [{"action": "ADD", "target_id": None, "merged_fact": None, "reason": "empty_existing"}
                    for _ in candidates]
        local_to_id = {i + 1: e["mem_id"] for i, e in enumerate(existing)}
        user_prompt = (
            "CANDIDATES:\n" + json.dumps(
                [{"index": i, "fact": c["fact"], "type": c["type"]} for i, c in enumerate(candidates)],
                ensure_ascii=False, indent=2)
            + "\n\nEXISTING MEMORIES:\n" + json.dumps(
                [{"id": i + 1, "fact": e["fact"], "type": e.get("mem_type")} for i, e in enumerate(existing)],
                ensure_ascii=False, indent=2)
            + "\n\nQuyết định cho TỪNG candidate."
        )
        fallback = {"action": "ADD", "target_id": None, "merged_fact": None, "reason": "decision_fallback"}
        try:
            data = self._llm_json(MEMORY_UPDATE_MODEL, _BATCH_UPDATE_SYSTEM_PROMPT, user_prompt,
                                  _DECISION_SCHEMA, max_tokens=200 + 150 * len(candidates))
            raw = data.get("decisions", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.warning("Decision LLM fail: %s — fallback ADD", e)
            raw = []

        by_idx: dict[int, dict] = {}
        for d in raw:
            if isinstance(d, dict) and isinstance(d.get("candidate"), int):
                by_idx.setdefault(d["candidate"], d)
        out = []
        for i in range(len(candidates)):
            d = by_idx.get(i)
            if not d:
                out.append(dict(fallback))
                continue
            action = str(d.get("action", "")).upper()
            if action not in {"ADD", "UPDATE", "DELETE", "NOOP"}:
                out.append(dict(fallback))
                continue
            tid = local_to_id.get(d.get("target_id")) if d.get("target_id") is not None else None
            if action in {"UPDATE", "DELETE"} and tid is None:
                # id không tồn tại trong danh sách đưa cho LLM → không được sửa/xoá bừa
                logger.warning("Decision %s với target_id không hợp lệ (%r) → %s",
                               action, d.get("target_id"), "ADD" if action == "UPDATE" else "NOOP")
                action = "ADD" if action == "UPDATE" else "NOOP"
            out.append({"action": action, "target_id": tid,
                        "merged_fact": d.get("merged_fact"), "reason": d.get("reason")})
        return out

    # ── Thực thi từng thao tác ─────────────────────────────────────────────

    def _do_add(self, user_id: str, cand: dict, emb: list[float], now: str) -> str:
        mem_id = uuid.uuid4().hex[:16]
        try:
            self.mongo_col.insert_one({
                "user_id": user_id, "mem_id": mem_id, "fact": cand["fact"], "mem_type": cand["type"],
                "sensitive": bool(cand.get("sensitive")), "embedding": list(map(float, emb)),
                "timestamp": now, "version": 1, "source_chat_id": cand.get("source_chat_id"),
                "history": [{"action": "ADD", "fact": cand["fact"], "at": now}],
            })
        except Exception as e:
            logger.warning("Mongo insert fail: %s", e)
            return "SKIP"
        logger.info("ADD user=%s [%s] %s", user_id, cand["type"], cand["fact"][:80])
        return "ADD"

    def _do_update(self, user_id: str, cand: dict, target_id: str, merged: str, reason: Any, now: str) -> str:
        try:
            merged_emb = self._embed(merged)
        except Exception as e:
            logger.warning("Embed merged fail: %s", e)
            return "SKIP"
        try:
            old = self.mongo_col.find_one({"user_id": user_id, "mem_id": target_id}) or {}
            # Sửa TẠI CHỖ, giữ nguyên mem_id (không còn xoá + chèn như với Milvus auto_id)
            self.mongo_col.update_one(
                {"user_id": user_id, "mem_id": target_id},
                {"$set": {"fact": merged, "mem_type": cand["type"], "timestamp": now,
                          "embedding": list(map(float, merged_emb)),
                          "sensitive": bool(cand.get("sensitive")) or is_sensitive_topic(merged),
                          "version": old.get("version", 1) + 1},
                 "$push": {"history": {"action": "UPDATE", "fact": merged, "at": now, "reason": reason}}},
            )
        except Exception as e:
            logger.warning("Mongo update fail: %s", e)
            return "SKIP"
        logger.info("UPDATE user=%s id=%s: %s", user_id, target_id, merged[:80])
        return "UPDATE"

    def _do_delete(self, user_id: str, cand: dict, target_id: str, reason: Any, now: str) -> str:
        try:
            self.mongo_col.update_one(
                {"user_id": user_id, "mem_id": target_id},
                {"$set": {"deleted": True, "deleted_at": now},
                 "$push": {"history": {"action": "DELETE", "at": now, "reason": reason,
                                       "negated_by": cand["fact"]}}},
            )
        except Exception as e:
            logger.warning("Mongo soft-delete fail: %s", e)
            return "SKIP"
        logger.info("DELETE user=%s id=%s (negated by: %s)", user_id, target_id, cand["fact"][:60])
        return "DELETE"

    # ── UPDATE (gộp) ────────────────────────────────────────────────────────

    def update_memories(self, user_id: str, candidates: list[dict]) -> list[str]:
        """Cập nhật bộ nhớ cho NHIỀU candidate với 1 lần embed + 1 lần gọi LLM quyết định.

        Trả về list thao tác đã thực hiện, cùng thứ tự candidates.
        """
        if not self._ready() or not user_id:
            return ["SKIP"] * len(candidates)
        valid: list[tuple[int, dict]] = []
        results = ["SKIP"] * len(candidates)
        for i, c in enumerate(candidates):
            fact = (c.get("fact") or "").strip()
            mtype = (c.get("type") or "").strip().lower()
            if fact and mtype in VALID_TYPES:
                valid.append((i, {**c, "fact": fact, "type": mtype}))
        if not valid:
            return results

        try:
            embs = self._embed_many([c["fact"] for _, c in valid])
        except Exception as e:
            logger.warning("Embed batch fail: %s", e)
            return results

        # Gộp memory tương đồng của mọi candidate (khử trùng theo mem_id)
        existing: dict[str, dict] = {}
        for emb in embs:
            for e in self._search_similar(user_id, emb):
                existing.setdefault(e["mem_id"], e)
        decisions = self._decide_actions([c for _, c in valid], list(existing.values()))

        now = _now_iso()
        touched: set[str] = set()  # một memory cũ chỉ bị sửa/xoá một lần trong lượt
        for (i, cand), emb, d in zip(valid, embs, decisions):
            action, target = d["action"], d["target_id"]
            # Guard: LLM hay nhầm DELETE cho fact có giá trị thay thế ("chuyển sang …")
            if action == "DELETE" and _looks_like_replacement(cand["fact"]) and len(cand["fact"]) >= 25:
                logger.info("Guard: DELETE → UPDATE vì candidate có thông tin thay thế")
                action = "UPDATE"
            if action in {"UPDATE", "DELETE"} and target in touched:
                action = "ADD" if action == "UPDATE" else "NOOP"
            if action == "ADD":
                results[i] = self._do_add(user_id, cand, emb, now)
            elif action == "UPDATE":
                touched.add(target)
                merged = (d.get("merged_fact") or cand["fact"]).strip()
                results[i] = self._do_update(user_id, cand, target, merged, d.get("reason"), now)
            elif action == "DELETE":
                touched.add(target)
                results[i] = self._do_delete(user_id, cand, target, d.get("reason"), now)
            else:
                logger.info("NOOP user=%s candidate=%s", user_id, cand["fact"][:60])
                results[i] = "NOOP"
        return results

    def update_memory(self, user_id: str, candidate_fact: dict) -> str:
        """Tương thích ngược: cập nhật 1 candidate."""
        return self.update_memories(user_id, [candidate_fact])[0]

    # ── RETRIEVE ───────────────────────────────────────────────────────────

    def retrieve_memories(
        self,
        user_id: str,
        query: str,
        top_k: int = MEMORY_TOP_K,
    ) -> list[dict]:
        """Lấy memory liên quan của user_id để inject vào context.

        Chiến lược:
        1. Vector search top_k bằng query (lấy memory liên quan ngữ nghĩa).
        2. LUÔN bổ sung ALL core memories (hồ sơ user — luôn cần để cá nhân hoá,
           kể cả khi query không match semantic với fact "sinh năm X").
        3. Pin thêm fact hồ sơ từ Mongo (năm sinh/giới tính/trường học) dù extractor
           gắn nhầm type — tránh trường hợp hỏi "đủ tuổi kết hôn" mà không thấy năm sinh.
        Dedup theo mem_id + nội dung fact.

        Trả về list[{"fact", "type", "score", "timestamp", "mem_id"}].
        """
        if not self._ready() or not user_id:
            return []
        mode = self.get_mode(user_id)
        if mode == "off":
            return []
        out = self._retrieve_all_sources(user_id, query, top_k)
        if mode == "no_sensitive":
            out = [m for m in out if not is_sensitive_topic(m.get("fact") or "")]
        return out

    def _retrieve_all_sources(self, user_id: str, query: str, top_k: int) -> list[dict]:
        """
        1. Ngữ nghĩa: cosine giữa query và memory của user → bỏ episodic hết hạn + dưới ngưỡng
           → xếp theo độ tương đồng có tính độ mới → giữ top_k.
        2. LUÔN kèm mọi memory core (hồ sơ) — kể cả khi query không khớp ngữ nghĩa.
        3. Kèm fact hồ sơ (năm sinh / giới tính / trường) dù extractor gắn nhầm loại.
        """
        docs = self._active_docs(user_id)
        now = datetime.now()
        out: list[dict] = []
        seen: set[str] = set()

        def add(d: dict, score: float, sim: float | None = None):
            key = d.get("mem_id") or (d.get("fact") or "").strip().lower()
            if not key or key in seen:
                return
            seen.add(key)
            item = {"mem_id": d.get("mem_id"), "fact": d.get("fact"), "type": d.get("mem_type"),
                    "timestamp": d.get("timestamp"), "score": round(score, 4)}
            if sim is not None:
                item["similarity"] = round(sim, 4)
            out.append(item)

        if query and docs:
            try:
                ranked = []
                for sim, d in self._similarities(self._embed(query), docs):
                    if sim < MEMORY_MIN_SIMILARITY or is_expired(d.get("mem_type"), d.get("timestamp"), now):
                        continue
                    ranked.append((retrieval_score(sim, d.get("mem_type"), d.get("timestamp"), now), sim, d))
                ranked.sort(key=lambda x: x[0], reverse=True)
                for score, sim, d in ranked[:top_k]:
                    add(d, score, sim)
            except Exception as e:
                logger.warning("retrieve_memories semantic fail user=%s: %s", user_id, e)

        for d in docs:
            if d.get("mem_type") == "core" or _PROFILE_FACT_RE.search(d.get("fact") or ""):
                add(d, 1.0)
        return out

    def format_for_context(self, memories: list[dict]) -> str:
        """Format list memory thành đoạn text gắn vào system prompt."""
        if not memories:
            return ""
        # Nhóm theo type cho dễ đọc
        order = ["core", "episodic", "semantic", "procedural"]
        grouped: dict[str, list[str]] = {k: [] for k in order}
        for m in memories:
            t = (m.get("type") or "").lower()
            grouped.setdefault(t, []).append(m.get("fact") or "")
        lines = ["[Bộ nhớ về người dùng — dùng để cá nhân hóa câu trả lời]"]
        for t in order:
            for f in grouped.get(t, []):
                if f:
                    lines.append(f"- ({t}) {f}")
        return "\n".join(lines)

    # ── ASYNC WRAPPER ──────────────────────────────────────────────────────

    async def process_turn_async(
        self,
        user_id: str,
        user_msg: str,
        bot_msg: str,
        chat_id: str | None = None,
    ) -> dict:
        """Chạy extraction + update không chặn response chính.

        Gọi qua asyncio.create_task() từ route sau khi đã stream xong.
        """
        return await asyncio.to_thread(self.process_turn, user_id, user_msg, bot_msg, chat_id)

    def process_turn(self, user_id: str, user_msg: str, bot_msg: str, chat_id: str | None = None) -> dict:
        """Trích xuất + cập nhật memory cho 1 lượt, KHOÁ theo user (lượt sau chờ lượt trước)."""
        if not user_id or not self._ready():
            return {"skipped": True, "reason": "no_user_or_not_ready"}
        mode = self.get_mode(user_id)
        if mode == "off":
            return {"skipped": True, "reason": "memory_off"}
        with _user_lock(user_id):
            try:
                facts = self.extract_memories(user_msg, bot_msg)
            except Exception as e:
                logger.warning("process_turn extract fail: %s", e)
                return {"skipped": True, "reason": f"extract_fail:{e}"}
            dropped = 0
            if mode == "no_sensitive":
                kept = [f for f in facts if not f.get("sensitive")]
                dropped = len(facts) - len(kept)
                if dropped:
                    logger.info("Bỏ %d fact nhạy cảm (user chưa cho phép lưu)", dropped)
                facts = kept
            if not facts:
                return {"extracted": 0, "actions": [], "dropped_sensitive": dropped}
            for f in facts:
                if chat_id:
                    f["source_chat_id"] = chat_id
            try:
                acts = self.update_memories(user_id, facts)
            except Exception as e:
                logger.warning("update_memories fail: %s", e)
                acts = ["ERROR"] * len(facts)
            return {"extracted": len(facts), "dropped_sensitive": dropped,
                    "actions": [{"fact": f["fact"][:80], "action": a} for f, a in zip(facts, acts)]}

    # ── CRUD cho endpoint quản lý ──────────────────────────────────────────

    def list_user_memories(self, user_id: str) -> list[dict]:
        """Liệt kê toàn bộ memory (chưa xoá) của user — đọc từ Mongo."""
        if self.mongo_col is None or not user_id:
            return []
        try:
            docs = list(self.mongo_col.find(
                {"user_id": user_id, "deleted": {"$ne": True}},
                {"history": 0, "embedding": 0},
            ).sort("timestamp", -1))
            out = []
            now = datetime.now()
            for d in docs:
                d["_id"] = str(d.get("_id"))
                d["expired"] = is_expired(d.get("mem_type"), d.get("timestamp"), now)
                d["sensitive"] = bool(d.get("sensitive")) or is_sensitive_topic(d.get("fact") or "")
                out.append(d)
            return out
        except Exception as e:
            logger.warning("list_user_memories fail: %s", e)
            return []

    def delete_memory(self, user_id: str, mem_id: str) -> bool:
        """Xoá hẳn 1 memory theo yêu cầu của người dùng."""
        if not self._ready() or not user_id:
            return False
        try:
            res = self.mongo_col.delete_one({"user_id": user_id, "mem_id": str(mem_id)})
            logger.info("Manual DELETE user=%s id=%s", user_id, mem_id)
            return res.deleted_count > 0
        except Exception as e:
            logger.warning("delete_memory fail: %s", e)
            return False

    def delete_all_for_user(self, user_id: str) -> int:
        """Xoá toàn bộ memory của user (quyền được quên). Trả về số lượng đã xoá."""
        if not self._ready() or not user_id:
            return 0
        try:
            res = self.mongo_col.delete_many({"user_id": user_id})
            logger.info("Right-to-be-forgotten user=%s deleted=%d", user_id, res.deleted_count)
            return res.deleted_count
        except Exception as e:
            logger.warning("delete_all_for_user fail: %s", e)
            return 0
