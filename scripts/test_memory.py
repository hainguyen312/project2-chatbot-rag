"""Smoke test cho MemoryManager: extract → update → retrieve → delete."""
from __future__ import annotations

import asyncio
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

from services.memory_manager import MemoryManager


def main():
    client = OpenAI()
    mm = MemoryManager(openai_client=client, mongo_uri=os.getenv("MONGODB_URI", ""))
    assert mm._ready(), "MemoryManager chưa sẵn sàng"

    user_id = "test_user_demo_001"

    # Clear trước test cho sạch
    print(">> Xoá toàn bộ memory cũ của test user…")
    mm.delete_all_for_user(user_id)

    # Turn 1
    print("\n=== TURN 1 ===")
    u1 = "Tôi mở quán cà phê ở Hạ Long, muốn biết thủ tục đăng ký kinh doanh hộ cá thể"
    b1 = ("Theo Nghị định 01/2021/NĐ-CP, hộ kinh doanh đăng ký tại UBND cấp huyện. "
          "Hồ sơ gồm giấy đề nghị đăng ký, bản sao CCCD chủ hộ, hợp đồng thuê địa điểm.")
    facts = mm.extract_memories(u1, b1)
    print(f"  Extracted {len(facts)} facts:")
    for f in facts:
        print(f"   - [{f['type']}] {f['fact']}")
    for f in facts:
        f["source_chat_id"] = "chat_demo_001"
        act = mm.update_memory(user_id, f)
        print(f"   → {act}")

    # Turn 2 — câu hỏi mới, retrieve phải lấy được context
    print("\n=== TURN 2 (retrieve) ===")
    q2 = "Nhân viên quán có cần đóng bảo hiểm xã hội không?"
    mems = mm.retrieve_memories(user_id, q2, top_k=5)
    print(f"  Retrieved {len(mems)} memories cho query: {q2!r}")
    for m in mems:
        print(f"   - score={m['score']:.3f} [{m['type']}] {m['fact']}")
    print("\n  format_for_context:")
    print(mm.format_for_context(mems))

    # Turn 3 — update memory (đổi ngành)
    print("\n=== TURN 3 (UPDATE) ===")
    u3 = "Mình vừa chuyển sang mở nhà hàng chứ không bán cà phê nữa, ở Hạ Long"
    b3 = "Việc thay đổi ngành nghề cần đăng ký bổ sung với cơ quan đăng ký kinh doanh."
    facts3 = mm.extract_memories(u3, b3)
    for f in facts3:
        f["source_chat_id"] = "chat_demo_002"
        act = mm.update_memory(user_id, f)
        print(f"   [{f['type']}] {f['fact'][:60]}… → {act}")

    # List final
    print("\n=== FINAL list ===")
    items = mm.list_user_memories(user_id)
    print(f"  Tổng {len(items)} memory active:")
    for m in items:
        print(f"   - id={m.get('milvus_id')} [{m.get('mem_type')}] v{m.get('version', 1)} {m.get('fact')}")

    # Cleanup
    print("\n>> Xoá test user…")
    n = mm.delete_all_for_user(user_id)
    print(f"  Đã xoá {n} record")


if __name__ == "__main__":
    main()
