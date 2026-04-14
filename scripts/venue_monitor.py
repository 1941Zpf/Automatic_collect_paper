#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import arxiv_daily_digest as digest


VENUE_NAMES = ["CVPR", "ICCV", "ECCV", "NeurIPS", "ICLR", "TPAMI", "IJCV", "ICML", "AAAI", "ACM MM"]


DEFAULT_SOURCES = [
    {"venue": "CVPR", "kind": "cvf", "url": "https://openaccess.thecvf.com/CVPR2026?day=all"},
    {"venue": "CVPR", "kind": "cvf", "url": "https://openaccess.thecvf.com/CVPR2025?day=all"},
    {"venue": "ICCV", "kind": "cvf", "url": "https://openaccess.thecvf.com/ICCV2025?day=all"},
    {"venue": "ECCV", "kind": "cvf", "url": "https://openaccess.thecvf.com/ECCV2024?day=all"},
    {"venue": "NeurIPS", "kind": "generic", "url": "https://papers.nips.cc/"},
    {"venue": "ICLR", "kind": "generic", "url": "https://openreview.net/group?id=ICLR.cc/2026/Conference"},
    {"venue": "TPAMI", "kind": "generic", "url": "https://www.computer.org/csdl/journal/tp"},
    {"venue": "IJCV", "kind": "generic", "url": "https://link.springer.com/journal/11263/online-first"},
    {"venue": "ICML", "kind": "pmlr", "url": "https://proceedings.mlr.press/"},
    {"venue": "AAAI", "kind": "generic", "url": "https://ojs.aaai.org/index.php/AAAI/issue/archive"},
    {"venue": "ACM MM", "kind": "generic", "url": "https://dl.acm.org/conference/mm/proceedings"},
]


def h(text: object) -> str:
    return html.escape(digest.safe_text(str(text or "")), quote=True)


def fingerprint(*parts: str) -> str:
    raw = "\n".join(digest.safe_text(part) for part in parts if part)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def strip_tags(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    return digest.safe_text(html.unescape(re.sub(r"<[^>]+>", " ", text)))


def absolute_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, html.unescape(href or ""))


def source_date(target_day: dt.date, idx: int) -> str:
    stamp = dt.datetime.combine(target_day, dt.time(12, 0), tzinfo=dt.timezone.utc) - dt.timedelta(seconds=idx)
    return stamp.isoformat().replace("+00:00", "Z")


def make_paper(venue: str, title: str, url: str, summary: str, target_day: dt.date, idx: int, source_url: str) -> digest.Paper:
    paper = digest.Paper(
        arxiv_id=f"venue:{fingerprint(venue, title, url)}",
        title=title or url,
        title_zh="",
        authors=[],
        published=source_date(target_day, idx),
        updated=source_date(target_day, idx),
        categories=[venue, "official-venue"],
        summary_en=summary,
        summary_zh="",
        link_abs=url or source_url,
        link_pdf=url or source_url,
        comment=f"Official venue monitor source: {source_url}",
        journal_ref=venue,
        major_area="CV",
        accepted_venue=digest.normalize_venue_name(venue),
        accepted_hint=f"Published or listed on official {venue} source.",
        source_platform="Official Venue",
        source_venue=digest.normalize_venue_name(venue),
        full_text_status="官网条目",
        full_text_url=url or source_url,
    )
    return digest.refresh_paper_derived_fields(paper)


def parse_cvf(venue: str, url: str, body: str, target_day: dt.date, limit: int) -> List[digest.Paper]:
    papers: List[digest.Paper] = []
    for idx, match in enumerate(re.finditer(r'<dt class="ptitle">\s*<br>\s*<a href="([^"]+)">(.*?)</a>\s*</dt>', body or "", flags=re.S | re.I)):
        paper_url = absolute_url(url, match.group(1))
        title = strip_tags(match.group(2))
        papers.append(make_paper(venue, title, paper_url, "", target_day, idx, url))
        if len(papers) >= limit:
            break
    if not papers:
        for idx, match in enumerate(re.finditer(r'<a href="([^"]+)">([^<]{12,220})</a>', body or "", flags=re.S | re.I)):
            title = strip_tags(match.group(2))
            if not title or title.lower() in {"pdf", "supp", "bibtex"}:
                continue
            papers.append(make_paper(venue, title, absolute_url(url, match.group(1)), "", target_day, idx, url))
            if len(papers) >= limit:
                break
    return papers


