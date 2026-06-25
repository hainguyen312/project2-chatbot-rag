"""Reproduce bug: user nói đang học trường A → chuyển sang trường B,
memory KHÔNG UPDATE mà ADD thành 2 record (dẫn đến duplicate)."""
from __future__ import annotations

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
    assert mm._ready()
    uid = "test_update_school_001"
    mm.delete_all_for_user(uid)

    print("=== Turn 1: học ĐH Bách Khoa ===")
    facts1 = mm.extract_memories(
        "Tôi đang học ngành Công nghệ thông tin tại Đại học Bách Khoa Hà Nội, muốn hỏi về quy chế tốt nghiệp",
        "Quy chế tốt nghiệp được quy định tại...",
    )
    for f in facts1:
        f["source_chat_id"] = "c1"
        print(f"  extract: [{f['type']}] {f['fact']}")
        print(f"   → {mm.update_memory(uid, f)}")

    print("\nMongo state sau turn 1:")
    for m in mm.list_user_memories(uid):
        print(f"  id={m['milvus_id']} [{m['mem_type']}] {m['fact']}")

    print("\n=== Turn 2: chuyển sang ĐH Kinh tế Quốc dân ===")
    facts2 = mm.extract_memories(
        "Mình vừa chuyển sang học Đại học Kinh tế Quốc dân, ngành Quản trị kinh doanh",
        "Việc chuyển trường cần thực hiện thủ tục…",
    )
    for f in facts2:
        f["source_chat_id"] = "c2"
        print(f"  extract: [{f['type']}] {f['fact']}")
        act = mm.update_memory(uid, f)
        print(f"   → {act}")

    print("\nMongo state sau turn 2 (kỳ vọng: 1 core đã update, KHÔNG duplicate):")
    for m in mm.list_user_memories(uid):
        print(f"  id={m['milvus_id']} [{m['mem_type']}] v{m.get('version',1)} {m['fact']}")

    print("\n>> Cleanup")
    n = mm.delete_all_for_user(uid)
    print(f"  deleted {n}")


if __name__ == "__main__":
    main()
