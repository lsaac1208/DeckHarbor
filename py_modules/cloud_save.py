"""NewFreedeck — 云存档引擎

SaveScanner: 识别已安装游戏的存档路径
  - 模拟器: 硬编码路径映射（10 个平台）
  - PC 游戏: Ludusavi 清单（10,000+ 游戏）

CloudSyncEngine: 与天翼云盘同步存档
  - tar.gz 打包/解包
  - 天翼云盘直传（上传/下载/列表）
  - 本地 JSON 同步历史
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tarfile
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional

import aiohttp
import ssl


# ── 轻量 XML 解析器（替代 xml.etree.ElementTree，避免 Decky Python 环境缺少该模块）──

def _xml_findall(xml_text: str, tag: str) -> list[str]:
    """返回 XML 中所有 <tag>...</tag> 块的纯文本（不嵌套同名标签时可用）。"""
    import re as _re
    # 匹配 <tag> 或 <tag attr="..."> 到 </tag>
    pattern = _re.compile(
        r'<' + _re.escape(tag) + r'[^>]*>(.*?)</' + _re.escape(tag) + r'>',
        _re.DOTALL | _re.IGNORECASE,
    )
    return [m.group(1) for m in pattern.finditer(xml_text)]


def _xml_child_text(xml_block: str, child_tag: str) -> str:
    """返回 xml_block 中 <child_tag>...</child_tag> 内的文本。"""
    import re as _re
    m = _re.search(
        r'<' + _re.escape(child_tag) + r'[^>]*>(.*?)</' + _re.escape(child_tag) + r'>',
        xml_block,
        _re.DOTALL | _re.IGNORECASE,
    )
    return (m.group(1) or "").strip() if m else ""


def _xml_text(xml_text: str, tag: str) -> str:
    """返回 XML 中第一个 <tag>...</tag> 内的文本。"""
    return _xml_child_text(xml_text, tag)


def _unescape_url(url: str) -> str:
    """解码 URL 中的 HTML 实体（天翼云返回 &amp; → &），否则下载请求失败。"""
    import html
    return html.unescape(url)


def _ssl_context() -> ssl.SSLContext:
    """Steam Deck 兼容的 SSL 上下文（跳过证书验证，避免 CA 缺失导致 SSL 错误）。"""
    try:
        from cloud._utils import steamos_ssl_context
        return steamos_ssl_context()
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

# ── 模拟器存档路径映射 ──────────────────

EMULATOR_SAVE_PATHS: Dict[str, list[str]] = {
    "switch": [
        os.path.expanduser("~/.local/share/eden/nand/user/save"),
        os.path.expanduser("~/.local/share/eden/saves"),
    ],
    "ps1": [
        os.path.expanduser("~/.local/share/duckstation/memcards"),
    ],
    "ps2": [
        os.path.expanduser("~/.config/PCSX2/memcards"),
        os.path.expanduser("~/.local/share/PCSX2/memcards"),
    ],
    "ps3": [
        os.path.expanduser("~/.config/rpcs3/dev_hdd0/home"),
        os.path.expanduser("~/.local/share/rpcs3/dev_hdd0/home"),
    ],
    "psp": [
        os.path.expanduser("~/.config/ppsspp/PSP/SAVEDATA"),
        os.path.expanduser("~/.local/share/ppsspp/PSP/SAVEDATA"),
    ],
    "3ds": [
        os.path.expanduser("~/.local/share/lime3ds-emu"),
        os.path.expanduser("~/.config/lime3ds-emu"),
    ],
    "gba": [
        os.path.expanduser("~/.config/mgba/saves"),
        os.path.expanduser("~/.local/share/mgba/saves"),
    ],
    "nds": [
        os.path.expanduser("~/.config/melonDS/saves"),
        os.path.expanduser("~/.local/share/melonDS/saves"),
    ],
    "fc": [
        os.path.expanduser("~/.local/share/nestopia/saves"),
        os.path.expanduser("~/.config/nestopia/saves"),
    ],
    "md": [
        os.path.expanduser("~/.local/share/blastem/saves"),
        os.path.expanduser("~/.config/blastem/saves"),
    ],
}


def _get_emu_save_paths(platform: str, emulator_install_base: str | None = None) -> list[str]:
    """获取模拟器的所有存档路径（标准路径 + 便携模式路径）。

    便携模式路径根据实际的模拟器安装目录动态计算。
    """
    paths = list(EMULATOR_SAVE_PATHS.get(platform, []))

    # 便携模式路径：{install_base}/Emulators/{platform}/{save_subdirs}
    if emulator_install_base:
        base = os.path.join(os.path.expanduser(emulator_install_base), "Emulators", platform)
        portable_subdirs = {
            "ps1": ["memcards"],
            "ps2": ["memcards"],
            "ps3": ["dev_hdd0", "dev_hdd0/home"],
            "psp": ["PSP", "PSP/SAVEDATA"],
            "switch": ["nand/user/save", "saves"],
        }
        for sub in portable_subdirs.get(platform, []):
            p = os.path.join(base, sub)
            if os.path.isdir(p) and p not in paths:
                paths.append(p)

    return paths


def _state_dir() -> str:
    home = os.path.expanduser("~")
    d = os.path.join(home, ".local", "share", "NewFreedeck")
    os.makedirs(d, exist_ok=True)
    return d


def _history_path() -> str:
    return os.path.join(_state_dir(), "cloud_save_history.json")


# ═══════════════════════════════════════════════
# SaveScanner
# ═══════════════════════════════════════════════

class SaveScanner:
    """发现已安装游戏的存档路径。"""

    # 清单内置于插件 defaults/ 目录（JSON 预编译版，秒加载）
    BUNDLED_JSON = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "defaults", "ludusavi_manifest.json",
    )
    BUNDLED_YAML = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "defaults", "ludusavi_manifest.yaml",
    )

    def __init__(self):
        self._manifest: dict | None = None

    # ── 清单管理 ──────────────────────────

    async def load_manifest(self) -> dict:
        """加载 Ludusavi 清单（预编译 JSON 秒加载）。"""
        if self._manifest is not None:
            return self._manifest

        # 1. 优先读 bundled JSON（< 0.5s）
        if os.path.exists(self.BUNDLED_JSON):
            self._manifest = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _load_json_cache(self.BUNDLED_JSON)
            )
            if self._manifest:
                return self._manifest

        # 2. 回退 bundled YAML（首次 70s，自动存 JSON 缓存）
        if os.path.exists(self.BUNDLED_YAML):
            json_cache = os.path.join(_state_dir(), "ludusavi_manifest.json")
            self._manifest = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _parse_and_cache(self.BUNDLED_YAML, json_cache)
            )
            if self._manifest:
                return self._manifest

        # 3. 最后尝试旧缓存
        old_json = os.path.join(_state_dir(), "ludusavi_manifest.json")
        if os.path.exists(old_json):
            self._manifest = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _load_json_cache(old_json)
            )
            if self._manifest:
                return self._manifest

        self._manifest = {}
        return self._manifest

    # ── 模拟器存档扫描 ──────────────────

    def scan_emulator_saves(self, platform: str, install_base: str | None = None) -> list[str]:
        """扫描指定平台模拟器的存档目录。

        返回实际存在的目录路径列表。
        """
        paths = _get_emu_save_paths(platform, install_base)
        existing: list[str] = []
        for p in paths:
            if os.path.isdir(p):
                existing.append(p)
        return existing

    def scan_all_emulators(self, install_base: str | None = None) -> dict[str, list[str]]:
        """扫描所有模拟器平台的存档。

        返回 {platform: [path, ...]}。
        """
        result: dict[str, list[str]] = {}
        for platform in EMULATOR_SAVE_PATHS:
            saves = self.scan_emulator_saves(platform, install_base)
            if saves:
                result[platform] = saves
        return result

    # ── PC 游戏存档扫描 ─────────────────

    async def scan_pc_game_saves(
        self, game_title: str, steam_appid: int = 0, install_dir: str = ""
    ) -> list[str]:
        """在 Ludusavi 清单中查找 PC 游戏的存档路径。

        匹配优先级: Steam AppID > installDir 名称 > 游戏标题模糊匹配。
        """
        manifest = await self.load_manifest()
        if not manifest:
            return []

        entry = _find_game_in_manifest(game_title, steam_appid, install_dir, manifest)
        if not entry:
            return []

        return _resolve_save_paths(entry, steam_appid, install_dir)

    # ── 全量扫描 ──────────────────────────

    def scan_all(
        self,
        shortcuts: list[dict] | None = None,
        emulator_install_dirs: dict[str, str] | None = None,
    ) -> dict[str, list[str]]:
        """同步扫描部分存档（模拟器路径，不依赖网络清单）。

        返回 {game_key: [path, ...]}。
        game_key 格式: "emu:<platform>" 或 "pc:<appid>"
        """
        result: dict[str, list[str]] = {}

        # 模拟器
        for platform, paths in self.scan_all_emulators().items():
            key = f"emu:{platform}"
            result[key] = paths

        # PC 游戏（从 shortcuts.vdf 读取）
        if shortcuts:
            for sc in shortcuts:
                appid = int(sc.get("appid", 0) or 0)
                if not appid:
                    continue
                # 先扫描常见路径
                name = str(sc.get("AppName", ""))
                exe = str(sc.get("Exe", ""))
                install_dir = os.path.dirname(exe) if exe else ""
                key = f"pc:{appid}"
                pc_paths = _scan_common_pc_save_paths(name, install_dir, appid)
                if pc_paths:
                    result[key] = pc_paths

        return result


# ═══════════════════════════════════════════════
# LocalIndex — 本地同步索引
# ═══════════════════════════════════════════════

class LocalIndex:
    """管理本地存档索引 cloud_save_index.json。

    索引文件: ~/.local/share/NewFreedeck/cloud_save_index.json
    """

    def __init__(self):
        self._path = os.path.join(_state_dir(), "cloud_save_index.json")
        self._data: dict = self._load()

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_file(self, game_id: str, rel_path: str) -> dict | None:
        """获取某个文件在索引中的记录。"""
        game = self._data.get(game_id, {})
        return game.get("files", {}).get(rel_path)

    def update(self, game_id: str, files_md5: dict[str, str]):
        """更新索引中的文件 MD5 和同步时间。"""
        if game_id not in self._data:
            self._data[game_id] = {"files": {}, "last_sync_at": ""}

        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._data[game_id]["last_sync_at"] = now

        for rel, md5 in files_md5.items():
            self._data[game_id]["files"][rel] = {
                "md5": md5,
                "size": 0,  # 不存储 size（可选）
                "mtime": now,
            }

        # 清除 pending_upload
        self._data[game_id].pop("pending_upload", None)
        self._save()

    def touch(self, game_id: str, files: list[str]):
        """标记文件已检查但无变更（仅更新时间）。"""
        if game_id not in self._data:
            self._data[game_id] = {"files": {}, "last_sync_at": ""}
        self._data[game_id]["last_sync_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._save()

    def mark_pending(self, game_id: str):
        """标记游戏有待上传的存档。"""
        if game_id not in self._data:
            self._data[game_id] = {"files": {}, "last_sync_at": ""}
        self._data[game_id]["pending_upload"] = True
        self._save()

    def clear_pending(self, game_id: str):
        """清除待上传标记。"""
        if game_id in self._data:
            self._data[game_id].pop("pending_upload", None)
            self._save()

    def is_pending(self, game_id: str) -> bool:
        """检查是否有待上传的存档。"""
        return bool(self._data.get(game_id, {}).get("pending_upload"))

    def get_game_state(self, game_id: str) -> dict:
        """获取游戏的整体同步状态。"""
        return self._data.get(game_id, {})

    # ── 挂起确认（覆盖前弹窗）──────────────

    def mark_confirm(self, game_id: str, confirm_type: str,
                     cloud_files: list | None = None,
                     local_files: list | None = None):
        """标记待确认的同步任务（覆盖前弹窗）。"""
        if game_id not in self._data:
            self._data[game_id] = {"files": {}, "last_sync_at": ""}
        self._data[game_id]["pending_confirm"] = {
            "type": confirm_type,
            "cloud_files": cloud_files or [],
            "local_files": local_files or [],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._save()

    def get_confirm(self, game_id: str) -> dict | None:
        return self._data.get(game_id, {}).get("pending_confirm")

    def get_all_confirms(self) -> dict:
        out = {}
        for gid, g in self._data.items():
            pc = g.get("pending_confirm")
            if pc:
                out[gid] = pc
        return out

    def clear_confirm(self, game_id: str):
        if game_id in self._data:
            self._data[game_id].pop("pending_confirm", None)
            self._save()


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def _md5_file(path: str) -> str:
    """计算文件的 MD5 哈希（16 进制字符串）。"""
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# 运行时文件（不参与"本地新/云端新"方向判定，避免 logs/cache 干扰存档方向）
_RUNTIME_PATTERNS = ("/logs/", "/shader_cache/", "/steam_settings/", "playtime.txt", "configs.user.ini", ".log", "/debug/", "/crashes/", "autoexec.lcs")


def _is_save_file(rel: str) -> bool:
    """判断是否为存档文件（排除运行时文件）。"""
    return not any(p in rel for p in _RUNTIME_PATTERNS)


def _md5_of(v) -> str:
    """从 manifest value 提取 md5 字符串（兼容字符串或 {"md5":...} dict）。"""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return str(v.get("md5", ""))
    return ""


# ═══════════════════════════════════════════════
# CloudSyncEngine
# ═══════════════════════════════════════════════

class CloudSyncEngine:
    """与天翼云盘同步存档。

    依赖:
      - SessionManager（获取有效凭证）
      - TianyiCloudProvider（天翼云盘 API）
    """

    REMOTE_DIR = "NewFreedeck/cloud_saves"

    def __init__(self, session_mgr, tianyi_provider, plugin_logger=None):
        self._session_mgr = session_mgr
        self._tianyi = tianyi_provider
        self._log = plugin_logger

    def _info(self, msg: str, *args):
        if self._log:
            try:
                self._log.info(msg, *args)
            except Exception:
                pass

    # ── 天翼云盘网盘操作 ─────────────────

    async def _get_credential(self):
        """获取有效凭证。"""
        from cloud import CloudProviderId
        return await self._session_mgr.ensure_valid(self._tianyi)

    async def _ensure_remote_dir(self) -> str:
        """确保云盘上存档目录存在，返回最内层目录的 file_id。"""
        cred = await self._get_credential()
        parent_id = "-11"  # 天翼云盘根目录

        for segment in ["NewFreedeck", "cloud_saves"]:
            dirs = await self._tianyi_list_dir(cred, parent_id)
            found = next((d for d in dirs if d.get("name") == segment), None)
            if found:
                parent_id = found["file_id"]
            else:
                created = await self._tianyi_create_dir(cred, segment, parent_id)
                parent_id = created["file_id"]

        return parent_id

    async def upload(self, local_paths: list[str], label: str = "backup") -> dict:
        """打包并上传存档到天翼云盘。

        参数:
          local_paths: 本地存档目录/文件路径列表
          label: 标签（如 "saves", "states"）

        返回: {"uploaded": [...], "count": N}
        """
        if not local_paths:
            return {"uploaded": [], "count": 0, "message": "无存档路径"}

        cred = await self._get_credential()
        parent_id = await self._ensure_remote_dir()

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        results: list[dict] = []

        for local_path in local_paths:
            if not os.path.exists(local_path):
                self._info("cloud_save upload: skip missing path %s", local_path)
                continue

            # 打包为 tar.gz
            archive_name = f"{label}_{timestamp}_{uuid.uuid4().hex[:6]}.tar.gz"
            tmp_path = os.path.join(tempfile.gettempdir(), archive_name)
            try:
                with tarfile.open(tmp_path, "w:gz") as tar:
                    arcname = os.path.basename(local_path.rstrip("/"))
                    tar.add(local_path, arcname=arcname)

                archive_size = os.path.getsize(tmp_path)
                self._info(
                    "cloud_save upload: %s -> %s (%d bytes)",
                    local_path, archive_name, archive_size,
                )

                # 上传到天翼云盘
                uploaded = await self._tianyi_upload_file(
                    cred, tmp_path, archive_name, parent_id
                )

                results.append({
                    "file_name": archive_name,
                    "local_path": local_path,
                    "remote_file_id": uploaded.get("file_id", ""),
                    "size": archive_size,
                })
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        # 写历史
        for r in results:
            _append_history({
                "id": uuid.uuid4().hex[:12],
                "file_name": r["file_name"],
                "direction": "upload",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "size": r["size"],
            })

        return {"uploaded": results, "count": len(results)}

    async def download(self, local_paths: list[str] | None = None) -> dict:
        """从天翼云盘下载存档并恢复到指定目录。

        参数:
          local_paths: 恢复目标目录列表（若为 None 则只列出云端文件）

        返回: {"downloaded": [...], "count": N}
        """
        cred = await self._get_credential()
        parent_id = await self._ensure_remote_dir()

        # 列出云端存档文件（按时间倒序）
        remote_files = await self._tianyi_list_dir(cred, parent_id)
        if not remote_files:
            return {"downloaded": [], "count": 0, "message": "云端无存档"}

        # 最新的 tar.gz 排在前面
        archives = sorted(
            [f for f in remote_files if f.get("name", "").endswith(".tar.gz")],
            key=lambda f: f.get("created_at", ""),
            reverse=True,
        )

        if not archives:
            return {"downloaded": [], "count": 0, "message": "云端无 tar.gz 存档"}

        if local_paths is None:
            # 仅列出云端文件信息
            return {
                "downloaded": [],
                "count": 0,
                "remote_files": [
                    {"file_name": f["name"], "size": f.get("size_bytes", 0)}
                    for f in archives
                ],
                "message": f"云端共 {len(archives)} 个存档文件",
            }

        # 下载最新存档
        results: list[dict] = []
        # 取最新一个存档文件
        latest = archives[0]
        tmp_path = os.path.join(tempfile.gettempdir(), latest["name"])

        try:
            # 下载
            self._info("cloud_save download: fetching %s", latest["name"])
            dl_url = await self._tianyi_get_download_url(cred, latest["file_id"], parent_id)
            await _download_file(dl_url, tmp_path)

            # 解包到目标目录
            for local_path in local_paths:
                if not os.path.exists(local_path):
                    os.makedirs(local_path, exist_ok=True)
                with tarfile.open(tmp_path, "r:gz") as tar:
                    tar.extractall(local_path)
                self._info("cloud_save download: extracted to %s", local_path)
                results.append({
                    "file_name": latest["name"],
                    "extracted_to": local_path,
                    "size": os.path.getsize(tmp_path),
                })
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        # 写历史
        for r in results:
            _append_history({
                "id": uuid.uuid4().hex[:12],
                "file_name": r["file_name"],
                "direction": "download",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "size": r.get("size", 0),
            })

        return {"downloaded": results, "count": len(results)}

    async def list_remote(self) -> list[dict]:
        """列出云端所有存档文件。"""
        try:
            cred = await self._get_credential()
            parent_id = await self._ensure_remote_dir()
            files = await self._tianyi_list_dir(cred, parent_id)
            return [
                {
                    "file_name": f.get("name", ""),
                    "file_id": f.get("file_id", ""),
                    "size": f.get("size_bytes", 0),
                    "created_at": f.get("created_at", ""),
                }
                for f in files
            ]
        except Exception as e:
            self._info("cloud_save list_remote failed: %s", e)
            return []

    # ── 单游戏粒度同步 ──────────────────

    REMOTE_ROOT = "FreedeckCloudSaves"

    async def _ensure_game_dir(self, game_id: str) -> str:
        """确保 FreedeckCloudSaves/{game_id}/ 目录存在，返回其 file_id。"""
        cred = await self._get_credential()

        # 列出根目录找 FreedeckCloudSaves
        root_dirs = await self._tianyi_list_dir(cred, "-11")
        root_id = None
        for d in root_dirs:
            if d.get("name") == self.REMOTE_ROOT:
                root_id = d["file_id"]
                break

        if not root_id:
            created = await self._tianyi_create_dir(cred, self.REMOTE_ROOT, "-11")
            root_id = created["file_id"]

        # 找/创建 {game_id} 子目录
        sub_dirs = await self._tianyi_list_dir(cred, root_id)
        game_dir_id = None
        safe_game_id = game_id.replace(":", "_").replace("/", "_")
        for d in sub_dirs:
            if d.get("name") == safe_game_id:
                game_dir_id = d["file_id"]
                break

        if not game_dir_id:
            created = await self._tianyi_create_dir(cred, safe_game_id, root_id)
            game_dir_id = created["file_id"]

        return game_dir_id

    async def upload_game(self, game_id: str, local_paths: list[str]) -> dict:
        """上传单个游戏的存档（不压缩，直存文件，更新 manifest）。

        流程:
          1. 确保云端目录存在
          2. 读取/下载云端 manifest.json
          3. 对比本地文件 MD5 vs 云端 manifest → 筛出变更文件
          4. 逐个上传变更文件
          5. 更新云端 manifest.json
          6. 更新本地索引
        """
        if not local_paths:
            return {"status": "no_paths", "count": 0, "message": "无存档路径"}

        cred = await self._get_credential()
        game_dir_id = await self._ensure_game_dir(game_id)

        # 加载本地索引
        index = LocalIndex()

        # 扫描本地文件并计算 MD5
        local_files: dict[str, str] = {}  # relpath → md5
        for local_path in local_paths:
            if not os.path.isdir(local_path):
                continue
            for root, _dirs, files in os.walk(local_path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, local_path)
                    try:
                        local_files[rel] = _md5_file(fpath)
                    except Exception:
                        continue

        if not local_files:
            return {"status": "no_files", "count": 0, "message": "存档目录为空"}

        # 下载云端 manifest（如果存在）
        cloud_manifest: dict[str, str] = {}
        try:
            cloud_manifest = await self._download_manifest_json(cred, game_dir_id)
        except Exception:
            pass  # 首次上传，无 manifest

        # 对比
        changed: list[str] = []   # 本地变更的文件（需上传）
        for rel, md5 in local_files.items():
            cloud_md5 = cloud_manifest.get(rel, "")
            idx_entry = index.get_file(game_id, rel)
            if md5 != cloud_md5:
                changed.append(rel)

        if not changed:
            # 无变更：若本地索引缺少该游戏基准（index 丢失/损坏/首次 no_change），
            # 必须补全 files 基准，否则 detect_update 三方对比全走 conflict（本地有文件无基准→冲突保护）
            existing = index._data.get(game_id, {}).get("files")
            if existing:
                index.touch(game_id, list(local_files.keys()))
            else:
                index.update(game_id, local_files)
            return {"status": "no_change", "count": 0, "message": "存档无变更"}

        # 上传变更文件
        uploaded = 0
        uploaded_bytes = 0
        for rel in changed:
            # 找到对应的本地完整路径
            fpath = ""
            for local_path in local_paths:
                candidate = os.path.join(local_path, rel)
                if os.path.isfile(candidate):
                    fpath = candidate
                    break
            if not fpath:
                continue

            # 用相对路径作为云端文件名（/ 替换为 _）
            safe_name = rel.replace("/", "_").replace("\\", "_")
            try:
                await self._tianyi_upload_file(cred, fpath, safe_name, game_dir_id)
                uploaded += 1
                try:
                    uploaded_bytes += os.path.getsize(fpath)
                except OSError:
                    pass
                self._info("cloud_save upload_game: %s/%s", game_id, rel)
            except Exception as e:
                self._info("cloud_save upload_game failed: %s/%s: %s", game_id, rel, e)

        # 更新本地索引
        index.update(game_id, local_files)

        # 更新云端 manifest（合并策略：保留云端已有条目，本地覆盖/新增。
        # 否则重装/首次启动本地暂缺文件时，重建 manifest 会丢失云端老条目，
        # 导致 detect_update/download 找不到老档（2026-08-17 01:0x 事故根因））
        new_manifest = dict(cloud_manifest)
        new_manifest.update(local_files)
        await self._upload_manifest_json(cred, game_dir_id, new_manifest, game_id)

        # 清除 pending 标记
        index.clear_pending(game_id)

        if uploaded > 0:
            _append_history({
                "id": uuid.uuid4().hex[:12],
                "file_name": f"{game_id}（{uploaded} 个文件）",
                "direction": "upload",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "size": uploaded_bytes,
            })

        return {"status": "ok", "count": uploaded, "message": f"已上传 {uploaded} 个文件"}

    async def detect_update(self, game_id: str, local_paths: list[str]) -> dict:
        """检测云端 vs 本地差异，判定同步状态（不执行下载）。"""
        if not local_paths:
            return {"status": "no_paths", "count": 0, "local_count": 0, "message": "无恢复目录"}

        cred = await self._get_credential()
        game_dir_id = await self._ensure_game_dir(game_id)

        try:
            cloud_manifest = await self._download_manifest_json(cred, game_dir_id)
        except Exception:
            return {"status": "first_play", "count": 0, "message": "云端无存档"}

        if not cloud_manifest:
            return {"status": "first_play", "count": 0, "message": "云端无存档"}

        index = LocalIndex()

        local_files: dict[str, str] = {}
        for local_path in local_paths:
            if not os.path.isdir(local_path):
                continue
            for root, _dirs, files in os.walk(local_path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, local_path)
                    if not _is_save_file(rel):
                        continue  # 运行时文件不参与方向判定
                    try:
                        local_files[rel] = _md5_file(fpath)
                    except Exception:
                        continue

        cloud_newer: list[str] = []
        conflict_files: list[dict] = []
        local_newer: list[str] = []

        for rel, raw_cloud_md5 in cloud_manifest.items():
            if not _is_save_file(rel):
                continue  # 运行时文件不参与方向判定
            cloud_md5 = _md5_of(raw_cloud_md5)
            idx_entry = index.get_file(game_id, rel)
            local_md5 = local_files.get(rel, "")

            if not local_md5:
                # 本地无此存档文件（缺失/被删/首次）→ 云端有 → 云端较新（需恢复）
                cloud_newer.append(rel)
            elif idx_entry:
                idx_md5 = idx_entry.get("md5", "")
                if cloud_md5 != idx_md5 and local_md5 == idx_md5:
                    cloud_newer.append(rel)
                elif cloud_md5 == idx_md5 and local_md5 != idx_md5:
                    local_newer.append(rel)
                elif cloud_md5 != idx_md5 and local_md5 != idx_md5 and local_md5 != cloud_md5:
                    conflict_files.append({
                        "name": rel,
                        "local_md5": local_md5,
                        "cloud_md5": cloud_md5,
                    })
            else:
                # 本地有、云端有、无基准 → 冲突保护（首次同步）
                conflict_files.append({
                    "name": rel,
                    "local_md5": local_md5,
                    "cloud_md5": cloud_md5,
                })

        if conflict_files:
            return {
                "status": "conflict",
                "cloud_files": [{"name": f["name"], "md5": _md5_of(f["cloud_md5"])[:16]}
                                for f in conflict_files],
                "local_files": [{"name": f["name"], "md5": _md5_of(f["local_md5"])[:16]}
                                for f in conflict_files],
                "local_count": len(local_files),
                "message": "存档冲突，需用户选择",
            }

        if cloud_newer:
            return {
                "status": "cloud_newer",
                "cloud_files": [{"name": n, "md5": _md5_of(cloud_manifest.get(n, ""))[:16]}
                                for n in cloud_newer],
                "local_count": len(local_files),
                "message": f"云端有 {len(cloud_newer)} 个文件更新",
            }

        if local_newer:
            return {"status": "local_newer", "count": 0, "message": "本地存档较新"}

        return {"status": "no_change", "count": 0, "message": "存档无变更"}

    async def download_game(self, game_id: str, local_paths: list[str], force: bool = False) -> dict:
        """从云端下载单游戏的存档（不压缩，直取文件）。

        流程:
          1. 列出云端目录
          2. 下载 manifest.json
          3. 对比本地索引 vs 云端 manifest
          4. 下载差异文件，覆盖本地
          5. 更新本地索引
        """
        if not local_paths:
            return {"status": "no_paths", "count": 0, "message": "无恢复目录"}

        cred = await self._get_credential()
        game_dir_id = await self._ensure_game_dir(game_id)

        # 下载云端 manifest
        try:
            cloud_manifest = await self._download_manifest_json(cred, game_dir_id)
        except Exception:
            return {"status": "first_play", "count": 0, "message": "云端无存档"}

        if not cloud_manifest:
            return {"status": "first_play", "count": 0, "message": "云端无存档"}

        # 加载本地索引
        index = LocalIndex()

        # 计算本地文件 MD5（用于冲突检测，运行时文件不参与方向判定）
        local_files: dict[str, str] = {}
        for local_path in local_paths:
            if not os.path.isdir(local_path):
                continue
            for root, _dirs, files in os.walk(local_path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, local_path)
                    if not _is_save_file(rel):
                        continue
                    try:
                        local_files[rel] = _md5_file(fpath)
                    except Exception:
                        continue

        # 对比
        cloud_newer: list[str] = []    # 云端有更新
        conflict_files: list[dict] = []  # 双方都变更
        local_newer: list[str] = []    # 仅本地有（未同步）

        for rel, raw_cloud_md5 in cloud_manifest.items():
            if not _is_save_file(rel):
                continue
            cloud_md5 = _md5_of(raw_cloud_md5)
            idx_entry = index.get_file(game_id, rel)
            local_md5 = local_files.get(rel, "")

            if not local_md5:
                # 本地无此文件（缺失/被删/首次）→ 云端拉取
                cloud_newer.append(rel)
            elif idx_entry:
                # 文件在索引中
                idx_md5 = idx_entry.get("md5", "")
                if cloud_md5 != idx_md5 and local_md5 == idx_md5:
                    # 云端更新，本地未变
                    cloud_newer.append(rel)
                elif cloud_md5 == idx_md5 and local_md5 != idx_md5:
                    # 本地更新，云端未变
                    local_newer.append(rel)
                elif cloud_md5 != idx_md5 and local_md5 != idx_md5 and local_md5 != cloud_md5:
                    # 双方都变更
                    conflict_files.append({
                        "name": rel,
                        "local_md5": local_md5,
                        "cloud_md5": cloud_md5,
                    })
            else:
                # 云端有记录但本地索引无记录：本地已有文件 → 冲突保护（防首次同步静默覆盖）
                conflict_files.append({
                    "name": rel,
                    "local_md5": local_md5,
                    "cloud_md5": cloud_md5,
                })

        if not cloud_newer and not conflict_files:
            # 云端无新增/变更
            if local_newer:
                index.mark_pending(game_id)
                return {"status": "local_newer", "count": 0, "message": "本地存档较新，退出时上传"}
            return {"status": "no_change", "count": 0, "message": "存档无变更"}

        if conflict_files and not force:
            return {
                "status": "conflict",
                "cloud_files": [{"name": f["name"], "md5": f["cloud_md5"][:16]}
                                for f in conflict_files],
                "local_files": [{"name": f["name"], "md5": f["local_md5"][:16]}
                                for f in conflict_files],
                "message": "存档冲突，需用户选择",
            }

        # force 模式：冲突文件也强制用云端
        if force:
            for cf in conflict_files:
                if cf["name"] not in cloud_newer:
                    cloud_newer.append(cf["name"])

        # 下载云端更新的文件
        # 1. 列出云端所有文件
        all_cloud_files = await self._tianyi_list_dir(cred, game_dir_id)
        cloud_file_map: dict[str, str] = {}
        for f in all_cloud_files:
            if not f.get("is_dir"):
                cloud_file_map[f["name"]] = f["file_id"]

        downloaded = 0
        downloaded_bytes = 0
        for rel in cloud_newer:
            safe_name = rel.replace("/", "_").replace("\\", "_")
            file_id = cloud_file_map.get(safe_name, "")
            if not file_id:
                continue

                        # 确定本地写入路径（语义匹配 rel 前缀 → Wine 路径，不依赖目录已存在。
            # v0.34.9: 重新下载后 compatdata 未创建时 isdir 探测失败 → 存档落错 install_dir，
            # 改为按 rel 前缀映射：Roaming→AppData/Roaming, Documents→Documents, Saved Games→Saved Games）
            first_seg = rel.split("/", 1)[0]
            rest_rel = rel.split("/", 1)[1] if "/" in rel else ""
            target_path = os.path.join(local_paths[0], rel)
            for lp in local_paths[1:]:
                seg = os.path.basename(lp.rstrip("/"))
                if first_seg == "Roaming" and seg.lower() == "appdata":
                    target_path = os.path.join(lp, "Roaming", rest_rel)
                    break
                if first_seg == seg:
                    target_path = os.path.join(lp, rest_rel) if rest_rel else lp
                    break
            if not target_path:
                continue

            try:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                dl_url = await self._tianyi_get_download_url(cred, file_id, game_dir_id)
                await _download_file(dl_url, target_path)
                # 下载后 MD5 校验（防坏档/不完整静默覆盖本地）
                expect_md5 = cloud_manifest.get(rel, "")
                if expect_md5 and _md5_file(target_path) != expect_md5:
                    try:
                        os.remove(target_path)
                    except OSError:
                        pass
                    self._info("cloud_save download_game MD5 mismatch: %s/%s", game_id, rel)
                    continue
                downloaded += 1
                try:
                    downloaded_bytes += os.path.getsize(target_path)
                except OSError:
                    pass
                self._info("cloud_save download_game: %s/%s", game_id, rel)
            except Exception as e:
                self._info("cloud_save download_game failed: %s/%s: %s", game_id, rel, e)

        # 更新本地索引
        resolved_files = {rel: cloud_manifest.get(rel, "") for rel in cloud_manifest}
        index.update(game_id, resolved_files)

        if downloaded > 0:
            _append_history({
                "id": uuid.uuid4().hex[:12],
                "file_name": f"{game_id}（{downloaded} 个文件）",
                "direction": "download",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "size": downloaded_bytes,
            })

        return {"status": "ok", "count": downloaded, "message": f"已下载 {downloaded} 个文件"}

    async def resolve_conflict(self, game_id: str, choice: str,
                               local_paths: list[str], force: bool = False) -> dict:
        """解决存档冲突。

        choice:
          - "use_cloud": 用云端存档覆盖本地
          - "keep_local": 保留本地，标记待上传
        """
        index = LocalIndex()

        if choice == "use_cloud":
            result = await self.download_game(game_id, local_paths, force=True)
            return {"status": "ok", "chosen": "use_cloud", **result}
        elif choice == "keep_local":
            index.mark_pending(game_id)
            return {"status": "ok", "chosen": "keep_local",
                    "message": "已保留本地存档，退出时上传"}
        else:
            return {"status": "error", "message": f"未知选项: {choice}"}

    async def _download_manifest_json(self, credential, dir_id: str) -> dict[str, str]:
        """下载云端 manifest.json 并解析。"""
        files = await self._tianyi_list_dir(credential, dir_id)
        manifest_file = next(
            (f for f in files if f.get("name") == "manifest.json"), None
        )
        if not manifest_file:
            return {}

        dl_url = await self._tianyi_get_download_url(
            credential, manifest_file["file_id"], dir_id
        )
        import tempfile
        tmp = tempfile.mktemp(suffix=".json")
        try:
            await _download_file(dl_url, tmp)
            with open(tmp, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("files", {}) if isinstance(data, dict) else {}
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    async def _upload_manifest_json(self, credential, dir_id: str,
                                    files_md5: dict[str, str], game_id: str):
        """上传/更新云端 manifest.json。"""
        manifest = {
            "game_id": game_id,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "files": files_md5,
        }
        import tempfile
        tmp = tempfile.mktemp(suffix=".json")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            await self._tianyi_upload_file(credential, tmp, "manifest.json", dir_id)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ── 天翼云盘底层 HTTP ───────────────

    async def _get_session_key(self, credential) -> str:
        """获取 sessionKey（上传必需）。"""
        import aiohttp as _aiohttp
        async with _aiohttp.ClientSession(connector=_aiohttp.TCPConnector(ssl=_ssl_context())) as session:
            ts = int(time.time() * 1000)
            url = f"https://cloud.189.cn/v2/getUserBriefInfo.action?noCache={ts}"
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Cookie": credential.cookie,
            }
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
            return str(data.get("sessionKey", ""))

    async def _tianyi_list_dir(self, credential, parent_id: str) -> list[dict]:
        """调用天翼云盘列出目录（XML 响应）。"""
        import aiohttp as _aiohttp

        async with _aiohttp.ClientSession(connector=_aiohttp.TCPConnector(ssl=_ssl_context())) as session:
            ts = int(time.time() * 1000)
            url = (
                f"https://cloud.189.cn/api/open/file/listFiles.action"
                f"?folderId={parent_id}&fileType=0&pageNum=1&pageSize=200"
                f"&noCache={ts}"
            )
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Cookie": credential.cookie,
            }
            async with session.get(url, headers=headers) as resp:
                text = await resp.text()

            results: list[dict] = []
            # 解析 XML 目录列表（不用 xml.etree，兼容 Decky 精简 Python 环境）
            for block in _xml_findall(text, "folder"):
                results.append({
                    "name": _xml_child_text(block, "name") or _xml_child_text(block, "fileName"),
                    "file_id": _xml_child_text(block, "id"),
                    "size_bytes": int(_xml_child_text(block, "size") or "0"),
                    "is_dir": True,
                    "created_at": _xml_child_text(block, "createDate"),
                })
            for block in _xml_findall(text, "file"):
                results.append({
                    "name": _xml_child_text(block, "name") or _xml_child_text(block, "fileName"),
                    "file_id": _xml_child_text(block, "id"),
                    "size_bytes": int(_xml_child_text(block, "size") or "0"),
                    "is_dir": False,
                    "created_at": _xml_child_text(block, "createDate"),
                })
            return results

    async def _tianyi_create_dir(self, credential, name: str, parent_id: str) -> dict:
        """调用天翼云盘创建目录（去掉 /open/ 前缀，XML 响应）。"""
        import aiohttp as _aiohttp

        async with _aiohttp.ClientSession(connector=_aiohttp.TCPConnector(ssl=_ssl_context())) as session:
            url = "https://cloud.189.cn/api/file/createFolder.action"
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Cookie": credential.cookie,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            data_str = f"parentFolderId={parent_id}&folderName={name}"
            async with session.post(url, headers=headers, data=data_str) as resp:
                text = await resp.text()

            # 解析 XML 响应（不用 xml.etree，兼容 Decky 精简 Python 环境）
            try:
                err_code = _xml_text(text, "code")
                if err_code:
                    err_msg = _xml_text(text, "message")
                    raise RuntimeError(f"创建目录失败: code={err_code}, msg={err_msg}")

                file_id = _xml_text(text, "id")
                if not file_id:
                    raise RuntimeError(f"创建目录返回无 id: {text[:200]}")
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError(f"创建目录响应解析失败: {text[:200]}")

            return {"name": name, "file_id": file_id}

    async def _tianyi_delete_file(self, credential, file_ids: list[str]) -> bool:
        """删除天翼云盘文件（同名上传前先删旧，避免自动加时间戳重命名）。"""
        import aiohttp as _aiohttp
        if not file_ids:
            return True
        try:
            async with _aiohttp.ClientSession(connector=_aiohttp.TCPConnector(ssl=_ssl_context())) as session:
                headers = {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "Cookie": credential.cookie,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                }
                # 天翼云删除 API: /api/batchDeleteFile.action，fileIdList 用分号连接（社区逆向 cloud189 库确认）
                ids = ";".join(str(i) for i in file_ids)
                async with session.post(
                    "https://cloud.189.cn/api/batchDeleteFile.action",
                    headers=headers,
                    data={"fileIdList": ids},
                ) as resp:
                    text = await resp.text()
                self._info("cloud_save delete_files: %s -> %s", ids, text[:120])
                return True
        except Exception as e:
            self._info("cloud_save delete_files failed: %s", e)
            return False

    async def _tianyi_upload_file(
        self, credential, local_path: str, remote_name: str, parent_id: str
    ) -> dict:
        """调用天翼云盘上传文件（旧版 DCIWebUploadAction multipart）。"""
        import aiohttp as _aiohttp
        from aiohttp import FormData

        # 上传前先删云端同名文件（天翼云同名上传会自动加时间戳重命名 → manifest/存档累积）
        try:
            existing = await self._tianyi_list_dir(credential, parent_id)
            same = [f for f in existing if not f.get("is_dir") and f.get("name") == remote_name]
            if same:
                await self._tianyi_delete_file(credential, [f["file_id"] for f in same])
        except Exception as e:
            self._info("cloud_save pre-delete failed: %s", e)

        # 获取 sessionKey
        session_key = await self._get_session_key(credential)

        async with _aiohttp.ClientSession(connector=_aiohttp.TCPConnector(ssl=_ssl_context())) as session:
            with open(local_path, "rb") as fh:
                form = FormData()
                form.add_field("parentId", parent_id)
                form.add_field("sessionKey", session_key)
                form.add_field("opertype", "1")
                form.add_field("fname", remote_name)
                form.add_field("Filedata", fh, filename=remote_name,
                               content_type="application/octet-stream")

                headers = {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "Cookie": credential.cookie,
                }
                async with session.post(
                    "https://hb02.upload.cloud.189.cn/v1/DCIWebUploadAction",
                    headers=headers,
                    data=form,
                ) as resp:
                    data = await resp.json()

            if data.get("MD5"):
                return {
                    "file_id": str(data.get("id", "")),
                    "file_name": data.get("name", remote_name),
                }

            raise RuntimeError(f"上传失败: {data}")

    async def _tianyi_get_download_url(
        self, credential, file_id: str, parent_id: str
    ) -> str:
        """获取天翼云盘文件的下载直链。"""
        import aiohttp as _aiohttp

        async with _aiohttp.ClientSession(connector=_aiohttp.TCPConnector(ssl=_ssl_context())) as session:
            ts = int(time.time() * 1000)
            url = (
                f"https://cloud.189.cn/api/open/file/getFileDownloadUrl.action?"
                f"fileId={file_id}&noCache={ts}"
            )
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Cookie": credential.cookie,
            }
            async with session.get(url, headers=headers) as resp:
                text = await resp.text()

            # 可能是 JSON 或 XML
            try:
                data = json.loads(text)
                if str(data.get("res_code", "")) == "0":
                    return _unescape_url(str(data.get("fileDownloadUrl", "")))
                raise RuntimeError(f"获取下载链接失败: {data}")
            except (json.JSONDecodeError, ValueError):
                pass

            # XML 响应（不用 xml.etree，兼容 Decky 精简 Python 环境）
            dl_url = _xml_text(text, "fileDownloadUrl")
            if dl_url:
                return _unescape_url(dl_url)
            raise RuntimeError(f"获取下载链接失败: {text[:200]}")


# ═══════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════

def _parse_manifest_sync(path: str) -> dict:
    """解析 Ludusavi YAML 清单，返回 {game_name: {steam: {id: ...}, files: {path: {tags: [...]}}}}。"""
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        return {}

    result: dict = {}
    for game_name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        parsed: dict = {}

        # files: {path_template: {tags: [...], when: [...]}}
        files = entry.get("files")
        if isinstance(files, dict):
            parsed["files"] = files

        # installDir
        install_dir = entry.get("installDir")
        if isinstance(install_dir, dict):
            parsed["installDir"] = install_dir

        # steam.id
        steam = entry.get("steam")
        if isinstance(steam, dict) and steam.get("id"):
            parsed["steam"] = {"id": int(steam["id"])}

        if parsed:
            result[str(game_name)] = parsed

    return result


def _parse_and_cache(yaml_path: str, json_path: str) -> dict:
    """解析 YAML 清单并缓存为 JSON（首次慢，后续秒加载）。"""
    manifest = _parse_manifest_sync(yaml_path)
    if not manifest:
        return {}
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False)
    except Exception:
        pass
    return manifest


def _load_json_cache(json_path: str) -> dict:
    """从 JSON 缓存加载清单（秒级）。"""
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_game_in_manifest(
    game_title: str, steam_appid: int, install_dir: str, manifest: dict
) -> str | None:
    """在清单中查找游戏（三层匹配）。"""
    if not manifest:
        return None

    # 1. Steam AppID 精确匹配
    if steam_appid:
        for name, entry in manifest.items():
            sid = entry.get("steam", {}).get("id", 0)
            if sid == steam_appid:
                return name

    # 2. installDir 名称匹配
    if install_dir:
        dir_name = os.path.basename(install_dir.rstrip("/")).lower()
        for name, entry in manifest.items():
            install_dirs = entry.get("installDir", {})
            if any(k.lower() == dir_name for k in install_dirs):
                return name

    # 3. 游戏标题模糊匹配
    title_clean = _normalize_for_match(game_title)
    if title_clean:
        for name in manifest:
            if _normalize_for_match(name) == title_clean:
                return name

    return None


def _normalize_for_match(text: str) -> str:
    """标准化名称用于匹配比较。"""
    return re.sub(
        r"[^a-z0-9]", "",
        text.lower()
        .replace(":", "")
        .replace("'", "")
        .replace("-", "")
        .replace(" ", "")
    )


def _resolve_save_paths(entry: dict, steam_appid: int, install_dir: str) -> list[str]:
    """解析清单条目中的存档路径模板。"""
    home = os.path.expanduser("~")
    base = install_dir or ""
    compat_data = ""
    if steam_appid:
        compat_data = os.path.join(
            home, ".steam", "steam", "steamapps", "compatdata", str(steam_appid), "pfx", "drive_c"
        )

    replacements = {
        "<home>": home,
        "<base>": base,
        "<storeUserId>": str(steam_appid),
        "<winAppData>": os.path.join(compat_data, "users", "steamuser", "AppData", "Roaming"),
        "<winLocalAppData>": os.path.join(compat_data, "users", "steamuser", "AppData", "Local"),
        "<winDocuments>": os.path.join(compat_data, "users", "steamuser", "Documents"),
        "<winUserProfile>": os.path.join(compat_data, "users", "steamuser"),
        "<xdgData>": os.environ.get("XDG_DATA_HOME", os.path.join(home, ".local", "share")),
        "<xdgConfig>": os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config")),
    }

    paths: list[str] = []
    for template, meta in entry.get("files", {}).items():
        resolved = template
        for placeholder, value in replacements.items():
            resolved = resolved.replace(placeholder, value)
        resolved = os.path.normpath(resolved)

        # 通配符展开（简单情况）
        if "*" in resolved:
            import glob as _glob
            expanded = _glob.glob(resolved)
            paths.extend(p for p in expanded if os.path.exists(p))
        elif os.path.exists(resolved):
            paths.append(resolved)

    return paths


def _scan_common_pc_save_paths(
    game_title: str, install_dir: str, steam_appid: int
) -> list[str]:
    """扫描 PC 游戏的常见存档目录（不带清单，兜底方案）。"""
    candidates: list[str] = []
    home = os.path.expanduser("~")

    # Steam compatdata 下的常见路径
    if steam_appid:
        compat = os.path.join(
            home, ".steam", "steam", "steamapps", "compatdata", str(steam_appid)
        )
        # v0.34.9: 不再要求 compat 目录已存在——重新下载后游戏首次启动前
        # compatdata 尚未创建，过滤会导致存档路径丢失、云端恢复落错目录。
        candidates.extend([
            os.path.join(compat, "pfx", "drive_c", "users", "steamuser", "Documents"),
            os.path.join(compat, "pfx", "drive_c", "users", "steamuser", "Saved Games"),
            os.path.join(compat, "pfx", "drive_c", "users", "steamuser", "AppData"),
        ])

    # 安装目录下常见的 save 文件夹（保留 isdir：这些目录不存在时无意义）
    if install_dir:
        for sub in ("saves", "save", "savedata", "SaveData"):
            p = os.path.join(install_dir, sub)
            if os.path.isdir(p):
                candidates.append(p)

    # v0.34.9 fix: compatdata 候选路径不按 isdir 过滤——
    # 重新下载后游戏首次启动前目录未创建，过滤会导致恢复路径丢失。
    # 不存在的目录 os.walk 自然为空，download_game 落盘时 makedirs 会创建。
    return candidates


# ═══════════════════════════════════════════════
# 历史记录
# ═══════════════════════════════════════════════

def _load_history() -> dict:
    path = _history_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"records": []}


def _save_history(data: dict):
    path = _history_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _append_history(record: dict):
    data = _load_history()
    records: list = data.get("records", [])
    records.insert(0, record)  # 最新的在前
    # 保留最近 200 条
    if len(records) > 200:
        records = records[:200]
    data["records"] = records
    _save_history(data)


def get_history() -> list[dict]:
    """读取同步历史记录（最新的在前）。"""
    data = _load_history()
    return data.get("records", [])


# ═══════════════════════════════════════════════
# HTTP 下载工具
# ═══════════════════════════════════════════════

async def _download_file(url: str, dest_path: str, *, timeout: float = 300.0):
    """下载文件到本地路径。

    修复：connector 已用 _ssl_context()（跳过证书校验，兼容 Decky 精简证书链），
    但 session.get(url, ssl=ssl_ctx) 显式传默认严格校验 context 会覆盖 connector 的
    context → SSLCertVerificationError → manifest 读取失败。移除显式 ssl 参数。
    """
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_obj, connector=aiohttp.TCPConnector(ssl=_ssl_context())) as session:
        async with session.get(url) as resp:
            with open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)