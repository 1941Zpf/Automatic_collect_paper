#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import http.client
import json
import math
import random
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

ARXIV_API_ENDPOINTS = [
    "https://export.arxiv.org/api/query",
    "http://export.arxiv.org/api/query",
]
DEFAULT_API_BASE = "https://api.openai.com/v1"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
DEFAULT_CATEGORIES = ["cs.CV", "cs.AI"]
TRANSLATION_CACHE_VERSION = "summary-v2"
FETCH_STATE_VERSION = "1"

TOP_VENUES = [
    "CVPR", "ICCV", "ECCV", "NeurIPS", "ICLR", "AAAI", "IJCAI", "ICML", "KDD", "ACL", "EMNLP", "NAACL",
    "TPAMI", "IJCV", "TIP", "TNNLS", "JMLR", "TOG", "Nature", "Science",
]

CANONICAL_SIGNAL_LABELS = {
    "CVPR", "ICCV", "ECCV", "NEURIPS", "ICLR", "AAAI", "IJCAI", "ICML", "KDD", "ACL", "EMNLP", "NAACL",
    "TPAMI", "IJCV", "TIP", "TNNLS", "JMLR", "TOG", "ICME", "ICASSP", "ETRA", "IJCNN", "ICVGIP",
    "PERCOM", "SIGGRAPH", "ISBI", "ACMMM", "TAC", "IPM", "NNET", "CCL",
}

SIGNAL_ALIAS_PATTERNS = [
    (r"ieee/?cvf conference on computer vision and pattern recognition", "CVPR"),
    (r"conference on computer vision and pattern recognition", "CVPR"),
    (r"computer vision and pattern recognition conference", "CVPR"),
    (r"international conference on computer vision", "ICCV"),
    (r"european conference on computer vision", "ECCV"),
    (r"neural information processing systems", "NEURIPS"),
    (r"international conference on learning representations", "ICLR"),
    (r"international conference on machine learning", "ICML"),
    (r"association for the advancement of artificial intelligence", "AAAI"),
    (r"international joint conference on artificial intelligence", "IJCAI"),
    (r"international journal of computer vision", "IJCV"),
    (r"ieee transactions on pattern analysis and machine intelligence", "TPAMI"),
    (r"ieee transactions on image processing", "TIP"),
    (r"ieee transactions on neural networks and learning systems", "TNNLS"),
    (r"ieee international conference on multimedia and expo", "ICME"),
    (r"ieee international conference on acoustics, speech, and signal processing", "ICASSP"),
    (r"computer vision, graphics and image processing", "ICVGIP"),
    (r"international joint conference on neural networks", "IJCNN"),
    (r"eye tracking research and applications", "ETRA"),
    (r"pervasive computing and communications", "PERCOM"),
    (r"symposium on biomedical imaging", "ISBI"),
    (r"transactions on affective computing", "TAC"),
    (r"acm multimedia", "ACMMM"),
    (r"acm mm", "ACMMM"),
    (r"information processing and management", "IPM"),
    (r"\bneural networks\b", "NNET"),
    (r"\bccl ?20\d{2}\b", "CCL"),
    (r"journal of machine learning research", "JMLR"),
    (r"acm transactions on graphics", "TOG"),
    (r"siggraph", "SIGGRAPH"),
    (r"association for computing machinery", "ACM"),
    (r"nature", "Nature"),
    (r"science", "Science"),
]

DEFAULT_FOCUS_TERMS = [
    "tracking",
    "multi-object tracking",
    "multimodal tracking",
    "multimodal fusion",
    "test-time adaptation",
    "test-time training",
    "test-time update",
    "domain adaptation",
    "domain shift",
    "distribution shift",
    "covariate shift",
    "online adaptation",
    "prompt tuning",
    "prompt optimization",
    "prompt learning",
    "visual prompt tuning",
    "context optimization",
    "test-time calibration",
    "unsupervised domain adaptation",
    "open-vocabulary tracking",
]

DEFAULT_FOCUS_MATCHERS = [
    r"\btracking\b",
    r"\btracker\b",
    r"\bmot\b",
    r"multi[- ]object tracking",
    r"multi[- ]target tracking",
    r"multimodal tracking",
    r"cross[- ]modal tracking",
    r"multimodal fusion",
    r"cross[- ]modal fusion",
    r"\btest[- ]time adaptation\b",
    r"\btest[- ]time training\b",
    r"\btest[- ]time update\b",
    r"\btta\b",
    r"\bttt\b",
    r"\bdomain adaptation\b",
    r"\bdomain shift\b",
    r"\bdistribution shift\b",
    r"\bcovariate shift\b",
    r"\bdomain generalization\b",
    r"\bonline adaptation\b",
    r"\bcontinual adaptation\b",
    r"\bprompt tuning\b",
    r"\bprompt optimization\b",
    r"\bprompt[- ]based\b",
    r"\bprompt learning\b",
    r"\bvisual prompt tuning\b",
    r"\bcontext optimization\b",
    r"\btest[- ]time calibration\b",
    r"\bunsupervised domain adaptation\b",
    r"\bopen[- ]vocabulary tracking\b",
]

ACTIVE_FOCUS_TERMS = DEFAULT_FOCUS_TERMS[:]
ACTIVE_FOCUS_MATCHERS = DEFAULT_FOCUS_MATCHERS[:]

DOMAIN_RULES = {
    "tracking": ["tracking", "tracker", "tracklet", "mot", "multi-object tracking", "multi-target tracking"],
    "detection": ["detection", "detector", "detect", "object detection"],
    "segmentation": ["segmentation", "segment", "segmented"],
    "generation": ["diffusion", "generative", "generation", "generate", "synthesis"],
    "multimodal": ["multimodal", "multi-modal", "vision-language", "vision language", "vlm", "video-language", "cross-modal", "cross modal"],
    "adaptation": ["domain adaptation", "domain shift", "test-time adaptation", "test-time training", "test-time update", "tta", "ttt", "domain generalization", "online adaptation"],
    "medical": ["medical", "clinical", "radiology", "ct", "mri", "pathology", "endoscopy", "surgical"],
    "robotics": ["robot", "robotics", "navigation", "control", "autonomous driving", "embodied"],
}

TASK_RULES = {
    "classification": ["classification", "classifier", "classify"],
    "retrieval": ["retrieval", "retrieve", "search"],
    "reasoning": ["reasoning", "reason", "chain-of-thought", "cot"],
    "forecasting": ["forecast", "forecasting", "prediction", "predictive", "time series"],
    "localization": ["localization", "localize", "geo-localization", "pose", "registration"],
    "planning": ["planning", "plan", "decision"],
    "understanding": ["understanding", "comprehension", "scene understanding"],
}

TYPE_RULES = {
    "survey": ["survey", "review", "overview", "taxonomy"],
    "benchmark": ["benchmark", "leaderboard", "evaluation"],
    "dataset": ["dataset", "corpus"],
    "system": ["system", "framework", "platform", "pipeline"],
    "theory": ["theory", "bound", "proof", "convergence"],
    "application": ["application", "case study", "real-world", "deployment"],
}


@dataclass
class Paper:
    arxiv_id: str
    title: str
    title_zh: str
    authors: List[str]
    published: str
    updated: str
    categories: List[str]
    summary_en: str
    summary_zh: str
    link_abs: str
    link_pdf: str
    comment: str
    journal_ref: str
    major_area: str
    domain_tags: List[str] = field(default_factory=list)
    task_tags: List[str] = field(default_factory=list)
    type_tags: List[str] = field(default_factory=list)
    focus_tags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    accepted_venue: str = ""
    accepted_hint: str = ""


def safe_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def html_unescape_clean(s: str) -> str:
    return safe_text(html.unescape(s or ""))


def normalize_venue_name(text: str) -> str:
    clean = safe_text(text)
    lower = clean.lower()
    for pattern, label in SIGNAL_ALIAS_PATTERNS:
        if re.search(pattern, lower):
            return label
    for venue in TOP_VENUES:
        if venue.lower() in lower:
            return venue
    tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9&/-]{1,12}\b", clean)
    for token in tokens:
        norm = normalize_signal_token(token)
        if not norm:
            continue
        compact = norm.replace(" ", "")
        if compact in CANONICAL_SIGNAL_LABELS:
            return compact
        if norm in CANONICAL_SIGNAL_LABELS:
            return norm
    return clean.upper()[:80] if clean else ""


def parse_csv_terms(text: str) -> List[str]:
    if not text:
        return []
    return [safe_text(x).lower() for x in text.split(",") if safe_text(x)]


@lru_cache(maxsize=512)
def rule_term_pattern(term: str) -> re.Pattern[str]:
    clean = safe_text(term).lower()
    normalized = re.sub(r"[_/]+", " ", clean)
    parts = [p for p in re.split(r"[\s-]+", normalized) if p]
    if not parts:
        return re.compile(r"$^")
    if len(parts) == 1:
        token = re.escape(parts[0])
        pattern = rf"\b{token}\b"
    else:
        pattern = r"\b" + r"(?:[-\s]+)".join(re.escape(part) for part in parts) + r"\b"
    return re.compile(pattern, flags=re.I)


def rule_term_matches(text: str, term: str) -> bool:
    return bool(rule_term_pattern(term).search(text or ""))


def rule_term_occurrences(text: str, term: str) -> int:
    return len(rule_term_pattern(term).findall(text or ""))


def wildcard_focus_pattern(term: str) -> str:
    escaped = re.escape(term.lower())
    escaped = escaped.replace(r"\ ", r"[- ]")
    return rf"\b{escaped}\b"


def configure_focus_terms(domain: str, override_terms: str, extra_terms: str) -> List[str]:
    if override_terms.strip():
        terms = parse_csv_terms(override_terms)
    else:
        terms = DEFAULT_FOCUS_TERMS[:]
        if domain.lower() == "ai":
            terms.extend([
                "agent",
                "agents",
                "reasoning",
                "post-training",
                "alignment",
                "retrieval augmented generation",
                "tool use",
                "multimodal large language model",
            ])
    for term in parse_csv_terms(extra_terms):
        if term not in terms:
            terms.append(term)
    return terms


def configure_focus_matchers(terms: List[str]) -> List[str]:
    matchers = DEFAULT_FOCUS_MATCHERS[:]
    for term in terms:
        pat = wildcard_focus_pattern(term)
        if pat not in matchers:
            matchers.append(pat)
    return matchers


def curl_fetch_url(url: str, user_agent: str, timeout: int) -> str:
    curl_cmd = [
        "curl",
        "-L",
        "--silent",
        "--show-error",
        "--fail",
        "--connect-timeout",
        str(max(3, min(6, timeout // 2))),
        "--max-time",
        str(timeout),
        "-H",
        "Connection: close",
        "-H",
        "Accept: */*",
        "-A",
        user_agent,
        url,
    ]
    result = subprocess.run(curl_cmd, capture_output=True, text=True, check=False, timeout=timeout + 2)
    if result.returncode == 0 and result.stdout:
        return result.stdout
    raise RuntimeError(result.stderr.strip() or f"curl failed for {url}")


def request_url(url: str, timeout: int = 20, retries: int = 2, allow_partial: bool = True) -> str:
    user_agent = os.environ.get("ARXIV_USER_AGENT", "arxiv-daily-digest/2.0 (research-bot)")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Connection": "close",
            "Accept": "*/*",
        },
    )
    last_exc: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except http.client.IncompleteRead as exc:
            # Some HTML pages are still usable when partially transferred, but
            # API XML/JSON payloads must be complete or downstream parsing will fail.
            partial = exc.partial or b""
            if allow_partial and partial:
                return partial.decode("utf-8", errors="replace")
            last_exc = exc
            if attempt == retries:
                raise
            time.sleep(min(1.5, 0.6 * attempt))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
            retry_after = exc.headers.get("Retry-After") if hasattr(exc, "headers") else None
            if retry_after and str(retry_after).isdigit():
                sleep_s = min(5.0, float(retry_after))
            else:
                sleep_s = min(3.0, 0.8 * attempt + random.uniform(0.0, 0.3))
            time.sleep(sleep_s)
        except Exception as exc:
            last_exc = exc
            if attempt == retries:
                break
            time.sleep(min(2.5, 0.7 * attempt + random.uniform(0.0, 0.3)))

    try:
        return curl_fetch_url(url, user_agent=user_agent, timeout=timeout)
    except Exception as exc:
        last_exc = exc

    if last_exc:
        raise last_exc
    raise RuntimeError("Unknown request error")


def normalize_api_base(api_base: str) -> str:
    base = (api_base or DEFAULT_API_BASE).strip().rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def http_post_json(url: str, api_key: str, payload: dict, timeout: int = 60) -> Optional[dict]:
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def http_post_json_detailed(url: str, api_key: str, payload: dict, timeout: int = 60) -> Tuple[Optional[dict], str]:
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body), ""
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = safe_text(body) or safe_text(str(exc))
        return None, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return None, safe_text(str(exc))


def call_openai_json(
    model: str,
    api_key: str,
    api_base: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 60,
    max_output_tokens: int = 160,
) -> Optional[dict]:
    base = normalize_api_base(api_base)
    prefer_chat_only = ("moonshot" in base.lower()) or ("kimi" in base.lower())

    if not prefer_chat_only:
        # Try OpenAI Responses API first.
        response_payload = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": max_output_tokens,
        }
        data = http_post_json(f"{base}/responses", api_key=api_key, payload=response_payload, timeout=timeout)
        if data:
            output_text = data.get("output_text")
            if isinstance(output_text, str) and output_text.strip():
                try:
                    return json.loads(output_text)
                except Exception:
                    return None

            output = data.get("output", [])
            chunks: List[str] = []
            if isinstance(output, list):
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    for content in item.get("content", []) or []:
                        if isinstance(content, dict) and content.get("type") == "output_text":
                            txt = content.get("text", "")
                            if txt:
                                chunks.append(txt)
            merged = safe_text(" ".join(chunks))
            if merged:
                try:
                    return json.loads(merged)
                except Exception:
                    return None

    # Fallback for OpenAI-compatible providers (e.g., Kimi/Moonshot).
    chat_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": max_output_tokens,
    }
    chat_data = http_post_json(f"{base}/chat/completions", api_key=api_key, payload=chat_payload, timeout=timeout)
    if not chat_data:
        return None
    try:
        content = chat_data["choices"][0]["message"]["content"]
    except Exception:
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        return json.loads(content)
    except Exception:
        m = re.search(r"\{.*\}", content, flags=re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def call_openai_text(
    model: str,
    api_key: str,
    api_base: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 60,
    max_output_tokens: int = 1200,
    temperature: float = 0.2,
) -> Optional[str]:
    base = normalize_api_base(api_base)
    prefer_chat_only = ("moonshot" in base.lower()) or ("kimi" in base.lower())

    if not prefer_chat_only:
        response_payload = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_output_tokens": max_output_tokens,
        }
        data = http_post_json(f"{base}/responses", api_key=api_key, payload=response_payload, timeout=timeout)
        if data:
            output_text = safe_text(str(data.get("output_text", "")))
            if output_text:
                return output_text
            output = data.get("output", [])
            chunks: List[str] = []
            if isinstance(output, list):
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    for content in item.get("content", []) or []:
                        if isinstance(content, dict) and content.get("type") == "output_text":
                            txt = safe_text(str(content.get("text", "")))
                            if txt:
                                chunks.append(txt)
            merged = safe_text(" ".join(chunks))
            if merged:
                return merged

    chat_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_output_tokens,
    }
    chat_data = http_post_json(f"{base}/chat/completions", api_key=api_key, payload=chat_payload, timeout=timeout)
    if not chat_data:
        return None
    try:
        content = chat_data["choices"][0]["message"]["content"]
    except Exception:
        return None
    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text"):
                chunks.append(str(item["text"]))
        content = " ".join(chunks)
    content = safe_text(str(content))
    return content or None


def call_openai_text_detailed(
    model: str,
    api_key: str,
    api_base: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 60,
    max_output_tokens: int = 1200,
    temperature: float = 0.2,
) -> Tuple[Optional[str], str]:
    base = normalize_api_base(api_base)
    prefer_chat_only = ("moonshot" in base.lower()) or ("kimi" in base.lower())
    debug_lines: List[str] = []

    def extract_content_from_chat_payload(chat_data: dict) -> Optional[str]:
        try:
            choice = chat_data["choices"][0]
        except Exception:
            return None
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        candidates: List[object] = []
        if isinstance(message, dict):
            candidates.append(message.get("content"))
            candidates.append(message.get("reasoning_content"))
        if isinstance(choice, dict):
            candidates.append(choice.get("text"))
            delta = choice.get("delta", {})
            if isinstance(delta, dict):
                candidates.append(delta.get("content"))
        for candidate in candidates:
            if isinstance(candidate, list):
                chunks: List[str] = []
                for item in candidate:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text" and item.get("text"):
                        chunks.append(str(item["text"]))
                candidate = " ".join(chunks)
            clean = safe_text(str(candidate or ""))
            if clean:
                return clean
        return None

    if not prefer_chat_only:
        response_payload = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_output_tokens": max_output_tokens,
        }
        data, err = http_post_json_detailed(f"{base}/responses", api_key=api_key, payload=response_payload, timeout=timeout)
        if err:
            debug_lines.append(f"/responses error: {err}")
        if data:
            if isinstance(data.get("error"), dict):
                debug_lines.append(f"/responses api_error: {safe_text(str(data['error']))}")
            output_text = safe_text(str(data.get("output_text", "")))
            if output_text:
                return output_text, "\n".join(debug_lines)
            output = data.get("output", [])
            chunks: List[str] = []
            if isinstance(output, list):
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    for content in item.get("content", []) or []:
                        if isinstance(content, dict) and content.get("type") == "output_text":
                            txt = safe_text(str(content.get("text", "")))
                            if txt:
                                chunks.append(txt)
            merged = safe_text(" ".join(chunks))
            if merged:
                return merged, "\n".join(debug_lines)
            debug_lines.append("/responses returned no usable text")

    chat_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_output_tokens,
    }
    chat_data, err = http_post_json_detailed(f"{base}/chat/completions", api_key=api_key, payload=chat_payload, timeout=timeout)
    if err:
        debug_lines.append(f"/chat/completions error: {err}")
    if chat_data:
        if isinstance(chat_data.get("error"), dict):
            debug_lines.append(f"/chat/completions api_error: {safe_text(str(chat_data['error']))}")
        content = extract_content_from_chat_payload(chat_data)
        if content:
            return content, "\n".join(debug_lines)
        debug_lines.append("/chat/completions returned no usable text")

    completion_payload = {
        "model": model,
        "prompt": f"System:\n{system_prompt}\n\nUser:\n{user_prompt}\n\nAssistant:\n",
        "temperature": temperature,
        "max_tokens": max_output_tokens,
    }
    completion_data, err = http_post_json_detailed(f"{base}/completions", api_key=api_key, payload=completion_payload, timeout=timeout)
    if err:
        debug_lines.append(f"/completions error: {err}")
    if completion_data:
        if isinstance(completion_data.get("error"), dict):
            debug_lines.append(f"/completions api_error: {safe_text(str(completion_data['error']))}")
        try:
            text = safe_text(str(completion_data["choices"][0].get("text", "")))
        except Exception:
            text = ""
        if text:
            return text, "\n".join(debug_lines)
        debug_lines.append("/completions returned no usable text")

    return None, "\n".join(debug_lines)


def fallback_title_zh(title: str) -> str:
    return f"[未翻译] {title}"


SUMMARY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "its", "of",
    "on", "or", "our", "that", "the", "their", "these", "this", "to", "we", "with", "via", "using", "use",
    "based", "towards", "toward", "approach", "approaches", "framework", "frameworks", "method", "methods",
    "model", "models", "task", "tasks", "paper", "study", "results", "result", "show", "shows",
}

SUMMARY_METHOD_MARKERS = (
    "we propose", "we present", "we introduce", "our method", "our approach", "our framework",
    "this paper presents", "this work proposes", "we develop", "we design", "we formulate",
)
SUMMARY_RESULT_MARKERS = (
    "experiments show", "experimental results", "results show", "outperform", "achieve",
    "state-of-the-art", "significantly improves", "demonstrate", "we show that", "performs better",
)
SUMMARY_PROBLEM_MARKERS = (
    "challenge", "problem", "task", "aim", "goal", "focuses on", "we study", "we investigate",
    "domain shift", "test-time", "distribution shift", "tracking", "adaptation",
)


def split_summary_sentences(text: str) -> List[str]:
    clean = safe_text(text)
    if not clean:
        return []
    parts = [safe_text(part) for part in re.split(r"(?<=[\.!?])\s+", clean) if safe_text(part)]
    if not parts:
        return [clean]
    return parts


def summary_tokens(text: str) -> set[str]:
    tokens = {
        tok
        for tok in re.findall(r"[a-z0-9][a-z0-9-]{2,}", safe_text(text).lower())
        if tok not in SUMMARY_STOPWORDS and len(tok) >= 3
    }
    return tokens


def select_summary_sentences(
    title: str,
    abstract: str,
    hint_terms: Optional[List[str]] = None,
    max_sentences: int = 3,
    max_chars: int = 520,
) -> str:
    sentences = split_summary_sentences(abstract)
    if not sentences:
        return ""
    if len(sentences) <= max(1, max_sentences):
        return safe_text(" ".join(sentences))[:max_chars]

    title_tokens = summary_tokens(title)
    hint_token_pool: set[str] = set()
    for term in hint_terms or []:
        hint_token_pool.update(summary_tokens(term))

    rows = []
    for idx, sentence in enumerate(sentences):
        lower = sentence.lower()
        tokens = summary_tokens(sentence)
        if not tokens:
            continue
        role_flags = {
            "problem": any(marker in lower for marker in SUMMARY_PROBLEM_MARKERS),
            "method": any(marker in lower for marker in SUMMARY_METHOD_MARKERS),
            "result": any(marker in lower for marker in SUMMARY_RESULT_MARKERS),
        }
        overlap_title = len(tokens & title_tokens)
        overlap_hint = len(tokens & hint_token_pool)
        length_bonus = 1.6 if 8 <= len(tokens) <= 34 else 0.4
        position_bonus = max(0.0, 1.2 - (idx * 0.12))
        base_score = (
            overlap_title * 3.2
            + overlap_hint * 2.8
            + (3.5 if role_flags["method"] else 0.0)
            + (3.2 if role_flags["result"] else 0.0)
            + (2.6 if role_flags["problem"] else 0.0)
            + length_bonus
            + position_bonus
        )
        rows.append(
            {
                "idx": idx,
                "sentence": sentence,
                "tokens": tokens,
                "score": base_score,
                "roles": role_flags,
            }
        )

    if not rows:
        return safe_text(" ".join(sentences[:max_sentences]))[:max_chars]

    selected: List[Dict[str, object]] = []
    selected_indices: set[int] = set()

    def can_add(row: Dict[str, object]) -> bool:
        row_tokens = row["tokens"]
        for chosen in selected:
            chosen_tokens = chosen["tokens"]
            overlap = len(row_tokens & chosen_tokens) / max(1, len(row_tokens | chosen_tokens))
            if overlap >= 0.72:
                return False
        return True

    for role in ("problem", "method", "result"):
        role_candidates = [row for row in rows if row["roles"][role] and row["idx"] not in selected_indices]
        role_candidates.sort(key=lambda row: (row["score"], -row["idx"]), reverse=True)
        for row in role_candidates:
            if can_add(row):
                selected.append(row)
                selected_indices.add(row["idx"])
                break
        if len(selected) >= max_sentences:
            break

    while len(selected) < max_sentences:
        ranked = []
        for row in rows:
            if row["idx"] in selected_indices:
                continue
            novelty_penalty = 0.0
            for chosen in selected:
                overlap = len(row["tokens"] & chosen["tokens"]) / max(1, len(row["tokens"] | chosen["tokens"]))
                novelty_penalty = max(novelty_penalty, overlap)
            ranked.append((row["score"] - (novelty_penalty * 4.0), row))
        if not ranked:
            break
        ranked.sort(key=lambda item: (item[0], item[1]["score"], -item[1]["idx"]), reverse=True)
        picked = None
        for _adjusted, row in ranked:
            if can_add(row) or not selected:
                picked = row
                break
        if picked is None:
            break
        selected.append(picked)
        selected_indices.add(picked["idx"])

    selected.sort(key=lambda row: row["idx"])
    summary = safe_text(" ".join(str(row["sentence"]) for row in selected))
    if len(summary) <= max_chars:
        return summary

    clipped: List[str] = []
    total = 0
    for row in selected:
        sentence = str(row["sentence"])
        extra = len(sentence) + (1 if clipped else 0)
        if clipped and total + extra > max_chars:
            break
        clipped.append(sentence)
        total += extra
    return safe_text(" ".join(clipped))[:max_chars]


