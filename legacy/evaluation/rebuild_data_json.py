"""
rebuild_data_json.py  (v3)
==========================
gold_docs trong data.json = Milvus id từ lần seed CŨ.
Collection đã bị drop/recreate → id thay đổi.

Giải pháp: scan Milvus hiện tại, tìm id cũ qua mapc.
Vì mapc là stable key (không đổi khi re-seed), ta:
  1. Lấy mapc của id cũ từ gold_candidates.json (đã search top-50)
     HOẶC query Milvus cũ nếu còn cache
  2. Scan Milvus hiện tại tìm Milvus id mới theo mapc

Cách chạy:
    cd /Users/haind/project2-chatbot-rag
    python evaluation/rebuild_data_json.py
"""

import sys, os, json, ast
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'retrieve'))

from pymilvus import connections, Collection
import pymysql

MILVUS_HOST    = "localhost"
MILVUS_PORT    = 19530
COLLECTION     = "phapdien_simple_tendieu"
MYSQL_HOST     = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT     = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_USER     = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "123456789")
MYSQL_DB       = os.getenv("MYSQL_DATABASE", "law")

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_IN        = os.path.join(BASE, "data.json")
DATA_OUT       = os.path.join(BASE, "data_v2.json")
CANDIDATES     = os.path.join(BASE, "gold_candidates.json")


# ── Bước 1: Lấy mapc của các old Milvus id ───────────────────────────────────
def get_mapc_for_old_ids_via_mysql(old_ids: list) -> dict:
    """
    MySQL pddieu.stt không khớp với old Milvus id.
    Nhưng khi seed_data_batch.py chạy, nó insert vào Milvus theo thứ tự
    SELECT từ MySQL. Ta tìm mapc theo row_number (1-indexed) trong MySQL.
    """
    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        database=MYSQL_DB, charset="utf8mb4"
    )
    try:
        with conn.cursor() as cur:
            # Tạo row_number bằng variable (MySQL 5.x compat) hoặc ROW_NUMBER() (MySQL 8+)
            # Thử MySQL 8+ trước
            try:
                placeholders = ",".join([str(i) for i in old_ids])
                cur.execute(f"""
                    SELECT rn, mapc FROM (
                        SELECT ROW_NUMBER() OVER (ORDER BY mapc) AS rn, mapc
                        FROM pddieu
                    ) ranked
                    WHERE rn IN ({placeholders})
                """)
                rows = cur.fetchall()
                if rows:
                    print(f"  ✓ MySQL ROW_NUMBER: {len(rows)}/{len(old_ids)} records")
                    return {int(row[0]): row[1] for row in rows}
            except Exception as e:
                print(f"  ⚠  ROW_NUMBER failed: {e}")

            # Fallback: thứ tự insert thường = thứ tự mapc ASC (cách seed_data_batch thường làm)
            # Lấy toàn bộ mapc theo thứ tự, rồi index
            cur.execute("SELECT mapc FROM pddieu ORDER BY mapc ASC")
            all_macps = [row[0] for row in cur.fetchall()]
            print(f"  Total pddieu rows: {len(all_macps)}")

            result = {}
            for old_id in old_ids:
                idx = old_id - 1  # Milvus id bắt đầu từ 1
                if 0 <= idx < len(all_macps):
                    result[old_id] = all_macps[idx]
            print(f"  ✓ Mapped by index: {len(result)}/{len(old_ids)}")
            return result
    finally:
        conn.close()


def build_mapc_to_milvus_index(col: Collection, needed_macps: set) -> dict:
    """Scan Milvus, build mapc → current Milvus id, chỉ cho needed_macps."""
    result = {}
    total = col.num_entities
    batch_size = 2000
    cursor_id = 0
    print(f"  Scanning {total} Milvus records...")

    while len(result) < len(needed_macps) and cursor_id <= total + batch_size:
        batch = col.query(
            expr=f"id > {cursor_id}",
            output_fields=["id", "metadata"],
            limit=batch_size
        )
        if not batch:
            break
        for r in batch:
            meta = r["metadata"]
            if isinstance(meta, str):
                try:
                    meta = ast.literal_eval(meta)
                except Exception:
                    continue
            mapc = meta.get("mapc", "")
            if mapc in needed_macps:
                result[mapc] = int(r["id"])
        cursor_id = int(batch[-1]["id"])
        print(f"  ... found {len(result)}/{len(needed_macps)} (scanned to id={cursor_id})", end="\r")

    print()
    return result


def main():
    print("=" * 65)
    print("REBUILD data.json  (v3 — old Milvus id → mapc → new Milvus id)")
    print("=" * 65)

    with open(DATA_IN, encoding="utf-8") as f:
        data = json.load(f)

    all_old_ids = sorted({gd for item in data for gd in item["gold_docs"]})
    print(f"\n📋 Old Milvus ids cần map: {len(all_old_ids)}")
    print(f"   {all_old_ids}")

    # Bước 1: old Milvus id → mapc
    print("\n🔗 Bước 1: old Milvus id → mapc (qua MySQL row order)...")
    old_id_to_mapc = get_mapc_for_old_ids_via_mysql(all_old_ids)

    if not old_id_to_mapc:
        print("❌ Không map được. Kiểm tra MySQL.")
        return

    print(f"\n  Ví dụ mapping (3 đầu):")
    for old_id, mapc in list(old_id_to_mapc.items())[:3]:
        print(f"    old_id={old_id} → mapc={mapc}")

    # Bước 2: mapc → new Milvus id
    print("\n🔗 Bước 2: mapc → new Milvus id (scan collection)...")
    connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
    col = Collection(COLLECTION)
    col.load()

    needed_macps = set(old_id_to_mapc.values())
    mapc_to_new_id = build_mapc_to_milvus_index(col, needed_macps)
    print(f"  ✓ Mapped: {len(mapc_to_new_id)}/{len(needed_macps)} macps")

    # Bước 3: Rebuild data.json
    print("\n✏️  Bước 3: Rebuild gold_docs...")
    new_data = []
    total_gold = 0
    mapped_gold = 0

    for item in data:
        new_gold = []
        for old_id in item["gold_docs"]:
            total_gold += 1
            mapc = old_id_to_mapc.get(old_id)
            if not mapc:
                print(f"  ⚠  old_id={old_id} → mapc not found")
                continue
            new_id = mapc_to_new_id.get(mapc)
            if not new_id:
                print(f"  ⚠  mapc={mapc} → Milvus id not found")
                continue
            new_gold.append(new_id)
            mapped_gold += 1

        new_item = dict(item)
        new_item["gold_docs_old"] = item["gold_docs"]
        new_item["gold_docs"] = new_gold
        new_data.append(new_item)
        print(f"  ✓ {item['query'][:55]}")
        print(f"    old={item['gold_docs']} → new={new_gold}")

    print(f"\n📊 {mapped_gold}/{total_gold} gold docs mapped thành công")

    if mapped_gold == 0:
        print("\n❌ Không map được gì. Seed order trong MySQL có thể khác.")
        print("   Thử xem seed_data_batch.py dùng ORDER BY gì để sort pddieu.")
        return

    with open(DATA_OUT, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Đã lưu: {DATA_OUT}")
    print(f"\n▶  Chạy evaluation:")
    print(f"   cd evaluation && python run_evaluation.py --data data_v2.json")


if __name__ == "__main__":
    main()