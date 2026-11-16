import os
from dotenv import load_dotenv  # thêm dòng này

load_dotenv()  # load trước khi getenv
from urllib.parse import urlparse
from tavily import TavilyClient

TAVILY_API_KEY  = os.getenv("TAVILY_API_KEY")
MAX_WEB_RESULTS = 5

client = TavilyClient(api_key=TAVILY_API_KEY)

# ── Trust config ──────────────────────────────────────────────
TRUSTED_DOMAINS = {
    "thuvienphapluat.vn":  1.0,
    "vbpl.vn":             1.0,
    "moj.gov.vn":          1.0,
    "phapdien.moj.gov.vn": 1.0,
    "quochoi.vn":          0.95,
    "chinhphu.vn":         0.9,
    "toaan.gov.vn":        0.9,
    "luatvietnam.vn":      0.85,
    "lawnet.vn":           0.85,
}

UNTRUSTED_PATTERNS = [
    "blogspot", "wordpress", "facebook", "tiktok",
    "youtube", "forum", "wiki", "123doc", "tailieu",
    "slideshare", "scribd",
]

LEGAL_KEYWORDS = [
    "điều", "khoản", "nghị định", "thông tư", "luật",
    "bộ luật", "quyết định", "quy định", "xử phạt",
    "điều khoản", "văn bản", "pháp luật", "hành chính",
]


# ── Trust scoring ─────────────────────────────────────────────
def _compute_trust_score(result: dict) -> float:
    url     = result.get("url", "")
    content = (result.get("noidung") or "").lower()
    title   = (result.get("ten") or "").lower()
    domain  = urlparse(url).netloc.replace("www.", "")

    domain_score = 0.4
    for trusted, score in TRUSTED_DOMAINS.items():
        if trusted in domain:
            domain_score = score
            break
    for pattern in UNTRUSTED_PATTERNS:
        if pattern in domain:
            domain_score = 0.1
            break

    keyword_hits  = sum(1 for kw in LEGAL_KEYWORDS if kw in content or kw in title)
    content_score = min(keyword_hits / 5, 1.0)

    return round(domain_score * 0.7 + content_score * 0.3, 3)


def _classify_trust(score: float) -> tuple[str, str]:
    """Trả về (trust_level, source_label)."""
    if score >= 0.7:
        return "high",   "[Web]"
    elif score >= 0.4:
        return "medium", "[Web - tham khảo]"
    else:
        return "low",    "[Web - không tin cậy]"


def _filter_and_score(results: list[dict]) -> list[dict]:
    scored = []
    for r in results:
        score        = _compute_trust_score(r)
        level, label = _classify_trust(score)

        if level == "low":
            print(f"[Trust] Loại bỏ ({score}): {r.get('url','')}")
            continue

        r["trust_score"]  = score
        r["trust_level"]  = level
        r["source_label"] = label
        r["source"]       = "web"
        scored.append(r)

    scored.sort(key=lambda x: x["trust_score"], reverse=True)
    print(f"[Trust] Còn lại: {len(scored)}/{len(results)} kết quả sau lọc")
    return scored


# ── Public API ────────────────────────────────────────────────
def search_tavily(query: str, top_k: int = MAX_WEB_RESULTS) -> list[dict]:
    try:
        response = client.search(
            query=f"pháp luật Việt Nam {query}",
            search_depth="advanced",
            max_results=top_k,
            include_answer=True,
        )
    except Exception as e:
        print(f"[Tavily] Lỗi search: {e}")
        return []

    raw = []

    # Answer tổng hợp — chưa có URL nên trust mặc định medium
    if response.get("answer"):
        raw.append({
            "mapc": None, "ten": "Tổng hợp từ web",
            "noidung": response["answer"],
            "tenchuong": None, "tendemuc": None, "tenchude": None,
            "passage": response["answer"],
            "score": 0.9, "url": "",
            "trust_score": 0.55, "trust_level": "medium",
            "source_label": "[Web - tham khảo]", "source": "web",
        })

    for r in response.get("results", []):
        passage = "\n".join(filter(None, [r.get("title",""), r.get("content","")[:800]]))
        raw.append({
            "mapc": None, "ten": r.get("title",""),
            "noidung": r.get("content","")[:800],
            "tenchuong": None, "tendemuc": None, "tenchude": None,
            "passage": passage,
            "score": r.get("score", 0.5),
            "url":   r.get("url",""),
        })

    filtered = _filter_and_score(raw)
    print(f"[Tavily] Trả về {len(filtered)} kết quả sau trust filter")
    return filtered
