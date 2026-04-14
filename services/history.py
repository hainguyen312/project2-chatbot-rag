import os
import json
import uuid
from openai import OpenAI
from datetime import datetime

CHAT_FILE = os.path.join("chat_history", "conversations.json")

def cleanup_empty_chats():
    """
    Xoá các hội thoại không có tin nhắn (rác).
    """
    chats = load_chats()
    to_delete = [cid for cid, chat in chats.items() if not chat.get("messages")]
    for cid in to_delete:
        del chats[cid]

    with open(CHAT_FILE, "w", encoding="utf-8") as f:
        json.dump(chats, f, ensure_ascii=False, indent=2)

def load_chats():
    """
    Đọc toàn bộ lịch sử hội thoại từ file JSON
    Trả về dict dạng {chat_id: {...}}
    """
    if not os.path.exists(CHAT_FILE):
        return {}
    try:
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                # Format cũ: danh sách
                return {str(i): chat for i, chat in enumerate(data)}
            elif isinstance(data, dict):
                return data
            else:
                return {}
    except Exception:
        return {}

def save_chat(chat_id, title, messages):
    """
    Ghi đè hoặc cập nhật hội thoại có sẵn
    """
    chats = load_chats()
    now = datetime.now().isoformat()

    if chat_id in chats:
        # Cập nhật cuộc hội thoại cũ
        chats[chat_id]["title"] = title
        chats[chat_id]["messages"] = messages
        chats[chat_id]["updated_at"] = now
    else:
        # Nếu chưa có, tạo mới
        chats[chat_id] = {
            "title": title,
            "messages": messages,
            "created_at": now,
            "updated_at": now
        }

    with open(CHAT_FILE, "w", encoding="utf-8") as f:
        json.dump(chats, f, ensure_ascii=False, indent=2)

def create_new_chat():
    """
    Tạo cuộc hội thoại mới, trả về chat_id
    """
    chats = load_chats()
    new_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    chats[new_id] = {
        "title": "Cuộc trò chuyện mới",
        "messages": [],
        "created_at": now,
        "updated_at": now
    }

    with open(CHAT_FILE, "w", encoding="utf-8") as f:
        json.dump(chats, f, ensure_ascii=False, indent=2)

    return new_id

def rename_chat(chat_id, new_title):
    """
    Đổi tên cuộc hội thoại
    """
    chats = load_chats()
    if chat_id in chats:
        chats[chat_id]["title"] = new_title
        chats[chat_id]["updated_at"] = datetime.now().isoformat()
        with open(CHAT_FILE, "w", encoding="utf-8") as f:
            json.dump(chats, f, ensure_ascii=False, indent=2)

def delete_chat(chat_id):
    """
    Xoá cuộc hội thoại theo ID
    """
    chats = load_chats()
    if chat_id in chats:
        del chats[chat_id]
        with open(CHAT_FILE, "w", encoding="utf-8") as f:
            json.dump(chats, f, ensure_ascii=False, indent=2)