def parse_rss_or_atom(venue: str, url: str, body: str, target_day: dt.date, limit: int) -> List[digest.Paper]:
    try:
        root = ET.fromstring(body)
    except Exception:
        return []
    papers: List[digest.Paper] = []
    for idx, item in enumerate(root.findall(".//item")):
        title = digest.safe_text(item.findtext("title", ""))
        link = digest.safe_text(item.findtext("link", ""))
        desc = strip_tags(item.findtext("description", ""))
        if title:
            papers.append(make_paper(venue, title, link, desc, target_day, idx, url))
        if len(papers) >= limit:
            break
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for idx, entry in enumerate(root.findall(".//a:entry", ns), start=len(papers)):
        title = digest.safe_text(entry.findtext("a:title", "", ns))
        link = ""
        for link_node in entry.findall("a:link", ns):
            if link_node.attrib.get("href"):
                link = link_node.attrib["href"]
                break
        desc = strip_tags(entry.findtext("a:summary", "", ns))
        if title:
            papers.append(make_paper(venue, title, link, desc, target_day, idx, url))
        if len(papers) >= limit:
            break
    return papers[:limit]


def parse_generic(venue: str, url: str, body: str, target_day: dt.date, limit: int) -> List[digest.Paper]:
    feed_items = parse_rss_or_atom(venue, url, body, target_day, limit)
    if feed_items:
        return feed_items
    candidates: List[Tuple[str, str]] = []
    patterns = [
        r'<meta[^>]+name=["\']citation_title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<h[1-4][^>]*>(.{12,260}?)</h[1-4]>',
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.{18,260}?)</a>',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, body or "", flags=re.S | re.I):
            if len(match.groups()) == 1:
                title = strip_tags(match.group(1))
                link = url
            else:
                link = absolute_url(url, match.group(1))
                title = strip_tags(match.group(2))
            lower = title.lower()
            if not title or any(skip in lower for skip in ["login", "sign in", "subscribe", "proceedings", "archive", "call for"]):
                continue
            if len(title.split()) < 4:
                continue
            candidates.append((title, link))
    seen = set()
    papers: List[digest.Paper] = []
    for idx, (title, link) in enumerate(candidates):
        key = fingerprint(title, link)
        if key in seen:
            continue
        seen.add(key)
        papers.append(make_paper(venue, title, link, "", target_day, idx, url))
        if len(papers) >= limit:
            break
    return papers


def parse_pmlr(venue: str, url: str, body: str, target_day: dt.date, limit: int) -> List[digest.Paper]:
    papers = []
    for idx, match in enumerate(re.finditer(r'<a href="([^"]+)">\s*<p class="title">(.*?)</p>', body or "", flags=re.S | re.I)):
        papers.append(make_paper(venue, strip_tags(match.group(2)), absolute_url(url, match.group(1)), "", target_day, idx, url))
        if len(papers) >= limit:
            break
    return papers or parse_generic(venue, url, body, target_day, limit)


def parse_source(source: Dict[str, str], body: str, target_day: dt.date, limit: int) -> List[digest.Paper]:
    kind = source.get("kind", "generic")
    venue = source.get("venue", "")
    url = source.get("url", "")
    if kind == "cvf":
        return parse_cvf(venue, url, body, target_day, limit)
    if kind == "pmlr":
        return parse_pmlr(venue, url, body, target_day, limit)
    return parse_generic(venue, url, body, target_day, limit)


