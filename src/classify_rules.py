from __future__ import annotations

import re
from pathlib import Path
from typing import Any


INITIAL_KEYWORDS = [
    "Loxodonta",
    "ロクソドンタ",
    "ロクソ",
    "フェスティバーロ",
    "ロクソフェス",
    "ダンスの時間",
    "TACT",
    "TACT/FEST",
    "TACT FEST",
    "国際児童青少年舞台芸術フェスティバル",
    "阿倍野区民センター",
    "指定管理",
    "Osaka Fringe",
    "大阪フリンジ",
    "大阪文化万博",
    "Osaka Culture Quest",
    "OSAKA CULTURE QUEST",
    "大阪文化クエスト",
    "なにわ大賞",
    "なにわ名物",
    "KIO",
    "中立",
    "YouTube",
    "Logic",
    "Final Cut",
    "FCPX",
    "タニノクロウ",
    "庭劇団ペニノ",
]


PROJECT_KEYWORDS = {
    "Loxodonta": ["Loxodonta", "ロクソドンタ", "ロクソ"],
    "フェスティバーロ": ["フェスティバーロ"],
    "ロクソフェス": ["ロクソフェス"],
    "ダンスの時間": ["ダンスの時間"],
    "TACT/FEST": ["TACT/FEST", "TACT"],
    "阿倍野区民センター": ["阿倍野区民センター", "阿倍野区民センター指定管理", "Abeno Civic Center", "阿倍野"],
    "Osaka Fringe / 大阪文化万博": ["Osaka Fringe", "大阪フリンジ", "大阪文化万博"],
    "Osaka Culture Quest": ["Osaka Culture Quest", "OSAKA CULTURE QUEST", "大阪文化クエスト"],
    "なにわ大賞": ["なにわ大賞"],
    "なにわ名物": ["なにわ名物"],
    "中立": ["中立"],
}


PERSON_KEYWORDS = {
    "タニノクロウ": ["タニノクロウ"],
}


ORGANIZATION_KEYWORDS = {
    "庭劇団ペニノ": ["庭劇団ペニノ"],
    "KIO": ["KIO"],
}


EVENT_KEYWORDS = {
    "TACT/FEST": ["TACT/FEST"],
    "阿倍野区民センター": ["阿倍野区民センター", "阿倍野区民センター指定管理"],
    "Osaka Fringe / 大阪文化万博": ["Osaka Fringe", "大阪フリンジ", "大阪文化万博"],
    "なにわ大賞": ["なにわ大賞"],
}


MEDIA_TYPE_BY_EXTENSION = {
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".heic": "image",
    ".tif": "image",
    ".tiff": "image",
    ".pdf": "document",
    ".txt": "document",
    ".md": "document",
    ".doc": "document",
    ".docx": "document",
    ".xls": "spreadsheet",
    ".xlsx": "spreadsheet",
    ".csv": "spreadsheet",
    ".numbers": "spreadsheet",
    ".ppt": "presentation",
    ".pptx": "presentation",
    ".key": "presentation",
    ".mov": "video",
    ".mp4": "video",
    ".m4v": "video",
    ".avi": "video",
    ".wmv": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".aif": "audio",
    ".aiff": "audio",
    ".m4a": "audio",
    ".logicx": "audio_project",
    ".fcpxml": "video_project",
}


TOOL_TAGS = {
    "YouTube": ["YouTube"],
    "Logic": ["Logic"],
    "Final Cut": ["Final Cut"],
    "FCPX": ["FCPX"],
}


YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


def classify_file(record: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(
        [
            str(record.get("file_name", "")),
            str(record.get("parent_folder", "")),
            str(record.get("full_path", "")),
        ]
    )
    path = Path(str(record.get("full_path", "")))
    extension = str(record.get("extension", "")).lower()

    projects = _match_keyword_groups(text, PROJECT_KEYWORDS)
    persons = _match_keyword_groups(text, PERSON_KEYWORDS)
    organizations = _match_keyword_groups(text, ORGANIZATION_KEYWORDS)
    events = _match_keyword_groups(text, EVENT_KEYWORDS)
    years = sorted(set(YEAR_RE.findall(text)))
    media_type = _guess_media_type(extension, text)
    tags = _build_tags(text, extension, media_type, projects, events)
    importance = _guess_importance(text, path, media_type, record.get("size_bytes"))

    return {
        "project_candidates": projects,
        "person_candidates": persons,
        "organization_candidates": organizations,
        "event_candidates": events,
        "year_candidates": years,
        "media_type_candidate": media_type,
        "importance_candidate": importance,
        "tag_candidates": tags,
    }


def _match_keyword_groups(text: str, groups: dict[str, list[str]]) -> list[str]:
    matched = []
    text_folded = text.casefold()
    for label, keywords in groups.items():
        if any(keyword.casefold() in text_folded for keyword in keywords):
            matched.append(label)
    return matched


def _guess_media_type(extension: str, text: str) -> str:
    if extension in MEDIA_TYPE_BY_EXTENSION:
        return MEDIA_TYPE_BY_EXTENSION[extension]

    text_folded = text.casefold()
    if "youtube" in text_folded:
        return "video"
    if "final cut" in text_folded or "fcpx" in text_folded:
        return "video_project"
    if "logic" in text_folded:
        return "audio_project"
    return "unknown"


def _build_tags(
    text: str,
    extension: str,
    media_type: str,
    projects: list[str],
    events: list[str],
) -> list[str]:
    tags = set(projects + events)
    if media_type != "unknown":
        tags.add(media_type)
    if extension:
        tags.add(extension.lstrip("."))

    text_folded = text.casefold()
    for label, keywords in TOOL_TAGS.items():
        if any(keyword.casefold() in text_folded for keyword in keywords):
            tags.add(label)

    for keyword in INITIAL_KEYWORDS:
        if keyword.casefold() in text_folded:
            tags.add(keyword)

    return sorted(tags)


def _guess_importance(
    text: str,
    path: Path,
    media_type: str,
    size_bytes: Any,
) -> str:
    text_folded = text.casefold()
    important_words = [
        "final",
        "master",
        "完成",
        "最終",
        "提出",
        "契約",
        "請求",
        "報告",
        "記録",
        "写真",
        "映像",
    ]
    draft_words = ["draft", "下書き", "仮", "temp", "tmp", "コピー"]

    if any(word.casefold() in text_folded for word in important_words):
        return "high"
    if any(word.casefold() in text_folded for word in draft_words):
        return "low"
    if media_type in {"video", "audio_project", "video_project"}:
        return "medium"

    try:
        if int(size_bytes or 0) > 500 * 1024 * 1024:
            return "medium"
    except (TypeError, ValueError):
        pass

    if len(path.parts) <= 3:
        return "medium"
    return "unknown"
