"""
MemoryManager — Hệ thống bộ nhớ dài hạn (Mem0-style) cho Agentic RAG pháp luật.

Kiến trúc:
- Extraction: gpt-4o-mini trích xuất các fact bền vững từ mỗi lượt hội thoại,
  phân loại theo 4 loại memory: core / episodic / semantic / procedural.
- Update: với mỗi candidate fact, tìm các memory tương đồng của user, để LLM
  quyết định ADD / UPDATE / DELETE / NOOP (giống thuật toán Mem0).
- Retrieve: vector search Milvus, filter theo user_id, trả về top_k.

Storage:
- Milvus `user_memory`: vector + metadata để search ngữ nghĩa nhanh.
- MongoDB `memories`: raw fact + lịch sử thay đổi (audit log).

Cả 2 store được giữ đồng bộ qua trường `milvus_id` (Milvus PK lưu trong Mongo).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Any

from pymilvus import Collection, connections, utility

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


MEMORY_COLLECTION_NAME = "user_memory"
MEMORY_EMBEDDING_DIM = 1536
MEMORY_MONGO_COLLECTION = os.getenv("MEMORY_MONGO_COLLECTION", "memories")
MEMORY_TOP_K = int(os.getenv("MEMORY_TOP_K", "5"))
MEMORY_EXTRACT_MODEL = os.getenv("MEMORY_EXTRACT_MODEL", "gpt-4o-mini")
MEMORY_UPDATE_MODEL = os.getenv("MEMORY_UPDATE_MODEL", "gpt-4o-mini")
MEMORY_SIM_TOP = 5  # số memory tương đồng đưa vào prompt update

VALID_TYPES = {"core", "episodic", "semantic", "procedural"}

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


# ── Prompts ───────────────────────────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """Bạn là bộ trích xuất bộ nhớ dài hạn cho trợ lý pháp luật Việt Nam.

Nhiệm vụ: đọc 1 lượt hội thoại (user + bot) và trích xuất các FACT BỀN VỮNG,
hữu ích cho cá nhân hóa câu trả lời pháp lý trong các phiên trò chuyện sau.

4 LOẠI MEMORY:
- core: hồ sơ người dùng ổn định — nghề nghiệp, địa phương, vai trò, ngành kinh doanh.
  Ví dụ: "Người dùng làm kế toán ở Hà Nội", "Chủ doanh nghiệp nhỏ ngành F&B".
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
5. Nếu lượt hội thoại không có thông tin đáng nhớ → trả về `[]`.

ĐỊNH DẠNG OUTPUT: CHỈ trả về JSON array hợp lệ, không có markdown, không giải thích.
Mỗi phần tử là object: {"fact": "<câu tiếng Việt>", "type": "core|episodic|semantic|procedural"}

