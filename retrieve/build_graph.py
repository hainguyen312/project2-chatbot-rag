import sys
from pathlib import Path

# Cho phép chạy: python retrieve/build_graph.py (từ thư mục gốc repo)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os
import pymysql
from neo4j import GraphDatabase
from tqdm import tqdm

from retrieve import legal_graph as LG
from retrieve.tavily_fallback import (
    is_context_sufficient,
    search_tavily,
    decompose_query,
    search_tavily_realtime,
)
 
# ─── CONFIG ───────────────────────────────────────────────────────────────────

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "123456789"),
    "database": os.getenv("MYSQL_DATABASE", "law"),
    "charset": "utf8mb4",
}

NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD = LG.NEO4J_URI, LG.NEO4J_USER, LG.NEO4J_PASSWORD

BATCH_SIZE = 500   # số node insert mỗi lần (tối ưu tốc độ)

# Retrieval (đọc từ env để A/B không cần sửa code)
RRF_K             = 60    # hằng số RRF chuẩn
RRF_W_BM25        = 1.0   # BM25 là nguồn chính; trọng số vector tính tương đối với nó
# Vector chỉ embed TÊN điều → dễ kéo nhiễu ("mũ bảo hiểm" → bảo hiểm y tế). Dò trọng số 2026-11-16 (50 câu thật,
# judge Sonnet 5.5 + 100 câu known-item): 0.5 là mức duy nhất không kém mặc định cũ (1.0) ở chỉ số trung bình nào
# (P@10 +0.024, nDCG@10 +0.018, MRR +0.013, Hit@10 +0.02); giảm tiếp (0.3, 0) tăng known-item nhưng đẩy điều trả lời
# trực tiếp xuống (MRR rel=2 giảm). Khác biệt trên 50 câu chưa có ý nghĩa thống kê (sign test p > 0.4).
RRF_W_VECTOR      = float(os.getenv("RRF_W_VECTOR", "0.5"))   # = vector_weight (tham số config duy nhất)
RERANK_LEX_WEIGHT = float(os.getenv("RERANK_LEX_WEIGHT", "0.3"))

# Trọng số quan hệ khi mở rộng đồ thị (graph_traverse): dẫn chiếu tường minh > được dẫn chiếu
# (văn bản cấp dưới hướng dẫn) > "mục liên quan" của Pháp Điển > dẫn chiếu bắc cầu 2 bước.
GRAPH_W = {
    "goc": 1.0,
    "dan_chieu": float(os.getenv("GRAPH_W_DAN_CHIEU", "0.8")),
    "duoc_dan_chieu": float(os.getenv("GRAPH_W_DUOC_DAN_CHIEU", "0.65")),
    "lien_quan": float(os.getenv("GRAPH_W_LIEN_QUAN", "0.5")),
    "dan_chieu_2": float(os.getenv("GRAPH_W_DAN_CHIEU_2", "0.4")),
}

# ─── KẾT NỐI ──────────────────────────────────────────────────────────────────

def get_mysql():
    return pymysql.connect(**MYSQL_CONFIG, cursorclass=pymysql.cursors.DictCursor)

def get_neo4j():
    try:
        # Tắt notification (warning "property does not exist"… khi graph rỗng) để log gọn
        return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD),
                                    notifications_min_severity="OFF")
    except TypeError:  # driver < 5.7
        return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# ─── BUILD ĐỒ THỊ (MySQL → Neo4j) ─────────────────────────────────────────────
# Logic phân tích + ghi nằm ở retrieve/legal_graph.py, dùng chung với đồng bộ từng phần
# (services/lawdata khi admin duyệt văn bản).

def insert_lienquan(driver, conn):
    """'Mục liên quan' do Pháp Điển biên soạn (bảng pdmuclienquan) → LIEN_QUAN hai chiều."""
    with conn.cursor() as cur:
        cur.execute("SELECT dieu_id1_id AS a, dieu_id2_id AS b FROM pdmuclienquan")
        rows = cur.fetchall()
    for i in tqdm(range(0, len(rows), BATCH_SIZE), desc="LIEN_QUAN"):
        with driver.session() as session:
            session.run("""
                UNWIND $batch AS r
                MATCH (a:Dieu {mapc: r.a})
                MATCH (b:Dieu {mapc: r.b})
                MERGE (a)-[:LIEN_QUAN]->(b)
                MERGE (b)-[:LIEN_QUAN]->(a)
            """, batch=rows[i : i + BATCH_SIZE])
    print(f"✅ LIEN_QUAN: {len(rows)} cặp (2 chiều)")


