#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import arxiv_daily_digest as digest


TTA_STRONG_PATTERNS = [
    ("test-time adaptation", r"\btest[- ]time adaptation\b", 8),
    ("test-time training", r"\btest[- ]time training\b", 8),
    ("test-time update", r"\btest[- ]time update\b", 8),
    ("test-time calibration", r"\btest[- ]time calibration\b", 7),
    ("continual test-time adaptation", r"\bcontinual test[- ]time adaptation\b", 8),
    ("fully test-time adaptation", r"\bfully test[- ]time adaptation\b", 8),
    ("online test-time adaptation", r"\bonline test[- ]time adaptation\b", 8),
    ("source-free domain adaptation", r"\bsource[- ]free domain adaptation\b", 7),
    ("source-free adaptation", r"\bsource[- ]free adaptation\b", 6),
    ("test-time prompt tuning", r"\btest[- ]time prompt(?: tuning)?\b", 6),
    ("adaptation at test time", r"\badaptation at test time\b", 6),
]

TTA_SUPPORT_PATTERNS = [
    ("test-time", r"\btest[- ]time\b", 2),
    ("source-free", r"\bsource[- ]free\b", 3),
    ("unlabeled target", r"\bunlabeled target\b", 3),
    ("without target labels", r"\bwithout target labels?\b", 3),
    ("no source data", r"\bno source data\b", 3),
    ("online adaptation", r"\bonline adaptation\b", 3),
    ("continual adaptation", r"\bcontinual adaptation\b", 2),
    ("domain shift", r"\bdomain shift\b", 2),
    ("distribution shift", r"\bdistribution shift\b", 2),
    ("covariate shift", r"\bcovariate shift\b", 2),
]

TTA_ACRONYM_PATTERNS = [
    ("TTA acronym", r"\btta\b", 3),
    ("TTT acronym", r"\bttt\b", 3),
]

ASSUMPTION_PROBES_RAW = {
    "source-free setting": [r"\bsource[- ]free\b", r"\bwithout source data\b", r"\bno source data\b"],
    "unlabeled target data": [r"\bunlabeled target\b", r"\bwithout target labels?\b", r"\bno target labels?\b"],
    "single target stream/domain": [r"\bsingle target domain\b", r"\bone target domain\b", r"\btarget stream\b", r"\bstreaming target\b"],
    "batch-dependent inference": [r"\bbatch norm(?:alization)?\b", r"\bbatch statistics\b", r"\btest batch\b"],
    "closed-set/shared label space": [r"\bclosed[- ]set\b", r"\bsame label space\b", r"\bshared label space\b"],
    "frozen pretrained backbone": [r"\bfrozen backbone\b", r"\bfrozen encoder\b", r"\bpre[- ]trained\b", r"\bfoundation model\b"],
    "online non-stationary stream": [r"\bonline adaptation\b", r"\bcontinual adaptation\b", r"\bnon[- ]stationary\b", r"\bstreaming\b"],
    "distribution shift framing": [r"\bdomain shift\b", r"\bdistribution shift\b", r"\bcovariate shift\b"],
}

METHOD_ROUTE_PROBES_RAW = {
    "entropy minimization": [r"\bentropy minim(?:ization|isation)\b"],
    "pseudo-label refinement": [r"\bpseudo[- ]label", r"\bself[- ]training\b"],
    "BN/statistics adaptation": [r"\bbatch norm(?:alization)?\b", r"\bbatch statistics\b"],
    "prompt or context tuning": [r"\bprompt tuning\b", r"\bprompt learning\b", r"\bcontext optimization\b", r"\bvisual prompt\b"],
    "adapter/lightweight modules": [r"\badapter\b", r"\blightweight module\b", r"\bparameter[- ]efficient\b"],
    "memory bank or prototypes": [r"\bmemory bank\b", r"\bprototype\b", r"\bprototype bank\b"],
    "teacher-student consistency": [r"\bteacher\b", r"\bstudent\b", r"\bema\b", r"\bconsistency\b"],
    "augmentation consistency": [r"\baugmentation\b", r"\baugmented\b", r"\bconsistency regularization\b"],
    "calibration/uncertainty": [r"\bcalibration\b", r"\buncertainty\b", r"\bconfidence\b"],
    "prompted foundation models": [r"\bfoundation model\b", r"\bclip\b", r"\bvision-language\b", r"\bvlm\b"],
}

FRAGILITY_PROBES_RAW = {
    "relies on unlabeled target stream": [r"\bunlabeled target\b", r"\bwithout target labels?\b", r"\btarget stream\b"],
    "may depend on test batch statistics": [r"\bbatch norm(?:alization)?\b", r"\bbatch statistics\b", r"\btest batch\b"],
    "may assume stable label space": [r"\bclosed[- ]set\b", r"\bsame label space\b", r"\bshared label space\b"],
    "may assume mild distribution shift": [r"\bcovariate shift\b", r"\bdomain shift\b", r"\bdistribution shift\b"],
    "may depend on pseudo-label quality": [r"\bpseudo[- ]label", r"\bself[- ]training\b"],
    "may trade accuracy for online cost": [r"\bonline adaptation\b", r"\bcontinual adaptation\b", r"\breal[- ]time\b", r"\bcompute\b", r"\blatency\b"],
}

TTA_ACTION_PATTERN = re.compile(
    r"\b(adaptation|adapting|adaptive|training|update|updating|calibration|calibrating|source[- ]free|prompt(?: tuning| learning)?|domain adaptation)\b",
    flags=re.I,
)


def compile_probe_map(raw: Dict[str, List[str]]) -> Dict[str, List[re.Pattern[str]]]:
    compiled: Dict[str, List[re.Pattern[str]]] = {}
    for label, patterns in raw.items():
        compiled[label] = [re.compile(pattern, flags=re.I) for pattern in patterns]
    return compiled


ASSUMPTION_PROBES = compile_probe_map(ASSUMPTION_PROBES_RAW)
METHOD_ROUTE_PROBES = compile_probe_map(METHOD_ROUTE_PROBES_RAW)
FRAGILITY_PROBES = compile_probe_map(FRAGILITY_PROBES_RAW)


def non_other(items: Iterable[str]) -> List[str]:
    out = []
    for item in items:
        clean = digest.safe_text(item)
        if not clean or clean == "other" or clean in out:
            continue
        out.append(clean)
    return out


def first_non_other(items: Iterable[str], fallback: str = "other") -> str:
    rows = non_other(items)
    return rows[0] if rows else fallback


def parse_categories(text: str) -> List[str]:
    return [digest.safe_text(part) for part in text.split(",") if digest.safe_text(part)]


