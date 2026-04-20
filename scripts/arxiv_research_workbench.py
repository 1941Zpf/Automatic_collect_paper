#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import arxiv_daily_digest as digest


METHOD_FAMILY_OPTIONS = [
    "representation alignment",
    "structural prior / graph / geometry",
    "prompting / conditioning",
    "memory / retrieval / prototype",
    "uncertainty / calibration",
    "self-training / pseudo-labeling",
    "parameter-efficient adaptation",
    "streaming / online update",
    "optimization / routing / assignment",
    "data synthesis / augmentation / generation",
    "evaluation / diagnosis / robustness probe",
    "system / efficiency mechanism",
    "other",
]

DECISION_VALUES = {"keep", "maybe", "reject"}
CONFIDENCE_VALUES = {"high", "medium", "low"}
EVIDENCE_TYPES = {"abstract_supported", "inferred"}

METHOD_FAMILY_LABELS = {
    "representation alignment": "表征对齐",
    "structural prior / graph / geometry": "结构先验 / 图结构 / 几何约束",
    "prompting / conditioning": "提示与条件控制",
    "memory / retrieval / prototype": "记忆 / 检索 / 原型机制",
    "uncertainty / calibration": "不确定性 / 校准",
    "self-training / pseudo-labeling": "自训练 / 伪标签",
    "parameter-efficient adaptation": "参数高效适配",
    "streaming / online update": "流式 / 在线更新",
    "optimization / routing / assignment": "优化 / 路由 / 分配",
    "data synthesis / augmentation / generation": "数据合成 / 增强 / 生成",
    "evaluation / diagnosis / robustness probe": "评测 / 诊断 / 鲁棒性探测",
    "system / efficiency mechanism": "系统 / 效率机制",
    "other": "其他",
}

DECISION_LABELS = {
    "keep": "建议保留",
    "maybe": "待验证",
    "reject": "不建议迁移",
}

CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

EVIDENCE_TYPE_LABELS = {
    "abstract_supported": "摘要直接支持",
    "inferred": "基于摘要推断",
}

PRIORITY_LABELS = {
    "high": "高优先级",
    "medium": "中优先级",
    "low": "低优先级",
}

BACKEND_LABELS = {
    "prepare-only": "仅整理材料",
    "Kimi/OpenAI-compatible": "Kimi 接口分析",
    "OpenRouter/OpenAI-compatible": "OpenRouter 接口分析",
}

TRANSFER_MARKER = "[[[TRANSFER_NOTE]]]"

SECTION_LABELS = {
    "target_tasks": "聚焦任务",
    "shared_assumptions": "共享假设",
    "recurring_setups": "重复设定",
    "main_bottlenecks": "主要瓶颈",
    "underexplored_needs": "尚未充分覆盖的需求",
    "external_capability_gaps": "外部能力缺口",
    "focus_method_patterns": "聚焦领域常见方法模式",
}

BOOLEAN_LABELS = {
    True: "是",
    False: "否",
}

METHOD_FAMILY_PATTERNS = {
    "representation alignment": [
        r"\balign(?:ment)?\b",
        r"\bcontrastive\b",
        r"\bconsisten(?:cy|t)\b",
        r"\bdisentangle",
        r"\bsemantic alignment\b",
    ],
    "structural prior / graph / geometry": [
        r"\bgraph\b",
        r"\bhypergraph\b",
        r"\bgeometry\b",
        r"\bgeometric\b",
        r"\btopolog",
        r"\b3d\b",
        r"\bstructur",
    ],
    "prompting / conditioning": [
        r"\bprompt",
        r"\bcontext\b",
        r"\bconditioning\b",
        r"\binstruction\b",
        r"\btoken\b",
    ],
    "memory / retrieval / prototype": [
        r"\bmemory\b",
        r"\bretriev",
        r"\bprototype\b",
        r"\bbank\b",
        r"\bexemplar\b",
    ],
    "uncertainty / calibration": [
        r"\buncertaint",
        r"\bconfidence\b",
        r"\bcalibration\b",
        r"\bentropy\b",
        r"\brisk\b",
    ],
    "self-training / pseudo-labeling": [
        r"\bpseudo\b",
        r"\bself[- ]train",
        r"\bself[- ]supervis",
        r"\bconsistency regularization\b",
    ],
    "parameter-efficient adaptation": [
        r"\badapter\b",
        r"\blora\b",
        r"\bparameter[- ]efficient\b",
        r"\blightweight\b",
        r"\bdelta tuning\b",
    ],
    "streaming / online update": [
        r"\bstream",
        r"\bonline\b",
        r"\bcontinual\b",
        r"\bsequential\b",
        r"\breal[- ]time\b",
    ],
    "optimization / routing / assignment": [
        r"\boptim",
        r"\brouting\b",
        r"\bassign",
        r"\bsinkhorn\b",
        r"\btransport\b",
        r"\bmixture[- ]of[- ]experts\b",
    ],
    "data synthesis / augmentation / generation": [
        r"\bdiffusion\b",
        r"\bgenerat",
        r"\bsynthes",
        r"\baugment",
        r"\bsimulation\b",
    ],
    "evaluation / diagnosis / robustness probe": [
        r"\bbenchmark\b",
        r"\banalys",
        r"\bdiagnos",
        r"\bprobe\b",
        r"\bforgetting\b",
        r"\bbias\b",
        r"\bred team",
    ],
    "system / efficiency mechanism": [
        r"\befficient\b",
        r"\blatency\b",
        r"\bcompression\b",
        r"\bscalab",
        r"\bsystem\b",
        r"\bdeployment\b",
        r"\bruntime\b",
    ],
}


def h(text: object) -> str:
    if text is None:
        return ""
    return html.escape(digest.safe_text(str(text)), quote=True)


def non_other(items: Iterable[object]) -> List[str]:
    out: List[str] = []
    for item in items:
        text = digest.safe_text(str(item))
        if not text:
            continue
        lower = text.lower()
        if lower in {"other", "unknown", "none"}:
            continue
        if text not in out:
            out.append(text)
    return out


def joined(items: Iterable[object], fallback: str = "") -> str:
    rows = non_other(items)
    return ", ".join(rows) if rows else fallback


def normalize_label_key(text: object) -> str:
    return digest.safe_text(str(text or "")).strip().lower()


def map_known_label(text: object) -> str:
    raw = digest.safe_text(str(text or "")).strip()
    if not raw:
        return ""
    key = normalize_label_key(raw)
    if key in METHOD_FAMILY_LABELS:
        return METHOD_FAMILY_LABELS[key]
    if key in DECISION_LABELS:
        return DECISION_LABELS[key]
    if key in CONFIDENCE_LABELS:
        return CONFIDENCE_LABELS[key]
    if key in EVIDENCE_TYPE_LABELS:
        return EVIDENCE_TYPE_LABELS[key]
    if key in PRIORITY_LABELS:
        return PRIORITY_LABELS[key]
    if key in BACKEND_LABELS:
        return BACKEND_LABELS[key]
    if key in {"other", "unknown"}:
        return "其他"
    if key in {"none", "null"}:
        return "无"
    if key in {"pass"}:
        return "通过"
    if key in {"check", "pending"}:
        return "待检查"
    return raw


def needs_report_translation(text: object) -> bool:
    raw = digest.safe_text(str(text or "")).strip()
    if not raw:
        return False
    if not re.search(r"[A-Za-z]", raw):
        return False
    if re.fullmatch(r"https?://\S+", raw):
        return False
    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", raw):
        return False
    if re.fullmatch(r"[A-Za-z0-9._:/-]+", raw) and ("/" in raw or raw.startswith("cs.") or raw.startswith("arxiv")):
        return False
    return True


def load_translation_cache(path: str) -> Dict[str, str]:
    if not path or not os.path.exists(path):
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, str] = {}
    for key, value in payload.items():
        clean_key = digest.safe_text(str(key))
        clean_value = digest.safe_text(str(value))
        if clean_key:
            out[clean_key] = clean_value or clean_key
    return out


def translate_texts_for_report(texts: Iterable[object], cache_path: str, timeout: int = 12, log_label: str = "") -> Dict[str, str]:
    unique_texts: List[str] = []
    seen: set[str] = set()
    for item in texts:
        raw = digest.safe_text(str(item or "")).strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        unique_texts.append(raw)

    cache = load_translation_cache(cache_path)
    pending: List[str] = []
    for raw in unique_texts:
        if raw in cache and digest.safe_text(cache[raw]):
            continue
        mapped = map_known_label(raw)
        if mapped != raw:
            cache[raw] = mapped
            continue
        if not needs_report_translation(raw):
            cache[raw] = raw
            continue
        pending.append(raw)

    skip_remote_translation = os.environ.get("FOCUS_TRANSFER_SKIP_HTML_TRANSLATION", "").strip().lower() in {"1", "true", "yes", "on"}
    if pending and skip_remote_translation:
        if log_label:
            print(f"[INFO] {log_label}: {len(unique_texts) - len(pending)}/{len(unique_texts)} cached, skip remote translation for {len(pending)} texts")
        for raw in pending:
            cache[raw] = raw
        digest.dump_json(cache_path, cache)
    elif pending:
        if log_label:
            print(f"[INFO] {log_label}: {len(unique_texts) - len(pending)}/{len(unique_texts)} cached, {len(pending)} texts pending translation")
        translated_rows: List[str] = []
        try:
            translated_rows = digest.google_translate_texts(pending, timeout=timeout, progress_label=log_label or "HTML translation")
        except Exception:
            translated_rows = [""] * len(pending)
        for raw, translated in zip(pending, translated_rows):
            clean_translated = digest.safe_text(str(translated or "")).strip()
            cache[raw] = clean_translated or raw
        digest.dump_json(cache_path, cache)
        if log_label:
            print(f"[INFO] {log_label}: translation cache updated -> {cache_path}")
    else:
        if cache_path and (not os.path.exists(cache_path)):
            digest.dump_json(cache_path, cache)
        if log_label and unique_texts:
            print(f"[INFO] {log_label}: all {len(unique_texts)} texts already cached")

    return {raw: digest.safe_text(cache.get(raw, raw)) or raw for raw in unique_texts}


def localize_text(value: object, translation_map: Dict[str, str], fallback: str = "") -> str:
    raw = digest.safe_text(str(value or "")).strip()
    if not raw:
        return fallback
    mapped = map_known_label(raw)
    if mapped != raw:
        return mapped
    return digest.safe_text(translation_map.get(raw, raw)) or fallback or raw


def localize_items(items: Iterable[object], translation_map: Dict[str, str], fallback: str = "") -> List[str]:
    out: List[str] = []
    for item in items:
        raw = digest.safe_text(str(item or "")).strip()
        if not raw:
            continue
        localized = localize_text(raw, translation_map)
        clean = digest.safe_text(localized).strip()
        if not clean:
            continue
        if normalize_label_key(clean) in {"other", "其他", "none", "无", "unknown"}:
            continue
        if clean not in out:
            out.append(clean)
    if out:
        return out
    return [fallback] if fallback else []


def joined_localized(items: Iterable[object], translation_map: Dict[str, str], fallback: str = "无") -> str:
    rows = localize_items(items, translation_map)
    return "、".join(rows) if rows else fallback


def paper_id(record: Dict[str, object]) -> str:
    return digest.normalize_arxiv_id(
        str(record.get("arxiv_id") or record.get("link_abs") or record.get("link_pdf") or record.get("title") or "")
    )