def build_all(driver, conn):
    """Build toàn bộ đồ thị. Chạy lại an toàn (MERGE); THAM_CHIEU được xoá và dựng lại theo parser hiện tại."""
    print("\n[0] Constraints & indexes...")
    LG.ensure_schema(driver)
    print("[1] ChuDe / DeMuc / Chuong...")
    LG.write_hierarchy(driver, conn)
    print("[2] Đọc pddieu...")
    rows = LG.article_rows(conn)
    print(f"[3] Dieu ({len(rows):,})...")
    for i in tqdm(range(0, len(rows), 5000), desc="Dieu"):
        LG.write_dieu(driver, rows[i:i + 5000])
    print("[4] Khoan...")
    n_k = 0
    for i in tqdm(range(0, len(rows), 5000), desc="Khoan"):
        n_k += LG.write_khoan(driver, rows[i:i + 5000], fresh=True)
    print(f"✅ Khoan: {n_k:,}")
    print("[5] VanBan + sửa đổi/bãi bỏ (từ vbqppl) + sổ cái admin...")
    n_v = LG.write_vanban(driver, rows, fresh=True)
    n_a = LG.write_admin_relations(driver, conn)
    print(f"✅ THUOC: {n_v:,}, quan hệ sổ cái: {n_a:,}")
    print("[6] LIEN_QUAN...")
    insert_lienquan(driver, conn)
    print("[7] THAM_CHIEU (parser dẫn chiếu trong nội dung)...")
    edges = LG.reference_edges(rows, LG.build_lookup(rows))
    LG.clear_thamchieu(driver)
    LG.write_thamchieu(driver, edges)
    print(f"✅ THAM_CHIEU: {len(edges):,}")
    return LG.graph_stats(driver)

# ─── GRAPH RAG RETRIEVER ───────────────────────────────────────────────────────
 