def load_sources(path: str) -> List[Dict[str, str]]:
    if not path:
        return DEFAULT_SOURCES[:]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("source config must be a JSON list")
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("venue") and item.get("url"):
            out.append({
                "venue": str(item.get("venue", "")),
                "kind": str(item.get("kind", "generic")),
                "url": str(item.get("url", "")),
            })
    return out


def load_state(path: str) -> Dict[str, object]:
    if not os.path.exists(path):
        return {"seen": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"seen": {}}
    except Exception:
        return {"seen": {}}


def save_state(path: str, state: Dict[str, object]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


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


def render_monitor_report(target_day: dt.date, papers: List[digest.Paper], source_stats: List[Dict[str, object]]) -> str:
    rows = []
    for paper in papers:
        rows.append(
            "<tr>"
            f"<td>{h(paper.source_venue or paper.accepted_venue)}</td>"
            f"<td><a href='{h(paper.link_abs)}' target='_blank'>{h(paper.title_zh or paper.title)}</a><div class='muted'>{h(paper.title)}</div></td>"
            f"<td>{h(paper.summary_zh)}</td>"
            f"<td>{h(paper.full_text_status)}</td>"
            "</tr>"
        )
    stat_rows = []
    for stat in source_stats:
        stat_rows.append(
            "<tr>"
            f"<td>{h(stat.get('venue'))}</td>"
            f"<td><a href='{h(stat.get('url'))}' target='_blank'>{h(stat.get('url'))}</a></td>"
            f"<td>{h(stat.get('status'))}</td>"
            f"<td>{h(stat.get('count'))}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CV Venue Monitor {h(target_day.isoformat())}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f7f8fb; color: #20242a; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 28px 18px 56px; }}
h1 {{ margin: 0 0 8px; font-size: 28px; }}
.muted {{ color: #626a73; font-size: 13px; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; margin-top: 18px; }}
th, td {{ border: 1px solid #d9dee7; padding: 10px; vertical-align: top; text-align: left; }}
th {{ background: #edf1f7; }}
a {{ color: #1756a9; }}
</style>
</head>
<body><main>
<h1>CV 顶会顶刊官网监控</h1>
<p class="muted">监控范围：{h(", ".join(VENUE_NAMES))}。部分官网是动态页面或需要订阅权限，本报告会记录抓取状态并保存新发现条目。</p>
<h2>新发现条目</h2>
<table><thead><tr><th>来源</th><th>标题</th><th>中文摘要</th><th>全文状态</th></tr></thead><tbody>
{''.join(rows) if rows else '<tr><td colspan="4">本次未发现新的可解析条目。</td></tr>'}
</tbody></table>
<h2>来源状态</h2>
<table><thead><tr><th>来源</th><th>URL</th><th>状态</th><th>解析条目数</th></tr></thead><tbody>{''.join(stat_rows)}</tbody></table>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor official CV venue/journal pages for newly visible papers.")
    parser.add_argument("--date", type=str, default="")
    parser.add_argument("--tz", type=str, default=os.environ.get("DIGEST_TZ", "Asia/Shanghai"))
    parser.add_argument("--source-config", type=str, default=os.environ.get("VENUE_SOURCE_CONFIG", ""))
    parser.add_argument("--per-source-limit", type=int, default=int(os.environ.get("VENUE_PER_SOURCE_LIMIT", "80")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("VENUE_TIMEOUT_SECONDS", "20")))
    parser.add_argument("--include-seen", type=int, choices=[0, 1], default=int(os.environ.get("VENUE_INCLUDE_SEEN", "0")))
    parser.add_argument("--google-timeout", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_TIMEOUT_SECONDS", "12")))
    parser.add_argument("--google-limit", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_LIMIT", "-1")))
    parser.add_argument("--google-summary-sentences", type=int, default=int(os.environ.get("GOOGLE_SUMMARY_SENTENCES", "3")))
    parser.add_argument("--google-full-abstract", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_FULL_ABSTRACT", "1")))
    parser.add_argument("--output-suffix", type=str, default=os.environ.get("VENUE_REPORT_SUFFIX", "venue_monitor"))
    parser.add_argument("--report-dir", type=str, default="reports")
    parser.add_argument("--data-dir", type=str, default="data")
    args = parser.parse_args()

    tz = ZoneInfo(args.tz)
    target_day = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(tz).date()
    os.makedirs(args.report_dir, exist_ok=True)
    os.makedirs(args.data_dir, exist_ok=True)
    digest.ACTIVE_TRANSLATION_CACHE_SALT = "venue-monitor"

    sources = load_sources(args.source_config)
    state_path = os.path.join(args.data_dir, "venue_monitor_state.json")
    state = load_state(state_path)
    seen = state.setdefault("seen", {})
    if not isinstance(seen, dict):
        seen = {}
        state["seen"] = seen

    all_papers: List[digest.Paper] = []
    source_stats: List[Dict[str, object]] = []
    for source in sources:
        url = source["url"]
        venue = source["venue"]
        try:
            body = digest.request_url(url, timeout=args.timeout, retries=1, allow_partial=False)
            parsed = parse_source(source, body, target_day, args.per_source_limit)
            source_stats.append({"venue": venue, "url": url, "status": "ok", "count": len(parsed)})
            print(f"[INFO] {venue} parsed {len(parsed)} items from {url}")
            all_papers.extend(parsed)
            time.sleep(0.4)
        except Exception as exc:
            source_stats.append({"venue": venue, "url": url, "status": f"failed: {exc}", "count": 0})
            print(f"[WARN] {venue} fetch failed: {url}: {exc}")

    all_papers = digest.dedupe_papers(all_papers)
    new_papers = []
    for paper in all_papers:
        key = paper.arxiv_id
        if args.include_seen or key not in seen:
            new_papers.append(paper)
        seen.setdefault(key, {
            "title": paper.title,
            "url": paper.link_abs,
            "venue": paper.source_venue or paper.accepted_venue,
            "first_seen": dt.datetime.now(tz).isoformat(timespec="seconds"),
        })
    translate(new_papers, args)
    save_state(state_path, state)

    suffix = digest.normalize_output_suffix(args.output_suffix) or "venue_monitor"
    html_path = os.path.join(args.report_dir, f"venue_monitor_{target_day.isoformat()}_{suffix}.html")
    json_path = os.path.join(args.data_dir, f"venue_monitor_{target_day.isoformat()}_{suffix}.json")
    md_path = os.path.join(args.report_dir, f"venue_monitor_{target_day.isoformat()}_{suffix}.md")
    html_text = render_monitor_report(target_day, new_papers, source_stats)
    md_lines = [f"# CV Venue Monitor {target_day.isoformat()}", ""]
    for paper in new_papers:
        md_lines.append(f"- [{paper.source_venue or paper.accepted_venue}] [{paper.title_zh or paper.title}]({paper.link_abs}) | {paper.full_text_status}")
    if not new_papers:
        md_lines.append("- 本次未发现新的可解析条目。")
    payload = {
        "date": target_day.isoformat(),
        "timezone": args.tz,
        "monitored_venues": VENUE_NAMES,
        "sources": sources,
        "source_stats": source_stats,
        "new_count": len(new_papers),
        "new_papers": [asdict(p) for p in new_papers],
        "all_parsed_count": len(all_papers),
        "state_path": state_path,
        "notes": {
            "coverage": "Official venue pages differ by publisher; dynamic pages and paywalled indexes may only yield page-change or heading-level entries.",
            "html_path": html_path,
            "markdown_path": md_path,
            "json_path": json_path,
        },
    }
    digest.write_text(html_path, html_text)
    digest.write_text(md_path, "\n".join(md_lines) + "\n")
    digest.dump_json(json_path, payload)
    print(f"[OK] New venue papers: {len(new_papers)}")
    print(f"[OK] State: {state_path}")
    print(f"[OK] HTML report: {html_path}")
    print(f"[OK] Markdown: {md_path}")
    print(f"[OK] JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