def fallback_summary_zh(abstract: str) -> str:
    clean = safe_text(abstract)
    if not clean:
        return "暂无摘要信息。"
    lead = select_summary_sentences("", clean, hint_terms=None, max_sentences=2, max_chars=240)
    return f"未启用LLM中文总结，原文要点：{lead}"


def google_summary_source(
    title: str,
    abstract: str,
    hint_terms: Optional[List[str]] = None,
    full_abstract: bool = False,
    sentences: int = 3,
) -> str:
    clean = safe_text(abstract)
    if not clean:
        return ""
    if full_abstract:
        return clean
    return select_summary_sentences(title, clean, hint_terms=hint_terms, max_sentences=max(1, sentences), max_chars=520)


def google_translate_text(text: str, timeout: int = 12, retries: int = 2) -> str:
    clean = safe_text(text)
    if not clean:
        return ""
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            params = urllib.parse.urlencode(
                {
                    "client": "gtx",
                    "sl": "en",
                    "tl": "zh-CN",
                    "dt": "t",
                    "q": clean,
                }
            )
            url = f"https://translate.googleapis.com/translate_a/single?{params}"
            user_agent = os.environ.get("ARXIV_USER_AGENT", "arxiv-daily-digest/2.0 (research-bot)")
            try:
                raw = curl_fetch_url(url, user_agent=user_agent, timeout=timeout)
            except Exception:
                raw = request_url(url, timeout=timeout, retries=1)
            data = json.loads(raw)
            chunks = []
            if isinstance(data, list) and data and isinstance(data[0], list):
                for item in data[0]:
                    if isinstance(item, list) and item and isinstance(item[0], str):
                        chunks.append(item[0])
            translated = safe_text("".join(chunks))
            if translated:
                return translated
        except Exception as exc:
            last_exc = exc
            time.sleep(min(2.0, 0.5 * attempt))
    if last_exc:
        raise last_exc
    return ""


def google_translate_texts(texts: List[str], timeout: int = 12, max_chars: int = 3000) -> List[str]:
    out = [""] * len(texts)
    batch: List[Tuple[int, str]] = []
    batch_chars = 0

    def flush(items: List[Tuple[int, str]]) -> None:
        if not items:
            return
        joined = "\n".join([f"[[[SEG{idx}]]] {text}" for idx, text in items])
        try:
            translated = google_translate_text(joined, timeout=timeout)
            matches = list(re.finditer(r"\[\[\[SEG(\d+)\]\]\]", translated))
            if len(matches) == len(items):
                for pos, match in enumerate(matches):
                    original_idx = int(match.group(1))
                    start = match.end()
                    end = matches[pos + 1].start() if pos + 1 < len(matches) else len(translated)
                    out[original_idx] = safe_text(translated[start:end])
                return
        except Exception:
            pass
        for original_idx, text in items:
            try:
                out[original_idx] = google_translate_text(text, timeout=timeout)
            except Exception:
                out[original_idx] = ""

    for idx, text in enumerate(texts):
        clean = safe_text(text)
        if not clean:
            continue
        marker_len = len(f"[[[SEG{idx}]]] ")
        if batch and batch_chars + len(clean) + marker_len > max_chars:
            flush(batch)
            batch = []
            batch_chars = 0
        batch.append((idx, clean))
        batch_chars += len(clean) + marker_len
    flush(batch)
    return out


def classify_from_rules(text: str, rules: Dict[str, List[str]]) -> List[str]:
    t = safe_text(text).lower()
    hit: List[str] = []
    for label, kws in rules.items():
        if any(rule_term_matches(t, k) for k in kws):
            hit.append(label)
    return hit or ["other"]


def extract_acceptance_info(comment: str, journal_ref: str) -> Tuple[str, str]:
    combined = f"{comment} {journal_ref}".strip()
    if not combined:
        return "", ""
    c = combined.lower()

    for venue in TOP_VENUES:
        if venue.lower() in c:
            if any(k in c for k in ["accept", "accepted", "to appear", "oral", "spotlight", "published"]):
                return normalize_venue_name(venue), safe_text(combined)
            return normalize_venue_name(venue), safe_text(combined)

    accepted = re.search(r"(accepted|to appear|published in)\s+([^.,;]+)", c)
    if accepted:
        venue_guess = accepted.group(2).strip()
        return normalize_venue_name(venue_guess), safe_text(combined)

    return "", safe_text(combined)


def parse_arxiv_entry(e: ET.Element, major_area: str) -> Paper:
    title = safe_text(e.findtext("a:title", default="", namespaces=ATOM_NS))
    summary_en = safe_text(e.findtext("a:summary", default="", namespaces=ATOM_NS))
    published = safe_text(e.findtext("a:published", default="", namespaces=ATOM_NS))
    updated = safe_text(e.findtext("a:updated", default="", namespaces=ATOM_NS))
    comment = safe_text(e.findtext("arxiv:comment", default="", namespaces=ATOM_NS))
    journal_ref = safe_text(e.findtext("arxiv:journal_ref", default="", namespaces=ATOM_NS))

    authors = [safe_text(a.findtext("a:name", default="", namespaces=ATOM_NS)) for a in e.findall("a:author", ATOM_NS)]
    authors = [a for a in authors if a]

    link_abs = ""
    link_pdf = ""
    for link in e.findall("a:link", ATOM_NS):
        rel = link.attrib.get("rel", "")
        href = link.attrib.get("href", "")
        title_attr = link.attrib.get("title", "")
        if rel == "alternate" and href:
            link_abs = href
        if title_attr == "pdf" and href:
            link_pdf = href

    id_text = safe_text(e.findtext("a:id", default="", namespaces=ATOM_NS))
    arxiv_id = id_text.rsplit("/", 1)[-1] if id_text else ""

    categories = [c.attrib.get("term", "") for c in e.findall("a:category", ATOM_NS)]
    categories = [c for c in categories if c]

    paper = Paper(
        arxiv_id=arxiv_id,
        title=title,
        title_zh="",
        authors=authors,
        published=published,
        updated=updated,
        categories=categories,
        summary_en=summary_en,
        summary_zh="",
        link_abs=link_abs or f"https://arxiv.org/abs/{arxiv_id}",
        link_pdf=link_pdf or f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        comment=comment,
        journal_ref=journal_ref,
        major_area=major_area,
    )

    return refresh_paper_derived_fields(paper)


def parse_feed(feed_xml: str, major_area: str) -> List[Paper]:
    root = ET.fromstring(feed_xml)
    return [parse_arxiv_entry(e, major_area) for e in root.findall("a:entry", ATOM_NS)]


def validate_feed_xml(feed_xml: str) -> None:
    if not safe_text(feed_xml):
        raise ValueError("Empty arXiv feed payload")
    ET.fromstring(feed_xml)


def strip_html_tags(text: str) -> str:
    return safe_text(re.sub(r"<[^>]+>", " ", text or ""))


def parse_recent_list_html(category: str, html_text: str, limit: int) -> List[Paper]:
    major_area = "CV" if category == "cs.CV" else "AI"
    pairs = re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", html_text, flags=re.S)
    papers: List[Paper] = []
    now_utc = dt.datetime.now(dt.timezone.utc)
    for idx, (dt_block, dd_block) in enumerate(pairs):
        m_id = re.search(r'href\s*=\s*"/abs/([^"\s]+)"', dt_block)
        if not m_id:
            continue
        arxiv_id = safe_text(m_id.group(1))
        title_m = re.search(r"<div class='list-title[^>]*>.*?<span class='descriptor'>Title:</span>(.*?)</div>", dd_block, flags=re.S)
        title = strip_html_tags(title_m.group(1) if title_m else "")

        authors_m = re.search(r"<div class='list-authors'>(.*?)</div>", dd_block, flags=re.S)
        authors_block = authors_m.group(1) if authors_m else ""
        authors = [safe_text(html.unescape(x)) for x in re.findall(r">([^<]+)</a>", authors_block)]
        authors = [a for a in authors if a]

        comments_m = re.search(r"<div class='list-comments[^>]*>.*?<span class='descriptor'>Comments:</span>(.*?)</div>", dd_block, flags=re.S)
        comment = strip_html_tags(comments_m.group(1) if comments_m else "")
        subj_m = re.search(r"<div class='list-subjects'>(.*?)</div>", dd_block, flags=re.S)
        subj_raw = strip_html_tags(subj_m.group(1) if subj_m else "")
        cats = re.findall(r"\((cs\.[A-Z]{2}|stat\.[A-Z]{2}|eess\.[A-Z]{2}|math\.[A-Z]{2})\)", subj_raw)
        categories = sorted(set(cats)) if cats else [category]

        # Keep ordering by assigning descending pseudo timestamps.
        pub_dt = now_utc - dt.timedelta(seconds=idx)
        pub_iso = pub_dt.isoformat().replace("+00:00", "Z")

        p = Paper(
            arxiv_id=arxiv_id,
            title=title or arxiv_id,
            title_zh="",
            authors=authors,
            published=pub_iso,
            updated=pub_iso,
            categories=categories,
            summary_en="",
            summary_zh="",
            link_abs=f"https://arxiv.org/abs/{arxiv_id}",
            link_pdf=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
            comment=comment,
            journal_ref="",
            major_area=major_area,
        )
        papers.append(refresh_paper_derived_fields(p))
    return papers[:limit]


def extract_abs_field(html_text: str, label: str) -> str:
    pattern = rf"<td class=[\"']tablecell label[\"']>{re.escape(label)}:</td>\s*<td class=[\"']tablecell [^\"']*[\"']>(.*?)</td>"
    m = re.search(pattern, html_text, flags=re.S | re.I)
    return strip_html_tags(m.group(1) if m else "")


def parse_abs_page_details(html_text: str) -> Dict[str, object]:
    abstract = ""
    abstract_match = re.search(r"<blockquote class=\"abstract[^\"]*\">(.*?)</blockquote>", html_text, flags=re.S | re.I)
    if abstract_match:
        abstract_raw = re.sub(r"<span class=\"descriptor\">Abstract:</span>", " ", abstract_match.group(1), flags=re.I)
        abstract = strip_html_tags(abstract_raw.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " "))

    comments = extract_abs_field(html_text, "Comments")
    journal_ref = extract_abs_field(html_text, "Journal reference")

    subjects = ""
    subjects_match = re.search(r"<td class=[\"']tablecell subjects[\"']>(.*?)</td>", html_text, flags=re.S | re.I)
    if subjects_match:
        subjects = strip_html_tags(subjects_match.group(1))
    categories = re.findall(r"\((cs\.[A-Z]{2}|stat\.[A-Z]{2}|eess\.[A-Z]{2}|math\.[A-Z]{2})\)", subjects)

    return {
        "summary_en": html_unescape_clean(abstract),
        "comment": html_unescape_clean(comments),
        "journal_ref": html_unescape_clean(journal_ref),
        "categories": sorted(set(categories)),
    }