class GraphRAGRetriever:
    """
    Kết hợp Milvus (vector search) + Neo4j (graph traversal).
    
    Cách dùng:
        retriever = GraphRAGRetriever(neo4j_driver, milvus_collection, openai_client)
        results = retriever.retrieve("hành vi chống người thi hành công vụ", top_k=5, hop=2)
        # results: list[dict] với keys: mapc, ten, noidung, score, source
    """
 
    def __init__(self, neo4j_driver, milvus_collection, openai_client):
        self.driver     = neo4j_driver
        self.collection = milvus_collection
        self.client     = openai_client
 
    # ── Embedding ────────────────────────────────────────────────────────────
 
    def _embed(self, text: str) -> list[float]:
        resp = self.client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return resp.data[0].embedding
 
    # ── Stage 1: Milvus vector search ────────────────────────────────────────
 
    def _vector_search(self, query: str, top_k: int) -> list[str]:
        """Trả về list mapc của các Dieu gần nhất về ngữ nghĩa (giữ API cũ)."""
        return [m for m, _ in self._vector_search_scored(query, top_k)]

    def _vector_search_scored(self, query: str, top_k: int, vec: list[float] | None = None) -> list[tuple[str, float]]:
        """Như _vector_search nhưng giữ cosine similarity: [(mapc, score)], dedup theo mapc."""
        if self.collection is None:
            print("[GraphRAG] Milvus collection chưa sẵn sàng, bỏ qua vector search")
            return []
        try:
            vec = vec or self._embed(query)
            results = self.collection.search(
                data=[vec],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"ef": 128}},
                limit=top_k,
                output_fields=["metadata"],
            )
        except Exception as e:
            print(f"[GraphRAG] Milvus search lỗi: {e}")
            return []

        if not results or not results[0]:
            print("[GraphRAG] Milvus trả về rỗng")
            return []

        scored: list[tuple[str, float]] = []
        seen: set[str] = set()
        for hit in results[0]:
            try:
                meta = hit.entity.get("metadata") or {}
            except Exception:
                meta = {}

            if isinstance(meta, str):
                import json
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            mapc = meta.get("mapc") or (meta.get("tendieu") or "").split(".")[0]
            if mapc and mapc not in seen:
                seen.add(mapc)
                scored.append((mapc, float(hit.distance)))

        print(f"[GraphRAG] _vector_search raw hits: {len(results[0])}, mapcs tìm được: {len(scored)}")

        # Nếu không lấy được mapc nào, in debug để kiểm tra cấu trúc metadata
        if not scored and results[0]:
            sample = results[0][0]
            try:
                print(f"[DEBUG] Sample entity fields: {sample.entity.fields}")
                print(f"[DEBUG] Sample metadata raw: {sample.entity.get('metadata')}")
            except Exception as e:
                print(f"[DEBUG] Không đọc được entity: {e}")

        return scored

    # ── Stage 1b: Hybrid (vector + BM25, Reciprocal Rank Fusion) ─────────────

    def _hybrid_search(self, query: str, top_k: int, pool: int = 50) -> list[dict]:
        """
        hybrid_new — bước truy hồi của chatbot: weighted RRF hai nguồn
          vec  : Milvus phapdien_simple_tendieu (embed TÊN điều), trọng số RRF_W_VECTOR (mặc định 0.5)
          bm25 : BM25 tên ×3 + nội dung (điều dài tách theo khoản), trọng số 1.0
        score(d) = Σ_nguồn w / (RRF_K + hạng + 1), chuẩn hoá về (0, 1] bằng cách chia cho điểm tối đa có thể
        (hạng 1 ở mọi nguồn có kết quả). Bước sau: _rerank (0.7 × score + 0.3 × độ phủ từ khoá).
        Trả về [{mapc, score, <src>_score, <src>_rank}].
        """
        from retrieve.lexical_bm25 import get_bm25

        vec = self._vector_search_scored(query, top_k=pool)
        idx = get_bm25()
        bm25 = idx.search(query, top_k=pool) if idx is not None else []

        sources = (("vec", vec, RRF_W_VECTOR), ("bm25", bm25, RRF_W_BM25))
        info: dict[str, dict] = {}
        for name, lst, w in sources:
            for rank, (m, s) in enumerate(lst):
                d = info.setdefault(m, {"mapc": m, "rrf": 0.0})
                d["rrf"] += w / (RRF_K + rank + 1)
                d[f"{name}_score"] = s
                d[f"{name}_rank"] = rank + 1
        if not info:
            return []
        max_rrf = sum(w for _, lst, w in sources if lst) / (RRF_K + 1)
        out = sorted(info.values(), key=lambda d: d["rrf"], reverse=True)[:top_k]
        for d in out:
            d["score"] = d.pop("rrf") / max_rrf
        return out

    # ── Stage 2: Graph expansion ──────────────────────────────────────────────

    def _graph_expand(self, seed_mapcs: list[str], hop: int) -> list[dict]:
        if not seed_mapcs:
            return []
        rows: list[dict] = []
        try:
            rows = self._graph_expand_neo4j(seed_mapcs, hop)
        except Exception as e:
            print(f"[GraphRAG] Neo4j expand lỗi: {e}")
        # Neo4j rỗng / chưa build → dùng trực tiếp bảng quan hệ MySQL pdmuclienquan
        if len(rows) <= len(seed_mapcs):
            mysql_rows = self._graph_expand_mysql(seed_mapcs, hop)
            if len(mysql_rows) > len(rows):
                rows = mysql_rows
        return rows

    def _graph_expand_mysql(self, seed_mapcs: list[str], hop: int, limit: int = 60) -> list[dict]:
        """LIEN_QUAN qua MySQL (2 chiều). Seed base_score 1.0, hop 1 → 0.6, hop 2 → 0.4."""
        level: dict[str, int] = {m: 0 for m in seed_mapcs}
        frontier = list(seed_mapcs)
        try:
            conn = get_mysql()
            with conn.cursor() as cur:
                for h in range(1, max(1, hop) + 1):
                    if not frontier or len(level) >= limit:
                        break
                    ph = ",".join(["%s"] * len(frontier))
                    cur.execute(
                        f"SELECT dieu_id2_id AS nb FROM pdmuclienquan WHERE dieu_id1_id IN ({ph}) "
                        f"UNION SELECT dieu_id1_id AS nb FROM pdmuclienquan WHERE dieu_id2_id IN ({ph})",
                        frontier + frontier,
                    )
                    nxt = []
                    for r in cur.fetchall():
                        if r["nb"] not in level and len(level) < limit:
                            level[r["nb"]] = h
                            nxt.append(r["nb"])
                    frontier = nxt
                ids = list(level)
                ph = ",".join(["%s"] * len(ids))
                cur.execute(
                    f"""SELECT d.mapc, d.ten, d.noidung, d.vbqppl, ch.ten AS tenchuong,
                               dm.ten AS tendemuc, cd.ten AS tenchude
                        FROM pddieu d
                        LEFT JOIN pdchuong ch ON d.chuong_id = ch.mapc
                        LEFT JOIN pddemuc dm ON d.demuc_id = dm.id
                        LEFT JOIN pdchude cd ON d.chude_id = cd.id
                        WHERE d.mapc IN ({ph})""",
                    ids,
                )
                fetched = {r["mapc"]: r for r in cur.fetchall()}
            conn.close()
        except Exception as e:
            print(f"[GraphRAG] MySQL expand lỗi: {e}")
            return []

        base_by_level = {0: 1.0, 1: 0.6, 2: 0.4}
        rows = []
        for m, lv in sorted(level.items(), key=lambda x: x[1]):
            r = fetched.get(m)
            if not r:
                continue
            rows.append({
                "mapc": m, "ten": r["ten"], "noidung": r["noidung"],
                "tenchuong": r["tenchuong"], "tendemuc": r["tendemuc"], "tenchude": r["tenchude"], "vbqppl": r["vbqppl"],
                "passage": "\n".join(filter(None, [r["tenchude"], r["tendemuc"], r["tenchuong"], r["ten"], r["noidung"]])),
                "base_score": base_by_level.get(lv, 0.3),
                "hop": lv,
                "quan_he": "goc" if lv == 0 else "lien_quan",
            })
        return rows

    def _graph_expand_neo4j(self, seed_mapcs: list[str], hop: int) -> list[dict]:
        """
        Mở rộng theo quan hệ có trọng số (GRAPH_W): dẫn chiếu đi ra > được dẫn chiếu (văn bản cấp
        dưới hướng dẫn) > LIEN_QUAN; hop=2 chỉ đi tiếp qua THAM_CHIEU (2 bước LIEN_QUAN quá nhiễu).
        Mỗi điều giữ quan hệ mạnh nhất: base_score = trọng số, quan_he = tên quan hệ.
        """
        cypher = """
            MATCH (seed:Dieu) WHERE seed.mapc IN $mapcs
            CALL {
                WITH seed RETURN seed AS n, 'goc' AS quan_he
                UNION
                WITH seed MATCH (seed)-[:THAM_CHIEU]->(n:Dieu) RETURN n, 'dan_chieu' AS quan_he
                UNION
                WITH seed MATCH (seed)<-[:THAM_CHIEU]-(n:Dieu)
                RETURN n, 'duoc_dan_chieu' AS quan_he LIMIT 15
                UNION
                WITH seed MATCH (seed)-[:LIEN_QUAN]->(n:Dieu) RETURN n, 'lien_quan' AS quan_he LIMIT 15
                UNION
                WITH seed MATCH (seed)-[:THAM_CHIEU]->(:Dieu)-[:THAM_CHIEU]->(n:Dieu) WHERE $hop >= 2
                RETURN n, 'dan_chieu_2' AS quan_he LIMIT 15
            }
            WITH n, quan_he, $w[quan_he] AS w
            ORDER BY w DESC
            WITH n, collect(quan_he)[0] AS quan_he, max(w) AS base_score
            OPTIONAL MATCH (ch:Chuong)-[:CO_DIEU]->(n)
            OPTIONAL MATCH (dm:DeMuc)-[:CO_CHUONG]->(ch)
            OPTIONAL MATCH (cd:ChuDe)-[:CO_DE_MUC]->(dm)
            RETURN n.mapc AS mapc, n.ten AS ten, n.noidung AS noidung, n.vbqppl AS vbqppl,
                   ch.ten AS tenchuong, dm.ten AS tendemuc, cd.ten AS tenchude,
                   base_score, quan_he
            ORDER BY base_score DESC
            LIMIT 40
        """
        with self.driver.session() as session:
            rows = []
            for r in session.run(cypher, mapcs=seed_mapcs, hop=hop, w=GRAPH_W):
                d = dict(r)
                d["passage"] = "\n".join(filter(None, [d["tenchude"], d["tendemuc"], d["tenchuong"],
                                                        d["ten"], d["noidung"]]))
                rows.append(d)
            return rows

    # ── Truy vấn đồ thị phục vụ agent ────────────────────────────────────────

    def _reference_closure(self, seed_mapcs: list[str], exclude: set[str] | None = None,
                           limit: int = 4, max_chars: int = 1500) -> list[dict]:
        """
        Điều/khoản mà các điều `seed_mapcs` DẪN CHIẾU TỚI (vd. Điều 8 "điều kiện kết hôn" → khoản 2
        Điều 5 "các trường hợp cấm kết hôn"). Có ghi khoản → chỉ lấy đúng các khoản đó.
        Trả [{mapc, ten, noidung, vbqppl, …, dan_chieu_tu, khoan, diem}] theo thứ tự seed.
        """
        if not seed_mapcs or self.driver is None:
            return []
        cypher = """
            UNWIND range(0, size($seeds) - 1) AS i
            MATCH (s:Dieu {mapc: $seeds[i]})-[r:THAM_CHIEU]->(t:Dieu)
            WHERE NOT t.mapc IN $exclude
            OPTIONAL MATCH (t)-[:CO_KHOAN]->(k:Khoan) WHERE k.so IN r.khoan
            WITH i, s, r, t, k ORDER BY i, toInteger(k.so)
            WITH i, s, r, t, [x IN collect(k.noidung) WHERE x IS NOT NULL] AS khoan_text
            OPTIONAL MATCH (ch:Chuong)-[:CO_DIEU]->(t)
            OPTIONAL MATCH (dm:DeMuc)-[:CO_CHUONG]->(ch)
            OPTIONAL MATCH (cd:ChuDe)-[:CO_DE_MUC]->(dm)
            RETURN t.mapc AS mapc, t.ten AS ten, t.vbqppl AS vbqppl,
                   CASE WHEN size(khoan_text) > 0 THEN reduce(a = '', x IN khoan_text | a + x + '\n')
                        ELSE t.noidung END AS noidung,
                   size(khoan_text) > 0 AS chi_khoan,
                   ch.ten AS tenchuong, dm.ten AS tendemuc, cd.ten AS tenchude,
                   s.mapc AS dan_chieu_tu, s.ten AS ten_nguon, r.khoan AS khoan, r.diem AS diem, i
            ORDER BY i
        """
        try:
            with self.driver.session() as session:
                rows = [dict(r) for r in session.run(cypher, seeds=list(seed_mapcs),
                                                     exclude=sorted(exclude or set()))]
        except Exception as e:
            print(f"[GraphRAG] reference closure lỗi: {e}")
            return []
        out, seen = [], set()
        for r in rows:
            if r["mapc"] in seen:
                continue
            seen.add(r["mapc"])
            r["noidung"] = LG.head_truncate(r["noidung"] or "", max_chars)
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def _exception_check(self, seed_mapcs: list[str], exclude: set[str] | None = None,
                         limit: int = 3, max_chars: int = 1500) -> list[dict]:
        """
        NGOẠI LỆ / CẤM liên quan tới các điều `seed_mapcs` — loại "điều chính cho phép nhưng điều liên quan cấm":
        điều nối với seed (THAM_CHIEU hai chiều hoặc LIEN_QUAN) mà là điều cấm/ngoại lệ (Dieu.ngoai_le), hoặc
        cạnh dẫn chiếu nằm trong câu "trừ trường hợp… / không thuộc… / không được…" (THAM_CHIEU.ngoai_le).
        Nội dung trả về = các khoản cấm/ngoại lệ + khoản được dẫn chiếu (không phải cả điều).
        Ưu tiên: dẫn chiếu trong ngữ cảnh ngoại lệ > dẫn chiếu > mục liên quan; rồi theo thứ hạng seed.
        """
        if not seed_mapcs or self.driver is None:
            return []
        cypher = """
            UNWIND range(0, size($seeds) - 1) AS i
            MATCH (s:Dieu {mapc: $seeds[i]})-[r:THAM_CHIEU|LIEN_QUAN]-(t:Dieu)
            WHERE NOT t.mapc IN $exclude AND (coalesce(t.ngoai_le, false) OR coalesce(r.ngoai_le, false))
            WITH i, s, t,
                 max(CASE WHEN type(r) = 'THAM_CHIEU' AND coalesce(r.ngoai_le, false) THEN 3
                          WHEN type(r) = 'THAM_CHIEU' THEN 2 ELSE 1 END) AS w,
                 reduce(a = [], x IN collect(CASE WHEN type(r) = 'THAM_CHIEU' AND startNode(r) = s
                                                  THEN r.khoan ELSE [] END) | a + x) AS khoan_dc
            OPTIONAL MATCH (t)-[:CO_KHOAN]->(k:Khoan)
            WHERE coalesce(k.ngoai_le, false) OR k.so IN khoan_dc
            WITH i, s, t, w, khoan_dc, k ORDER BY toInteger(k.so)
            WITH i, s, t, w, khoan_dc, [x IN collect(k.noidung) WHERE x IS NOT NULL] AS ktext
            OPTIONAL MATCH (ch:Chuong)-[:CO_DIEU]->(t)
            OPTIONAL MATCH (dm:DeMuc)-[:CO_CHUONG]->(ch)
            OPTIONAL MATCH (cd:ChuDe)-[:CO_DE_MUC]->(dm)
            RETURN t.mapc AS mapc, t.ten AS ten, t.vbqppl AS vbqppl,
                   CASE WHEN size(ktext) > 0 THEN reduce(a = '', x IN ktext | a + x + '\n')
                        ELSE t.noidung END AS noidung,
                   ch.ten AS tenchuong, dm.ten AS tendemuc, cd.ten AS tenchude,
                   s.mapc AS ngoai_le_cua, s.ten AS ten_nguon, khoan_dc AS khoan, w, i
            ORDER BY w DESC, i ASC
        """
        try:
            with self.driver.session() as session:
                rows = [dict(r) for r in session.run(cypher, seeds=list(seed_mapcs),
                                                     exclude=sorted(exclude or set()))]
        except Exception as e:
            print(f"[GraphRAG] exception check lỗi: {e}")
            return []
        out, seen = [], set()
        for r in rows:
            if r["mapc"] in seen:
                continue
            seen.add(r["mapc"])
            r["noidung"] = LG.head_truncate(r["noidung"] or "", max_chars)
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def _graph_degrees(self, mapcs: list[str]) -> dict[str, int]:
        """Số điều KHÁC trong cùng danh sách ứng viên mà mỗi điều nối tới (THAM_CHIEU / LIEN_QUAN)."""
        if not mapcs or self.driver is None:
            return {}
        try:
            with self.driver.session() as session:
                res = session.run("""
                    UNWIND $mapcs AS m
                    MATCH (a:Dieu {mapc: m})-[:THAM_CHIEU|LIEN_QUAN]-(b:Dieu)
                    WHERE b.mapc IN $mapcs AND b.mapc <> m
                    RETURN m AS mapc, count(DISTINCT b) AS deg
                """, mapcs=list(mapcs))
                return {r["mapc"]: int(r["deg"]) for r in res}
        except Exception as e:
            print(f"[GraphRAG] graph degree lỗi: {e}")
            return {}

    def _graph_boost(self, ranked: list[dict], beta: float, head: int = 30) -> list[dict]:
        """
        Cộng điểm cho ứng viên có liên kết với các ứng viên khác trong top `head`
        (score += beta · min(bậc, 3) / 3): điều nằm giữa một cụm điều cùng được truy hồi thường đúng
        trọng tâm hơn điều đứng lẻ. beta = 0 → giữ nguyên.
        """
        if beta <= 0 or not ranked:
            return ranked
        deg = self._graph_degrees([c.get("mapc") for c in ranked[:head] if c.get("mapc")])
        if not deg:
            return ranked
        for c in ranked[:head]:
            d = deg.get(c.get("mapc"), 0)
            c["graph_degree"] = d
            c["score"] = min(1.0, float(c.get("score", 0.0)) + beta * min(d, 3) / 3)
        ranked[:head] = sorted(ranked[:head], key=lambda x: -float(x.get("score", 0.0)))
        return ranked

    def _newest_versions(self, so_hieus: list[str]) -> dict[str, dict]:
        """
        Văn bản đã bị THAY THẾ / BÃI BỎ (sổ cái admin) → văn bản mới nhất theo chuỗi thay thế
        + số điều của văn bản đó đang có trong CSDL: {so_hieu: {"moi_nhat", "so_dieu_trong_db"}}.
        """
        so_hieus = sorted({s for s in so_hieus if s})
        if not so_hieus or self.driver is None:
            return {}
        try:
            with self.driver.session() as session:
                res = session.run("""
                    UNWIND $docs AS sh
                    MATCH p = (moi:VanBan)-[:THAY_THE|BAI_BO*1..5]->(v:VanBan {so_hieu: sh})
                    WHERE NOT ()-[:THAY_THE|BAI_BO]->(moi)
                    WITH sh, moi, length(p) AS len ORDER BY len DESC
                    WITH sh, collect(moi)[0] AS moi
                    OPTIONAL MATCH (d:Dieu)-[:THUOC]->(moi)
                    RETURN sh, moi.so_hieu AS moi_nhat, count(d) AS so_dieu
                """, docs=so_hieus)
                return {r["sh"]: {"moi_nhat": r["moi_nhat"], "so_dieu_trong_db": int(r["so_dieu"])}
                        for r in res if r["moi_nhat"]}
        except Exception as e:
            print(f"[GraphRAG] newest versions lỗi: {e}")
            return {}

    def graph_stats(self) -> dict[str, int]:
        try:
            return LG.graph_stats(self.driver)
        except Exception as e:
            print(f"[GraphRAG] Không đọc được thống kê Neo4j: {e}")
            return {}

    # ── Stage 3: Rerank = base_score (retrieval) + lexical coverage, có chặn ──

    def _rerank(self, candidates: list[dict], query: str, top_k: int) -> list[dict]:
        """
        score = (1-RERANK_LEX_WEIGHT) * base_score + RERANK_LEX_WEIGHT * coverage
          - base_score: điểm retrieval đã chuẩn hoá [0,1] (hybrid RRF / hop level của graph)
          - coverage  : tỉ lệ âm tiết + bigram CÓ NGHĨA của query (đã bỏ hư từ) xuất hiện
                        trong tên + nội dung điều; tên điều tính gấp đôi.
        Bản cũ cộng 0.05 × số token trùng (tính cả "của", "và", "người"…) không chặn trên,
        và base_score của vector_search bị gán hằng 0.8 → thứ tự thực chất do đếm hư từ.
        """
        from retrieve.lexical_bm25 import clean_title, tokenize

        q_toks = set(tokenize(query))
        seen = set()
        deduped = []

        for c in candidates:
            key = c.get("mapc") or c.get("passage", "")[:80]
            if key in seen:
                continue
            seen.add(key)
            base = max(0.0, min(1.0, float(c.get("base_score", 0.5))))
            if q_toks:
                title_t = set(tokenize(clean_title(c.get("ten") or "")))
                body_t = set(tokenize((c.get("noidung") or "")[:12000]))
                hit = sum(2.0 if t in title_t else 1.0 if t in body_t else 0.0 for t in q_toks)
                coverage = hit / (2.0 * len(q_toks))
            else:
                coverage = 0.0
            c["lex_coverage"] = round(coverage, 4)
            c["score"] = (1 - RERANK_LEX_WEIGHT) * base + RERANK_LEX_WEIGHT * coverage
            deduped.append(c)

        deduped.sort(key=lambda x: x["score"], reverse=True)
        return deduped[:top_k]
    # ── Main retrieve ─────────────────────────────────────────────────────────
 
    def retrieve(self, query, top_k=20, seed_k=5, hop=2) -> list[dict]:
        # Decompose trước khi search
        decomposed     = decompose_query(query)
        legal_query    = decomposed.get("legal_query") or query
        realtime_query = decomposed.get("realtime_query")
        calc_hint      = decomposed.get("calc_hint")

        if realtime_query:
            print(f"[GraphRAG] Phát hiện query thực tế: '{realtime_query}'")

        # Stage 1–3: Graph RAG với legal_query
        seed_mapcs = self._vector_search(legal_query, top_k=seed_k)
        print(f"[GraphRAG] Seeds: {len(seed_mapcs)}")

        candidates = self._graph_expand(seed_mapcs, hop=hop)
        print(f"[GraphRAG] Candidates: {len(candidates)}")

        final = self._rerank(candidates, legal_query, top_k=top_k)

        for r in final:
            r.setdefault("source", "phapdien")
            r.setdefault("source_label", "[Pháp Điển]")

        # Stage 4a: Fallback pháp lý — TRUYỀN query VÀO để check relevance + thời sự
        if not is_context_sufficient(final, query=query):
            print(f"[GraphRAG] Context không đủ/không liên quan → Tavily legal fallback")
            web_legal = search_tavily(legal_query)
            final = final + web_legal
            print(f"[GraphRAG] Sau legal fallback: {len(final)} kết quả")

        # Stage 4b: Realtime data
        if realtime_query:
            realtime_results = search_tavily_realtime(realtime_query)
            final = realtime_results + final
            print(f"[GraphRAG] Sau realtime: {len(final)} kết quả")

        # Stage 4c: Gắn calc hint nếu có
        if calc_hint:
            final.insert(0, {
                "mapc": None, "ten": "Yêu cầu tính toán",
                "noidung": calc_hint,
                "passage": f"[Hướng dẫn tính toán]\n{calc_hint}",
                "score": 1.0, "source": "system",
                "source_label": "[Hệ thống]",
            })

        print(f"[GraphRAG] Tổng kết quả trả về: {len(final)}")
        return final
 
 
