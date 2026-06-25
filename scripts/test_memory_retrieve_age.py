"""Reproduce: memory 'sinh năm 2003' có retrieve được khi hỏi
'tôi có đủ tuổi kết hôn không' không?"""
from __future__ import annotations
import os, sys, warnings
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
    uid = "test_age_retrieve_001"
    mm.delete_all_for_user(uid)

    # Setup: thêm memory về năm sinh
    print(">> Setup memory")
    for f in [
        {"fact": "Người dùng sinh năm 2003", "type": "core"},
        {"fact": "Người dùng là sinh viên ngành CNTT", "type": "core"},
        {"fact": "Người dùng quan tâm Luật Lao động", "type": "semantic"},
    ]:
        f["source_chat_id"] = "setup"
        print(f"  → {mm.update_memory(uid, f)}: {f['fact']}")

    queries = [
        "tôi có đủ tuổi kết hôn không",
        "tôi có được đăng ký kết hôn không",
        "tôi bao nhiêu tuổi",
        "thủ tục kết hôn",
        "luật lao động",
    ]
    for q in queries:
        print(f"\n>> Query: {q!r}")
        mems = mm.retrieve_memories(uid, q, top_k=5)
        for m in mems:
            print(f"   score={m['score']:.3f} [{m['type']}] {m['fact']}")
        if not mems:
            print("   (no memory matched)")

    mm.delete_all_for_user(uid)


if __name__ == "__main__":
    main()
