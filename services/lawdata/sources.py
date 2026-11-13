"""
Lấy PDF văn bản từ link.

Hỗ trợ:
- Trang văn bản trên Công báo (congbao.chinhphu.vn/van-ban/….htm): robots.txt cho phép
  toàn bộ; đọc thêm ngày ban hành / hiệu lực trên trang và tải PDF bản chính thức.
- Link PDF trực tiếp ở trang khác: chỉ tải khi robots.txt của trang đó cho phép.

Không gọi các API bị robots.txt chặn (vd. phapluat.gov.vn/api, vbpl.vn/api).
"""
from __future__ import annotations

import html
import ipaddress
import re
import socket
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests

UA = "VNLawDataTool/1.0 (+admin data ingestion; low rate)"
MAX_PDF_BYTES = 40 * 1024 * 1024
TIMEOUT = 60
POLITE_DELAY_S = 2.0
CONGBAO_HOSTS = {"congbao.chinhphu.vn"}

_last_fetch: dict[str, float] = {}
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


class SourceError(Exception):
    pass


@dataclass
class Fetched:
    pdf: bytes
    url: str                     # link người dùng nhập
    pdf_url: str
    hints: dict = field(default_factory=dict)   # metadata đọc được từ trang nguồn


def _check_public_host(host: str) -> None:
    """Chặn link trỏ vào máy nội bộ (localhost, mạng LAN…)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise SourceError(f"Không phân giải được tên miền {host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise SourceError("Link trỏ tới địa chỉ nội bộ — không được phép")


def _robots_allows(url: str) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    rp = _robots_cache.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers={"User-Agent": UA}, timeout=20)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            rp.parse([])  # không đọc được robots.txt → coi như không cấm
        _robots_cache[base] = rp
    return rp.can_fetch(UA, url)


def _get(url: str, *, stream: bool = False) -> requests.Response:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise SourceError("Chỉ hỗ trợ link http/https")
    _check_public_host(p.hostname)
    if not _robots_allows(url):
        raise SourceError(f"robots.txt của {p.hostname} không cho phép tải link này")
    wait = POLITE_DELAY_S - (time.time() - _last_fetch.get(p.hostname, 0))
    if wait > 0:
        time.sleep(wait)
    _last_fetch[p.hostname] = time.time()
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, stream=stream, allow_redirects=True)
    if r.status_code != 200:
        raise SourceError(f"{p.hostname} trả mã {r.status_code}")
    return r


def _download_pdf(url: str) -> bytes:
    r = _get(url, stream=True)
    buf = bytearray()
    for chunk in r.iter_content(64 * 1024):
        buf += chunk
        if len(buf) > MAX_PDF_BYTES:
            raise SourceError("File lớn hơn 40MB")
    if not bytes(buf[:5]).startswith(b"%PDF"):
        raise SourceError("Link không trả về file PDF")
    return bytes(buf)


def _dmy_to_iso(s: str) -> str:
    d, m, y = s.split("/")
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def parse_congbao_page(page_html: str, page_url: str) -> tuple[str, dict]:
    """→ (link PDF, hints). Tách riêng để test không cần mạng."""
    text = html.unescape(page_html)
    pdfs = re.findall(r'https://congbaocdn\.chinhphu\.vn/[^"\'\s<>]+?\.pdf', text)
    if not pdfs:
        rel = re.findall(r'href="([^"]+\.pdf)"', text)
        pdfs = [urljoin(page_url, u) for u in rel]
    if not pdfs:
        raise SourceError("Không tìm thấy file PDF trên trang Công báo")
    hints: dict = {}
    if m := re.search(r"Ban hành:\s*(\d{1,2}/\d{1,2}/\d{4})", text):
        hints["ngay_ban_hanh"] = _dmy_to_iso(m.group(1))
    if m := re.search(r"Hiệu lực:\s*(\d{1,2}/\d{1,2}/\d{4})", text):
        hints["hieu_luc_tu"] = _dmy_to_iso(m.group(1))
    if m := re.search(r"<title>\s*(.*?)\s*</title>", text, re.S):
        hints["tieu_de_trang"] = re.sub(r"\s+", " ", m.group(1))[:300]
    return pdfs[0], hints


def fetch_url(url: str) -> Fetched:
    url = url.strip()
    p = urlparse(url)
    if (p.hostname or "") in CONGBAO_HOSTS and p.path.endswith(".htm"):
        page = _get(url).text
        pdf_url, hints = parse_congbao_page(page, url)
        hints["nguon"] = "congbao"
        return Fetched(_download_pdf(pdf_url), url, pdf_url, hints)
    if p.path.lower().endswith(".pdf") or "pdf" in p.query.lower():
        return Fetched(_download_pdf(url), url, url, {"nguon": "link_pdf"})
    raise SourceError("Chỉ hỗ trợ link trang văn bản Công báo (…/van-ban/….htm) hoặc link PDF trực tiếp")
