import sys
from pathlib import Path

# Cho phép chạy: python retrieve/build_graph.py (từ thư mục gốc repo)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os
import re
import pymysql
from neo4j import GraphDatabase
from tqdm import tqdm

from retrieve.tavily_fallback import (
    is_context_sufficient,
    search_tavily,
    decompose_query,
    search_tavily_realtime,
)
 
# ─── CONFIG ───────────────────────────────────────────────────────────────────
 
MYSQL_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "123456789",
    "database": "law",
    "charset": "utf8mb4",
}
 
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "password123"
 
BATCH_SIZE = 500   # số node insert mỗi lần (tối ưu tốc độ)

# Retrieval (đọc từ env để A/B không cần sửa code)
RRF_K             = int(os.getenv("RRF_K", "60"))
RRF_W_VECTOR      = float(os.getenv("RRF_W_VECTOR", "1.0"))
RRF_W_BM25        = float(os.getenv("RRF_W_BM25", "1.0"))
RRF_W_CTX         = float(os.getenv("RRF_W_CTX", "1.0"))
CTX_COLLECTION    = os.getenv("CTX_COLLECTION", "phapdien_ctx_chunks")
CTX_CHUNK_POOL    = int(os.getenv("CTX_CHUNK_POOL", "300"))
RERANK_LEX_WEIGHT = float(os.getenv("RERANK_LEX_WEIGHT", "0.3"))
 
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
 
# ─── BƯỚC 0: TẠO CONSTRAINTS & INDEX ─────────────────────────────────────────
 
def create_constraints(driver):
    """
    Chạy 1 lần để tạo index và unique constraint.
    Giúp tăng tốc MERGE và tìm kiếm node.
    """
    constraints = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:ChuDe)  REQUIRE n.id   IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:DeMuc)  REQUIRE n.id   IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Chuong) REQUIRE n.mapc IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Dieu)   REQUIRE n.mapc IS UNIQUE",
        # Full-text search index cho tìm kiếm keyword
        """CREATE FULLTEXT INDEX dieu_fulltext IF NOT EXISTS
           FOR (n:Dieu) ON EACH [n.ten, n.noidung]""",
    ]
    with driver.session() as session:
        for cypher in constraints:
            try:
                session.run(cypher)
            except Exception as e:
                print(f"[constraint] {e}")
    print("✅ Constraints & indexes OK")
 
# ─── BƯỚC 1: INSERT PHÂN CẤP ──────────────────────────────────────────────────
 
