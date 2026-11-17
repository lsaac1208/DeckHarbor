"""稍后再玩收藏（play_later.json 本地持久化）。

条目模型移植自老插件 TianyiPlayLaterItem：
  item_id / game_id / title / platform / payload / added_at / updated_at
payload 为完整目录条目快照（供 tab 页卡片直接渲染）。

存储：本地 JSON（~/.local/share/NewFreedeck/play_later.json），
范式与 installed_games.py 保持一致（无云同步依赖）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger("NewFreedeck")


# ═══════════════════════════════════════════════
# JSON 持久化
# ═══════════════════════════════════════════════

def _json_path() -> str:
    home = os.environ.get("DECKY_USER_HOME", os.path.expanduser("~"))
    return os.path.join(home, ".local", "share", "NewFreedeck", "play_later.json")


def load_play_later_items() -> List[Dict[str, Any]]:
    """读取 play_later.json，返回条目 record 列表。"""
    path = _json_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict) and r.get("item_id")]
        return []
    except Exception:
        _LOGGER.exception("load play_later.json failed")
        return []


def _save_play_later_items(items: List[Dict[str, Any]]) -> None:
    """写入 play_later.json（原子写临时文件后替换）。"""
    path = _json_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        _LOGGER.exception("save play_later.json failed")
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _now_ts() -> int:
    return int(time.time())


# ═══════════════════════════════════════════════
# 条目快照归一化 + 稳定 ID（移植自老插件）
# ═══════════════════════════════════════════════

def normalize_play_later_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """规范化"稍后再玩"条目快照。"""
    raw = dict(payload or {}) if isinstance(payload, dict) else {}
    normalized: Dict[str, Any] = {}

    string_fields = (
        "source_id", "source_name", "source_url", "source_item_id", "action_url",
        "acquisition", "acquisition_note", "summary", "license", "creator", "verified_at", "server", "genre",
        "game_id",
        "title",
        "title_zh",
        "title_en",
        "version",
        "file_name",
        "platform",
        "category_parent",
        "categories",
        "down_url",
        "pwd",
        "archive_password",
        "openpath",
        "open_path",
        "size_text",
        "install_size_text",
        "cover_url",
        "square_cover_url",
        "cover",
        "image_url",
        "image",
        "pic_url",
        "pic",
        "thumbnail",
        "poster",
        "proxy_cover_url",
        "proxy_square_cover_url",
        "remote_cover_url",
        "remote_square_cover_url",
        "display_cover_url",
        "preview_cover_url",
        "play_later_id",
    )
    for key in string_fields:
        normalized[key] = str(raw.get(key, "") or "").strip()

    normalized["size_bytes"] = max(0, _to_int(raw.get("size_bytes", 0), 0))
    normalized["install_size_bytes"] = max(0, _to_int(raw.get("install_size_bytes", 0), 0))
    normalized["app_id"] = max(0, _to_int(raw.get("app_id", 0), 0))

    cover_candidates_raw = raw.get("cover_candidates")
    cover_candidates: List[str] = []
    if isinstance(cover_candidates_raw, list):
        for item in cover_candidates_raw:
            text = str(item or "").strip()
            if text:
                cover_candidates.append(text)
    normalized["cover_candidates"] = cover_candidates

    return normalized


def build_play_later_item_id(payload: Optional[Dict[str, Any]]) -> str:
    """生成稳定的"稍后再玩"条目 ID。"""
    normalized = normalize_play_later_payload(payload)
    platform = str(normalized.get("platform", "") or "").strip().lower()
    game_id = str(normalized.get("game_id", "") or "").strip().lower()
    version = str(normalized.get("version", "") or "").strip().lower()
    down_url = str(normalized.get("down_url", "") or "").strip().lower()
    open_path = str(normalized.get("open_path", "") or normalized.get("openpath", "") or "").strip().lower()
    file_name = str(normalized.get("file_name", "") or "").strip().lower()
    title = str(normalized.get("title", "") or "").strip().lower()
    raw = "||".join([platform, game_id, version, down_url, open_path, file_name, title])
    if not raw.strip("|"):
        raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


# ═══════════════════════════════════════════════
# 增删改查
# ═══════════════════════════════════════════════

def play_later_item_to_view(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把 record 转成前端卡片可用的目录条目（payload 平铺 + 收藏元数据）。"""
    if not isinstance(record, dict):
        return {}
    payload = dict(record.get("payload") or {}) if isinstance(record.get("payload"), dict) else {}
    view: Dict[str, Any] = dict(payload)
    view["play_later_id"] = str(record.get("item_id", "") or "").strip()
    view["play_later_added_at"] = _to_int(record.get("added_at"), 0)
    view["play_later_updated_at"] = _to_int(record.get("updated_at"), 0)
    view["title"] = str(record.get("title", "") or payload.get("title", "") or "").strip()
    view["game_id"] = str(record.get("game_id", "") or payload.get("game_id", "") or "").strip()
    view["platform"] = str(record.get("platform", "") or payload.get("platform", "") or "").strip()
    return view


def upsert_play_later_item(payload: Optional[Dict[str, Any]]) -> tuple:
    """新增或更新稍后再玩条目。返回 (added: bool, item_view: dict)。"""
    normalized = normalize_play_later_payload(payload)
    if not normalized.get("game_id") and not normalized.get("title"):
        raise ValueError("稍后再玩条目缺少游戏标识")
    if not normalized.get("down_url"):
        raise ValueError("稍后再玩条目缺少分享链接")

    item_id = normalized.get("play_later_id") or build_play_later_item_id(normalized)
    normalized["play_later_id"] = item_id
    now = _now_ts()
    next_record: Dict[str, Any] = {
        "item_id": item_id,
        "game_id": normalized.get("game_id", ""),
        "title": normalized.get("title", "") or normalized.get("title_zh", "") or normalized.get("title_en", ""),
        "platform": normalized.get("platform", ""),
        "payload": normalized,
        "added_at": now,
        "updated_at": now,
    }

    items = load_play_later_items()
    # 平铺 store 的 record 字段在 payload 里（record title 优先）
    # title 覆盖：record.title 为空时回退 payload.title
    for idx, current in enumerate(items):
        if str(current.get("item_id", "") or "").strip() != item_id:
            continue
        next_record["added_at"] = max(0, _to_int(current.get("added_at"), now)) or now
        next_record["title"] = next_record["title"] or str(current.get("title", "") or "").strip()
        next_record["game_id"] = next_record["game_id"] or str(current.get("game_id", "") or "").strip()
        next_record["platform"] = next_record["platform"] or str(current.get("platform", "") or "").strip()
        items[idx] = next_record
        _save_play_later_items(items)
        return False, play_later_item_to_view(next_record)

    items.insert(0, next_record)
    _save_play_later_items(items)
    return True, play_later_item_to_view(next_record)


def remove_play_later_item(item_id: str) -> Optional[Dict[str, Any]]:
    """移除稍后再玩条目，返回被移除的 record（不存在则 None）。"""
    target = str(item_id or "").strip()
    if not target:
        return None
    items = load_play_later_items()
    for idx, current in enumerate(items):
        if str(current.get("item_id", "") or "").strip() != target:
            continue
        removed = items.pop(idx)
        _save_play_later_items(items)
        return removed
    return None