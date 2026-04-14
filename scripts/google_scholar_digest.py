#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import html
import json
import mailbox
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
from email import policy
from email.message import Message
from email.parser import BytesParser
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import arxiv_daily_digest as digest


DEFAULT_SCHOLAR_QUERIES = [
    "computer vision test-time adaptation",
    "computer vision domain shift distribution shift",
    "multimodal object tracking RGB-T RGB-D RGB-E",
    "RGB-X tracking computer vision",
]

SCHOLAR_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class ScholarBlockedError(RuntimeError):
    pass


def source_id(*parts: str) -> str:
    raw = "\n".join(digest.safe_text(p) for p in parts if p)
    return "scholar:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def sha1_short(text: str, length: int = 20) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def parse_year(text: str) -> str:
    match = re.search(r"\b(20\d{2}|19\d{2})\b", text or "")
    return match.group(1) if match else ""


def extract_years(text: str) -> List[str]:
    return re.findall(r"\b(20\d{2}|19\d{2})\b", text or "")


def pseudo_date(year: str, fallback_day: dt.date, index: int) -> str:
    if year:
        return dt.datetime(int(year), 1, 1, 12, 0, tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")
    stamp = dt.datetime.combine(fallback_day, dt.time(12, 0), tzinfo=dt.timezone.utc) - dt.timedelta(seconds=index)
    return stamp.isoformat().replace("+00:00", "Z")


def strip_tags(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    return digest.safe_text(html.unescape(re.sub(r"<[^>]+>", " ", text)))


def strip_tags_with_breaks(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(div|p|li|tr|td|section|article|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def expand_input_patterns(patterns: str) -> List[str]:
    found: List[str] = []
    seen: set[str] = set()
    for raw in re.split(r";;|\n", patterns or ""):
        pattern = digest.safe_text(raw)
        if not pattern:
            continue
        matches = sorted(glob.glob(pattern))
        if not matches and os.path.exists(pattern):
            matches = [pattern]
        for path in matches:
            abs_path = os.path.abspath(path)
            if abs_path not in seen and os.path.isfile(abs_path):
                seen.add(abs_path)
                found.append(abs_path)
    return found


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


KNOWN_VENUE_PATTERNS = [
    (r"IEEE Transactions on Pattern Analysis and Machine Intelligence|IEEE Trans(?:actions)? on PAMI|\bTPAMI\b", "TPAMI"),
    (r"International Journal of Computer Vision|\bIJCV\b", "IJCV"),
    (r"Conference on Computer Vision and Pattern Recognition|\bCVPR\b", "CVPR"),
    (r"International Conference on Computer Vision|\bICCV\b", "ICCV"),
    (r"European Conference on Computer Vision|\bECCV\b", "ECCV"),
    (r"Neural Information Processing Systems|\bNeurIPS\b|\bNIPS\b", "NeurIPS"),
    (r"International Conference on Learning Representations|\bICLR\b", "ICLR"),
    (r"International Conference on Machine Learning|\bICML\b", "ICML"),
    (r"Association for the Advancement of Artificial Intelligence|\bAAAI\b", "AAAI"),
    (r"ACM Multimedia|\bACM MM\b|\bMM\s*20\d{2}\b", "ACM MM"),
]


def clean_scholar_text(text: str) -> str:
    text = digest.safe_text(text)
    text = text.replace("\ufffd", "")
    text = text.replace("�", "")
    return digest.safe_text(text)


def detect_scholar_block_reason(body: str) -> str:
    lower = (body or "").lower()
    if not lower:
        return ""
    if (
        "our systems have detected unusual traffic" in lower
        or 'id="captcha-form"' in lower
        or "g-recaptcha" in lower
        or "please show you" in lower and "robot" in lower
        or "gs_captcha_" in lower
    ):
        return "Google Scholar blocked the request with a CAPTCHA / unusual traffic page."
    if "consent.google" in lower and ("before you continue" in lower or "cookies" in lower):
        return "Google Scholar returned a consent page instead of search results."
    return ""


def is_rate_limit_error_text(text: str) -> bool:
    lower = (text or "").lower()
    return "429" in lower or "too many requests" in lower or "rate limit" in lower


def scholar_request_url(url: str, timeout: int, cookie_path: str, retries: int = 2, accept_language: str = "zh-CN,zh;q=0.9,en;q=0.8") -> str:
    last_error = ""
    os.makedirs(os.path.dirname(cookie_path) or ".", exist_ok=True)
    for attempt in range(retries + 1):
        cmd = [
            "curl",
            "-L",
            "--silent",
            "--show-error",
            "--fail",
            "--compressed",
            "--connect-timeout",
            str(max(4, min(8, timeout // 2))),
            "--max-time",
            str(timeout),
            "--cookie",
            cookie_path,
            "--cookie-jar",
            cookie_path,
            "-H",
            "Connection: close",
            "-H",
            "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H",
            f"Accept-Language: {accept_language}",
            "-H",
            "Cache-Control: no-cache",
            "-H",
            "Pragma: no-cache",
            "-H",
            "Referer: https://scholar.google.com/",
            "-A",
            SCHOLAR_BROWSER_USER_AGENT,
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, check=False, timeout=timeout + 3)
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        if result.returncode == 0 and stdout:
            block_reason = detect_scholar_block_reason(stdout)
            if not block_reason:
                return stdout
            raise ScholarBlockedError(block_reason)
        else:
            last_error = stderr.strip() or f"curl failed for {url}"
            if is_rate_limit_error_text(last_error):
                raise ScholarBlockedError(last_error)
        if attempt < retries:
            time.sleep(6 + attempt * 6 + random.uniform(0.4, 1.2))
    if detect_scholar_block_reason(last_error):
        raise ScholarBlockedError(last_error)
    raise RuntimeError(last_error or f"curl failed for {url}")


def is_google_scholar_url(url: str) -> bool:
    clean = digest.safe_text(url)
    if not clean:
        return False
    parsed = urllib.parse.urlsplit(clean)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path or ""
    return netloc in {"scholar.google.com", "google.com"} and path.startswith("/scholar")


def chrome_available() -> bool:
    return sys.platform == "darwin" and Path("/Applications/Google Chrome.app").exists()


def run_osascript(script: str, timeout: int) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        check=False,
        timeout=timeout,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "osascript failed")
    return result.stdout


def apple_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def chrome_open_url(url: str, timeout: int) -> None:
    script = f'''
tell application "Google Chrome"
  activate
  if (count of windows) = 0 then
    make new window
  end if
  tell front window
    set tabCount to (count of tabs)
    make new tab with properties {{URL:{apple_quote(url)}}}
    set active tab index to (tabCount + 1)
  end tell
end tell
'''
    run_osascript(script, timeout=timeout)


def chrome_page_snapshot(timeout: int) -> Tuple[str, str]:
    script = '''
tell application "Google Chrome"
  if (count of windows) = 0 then error "Google Chrome has no open windows."
  set t to active tab of front window
  set pageTitle to title of t
  set pageHtml to execute t javascript "document.documentElement.outerHTML"
  return pageTitle & linefeed & "===HTML===" & linefeed & pageHtml
end tell
'''
    raw = run_osascript(script, timeout=timeout)
    marker = "\n===HTML===\n"
    if marker in raw:
        title, html_text = raw.split(marker, 1)
        return title.strip(), html_text
    return raw.strip(), ""


def fetch_scholar_html_via_chrome(url: str, wait_timeout: int, poll_seconds: float = 2.0) -> str:
    chrome_open_url(url, timeout=min(20, wait_timeout))
    captcha_notice_printed = False
    deadline = time.time() + max(10, wait_timeout)
    while time.time() < deadline:
        _title, html_text = chrome_page_snapshot(timeout=min(15, wait_timeout))
        if html_text:
            block_reason = detect_scholar_block_reason(html_text)
            result_count = len(re.findall(r"<h3 class=\"gs_rt\"[^>]*>", html_text, flags=re.I))
            if result_count > 0:
                return html_text
            if block_reason and not captcha_notice_printed:
                print("[ACTION] Google Scholar 在 Chrome 中弹出了验证码/异常流量页，请在浏览器中完成验证，脚本会继续等待结果。")
                captcha_notice_printed = True
        time.sleep(max(0.5, poll_seconds))
    raise ScholarBlockedError("Chrome browser session did not leave the Google Scholar CAPTCHA / sorry page before timeout.")


def is_scholar_snippet(text: str) -> bool:
    clean = digest.safe_text(text)
    return bool(clean) and ("…" in clean or "..." in clean or clean.count("…") >= 1)


def parse_source_venue(publication_info: str) -> str:
    info = clean_scholar_text(publication_info)
    for pattern, label in KNOWN_VENUE_PATTERNS:
        if re.search(pattern, info, flags=re.I):
            return label
    parts = [digest.safe_text(x) for x in re.split(r"\s*[-–—]\s*", info) if digest.safe_text(x)]
    if len(parts) >= 2:
        candidate_parts = parts[1:-1] or parts[1:]
        for venue_part in candidate_parts:
            venue_part = re.sub(r",?\s*(19|20)\d{2}\b.*$", "", venue_part).strip(" ,")
            if not venue_part or re.fullmatch(r"(19|20)\d{2}", venue_part):
                continue
            normalized = digest.normalize_venue_name(venue_part)
            return normalized if normalized and normalized != venue_part.upper()[:80] else venue_part[:120]
    return digest.normalize_venue_name(info)


def parse_date_text(text: str) -> Tuple[str, str]:
    clean = digest.safe_text(str(text or ""))
    if not clean:
        return "", ""
    match = re.search(r"\b((?:19|20)\d{2})[-/](\d{1,2})(?:[-/](\d{1,2}))?", clean)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3) or "1")
        try:
            return dt.date(year, month, day).isoformat(), "day" if match.group(3) else "month"
        except ValueError:
            pass
    match = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b",
        clean,
        flags=re.I,
    )
    if match:
        month = MONTHS.get(match.group(1).lower().rstrip("."), 0)
        try:
            return dt.date(int(match.group(3)), month, int(match.group(2))).isoformat(), "day"
        except ValueError:
            pass
    match = re.search(
        r"\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?,?\s+((?:19|20)\d{2})\b",
        clean,
        flags=re.I,
    )
    if match:
        month = MONTHS.get(match.group(2).lower().rstrip("."), 0)
        try:
            return dt.date(int(match.group(3)), month, int(match.group(1))).isoformat(), "day"
        except ValueError:
            pass
    match = re.search(r"\b((?:19|20)\d{2})\b", clean)
    if match:
        return f"{match.group(1)}-01-01", "year"
    return "", ""


def meta_contents(body: str, names: Iterable[str]) -> Dict[str, str]:
    wanted = {name.lower() for name in names}
    found: Dict[str, str] = {}
    for match in re.finditer(r"<meta\b[^>]*>", body or "", flags=re.S | re.I):
        tag = match.group(0)
        key_match = re.search(r"\b(?:name|property|itemprop)=['\"]([^'\"]+)['\"]", tag, flags=re.I)
        content_match = re.search(r"\bcontent=['\"]([^'\"]*)['\"]", tag, flags=re.S | re.I)
        if not key_match or not content_match:
            continue
        key = key_match.group(1).lower()
        if key in wanted and key not in found:
            found[key] = clean_scholar_text(html.unescape(content_match.group(1)))
    return found


def iter_jsonld_objects(body: str) -> Iterable[object]:
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', body or "", flags=re.S | re.I):
        raw = html.unescape(match.group(1)).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, list):
            for item in data:
                yield item
        else:
            yield data


def first_jsonld_value(data: object, keys: Iterable[str]) -> str:
    wanted = set(keys)
    if isinstance(data, dict):
        for key in wanted:
            value = data.get(key)
            if isinstance(value, str) and digest.safe_text(value):
                return clean_scholar_text(value)
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                value = first_jsonld_value(item, keys)
                if value:
                    return value
    elif isinstance(data, list):
        for item in data:
            value = first_jsonld_value(item, keys)
            if value:
                return value
    return ""


def extract_detail_metadata(body: str) -> Dict[str, str]:
    meta = meta_contents(
        body,
        [
            "citation_title", "citation_abstract", "citation_publication_date", "citation_online_date",
            "citation_date", "citation_journal_title", "citation_conference_title", "citation_publisher",
            "citation_pdf_url", "citation_doi", "dc.description", "dc.date", "description",
            "og:description", "og:title", "article:published_time", "datepublished",
        ],
    )
    jsonld_items = list(iter_jsonld_objects(body))
    abstract = (
        meta.get("citation_abstract")
        or first_jsonld_value(jsonld_items, ["abstract"])
        or meta.get("dc.description")
        or meta.get("og:description")
        or meta.get("description")
        or first_jsonld_value(jsonld_items, ["description"])
    )
    abstract = clean_scholar_text(strip_tags(abstract))
    if not abstract or len(abstract) < 80:
        abstract = extract_visible_abstract(body)
    title = meta.get("citation_title") or meta.get("og:title") or first_jsonld_value(jsonld_items, ["headline", "name"])
    venue = meta.get("citation_journal_title") or meta.get("citation_conference_title") or meta.get("citation_publisher")
    date_text = (
        meta.get("citation_publication_date")
        or meta.get("citation_online_date")
        or meta.get("citation_date")
        or meta.get("dc.date")
        or meta.get("article:published_time")
        or first_jsonld_value(jsonld_items, ["datePublished", "dateCreated", "dateModified"])
    )
    date_iso, precision = parse_date_text(date_text)
    return {
        "title": clean_scholar_text(title),
        "abstract": abstract,
        "venue": clean_scholar_text(venue),
        "date": date_iso,
        "date_precision": precision,
        "pdf_url": clean_scholar_text(meta.get("citation_pdf_url", "")),
        "doi": clean_scholar_text(meta.get("citation_doi", "")),
    }


def extract_visible_abstract(body: str) -> str:
    candidates: List[str] = []
    patterns = [
        r'<(?:section|div|article)[^>]+(?:id|class)=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.*?)</(?:section|div|article)>',
        r'<h[1-4][^>]*>\s*Abstract\s*</h[1-4]>\s*<p[^>]*>(.*?)</p>',
        r'<span[^>]+class=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.*?)</span>',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, body or "", flags=re.S | re.I):
            text = clean_scholar_text(strip_tags(match.group(1)))
            if len(text) >= 80 and not looks_like_boilerplate(text):
                candidates.append(text)
    if not candidates:
        return ""
    candidates.sort(key=len, reverse=True)
    return candidates[0][:5000]


def looks_like_boilerplate(text: str) -> bool:
    lower = text.lower()
    bad = ["cookie", "javascript", "enable cookies", "privacy policy", "institutional access", "sign in"]
    return any(item in lower for item in bad)


def scholar_status(pdf_url: str, result_url: str) -> str:
    if pdf_url:
        return "有全文链接"
    if result_url and re.search(r"\.(pdf|ps)(\?|$)", result_url, flags=re.I):
        return "有全文链接"
    return "未发现全文链接"


def refresh_scholar_paper(paper: digest.Paper) -> digest.Paper:
    source_venue = paper.source_venue
    accepted_venue = paper.accepted_venue
    accepted_hint = paper.accepted_hint
    source_platform = paper.source_platform
    full_text_status = paper.full_text_status
    full_text_url = paper.full_text_url
    source_date_precision = paper.source_date_precision
    source_year = paper.source_year
    paper = digest.refresh_paper_derived_fields(paper)
    paper.source_venue = source_venue
    paper.source_platform = source_platform
    paper.full_text_status = full_text_status
    paper.full_text_url = full_text_url
    paper.source_date_precision = source_date_precision
    paper.source_year = source_year
    if accepted_venue:
        paper.accepted_venue = accepted_venue
    if accepted_hint:
        paper.accepted_hint = accepted_hint
    return paper


def make_paper(
    *,
    title: str,
    url: str,
    snippet: str,
    publication_info: str,
    pdf_url: str,
    fallback_day: dt.date,
    index: int,
    query: str,
) -> digest.Paper:
    year = parse_year(publication_info)
    authors = []
    if publication_info:
        author_part = publication_info.split("-", 1)[0]
        authors = [digest.safe_text(x) for x in re.split(r",| and ", author_part) if digest.safe_text(x)]
    status = scholar_status(pdf_url, url)
    full_text_url = pdf_url or (url if status == "有全文链接" else "")
    source_venue = parse_source_venue(publication_info)
    accepted_hint = digest.safe_text(f"Google Scholar publication line: {publication_info}")
    paper = digest.Paper(
        arxiv_id=source_id(title, url, publication_info),
        title=title or url or "Untitled Google Scholar result",
        title_zh="",
        authors=authors[:12],
        published=pseudo_date(year, fallback_day, index),
        updated=pseudo_date(year, fallback_day, index),
        categories=["Google Scholar", "cs.CV"],
        summary_en=snippet,
        summary_zh="",
        link_abs=url or pdf_url,
        link_pdf=pdf_url or url,
        comment=digest.safe_text(f"Google Scholar query: {query}; {publication_info}"),
        journal_ref=publication_info,
        major_area="CV",
        accepted_venue=source_venue,
        accepted_hint=accepted_hint,
        source_platform="Google Scholar",
        source_venue=source_venue,
        full_text_status=status,
        full_text_url=full_text_url,
        source_date_precision="year" if year else "",
        source_year=year,
    )
    return refresh_scholar_paper(paper)


def decode_scholar_redirect_url(url: str) -> str:
    clean = digest.safe_text(html.unescape(url))
    if not clean:
        return ""
    if clean.startswith("/"):
        clean = urllib.parse.urljoin("https://scholar.google.com", clean)
    parsed = urllib.parse.urlsplit(clean)
    query = urllib.parse.parse_qs(parsed.query)
    if parsed.netloc.endswith("scholar.google.com") and parsed.path in {"/scholar_url", "/scholar", "/scholar_lookup"}:
        for key in ["url", "q"]:
            candidates = query.get(key) or []
            if candidates:
                target = digest.safe_text(urllib.parse.unquote(candidates[0]))
                if target.startswith("http"):
                    return target
    return clean


def is_probable_alert_title(title: str, url: str) -> bool:
    clean = digest.safe_text(title)
    lower = clean.lower()
    if len(clean) < 12:
        return False
    if not url.startswith("http"):
        return False
    bad_tokens = [
        "google scholar",
        "unsubscribe",
        "alert",
        "alerts",
        "help",
        "sign in",
        "view all",
        "manage alerts",
        "my profile",
        "my library",
        "settings",
    ]
    return not any(token in lower for token in bad_tokens)


def pick_alert_publication_and_snippet(context_text: str) -> Tuple[str, str]:
    lines = [digest.safe_text(x) for x in re.split(r"\n+|[•·]+", context_text or "") if digest.safe_text(x)]
    publication = ""
    snippet_candidates: List[str] = []
    for line in lines:
        lower = line.lower()
        if any(token in lower for token in ["cited by", "related articles", "save", "review", "alert", "unsubscribe"]):
            continue
        if not publication and (parse_year(line) or " - " in line or "… - " in line):
            publication = line[:240]
            continue
        if len(line) >= 80:
            snippet_candidates.append(line[:5000])
    snippet = snippet_candidates[0] if snippet_candidates else ""
    return publication, snippet


def parse_scholar_alert_html(html_text: str, query: str, fallback_day: dt.date, limit: int) -> List[digest.Paper]:
    anchors = list(re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html_text or "", flags=re.S | re.I))
    papers: List[digest.Paper] = []
    seen: set[str] = set()
    for idx, match in enumerate(anchors):
        href = decode_scholar_redirect_url(match.group(1))
        title = strip_tags(match.group(2))
        if not is_probable_alert_title(title, href):
            continue
        next_start = anchors[idx + 1].start() if idx + 1 < len(anchors) else min(len(html_text or ""), match.end() + 1200)
        context_text = strip_tags_with_breaks((html_text or "")[match.end():next_start])
        publication_info, snippet = pick_alert_publication_and_snippet(context_text)
        paper = make_paper(
            title=title,
            url=href,
            snippet=snippet,
            publication_info=publication_info,
            pdf_url="",
            fallback_day=fallback_day,
            index=len(papers),
            query=query,
        )
        key = normalize_identity_url(paper.link_abs) or normalize_identity_text(paper.title)
        if not key or key in seen:
            continue
        seen.add(key)
        papers.append(paper)
        if len(papers) >= limit:
            break
    return papers


def extract_message_bodies(message: Message) -> Tuple[str, str]:
    html_parts: List[str] = []
    text_parts: List[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except Exception:
                text = payload.decode("utf-8", errors="replace")
            ctype = part.get_content_type()
            if ctype == "text/html":
                html_parts.append(text)
            elif ctype == "text/plain":
                text_parts.append(text)
    else:
        payload = message.get_payload(decode=True) or b""
        charset = message.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except Exception:
            text = payload.decode("utf-8", errors="replace")
        if message.get_content_type() == "text/html":
            html_parts.append(text)
        else:
            text_parts.append(text)
    return "\n".join(html_parts), "\n".join(text_parts)


def load_saved_html_results(patterns: str, fallback_day: dt.date, limit: int) -> List[digest.Paper]:
    papers: List[digest.Paper] = []
    for path in expand_input_patterns(patterns):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                body = f.read()
        except Exception:
            continue
        query = f"saved_html:{os.path.basename(path)}"
        papers.extend(parse_scholar_html(body, query, fallback_day, limit))
        if len(papers) >= limit:
            break
    return digest.dedupe_papers(papers)[:limit]


def load_alert_results(alert_glob: str, alert_mbox: str, fallback_day: dt.date, limit: int) -> List[digest.Paper]:
    papers: List[digest.Paper] = []
    for path in expand_input_patterns(alert_glob):
        try:
            with open(path, "rb") as f:
                message = BytesParser(policy=policy.default).parse(f)
        except Exception:
            continue
        subject = digest.safe_text(str(message.get("subject", ""))) or os.path.basename(path)
        html_body, text_body = extract_message_bodies(message)
        body = html_body or text_body
        if not body:
            continue
        papers.extend(parse_scholar_alert_html(body if html_body else html.escape(text_body).replace("\n", "<br>"), subject, fallback_day, limit))
        if len(papers) >= limit:
            return digest.dedupe_papers(papers)[:limit]
    for path in expand_input_patterns(alert_mbox):
        try:
            box = mailbox.mbox(path)
        except Exception:
            continue
        for message in box:
            subject = digest.safe_text(str(message.get("subject", ""))) or os.path.basename(path)
            html_body, text_body = extract_message_bodies(message)
            body = html_body or text_body
            if not body:
                continue
            papers.extend(parse_scholar_alert_html(body if html_body else html.escape(text_body).replace("\n", "<br>"), subject, fallback_day, limit))
            if len(papers) >= limit:
                return digest.dedupe_papers(papers)[:limit]
    return digest.dedupe_papers(papers)[:limit]


def parse_scholar_html(html_text: str, query: str, fallback_day: dt.date, limit: int) -> List[digest.Paper]:
    block_reason = detect_scholar_block_reason(html_text)
    if block_reason:
        raise ScholarBlockedError(block_reason)
    blocks = re.findall(r"<div class=\"gs_r gs_or.*?</div>\s*</div>\s*</div>", html_text or "", flags=re.S)
    if not blocks:
        blocks = re.findall(r"<div class=\"gs_r.*?</div>\s*</div>\s*</div>", html_text or "", flags=re.S)
    papers: List[digest.Paper] = []
    for idx, block in enumerate(blocks):
        title_match = re.search(r"<h3 class=\"gs_rt\"[^>]*>(.*?)</h3>", block, flags=re.S)
        if not title_match:
            continue
        title_html = title_match.group(1)
        href_match = re.search(r"<a[^>]+href=\"([^\"]+)\"", title_html, flags=re.S)
        url = decode_scholar_redirect_url(html.unescape(href_match.group(1))) if href_match else ""
        title = strip_tags(title_html)
        title = re.sub(r"^\[[A-Z]+\]\s*", "", title).strip()
        info_match = re.search(r"<div class=\"gs_a\"[^>]*>(.*?)</div>", block, flags=re.S)
        snippet_match = re.search(r"<div class=\"gs_rs\"[^>]*>(.*?)</div>", block, flags=re.S)
        pdf_match = re.search(r"<div class=\"gs_or_ggsm\".*?<a[^>]+href=\"([^\"]+)\"", block, flags=re.S)
        paper = make_paper(
            title=title,
            url=url,
            snippet=strip_tags(snippet_match.group(1) if snippet_match else ""),
            publication_info=strip_tags(info_match.group(1) if info_match else ""),
            pdf_url=decode_scholar_redirect_url(html.unescape(pdf_match.group(1))) if pdf_match else "",
            fallback_day=fallback_day,
            index=idx,
            query=query,
        )
        papers.append(paper)
        if len(papers) >= limit:
            break
    return papers


def fetch_scholar_html(
    query: str,
    start: int,
    timeout: int,
    num: int = 20,
    year_from: int = 0,
    sort_by_date: bool = False,
    hl: str = "zh-CN",
    cookie_path: str = "/tmp/google_scholar_cookies.txt",
) -> str:
    params_dict = {"q": query, "hl": hl, "as_sdt": "0,5", "start": start, "num": max(1, min(20, num))}
    if year_from > 0:
        params_dict["as_ylo"] = str(year_from)
    if sort_by_date:
        params_dict["scisbd"] = "1"
    params = urllib.parse.urlencode(params_dict)
    url = f"https://scholar.google.com/scholar?{params}"
    body = scholar_request_url(url, timeout=timeout, cookie_path=cookie_path)
    block_reason = detect_scholar_block_reason(body)
    if block_reason:
        raise ScholarBlockedError(block_reason)
    return body


def scholar_query_url(
    query: str,
    start: int,
    num: int = 20,
    year_from: int = 0,
    sort_by_date: bool = False,
    hl: str = "zh-CN",
) -> str:
    params_dict = {"q": query, "hl": hl, "as_sdt": "0,5", "start": start, "num": max(1, min(20, num))}
    if year_from > 0:
        params_dict["as_ylo"] = str(year_from)
    if sort_by_date:
        params_dict["scisbd"] = "1"
    return f"https://scholar.google.com/scholar?{urllib.parse.urlencode(params_dict)}"


def fetch_from_serpapi(
    query: str,
    api_key: str,
    limit: int,
    fallback_day: dt.date,
    timeout: int,
    year_from: int = 0,
    sort_by_date: bool = False,
) -> List[digest.Paper]:
    papers: List[digest.Paper] = []
    start = 0
    while len(papers) < limit:
        params = urllib.parse.urlencode({
            "engine": "google_scholar",
            "q": query,
            "api_key": api_key,
            "num": min(20, limit - len(papers)),
            "start": start,
        })
        if year_from > 0:
            params += "&" + urllib.parse.urlencode({"as_ylo": str(year_from)})
        if sort_by_date:
            params += "&" + urllib.parse.urlencode({"scisbd": "1"})
        raw = digest.request_url(f"https://serpapi.com/search.json?{params}", timeout=timeout, retries=1, allow_partial=False)
        data = json.loads(raw)
        results = data.get("organic_results", []) or []
        if not results:
            break
        for idx, item in enumerate(results):
            resources = item.get("resources", []) or []
            pdf_url = ""
            for resource in resources:
                link = str(resource.get("link", ""))
                if link and (str(resource.get("file_format", "")).upper() == "PDF" or link.lower().endswith(".pdf")):
                    pdf_url = link
                    break
            publication_info = " ".join([
                str(item.get("publication_info", {}).get("summary", "")),
                str(item.get("inline_links", {}).get("cited_by", {}).get("total", "")),
            ])
            papers.append(make_paper(
                title=str(item.get("title", "")),
                url=str(item.get("link", "")),
                snippet=str(item.get("snippet", "")),
                publication_info=publication_info,
                pdf_url=pdf_url,
                fallback_day=fallback_day,
                index=start + idx,
                query=query,
            ))
            if len(papers) >= limit:
                break
        start += len(results)
        time.sleep(0.4)
    return papers


def detail_cache_key(paper: digest.Paper) -> str:
    return detail_candidate_url(paper) or paper.link_abs or paper.link_pdf or paper.arxiv_id


def detail_candidate_url(paper: digest.Paper) -> str:
    for raw in [paper.link_abs, paper.full_text_url, paper.link_pdf]:
        url = decode_scholar_redirect_url(raw)
        if not url or is_google_scholar_url(url):
            continue
        if re.search(r"\.(pdf|ps)(?:[?#]|$)", url, flags=re.I):
            continue
        return url
    return ""


def apply_detail_metadata(paper: digest.Paper, metadata: Dict[str, str]) -> digest.Paper:
    title = digest.safe_text(metadata.get("title", ""))
    if title and len(title) >= 8:
        paper.title = title
    abstract = digest.safe_text(metadata.get("abstract", ""))
    if abstract and len(abstract) >= 80 and not looks_like_boilerplate(abstract):
        paper.summary_en = abstract
    elif is_scholar_snippet(paper.summary_en):
        paper.summary_en = "Detail page did not expose a complete parseable abstract; the incomplete Google Scholar snippet was omitted."
    venue = parse_source_venue(metadata.get("venue", "") or paper.journal_ref)
    if venue:
        paper.source_venue = venue
        paper.accepted_venue = venue
    date_iso = metadata.get("date", "")
    precision = metadata.get("date_precision", "")
    if date_iso:
        paper.published = f"{date_iso}T12:00:00Z"
        paper.updated = paper.published
        paper.source_date_precision = precision
        if not paper.source_year:
            paper.source_year = date_iso[:4]
    pdf_url = digest.safe_text(metadata.get("pdf_url", ""))
    if pdf_url:
        paper.link_pdf = pdf_url
        paper.full_text_url = pdf_url
        paper.full_text_status = "有全文链接"
    elif "Detail page did not expose" in paper.summary_en:
        paper.full_text_status = f"{paper.full_text_status}; 未解析到完整摘要" if paper.full_text_status else "未解析到完整摘要"
    doi = digest.safe_text(metadata.get("doi", ""))
    if doi and doi not in paper.comment:
        paper.comment = digest.safe_text(f"{paper.comment}; DOI: {doi}")
    if paper.source_venue and not paper.accepted_hint:
        paper.accepted_hint = f"Google Scholar publication line: {paper.journal_ref}"
    return refresh_scholar_paper(paper)


def load_detail_cache(path: str) -> Dict[str, dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_detail_cache(path: str, cache: Dict[str, dict]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def enrich_from_detail_pages(
    papers: List[digest.Paper],
    cache: Dict[str, dict],
    cache_path: str,
    limit: int,
    timeout: int,
) -> Dict[str, int]:
    stats = {"attempted": 0, "abstracts": 0, "dates": 0, "failed": 0, "cached": 0}
    targets = papers if limit < 0 else papers[:max(0, limit)]
    changed = False
    for idx, paper in enumerate(targets, start=1):
        url = detail_candidate_url(paper)
        if not url or url.startswith("scholar:"):
            continue
        key = detail_cache_key(paper)
        cached = cache.get(key, {})
        metadata = cached.get("metadata") if isinstance(cached, dict) else None
        if isinstance(metadata, dict):
            stats["cached"] += 1
        else:
            stats["attempted"] += 1
            try:
                body = digest.request_url(url, timeout=timeout, retries=1, allow_partial=True)
                metadata = extract_detail_metadata(body)
                cache[key] = {
                    "url": url,
                    "metadata": metadata,
                    "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
                }
                changed = True
                time.sleep(0.25)
            except Exception as exc:
                stats["failed"] += 1
                cache[key] = {
                    "url": url,
                    "metadata": {},
                    "error": str(exc),
                    "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
                }
                changed = True
                continue
        before_abstract = paper.summary_en
        before_date = paper.published
        apply_detail_metadata(paper, metadata or {})
        if paper.summary_en and paper.summary_en != before_abstract and not paper.summary_en.startswith("Detail page did not expose"):
            stats["abstracts"] += 1
        if paper.published != before_date and paper.source_date_precision == "day":
            stats["dates"] += 1
        if changed and idx % 20 == 0:
            save_detail_cache(cache_path, cache)
            changed = False
    if changed:
        save_detail_cache(cache_path, cache)
    return stats


def date_window(target_day: dt.date, days: int) -> Tuple[dt.date, dt.date]:
    days = max(1, days)
    return target_day - dt.timedelta(days=days - 1), target_day


def add_date_terms(query: str, target_day: dt.date, days: int) -> str:
    start_day, end_day = date_window(target_day, days)
    before_day = end_day + dt.timedelta(days=1)
    return f"{query} after:{start_day.isoformat()} before:{before_day.isoformat()}"


def paper_year(paper: digest.Paper) -> str:
    if paper.source_year:
        return paper.source_year
    for text in [paper.journal_ref, paper.comment, paper.published, paper.updated]:
        year = parse_year(text)
        if year:
            return year
    return ""


def filter_by_year(papers: List[digest.Paper], year_from: int, mode: str) -> Tuple[List[digest.Paper], Dict[str, int]]:
    if mode == "off" or year_from <= 0:
        return papers, {"kept": len(papers), "excluded": 0, "before_year": 0, "unknown_year_kept": 0}
    stats = {"kept": len(papers), "excluded": 0, "before_year": 0, "unknown_year_kept": 0}
    for paper in papers:
        year = paper_year(paper)
        if year:
            paper.source_year = year
            if int(year) < year_from:
                stats["before_year"] += 1
        else:
            stats["unknown_year_kept"] += 1
    return papers, stats


def normalize_identity_text(text: str) -> str:
    clean = digest.safe_text(text).lower()
    clean = html.unescape(clean)
    clean = re.sub(r"^\[[a-z]\]\s*", "", clean)
    clean = re.sub(r"[^a-z0-9]+", " ", clean)
    return digest.safe_text(clean)


def normalize_identity_url(url: str) -> str:
    clean = digest.safe_text(url)
    if not clean:
        return ""
    parsed = urllib.parse.urlsplit(clean)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+", "/", parsed.path or "/")
    path = path.rstrip("/")
    if netloc in {"scholar.google.com", "google.com"}:
        return ""
    return urllib.parse.urlunsplit((scheme, netloc, path, "", ""))


def extract_doi_from_text(text: str) -> str:
    match = re.search(r"\b10\.\d{4,9}/[^\s\"'<>]+", text or "", flags=re.I)
    if not match:
        return ""
    doi = match.group(0).rstrip(".,;)】]").lower()
    return doi


def extract_arxiv_from_url(url: str) -> str:
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#/]+)", url or "", flags=re.I)
    return match.group(1).replace(".pdf", "") if match else ""


def scholar_identity_candidates(paper: digest.Paper) -> List[str]:
    keys: List[str] = []
    merged = " ".join([paper.comment, paper.journal_ref, paper.link_abs, paper.link_pdf, paper.full_text_url])
    doi = extract_doi_from_text(merged)
    if doi:
        keys.append(f"doi:{doi}")
    for url in [paper.link_abs, paper.link_pdf, paper.full_text_url]:
        arxiv_id = extract_arxiv_from_url(url)
        if arxiv_id:
            keys.append(f"arxiv:{arxiv_id.lower()}")
    for url in [paper.link_abs, paper.link_pdf, paper.full_text_url]:
        norm_url = normalize_identity_url(url)
        if norm_url:
            keys.append(f"url:{sha1_short(norm_url, 24)}")
    title_norm = normalize_identity_text(paper.title)
    venue_norm = normalize_identity_text(paper.source_venue or paper.accepted_venue or paper.journal_ref)
    year = paper_year(paper)
    if title_norm and year:
        keys.append(f"title_year:{sha1_short(title_norm + '|' + venue_norm + '|' + year, 24)}")
    if title_norm and len(title_norm) >= 20:
        keys.append(f"title:{sha1_short(title_norm, 24)}")
    deduped: List[str] = []
    for key in keys:
        if key and key not in deduped:
            deduped.append(key)
    return deduped or [paper.arxiv_id or source_id(paper.title, paper.link_abs, paper.journal_ref)]


def default_seen_state(year_from: int = 0) -> Dict[str, object]:
    return {
        "version": 2,
        "scope": "google_scholar_dedupe_keys",
        "year": int(year_from or 0),
        "keys": {},
        "records": {},
        "updated_at": "",
    }


def load_seen_state(path: str) -> Dict[str, object]:
    if not os.path.exists(path):
        return default_seen_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default_seen_state()
    if not isinstance(data, dict):
        return default_seen_state()
    if isinstance(data.get("keys"), dict):
        normalized = default_seen_state(int(data.get("year", 0) or 0))
        normalized["version"] = int(data.get("version", 2) or 2)
        normalized["updated_at"] = digest.safe_text(str(data.get("updated_at", "")))
        keys = {}
        for key, value in data.get("keys", {}).items():
            if digest.safe_text(str(key)):
                keys[digest.safe_text(str(key))] = digest.safe_text(str(value))
        normalized["keys"] = keys
        records = {}
        for key, value in data.get("records", {}).items() if isinstance(data.get("records"), dict) else []:
            primary = digest.safe_text(str(key))
            if primary and isinstance(value, dict):
                records[primary] = {
                    "title": digest.safe_text(str(value.get("title", ""))),
                    "url": digest.safe_text(str(value.get("url", ""))),
                    "year": digest.safe_text(str(value.get("year", ""))),
                    "added_on": digest.safe_text(str(value.get("added_on", ""))),
                }
        normalized["records"] = records
        return normalized
    normalized = default_seen_state()
    keys: Dict[str, str] = {}
    records: Dict[str, Dict[str, str]] = {}
    seen = data.get("seen", {}) if isinstance(data.get("seen"), dict) else {}
    aliases = data.get("aliases", {}) if isinstance(data.get("aliases"), dict) else {}
    fallback_stamp = digest.safe_text(str(data.get("updated_at", "")))
    for key, entry in seen.items():
        stamp = fallback_stamp
        if isinstance(entry, dict):
            stamp = digest.safe_text(str(entry.get("last_report_date", ""))) or digest.safe_text(str(entry.get("last_seen_at", ""))) or fallback_stamp
            primary = digest.safe_text(str(key))
            if primary:
                records[primary] = {
                    "title": digest.safe_text(str(entry.get("title", ""))),
                    "url": digest.safe_text(str(entry.get("link_abs", ""))) or digest.safe_text(str(entry.get("link_pdf", ""))),
                    "year": digest.safe_text(str(entry.get("year", ""))),
                    "added_on": stamp,
                }
            for item in entry.get("identity_keys", []) if isinstance(entry.get("identity_keys"), list) else []:
                item = digest.safe_text(str(item))
                if item:
                    keys[item] = stamp
        key = digest.safe_text(str(key))
        if key:
            keys[key] = stamp
    for key, value in aliases.items():
        key = digest.safe_text(str(key))
        if key:
            keys[key] = fallback_stamp or digest.safe_text(str(value))
    normalized["keys"] = keys
    normalized["records"] = records
    normalized["updated_at"] = fallback_stamp
    return normalized


def save_seen_state(path: str, state: Dict[str, object]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def seen_key_map(state: Dict[str, object]) -> Dict[str, str]:
    keys = state.get("keys", {})
    return keys if isinstance(keys, dict) else {}


def seen_record_map(state: Dict[str, object]) -> Dict[str, Dict[str, str]]:
    records = state.get("records", {})
    return records if isinstance(records, dict) else {}


def load_runtime_state(path: str) -> Dict[str, object]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_runtime_state(path: str, state: Dict[str, object]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def default_query_state() -> Dict[str, object]:
    return {
        "version": 1,
        "scope": "google_scholar_query_progress",
        "queries": {},
        "updated_at": "",
    }


def load_query_state(path: str) -> Dict[str, object]:
    if not os.path.exists(path):
        return default_query_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default_query_state()
    if not isinstance(data, dict):
        return default_query_state()
    state = default_query_state()
    queries = data.get("queries", {})
    if isinstance(queries, dict):
        state["queries"] = queries
    state["updated_at"] = digest.safe_text(str(data.get("updated_at", "")))
    version = data.get("version", 1)
    try:
        state["version"] = int(version)
    except Exception:
        state["version"] = 1
    return state


def save_query_state(path: str, state: Dict[str, object]) -> None:
    state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def query_state_bucket_key(query: str, year_from: int, hl: str) -> str:
    return sha1_short(f"{digest.safe_text(query)}|{int(year_from or 0)}|{digest.safe_text(hl)}", 24)


def query_state_entries(state: Dict[str, object]) -> Dict[str, object]:
    queries = state.get("queries", {})
    return queries if isinstance(queries, dict) else {}


def get_query_state_entry(state: Dict[str, object], query: str, year_from: int, hl: str) -> Dict[str, object]:
    queries = query_state_entries(state)
    if not isinstance(queries, dict):
        queries = {}
        state["queries"] = queries
    key = query_state_bucket_key(query, year_from, hl)
    entry = queries.get(key)
    if not isinstance(entry, dict):
        entry = {
            "query": query,
            "year_from": int(year_from or 0),
            "hl": hl,
            "next_start": 0,
            "pages": {},
            "last_success_at": "",
            "last_blocked_at": "",
            "last_error": "",
        }
        queries[key] = entry
    else:
        entry["query"] = query
        entry["year_from"] = int(year_from or 0)
        entry["hl"] = hl
        if not isinstance(entry.get("pages"), dict):
            entry["pages"] = {}
    return entry


def serialize_query_page_paper(paper: digest.Paper, query: str) -> Dict[str, str]:
    return {
        "query": query,
        "title": paper.title,
        "link_abs": paper.link_abs,
        "link_pdf": paper.link_pdf,
        "summary_en": paper.summary_en,
        "publication_info": paper.journal_ref,
        "published": paper.published,
        "updated": paper.updated,
        "source_venue": paper.source_venue,
        "accepted_venue": paper.accepted_venue,
        "full_text_status": paper.full_text_status,
        "full_text_url": paper.full_text_url,
        "source_date_precision": paper.source_date_precision,
        "source_year": paper.source_year,
        "comment": paper.comment,
    }


def deserialize_query_page_papers(items: object, fallback_day: dt.date, limit: int) -> List[digest.Paper]:
    if not isinstance(items, list):
        return []
    papers: List[digest.Paper] = []
    for idx, item in enumerate(items[:limit]):
        if not isinstance(item, dict):
            continue
        paper = make_paper(
            title=str(item.get("title", "")),
            url=str(item.get("link_abs", item.get("url", ""))),
            snippet=str(item.get("summary_en", item.get("snippet", ""))),
            publication_info=str(item.get("publication_info", item.get("journal_ref", ""))),
            pdf_url=str(item.get("link_pdf", item.get("pdf_url", ""))),
            fallback_day=fallback_day,
            index=idx,
            query=str(item.get("query", "")),
        )
        if item.get("published"):
            paper.published = str(item.get("published", ""))
            paper.updated = str(item.get("updated", paper.published))
        if item.get("source_date_precision"):
            paper.source_date_precision = str(item.get("source_date_precision", ""))
        if item.get("source_year"):
            paper.source_year = str(item.get("source_year", ""))
        if item.get("source_venue"):
            paper.source_venue = str(item.get("source_venue", ""))
        if item.get("accepted_venue"):
            paper.accepted_venue = str(item.get("accepted_venue", ""))
        if item.get("full_text_status"):
            paper.full_text_status = str(item.get("full_text_status", ""))
        if item.get("full_text_url"):
            paper.full_text_url = str(item.get("full_text_url", ""))
        if item.get("comment"):
            paper.comment = str(item.get("comment", ""))
        papers.append(refresh_scholar_paper(paper))
    return papers


def set_query_state_page(entry: Dict[str, object], start: int, papers: List[digest.Paper], query: str) -> None:
    pages = entry.get("pages", {})
    if not isinstance(pages, dict):
        pages = {}
        entry["pages"] = pages
    pages[str(max(0, int(start)))] = {
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "result_count": len(papers),
        "papers": [serialize_query_page_paper(paper, query) for paper in papers],
    }


def get_cached_query_page(entry: Dict[str, object], start: int, fallback_day: dt.date, limit: int) -> List[digest.Paper]:
    pages = entry.get("pages", {})
    if not isinstance(pages, dict):
        return []
    page = pages.get(str(max(0, int(start))))
    if not isinstance(page, dict):
        return []
    return deserialize_query_page_papers(page.get("papers"), fallback_day, limit)


def parse_iso_datetime(text: str) -> Optional[dt.datetime]:
    clean = digest.safe_text(text)
    if not clean:
        return None
    try:
        return dt.datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except Exception:
        return None


def filter_seen_papers(papers: List[digest.Paper], state: Dict[str, object], ignore_seen: bool) -> Tuple[List[digest.Paper], Dict[str, int]]:
    if not ignore_seen:
        return papers, {"kept": len(papers), "ignored_seen": 0}
    key_map = seen_key_map(state)
    kept: List[digest.Paper] = []
    ignored = 0
    for paper in papers:
        keys = scholar_identity_candidates(paper)
        if any(key in key_map for key in keys):
            ignored += 1
            continue
        kept.append(paper)
    return kept, {"kept": len(kept), "ignored_seen": ignored}


def merge_papers(base: digest.Paper, incoming: digest.Paper) -> digest.Paper:
    if incoming.summary_en and len(incoming.summary_en) > len(base.summary_en):
        base.summary_en = incoming.summary_en
    if incoming.summary_zh and len(incoming.summary_zh) > len(base.summary_zh):
        base.summary_zh = incoming.summary_zh
    if incoming.title_zh and not base.title_zh:
        base.title_zh = incoming.title_zh
    if incoming.link_pdf and (not base.link_pdf or base.link_pdf == base.link_abs):
        base.link_pdf = incoming.link_pdf
    if incoming.full_text_url and not base.full_text_url:
        base.full_text_url = incoming.full_text_url
    if incoming.full_text_status and incoming.full_text_status not in base.full_text_status:
        base.full_text_status = digest.safe_text("; ".join([x for x in [base.full_text_status, incoming.full_text_status] if x]))
    if incoming.source_venue and not base.source_venue:
        base.source_venue = incoming.source_venue
    if incoming.accepted_venue and not base.accepted_venue:
        base.accepted_venue = incoming.accepted_venue
    if incoming.source_year and not base.source_year:
        base.source_year = incoming.source_year
    if incoming.source_date_precision and not base.source_date_precision:
        base.source_date_precision = incoming.source_date_precision
    if incoming.comment and incoming.comment not in base.comment:
        base.comment = digest.safe_text(f"{base.comment}; {incoming.comment}")
    base.focus_tags = sorted(set(base.focus_tags + incoming.focus_tags))
    base.domain_tags = sorted(set(base.domain_tags + incoming.domain_tags))
    base.task_tags = sorted(set(base.task_tags + incoming.task_tags))
    base.type_tags = sorted(set(base.type_tags + incoming.type_tags))
    base.keywords = sorted(set(base.keywords + incoming.keywords))
    return refresh_scholar_paper(base)


def dedupe_by_identity(papers: List[digest.Paper]) -> Tuple[List[digest.Paper], Dict[str, int]]:
    merged: Dict[str, digest.Paper] = {}
    aliases: Dict[str, str] = {}
    merged_count = 0
    for paper in papers:
        keys = scholar_identity_candidates(paper)
        primary = ""
        for key in keys:
            if key in aliases:
                primary = aliases[key]
                break
            if key in merged:
                primary = key
                break
        if not primary:
            primary = keys[0]
            merged[primary] = paper
        else:
            merged[primary] = merge_papers(merged[primary], paper)
            merged_count += 1
        for key in keys:
            aliases[key] = primary
    out = list(merged.values())
    out.sort(key=lambda paper: (paper_year(paper), paper.title), reverse=True)
    return out, {"kept": len(out), "merged_duplicates": merged_count}


def update_seen_state(
    state: Dict[str, object],
    papers: List[digest.Paper],
    target_day: dt.date,
) -> Dict[str, object]:
    key_map = seen_key_map(state)
    if not isinstance(key_map, dict):
        key_map = {}
        state["keys"] = key_map
    record_map = seen_record_map(state)
    if not isinstance(record_map, dict):
        record_map = {}
        state["records"] = record_map
    stamp = target_day.isoformat()
    for paper in papers:
        identity_keys = scholar_identity_candidates(paper)
        primary = identity_keys[0]
        record_map[primary] = {
            "title": paper.title,
            "url": paper.link_abs or paper.link_pdf,
            "year": paper_year(paper),
            "added_on": stamp,
        }
        for key in identity_keys:
            key_map[key] = stamp
    state["version"] = 2
    state["scope"] = "google_scholar_dedupe_keys"
    state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    if not state.get("year"):
        state["year"] = target_day.year
    return state


def filter_by_date_window(
    papers: List[digest.Paper],
    target_day: dt.date,
    days: int,
    mode: str,
    trust_query_window: bool = False,
) -> Tuple[List[digest.Paper], Dict[str, int]]:
    if mode == "off":
        return papers, {
            "kept": len(papers),
            "kept_precise": len(papers),
            "kept_query_window": 0,
            "excluded": 0,
            "unknown_date": 0,
            "outside_window": 0,
            "coarse_date_mismatch": 0,
        }
    start_day, end_day = date_window(target_day, days)
    kept: List[digest.Paper] = []
    stats = {
        "kept": 0,
        "kept_precise": 0,
        "kept_query_window": 0,
        "excluded": 0,
        "unknown_date": 0,
        "outside_window": 0,
        "coarse_date_mismatch": 0,
    }
    for paper in papers:
        precision = digest.safe_text(paper.source_date_precision)
        try:
            paper_day = dt.datetime.fromisoformat(paper.published.replace("Z", "+00:00")).date()
        except Exception:
            paper_day = None
        if precision == "day" and paper_day and start_day <= paper_day <= end_day:
            kept.append(paper)
            stats["kept"] += 1
            stats["kept_precise"] += 1
            continue
        if precision == "day":
            stats["excluded"] += 1
            stats["outside_window"] += 1
            continue
        if mode == "query" and trust_query_window:
            if precision == "year" and paper_day and paper_day.year != target_day.year:
                stats["excluded"] += 1
                stats["coarse_date_mismatch"] += 1
                continue
            if precision == "month" and paper_day and (paper_day.year, paper_day.month) not in {
                (start_day.year, start_day.month),
                (end_day.year, end_day.month),
            }:
                stats["excluded"] += 1
                stats["coarse_date_mismatch"] += 1
                continue
            paper.source_date_precision = precision or "scholar_query_window"
            if "Scholar date-query window" not in paper.comment:
                paper.comment = digest.safe_text(
                    f"{paper.comment}; Scholar date-query window: {start_day.isoformat()}..{end_day.isoformat()}"
                )
            kept.append(paper)
            stats["kept"] += 1
            stats["kept_query_window"] += 1
            continue
        if mode == "relaxed" and paper_day and paper_day.year == target_day.year and precision in {"year", "month"}:
            kept.append(paper)
            stats["kept"] += 1
            continue
        stats["excluded"] += 1
        if not precision or precision in {"year", "month"}:
            stats["unknown_date"] += 1
        else:
            stats["outside_window"] += 1
    return kept, stats


def has_complete_abstract(paper: digest.Paper) -> bool:
    summary = digest.safe_text(paper.summary_en)
    if not summary or len(summary) < 80:
        return False
    if summary.startswith("Detail page did not expose"):
        return False
    if is_scholar_snippet(summary):
        return False
    return True


def filter_by_abstract_quality(papers: List[digest.Paper], required: bool) -> Tuple[List[digest.Paper], Dict[str, int]]:
    if not required:
        return papers, {"kept": len(papers), "excluded": 0}
    kept: List[digest.Paper] = []
    excluded = 0
    for paper in papers:
        if has_complete_abstract(paper):
            kept.append(paper)
        else:
            excluded += 1
    return kept, {"kept": len(kept), "excluded": excluded}


def load_manual_results(path: str, fallback_day: dt.date, limit: int) -> List[digest.Paper]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        items = data.get("organic_results") or data.get("papers") or data.get("new_papers") or data
    else:
        items = data
    if not isinstance(items, list):
        raise ValueError("manual JSON must be a list or contain organic_results")
    papers: List[digest.Paper] = []
    for idx, item in enumerate(items[:limit]):
        if not isinstance(item, dict):
            continue
        paper = make_paper(
            title=str(item.get("title", "")),
            url=str(item.get("link", item.get("url", item.get("link_abs", "")))),
            snippet=str(item.get("snippet", item.get("summary", item.get("summary_en", "")))),
            publication_info=str(item.get("publication_info", item.get("venue", item.get("journal_ref", "")))),
            pdf_url=str(item.get("pdf_url", item.get("pdf", item.get("link_pdf", "")))),
            fallback_day=fallback_day,
            index=idx,
            query=str(item.get("query", "manual")),
        )
        if item.get("published"):
            paper.published = str(item.get("published", ""))
            paper.updated = str(item.get("updated", paper.published))
            precision = str(item.get("source_date_precision", ""))
            if not precision:
                year = parse_year(str(item.get("publication_info", item.get("journal_ref", ""))))
                date_part = paper.published[:10]
                if year and date_part == f"{year}-12-31":
                    precision = "year"
                elif re.match(r"\d{4}-\d{2}-\d{2}T", paper.published):
                    precision = "day"
            paper.source_date_precision = precision
        if item.get("source_venue"):
            paper.source_venue = parse_source_venue(str(item.get("journal_ref", "")) or str(item.get("source_venue", "")))
            paper.accepted_venue = paper.source_venue
        if item.get("full_text_status"):
            paper.full_text_status = str(item.get("full_text_status", ""))
        if item.get("full_text_url"):
            paper.full_text_url = str(item.get("full_text_url", ""))
        if item.get("title_zh"):
            paper.title_zh = str(item.get("title_zh", ""))
        if item.get("summary_zh"):
            paper.summary_zh = str(item.get("summary_zh", ""))
        papers.append(refresh_scholar_paper(paper))
    return papers


def build_queries(mode: str, focus_terms: List[str], custom_queries: str) -> List[str]:
    custom = [digest.safe_text(x) for x in custom_queries.split(";;") if digest.safe_text(x)]
    if custom:
        return custom
    if mode == "all-cv":
        return [
            "computer vision",
            "\"computer vision\" \"deep learning\"",
            "\"image segmentation\" OR \"object detection\" OR tracking",
        ]
    if focus_terms:
        queries: List[str] = []
        seen: set[str] = set()
        for term in focus_terms:
            query = digest.normalize_focus_term(term)
            query = digest.safe_text(query)
            if query and query not in seen:
                seen.add(query)
                queries.append(query)
        if queries:
            return queries
    return DEFAULT_SCHOLAR_QUERIES[:]


def fetch_query_page(
    *,
    query: str,
    start: int,
    limit: int,
    target_day: dt.date,
    target_year: int,
    args: argparse.Namespace,
    entry: Dict[str, object],
    runtime_state: Dict[str, object],
    prefer_browser_session: bool,
) -> Tuple[List[digest.Paper], bool, bool]:
    cached = get_cached_query_page(entry, start, target_day, limit)
    blocked_at = parse_iso_datetime(str(entry.get("last_blocked_at", "")))
    cooldown_minutes = max(0, int(args.blocked_cooldown_minutes))
    if blocked_at and cooldown_minutes > 0:
        delta = dt.datetime.now(blocked_at.tzinfo or dt.timezone.utc) - blocked_at.replace(tzinfo=blocked_at.tzinfo or dt.timezone.utc)
        if delta < dt.timedelta(minutes=cooldown_minutes):
            if cached:
                print(
                    f"[WARN] Scholar query is in cooldown after a recent block, use cached page: "
                    f"{query} (start={start}) -> {len(cached)}"
                )
                return cached, prefer_browser_session, True
            raise ScholarBlockedError(
                f"Recent Google Scholar block is still cooling down for query '{query}' (last blocked at {entry.get('last_blocked_at', '')})."
            )
    query_url = scholar_query_url(
        query,
        start=start,
        num=limit,
        year_from=target_year if args.year_filter != "off" else 0,
        sort_by_date=bool(args.sort_by_date),
        hl=args.scholar_hl,
    )
    used_cache = False
    try:
        if prefer_browser_session:
            print(f"[INFO] 直接使用 Chrome 会话抓取 Scholar: {query} (start={start})")
            html_text = fetch_scholar_html_via_chrome(
                query_url,
                wait_timeout=args.browser_wait_timeout,
                poll_seconds=args.browser_poll_seconds,
            )
        else:
            try:
                html_text = fetch_scholar_html(
                    query,
                    start=start,
                    timeout=args.timeout,
                    num=limit,
                    year_from=target_year if args.year_filter != "off" else 0,
                    sort_by_date=bool(args.sort_by_date),
                    hl=args.scholar_hl,
                    cookie_path=args.scholar_cookie_path,
                )
            except Exception as exc:
                if args.browser_fallback and chrome_available():
                    print(f"[INFO] HTTP Scholar 请求失败，切换到 Chrome 会话抓取: {query} ({exc})")
                    html_text = fetch_scholar_html_via_chrome(
                        query_url,
                        wait_timeout=args.browser_wait_timeout,
                        poll_seconds=args.browser_poll_seconds,
                    )
                    runtime_state["transport_mode"] = "browser_session"
                    runtime_state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
                    prefer_browser_session = True
                else:
                    raise
        chunk = parse_scholar_html(html_text, query, target_day, limit)
        set_query_state_page(entry, start, chunk, query)
        entry["last_success_at"] = dt.datetime.now().isoformat(timespec="seconds")
        entry["last_error"] = ""
        return chunk, prefer_browser_session, used_cache
    except Exception as exc:
        error_text = str(exc)
        entry["last_error"] = error_text
        if isinstance(exc, ScholarBlockedError) or is_rate_limit_error_text(error_text):
            entry["last_blocked_at"] = dt.datetime.now().isoformat(timespec="seconds")
            cached = get_cached_query_page(entry, start, target_day, limit)
            if cached:
                used_cache = True
                print(f"[WARN] Scholar query blocked, fallback to cached page: {query} (start={start}) -> {len(cached)}")
                return cached, prefer_browser_session, used_cache
        raise


def translate(papers: List[digest.Paper], args: argparse.Namespace) -> None:
    cache_path = os.path.join(args.data_dir, "google_translation_cache.json")
    cache = digest.load_llm_cache(cache_path)
    digest.apply_translation_cache(papers, [cache])
    digest.google_enrich_title_and_summary(
        papers,
        cache=cache,
        cache_path=cache_path,
        limit=args.google_limit,
        timeout=args.google_timeout,
        full_abstract=bool(args.google_full_abstract),
        summary_sentences=args.google_summary_sentences,
    )
    for paper in papers:
        if digest.missing_translation(paper):
            paper.title_zh = digest.fallback_title_zh(paper.title)
            paper.summary_zh = digest.fallback_summary_zh(paper.summary_en)


def render_markdown_quick(target_day: dt.date, tz_name: str, groups: Dict[str, List[digest.Paper]]) -> str:
    total = sum(len(v) for v in groups.values())
    lines = [
        f"# Google Scholar CV Focus Digest ({target_day.isoformat()}, {tz_name})",
        "",
        f"- 总数: {total}",
    ]
    for key, rows in groups.items():
        lines.append(f"- {key}: {len(rows)}")
    for key, rows in groups.items():
        lines.append("")
        lines.append(f"## {key}")
        for paper in rows:
            lines.append(f"- [{paper.arxiv_id}]({paper.link_abs}) {paper.title_zh} | {paper.summary_zh}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Google Scholar CV focus digest.")
    parser.add_argument("--date", type=str, default="")
    parser.add_argument("--tz", type=str, default=os.environ.get("DIGEST_TZ", "Asia/Shanghai"))
    parser.add_argument("--query-mode", choices=["focus", "all-cv"], default=os.environ.get("SCHOLAR_QUERY_MODE", "focus"))
    parser.add_argument("--queries", type=str, default=os.environ.get("SCHOLAR_QUERIES", ""), help="Separate custom queries with ';;'.")
    parser.add_argument("--focus-terms", type=str, default=os.environ.get("FOCUS_TERMS_OVERRIDE", ""))
    parser.add_argument("--focus-terms-extra", type=str, default=os.environ.get("FOCUS_TERMS_EXTRA", ""))
    parser.add_argument("--source", choices=["auto", "serpapi", "html", "manual", "saved-html", "alerts"], default=os.environ.get("SCHOLAR_SOURCE", "auto"))
    parser.add_argument("--input-json", type=str, default="")
    parser.add_argument("--input-html-glob", type=str, default=os.environ.get("SCHOLAR_INPUT_HTML_GLOB", ""))
    parser.add_argument("--input-alert-glob", type=str, default=os.environ.get("SCHOLAR_INPUT_ALERT_GLOB", ""))
    parser.add_argument("--input-alert-mbox", type=str, default=os.environ.get("SCHOLAR_INPUT_ALERT_MBOX", ""))
    parser.add_argument("--max-results", type=int, default=int(os.environ.get("SCHOLAR_MAX_RESULTS", "80")))
    parser.add_argument("--per-query", type=int, default=int(os.environ.get("SCHOLAR_PER_QUERY", "20")))
    parser.add_argument("--scholar-hl", type=str, default=os.environ.get("SCHOLAR_HL", "zh-CN"))
    parser.add_argument("--scholar-cookie-path", type=str, default=os.environ.get("SCHOLAR_COOKIE_PATH", "data/google_scholar_cookies.txt"))
    parser.add_argument("--browser-fallback", type=int, choices=[0, 1], default=int(os.environ.get("SCHOLAR_BROWSER_FALLBACK", "0")))
    parser.add_argument("--browser-wait-timeout", type=int, default=int(os.environ.get("SCHOLAR_BROWSER_WAIT_TIMEOUT", "120")))
    parser.add_argument("--browser-poll-seconds", type=float, default=float(os.environ.get("SCHOLAR_BROWSER_POLL_SECONDS", "2.0")))
    parser.add_argument("--runtime-state-path", type=str, default=os.environ.get("SCHOLAR_RUNTIME_STATE_PATH", ""))
    parser.add_argument("--query-state-path", type=str, default=os.environ.get("SCHOLAR_QUERY_STATE_PATH", ""))
    parser.add_argument("--query-expand-pages", type=int, default=int(os.environ.get("SCHOLAR_QUERY_EXPAND_PAGES", "1")))
    parser.add_argument("--blocked-cooldown-minutes", type=int, default=int(os.environ.get("SCHOLAR_BLOCKED_COOLDOWN_MINUTES", "180")))
    parser.add_argument("--year-from", type=int, default=int(os.environ.get("SCHOLAR_YEAR_FROM", "0")), help="Default: target year.")
    parser.add_argument("--year-filter", choices=["current", "off"], default=os.environ.get("SCHOLAR_YEAR_FILTER", "current"))
    parser.add_argument("--sort-by-date", type=int, choices=[0, 1], default=int(os.environ.get("SCHOLAR_SORT_BY_DATE", "0")))
    parser.add_argument("--query-sleep-seconds", type=float, default=float(os.environ.get("SCHOLAR_QUERY_SLEEP_SECONDS", "4.5")))
    parser.add_argument("--detail-enrich-limit", type=int, default=int(os.environ.get("SCHOLAR_DETAIL_ENRICH_LIMIT", "-1")))
    parser.add_argument("--detail-timeout", type=int, default=int(os.environ.get("SCHOLAR_DETAIL_TIMEOUT_SECONDS", "18")))
    parser.add_argument("--venue-watch-limit", type=int, default=int(os.environ.get("SCHOLAR_VENUE_WATCH_LIMIT", "120")))
    parser.add_argument("--require-full-abstract", type=int, choices=[0, 1], default=int(os.environ.get("SCHOLAR_REQUIRE_FULL_ABSTRACT", "1")))
    parser.add_argument("--ignore-fetched", type=int, choices=[0, 1], default=int(os.environ.get("SCHOLAR_IGNORE_FETCHED", "1")))
    parser.add_argument("--seen-state-path", type=str, default=os.environ.get("SCHOLAR_SEEN_STATE_PATH", ""))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("SCHOLAR_TIMEOUT_SECONDS", "15")))
    parser.add_argument("--google-timeout", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_TIMEOUT_SECONDS", "12")))
    parser.add_argument("--google-limit", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_LIMIT", "-1")))
    parser.add_argument("--google-summary-sentences", type=int, default=int(os.environ.get("GOOGLE_SUMMARY_SENTENCES", "3")))
    parser.add_argument("--google-full-abstract", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_FULL_ABSTRACT", "1")))
    parser.add_argument("--output-suffix", type=str, default=os.environ.get("SCHOLAR_REPORT_SUFFIX", "scholar"))
    parser.add_argument("--report-dir", type=str, default="reports")
    parser.add_argument("--data-dir", type=str, default="data")
    args = parser.parse_args()

    tz = ZoneInfo(args.tz)
    target_day = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(tz).date()
    target_year = args.year_from if args.year_from > 0 else target_day.year
    os.makedirs(args.report_dir, exist_ok=True)
    os.makedirs(args.data_dir, exist_ok=True)

    focus_terms = digest.configure_focus_terms("cv", args.focus_terms, args.focus_terms_extra)
    digest.ACTIVE_FOCUS_TERMS = focus_terms
    digest.ACTIVE_FOCUS_MATCHERS = digest.configure_focus_matchers(focus_terms)
    digest.ACTIVE_TRANSLATION_CACHE_SALT = "google-scholar"

    if args.query_mode == "all-cv":
        print("[WARN] Google Scholar has no official CV-wide feed/API; all-cv mode is a best-effort broad query set, not complete coverage.")

    papers: List[digest.Paper] = []
    query_count = 0
    successful_query_count = 0
    blocked_query_count = 0
    prefilter_seen_ignored = 0
    seen_state_path = args.seen_state_path or os.path.join(args.data_dir, "google_scholar_seen_state.json")
    seen_state = load_seen_state(seen_state_path)
    seen_before = len(seen_key_map(seen_state))
    runtime_state_path = args.runtime_state_path or os.path.join(args.data_dir, "google_scholar_runtime_state.json")
    runtime_state = load_runtime_state(runtime_state_path)
    query_state_path = args.query_state_path or os.path.join(args.data_dir, "google_scholar_query_state.json")
    query_state = load_query_state(query_state_path)
    prefer_browser_session = bool(args.browser_fallback) and chrome_available() and runtime_state.get("transport_mode") == "browser_session"
    detail_cache_path = os.path.join(args.data_dir, "google_scholar_detail_cache.json")
    detail_cache = load_detail_cache(detail_cache_path)
    if args.source == "manual" or args.input_json:
        if not args.input_json:
            print("[ERROR] --source manual requires --input-json", file=sys.stderr)
            return 2
        papers = load_manual_results(args.input_json, target_day, args.max_results)
    elif args.source == "saved-html" or args.input_html_glob:
        if not args.input_html_glob:
            print("[ERROR] --source saved-html requires --input-html-glob", file=sys.stderr)
            return 2
        papers = load_saved_html_results(args.input_html_glob, target_day, args.max_results)
    elif args.source == "alerts" or args.input_alert_glob or args.input_alert_mbox:
        if not args.input_alert_glob and not args.input_alert_mbox:
            print("[ERROR] --source alerts requires --input-alert-glob or --input-alert-mbox", file=sys.stderr)
            return 2
        papers = load_alert_results(args.input_alert_glob, args.input_alert_mbox, target_day, args.max_results)
    else:
        queries = build_queries(args.query_mode, focus_terms, args.queries)
        serpapi_key = os.environ.get("SERPAPI_API_KEY", "").strip()
        stop_due_to_rate_limit = False
        for query in queries:
            if stop_due_to_rate_limit:
                break
            query_count += 1
            remaining = max(0, args.max_results - len(papers))
            if remaining <= 0:
                break
            limit = min(args.per_query, remaining)
            entry = get_query_state_entry(query_state, query, target_year if args.year_filter != "off" else 0, args.scholar_hl)
            try:
                if args.source == "serpapi" or (args.source == "auto" and serpapi_key):
                    chunk = fetch_from_serpapi(
                        query,
                        serpapi_key,
                        limit,
                        target_day,
                        args.timeout,
                        year_from=target_year if args.year_filter != "off" else 0,
                        sort_by_date=bool(args.sort_by_date),
                    )
                    print(f"[INFO] Scholar query fetched candidates: {query} start=0 -> {len(chunk)} (serpapi)")
                else:
                    head_chunk, prefer_browser_session, used_cache = fetch_query_page(
                        query=query,
                        start=0,
                        limit=limit,
                        target_day=target_day,
                        target_year=target_year,
                        args=args,
                        entry=entry,
                        runtime_state=runtime_state,
                        prefer_browser_session=prefer_browser_session,
                    )
                    head_new, head_seen_stats = filter_seen_papers(head_chunk, seen_state, bool(args.ignore_fetched))
                    prefilter_seen_ignored += head_seen_stats["ignored_seen"]
                    print(
                        f"[INFO] Scholar query page: {query} start=0 -> "
                        f"candidates={len(head_chunk)}, new={len(head_new)}, ignored_seen={head_seen_stats['ignored_seen']}"
                        + (" (cached)" if used_cache else "")
                    )
                    chunk = head_new
                    if not chunk and bool(args.ignore_fetched):
                        next_start = int(entry.get("next_start", 0) or 0)
                        if next_start <= 0:
                            next_start = max(1, args.per_query)
                        expansion_steps = max(0, int(args.query_expand_pages))
                        for _ in range(expansion_steps):
                            expand_limit = min(args.per_query, max(0, args.max_results - len(papers) - len(chunk)))
                            if expand_limit <= 0:
                                break
                            expand_chunk, prefer_browser_session, used_expand_cache = fetch_query_page(
                                query=query,
                                start=next_start,
                                limit=expand_limit,
                                target_day=target_day,
                                target_year=target_year,
                                args=args,
                                entry=entry,
                                runtime_state=runtime_state,
                                prefer_browser_session=prefer_browser_session,
                            )
                            expand_new, expand_seen_stats = filter_seen_papers(expand_chunk, seen_state, True)
                            prefilter_seen_ignored += expand_seen_stats["ignored_seen"]
                            print(
                                f"[INFO] Scholar query expansion: {query} start={next_start} -> "
                                f"candidates={len(expand_chunk)}, new={len(expand_new)}, ignored_seen={expand_seen_stats['ignored_seen']}"
                                + (" (cached)" if used_expand_cache else "")
                            )
                            if expand_chunk and not expand_new:
                                next_start += max(1, args.per_query)
                                entry["next_start"] = next_start
                                continue
                            entry["next_start"] = next_start
                            chunk.extend(expand_new)
                            break
                successful_query_count += 1
                papers.extend(chunk)
                query_state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
                time.sleep(max(0.0, args.query_sleep_seconds) + random.uniform(0.2, 1.1))
            except Exception as exc:
                error_text = str(exc)
                if isinstance(exc, ScholarBlockedError) or is_rate_limit_error_text(error_text):
                    blocked_query_count += 1
                    stop_due_to_rate_limit = True
                    runtime_state["transport_mode"] = "browser_session" if bool(args.browser_fallback) else "http_blocked"
                    runtime_state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
                    entry["last_blocked_at"] = runtime_state["updated_at"]
                    entry["last_error"] = error_text
                print(f"[WARN] Scholar query failed: {query}: {exc}")

    papers = digest.dedupe_papers(papers)[:args.max_results]
    candidate_count = len(papers)
    is_live_source = not (
        args.source == "manual"
        or bool(args.input_json)
        or args.source == "saved-html"
        or bool(args.input_html_glob)
        or args.source == "alerts"
        or bool(args.input_alert_glob)
        or bool(args.input_alert_mbox)
    )
    if is_live_source and query_count > 0 and candidate_count == 0:
        if blocked_query_count > 0:
            if runtime_state:
                save_runtime_state(runtime_state_path, runtime_state)
            if query_state:
                save_query_state(query_state_path, query_state)
            print("[ERROR] Google Scholar returned 429 / CAPTCHA on this network/IP; unattended shell-only scraping cannot pass reCAPTCHA automatically.", file=sys.stderr)
            print("[ERROR] Use an authorized API source for unattended runs, for example: --source serpapi with SERPAPI_API_KEY.", file=sys.stderr)
            print("[WARN] Previous Scholar report was kept unchanged.")
            return 3
        if successful_query_count == 0:
            if runtime_state:
                save_runtime_state(runtime_state_path, runtime_state)
            if query_state:
                save_query_state(query_state_path, query_state)
            print("[WARN] All Google Scholar queries failed; keep the previous Scholar report unchanged.")
            return 0
    detail_stats = {"attempted": 0, "abstracts": 0, "dates": 0, "failed": 0, "cached": 0}
    detail_stats = enrich_from_detail_pages(
        papers,
        cache=detail_cache,
        cache_path=detail_cache_path,
        limit=args.detail_enrich_limit,
        timeout=args.detail_timeout,
    )
    papers, dedupe_stats = dedupe_by_identity(papers)
    if args.query_mode == "focus":
        papers = [paper for paper in papers if paper.focus_tags]
    papers, year_stats = filter_by_year(papers, target_year, args.year_filter)
    papers, abstract_stats = filter_by_abstract_quality(papers, bool(args.require_full_abstract))
    papers, seen_stats = filter_seen_papers(papers, seen_state, bool(args.ignore_fetched))
    if bool(args.ignore_fetched) and prefilter_seen_ignored:
        seen_stats["ignored_seen"] = int(seen_stats.get("ignored_seen", 0)) + int(prefilter_seen_ignored)
        seen_stats["kept"] = len(papers)
    print(
        "[INFO] Scholar year scope: "
        f"query_as_ylo={target_year}, candidates={candidate_count}, "
        f"parsed_before_{target_year}={year_stats['before_year']}, parsed_unknown_year={year_stats['unknown_year_kept']}"
    )
    if args.require_full_abstract:
        print(
            "[INFO] Scholar abstract filter: "
            f"kept={abstract_stats['kept']}, excluded_without_full_abstract={abstract_stats['excluded']}"
        )
    if dedupe_stats["merged_duplicates"]:
        print(
            "[INFO] Scholar identity dedupe: "
            f"kept_unique={dedupe_stats['kept']}, merged_duplicates={dedupe_stats['merged_duplicates']}"
        )
    if args.ignore_fetched:
        print(
            "[INFO] Scholar seen-state filter: "
            f"kept_new={seen_stats['kept']}, ignored_seen={seen_stats['ignored_seen']}, state_seen_before={seen_before}"
        )
    print(
        "[INFO] Scholar detail enrichment: "
        f"attempted={detail_stats['attempted']}, cached={detail_stats['cached']}, "
        f"abstracts={detail_stats['abstracts']}, day_dates={detail_stats['dates']}, failed={detail_stats['failed']}"
    )
    if bool(args.ignore_fetched) and not papers and seen_stats["ignored_seen"] > 0:
        if runtime_state:
            save_runtime_state(runtime_state_path, runtime_state)
        if query_state:
            save_query_state(query_state_path, query_state)
        print("[INFO] No new Google Scholar papers after seen-state filtering; keep the previous Scholar report unchanged.")
        return 0
    summary_note = f"本分支直接使用 Google Scholar 的 as_ylo={target_year} 年份筛选链接抓取候选，再进入详情页提取完整摘要；本地去重表只用于避免重复抓取相同论文。"
    translate(papers, args)
    groups = digest.split_by_major_area(papers)
    focus_latest = digest.derive_focus_from_papers(papers, len(papers))
    venue_watch = [p for p in papers if digest.accepted_rank(p) > 0][:args.venue_watch_limit]
    html_text = digest.render_html_report(
        "Google Scholar CV Focus Digest",
        target_day,
        args.tz,
        groups,
        focus_latest,
        [],
        [],
        venue_watch,
        report_meta={
            "fetch_summary": {"enabled": False},
            "source_summary": {
                "note": summary_note,
                "cards": [
                    {"value": str(candidate_count), "label": "Scholar候选"},
                    {"value": str(year_stats["kept"]), "label": f"{target_year}年以来"},
                    {"value": str(abstract_stats["kept"]), "label": "完整摘要候选"},
                    {"value": str(seen_stats["ignored_seen"]), "label": "已见忽略"},
                    {"value": str(len(papers)), "label": "本次新增"},
                    {"value": str(abstract_stats["excluded"]), "label": "无完整摘要排除"},
                ],
            },
        },
    )
    md_text = render_markdown_quick(target_day, args.tz, groups)
    suffix = digest.normalize_output_suffix(args.output_suffix) or "scholar"
    html_path = os.path.join(args.report_dir, f"google_scholar_digest_{target_day.isoformat()}_{suffix}.html")
    md_path = os.path.join(args.report_dir, f"google_scholar_digest_{target_day.isoformat()}_{suffix}.md")
    json_path = os.path.join(args.data_dir, f"google_scholar_digest_{target_day.isoformat()}_{suffix}.json")
    payload = {
        "date": target_day.isoformat(),
        "timezone": args.tz,
        "source": "Google Scholar",
        "query_mode": args.query_mode,
        "coverage_note": "Google Scholar does not provide an official complete CV feed; focus mode is the reliable default. all-cv mode is best-effort only.",
        "focus_terms": focus_terms,
        "blocked_query_count": blocked_query_count,
        "year_from": target_year,
        "year_filter": args.year_filter,
        "candidate_count_before_year_filter": candidate_count,
        "identity_dedupe_stats": dedupe_stats,
        "year_filter_stats": year_stats,
        "abstract_filter_stats": abstract_stats,
        "seen_filter_stats": seen_stats,
        "seen_state_path": seen_state_path,
        "seen_state_count_before": seen_before,
        "detail_enrichment_stats": detail_stats,
        "detail_cache_path": detail_cache_path,
        "query_state_path": query_state_path,
        "query_prefilter_seen_ignored": prefilter_seen_ignored,
        "paper_count": len(papers),
        "papers": [asdict(p) for p in papers],
        "daily_groups": {k: [asdict(p) for p in v] for k, v in groups.items()},
        "focus_latest_count": len(focus_latest),
        "focus_latest": [asdict(p) for p in focus_latest],
        "venue_watch_count": len(venue_watch),
        "venue_watch": [asdict(p) for p in venue_watch],
        "notes": {"html_path": html_path, "markdown_path": md_path, "json_path": json_path},
    }
    should_update_seen_state = bool(args.ignore_fetched) or args.source == "manual" or bool(args.input_json)
    digest.write_text(html_path, html_text)
    digest.write_text(md_path, md_text)
    digest.dump_json(json_path, payload)
    if runtime_state:
        save_runtime_state(runtime_state_path, runtime_state)
    if query_state:
        save_query_state(query_state_path, query_state)
    if should_update_seen_state and papers:
        update_seen_state(seen_state, papers, target_day)
        save_seen_state(seen_state_path, seen_state)
        payload["seen_state_count_after"] = len(seen_key_map(seen_state))
        digest.dump_json(json_path, payload)
    print(f"[OK] Google Scholar papers: {len(papers)}")
    if should_update_seen_state:
        print(f"[OK] Scholar seen state: {seen_state_path}")
    if runtime_state:
        print(f"[OK] Scholar runtime state: {runtime_state_path}")
    if query_state:
        print(f"[OK] Scholar query state: {query_state_path}")
    print(f"[OK] HTML report: {html_path}")
    print(f"[OK] Markdown: {md_path}")
    print(f"[OK] JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
