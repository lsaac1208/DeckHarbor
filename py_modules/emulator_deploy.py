"""NewFreedeck — 捆绑模拟器本地部署（defaults/emulators → 安装目录）

设计: docs/superpowers/specs/2026-08-17-bundled-emulators-design.md
不依赖 decky 环境，可独立测试。
"""

import json
import logging
import os
import shutil
from typing import Dict, Optional

_log = logging.getLogger("emulator_deploy")

_plugin_dir: Optional[str] = None
_mark_path_override: Optional[str] = None

BUNDLED_PLATFORMS = ["switch", "ps1", "ps2", "ps3", "psp", "3ds", "gba", "nds", "fc", "md"]


class _DeployAborted(Exception):
    """拷贝期间用户卸载了该平台：中止部署，不覆盖 user_removed 标记。"""


# ── 捆绑目录 ─────────────────────────

def init(plugin_dir: str, mark_file: Optional[str] = None) -> None:
    """注入插件根目录（main.py 启动时调用）；mark_file 仅供测试覆盖。"""
    global _plugin_dir, _mark_path_override
    _plugin_dir = plugin_dir
    _mark_path_override = mark_file


def bundled_emulator_dir(platform: str) -> Optional[str]:
    """插件包内捆绑目录（存在、非空且含模拟器可执行体才返回）。"""
    if not _plugin_dir:
        return None
    d = os.path.join(_plugin_dir, "defaults", "emulators", platform)
    if not (os.path.isdir(d) and os.listdir(d)):
        return None
    if not detect_emulator_executable(d):
        return None  # 插件包不完整：缺本体，按未捆绑处理（跳过该平台）
    return d


def _is_non_emulator_appimage(name: str) -> bool:
    """联机服务器/更新器等非本体 AppImage（如 lime3ds-room）不当作模拟器本体。"""
    lower = name.lower()
    return "-room" in lower or "-server" in lower or "-updater" in lower


def detect_emulator_executable(root_dir: str) -> Optional[str]:
    """在目录里找模拟器可执行体。

    AppImage 只扫顶层目录（真实 bundle 的 AppImage 都在平台目录根）；
    md(blastem) 是原生二进制，特判 `blastem64-0.6.2/blastem` 与 `blastem` 两个路径。
    """
    if not root_dir or not os.path.isdir(root_dir):
        return None
    # 只查顶层：各平台 bundle 的 AppImage 都直接位于平台目录根部
    # （递归 os.walk 会把父目录里嵌套的无关 AppImage 误判成本平台本体）
    # AppImage 两趟扫描：优先排除 -room/-server/-updater（不区分大小写）后的
    # 排序首个；无本体时退回排序后的任意首个。排序保证跨文件系统结果确定
    # （SteamOS btrfs 的 listdir 顺序依赖插入序，可能把 lime3ds-room 排在本体前）。
    try:
        appimages = sorted(
            f for f in os.listdir(root_dir)
            if f.lower().endswith(".appimage") and os.path.isfile(os.path.join(root_dir, f))
        )
    except OSError:
        appimages = []
    for f in appimages:
        if not _is_non_emulator_appimage(f):
            return os.path.join(root_dir, f)
    for candidate in (os.path.join(root_dir, "blastem64-0.6.2", "blastem"),
                      os.path.join(root_dir, "blastem")):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _is_exec_candidate(name: str) -> bool:
    """判断文件是否需要补执行权限：AppImage/.sh/无扩展名（含 blastem 等原生二进制）。"""
    if name.lower().endswith((".appimage", ".sh")):
        return True
    return "." not in os.path.basename(name)


def chmod_exec_recursive(root_dir: str) -> int:
    """递归为可执行体补 +x（只加权限位，不收回已有权限）。返回处理数量。

    云盘 7z/tar 解压常丢失 Unix 执行权限位；Steam AddShortcut 直接 exec
    AppImage，644 会导致启动失败（真实设备上 switch/gba 已复现）。
    """
    if not root_dir or not os.path.isdir(root_dir):
        return 0
    n = 0
    for r, _, files in os.walk(root_dir):
        for f in files:
            if not _is_exec_candidate(f):
                continue
            p = os.path.join(r, f)
            try:
                os.chmod(p, os.stat(p).st_mode | 0o111)
                n += 1
            except OSError:
                _log.warning("chmod +x 失败: %s", p)
    return n


def repair_permissions(install_path: str) -> int:
    """修复整个 Emulators 安装树下已装模拟器的执行权限。

    不依赖部署标记（deploy 状态机会对已有本体的平台 skip，无法覆盖
    云盘安装后遗留的 644 本体），插件启动时调用一次即可自愈。
    """
    emu_root = os.path.join(install_path, "Emulators")
    if not os.path.isdir(emu_root):
        return 0
    return chmod_exec_recursive(emu_root)


def detect_emulator_executable_recursive(root_dir: str) -> Optional[str]:
    """detect_emulator_executable 的递归版。

    顶层扫描（AppImage/blastem）优先；找不到时回退递归扫 AppImage，
    兼容云盘安装时 AppImage 嵌套在子目录的历史布局。

    注：递归回退只扫 .appimage，不扫嵌套 blastem——云盘安装的 md 固定使用
    标准 blastem64-0.6.2/blastem 布局，已被顶层候选路径覆盖，无需回退。
    """
    top = detect_emulator_executable(root_dir)
    if top:
        return top
    if root_dir and os.path.isdir(root_dir):
        hits = sorted(
            os.path.join(r, f)
            for r, _, files in os.walk(root_dir)
            for f in files
            if f.lower().endswith(".appimage")
        )
        for p in hits:
            if not _is_non_emulator_appimage(os.path.basename(p)):
                return p
    return None

