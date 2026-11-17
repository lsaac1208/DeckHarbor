"""已安装游戏索引（installed_games.json 持久化 + 迁移 + callable 逻辑）。"""

from __future__ import annotations

import logging
import os
from typing import Optional

_LOGGER = logging.getLogger("NewFreedeck")


# ═══════════════════════════════════════════════
# JSON 持久化
# ═══════════════════════════════════════════════

def _json_path() -> str:
    home = os.environ.get("DECKY_USER_HOME", os.path.expanduser("~"))
    return os.path.join(home, ".local", "share", "NewFreedeck", "installed_games.json")


def load_installed_games() -> list[dict]:
    """读取 installed_games.json，返回游戏列表（不含 _migrated 标记）。"""
    path = _json_path()
    if not os.path.isfile(path):
        return []
    try:
        import json as _json
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return [g for g in data if isinstance(g, dict) and not g.get("_migrated")]
    except Exception:
        return []


def save_installed_games(games: list[dict]):
    """写入 installed_games.json。"""
    import json as _json
    path = _json_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from library_state import atomic_json
    from pathlib import Path
    atomic_json(Path(path), games)


def is_migrated() -> bool:
    """检查是否已完成首次迁移。

    修正：正式条目（add_installed_game 写入）没有 _migrated 标记，
    原实现导致永远 False、每次 list 都走迁移分支、JSON 旧记录被无视。
    改为：JSON 中存在任何有效游戏记录即视为已迁移。
    """
    path = _json_path()
    if not os.path.isfile(path):
        return False
    try:
        import json as _json
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return isinstance(data, list)
    except Exception:
        return False


def add_installed_game(game: dict):
    """添加一个已安装游戏到 JSON（去重）。"""
    import datetime as _dt
    # 兼容前端渲染：预览页读 game.title，但下载写入的是 title_zh/title_en
    game = dict(game)
    if not game.get("title") and (game.get("title_zh") or game.get("title_en")):
        game["title"] = game.get("title_zh") or game.get("title_en") or ""
    games = load_installed_games()
    existing = {g.get("game_id"): i for i, g in enumerate(games)}
    game["installed_at"] = _dt.datetime.now().isoformat()
    if game.get("game_id") in existing:
        old = games[existing[game["game_id"]]]
        game["installed_at"] = old.get("installed_at") or game["installed_at"]
        games[existing[game["game_id"]]] = {**old, **game}
    else:
        games.append(game)
    save_installed_games(games)


def remove_installed_game(game_id: str) -> bool:
    """从 JSON 中移除一个游戏。"""
    games = load_installed_games()
    new_games = [g for g in games if g.get("game_id") != game_id]
    if len(new_games) == len(games):
        return False
    save_installed_games(new_games)
    return True


def validate_games(games: list[dict]) -> list[dict]:
    """过滤掉 install_dir 不存在、为空目录、或已被手动删除的游戏。

    不修改 JSON 文件，仅过滤返回。
    """
    valid = []
    for g in games:
        install_dir = str(g.get("install_dir", ""))
        if g.get("rom_path") and not os.path.isfile(str(g["rom_path"])):
            valid.append(dict(g, available=False))
            continue
        # 目录不存在 → 保留记录并标注，可在库内修复或移除
        if not install_dir or not os.path.isdir(install_dir):
            valid.append(dict(g, available=False))
            continue
        # Preserve disconnected/empty storage records so users can repair or remove them.
        try:
            if not any(os.scandir(install_dir)):
                valid.append(dict(g, available=False))
                continue
        except Exception:
            valid.append(dict(g, available=False))
            continue
        valid.append(dict(g, available=True))
    return valid


# ═══════════════════════════════════════════════
# 首次迁移
# ═══════════════════════════════════════════════

