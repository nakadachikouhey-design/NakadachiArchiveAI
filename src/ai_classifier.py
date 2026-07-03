from __future__ import annotations

import re
from typing import Any


CATEGORY_RULES = {
    "contract_finance": [
        "契約",
        "請求",
        "見積",
        "領収",
        "精算",
        "budget",
        "invoice",
        "receipt",
        "contract",
    ],
    "grant_report": ["助成", "報告", "申請", "実績", "grant", "report"],
    "production": ["台本", "稽古", "舞台", "公演", "制作", "進行", "cue", "script"],
    "photo_documentation": ["写真", "photo", "image", "記録写真"],
    "video_documentation": ["映像", "動画", "movie", "video", "youtube"],
    "audio_music": ["音声", "録音", "音楽", "logic", "audio", "sound"],
    "design_publicity": ["チラシ", "フライヤー", "広報", "press", "poster", "design"],
    "meeting_notes": ["議事", "打合", "打ち合わせ", "meeting", "memo", "minutes"],
    "archive_index": ["目録", "一覧", "index", "list"],
}


TAG_PATTERNS = {
    "final": ["final", "完成", "最終", "master"],
    "draft": ["draft", "下書き", "仮", "temp"],
    "finance": ["請求", "見積", "領収", "精算", "invoice", "receipt"],
    "legal": ["契約", "覚書", "agreement", "contract"],
    "publicity": ["広報", "press", "チラシ", "flyer", "poster"],
    "record": ["記録", "documentation", "archive"],
}


TOKEN_RE = re.compile(r"[A-Za-z0-9_./+-]+|[\u3040-\u30ff\u3400-\u9fffー]{2,}")


def classify_with_ai(record: dict[str, Any], text_excerpt: str = "") -> dict[str, Any]:
    """Local AI-like classifier.

    This is intentionally deterministic and offline. It creates a searchable
    semantic layer from file names, paths, metadata, OCR snippets, and initial
    rule classifications. A later OpenAI-powered classifier can replace this
    function without changing the scanner or database layout.
    """
    haystack = " ".join(
        [
            str(record.get("file_name", "")),
            str(record.get("parent_folder", "")),
            str(record.get("full_path", "")),
            str(record.get("media_type_candidate", "")),
            text_excerpt[:2000],
        ]
    )
    tag_source = " ".join(
        [
            str(record.get("file_name", "")),
            str(record.get("parent_folder", "")),
            text_excerpt[:2000],
        ]
    )
    folded = haystack.casefold()
    category_scores: dict[str, int] = {}

    for category, keywords in CATEGORY_RULES.items():
        score = sum(1 for keyword in keywords if keyword.casefold() in folded)
        if score:
            category_scores[category] = score

    media_type = str(record.get("media_type_candidate") or "unknown")
    if media_type == "image":
        category_scores["photo_documentation"] = category_scores.get("photo_documentation", 0) + 1
    elif media_type == "video":
        category_scores["video_documentation"] = category_scores.get("video_documentation", 0) + 1
    elif media_type in {"audio", "audio_project"}:
        category_scores["audio_music"] = category_scores.get("audio_music", 0) + 1

    if category_scores:
        category = sorted(category_scores.items(), key=lambda item: (-item[1], item[0]))[0][0]
        confidence = min(0.95, 0.45 + category_scores[category] * 0.15)
    else:
        category = _fallback_category(media_type)
        confidence = 0.35 if category != "unknown" else 0.1

    generated_tags = set(record.get("tag_candidates") or [])
    generated_tags.update(record.get("project_candidates") or [])
    generated_tags.update(record.get("person_candidates") or [])
    generated_tags.update(record.get("organization_candidates") or [])
    generated_tags.update(record.get("event_candidates") or [])
    generated_tags.add(category)

    for tag, keywords in TAG_PATTERNS.items():
        if any(keyword.casefold() in folded for keyword in keywords):
            generated_tags.add(tag)

    generated_tags.update(_extract_name_tokens(tag_source))

    return {
        "ai_category": category,
        "ai_subcategory": _subcategory(category, media_type),
        "ai_confidence": round(confidence, 2),
        "ai_reason": _reason(category, media_type, bool(text_excerpt)),
        "generated_tags": sorted(tag for tag in generated_tags if tag),
        "classifier": "local_rules_v1",
    }


def _fallback_category(media_type: str) -> str:
    return {
        "document": "document",
        "spreadsheet": "spreadsheet",
        "presentation": "presentation",
        "image": "photo_documentation",
        "video": "video_documentation",
        "audio": "audio_music",
        "audio_project": "audio_music",
        "video_project": "video_documentation",
    }.get(media_type, "unknown")


def _subcategory(category: str, media_type: str) -> str:
    if category in {"photo_documentation", "video_documentation", "audio_music"}:
        return media_type
    return category


def _reason(category: str, media_type: str, has_text: bool) -> str:
    source = "filename/path/metadata"
    if has_text:
        source += "/text"
    return f"{category} inferred from {source}; media_type={media_type}"


def _extract_name_tokens(text: str) -> set[str]:
    tokens = set()
    for token in TOKEN_RE.findall(text):
        clean = token.strip("._-/+ ")
        if 2 <= len(clean) <= 40 and not clean.isdigit():
            tokens.add(clean)
    return tokens