def insert_chude(driver, conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, ten, stt FROM pdchude")
        rows = cur.fetchall()
 
    with driver.session() as session:
        session.run("""
            UNWIND $rows AS r
            MERGE (n:ChuDe {id: r.id})
            SET n.ten = r.ten, n.stt = r.stt
        """, rows=rows)
    print(f"✅ ChuDe: {len(rows)} nodes")
 
 
def insert_demuc(driver, conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, ten, stt, chude_id FROM pddemuc")
        rows = cur.fetchall()
 
    with driver.session() as session:
        # Insert node
        session.run("""
            UNWIND $rows AS r
            MERGE (n:DeMuc {id: r.id})
            SET n.ten = r.ten, n.stt = r.stt
        """, rows=rows)
        # Tạo relationship với ChuDe
        session.run("""
            UNWIND $rows AS r
            MATCH (cd:ChuDe {id: r.chude_id})
            MATCH (dm:DeMuc {id: r.id})
            MERGE (cd)-[:CO_DE_MUC]->(dm)
        """, rows=rows)
    print(f"✅ DeMuc: {len(rows)} nodes + relationships")
 
 
def insert_chuong(driver, conn):
    with conn.cursor() as cur:
        cur.execute("SELECT mapc, ten, chimuc, stt, demuc_id FROM pdchuong")
        rows = cur.fetchall()
 
    with driver.session() as session:
        session.run("""
            UNWIND $rows AS r
            MERGE (n:Chuong {mapc: r.mapc})
            SET n.ten = r.ten, n.chimuc = r.chimuc, n.stt = r.stt
        """, rows=rows)
        session.run("""
            UNWIND $rows AS r
            MATCH (dm:DeMuc {id: r.demuc_id})
            MATCH (ch:Chuong {mapc: r.mapc})
            MERGE (dm)-[:CO_CHUONG]->(ch)
        """, rows=rows)
    print(f"✅ Chuong: {len(rows)} nodes + relationships")
 
# ─── BƯỚC 2: INSERT DIEU (batch vì 66k rows) ──────────────────────────────────
 
def insert_dieu(driver, conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT mapc, ten, noidung, chimuc, stt,
                   chuong_id, demuc_id, chude_id
            FROM pddieu
        """)
        rows = cur.fetchall()
 
    total = len(rows)
    print(f"  Inserting {total} Dieu nodes...")
 
    for i in tqdm(range(0, total, BATCH_SIZE), desc="Dieu nodes"):
        batch = rows[i : i + BATCH_SIZE]

        with driver.session() as session:
            session.run("""
                UNWIND $batch AS r
                MERGE (d:Dieu {mapc: r.mapc})
                SET d.ten       = r.ten,
                    d.noidung   = r.noidung,
                    d.chimuc    = r.chimuc,
                    d.stt       = r.stt,
                    d.demuc_id  = r.demuc_id,
                    d.chude_id  = r.chude_id
            """, batch=batch)
 
    print(f"  Linking Dieu → Chuong...")
    for i in tqdm(range(0, total, BATCH_SIZE), desc="Dieu→Chuong"):
        batch = rows[i : i + BATCH_SIZE]
        with driver.session() as session:
            session.run("""
                UNWIND $batch AS r
                MATCH (d:Dieu   {mapc: r.mapc})
                MATCH (ch:Chuong {mapc: r.chuong_id})
                MERGE (ch)-[:CO_DIEU]->(d)
            """, batch=batch)
 
    print(f"✅ Dieu: {total} nodes")
 
# ─── BƯỚC 3: INSERT LIEN_QUAN (38k edges) ─────────────────────────────────────
 
def insert_lienquan(driver, conn):
    with conn.cursor() as cur:
        cur.execute("SELECT dieu_id1_id, dieu_id2_id FROM pdmuclienquan")
        rows = cur.fetchall()
 
    total = len(rows)
    print(f"  Inserting {total} LIEN_QUAN edges...")
 
    for i in tqdm(range(0, total, BATCH_SIZE), desc="LIEN_QUAN"):
        batch = rows[i : i + BATCH_SIZE]
        with driver.session() as session:
            session.run("""
                UNWIND $batch AS r
                MATCH (a:Dieu {mapc: r.dieu_id1_id})
                MATCH (b:Dieu {mapc: r.dieu_id2_id})
                MERGE (a)-[:LIEN_QUAN]->(b)
                MERGE (b)-[:LIEN_QUAN]->(a)
            """, batch=batch)
 
    print(f"✅ LIEN_QUAN: {total} edges (bidirectional)")
 
# ─── BƯỚC 4: PARSE THAM_CHIEU TỪ NỘI DUNG ────────────────────────────────────
 
# Pattern nhận dạng mã điều luật trong nội dung (vd: 1.1.LQ.1, 2.3.NĐ.5.2)
MAPC_PATTERN = re.compile(r'\b(\d+\.\d+\.[A-ZĐ]+\.\d+(?:\.\d+)*)\b')
 
def insert_thamchieu(driver, conn):
    with conn.cursor() as cur:
        cur.execute("SELECT mapc, noidung FROM pddieu WHERE noidung IS NOT NULL")
        rows = cur.fetchall()

    # LƯU Ý: nội dung điều luật hầu như KHÔNG chứa mã pháp điển (chỉ ~13/66k điều khớp
    # MAPC_PATTERN); tham chiếu thực tế có dạng "khoản 2 Điều 5 của Luật này". Cạnh
    # THAM_CHIEU vì vậy rất thưa — muốn đầy đủ cần parser "Điều X + văn bản Y" → mapc.

    # Lấy tập hợp mapc hợp lệ để tránh tạo edge tới node không tồn tại
    with conn.cursor() as cur:
        cur.execute("SELECT mapc FROM pddieu")
        valid_mapcs = {r["mapc"] for r in cur.fetchall()}
 
    edges = []
    for row in rows:
        src = row["mapc"]
        found = MAPC_PATTERN.findall(row["noidung"] or "")
        for tgt in set(found):
            if tgt != src and tgt in valid_mapcs:
                edges.append({"src": src, "tgt": tgt})
 
    total = len(edges)
    print(f"  Parsed {total} THAM_CHIEU edges từ noidung...")
 
    for i in tqdm(range(0, total, BATCH_SIZE), desc="THAM_CHIEU"):
        batch = edges[i : i + BATCH_SIZE]
        with driver.session() as session:
            session.run("""
                UNWIND $batch AS r
                MATCH (a:Dieu {mapc: r.src})
                MATCH (b:Dieu {mapc: r.tgt})
                MERGE (a)-[:THAM_CHIEU]->(b)
            """, batch=batch)
 
    print(f"✅ THAM_CHIEU: {total} edges")
 
# ─── GRAPH RAG RETRIEVER ───────────────────────────────────────────────────────
 
class GraphRAGRetriever:
    """
    Kết hợp Milvus (vector search) + Neo4j (graph traversal).
    
    Cách dùng:
        retriever = GraphRAGRetriever(neo4j_driver, milvus_collection, openai_client)
        results = retriever.retrieve("hành vi chống người thi hành công vụ", top_k=5, hop=2)
        # results: list[dict] với keys: mapc, ten, noidung, score, source
    """
 
    def __init__(self, neo4j_driver, milvus_collection, openai_client, ctx_collection=None):
        self.driver     = neo4j_driver
        self.collection = milvus_collection
        self.client     = openai_client
        # Collection chunk ngữ cảnh (header + nội dung điều). None → tự load lần đầu nếu có.
        self._ctx_collection = ctx_collection
        self._ctx_checked = ctx_collection is not None

    @property
    def ctx_collection(self):
        if not self._ctx_checked:
            self._ctx_checked = True
            try:
                from pymilvus import Collection, utility

                if utility.has_collection(CTX_COLLECTION):
                    col = Collection(CTX_COLLECTION)
                    col.load()
                    self._ctx_collection = col
                    print(f"[GraphRAG] Loaded ctx collection: {CTX_COLLECTION}")
            except Exception as e:
                print(f"[GraphRAG] Không load được {CTX_COLLECTION}: {e}")
        return self._ctx_collection
 
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

    def _ctx_search_scored(self, query: str, top_k: int, vec: list[float] | None = None) -> list[tuple[str, float]]:
        """Vector search trên chunk ngữ cảnh, gộp về Điều bằng max score: [(mapc, score)]."""
        col = self.ctx_collection
        if col is None:
            return []
        try:
            vec = vec or self._embed(query)
            chunk_k = min(CTX_CHUNK_POOL, max(top_k * 6, 100))
            res = col.search(
                data=[vec], anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"ef": max(chunk_k, 128)}},
                limit=chunk_k, output_fields=["mapc"],
            )
        except Exception as e:
            print(f"[GraphRAG] ctx search lỗi: {e}")
            return []
        best: dict[str, float] = {}
        for h in (res[0] if res else []):
            m = h.entity.get("mapc")
            sc = float(h.distance)
            if m and sc > best.get(m, -1.0):
                best[m] = sc
        return sorted(best.items(), key=lambda x: x[1], reverse=True)[:top_k]

    # ── Stage 1b: Hybrid (vector + BM25, Reciprocal Rank Fusion) ─────────────

    def _hybrid_search(self, query: str, top_k: int, pool: int = 50, mode: str | None = None,
                       weights: dict[str, float] | None = None) -> list[dict]:
        """
        Fuse bằng weighted RRF các nguồn:
          vec  : Milvus embed TÊN điều        (mọi mode trừ "ctx")
          ctx  : Milvus chunk header+nội dung (mode "hybrid3", "ctx")
          bm25 : BM25 tên + nội dung          (mode "hybrid", "hybrid3", "bm25")
        weights: ghi đè trọng số RRF theo nguồn, vd. {"ctx": 0.5} (mặc định lấy từ env).
        Trả về [{mapc, score, <src>_score, <src>_rank}], score ∈ (0, 1].
        Mode mặc định: RETRIEVAL_MODE, không đặt thì "hybrid". Đánh giá 2026-11-12 (50 câu, judge
        Sonnet 5.5): hybrid3 hoà hybrid (24 thắng / 24 thua theo nDCG@10) — sửa được vài câu văn nói
        nhưng kéo nhiễu ngữ nghĩa ("mũ bảo hiểm" → bảo hiểm y tế) → chưa bật mặc định.
        """
        mode = (mode or os.getenv("RETRIEVAL_MODE") or "hybrid").strip().lower()
        use_vec = mode in ("vector", "hybrid", "hybrid3")
        use_ctx = mode in ("ctx", "hybrid3")
        use_bm25 = mode in ("hybrid", "hybrid3", "bm25")

        qvec = self._embed(query) if (use_vec or use_ctx) else None
        vec = self._vector_search_scored(query, top_k=pool, vec=qvec) if use_vec else []
        ctx = self._ctx_search_scored(query, top_k=pool, vec=qvec) if use_ctx else []
        bm25: list[tuple[str, float]] = []
        if use_bm25:
            from retrieve.lexical_bm25 import get_bm25

            idx = get_bm25()
            if idx is not None:
                bm25 = idx.search(query, top_k=pool)

        wt = {"vec": RRF_W_VECTOR, "ctx": RRF_W_CTX, "bm25": RRF_W_BM25, **(weights or {})}
        sources = (("vec", vec, wt["vec"]), ("ctx", ctx, wt["ctx"]), ("bm25", bm25, wt["bm25"]))
        info: dict[str, dict] = {}
        for name, lst, w in sources:
            for rank, (m, s) in enumerate(lst):
                d = info.setdefault(m, {"mapc": m, "rrf": 0.0})
                d["rrf"] += w / (RRF_K + rank + 1)
                d[f"{name}_score"] = s
                d[f"{name}_rank"] = rank + 1
        if not info:
            return []
        # Chuẩn hoá về (0,1]: chia cho điểm tối đa có thể (hạng 1 ở mọi nguồn có kết quả)
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
            })
        return rows

    def _graph_expand_neo4j(self, seed_mapcs: list[str], hop: int) -> list[dict]:

        # Neo4j không cho dùng $param trong *1..n — phải format trực tiếp
        cypher = f"""
            MATCH (seed:Dieu)
            WHERE seed.mapc IN $mapcs

            CALL {{
                WITH seed
                MATCH (seed)-[:LIEN_QUAN|THAM_CHIEU*1..{hop}]-(neighbor:Dieu)
                RETURN neighbor
                UNION
                WITH seed
                RETURN seed AS neighbor
            }}

            OPTIONAL MATCH (ch:Chuong)-[:CO_DIEU]->(neighbor)
            OPTIONAL MATCH (dm:DeMuc)-[:CO_CHUONG]->(ch)
            OPTIONAL MATCH (cd:ChuDe)-[:CO_DE_MUC]->(dm)

            RETURN DISTINCT
                neighbor.mapc    AS mapc,
                neighbor.ten     AS ten,
                neighbor.noidung AS noidung,
                ch.ten           AS tenchuong,
                dm.ten           AS tendemuc,
                cd.ten           AS tenchude,
                CASE WHEN neighbor.mapc IN $mapcs THEN 1.0 ELSE 0.5 END AS base_score
            ORDER BY base_score DESC
            LIMIT 40
        """

        with self.driver.session() as session:
            result = session.run(cypher, mapcs=seed_mapcs)

            rows = []
            for r in result:
                passage = "\n".join(filter(None, [
                    r["tenchude"],
                    r["tendemuc"],
                    r["tenchuong"],
                    r["ten"],
                    r["noidung"],
                ]))
                rows.append({
                    "mapc":       r["mapc"],
                    "ten":        r["ten"],
                    "noidung":    r["noidung"],
                    "tenchuong":  r["tenchuong"],
                    "tendemuc":   r["tendemuc"],
                    "tenchude":   r["tenchude"],
                    "passage":    passage,
                    "base_score": r["base_score"],
                })
            return rows
 
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
 
 
# ─── NOTE: Thêm mapc vào metadata khi seed ────────────────────────────────────
#
# Trong seed_data_batch.py, sửa phần build metadata:
#
#   meta = {
#       "mapc":     rec.get("mapc") or None,   # ← THÊM DÒNG NÀY
#       "tenchude": rec.get("tenchude") or None,
#       "tendemuc": rec.get("tendemuc") or None,
#       "tenchuong":rec.get("tenchuong") or None,
#       "tendieu":  rec.get("tendieu") or None,
#       "noidung":  body[:500],
#   }
#
# Và trong query MySQL thêm d.mapc:
#   SELECT d.mapc, cd.ten AS tenchude, dm.ten AS tendemuc, ...
#
# ──────────────────────────────────────────────────────────────────────────────
 
 
# ─── MAIN ─────────────────────────────────────────────────────────────────────
 
def main():
    print("=" * 60)
    print("Graph RAG Builder — MySQL → Neo4j")
    print("=" * 60)
 
    conn   = get_mysql()
    driver = get_neo4j()
 
    try:
        print("\n[0] Tạo constraints & indexes...")
        create_constraints(driver)
 
        print("\n[1] Insert ChuDe...")
        insert_chude(driver, conn)
 
        print("\n[2] Insert DeMuc...")
        insert_demuc(driver, conn)
 
        print("\n[3] Insert Chuong...")
        insert_chuong(driver, conn)
 
        print("\n[4] Insert Dieu (66k nodes — sẽ mất vài phút)...")
        insert_dieu(driver, conn)
 
        print("\n[5] Insert LIEN_QUAN (38k edges)...")
        insert_lienquan(driver, conn)
 
        print("\n[6] Parse & insert THAM_CHIEU từ noidung...")
        insert_thamchieu(driver, conn)
 
        print("\n" + "=" * 60)
        print("🎉 DONE — Graph đã được build thành công!")
        print("   Neo4j Browser: http://localhost:7474")
        print("   Login: neo4j / password123")
        print("=" * 60)
 
    finally:
        conn.close()
        driver.close()
 
 
if __name__ == "__main__":
    main()
 