def fetch_abs_page(abs_url: str, attempts: int = 3) -> str:
    urls = [abs_url]
    if abs_url.startswith("https://"):
        urls.append("http://" + abs_url[len("https://"):])
    last_exc: Optional[Exception] = None
    user_agent = os.environ.get("ARXIV_USER_AGENT", "arxiv-daily-digest/2.0 (research-bot)")
    for attempt in range(1, max(1, attempts) + 1):
        for url in urls:
            try:
                return curl_fetch_url(url, user_agent=user_agent, timeout=12)
            except Exception as exc:
                last_exc = exc
            try:
                return request_url(url, timeout=12, retries=1)
            except Exception as exc:
                last_exc = exc
                continue
        if attempt < attempts:
            time.sleep(min(2.0, 0.4 * attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError("Failed to fetch arXiv abs page")


def enrich_papers_from_abs_pages(papers: List[Paper], limit: int, cache: Dict[str, dict], cache_path: str) -> None:
    if limit == 0:
        return
    scoped_papers = papers if limit < 0 else papers[:limit]
    touched = 0
    for p in scoped_papers:
        cache_key = p.arxiv_id or p.link_abs
        cached = cache.get(cache_key, {})
        cached_summary = safe_text(str(cached.get("summary_en", ""))) if cached else ""
        cache_fetched = bool(cached_summary)
        if cached:
            p.summary_en = cached_summary or p.summary_en
            p.comment = safe_text(str(cached.get("comment", p.comment or ""))) or p.comment
            p.journal_ref = safe_text(str(cached.get("journal_ref", p.journal_ref or ""))) or p.journal_ref
            cached_categories = list(cached.get("categories", []) or [])
            if cached_categories:
                p.categories = sorted(set(p.categories + cached_categories))
        if cache_fetched or p.summary_en:
            refresh_paper_derived_fields(p)
            continue
        try:
            html_text = fetch_abs_page(p.link_abs)
            details = parse_abs_page_details(html_text)
        except Exception:
            continue
        if details.get("summary_en"):
            p.summary_en = str(details["summary_en"])
        if details.get("comment"):
            p.comment = str(details["comment"])
        if details.get("journal_ref"):
            p.journal_ref = str(details["journal_ref"])
        extra_categories = list(details.get("categories", []) or [])
        if extra_categories:
            p.categories = sorted(set(p.categories + extra_categories))
        if not p.summary_en:
            continue
        cache[cache_key] = {
            "summary_en": p.summary_en,
            "comment": p.comment,
            "journal_ref": p.journal_ref,
            "categories": p.categories,
            "fetched": True,
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        refresh_paper_derived_fields(p)
        touched += 1
        if touched % 10 == 0:
            save_llm_cache(cache_path, cache)
        time.sleep(0.1)
    if touched:
        save_llm_cache(cache_path, cache)


def enrich_missing_abstracts_from_abs_pages(
    papers: List[Paper],
    limit: int,
    cache: Dict[str, dict],
    cache_path: str,
    label: str,
    passes: int = 2,
) -> None:
    if limit == 0:
        return
    unique_papers = dedupe_papers(papers)
    last_ready = 0
    last_total = 0
    for pass_idx in range(1, max(1, passes) + 1):
        targets = [p for p in unique_papers if not safe_text(p.summary_en)]
        if limit > 0:
            targets = targets[:limit]
        if not targets:
            print(f"[INFO] {label} abs enrichment ready: 0/0; missing abstracts: 0")
            return
        enrich_papers_from_abs_pages(targets, -1, cache, cache_path)
        last_ready = sum(1 for p in targets if safe_text(p.summary_en))
        last_total = len(targets)
        missing_after = sum(1 for p in unique_papers if not safe_text(p.summary_en))
        if missing_after == 0:
            print(f"[INFO] {label} abs enrichment ready: {last_ready}/{last_total}; missing abstracts: 0")
            return
        if pass_idx < passes:
            print(f"[INFO] {label} abs enrichment retry {pass_idx}: {last_ready}/{last_total}; missing abstracts: {missing_after}")
            time.sleep(0.5)
    missing_after = sum(1 for p in unique_papers if not safe_text(p.summary_en))
    print(f"[INFO] {label} abs enrichment ready: {last_ready}/{last_total}; missing abstracts: {missing_after}")


def fetch_recent_list_page(category: str, skip: int, show: int) -> str:
    last_exc: Optional[Exception] = None
    urls = [
        f"https://arxiv.org/list/{category}/recent?skip={skip}&show={show}",
        f"http://arxiv.org/list/{category}/recent?skip={skip}&show={show}",
    ]
    user_agent = os.environ.get("ARXIV_USER_AGENT", "arxiv-daily-digest/2.0 (research-bot)")
    for url in urls:
        try:
            return curl_fetch_url(url, user_agent=user_agent, timeout=8)
        except (ssl.SSLError, urllib.error.URLError, TimeoutError, OSError, http.client.IncompleteRead) as exc:
            last_exc = exc
        except Exception as exc:
            last_exc = exc
        try:
            return request_url(url, timeout=8, retries=1)
        except (ssl.SSLError, urllib.error.URLError, TimeoutError, OSError, http.client.IncompleteRead) as exc:
            last_exc = exc
            continue
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        raise last_exc
    raise RuntimeError("Failed to fetch arXiv recent list page")


def fetch_recent_list_fallback(category: str, limit: int = 300) -> List[Paper]:
    last_exc: Optional[Exception] = None
    for show in (100, 50, 25):
        merged: Dict[str, Paper] = {}
        skip = 0
        attempts = 0
        empty_pages = 0
        max_attempts = max(4, min(60, (limit + show - 1) // show + 4))
        while len(merged) < limit and attempts < max_attempts:
            try:
                html_text = fetch_recent_list_page(category, skip=skip, show=show)
            except Exception as exc:
                last_exc = exc
                if merged:
                    break
                empty_pages += 1
                if empty_pages >= 2:
                    break
                continue
            page = parse_recent_list_html(category, html_text, limit=show)
            if not page:
                break
            for p in page:
                key = p.arxiv_id or p.link_abs
                if key not in merged:
                    merged[key] = p
            skip += show
            attempts += 1
            time.sleep(0.15)
        out = list(merged.values())
        out.sort(key=lambda x: x.published, reverse=True)
        if out:
            return out[:limit]
    if last_exc:
        raise last_exc
    return []


def clean_tag_list(tags: List[str]) -> List[str]:
    uniq = [t for t in dict.fromkeys(tags) if t]
    if len(uniq) > 1:
        uniq = [t for t in uniq if t != "other"]
    return uniq or ["other"]


KEYWORD_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "on", "in", "with", "without", "via", "from", "by", "into",
    "using", "use", "used", "toward", "towards", "through", "under", "over", "beyond", "new", "novel", "robust",
    "efficient", "improved", "improving", "general", "generic", "unified", "towards", "based", "driven",
    "learning", "learn", "model", "models", "framework", "frameworks", "method", "methods", "approach", "approaches",
    "system", "systems", "paper", "study", "analysis", "task", "tasks", "benchmark", "dataset", "datasets",
    "image", "images", "video", "videos", "visual", "vision", "language", "multimodal", "artificial", "intelligence",
    "computer", "toward", "large", "small", "foundation", "towards", "across", "cross", "via", "based", "other",
    "that", "this", "these", "those", "better", "less", "more", "many", "few", "all", "not", "our", "their",
    "its", "your", "my", "than", "then", "while", "where", "when", "which", "whose", "improve", "improves",
    "improved", "improving", "toward", "towards", "beyond", "toward", "towards", "real", "complete",
    "universal", "versatile", "aware", "guided", "driven", "oriented",
}

KEYWORD_SEGMENT_STOPWORDS = {
    "framework", "frameworks", "model", "models", "system", "systems", "method", "methods", "approach",
    "approaches", "pipeline", "pipelines", "study", "analysis", "suite", "toolkit", "agent", "agents",
}

KEYWORD_SINGLE_TOKEN_NOISE = {
    "adaptation", "application", "automation", "context", "detail", "diagnosis", "domain", "driving",
    "federated", "forecasting", "generation", "guidance", "human", "incrementally", "instrument",
    "instruments", "knowledge", "localization", "medical", "mechanisms", "motion", "multiple", "planetary",
    "positive", "privacy", "propagation", "proxy", "reasoning", "retrieval", "scale", "semantic", "sensing",
    "single", "survey", "theory", "tracking", "understanding",
}

KEYWORD_PHRASE_PREFIX_NOISE = {
    "benchmarking", "enhancing", "exploring", "leveraging", "mechanisms", "mitigating", "not", "towards",
    "toward", "understanding", "unifying", "unlocking",
}

KEYWORD_PHRASE_ALIASES = {
    "multi object tracking": "multi-object tracking",
    "test time adaptation": "test-time adaptation",
    "test time training": "test-time training",
    "test time update": "test-time update",
    "domain shift": "domain shift",
    "distribution shift": "distribution shift",
    "domain adaptation": "domain adaptation",
    "vision language action": "vision-language-action",
    "vision language model": "vision-language model",
    "vision language models": "vision-language model",
    "vision language": "vision-language",
    "open vocabulary tracking": "open-vocabulary tracking",
    "prompt tuning": "prompt tuning",
    "visual prompt tuning": "visual prompt tuning",
    "prompt learning": "prompt learning",
    "online adaptation": "online adaptation",
    "medical image segmentation": "medical image segmentation",
    "autonomous driving": "autonomous driving",
}


def normalize_keyword_phrase(text: str) -> str:
    clean = safe_text(text).strip(".,;:()[]{}-")
    if not clean:
        return ""
    if re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,15}", clean):
        return clean.upper()
    lowered = clean.lower()
    lowered = re.sub(r"[_/]+", " ", lowered)
    lowered = lowered.replace("-", " ")
    lowered = re.sub(r"\s+", " ", lowered).strip()
    lowered = re.sub(r"\b\d{2,4}\b", "", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    if lowered in KEYWORD_PHRASE_ALIASES:
        return KEYWORD_PHRASE_ALIASES[lowered]
    if len(lowered) < 3:
        return ""
    return lowered


def format_keyword_label(keyword: str) -> str:
    text = safe_text(keyword)
    if not text:
        return ""
    upper_text = text.upper()
    if upper_text in CANONICAL_SIGNAL_LABELS:
        return upper_text
    if upper_text in {"VLA", "VLM", "MOT", "TTA", "TTT", "RAG", "MRI", "CT", "RL", "COT", "DINO", "LLM", "MLLM", "CLIP", "SAM", "VQA", "UAV", "OOD", "ID", "OCR", "CNN", "GNN"}:
        return upper_text
    if re.fullmatch(r"[A-Z0-9-]{2,12}", text):
        return text
    parts = []
    for word in text.split():
        if re.fullmatch(r"[A-Z0-9-]{2,12}", word):
            parts.append(word)
        elif "-" in word:
            hy = "-".join([
                w.upper() if w.upper() in {"VLA", "VLM", "MOT", "TTA", "TTT", "RAG", "MRI", "CT", "RL", "COT", "DINO", "LLM", "MLLM", "CLIP", "SAM", "VQA", "UAV", "OOD", "ID", "OCR", "CNN", "GNN"} else w.capitalize()
                for w in word.split("-")
            ])
            parts.append(hy)
        else:
            parts.append(word.capitalize())
    return " ".join(parts)


def map_category_keyword(category: str) -> str:
    mapping = {
        "cs.LG": "machine learning",
        "stat.ML": "machine learning",
        "cs.RO": "robotics",
        "cs.CL": "natural language processing",
    }
    return mapping.get(category, "")


def extract_title_phrase_candidates(title: str) -> List[str]:
    raw_title = safe_text(title)
    if not raw_title:
        return []

    candidates: List[str] = []

    def push(text: str) -> None:
        text = safe_text(re.sub(r"^[Aa]n?\s+", "", safe_text(text)))
        if not text:
            return
        words = text.split()
        if len(words) < 2 or len(words) > 7:
            return
        if "," in text and len(words) > 5:
            return
        if words[-1].lower() in KEYWORD_SEGMENT_STOPWORDS:
            return
        candidates.append(text)

    title_segments = [safe_text(seg) for seg in re.split(r"[:;]", raw_title) if safe_text(seg)]
    for seg in title_segments:
        lower_seg = seg.lower()
        has_generic_head = any(f" {term} " in f" {lower_seg} " for term in KEYWORD_SEGMENT_STOPWORDS)
        if not has_generic_head or not any(splitter in lower_seg for splitter in [" for ", " via ", " with ", " under ", " using ", " from ", " on "]):
            push(seg)
        lower_seg = seg.lower()
        for splitter in [" for ", " via ", " with ", " under ", " using ", " from ", " on "]:
            if splitter in lower_seg:
                idx = lower_seg.index(splitter)
                push(seg[:idx])
                break

    return list(dict.fromkeys(candidates))


def is_keyword_candidate_noise(term: str, source: str = "") -> bool:
    norm = normalize_keyword_phrase(term)
    if not norm:
        return True
    words = norm.split()
    compact = norm.replace(" ", "").replace("-", "")
    if compact.upper() in CANONICAL_SIGNAL_LABELS:
        return False
    if norm in ACTIVE_FOCUS_TERMS:
        return False
    if len(words) == 1:
        token = words[0]
        if token in KEYWORD_SINGLE_TOKEN_NOISE and source == "ngram":
            return True
        if token in KEYWORD_STOPWORDS:
            return True
        if len(token) < 4 and not re.fullmatch(r"[A-Z0-9-]{2,12}", term):
            return True
    if len(words) >= 2:
        if words[0] in KEYWORD_STOPWORDS or words[-1] in KEYWORD_STOPWORDS:
            return True
        if words[0] in KEYWORD_PHRASE_PREFIX_NOISE:
            return True
        if words[-1] in KEYWORD_SEGMENT_STOPWORDS:
            return True
    return False


def extract_keywords_from_paper(p: Paper, max_keywords: int = 8) -> List[str]:
    scores: Dict[str, int] = {}

    def add(term: str, weight: int, source: str = "") -> None:
        norm = normalize_keyword_phrase(term)
        if not norm:
            return
        if norm in KEYWORD_STOPWORDS:
            return
        if len(norm) < 3:
            return
        if is_keyword_candidate_noise(norm, source):
            return
        scores[norm] = scores.get(norm, 0) + weight

    title_text = safe_text(p.title)
    summary_text = safe_text(p.summary_en)
    summary_head = " ".join(re.split(r"(?<=[\.!?])\s+", summary_text)[:2]).strip()
    merged_text = f"{title_text} {summary_head}".lower()

    for tag in clean_tag_list(p.domain_tags) + clean_tag_list(p.task_tags) + clean_tag_list(p.type_tags):
        if tag == "other":
            continue
        add(tag, 3, source="tag")
    for tag in clean_tag_list(p.focus_tags):
        if tag == "other":
            continue
        add(tag, 8, source="focus")
    for cat in p.categories:
        cat_kw = map_category_keyword(cat)
        if cat_kw:
            add(cat_kw, 3, source="category")
    if p.accepted_venue:
        add(normalize_venue_name(p.accepted_venue), 5, source="venue")

    if ":" in title_text:
        prefix, suffix = title_text.split(":", 1)
        if 2 <= len(prefix.split()) <= 6:
            add(prefix, 8, source="title-segment")
        title_text = safe_text(suffix)

    for phrase in extract_title_phrase_candidates(p.title):
        add(phrase, 8, source="title-segment")

    for term in ACTIVE_FOCUS_TERMS:
        if term in merged_text:
            add(term, 10, source="focus")

    acronym_tokens = re.findall(r"\b[A-Z][A-Z0-9-]{1,12}\b", p.title or "")
    for token in acronym_tokens:
        add(token, 7, source="acronym")

    hyphen_tokens = re.findall(r"\b[a-zA-Z]{3,}(?:[-/][a-zA-Z]{2,})+\b", p.title or "")
    for token in hyphen_tokens:
        add(token, 7, source="hyphen")

    text_for_ngrams = safe_text(p.title).lower()
    words = re.findall(r"[a-z0-9][a-z0-9+-]*", text_for_ngrams)
    for n in (3, 2):
        for idx in range(0, max(0, len(words) - n + 1)):
            gram_words = words[idx:idx + n]
            if any(w in KEYWORD_STOPWORDS for w in gram_words):
                continue
            phrase = " ".join(gram_words)
            if len(phrase) < 5:
                continue
            if is_keyword_candidate_noise(phrase, source="ngram"):
                continue
            if gram_words[-1] in KEYWORD_SEGMENT_STOPWORDS:
                continue
            add(phrase, 6 if n == 3 else 5, source="ngram")

    ordered = sorted(scores.items(), key=lambda item: (item[1], len(item[0])), reverse=True)
    out: List[str] = []
    for keyword, _score in ordered:
        if keyword in out:
            continue
        if any(keyword in existing or existing in keyword for existing in out):
            continue
        out.append(keyword)
        if len(out) >= max_keywords:
            break
    specific = [kw for kw in out if kw not in GRAPH_GENERIC_KEYWORDS]
    if len(specific) >= 3:
        return specific[:max_keywords]
    return out


def graph_theme_min_score(keyword: str) -> int:
    compact = keyword.replace(" ", "").replace("-", "").replace("/", "")
    if compact.upper() in CANONICAL_SIGNAL_LABELS:
        return 3
    if keyword in ACTIVE_FOCUS_TERMS:
        if keyword in GRAPH_GENERIC_KEYWORDS or (" " not in keyword and "-" not in keyword):
            return 7
        return 4
    if keyword in GRAPH_GENERIC_KEYWORDS:
        return 7
    if re.fullmatch(r"[A-Z0-9-]{2,12}", keyword):
        return 4
    if " " in keyword or "-" in keyword or "/" in keyword:
        return 4
    return 5


def collect_graph_theme_scores(p: Paper) -> Dict[str, int]:
    scores: Dict[str, int] = {}
    explicit_blob = safe_text(f"{p.title} {p.summary_en} {p.comment}").lower()
    title_blob = safe_text(p.title).lower()

    def add(term: str, weight: int, source: str = "graph") -> None:
        norm = normalize_keyword_phrase(term)
        if not norm or norm == "other":
            return
        if is_keyword_candidate_noise(norm, source=source):
            compact = norm.replace(" ", "").replace("-", "").replace("/", "")
            if norm not in ACTIVE_FOCUS_TERMS and compact.upper() not in CANONICAL_SIGNAL_LABELS:
                return
        scores[norm] = scores.get(norm, 0) + weight

    for term in p.keywords or []:
        add(term, 6 if (term in ACTIVE_FOCUS_TERMS or " " in term or "-" in term) else 4, source="keyword")
    for term in clean_tag_list(p.focus_tags):
        add(term, 8, source="focus")
    for term in clean_tag_list(p.domain_tags) + clean_tag_list(p.task_tags) + clean_tag_list(p.type_tags):
        add(term, 2, source="tag")
    for cat in p.categories:
        mapped = map_category_keyword(cat)
        if mapped:
            add(mapped, 3, source="category")
    if p.accepted_venue:
        add(normalize_venue_name(p.accepted_venue), 4, source="venue")

    title_hits: Dict[str, bool] = {}
    occurrence_counts: Dict[str, int] = {}
    for term in list(scores.keys()):
        title_hit = rule_term_matches(title_blob, term)
        occurrence_count = rule_term_occurrences(explicit_blob, term)
        title_hits[term] = title_hit
        occurrence_counts[term] = occurrence_count
        if title_hit:
            scores[term] += 4
        elif occurrence_count > 0:
            scores[term] += 2 + min(3, occurrence_count - 1)

    filtered = {
        term: score
        for term, score in scores.items()
        if score >= graph_theme_min_score(term)
        and not (
            (term in GRAPH_GENERIC_KEYWORDS or (term in ACTIVE_FOCUS_TERMS and " " not in term and "-" not in term))
            and not title_hits.get(term, False)
            and occurrence_counts.get(term, 0) < 2
        )
    }
    ordered = sorted(
        filtered.items(),
        key=lambda item: (
            item[1],
            1 if item[0] in ACTIVE_FOCUS_TERMS else 0,
            1 if (" " in item[0] or "-" in item[0]) else 0,
            len(item[0]),
        ),
        reverse=True,
    )
    return dict(ordered)


def collect_graph_theme_candidates(p: Paper, max_candidates: int = 12) -> List[str]:
    return list(collect_graph_theme_scores(p).keys())[:max_candidates]


def refresh_paper_derived_fields(p: Paper) -> Paper:
    merged_text = f"{p.title} {p.summary_en} {p.comment} {p.journal_ref}"
    p.domain_tags = classify_from_rules(merged_text, DOMAIN_RULES)
    p.task_tags = classify_from_rules(merged_text, TASK_RULES)
    p.type_tags = classify_from_rules(merged_text, TYPE_RULES)
    lower = merged_text.lower()
    p.focus_tags = [term for term in ACTIVE_FOCUS_TERMS if rule_term_matches(lower, term)]
    venue, hint = extract_acceptance_info(p.comment, p.journal_ref)
    p.accepted_venue = venue
    p.accepted_hint = hint
    p.keywords = extract_keywords_from_paper(p)
    return p


def iso_to_dt(iso_s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso_s.replace("Z", "+00:00"))


def build_category_query(category: str) -> str:
    return f"cat:{category}"


def build_focus_query(categories: List[str], focus_terms: List[str]) -> str:
    cats = " OR ".join([f"cat:{c}" for c in categories])
    terms = " OR ".join([f"all:\"{t}\"" if " " in t else f"all:{t}" for t in focus_terms])
    return f"({cats}) AND ({terms})"


def fetch_page(search_query: str, start: int, max_results: int, sort_by: str = "submittedDate", sort_order: str = "descending") -> str:
    params = {
        "search_query": search_query,
        "start": start,
        "max_results": max_results,
        "sortBy": sort_by,
        "sortOrder": sort_order,
    }
    query_str = urllib.parse.urlencode(params)
    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):
        for base in ARXIV_API_ENDPOINTS:
            url = f"{base}?{query_str}"
            try:
                xml_text = request_url(url, timeout=20, retries=2, allow_partial=False)
                validate_feed_xml(xml_text)
                return xml_text
            except Exception as exc:
                last_exc = exc
                continue
        if attempt < 3:
            time.sleep(min(2.0, 0.5 * attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError("Failed to fetch arXiv page")


def fetch_daily_by_category(
    category: str,
    target_day: dt.date,
    tz_name: str,
    page_size: int,
    max_scan: int,
    day_window_days: int = 2,
) -> List[Paper]:
    tz = ZoneInfo(tz_name)
    days = max(1, day_window_days)
    start_day = target_day - dt.timedelta(days=days - 1)
    start_local = dt.datetime.combine(start_day, dt.time.min, tzinfo=tz)
    end_local = dt.datetime.combine(target_day + dt.timedelta(days=1), dt.time.min, tzinfo=tz)
    start_utc = start_local.astimezone(dt.timezone.utc)
    end_utc = end_local.astimezone(dt.timezone.utc)

    query = build_category_query(category)
    major_area = "CV" if category == "cs.CV" else "AI"

    gathered: List[Paper] = []
    start = 0
    while start < max_scan:
        xml_text = fetch_page(query, start=start, max_results=page_size, sort_by="submittedDate", sort_order="descending")
        page = parse_feed(xml_text, major_area=major_area)
        if not page:
            break

        should_stop = False
        for p in page:
            try:
                pub = iso_to_dt(p.published)
            except Exception:
                continue

            if pub < start_utc:
                should_stop = True
                continue
            if pub >= end_utc:
                continue
            gathered.append(p)

        start += page_size
        time.sleep(0.45)
        if should_stop:
            break

    return gathered


def fetch_latest_by_category(category: str, limit: int, page_size: int = 200) -> List[Paper]:
    query = build_category_query(category)
    major_area = "CV" if category == "cs.CV" else "AI"
    got: List[Paper] = []
    start = 0
    while len(got) < limit:
        chunk = min(page_size, limit - len(got))
        xml_text = fetch_page(query, start=start, max_results=chunk, sort_by="submittedDate", sort_order="descending")
        page = parse_feed(xml_text, major_area=major_area)
        if not page:
            break
        got.extend(page)
        start += chunk
        time.sleep(0.35)
    return got[:limit]


def dedupe_papers(papers: Iterable[Paper]) -> List[Paper]:
    merged: Dict[str, Paper] = {}
    for p in papers:
        key = p.arxiv_id or p.link_abs
        if key in merged:
            old = merged[key]
            # cross-list markers
            if p.major_area != old.major_area:
                old.major_area = "CV+AI"
            old.categories = sorted(set(old.categories + p.categories))
            if p.comment and p.comment not in old.comment:
                old.comment = safe_text(f"{old.comment}; {p.comment}")
            if p.journal_ref and p.journal_ref not in old.journal_ref:
                old.journal_ref = safe_text(f"{old.journal_ref}; {p.journal_ref}")
            old.domain_tags = sorted(set(old.domain_tags + p.domain_tags))
            old.task_tags = sorted(set(old.task_tags + p.task_tags))
            old.type_tags = sorted(set(old.type_tags + p.type_tags))
            old.focus_tags = sorted(set(old.focus_tags + p.focus_tags))
            old.keywords = sorted(set(old.keywords + p.keywords))
        else:
            merged[key] = p

    out = list(merged.values())
    out.sort(key=lambda x: x.published, reverse=True)
    return out


def paper_content_hash(p: Paper) -> str:
    raw = f"{TRANSLATION_CACHE_VERSION}\n{p.title}\n{p.summary_en}\n{p.comment}\n{p.journal_ref}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_llm_cache(path: str) -> Dict[str, dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_llm_cache(path: str, cache: Dict[str, dict]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def missing_translation(p: Paper) -> bool:
    title_missing = (not p.title_zh) or p.title_zh.startswith("[未翻译]")
    summary_missing = (
        (not p.summary_zh)
        or p.summary_zh.startswith("未启用LLM中文总结")
        or p.summary_zh == "暂无摘要信息。"
        or (bool(p.title_zh) and safe_text(p.summary_zh) == safe_text(p.title_zh))
    )
    return title_missing or summary_missing


def cached_translation_is_usable(p: Paper, cached: Dict[str, object]) -> bool:
    title_zh = safe_text(str(cached.get("title_zh", "")))
    summary_zh = safe_text(str(cached.get("summary_zh", "")))
    if not title_zh or not summary_zh:
        return False
    if title_zh.startswith("[未翻译]"):
        return False
    if summary_zh.startswith("未启用LLM中文总结") or summary_zh == "暂无摘要信息。":
        return False
    if safe_text(title_zh) == safe_text(summary_zh):
        return False
    return True


def apply_translation_cache(papers: List[Paper], caches: List[Dict[str, dict]]) -> None:
    for p in papers:
        cache_key = p.arxiv_id or p.link_abs
        p_hash = paper_content_hash(p)
        for cache in caches:
            cached = cache.get(cache_key, {})
            if cached.get("hash") == p_hash and cached.get("status") == "ok" and cached_translation_is_usable(p, cached):
                p.title_zh = safe_text(str(cached.get("title_zh", ""))) or p.title_zh
                p.summary_zh = safe_text(str(cached.get("summary_zh", ""))) or p.summary_zh
                break


def google_enrich_title_and_summary(
    papers: List[Paper],
    cache: Dict[str, dict],
    cache_path: str,
    limit: int = -1,
    timeout: int = 12,
    full_abstract: bool = False,
    summary_sentences: int = 3,
    batch_size: int = 12,
) -> None:
    changed = 0
    targets: List[Paper] = []
    for p in papers:
        if not missing_translation(p):
            continue
        cache_key = p.arxiv_id or p.link_abs
        p_hash = paper_content_hash(p)
        cached = cache.get(cache_key, {})
        if cached.get("hash") == p_hash and cached.get("status") == "ok" and cached_translation_is_usable(p, cached):
            p.title_zh = safe_text(str(cached.get("title_zh", ""))) or fallback_title_zh(p.title)
            p.summary_zh = safe_text(str(cached.get("summary_zh", ""))) or fallback_summary_zh(p.summary_en)
            continue
        targets.append(p)
    if limit > 0:
        targets = targets[:limit]
    for start in range(0, len(targets), max(1, batch_size)):
        batch = targets[start:start + max(1, batch_size)]
        texts: List[str] = []
        for p in batch:
            texts.append(p.title)
            summary_source = google_summary_source(
                p.title,
                p.summary_en,
                hint_terms=clean_tag_list(p.focus_tags) + clean_tag_list(p.domain_tags) + clean_tag_list(p.task_tags),
                full_abstract=full_abstract,
                sentences=summary_sentences,
            )
            texts.append(summary_source)
        translated = google_translate_texts(texts, timeout=timeout)
        for offset, p in enumerate(batch):
            title_zh = safe_text(translated[offset * 2]) if offset * 2 < len(translated) else ""
            summary_zh = safe_text(translated[offset * 2 + 1]) if offset * 2 + 1 < len(translated) else ""
            if not title_zh or not summary_zh:
                try:
                    title_zh = title_zh or google_translate_text(p.title, timeout=timeout)
                    summary_source = google_summary_source(
                        p.title,
                        p.summary_en,
                        hint_terms=clean_tag_list(p.focus_tags) + clean_tag_list(p.domain_tags) + clean_tag_list(p.task_tags),
                        full_abstract=full_abstract,
                        sentences=summary_sentences,
                    )
                    summary_zh = summary_zh or google_translate_text(summary_source, timeout=timeout)
                except Exception:
                    title_zh = ""
                    summary_zh = ""
            if not title_zh or not summary_zh or safe_text(title_zh) == safe_text(summary_zh):
                p.title_zh = fallback_title_zh(p.title)
                p.summary_zh = fallback_summary_zh(p.summary_en)
                continue
            p.title_zh = title_zh
            p.summary_zh = summary_zh
            if not missing_translation(p):
                cache_key = p.arxiv_id or p.link_abs
                cache[cache_key] = {
                    "status": "ok",
                    "hash": paper_content_hash(p),
                    "title_zh": p.title_zh,
                    "summary_zh": p.summary_zh,
                    "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "model": "google-translate",
                }
                changed += 1
        if changed and changed % 40 == 0:
            save_llm_cache(cache_path, cache)
        print(f"[INFO] Google Translate processed: {min(start + len(batch), len(targets))}/{len(targets)}")
        time.sleep(0.05)

    if changed:
        save_llm_cache(cache_path, cache)


def llm_enrich_title_and_summary(
    papers: List[Paper],
    model: str,
    api_base: str,
    cache: Dict[str, dict],
    cache_path: str,
    llm_limit: int = 120,
    max_retries: int = 4,
    failed_retry_cooldown_hours: int = 24,
    request_timeout: int = 25,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip() or os.environ.get("KIMI_API_KEY", "").strip()
    if not api_key:
        for p in papers:
            p.title_zh = fallback_title_zh(p.title)
            p.summary_zh = fallback_summary_zh(p.summary_en)
        return

    system_prompt = (
        "你是计算机视觉和人工智能论文助理。"
        "请输出JSON，字段：title_zh, summary_zh。"
        "title_zh要求简洁准确。"
        "summary_zh输出2-3句，覆盖研究问题、核心方法与主要价值。"
        "禁止虚构实验结果。"
    )

    changed = 0
    skipped_recent_failures = 0
    for idx, p in enumerate(papers, start=1):
        p_hash = paper_content_hash(p)
        cache_key = p.arxiv_id or p.link_abs
        cached = cache.get(cache_key, {})
        if cached.get("hash") == p_hash and cached.get("status") == "ok" and cached_translation_is_usable(p, cached):
            p.title_zh = safe_text(str(cached.get("title_zh", ""))) or fallback_title_zh(p.title)
            p.summary_zh = safe_text(str(cached.get("summary_zh", ""))) or fallback_summary_zh(p.summary_en)
            continue

        if cached.get("hash") == p_hash and cached.get("status") == "failed":
            updated_at = str(cached.get("updated_at", ""))
            retry_allowed = True
            if updated_at:
                try:
                    last_dt = dt.datetime.fromisoformat(updated_at)
                    now_dt = dt.datetime.now(last_dt.tzinfo) if last_dt.tzinfo else dt.datetime.now()
                    retry_allowed = (now_dt - last_dt) >= dt.timedelta(hours=failed_retry_cooldown_hours)
                except Exception:
                    retry_allowed = True
            if not retry_allowed:
                p.title_zh = fallback_title_zh(p.title)
                p.summary_zh = fallback_summary_zh(p.summary_en)
                skipped_recent_failures += 1
                continue

        if llm_limit == 0:
            p.title_zh = fallback_title_zh(p.title)
            p.summary_zh = fallback_summary_zh(p.summary_en)
            continue

        if llm_limit > 0 and idx > llm_limit:
            p.title_zh = fallback_title_zh(p.title)
            p.summary_zh = fallback_summary_zh(p.summary_en)
            continue

        user_prompt = f"标题: {p.title}\n摘要: {safe_text(p.summary_en)}\ncomment: {safe_text(p.comment)}\n"
        data = None
        err_msg = ""
        for attempt in range(1, max_retries + 1):
            data = call_openai_json(model, api_key, api_base, system_prompt, user_prompt, timeout=request_timeout)
            if data:
                break
            err_msg = f"empty_response_attempt_{attempt}"
            time.sleep(min(3.0, 0.8 * attempt))

        if data:
            p.title_zh = safe_text(str(data.get("title_zh", ""))) or fallback_title_zh(p.title)
            p.summary_zh = safe_text(str(data.get("summary_zh", ""))) or fallback_summary_zh(p.summary_en)
            cache[cache_key] = {
                "status": "ok",
                "hash": p_hash,
                "title_zh": p.title_zh,
                "summary_zh": p.summary_zh,
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "model": model,
            }
        else:
            p.title_zh = fallback_title_zh(p.title)
            p.summary_zh = fallback_summary_zh(p.summary_en)
            cache[cache_key] = {
                "status": "failed",
                "hash": p_hash,
                "error": err_msg or "unknown",
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "model": model,
            }
        changed += 1

        if changed == 1 or changed % 10 == 0:
            upper = min(len(papers), llm_limit) if llm_limit > 0 else len(papers)
            print(f"[INFO] LLM processed: {idx}/{upper}")
        if changed % 5 == 0:
            save_llm_cache(cache_path, cache)
        if idx % 8 == 0:
            time.sleep(0.2)

    if changed:
        save_llm_cache(cache_path, cache)
    if skipped_recent_failures:
        print(f"[INFO] LLM skipped recent failed cache entries: {skipped_recent_failures}")


def llm_fill_missing_translations_batched(
    papers: List[Paper],
    model: str,
    api_base: str,
    cache: Dict[str, dict],
    cache_path: str,
    batch_size: int = 12,
    max_retries: int = 3,
    request_timeout: int = 35,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip() or os.environ.get("KIMI_API_KEY", "").strip()
    if not api_key:
        return

    targets = [p for p in papers if missing_translation(p)]
    if not targets:
        return

    system_prompt = (
        "你是计算机视觉和人工智能论文助理。"
        "请将输入的每篇论文全部翻译为中文，输出JSON，字段为 items。"
        "items中的每个元素必须包含：id, title_zh, summary_zh。"
        "不得遗漏任何输入id。title_zh准确自然。summary_zh使用2-3句中文概述问题、方法和价值。"
    )

    changed = 0
    total_batches = (len(targets) + batch_size - 1) // batch_size
    for batch_idx, start in enumerate(range(0, len(targets), batch_size), start=1):
        batch = targets[start:start + batch_size]
        unresolved = batch[:]
        for attempt in range(1, max_retries + 1):
            payload_items = [
                {
                    "id": p.arxiv_id or p.link_abs,
                    "title": p.title,
                    "abstract": safe_text(p.summary_en),
                    "comment": safe_text(p.comment),
                }
                for p in unresolved
            ]
            user_prompt = "请逐条翻译以下论文，返回完整JSON：\n" + json.dumps(payload_items, ensure_ascii=False)
            data = call_openai_json(
                model,
                api_key,
                api_base,
                system_prompt,
                user_prompt,
                timeout=request_timeout,
                max_output_tokens=max(400, 180 * len(unresolved)),
            )
            translated: Dict[str, dict] = {}
            if isinstance(data, dict):
                items = data.get("items", [])
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        item_id = str(item.get("id", "")).strip()
                        title_zh = safe_text(str(item.get("title_zh", "")))
                        summary_zh = safe_text(str(item.get("summary_zh", "")))
                        if item_id and title_zh and summary_zh:
                            translated[item_id] = {"title_zh": title_zh, "summary_zh": summary_zh}
            next_unresolved: List[Paper] = []
            for p in unresolved:
                cache_key = p.arxiv_id or p.link_abs
                payload = translated.get(cache_key)
                if not payload:
                    next_unresolved.append(p)
                    continue
                p.title_zh = payload["title_zh"]
                p.summary_zh = payload["summary_zh"]
                cache[cache_key] = {
                    "status": "ok",
                    "hash": paper_content_hash(p),
                    "title_zh": p.title_zh,
                    "summary_zh": p.summary_zh,
                    "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "model": model,
                }
                changed += 1
            if not next_unresolved:
                break
            unresolved = next_unresolved
            time.sleep(min(2.0, 0.6 * attempt))

        print(f"[INFO] LLM batch fill: {batch_idx}/{total_batches}")
        if changed and changed % 12 == 0:
            save_llm_cache(cache_path, cache)

    if changed:
        save_llm_cache(cache_path, cache)


def fetch_focus_pool(categories: List[str], focus_terms: List[str], latest_n: int, hot_n: int, page_size: int = 100) -> Tuple[List[Paper], List[Paper]]:
    query = build_focus_query(categories, focus_terms)

    def run_fetch(target_n: int, sort_by: str) -> List[Paper]:
        got: List[Paper] = []
        start = 0
        while len(got) < target_n:
            chunk = min(page_size, target_n - len(got))
            xml_text = fetch_page(query, start=start, max_results=chunk, sort_by=sort_by, sort_order="descending")
            page = parse_feed(xml_text, major_area="Focus")
            if not page:
                break
            got.extend(page)
            start += chunk
            time.sleep(0.45)
        return dedupe_papers(got)[:target_n]

    latest = run_fetch(latest_n, sort_by="submittedDate")
    hot = run_fetch(hot_n, sort_by="relevance")
    return latest, hot


def fetch_venue_pool(categories: List[str], venues: List[str], latest_n: int, page_size: int = 100) -> List[Paper]:
    cats = " OR ".join([f"cat:{c}" for c in categories])
    venue_query = " OR ".join([f"all:{v}" for v in venues])
    query = f"({cats}) AND ({venue_query})"

    got: List[Paper] = []
    start = 0
    while len(got) < latest_n:
        chunk = min(page_size, latest_n - len(got))
        xml_text = fetch_page(query, start=start, max_results=chunk, sort_by="submittedDate", sort_order="descending")
        page = parse_feed(xml_text, major_area="Venue")
        if not page:
            break
        got.extend(page)
        start += chunk
        time.sleep(0.4)

    return dedupe_papers(got)[:latest_n]


def focus_score(p: Paper) -> int:
    blob = f"{p.title} {p.summary_en} {p.comment} {' '.join(p.categories)}".lower()
    score = 0
    for pat in ACTIVE_FOCUS_MATCHERS:
        if re.search(pat, blob):
            score += 1
    if rule_term_matches(blob, "tracking") or rule_term_matches(blob, "tracker"):
        score += 2
    if any(rule_term_matches(blob, term) for term in ["test-time adaptation", "test-time training", "test-time update", "domain adaptation", "domain shift"]):
        score += 1
    if rule_term_matches(blob, "prompt") or rule_term_matches(blob, "prompt tuning") or rule_term_matches(blob, "prompt learning"):
        score += 1
    return score


def derive_focus_from_papers(papers: List[Paper], limit: int, min_score: int = 1) -> List[Paper]:
    if limit <= 0:
        return []
    hits: List[Paper] = []
    for p in papers:
        score = focus_score(p)
        if score >= min_score:
            p.major_area = p.major_area or "CV"
            hits.append(p)
    hits = dedupe_papers(hits)
    hits.sort(key=lambda x: (focus_score(x), x.published), reverse=True)
    return hits[:limit]


def split_by_major_area(papers: List[Paper]) -> Dict[str, List[Paper]]:
    groups = {"CV": [], "AI": [], "CV+AI": [], "Other": []}
    for p in papers:
        if p.major_area in groups:
            groups[p.major_area].append(p)
        else:
            groups["Other"].append(p)
    for k in groups:
        groups[k].sort(key=lambda x: x.published, reverse=True)
    return groups


def collect_venue_watch(papers: List[Paper], limit: int = 120) -> List[Paper]:
    merged: Dict[str, Paper] = {}
    for p in papers:
        if not (p.accepted_venue or any(v.lower() in (p.accepted_hint or "").lower() for v in TOP_VENUES)):
            continue
        key = p.arxiv_id or p.link_abs
        if key not in merged:
            merged[key] = p
            continue
        old = merged[key]
        if p.accepted_hint and p.accepted_hint not in old.accepted_hint:
            old.accepted_hint = safe_text(f"{old.accepted_hint}; {p.accepted_hint}")
        if p.accepted_venue and p.accepted_venue not in old.accepted_venue:
            old.accepted_venue = safe_text(f"{old.accepted_venue}; {p.accepted_venue}")
    hits = list(merged.values())
    hits.sort(key=lambda x: x.published, reverse=True)
    return hits[:limit]


SIGNAL_STOPWORDS = {
    "ACCEPTED", "ACCEPT", "BY", "TO", "AT", "IN", "FOR", "OF", "THE", "A", "AN", "AND", "OR",
    "PAPER", "PAPERS", "PREPRINT", "VERSION", "CAMERA", "READY", "WORKSHOP", "JOURNAL", "PROCEEDINGS",
    "PAGES", "PAGE", "FIGURES", "FIGURE", "REVISED", "SUBMITTED", "ARXIV", "WITH", "ON", "AS", "IS",
    "PUBLICATION", "PUBLICATIONS", "PUBLISHED", "PRESENTED", "PRESENTATION", "CONFERENCE", "PROCEEDING",
    "APPEAR", "APPEARS", "APPEARING", "TRACK", "MAIN", "SPOTLIGHT", "ORAL", "SYMPOSIUM",
    "COMPUTER", "INFORMATION",
}


def normalize_signal_token(token: str) -> str:
    u = token.upper().strip(".")
    u = re.sub(r"\d{2,4}$", "", u)
    u = u.replace("/", "").replace("&", "")
    if not u or u in SIGNAL_STOPWORDS or len(u) < 3:
        return ""
    return u


def extract_signal_group(p: Paper) -> str:
    if p.accepted_venue:
        normalized = normalize_venue_name(p.accepted_venue)
        if normalized and normalized not in {"IEEE", "ACM"}:
            return normalized
    sources = [p.accepted_venue, p.accepted_hint, p.comment, p.journal_ref]
    merged_text = safe_text(" ".join(sources)).lower()
    for pattern, label in SIGNAL_ALIAS_PATTERNS:
        if re.search(pattern, merged_text):
            return label
    strong_candidates: List[str] = []
    weak_candidates: List[str] = []
    for source in sources:
        tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9./&-]{2,}\b", safe_text(source))
        for token in tokens:
            norm = normalize_signal_token(token)
            if not norm:
                continue
            if re.fullmatch(r"[A-Z]{3,12}", norm):
                strong_candidates.append(norm)
            else:
                weak_candidates.append(norm)
    if strong_candidates:
        return strong_candidates[0]
    if weak_candidates:
        return weak_candidates[0]
    return "OTHER"


def accepted_rank(p: Paper) -> int:
    text = f"{p.accepted_venue} {p.accepted_hint} {p.comment} {p.journal_ref}".lower()
    if "accepted" in text or "accept" in text:
        return 3
    if "to appear" in text or "published" in text:
        return 2
    if p.accepted_venue:
        return 1
    return 0


def render_signal_table(venue_watch: List[Paper], max_rows_per_group: int = 40) -> str:
    groups: Dict[str, List[Paper]] = {}
    for p in venue_watch:
        key = extract_signal_group(p)
        groups.setdefault(key, []).append(p)

    # Group ordering: larger groups first, OTHER last.
    ordered_keys = sorted([k for k in groups.keys() if k != "OTHER"], key=lambda k: len(groups[k]), reverse=True)
    if "OTHER" in groups:
        ordered_keys.append("OTHER")

    out: List[str] = []
    out.append("<section id='signal-table'><h2>中稿线索总表（动态分组）</h2>")
    out.append("<p class='subtitle'>分组按线索关键词自动生成；每组内 Accepted 优先。</p>")
    out.append("<div class='signal-wrap'>")
    for g in ordered_keys:
        rows_all = groups[g]
        rows_all.sort(key=lambda p: (accepted_rank(p), p.published), reverse=True)
        rows = rows_all[:max_rows_per_group]
        out.append(f"<h3>{html.escape(g)} ({len(rows_all)})</h3>")
        out.append("<table class='signal-table'>")
        out.append("<thead><tr><th>#</th><th>论文</th><th>中文摘要</th><th>中稿线索</th><th>状态</th><th>链接</th></tr></thead><tbody>")
        for idx, p in enumerate(rows, start=1):
            hint = p.accepted_hint or p.comment or p.journal_ref or p.accepted_venue
            status = "Accepted" if accepted_rank(p) >= 3 else "Mentioned"
            title_block = html.escape(p.title)
            if p.title_zh:
                title_block += f"<div class='signal-title-zh'>{html.escape(p.title_zh)}</div>"
            out.append("<tr>")
            out.append(f"<td>{idx}</td>")
            out.append(f"<td>{title_block}</td>")
            out.append(f"<td>{html.escape(safe_text(p.summary_zh)[:220])}</td>")
            out.append(f"<td>{html.escape(safe_text(hint)[:160])}</td>")
            out.append(f"<td>{status}</td>")
            out.append(
                f"<td><a href='{html.escape(p.link_abs)}' target='_blank'>arXiv</a> | "
                f"<a href='{html.escape(p.link_pdf)}' target='_blank'>PDF</a></td>"
            )
            out.append("</tr>")
        out.append("</tbody></table>")
    out.append("</div></section>")
    return "\n".join(out)


GRAPH_GENERIC_KEYWORDS = {
    "adaptation", "application", "benchmark", "classification", "dataset", "datasets", "detection",
    "forecasting", "generation", "localization", "medical", "planning", "reasoning", "retrieval",
    "robotics", "segmentation", "system", "test time", "theory", "understanding", "multimodal", "tracking",
    "machine learning", "natural language processing",
}


def is_graph_theme_candidate(keyword: str) -> bool:
    norm = normalize_keyword_phrase(keyword)
    if not norm or norm == "other":
        return False
    compact = norm.replace(" ", "").replace("-", "").replace("/", "")
    if compact.upper() in CANONICAL_SIGNAL_LABELS:
        return True
    if norm in ACTIVE_FOCUS_TERMS:
        return True
    if norm in GRAPH_GENERIC_KEYWORDS:
        return False
    if "-" in norm or "/" in norm:
        return True
    if " " in norm:
        return True
    if re.fullmatch(r"[A-Z0-9-]{2,12}", keyword):
        return True
    return len(norm) >= 7


def build_knowledge_graph_data(
    papers: List[Paper],
    max_keyword_nodes: int = 42,
    max_related_keywords: int = 6,
) -> Dict[str, object]:
    unique_papers = dedupe_papers(papers)
    for p in unique_papers:
        if not p.keywords:
            refresh_paper_derived_fields(p)

    keyword_meta: Dict[str, Dict[str, object]] = {}
    paper_keywords: Dict[str, List[str]] = {}
    paper_theme_scores: Dict[str, Dict[str, int]] = {}
    paper_lookup: Dict[str, Paper] = {}
    cooccurrence: Dict[Tuple[str, str], int] = {}

    def ensure_keyword_meta(keyword: str) -> Dict[str, object]:
        return keyword_meta.setdefault(
            keyword,
            {
                "paper_ids": set(),
                "focus_ids": set(),
                "accepted_ids": set(),
                "area_counts": {},
                "latest_published": "",
            },
        )

    for p in unique_papers:
        pid = p.arxiv_id or p.link_abs
        paper_lookup[pid] = p
        display_keys = [k for k in dict.fromkeys(p.keywords or []) if k and k != "other"][:6]
        if not display_keys:
            fallback = clean_tag_list(p.domain_tags) + clean_tag_list(p.task_tags) + clean_tag_list(p.type_tags)
            display_keys = [normalize_keyword_phrase(x) for x in fallback if x and x != "other"][:4]
            display_keys = [x for x in display_keys if x]

        theme_scores = collect_graph_theme_scores(p)
        theme_keys = list(theme_scores.keys())
        if not theme_keys:
            theme_keys = display_keys[:]
        if not theme_keys:
            theme_keys = [x for x in display_keys if x][:4]
        paper_keywords[pid] = theme_keys
        paper_theme_scores[pid] = theme_scores

        focus_tags = [tag for tag in clean_tag_list(p.focus_tags) if tag != "other"]
        is_focus = bool(focus_tags) or focus_score(p) > 0
        is_signal = accepted_rank(p) > 0
        for kw in set(theme_keys):
            meta = ensure_keyword_meta(kw)
            meta["paper_ids"].add(pid)
            if is_focus:
                meta["focus_ids"].add(pid)
            if is_signal:
                meta["accepted_ids"].add(pid)
            area = p.major_area or "Other"
            area_counts = meta["area_counts"]
            area_counts[area] = area_counts.get(area, 0) + 1
            latest_published = safe_text(meta["latest_published"])
            if p.published and p.published > latest_published:
                meta["latest_published"] = p.published

        deduped_keys = sorted(set(theme_keys[:8]))
        for i, left in enumerate(deduped_keys):
            for right in deduped_keys[i + 1:]:
                pair_key = tuple(sorted((left, right)))
                cooccurrence[pair_key] = cooccurrence.get(pair_key, 0) + 1

    def theme_score(keyword: str) -> float:
        meta = keyword_meta[keyword]
        paper_count = len(meta["paper_ids"])
        focus_count = len(meta["focus_ids"])
        accepted_count = len(meta["accepted_ids"])
        relation_strength = sum(weight for (left, right), weight in cooccurrence.items() if keyword in (left, right))
        specificity_bonus = 4.0 if (" " in keyword or "-" in keyword or keyword in ACTIVE_FOCUS_TERMS) else 0.0
        venue_penalty = 16.0 if keyword.upper() in CANONICAL_SIGNAL_LABELS else 0.0
        return (
            (paper_count * 5.0)
            + (focus_count * 3.5)
            + (accepted_count * 2.5)
            + relation_strength
            + specificity_bonus
            - venue_penalty
        )

    ranking = sorted(
        keyword_meta.items(),
        key=lambda item: (theme_score(item[0]), len(item[1]["paper_ids"]), len(item[1]["accepted_ids"]), len(item[1]["focus_ids"]), len(item[0])),
        reverse=True,
    )
    preferred_keywords = [keyword for keyword, meta in ranking if len(meta["paper_ids"]) >= 2 and is_graph_theme_candidate(keyword)]
    fallback_keywords = [keyword for keyword, meta in ranking if len(meta["paper_ids"]) >= 2 and keyword not in preferred_keywords]

    ordered_keywords: List[str] = []
    covered_papers: set[str] = set()

    def select_keyword(keyword: str) -> None:
        if keyword in ordered_keywords:
            return
        ordered_keywords.append(keyword)
        covered_papers.update(keyword_meta[keyword]["paper_ids"])

    focus_seed_keywords = [
        keyword for keyword in preferred_keywords
        if keyword in ACTIVE_FOCUS_TERMS and len(keyword_meta[keyword]["paper_ids"]) >= 2
    ]
    for keyword in focus_seed_keywords[:max_keyword_nodes]:
        select_keyword(keyword)

    seed_count = min(18, max(0, max_keyword_nodes - len(ordered_keywords)), len(preferred_keywords))
    for keyword in preferred_keywords[:seed_count]:
        select_keyword(keyword)

    coverage_target = 0.92
    def greedy_candidates(pool: List[str]) -> None:
        while len(ordered_keywords) < max_keyword_nodes:
            best_keyword = ""
            best_gain = -1
            best_score = -1.0
            for keyword in pool:
                if keyword in ordered_keywords:
                    continue
                gain = len(set(keyword_meta[keyword]["paper_ids"]) - covered_papers)
                score = theme_score(keyword)
                if gain > best_gain or (gain == best_gain and score > best_score):
                    best_keyword = keyword
                    best_gain = gain
                    best_score = score
            if not best_keyword:
                break
            if best_gain <= 0 and (len(covered_papers) / max(1, len(unique_papers))) >= coverage_target:
                break
            select_keyword(best_keyword)
            if (len(covered_papers) / max(1, len(unique_papers))) >= coverage_target and len(ordered_keywords) >= min(26, max_keyword_nodes):
                break

    greedy_candidates(preferred_keywords)
    greedy_candidates(fallback_keywords)

    selected_set = set(ordered_keywords)
    relation_lookup: Dict[Tuple[str, str], Dict[str, float]] = {}
    for (left, right), shared_count in cooccurrence.items():
        if left not in selected_set or right not in selected_set:
            continue
        left_count = len(keyword_meta[left]["paper_ids"])
        right_count = len(keyword_meta[right]["paper_ids"])
        union_count = max(1, left_count + right_count - shared_count)
        jaccard = shared_count / union_count
        relation_lookup[(left, right)] = {
            "shared": shared_count,
            "jaccard": round(jaccard, 3),
            "strength": round((shared_count * 3.0) + (jaccard * 10.0), 3),
        }

    adjacency_rows: Dict[str, List[Dict[str, object]]] = {keyword: [] for keyword in ordered_keywords}
    for (left, right), relation in relation_lookup.items():
        adjacency_rows[left].append(
            {
                "id": f"kw:{right}",
                "label": format_keyword_label(right),
                "shared": relation["shared"],
                "jaccard": relation["jaccard"],
                "strength": relation["strength"],
                "paper_count": len(keyword_meta[right]["paper_ids"]),
                "focus_count": len(keyword_meta[right]["focus_ids"]),
                "accepted_count": len(keyword_meta[right]["accepted_ids"]),
            }
        )
        adjacency_rows[right].append(
            {
                "id": f"kw:{left}",
                "label": format_keyword_label(left),
                "shared": relation["shared"],
                "jaccard": relation["jaccard"],
                "strength": relation["strength"],
                "paper_count": len(keyword_meta[left]["paper_ids"]),
                "focus_count": len(keyword_meta[left]["focus_ids"]),
                "accepted_count": len(keyword_meta[left]["accepted_ids"]),
            }
        )
    for keyword, rows in adjacency_rows.items():
        rows.sort(
            key=lambda item: (
                item["shared"],
                item["strength"],
                item["accepted_count"],
                item["focus_count"],
                item["paper_count"],
            ),
            reverse=True,
        )

    def relation_rows(keyword: str, limit: Optional[int] = None) -> List[Dict[str, object]]:
        rows = adjacency_rows.get(keyword, [])
        if limit is None:
            return [dict(row) for row in rows]
        return [dict(row) for row in rows[:limit]]

    def paper_priority(p: Paper) -> Tuple[int, int, str]:
        return (accepted_rank(p), focus_score(p), p.published)

    themes: List[Dict[str, object]] = []
    for keyword in ordered_keywords:
        meta = keyword_meta[keyword]
        paper_ids = list(meta["paper_ids"])
        related_papers = sorted(
            [paper_lookup[pid] for pid in paper_ids if pid in paper_lookup],
            key=lambda paper: (
                paper_theme_scores.get(paper.arxiv_id or paper.link_abs, {}).get(keyword, 0),
                accepted_rank(paper),
                focus_score(paper),
                paper.published,
            ),
            reverse=True,
        )
        focus_papers = [p for p in related_papers if focus_score(p) > 0][:4]
        accepted_papers = [p for p in related_papers if accepted_rank(p) > 0][:4]
        area_counts = meta["area_counts"]
        sorted_areas = [k for k, _v in sorted(area_counts.items(), key=lambda item: item[1], reverse=True) if k]
        focus_count = len(meta["focus_ids"])
        accepted_count = len(meta["accepted_ids"])
        paper_count = len(meta["paper_ids"])
        if keyword in ACTIVE_FOCUS_TERMS or focus_count >= max(4, (paper_count + 1) // 2):
            theme_kind = "focus"
        elif accepted_count >= max(2, paper_count // 4):
            theme_kind = "signal"
        else:
            theme_kind = "general"

        themes.append(
            {
                "id": f"kw:{keyword}",
                "raw_keyword": keyword,
                "label": format_keyword_label(keyword),
                "is_venue_theme": keyword.upper() in CANONICAL_SIGNAL_LABELS,
                "is_generic_theme": keyword in GRAPH_GENERIC_KEYWORDS,
                "paper_count": paper_count,
                "focus_count": focus_count,
                "accepted_count": accepted_count,
                "theme_kind": theme_kind,
                "latest_published": safe_text(meta["latest_published"]),
                "areas": sorted_areas[:3],
                "score": round(theme_score(keyword), 2),
                "share_pct": round((paper_count / max(1, len(unique_papers))) * 100.0, 1),
                "related_count": len(adjacency_rows.get(keyword, [])),
                "related_keywords": relation_rows(keyword, max_related_keywords),
                "graph_neighbors": relation_rows(keyword, 18),
                "papers": [
                    {
                        "id": p.arxiv_id or p.link_abs,
                        "title": p.title,
                        "title_zh": p.title_zh,
                        "summary_zh": p.summary_zh,
                        "url": p.link_abs,
                        "accepted_venue": p.accepted_venue,
                        "published": p.published,
                        "focus": focus_score(p) > 0,
                        "match_score": paper_theme_scores.get(p.arxiv_id or p.link_abs, {}).get(keyword, 0),
                    }
                    for p in related_papers
                ],
                "accepted_papers": [
                    {
                        "id": p.arxiv_id or p.link_abs,
                        "title": p.title,
                        "title_zh": p.title_zh,
                        "url": p.link_abs,
                        "accepted_venue": p.accepted_venue,
                    }
                    for p in [p for p in related_papers if accepted_rank(p) > 0]
                ],
                "focus_papers": [
                    {
                        "id": p.arxiv_id or p.link_abs,
                        "title": p.title,
                        "title_zh": p.title_zh,
                        "url": p.link_abs,
                    }
                    for p in [p for p in related_papers if focus_score(p) > 0]
                ],
            }
        )

    themes.sort(
        key=lambda item: (
            1 if is_graph_theme_candidate(item["raw_keyword"]) else 0,
            1 if not item.get("is_venue_theme") else 0,
            item["score"],
            item["paper_count"],
            item["accepted_count"],
            item["focus_count"],
        ),
        reverse=True,
    )

    spotlight_ids = [
        theme["id"]
        for theme in themes
        if is_graph_theme_candidate(theme["raw_keyword"])
        and theme["raw_keyword"].upper() not in CANONICAL_SIGNAL_LABELS
        and theme["raw_keyword"] != "__all_papers__"
    ][:8]
    if len(spotlight_ids) < min(8, len(themes)):
        spotlight_ids.extend([theme["id"] for theme in themes if theme["id"] not in spotlight_ids][:max(0, min(8, len(themes)) - len(spotlight_ids))])
    focus_ids = [theme["id"] for theme in themes if theme["focus_count"] > 0][:8]
    signal_ids = [theme["id"] for theme in themes if theme["accepted_count"] > 0][:8]

    paper_search = []
    for p in unique_papers:
        pid = p.arxiv_id or p.link_abs
        keys = [k for k in paper_keywords.get(pid, []) if k in selected_set]
        primary = keys[0] if keys else "__all_papers__"
        paper_search.append(
            {
                "id": pid,
                "title": p.title,
                "title_zh": p.title_zh,
                "url": p.link_abs,
                "theme_id": f"kw:{primary}",
                "theme_label": "全部论文" if primary == "__all_papers__" else format_keyword_label(primary),
                "accepted_venue": p.accepted_venue,
                "keywords": [format_keyword_label(k) for k in (keys[:4] if keys else paper_keywords.get(pid, [])[:4])],
            }
        )

    relation_rows = [
        {
            "source": f"kw:{left}",
            "target": f"kw:{right}",
            "shared": relation["shared"],
            "jaccard": relation["jaccard"],
            "strength": relation["strength"],
        }
        for (left, right), relation in sorted(
            relation_lookup.items(),
            key=lambda item: (item[1]["shared"], item[1]["jaccard"]),
            reverse=True,
        )
    ]

    uncovered_papers = [p for p in unique_papers if (p.arxiv_id or p.link_abs) not in covered_papers]
    if uncovered_papers:
        themes.append(
            {
                "id": "kw:__all_papers__",
                "raw_keyword": "__all_papers__",
                "label": "全部论文",
                "is_venue_theme": False,
                "is_generic_theme": False,
                "paper_count": len(unique_papers),
                "focus_count": sum(1 for p in unique_papers if focus_score(p) > 0),
                "accepted_count": sum(1 for p in unique_papers if accepted_rank(p) > 0),
                "theme_kind": "general",
                "latest_published": max((safe_text(p.published) for p in unique_papers), default=""),
                "areas": sorted({p.major_area for p in unique_papers if p.major_area})[:3],
                "score": 0.0,
                "share_pct": 100.0,
                "related_keywords": [],
                "papers": [
                    {
                        "id": p.arxiv_id or p.link_abs,
                        "title": p.title,
                        "title_zh": p.title_zh,
                        "summary_zh": p.summary_zh,
                        "url": p.link_abs,
                        "accepted_venue": p.accepted_venue,
                        "published": p.published,
                        "focus": focus_score(p) > 0,
                    }
                    for p in unique_papers
                ],
                "accepted_papers": [
                    {
                        "id": p.arxiv_id or p.link_abs,
                        "title": p.title,
                        "title_zh": p.title_zh,
                        "url": p.link_abs,
                        "accepted_venue": p.accepted_venue,
                    }
                    for p in unique_papers if accepted_rank(p) > 0
                ],
                "focus_papers": [
                    {
                        "id": p.arxiv_id or p.link_abs,
                        "title": p.title,
                        "title_zh": p.title_zh,
                        "url": p.link_abs,
                    }
                    for p in unique_papers if focus_score(p) > 0
                ],
            }
        )
        selected_set.add("__all_papers__")

    return {
        "stats": {
            "paper_count": len(unique_papers),
            "theme_count": len(themes),
            "relation_count": len(relation_rows),
        },
        "themes": themes,
        "relations": relation_rows,
        "spotlight_ids": spotlight_ids,
        "focus_ids": focus_ids,
        "signal_ids": signal_ids,
        "paper_search": paper_search,
    }


def render_knowledge_graph_section(papers: List[Paper]) -> str:
    graph_data = build_knowledge_graph_data(papers)
    payload = json.dumps(graph_data, ensure_ascii=False).replace("</", "<\/")
    html_block = """
<section id='knowledge-graph' class='graph-section'>
  <div class='graph-hero'>
    <div>
      <p class='graph-kicker'>Knowledge Graph</p>
      <h2>论文主题探索器</h2>
      <p class='subtitle'>参考 Karpathy 式 arXiv 探索思路，把图谱从“全局一张大网”改成“先检索主题，再看局部关系和全部论文”的探索器。默认视图先把当前主题和一级关联主题铺满画布，二级延伸主题放在更外圈，只有缩小或拖拽时才逐步进入视野。</p>
    </div>
    <div class='graph-stat-grid'>
      <div><strong>__PAPER_COUNT__</strong><span>覆盖论文</span></div>
      <div><strong>__THEME_COUNT__</strong><span>可探索主题</span></div>
      <div><strong>__RELATION_COUNT__</strong><span>主题关系</span></div>
    </div>
  </div>
  <div class='graph-toolbar-shell'>
    <div class='graph-toolbar'>
      <input id='kg-search' type='search' placeholder='搜索主题或论文标题，例如 tracking / diffusion / domain shift' />
      <div class='graph-toolbar-actions'>
        <button id='kg-sort-default' type='button' class='kg-toolbar-btn is-active'>综合排序</button>
        <button id='kg-sort-focus' type='button' class='kg-toolbar-btn'>Focus优先</button>
        <button id='kg-sort-signal' type='button' class='kg-toolbar-btn'>中稿优先</button>
        <button id='kg-reset' type='button' class='kg-toolbar-btn'>重置</button>
      </div>
    </div>
    <div class='graph-spotlight-row'>
      <span class='graph-spotlight-label'>快速进入</span>
      <div id='kg-spotlights' class='kg-spotlights'></div>
    </div>
  </div>
  <div class='graph-layout'>
    <aside class='kg-sidebar'>
      <div class='kg-sidebar-head'>
        <h3>主题索引</h3>
        <p>按覆盖论文、Focus 命中和中稿线索综合排序。先从这里选主题，再到中间看关系网。</p>
      </div>
      <div id='kg-search-results' class='kg-search-results' hidden></div>
      <div id='kg-theme-list' class='kg-theme-list'></div>
    </aside>
    <div class='graph-canvas-wrap'>
      <div class='graph-legend'>
        <span><i class='legend-dot keyword'></i> 当前主题 / 一级主题</span>
        <span><i class='legend-dot secondary'></i> 二级延伸主题</span>
        <span><i class='legend-line'></i> 主题共现强度</span>
        <span class='graph-legend-note'>默认展示主节点、一级节点与二级延伸节点</span>
      </div>
      <div class='kg-canvas-hud'>
        <div class='kg-view-controls'>
          <button id='kg-zoom-out' type='button' class='kg-view-btn' aria-label='缩小'>-</button>
          <button id='kg-zoom-in' type='button' class='kg-view-btn' aria-label='放大'>+</button>
          <button id='kg-center-view' type='button' class='kg-view-btn is-wide'>居中</button>
        </div>
        <div id='kg-zoom-level' class='kg-zoom-level'>100%</div>
      </div>
      <svg id='kg-svg' viewBox='0 0 1200 760' preserveAspectRatio='xMidYMid meet' aria-label='论文关系知识图谱'></svg>
      <p class='graph-canvas-note'>滚轮缩放，拖动画布，双击空白处回正视图，双击节点居中并聚焦；论文跳转请使用右侧面板和搜索结果里的链接。</p>
      <div id='kg-tooltip' class='kg-tooltip' hidden></div>
    </div>
    <aside id='kg-panel' class='kg-panel'>
      <h3>交互说明</h3>
      <p>1. 先在左侧主题索引或搜索框里定位主题。</p>
      <p>2. 中间默认展开一级关系和二级延伸关系，帮助你同时看核心主题与外延方向。</p>
      <p>3. 右侧同时展示该主题下的全部论文、Focus 论文和中稿线索论文。</p>
      <p class='graph-panel-note'>右侧内容只在点击时变化，因此不会因为鼠标移动而让页面抖动。</p>
    </aside>
  </div>
  <script>
  (function() {
    const data = __PAYLOAD__;
    const svg = document.getElementById('kg-svg');
    const tooltip = document.getElementById('kg-tooltip');
    const panel = document.getElementById('kg-panel');
    const searchInput = document.getElementById('kg-search');
    const themeList = document.getElementById('kg-theme-list');
    const searchResults = document.getElementById('kg-search-results');
    const spotlightWrap = document.getElementById('kg-spotlights');
    const resetBtn = document.getElementById('kg-reset');
    const sortDefaultBtn = document.getElementById('kg-sort-default');
    const sortFocusBtn = document.getElementById('kg-sort-focus');
    const sortSignalBtn = document.getElementById('kg-sort-signal');
    const zoomInBtn = document.getElementById('kg-zoom-in');
    const zoomOutBtn = document.getElementById('kg-zoom-out');
    const centerViewBtn = document.getElementById('kg-center-view');
    const zoomLevel = document.getElementById('kg-zoom-level');
    if (!svg || !panel || !searchInput || !themeList || !searchResults || !spotlightWrap || !resetBtn || !sortDefaultBtn || !sortFocusBtn || !sortSignalBtn || !zoomInBtn || !zoomOutBtn || !centerViewBtn || !zoomLevel) return;

    const ns = 'http://www.w3.org/2000/svg';
    const themeMap = new Map((data.themes || []).map(theme => [theme.id, theme]));
    const relations = new Map();
    (data.relations || []).forEach(rel => {
      const key = [rel.source, rel.target].sort().join('|');
      relations.set(key, rel);
    });

    const make = (tag, attrs = {}) => {
      const el = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
      return el;
    };

    const escapeHtml = (value) => String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');

    const chipHtml = (items, cls = 'kg-chip') => (items || []).filter(Boolean).map(item => `<span class="${cls}">${escapeHtml(item)}</span>`).join('');
    const wrapLabel = (label, limit = 18) => {
      const raw = String(label || '').trim();
      if (!raw) return [''];
      if (raw.length <= limit) return [raw];
      const parts = raw.split(/[- ]/).filter(Boolean);
      if (parts.length >= 2) {
        const splitIndex = Math.ceil(parts.length / 2);
        const line1 = parts.slice(0, splitIndex).join(' ');
        const line2 = parts.slice(splitIndex).join(' ');
        return [line1.slice(0, limit), line2.slice(0, limit)];
      }
      return [raw.slice(0, limit), raw.slice(limit, limit * 2)];
    };
    const getNodeRadius = (theme, mode = 'neighbor') => {
      if (mode === 'active') return 40;
      if (mode === 'secondary') return Math.max(16, Math.min(22, 10 + Math.log(Math.max(2, theme.paper_count || 2)) * 3.0));
      return Math.max(22, Math.min(32, 14 + Math.log(Math.max(2, theme.paper_count || 2)) * 5.4));
    };
    const clipLineToCircles = (from, to, fromRadius, toRadius) => {
      const dx = to.x - from.x;
      const dy = to.y - from.y;
      const length = Math.max(1, Math.hypot(dx, dy));
      const ux = dx / length;
      const uy = dy / length;
      return {
        x1: from.x + (ux * fromRadius),
        y1: from.y + (uy * fromRadius),
        x2: to.x - (ux * toRadius),
        y2: to.y - (uy * toRadius),
      };
    };

    const metricHtml = (theme) => `
      <div class="kg-metric-grid">
        <div><strong>${theme.paper_count || 0}</strong><span>论文</span></div>
        <div><strong>${theme.focus_count || 0}</strong><span>Focus</span></div>
        <div><strong>${theme.accepted_count || 0}</strong><span>中稿线索</span></div>
        <div><strong>${theme.related_count || (theme.related_keywords ? theme.related_keywords.length : 0)}</strong><span>强相关主题</span></div>
      </div>
    `;

    const paperRowHtml = (item, highlightId = '') => {
      const title = item.title_zh || item.title || item.id;
      const venue = item.accepted_venue ? `<span class="kg-paper-venue">${escapeHtml(item.accepted_venue)}</span>` : '';
      const focusBadge = item.focus ? '<span class="kg-paper-focus">Focus</span>' : '';
      const summary = item.summary_zh ? `<p>${escapeHtml((item.summary_zh || '').slice(0, 140))}</p>` : '';
      const selectedClass = highlightId && highlightId === item.id ? ' is-highlight' : '';
      return `
        <li class="kg-paper-item${selectedClass}">
          <a href="${escapeHtml(item.url)}" target="_blank">${escapeHtml(title)}</a>
          <div class="kg-paper-meta">${venue}${focusBadge}</div>
          ${summary}
        </li>
      `;
    };

    const renderPanel = (theme, highlightPaperId = '') => {
      if (!theme) {
        panel.innerHTML = `
          <h3>交互说明</h3>
          <p>1. 先从左侧主题索引或搜索结果中选择一个主题。</p>
          <p>2. 中间画布默认优先展示当前主题和一级关联主题，二级延伸主题位于更外圈，需要缩小或拖拽后再看全。</p>
          <p>3. 右侧会给出该主题下的全部论文、Focus 论文和中稿线索论文。</p>
          <p class="graph-panel-note">右侧内容只在点击时变化，因此不会再因为鼠标移动而让页面抖动。</p>
        `;
        return;
      }

      const relatedKeywords = (theme.related_keywords || []).map(item =>
        `<li><button type="button" class="kg-inline-btn" data-target="${escapeHtml(item.id)}">${escapeHtml(item.label)}</button><span class="kg-related-score">共享论文 ${item.shared} · Jaccard ${Number(item.jaccard || 0).toFixed(2)}</span></li>`
      ).join('');

      const topPapers = (theme.papers || []).map(item => paperRowHtml(item, highlightPaperId)).join('');
      const acceptedPapers = (theme.accepted_papers || []).map(item => `
        <li><a href="${escapeHtml(item.url)}" target="_blank">${escapeHtml(item.title_zh || item.title || item.id)}</a>${item.accepted_venue ? `<span class="kg-paper-venue">${escapeHtml(item.accepted_venue)}</span>` : ''}</li>
      `).join('');
      const focusPapers = (theme.focus_papers || []).map(item => `
        <li><a href="${escapeHtml(item.url)}" target="_blank">${escapeHtml(item.title_zh || item.title || item.id)}</a></li>
      `).join('');
      const areaChips = chipHtml(theme.areas || [], 'kg-chip kg-chip-soft');
      const stateChips = chipHtml([
        theme.theme_kind === 'focus' ? 'Focus主题' : '',
        theme.theme_kind === 'signal' ? '中稿密集' : '',
        theme.share_pct ? `覆盖 ${theme.share_pct}%` : '',
      ].filter(Boolean), 'kg-chip');

      panel.innerHTML = `
        <h3>${escapeHtml(theme.label)}</h3>
        <p class="kg-panel-desc">当前主题覆盖 ${theme.paper_count || 0} 篇论文，其中 Focus 命中 ${theme.focus_count || 0} 篇，中稿线索 ${theme.accepted_count || 0} 篇。你可以拖动与缩放图谱继续探索，也可以用下方链接直接打开 arXiv 搜索。</p>
        <div class="kg-chip-row">${stateChips}${areaChips}</div>
        ${metricHtml(theme)}
        <p class="kg-panel-links"><a href="https://arxiv.org/search/?query=${encodeURIComponent(theme.raw_keyword || '')}&searchtype=all" target="_blank">打开 arXiv 搜索</a></p>
        <div class="kg-subblock">
          <h4>关联关键词</h4>
          <ul class="kg-related-list">${relatedKeywords || '<li>暂无更强关联。</li>'}</ul>
        </div>
        <div class="kg-subblock">
          <h4>全部论文 (${theme.paper_count || 0})</h4>
          <ul class="kg-related-list kg-paper-list">${topPapers || '<li>暂无关联论文。</li>'}</ul>
        </div>
        <div class="kg-subgrid">
          <div class="kg-subblock">
            <h4>中稿线索论文 (${theme.accepted_count || 0})</h4>
            <ul class="kg-related-list">${acceptedPapers || '<li>暂无中稿线索论文。</li>'}</ul>
          </div>
          <div class="kg-subblock">
            <h4>Focus论文 (${theme.focus_count || 0})</h4>
            <ul class="kg-related-list">${focusPapers || '<li>暂无Focus论文。</li>'}</ul>
          </div>
        </div>
      `;

      panel.querySelectorAll('.kg-inline-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const targetId = btn.getAttribute('data-target') || '';
          const target = themeMap.get(targetId);
          if (!target) return;
          selectTheme(targetId);
        });
      });
    };

    const getGraphNeighborRows = (theme, limit = 999) => {
      const rows = (theme && (theme.graph_neighbors || theme.related_keywords || [])) || [];
      if (!Number.isFinite(limit) || limit >= rows.length) return rows.slice();
      return rows.slice(0, limit);
    };

    const setTooltip = (evt, theme) => {
      tooltip.innerHTML = `<strong>${escapeHtml(theme.label)}</strong><span>${escapeHtml(`${theme.paper_count || 0} 篇论文 · Focus ${theme.focus_count || 0} · 中稿 ${theme.accepted_count || 0}`)}</span>`;
      tooltip.hidden = false;
      const rect = svg.getBoundingClientRect();
      tooltip.style.left = `${evt.clientX - rect.left + 16}px`;
      tooltip.style.top = `${evt.clientY - rect.top + 16}px`;
    };

    const clearTooltip = () => {
      tooltip.hidden = true;
    };

    let activeId = '';
    let hoverId = '';
    let searchQuery = '';
    let sortMode = 'default';
    let highlightedPaperId = '';
    let graphNodeEls = new Map();
    let graphEdgeEls = [];
    let viewportGroup = null;
    let viewState = { scale: 1, x: 0, y: 0 };
    let dragState = null;
    const VIEWBOX = { width: 1200, height: 760 };
    const DEFAULT_VIEW = { scale: 1, x: 0, y: 0 };
    const ZOOM_MIN = 0.78;
    const ZOOM_MAX = 1.9;

    const rankThemes = (themes, mode) => {
      const rows = [...themes];
      rows.sort((left, right) => {
        const leftPreferred = (left.is_generic_theme || left.is_venue_theme) ? 0 : 1;
        const rightPreferred = (right.is_generic_theme || right.is_venue_theme) ? 0 : 1;
        if (rightPreferred !== leftPreferred) return rightPreferred - leftPreferred;
        if (mode === 'focus') {
          return (right.focus_count - left.focus_count) || (right.paper_count - left.paper_count) || (right.score - left.score);
        }
        if (mode === 'signal') {
          return (right.accepted_count - left.accepted_count) || (right.paper_count - left.paper_count) || (right.score - left.score);
        }
        return (right.score - left.score) || (right.paper_count - left.paper_count) || (right.accepted_count - left.accepted_count);
      });
      return rows;
    };

    const themeBadgeList = (theme) => {
      const badges = [
        `${theme.paper_count || 0}篇`,
        theme.focus_count ? `Focus ${theme.focus_count}` : '',
        theme.accepted_count ? `中稿 ${theme.accepted_count}` : '',
      ].filter(Boolean);
      return chipHtml(badges, 'kg-chip kg-chip-soft');
    };

    const renderThemeList = () => {
      const query = searchQuery.trim().toLowerCase();
      const ranked = rankThemes(data.themes || [], sortMode);
      const visibleThemes = !query
        ? ranked
        : ranked.filter(theme =>
            String(theme.label || '').toLowerCase().includes(query)
            || String(theme.raw_keyword || '').toLowerCase().includes(query)
            || (theme.papers || []).some(item => String(item.title || '').toLowerCase().includes(query) || String(item.title_zh || '').toLowerCase().includes(query))
          );

      themeList.innerHTML = visibleThemes.map(theme => `
        <button type="button" class="kg-theme-item${theme.id === activeId ? ' is-active' : ''}" data-theme-id="${escapeHtml(theme.id)}">
          <div class="kg-theme-item-top">
            <strong>${escapeHtml(theme.label)}</strong>
            <span class="kg-theme-share">${theme.share_pct || 0}%</span>
          </div>
          <div class="kg-theme-item-badges">${themeBadgeList(theme)}</div>
        </button>
      `).join('') || '<p class="kg-empty">没有匹配到主题。</p>';

      themeList.querySelectorAll('.kg-theme-item').forEach(btn => {
        btn.addEventListener('click', () => {
          const themeId = btn.getAttribute('data-theme-id') || '';
          if (themeId) selectTheme(themeId);
        });
      });
    };

    const renderSpotlights = () => {
      const ids = (data.spotlight_ids || []).slice(0, 8);
      spotlightWrap.innerHTML = ids.map(id => {
        const theme = themeMap.get(id);
        if (!theme) return '';
        return `<button type="button" class="kg-spotlight-pill${id === activeId ? ' is-active' : ''}" data-theme-id="${escapeHtml(id)}">${escapeHtml(theme.label)}<span>${theme.paper_count || 0}</span></button>`;
      }).join('');
      spotlightWrap.querySelectorAll('.kg-spotlight-pill').forEach(btn => {
        btn.addEventListener('click', () => {
          const themeId = btn.getAttribute('data-theme-id') || '';
          if (themeId) selectTheme(themeId);
        });
      });
    };

    const renderSearchResults = () => {
      const query = searchQuery.trim().toLowerCase();
      if (!query) {
        searchResults.hidden = true;
        searchResults.innerHTML = '';
        return;
      }
      const themeMatches = rankThemes(data.themes || [], sortMode)
        .filter(theme =>
          String(theme.label || '').toLowerCase().includes(query)
          || String(theme.raw_keyword || '').toLowerCase().includes(query)
        )
        .slice(0, 6);
      const paperMatches = (data.paper_search || [])
        .filter(item =>
          String(item.title || '').toLowerCase().includes(query)
          || String(item.title_zh || '').toLowerCase().includes(query)
          || (item.keywords || []).some(keyword => String(keyword || '').toLowerCase().includes(query))
        )
        .slice(0, 8);

      searchResults.hidden = false;
      searchResults.innerHTML = `
        <div class="kg-result-block">
          <h4>主题匹配</h4>
          ${themeMatches.map(theme => `
            <button type="button" class="kg-result-row" data-kind="theme" data-target="${escapeHtml(theme.id)}">
              <strong>${escapeHtml(theme.label)}</strong>
              <span>${theme.paper_count || 0}篇 · Focus ${theme.focus_count || 0} · 中稿 ${theme.accepted_count || 0}</span>
            </button>
          `).join('') || '<p class="kg-empty">没有主题命中。</p>'}
        </div>
        <div class="kg-result-block">
          <h4>论文匹配</h4>
          ${paperMatches.map(item => `
            <div class="kg-result-row kg-result-paper">
              <button type="button" data-kind="paper" data-target="${escapeHtml(item.theme_id || '')}" data-paper-id="${escapeHtml(item.id)}">
                <strong>${escapeHtml(item.title_zh || item.title || item.id)}</strong>
                <span>${escapeHtml(item.theme_label || '')}</span>
              </button>
              <a href="${escapeHtml(item.url)}" target="_blank">arXiv</a>
            </div>
          `).join('') || '<p class="kg-empty">没有论文命中。</p>'}
        </div>
      `;

      searchResults.querySelectorAll('button[data-kind="theme"]').forEach(btn => {
        btn.addEventListener('click', () => {
          const targetId = btn.getAttribute('data-target') || '';
          if (targetId) selectTheme(targetId);
        });
      });
      searchResults.querySelectorAll('button[data-kind="paper"]').forEach(btn => {
        btn.addEventListener('click', () => {
          const targetId = btn.getAttribute('data-target') || '';
          highlightedPaperId = btn.getAttribute('data-paper-id') || '';
          if (targetId) {
            selectTheme(targetId);
          }
        });
      });
    };

    const refreshGraphHoverState = () => {
      graphNodeEls.forEach((el, themeId) => {
        el.classList.toggle('is-hovered', !!hoverId && themeId === hoverId);
        el.classList.toggle('is-soft-faded', !!hoverId && themeId !== hoverId && themeId !== activeId);
      });
      graphEdgeEls.forEach(edge => {
        const isPrimary = edge.source === activeId || edge.target === activeId;
        const isHoverHit = !!hoverId && (
          edge.source === hoverId
          || edge.target === hoverId
          || (edge.via && edge.via === hoverId)
          || (hoverId === activeId && isPrimary)
        );
        edge.el.classList.toggle('is-highlight', isHoverHit);
        edge.el.classList.toggle('is-faded', !!hoverId && !isHoverHit);
      });
    };

    const updateZoomLevel = () => {
      zoomLevel.textContent = `${Math.round(viewState.scale * 100)}%`;
    };

    const applyViewportTransform = () => {
      if (!viewportGroup) return;
      viewportGroup.setAttribute('transform', `translate(${viewState.x} ${viewState.y}) scale(${viewState.scale})`);
      updateZoomLevel();
    };

    const svgPointFromEvent = (evt) => {
      const pt = svg.createSVGPoint();
      pt.x = evt.clientX;
      pt.y = evt.clientY;
      const ctm = svg.getScreenCTM();
      return ctm ? pt.matrixTransform(ctm.inverse()) : { x: 0, y: 0 };
    };

    const setView = (nextView) => {
      viewState = {
        scale: Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, nextView.scale)),
        x: nextView.x,
        y: nextView.y,
      };
      applyViewportTransform();
    };

    const zoomAtPoint = (targetScale, centerPoint) => {
      const nextScale = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, targetScale));
      const worldX = (centerPoint.x - viewState.x) / viewState.scale;
      const worldY = (centerPoint.y - viewState.y) / viewState.scale;
      setView({
        scale: nextScale,
        x: centerPoint.x - (worldX * nextScale),
        y: centerPoint.y - (worldY * nextScale),
      });
    };

    const resetViewport = (scale = 1) => {
      setView({ ...DEFAULT_VIEW, scale });
    };

    const centerViewportOn = (point, scale = viewState.scale) => {
      const nextScale = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, scale));
      setView({
        scale: nextScale,
        x: (VIEWBOX.width / 2) - (point.x * nextScale),
        y: (VIEWBOX.height / 2) - (point.y * nextScale),
      });
    };

    const beginDrag = (evt) => {
      if (evt.button !== 0) return;
      if (evt.target.closest && evt.target.closest('.kg-node')) return;
      dragState = {
        pointerId: evt.pointerId,
        startX: evt.clientX,
        startY: evt.clientY,
        originX: viewState.x,
        originY: viewState.y,
      };
      svg.classList.add('is-dragging');
      if (svg.setPointerCapture) svg.setPointerCapture(evt.pointerId);
    };

    const updateDrag = (evt) => {
      if (!dragState || evt.pointerId !== dragState.pointerId) return;
      setView({
        scale: viewState.scale,
        x: dragState.originX + (evt.clientX - dragState.startX),
        y: dragState.originY + (evt.clientY - dragState.startY),
      });
    };

    const endDrag = (evt) => {
      if (!dragState) return;
      if (evt && evt.pointerId && dragState.pointerId !== evt.pointerId) return;
      if (svg.releasePointerCapture && dragState.pointerId != null) {
        try { svg.releasePointerCapture(dragState.pointerId); } catch (_err) {}
      }
      dragState = null;
      svg.classList.remove('is-dragging');
    };

    const drawNode = (group, theme, position, mode = 'neighbor') => {
      const g = make('g', {
        class: `kg-node kg-node-${mode}${theme.id === activeId ? ' is-selected' : ''}`,
        transform: `translate(${position.x}, ${position.y})`
      });
      const radius = getNodeRadius(theme, mode);
      const hit = make('circle', { r: radius + 12, class: 'kg-hit' });
      const halo = make('circle', { r: radius + 6, class: 'kg-halo' });
      const core = make('circle', { r: radius, class: 'kg-core' });
      const text = make('text', {
        class: `kg-label keyword-label ${mode === 'active' ? 'is-active' : (mode === 'secondary' ? 'is-secondary' : 'is-neighbor')}`,
        x: 0,
        y: radius + (mode === 'active' ? 28 : (mode === 'secondary' ? 18 : 24)),
        'text-anchor': 'middle'
      });
      wrapLabel(theme.label, mode === 'active' ? 22 : (mode === 'secondary' ? 14 : 18)).forEach((line, index) => {
        const span = make('tspan', {
          x: 0,
          dy: index === 0 ? 0 : (mode === 'active' ? 17 : (mode === 'secondary' ? 12 : 15))
        });
        span.textContent = line;
        text.appendChild(span);
      });
      [hit, halo, core, text].forEach(el => {
        el.addEventListener('mouseenter', evt => {
          hoverId = theme.id;
          setTooltip(evt, theme);
          refreshGraphHoverState();
        });
        el.addEventListener('mousemove', evt => setTooltip(evt, theme));
        el.addEventListener('mouseleave', () => {
          hoverId = '';
          clearTooltip();
          refreshGraphHoverState();
        });
        el.addEventListener('dblclick', evt => {
          evt.preventDefault();
          evt.stopPropagation();
          if (theme.id !== activeId) {
            selectTheme(theme.id);
            resetViewport(1.08);
            return;
          }
          centerViewportOn(position, Math.max(viewState.scale, mode === 'active' ? 1.14 : 1.04));
        });
        el.addEventListener('click', evt => {
          evt.preventDefault();
          evt.stopPropagation();
          if (theme.id === activeId) {
            return;
          }
          selectTheme(theme.id);
        });
      });
      g.appendChild(hit);
      g.appendChild(halo);
      g.appendChild(core);
      g.appendChild(text);
      group.appendChild(g);
      graphNodeEls.set(theme.id, g);
    };

    const drawGraph = () => {
      svg.innerHTML = '';
      graphNodeEls = new Map();
      graphEdgeEls = [];
      const theme = themeMap.get(activeId);
      if (!theme) return;

      viewportGroup = make('g', { class: 'kg-viewport' });
      const edgeGroup = make('g', { class: 'kg-edges' });
      const nodeGroup = make('g', { class: 'kg-nodes' });
      viewportGroup.appendChild(edgeGroup);
      viewportGroup.appendChild(nodeGroup);
      svg.appendChild(viewportGroup);

      const center = { x: 600, y: 368 };
      const centerRadius = getNodeRadius(theme, 'active');
      const primaryRows = getGraphNeighborRows(theme, 7)
        .map(item => ({ row: item, theme: themeMap.get(item.id) }))
        .filter(item => item.theme);
      const related = primaryRows.map(item => item.theme);
      const positions = new Map();
      positions.set(theme.id, center);
      const primaryAngles = new Map();
      const primaryOrbitX = related.length <= 4 ? 420 : 458;
      const primaryOrbitY = related.length <= 4 ? 286 : 308;
      related.forEach((neighbor, index) => {
        const angle = (-Math.PI / 2) + ((Math.PI * 2) * index / Math.max(1, related.length));
        primaryAngles.set(neighbor.id, angle);
        positions.set(neighbor.id, {
          x: center.x + (Math.cos(angle) * primaryOrbitX),
          y: center.y + (Math.sin(angle) * primaryOrbitY)
        });
      });

      const primarySet = new Set(related.map(item => item.id));
      const secondaryNodes = [];
      const secondarySeen = new Set();
      const secondaryBudget = 16;
      const perPrimaryPasses = [2, 3];
      perPrimaryPasses.forEach(limitPerPrimary => {
        related.forEach(primary => {
          if (secondaryNodes.length >= secondaryBudget) return;
          const primaryAngle = primaryAngles.get(primary.id) || 0;
          const secondaryRows = getGraphNeighborRows(primary, 12)
            .map(item => ({ row: item, theme: themeMap.get(item.id) }))
            .filter(item => item.theme)
            .filter(item =>
              item.theme.id !== activeId
              && item.theme.id !== primary.id
              && !primarySet.has(item.theme.id)
              && !secondarySeen.has(item.theme.id)
            )
            .slice(0, limitPerPrimary);
          const siblingCount = secondaryRows.length;
          secondaryRows.forEach((item, index) => {
            if (secondaryNodes.length >= secondaryBudget || secondarySeen.has(item.theme.id)) return;
            const spread = siblingCount <= 1 ? 0 : Math.min(0.8, 0.28 * (siblingCount - 1));
            const angleOffset = siblingCount <= 1 ? 0 : (-spread / 2) + (spread * index / Math.max(1, siblingCount - 1));
            const shellIndex = Math.floor(index / 3);
            const orbitX = primaryOrbitX + 250 + (shellIndex * 88);
            const orbitY = primaryOrbitY + 205 + (shellIndex * 72);
            const positionAngle = primaryAngle + angleOffset;
            const position = {
              x: center.x + (Math.cos(positionAngle) * orbitX),
              y: center.y + (Math.sin(positionAngle) * orbitY)
            };
            positions.set(item.theme.id, position);
            secondaryNodes.push({
              theme: item.theme,
              parentId: primary.id,
              angle: positionAngle,
              position,
            });
            secondarySeen.add(item.theme.id);
          });
        });
      });

      related.forEach(neighbor => {
        const relKey = [theme.id, neighbor.id].sort().join('|');
        const rel = relations.get(relKey);
        const pos = positions.get(neighbor.id);
        if (!pos || !rel) return;
        const neighborRadius = getNodeRadius(neighbor, 'neighbor');
        const clipped = clipLineToCircles(center, pos, centerRadius, neighborRadius);
        const line = make('line', {
          x1: clipped.x1,
          y1: clipped.y1,
          x2: clipped.x2,
          y2: clipped.y2,
          class: 'kg-edge',
          'stroke-width': Math.min(6, 1.6 + Number(rel.shared || 0) * 0.7),
        });
        edgeGroup.appendChild(line);
        graphEdgeEls.push({ el: line, source: theme.id, target: neighbor.id });
      });

      secondaryNodes.forEach(node => {
        const parentPos = positions.get(node.parentId);
        const secondaryPos = node.position;
        const parentTheme = themeMap.get(node.parentId);
        const secondaryTheme = node.theme;
        if (!parentPos || !secondaryPos || !parentTheme || !secondaryTheme) return;
        const clipped = clipLineToCircles(
          parentPos,
          secondaryPos,
          getNodeRadius(parentTheme, 'neighbor'),
          getNodeRadius(secondaryTheme, 'secondary'),
        );
        const line = make('line', {
          x1: clipped.x1,
          y1: clipped.y1,
          x2: clipped.x2,
          y2: clipped.y2,
          class: 'kg-edge kg-edge-tertiary',
          'stroke-width': 1.6,
        });
        edgeGroup.appendChild(line);
        graphEdgeEls.push({ el: line, source: node.parentId, target: secondaryTheme.id, via: node.parentId });
      });

      related.forEach((left, leftIndex) => {
        for (let rightIndex = leftIndex + 1; rightIndex < related.length; rightIndex += 1) {
          const right = related[rightIndex];
          const relKey = [left.id, right.id].sort().join('|');
          const rel = relations.get(relKey);
          if (!rel || Number(rel.shared || 0) < 2) continue;
          const leftPos = positions.get(left.id);
          const rightPos = positions.get(right.id);
          const clipped = clipLineToCircles(
            leftPos,
            rightPos,
            getNodeRadius(left, 'neighbor'),
            getNodeRadius(right, 'neighbor'),
          );
          const line = make('line', {
            x1: clipped.x1,
            y1: clipped.y1,
            x2: clipped.x2,
            y2: clipped.y2,
            class: 'kg-edge kg-edge-secondary',
            'stroke-width': Math.min(4, 0.9 + Number(rel.shared || 0) * 0.4),
          });
          edgeGroup.appendChild(line);
          graphEdgeEls.push({ el: line, source: left.id, target: right.id });
        }
      });

      drawNode(nodeGroup, theme, center, 'active');
      related.forEach(neighbor => drawNode(nodeGroup, neighbor, positions.get(neighbor.id), 'neighbor'));
      secondaryNodes.forEach(node => drawNode(nodeGroup, node.theme, node.position, 'secondary'));
      applyViewportTransform();
      refreshGraphHoverState();
    };

    const selectTheme = (themeId) => {
      const theme = themeMap.get(themeId);
      if (!theme) return;
      activeId = themeId;
      hoverId = '';
      clearTooltip();
      renderThemeList();
      renderSpotlights();
      drawGraph();
      resetViewport(1);
      renderPanel(theme, highlightedPaperId);
      highlightedPaperId = '';
    };

    const setSortMode = (mode) => {
      sortMode = mode;
      sortDefaultBtn.classList.toggle('is-active', mode === 'default');
      sortFocusBtn.classList.toggle('is-active', mode === 'focus');
      sortSignalBtn.classList.toggle('is-active', mode === 'signal');
      renderThemeList();
      renderSearchResults();
    };

    svg.addEventListener('click', () => {
      hoverId = '';
      clearTooltip();
      refreshGraphHoverState();
    });
    svg.addEventListener('dblclick', evt => {
      if (evt.target.closest && evt.target.closest('.kg-node')) return;
      evt.preventDefault();
      resetViewport(1);
    });
    svg.addEventListener('pointerdown', beginDrag);
    svg.addEventListener('pointermove', updateDrag);
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointerleave', endDrag);
    svg.addEventListener('wheel', evt => {
      evt.preventDefault();
      const point = svgPointFromEvent(evt);
      const nextScale = viewState.scale * (evt.deltaY < 0 ? 1.1 : 0.9);
      zoomAtPoint(nextScale, point);
    }, { passive: false });

    searchInput.addEventListener('input', () => {
      searchQuery = searchInput.value || '';
      renderSearchResults();
      renderThemeList();
    });

    resetBtn.addEventListener('click', () => {
      searchQuery = '';
      searchInput.value = '';
      highlightedPaperId = '';
      renderSearchResults();
      setSortMode('default');
      const firstThemeId = (data.spotlight_ids && data.spotlight_ids[0]) || (data.themes && data.themes[0] ? data.themes[0].id : '');
      if (firstThemeId) selectTheme(firstThemeId);
      else renderPanel(null);
    });

    sortDefaultBtn.addEventListener('click', () => setSortMode('default'));
    sortFocusBtn.addEventListener('click', () => setSortMode('focus'));
    sortSignalBtn.addEventListener('click', () => setSortMode('signal'));
    zoomInBtn.addEventListener('click', () => zoomAtPoint(viewState.scale * 1.14, { x: VIEWBOX.width / 2, y: VIEWBOX.height / 2 }));
    zoomOutBtn.addEventListener('click', () => zoomAtPoint(viewState.scale * 0.88, { x: VIEWBOX.width / 2, y: VIEWBOX.height / 2 }));
    centerViewBtn.addEventListener('click', () => resetViewport(1));

    renderSpotlights();
    renderSearchResults();
    renderThemeList();
    const firstThemeId = (data.spotlight_ids && data.spotlight_ids[0]) || (data.themes && data.themes[0] ? data.themes[0].id : '');
    if (firstThemeId) {
      selectTheme(firstThemeId);
    } else {
      renderPanel(null);
    }
  })();
  </script>
</section>
"""
    return (
        html_block.replace("__PAYLOAD__", payload)
        .replace("__PAPER_COUNT__", str(graph_data["stats"]["paper_count"]))
        .replace("__THEME_COUNT__", str(graph_data["stats"]["theme_count"]))
        .replace("__RELATION_COUNT__", str(graph_data["stats"]["relation_count"]))
    )


def render_html_section_overview(title: str, papers: List[Paper], prefix: str) -> str:
    out: List[str] = []
    out.append(f"<section id='{prefix}'><h2>{html.escape(title)} ({len(papers)})</h2>")
    if not papers:
        out.append("<p>暂无记录。</p></section>")
        return "\n".join(out)

    out.append("<div class='paper-list'>")
    for i, p in enumerate(papers, start=1):
        domain_tags = clean_tag_list(p.domain_tags)
        task_tags = clean_tag_list(p.task_tags)
        type_tags = clean_tag_list(p.type_tags)
        out.append("<article class='paper-card'>")
        out.append(f"<h3>{i}. {html.escape(p.title)}</h3>")
        out.append(f"<p class='title-zh'>{html.escape(p.title_zh)}</p>")
        out.append(f"<p class='summary'>{html.escape(p.summary_zh)}</p>")
        out.append("<p class='meta'>")
        out.append(f"<span><strong>分类:</strong> {html.escape(p.major_area)}</span> ")
        out.append(f"<span><strong>领域:</strong> {html.escape(', '.join(domain_tags))}</span> ")
        out.append(f"<span><strong>任务:</strong> {html.escape(', '.join(task_tags))}</span> ")
        out.append(f"<span><strong>类型:</strong> {html.escape(', '.join(type_tags))}</span> ")
        if p.accepted_venue:
            out.append(f"<span><strong>中稿线索:</strong> {html.escape(p.accepted_venue)}</span>")
        out.append("</p>")
        out.append("<p class='links'>")
        out.append(f"<a href='{html.escape(p.link_abs)}' target='_blank'>arXiv</a> | ")
        out.append(f"<a href='{html.escape(p.link_pdf)}' target='_blank'>PDF</a>")
        out.append("</p>")
        out.append("</article>")
    out.append("</div></section>")
    return "\n".join(out)


def render_html_report(
    report_title: str,
    target_day: dt.date,
    tz_name: str,
    daily_groups: Dict[str, List[Paper]],
    focus_latest: List[Paper],
    focus_hot: List[Paper],
    venue_pool: List[Paper],
    venue_watch: List[Paper],
    report_meta: Optional[Dict[str, object]] = None,
) -> str:
    daily_keys = [k for k in ["CV", "AI", "CV+AI", "Other"] if daily_groups.get(k)]
    total_daily = sum(len(daily_groups.get(k, [])) for k in daily_keys)
    show_focus_hot = len(focus_hot) > 0
    show_venue_pool = len(venue_pool) > 0
    show_venue_watch = len(venue_watch) > 0
    graph_papers = dedupe_papers(
        [p for rows in daily_groups.values() for p in rows] + focus_latest + focus_hot + venue_pool + venue_watch
    )
    show_graph = len(graph_papers) > 0

    body: List[str] = []
    body.append("<!doctype html>")
    body.append("<html lang='zh-CN'><head><meta charset='utf-8' />")
    body.append("<meta name='viewport' content='width=device-width, initial-scale=1' />")
    body.append(f"<title>{html.escape(report_title)}</title>")
    body.append("<style>")
    body.append("""
:root {
  --bg: #f7f8fa;
  --panel: #ffffff;
  --ink: #15202b;
  --muted: #5c6b7a;
  --line: #d9e2ec;
  --accent: #0a7f5a;
  --accent-soft: #e8f7f1;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: radial-gradient(circle at 10% 10%, #eef8ff, var(--bg));
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
}
.container {
  max-width: 1200px;
  margin: 24px auto;
  padding: 0 16px 48px;
}
header {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 18px;
}
h1 { margin: 0 0 8px; font-size: 28px; }
.subtitle { color: var(--muted); margin: 0; }
nav {
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
nav a {
  text-decoration: none;
  color: #084d3a;
  background: var(--accent-soft);
  border: 1px solid #b7e7d8;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 13px;
}
.run-summary-wrap {
  margin-top: 16px;
  padding: 14px;
  border-radius: 16px;
  border: 1px solid #d7e6f1;
  background: linear-gradient(135deg, rgba(232, 247, 241, 0.9), rgba(242, 247, 255, 0.95));
}
.run-summary-note {
  margin: 0 0 12px;
  color: #33556d;
  font-size: 13px;
}
.run-summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) minmax(280px, 1.7fr);
  gap: 12px;
}
.run-summary-card {
  border-radius: 14px;
  border: 1px solid rgba(15, 79, 114, 0.12);
  background: rgba(255, 255, 255, 0.92);
  padding: 14px 16px;
  min-height: 108px;
}
.run-summary-card strong {
  display: block;
  font-size: 30px;
  line-height: 1.1;
  color: #0f4f72;
  margin-bottom: 8px;
}
.run-summary-card span {
  display: block;
  color: var(--muted);
  font-size: 13px;
}
.run-summary-card.is-path strong {
  font-size: 18px;
  margin-bottom: 10px;
}
.run-summary-path {
  margin-top: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: #0f4f72;
  line-height: 1.5;
  word-break: break-all;
}
section {
  margin-top: 18px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 16px;
}
.paper-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}
.paper-card {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px;
  background: #fff;
}
.paper-card h3 { margin: 0 0 8px; font-size: 17px; }
.title-zh { margin: 0 0 8px; color: #0f4f72; font-weight: 600; }
.summary { margin: 0 0 10px; color: #1d2d3a; }
.meta, .links { color: var(--muted); font-size: 13px; }
.links a { color: var(--accent); }
.signal-wrap { overflow-x: auto; }
.signal-table { width: 100%; border-collapse: collapse; margin: 8px 0 16px; }
.signal-table th, .signal-table td { border: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; font-size: 13px; }
.signal-table th { background: #f1f6fb; }
.signal-title-zh { color: #0f4f72; font-weight: 600; margin-top: 4px; }
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
.graph-stat-grid strong {
  display: block;
  font-size: 28px;
  color: #0f4f72;
}
.graph-stat-grid span {
  display: block;
  color: var(--muted);
  font-size: 13px;
  margin-top: 4px;
}
.graph-toolbar-shell {
  padding: 16px 18px 14px;
  border-bottom: 1px solid rgba(15, 79, 114, 0.12);
  background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(247,251,255,0.98));
}
.graph-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}
.graph-toolbar input {
  flex: 1 1 360px;
  min-width: 240px;
  border: 1px solid #c9dceb;
  border-radius: 999px;
  padding: 12px 16px;
  font-size: 14px;
  background: #fff;
}
.graph-toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.kg-toolbar-btn {
  border: 1px solid #d3e2ef;
  background: #fff;
  color: #0f4f72;
  border-radius: 999px;
  padding: 9px 14px;
  font-size: 13px;
  cursor: pointer;
}
.kg-toolbar-btn.is-active {
  background: #0f4f72;
  color: #fff;
  border-color: #0f4f72;
}
.graph-spotlight-row {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  align-items: center;
  margin-top: 14px;
}
.graph-spotlight-label {
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.kg-spotlights {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
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
.kg-spotlight-pill.is-active {
  background: #0f4f72;
  color: #fff;
  border-color: #0f4f72;
}
.kg-spotlight-pill.is-active span {
  background: rgba(255,255,255,0.18);
  color: #fff;
}
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
.kg-sidebar-head h3 {
  margin: 0 0 6px;
  font-size: 20px;
}
.kg-sidebar-head p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}
.kg-search-results {
  margin-top: 14px;
  padding: 12px;
  border-radius: 14px;
  border: 1px solid #dce8f2;
  background: #fff;
  overflow-y: auto;
  max-height: 260px;
}
.kg-result-block + .kg-result-block {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #edf2f7;
}
.kg-result-block h4 {
  margin: 0 0 8px;
  font-size: 13px;
  color: #0f4f72;
}
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
.kg-result-row strong {
  font-size: 13px;
  color: #17324a;
}
.kg-result-row span {
  font-size: 12px;
  color: var(--muted);
}
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
.kg-result-paper a {
  color: #0f4f72;
  text-decoration: none;
  font-size: 12px;
}
.kg-theme-list {
  margin-top: 14px;
  overflow-y: auto;
  padding-right: 4px;
}
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
.kg-theme-item.is-active {
  border-color: #0f4f72;
  box-shadow: 0 10px 25px rgba(15, 79, 114, 0.12);
}
.kg-theme-item-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.kg-theme-item-top strong {
  font-size: 14px;
  color: #17324a;
}
.kg-theme-share {
  color: var(--muted);
  font-size: 12px;
}
.kg-theme-item-badges {
  margin-top: 10px;
}
.graph-canvas-wrap {
  position: relative;
  min-height: 760px;
  padding: 18px 14px 14px;
  background:
    radial-gradient(circle at center, rgba(235,248,255,0.9), rgba(247,250,252,0.95)),
    linear-gradient(180deg, #fbfdff 0%, #f7fbff 100%);
  border-right: 1px solid rgba(15, 79, 114, 0.1);
}
.graph-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: center;
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 12px;
}
.graph-legend span {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.graph-legend-note {
  opacity: 0.8;
}
.legend-dot {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  display: inline-block;
}
.legend-dot.keyword {
  background: #0f4f72;
  box-shadow: 0 0 0 3px rgba(15, 79, 114, 0.12);
}
.legend-dot.secondary {
  background: #5b8db4;
  box-shadow: 0 0 0 3px rgba(91, 141, 180, 0.14);
}
.legend-line {
  width: 20px;
  height: 2px;
  background: rgba(15, 79, 114, 0.24);
  display: inline-block;
}
#kg-svg {
  width: 100%;
  height: 680px;
  display: block;
  cursor: grab;
}
#kg-svg.is-dragging {
  cursor: grabbing;
}
.graph-canvas-note {
  margin: 2px 8px 0;
  color: var(--muted);
  font-size: 12px;
}
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
.kg-view-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}
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
.kg-view-btn.is-wide {
  min-width: 64px;
  font-size: 13px;
}
.kg-zoom-level {
  min-width: 58px;
  text-align: center;
  font-size: 13px;
  font-weight: 700;
  color: #17324a;
}
.kg-edge {
  stroke: rgba(15, 79, 114, 0.2);
  stroke-width: 1.4;
  stroke-linecap: round;
  transition: stroke 140ms ease, opacity 140ms ease, stroke-width 140ms ease;
}
.kg-edge-secondary {
  stroke: rgba(15, 79, 114, 0.08);
}
.kg-edge-tertiary {
  stroke: rgba(91, 141, 180, 0.16);
}
.kg-edge.is-highlight {
  stroke: rgba(15, 79, 114, 0.54);
  opacity: 1;
}
.kg-edge.is-faded {
  opacity: 0.18;
}
.kg-node {
  cursor: pointer;
  transition: opacity 160ms ease;
}
.kg-node .kg-hit {
  fill: transparent;
}
.kg-node .kg-halo {
  fill: rgba(15, 79, 114, 0.08);
  stroke: rgba(15, 79, 114, 0.14);
  stroke-width: 1.5;
  opacity: 0;
  transition: opacity 140ms ease, fill 140ms ease, stroke 140ms ease;
}
.kg-node .kg-core {
  transition: fill 160ms ease, stroke 160ms ease, stroke-width 160ms ease;
}
.kg-node-active .kg-core {
  fill: #f97316;
  stroke: #ffffff;
  stroke-width: 3;
}
.kg-node-neighbor .kg-core {
  fill: #0f4f72;
  stroke: #ffffff;
  stroke-width: 2.5;
}
.kg-node-secondary .kg-core {
  fill: #6f9abb;
  stroke: #ffffff;
  stroke-width: 2.1;
}
.kg-node.is-selected .kg-halo,
.kg-node.is-hovered .kg-halo {
  opacity: 1;
}
.kg-node.is-hovered .kg-core {
  fill: #1d7aa5;
  stroke-width: 3.2;
}
.kg-node.kg-node-secondary.is-hovered .kg-core {
  fill: #4c7ea5;
  stroke-width: 2.8;
}
.kg-node.is-selected .kg-core {
  stroke-width: 4;
}
.kg-node.is-soft-faded {
  opacity: 0.58;
}
.kg-label {
  font-family: "Avenir Next", "SF Pro Display", "PingFang SC", "Noto Sans SC", -apple-system, BlinkMacSystemFont, sans-serif;
  fill: #18324a;
  pointer-events: none;
  paint-order: stroke;
  stroke: rgba(255,255,255,0.96);
  stroke-width: 6px;
  stroke-linejoin: round;
}
.keyword-label {
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 0.01em;
}
.keyword-label.is-active {
  font-size: 23px;
  font-weight: 800;
}
.keyword-label.is-neighbor {
  font-size: 18px;
}
.keyword-label.is-secondary {
  font-size: 13px;
  font-weight: 700;
}
.kg-panel {
  padding: 20px 18px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbfd 100%);
  min-height: 760px;
  max-height: 760px;
  overflow-y: auto;
  overflow-x: hidden;
}
.kg-panel h3 { margin-top: 0; font-size: 22px; }
.kg-panel-desc {
  color: #213547;
  line-height: 1.7;
}
.kg-panel-note {
  color: var(--muted);
  font-size: 13px;
}
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
.kg-chip-row, .kg-meta-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0;
}
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
.kg-chip-soft {
  background: #fff;
  border-color: #e3edf5;
}
.kg-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin: 14px 0 6px;
}
.kg-metric-grid div {
  border: 1px solid #e1ecf4;
  border-radius: 12px;
  padding: 10px;
  background: #fff;
  text-align: center;
}
.kg-metric-grid strong {
  display: block;
  color: #0f4f72;
  font-size: 20px;
}
.kg-metric-grid span {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: 12px;
}
.kg-meta-list span {
  display: inline-flex;
  align-items: center;
  padding: 4px 9px;
  border-radius: 999px;
  background: #fff;
  border: 1px solid #e2ebf3;
  font-size: 12px;
}
.kg-subblock {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid #e2ebf3;
}
.kg-subgrid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.kg-subblock h4 {
  margin: 0 0 10px;
}
.kg-related-list {
  margin: 0;
  padding-left: 18px;
}
.kg-related-list li {
  margin-bottom: 8px;
}
.kg-related-list a {
  color: #0f4f72;
  text-decoration: none;
}
.kg-inline-btn {
  border: 0;
  background: transparent;
  color: #0f4f72;
  padding: 0;
  cursor: pointer;
  font: inherit;
  text-decoration: underline;
}
.kg-related-score {
  display: inline-block;
  margin-left: 8px;
  color: var(--muted);
  font-size: 12px;
}
.kg-paper-list li p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}
.kg-paper-list {
  padding-left: 0;
  list-style: none;
}
.kg-paper-item {
  border-radius: 12px;
  padding: 8px 10px;
  background: #fbfdff;
  border: 1px solid transparent;
}
.kg-paper-item.is-highlight {
  border-color: #f97316;
  background: #fff7ed;
}
.kg-paper-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}
.kg-paper-venue {
  display: inline-flex;
  padding: 2px 7px;
  border-radius: 999px;
  background: #fff2e8;
  color: #b45309;
  border: 1px solid #fed7aa;
  font-size: 11px;
}
.kg-paper-focus {
  display: inline-flex;
  padding: 2px 7px;
  border-radius: 999px;
  background: #edf7f3;
  color: #0b6b51;
  border: 1px solid #cce8dd;
  font-size: 11px;
}
.kg-empty {
  color: var(--muted);
  font-size: 13px;
  margin: 0;
}
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
.kg-tooltip strong {
  display: block;
  font-size: 13px;
}
.kg-tooltip span {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: rgba(255,255,255,0.82);
}
footer { margin-top: 20px; color: var(--muted); font-size: 12px; }
@media (max-width: 700px) {
  .paper-list { grid-template-columns: 1fr; }
  h1 { font-size: 24px; }
}
@media (max-width: 980px) {
  .graph-hero {
    grid-template-columns: 1fr;
  }
  .graph-layout {
    grid-template-columns: 1fr;
  }
  .kg-sidebar {
    max-height: none;
    border-right: 0;
    border-bottom: 1px solid rgba(15, 79, 114, 0.1);
  }
  .graph-canvas-wrap {
    min-height: 560px;
    border-right: 0;
    border-bottom: 1px solid rgba(15, 79, 114, 0.1);
  }
  .kg-canvas-hud {
    right: 12px;
    top: 12px;
    padding: 8px 10px;
  }
  .kg-view-btn {
    min-width: 34px;
    height: 34px;
  }
  #kg-svg {
    height: 560px;
  }
  .kg-panel {
    min-height: auto;
    max-height: none;
  }
  .kg-subgrid {
    grid-template-columns: 1fr;
  }
  .graph-stat-grid {
    grid-template-columns: repeat(3, 1fr);
  }
  .run-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .run-summary-card.is-path {
    grid-column: 1 / -1;
    min-height: auto;
  }
}
""")
    body.append("</style></head><body><div class='container'>")
    body.append("<header>")
    body.append(f"<h1>{html.escape(report_title)}</h1>")
    subtitle_parts = [
        f"日期: {target_day.isoformat()} ({html.escape(tz_name)})",
        f"每日总数: {total_daily}",
        f"Focus最新: {len(focus_latest)}",
    ]
    if show_focus_hot:
        subtitle_parts.append(f"Focus重点: {len(focus_hot)}")
    if show_venue_pool:
        subtitle_parts.append(f"顶会顶刊池: {len(venue_pool)}")
    if show_venue_watch:
        subtitle_parts.append(f"顶会顶刊线索: {len(venue_watch)}")
    body.append(f"<p class='subtitle'>{' | '.join(subtitle_parts)}</p>")
    fetch_summary = report_meta.get("fetch_summary") if isinstance(report_meta, dict) else None
    if isinstance(fetch_summary, dict):
        ignore_mode = "已启用忽略已抓取" if fetch_summary.get("ignore_fetched") else "未启用忽略已抓取"
        state_path = html.escape(str(fetch_summary.get("state_path", "")))
        selected_seen_count = int(fetch_summary.get("selected_seen_count", 0))
        note_suffix = ""
        if not fetch_summary.get("ignore_fetched") and selected_seen_count > 0:
            note_suffix = f" 当前报告中包含 {selected_seen_count} 篇此前已抓取论文。"
        body.append("<div class='run-summary-wrap'>")
        body.append(
            "<p class='run-summary-note'>"
            f"抓取记录摘要按最终报告中的唯一论文统计；本次状态: {html.escape(ignore_mode)}。"
            f"{html.escape(note_suffix)}"
            "</p>"
        )
        body.append("<div class='run-summary-grid'>")
        body.append(
            "<div class='run-summary-card'>"
            f"<strong>{int(fetch_summary.get('new_count', 0))}</strong>"
            "<span>本次新增数</span>"
            "</div>"
        )
        body.append(
            "<div class='run-summary-card'>"
            f"<strong>{int(fetch_summary.get('backfill_count', 0))}</strong>"
            "<span>本次回补数</span>"
            "</div>"
        )
        body.append(
            "<div class='run-summary-card'>"
            f"<strong>{int(fetch_summary.get('ignored_seen_count', 0))}</strong>"
            "<span>已见忽略数</span>"
            "</div>"
        )
        body.append(
            "<div class='run-summary-card is-path'>"
            "<strong>当前配置状态文件</strong>"
            "<span>当前抓取配置对应的已抓取记录文件</span>"
            f"<div class='run-summary-path'>{state_path}</div>"
            "</div>"
        )
        body.append("</div></div>")
    body.append("<nav>")
    if daily_groups.get("CV"):
        body.append("<a href='#daily-cv'>Daily CV</a>")
    if daily_groups.get("AI"):
        body.append("<a href='#daily-ai'>Daily AI</a>")
    if daily_groups.get("CV+AI"):
        body.append("<a href='#daily-cvai'>Daily CV+AI</a>")
    if daily_groups.get("Other"):
        body.append("<a href='#daily-other'>Daily Other</a>")
    if show_graph:
        body.append("<a href='#knowledge-graph'>知识图谱</a>")
    body.append(f"<a href='#focus-latest'>Focus 最新{len(focus_latest) if focus_latest else 0}</a>")
    if show_focus_hot:
        body.append(f"<a href='#focus-hot'>Focus 重点{len(focus_hot)}</a>")
    if show_venue_pool:
        body.append("<a href='#venue-pool'>顶会顶刊池</a>")
    if show_venue_watch:
        body.append("<a href='#signal-table'>中稿线索总表</a>")
    body.append("</nav></header>")

    if show_graph:
        body.append(render_knowledge_graph_section(graph_papers))

    if daily_groups.get("CV"):
        body.append(render_html_section_overview("Daily - Computer Vision", daily_groups.get("CV", []), "daily-cv"))
    if daily_groups.get("AI"):
        body.append(render_html_section_overview("Daily - Artificial Intelligence", daily_groups.get("AI", []), "daily-ai"))
    if daily_groups.get("CV+AI"):
        body.append(render_html_section_overview("Daily - Cross-listed CV+AI", daily_groups.get("CV+AI", []), "daily-cvai"))
    if daily_groups.get("Other"):
        body.append(render_html_section_overview("Daily - Other", daily_groups.get("Other", []), "daily-other"))

    body.append(render_html_section_overview("Focus Topic - Latest", focus_latest, "focus-latest"))
    if show_focus_hot:
        body.append(render_html_section_overview("Focus Topic - Relevance/Hot", focus_hot, "focus-hot"))
    if show_venue_pool:
        body.append(render_html_section_overview("Top Venue / Journal Pool", venue_pool, "venue-pool"))
    if show_venue_watch:
        body.append(render_signal_table(venue_watch))

    body.append("<footer>Generated by arxiv_daily_digest.py</footer>")
    body.append("</div></body></html>")
    return "\n".join(body)


def render_markdown_quick(target_day: dt.date, tz_name: str, daily_groups: Dict[str, List[Paper]]) -> str:
    sections = [sec for sec in ["CV", "AI", "CV+AI", "Other"] if daily_groups.get(sec)]
    total_daily = sum(len(daily_groups.get(sec, [])) for sec in sections)
    lines = [f"# arXiv Daily Digest ({target_day.isoformat()}, {tz_name})", ""]
    lines.append(f"- 总数: {total_daily}")
    for sec in sections:
        lines.append(f"- {sec}: {len(daily_groups.get(sec, []))}")
    lines.append("")

    for sec in sections:
        if not daily_groups.get(sec):
            continue
        lines.append(f"## {sec}")
        for p in daily_groups.get(sec, [])[:80]:
            lines.append(f"- [{p.arxiv_id}]({p.link_abs}) {p.title_zh} | {p.summary_zh}")
        lines.append("")
    return "\n".join(lines)


def paper_from_dict(d: dict) -> Paper:
    paper = Paper(
        arxiv_id=str(d.get("arxiv_id", "")),
        title=str(d.get("title", "")),
        title_zh=str(d.get("title_zh", "")),
        authors=list(d.get("authors", []) or []),
        published=str(d.get("published", "")),
        updated=str(d.get("updated", "")),
        categories=list(d.get("categories", []) or []),
        summary_en=str(d.get("summary_en", "")),
        summary_zh=str(d.get("summary_zh", "")),
        link_abs=str(d.get("link_abs", "")),
        link_pdf=str(d.get("link_pdf", "")),
        comment=str(d.get("comment", "")),
        journal_ref=str(d.get("journal_ref", "")),
        major_area=str(d.get("major_area", "")),
        domain_tags=list(d.get("domain_tags", []) or []),
        task_tags=list(d.get("task_tags", []) or []),
        type_tags=list(d.get("type_tags", []) or []),
        focus_tags=list(d.get("focus_tags", []) or []),
        keywords=list(d.get("keywords", []) or []),
        accepted_venue=str(d.get("accepted_venue", "")),
        accepted_hint=str(d.get("accepted_hint", "")),
    )
    return refresh_paper_derived_fields(paper)


def papers_from_dicts(items: List[dict]) -> List[Paper]:
    return [paper_from_dict(x) for x in items if isinstance(x, dict)]


def normalize_output_suffix(text: str) -> str:
    clean = safe_text(text)
    if not clean:
        return ""
    clean = clean.replace(" ", "-")
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", clean)
    clean = re.sub(r"-{2,}", "-", clean)
    clean = clean.strip("._-")
    return clean[:48]


def normalize_arxiv_id(text: str) -> str:
    clean = safe_text(text)
    if not clean:
        return ""
    if "arxiv.org/" in clean:
        clean = clean.rstrip("/").rsplit("/", 1)[-1]
    clean = re.sub(r"^arxiv:", "", clean, flags=re.I)
    clean = re.sub(r"v\d+$", "", clean, flags=re.I)
    return clean


def paper_state_id(p: Paper) -> str:
    return normalize_arxiv_id(p.arxiv_id or p.link_abs or p.link_pdf or "")


def arxiv_id_sort_key(text: str) -> Tuple[int, int, str]:
    clean = normalize_arxiv_id(text)
    if not clean:
        return (-1, -1, "")
    modern = re.fullmatch(r"(\d{2})(\d{2})\.(\d{4,5})", clean)
    if modern:
        yymm = int(modern.group(1) + modern.group(2))
        serial = int(modern.group(3))
        return (yymm, serial, clean)
    legacy = re.fullmatch(r"([a-z-]+)\/(\d{7})", clean, flags=re.I)
    if legacy:
        return (0, int(legacy.group(2)), clean.lower())
    return (-1, -1, clean.lower())


def max_arxiv_id(ids: Iterable[str]) -> str:
    normalized = [normalize_arxiv_id(x) for x in ids if normalize_arxiv_id(x)]
    if not normalized:
        return ""
    return max(normalized, key=arxiv_id_sort_key)


def min_arxiv_id(ids: Iterable[str]) -> str:
    normalized = [normalize_arxiv_id(x) for x in ids if normalize_arxiv_id(x)]
    if not normalized:
        return ""
    return min(normalized, key=arxiv_id_sort_key)


def is_newer_arxiv_id(candidate: str, baseline: str) -> bool:
    cand = normalize_arxiv_id(candidate)
    base = normalize_arxiv_id(baseline)
    if not cand or not base:
        return False
    return arxiv_id_sort_key(cand) > arxiv_id_sort_key(base)


def build_fetch_state_config(args: argparse.Namespace, categories: List[str], focus_terms: List[str]) -> Dict[str, object]:
    return {
        "version": FETCH_STATE_VERSION,
        "domain": safe_text(args.domain).lower(),
        "categories": sorted({safe_text(cat) for cat in categories if safe_text(cat)}),
        "arxiv_mode": safe_text(args.arxiv_mode).lower(),
        "day_window_days": int(args.day_window_days),
        "daily_limit_per_cat": int(args.daily_limit_per_cat),
        "page_size": int(args.page_size),
        "max_scan": int(args.max_scan),
        "focus_latest": int(args.focus_latest),
        "focus_hot": int(args.focus_hot),
        "focus_api_enable": int(args.focus_api_enable),
        "focus_recent_scan": int(args.focus_recent_scan),
        "focus_terms": sorted({safe_text(term).lower() for term in focus_terms if safe_text(term)}),
        "venue_latest": int(args.venue_latest),
        "venue_watch_limit": int(args.venue_watch_limit),
    }


def fetch_state_signature(config: Dict[str, object]) -> str:
    raw = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def fetch_state_slug(config: Dict[str, object]) -> str:
    domain = safe_text(str(config.get("domain", "digest"))).lower() or "digest"
    categories = [safe_text(str(cat)).lower().replace(".", "") for cat in list(config.get("categories", []) or []) if safe_text(str(cat))]
    cat_part = "-".join(categories[:2]) if categories else domain
    base = normalize_output_suffix(f"{domain}_{cat_part}")
    return base or "digest"


def load_fetch_state(path: str) -> Dict[str, object]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def seen_ids_from_state(state: Dict[str, object]) -> set[str]:
    fetched_ids = state.get("fetched_ids", {})
    if isinstance(fetched_ids, dict):
        return {normalize_arxiv_id(str(k)) for k in fetched_ids.keys() if normalize_arxiv_id(str(k))}
    if isinstance(fetched_ids, list):
        return {normalize_arxiv_id(str(k)) for k in fetched_ids if normalize_arxiv_id(str(k))}
    return set()


def latest_fetched_arxiv_id_from_state(state: Dict[str, object]) -> str:
    explicit = normalize_arxiv_id(str(state.get("latest_fetched_arxiv_id", "")))
    if explicit:
        return explicit
    return max_arxiv_id(seen_ids_from_state(state))


def filter_papers_by_seen_ids(
    papers: List[Paper],
    seen_ids: set[str],
    latest_seen_id: str = "",
    limit: int = -1,
) -> Tuple[List[Paper], Dict[str, int]]:
    if not seen_ids and not latest_seen_id:
        scoped = papers[:] if limit <= 0 else papers[:limit]
        return scoped, {"ignored_seen": 0, "selected_new": len(scoped), "selected_backfill": 0}
    selected_new: List[Paper] = []
    selected_backfill: List[Paper] = []
    ignored_seen = 0
    for p in papers:
        pid = paper_state_id(p)
        if pid and pid in seen_ids:
            ignored_seen += 1
            continue
        if pid and latest_seen_id and not is_newer_arxiv_id(pid, latest_seen_id):
            selected_backfill.append(p)
        else:
            selected_new.append(p)
    selected = selected_new[:]
    if limit > 0:
        if len(selected) < limit:
            need = limit - len(selected)
            selected.extend(selected_backfill[:need])
        else:
            selected = selected[:limit]
    else:
        selected.extend(selected_backfill)
    return selected, {
        "ignored_seen": ignored_seen,
        "selected_new": len(selected_new) if limit <= 0 else min(len(selected_new), len(selected)),
        "selected_backfill": max(0, len(selected) - min(len(selected_new), len(selected))),
    }


def collect_seen_paper_ids(papers: List[Paper], seen_ids: set[str]) -> set[str]:
    hits: set[str] = set()
    if not seen_ids:
        return hits
    for p in dedupe_papers(papers):
        pid = paper_state_id(p)
        if pid and pid in seen_ids:
            hits.add(pid)
    return hits


def build_fetch_report_summary(
    papers: List[Paper],
    seen_ids: set[str],
    latest_seen_id: str,
    ignored_seen_ids: set[str],
    fetch_state_path: str,
    ignore_fetched: bool,
) -> Dict[str, object]:
    selected_new_ids: set[str] = set()
    selected_backfill_ids: set[str] = set()
    selected_seen_ids: set[str] = set()
    for p in dedupe_papers(papers):
        pid = paper_state_id(p)
        if not pid:
            continue
        if pid in seen_ids:
            selected_seen_ids.add(pid)
            continue
        if latest_seen_id and not is_newer_arxiv_id(pid, latest_seen_id):
            selected_backfill_ids.add(pid)
        else:
            selected_new_ids.add(pid)
    return {
        "new_count": len(selected_new_ids),
        "backfill_count": len(selected_backfill_ids),
        "ignored_seen_count": len(ignored_seen_ids) if ignore_fetched else 0,
        "selected_seen_count": len(selected_seen_ids),
        "state_path": fetch_state_path,
        "ignore_fetched": bool(ignore_fetched),
    }


def update_fetch_state(
    state: Dict[str, object],
    config: Dict[str, object],
    signature: str,
    target_day: dt.date,
    papers: List[Paper],
    daily_count: int,
    focus_count: int,
    focus_hot_count: int,
    venue_pool_count: int,
    venue_watch_count: int,
) -> Dict[str, object]:
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    fetched_ids = state.get("fetched_ids", {})
    if not isinstance(fetched_ids, dict):
        fetched_ids = {}
    run_ids = [paper_state_id(p) for p in dedupe_papers(papers) if paper_state_id(p)]

    for p in dedupe_papers(papers):
        pid = paper_state_id(p)
        if not pid:
            continue
        existing = fetched_ids.get(pid, {})
        if not isinstance(existing, dict):
            existing = {}
        fetched_ids[pid] = {
            "first_seen_run_date": str(existing.get("first_seen_run_date") or target_day.isoformat()),
            "last_seen_run_date": target_day.isoformat(),
        }

    if not state.get("created_at"):
        state["created_at"] = now_iso
    state["updated_at"] = now_iso
    state["version"] = FETCH_STATE_VERSION
    state["config_signature"] = signature
    state["config"] = config
    state["fetched_ids"] = fetched_ids
    state["fetched_count"] = len(fetched_ids)
    state["latest_fetched_arxiv_id"] = max_arxiv_id(fetched_ids.keys())
    state["oldest_fetched_arxiv_id"] = min_arxiv_id(fetched_ids.keys())
    run_record = {
        "date": target_day.isoformat(),
        "newest_id": max_arxiv_id(run_ids),
        "oldest_id": min_arxiv_id(run_ids),
        "daily_count": daily_count,
        "focus_latest_count": focus_count,
        "focus_hot_count": focus_hot_count,
        "venue_pool_count": venue_pool_count,
        "venue_watch_count": venue_watch_count,
        "reported_unique_ids": len(run_ids),
    }
    state["last_run"] = run_record
    history = state.get("run_history", [])
    if not isinstance(history, list):
        history = []
    history.append(run_record)
    state["run_history"] = history[-40:]
    return state


def dump_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def count_llm_pending(
    papers: List[Paper],
    cache: Dict[str, dict],
    llm_limit: int,
    failed_retry_cooldown_hours: int,
) -> int:
    if llm_limit == 0:
        return 0
    pending = 0
    for idx, p in enumerate(papers, start=1):
        if llm_limit > 0 and idx > llm_limit:
            break
        cache_key = p.arxiv_id or p.link_abs
        cached = cache.get(cache_key, {})
        p_hash = paper_content_hash(p)
        if cached.get("hash") == p_hash and cached.get("status") == "ok":
            continue
        if cached.get("hash") == p_hash and cached.get("status") == "failed":
            updated_at = str(cached.get("updated_at", ""))
            if updated_at:
                try:
                    last_dt = dt.datetime.fromisoformat(updated_at)
                    now_dt = dt.datetime.now(last_dt.tzinfo) if last_dt.tzinfo else dt.datetime.now()
                    if (now_dt - last_dt) < dt.timedelta(hours=failed_retry_cooldown_hours):
                        continue
                except Exception:
                    pass
        pending += 1
    return pending


def resolve_default_categories(domain: str) -> List[str]:
    dom = (domain or "cv").strip().lower()
    if dom == "ai":
        return ["cs.AI"]
    if dom in {"both", "cv,ai", "ai,cv"}:
        return ["cs.CV", "cs.AI"]
    return ["cs.CV"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily arXiv crawler for CV+AI with HTML report.")
    parser.add_argument("--date", type=str, default="", help="Local date, YYYY-MM-DD. Default: today in timezone.")
    parser.add_argument("--tz", type=str, default=os.environ.get("DIGEST_TZ", "Asia/Shanghai"))
    parser.add_argument("--domain", type=str, default=os.environ.get("DIGEST_DOMAIN", "cv"), choices=["cv", "ai", "both"])
    parser.add_argument("--categories", type=str, default=os.environ.get("ARXIV_CATEGORIES", ""))
    parser.add_argument("--arxiv-mode", type=str, default=os.environ.get("ARXIV_MODE", "recent_only"), choices=["recent_only", "api_only", "auto"])
    parser.add_argument("--day-window-days", type=int, default=int(os.environ.get("DAY_WINDOW_DAYS", "2")))
    parser.add_argument("--daily-limit-per-cat", type=int, default=int(os.environ.get("DAILY_LIMIT_PER_CAT", "260")))
    parser.add_argument("--page-size", type=int, default=int(os.environ.get("ARXIV_PAGE_SIZE", "200")))
    parser.add_argument("--max-scan", type=int, default=int(os.environ.get("ARXIV_MAX_SCAN", "5000")))
    parser.add_argument("--focus-latest", type=int, default=int(os.environ.get("FOCUS_LATEST_N", "200")))
    parser.add_argument("--focus-hot", type=int, default=int(os.environ.get("FOCUS_HOT_N", "200")))
    parser.add_argument("--focus-api-enable", type=int, default=int(os.environ.get("FOCUS_API_ENABLE", "0")))
    parser.add_argument("--focus-recent-scan", type=int, default=int(os.environ.get("FOCUS_RECENT_SCAN", "1200")))
    parser.add_argument("--focus-terms", type=str, default=os.environ.get("FOCUS_TERMS_OVERRIDE", ""))
    parser.add_argument("--focus-terms-extra", type=str, default=os.environ.get("FOCUS_TERMS_EXTRA", ""))
    parser.add_argument("--venue-latest", type=int, default=int(os.environ.get("VENUE_LATEST_N", "200")))
    parser.add_argument("--venue-watch-limit", type=int, default=int(os.environ.get("VENUE_WATCH_LIMIT", "100")))
    parser.add_argument("--abs-enrich-limit", type=int, default=int(os.environ.get("ABS_ENRICH_LIMIT", "-1")))
    parser.add_argument("--focus-abs-enrich-limit", type=int, default=int(os.environ.get("FOCUS_ABS_ENRICH_LIMIT", "0")))
    parser.add_argument("--report-abs-enrich-limit", type=int, default=int(os.environ.get("REPORT_ABS_ENRICH_LIMIT", "-1")))
    parser.add_argument("--model", type=str, default=os.environ.get("TRANSLATE_MODEL", os.environ.get("KIMI_MODEL", "moonshot-v1-8k")))
    parser.add_argument("--translate-backend", type=str, default=os.environ.get("TRANSLATE_BACKEND", "google"), choices=["llm", "google", "auto"])
    parser.add_argument("--api-base", type=str, default=os.environ.get("OPENAI_BASE_URL", os.environ.get("KIMI_API_BASE", DEFAULT_API_BASE)))
    parser.add_argument("--llm-limit", type=int, default=int(os.environ.get("LLM_LIMIT", "-1")))
    parser.add_argument("--llm-max-retries", type=int, default=int(os.environ.get("LLM_MAX_RETRIES", "2")))
    parser.add_argument("--llm-failed-cooldown-hours", type=int, default=int(os.environ.get("LLM_FAILED_COOLDOWN_HOURS", "24")))
    parser.add_argument("--llm-timeout", type=int, default=int(os.environ.get("LLM_TIMEOUT_SECONDS", "25")))
    parser.add_argument("--google-timeout", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_TIMEOUT_SECONDS", "12")))
    parser.add_argument("--google-limit", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_LIMIT", "-1")))
    parser.add_argument("--google-summary-sentences", type=int, default=int(os.environ.get("GOOGLE_SUMMARY_SENTENCES", "3")))
    parser.add_argument("--google-full-abstract", type=int, default=int(os.environ.get("GOOGLE_TRANSLATE_FULL_ABSTRACT", "0")))
    parser.add_argument("--ignore-fetched", type=int, default=int(os.environ.get("IGNORE_FETCHED_ARTICLES", "1")), choices=[0, 1])
    parser.add_argument("--fetched-state-dir", type=str, default=os.environ.get("FETCH_STATE_DIR", "fetch_state"))
    parser.add_argument("--output-suffix", type=str, default=os.environ.get("REPORT_FILE_SUFFIX", ""))
    parser.add_argument("--report-dir", type=str, default="reports")
    parser.add_argument("--data-dir", type=str, default="data")
    args = parser.parse_args()

    try:
        tz = ZoneInfo(args.tz)
    except Exception:
        print(f"[ERROR] Invalid timezone: {args.tz}", file=sys.stderr)
        return 2

    if args.date:
        try:
            target_day = dt.date.fromisoformat(args.date)
        except Exception:
            print("[ERROR] --date must be YYYY-MM-DD", file=sys.stderr)
            return 2
    else:
        target_day = dt.datetime.now(tz).date()

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    if not categories:
        categories = resolve_default_categories(args.domain)

    global ACTIVE_FOCUS_TERMS, ACTIVE_FOCUS_MATCHERS
    ACTIVE_FOCUS_TERMS = configure_focus_terms(args.domain, args.focus_terms, args.focus_terms_extra)
    ACTIVE_FOCUS_MATCHERS = configure_focus_matchers(ACTIVE_FOCUS_TERMS)

    os.makedirs(args.report_dir, exist_ok=True)
    os.makedirs(args.data_dir, exist_ok=True)
    fetch_state_dir = os.path.join(args.data_dir, args.fetched_state_dir)
    os.makedirs(fetch_state_dir, exist_ok=True)

    llm_cache_path = os.path.join(args.data_dir, "llm_translation_cache.json")
    llm_cache = load_llm_cache(llm_cache_path)
    google_cache_path = os.path.join(args.data_dir, "google_translation_cache.json")
    google_cache = load_llm_cache(google_cache_path)
    abs_cache_path = os.path.join(args.data_dir, "abs_metadata_cache.json")
    abs_cache = load_llm_cache(abs_cache_path)
    last_success_path = os.path.join(args.data_dir, "last_success_digest.json")
    had_fetch_errors = False
    used_cached_snapshot = False
    cached_from_date = ""
    fetch_state_config = build_fetch_state_config(args, categories, ACTIVE_FOCUS_TERMS)
    fetch_state_sig = fetch_state_signature(fetch_state_config)
    fetch_state_name = f"fetch_state_{fetch_state_slug(fetch_state_config)}_{fetch_state_sig}.json"
    fetch_state_path = os.path.join(fetch_state_dir, fetch_state_name)
    fetch_state = load_fetch_state(fetch_state_path)
    seen_ids = seen_ids_from_state(fetch_state)
    latest_seen_id = latest_fetched_arxiv_id_from_state(fetch_state)
    ignored_seen_ids_union: set[str] = set()
    selection_stats = {
        "daily": {"ignored_seen": 0, "selected_new": 0, "selected_backfill": 0},
        "focus": {"ignored_seen": 0, "selected_new": 0, "selected_backfill": 0},
        "focus_hot": {"ignored_seen": 0, "selected_new": 0, "selected_backfill": 0},
        "venue_pool": {"ignored_seen": 0, "selected_new": 0, "selected_backfill": 0},
    }
    if args.ignore_fetched:
        latest_note = f"，最新已抓取 ID {latest_seen_id}" if latest_seen_id else ""
        print(f"[INFO] Fetch state: {fetch_state_path} (已记录 {len(seen_ids)} 篇{latest_note})")

    # 1) Daily fetch
    daily_raw: List[Paper] = []
    daily_fetch_limit = max(200, args.daily_limit_per_cat)
    if args.ignore_fetched:
        daily_fetch_limit = min(args.max_scan, max(daily_fetch_limit * 4, daily_fetch_limit + 200))
    for cat in categories:
        if cat not in ["cs.CV", "cs.AI"]:
            continue
        if args.arxiv_mode == "recent_only":
            try:
                fallback = fetch_recent_list_fallback(cat, limit=daily_fetch_limit)
                daily_raw.extend(fallback)
                print(f"[INFO] {cat} fetched recent/list: {len(fallback)}")
            except Exception as exc:
                had_fetch_errors = True
                print(f"[WARN] recent/list fetch failed {cat}: {exc}")
                try:
                    api_latest = fetch_latest_by_category(cat, limit=max(50, daily_fetch_limit // 2), page_size=args.page_size)
                    daily_raw.extend(api_latest)
                    print(f"[INFO] {cat} fallback(api latest) fetched: {len(api_latest)}")
                except Exception as api_exc:
                    print(f"[WARN] api latest fallback failed {cat}: {api_exc}")
            continue

        try:
            by_window = fetch_daily_by_category(
                cat,
                target_day,
                args.tz,
                page_size=args.page_size,
                max_scan=args.max_scan,
                day_window_days=args.day_window_days,
            )
            gap = max(0, daily_fetch_limit - len(by_window))
            by_latest = fetch_latest_by_category(cat, limit=gap, page_size=args.page_size) if gap > 0 else []
            daily_raw.extend(by_window)
            daily_raw.extend(by_latest)
            print(f"[INFO] {cat} fetched window/latest: {len(by_window)}/{len(by_latest)}")
        except Exception as exc:
            had_fetch_errors = True
            if args.arxiv_mode == "api_only":
                print(f"[WARN] Failed fetching {cat}: {exc}")
            try:
                fallback = fetch_recent_list_fallback(cat, limit=daily_fetch_limit)
                daily_raw.extend(fallback)
                print(f"[INFO] {cat} fallback(list/recent) fetched: {len(fallback)}")
            except Exception as exc2:
                print(f"[WARN] Fallback list fetch failed {cat}: {exc2}")

    daily_papers = dedupe_papers(daily_raw)
    if args.ignore_fetched:
        ignored_seen_ids_union.update(collect_seen_paper_ids(daily_papers, seen_ids))
        daily_papers, selection_stats["daily"] = filter_papers_by_seen_ids(
            daily_papers,
            seen_ids,
            latest_seen_id=latest_seen_id,
            limit=args.daily_limit_per_cat,
        )
    elif args.daily_limit_per_cat > 0:
        daily_papers = daily_papers[:args.daily_limit_per_cat]
    enrich_papers_from_abs_pages(daily_papers, args.abs_enrich_limit, abs_cache, abs_cache_path)
    if args.abs_enrich_limit != 0:
        daily_scope = daily_papers if args.abs_enrich_limit < 0 else daily_papers[:args.abs_enrich_limit]
        enriched_daily = sum(1 for p in daily_scope if p.summary_en)
        print(f"[INFO] Daily abs enrichment ready: {enriched_daily}/{len(daily_scope)}")

    # 2) Focus pools (derive from daily first; API query only as optional enhancement)
    focus_latest: List[Paper] = derive_focus_from_papers(daily_papers, args.focus_latest) if args.focus_latest > 0 else []
    focus_hot: List[Paper] = []
    if args.focus_latest > 0 and len(focus_latest) < args.focus_latest and categories:
        try:
            expanded_raw: List[Paper] = []
            per_cat_limit = max(100, args.focus_recent_scan // max(1, len(categories)))
            for cat in categories:
                expanded_raw.extend(fetch_recent_list_fallback(cat, limit=per_cat_limit))
            expanded = dedupe_papers(expanded_raw)
            if args.ignore_fetched:
                ignored_seen_ids_union.update(collect_seen_paper_ids(expanded, seen_ids))
                expanded, selection_stats["focus"] = filter_papers_by_seen_ids(
                    expanded,
                    seen_ids,
                    latest_seen_id=latest_seen_id,
                    limit=-1,
                )
            if args.focus_abs_enrich_limit > 0:
                enrich_papers_from_abs_pages(expanded, args.focus_abs_enrich_limit, abs_cache, abs_cache_path)
                enriched_focus = sum(1 for p in expanded[:args.focus_abs_enrich_limit] if p.summary_en)
                print(f"[INFO] Focus abs enrichment ready: {enriched_focus}/{min(len(expanded), args.focus_abs_enrich_limit)}")
            expanded_focus = derive_focus_from_papers(expanded, args.focus_latest, min_score=1)
            focus_latest = dedupe_papers(list(focus_latest) + list(expanded_focus))[:args.focus_latest]
            print(f"[INFO] Focus expanded from recent/list: {len(expanded_focus)} candidates")
        except Exception as exc:
            # Optional enhancement path; keep pipeline successful if unavailable.
            print(f"[INFO] Focus recent expansion skipped due to network: {exc}")
    need_more_focus = args.focus_latest > 0 and len(focus_latest) < args.focus_latest
    if args.focus_api_enable and (need_more_focus or args.focus_hot > 0):
        try:
            api_focus_latest, api_focus_hot = fetch_focus_pool(categories, ACTIVE_FOCUS_TERMS, args.focus_latest, args.focus_hot)
            if args.ignore_fetched:
                ignored_seen_ids_union.update(collect_seen_paper_ids(api_focus_latest, seen_ids))
                ignored_seen_ids_union.update(collect_seen_paper_ids(api_focus_hot, seen_ids))
                api_focus_latest, api_focus_latest_stats = filter_papers_by_seen_ids(
                    api_focus_latest,
                    seen_ids,
                    latest_seen_id=latest_seen_id,
                    limit=-1,
                )
                api_focus_hot, selection_stats["focus_hot"] = filter_papers_by_seen_ids(
                    api_focus_hot,
                    seen_ids,
                    latest_seen_id=latest_seen_id,
                    limit=-1,
                )
                selection_stats["focus"]["ignored_seen"] += api_focus_latest_stats["ignored_seen"]
                selection_stats["focus"]["selected_new"] += api_focus_latest_stats["selected_new"]
                selection_stats["focus"]["selected_backfill"] += api_focus_latest_stats["selected_backfill"]
            if api_focus_latest:
                # Merge derived + API and keep newest
                focus_latest = dedupe_papers(list(focus_latest) + list(api_focus_latest))[:args.focus_latest]
            focus_hot = api_focus_hot
        except Exception as exc:
            had_fetch_errors = True
            print(f"[WARN] Focus pool fetch failed: {exc}")

    # 2.5) Venue pool (recent papers mentioning top venues/journals)
    venue_pool: List[Paper] = []
    try:
        venue_pool = fetch_venue_pool(categories, TOP_VENUES, args.venue_latest)
        if args.ignore_fetched:
            ignored_seen_ids_union.update(collect_seen_paper_ids(venue_pool, seen_ids))
            venue_pool, selection_stats["venue_pool"] = filter_papers_by_seen_ids(
                venue_pool,
                seen_ids,
                latest_seen_id=latest_seen_id,
                limit=args.venue_latest if args.venue_latest > 0 else -1,
            )
    except Exception as exc:
        had_fetch_errors = True
        print(f"[WARN] Venue pool fetch failed: {exc}")

    # 3) Translation enrich only once on the deduped union to avoid repeated API work.
    # recent/list does not provide abstracts, so make sure every reported paper has
    # an abs-page abstract before translation. Otherwise a translator could only
    # translate the title and make the summary misleading.
    llm_union = dedupe_papers(daily_papers + focus_latest + focus_hot + venue_pool)
    enrich_missing_abstracts_from_abs_pages(
        llm_union,
        args.report_abs_enrich_limit,
        abs_cache,
        abs_cache_path,
        "Report",
        passes=3,
    )
    if args.translate_backend == "google":
        apply_translation_cache(llm_union, [google_cache])
    elif args.translate_backend == "llm":
        apply_translation_cache(llm_union, [llm_cache])
    else:
        apply_translation_cache(llm_union, [llm_cache, google_cache])
    if args.translate_backend in ["llm", "auto"]:
        pending_llm = count_llm_pending(
            llm_union,
            cache=llm_cache,
            llm_limit=args.llm_limit,
            failed_retry_cooldown_hours=args.llm_failed_cooldown_hours,
        )
        if args.llm_limit != 0:
            print(f"[INFO] LLM pending unique papers: {pending_llm}")
        llm_enrich_title_and_summary(
            llm_union,
            model=args.model,
            api_base=args.api_base,
            cache=llm_cache,
            cache_path=llm_cache_path,
            llm_limit=args.llm_limit,
            max_retries=args.llm_max_retries,
            failed_retry_cooldown_hours=args.llm_failed_cooldown_hours,
            request_timeout=args.llm_timeout,
        )
        apply_translation_cache(llm_union, [llm_cache, google_cache])
    if args.translate_backend == "google":
        google_enrich_title_and_summary(
            llm_union,
            cache=google_cache,
            cache_path=google_cache_path,
            limit=args.google_limit,
            timeout=args.google_timeout,
            full_abstract=bool(args.google_full_abstract),
            summary_sentences=args.google_summary_sentences,
        )
        apply_translation_cache(llm_union, [google_cache])
    remaining_missing = sum(1 for p in llm_union if missing_translation(p))
    if args.translate_backend == "llm" and args.llm_limit != 0 and remaining_missing:
        print(f"[INFO] LLM batch fill pending: {remaining_missing}")
        llm_fill_missing_translations_batched(
            llm_union,
            model=args.model,
            api_base=args.api_base,
            cache=llm_cache,
            cache_path=llm_cache_path,
            batch_size=12,
            max_retries=max(2, args.llm_max_retries),
            request_timeout=max(30, args.llm_timeout),
        )
        remaining_missing = sum(1 for p in llm_union if missing_translation(p))
        if remaining_missing:
            print(f"[INFO] LLM final single fill pending: {remaining_missing}")
            llm_fill_missing_translations_batched(
                llm_union,
                model=args.model,
                api_base=args.api_base,
                cache=llm_cache,
                cache_path=llm_cache_path,
                batch_size=1,
                max_retries=max(3, args.llm_max_retries),
                request_timeout=max(35, args.llm_timeout),
            )
    elif args.translate_backend == "auto" and remaining_missing:
        print(f"[INFO] Google Translate fallback pending: {remaining_missing}")
        google_enrich_title_and_summary(
            llm_union,
            cache=google_cache,
            cache_path=google_cache_path,
            limit=args.google_limit,
            timeout=args.google_timeout,
            full_abstract=bool(args.google_full_abstract),
            summary_sentences=args.google_summary_sentences,
        )
        apply_translation_cache(llm_union, [llm_cache, google_cache])
    remaining_missing = sum(1 for p in llm_union if missing_translation(p))
    if args.translate_backend == "google" and args.google_limit > 0 and remaining_missing:
        print(f"[INFO] Translation missing after fill: {remaining_missing} (google-limit={args.google_limit})")
    else:
        print(f"[INFO] Translation missing after fill: {remaining_missing}")
    if args.translate_backend == "google":
        apply_translation_cache(daily_papers + focus_latest + focus_hot + venue_pool, [google_cache])
    elif args.translate_backend == "llm":
        apply_translation_cache(daily_papers + focus_latest + focus_hot + venue_pool, [llm_cache])
    else:
        apply_translation_cache(daily_papers + focus_latest + focus_hot + venue_pool, [llm_cache, google_cache])

    daily_groups = split_by_major_area(daily_papers)
    venue_watch = collect_venue_watch(daily_papers + focus_latest + focus_hot + venue_pool, limit=args.venue_watch_limit)
    fetch_report_summary = build_fetch_report_summary(
        daily_papers + focus_latest + focus_hot + venue_pool,
        seen_ids,
        latest_seen_id,
        ignored_seen_ids_union,
        fetch_state_path,
        bool(args.ignore_fetched),
    )

    total_material = len(daily_papers) + len(focus_latest) + len(focus_hot) + len(venue_pool) + len(venue_watch)
    if args.ignore_fetched:
        ignored_total = sum(item["ignored_seen"] for item in selection_stats.values())
        print(
            "[INFO] Fetch selection summary: "
            f"daily 新={selection_stats['daily']['selected_new']}/回补={selection_stats['daily']['selected_backfill']}/已见忽略={selection_stats['daily']['ignored_seen']}; "
            f"focus 新={selection_stats['focus']['selected_new']}/回补={selection_stats['focus']['selected_backfill']}/已见忽略={selection_stats['focus']['ignored_seen']}; "
            f"focus_hot 新={selection_stats['focus_hot']['selected_new']}/回补={selection_stats['focus_hot']['selected_backfill']}/已见忽略={selection_stats['focus_hot']['ignored_seen']}; "
            f"venue_pool 新={selection_stats['venue_pool']['selected_new']}/回补={selection_stats['venue_pool']['selected_backfill']}/已见忽略={selection_stats['venue_pool']['ignored_seen']} "
            f"(已见忽略合计 {ignored_total})"
        )
        if total_material == 0 and not had_fetch_errors:
            print("[INFO] 当前配置下没有新的未抓取论文；保持已有报告不变。")
            return 0

    if had_fetch_errors and total_material == 0:
        if args.ignore_fetched:
            print("[WARN] 抓取失败且当前配置下没有新的未抓取论文；保持已有报告不变。")
            return 0
        if os.path.exists(last_success_path):
            try:
                cached = json.load(open(last_success_path, "r", encoding="utf-8"))
                daily_groups = {k: papers_from_dicts(v) for k, v in (cached.get("daily_groups", {}) or {}).items()}
                focus_latest = papers_from_dicts(cached.get("focus_latest", []) or [])
                focus_hot = papers_from_dicts(cached.get("focus_hot", []) or [])
                venue_pool = papers_from_dicts(cached.get("venue_pool", []) or [])
                venue_watch = papers_from_dicts(cached.get("venue_watch", []) or [])
                used_cached_snapshot = True
                cached_from_date = str(cached.get("date", "unknown"))
                print(f"[WARN] Network fetch failed; fallback to last success snapshot from {cached_from_date}.")
            except Exception as exc:
                print(f"[WARN] Failed loading cached snapshot: {exc}")
                print("[WARN] Fetch failed and returned empty dataset; keep previous report unchanged.")
                return 0
        else:
            print("[WARN] Fetch failed and returned empty dataset; keep previous report unchanged.")
            return 0

    if categories == ["cs.AI"]:
        report_title = "arXiv AI 每日追踪与重点方向报告"
    elif set(categories) == {"cs.CV", "cs.AI"}:
        report_title = "arXiv CV/AI 每日追踪与重点方向报告"
    else:
        report_title = "arXiv CV 每日追踪与重点方向报告"
    html_text = render_html_report(
        report_title,
        target_day,
        args.tz,
        daily_groups,
        focus_latest,
        focus_hot,
        venue_pool,
        venue_watch,
        report_meta={"fetch_summary": fetch_report_summary},
    )
    md_text = render_markdown_quick(target_day, args.tz, daily_groups)

    output_suffix = normalize_output_suffix(args.output_suffix)
    suffix_part = f"_{output_suffix}" if output_suffix else ""

    html_path = os.path.join(args.report_dir, f"arxiv_digest_{target_day.isoformat()}{suffix_part}.html")
    html_latest_path = os.path.join(args.report_dir, f"arxiv_digest_latest{suffix_part}.html")
    md_path = os.path.join(args.report_dir, f"arxiv_digest_{target_day.isoformat()}{suffix_part}.md")
    md_latest_path = os.path.join(args.report_dir, f"arxiv_digest_latest{suffix_part}.md")
    json_path = os.path.join(args.data_dir, f"arxiv_digest_{target_day.isoformat()}{suffix_part}.json")

    payload = {
        "date": target_day.isoformat(),
        "timezone": args.tz,
        "categories": categories,
        "daily_count": len(daily_papers),
        "daily_groups": {k: [asdict(x) for x in v] for k, v in daily_groups.items()},
        "focus_latest_count": len(focus_latest),
        "focus_hot_count": len(focus_hot),
        "focus_latest": [asdict(x) for x in focus_latest],
        "focus_hot": [asdict(x) for x in focus_hot],
        "venue_pool_count": len(venue_pool),
        "venue_pool": [asdict(x) for x in venue_pool],
        "venue_watch_count": len(venue_watch),
        "venue_watch": [asdict(x) for x in venue_watch],
        "notes": {
            "chinese_summary": "Chinese translation uses the configured translate_backend: google, llm, or auto.",
            "hot_definition": "relevance-ranked on focus query in arXiv API.",
            "daily_window_logic": "Daily section combines target date and previous day (local timezone window) plus latest fallback.",
            "domain": args.domain,
            "focus_terms": ACTIVE_FOCUS_TERMS,
            "translate_backend": args.translate_backend,
            "output_suffix": output_suffix,
            "ignore_fetched": bool(args.ignore_fetched),
            "fetch_state_signature": fetch_state_sig,
            "fetch_state_path": fetch_state_path,
            "latest_fetched_arxiv_id_before_run": latest_seen_id,
            "fetch_selection": selection_stats,
            "fetch_report_summary": fetch_report_summary,
            "llm_api_base": normalize_api_base(args.api_base),
            "llm_cache_path": llm_cache_path,
            "google_cache_path": google_cache_path,
            "google_summary_sentences": args.google_summary_sentences,
            "google_translate_full_abstract": bool(args.google_full_abstract),
            "abs_cache_path": abs_cache_path,
            "llm_max_retries": args.llm_max_retries,
            "focus_recent_scan": args.focus_recent_scan,
            "used_cached_snapshot": used_cached_snapshot,
            "cached_from_date": cached_from_date,
        },
    }

    write_text(html_path, html_text)
    write_text(html_latest_path, html_text)
    write_text(md_path, md_text)
    write_text(md_latest_path, md_text)
    dump_json(json_path, payload)
    dump_json(last_success_path, payload)
    if not used_cached_snapshot:
        report_union = dedupe_papers(daily_papers + focus_latest + focus_hot + venue_pool + venue_watch)
        fetch_state = update_fetch_state(
            fetch_state,
            config=fetch_state_config,
            signature=fetch_state_sig,
            target_day=target_day,
            papers=report_union,
            daily_count=len(daily_papers),
            focus_count=len(focus_latest),
            focus_hot_count=len(focus_hot),
            venue_pool_count=len(venue_pool),
            venue_watch_count=len(venue_watch),
        )
        dump_json(fetch_state_path, fetch_state)

    print(f"[OK] Daily papers total: {len(daily_papers)}")
    print(f"[OK] Focus latest/hot: {len(focus_latest)}/{len(focus_hot)}")
    print(f"[OK] Venue pool: {len(venue_pool)}")
    print(f"[OK] Venue signals: {len(venue_watch)}")
    print(f"[OK] Fetch state: {fetch_state_path}")
    print(f"[OK] Translation backend: {args.translate_backend}")
    print(f"[OK] LLM cache: {llm_cache_path}")
    print(f"[OK] Google cache: {google_cache_path}")
    print(f"[OK] Last success snapshot: {last_success_path}")
    print(f"[OK] HTML report: {html_path}")
    print(f"[OK] HTML latest: {html_latest_path}")
    print(f"[OK] Markdown: {md_path}")
    print(f"[OK] JSON: {json_path}")

    if args.translate_backend in ["llm", "auto"] and not (os.environ.get("OPENAI_API_KEY") or os.environ.get("KIMI_API_KEY")):
        print("[NOTE] OPENAI_API_KEY/KIMI_API_KEY not set; used fallback translation/summary placeholders.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