def paper_hash(record: Dict[str, object]) -> str:
    payload = {
        "id": paper_id(record),
        "title": digest.safe_text(str(record.get("title", ""))),
        "summary_en": digest.safe_text(str(record.get("summary_en", ""))),
        "summary_zh": digest.safe_text(str(record.get("summary_zh", ""))),
        "domain_tags": non_other(record.get("domain_tags", []) or []),
        "task_tags": non_other(record.get("task_tags", []) or []),
        "type_tags": non_other(record.get("type_tags", []) or []),
        "keywords": non_other(record.get("keywords", []) or []),
        "accepted_venue": digest.safe_text(str(record.get("accepted_venue", ""))),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_output_suffix(text: str) -> str:
    return digest.normalize_output_suffix(text)


def normalize_packet_component(text: str, max_len: int = 64) -> str:
    clean = digest.safe_text(text)
    if not clean:
        return ""
    clean = clean.replace(" ", "-")
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", clean)
    clean = re.sub(r"-{2,}", "-", clean)
    clean = clean.strip("._-")
    return clean[:max_len]


def build_packet_name(payload: Dict[str, object], digest_json_path: str, suffix: str) -> str:
    note_suffix = normalize_packet_component(str((payload.get("notes") or {}).get("output_suffix", "")))
    digest_stem = Path(digest_json_path).stem
    digest_stem = re.sub(r"^arxiv_digest_", "", digest_stem)
    base_raw = digest_stem if digest_stem and digest_stem != "last_success_digest" else digest.safe_text(str(payload.get("date", "digest")))
    base = normalize_packet_component(base_raw, max_len=96)
    focus_terms = non_other((payload.get("notes") or {}).get("focus_terms", []) or [])
    focus_sig = hashlib.sha1(",".join(term.lower() for term in focus_terms).encode("utf-8")).hexdigest()[:8] if focus_terms else "nofocus"
    parts = [base]
    if note_suffix and note_suffix not in base:
        parts.append(note_suffix)
    parts.extend(["focus-transfer", focus_sig])
    extra = normalize_packet_component(suffix)
    if extra:
        parts.append(extra)
    packet_name = "_".join(part for part in parts if part)
    return packet_name or f"focus_transfer_{focus_sig}"


def record_blob(record: Dict[str, object]) -> str:
    text = " ".join(
        [
            str(record.get("title", "")),
            str(record.get("summary_en", "")),
            str(record.get("summary_zh", "")),
            str(record.get("comment", "")),
            str(record.get("journal_ref", "")),
            " ".join(record.get("categories", []) or []),
            " ".join(record.get("domain_tags", []) or []),
            " ".join(record.get("task_tags", []) or []),
            " ".join(record.get("type_tags", []) or []),
            " ".join(record.get("keywords", []) or []),
        ]
    )
    return digest.safe_text(text).lower()


def build_focus_rule_rows(focus_terms: List[str]) -> List[Tuple[str, re.Pattern[str]]]:
    rows: List[Tuple[str, re.Pattern[str]]] = []
    seen: set[Tuple[str, str]] = set()
    for term in focus_terms:
        canonical = digest.normalize_focus_term(term)
        if not canonical:
            continue
        for variant in digest.focus_term_variants(canonical):
            pattern = digest.rule_term_pattern(variant)
            key = (canonical, pattern.pattern)
            if key in seen:
                continue
            seen.add(key)
            rows.append((canonical, pattern))
    return rows


def focus_hits_for_record(record: Dict[str, object], focus_rule_rows: List[Tuple[str, re.Pattern[str]]]) -> List[str]:
    blob = record_blob(record)
    hits: List[str] = []
    for canonical, pattern in focus_rule_rows:
        if pattern.search(blob):
            if canonical not in hits:
                hits.append(canonical)
    return hits


def dedupe_records(records: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        pid = paper_id(record)
        if not pid:
            fallback = digest.safe_text(str(record.get("title", "")))
            if not fallback:
                continue
            pid = f"title:{fallback.lower()}"
        if pid in seen:
            continue
        seen.add(pid)
        out.append(dict(record))
    return out


def collect_all_digest_papers(payload: Dict[str, object]) -> List[Dict[str, object]]:
    daily_groups = payload.get("daily_groups", {}) or {}
    merged: List[Dict[str, object]] = []
    if isinstance(daily_groups, dict):
        for rows in daily_groups.values():
            if isinstance(rows, list):
                merged.extend([row for row in rows if isinstance(row, dict)])
    for key in ("focus_latest", "focus_hot", "venue_pool", "venue_watch"):
        rows = payload.get(key, []) or []
        if isinstance(rows, list):
            merged.extend([row for row in rows if isinstance(row, dict)])
    return dedupe_records(merged)


def guess_source_field(record: Dict[str, object]) -> str:
    domain_tags = non_other(record.get("domain_tags", []) or [])
    task_tags = non_other(record.get("task_tags", []) or [])
    keywords = [kw for kw in non_other(record.get("keywords", []) or []) if kw.upper() not in digest.CANONICAL_SIGNAL_LABELS]
    if len(domain_tags) >= 2:
        return f"{domain_tags[0]} + {domain_tags[1]}"
    if domain_tags:
        return domain_tags[0]
    if task_tags:
        return task_tags[0]
    if keywords:
        return keywords[0]
    return digest.safe_text(str(record.get("major_area", ""))) or "other"


def guess_method_families(record: Dict[str, object]) -> List[str]:
    blob = record_blob(record)
    hits: List[str] = []
    for label, patterns in METHOD_FAMILY_PATTERNS.items():
        if any(re.search(pattern, blob, flags=re.I) for pattern in patterns):
            hits.append(label)
    return hits[:3] if hits else ["other"]


def brief_summary_en(record: Dict[str, object], max_chars: int = 420) -> str:
    return digest.select_summary_sentences(
        str(record.get("title", "")),
        str(record.get("summary_en", "")),
        hint_terms=non_other(record.get("domain_tags", []) or []) + non_other(record.get("task_tags", []) or []),
        max_sentences=3,
        max_chars=max_chars,
    )


def accepted_rank_for_record(record: Dict[str, object]) -> int:
    try:
        return digest.accepted_rank(digest.paper_from_dict(record))
    except Exception:
        text = " ".join(
            [
                str(record.get("accepted_venue", "")),
                str(record.get("accepted_hint", "")),
                str(record.get("source_venue", "")),
                str(record.get("comment", "")),
                str(record.get("journal_ref", "")),
            ]
        ).lower()
        if "accepted" in text or "accept" in text:
            return 3
        if "to appear" in text or "published" in text:
            return 2
        if digest.safe_text(str(record.get("accepted_venue", ""))):
            return 1
        source_platform = digest.safe_text(str(record.get("source_platform", ""))).lower()
        if digest.safe_text(str(record.get("source_venue", ""))) and source_platform and source_platform != "arxiv":
            return 1
        return 0


def accepted_label_for_record(record: Dict[str, object]) -> str:
    try:
        return digest.paper_signal_label(digest.paper_from_dict(record))
    except Exception:
        return digest.safe_text(str(record.get("accepted_venue", "") or record.get("source_venue", "")))


def paper_sort_key(record: Dict[str, object]) -> Tuple[str, int, str]:
    published = digest.safe_text(str(record.get("published", "")))
    title = digest.safe_text(str(record.get("title", "")))
    return (published, accepted_rank_for_record(record), title)


def prepare_focus_transfer_inputs(payload: Dict[str, object]) -> Dict[str, object]:
    focus_terms = digest.normalize_focus_terms(non_other((payload.get("notes") or {}).get("focus_terms", []) or []))
    all_records = collect_all_digest_papers(payload)
    focus_pool = dedupe_records((payload.get("focus_latest", []) or []) + (payload.get("focus_hot", []) or []))
    focus_pool_ids = {paper_id(record) for record in focus_pool if paper_id(record)}
    focus_rule_rows = build_focus_rule_rows(focus_terms)

    focus_records: List[Dict[str, object]] = []
    non_focus_records: List[Dict[str, object]] = []
    extra_focus_count = 0

    for raw_record in all_records:
        record = dict(raw_record)
        pid = paper_id(record)
        hits = focus_hits_for_record(record, focus_rule_rows)
        record["focus_term_hits"] = hits
        record["source_field_guess"] = guess_source_field(record)
        record["method_family_guess"] = guess_method_families(record)
        record["summary_brief_en"] = brief_summary_en(record)
        record["accepted_rank"] = accepted_rank_for_record(record)
        record["accepted_signal_label"] = accepted_label_for_record(record) if record["accepted_rank"] > 0 else ""
        in_focus_pool = pid in focus_pool_ids
        if in_focus_pool or bool(hits):
            record["focus_reason"] = "focus_pool" if in_focus_pool else "focus_matcher"
            if not in_focus_pool and hits:
                extra_focus_count += 1
            focus_records.append(record)
        else:
            non_focus_records.append(record)

    focus_records = dedupe_records(sorted(focus_records, key=paper_sort_key, reverse=True))
    non_focus_records = dedupe_records(sorted(non_focus_records, key=paper_sort_key, reverse=True))
    transfer_candidate_records = [record for record in non_focus_records if accepted_rank_for_record(record) > 0]

    return {
        "focus_terms": focus_terms,
        "all_records": all_records,
        "focus_records": focus_records,
        "non_focus_records": non_focus_records,
        "transfer_candidate_records": transfer_candidate_records,
        "transfer_candidate_scope": "accepted_non_focus",
        "extra_focus_count": extra_focus_count,
        "daily_count": sum(len(rows or []) for rows in (payload.get("daily_groups", {}) or {}).values()),
        "focus_pool_count": len(focus_pool),
        "focus_total_count": len(focus_records),
        "non_focus_total_count": len(non_focus_records),
        "transfer_candidate_total_count": len(transfer_candidate_records),
    }


def compact_record_for_llm(record: Dict[str, object]) -> Dict[str, object]:
    return {
        "paper_id": paper_id(record),
        "title": digest.safe_text(str(record.get("title", ""))),
        "title_zh": digest.safe_text(str(record.get("title_zh", ""))),
        "published": digest.safe_text(str(record.get("published", ""))),
        "accepted_venue": digest.safe_text(str(record.get("accepted_venue", ""))),
        "accepted_signal_label": digest.safe_text(str(record.get("accepted_signal_label", ""))) or accepted_label_for_record(record),
        "accepted_rank": accepted_rank_for_record(record),
        "major_area": digest.safe_text(str(record.get("major_area", ""))),
        "domain_tags": non_other(record.get("domain_tags", []) or []),
        "task_tags": non_other(record.get("task_tags", []) or []),
        "type_tags": non_other(record.get("type_tags", []) or []),
        "keywords": non_other(record.get("keywords", []) or []),
        "focus_term_hits": non_other(record.get("focus_term_hits", []) or []),
        "source_field_guess": digest.safe_text(str(record.get("source_field_guess", ""))),
        "method_family_guess": non_other(record.get("method_family_guess", []) or []),
        "summary_brief_en": digest.safe_text(str(record.get("summary_brief_en", ""))),
        "link_abs": digest.safe_text(str(record.get("link_abs", ""))),
    }


def render_corpus_markdown(title: str, records: List[Dict[str, object]]) -> str:
    out = [f"# {title}", ""]
    for index, record in enumerate(records, start=1):
        out.append(f"## {index}. [{paper_id(record)}] {digest.safe_text(str(record.get('title', '')))}")
        out.append(f"- 中文标题：{digest.safe_text(str(record.get('title_zh', '')))}")
        out.append(f"- 发布时间：{digest.safe_text(str(record.get('published', '')))}")
        out.append(f"- 会议线索：{digest.safe_text(str(record.get('accepted_venue', ''))) or '无'}")
        out.append(f"- 领域标签：{joined(record.get('domain_tags', []) or [], '无')}")
        out.append(f"- 任务标签：{joined(record.get('task_tags', []) or [], '无')}")
        out.append(f"- 类型标签：{joined(record.get('type_tags', []) or [], '无')}")
        out.append(f"- 关键词：{joined(record.get('keywords', []) or [], '无')}")
        if record.get("focus_term_hits"):
            out.append(f"- 命中聚焦词：{joined(record.get('focus_term_hits', []) or [], '无')}")
        out.append(f"- 来源领域猜测：{digest.safe_text(str(record.get('source_field_guess', ''))) or '其他'}")
        out.append(f"- 方法路线猜测：{joined(record.get('method_family_guess', []) or [], '其他')}")
        out.append(f"- 英文摘要压缩：{digest.safe_text(str(record.get('summary_brief_en', '')))}")
        out.append(f"- 原始摘要：{digest.safe_text(str(record.get('summary_en', '')))}")
        out.append(f"- 链接：{digest.safe_text(str(record.get('link_abs', '')))}")
        out.append("")
    return "\n".join(out).strip() + "\n"


def truncate_text_middle(text: str, max_chars: int) -> str:
    clean = str(text or "")
    if max_chars <= 0 or len(clean) <= max_chars:
        return clean
    head = max_chars // 2
    tail = max_chars - head - 48
    return clean[:head].rstrip() + "\n\n...[中间内容已截断]...\n\n" + clean[-max(0, tail):].lstrip()


def focus_memory_slug(term: str) -> str:
    canonical = digest.normalize_focus_term(term)
    base = normalize_packet_component(canonical, max_len=72).lower()
    sig = hashlib.sha1(canonical.lower().encode("utf-8")).hexdigest()[:8] if canonical else "nofocus"
    return f"{base or 'focus'}_{sig}"


def focus_memory_path(memory_dir: str, term: str) -> str:
    return os.path.join(memory_dir, f"{focus_memory_slug(term)}.md")


def focus_memory_json_path(memory_dir: str, term: str) -> str:
    return os.path.join(memory_dir, f"{focus_memory_slug(term)}.json")


def focus_records_for_term(
    term: str,
    focus_records: List[Dict[str, object]],
    *,
    allow_single_term_fallback: bool = False,
) -> List[Dict[str, object]]:
    canonical = digest.normalize_focus_term(term)
    matched: List[Dict[str, object]] = []
    for record in focus_records:
        hits = [digest.normalize_focus_term(str(item)) for item in non_other(record.get("focus_term_hits", []) or [])]
        if canonical and canonical in hits:
            matched.append(record)
    if not matched and allow_single_term_fallback:
        return focus_records[:]
    return matched


def build_focus_memory_update_prompt(
    *,
    packet_name: str,
    focus_term: str,
    existing_memory_md: str,
    term_records: List[Dict[str, object]],
    other_focus_records: List[Dict[str, object]],
    landscape_topic: Optional[Dict[str, object]],
) -> str:
    corpus = [compact_record_for_llm(record) for record in term_records]
    neighbor_corpus = [compact_record_for_llm(record) for record in other_focus_records[:24]]
    current_paper_ids = [paper_id(record) for record in term_records if paper_id(record)]
    schema = {
        "focus_term": "string",
        "task_definition": "string",
        "technical_routes": [
            {
                "route_name": "string",
                "summary": "string",
                "representative_paper_ids": ["paper_id"],
                "why_it_matters": "string",
            }
        ],
        "motivations_and_inspirations": ["string"],
        "development_trends_and_hotspots": ["string"],
        "open_questions": ["string"],
        "current_representative_paper_ids": ["paper_id"],
        "update_summary": "string",
    }
    existing_note = existing_memory_md.strip() or "（这是首次建档，暂无历史记忆文件。）"
    topic_note = landscape_topic or {}
    return (
        f"Packet: {packet_name}\n\n"
        f"Focus term:\n{json.dumps(focus_term, ensure_ascii=False)}\n\n"
        "Existing long-term focus memory Markdown:\n"
        f"{truncate_text_middle(existing_note, 14000)}\n\n"
        "Current focus-term paper_id candidates (only these ids may appear in current_representative_paper_ids):\n"
        f"{json.dumps(current_paper_ids, ensure_ascii=False)}\n\n"
        "Current focus-term paper corpus JSON:\n"
        f"{json.dumps(corpus, ensure_ascii=False, indent=2)}\n\n"
        "Other focus-term paper corpus JSON (neighboring focus context, not the main target):\n"
        f"{json.dumps(neighbor_corpus, ensure_ascii=False, indent=2)}\n\n"
        "Current batch landscape topic JSON:\n"
        f"{json.dumps(topic_note, ensure_ascii=False, indent=2)}\n\n"
        "任务：为这个单独的 focus 词维护一份长期研究记忆。你要把“已有记忆文件”和“本次论文摘要”整合成一版更完善的结构化总结。\n"
        "要求：\n"
        "1. 输出的是完整新版内容，不是追加日志；如果已有记忆与本次论文冲突，以本次论文摘要为证据修正表述。\n"
        "2. 必须覆盖四个核心部分：任务定义、技术路线汇总、动机与启发、发展趋势与热点问题。\n"
        "3. 如果当前配置有多个 focus 词，必须把其他 focus 词论文作为相邻背景参考：用于发现共享动机、共用技术和潜在迁移启发，但不要把它们误写成当前 focus 词的直接证据。\n"
        "4. 如果这是首次建档，允许结合你对该 focus 任务的通用研究常识补足背景，但要克制，不要假装读过正文，不要编造具体实验数值。\n"
        "5. 技术路线要写成可用于后续迁移性判断的能力地图，例如“需要什么能力、常用机制、适合借鉴哪些外部方法”。\n"
        "6. `current_representative_paper_ids` 必须只从上面的 current paper_id candidates 中选择；不要把已有记忆里的历史 paper_id、也不要把邻近 focus 词的 paper_id 复制进来。如果本次该 focus 词没有论文，可以返回空数组。\n"
        "7. `technical_routes[*].representative_paper_ids` 也应优先引用本次 current focus-term paper corpus；如果需要保留历史视角，请写在 summary 里，而不是把历史 id 填进 `current_representative_paper_ids`。\n"
        "8. 每个列表控制在 4 到 10 条，避免空话；只输出 JSON 对象，不要输出 Markdown。\n\n"
        f"JSON schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )


def validate_focus_memory_profile(profile: Optional[dict], focus_term: str, term_records: List[Dict[str, object]]) -> Tuple[bool, List[str]]:
    if not isinstance(profile, dict):
        return False, ["result is not a JSON object"]
    errors: List[str] = []
    for key in ["focus_term", "task_definition", "update_summary"]:
        if not digest.safe_text(str(profile.get(key, ""))):
            errors.append(f"{key} is empty")
    for key in ["technical_routes", "motivations_and_inspirations", "development_trends_and_hotspots"]:
        rows = profile.get(key, []) or []
        if not isinstance(rows, list) or not rows:
            errors.append(f"{key} is empty")
    routes = profile.get("technical_routes", []) or []
    if isinstance(routes, list):
        for index, route in enumerate(routes, start=1):
            if not isinstance(route, dict):
                errors.append(f"technical_routes[{index}] is not an object")
                continue
            if not digest.safe_text(str(route.get("route_name", ""))):
                errors.append(f"technical_routes[{index}].route_name is empty")
            if not digest.safe_text(str(route.get("summary", ""))):
                errors.append(f"technical_routes[{index}].summary is empty")
    current_ids = {paper_id(record) for record in term_records if paper_id(record)}
    current_rep_ids = [
        digest.safe_text(str(pid))
        for pid in list(profile.get("current_representative_paper_ids", []) or [])
        if digest.safe_text(str(pid))
    ]
    if current_ids and not any(pid in current_ids for pid in current_rep_ids):
        errors.append("current_representative_paper_ids must include at least one current paper_id")
    return not errors, errors


def dedupe_texts(items: Iterable[object]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = digest.safe_text(str(item))
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def select_current_representative_paper_ids(
    term_records: List[Dict[str, object]],
    profile: Optional[Dict[str, object]] = None,
    landscape_topic: Optional[Dict[str, object]] = None,
) -> List[str]:
    current_ids = [paper_id(record) for record in term_records if paper_id(record)]
    if not current_ids:
        return []
    current_id_set = set(current_ids)
    chosen: List[str] = []
    if isinstance(profile, dict):
        chosen.extend(profile.get("current_representative_paper_ids", []) or [])
        for route in list(profile.get("technical_routes", []) or []):
            if not isinstance(route, dict):
                continue
            chosen.extend(route.get("representative_paper_ids", []) or [])
    if isinstance(landscape_topic, dict):
        chosen.extend(landscape_topic.get("representative_paper_ids", []) or [])
    filtered = [pid for pid in dedupe_texts(chosen) if pid in current_id_set]
    if filtered:
        return filtered[: min(4, len(current_ids))]
    return current_ids[: min(4, len(current_ids))]


def build_minimal_focus_memory_profile(
    focus_term: str,
    term_records: List[Dict[str, object]],
    landscape_topic: Optional[Dict[str, object]] = None,
    fallback_profile: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    canonical = digest.normalize_focus_term(focus_term)
    fallback = dict(fallback_profile or {}) if isinstance(fallback_profile, dict) else {}
    current_rep_ids = select_current_representative_paper_ids(term_records, fallback, landscape_topic)
    trend_summary = ""
    hot_problems: List[str] = []
    if isinstance(landscape_topic, dict):
        trend_summary = digest.safe_text(str(landscape_topic.get("trend_summary", "")))
        hot_problems = dedupe_texts(landscape_topic.get("hot_problems", []) or [])

    routes: List[Dict[str, object]] = []
    for index, record in enumerate(term_records[: max(1, min(3, len(term_records)))], start=1):
        pid = paper_id(record)
        if not pid:
            continue
        title = digest.safe_text(str(record.get("title_zh", ""))) or digest.safe_text(str(record.get("title", ""))) or pid
        method_guess = ""
        method_family = record.get("method_family_guess", []) or []
        if isinstance(method_family, list):
            method_guess = digest.safe_text(str(method_family[0])) if method_family else ""
        route_name = method_guess or f"当前批次代表路线 {index}"
        summary = (
            digest.safe_text(str(record.get("summary_zh", "")))
            or digest.safe_text(str(record.get("summary_brief_zh", "")))
            or digest.safe_text(str(record.get("summary_brief_en", "")))
            or digest.safe_text(str(record.get("summary_en", "")))
        )
        if summary:
            summary = truncate_text_middle(summary.replace("\n", " "), 180)
        else:
            summary = f"代表论文《{title}》体现了当前批次中与 {canonical} 相关的一条主要研究路线。"
        routes.append(
            {
                "route_name": route_name,
                "summary": summary,
                "representative_paper_ids": [pid],
                "why_it_matters": f"可作为当前批次 {canonical} 方向的代表性证据，用于后续迁移性判断。",
            }
        )

    motivations = dedupe_texts((fallback.get("motivations_and_inspirations", []) or []))[:6]
    if not motivations:
        motivations = [
            f"{canonical} 在真实场景中经常伴随数据条件变化，需要模型保持稳定泛化能力。",
            f"当前批次论文强调了 {canonical} 相关问题在不同任务和模态中的持续出现。",
        ]
    developments = dedupe_texts((fallback.get("development_trends_and_hotspots", []) or []) + hot_problems)[:8]
    if trend_summary and trend_summary not in developments:
        developments = [trend_summary] + developments
    if not developments:
        developments = [f"当前批次论文显示，{canonical} 仍然是值得持续跟踪的核心问题。"]
    open_questions = dedupe_texts((fallback.get("open_questions", []) or []))[:6]
    if not open_questions:
        open_questions = [
            f"如何让 {canonical} 方法在更多任务与数据条件下稳定迁移。",
            "如何在鲁棒性、效率和泛化之间取得更好的平衡。",
        ]

    task_definition = digest.safe_text(str(fallback.get("task_definition", "")))
    if not task_definition:
        task_definition = f"围绕 {canonical} 相关问题，整理当前论文中的任务设定、能力需求与可迁移技术路线。"
    update_summary = f"本次更新基于 {len(term_records)} 篇当前论文自动整理，并已强制对齐当前批次代表 paper_id。"

    return {
        "focus_term": canonical,
        "task_definition": task_definition,
        "technical_routes": routes or (fallback.get("technical_routes", []) or []),
        "motivations_and_inspirations": motivations,
        "development_trends_and_hotspots": developments,
        "open_questions": open_questions,
        "current_representative_paper_ids": current_rep_ids,
        "update_summary": update_summary,
    }


def normalize_focus_memory_profile(
    profile: Optional[Dict[str, object]],
    focus_term: str,
    term_records: List[Dict[str, object]],
    landscape_topic: Optional[Dict[str, object]] = None,
    fallback_profile: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    canonical = digest.normalize_focus_term(focus_term)
    fallback = dict(fallback_profile or {}) if isinstance(fallback_profile, dict) else {}
    normalized = dict(profile or {}) if isinstance(profile, dict) else {}
    normalized["focus_term"] = canonical

    for key in ["task_definition", "update_summary"]:
        value = digest.safe_text(str(normalized.get(key, "")))
        if not value:
            value = digest.safe_text(str(fallback.get(key, "")))
        normalized[key] = value

    for key in ["motivations_and_inspirations", "development_trends_and_hotspots", "open_questions"]:
        rows = normalized.get(key, []) or []
        if not isinstance(rows, list) or not dedupe_texts(rows):
            rows = fallback.get(key, []) or []
        normalized[key] = dedupe_texts(rows)

    routes = normalized.get("technical_routes", []) or []
    if not isinstance(routes, list) or not routes:
        routes = fallback.get("technical_routes", []) or []
    normalized_routes: List[Dict[str, object]] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        route_obj = dict(route)
        route_obj["route_name"] = digest.safe_text(str(route_obj.get("route_name", "")))
        route_obj["summary"] = digest.safe_text(str(route_obj.get("summary", "")))
        route_obj["why_it_matters"] = digest.safe_text(str(route_obj.get("why_it_matters", "")))
        route_obj["representative_paper_ids"] = dedupe_texts(route_obj.get("representative_paper_ids", []) or [])
        normalized_routes.append(route_obj)
    normalized["technical_routes"] = normalized_routes
    normalized["current_representative_paper_ids"] = select_current_representative_paper_ids(
        term_records,
        normalized,
        landscape_topic,
    )
    return normalized


def markdown_list(items: Iterable[object], fallback: str = "暂无") -> str:
    rows = [digest.safe_text(str(item)) for item in items if digest.safe_text(str(item))]
    if not rows:
        return f"- {fallback}"
    return "\n".join(f"- {row}" for row in rows)


def render_focus_memory_markdown(profile: Dict[str, object], focus_term: str, term_records: List[Dict[str, object]]) -> str:
    canonical = digest.normalize_focus_term(focus_term)
    paper_lookup = {paper_id(record): record for record in term_records if paper_id(record)}
    updated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out: List[str] = [
        f"# Focus 任务记忆：{canonical}",
        "",
        "> 这个文件由 focus-transfer 扩展自动维护。它按单个 focus 词长期复用，不按某一次 focus 组合隔离。",
        "",
        f"- 规范化 focus 词：`{canonical}`",
        f"- 最近更新：{updated_at}",
        f"- 本次整合论文数：{len(term_records)}",
        f"- 本次更新摘要：{digest.safe_text(str(profile.get('update_summary', '')))}",
        "",
        "## 任务定义",
        "",
        digest.safe_text(str(profile.get("task_definition", ""))) or "暂无。",
        "",
        "## 技术路线汇总",
        "",
    ]
    routes = profile.get("technical_routes", []) or []
    if not routes:
        out.append("- 暂无。")
    else:
        for route in routes:
            if not isinstance(route, dict):
                continue
            name = digest.safe_text(str(route.get("route_name", ""))) or "未命名路线"
            summary = digest.safe_text(str(route.get("summary", "")))
            why = digest.safe_text(str(route.get("why_it_matters", "")))
            rep_ids = [
                digest.safe_text(str(pid))
                for pid in list(route.get("representative_paper_ids", []) or [])
                if digest.safe_text(str(pid))
            ]
            rep_text = "、".join(rep_ids) if rep_ids else "暂无"
            out.append(f"- **{name}**：{summary}")
            if why:
                out.append(f"  - 迁移判断价值：{why}")
            out.append(f"  - 代表论文：{rep_text}")
    out.extend(["", "## 动机与启发", "", markdown_list(profile.get("motivations_and_inspirations", []) or []), ""])
    out.extend(["## 发展趋势与热点问题", "", markdown_list(profile.get("development_trends_and_hotspots", []) or []), ""])
    out.extend(["## 尚未充分解决的问题", "", markdown_list(profile.get("open_questions", []) or [], "暂无明确记录。"), ""])
    current_rep_ids = [
        digest.safe_text(str(pid))
        for pid in list(profile.get("current_representative_paper_ids", []) or [])
        if digest.safe_text(str(pid))
    ]
    out.extend(["## 本次代表论文", ""])
    if current_rep_ids:
        for pid in current_rep_ids:
            record = paper_lookup.get(pid, {})
            title = digest.safe_text(str(record.get("title_zh", ""))) or digest.safe_text(str(record.get("title", ""))) or pid
            url = digest.safe_text(str(record.get("link_abs", ""))) or f"https://arxiv.org/abs/{pid}"
            out.append(f"- [{title}]({url})（{pid}）")
    else:
        out.append("- 本次没有足够论文证据。")
    out.append("")
    return "\n".join(out).strip() + "\n"


def render_focus_memory_index(entries: List[Dict[str, object]]) -> str:
    out = ["# Focus 长期记忆文件索引", ""]
    if not entries:
        out.append("暂无 focus 长期记忆文件。")
        return "\n".join(out).strip() + "\n"
    for entry in entries:
        term = digest.safe_text(str(entry.get("focus_term", "")))
        path = digest.safe_text(str(entry.get("path", "")))
        updated = digest.safe_text(str(entry.get("updated", "")))
        record_count = int(entry.get("record_count", 0) or 0)
        status = digest.safe_text(str(entry.get("status", "")))
        out.append(f"- **{term}**：`{path}`")
        out.append(f"  - 状态：{status or 'unknown'}；本次论文数：{record_count}；更新时间：{updated or '未更新'}")
    out.append("")
    return "\n".join(out).strip() + "\n"


def build_focus_memory_context(entries: List[Dict[str, object]], max_chars_per_term: int = 6000) -> str:
    blocks: List[str] = []
    for entry in entries:
        term = digest.safe_text(str(entry.get("focus_term", "")))
        path = digest.safe_text(str(entry.get("path", "")))
        content = str(entry.get("content", "") or "").strip()
        if not content:
            continue
        blocks.append(
            f"## {term}\n"
            f"Memory file: {path}\n\n"
            f"{truncate_text_middle(content, max_chars_per_term)}"
        )
    if not blocks:
        return "（暂无可用的 focus 长期记忆文件；请只基于本次 focus landscape 和候选论文摘要判断。）"
    return "\n\n---\n\n".join(blocks)


def find_landscape_topic_for_term(focus_landscape: Optional[Dict[str, object]], focus_term: str) -> Optional[Dict[str, object]]:
    canonical = digest.normalize_focus_term(focus_term)
    if not isinstance(focus_landscape, dict):
        return None
    for topic in list(focus_landscape.get("focus_topics", []) or []):
        if not isinstance(topic, dict):
            continue
        if digest.normalize_focus_term(str(topic.get("focus_term", ""))) == canonical:
            return topic
    return None


def load_focus_memory_entries(focus_terms: List[str], memory_dir: str) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    for term in focus_terms:
        canonical = digest.normalize_focus_term(term)
        path = focus_memory_path(memory_dir, canonical)
        content = ""
        if os.path.exists(path):
            content = Path(path).read_text(encoding="utf-8")
        entries.append(
            {
                "focus_term": canonical,
                "path": path,
                "content": content,
                "record_count": 0,
                "status": "loaded" if content else "missing",
                "updated": "",
            }
        )
    return entries


def write_focus_memory_placeholder(path: str, focus_term: str) -> str:
    canonical = digest.normalize_focus_term(focus_term)
    content = (
        f"# Focus 任务记忆：{canonical}\n\n"
        "> 这个文件由 focus-transfer 扩展自动维护。当前还没有足够的 focus 论文可用于建档。\n\n"
        f"- 规范化 focus 词：`{canonical}`\n"
        f"- 最近更新：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "## 任务定义\n\n暂无。\n\n"
        "## 技术路线汇总\n\n- 暂无。\n\n"
        "## 动机与启发\n\n- 暂无。\n\n"
        "## 发展趋势与热点问题\n\n- 暂无。\n"
    )
    digest.write_text(path, content)
    return content


def update_focus_memory_files(
    *,
    model: str,
    api_base: str,
    api_key: str,
    endpoint_mode: str,
    message_style: str,
    stream: bool,
    timeout: int,
    packet_name: str,
    focus_terms: List[str],
    focus_records: List[Dict[str, object]],
    focus_landscape: Optional[Dict[str, object]],
    memory_dir: str,
    report_dir: str,
    data_dir: str,
    max_output_tokens: int,
) -> List[Dict[str, object]]:
    os.makedirs(memory_dir, exist_ok=True)
    entries: List[Dict[str, object]] = []
    for term in focus_terms:
        canonical = digest.normalize_focus_term(term)
        path = focus_memory_path(memory_dir, canonical)
        json_path = focus_memory_json_path(memory_dir, canonical)
        existing_md = Path(path).read_text(encoding="utf-8") if os.path.exists(path) else ""
        term_records = focus_records_for_term(
            canonical,
            focus_records,
            allow_single_term_fallback=(len(focus_terms) == 1),
        )
        term_record_ids = {paper_id(record) for record in term_records if paper_id(record)}
        other_focus_records = [
            record for record in focus_records
            if paper_id(record) and paper_id(record) not in term_record_ids
        ]
        records_hash = hashlib.sha256(
            json.dumps([paper_hash(record) for record in term_records], ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        cached_meta = load_json_file(json_path) or {}
        if (
            existing_md
            and cached_meta
            and digest.safe_text(str(cached_meta.get("_records_hash", ""))) == records_hash
            and digest.safe_text(str(cached_meta.get("_model", ""))) == model
        ):
            entries.append(
                {
                    "focus_term": canonical,
                    "path": path,
                    "content": existing_md,
                    "record_count": len(term_records),
                    "status": "reused",
                    "updated": digest.safe_text(str(cached_meta.get("_updated_at", ""))),
                }
            )
            print(f"[INFO] Focus memory reuse: {canonical} -> {path}")
            continue
        if not term_records and existing_md:
            content = existing_md
            updated = dt.datetime.now().isoformat(timespec="seconds")
            digest.dump_json(
                json_path,
                {
                    "_focus_term": canonical,
                    "_records_hash": records_hash,
                    "_model": model,
                    "_updated_at": updated,
                    "_status": "no_current_records",
                },
            )
            entries.append(
                {
                    "focus_term": canonical,
                    "path": path,
                    "content": content,
                    "record_count": 0,
                    "status": "no_current_records",
                    "updated": updated,
                }
            )
            print(f"[INFO] Focus memory skipped (no current records): {canonical} -> {path}")
            continue

        landscape_topic = find_landscape_topic_for_term(focus_landscape, canonical)
        prompt_text = build_focus_memory_update_prompt(
            packet_name=packet_name,
            focus_term=canonical,
            existing_memory_md=existing_md,
            term_records=term_records,
            other_focus_records=other_focus_records,
            landscape_topic=landscape_topic,
        )
        prompt_path = os.path.join(report_dir, f"prompt_focus_memory_{focus_memory_slug(canonical)}.md")
        digest.write_text(prompt_path, prompt_text)
        result, repair_notes = call_json_with_repair(
            model=model,
            api_base=api_base,
            api_key=api_key,
            endpoint_mode=endpoint_mode,
            message_style=message_style,
            stream=stream,
            timeout=timeout,
            prompt_text=prompt_text,
            validator=lambda payload, current_term=canonical, current_records=term_records: validate_focus_memory_profile(payload, current_term, current_records),
            repair_name=f"focus_memory:{canonical}",
            max_output_tokens=max_output_tokens,
        )
        normalized = normalize_focus_memory_profile(
            result,
            canonical,
            term_records,
            landscape_topic=landscape_topic,
            fallback_profile=cached_meta,
        )
        valid, errors = validate_focus_memory_profile(normalized, canonical, term_records)
        if not valid:
            fallback_normalized = normalize_focus_memory_profile(
                cached_meta,
                canonical,
                term_records,
                landscape_topic=landscape_topic,
                fallback_profile=cached_meta,
            )
            fallback_valid, fallback_errors = validate_focus_memory_profile(fallback_normalized, canonical, term_records)
            if fallback_valid:
                normalized = fallback_normalized
                repair_notes = repair_notes + [f"fallback: reused previous memory after invalid model output: {'; '.join(errors)}"]
                print(f"[WARN] Focus memory fallback to previous cache: {canonical}: {'; '.join(errors)}")
            else:
                normalized = build_minimal_focus_memory_profile(
                    canonical,
                    term_records,
                    landscape_topic=landscape_topic,
                    fallback_profile=cached_meta,
                )
                minimal_valid, minimal_errors = validate_focus_memory_profile(normalized, canonical, term_records)
                if not minimal_valid:
                    raise RuntimeError(
                        f"Focus memory update failed for {canonical}: {'; '.join(errors)}; "
                        f"fallback invalid: {'; '.join(fallback_errors)}; minimal invalid: {'; '.join(minimal_errors)}"
                    )
                repair_notes = repair_notes + [f"fallback: synthesized minimal memory after invalid model output: {'; '.join(errors)}"]
                print(f"[WARN] Focus memory synthesized minimal profile: {canonical}: {'; '.join(errors)}")
        normalized["_focus_term"] = canonical
        normalized["_records_hash"] = records_hash
        normalized["_model"] = current_analysis_model(model)
        normalized["_updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        normalized["_repair_notes"] = repair_notes
        content = render_focus_memory_markdown(normalized, canonical, term_records)
        digest.write_text(path, content)
        digest.dump_json(json_path, normalized)
        entries.append(
            {
                "focus_term": canonical,
                "path": path,
                "json_path": json_path,
                "content": content,
                "record_count": len(term_records),
                "status": "updated",
                "updated": normalized["_updated_at"],
            }
        )
        print(f"[INFO] Focus memory updated: {canonical} -> {path}")

    digest.write_text(os.path.join(report_dir, "focus_memory_index.md"), render_focus_memory_index(entries))
    digest.dump_json(
        os.path.join(data_dir, "focus_memory_files.json"),
        {
            "items": [
                {key: value for key, value in entry.items() if key != "content"}
                for entry in entries
            ]
        },
    )
    return entries


def resolve_analysis_model(api_base: str, requested_model: str) -> str:
    return resolve_analysis_model_with_key(api_base, requested_model, "")


def request_model_catalog(url: str, api_key: str, timeout: int = 12, retries: int = 2) -> str:
    headers = {
        "User-Agent": os.environ.get("ARXIV_USER_AGENT", "arxiv-daily-digest/2.0 (research-bot)"),
        "Connection": "close",
        "Accept": "*/*",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in (401, 403):
                raise
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
            time.sleep(min(2.0, 0.6 * attempt))
        except Exception as exc:
            last_exc = exc
            if attempt == retries:
                break
            time.sleep(min(2.0, 0.6 * attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Failed to fetch model catalog: {url}")


def resolve_analysis_model_with_key(api_base: str, requested_model: str, api_key: str) -> str:
    requested = digest.safe_text(requested_model)
    if requested and requested.lower() != "auto":
        return requested
    base = digest.normalize_api_base(api_base)
    raw = request_model_catalog(f"{base}/models", api_key=api_key, timeout=12, retries=2)
    data = json.loads(raw)
    for item in list(data.get("data", []) or []):
        model_id = digest.safe_text(str(item.get("id", "")))
        if model_id:
            return model_id
    raise RuntimeError("Failed to auto-detect a model id from /v1/models")


def default_analysis_api_base() -> str:
    return os.environ.get(
        "FOCUS_TRANSFER_API_BASE",
        os.environ.get(
            "OPENROUTER_API_BASE",
            os.environ.get(
                "OPENAI_BASE_URL",
                os.environ.get("KIMI_API_BASE", "https://openrouter.ai/api/v1"),
            ),
        ),
    )


def default_analysis_api_key() -> str:
    return os.environ.get(
        "FOCUS_TRANSFER_API_KEY",
        os.environ.get(
            "OPENROUTER_API_KEY",
            os.environ.get("OPENAI_API_KEY", os.environ.get("KIMI_API_KEY", "")),
        ),
    )


def default_analysis_model() -> str:
    explicit = digest.safe_text(os.environ.get("FOCUS_TRANSFER_MODEL", ""))
    if explicit:
        return explicit
    base = digest.normalize_api_base(default_analysis_api_base()).lower()
    if base and not any(token in base for token in ("openrouter", "openai", "moonshot", "kimi")):
        return "auto"
    return os.environ.get(
        "OPENROUTER_MODEL",
        os.environ.get(
            "OPENAI_MODEL",
            os.environ.get("KIMI_MODEL", "openrouter/elephant-alpha"),
        ),
    )


ANALYSIS_PROVIDER_CHAIN: List[Dict[str, str]] = []
ANALYSIS_PROVIDER_ACTIVE_INDEX = 0
ANALYSIS_PROVIDER_ANNOUNCED_INDEX = -1


def provider_host_label(api_base: str) -> str:
    base = digest.normalize_api_base(api_base)
    host = re.sub(r"^https?://", "", base).split("/", 1)[0].strip().lower()
    return host or "unknown-host"


def provider_name_from_base(api_base: str, explicit_name: str = "") -> str:
    if explicit_name:
        return explicit_name
    base = digest.normalize_api_base(api_base).lower()
    if "openrouter" in base:
        return "OpenRouter"
    if "moonshot" in base or "kimi" in base:
        return "Kimi"
    if "openai" in base:
        return "OpenAI"
    host = provider_host_label(api_base)
    return host or "Custom"


def provider_display(provider: Dict[str, str]) -> str:
    name = provider_name_from_base(str(provider.get("api_base", "")), str(provider.get("name", "")))
    host = provider_host_label(str(provider.get("api_base", "")))
    model = digest.safe_text(str(provider.get("model", ""))) or "unknown-model"
    return f"{name} ({host}) model={model}"


def current_analysis_provider() -> Optional[Dict[str, str]]:
    if 0 <= ANALYSIS_PROVIDER_ACTIVE_INDEX < len(ANALYSIS_PROVIDER_CHAIN):
        return ANALYSIS_PROVIDER_CHAIN[ANALYSIS_PROVIDER_ACTIVE_INDEX]
    return None


def current_analysis_model(default_model: str = "") -> str:
    provider = current_analysis_provider()
    if isinstance(provider, dict):
        model = digest.safe_text(str(provider.get("model", "")))
        if model:
            return model
    return digest.safe_text(default_model)


def build_analysis_provider_chain(
    primary_api_base: str,
    primary_api_key: str,
    primary_model: str,
) -> List[Dict[str, str]]:
    chain: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()

    def add(name: str, api_base: str, api_key: str, model: str) -> None:
        base = digest.normalize_api_base(api_base)
        key = digest.safe_text(api_key)
        chosen_model = digest.safe_text(model)
        if not base or not key or not chosen_model:
            return
        fingerprint = (base, chosen_model)
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        chain.append(
            {
                "name": provider_name_from_base(base, name),
                "api_base": base,
                "api_key": key,
                "model": chosen_model,
            }
        )

    add("Primary", primary_api_base, primary_api_key, primary_model)
    add("OpenRouter", os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"), os.environ.get("OPENROUTER_API_KEY", ""), os.environ.get("OPENROUTER_MODEL", "openrouter/elephant-alpha"))
    add("OpenAI", os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), os.environ.get("OPENAI_API_KEY", ""), os.environ.get("OPENAI_MODEL", ""))
    add("Kimi", os.environ.get("KIMI_API_BASE", "https://api.moonshot.cn/v1"), os.environ.get("KIMI_API_KEY", ""), os.environ.get("KIMI_MODEL", "moonshot-v1-8k"))
    return chain


def configure_analysis_provider_chain(primary_api_base: str, primary_api_key: str, primary_model: str) -> List[Dict[str, str]]:
    global ANALYSIS_PROVIDER_CHAIN, ANALYSIS_PROVIDER_ACTIVE_INDEX, ANALYSIS_PROVIDER_ANNOUNCED_INDEX
    ANALYSIS_PROVIDER_CHAIN = build_analysis_provider_chain(primary_api_base, primary_api_key, primary_model)
    ANALYSIS_PROVIDER_ACTIVE_INDEX = 0
    ANALYSIS_PROVIDER_ANNOUNCED_INDEX = -1
    if not ANALYSIS_PROVIDER_CHAIN:
        print("[WARN] Transfer analysis provider chain is empty.")
        return []
    primary = ANALYSIS_PROVIDER_CHAIN[0]
    print(f"[INFO] Transfer analysis current primary API: {provider_display(primary)}")
    if len(ANALYSIS_PROVIDER_CHAIN) > 1:
        fallback_text = " -> ".join(provider_display(provider) for provider in ANALYSIS_PROVIDER_CHAIN[1:])
        print(f"[INFO] Transfer analysis fallback chain (only used if the current primary fails): {fallback_text}")
    ANALYSIS_PROVIDER_ANNOUNCED_INDEX = 0
    return ANALYSIS_PROVIDER_CHAIN


def announce_analysis_provider(index: int, reason: str = "active") -> None:
    global ANALYSIS_PROVIDER_ANNOUNCED_INDEX
    if not (0 <= index < len(ANALYSIS_PROVIDER_CHAIN)):
        return
    if ANALYSIS_PROVIDER_ANNOUNCED_INDEX == index:
        return
    provider = ANALYSIS_PROVIDER_CHAIN[index]
    print(f"[INFO] Transfer analysis provider {reason}: {provider_display(provider)}")
    ANALYSIS_PROVIDER_ANNOUNCED_INDEX = index


def call_analysis_json_with_provider_fallback(
    system_prompt: str,
    user_prompt: str,
    timeout: int,
    max_output_tokens: int,
    endpoint_mode: str,
    message_style: str,
    stream: bool,
) -> Tuple[Optional[dict], List[str]]:
    global ANALYSIS_PROVIDER_ACTIVE_INDEX
    if not ANALYSIS_PROVIDER_CHAIN:
        return None, ["provider-chain empty"]
    order = list(range(ANALYSIS_PROVIDER_ACTIVE_INDEX, len(ANALYSIS_PROVIDER_CHAIN))) + list(range(0, ANALYSIS_PROVIDER_ACTIVE_INDEX))
    notes: List[str] = []
    for pos, provider_index in enumerate(order):
        provider = ANALYSIS_PROVIDER_CHAIN[provider_index]
        result = digest.call_openai_json(
            model=str(provider.get("model", "")),
            api_key=str(provider.get("api_key", "")),
            api_base=str(provider.get("api_base", "")),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            endpoint_mode=endpoint_mode,
            message_style=message_style,
            stream=stream,
        )
        if isinstance(result, dict):
            if provider_index != ANALYSIS_PROVIDER_ACTIVE_INDEX:
                previous = ANALYSIS_PROVIDER_CHAIN[ANALYSIS_PROVIDER_ACTIVE_INDEX]
                print(f"[INFO] Transfer analysis API switched to fallback: {provider_display(previous)} -> {provider_display(provider)}")
            ANALYSIS_PROVIDER_ACTIVE_INDEX = provider_index
            notes.append(f"provider: {provider_display(provider)}")
            return result, notes
        notes.append(f"provider_failed: {provider_display(provider)}")
        print(f"[WARN] Transfer analysis API returned no JSON: {provider_display(provider)}")
        if pos + 1 < len(order):
            next_provider = ANALYSIS_PROVIDER_CHAIN[order[pos + 1]]
            print(f"[WARN] Transfer analysis API switching to fallback: {provider_display(next_provider)}")
    return None, notes


def build_focus_profile_prompt(packet_name: str, focus_terms: List[str], focus_records: List[Dict[str, object]]) -> str:
    focus_context = [compact_record_for_llm(record) for record in focus_records]
    schema = {
        "focus_terms": ["string"],
        "target_problem_statement": "string",
        "target_tasks": ["string"],
        "shared_assumptions": ["string"],
        "recurring_setups": ["string"],
        "main_bottlenecks": ["string"],
        "underexplored_needs": ["string"],
        "external_capability_gaps": ["string"],
        "focus_method_patterns": ["string"],
        "representative_paper_ids": ["paper_id"],
        "notes": "string",
    }
    return (
        f"Packet: {packet_name}\n\n"
        f"Focus terms:\n{json.dumps(focus_terms, ensure_ascii=False)}\n\n"
        "Focus paper corpus JSON:\n"
        f"{json.dumps(focus_context, ensure_ascii=False, indent=2)}\n\n"
        "任务：你要只基于上面的 focus 论文摘要，输出一个 JSON 对象，用于指导后续“非 focus 论文能否迁移到 focus 领域”的筛选。\n"
        "要求：\n"
        "1. 只能依据给定摘要和标签，不能假装读过正文。\n"
        "2. `representative_paper_ids` 必须来自给定 focus paper_id。\n"
        "3. `external_capability_gaps` 要明确写出“外部领域的方法最应该补给 focus 的能力”。\n"
        "4. 每个 list 最多 8 条，尽量可执行，不要空话。\n"
        "5. 只输出 JSON 对象，不要输出 markdown。\n\n"
        f"JSON schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )


def validate_focus_profile(profile: Optional[dict], focus_ids: set[str]) -> Tuple[bool, List[str]]:
    if not isinstance(profile, dict):
        return False, ["result is not a JSON object"]
    required = [
        "focus_terms",
        "target_problem_statement",
        "target_tasks",
        "shared_assumptions",
        "main_bottlenecks",
        "underexplored_needs",
        "external_capability_gaps",
        "representative_paper_ids",
    ]
    missing = [key for key in required if key not in profile]
    errors = [f"missing field: {key}" for key in missing]
    if digest.safe_text(str(profile.get("target_problem_statement", ""))) == "":
        errors.append("target_problem_statement is empty")
    for key in ("focus_terms", "target_tasks", "shared_assumptions", "main_bottlenecks", "underexplored_needs", "external_capability_gaps"):
        rows = non_other(profile.get(key, []) or [])
        if not rows:
            errors.append(f"{key} is empty")
    rep_ids = [digest.safe_text(str(pid)) for pid in list(profile.get("representative_paper_ids", []) or []) if digest.safe_text(str(pid))]
    if not rep_ids:
        errors.append("representative_paper_ids is empty")
    if rep_ids and focus_ids and any(pid not in focus_ids for pid in rep_ids):
        errors.append("representative_paper_ids contains unknown focus ids")
    return not errors, errors


def build_focus_term_groups(
    focus_terms: List[str],
    focus_records: List[Dict[str, object]],
    per_term_limit: int = 14,
) -> List[Dict[str, object]]:
    groups: List[Dict[str, object]] = []
    fallback_records = focus_records[:per_term_limit] if per_term_limit > 0 else focus_records
    for term in focus_terms:
        matched = [record for record in focus_records if term in non_other(record.get("focus_term_hits", []) or [])]
        selected = matched[:per_term_limit] if per_term_limit > 0 else matched
        if not selected and len(focus_terms) == 1:
            selected = fallback_records
        if not selected:
            continue
        groups.append(
            {
                "focus_term": term,
                "paper_ids": [paper_id(record) for record in selected if paper_id(record)],
                "papers": [compact_record_for_llm(record) for record in selected],
            }
        )
    if groups:
        return groups
    return [
        {
            "focus_term": focus_terms[0] if focus_terms else "focus",
            "paper_ids": [paper_id(record) for record in fallback_records if paper_id(record)],
            "papers": [compact_record_for_llm(record) for record in fallback_records],
        }
    ]


def focus_term_group_id_map(
    focus_terms: List[str],
    focus_records: List[Dict[str, object]],
    per_term_limit: int = 14,
) -> Dict[str, List[str]]:
    groups = build_focus_term_groups(focus_terms, focus_records, per_term_limit=per_term_limit)
    out: Dict[str, List[str]] = {}
    for group in groups:
        term = digest.normalize_focus_term(str(group.get("focus_term", "")))
        if not term:
            continue
        out[term] = dedupe_texts(group.get("paper_ids", []) or [])
    return out


def required_representative_count(available_ids: List[str]) -> int:
    count = len(available_ids)
    if count >= 6:
        return 4
    if count >= 3:
        return 3
    return count


def select_landscape_representative_paper_ids(
    available_ids: List[str],
    topic: Optional[Dict[str, object]] = None,
    fallback_topic: Optional[Dict[str, object]] = None,
) -> List[str]:
    available = dedupe_texts(available_ids)
    if not available:
        return []
    available_set = set(available)
    chosen: List[str] = []
    if isinstance(topic, dict):
        chosen.extend(topic.get("representative_paper_ids", []) or [])
    if isinstance(fallback_topic, dict):
        chosen.extend(fallback_topic.get("representative_paper_ids", []) or [])
    filtered = [pid for pid in dedupe_texts(chosen) if pid in available_set]
    required = required_representative_count(available)
    if len(filtered) < required:
        for pid in available:
            if pid not in filtered:
                filtered.append(pid)
            if len(filtered) >= required:
                break
    return filtered[: max(required, len(filtered))]


def build_minimal_focus_landscape_topic(
    focus_term: str,
    term_records: List[Dict[str, object]],
    fallback_topic: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    canonical = digest.normalize_focus_term(focus_term)
    available_ids = [paper_id(record) for record in term_records if paper_id(record)]
    first_summary = ""
    for record in term_records:
        first_summary = (
            digest.safe_text(str(record.get("summary_brief_zh", "")))
            or digest.safe_text(str(record.get("summary_zh", "")))
            or digest.safe_text(str(record.get("summary_brief_en", "")))
            or digest.safe_text(str(record.get("summary_en", "")))
        )
        if first_summary:
            break
    if first_summary:
        trend_summary = truncate_text_middle(first_summary.replace("\n", " "), 180)
    else:
        trend_summary = f"当前批次论文显示，{canonical} 仍是一个持续活跃且具备迁移价值的重点主题。"
    hot_problems = dedupe_texts((fallback_topic or {}).get("hot_problems", []) or [])[:4]
    if not hot_problems:
        hot_problems = [
            f"{canonical} 在不同任务与数据条件下的稳健泛化",
            f"{canonical} 场景下的方法效率与部署成本",
            f"{canonical} 与相邻任务之间的能力迁移",
        ][: max(1, min(3, len(available_ids) or 3))]
    return {
        "focus_term": canonical,
        "trend_summary": trend_summary,
        "hot_problems": hot_problems,
        "representative_paper_ids": select_landscape_representative_paper_ids(available_ids, fallback_topic=fallback_topic),
    }


def build_minimal_focus_landscape(
    focus_terms: List[str],
    focus_records: List[Dict[str, object]],
    fallback_summary: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    term_groups = build_focus_term_groups(focus_terms, focus_records)
    fallback_topics = {
        digest.normalize_focus_term(str(topic.get("focus_term", ""))): topic
        for topic in list((fallback_summary or {}).get("focus_topics", []) or [])
        if isinstance(topic, dict) and digest.normalize_focus_term(str(topic.get("focus_term", "")))
    }
    topics: List[Dict[str, object]] = []
    covered: set[str] = set()
    for group in term_groups:
        term = digest.normalize_focus_term(str(group.get("focus_term", "")))
        if not term or term in covered:
            continue
        records = list(group.get("papers", []) or [])
        topics.append(build_minimal_focus_landscape_topic(term, records, fallback_topics.get(term)))
        covered.add(term)
    overall_summary = digest.safe_text(str((fallback_summary or {}).get("overall_summary", "")))
    if not overall_summary:
        overall_summary = (
            f"当前 focus 语料共覆盖 {len(topics)} 个活跃主题，"
            "摘要结果已按主题整理代表论文与热点问题，可直接用于后续可迁移性判断。"
        )
    return {"overall_summary": overall_summary, "focus_topics": topics}


def normalize_focus_landscape(
    summary: Optional[Dict[str, object]],
    focus_terms: List[str],
    focus_records: List[Dict[str, object]],
    fallback_summary: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    term_groups = build_focus_term_groups(focus_terms, focus_records)
    group_records = {
        digest.normalize_focus_term(str(group.get("focus_term", ""))): list(group.get("papers", []) or [])
        for group in term_groups
        if digest.normalize_focus_term(str(group.get("focus_term", "")))
    }
    group_ids = {
        term: [paper_id(record) for record in records if paper_id(record)]
        for term, records in group_records.items()
    }
    fallback_topics = {
        digest.normalize_focus_term(str(topic.get("focus_term", ""))): dict(topic)
        for topic in list((fallback_summary or {}).get("focus_topics", []) or [])
        if isinstance(topic, dict) and digest.normalize_focus_term(str(topic.get("focus_term", "")))
    }
    normalized = dict(summary or {}) if isinstance(summary, dict) else {}
    normalized_topics: List[Dict[str, object]] = []
    seen_terms: set[str] = set()
    for topic in list(normalized.get("focus_topics", []) or []):
        if not isinstance(topic, dict):
            continue
        term = digest.normalize_focus_term(str(topic.get("focus_term", "")))
        if not term or term in seen_terms:
            continue
        fallback_topic = fallback_topics.get(term)
        records = group_records.get(term, [])
        available_ids = group_ids.get(term, [])
        topic_obj = dict(topic)
        topic_obj["focus_term"] = term
        topic_obj["trend_summary"] = (
            digest.safe_text(str(topic_obj.get("trend_summary", "")))
            or digest.safe_text(str((fallback_topic or {}).get("trend_summary", "")))
        )
        topic_obj["hot_problems"] = dedupe_texts(topic_obj.get("hot_problems", []) or []) or dedupe_texts((fallback_topic or {}).get("hot_problems", []) or [])
        topic_obj["representative_paper_ids"] = select_landscape_representative_paper_ids(
            available_ids,
            topic_obj,
            fallback_topic,
        )
        if (not topic_obj["trend_summary"]) or (not topic_obj["hot_problems"]):
            topic_obj = build_minimal_focus_landscape_topic(term, records, fallback_topic)
        normalized_topics.append(topic_obj)
        seen_terms.add(term)
    for group in term_groups:
        term = digest.normalize_focus_term(str(group.get("focus_term", "")))
        if not term or term in seen_terms:
            continue
        normalized_topics.append(
            build_minimal_focus_landscape_topic(
                term,
                list(group.get("papers", []) or []),
                fallback_topics.get(term),
            )
        )
        seen_terms.add(term)
    normalized["focus_topics"] = normalized_topics
    normalized["overall_summary"] = (
        digest.safe_text(str(normalized.get("overall_summary", "")))
        or digest.safe_text(str((fallback_summary or {}).get("overall_summary", "")))
        or build_minimal_focus_landscape(focus_terms, focus_records, fallback_summary).get("overall_summary", "")
    )
    return normalized


def build_focus_landscape_prompt(packet_name: str, focus_terms: List[str], focus_records: List[Dict[str, object]]) -> str:
    term_groups = build_focus_term_groups(focus_terms, focus_records)
    schema = {
        "overall_summary": "string",
        "focus_topics": [
            {
                "focus_term": "string",
                "trend_summary": "string",
                "hot_problems": ["string"],
                "representative_paper_ids": ["paper_id"],
            }
        ],
    }
    return (
        f"Packet: {packet_name}\n\n"
        f"Focus terms:\n{json.dumps(focus_terms, ensure_ascii=False)}\n\n"
        "Grouped focus paper corpus JSON:\n"
        f"{json.dumps(term_groups, ensure_ascii=False, indent=2)}\n\n"
        "任务：只基于上面的 focus 论文摘要，总结当前 focus 领域的发展趋势和热点问题。\n"
        "要求：\n"
        "1. 如果 focus 关键词有多个，就按每个 focus_term 分别总结，不要把不同 focus_term 混成一段。\n"
        "2. `trend_summary` 要概括这个 focus_term 近期在做什么、方向怎么变化。\n"
        "3. `hot_problems` 只写真正被重复追逐的问题，避免空话。\n"
        "4. `representative_paper_ids` 必须只来自对应 focus_term 那一组的 `paper_ids`，不能混用别的 focus_term 的 id，也不能沿用历史旧 id。\n"
        "5. 若某个 focus_term 给出的候选论文不少于 6 篇，则至少返回 4 篇代表论文；若候选论文为 3 到 5 篇，则至少返回 3 篇；若不足 3 篇，则尽量全部返回。\n"
        "6. 只输出一个 JSON 对象。\n\n"
        f"JSON schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )


def validate_focus_landscape(summary: Optional[dict], focus_terms: List[str], focus_records: List[Dict[str, object]]) -> Tuple[bool, List[str]]:
    if not isinstance(summary, dict):
        return False, ["result is not a JSON object"]
    errors: List[str] = []
    if not digest.safe_text(str(summary.get("overall_summary", ""))):
        errors.append("overall_summary is empty")
    topics = list(summary.get("focus_topics", []) or [])
    if not topics:
        errors.append("focus_topics is empty")
        return False, errors
    paper_ids = {paper_id(record) for record in focus_records if paper_id(record)}
    term_groups = build_focus_term_groups(focus_terms, focus_records)
    term_group_ids = {
        digest.normalize_focus_term(str(group.get("focus_term", ""))): [
            digest.safe_text(str(pid)) for pid in list(group.get("paper_ids", []) or []) if digest.safe_text(str(pid))
        ]
        for group in term_groups
        if isinstance(group, dict) and digest.normalize_focus_term(str(group.get("focus_term", "")))
    }
    valid_terms = {digest.normalize_focus_term(term) for term in focus_terms if digest.normalize_focus_term(term)}
    seen_terms: set[str] = set()
    for topic in topics:
        if not isinstance(topic, dict):
            errors.append("focus_topics contains non-object")
            continue
        term = digest.normalize_focus_term(str(topic.get("focus_term", "")))
        if not term:
            errors.append("focus_topic missing focus_term")
        elif valid_terms and term not in valid_terms:
            errors.append(f"unknown focus_term: {term}")
        else:
            seen_terms.add(term)
        if not digest.safe_text(str(topic.get("trend_summary", ""))):
            errors.append(f"trend_summary is empty for {term or '<unknown>'}")
        hot_problems = non_other(topic.get("hot_problems", []) or [])
        if not hot_problems:
            errors.append(f"hot_problems is empty for {term or '<unknown>'}")
        rep_ids = [digest.safe_text(str(pid)) for pid in list(topic.get("representative_paper_ids", []) or []) if digest.safe_text(str(pid))]
        available_ids = term_group_ids.get(term, [])
        allowed_ids = set(available_ids) if available_ids else paper_ids
        unique_rep_ids = list(dict.fromkeys(rep_ids))
        if unique_rep_ids and any(pid not in allowed_ids for pid in unique_rep_ids):
            errors.append(f"representative_paper_ids contain unknown ids for {term or '<unknown>'}")
        if available_ids:
            if len(available_ids) >= 6:
                required_rep_count = 4
            elif len(available_ids) >= 3:
                required_rep_count = 3
            else:
                required_rep_count = len(available_ids)
            if len(unique_rep_ids) < required_rep_count:
                errors.append(
                    f"representative_paper_ids too short for {term or '<unknown>'}: "
                    f"expected at least {required_rep_count}, got {len(unique_rep_ids)}"
                )
    if valid_terms and not (seen_terms & valid_terms):
        errors.append("no focus_term summaries matched the configured focus terms")
    return not errors, errors


def normalize_transfer_note_judgment(candidate: Dict[str, object], judgment: Dict[str, object]) -> Dict[str, object]:
    out = dict(judgment)
    out["paper_id"] = digest.safe_text(str(out.get("paper_id", ""))) or paper_id(candidate)
    out["decision"] = normalize_decision(str(out.get("decision", "")))
    out["source_field"] = digest.safe_text(str(out.get("source_field", ""))) or digest.safe_text(str(candidate.get("source_field_guess", ""))) or "other"
    out["reason_short"] = digest.safe_text(str(out.get("reason_short", "")))
    out["transfer_note"] = digest.safe_text(str(out.get("transfer_note", "")))
    return out


def validate_transfer_note_judgment(judgment: Optional[dict], candidate: Dict[str, object]) -> Tuple[bool, List[str]]:
    if not isinstance(judgment, dict):
        return False, ["result is not a JSON object"]
    normalized = normalize_transfer_note_judgment(candidate, judgment)
    errors: List[str] = []
    expected_id = paper_id(candidate)
    if normalized["paper_id"] != expected_id:
        errors.append(f"paper_id mismatch: expected {expected_id}")
    if normalized["decision"] not in DECISION_VALUES:
        errors.append("decision must be keep/maybe/reject")
    if not normalized["source_field"]:
        errors.append("source_field is empty")
    if not normalized["reason_short"]:
        errors.append("reason_short is empty")
    if normalized["decision"] in {"keep", "maybe"} and not normalized["transfer_note"]:
        errors.append("transfer_note is empty for keep/maybe")
    return not errors, errors


def build_transfer_note_prompt(
    packet_name: str,
    focus_landscape: Dict[str, object],
    candidate: Dict[str, object],
    focus_memory_context: str = "",
) -> str:
    schema = {
        "paper_id": "paper_id",
        "decision": "keep | maybe | reject",
        "source_field": "string",
        "reason_short": "string",
        "transfer_note": "string",
    }
    return (
        f"Packet: {packet_name}\n\n"
        "Focus landscape JSON:\n"
        f"{json.dumps(focus_landscape, ensure_ascii=False, indent=2)}\n\n"
        "Long-term focus memory Markdown:\n"
        f"{truncate_text_middle(focus_memory_context, 24000)}\n\n"
        "Candidate non-focus paper JSON:\n"
        f"{json.dumps(compact_record_for_llm(candidate), ensure_ascii=False, indent=2)}\n\n"
        "任务：判断这篇非 focus 论文的动机、思想或方法是否值得迁移到当前 focus 领域。\n"
        "要求：\n"
        "1. `decision` 只能是 keep / maybe / reject。\n"
        "2. 如果 `decision=keep` 或 `decision=maybe`，`transfer_note` 必须写成 1 到 3 句中文，直接说明这篇论文可以借什么、落到哪个 focus 问题、怎样迁移。\n"
        "3. 如果 `decision=reject`，`transfer_note` 置空即可，`reason_short` 要明确说明为什么当前不建议迁移。\n"
        "4. 如果当前有多个 focus 词，判断某个 focus 目标时也要参考其他 focus 词的论文和长期记忆，因为它们可能提供相邻任务定义、共享假设、传感器设置或评测动机。\n"
        "5. 长期记忆文件用于理解 focus 任务定义、技术路线和热点；但候选论文是否可迁移仍必须由候选摘要支持。\n"
        "6. 不要编造正文实验，不要写宽泛套话。\n"
        "7. 只输出 JSON 对象。\n\n"
        f"JSON schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )


def build_transfer_judgment_prompt(
    packet_name: str,
    focus_profile: Dict[str, object],
    candidate: Dict[str, object],
) -> str:
    schema = {
        "paper_id": "paper_id",
        "decision": "keep | maybe | reject",
        "confidence": "high | medium | low",
        "source_field": "string",
        "method_family": ["choose from controlled vocabulary"],
        "focus_problem_targets": ["string"],
        "reusable_idea": "string",
        "why_transferable": "string",
        "adaptation_path": "string",
        "required_modifications": ["string"],
        "minimal_experiment": {
            "setup": "string",
            "datasets": ["string"],
            "metrics": ["string"],
            "baselines": ["string"],
            "success_signal": "string",
        },
        "risks": ["string"],
        "evidence_type": "abstract_supported | inferred",
        "reason_short": "string",
        "reject_reason": "string",
    }
    return (
        f"Packet: {packet_name}\n\n"
        "Focus profile JSON:\n"
        f"{json.dumps(focus_profile, ensure_ascii=False, indent=2)}\n\n"
        "Candidate non-focus paper JSON:\n"
        f"{json.dumps(compact_record_for_llm(candidate), ensure_ascii=False, indent=2)}\n\n"
        "任务：判断这篇非 focus 论文是否值得迁移到当前 focus 领域。\n"
        "要求：\n"
        "1. 只能基于给定摘要做判断。\n"
        "2. `decision` 只能是 keep / maybe / reject。\n"
        "3. `method_family` 必须优先从下面的 controlled vocabulary 里选 1-2 个：\n"
        f"{json.dumps(METHOD_FAMILY_OPTIONS, ensure_ascii=False)}\n"
        "4. 如果 `decision=keep` 或 `decision=maybe`，必须给出可操作的 `adaptation_path`、`required_modifications`、`minimal_experiment`、`risks`。\n"
        "5. 如果 `decision=reject`，也要写清楚 `reject_reason`，并且不要硬编应用路径。\n"
        "6. `evidence_type` 只能是 `abstract_supported` 或 `inferred`。\n"
        "7. 只输出 JSON 对象，不要输出 markdown。\n\n"
        f"JSON schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )


def normalize_decision(value: str) -> str:
    text = digest.safe_text(value).lower()
    aliases = {
        "keep": "keep",
        "retain": "keep",
        "high_confidence_keep": "keep",
        "maybe": "maybe",
        "candidate": "maybe",
        "medium_candidate": "maybe",
        "reject": "reject",
        "drop": "reject",
        "no": "reject",
    }
    return aliases.get(text, text)


def normalize_confidence(value: str) -> str:
    text = digest.safe_text(value).lower()
    aliases = {"high": "high", "medium": "medium", "low": "low"}
    return aliases.get(text, text)


def normalize_evidence_type(value: str) -> str:
    text = digest.safe_text(value).lower().replace("-", "_")
    aliases = {
        "abstract_supported": "abstract_supported",
        "abstractsupport": "abstract_supported",
        "direct": "abstract_supported",
        "inferred": "inferred",
        "inference": "inferred",
    }
    return aliases.get(text, text)


def normalize_judgment(candidate: Dict[str, object], judgment: Dict[str, object]) -> Dict[str, object]:
    out = dict(judgment)
    out["paper_id"] = digest.safe_text(str(out.get("paper_id", ""))) or paper_id(candidate)
    out["decision"] = normalize_decision(str(out.get("decision", "")))
    out["confidence"] = normalize_confidence(str(out.get("confidence", "")))
    out["source_field"] = digest.safe_text(str(out.get("source_field", ""))) or digest.safe_text(str(candidate.get("source_field_guess", ""))) or "other"
    method_family = out.get("method_family", [])
    if isinstance(method_family, str):
        method_family = [method_family]
    out["method_family"] = non_other(method_family or []) or non_other(candidate.get("method_family_guess", []) or []) or ["other"]
    out["focus_problem_targets"] = non_other(out.get("focus_problem_targets", []) or [])
    out["required_modifications"] = non_other(out.get("required_modifications", []) or [])
    out["risks"] = non_other(out.get("risks", []) or [])
    out["evidence_type"] = normalize_evidence_type(str(out.get("evidence_type", "")))
    out["reason_short"] = digest.safe_text(str(out.get("reason_short", "")))
    out["reusable_idea"] = digest.safe_text(str(out.get("reusable_idea", "")))
    out["why_transferable"] = digest.safe_text(str(out.get("why_transferable", "")))
    out["adaptation_path"] = digest.safe_text(str(out.get("adaptation_path", "")))
    out["reject_reason"] = digest.safe_text(str(out.get("reject_reason", "")))
    experiment = out.get("minimal_experiment", {}) or {}
    if not isinstance(experiment, dict):
        experiment = {}
    out["minimal_experiment"] = {
        "setup": digest.safe_text(str(experiment.get("setup", ""))),
        "datasets": non_other(experiment.get("datasets", []) or []),
        "metrics": non_other(experiment.get("metrics", []) or []),
        "baselines": non_other(experiment.get("baselines", []) or []),
        "success_signal": digest.safe_text(str(experiment.get("success_signal", ""))),
    }
    return out


def validate_transfer_judgment(judgment: Optional[dict], candidate: Dict[str, object]) -> Tuple[bool, List[str]]:
    if not isinstance(judgment, dict):
        return False, ["result is not a JSON object"]
    normalized = normalize_judgment(candidate, judgment)
    errors: List[str] = []
    expected_id = paper_id(candidate)
    if normalized["paper_id"] != expected_id:
        errors.append(f"paper_id mismatch: expected {expected_id}")
    if normalized["decision"] not in DECISION_VALUES:
        errors.append("decision must be keep/maybe/reject")
    if normalized["confidence"] not in CONFIDENCE_VALUES:
        errors.append("confidence must be high/medium/low")
    if normalized["evidence_type"] not in EVIDENCE_TYPES:
        errors.append("evidence_type must be abstract_supported/inferred")
    if not normalized["source_field"]:
        errors.append("source_field is empty")
    if not normalized["method_family"]:
        errors.append("method_family is empty")
    if not normalized["reason_short"]:
        errors.append("reason_short is empty")
    if normalized["decision"] in {"keep", "maybe"}:
        if not normalized["reusable_idea"]:
            errors.append("reusable_idea is empty")
        if not normalized["adaptation_path"]:
            errors.append("adaptation_path is empty")
        if not normalized["required_modifications"]:
            errors.append("required_modifications is empty")
        experiment = normalized["minimal_experiment"]
        if not experiment.get("setup"):
            errors.append("minimal_experiment.setup is empty")
        if not experiment.get("metrics"):
            errors.append("minimal_experiment.metrics is empty")
        if not normalized["risks"]:
            errors.append("risks is empty")
    if normalized["decision"] == "reject" and not normalized["reject_reason"]:
        errors.append("reject_reason is empty")
    return not errors, errors


def build_transfer_synthesis_prompt(
    packet_name: str,
    focus_profile: Dict[str, object],
    transferable_judgments: List[Dict[str, object]],
) -> str:
    compact_judgments = []
    for judgment in transferable_judgments:
        compact_judgments.append(
            {
                "paper_id": digest.safe_text(str(judgment.get("paper_id", ""))),
                "decision": digest.safe_text(str(judgment.get("decision", ""))),
                "confidence": digest.safe_text(str(judgment.get("confidence", ""))),
                "source_field": digest.safe_text(str(judgment.get("source_field", ""))),
                "method_family": non_other(judgment.get("method_family", []) or []),
                "focus_problem_targets": non_other(judgment.get("focus_problem_targets", []) or []),
                "reusable_idea": digest.safe_text(str(judgment.get("reusable_idea", ""))),
                "adaptation_path": digest.safe_text(str(judgment.get("adaptation_path", ""))),
                "required_modifications": non_other(judgment.get("required_modifications", []) or []),
                "risks": non_other(judgment.get("risks", []) or []),
            }
        )
    schema = {
        "portfolio_summary": "string",
        "overall_findings": ["string"],
        "method_clusters": [
            {
                "method_family": "string",
                "why_it_matters_for_focus": "string",
                "best_source_fields": ["string"],
                "recommended_paper_ids": ["paper_id"],
                "implementation_pattern": "string",
                "common_modifications": ["string"],
                "common_risks": ["string"],
            }
        ],
        "top_recommendations": [{"paper_id": "paper_id", "priority": "high|medium", "reason": "string"}],
        "do_not_overinvest": ["string"],
    }
    return (
        f"Packet: {packet_name}\n\n"
        "Focus profile JSON:\n"
        f"{json.dumps(focus_profile, ensure_ascii=False, indent=2)}\n\n"
        "Transferable judgments JSON (only keep/maybe papers):\n"
        f"{json.dumps(compact_judgments, ensure_ascii=False, indent=2)}\n\n"
        "任务：把这些逐篇判断整理成方法簇和组合结论，用于最终 HTML 报告。\n"
        "要求：\n"
        "1. `recommended_paper_ids` 和 `top_recommendations.paper_id` 必须来自输入 paper_id。\n"
        "2. 方法簇请按 `method_family` 汇总，不要发明脱离输入的新路线。\n"
        "3. `do_not_overinvest` 要指出哪些路线看起来热闹，但迁移到 focus 领域时风险太高或收益有限。\n"
        "4. 只输出 JSON 对象。\n\n"
        f"JSON schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )


def validate_transfer_synthesis(summary: Optional[dict], valid_paper_ids: set[str]) -> Tuple[bool, List[str]]:
    if not isinstance(summary, dict):
        return False, ["result is not a JSON object"]
    errors: List[str] = []
    if not digest.safe_text(str(summary.get("portfolio_summary", ""))):
        errors.append("portfolio_summary is empty")
    if not non_other(summary.get("overall_findings", []) or []):
        errors.append("overall_findings is empty")
    clusters = list(summary.get("method_clusters", []) or [])
    if not clusters:
        errors.append("method_clusters is empty")
    for cluster in clusters:
        if not isinstance(cluster, dict):
            errors.append("method_clusters contains non-object")
            continue
        if not digest.safe_text(str(cluster.get("method_family", ""))):
            errors.append("cluster missing method_family")
        cluster_ids = [digest.safe_text(str(pid)) for pid in list(cluster.get("recommended_paper_ids", []) or []) if digest.safe_text(str(pid))]
        if cluster_ids and any(pid not in valid_paper_ids for pid in cluster_ids):
            errors.append("cluster contains unknown recommended_paper_ids")
    top_recs = list(summary.get("top_recommendations", []) or [])
    if not top_recs:
        errors.append("top_recommendations is empty")
    for row in top_recs:
        if not isinstance(row, dict):
            errors.append("top_recommendations contains non-object")
            continue
        pid = digest.safe_text(str(row.get("paper_id", "")))
        if pid and pid not in valid_paper_ids:
            errors.append("top_recommendations contains unknown paper_id")
    return not errors, errors


def build_analysis_system_prompt() -> str:
    return (
        "你是一个严谨的计算机视觉研究分析器。"
        "你必须只基于给定摘要、标签和上下文做判断，不能假装读过正文，不能编造实验结果。"
        "输出要面向研究决策：明确、克制、结构化。"
    )


def call_json_with_repair(
    *,
    model: str,
    api_base: str,
    api_key: str,
    endpoint_mode: str,
    message_style: str,
    stream: bool,
    timeout: int,
    prompt_text: str,
    validator,
    repair_name: str,
    max_output_tokens: int,
) -> Tuple[Optional[dict], List[str]]:
    system_prompt = build_analysis_system_prompt()
    notes: List[str] = []
    result, provider_notes = call_analysis_json_with_provider_fallback(
        system_prompt=system_prompt,
        user_prompt=prompt_text,
        timeout=timeout,
        max_output_tokens=max_output_tokens,
        endpoint_mode=endpoint_mode,
        message_style=message_style,
        stream=stream,
    )
    notes.extend(provider_notes)
    valid, errors = validator(result)
    if valid:
        return result, notes
    notes.extend([f"round1: {err}" for err in errors])
    repair_prompt = (
        f"{prompt_text}\n\n"
        f"上一轮 `{repair_name}` 输出不合格，必须修复以下问题：\n"
        + "\n".join(f"- {err}" for err in errors)
        + "\n\n只重新输出一个合法的 JSON 对象，不要输出解释。"
    )
    result2, provider_notes2 = call_analysis_json_with_provider_fallback(
        system_prompt=system_prompt,
        user_prompt=repair_prompt,
        timeout=timeout,
        max_output_tokens=max_output_tokens,
        endpoint_mode=endpoint_mode,
        message_style=message_style,
        stream=stream,
    )
    notes.extend(provider_notes2)
    valid2, errors2 = validator(result2)
    if valid2:
        notes.append("repair: success")
        return result2, notes
    notes.extend([f"repair: {err}" for err in errors2])
    return result2 if isinstance(result2, dict) else result, notes


def render_focus_profile_markdown(profile: Dict[str, object]) -> str:
    out = ["# 聚焦领域任务画像", ""]
    out.append(f"- 聚焦关键词：{joined(profile.get('focus_terms', []) or [], '无')}")
    out.append(f"- 目标问题：{digest.safe_text(str(profile.get('target_problem_statement', '')))}")
    out.append(f"- 代表论文：{joined(profile.get('representative_paper_ids', []) or [], '无')}")
    out.append("")
    for key in [
        "target_tasks",
        "shared_assumptions",
        "recurring_setups",
        "main_bottlenecks",
        "underexplored_needs",
        "external_capability_gaps",
        "focus_method_patterns",
    ]:
        title = SECTION_LABELS[key]
        out.append(f"## {title}")
        rows = non_other(profile.get(key, []) or [])
        if not rows:
            out.append("- 无")
        else:
            for row in rows:
                out.append(f"- {row}")
        out.append("")
    note = digest.safe_text(str(profile.get("notes", "")))
    if note:
        out.append("## 备注")
        out.append(note)
        out.append("")
    return "\n".join(out).strip() + "\n"


def render_focus_landscape_markdown(summary: Dict[str, object]) -> str:
    out = ["# 聚焦领域趋势与热点", ""]
    out.append(digest.safe_text(str(summary.get("overall_summary", ""))) or "暂无总结。")
    out.append("")
    for topic in list(summary.get("focus_topics", []) or []):
        if not isinstance(topic, dict):
            continue
        out.append(f"## {digest.safe_text(str(topic.get('focus_term', '')))}")
        out.append(digest.safe_text(str(topic.get("trend_summary", ""))) or "暂无趋势总结。")
        out.append("")
        out.append("### 热点问题")
        rows = non_other(topic.get("hot_problems", []) or [])
        if not rows:
            out.append("- 无")
        else:
            for row in rows:
                out.append(f"- {row}")
        rep_ids = [digest.safe_text(str(pid)) for pid in list(topic.get("representative_paper_ids", []) or []) if digest.safe_text(str(pid))]
        if rep_ids:
            out.append("")
            out.append(f"### 代表论文\n- {'、'.join(rep_ids)}")
        out.append("")
    return "\n".join(out).strip() + "\n"


def render_transfer_synthesis_markdown(summary: Dict[str, object]) -> str:
    out = ["# 跨领域迁移组合总结", ""]
    out.append(digest.safe_text(str(summary.get("portfolio_summary", ""))) or "暂无总结。")
    out.append("")
    out.append("## 总体发现")
    for row in non_other(summary.get("overall_findings", []) or []):
        out.append(f"- {row}")
    out.append("")
    out.append("## 方法路线簇")
    for cluster in list(summary.get("method_clusters", []) or []):
        if not isinstance(cluster, dict):
            continue
        out.append(f"### {digest.safe_text(str(cluster.get('method_family', '')))}")
        out.append(f"- 对聚焦领域的价值：{digest.safe_text(str(cluster.get('why_it_matters_for_focus', '')))}")
        out.append(f"- 最相关来源领域：{joined(cluster.get('best_source_fields', []) or [], '无')}")
        out.append(f"- 代表论文：{joined(cluster.get('recommended_paper_ids', []) or [], '无')}")
        out.append(f"- 实施模式：{digest.safe_text(str(cluster.get('implementation_pattern', '')))}")
        out.append(f"- 常见改造：{joined(cluster.get('common_modifications', []) or [], '无')}")
        out.append(f"- 常见风险：{joined(cluster.get('common_risks', []) or [], '无')}")
        out.append("")
    out.append("## 优先推荐论文")
    for row in list(summary.get("top_recommendations", []) or []):
        if not isinstance(row, dict):
            continue
        out.append(
            f"- {digest.safe_text(str(row.get('paper_id', '')))} | "
            f"优先级={digest.safe_text(str(row.get('priority', '')))} | "
            f"{digest.safe_text(str(row.get('reason', '')))}"
        )
    out.append("")
    out.append("## 不建议重投入的方向")
    for row in non_other(summary.get("do_not_overinvest", []) or []):
        out.append(f"- {row}")
    out.append("")
    return "\n".join(out).strip() + "\n"


def render_paper_judgments_markdown(judgments: List[Dict[str, object]], paper_lookup: Dict[str, Dict[str, object]]) -> str:
    out = ["# 逐篇迁移判断", ""]
    by_decision = defaultdict(list)
    for judgment in judgments:
        by_decision[digest.safe_text(str(judgment.get("decision", "")))].append(judgment)
    for decision, title in (("keep", "建议保留"), ("maybe", "待验证"), ("reject", "不建议迁移")):
        rows = by_decision.get(decision, [])
        out.append(f"## {title}（{len(rows)}）")
        for judgment in rows:
            pid = digest.safe_text(str(judgment.get("paper_id", "")))
            paper = paper_lookup.get(pid, {})
            out.append(f"### [{pid}] {digest.safe_text(str(paper.get('title', '')))}")
            out.append(f"- 置信度：{digest.safe_text(str(judgment.get('confidence', '')))}")
            out.append(f"- 来源领域：{digest.safe_text(str(judgment.get('source_field', '')))}")
            out.append(f"- 方法路线：{joined(judgment.get('method_family', []) or [], '其他')}")
            out.append(f"- 目标问题：{joined(judgment.get('focus_problem_targets', []) or [], '无')}")
            out.append(f"- 可复用机制：{digest.safe_text(str(judgment.get('reusable_idea', '')))}")
            out.append(f"- 可迁移理由：{digest.safe_text(str(judgment.get('why_transferable', '')))}")
            out.append(f"- 应用路径：{digest.safe_text(str(judgment.get('adaptation_path', '')))}")
            out.append(f"- 必要改造：{joined(judgment.get('required_modifications', []) or [], '无')}")
            experiment = judgment.get("minimal_experiment", {}) or {}
            out.append(
                "- 最小实验："
                f"实验设置={digest.safe_text(str(experiment.get('setup', '')))} | "
                f"数据集={joined(experiment.get('datasets', []) or [], '无')} | "
                f"指标={joined(experiment.get('metrics', []) or [], '无')} | "
                f"基线={joined(experiment.get('baselines', []) or [], '无')} | "
                f"成功信号={digest.safe_text(str(experiment.get('success_signal', '')))}"
            )
            out.append(f"- 风险：{joined(judgment.get('risks', []) or [], '无')}")
            out.append(f"- 证据类型：{digest.safe_text(str(judgment.get('evidence_type', '')))}")
            if decision == "reject":
                out.append(f"- 不建议原因：{digest.safe_text(str(judgment.get('reject_reason', '')))}")
            out.append(f"- 简短结论：{digest.safe_text(str(judgment.get('reason_short', '')))}")
            out.append("")
        out.append("")
    return "\n".join(out).strip() + "\n"


def render_transfer_note_markdown(judgments: List[Dict[str, object]], paper_lookup: Dict[str, Dict[str, object]]) -> str:
    out = ["# 非聚焦论文迁移判断", ""]
    grouped = defaultdict(list)
    for row in judgments:
        grouped[digest.safe_text(str(row.get("decision", "")))].append(row)
    for decision, title in (("keep", "建议优先借鉴"), ("maybe", "建议先验证"), ("reject", "当前不建议迁移")):
        out.append(f"## {title}（{len(grouped.get(decision, []))}）")
        for row in grouped.get(decision, []):
            pid = digest.safe_text(str(row.get("paper_id", "")))
            paper = paper_lookup.get(pid, {})
            out.append(f"### [{pid}] {digest.safe_text(str(paper.get('title', '')))}")
            out.append(f"- 来源领域：{digest.safe_text(str(row.get('source_field', '')))}")
            out.append(f"- 简短结论：{digest.safe_text(str(row.get('reason_short', '')))}")
            if decision in {"keep", "maybe"}:
                out.append(f"- 迁移说明：{digest.safe_text(str(row.get('transfer_note', '')))}")
            out.append("")
        out.append("")
    return "\n".join(out).strip() + "\n"


def load_json_file(path: str) -> Optional[dict]:
    if not path or not os.path.exists(path):
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def archive_focus_transfer_root(root_dir: str, current_packet_name: str, archive_folder: str = "previous_packets") -> Tuple[str, int]:
    os.makedirs(root_dir, exist_ok=True)
    entries = sorted(os.listdir(root_dir))
    candidates: List[str] = []
    for name in entries:
        if name in {"", archive_folder, current_packet_name}:
            continue
        source = os.path.join(root_dir, name)
        if not os.path.exists(source):
            continue
        candidates.append(name)
    if not candidates:
        return os.path.join(root_dir, archive_folder), 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_batch_dir = os.path.join(root_dir, archive_folder, stamp)
    os.makedirs(archive_batch_dir, exist_ok=True)
    moved = 0
    for name in candidates:
        source = os.path.join(root_dir, name)
        target = os.path.join(archive_batch_dir, name)
        suffix = 1
        while os.path.exists(target):
            target = os.path.join(archive_batch_dir, f"{name}_{suffix}")
            suffix += 1
        shutil.move(source, target)
        moved += 1
    return archive_batch_dir, moved


def collect_report_translation_inputs(
    *,
    focus_terms: List[str],
    focus_profile: Optional[Dict[str, object]],
    judgments: List[Dict[str, object]],
    synthesis: Optional[Dict[str, object]],
    graph_data: Dict[str, object],
    paper_lookup: Dict[str, Dict[str, object]],
) -> List[str]:
    texts: List[str] = []

    def add(value: object) -> None:
        clean = digest.safe_text(str(value or "")).strip()
        if clean:
            texts.append(clean)

    def add_many(items: Iterable[object]) -> None:
        for item in items:
            add(item)

    add_many(focus_terms)
    for paper in paper_lookup.values():
        if isinstance(paper, dict):
            if not digest.safe_text(str(paper.get("title_zh", ""))):
                add(paper.get("title", ""))

    if focus_profile:
        add(focus_profile.get("target_problem_statement", ""))
        add(focus_profile.get("notes", ""))
        add_many(focus_profile.get("focus_terms", []) or [])
        add_many(focus_profile.get("target_tasks", []) or [])
        add_many(focus_profile.get("shared_assumptions", []) or [])
        add_many(focus_profile.get("recurring_setups", []) or [])
        add_many(focus_profile.get("main_bottlenecks", []) or [])
        add_many(focus_profile.get("underexplored_needs", []) or [])
        add_many(focus_profile.get("external_capability_gaps", []) or [])
        add_many(focus_profile.get("focus_method_patterns", []) or [])

    for judgment in judgments:
        if not isinstance(judgment, dict):
            continue
        add(judgment.get("source_field", ""))
        add(judgment.get("reason_short", ""))
        add(judgment.get("reusable_idea", ""))
        add(judgment.get("why_transferable", ""))
        add(judgment.get("adaptation_path", ""))
        add(judgment.get("reject_reason", ""))
        add_many(judgment.get("method_family", []) or [])
        add_many(judgment.get("focus_problem_targets", []) or [])
        add_many(judgment.get("required_modifications", []) or [])
        add_many(judgment.get("risks", []) or [])
        experiment = judgment.get("minimal_experiment", {}) or {}
        if isinstance(experiment, dict):
            add(experiment.get("setup", ""))
            add(experiment.get("success_signal", ""))
            add_many(experiment.get("datasets", []) or [])
            add_many(experiment.get("metrics", []) or [])
            add_many(experiment.get("baselines", []) or [])

    if synthesis:
        add(synthesis.get("portfolio_summary", ""))
        add_many(synthesis.get("overall_findings", []) or [])
        add_many(synthesis.get("do_not_overinvest", []) or [])
        for cluster in list(synthesis.get("method_clusters", []) or []):
            if not isinstance(cluster, dict):
                continue
            add(cluster.get("method_family", ""))
            add(cluster.get("why_it_matters_for_focus", ""))
            add(cluster.get("implementation_pattern", ""))
            add_many(cluster.get("best_source_fields", []) or [])
            add_many(cluster.get("recommended_paper_ids", []) or [])
            add_many(cluster.get("common_modifications", []) or [])
            add_many(cluster.get("common_risks", []) or [])
        for row in list(synthesis.get("top_recommendations", []) or []):
            if not isinstance(row, dict):
                continue
            add(row.get("priority", ""))
            add(row.get("reason", ""))

    for field in list(graph_data.get("fields", []) or []):
        if not isinstance(field, dict):
            continue
        add(field.get("label", ""))
        for method in list(field.get("top_methods", []) or []):
            if isinstance(method, dict):
                add(method.get("label", ""))
        for paper in list(field.get("papers", []) or []):
            if not isinstance(paper, dict):
                continue
            add(paper.get("title", ""))
            add(paper.get("decision", ""))
            add(paper.get("confidence", ""))
            add(paper.get("adaptation_path", ""))
            add(paper.get("reusable_idea", ""))
            add_many(paper.get("method_family", []) or [])

    for method in list(graph_data.get("methods", []) or []):
        if not isinstance(method, dict):
            continue
        add(method.get("label", ""))
        for paper in list(method.get("papers", []) or []):
            if not isinstance(paper, dict):
                continue
            add(paper.get("title", ""))
            add(paper.get("source_field", ""))
            add(paper.get("decision", ""))
            add(paper.get("confidence", ""))
            add(paper.get("adaptation_path", ""))

    for relation in list(graph_data.get("relations", []) or []):
        if isinstance(relation, dict):
            add(relation.get("field_label", ""))
            add(relation.get("method_label", ""))

    return texts


def localize_focus_profile(profile: Optional[Dict[str, object]], translation_map: Dict[str, str], focus_terms: List[str]) -> Optional[Dict[str, object]]:
    if not profile:
        return None
    localized = dict(profile)
    localized["focus_terms_zh"] = localize_items(profile.get("focus_terms", []) or focus_terms, translation_map)
    localized["target_problem_statement_zh"] = localize_text(profile.get("target_problem_statement", ""), translation_map, "暂无画像摘要")
    localized["notes_zh"] = localize_text(profile.get("notes", ""), translation_map)
    for key in SECTION_LABELS:
        localized[f"{key}_zh"] = localize_items(profile.get(key, []) or [], translation_map)
    return localized


def localize_judgments(
    judgments: List[Dict[str, object]],
    paper_lookup: Dict[str, Dict[str, object]],
    translation_map: Dict[str, str],
) -> List[Dict[str, object]]:
    localized_rows: List[Dict[str, object]] = []
    for row in judgments:
        if not isinstance(row, dict):
            continue
        pid = digest.safe_text(str(row.get("paper_id", "")))
        paper = paper_lookup.get(pid, {})
        title_zh = digest.safe_text(str(paper.get("title_zh", ""))) or localize_text(paper.get("title", ""), translation_map, pid)
        localized = dict(row)
        localized["paper_title_zh"] = title_zh or pid
        localized["decision_zh"] = localize_text(row.get("decision", ""), translation_map, "待判断")
        localized["confidence_zh"] = localize_text(row.get("confidence", ""), translation_map, "未标注")
        localized["source_field_zh"] = localize_text(row.get("source_field", ""), translation_map, "其他")
        localized["method_family_zh"] = localize_items(row.get("method_family", []) or [], translation_map, "其他")
        localized["focus_problem_targets_zh"] = localize_items(row.get("focus_problem_targets", []) or [], translation_map)
        localized["reusable_idea_zh"] = localize_text(row.get("reusable_idea", ""), translation_map)
        localized["why_transferable_zh"] = localize_text(row.get("why_transferable", ""), translation_map)
        localized["adaptation_path_zh"] = localize_text(row.get("adaptation_path", ""), translation_map)
        localized["required_modifications_zh"] = localize_items(row.get("required_modifications", []) or [], translation_map)
        localized["risks_zh"] = localize_items(row.get("risks", []) or [], translation_map)
        localized["evidence_type_zh"] = localize_text(row.get("evidence_type", ""), translation_map)
        localized["reason_short_zh"] = localize_text(row.get("reason_short", ""), translation_map)
        localized["reject_reason_zh"] = localize_text(row.get("reject_reason", ""), translation_map)
        experiment = row.get("minimal_experiment", {}) or {}
        if isinstance(experiment, dict):
            localized["minimal_experiment_zh"] = {
                "setup": localize_text(experiment.get("setup", ""), translation_map),
                "datasets": localize_items(experiment.get("datasets", []) or [], translation_map),
                "metrics": localize_items(experiment.get("metrics", []) or [], translation_map),
                "baselines": localize_items(experiment.get("baselines", []) or [], translation_map),
                "success_signal": localize_text(experiment.get("success_signal", ""), translation_map),
            }
        else:
            localized["minimal_experiment_zh"] = {"setup": "", "datasets": [], "metrics": [], "baselines": [], "success_signal": ""}
        localized_rows.append(localized)
    return localized_rows


def localize_synthesis(summary: Optional[Dict[str, object]], translation_map: Dict[str, str]) -> Optional[Dict[str, object]]:
    if not summary:
        return None
    localized = dict(summary)
    localized["portfolio_summary_zh"] = localize_text(summary.get("portfolio_summary", ""), translation_map)
    localized["overall_findings_zh"] = localize_items(summary.get("overall_findings", []) or [], translation_map)
    localized["do_not_overinvest_zh"] = localize_items(summary.get("do_not_overinvest", []) or [], translation_map)
    clusters_out: List[Dict[str, object]] = []
    for cluster in list(summary.get("method_clusters", []) or []):
        if not isinstance(cluster, dict):
            continue
        row = dict(cluster)
        row["method_family_zh"] = localize_text(cluster.get("method_family", ""), translation_map, "其他")
        row["why_it_matters_for_focus_zh"] = localize_text(cluster.get("why_it_matters_for_focus", ""), translation_map)
        row["best_source_fields_zh"] = localize_items(cluster.get("best_source_fields", []) or [], translation_map)
        row["implementation_pattern_zh"] = localize_text(cluster.get("implementation_pattern", ""), translation_map)
        row["common_modifications_zh"] = localize_items(cluster.get("common_modifications", []) or [], translation_map)
        row["common_risks_zh"] = localize_items(cluster.get("common_risks", []) or [], translation_map)
        clusters_out.append(row)
    localized["method_clusters_zh"] = clusters_out
    top_recommendations: List[Dict[str, object]] = []
    for row in list(summary.get("top_recommendations", []) or []):
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["priority_zh"] = localize_text(row.get("priority", ""), translation_map)
        item["reason_zh"] = localize_text(row.get("reason", ""), translation_map)
        top_recommendations.append(item)
    localized["top_recommendations_zh"] = top_recommendations
    return localized


def localize_graph_data(graph_data: Dict[str, object], translation_map: Dict[str, str]) -> Dict[str, object]:
    localized = {
        "stats": dict(graph_data.get("stats", {}) or {}),
        "fields": [],
        "methods": [],
        "relations": [],
    }
    for field in list(graph_data.get("fields", []) or []):
        if not isinstance(field, dict):
            continue
        row = dict(field)
        row["label"] = localize_text(field.get("label", ""), translation_map, "其他")
        row["top_methods"] = [
            {"label": localize_text(item.get("label", ""), translation_map, "其他"), "count": item.get("count", 0)}
            for item in list(field.get("top_methods", []) or [])
            if isinstance(item, dict)
        ]
        papers = []
        for paper in list(field.get("papers", []) or []):
            if not isinstance(paper, dict):
                continue
            item = dict(paper)
            item["title_zh"] = digest.safe_text(str(paper.get("title_zh", ""))) or localize_text(paper.get("title", ""), translation_map, item.get("id", ""))
            item["decision"] = localize_text(paper.get("decision", ""), translation_map)
            item["confidence"] = localize_text(paper.get("confidence", ""), translation_map)
            item["method_family"] = localize_items(paper.get("method_family", []) or [], translation_map, "其他")
            item["adaptation_path"] = localize_text(paper.get("adaptation_path", ""), translation_map)
            item["reusable_idea"] = localize_text(paper.get("reusable_idea", ""), translation_map)
            papers.append(item)
        row["papers"] = papers
        localized["fields"].append(row)

    for method in list(graph_data.get("methods", []) or []):
        if not isinstance(method, dict):
            continue
        row = dict(method)
        row["label"] = localize_text(method.get("label", ""), translation_map, "其他")
        summary = dict(method.get("summary", {}) or {})
        if summary:
            summary["method_family"] = localize_text(summary.get("method_family", ""), translation_map, "其他")
            summary["why_it_matters_for_focus"] = localize_text(summary.get("why_it_matters_for_focus", ""), translation_map)
            summary["best_source_fields"] = localize_items(summary.get("best_source_fields", []) or [], translation_map)
            summary["common_modifications"] = localize_items(summary.get("common_modifications", []) or [], translation_map)
            summary["common_risks"] = localize_items(summary.get("common_risks", []) or [], translation_map)
        row["summary"] = summary
        row["top_fields"] = [
            {"label": localize_text(item.get("label", ""), translation_map, "其他"), "count": item.get("count", 0)}
            for item in list(method.get("top_fields", []) or [])
            if isinstance(item, dict)
        ]
        papers = []
        for paper in list(method.get("papers", []) or []):
            if not isinstance(paper, dict):
                continue
            item = dict(paper)
            item["title_zh"] = digest.safe_text(str(paper.get("title_zh", ""))) or localize_text(paper.get("title", ""), translation_map, item.get("id", ""))
            item["source_field"] = localize_text(paper.get("source_field", ""), translation_map, "其他")
            item["decision"] = localize_text(paper.get("decision", ""), translation_map)
            item["confidence"] = localize_text(paper.get("confidence", ""), translation_map)
            item["adaptation_path"] = localize_text(paper.get("adaptation_path", ""), translation_map)
            papers.append(item)
        row["papers"] = papers
        localized["methods"].append(row)

    for relation in list(graph_data.get("relations", []) or []):
        if not isinstance(relation, dict):
            continue
        row = dict(relation)
        row["field_label"] = localize_text(relation.get("field_label", ""), translation_map, "其他")
        row["method_label"] = localize_text(relation.get("method_label", ""), translation_map, "其他")
        localized["relations"].append(row)

    return localized


def collect_extension_report_translation_inputs(
    *,
    focus_terms: List[str],
    focus_records: List[Dict[str, object]],
    focus_landscape: Optional[Dict[str, object]],
    judgments: List[Dict[str, object]],
    non_focus_records: List[Dict[str, object]],
) -> List[str]:
    texts: List[str] = []
    tag_counter: Counter[str] = Counter()
    area_counter: Counter[str] = Counter()

    def add(value: object) -> None:
        clean = digest.safe_text(str(value or "")).strip()
        if clean:
            texts.append(clean)

    def add_many(items: Iterable[object]) -> None:
        for item in items:
            add(item)

    add_many(focus_terms)
    for record in focus_records:
        if not isinstance(record, dict):
            continue
        if not digest.safe_text(str(record.get("title_zh", ""))):
            add(record.get("title", ""))
    for record in non_focus_records:
        if not isinstance(record, dict):
            continue
        if not digest.safe_text(str(record.get("title_zh", ""))):
            add(record.get("title", ""))
        major_area = digest.safe_text(str(record.get("major_area", "")))
        if major_area:
            area_counter[major_area] += 1
        for item in (
            list(record.get("domain_tags", []) or [])
            + list(record.get("task_tags", []) or [])
            + list(record.get("type_tags", []) or [])
            + list(record.get("keywords", []) or [])
        ):
            clean_item = digest.safe_text(str(item or "")).strip()
            if clean_item:
                tag_counter[clean_item] += 1

    add_many([item for item, _count in area_counter.most_common(24)])
    add_many([item for item, _count in tag_counter.most_common(120)])

    if focus_landscape:
        add(focus_landscape.get("overall_summary", ""))
        for topic in list(focus_landscape.get("focus_topics", []) or []):
            if not isinstance(topic, dict):
                continue
            add(topic.get("focus_term", ""))
            add(topic.get("trend_summary", ""))
            add_many(topic.get("hot_problems", []) or [])

    for row in judgments:
        if not isinstance(row, dict):
            continue
        add(row.get("source_field", ""))
        add(row.get("reason_short", ""))
        add(row.get("transfer_note", ""))

    return texts


def localize_focus_landscape(summary: Optional[Dict[str, object]], translation_map: Dict[str, str]) -> Optional[Dict[str, object]]:
    if not summary:
        return None
    localized = dict(summary)
    localized["overall_summary_zh"] = localize_text(summary.get("overall_summary", ""), translation_map, "暂无总结。")
    topics_out: List[Dict[str, object]] = []
    for topic in list(summary.get("focus_topics", []) or []):
        if not isinstance(topic, dict):
            continue
        row = dict(topic)
        row["focus_term_zh"] = localize_text(topic.get("focus_term", ""), translation_map, "聚焦主题")
        row["trend_summary_zh"] = localize_text(topic.get("trend_summary", ""), translation_map)
        row["hot_problems_zh"] = localize_items(topic.get("hot_problems", []) or [], translation_map)
        topics_out.append(row)
    localized["focus_topics_zh"] = topics_out
    return localized


def localize_transfer_note_rows(
    judgments: List[Dict[str, object]],
    paper_lookup: Dict[str, Dict[str, object]],
    translation_map: Dict[str, str],
) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for row in judgments:
        if not isinstance(row, dict):
            continue
        pid = digest.safe_text(str(row.get("paper_id", "")))
        paper = paper_lookup.get(pid, {})
        localized = dict(row)
        localized["paper_title_zh"] = digest.safe_text(str(paper.get("title_zh", ""))) or localize_text(paper.get("title", ""), translation_map, pid)
        localized["decision_zh"] = localize_text(row.get("decision", ""), translation_map)
        localized["source_field_zh"] = localize_text(row.get("source_field", ""), translation_map, "其他")
        localized["reason_short_zh"] = localize_text(row.get("reason_short", ""), translation_map)
        localized["transfer_note_zh"] = localize_text(row.get("transfer_note", ""), translation_map)
        out.append(localized)
    return out


def build_non_focus_graph_papers(
    non_focus_records: List[Dict[str, object]],
    localized_judgments: List[Dict[str, object]],
    translation_map: Dict[str, str],
) -> List[digest.Paper]:
    judgment_map = {
        digest.safe_text(str(row.get("paper_id", ""))): row
        for row in localized_judgments
        if digest.safe_text(str(row.get("paper_id", "")))
    }
    papers: List[digest.Paper] = []
    for record in non_focus_records:
        if not isinstance(record, dict):
            continue
        pid = paper_id(record)
        judgment = judgment_map.get(pid, {})
        decision = digest.safe_text(str(judgment.get("decision", "")))
        title = digest.safe_text(str(record.get("title", "")))
        title_zh = digest.safe_text(str(record.get("title_zh", ""))) or localize_text(title, translation_map, pid)
        summary_zh = digest.safe_text(str(record.get("summary_zh", ""))) or localize_text(record.get("summary_en", ""), translation_map)
        if decision in {"keep", "maybe"} and digest.safe_text(str(judgment.get("transfer_note_zh", ""))):
            prefix = "建议优先借鉴" if decision == "keep" else "建议先验证"
            summary_zh = f"{summary_zh}\n{TRANSFER_MARKER}{prefix}：{digest.safe_text(str(judgment.get('transfer_note_zh', '')))}"
        papers.append(
            digest.Paper(
                arxiv_id=pid,
                title=title,
                title_zh=title_zh,
                authors=[],
                published=digest.safe_text(str(record.get("published", ""))),
                updated=digest.safe_text(str(record.get("updated", ""))),
                categories=list(record.get("categories", []) or []),
                summary_en=digest.safe_text(str(record.get("summary_en", ""))),
                summary_zh=summary_zh,
                link_abs=digest.safe_text(str(record.get("link_abs", ""))),
                link_pdf=digest.safe_text(str(record.get("link_pdf", ""))),
                comment=digest.safe_text(str(record.get("comment", ""))),
                journal_ref=digest.safe_text(str(record.get("journal_ref", ""))),
                major_area=localize_text(record.get("major_area", ""), translation_map),
                domain_tags=localize_items(record.get("domain_tags", []) or [], translation_map),
                task_tags=localize_items(record.get("task_tags", []) or [], translation_map),
                type_tags=localize_items(record.get("type_tags", []) or [], translation_map),
                focus_tags=[],
                keywords=localize_items(record.get("keywords", []) or [], translation_map),
                accepted_venue="",
                accepted_hint="",
            )
        )
    return papers


def adapt_digest_knowledge_graph_html(html_block: str) -> str:
    out = html_block
    replacements = [
        ("id='knowledge-graph'", "id='transfer-explorer'"),
        ("Knowledge Graph", "主题探索器"),
        ("搜索主题或论文标题，例如 tracking / diffusion / domain shift", "搜索主题或论文标题"),
        ("Focus优先", "可迁移优先"),
        ("打开 arXiv 搜索", "打开论文搜索"),
        (">arXiv<", ">原文<"),
    ]
    for source, target in replacements:
        out = out.replace(source, target)
    return out


def render_non_focus_explorer_section(
    non_focus_records: List[Dict[str, object]],
    localized_judgments: List[Dict[str, object]],
    translation_map: Dict[str, str],
) -> str:
    papers = build_non_focus_graph_papers(non_focus_records, localized_judgments, translation_map)
    explorer_html = digest.render_knowledge_graph_section(papers)
    graph_style = """
<style>
.graph-section {
  padding: 0;
  overflow: hidden;
  border: 1px solid #0f4f72;
  box-shadow: 0 18px 50px rgba(15, 79, 114, 0.12);
}
.graph-hero {
  display: grid;
  grid-template-columns: 1.6fr 1fr;
  gap: 18px;
  padding: 22px;
  background:
    radial-gradient(circle at 10% 10%, rgba(255,255,255,0.92), rgba(255,255,255,0.72)),
    linear-gradient(135deg, #dff6ff 0%, #edfdf5 48%, #f4f7ff 100%);
  border-bottom: 1px solid rgba(15, 79, 114, 0.12);
}
.graph-kicker {
  margin: 0 0 8px;
  color: #0f4f72;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-size: 12px;
  font-weight: 700;
}
.graph-hero h2 { margin: 0 0 10px; font-size: 30px; }
.graph-stat-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  align-self: start;
}
.graph-stat-grid div {
  background: rgba(255,255,255,0.85);
  border: 1px solid rgba(15, 79, 114, 0.16);
  border-radius: 14px;
  padding: 14px 12px;
  text-align: center;
}
.graph-stat-grid strong { display: block; font-size: 28px; color: #0f4f72; }
.graph-stat-grid span { display: block; color: #5c6b7a; font-size: 13px; margin-top: 4px; }
.graph-toolbar-shell {
  padding: 16px 18px 14px;
  border-bottom: 1px solid rgba(15, 79, 114, 0.12);
  background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(247,251,255,0.98));
}
.graph-toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.graph-toolbar input {
  flex: 1 1 360px;
  min-width: 240px;
  border: 1px solid #c9dceb;
  border-radius: 999px;
  padding: 12px 16px;
  font-size: 14px;
  background: #fff;
}
.graph-toolbar-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.kg-toolbar-btn {
  border: 1px solid #d3e2ef;
  background: #fff;
  color: #0f4f72;
  border-radius: 999px;
  padding: 9px 14px;
  font-size: 13px;
  cursor: pointer;
}
.kg-toolbar-btn.is-active { background: #0f4f72; color: #fff; border-color: #0f4f72; }
.graph-spotlight-row {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  align-items: center;
  margin-top: 14px;
}
.graph-spotlight-label {
  color: #5c6b7a;
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.kg-spotlights { display: flex; flex-wrap: wrap; gap: 8px; }
.kg-spotlight-pill {
  border: 1px solid #d6e5f1;
  background: #fff;
  color: #0f4f72;
  border-radius: 999px;
  padding: 7px 12px;
  font-size: 12px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.kg-spotlight-pill span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  border-radius: 999px;
  background: #eef5fb;
  font-size: 11px;
}
.kg-spotlight-pill.is-active { background: #0f4f72; color: #fff; border-color: #0f4f72; }
.kg-spotlight-pill.is-active span { background: rgba(255,255,255,0.18); color: #fff; }
.graph-layout {
  display: grid;
  grid-template-columns: minmax(250px, 0.9fr) minmax(0, 1.45fr) minmax(320px, 0.95fr);
  gap: 0;
  min-height: 760px;
  align-items: stretch;
}
.kg-sidebar {
  padding: 18px 16px;
  background: linear-gradient(180deg, #fcfeff 0%, #f7fbff 100%);
  border-right: 1px solid rgba(15, 79, 114, 0.1);
  max-height: 760px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.kg-sidebar-head h3 { margin: 0 0 6px; font-size: 20px; }
.kg-sidebar-head p { margin: 0; color: #5c6b7a; font-size: 13px; line-height: 1.6; }
.kg-search-results {
  margin-top: 14px;
  padding: 12px;
  border-radius: 14px;
  border: 1px solid #dce8f2;
  background: #fff;
  overflow-y: auto;
  max-height: 260px;
}
.kg-result-block + .kg-result-block { margin-top: 12px; padding-top: 12px; border-top: 1px solid #edf2f7; }
.kg-result-block h4 { margin: 0 0 8px; font-size: 13px; color: #0f4f72; }
.kg-result-row {
  width: 100%;
  border: 0;
  background: #f8fbfd;
  border-radius: 12px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  cursor: pointer;
  text-align: left;
  margin-bottom: 8px;
}
.kg-result-row strong { font-size: 13px; color: #17324a; }
.kg-result-row span { font-size: 12px; color: #5c6b7a; }
.kg-result-paper {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  background: transparent;
  padding: 0;
}
.kg-result-paper button {
  width: 100%;
  border: 0;
  background: #f8fbfd;
  border-radius: 12px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  cursor: pointer;
  text-align: left;
}
.kg-result-paper a { color: #0f4f72; text-decoration: none; font-size: 12px; }
.kg-theme-list { margin-top: 14px; overflow-y: auto; padding-right: 4px; }
.kg-theme-item {
  width: 100%;
  border: 1px solid #d9e6f1;
  background: #fff;
  border-radius: 14px;
  padding: 12px;
  text-align: left;
  margin-bottom: 10px;
  cursor: pointer;
}
.kg-theme-item.is-active { border-color: #0f4f72; box-shadow: 0 10px 25px rgba(15, 79, 114, 0.12); }
.kg-theme-item-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.kg-theme-item-top strong { font-size: 14px; color: #17324a; }
.kg-theme-share { color: #5c6b7a; font-size: 12px; }
.kg-theme-item-badges { margin-top: 10px; }
.graph-canvas-wrap {
  position: relative;
  min-height: 760px;
  padding: 18px 14px 14px;
  background:
    radial-gradient(circle at center, rgba(235,248,255,0.9), rgba(247,250,252,0.95)),
    linear-gradient(180deg, #fbfdff 0%, #f7fbff 100%);
  border-right: 1px solid rgba(15, 79, 114, 0.1);
}
.graph-legend { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; margin-bottom: 8px; color: #5c6b7a; font-size: 12px; }
.graph-legend span { display: inline-flex; align-items: center; gap: 8px; }
.graph-legend-note { opacity: 0.8; }
.legend-dot { width: 12px; height: 12px; border-radius: 999px; display: inline-block; }
.legend-dot.keyword { background: #0f4f72; box-shadow: 0 0 0 3px rgba(15, 79, 114, 0.12); }
.legend-dot.secondary { background: #5b8db4; box-shadow: 0 0 0 3px rgba(91, 141, 180, 0.14); }
.legend-line { width: 20px; height: 2px; background: rgba(15, 79, 114, 0.24); display: inline-block; }
#kg-svg { width: 100%; height: 680px; display: block; cursor: grab; }
#kg-svg.is-dragging { cursor: grabbing; }
.graph-canvas-note { margin: 2px 8px 0; color: #5c6b7a; font-size: 12px; }
.kg-canvas-hud {
  position: absolute;
  right: 18px;
  top: 16px;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 16px;
  background: rgba(255,255,255,0.82);
  border: 1px solid rgba(15, 79, 114, 0.12);
  backdrop-filter: blur(10px);
  box-shadow: 0 12px 28px rgba(15, 79, 114, 0.12);
}
.kg-view-controls { display: flex; align-items: center; gap: 8px; }
.kg-view-btn {
  border: 1px solid rgba(15, 79, 114, 0.16);
  background: #fff;
  color: #0f4f72;
  min-width: 38px;
  height: 38px;
  border-radius: 12px;
  cursor: pointer;
  font-size: 18px;
  font-weight: 700;
  box-shadow: 0 2px 8px rgba(15, 79, 114, 0.08);
}
.kg-view-btn.is-wide { min-width: 64px; font-size: 13px; }
.kg-zoom-level { min-width: 58px; text-align: center; font-size: 13px; font-weight: 700; color: #17324a; }
.kg-edge { stroke: rgba(15, 79, 114, 0.2); stroke-width: 1.4; stroke-linecap: round; transition: stroke 140ms ease, opacity 140ms ease, stroke-width 140ms ease; }
.kg-edge-secondary { stroke: rgba(15, 79, 114, 0.08); }
.kg-edge-tertiary { stroke: rgba(91, 141, 180, 0.16); }
.kg-edge.is-highlight { stroke: rgba(15, 79, 114, 0.54); opacity: 1; }
.kg-edge.is-faded { opacity: 0.18; }
.kg-node { cursor: pointer; transition: opacity 160ms ease; }
.kg-node .kg-hit { fill: transparent; }
.kg-node .kg-halo { fill: rgba(15, 79, 114, 0.08); stroke: rgba(15, 79, 114, 0.14); stroke-width: 1.5; opacity: 0; transition: opacity 140ms ease, fill 140ms ease, stroke 140ms ease; }
.kg-node .kg-core { transition: fill 160ms ease, stroke 160ms ease, stroke-width 160ms ease; }
.kg-node-active .kg-core { fill: #f97316; stroke: #ffffff; stroke-width: 3; }
.kg-node-neighbor .kg-core { fill: #0f4f72; stroke: #ffffff; stroke-width: 2.5; }
.kg-node-secondary .kg-core { fill: #6f9abb; stroke: #ffffff; stroke-width: 2.1; }
.kg-node.is-selected .kg-halo, .kg-node.is-hovered .kg-halo { opacity: 1; }
.kg-node.is-hovered .kg-core { fill: #1d7aa5; stroke-width: 3.2; }
.kg-node.kg-node-secondary.is-hovered .kg-core { fill: #4c7ea5; stroke-width: 2.8; }
.kg-node.is-selected .kg-core { stroke-width: 4; }
.kg-node.is-soft-faded { opacity: 0.58; }
.kg-label {
  font-family: "Avenir Next", "SF Pro Display", "PingFang SC", "Noto Sans SC", -apple-system, BlinkMacSystemFont, sans-serif;
  fill: #18324a;
  pointer-events: none;
  paint-order: stroke;
  stroke: rgba(255,255,255,0.96);
  stroke-width: 6px;
  stroke-linejoin: round;
}
.keyword-label { font-size: 17px; font-weight: 700; letter-spacing: 0.01em; }
.keyword-label.is-active { font-size: 23px; font-weight: 800; }
.keyword-label.is-neighbor { font-size: 18px; }
.keyword-label.is-secondary { font-size: 13px; font-weight: 700; }
.kg-panel {
  padding: 20px 18px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbfd 100%);
  min-height: 760px;
  max-height: 760px;
  overflow-y: auto;
  overflow-x: hidden;
}
.kg-panel h3 { margin-top: 0; font-size: 22px; }
.kg-panel-desc { color: #213547; line-height: 1.7; }
.graph-panel-note { color: #5c6b7a; font-size: 13px; }
.kg-panel-links { margin: 14px 0 6px; }
.kg-panel-links a {
  text-decoration: none;
  color: #084d3a;
  background: #e9f8f0;
  border: 1px solid #b7e7d8;
  padding: 7px 12px;
  border-radius: 999px;
  font-size: 13px;
}
.kg-chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.kg-chip {
  display: inline-flex;
  align-items: center;
  padding: 4px 9px;
  border-radius: 999px;
  background: #eef5fb;
  color: #0f4f72;
  font-size: 12px;
  border: 1px solid #d5e6f2;
}
.kg-chip-soft { background: #fff; border-color: #e3edf5; }
.kg-metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 14px 0 6px; }
.kg-metric-grid div { border: 1px solid #e1ecf4; border-radius: 12px; padding: 10px; background: #fff; text-align: center; }
.kg-metric-grid strong { display: block; color: #0f4f72; font-size: 20px; }
.kg-metric-grid span { display: block; margin-top: 4px; color: #5c6b7a; font-size: 12px; }
.kg-subblock { margin-top: 16px; padding-top: 14px; border-top: 1px solid #e2ebf3; }
.kg-subgrid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.kg-subblock h4 { margin: 0 0 10px; }
.kg-related-list { margin: 0; padding-left: 18px; }
.kg-related-list li { margin-bottom: 8px; }
.kg-related-list a { color: #0f4f72; text-decoration: none; }
.kg-inline-btn { border: 0; background: transparent; color: #0f4f72; padding: 0; cursor: pointer; font: inherit; text-decoration: underline; }
.kg-related-score { display: inline-block; margin-left: 8px; color: #5c6b7a; font-size: 12px; }
.kg-paper-list li p { margin: 4px 0 0; color: #5c6b7a; font-size: 12px; line-height: 1.5; }
.kg-paper-list { padding-left: 0; list-style: none; }
.kg-paper-item { border-radius: 12px; padding: 8px 10px; background: #fbfdff; border: 1px solid transparent; }
.kg-paper-item.is-highlight { border-color: #f97316; background: #fff7ed; }
.kg-paper-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
#transfer-explorer .kg-paper-item.is-transferable {
  border-color: #86efac;
  background: linear-gradient(180deg, #f8fffb 0%, #effcf5 100%);
  box-shadow: inset 0 0 0 1px rgba(22, 163, 74, 0.08);
}
#transfer-explorer .kg-paper-summary { margin: 6px 0 0; }
#transfer-explorer .kg-paper-transfer {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  background: #edf7f3;
  color: #0b6b51;
  border: 1px solid #cce8dd;
  font-size: 11px;
  line-height: 1.2;
  white-space: nowrap;
}
#transfer-explorer .kg-paper-transfer-inline { margin-left: 8px; }
#transfer-explorer .kg-transfer-block {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid #bbf7d0;
  border-left: 4px solid #16a34a;
  background: #ecfdf5;
}
#transfer-explorer .kg-transfer-label {
  color: #047857;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
}
#transfer-explorer .kg-transfer-note {
  margin: 6px 0 0;
  color: #065f46;
  font-size: 13px;
  line-height: 1.7;
  font-weight: 700;
}
.kg-empty { color: #5c6b7a; font-size: 13px; margin: 0; }
.kg-tooltip {
  position: absolute;
  z-index: 5;
  max-width: 280px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(16, 24, 40, 0.92);
  color: #fff;
  box-shadow: 0 18px 38px rgba(16, 24, 40, 0.25);
  pointer-events: none;
}
.kg-tooltip strong { display: block; font-size: 13px; }
.kg-tooltip span { display: block; margin-top: 4px; font-size: 12px; color: rgba(255,255,255,0.82); }
@media (max-width: 980px) {
  .graph-hero { grid-template-columns: 1fr; }
  .graph-layout { grid-template-columns: 1fr; }
  .kg-sidebar { max-height: none; border-right: 0; border-bottom: 1px solid rgba(15, 79, 114, 0.1); }
  .graph-canvas-wrap { min-height: 560px; border-right: 0; border-bottom: 1px solid rgba(15, 79, 114, 0.1); }
  .kg-canvas-hud { right: 12px; top: 12px; padding: 8px 10px; }
  .kg-view-btn { min-width: 34px; height: 34px; }
  #kg-svg { height: 560px; }
  .kg-panel { min-height: auto; max-height: none; }
  .kg-subgrid { grid-template-columns: 1fr; }
  .graph-stat-grid { grid-template-columns: repeat(3, 1fr); }
}
</style>
"""
    return graph_style + adapt_digest_knowledge_graph_html(explorer_html)


def resolve_main_digest_paths(payload: Dict[str, object], digest_json_path: str) -> Dict[str, str]:
    notes = payload.get("notes") or {}
    output_suffix = digest.normalize_output_suffix(str(notes.get("output_suffix", "")))
    suffix_part = f"_{output_suffix}" if output_suffix else ""
    digest_json_abs = os.path.abspath(digest_json_path)
    data_dir = os.path.abspath(str(notes.get("data_dir", ""))) if digest.safe_text(str(notes.get("data_dir", ""))) else os.path.dirname(digest_json_abs)
    root_dir = os.path.dirname(data_dir)
    report_dir = os.path.abspath(str(notes.get("report_dir", ""))) if digest.safe_text(str(notes.get("report_dir", ""))) else os.path.join(root_dir, "reports")
    date_str = digest.safe_text(str(payload.get("date", ""))) or dt.date.today().isoformat()
    html_path = digest.safe_text(str(notes.get("html_path", ""))) or os.path.join(report_dir, f"arxiv_digest_{date_str}{suffix_part}.html")
    md_path = digest.safe_text(str(notes.get("markdown_path", ""))) or os.path.join(report_dir, f"arxiv_digest_{date_str}{suffix_part}.md")
    json_path = digest.safe_text(str(notes.get("json_path", ""))) or os.path.join(data_dir, f"arxiv_digest_{date_str}{suffix_part}.json")
    json_latest_path = digest.safe_text(str(notes.get("json_latest_path", ""))) or os.path.join(data_dir, "last_success_digest.json")
    return {
        "report_dir": report_dir,
        "data_dir": data_dir,
        "html_path": html_path,
        "md_path": md_path,
        "json_path": json_path,
        "json_latest_path": json_latest_path,
    }


def build_integrated_main_graph_papers(
    payload: Dict[str, object],
    localized_judgments: List[Dict[str, object]],
    translation_map: Dict[str, str],
) -> List[digest.Paper]:
    judgment_map = {
        digest.safe_text(str(row.get("paper_id", ""))): row
        for row in localized_judgments
        if digest.safe_text(str(row.get("paper_id", "")))
    }
    papers: List[digest.Paper] = []
    for record in collect_all_digest_papers(payload):
        if not isinstance(record, dict):
            continue
        pid = paper_id(record)
        localized = dict(record)
        title = digest.safe_text(str(record.get("title", "")))
        title_zh = digest.safe_text(str(record.get("title_zh", ""))) or localize_text(title, translation_map, pid)
        summary_zh = digest.safe_text(str(record.get("summary_zh", ""))) or localize_text(record.get("summary_en", ""), translation_map)
        judgment = judgment_map.get(pid, {})
        decision = digest.safe_text(str(judgment.get("decision", "")))
        transfer_note_zh = digest.safe_text(str(judgment.get("transfer_note_zh", "")))
        if decision in {"keep", "maybe"} and transfer_note_zh:
            prefix = "建议优先借鉴" if decision == "keep" else "建议先验证"
            summary_zh = f"{summary_zh}\n{TRANSFER_MARKER}{prefix}：{transfer_note_zh}"
        localized["title_zh"] = title_zh
        localized["summary_zh"] = summary_zh
        papers.append(digest.paper_from_dict(localized))
    return digest.dedupe_papers(papers)


def adapt_main_digest_knowledge_graph_html(html_block: str) -> str:
    # The main digest graph now understands transfer notes natively. Keep this
    # compatibility hook as a no-op so older call sites do not inject duplicate JS.
    return html_block


def render_main_digest_graph_section(
    payload: Dict[str, object],
    localized_judgments: List[Dict[str, object]],
    translation_map: Dict[str, str],
) -> str:
    papers = build_integrated_main_graph_papers(payload, localized_judgments, translation_map)
    explorer_html = digest.render_knowledge_graph_section(papers)
    graph_style = """
<style>
#knowledge-graph .kg-paper-item.is-transferable {
  border-color: #86efac;
  background: linear-gradient(180deg, #f8fffb 0%, #effcf5 100%);
  box-shadow: inset 0 0 0 1px rgba(22, 163, 74, 0.08);
}
#knowledge-graph .kg-paper-transfer {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  background: #edf7f3;
  color: #0b6b51;
  border: 1px solid #cce8dd;
  font-size: 11px;
  line-height: 1.2;
  white-space: nowrap;
}
#knowledge-graph .kg-paper-transfer-inline { margin-left: 8px; }
#knowledge-graph .kg-paper-summary { margin: 6px 0 0; }
#knowledge-graph .kg-transfer-block {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid #bbf7d0;
  border-left: 4px solid #16a34a;
  background: #ecfdf5;
}
#knowledge-graph .kg-transfer-label {
  color: #047857;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
}
#knowledge-graph .kg-transfer-note {
  margin: 6px 0 0;
  color: #065f46;
  font-size: 13px;
  line-height: 1.7;
  font-weight: 700;
}
</style>
"""
    return graph_style + adapt_main_digest_knowledge_graph_html(explorer_html)


def render_main_digest_focus_trends_section(
    focus_landscape: Optional[Dict[str, object]],
    focus_records: List[Dict[str, object]],
    translation_map: Dict[str, str],
) -> str:
    localized_landscape = localize_focus_landscape(focus_landscape, translation_map)
    if not localized_landscape:
        return ""
    focus_record_lookup = {paper_id(record): record for record in focus_records if paper_id(record)}
    trend_cards = ""
    if list(localized_landscape.get("focus_topics_zh", []) or []):
        blocks = []
        for topic in list(localized_landscape.get("focus_topics_zh", []) or []):
            if not isinstance(topic, dict):
                continue
            hot_problems = topic.get("hot_problems_zh", []) or []
            hot_html = "<ul class='focus-transfer-plain-list'>" + "".join(f"<li>{h(item)}</li>" for item in hot_problems) + "</ul>" if hot_problems else "<p class='focus-transfer-empty-note'>暂无热点问题总结。</p>"
            rep_ids = [digest.safe_text(str(pid)) for pid in list(topic.get("representative_paper_ids", []) or []) if digest.safe_text(str(pid))]
            rep_links: List[str] = []
            for pid in list(dict.fromkeys(rep_ids)):
                record = focus_record_lookup.get(pid, {})
                url = digest.safe_text(str(record.get("link_abs", ""))) or f"https://arxiv.org/abs/{pid}"
                title_zh = digest.safe_text(str(record.get("title_zh", ""))) or localize_text(record.get("title", ""), translation_map, pid)
                label = f"{title_zh}（{pid}）" if title_zh and title_zh != pid else pid
                rep_links.append(
                    f"<a class='focus-transfer-trend-link' href='{h(url)}' target='_blank' rel='noopener noreferrer'>{h(label)}</a>"
                )
            blocks.append(
                "<article class='focus-transfer-trend-card'>"
                f"<h3>{h(topic.get('focus_term_zh', '聚焦主题'))}</h3>"
                f"<p class='focus-transfer-trend-summary'>{h(topic.get('trend_summary_zh', '') or '暂无趋势总结。')}</p>"
                "<h4>热点问题</h4>"
                f"{hot_html}"
                + (
                    "<div class='focus-transfer-trend-meta'><strong>代表论文：</strong>"
                    f"<div class='focus-transfer-trend-links'>{''.join(rep_links)}</div></div>"
                    if rep_links
                    else ""
                )
                + "</article>"
            )
        trend_cards = "".join(blocks)
    return (
        "<style>"
        ".focus-transfer-trends-section { margin-top: 18px; }"
        ".focus-transfer-section-head { display: grid; grid-template-columns: minmax(0, 280px) minmax(0, 1fr); gap: 16px; align-items: start; margin-bottom: 14px; }"
        ".focus-transfer-section-kicker { margin: 0 0 6px; color: #0f4f72; font-size: 12px; font-weight: 700; letter-spacing: 0.08em; }"
        ".focus-transfer-section-note { color: #213547; line-height: 1.7; margin: 0 0 12px; }"
        ".focus-transfer-trend-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }"
        ".focus-transfer-trend-card { border: 1px solid #d9e2ec; border-radius: 16px; background: #fff; padding: 16px; }"
        ".focus-transfer-trend-summary { margin: 0 0 14px; color: #213547; line-height: 1.8; }"
        ".focus-transfer-trend-meta { margin: 12px 0 0; color: #5c6b7a; font-size: 13px; }"
        ".focus-transfer-trend-links { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }"
        ".focus-transfer-trend-link { display: inline-flex; align-items: center; padding: 7px 10px; border-radius: 999px; text-decoration: none; color: #0f4f72; background: #eef5fb; border: 1px solid #d5e6f2; line-height: 1.5; }"
        ".focus-transfer-trend-link:hover { background: #e2f1fb; }"
        ".focus-transfer-plain-list { margin: 0; padding-left: 18px; }"
        ".focus-transfer-plain-list li { margin-bottom: 8px; }"
        ".focus-transfer-empty-note { color: #5c6b7a; margin: 0; }"
        "@media (max-width: 1100px) { .focus-transfer-trend-grid { grid-template-columns: 1fr; } .focus-transfer-section-head { grid-template-columns: 1fr; } }"
        "</style>"
        "<section id='focus-transfer-trends' class='focus-transfer-trends-section'>"
        "<div class='focus-transfer-section-head'>"
        "<div><p class='focus-transfer-section-kicker'>聚焦领域总结</p><h2>发展趋势与热点问题</h2></div>"
        f"<p class='focus-transfer-section-note'>{h((localized_landscape or {}).get('overall_summary_zh', '') or '当前还没有模型生成的聚焦领域趋势总结。')}</p>"
        "</div>"
        + (f"<div class='focus-transfer-trend-grid'>{trend_cards}</div>" if trend_cards else "")
        + "</section>"
    )


def sync_focus_transfer_into_main_digest(
    *,
    digest_json_path: str,
    payload: Dict[str, object],
    prepared: Dict[str, object],
    focus_landscape: Optional[Dict[str, object]],
    judgments: List[Dict[str, object]],
    manifest_path: str,
    translation_map: Optional[Dict[str, str]] = None,
    focus_memory_entries: Optional[List[Dict[str, object]]] = None,
) -> Dict[str, str]:
    paths = resolve_main_digest_paths(payload, digest_json_path)
    if translation_map is None:
        translation_cache_path = os.path.join(os.path.dirname(manifest_path), "html_translation_cache.json")
        translation_map = translate_texts_for_report(
            collect_extension_report_translation_inputs(
                focus_terms=prepared["focus_terms"],
                focus_records=prepared["focus_records"],
                focus_landscape=focus_landscape,
                judgments=judgments,
                non_focus_records=prepared["non_focus_records"],
            ),
            translation_cache_path,
            log_label="主报告分析回写翻译",
        )
    localized_judgments = localize_transfer_note_rows(
        judgments,
        {paper_id(record): record for record in prepared["focus_records"] + prepared["non_focus_records"]},
        translation_map,
    )
    paper_lookup = {
        paper_id(record): record
        for record in prepared["focus_records"] + prepared["non_focus_records"]
        if isinstance(record, dict) and paper_id(record)
    }
    localized_judgment_lookup = {
        digest.safe_text(str(row.get("paper_id", ""))): row
        for row in localized_judgments
        if digest.safe_text(str(row.get("paper_id", "")))
    }
    keep_targets: List[Dict[str, str]] = []
    for row in judgments:
        if digest.safe_text(str(row.get("decision", ""))) != "keep":
            continue
        pid = digest.safe_text(str(row.get("paper_id", "")))
        if not pid:
            continue
        record = paper_lookup.get(pid, {})
        localized = localized_judgment_lookup.get(pid, {})
        title = digest.safe_text(str(record.get("title", "")))
        title_zh = digest.safe_text(str(localized.get("paper_title_zh", ""))) or digest.safe_text(str(record.get("title_zh", ""))) or localize_text(title, translation_map, pid)
        keep_targets.append(
            {
                "paper_id": pid,
                "title": title,
                "title_zh": title_zh or pid,
            }
        )
    graph_html_override = render_main_digest_graph_section(payload, localized_judgments, translation_map)
    after_graph_html = render_main_digest_focus_trends_section(focus_landscape, prepared["focus_records"], translation_map)
    keep_count = sum(1 for row in judgments if digest.safe_text(str(row.get("decision", ""))) == "keep")
    maybe_count = sum(1 for row in judgments if digest.safe_text(str(row.get("decision", ""))) == "maybe")
    analyzed_candidate_count = len({digest.safe_text(str(row.get("paper_id", ""))) for row in judgments if digest.safe_text(str(row.get("paper_id", "")))})
    total_non_focus_count = len(prepared["non_focus_records"])
    total_transfer_candidate_count = len(list(prepared.get("transfer_candidate_records", []) or []))
    focus_transfer_meta = {
        "status": "analyzed" if (focus_landscape or judgments) else "not_analyzed",
        "status_zh": "已完成可迁移性分析" if (focus_landscape or judgments) else "当前未分析可迁移性",
        "note": (
            f"本次已分析 {analyzed_candidate_count} / {total_transfer_candidate_count} 篇非 focus 中稿候选；全部非 focus 论文共 {total_non_focus_count} 篇。其中建议迁移 {keep_count} 篇，待验证 {maybe_count} 篇。"
            if (focus_landscape or judgments)
            else "当前还没有可写回主日报的可迁移性分析结果。"
        ),
        "keep_count": keep_count,
        "maybe_count": maybe_count,
        "keep_targets": keep_targets,
        "analyzed_candidate_count": analyzed_candidate_count,
        "transfer_candidate_count": total_transfer_candidate_count,
        "transfer_candidate_scope": prepared.get("transfer_candidate_scope", "accepted_non_focus"),
        "total_non_focus_count": total_non_focus_count,
        "manifest_path": manifest_path,
        "focus_memory_files": [
            {key: value for key, value in entry.items() if key != "content"}
            for entry in (focus_memory_entries or [])
        ],
    }
    daily_groups = {k: digest.papers_from_dicts(v) for k, v in (payload.get("daily_groups", {}) or {}).items()}
    focus_latest = digest.papers_from_dicts(payload.get("focus_latest", []) or [])
    focus_hot = digest.papers_from_dicts(payload.get("focus_hot", []) or [])
    venue_pool = digest.papers_from_dicts(payload.get("venue_pool", []) or [])
    venue_watch = digest.papers_from_dicts(payload.get("venue_watch", []) or [])
    report_title = digest.resolve_report_title(list(payload.get("categories", []) or []))
    target_day = dt.date.fromisoformat(digest.safe_text(str(payload.get("date", ""))) or dt.date.today().isoformat())
    timezone_name = digest.safe_text(str(payload.get("timezone", ""))) or "Asia/Shanghai"
    fetch_summary = ((payload.get("notes") or {}).get("fetch_report_summary", {}) or {})
    html_text = digest.render_html_report(
        report_title,
        target_day,
        timezone_name,
        daily_groups,
        focus_latest,
        focus_hot,
        venue_pool,
        venue_watch,
        report_meta={
            "fetch_summary": fetch_summary,
            "focus_transfer": focus_transfer_meta,
            "graph_html_override": graph_html_override,
            "after_graph_html": after_graph_html,
        },
    )
    digest.write_text(paths["html_path"], html_text)

    payload_notes = payload.setdefault("notes", {})
    payload_notes.pop("html_latest_path", None)
    payload_notes.pop("markdown_latest_path", None)
    payload_notes["focus_transfer"] = {
        "status": focus_transfer_meta["status"],
        "status_zh": focus_transfer_meta["status_zh"],
        "keep_count": keep_count,
        "maybe_count": maybe_count,
        "keep_targets": keep_targets,
        "analyzed_candidate_count": analyzed_candidate_count,
        "transfer_candidate_count": total_transfer_candidate_count,
        "transfer_candidate_scope": prepared.get("transfer_candidate_scope", "accepted_non_focus"),
        "total_non_focus_count": total_non_focus_count,
        "manifest_path": manifest_path,
        "focus_memory_files": [
            {key: value for key, value in entry.items() if key != "content"}
            for entry in (focus_memory_entries or [])
        ],
        "integrated_into_main_report": True,
    }
    for json_target in [paths["json_path"], paths["json_latest_path"]]:
        if json_target:
            digest.dump_json(json_target, payload)
    return paths


def build_method_clusters(judgments: List[Dict[str, object]]) -> List[Dict[str, object]]:
    clusters: Dict[str, Dict[str, object]] = {}
    for judgment in judgments:
        if digest.safe_text(str(judgment.get("decision", ""))) == "reject":
            continue
        families = non_other(judgment.get("method_family", []) or [])
        primary_family = families[0] if families else "other"
        cluster = clusters.setdefault(
            primary_family,
            {
                "method_family": primary_family,
                "paper_ids": [],
                "source_fields": [],
                "focus_problem_targets": [],
                "common_risks": [],
                "required_modifications": [],
                "decision_counter": Counter(),
            },
        )
        pid = digest.safe_text(str(judgment.get("paper_id", "")))
        if pid and pid not in cluster["paper_ids"]:
            cluster["paper_ids"].append(pid)
        cluster["source_fields"].extend(non_other([judgment.get("source_field", "")]))
        cluster["focus_problem_targets"].extend(non_other(judgment.get("focus_problem_targets", []) or []))
        cluster["common_risks"].extend(non_other(judgment.get("risks", []) or []))
        cluster["required_modifications"].extend(non_other(judgment.get("required_modifications", []) or []))
        cluster["decision_counter"][digest.safe_text(str(judgment.get("decision", "")))] += 1

    out: List[Dict[str, object]] = []
    for cluster in clusters.values():
        out.append(
            {
                "method_family": cluster["method_family"],
                "paper_count": len(cluster["paper_ids"]),
                "paper_ids": cluster["paper_ids"],
                "top_source_fields": [row for row, _count in Counter(cluster["source_fields"]).most_common(6)],
                "top_focus_targets": [row for row, _count in Counter(cluster["focus_problem_targets"]).most_common(6)],
                "top_risks": [row for row, _count in Counter(cluster["common_risks"]).most_common(6)],
                "top_modifications": [row for row, _count in Counter(cluster["required_modifications"]).most_common(6)],
                "decision_counter": dict(cluster["decision_counter"]),
            }
        )
    out.sort(key=lambda row: (int(row["paper_count"]), row["method_family"]), reverse=True)
    return out


def build_quality_gate(
    focus_profile: Optional[Dict[str, object]],
    analyzed_candidates: List[Dict[str, object]],
    judgments: List[Dict[str, object]],
    synthesis: Optional[Dict[str, object]],
) -> Dict[str, object]:
    candidate_ids = {paper_id(record) for record in analyzed_candidates if paper_id(record)}
    judgment_ids = {digest.safe_text(str(row.get("paper_id", ""))) for row in judgments if digest.safe_text(str(row.get("paper_id", "")))}
    missing_ids = sorted(candidate_ids - judgment_ids)
    extra_ids = sorted(judgment_ids - candidate_ids)
    invalid_judgments: List[str] = []
    decision_counter = Counter()
    for judgment in judgments:
        pid = digest.safe_text(str(judgment.get("paper_id", "")))
        decision = digest.safe_text(str(judgment.get("decision", "")))
        if pid:
            decision_counter[decision] += 1
        if decision not in DECISION_VALUES:
            invalid_judgments.append(pid or "<unknown>")
    transferable = [row for row in judgments if digest.safe_text(str(row.get("decision", ""))) in {"keep", "maybe"}]
    gate = {
        "focus_profile_present": bool(focus_profile),
        "candidate_count": len(candidate_ids),
        "judgment_count": len(judgments),
        "missing_candidate_ids": missing_ids,
        "extra_candidate_ids": extra_ids,
        "invalid_judgment_ids": invalid_judgments,
        "decision_counter": dict(decision_counter),
        "transferable_count": len(transferable),
        "synthesis_present": bool(synthesis),
        "passed": bool(focus_profile) and not missing_ids and not extra_ids and not invalid_judgments,
    }
    return gate


def build_graph_data(
    judgments: List[Dict[str, object]],
    paper_lookup: Dict[str, Dict[str, object]],
    synthesis: Optional[Dict[str, object]],
) -> Dict[str, object]:
    transferable = [row for row in judgments if digest.safe_text(str(row.get("decision", ""))) in {"keep", "maybe"}]
    field_nodes: Dict[str, Dict[str, object]] = {}
    method_nodes: Dict[str, Dict[str, object]] = {}
    edges: Dict[Tuple[str, str], Dict[str, object]] = {}
    cluster_map: Dict[str, Dict[str, object]] = {}
    for cluster in list((synthesis or {}).get("method_clusters", []) or []):
        if isinstance(cluster, dict):
            method = digest.safe_text(str(cluster.get("method_family", "")))
            if method:
                cluster_map[method] = cluster

    for row in transferable:
        pid = digest.safe_text(str(row.get("paper_id", "")))
        if not pid:
            continue
        field = digest.safe_text(str(row.get("source_field", ""))) or "other"
        method = non_other(row.get("method_family", []) or [])
        primary_method = method[0] if method else "other"
        paper = paper_lookup.get(pid, {})
        field_node = field_nodes.setdefault(
            field,
            {
                "id": f"field:{digest.normalize_output_suffix(field) or 'other'}",
                "label": field,
                "paper_ids": [],
                "methods": Counter(),
                "decision_counter": Counter(),
            },
        )
        if pid not in field_node["paper_ids"]:
            field_node["paper_ids"].append(pid)
        field_node["methods"][primary_method] += 1
        field_node["decision_counter"][digest.safe_text(str(row.get("decision", "")))] += 1

        method_node = method_nodes.setdefault(
            primary_method,
            {
                "id": f"method:{digest.normalize_output_suffix(primary_method) or 'other'}",
                "label": primary_method,
                "paper_ids": [],
                "fields": Counter(),
                "summary": cluster_map.get(primary_method, {}),
            },
        )
        if pid not in method_node["paper_ids"]:
            method_node["paper_ids"].append(pid)
        method_node["fields"][field] += 1

        key = (field, primary_method)
        edge = edges.setdefault(
            key,
            {
                "source_field": field,
                "method_family": primary_method,
                "paper_ids": [],
                "count": 0,
            },
        )
        if pid not in edge["paper_ids"]:
            edge["paper_ids"].append(pid)
            edge["count"] += 1

    field_rows: List[Dict[str, object]] = []
    for field, node in field_nodes.items():
        papers = []
        for pid in node["paper_ids"]:
            paper = paper_lookup.get(pid, {})
            judgment = next((item for item in transferable if digest.safe_text(str(item.get("paper_id", ""))) == pid), {})
            papers.append(
                {
                    "id": pid,
                    "title": digest.safe_text(str(paper.get("title", ""))),
                    "title_zh": digest.safe_text(str(paper.get("title_zh", ""))),
                    "url": digest.safe_text(str(paper.get("link_abs", ""))),
                    "decision": digest.safe_text(str(judgment.get("decision", ""))),
                    "confidence": digest.safe_text(str(judgment.get("confidence", ""))),
                    "method_family": non_other(judgment.get("method_family", []) or []),
                    "adaptation_path": digest.safe_text(str(judgment.get("adaptation_path", ""))),
                    "reusable_idea": digest.safe_text(str(judgment.get("reusable_idea", ""))),
                }
            )
        papers.sort(key=lambda item: (item["decision"] != "keep", item["confidence"] != "high", item["title"]))
        field_rows.append(
            {
                "id": node["id"],
                "label": field,
                "paper_count": len(node["paper_ids"]),
                "method_count": len(node["methods"]),
                "top_methods": [{"label": label, "count": count} for label, count in node["methods"].most_common(8)],
                "decision_counter": dict(node["decision_counter"]),
                "papers": papers,
            }
        )

    method_rows: List[Dict[str, object]] = []
    for method, node in method_nodes.items():
        papers = []
        for pid in node["paper_ids"]:
            paper = paper_lookup.get(pid, {})
            judgment = next((item for item in transferable if digest.safe_text(str(item.get("paper_id", ""))) == pid), {})
            papers.append(
                {
                    "id": pid,
                    "title": digest.safe_text(str(paper.get("title", ""))),
                    "title_zh": digest.safe_text(str(paper.get("title_zh", ""))),
                    "url": digest.safe_text(str(paper.get("link_abs", ""))),
                    "source_field": digest.safe_text(str(judgment.get("source_field", ""))),
                    "decision": digest.safe_text(str(judgment.get("decision", ""))),
                    "confidence": digest.safe_text(str(judgment.get("confidence", ""))),
                    "adaptation_path": digest.safe_text(str(judgment.get("adaptation_path", ""))),
                }
            )
        method_rows.append(
            {
                "id": node["id"],
                "label": method,
                "paper_count": len(node["paper_ids"]),
                "field_count": len(node["fields"]),
                "top_fields": [{"label": label, "count": count} for label, count in node["fields"].most_common(8)],
                "papers": papers,
                "summary": node["summary"],
            }
        )
    field_rows.sort(key=lambda row: (row["paper_count"], row["method_count"], row["label"]), reverse=True)
    method_rows.sort(key=lambda row: (row["paper_count"], row["field_count"], row["label"]), reverse=True)

    relation_rows = [
        {
            "field_id": f"field:{digest.normalize_output_suffix(field) or 'other'}",
            "field_label": field,
            "method_id": f"method:{digest.normalize_output_suffix(method) or 'other'}",
            "method_label": method,
            "count": edge["count"],
            "paper_ids": edge["paper_ids"],
        }
        for (field, method), edge in edges.items()
    ]
    relation_rows.sort(key=lambda row: (row["count"], row["field_label"], row["method_label"]), reverse=True)

    return {
        "stats": {
            "field_count": len(field_rows),
            "method_count": len(method_rows),
            "transferable_paper_count": len(transferable),
            "relation_count": len(relation_rows),
        },
        "fields": field_rows,
        "methods": method_rows,
        "relations": relation_rows,
    }


def render_graph_section(graph_data: Dict[str, object]) -> str:
    payload = json.dumps(graph_data, ensure_ascii=False).replace("</", "<\\/")
    html_block = """
<section id='transfer-graph' class='graph-section'>
  <div class='graph-hero'>
    <div>
      <p class='graph-kicker'>迁移图谱</p>
      <h2>跨领域迁移图谱</h2>
      <p class='subtitle'>左侧是可以向聚焦领域迁移思想的外部领域分类，中间是这些领域与可迁移方法簇之间的关系图，右侧是当前选择下的具体论文与应用路径。</p>
    </div>
    <div class='graph-stat-grid'>
      <div><strong>__FIELD_COUNT__</strong><span>外部领域</span></div>
      <div><strong>__METHOD_COUNT__</strong><span>方法簇</span></div>
      <div><strong>__PAPER_COUNT__</strong><span>可迁移论文</span></div>
    </div>
  </div>
  <div class='graph-toolbar-shell'>
    <div class='graph-toolbar'>
      <input id='tf-search' type='search' placeholder='搜索外部领域、方法簇或论文标题' />
      <div class='graph-toolbar-actions'>
        <button id='tf-reset' type='button' class='kg-toolbar-btn'>重置</button>
      </div>
    </div>
  </div>
  <div class='graph-layout'>
    <aside class='kg-sidebar'>
      <div class='kg-sidebar-head'>
        <h3>外部领域</h3>
        <p>这里只展示被判断为可迁移到聚焦领域的外部领域分类。点击任意领域，查看它连向哪些方法簇，以及具体有哪些论文可用。</p>
      </div>
      <div id='tf-field-list' class='kg-theme-list'></div>
    </aside>
    <div class='graph-canvas-wrap'>
      <div class='graph-legend'>
        <span><i class='legend-dot keyword'></i> 外部领域</span>
        <span><i class='legend-dot secondary'></i> 方法簇</span>
        <span><i class='legend-line'></i> 连接强度 = 可迁移论文数</span>
      </div>
      <svg id='tf-svg' viewBox='0 0 1200 760' preserveAspectRatio='xMidYMid meet' aria-label='跨领域迁移图谱'></svg>
      <p class='graph-canvas-note'>点击左侧领域后，中间只展示该领域相关的方法簇关系；右侧会同步切换到具体论文列表。</p>
    </div>
    <aside id='tf-panel' class='kg-panel'>
      <h3>图谱说明</h3>
      <p>1. 先从左侧选择一个外部领域。</p>
      <p>2. 中间会显示这个领域可迁移到聚焦领域的方法簇关系。</p>
      <p>3. 右侧会列出该领域下的具体论文、可复用机制和应用路径。</p>
    </aside>
  </div>
  <script>
  (function() {
    const data = __PAYLOAD__;
    const svg = document.getElementById('tf-svg');
    const fieldList = document.getElementById('tf-field-list');
    const panel = document.getElementById('tf-panel');
    const searchInput = document.getElementById('tf-search');
    const resetBtn = document.getElementById('tf-reset');
    if (!svg || !fieldList || !panel || !searchInput || !resetBtn) return;

    const ns = 'http://www.w3.org/2000/svg';
    const fieldMap = new Map((data.fields || []).map(item => [item.id, item]));
    const methodMap = new Map((data.methods || []).map(item => [item.id, item]));
    const escapeHtml = (value) => String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
    const make = (tag, attrs = {}) => {
      const el = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
      return el;
    };

    let activeFieldId = '';
    let searchQuery = '';

    const visibleFields = () => {
      const query = (searchQuery || '').trim().toLowerCase();
      const fields = [...(data.fields || [])];
      if (!query) return fields;
      return fields.filter(field => {
        if (String(field.label || '').toLowerCase().includes(query)) return true;
        return (field.papers || []).some(paper =>
          String(paper.title || '').toLowerCase().includes(query)
          || String(paper.title_zh || '').toLowerCase().includes(query)
        );
      });
    };

    const renderFieldList = () => {
      const rows = visibleFields();
      fieldList.innerHTML = rows.map(field => `
        <button type="button" class="kg-theme-item${field.id === activeFieldId ? ' is-active' : ''}" data-field-id="${escapeHtml(field.id)}">
          <div class="kg-theme-item-top">
            <strong>${escapeHtml(field.label)}</strong>
            <span class="kg-theme-share">${field.paper_count || 0}篇</span>
          </div>
          <div class="kg-theme-item-badges">
            <span class="kg-chip kg-chip-soft">方法簇 ${field.method_count || 0}</span>
          </div>
        </button>
      `).join('') || '<p class="kg-empty">没有匹配到外部领域。</p>';
      fieldList.querySelectorAll('[data-field-id]').forEach(btn => {
        btn.addEventListener('click', () => {
          const fieldId = btn.getAttribute('data-field-id') || '';
          if (fieldId) selectField(fieldId);
        });
      });
    };

    const renderPanel = (field) => {
      if (!field) {
        panel.innerHTML = `
          <h3>图谱说明</h3>
          <p>1. 先从左侧选择一个外部领域。</p>
          <p>2. 中间会显示这个领域可迁移到聚焦领域的方法簇关系。</p>
          <p>3. 右侧会列出该领域下的具体论文、可复用机制和应用路径。</p>
        `;
        return;
      }
      const methods = (field.top_methods || []).map(item => {
        const methodId = `method:${String(item.label || '').toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
        const method = [...(data.methods || [])].find(row => row.label === item.label) || {};
        const summary = method.summary || {};
        const why = summary.why_it_matters_for_focus || '';
        return `
          <div class="mini-panel">
            <h4>${escapeHtml(item.label)}</h4>
            <p>${escapeHtml(why || `关联论文 ${item.count || 0} 篇`)}</p>
          </div>
        `;
      }).join('') || '<p class="kg-empty">暂无方法簇。</p>';
      const papers = (field.papers || []).map(paper => `
        <li class="kg-paper-item">
          <a href="${escapeHtml(paper.url || '')}" target="_blank">${escapeHtml(paper.title_zh || paper.title || paper.id)}</a>
          <div class="kg-paper-meta">
            <span class="kg-paper-focus">${escapeHtml(paper.decision || '')}/${escapeHtml(paper.confidence || '')}</span>
          </div>
          <p>${escapeHtml(paper.reusable_idea || paper.adaptation_path || '')}</p>
        </li>
      `).join('') || '<li>暂无论文。</li>';
      panel.innerHTML = `
        <h3>${escapeHtml(field.label)}</h3>
        <p class="kg-panel-desc">当前外部领域共有 ${field.paper_count || 0} 篇可迁移论文，连接 ${field.method_count || 0} 个方法簇。</p>
        <div class="kg-metric-grid">
          <div><strong>${field.paper_count || 0}</strong><span>论文</span></div>
          <div><strong>${field.method_count || 0}</strong><span>方法簇</span></div>
          <div><strong>${(field.decision_counter || {}).keep || 0}</strong><span>高价值</span></div>
          <div><strong>${(field.decision_counter || {}).maybe || 0}</strong><span>待验证</span></div>
        </div>
        <div class="kg-subblock">
          <h4>关联方法簇</h4>
          <div class="panel-grid">${methods}</div>
        </div>
        <div class="kg-subblock">
          <h4>具体论文</h4>
          <ul class="kg-related-list kg-paper-list">${papers}</ul>
        </div>
      `;
    };

    const drawGraph = (field) => {
      svg.innerHTML = '';
      if (!field) return;
      const methods = (field.top_methods || []).map(item => {
        return (data.methods || []).find(row => row.label === item.label);
      }).filter(Boolean);

      const fieldNode = { x: 180, y: 380, r: 42 };
      const fieldCircle = make('circle', { cx: fieldNode.x, cy: fieldNode.y, r: fieldNode.r, fill: '#0f4f72', stroke: '#fff', 'stroke-width': 4 });
      const fieldLabel = make('text', { x: fieldNode.x, y: fieldNode.y + 70, 'text-anchor': 'middle', class: 'kg-label keyword-label is-active' });
      fieldLabel.textContent = field.label;
      svg.appendChild(fieldCircle);
      svg.appendChild(fieldLabel);

      const methodCount = methods.length || 1;
      methods.forEach((method, index) => {
        const y = 120 + ((520 / Math.max(1, methodCount - 1 || 1)) * index);
        const mx = 680;
        const r = 28;
        const relation = (data.relations || []).find(row => row.field_label === field.label && row.method_label === method.label);
        const weight = relation ? Number(relation.count || 1) : 1;
        const line = make('line', {
          x1: fieldNode.x + fieldNode.r,
          y1: fieldNode.y,
          x2: mx - r,
          y2: y,
          class: 'kg-edge',
          'stroke-width': Math.min(8, 1.6 + (weight * 0.9)),
        });
        const circle = make('circle', { cx: mx, cy: y, r, fill: '#6f9abb', stroke: '#fff', 'stroke-width': 3 });
        const label = make('text', { x: mx, y: y + 56, 'text-anchor': 'middle', class: 'kg-label keyword-label is-neighbor' });
        label.textContent = method.label;
        svg.appendChild(line);
        svg.appendChild(circle);
        svg.appendChild(label);
      });
    };

    const selectField = (fieldId) => {
      const field = fieldMap.get(fieldId);
      if (!field) return;
      activeFieldId = fieldId;
      renderFieldList();
      drawGraph(field);
      renderPanel(field);
    };

    searchInput.addEventListener('input', () => {
      searchQuery = searchInput.value || '';
      renderFieldList();
    });
    resetBtn.addEventListener('click', () => {
      searchQuery = '';
      searchInput.value = '';
      const first = visibleFields()[0];
      if (first) selectField(first.id);
      else {
        activeFieldId = '';
        renderFieldList();
        drawGraph(null);
        renderPanel(null);
      }
    });

    renderFieldList();
    const first = visibleFields()[0];
    if (first) selectField(first.id);
    else renderPanel(null);
  })();
  </script>
</section>
"""
    return (
        html_block.replace("__PAYLOAD__", payload)
        .replace("__FIELD_COUNT__", str(graph_data.get("stats", {}).get("field_count", 0)))
        .replace("__METHOD_COUNT__", str(graph_data.get("stats", {}).get("method_count", 0)))
        .replace("__PAPER_COUNT__", str(graph_data.get("stats", {}).get("transferable_paper_count", 0)))
    )


def render_html_report(
    *,
    packet_name: str,
    digest_json_path: str,
    payload: Dict[str, object],
    prepared: Dict[str, object],
    report_dir: str,
    data_dir: str,
    focus_profile: Optional[Dict[str, object]],
    judgments: List[Dict[str, object]],
    synthesis: Optional[Dict[str, object]],
    quality_gate: Dict[str, object],
    graph_data: Dict[str, object],
    backend_label: str,
    translation_map: Optional[Dict[str, str]] = None,
) -> str:
    focus_terms = prepared["focus_terms"]
    focus_records = prepared["focus_records"]
    non_focus_records = prepared["non_focus_records"]
    transfer_candidate_records = list(prepared.get("transfer_candidate_records", []) or [])
    paper_lookup = {paper_id(record): record for record in focus_records + non_focus_records}
    if translation_map is None:
        translation_cache_path = os.path.join(data_dir, "html_translation_cache.json")
        translation_map = translate_texts_for_report(
            collect_extension_report_translation_inputs(
                focus_terms=focus_terms,
                focus_records=focus_records,
                focus_landscape=focus_profile,
                judgments=judgments,
                non_focus_records=non_focus_records,
            ),
            translation_cache_path,
            log_label="扩展报告翻译",
        )
    localized_focus_terms = localize_items(focus_terms, translation_map)
    localized_landscape = localize_focus_landscape(focus_profile, translation_map)
    localized_judgments = localize_transfer_note_rows(judgments, paper_lookup, translation_map)
    localized_backend = localize_text(backend_label, translation_map, "仅整理材料")
    focus_record_lookup = {paper_id(record): record for record in focus_records if paper_id(record)}
    focus_term_summary = "、".join(localized_focus_terms)
    if not focus_term_summary or re.search(r"[A-Za-z]", focus_term_summary):
        focus_term_summary = "聚焦关键词已由主分支配置提供，页面不再展开原始英文术语。"
    decision_counter = Counter(digest.safe_text(str(row.get("decision", ""))) for row in judgments)
    quality_state = "通过" if quality_gate.get("passed") else "待检查"

    def metric_card(value: object, label: str, note: str) -> str:
        return (
            "<div class='metric-card'>"
            f"<strong>{h(value)}</strong>"
            f"<span>{h(label)}</span>"
            f"<small>{h(note)}</small>"
            "</div>"
        )

    explorer_html = render_non_focus_explorer_section(non_focus_records, localized_judgments, translation_map)

    trend_cards = ""
    if localized_landscape and list(localized_landscape.get("focus_topics_zh", []) or []):
        blocks = []
        for topic in list(localized_landscape.get("focus_topics_zh", []) or []):
            if not isinstance(topic, dict):
                continue
            hot_problems = topic.get("hot_problems_zh", []) or []
            hot_html = "<ul class='plain-list'>" + "".join(f"<li>{h(item)}</li>" for item in hot_problems) + "</ul>" if hot_problems else "<p class='empty-note'>暂无热点问题总结。</p>"
            rep_ids = [digest.safe_text(str(pid)) for pid in list(topic.get("representative_paper_ids", []) or []) if digest.safe_text(str(pid))]
            rep_links: List[str] = []
            for pid in list(dict.fromkeys(rep_ids)):
                record = focus_record_lookup.get(pid, {})
                url = digest.safe_text(str(record.get("link_abs", ""))) or f"https://arxiv.org/abs/{pid}"
                title_zh = digest.safe_text(str(record.get("title_zh", ""))) or localize_text(record.get("title", ""), translation_map, pid)
                label = f"{title_zh}（{pid}）" if title_zh and title_zh != pid else pid
                rep_links.append(
                    f"<a class='trend-link' href='{h(url)}' target='_blank' rel='noopener noreferrer'>{h(label)}</a>"
                )
            blocks.append(
                "<article class='trend-card'>"
                f"<h3>{h(topic.get('focus_term_zh', '聚焦主题'))}</h3>"
                f"<p class='trend-summary'>{h(topic.get('trend_summary_zh', '') or '暂无趋势总结。')}</p>"
                "<h4>热点问题</h4>"
                f"{hot_html}"
                + (
                    "<div class='trend-meta'><strong>代表论文：</strong>"
                    f"<div class='trend-links'>{''.join(rep_links)}</div></div>"
                    if rep_links
                    else ""
                )
                + "</article>"
            )
        trend_cards = "".join(blocks)

    focus_trends_section = (
        "<section id='focus-trends'>"
        "<div class='section-head'>"
        "<div><p class='section-kicker'>聚焦领域总结</p><h2>发展趋势与热点问题</h2></div>"
        f"<p class='section-note'>{h((localized_landscape or {}).get('overall_summary_zh', '') or '当前还没有模型生成的聚焦领域趋势总结。启用分析后，这里会按一个或多个聚焦主题分别给出趋势与热点问题。')}</p>"
        "</div>"
        + (f"<div class='trend-grid'>{trend_cards}</div>" if trend_cards else "")
        + "</section>"
    )

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{h("跨领域迁移分析报告")}</title>
  <style>
:root {{
  --bg: #f7f8fa;
  --panel: #ffffff;
  --ink: #15202b;
  --muted: #5c6b7a;
  --line: #d9e2ec;
  --accent: #0a7f5a;
  --accent-soft: #e8f7f1;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: radial-gradient(circle at 10% 10%, #eef8ff, var(--bg));
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
}}
.container {{
  max-width: 1240px;
  margin: 24px auto;
  padding: 0 16px 48px;
}}
header {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 22px;
}}
h1 {{ margin: 0 0 8px; font-size: 28px; }}
h2 {{ margin: 0 0 12px; font-size: 24px; }}
h3 {{ margin: 0 0 8px; font-size: 18px; }}
h4 {{ margin: 0 0 8px; font-size: 16px; }}
.subtitle {{ color: var(--muted); margin: 0; line-height: 1.7; }}
.lead {{
  margin-top: 10px;
  color: #213547;
  line-height: 1.8;
}}
.page-section {{
  margin-top: 18px;
}}
nav {{
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}}
nav a {{
  text-decoration: none;
  color: #084d3a;
  background: var(--accent-soft);
  border: 1px solid #b7e7d8;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 13px;
}}
section {{
  margin-top: 18px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 18px;
}}
.metric-grid {{
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}}
.metric-card {{
  border: 1px solid rgba(15, 79, 114, 0.12);
  background: rgba(255,255,255,0.92);
  border-radius: 14px;
  padding: 14px;
}}
.metric-card strong {{
  display: block;
  font-size: 30px;
  color: #0f4f72;
  margin-bottom: 8px;
}}
.metric-card span {{
  display: block;
  color: var(--muted);
  font-size: 13px;
}}
.metric-card small {{
  display: block;
  margin-top: 6px;
  color: var(--muted);
  font-size: 12px;
}}
.section-note {{
  color: #213547;
  line-height: 1.7;
  margin: 0 0 12px;
}}
.section-meta {{
  margin: 0 0 10px;
  color: var(--muted);
  font-size: 13px;
}}
.section-head {{
  display: grid;
  grid-template-columns: minmax(0, 280px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  margin-bottom: 14px;
}}
.section-kicker {{
  margin: 0 0 6px;
  color: #0f4f72;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
}}
.trend-grid {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}}
.trend-card {{
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fff;
  padding: 16px;
}}
.trend-summary {{
  margin: 0 0 14px;
  color: #213547;
  line-height: 1.8;
}}
.trend-meta {{
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 13px;
}}
.trend-links {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}}
.trend-link {{
  display: inline-flex;
  align-items: center;
  padding: 7px 10px;
  border-radius: 999px;
  text-decoration: none;
  color: #0f4f72;
  background: #eef5fb;
  border: 1px solid #d5e6f2;
  line-height: 1.5;
}}
.trend-link:hover {{
  background: #e2f1fb;
}}
.plain-list {{
  margin: 0;
  padding-left: 18px;
}}
.plain-list li {{
  margin-bottom: 8px;
}}
.empty-note {{
  color: var(--muted);
  margin: 0;
}}
.graph-shell {{
  border: 0;
  border-radius: 0;
  overflow: visible;
}}
footer {{
  margin-top: 20px;
  color: var(--muted);
  font-size: 12px;
}}
@media (max-width: 1100px) {{
  .metric-grid,
  .trend-grid {{
    grid-template-columns: 1fr;
  }}
  .section-head {{
    grid-template-columns: 1fr;
  }}
}}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>跨领域迁移分析报告</h1>
      <p class="subtitle">
        <strong>日期：</strong> {h(payload.get("date", ""))} |
        <strong>分析方式：</strong> {h(localized_backend)} |
        <strong>质检状态：</strong> {h(quality_state)}
      </p>
      <p class="lead">
        这份报告直接复用主分支已经抓取、分类和建图后的论文结果：一方面总结聚焦领域论文的发展趋势与热点问题，另一方面只对非聚焦论文中的中稿线索文章判断其思想和方法是否值得迁移到聚焦领域，并把可迁移思路直接标注到主题探索器里。
      </p>
      <p class="subtitle">
        <strong>聚焦主题：</strong> {h(focus_term_summary)}
      </p>
      <nav>
        <a href="#overview">概览</a>
        <a href="#transfer-explorer">主题探索器</a>
        <a href="#focus-trends">趋势与热点</a>
      </nav>
      <div id="overview" class="metric-grid">
        {metric_card(len(focus_records), "聚焦论文", "来自主分支聚焦池与关键词扩展")}
        {metric_card(len(transfer_candidate_records), "中稿迁移候选", f"来自 {len(non_focus_records)} 篇非聚焦论文")}
        {metric_card(decision_counter.get("keep", 0), "建议保留", "优先借鉴的迁移对象")}
        {metric_card(decision_counter.get("maybe", 0), "待验证", "建议先做小实验")}
        {metric_card(decision_counter.get("reject", 0), "不建议迁移", "当前改造成本或风险偏高")}
        {metric_card(quality_state, "质检状态", "覆盖率与路线汇总一致性检查")}
      </div>
    </header>

    <section class="page-section">
      <div class="section-head">
        <div><p class="section-kicker">非聚焦论文</p><h2>主题探索器</h2></div>
        <p class="section-note">这一部分直接沿用主分支的主题探索器结构，但对象仍展示非聚焦论文全量结果；可迁移性判断只会出现在其中具备中稿线索、且被本次送入分析的论文上。若某篇论文被判断为可迁移，右侧论文卡片会出现醒目的“可迁移”标签，并把迁移思路单独放进高亮说明框。</p>
      </div>
      <div class="graph-shell">
        {explorer_html}
      </div>
    </section>

    {focus_trends_section}

    <footer>本页面由主分支日报结果与扩展迁移分析共同生成。</footer>
  </div>
</body>
</html>
"""
    return page


def run_focus_landscape_analysis(
    *,
    model: str,
    api_base: str,
    api_key: str,
    endpoint_mode: str,
    message_style: str,
    stream: bool,
    timeout: int,
    packet_name: str,
    focus_terms: List[str],
    focus_records: List[Dict[str, object]],
    data_dir: str,
    report_dir: str,
    focus_context_limit: int,
    max_output_tokens: int,
) -> Dict[str, object]:
    analysis_focus = focus_records[:focus_context_limit] if focus_context_limit > 0 else focus_records
    focus_hash = hashlib.sha256(
        json.dumps([paper_hash(record) for record in analysis_focus], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    existing = load_json_file(os.path.join(data_dir, "focus_landscape_trends.json"))
    if existing and digest.safe_text(str(existing.get("_focus_hash", ""))) == focus_hash:
        return existing

    prompt_text = build_focus_landscape_prompt(packet_name, focus_terms, analysis_focus)
    digest.write_text(os.path.join(report_dir, "prompt_focus_landscape.md"), prompt_text)
    result, repair_notes = call_json_with_repair(
        model=model,
        api_base=api_base,
        api_key=api_key,
        endpoint_mode=endpoint_mode,
        message_style=message_style,
        stream=stream,
        timeout=timeout,
        prompt_text=prompt_text,
        validator=lambda payload: validate_focus_landscape(payload, focus_terms, analysis_focus),
        repair_name="focus_landscape",
        max_output_tokens=max_output_tokens,
    )
    normalized = normalize_focus_landscape(
        result,
        focus_terms,
        analysis_focus,
        fallback_summary=existing,
    )
    valid, errors = validate_focus_landscape(normalized, focus_terms, analysis_focus)
    if not valid:
        fallback_normalized = normalize_focus_landscape(
            existing,
            focus_terms,
            analysis_focus,
            fallback_summary=existing,
        )
        fallback_valid, fallback_errors = validate_focus_landscape(fallback_normalized, focus_terms, analysis_focus)
        if fallback_valid:
            normalized = fallback_normalized
            repair_notes = repair_notes + [f"fallback: reused previous focus landscape after invalid model output: {'; '.join(errors)}"]
            print(f"[WARN] Focus landscape fallback to previous cache: {'; '.join(errors)}")
        else:
            normalized = build_minimal_focus_landscape(
                focus_terms,
                analysis_focus,
                fallback_summary=existing,
            )
            minimal_valid, minimal_errors = validate_focus_landscape(normalized, focus_terms, analysis_focus)
            if not minimal_valid:
                raise RuntimeError(
                    f"Focus landscape analysis failed validation: {'; '.join(errors)}; "
                    f"fallback invalid: {'; '.join(fallback_errors)}; minimal invalid: {'; '.join(minimal_errors)}"
                )
            repair_notes = repair_notes + [f"fallback: synthesized minimal focus landscape after invalid model output: {'; '.join(errors)}"]
            print(f"[WARN] Focus landscape synthesized minimal profile: {'; '.join(errors)}")
    normalized["_focus_hash"] = focus_hash
    normalized["_repair_notes"] = repair_notes
    digest.dump_json(os.path.join(data_dir, "focus_landscape_trends.json"), normalized)
    digest.write_text(os.path.join(report_dir, "focus_landscape_trends.md"), render_focus_landscape_markdown(normalized))
    return normalized


def run_candidate_transfer_note_analyses(
    *,
    model: str,
    api_base: str,
    api_key: str,
    endpoint_mode: str,
    message_style: str,
    stream: bool,
    timeout: int,
    packet_name: str,
    focus_landscape: Dict[str, object],
    focus_memory_context: str,
    candidates: List[Dict[str, object]],
    candidate_limit: int,
    data_dir: str,
    report_dir: str,
    max_output_tokens: int,
) -> Tuple[List[Dict[str, object]], int]:
    target_candidates = candidates if candidate_limit <= 0 else candidates[:candidate_limit]
    focus_hash = hashlib.sha256(
        json.dumps(
            {
                "focus_landscape": focus_landscape,
                "focus_memory_context": focus_memory_context,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    cache = load_json_file(os.path.join(data_dir, "paper_transfer_judgments.json")) or {}
    existing_items = list(cache.get("items", []) or [])
    existing_map = {
        digest.safe_text(str(item.get("paper_id", ""))): item
        for item in existing_items
        if isinstance(item, dict) and digest.safe_text(str(item.get("paper_id", "")))
    }
    judgments: List[Dict[str, object]] = []
    repaired_count = 0
    total = len(target_candidates)
    for index, candidate in enumerate(target_candidates, start=1):
        pid = paper_id(candidate)
        candidate_content_hash = paper_hash(candidate)
        cached = existing_map.get(pid)
        if (
            cached
            and digest.safe_text(str(cached.get("_candidate_hash", ""))) == candidate_content_hash
            and digest.safe_text(str(cached.get("_focus_landscape_hash", ""))) == focus_hash
        ):
            judgments.append(cached)
            print(f"[INFO] Transfer note reuse {index}/{total}: {pid}")
            continue

        prompt_text = build_transfer_note_prompt(packet_name, focus_landscape, candidate, focus_memory_context)
        result, repair_notes = call_json_with_repair(
            model=model,
            api_base=api_base,
            api_key=api_key,
            endpoint_mode=endpoint_mode,
            message_style=message_style,
            stream=stream,
            timeout=timeout,
            prompt_text=prompt_text,
            validator=lambda payload: validate_transfer_note_judgment(payload, candidate),
            repair_name=f"transfer_note:{pid}",
            max_output_tokens=max_output_tokens,
        )
        valid, errors = validate_transfer_note_judgment(result, candidate)
        if not valid:
            repaired_count += 1
            normalized = normalize_transfer_note_judgment(candidate, result or {})
            normalized["decision"] = "reject"
            normalized["reason_short"] = digest.safe_text("; ".join(errors)) or "analysis_validation_failed"
            normalized["transfer_note"] = ""
            normalized["_analysis_error"] = errors
            normalized["_repair_notes"] = repair_notes
        else:
            normalized = normalize_transfer_note_judgment(candidate, result or {})
            if repair_notes:
                normalized["_repair_notes"] = repair_notes
        normalized["_candidate_hash"] = candidate_content_hash
        normalized["_focus_landscape_hash"] = focus_hash
        normalized["_model"] = current_analysis_model(model)
        judgments.append(normalized)
        print(f"[INFO] Transfer note done {index}/{total}: {pid} -> {normalized['decision']}")
        digest.dump_json(
            os.path.join(data_dir, "paper_transfer_judgments.json"),
            {"items": judgments, "candidate_scope": "accepted_non_focus", "candidate_scope_count": len(target_candidates)},
        )

    digest.dump_json(
        os.path.join(data_dir, "paper_transfer_judgments.json"),
        {"items": judgments, "candidate_scope": "accepted_non_focus", "candidate_scope_count": len(target_candidates)},
    )
    paper_lookup = {paper_id(record): record for record in target_candidates}
    digest.write_text(
        os.path.join(report_dir, "paper_transfer_judgments.md"),
        render_transfer_note_markdown(judgments, paper_lookup),
    )
    return judgments, repaired_count


def run_focus_profile_analysis(
    *,
    model: str,
    api_base: str,
    api_key: str,
    endpoint_mode: str,
    message_style: str,
    stream: bool,
    timeout: int,
    packet_name: str,
    focus_terms: List[str],
    focus_records: List[Dict[str, object]],
    data_dir: str,
    report_dir: str,
    focus_context_limit: int,
    max_output_tokens: int,
) -> Dict[str, object]:
    analysis_focus = focus_records[:focus_context_limit] if focus_context_limit > 0 else focus_records
    focus_ids = {paper_id(record) for record in analysis_focus if paper_id(record)}
    focus_hash = hashlib.sha256(
        json.dumps([paper_hash(record) for record in analysis_focus], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    existing = load_json_file(os.path.join(data_dir, "focus_profile.json"))
    if existing and digest.safe_text(str(existing.get("_focus_hash", ""))) == focus_hash:
        return existing

    prompt_text = build_focus_profile_prompt(packet_name, focus_terms, analysis_focus)
    digest.write_text(os.path.join(report_dir, "prompt_focus_profile.md"), prompt_text)
    result, repair_notes = call_json_with_repair(
        model=model,
        api_base=api_base,
        api_key=api_key,
        endpoint_mode=endpoint_mode,
        message_style=message_style,
        stream=stream,
        timeout=timeout,
        prompt_text=prompt_text,
        validator=lambda payload: validate_focus_profile(payload, focus_ids),
        repair_name="focus_profile",
        max_output_tokens=max_output_tokens,
    )
    valid, errors = validate_focus_profile(result, focus_ids)
    if not valid:
        raise RuntimeError(f"Focus profile analysis failed validation: {'; '.join(errors)}")
    normalized = dict(result or {})
    normalized["_focus_hash"] = focus_hash
    normalized["_repair_notes"] = repair_notes
    digest.dump_json(os.path.join(data_dir, "focus_profile.json"), normalized)
    digest.write_text(os.path.join(report_dir, "focus_profile.md"), render_focus_profile_markdown(normalized))
    return normalized


def run_candidate_analyses(
    *,
    model: str,
    api_base: str,
    api_key: str,
    endpoint_mode: str,
    message_style: str,
    stream: bool,
    timeout: int,
    packet_name: str,
    focus_profile: Dict[str, object],
    candidates: List[Dict[str, object]],
    candidate_limit: int,
    data_dir: str,
    report_dir: str,
    max_output_tokens: int,
) -> Tuple[List[Dict[str, object]], int]:
    target_candidates = candidates if candidate_limit <= 0 else candidates[:candidate_limit]
    focus_profile_hash = hashlib.sha256(
        json.dumps(focus_profile, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache = load_json_file(os.path.join(data_dir, "paper_transfer_judgments.json")) or {}
    existing_items = list(cache.get("items", []) or [])
    existing_map = {
        digest.safe_text(str(item.get("paper_id", ""))): item
        for item in existing_items
        if isinstance(item, dict) and digest.safe_text(str(item.get("paper_id", "")))
    }
    repaired_count = 0
    judgments: List[Dict[str, object]] = []
    total = len(target_candidates)

    for index, candidate in enumerate(target_candidates, start=1):
        pid = paper_id(candidate)
        candidate_content_hash = paper_hash(candidate)
        cached = existing_map.get(pid)
        if (
            cached
            and digest.safe_text(str(cached.get("_candidate_hash", ""))) == candidate_content_hash
            and digest.safe_text(str(cached.get("_focus_profile_hash", ""))) == focus_profile_hash
        ):
            judgments.append(cached)
            print(f"[INFO] Transfer judgment reuse {index}/{total}: {pid}")
            continue

        prompt_text = build_transfer_judgment_prompt(packet_name, focus_profile, candidate)
        result, repair_notes = call_json_with_repair(
            model=model,
            api_base=api_base,
            api_key=api_key,
            endpoint_mode=endpoint_mode,
            message_style=message_style,
            stream=stream,
            timeout=timeout,
            prompt_text=prompt_text,
            validator=lambda payload: validate_transfer_judgment(payload, candidate),
            repair_name=f"transfer_judgment:{pid}",
            max_output_tokens=max_output_tokens,
        )
        valid, errors = validate_transfer_judgment(result, candidate)
        if not valid:
            repaired_count += 1
            normalized = normalize_judgment(candidate, result or {})
            normalized["decision"] = "reject"
            normalized["confidence"] = "low"
            normalized["reject_reason"] = digest.safe_text("; ".join(errors)) or "analysis_validation_failed"
            normalized["reason_short"] = "analysis_validation_failed"
            normalized["_analysis_error"] = errors
            normalized["_repair_notes"] = repair_notes
        else:
            normalized = normalize_judgment(candidate, result or {})
            if repair_notes:
                normalized["_repair_notes"] = repair_notes
        normalized["_candidate_hash"] = candidate_content_hash
        normalized["_focus_profile_hash"] = focus_profile_hash
        normalized["_model"] = current_analysis_model(model)
        judgments.append(normalized)
        print(f"[INFO] Transfer judgment done {index}/{total}: {pid} -> {normalized['decision']}/{normalized['confidence']}")
        digest.dump_json(
            os.path.join(data_dir, "paper_transfer_judgments.json"),
            {"items": judgments, "candidate_scope_count": len(target_candidates)},
        )

    digest.dump_json(
        os.path.join(data_dir, "paper_transfer_judgments.json"),
        {"items": judgments, "candidate_scope_count": len(target_candidates)},
    )
    paper_lookup = {paper_id(record): record for record in target_candidates}
    digest.write_text(
        os.path.join(report_dir, "paper_transfer_judgments.md"),
        render_paper_judgments_markdown(judgments, paper_lookup),
    )
    return judgments, repaired_count


def run_transfer_synthesis(
    *,
    model: str,
    api_base: str,
    api_key: str,
    endpoint_mode: str,
    message_style: str,
    stream: bool,
    timeout: int,
    packet_name: str,
    focus_profile: Dict[str, object],
    judgments: List[Dict[str, object]],
    data_dir: str,
    report_dir: str,
    max_output_tokens: int,
) -> Optional[Dict[str, object]]:
    transferable = [row for row in judgments if digest.safe_text(str(row.get("decision", ""))) in {"keep", "maybe"}]
    if not transferable:
        digest.dump_json(os.path.join(data_dir, "transfer_method_clusters.json"), {"method_clusters": []})
        digest.write_text(
            os.path.join(report_dir, "transfer_method_clusters.md"),
            "# 跨领域迁移组合总结\n\n当前没有论文被保留为可迁移路线。\n",
        )
        return {"portfolio_summary": "当前没有论文被保留为可迁移路线。", "method_clusters": [], "overall_findings": [], "top_recommendations": [], "do_not_overinvest": []}

    valid_paper_ids = {digest.safe_text(str(row.get("paper_id", ""))) for row in transferable}
    prompt_text = build_transfer_synthesis_prompt(packet_name, focus_profile, transferable)
    digest.write_text(os.path.join(report_dir, "prompt_transfer_synthesis.md"), prompt_text)
    result, repair_notes = call_json_with_repair(
        model=model,
        api_base=api_base,
        api_key=api_key,
        endpoint_mode=endpoint_mode,
        message_style=message_style,
        stream=stream,
        timeout=timeout,
        prompt_text=prompt_text,
        validator=lambda payload: validate_transfer_synthesis(payload, valid_paper_ids),
        repair_name="transfer_synthesis",
        max_output_tokens=max_output_tokens,
    )
    valid, errors = validate_transfer_synthesis(result, valid_paper_ids)
    if not valid:
        print(f"[WARN] Transfer synthesis validation failed; fallback to heuristic clusters: {'; '.join(errors)}")
        fallback = {
            "portfolio_summary": "模型路线汇总未通过校验，已退回到程序化的方法路线聚合结果。",
            "overall_findings": [
                "结构化路线汇总没有通过校验，因此本报告改用程序化的方法路线聚合兜底。",
                "逐篇迁移判断仍然是当前最可靠的依据，请优先结合逐篇卡片阅读。",
            ],
            "method_clusters": [
                {
                    "method_family": row["method_family"],
                    "why_it_matters_for_focus": "具体迁移方式请直接查看逐篇迁移判断卡片。",
                    "best_source_fields": row["top_source_fields"],
                    "recommended_paper_ids": row["paper_ids"][:6],
                    "implementation_pattern": joined(row["top_modifications"], "请结合逐篇迁移判断查看"),
                    "common_modifications": row["top_modifications"],
                    "common_risks": row["top_risks"],
                }
                for row in build_method_clusters(transferable)
            ],
            "top_recommendations": [
                {"paper_id": digest.safe_text(str(row.get("paper_id", ""))), "priority": "high" if digest.safe_text(str(row.get("decision", ""))) == "keep" else "medium", "reason": digest.safe_text(str(row.get("reason_short", "")))}
                for row in transferable[:8]
            ],
            "do_not_overinvest": [],
            "_repair_notes": repair_notes + errors,
        }
        digest.dump_json(os.path.join(data_dir, "transfer_method_clusters.json"), fallback)
        digest.write_text(os.path.join(report_dir, "transfer_method_clusters.md"), render_transfer_synthesis_markdown(fallback))
        return fallback

    normalized = dict(result or {})
    normalized["_repair_notes"] = repair_notes
    digest.dump_json(os.path.join(data_dir, "transfer_method_clusters.json"), normalized)
    digest.write_text(os.path.join(report_dir, "transfer_method_clusters.md"), render_transfer_synthesis_markdown(normalized))
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Focus-transfer extension that consumes arXiv daily digest JSON, profiles the focus area, analyzes accepted non-focus candidates, and renders an HTML report."
    )
    parser.add_argument("--digest-json", type=str, default=os.environ.get("FOCUS_TRANSFER_DIGEST_JSON", "data/last_success_digest.json"))
    parser.add_argument("--output-suffix", type=str, default=os.environ.get("FOCUS_TRANSFER_OUTPUT_SUFFIX", ""))
    parser.add_argument("--report-root", type=str, default=os.environ.get("FOCUS_TRANSFER_REPORT_ROOT", "reports/focus_transfer"))
    parser.add_argument("--data-root", type=str, default=os.environ.get("FOCUS_TRANSFER_DATA_ROOT", "data/focus_transfer"))
    parser.add_argument("--analysis-backend", type=str, default=os.environ.get("FOCUS_TRANSFER_ANALYSIS_BACKEND", "local"), choices=["none", "local"])
    parser.add_argument(
        "--analysis-api-base",
        type=str,
        default=default_analysis_api_base(),
    )
    parser.add_argument(
        "--analysis-api-key",
        type=str,
        default=default_analysis_api_key(),
    )
    parser.add_argument(
        "--analysis-model",
        type=str,
        default=default_analysis_model(),
    )
    parser.add_argument("--analysis-endpoint-mode", type=str, default=os.environ.get("FOCUS_TRANSFER_ENDPOINT", "chat"), choices=["auto", "responses", "chat", "completions"])
    parser.add_argument("--analysis-message-style", type=str, default=os.environ.get("FOCUS_TRANSFER_MESSAGE_STYLE", "normal"), choices=["normal", "user_only"])
    parser.add_argument("--analysis-stream", type=int, default=int(os.environ.get("FOCUS_TRANSFER_STREAM", "0")), choices=[0, 1])
    parser.add_argument("--analysis-timeout", type=int, default=int(os.environ.get("FOCUS_TRANSFER_TIMEOUT_SECONDS", "300")))
    parser.add_argument("--analysis-max-output-tokens", type=int, default=int(os.environ.get("FOCUS_TRANSFER_MAX_OUTPUT_TOKENS", "1800")))
    parser.add_argument("--focus-context-limit", type=int, default=int(os.environ.get("FOCUS_TRANSFER_FOCUS_CONTEXT_LIMIT", "48")))
    parser.add_argument("--candidate-limit", type=int, default=int(os.environ.get("FOCUS_TRANSFER_CANDIDATE_LIMIT", "-1")))
    parser.add_argument("--focus-memory-dir", type=str, default=os.environ.get("FOCUS_TRANSFER_MEMORY_DIR", "data/focus_memory"))
    parser.add_argument("--focus-memory-context-chars", type=int, default=int(os.environ.get("FOCUS_TRANSFER_MEMORY_CONTEXT_CHARS", "6000")))
    args = parser.parse_args()

    digest_json_path = os.path.abspath(args.digest_json)
    if not os.path.exists(digest_json_path):
        print(f"[ERROR] Digest JSON not found: {digest_json_path}", file=sys.stderr)
        return 2

    payload = load_json_file(digest_json_path)
    if not payload:
        print(f"[ERROR] Failed to load digest JSON: {digest_json_path}", file=sys.stderr)
        return 2

    prepared = prepare_focus_transfer_inputs(payload)
    if not prepared["focus_terms"]:
        print("[ERROR] Digest JSON has no focus terms; this extension needs focus keywords from the main digest.", file=sys.stderr)
        return 2
    if not prepared["focus_records"]:
        print("[ERROR] No focus papers were found after combining focus pool and focus keyword matching.", file=sys.stderr)
        return 2
    if not prepared["non_focus_records"]:
        print("[WARN] No non-focus papers were found; transferability analysis will have an empty candidate set.")

    packet_name = build_packet_name(payload, digest_json_path, args.output_suffix)
    report_dir = os.path.join(args.report_root, packet_name)
    data_dir = os.path.join(args.data_root, packet_name)
    os.makedirs(args.report_root, exist_ok=True)
    os.makedirs(args.data_root, exist_ok=True)
    archived_report_dir, archived_report_count = archive_focus_transfer_root(args.report_root, packet_name)
    archived_data_dir, archived_data_count = archive_focus_transfer_root(args.data_root, packet_name)
    os.makedirs(report_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    print(f"[INFO] Packet: {packet_name}")
    print(f"[INFO] Digest JSON: {digest_json_path}")
    print(f"[INFO] Focus terms: {joined(prepared['focus_terms'], 'none')}")
    print(f"[INFO] Focus papers: {len(prepared['focus_records'])}")
    print(f"[INFO] Non-focus papers: {len(prepared['non_focus_records'])}")
    print(f"[INFO] Transfer candidates (accepted non-focus): {len(prepared['transfer_candidate_records'])}")
    if archived_report_count:
        print(f"[INFO] Archived previous focus-transfer report packets: {archived_report_count} -> {archived_report_dir}")
    if archived_data_count:
        print(f"[INFO] Archived previous focus-transfer data packets: {archived_data_count} -> {archived_data_dir}")

    focus_records = prepared["focus_records"]
    non_focus_records = prepared["non_focus_records"]
    transfer_candidate_records = prepared["transfer_candidate_records"]
    analyzed_candidates = transfer_candidate_records if args.candidate_limit <= 0 else transfer_candidate_records[: args.candidate_limit]
    paper_lookup = {paper_id(record): record for record in focus_records + non_focus_records}

    digest.write_text(os.path.join(report_dir, "focus_corpus.md"), render_corpus_markdown("Focus Corpus", focus_records))
    digest.write_text(
        os.path.join(report_dir, "non_focus_candidates.md"),
        render_corpus_markdown("非 Focus 中稿迁移候选语料", transfer_candidate_records),
    )
    digest.write_text(
        os.path.join(report_dir, "non_focus_all_papers.md"),
        render_corpus_markdown("全部非 Focus 论文语料", non_focus_records),
    )
    digest.dump_json(os.path.join(data_dir, "focus_papers.json"), {"items": focus_records})
    digest.dump_json(os.path.join(data_dir, "non_focus_papers.json"), {"items": non_focus_records})
    digest.dump_json(
        os.path.join(data_dir, "transfer_candidate_papers.json"),
        {"candidate_scope": "accepted_non_focus", "items": transfer_candidate_records},
    )

    focus_landscape: Optional[Dict[str, object]] = None
    focus_memory_entries: List[Dict[str, object]] = []
    judgments: List[Dict[str, object]] = []
    repaired_count = 0
    backend_label = "prepare-only"

    if args.analysis_backend == "local":
        backend_label = "OpenRouter/OpenAI-compatible"
        api_key = digest.safe_text(args.analysis_api_key)
        model = resolve_analysis_model_with_key(args.analysis_api_base, args.analysis_model, api_key)
        endpoint_mode = digest.normalize_openai_endpoint_mode(args.analysis_endpoint_mode)
        message_style = digest.normalize_openai_message_style(args.analysis_message_style)
        stream = bool(args.analysis_stream)
        provider_chain = configure_analysis_provider_chain(args.analysis_api_base, api_key, model)
        if not provider_chain:
            print("[ERROR] No usable Transfer analysis API providers are configured.", file=sys.stderr)
            return 2

        focus_landscape = run_focus_landscape_analysis(
            model=model,
            api_base=args.analysis_api_base,
            api_key=api_key,
            endpoint_mode=endpoint_mode,
            message_style=message_style,
            stream=stream,
            timeout=args.analysis_timeout,
            packet_name=packet_name,
            focus_terms=prepared["focus_terms"],
            focus_records=focus_records,
            data_dir=data_dir,
            report_dir=report_dir,
            focus_context_limit=args.focus_context_limit,
            max_output_tokens=max(1200, args.analysis_max_output_tokens),
        )

        focus_memory_entries = update_focus_memory_files(
            model=model,
            api_base=args.analysis_api_base,
            api_key=api_key,
            endpoint_mode=endpoint_mode,
            message_style=message_style,
            stream=stream,
            timeout=args.analysis_timeout,
            packet_name=packet_name,
            focus_terms=prepared["focus_terms"],
            focus_records=focus_records,
            focus_landscape=focus_landscape,
            memory_dir=args.focus_memory_dir,
            report_dir=report_dir,
            data_dir=data_dir,
            max_output_tokens=max(1600, args.analysis_max_output_tokens),
        )
        focus_memory_context = build_focus_memory_context(
            focus_memory_entries,
            max_chars_per_term=max(1200, args.focus_memory_context_chars),
        )

        judgments, repaired_count = run_candidate_transfer_note_analyses(
            model=model,
            api_base=args.analysis_api_base,
            api_key=api_key,
            endpoint_mode=endpoint_mode,
            message_style=message_style,
            stream=stream,
            timeout=args.analysis_timeout,
            packet_name=packet_name,
            focus_landscape=focus_landscape,
            focus_memory_context=focus_memory_context,
            candidates=transfer_candidate_records,
            candidate_limit=args.candidate_limit,
            data_dir=data_dir,
            report_dir=report_dir,
            max_output_tokens=max(800, min(1200, args.analysis_max_output_tokens)),
        )
    else:
        print("[INFO] Analysis backend=none: generating corpus, packet files, and HTML skeleton only.")
        os.makedirs(args.focus_memory_dir, exist_ok=True)
        focus_memory_entries = load_focus_memory_entries(prepared["focus_terms"], args.focus_memory_dir)
        digest.write_text(os.path.join(report_dir, "focus_memory_index.md"), render_focus_memory_index(focus_memory_entries))
        digest.dump_json(
            os.path.join(data_dir, "focus_memory_files.json"),
            {"items": [{key: value for key, value in entry.items() if key != "content"} for entry in focus_memory_entries]},
        )

    if not judgments:
        existing_judgments = load_json_file(os.path.join(data_dir, "paper_transfer_judgments.json")) or {}
        judgments = list(existing_judgments.get("items", []) or [])
    if not focus_landscape:
        focus_landscape = load_json_file(os.path.join(data_dir, "focus_landscape_trends.json"))

    quality_gate = build_quality_gate(focus_landscape, analyzed_candidates, judgments, None)
    digest.dump_json(os.path.join(data_dir, "analysis_quality_gate.json"), quality_gate)

    graph_data = build_graph_data(judgments, paper_lookup, None)
    digest.dump_json(os.path.join(data_dir, "transfer_graph.json"), graph_data)

    shared_translation_cache_path = os.path.join(data_dir, "html_translation_cache.json")
    print("[INFO] Preparing localized analysis content for HTML output...")
    shared_translation_map = translate_texts_for_report(
        collect_extension_report_translation_inputs(
            focus_terms=prepared["focus_terms"],
            focus_records=focus_records,
            focus_landscape=focus_landscape,
            judgments=judgments,
            non_focus_records=non_focus_records,
        ),
        shared_translation_cache_path,
        log_label="分析结果翻译",
    )

    html_path = os.path.join(report_dir, "focus_transfer_report.html")
    print("[INFO] Rendering standalone focus-transfer HTML report...")
    digest.write_text(
        html_path,
        render_html_report(
            packet_name=packet_name,
            digest_json_path=digest_json_path,
            payload=payload,
            prepared=prepared,
            report_dir=report_dir,
            data_dir=data_dir,
            focus_profile=focus_landscape,
            judgments=judgments,
            synthesis=None,
            quality_gate=quality_gate,
            graph_data=graph_data,
            backend_label=backend_label,
            translation_map=shared_translation_map,
        ),
    )

    manifest_path = os.path.join(data_dir, "analysis_manifest.json")
    manifest = {
        "packet_name": packet_name,
        "digest_json": digest_json_path,
        "date": payload.get("date", ""),
        "focus_terms": prepared["focus_terms"],
        "focus_paper_count": len(focus_records),
        "non_focus_paper_count": len(non_focus_records),
        "transfer_candidate_scope": "accepted_non_focus",
        "transfer_candidate_count": len(transfer_candidate_records),
        "analyzed_candidate_count": len(analyzed_candidates),
        "analysis_backend": args.analysis_backend,
        "analysis_api_base": str((current_analysis_provider() or {}).get("api_base", args.analysis_api_base)) if args.analysis_backend == "local" else "",
        "analysis_model": current_analysis_model(args.analysis_model) if args.analysis_backend == "local" else "",
        "analysis_provider_chain": [
            {"name": provider.get("name", ""), "api_base": provider.get("api_base", ""), "model": provider.get("model", "")}
            for provider in ANALYSIS_PROVIDER_CHAIN
        ] if args.analysis_backend == "local" else [],
        "focus_profile_path": os.path.join(data_dir, "focus_landscape_trends.json"),
        "focus_memory_dir": os.path.abspath(args.focus_memory_dir),
        "focus_memory_files_path": os.path.join(data_dir, "focus_memory_files.json"),
        "focus_memory_files": [
            {key: value for key, value in entry.items() if key != "content"}
            for entry in focus_memory_entries
        ],
        "judgments_path": os.path.join(data_dir, "paper_transfer_judgments.json"),
        "synthesis_path": "",
        "graph_path": os.path.join(data_dir, "transfer_graph.json"),
        "quality_gate_path": os.path.join(data_dir, "analysis_quality_gate.json"),
        "html_report": html_path,
        "repaired_count": repaired_count,
        "quality_gate": quality_gate,
        "archived_report_dir": archived_report_dir,
        "archived_report_count": archived_report_count,
        "archived_data_dir": archived_data_dir,
        "archived_data_count": archived_data_count,
    }
    digest.dump_json(manifest_path, manifest)

    print("[INFO] Syncing focus-transfer analysis back into the main digest report...")
    main_digest_paths = sync_focus_transfer_into_main_digest(
        digest_json_path=digest_json_path,
        payload=payload,
        prepared=prepared,
        focus_landscape=focus_landscape,
        judgments=judgments,
        manifest_path=manifest_path,
        translation_map=shared_translation_map,
        focus_memory_entries=focus_memory_entries,
    )

    print(f"[OK] Focus corpus: {os.path.join(report_dir, 'focus_corpus.md')}")
    print(f"[OK] Non-focus accepted candidates: {os.path.join(report_dir, 'non_focus_candidates.md')}")
    print(f"[OK] All non-focus papers: {os.path.join(report_dir, 'non_focus_all_papers.md')}")
    print(f"[OK] Focus landscape: {os.path.join(data_dir, 'focus_landscape_trends.json')}")
    print(f"[OK] Focus memory index: {os.path.join(report_dir, 'focus_memory_index.md')}")
    print(f"[OK] Paper judgments: {os.path.join(data_dir, 'paper_transfer_judgments.json')}")
    print(f"[OK] Graph data: {os.path.join(data_dir, 'transfer_graph.json')}")
    print(f"[OK] Quality gate: {os.path.join(data_dir, 'analysis_quality_gate.json')}")
    print(f"[OK] HTML report: {html_path}")
    print(f"[OK] Main digest HTML integrated: {main_digest_paths['html_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