def to_local_day(iso_text: str, tz_name: str) -> str:
    try:
        return digest.iso_to_dt(iso_text).astimezone(ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        return ""


def days_ago(iso_text: str, target_day: dt.date, tz_name: str) -> int:
    try:
        local_day = digest.iso_to_dt(iso_text).astimezone(ZoneInfo(tz_name)).date()
        return max(0, (target_day - local_day).days)
    except Exception:
        return 9999


def joined(items: Iterable[str]) -> str:
    rows = [digest.safe_text(item) for item in items if digest.safe_text(item)]
    return "; ".join(rows)


def summary_brief(paper: digest.Paper, max_chars: int = 420) -> str:
    brief = digest.select_summary_sentences(
        paper.title,
        paper.summary_en,
        hint_terms=non_other(paper.focus_tags) + non_other(paper.domain_tags) + non_other(paper.task_tags),
        max_sentences=3,
        max_chars=max_chars,
    )
    if brief:
        return brief
    return digest.safe_text(paper.summary_en)[:max_chars]


def match_probe_labels(text: str, probe_map: Dict[str, List[re.Pattern[str]]]) -> List[str]:
    lower = digest.safe_text(text).lower()
    hits: List[str] = []
    for label, patterns in probe_map.items():
        if any(pattern.search(lower) for pattern in patterns):
            hits.append(label)
    return hits


def score_tta_paper(paper: digest.Paper) -> Tuple[int, List[str]]:
    title = digest.safe_text(paper.title)
    title_lower = title.lower()
    blob = digest.safe_text(
        f"{paper.title} {paper.summary_en} {paper.comment} {paper.journal_ref} {' '.join(paper.categories)}"
    ).lower()
    score = 0
    reasons: List[str] = []

    for label, pattern, weight in TTA_STRONG_PATTERNS:
        if re.search(pattern, title_lower):
            score += weight + 2
            reasons.append(f"title:{label}")
        elif re.search(pattern, blob):
            score += weight
            reasons.append(f"abstract:{label}")

    for label, pattern, weight in TTA_SUPPORT_PATTERNS:
        if re.search(pattern, title_lower):
            score += weight + 1
            reasons.append(f"title:{label}")
        elif re.search(pattern, blob):
            score += weight
            reasons.append(f"abstract:{label}")

    for label, pattern, weight in TTA_ACRONYM_PATTERNS:
        if re.search(pattern, title_lower):
            score += weight
            reasons.append(f"title:{label}")
        elif re.search(pattern, blob) and re.search(r"\b(domain|distribution|test[- ]time|source[- ]free|target)\b", blob):
            score += max(1, weight - 1)
            reasons.append(f"context:{label}")

    if re.search(r"\b(domain shift|distribution shift|covariate shift)\b", blob) and re.search(r"\btest[- ]time\b", blob):
        score += 2
        reasons.append("paired:test-time+shift")
    if re.search(r"\bsource[- ]free\b", blob) and re.search(r"\badaptation\b", blob):
        score += 2
        reasons.append("paired:source-free+adaptation")
    if re.search(r"\bunlabeled target\b", blob) and re.search(r"\badaptation\b", blob):
        score += 1
        reasons.append("paired:unlabeled-target+adaptation")

    reasons = list(dict.fromkeys(reasons))
    return score, reasons


def fetch_category_range(
    categories: List[str],
    target_day: dt.date,
    tz_name: str,
    page_size: int,
    max_scan: int,
    day_window_days: int,
) -> List[digest.Paper]:
    gathered: List[digest.Paper] = []
    for category in categories:
        papers = digest.fetch_daily_by_category(
            category=category,
            target_day=target_day,
            tz_name=tz_name,
            page_size=page_size,
            max_scan=max_scan,
            day_window_days=day_window_days,
        )
        gathered.extend(papers)
    return digest.dedupe_papers(gathered)


def apply_optional_translation(
    papers: List[digest.Paper],
    args: argparse.Namespace,
    data_dir: str,
) -> None:
    if args.translate_backend == "none":
        return

    google_cache_path = os.path.join(data_dir, "google_translation_cache.json")
    llm_cache_path = os.path.join(data_dir, "llm_translation_cache.json")
    google_cache = digest.load_llm_cache(google_cache_path)
    llm_cache = digest.load_llm_cache(llm_cache_path)

    if args.translate_backend == "google":
        digest.apply_translation_cache(papers, [google_cache])
        digest.google_enrich_title_and_summary(
            papers,
            google_cache,
            google_cache_path,
            limit=args.translate_limit,
            timeout=args.google_timeout,
            full_abstract=bool(args.google_full_abstract),
            summary_sentences=args.google_summary_sentences,
        )
    elif args.translate_backend == "llm":
        digest.apply_translation_cache(papers, [llm_cache])
        digest.llm_enrich_title_and_summary(
            papers,
            model=args.model,
            api_base=args.api_base,
            cache=llm_cache,
            cache_path=llm_cache_path,
            llm_limit=args.translate_limit,
            max_retries=args.llm_max_retries,
            failed_retry_cooldown_hours=args.llm_failed_cooldown_hours,
            request_timeout=args.llm_timeout,
        )
    elif args.translate_backend == "auto":
        digest.apply_translation_cache(papers, [llm_cache, google_cache])
        digest.llm_enrich_title_and_summary(
            papers,
            model=args.model,
            api_base=args.api_base,
            cache=llm_cache,
            cache_path=llm_cache_path,
            llm_limit=args.translate_limit,
            max_retries=args.llm_max_retries,
            failed_retry_cooldown_hours=args.llm_failed_cooldown_hours,
            request_timeout=args.llm_timeout,
        )
        remaining = [paper for paper in papers if digest.missing_translation(paper)]
        if remaining:
            digest.google_enrich_title_and_summary(
                remaining,
                google_cache,
                google_cache_path,
                limit=-1,
                timeout=args.google_timeout,
                full_abstract=bool(args.google_full_abstract),
                summary_sentences=args.google_summary_sentences,
            )


def paper_record(
    paper: digest.Paper,
    target_day: dt.date,
    tz_name: str,
    score: int = 0,
    reasons: Optional[List[str]] = None,
) -> Dict[str, object]:
    text_blob = digest.safe_text(f"{paper.title} {paper.summary_en} {paper.comment} {paper.journal_ref}")
    assumptions = match_probe_labels(text_blob, ASSUMPTION_PROBES)
    routes = match_probe_labels(text_blob, METHOD_ROUTE_PROBES)
    fragilities = match_probe_labels(text_blob, FRAGILITY_PROBES)
    keywords = non_other(paper.keywords)
    record = {
        "arxiv_id": paper.arxiv_id,
        "date_local": to_local_day(paper.published, tz_name),
        "days_ago": days_ago(paper.published, target_day, tz_name),
        "title": paper.title,
        "title_zh": digest.safe_text(paper.title_zh),
        "authors": list(paper.authors),
        "categories": list(paper.categories),
        "major_area": paper.major_area,
        "domain_tags": non_other(paper.domain_tags),
        "task_tags": non_other(paper.task_tags),
        "type_tags": non_other(paper.type_tags),
        "focus_tags": non_other(paper.focus_tags),
        "keywords": keywords,
        "primary_domain": first_non_other(paper.domain_tags),
        "primary_task": first_non_other(paper.task_tags),
        "primary_type": first_non_other(paper.type_tags),
        "assumption_signals": assumptions,
        "method_routes": routes,
        "fragility_flags": fragilities,
        "summary_brief_en": summary_brief(paper),
        "summary_en": digest.safe_text(paper.summary_en),
        "summary_zh": digest.safe_text(paper.summary_zh),
        "comment": digest.safe_text(paper.comment),
        "journal_ref": digest.safe_text(paper.journal_ref),
        "accepted_venue": digest.safe_text(paper.accepted_venue),
        "accepted_hint": digest.safe_text(paper.accepted_hint),
        "link_abs": paper.link_abs,
        "link_pdf": paper.link_pdf,
        "tta_match_score": score,
        "tta_match_reasons": list(reasons or []),
    }
    return record


def filter_tta_records(
    papers: List[digest.Paper],
    target_day: dt.date,
    tz_name: str,
    threshold: int,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for paper in papers:
        score, reasons = score_tta_paper(paper)
        if score < threshold:
            continue
        blob = digest.safe_text(f"{paper.title} {paper.summary_en} {paper.comment} {paper.journal_ref}")
        strong_hit = any(
            any(token in reason for token in [
                "test-time adaptation",
                "test-time training",
                "test-time update",
                "test-time calibration",
                "source-free adaptation",
                "source-free domain adaptation",
                "test-time prompt tuning",
                "adaptation at test time",
            ])
            for reason in reasons
        )
        if not strong_hit and not TTA_ACTION_PATTERN.search(blob):
            continue
        rows.append(paper_record(paper, target_day=target_day, tz_name=tz_name, score=score, reasons=reasons))
    rows.sort(key=lambda row: (int(row["tta_match_score"]), str(row["date_local"])), reverse=True)
    return rows


def limit_records(records: List[Dict[str, object]], limit: int) -> List[Dict[str, object]]:
    if limit > 0:
        return records[:limit]
    return records


def count_labels(records: List[Dict[str, object]], key: str) -> List[Tuple[str, int]]:
    counter: Counter[str] = Counter()
    for record in records:
        for item in list(record.get(key, []) or []):
            clean = digest.safe_text(str(item))
            if clean:
                counter[clean] += 1
    return counter.most_common()


def build_trend_rows(records: List[Dict[str, object]], bucket_days: int) -> List[Dict[str, object]]:
    if not records:
        return []
    bucket_days = max(7, bucket_days)
    label_counts: Dict[str, List[int]] = defaultdict(lambda: [0, 0, 0])
    for record in records:
        age = int(record.get("days_ago", 9999))
        if age < bucket_days:
            bucket_idx = 0
        elif age < bucket_days * 2:
            bucket_idx = 1
        elif age < bucket_days * 3:
            bucket_idx = 2
        else:
            continue
        labels: List[str] = []
        labels.extend(list(record.get("task_tags", []) or []))
        labels.extend(list(record.get("assumption_signals", []) or []))
        labels.extend(list(record.get("method_routes", []) or []))
        labels.extend(list(record.get("keywords", []) or [])[:3])
        for label in dict.fromkeys(labels):
            clean = digest.safe_text(str(label))
            if clean and clean != "other":
                label_counts[clean][bucket_idx] += 1

    rows: List[Dict[str, object]] = []
    for label, buckets in label_counts.items():
        total = sum(buckets)
        if total < 2:
            continue
        recent, middle, early = buckets
        status = "stable"
        if recent >= 2 and recent >= middle + 1 and recent >= early + 1:
            status = "heating_up"
        elif recent == 0 and middle + early >= 3:
            status = "cooling_down"
        elif recent >= 2 and middle == 0 and early == 0:
            status = "newly_appearing"
        elif recent <= max(0, middle - 2) and middle >= 2:
            status = "recently_ignored"
        rows.append(
            {
                "label": label,
                "recent_bucket": recent,
                "middle_bucket": middle,
                "early_bucket": early,
                "total": total,
                "status": status,
            }
        )
    rows.sort(
        key=lambda row: (
            1 if row["status"] in {"heating_up", "newly_appearing"} else 0,
            int(row["recent_bucket"]),
            int(row["total"]),
            len(str(row["label"])),
        ),
        reverse=True,
    )
    return rows


def build_setting_clusters(records: List[Dict[str, object]]) -> List[Dict[str, object]]:
    clusters: Dict[Tuple[str, Tuple[str, ...]], Dict[str, object]] = {}
    for record in records:
        primary_task = str(record.get("primary_task") or "other")
        assumptions = tuple(list(record.get("assumption_signals", []) or [])[:2])
        signature = (primary_task, assumptions)
        cluster = clusters.setdefault(
            signature,
            {
                "primary_task": primary_task,
                "assumptions": list(assumptions),
                "paper_ids": [],
                "titles": [],
                "method_counter": Counter(),
                "domain_counter": Counter(),
            },
        )
        cluster["paper_ids"].append(str(record.get("arxiv_id", "")))
        cluster["titles"].append(str(record.get("title", "")))
        for route in list(record.get("method_routes", []) or []):
            cluster["method_counter"][route] += 1
        for domain in list(record.get("domain_tags", []) or []):
            cluster["domain_counter"][domain] += 1

    out: List[Dict[str, object]] = []
    for cluster in clusters.values():
        paper_ids = list(dict.fromkeys(cluster["paper_ids"]))
        if len(paper_ids) < 2:
            continue
        out.append(
            {
                "primary_task": cluster["primary_task"],
                "assumptions": cluster["assumptions"],
                "paper_count": len(paper_ids),
                "paper_ids": paper_ids,
                "top_methods": [label for label, _count in cluster["method_counter"].most_common(5)],
                "top_domains": [label for label, _count in cluster["domain_counter"].most_common(4)],
            }
        )
    out.sort(key=lambda row: (int(row["paper_count"]), len(row["assumptions"])), reverse=True)
    return out


def build_recent_cv_highlights(
    recent_cv_records: List[Dict[str, object]],
    tta_records: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    tta_keywords = {keyword for keyword, _count in count_labels(tta_records, "keywords")[:80]}
    highlights: List[Dict[str, object]] = []
    for record in recent_cv_records:
        keywords = list(record.get("keywords", []) or [])
        novel_keywords = [keyword for keyword in keywords if keyword not in tta_keywords][:4]
        score = len(novel_keywords) * 2 + len(list(record.get("task_tags", []) or []))
        highlights.append(
            {
                "arxiv_id": record["arxiv_id"],
                "title": record["title"],
                "date_local": record["date_local"],
                "primary_task": record["primary_task"],
                "primary_domain": record["primary_domain"],
                "novel_keywords_vs_tta": novel_keywords,
                "summary_brief_en": record["summary_brief_en"],
                "link_abs": record["link_abs"],
                "score": score,
            }
        )
    highlights.sort(key=lambda row: (int(row["score"]), str(row["date_local"])), reverse=True)
    return highlights[:30]


def build_bridge_candidates(
    tta_clusters: List[Dict[str, object]],
    recent_cv_records: List[Dict[str, object]],
    tta_records: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    tta_keywords = {keyword for keyword, _count in count_labels(tta_records, "keywords")[:120]}
    candidates: List[Dict[str, object]] = []
    seen: set[Tuple[str, str]] = set()
    for cluster in tta_clusters[:12]:
        cluster_task = str(cluster.get("primary_task") or "other")
        cluster_domains = set(cluster.get("top_domains", []) or [])
        for record in recent_cv_records:
            key = (",".join(cluster.get("paper_ids", [])[:2]), str(record.get("arxiv_id", "")))
            if key in seen:
                continue
            shared_tasks = []
            if cluster_task != "other" and cluster_task in list(record.get("task_tags", []) or []):
                shared_tasks.append(cluster_task)
            shared_domains = sorted(cluster_domains & set(list(record.get("domain_tags", []) or [])))
            novel_keywords = [keyword for keyword in list(record.get("keywords", []) or []) if keyword not in tta_keywords][:3]
            score = (4 * len(shared_tasks)) + (2 * len(shared_domains)) + len(novel_keywords)
            if score < 3:
                continue
            seen.add(key)
            candidates.append(
                {
                    "idea_type": "old_problem_new_method" if novel_keywords else "cross_subfield_bridge",
                    "tta_problem_task": cluster_task,
                    "tta_assumptions": list(cluster.get("assumptions", []) or []),
                    "tta_source_papers": list(cluster.get("paper_ids", []) or [])[:4],
                    "recent_cv_paper": record["arxiv_id"],
                    "recent_cv_title": record["title"],
                    "shared_tasks": shared_tasks,
                    "shared_domains": shared_domains,
                    "new_method_keywords": novel_keywords,
                    "score": score,
                }
            )
    candidates.sort(key=lambda row: (int(row["score"]), len(list(row["new_method_keywords"]))), reverse=True)
    return candidates[:25]


def build_tta_brief(
    records: List[Dict[str, object]],
    tta_days: int,
    trend_bucket_days: int,
) -> str:
    top_tasks = count_labels(records, "task_tags")[:8]
    top_assumptions = count_labels(records, "assumption_signals")[:10]
    top_routes = count_labels(records, "method_routes")[:10]
    top_fragilities = count_labels(records, "fragility_flags")[:10]
    trend_rows = build_trend_rows(records, bucket_days=trend_bucket_days)[:16]
    setting_clusters = build_setting_clusters(records)[:12]
    month_counter = Counter(str(record.get("date_local", ""))[:7] for record in records if str(record.get("date_local", "")))

    out: List[str] = []
    out.append("# TTA Landscape Brief")
    out.append("")
    out.append(f"- 覆盖窗口: 最近 {tta_days} 天")
    out.append(f"- 命中论文数: {len(records)}")
    out.append(f"- 趋势桶宽度: {trend_bucket_days} 天")
    out.append("")
    out.append("## Monthly Counts")
    out.append("")
    for month, count in sorted(month_counter.items()):
        out.append(f"- {month}: {count}")
    out.append("")
    out.append("## Top Tasks")
    out.append("")
    for label, count in top_tasks:
        out.append(f"- {label}: {count}")
    out.append("")
    out.append("## Shared Assumption Signals")
    out.append("")
    for label, count in top_assumptions:
        out.append(f"- {label}: {count}")
    out.append("")
    out.append("## Dominant Method Routes")
    out.append("")
    for label, count in top_routes:
        out.append(f"- {label}: {count}")
    out.append("")
    out.append("## Fragility Flags")
    out.append("")
    for label, count in top_fragilities:
        out.append(f"- {label}: {count}")
    out.append("")
    out.append("## Repeated Settings")
    out.append("")
    for row in setting_clusters:
        assumptions = joined(row["assumptions"]) or "none explicit"
        top_methods = joined(row["top_methods"]) or "no strong route signal"
        out.append(
            f"- task={row['primary_task']} | assumptions={assumptions} | papers={row['paper_count']} | routes={top_methods} | ids={joined(row['paper_ids'][:5])}"
        )
    out.append("")
    out.append("## Trend Candidates")
    out.append("")
    for row in trend_rows:
        out.append(
            f"- {row['label']}: recent={row['recent_bucket']}, middle={row['middle_bucket']}, early={row['early_bucket']}, status={row['status']}"
        )
    return "\n".join(out).strip() + "\n"


def build_bridge_brief(
    tta_records: List[Dict[str, object]],
    recent_cv_records: List[Dict[str, object]],
) -> str:
    cv_highlights = build_recent_cv_highlights(recent_cv_records, tta_records)
    bridge_candidates = build_bridge_candidates(build_setting_clusters(tta_records), recent_cv_records, tta_records)

    out: List[str] = []
    out.append("# Cross-Ideation Seeds")
    out.append("")
    out.append(f"- TTA corpus size: {len(tta_records)}")
    out.append(f"- Recent CV corpus size: {len(recent_cv_records)}")
    out.append("")
    out.append("## Recent CV Highlights")
    out.append("")
    for row in cv_highlights:
        out.append(
            f"- [{row['arxiv_id']}] {row['title']} | task={row['primary_task']} | novel_keywords={joined(row['novel_keywords_vs_tta']) or 'n/a'}"
        )
    out.append("")
    out.append("## Bridge Candidates")
    out.append("")
    for row in bridge_candidates:
        out.append(
            f"- type={row['idea_type']} | TTA task={row['tta_problem_task']} | assumptions={joined(row['tta_assumptions']) or 'none'} | recent={row['recent_cv_paper']} | keywords={joined(row['new_method_keywords']) or 'n/a'} | shared_task={joined(row['shared_tasks']) or 'n/a'} | shared_domain={joined(row['shared_domains']) or 'n/a'}"
        )
    return "\n".join(out).strip() + "\n"


def render_corpus_markdown(title: str, records: List[Dict[str, object]]) -> str:
    out: List[str] = []
    out.append(f"# {title}")
    out.append("")
    out.append(f"- 论文数: {len(records)}")
    out.append("")
    for index, record in enumerate(records, start=1):
        title_zh = digest.safe_text(str(record.get("title_zh", "")))
        summary_zh = digest.safe_text(str(record.get("summary_zh", "")))
        out.append(f"## {index}. [{record['arxiv_id']}] {record['title']}")
        out.append("")
        out.append(f"- Date: {record['date_local']}")
        out.append(f"- Task: {joined(record.get('task_tags', [])) or 'other'}")
        out.append(f"- Domain: {joined(record.get('domain_tags', [])) or 'other'}")
        out.append(f"- Type: {joined(record.get('type_tags', [])) or 'other'}")
        if int(record.get("tta_match_score", 0)) > 0:
            out.append(f"- TTA Score: {record['tta_match_score']} ({joined(record.get('tta_match_reasons', []))})")
        out.append(f"- Assumptions: {joined(record.get('assumption_signals', [])) or 'none explicit'}")
        out.append(f"- Routes: {joined(record.get('method_routes', [])) or 'none explicit'}")
        out.append(f"- Fragility Flags: {joined(record.get('fragility_flags', [])) or 'none explicit'}")
        out.append(f"- Keywords: {joined(record.get('keywords', [])) or 'none explicit'}")
        out.append(f"- English Brief: {record['summary_brief_en']}")
        if title_zh:
            out.append(f"- 中文标题: {title_zh}")
        if summary_zh:
            out.append(f"- 中文摘要: {summary_zh}")
        out.append(f"- Abstract: {record['summary_en']}")
        out.append(f"- arXiv: {record['link_abs']}")
        out.append("")
    return "\n".join(out).strip() + "\n"


def write_csv(path: str, records: List[Dict[str, object]]) -> None:
    fieldnames = [
        "arxiv_id",
        "date_local",
        "days_ago",
        "title",
        "title_zh",
        "authors",
        "categories",
        "major_area",
        "domain_tags",
        "task_tags",
        "type_tags",
        "focus_tags",
        "keywords",
        "primary_domain",
        "primary_task",
        "primary_type",
        "assumption_signals",
        "method_routes",
        "fragility_flags",
        "summary_brief_en",
        "summary_en",
        "summary_zh",
        "comment",
        "journal_ref",
        "accepted_venue",
        "accepted_hint",
        "link_abs",
        "link_pdf",
        "tta_match_score",
        "tta_match_reasons",
    ]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = {}
            for fieldname in fieldnames:
                value = record.get(fieldname, "")
                if isinstance(value, list):
                    row[fieldname] = " | ".join(str(item) for item in value)
                else:
                    row[fieldname] = value
            writer.writerow(row)


def build_project_instructions() -> str:
    return (
        "# ChatGPT Project Instructions\n\n"
        "你是一个严格基于上传语料工作的计算机视觉研究分析助手。\n\n"
        "工作规则：\n"
        "- 只能基于上传文件中的标题、摘要、标签、assumption signals、method routes、fragility flags 和 bridge candidates 发言。\n"
        "- 不要假装读过正文，不要编造实验数字。\n"
        "- 每个重要判断至少附 2-3 篇论文 ID；如果证据不够，要明确写“证据不足”。\n"
        "- 区分“直接证据”和“基于摘要的推断”。\n"
        "- 优先抽取结构：问题、假设、设定、方法路线、局限、值得验证的矛盾。\n"
        "- 当你提出新研究方向时，必须输出：研究问题、核心假设、最小实验、主要风险、预期贡献。\n"
        "- 如果多个方向只是换数据集或换 backbone，不算作真正新方向，要合并并指出同质化。\n"
        "- 默认使用中文回答，但保留关键英文术语。\n"
    )


def build_prompt_tta_landscape(packet_name: str) -> str:
    return (
        f"# Prompt 1: TTA Landscape Audit\n\n"
        f"请基于这个 research packet `{packet_name}` 的 TTA 语料与 brief，做一次“研究地形审计”，不要泛泛总结，要像做研究选题扫描。\n\n"
        "输出结构必须严格包含以下部分：\n"
        "1. 这批论文主要在解决什么问题\n"
        "2. 这些工作共享了哪些假设\n"
        "3. 哪些问题被反复做，但核心设定几乎没变\n"
        "4. 哪些空白点目前几乎没人碰\n"
        "5. 哪些论文普遍默认了不合理前提\n"
        "6. 哪些热门路线正在积累相同局限\n"
        "7. 哪些相互矛盾或张力很大的结果值得追\n"
        "8. 最近 1-3 个月什么问题在变热，什么问题反而被忽视\n"
        "9. 给我一个按“值得优先跟进”排序的 8 个研究问题清单\n\n"
        "输出要求：\n"
        "- 每一部分都要引用论文 ID。\n"
        "- 对“被反复做但设定没变”的判断，尽量明确到 task + assumption 组合。\n"
        "- 对“空白点”和“不合理前提”，要写出为什么它是空白或为什么它不合理。\n"
        "- 趋势部分请结合 trend candidates，但不要盲信它们，要自己复核语料。\n"
        "- 最后一节请输出一个表格，列为：研究问题 | 现有证据 | 为什么现在值得做 | 代表论文 ID。\n"
    )


def build_prompt_cross_ideation(packet_name: str) -> str:
    return (
        f"# Prompt 2: Cross-Ideation Engine\n\n"
        f"请基于这个 research packet `{packet_name}` 中的 TTA 语料、近 1-2 天 CV 语料，以及 bridge seeds，系统地产出一批“可验证的研究方向”，不要做空泛 brainstorm。\n\n"
        "你必须同时尝试四类来源：\n"
        "1. 从两个原本不太相连的子领域里找交叉点\n"
        "2. 从一个老问题和一个新方法里做组合\n"
        "3. 从某篇论文的 limitation / fragility flag 出发，拓展成可验证方向\n"
        "4. 从一个常见假设反推出“不满足这个假设时”的新问题\n\n"
        "输出规则：\n"
        "- 至少给出 12 个方向，按“新颖性 x 可验证性 x 实验代价”综合排序。\n"
        "- 每个方向必须严格使用以下模板：\n"
        "  研究问题：\n"
        "  灵感来源：\n"
        "  核心假设：\n"
        "  最小实验：\n"
        "  主要风险：\n"
        "  预期贡献：\n"
        "  支撑论文 ID：\n"
        "- 不要只做换 backbone、换数据集、换 loss 的弱创新。\n"
        "- 如果某个方向其实依赖很强的隐含前提，请直接把这个前提写进“主要风险”。\n"
        "- 最后再给出一个 Top 5 shortlist，说明为什么这 5 个最值得先做。\n"
    )


def build_quickstart_manifest(
    packet_name: str,
    report_dir: str,
    data_dir: str,
) -> str:
    project_instructions_path = Path(report_dir) / "project_instructions.md"
    tta_corpus_path = Path(report_dir) / "tta_corpus.md"
    tta_brief_path = Path(report_dir) / "tta_landscape_brief.md"
    cv_corpus_path = Path(report_dir) / "cv_recent_corpus.md"
    bridge_path = Path(report_dir) / "cross_ideation_seeds.md"
    prompt_tta_path = Path(report_dir) / "prompt_tta_landscape.md"
    prompt_cross_path = Path(report_dir) / "prompt_cross_ideation.md"
    payload_path = Path(data_dir) / "packet_manifest.json"

    return (
        f"# Research Workbench Quickstart\n\n"
        f"- Packet: `{packet_name}`\n"
        f"- 建议放进 ChatGPT Project 的核心文件：\n"
        f"  - `{tta_corpus_path.name}`\n"
        f"  - `{tta_brief_path.name}`\n"
        f"  - `{cv_corpus_path.name}`\n"
        f"  - `{bridge_path.name}`\n"
        f"- Project Instructions: 复制 `{project_instructions_path.name}` 的全文到 ChatGPT Project Instructions。\n"
        f"- 第一次运行：上传上面 4 个文件后，把 `{prompt_tta_path.name}` 贴进去。\n"
        f"- 第二次运行：沿用同一个 Project，再把 `{prompt_cross_path.name}` 贴进去。\n"
        f"- 机器可读清单：`{payload_path.name}`。\n"
    )


def normalize_packet_name(target_day: dt.date, tta_days: int, cv_recent_days: int, suffix: str) -> str:
    base = f"{target_day.isoformat()}_tta{tta_days}d_cv{cv_recent_days}d"
    suffix_clean = digest.normalize_output_suffix(suffix)
    if suffix_clean:
        return f"{base}_{suffix_clean}"
    return base


def build_packet_manifest(
    packet_name: str,
    target_day: dt.date,
    args: argparse.Namespace,
    tta_records: List[Dict[str, object]],
    recent_cv_records: List[Dict[str, object]],
    report_dir: str,
    data_dir: str,
) -> Dict[str, object]:
    return {
        "packet_name": packet_name,
        "date": target_day.isoformat(),
        "timezone": args.tz,
        "categories": parse_categories(args.categories),
        "tta_days": args.tta_days,
        "cv_recent_days": args.cv_recent_days,
        "tta_count": len(tta_records),
        "cv_recent_count": len(recent_cv_records),
        "translate_backend": args.translate_backend,
        "report_dir": report_dir,
        "data_dir": data_dir,
        "recommended_chatgpt_files": [
            os.path.join(report_dir, "tta_corpus.md"),
            os.path.join(report_dir, "tta_landscape_brief.md"),
            os.path.join(report_dir, "cv_recent_corpus.md"),
            os.path.join(report_dir, "cross_ideation_seeds.md"),
        ],
        "prompt_files": {
            "project_instructions": os.path.join(report_dir, "project_instructions.md"),
            "tta_landscape": os.path.join(report_dir, "prompt_tta_landscape.md"),
            "cross_ideation": os.path.join(report_dir, "prompt_cross_ideation.md"),
            "quickstart": os.path.join(report_dir, "quickstart.md"),
        },
        "data_files": {
            "tta_json": os.path.join(data_dir, "tta_papers.json"),
            "tta_csv": os.path.join(data_dir, "tta_papers.csv"),
            "cv_recent_json": os.path.join(data_dir, "cv_recent_papers.json"),
            "cv_recent_csv": os.path.join(data_dir, "cv_recent_papers.csv"),
            "packet_json": os.path.join(data_dir, "packet_manifest.json"),
        },
    }


def load_existing_packet_records(data_dir: str) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], Dict[str, object]]:
    tta_path = os.path.join(data_dir, "tta_papers.json")
    cv_path = os.path.join(data_dir, "cv_recent_papers.json")
    manifest_path = os.path.join(data_dir, "packet_manifest.json")
    if not os.path.exists(tta_path) or not os.path.exists(cv_path):
        raise FileNotFoundError(
            f"Skip-fetch mode needs existing packet data, but missing {tta_path if not os.path.exists(tta_path) else cv_path}"
        )
    tta_payload = json.loads(Path(tta_path).read_text(encoding="utf-8"))
    cv_payload = json.loads(Path(cv_path).read_text(encoding="utf-8"))
    manifest: Dict[str, object] = {}
    if os.path.exists(manifest_path):
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    tta_records = list(tta_payload.get("items", []) or [])
    cv_records = list(cv_payload.get("items", []) or [])
    return tta_records, cv_records, manifest


def compact_record_for_llm(record: Dict[str, object], mode: str) -> Dict[str, object]:
    base = {
        "id": record.get("arxiv_id", ""),
        "date": record.get("date_local", ""),
        "title": record.get("title", ""),
        "task_tags": list(record.get("task_tags", []) or []),
        "domain_tags": list(record.get("domain_tags", []) or []),
        "keywords": list(record.get("keywords", []) or [])[:5],
        "summary_brief_en": record.get("summary_brief_en", ""),
    }
    if mode == "tta":
        base.update(
            {
                "assumption_signals": list(record.get("assumption_signals", []) or []),
                "method_routes": list(record.get("method_routes", []) or []),
                "fragility_flags": list(record.get("fragility_flags", []) or []),
                "tta_match_score": int(record.get("tta_match_score", 0)),
            }
        )
    return base


def resolve_analysis_model(api_base: str, requested_model: str) -> str:
    requested = digest.safe_text(requested_model)
    if requested and requested.lower() != "auto":
        return requested
    base = digest.normalize_api_base(api_base)
    raw = digest.request_url(f"{base}/models", timeout=12, retries=2, allow_partial=False)
    data = json.loads(raw)
    for item in list(data.get("data", []) or []):
        model_id = digest.safe_text(str(item.get("id", "")))
        if model_id:
            return model_id
    raise RuntimeError("Failed to auto-detect a local LLM model id from /v1/models")


def join_lines(items: Iterable[str], fallback: str = "none") -> str:
    rows = [f"- {digest.safe_text(str(item))}" for item in items if digest.safe_text(str(item))]
    return "\n".join(rows) if rows else f"- {fallback}"


def build_analysis_system_prompt() -> str:
    return (
        "你是一个严谨的计算机视觉研究分析器。"
        "你只能基于给定语料、摘要、标签、assumption signals、method routes、fragility flags 和 seeds 输出结论。"
        "不能假装读过正文，不能编造实验数字。"
        "请优先做结构化、可验证、可执行的分析。"
    )


def build_review_system_prompt() -> str:
    return (
        "你是一个专门审查研究分析质量的 reviewer。"
        "请严格输出JSON，字段：overall_score, strengths, missing_dimensions, unsupported_claims, prompt_adjustments, must_fix。"
        "只允许输出JSON对象。"
    )


def run_iterative_text_analysis(
    *,
    name: str,
    model: str,
    api_base: str,
    api_key: str,
    context_text: str,
    task_prompt: str,
    report_dir: str,
    max_output_tokens: int,
    review_output_tokens: int,
) -> Dict[str, object]:
    system_prompt = build_analysis_system_prompt()
    review_system = build_review_system_prompt()

    round1_prompt = context_text.strip() + "\n\n" + task_prompt.strip()
    digest.write_text(os.path.join(report_dir, f"{name}_round1_prompt.md"), round1_prompt)

    reduction_steps = [1.0, 0.72, 0.5, 0.35]
    draft: Optional[str] = None
    draft_debug = ""
    used_ratio = 1.0
    context_compact = context_text.strip()
    for ratio in reduction_steps:
        if ratio >= 0.999:
            current_context = context_compact
        else:
            max_chars = max(6000, int(len(context_compact) * ratio))
            current_context = context_compact[:max_chars].rstrip()
        current_prompt = current_context + "\n\n" + task_prompt.strip()
        draft, draft_debug = digest.call_openai_text_detailed(
            model=model,
            api_key=api_key,
            api_base=api_base,
            system_prompt=system_prompt,
            user_prompt=current_prompt,
            timeout=120,
            max_output_tokens=max_output_tokens,
            temperature=0.2 if "landscape" in name else 0.4,
        )
        if draft:
            used_ratio = ratio
            if current_prompt != round1_prompt:
                digest.write_text(os.path.join(report_dir, f"{name}_round1_prompt_effective.md"), current_prompt)
            break
    if not draft:
        error_path = os.path.join(report_dir, f"{name}_round1_error.txt")
        digest.write_text(
            error_path,
            (
                f"Failed to get non-empty text from local LLM for {name}.\n\n"
                f"API base: {api_base}\n"
                f"Model: {model}\n"
                f"Original prompt chars: {len(round1_prompt)}\n"
                f"Last debug info:\n{draft_debug or '(no diagnostic details returned)'}\n"
            ),
        )
        raise RuntimeError(f"Local LLM returned empty draft for {name}; details saved to {error_path}")
    digest.write_text(os.path.join(report_dir, f"{name}_round1.md"), draft)
    if used_ratio < 0.999:
        digest.write_text(
            os.path.join(report_dir, f"{name}_round1_reduction_note.txt"),
            f"Context reduced to {int(used_ratio * 100)}% of original prompt to fit local model response behavior.\n",
        )

    review_user = (
        f"任务说明：\n{task_prompt.strip()}\n\n"
        f"模型首轮输出：\n{draft.strip()}\n\n"
        "请从以下角度审查并输出JSON：\n"
        "1. 哪些维度漏掉了\n"
        "2. 哪些判断可能证据不足\n"
        "3. prompt 应该补哪些约束或要求\n"
        "4. 哪些段落最该重写\n"
    )
    review = digest.call_openai_json(
        model=model,
        api_key=api_key,
        api_base=api_base,
        system_prompt=review_system,
        user_prompt=review_user,
        timeout=120,
        max_output_tokens=review_output_tokens,
    )
    if not isinstance(review, dict):
        review = {
            "overall_score": 0,
            "strengths": [],
            "missing_dimensions": ["review_generation_failed"],
            "unsupported_claims": [],
            "prompt_adjustments": ["Be stricter about evidence and missing sections."],
            "must_fix": ["Review generation failed; produce a more structured second pass."],
        }
    digest.dump_json(os.path.join(report_dir, f"{name}_review.json"), review)

    revision_notes = []
    revision_notes.extend(list(review.get("missing_dimensions", []) or []))
    revision_notes.extend(list(review.get("unsupported_claims", []) or []))
    revision_notes.extend(list(review.get("prompt_adjustments", []) or []))
    revision_notes.extend(list(review.get("must_fix", []) or []))
    round2_prompt = (
        context_text.strip()
        + "\n\n原始任务：\n"
        + task_prompt.strip()
        + "\n\n上一轮输出存在的问题，请严格修正：\n"
        + join_lines(revision_notes, fallback="Keep the structure but make evidence tighter.")
        + "\n\n请直接输出修订后的最终版本。"
    )
    digest.write_text(os.path.join(report_dir, f"{name}_round2_prompt.md"), round2_prompt)
    final_text: Optional[str] = None
    final_debug = ""
    used_round2_ratio = 1.0
    for ratio in reduction_steps:
        if ratio >= 0.999:
            current_prompt = round2_prompt
        else:
            max_chars = max(7000, int(len(round2_prompt) * ratio))
            current_prompt = round2_prompt[:max_chars].rstrip()
        final_text, final_debug = digest.call_openai_text_detailed(
            model=model,
            api_key=api_key,
            api_base=api_base,
            system_prompt=system_prompt,
            user_prompt=current_prompt,
            timeout=120,
            max_output_tokens=max_output_tokens,
            temperature=0.2 if "landscape" in name else 0.35,
        )
        if final_text:
            used_round2_ratio = ratio
            if current_prompt != round2_prompt:
                digest.write_text(os.path.join(report_dir, f"{name}_round2_prompt_effective.md"), current_prompt)
            break
    if not final_text:
        error_path = os.path.join(report_dir, f"{name}_round2_error.txt")
        digest.write_text(
            error_path,
            (
                f"Failed to get non-empty refined text from local LLM for {name}.\n\n"
                f"API base: {api_base}\n"
                f"Model: {model}\n"
                f"Prompt chars: {len(round2_prompt)}\n"
                f"Last debug info:\n{final_debug or '(no diagnostic details returned)'}\n"
            ),
        )
        raise RuntimeError(f"Local LLM returned empty refined output for {name}; details saved to {error_path}")
    digest.write_text(os.path.join(report_dir, f"{name}_final.md"), final_text)
    if used_round2_ratio < 0.999:
        digest.write_text(
            os.path.join(report_dir, f"{name}_round2_reduction_note.txt"),
            f"Context reduced to {int(used_round2_ratio * 100)}% of original round2 prompt to fit local model response behavior.\n",
        )
    return {
        "draft_path": os.path.join(report_dir, f"{name}_round1.md"),
        "review_path": os.path.join(report_dir, f"{name}_review.json"),
        "final_path": os.path.join(report_dir, f"{name}_final.md"),
    }


def run_local_llm_analysis(
    *,
    packet_name: str,
    report_dir: str,
    data_dir: str,
    args: argparse.Namespace,
    tta_records: List[Dict[str, object]],
    recent_cv_records: List[Dict[str, object]],
    tta_brief: str,
    bridge_brief: str,
) -> Dict[str, object]:
    model = resolve_analysis_model(args.analysis_api_base, args.analysis_model)
    api_key = digest.safe_text(args.analysis_api_key)
    analysis_dir = os.path.join(report_dir, "auto_analysis")
    os.makedirs(analysis_dir, exist_ok=True)

    tta_context_items = [compact_record_for_llm(record, mode="tta") for record in tta_records[:args.analysis_max_tta_records]]
    recent_cv_highlights = build_recent_cv_highlights(recent_cv_records, tta_records)[:args.analysis_cv_highlight_limit]
    cv_context_items = [
        {
            "id": row["arxiv_id"],
            "date": row["date_local"],
            "title": row["title"],
            "primary_task": row["primary_task"],
            "primary_domain": row["primary_domain"],
            "novel_keywords_vs_tta": row["novel_keywords_vs_tta"],
            "summary_brief_en": row["summary_brief_en"],
        }
        for row in recent_cv_highlights
    ]

    tta_context = (
        f"Packet: {packet_name}\n\n"
        "TTA landscape brief:\n"
        f"{tta_brief.strip()}\n\n"
        "Compact TTA corpus JSON:\n"
        f"{json.dumps(tta_context_items, ensure_ascii=False, indent=2)}"
    )
    tta_task = (
        "请基于以上语料，输出一份系统性的 TTA 研究版图审计报告。\n"
        "必须覆盖：主要问题、共享假设、重复设定、空白点、不合理前提、热门路线共同局限、矛盾结果、最近1-3个月变热/被忽视的问题。\n"
        "最后输出一个优先研究问题 shortlist，每个问题都要附支撑论文 ID。"
    )
    landscape_result = run_iterative_text_analysis(
        name="tta_landscape_analysis",
        model=model,
        api_base=args.analysis_api_base,
        api_key=api_key,
        context_text=tta_context,
        task_prompt=tta_task,
        report_dir=analysis_dir,
        max_output_tokens=args.analysis_max_output_tokens,
        review_output_tokens=args.analysis_review_output_tokens,
    )

    landscape_final_text = Path(landscape_result["final_path"]).read_text(encoding="utf-8")
    cross_context = (
        f"Packet: {packet_name}\n\n"
        "Final TTA landscape analysis:\n"
        f"{landscape_final_text.strip()}\n\n"
        "Cross-ideation seeds:\n"
        f"{bridge_brief.strip()}\n\n"
        "Recent CV highlights JSON:\n"
        f"{json.dumps(cv_context_items, ensure_ascii=False, indent=2)}"
    )
    cross_task = (
        "请生成一份自动化的研究方向报告，而不是随意 brainstorm。\n"
        "至少提出 12 个方向，来源必须覆盖：子领域交叉、老问题+新方法、从 limitation/fragility 出发、从常见假设反推出不满足假设时的问题。\n"
        "每个方向必须严格包含：研究问题、灵感来源、核心假设、最小实验、主要风险、预期贡献、支撑论文 ID。"
    )
    cross_result = run_iterative_text_analysis(
        name="cross_ideation_analysis",
        model=model,
        api_base=args.analysis_api_base,
        api_key=api_key,
        context_text=cross_context,
        task_prompt=cross_task,
        report_dir=analysis_dir,
        max_output_tokens=args.analysis_max_output_tokens,
        review_output_tokens=args.analysis_review_output_tokens,
    )

    summary_text = (
        f"# Auto Analysis Summary\n\n"
        f"- Packet: `{packet_name}`\n"
        f"- Backend: local LLM\n"
        f"- API Base: `{args.analysis_api_base}`\n"
        f"- Model: `{model}`\n"
        f"- Landscape Final: `{os.path.basename(landscape_result['final_path'])}`\n"
        f"- Cross Final: `{os.path.basename(cross_result['final_path'])}`\n"
        f"- Analysis Dir: `{analysis_dir}`\n"
    )
    digest.write_text(os.path.join(analysis_dir, "auto_analysis_summary.md"), summary_text)
    manifest = {
        "backend": "local",
        "api_base": args.analysis_api_base,
        "model": model,
        "analysis_dir": analysis_dir,
        "landscape": landscape_result,
        "cross_ideation": cross_result,
        "summary_path": os.path.join(analysis_dir, "auto_analysis_summary.md"),
    }
    digest.dump_json(os.path.join(data_dir, "auto_analysis_manifest.json"), manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Research workbench for TTA landscape analysis and cross-ideation.")
    parser.add_argument("--date", type=str, default="")
    parser.add_argument("--tz", type=str, default=os.environ.get("DIGEST_TZ", "Asia/Shanghai"))
    parser.add_argument("--categories", type=str, default=os.environ.get("ARXIV_CATEGORIES", "cs.CV"))
    parser.add_argument("--tta-days", type=int, default=int(os.environ.get("RESEARCH_TTA_DAYS", "90")))
    parser.add_argument("--cv-recent-days", type=int, default=int(os.environ.get("RESEARCH_CV_RECENT_DAYS", "2")))
    parser.add_argument("--page-size", type=int, default=int(os.environ.get("ARXIV_PAGE_SIZE", "200")))
    parser.add_argument("--max-scan", type=int, default=int(os.environ.get("ARXIV_MAX_SCAN", "5000")))
    parser.add_argument("--tta-threshold", type=int, default=int(os.environ.get("RESEARCH_TTA_THRESHOLD", "7")))
    parser.add_argument("--tta-limit", type=int, default=int(os.environ.get("RESEARCH_TTA_LIMIT", "-1")))
    parser.add_argument("--cv-limit", type=int, default=int(os.environ.get("RESEARCH_CV_LIMIT", "-1")))
    parser.add_argument("--translate-backend", type=str, default=os.environ.get("RESEARCH_TRANSLATE_BACKEND", "none"), choices=["none", "google", "llm", "auto"])
    parser.add_argument("--translate-limit", type=int, default=int(os.environ.get("RESEARCH_TRANSLATE_LIMIT", "-1")))
    parser.add_argument("--google-timeout", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_TIMEOUT_SECONDS", "12")))
    parser.add_argument("--google-summary-sentences", type=int, default=int(os.environ.get("GOOGLE_SUMMARY_SENTENCES", "3")))
    parser.add_argument("--google-full-abstract", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_FULL_ABSTRACT", "0")))
    parser.add_argument("--model", type=str, default=os.environ.get("TRANSLATE_MODEL", os.environ.get("KIMI_MODEL", "moonshot-v1-8k")))
    parser.add_argument("--api-base", type=str, default=os.environ.get("OPENAI_BASE_URL", os.environ.get("KIMI_API_BASE", digest.DEFAULT_API_BASE)))
    parser.add_argument("--llm-max-retries", type=int, default=int(os.environ.get("LLM_MAX_RETRIES", "2")))
    parser.add_argument("--llm-failed-cooldown-hours", dest="llm_failed_cooldown_hours", type=int, default=int(os.environ.get("LLM_FAILED_COOLDOWN_HOURS", "24")))
    parser.add_argument("--llm-timeout", type=int, default=int(os.environ.get("LLM_TIMEOUT_SECONDS", "25")))
    parser.add_argument("--output-suffix", type=str, default=os.environ.get("RESEARCH_OUTPUT_SUFFIX", ""))
    parser.add_argument("--report-root", type=str, default="reports/research_workbench")
    parser.add_argument("--data-root", type=str, default="data/research_workbench")
    parser.add_argument("--analysis-backend", type=str, default=os.environ.get("RESEARCH_ANALYSIS_BACKEND", "none"), choices=["none", "local"])
    parser.add_argument("--analysis-api-base", type=str, default=os.environ.get("LOCAL_LLM_API_BASE", "http://localhost:8080"))
    parser.add_argument("--analysis-api-key", type=str, default=os.environ.get("LOCAL_LLM_API_KEY", ""))
    parser.add_argument("--analysis-model", type=str, default=os.environ.get("LOCAL_LLM_MODEL", "auto"))
    parser.add_argument("--analysis-max-tta-records", type=int, default=int(os.environ.get("RESEARCH_ANALYSIS_MAX_TTA_RECORDS", "80")))
    parser.add_argument("--analysis-cv-highlight-limit", type=int, default=int(os.environ.get("RESEARCH_ANALYSIS_CV_HIGHLIGHT_LIMIT", "40")))
    parser.add_argument("--analysis-max-output-tokens", type=int, default=int(os.environ.get("RESEARCH_ANALYSIS_MAX_OUTPUT_TOKENS", "2400")))
    parser.add_argument("--analysis-review-output-tokens", type=int, default=int(os.environ.get("RESEARCH_ANALYSIS_REVIEW_OUTPUT_TOKENS", "900")))
    parser.add_argument("--skip-fetch", type=int, default=int(os.environ.get("RESEARCH_SKIP_FETCH", "0")), choices=[0, 1])
    parser.add_argument("--reuse-packet", type=str, default=os.environ.get("RESEARCH_REUSE_PACKET", ""))
    args = parser.parse_args()

    if args.date:
        target_day = dt.date.fromisoformat(args.date)
    else:
        target_day = dt.datetime.now(ZoneInfo(args.tz)).date()

    categories = parse_categories(args.categories) or ["cs.CV"]
    packet_name = normalize_packet_name(target_day, args.tta_days, args.cv_recent_days, args.output_suffix)
    active_packet_name = digest.safe_text(args.reuse_packet) or packet_name
    report_dir = os.path.join(args.report_root, active_packet_name)
    data_dir = os.path.join(args.data_root, active_packet_name)
    os.makedirs(report_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    print(f"[INFO] Packet: {active_packet_name}")

    manifest_meta: Dict[str, object] = {}
    if args.skip_fetch:
        print("[INFO] Skip fetch enabled: reusing existing packet data for downstream analysis only.")
        tta_records, recent_cv_records, manifest_meta = load_existing_packet_records(data_dir)
        source_tta_days = int(manifest_meta.get("tta_days", args.tta_days) or args.tta_days)
        source_cv_recent_days = int(manifest_meta.get("cv_recent_days", args.cv_recent_days) or args.cv_recent_days)
    else:
        print(f"[INFO] Fetching categories: {', '.join(categories)}")
        tta_source_papers = fetch_category_range(
            categories=categories,
            target_day=target_day,
            tz_name=args.tz,
            page_size=args.page_size,
            max_scan=args.max_scan,
            day_window_days=args.tta_days,
        )
        recent_cv_papers = fetch_category_range(
            categories=categories,
            target_day=target_day,
            tz_name=args.tz,
            page_size=args.page_size,
            max_scan=args.max_scan,
            day_window_days=args.cv_recent_days,
        )

        union_for_translation = digest.dedupe_papers(tta_source_papers + recent_cv_papers)
        apply_optional_translation(union_for_translation, args=args, data_dir="data")

        tta_records = limit_records(
            filter_tta_records(tta_source_papers, target_day=target_day, tz_name=args.tz, threshold=args.tta_threshold),
            args.tta_limit,
        )
        recent_cv_records = limit_records(
            [paper_record(paper, target_day=target_day, tz_name=args.tz) for paper in recent_cv_papers],
            args.cv_limit,
        )
        source_tta_days = args.tta_days
        source_cv_recent_days = args.cv_recent_days

    tta_brief = build_tta_brief(
        tta_records,
        tta_days=source_tta_days,
        trend_bucket_days=max(10, source_tta_days // 3),
    )
    bridge_brief = build_bridge_brief(tta_records, recent_cv_records)
    project_instructions = build_project_instructions()
    prompt_tta = build_prompt_tta_landscape(active_packet_name)
    prompt_cross = build_prompt_cross_ideation(active_packet_name)
    quickstart = build_quickstart_manifest(active_packet_name, report_dir=report_dir, data_dir=data_dir)

    digest.write_text(os.path.join(report_dir, "tta_corpus.md"), render_corpus_markdown("TTA Corpus", tta_records))
    digest.write_text(os.path.join(report_dir, "cv_recent_corpus.md"), render_corpus_markdown("Recent CV Corpus", recent_cv_records))
    digest.write_text(os.path.join(report_dir, "tta_landscape_brief.md"), tta_brief)
    digest.write_text(os.path.join(report_dir, "cross_ideation_seeds.md"), bridge_brief)
    digest.write_text(os.path.join(report_dir, "project_instructions.md"), project_instructions)
    digest.write_text(os.path.join(report_dir, "prompt_tta_landscape.md"), prompt_tta)
    digest.write_text(os.path.join(report_dir, "prompt_cross_ideation.md"), prompt_cross)
    digest.write_text(os.path.join(report_dir, "quickstart.md"), quickstart)

    digest.dump_json(os.path.join(data_dir, "tta_papers.json"), {"items": tta_records})
    digest.dump_json(os.path.join(data_dir, "cv_recent_papers.json"), {"items": recent_cv_records})
    write_csv(os.path.join(data_dir, "tta_papers.csv"), tta_records)
    write_csv(os.path.join(data_dir, "cv_recent_papers.csv"), recent_cv_records)

    manifest = build_packet_manifest(
        packet_name=active_packet_name,
        target_day=target_day,
        args=args,
        tta_records=tta_records,
        recent_cv_records=recent_cv_records,
        report_dir=report_dir,
        data_dir=data_dir,
    )
    manifest["skip_fetch"] = bool(args.skip_fetch)
    manifest["reuse_packet"] = digest.safe_text(args.reuse_packet)
    manifest["source_tta_days"] = source_tta_days
    manifest["source_cv_recent_days"] = source_cv_recent_days

    auto_analysis_manifest = None
    if args.analysis_backend == "local":
        auto_analysis_manifest = run_local_llm_analysis(
            packet_name=active_packet_name,
            report_dir=report_dir,
            data_dir=data_dir,
            args=args,
            tta_records=tta_records,
            recent_cv_records=recent_cv_records,
            tta_brief=tta_brief,
            bridge_brief=bridge_brief,
        )
        manifest["auto_analysis_manifest"] = os.path.join(data_dir, "auto_analysis_manifest.json")

    digest.dump_json(os.path.join(data_dir, "packet_manifest.json"), manifest)

    print(f"[OK] TTA papers: {len(tta_records)}")
    print(f"[OK] Recent CV papers: {len(recent_cv_records)}")
    print(f"[OK] Report dir: {report_dir}")
    print(f"[OK] Data dir: {data_dir}")
    print(f"[OK] Quickstart: {os.path.join(report_dir, 'quickstart.md')}")
    print(f"[OK] Project instructions: {os.path.join(report_dir, 'project_instructions.md')}")
    print(f"[OK] Prompt 1: {os.path.join(report_dir, 'prompt_tta_landscape.md')}")
    print(f"[OK] Prompt 2: {os.path.join(report_dir, 'prompt_cross_ideation.md')}")
    if auto_analysis_manifest:
        print(f"[OK] Auto analysis summary: {auto_analysis_manifest['summary_path']}")
        print(f"[OK] Auto TTA final: {auto_analysis_manifest['landscape']['final_path']}")
        print(f"[OK] Auto Cross final: {auto_analysis_manifest['cross_ideation']['final_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