# ── 部署标记 ─────────────────────────

def _mark_file() -> str:
    if _mark_path_override:
        return _mark_path_override
    home = os.path.expanduser("~")
    d = os.path.join(home, ".local", "share", "NewFreedeck")
    return os.path.join(d, "emu_deploy.json")


def load_deploy_marks() -> Dict[str, Dict]:
    """读取部署标记；文件缺失/损坏时返回空 dict，不覆盖用户意图。"""
    try:
        with open(_mark_file(), "r") as f:
            data = json.load(f)
    except (FileNotFoundError, OSError):
        return {}  # 首次运行/文件不存在
    except Exception:
        # 损坏的标记文件：挪到一边，下次从干净状态开始，不覆盖用户意图
        try:
            os.replace(_mark_file(), _mark_file() + ".corrupt")
        except Exception:
            pass
        return {}
    if not isinstance(data, dict):
        return {}
    # 过滤非法平台值（非 dict 直接丢弃，防止单条脏数据拖垮整个部署任务）
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def save_deploy_marks(marks: Dict[str, Dict]) -> bool:
    """原子写回标记文件；成功返回 True，失败返回 False（调用方可记录日志）。"""
    try:
        os.makedirs(os.path.dirname(_mark_file()), exist_ok=True)
        tmp = _mark_file() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(marks, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _mark_file())
        return True
    except Exception:
        _log.exception("保存部署标记失败: %s", _mark_file())
        return False


def mark_deployed(platform: str, install_path: str) -> bool:
    """记录平台已部署；持久化成功返回 True，否则 False。"""
    marks = load_deploy_marks()
    marks[platform] = {"status": "deployed", "install_path": install_path}
    return save_deploy_marks(marks)


def mark_user_removed(platform: str) -> bool:
    """记录用户卸载了平台（部署时跳过，不再自动重拷）；持久化成功返回 True，否则 False。"""
    marks = load_deploy_marks()
    prev = marks.get(platform) if isinstance(marks.get(platform), dict) else {}
    marks[platform] = {"status": "user_removed",
                       "install_path": prev.get("install_path", "")}
    return save_deploy_marks(marks)

# ── 状态机 ─────────────────────────

def decide_deploy(platform: str, mark: Optional[Dict], install_path: str) -> str:
    """部署状态机纯函数，返回 "deploy" | "skip"。

    - 插件包没捆这个平台 → skip
    - 用户卸载过（user_removed）→ skip（不自动重拷）
    - 安装目录已有本体 → skip
    - 其余（无标记/标记 deployed 但本体丢失/换了 install_path）→ deploy
    """
    if bundled_emulator_dir(platform) is None:
        return "skip"
    if isinstance(mark, dict) and mark.get("status") == "user_removed":
        return "skip"
    install_dir = os.path.join(install_path, "Emulators", platform)
    if detect_emulator_executable_recursive(install_dir):
        return "skip"
    return "deploy"

# ── 部署执行 ─────────────────────────

def deploy_one(platform: str, install_path: str) -> bool:
    """拷贝捆绑目录到安装目录：先拷临时目录，成功再 rename（中断不留半成品）。

    拷贝完成后会复查用户标记（大体积拷贝耗时长，期间用户可能卸载该平台），
    user_removed 则抛 _DeployAborted 中止；部署成功的可执行体强制 +x。
    """
    bundled = bundled_emulator_dir(platform)
    if not bundled:
        return False
    install_dir = os.path.join(install_path, "Emulators", platform)
    os.makedirs(os.path.dirname(install_dir), exist_ok=True)
    tmp = install_dir + ".deploying"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        shutil.copytree(bundled, tmp)
        mark = load_deploy_marks().get(platform)
        if isinstance(mark, dict) and mark.get("status") == "user_removed":
            raise _DeployAborted(platform)
        if os.path.isdir(install_dir):
            shutil.rmtree(install_dir, ignore_errors=True)
        os.rename(tmp, install_dir)
        # 防御性 chmod：bundle 源可能带 0644（历史上 nds/psp 出过），
        # 不修的话真机 Popen/AddShortcut 启动会失败
        exe = detect_emulator_executable(install_dir)
        if exe:
            try:
                os.chmod(exe, os.stat(exe).st_mode | 0o111)
            except OSError:
                _log.warning("chmod +x 失败: %s", exe)
        return True
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def deploy_missing(install_path: str) -> Dict[str, str]:
    """遍历全部平台按状态机部署。返回 {platform: "deployed"|"skipped"|"failed"}。"""
    if _plugin_dir is None:
        _log.warning("emulator_deploy 未初始化（init 未调用），跳过捆绑模拟器部署")
        return {p: "skipped" for p in BUNDLED_PLATFORMS}
    marks = load_deploy_marks()
    result: Dict[str, str] = {}
    for platform in BUNDLED_PLATFORMS:
        if decide_deploy(platform, marks.get(platform), install_path) == "skip":
            result[platform] = "skipped"
            continue
        try:
            if deploy_one(platform, install_path):
                mark_deployed(platform, install_path)
                result[platform] = "deployed"
            else:
                result[platform] = "failed"
        except _DeployAborted:
            result[platform] = "skipped"  # 拷贝期间用户卸载：尊重 user_removed，不落盘
        except Exception:
            _log.exception("捆绑模拟器部署失败: %s", platform)
            result[platform] = "failed"
    return result