async def migrate_from_shortcuts(
    completed_downloads: list[dict],
    shortcuts: list[dict],
) -> list[dict]:
    """从 shortcuts.vdf + 已完成下载任务中迁移 installed_games.json。

    如果 _downloads 为空（插件重启后），则改为从 shortcuts.vdf
    直接提取安装目录在 FreedeckGames 下的快捷方式。
    """
    migrated = []

    if completed_downloads:
        # 有下载任务 → 精确匹配
        for task in completed_downloads:
            raw_appid = int(task.get("steam_appid", 0) or 0)
            # 归一化：将 signed int32 转为 unsigned runtime appid
            appid = (raw_appid & 0xffffffff) | 0x80000000 if raw_appid else 0
            game_title = str(task.get("game_title", ""))
            extract_output = str(task.get("extract_output", ""))
            matched = None

            if appid:
                for sc in shortcuts:
                    if int(sc.get("_appid", 0) or 0) == appid:
                        matched = sc; break

            if not matched and game_title:
                for sc in shortcuts:
                    if str(sc.get("AppName", "")) == game_title:
                        matched = sc; break

            if not matched and extract_output:
                for sc in shortcuts:
                    sc_dir = os.path.dirname(str(sc.get("Exe", "")))
                    if sc_dir and sc_dir.startswith(extract_output):
                        matched = sc; break

            migrated.append({
                "game_id": str(task.get("game_id", "")),
                "title": game_title,
                "steam_appid": appid,
                "install_dir": extract_output,
                "size_bytes": int(task.get("file_size", 0) or 0),
                "cover_url": str(task.get("cover_url", "")),
            })
    else:
        # 无下载任务（插件重启后）→ 从 shortcuts.vdf 中提取
        # 过滤：仅安装目录在 FreedeckGames 下的快捷方式
        home = os.path.expanduser("~")
        freedeck_root = os.path.join(home, "FreedeckGames")
        seen_appids = set()
        for sc in shortcuts:
            raw_appid = int(sc.get("_appid", 0) or 0)
            if not raw_appid or raw_appid in seen_appids:
                continue
            # 将 signed int32 转为 unsigned runtime appid
            appid = (raw_appid & 0xffffffff) | 0x80000000
            exe = str(sc.get("Exe", ""))
            install_dir = os.path.dirname(exe) if exe else ""
            # 仅过滤 FreedeckGames 目录下的游戏
            if not install_dir or not install_dir.startswith(freedeck_root):
                continue
            seen_appids.add(appid)
            # 估算目录大小
            size_bytes = 0
            try:
                import subprocess
                r = subprocess.run(
                    ["du", "-sb", install_dir],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    size_bytes = int(r.stdout.split()[0])
            except Exception:
                pass
            migrated.append({
                "game_id": str(appid),
                "title": str(sc.get("AppName", "")),
                "steam_appid": appid,
                "install_dir": install_dir,
                "size_bytes": size_bytes,
                "cover_url": "",
            })

    # 保存并写入迁移标记
    migrated.append({"_migrated": True})
    save_installed_games(migrated)
    count = len([g for g in migrated if not g.get("_migrated")])
    _LOGGER.info("installed_games migration: %d games saved", count)
    return [g for g in migrated if not g.get("_migrated")]


def _load_raw() -> list[dict]:
    """读取 installed_games.json 原始数据（含 _migrated 标记）。"""
    path = _json_path()
    if not os.path.isfile(path):
        return []
    try:
        import json as _json
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return []


def _migrate_existing(raw_games: list[dict]) -> list[dict]:
    """将旧版 installed_games.json（无游戏元数据键）转为新版。"""
    fixed = []
    changed = False
    for g in raw_games:
        if isinstance(g, dict):
            fixed.append(g)
        else:
            changed = True
    if changed and fixed:
        save_installed_games(fixed)
    return [g for g in fixed if not g.get("_migrated")]


# ═══════════════════════════════════════════════
# Callable 逻辑
# ═══════════════════════════════════════════════

def find_by_appid(appid: int) -> Optional[dict]:
    """通过 steam_appid 查找已安装游戏（用于云存档自动触发）。

    遍历 installed_games.json，O(n) 时间复杂度。
    游戏数量通常 < 100，性能无影响。
    """
    if not appid:
        return None
    for g in load_installed_games():
        if int(g.get("steam_appid", 0) or 0) == appid:
            return g
    return None


def find_by_game_id(game_id: str) -> Optional[dict]:
    """通过 game_id 查找已安装游戏。"""
    if not game_id:
        return None
    for g in load_installed_games():
        if g.get("game_id") == game_id:
            return g
    return None


def get_managed_game_ids() -> set[str]:
    """获取所有通过插件安装的 game_id 集合（用于云存档下载过滤）。"""
    return {g["game_id"] for g in load_installed_games() if g.get("game_id")}


# game_id 前缀 → platform 映射（用于补全旧数据）
_GAME_ID_TO_PLATFORM = {
    "PS1": "ps1", "PS2": "ps2", "PS3": "ps3", "PSP": "psp",
    "3DS": "3ds", "GBA": "gba", "NDS": "nds",
    "FC": "fc", "MD": "md", "SWITCH": "switch", "NS": "switch",
}


def _infer_platform(game_id: str) -> str:
    """从 game_id 前缀推断 platform。"""
    gid = str(game_id or "").upper().strip()
    for prefix, plat in _GAME_ID_TO_PLATFORM.items():
        if gid.startswith(prefix):
            return plat
    return ""


def ensure_platform_field(games: list[dict]) -> list[dict]:
    """为没有 platform 字段的旧数据自动补全 platform 和 game_type；并兜底 title。"""
    changed = False
    for g in games:
        if not g.get("title") and (g.get("title_zh") or g.get("title_en")):
            g["title"] = g.get("title_zh") or g.get("title_en") or ""
            changed = True
        if g.get("platform"):
            continue
        gid = str(g.get("game_id", ""))
        plat = _infer_platform(gid)
        if plat:
            g["platform"] = plat
            if not g.get("game_type"):
                g["game_type"] = "emulator_rom"
            changed = True
    if changed:
        save_installed_games(games)
    return games


def _repair_missing_appids(games: list[dict]) -> list[dict]:
    """对于 steam_appid=0 的游戏，从 shortcuts.vdf 反查真实 appid。

    匹配策略：按 install_dir 匹配 shortcuts.vdf 中 Exe 所在目录。
    """
    needs_repair = [g for g in games if int(g.get("steam_appid", 0) or 0) == 0 and g.get("install_dir")]
    if not needs_repair:
        return games

    # 直接读取 shortcuts.vdf（与 main.py _load_steam_shortcuts 逻辑一致）
    shortcuts: list[dict] = []
    try:
        home = os.path.expanduser("~")
        steam_root = os.path.join(home, ".local", "share", "Steam")
        # 查找 userdata 目录
        userdata_base = os.path.join(steam_root, "userdata")
        if os.path.isdir(userdata_base):
            for uid in os.listdir(userdata_base):
                sp = os.path.join(userdata_base, str(uid), "config", "shortcuts.vdf")
                if os.path.isfile(sp):
                    from vdf import binary_load
                    with open(sp, "rb") as f:
                        data = binary_load(f)
                    for _key, entry in data.get("shortcuts", {}).items():
                        if isinstance(entry, dict):
                            raw = int(entry.get("appid", 0) or 0)
                            entry["_appid"] = raw
                            shortcuts.append(entry)
                    break  # 只取第一个有效的 userdata
    except Exception:
        _LOGGER.warning("_repair_missing_appids: cannot load shortcuts.vdf")
        return games

    if not shortcuts:
        return games

    repaired = 0
    for g in needs_repair:
        install_dir = str(g.get("install_dir", "")).rstrip("/")
        if not install_dir:
            continue
        for sc in shortcuts:
            sc_exe = str(sc.get("Exe", ""))
            sc_dir = os.path.dirname(sc_exe).rstrip("/") if sc_exe else ""
            if sc_dir and sc_dir == install_dir:
                raw_appid = int(sc.get("_appid", 0) or 0)
                if raw_appid:
                    appid = (raw_appid & 0xffffffff) | 0x80000000
                    g["steam_appid"] = appid
                    repaired += 1
                    _LOGGER.info("_repair_missing_appids: fixed %s → appid=%d", g.get("title", ""), appid)
                break

    if repaired > 0:
        save_installed_games(games)
        _LOGGER.info("_repair_missing_appids: repaired %d games", repaired)

    return games


async def handle_list_installed_games(
    plugin_instance,  # Plugin class instance for _downloads
    _load_steam_shortcuts_fn,
) -> dict:
    """list_installed_games callable 逻辑。"""
    _LOGGER.info("Decky callable: list_installed_games")

    if not is_migrated():
        _LOGGER.info("list_installed_games: running first-time migration")
        try:
            import asyncio
            shortcuts = await asyncio.to_thread(_load_steam_shortcuts_fn)
            completed = [
                t for t in plugin_instance._downloads.values()
                if t.get("import_status") == "complete" and t.get("steam_appid")
            ]
            games = await migrate_from_shortcuts(completed, shortcuts)
        except Exception as e:
            _LOGGER.warning("list_installed_games: migration failed: %s", e)
            games = load_installed_games()
    else:
        games = load_installed_games()

    # 补全旧数据的 platform 字段
    games = ensure_platform_field(games)

    # 过滤无效目录（已被手动删除的）
    games = validate_games(games)

    # 修复 steam_appid=0 的游戏（从 shortcuts.vdf 反查真实 appid）
    games = _repair_missing_appids(games)

    return {"status": "success", "data": {"games": games}}


async def handle_uninstall_game(
    game_id: str,
    protected_dirs: list[str] | None = None,
) -> dict:
    """uninstall_game callable 逻辑。"""
    if not game_id:
        return {"status": "error", "message": "缺少 game_id 参数"}

    _LOGGER.info("Decky callable: uninstall_game game_id=%s", game_id)
    games = load_installed_games()
    target = next((g for g in games if g.get("game_id") == game_id), None)
    if not target:
        return {"status": "error", "message": "未找到该游戏记录"}

    if target.get("managed_files") is False:
        return {"status": "error", "message": "本地关联游戏请使用移除记录，原文件不会删除"}
    install_dir = str(target.get("install_dir", ""))
    home = os.environ.get("DECKY_USER_HOME", os.path.expanduser("~"))
    root = os.path.realpath(install_dir) if install_dir else ""
    protected = ["/", "/home", home, os.path.join(home, "FreedeckGames"),
                 os.path.join(home, "FreedeckGames", "freedeckInstall"),
                 os.path.join(home, "FreedeckGames", "Emulators")] + (protected_dirs or [])
    if not root or not os.path.isabs(install_dir) or os.path.islink(install_dir):
        return {"status": "error", "message": "安装目录无效，不能自动卸载"}
    if any(os.path.commonpath([root, os.path.realpath(p)]) == root for p in protected if p):
        return {"status": "error", "message": "该记录指向共享目录，不能整体删除，请手动管理游戏文件"}
    for game in games:
        other = game.get("install_dir")
        if game is not target and other:
            other = os.path.realpath(str(other))
            if os.path.commonpath([root, other]) in (root, other):
                return {"status": "error", "message": "该目录与其他游戏共用，不能整体删除"}
    if install_dir and os.path.isdir(install_dir):
        try:
            import shutil
            import asyncio
            await asyncio.to_thread(shutil.rmtree, install_dir)
            _LOGGER.info("uninstall_game: removed dir %s", install_dir)
        except Exception as e:
            _LOGGER.warning("uninstall_game: failed to remove dir %s: %s", install_dir, e)
            return {"status": "error", "message": f"删除游戏文件失败：{e}"}

    remove_installed_game(game_id)
    return {"status": "success", "data": {"ok": True}}