# ─── MAIN ─────────────────────────────────────────────────────────────────────
 
def refresh_derived(driver, conn):
    """Tính lại phần SUY RA từ nội dung (cờ ngoại lệ, cạnh THAM_CHIEU) trên đồ thị đã build — dùng khi đổi
    parser, không phải ghi lại 66k điều + 196k khoản."""
    rows = LG.article_rows(conn)
    n = LG.mark_exceptions(driver, rows)
    print(f"✅ Điều cấm/ngoại lệ: {n:,}")
    edges = LG.reference_edges(rows, LG.build_lookup(rows))
    LG.clear_thamchieu(driver)
    LG.write_thamchieu(driver, edges)
    print(f"✅ THAM_CHIEU: {len(edges):,} (ngữ cảnh ngoại lệ: {sum(e['ngoai_le'] for e in edges):,})")
    return LG.graph_stats(driver)


def main():
    print("=" * 60)
    print("Graph RAG Builder — MySQL → Neo4j")
    print("=" * 60)
    conn = get_mysql()
    driver = get_neo4j()
    try:
        stats = refresh_derived(driver, conn) if "--refresh-derived" in sys.argv else build_all(driver, conn)
        print("\n" + "=" * 60)
        print("🎉 DONE —", ", ".join(f"{k}={v:,}" for k, v in stats.items()))
        print(f"   Neo4j Browser: http://localhost:7474  ({NEO4J_USER} / {NEO4J_PASSWORD})")
        print("=" * 60)
    finally:
        conn.close()
        driver.close()


if __name__ == "__main__":
    main()