VÍ DỤ:
Input user: "Tôi mở quán cà phê ở Hạ Long, muốn biết thủ tục đăng ký kinh doanh hộ cá thể"
Input bot: "Theo Nghị định 01/2021/NĐ-CP, hộ kinh doanh đăng ký tại UBND cấp huyện..."
Output:
[
  {"fact": "Người dùng mở quán cà phê ở Hạ Long, Quảng Ninh", "type": "core"},
  {"fact": "Đang tìm hiểu thủ tục đăng ký kinh doanh hộ cá thể", "type": "episodic"}
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


def _now_iso() -> str:
    return datetime.now().isoformat()


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

        # Milvus: tận dụng connection alias "default" đã có (two_stage_search.py
        # đã connect sẵn). Nếu chưa, connect lại (pymilvus connect idempotent).
        try:
            if not connections.has_connection("default"):
                connections.connect("default", host=milvus_host, port=milvus_port)
        except Exception as e:
            logger.warning("Không kết nối được Milvus: %s", e)

        self.collection: Collection | None = None
        try:
            if utility.has_collection(MEMORY_COLLECTION_NAME):
                self.collection = Collection(MEMORY_COLLECTION_NAME)
                self.collection.load()
                logger.info("Loaded Milvus collection '%s'", MEMORY_COLLECTION_NAME)
            else:
                logger.warning(
                    "Milvus collection '%s' chưa tồn tại. "
                    "Chạy `python scripts/init_memory_collection.py` để khởi tạo.",
                    MEMORY_COLLECTION_NAME,
                )
        except Exception as e:
            logger.warning("Lỗi load Milvus collection: %s", e)

        # Mongo
        self.mongo_col = None
        if MongoClient and mongo_uri:
            try:
                self._mongo_client = MongoClient(
                    mongo_uri,
                    serverSelectionTimeoutMS=8000,
                    connectTimeoutMS=8000,
                    socketTimeoutMS=8000,
                )
                self._mongo_client.admin.command("ping")
                self.mongo_col = self._mongo_client[self.mongo_db_name][MEMORY_MONGO_COLLECTION]
                # Index để query nhanh theo user_id
                try:
                    self.mongo_col.create_index([("user_id", 1)])
                    self.mongo_col.create_index([("user_id", 1), ("milvus_id", 1)])
                except Exception:
                    pass
                logger.info("Kết nối MongoDB '%s.%s'", self.mongo_db_name, MEMORY_MONGO_COLLECTION)
            except Exception as e:
                logger.warning("Không kết nối được MongoDB: %s", e)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _ready(self) -> bool:
        return self.collection is not None and self.mongo_col is not None

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
            resp = self.client.chat.completions.create(
                model=MEMORY_EXTRACT_MODEL,
                messages=[
                    {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=600,
            )
            raw = _strip_json_fence(resp.choices[0].message.content or "")
            if not raw or raw == "[]":
                return []
            data = json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Extraction parse fail: %s", e)
            return []

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
            if _is_sensitive(fact):
                logger.info("Bỏ qua fact nhạy cảm: %s…", fact[:50])
                continue
            if len(fact) > 1800:
                fact = fact[:1800]
            out.append({"fact": fact, "type": mtype})
        return out

    # ── UPDATE ─────────────────────────────────────────────────────────────

    def _decide_action(
        self,
        candidate: dict,
        existing: list[dict],
    ) -> dict:
        """Gọi LLM để quyết định ADD/UPDATE/DELETE/NOOP."""
        existing_payload = [
            {"id": e["milvus_id"], "fact": e["fact"], "type": e.get("mem_type")}
            for e in existing
        ]
        user_prompt = (
            f"CANDIDATE FACT:\n{json.dumps(candidate, ensure_ascii=False)}\n\n"
            f"EXISTING MEMORIES (tương đồng nhất, top-{len(existing_payload)}):\n"
            f"{json.dumps(existing_payload, ensure_ascii=False, indent=2)}\n\n"
            "Quyết định thao tác theo format JSON đã quy định."
        )
        try:
            resp = self.client.chat.completions.create(
                model=MEMORY_UPDATE_MODEL,
                messages=[
                    {"role": "system", "content": _UPDATE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=300,
            )
            raw = _strip_json_fence(resp.choices[0].message.content or "")
            data = json.loads(raw)
        except Exception as e:
            logger.warning("Update decision parse fail: %s — fallback ADD", e)
            return {"action": "ADD", "reason": "decision_fail_fallback"}

        action = (data.get("action") or "").upper()
        if action not in {"ADD", "UPDATE", "DELETE", "NOOP"}:
            return {"action": "ADD", "reason": "invalid_action_fallback"}
        return data

    def _insert_milvus(
        self,
        user_id: str,
        mem_type: str,
        fact: str,
        embedding: list[float],
        timestamp: str,
    ) -> int | None:
        """Insert 1 record vào Milvus, trả về primary key (auto_id)."""
        if not self.collection:
            return None
        try:
            res = self.collection.insert([
                [user_id],
                [mem_type],
                [fact],
                [embedding],
                [timestamp],
            ])
            self.collection.flush()
            pks = list(res.primary_keys)
            return int(pks[0]) if pks else None
        except Exception as e:
            logger.warning("Milvus insert fail: %s", e)
            return None

    def _delete_milvus(self, milvus_id: int) -> bool:
        if not self.collection:
            return False
        try:
            self.collection.delete(expr=f"id in [{int(milvus_id)}]")
            self.collection.flush()
            return True
        except Exception as e:
            logger.warning("Milvus delete fail: %s", e)
            return False

    def update_memory(self, user_id: str, candidate_fact: dict) -> str:
        """So khớp candidate với memory đã có, LLM quyết định ADD/UPDATE/DELETE/NOOP.

        Trả về tên thao tác đã thực hiện (chuỗi).
        """
        if not self._ready() or not user_id:
            return "SKIP"

        fact_text = candidate_fact.get("fact", "").strip()
        mem_type = candidate_fact.get("type", "").strip().lower()
        if not fact_text or mem_type not in VALID_TYPES:
            return "SKIP"

        # 1. Embed candidate
        try:
            emb = self._embed(fact_text)
        except Exception as e:
            logger.warning("Embed fail: %s", e)
            return "SKIP"

        # 2. Search top-K memory tương đồng của user trong Milvus
        existing: list[dict] = []
        try:
            results = self.collection.search(
                data=[emb],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"ef": 64}},
                limit=MEMORY_SIM_TOP,
                expr=f'user_id == "{user_id}"',
                output_fields=["user_id", "mem_type", "fact", "timestamp"],
            )
            for hit in results[0]:
                existing.append({
                    "milvus_id": int(hit.id),
                    "fact": hit.entity.get("fact"),
                    "mem_type": hit.entity.get("mem_type"),
                    "score": float(hit.distance),
                })
        except Exception as e:
            logger.warning("Milvus search fail (update_memory): %s", e)

        # 3. Nếu chưa có memory tương đồng nào → ADD luôn
        if not existing:
            decision = {"action": "ADD", "reason": "empty_existing"}
        else:
            decision = self._decide_action(candidate_fact, existing)

        action = decision.get("action", "NOOP").upper()

        # Guard: LLM hay nhầm DELETE cho các fact có thay thế (vd "chuyển sang
        # trường khác"). Convert DELETE → UPDATE nếu fact mới có dấu hiệu thay thế
        # và đủ dài để mang thông tin có nghĩa.
        if action == "DELETE" and _looks_like_replacement(fact_text) and len(fact_text) >= 25:
            logger.info("Guard: DELETE → UPDATE vì candidate có thông tin thay thế")
            action = "UPDATE"
            decision["action"] = "UPDATE"
            # Giữ target_id từ LLM. Nếu thiếu merged_fact, dùng candidate fact.
            decision.setdefault("merged_fact", fact_text)
        now = _now_iso()

        # 4. Thực thi thao tác trên cả Milvus + Mongo
        if action == "ADD":
            pk = self._insert_milvus(user_id, mem_type, fact_text, emb, now)
            if pk is None:
                return "SKIP"
            try:
                self.mongo_col.insert_one({
                    "user_id": user_id,
                    "milvus_id": pk,
                    "fact": fact_text,
                    "mem_type": mem_type,
                    "timestamp": now,
                    "version": 1,
                    "source_chat_id": candidate_fact.get("source_chat_id"),
                    "history": [{"action": "ADD", "fact": fact_text, "at": now}],
                })
            except Exception as e:
                logger.warning("Mongo insert fail: %s", e)
            logger.info("ADD user=%s [%s] %s", user_id, mem_type, fact_text[:80])
            return "ADD"

        if action == "UPDATE":
            target_id = decision.get("target_id")
            merged = (decision.get("merged_fact") or fact_text).strip()
            if not isinstance(target_id, int) and not str(target_id).isdigit():
                logger.warning("UPDATE thiếu target_id hợp lệ → fallback ADD")
                return self.update_memory(user_id, {**candidate_fact, "_force_add": True}) \
                    if not candidate_fact.get("_force_add") else "SKIP"
            target_id = int(target_id)

            # Embed bản merged để index nhất quán
            try:
                merged_emb = self._embed(merged)
            except Exception as e:
                logger.warning("Embed merged fail: %s", e)
                return "SKIP"

            # Milvus không hỗ trợ in-place update với auto_id → delete + insert mới
            self._delete_milvus(target_id)
            new_pk = self._insert_milvus(user_id, mem_type, merged, merged_emb, now)
            if new_pk is None:
                return "SKIP"

            try:
                old_doc = self.mongo_col.find_one({"user_id": user_id, "milvus_id": target_id})
                hist = (old_doc or {}).get("history", [])
                hist.append({"action": "UPDATE", "fact": merged, "at": now,
                             "reason": decision.get("reason")})
                self.mongo_col.update_one(
                    {"user_id": user_id, "milvus_id": target_id},
                    {"$set": {
                        "milvus_id": new_pk,
                        "fact": merged,
                        "mem_type": mem_type,
                        "timestamp": now,
                        "version": (old_doc or {}).get("version", 1) + 1,
                        "history": hist,
                    }},
                    upsert=True,
                )
            except Exception as e:
                logger.warning("Mongo update fail: %s", e)
            logger.info("UPDATE user=%s id=%s → %s: %s",
                        user_id, target_id, new_pk, merged[:80])
            return "UPDATE"

        if action == "DELETE":
            target_id = decision.get("target_id")
            if target_id is None:
                return "NOOP"
            target_id = int(target_id)
            self._delete_milvus(target_id)
            try:
                self.mongo_col.update_one(
                    {"user_id": user_id, "milvus_id": target_id},
                    {"$set": {
                        "deleted": True,
                        "deleted_at": now,
                    },
                     "$push": {
                        "history": {"action": "DELETE", "at": now,
                                    "reason": decision.get("reason"),
                                    "negated_by": fact_text},
                     }},
                )
            except Exception as e:
                logger.warning("Mongo soft-delete fail: %s", e)
            logger.info("DELETE user=%s id=%s (negated by: %s)",
                        user_id, target_id, fact_text[:60])
            return "DELETE"

        # NOOP
        logger.info("NOOP user=%s candidate=%s", user_id, fact_text[:60])
        return "NOOP"

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
        Dedup theo milvus_id, giới hạn tổng ≤ top_k + 8.

        Trả về list[{"fact", "type", "score", "timestamp", "milvus_id"}].
        """
        if not self.collection or not user_id:
            return []

        seen: set[int] = set()
        out: list[dict] = []

        # 1. Semantic search
        if query:
            try:
                emb = self._embed(query)
                results = self.collection.search(
                    data=[emb],
                    anns_field="embedding",
                    param={"metric_type": "COSINE", "params": {"ef": 64}},
                    limit=top_k,
                    expr=f'user_id == "{user_id}"',
                    output_fields=["user_id", "mem_type", "fact", "timestamp"],
                )
                for hit in results[0]:
                    mid = int(hit.id)
                    if mid in seen:
                        continue
                    seen.add(mid)
                    out.append({
                        "milvus_id": mid,
                        "fact": hit.entity.get("fact"),
                        "type": hit.entity.get("mem_type"),
                        "timestamp": hit.entity.get("timestamp"),
                        "score": round(float(hit.distance), 4),
                    })
            except Exception as e:
                logger.warning("retrieve_memories semantic fail user=%s: %s", user_id, e)

        # 2. Luôn include core memories (user profile) — bất kể semantic match
        try:
            core_rows = self.collection.query(
                expr=f'user_id == "{user_id}" and mem_type == "core"',
                output_fields=["id", "mem_type", "fact", "timestamp"],
                limit=8,
            )
            for r in core_rows:
                mid = int(r["id"])
                if mid in seen:
                    continue
                seen.add(mid)
                out.append({
                    "milvus_id": mid,
                    "fact": r.get("fact"),
                    "type": r.get("mem_type"),
                    "timestamp": r.get("timestamp"),
                    "score": 1.0,  # core luôn ưu tiên cao
                })
        except Exception as e:
            logger.warning("retrieve_memories core fail user=%s: %s", user_id, e)

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
        if not user_id or not self._ready():
            return {"skipped": True, "reason": "no_user_or_not_ready"}

        try:
            facts = await asyncio.to_thread(
                self.extract_memories, user_msg, bot_msg
            )
        except Exception as e:
            logger.warning("process_turn_async extract fail: %s", e)
            return {"skipped": True, "reason": f"extract_fail:{e}"}

        if not facts:
            return {"extracted": 0, "actions": []}

        actions = []
        for fact in facts:
            if chat_id:
                fact["source_chat_id"] = chat_id
            try:
                act = await asyncio.to_thread(self.update_memory, user_id, fact)
            except Exception as e:
                logger.warning("update_memory fail: %s", e)
                act = "ERROR"
            actions.append({"fact": fact["fact"][:80], "action": act})
        return {"extracted": len(facts), "actions": actions}

    # ── CRUD cho endpoint quản lý ──────────────────────────────────────────

    def list_user_memories(self, user_id: str) -> list[dict]:
        """Liệt kê toàn bộ memory (chưa xoá) của user — đọc từ Mongo."""
        if self.mongo_col is None or not user_id:
            return []
        try:
            docs = list(self.mongo_col.find(
                {"user_id": user_id, "deleted": {"$ne": True}},
                {"history": 0},
            ).sort("timestamp", -1))
            out = []
            for d in docs:
                d["_id"] = str(d.get("_id"))
                out.append(d)
            return out
        except Exception as e:
            logger.warning("list_user_memories fail: %s", e)
            return []

    def delete_memory(self, user_id: str, milvus_id: int) -> bool:
        """Xoá 1 memory cụ thể (cả Milvus + Mongo)."""
        if not self._ready() or not user_id:
            return False
        self._delete_milvus(milvus_id)
        try:
            res = self.mongo_col.delete_one({"user_id": user_id, "milvus_id": int(milvus_id)})
            logger.info("Manual DELETE user=%s id=%s", user_id, milvus_id)
            return res.deleted_count > 0
        except Exception as e:
            logger.warning("delete_memory mongo fail: %s", e)
            return False

    def delete_all_for_user(self, user_id: str) -> int:
        """Xoá toàn bộ memory của user (quyền được quên). Trả về số lượng đã xoá."""
        if not self._ready() or not user_id:
            return 0
        try:
            docs = list(self.mongo_col.find({"user_id": user_id}, {"milvus_id": 1}))
            ids = [int(d["milvus_id"]) for d in docs if d.get("milvus_id") is not None]
            if ids and self.collection is not None:
                expr = "id in [" + ",".join(str(i) for i in ids) + "]"
                try:
                    self.collection.delete(expr=expr)
                    self.collection.flush()
                except Exception as e:
                    logger.warning("Bulk Milvus delete fail: %s", e)
            res = self.mongo_col.delete_many({"user_id": user_id})
            logger.info("Right-to-be-forgotten user=%s deleted=%d", user_id, res.deleted_count)
            return res.deleted_count
        except Exception as e:
            logger.warning("delete_all_for_user fail: %s", e)
            return 0
