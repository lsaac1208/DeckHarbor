"""NewFreedeck — 安装管道（文件匹配 / 类型检测 / ROM 导入）
模块化设计，方便后续按平台扩展。"""

import os
import re
from typing import Dict, Any, List, Optional, Tuple

# ── 归档扩展名 ─────────────────────────

ARCHIVE_EXTENSIONS = {
    ".7z", ".zip", ".rar", ".tar", ".gz", ".bz2", ".xz", ".iso",
    ".001", ".002", ".003",  # 分卷后缀（检测时看完整名）
    ".z01", ".z02", ".z03",  # split ZIP 分卷 (.z01-.z99)
}

# ── ROM 扩展名（按平台）────────────────

ROM_EXTENSIONS: Dict[str, set] = {
    "switch":  {".xci", ".nsp", ".nsz", ".xcz"},
    "ps1":     {".cue", ".chd", ".pbp", ".m3u", ".bin", ".iso", ".img"},
    "ps2":     {".iso", ".chd", ".cso", ".gz"},
    "ps3":     {".pkg", ".iso"},  # PS3 多为文件夹格式，单独处理
    "psp":     {".iso", ".cso", ".pbp"},
    "3ds":     {".3ds", ".cia", ".cci", ".cxi"},
    "gba":     {".gba", ".agb", ".gb", ".gbc"},
    "nds":     {".nds", ".ids"},
    "fc":      {".nes", ".fds", ".unf", ".unif"},
    "md":      {".md", ".bin", ".gen", ".smd", ".32x"},
}

# ── 平台别名归一化 ─────────────────────

PLATFORM_ALIASES: Dict[str, str] = {
    "ns": "switch",
    "nes": "fc",
    "famicom": "fc",
    "snes": "sfc",
    "megadrive": "md",
    "genesis": "md",
    "gameboy": "gb",
    "gameboyadvance": "gba",
    "nintendods": "nds",
    "psx": "ps1",
    "playstation": "ps1",
    "playstation2": "ps2",
    "playstation3": "ps3",
    "playstationportable": "psp",
}


def normalize_platform(platform: str) -> str:
    """归一化平台名。ns→switch, nes→fc 等。"""
    p = str(platform or "").strip().lower()
    return PLATFORM_ALIASES.get(p, p)


# ── 平台是否为文件夹分发 ─────────────────

FOLDER_BASED_PLATFORMS = {"ps3"}

# ── ShareFile 兼容 ─────────────────────
# 天翼云盘返回 dataclass 对象，不是 dict。统一用此函数读取属性。

def _fget(file_obj, key: str, default=None):
    """兼容 dict 和 dataclass 的属性读取。"""
    if isinstance(file_obj, dict):
        return file_obj.get(key, default)
    return getattr(file_obj, key, default)


# ── 检测函数 ───────────────────────────

def file_type(filename: str, platform: str = "") -> str:
    """判断文件类型：archive | rom | unknown"""
    name = str(filename or "").strip().lower()
    if not name:
        return "unknown"

    # 分卷包
    if re.search(r"\.(?:7z|zip|rar)\.[\d]{2,4}$", name):
        return "archive"
    if re.search(r"\.part[\d]+\.(?:rar|7z|zip)$", name):
        return "archive"
    if re.search(r"\.r[\d]{2,4}$", name):
        return "archive"
    if re.search(r"\.z[\d]{2,4}$", name):
        return "archive"

    # ROM 优先（歧义格式 .iso .bin 等先按 ROM 处理）
    _, ext = os.path.splitext(name)
    platform_norm = normalize_platform(platform)
    exts = ROM_EXTENSIONS.get(platform_norm, set())
    if ext in exts:
        return "rom"

    # 常规归档
    if ext in ARCHIVE_EXTENSIONS:
        return "archive"

    return "unknown"


def is_archive(filename: str) -> bool:
    return file_type(filename) == "archive"


def is_rom(filename: str, platform: str = "") -> bool:
    return file_type(filename, platform) == "rom"


def needs_extraction(filename: str, platform: str = "") -> bool:
    """是否需要解压。

    规则：
    - 纯归档 (.7z .zip .rar .tar .gz .bz2 .xz) → 需要解压
    - 分卷包 (.001 .part1 .r01) → 需要解压
    - ROM 格式 (.xci .nsp .gba .nes 等) → 跳过
    - 歧义格式 (.iso .bin .chd)：如果当前平台把它当 ROM 格式 → 跳过
    """
    if is_rom(filename, platform):
        return False
    return is_archive(filename)


# ── 文件名匹配 ─────────────────────────

def _normalize(name: str) -> str:
    """归一化文件名用于匹配。"""
    n = str(name or "").strip().lower()
    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"[/｜|\\]+", " ", n)
    # 去掉扩展名
    base, _ = os.path.splitext(n)
    return base.strip()


def _collect_split_archive_parts(
    base: str,
    filename: str,
    files: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """给定分卷基础名和原始文件名，收集同一分卷包的所有文件。

    支持的格式：
      - .z01/.z02 → 主文件 base.zip + 分卷 base.z01/.z02/...
      - .7z.001/.rar.001 → 主文件 base + 分卷 base.001/.002/...
      - .part1.rar/.part2.rar → 主文件 base.rar + 分卷 base.partN.rar
      - .r01/.r02 → 主文件 base.rar + 分卷 base.rNN
      - .001/.002 → 无扩展名分卷，主文件 base + 分卷 base.001/...
    """
    name = str(filename or "").lower()
    escaped = re.escape(base)

    if re.search(r'\.z\d{2,4}$', name):
        # split ZIP: base.zip + base.z01 + base.z02 + ...
        pat = re.compile(r'^' + escaped + r'\.(zip|z\d{2,4})$', re.I)
    elif re.search(r'\.(?:7z|zip|rar|iso|gz|bz2|xz)\.\d{2,4}$', name):
        # .7z.001 / .rar.005 等：主文件 base(.7z) + 分卷 base.001/.002/...
        pat = re.compile(r'^' + escaped + r'(\.\d{2,4})?$', re.I)
    elif re.search(r'\.part\d+\.(?:rar|7z|zip)$', name):
        # .part1.rar 等：主文件 base.rar + 分卷 base.partN.rar
        pat = re.compile(r'^' + escaped + r'\.(part\d+\.(?:rar|7z|zip)|rar|7z|zip)$', re.I)
    elif re.search(r'\.r\d{2,4}$', name):
        # .r01 等：主文件 base.rar + 分卷 base.rNN
        pat = re.compile(r'^' + escaped + r'\.(r\d{2,4}|rar)$', re.I)
    elif re.search(r'\.\d{3,4}$', name):
        # .001 等：无扩展名分卷
        pat = re.compile(r'^' + escaped + r'(\.\d{3,4})?$', re.I)
    else:
        return []

    result = []
    for f in files:
        fname = _fget(f, "name", _fget(f, "file_name", ""))
        if pat.match(str(fname)):
            result.append(f)
    return result


def match_files(
    target_name: str,
    files: List[Dict[str, Any]],
    platform: str = "",
) -> List[Dict[str, Any]]:
    """从文件列表中匹配目标文件。返回匹配结果列表。

    策略：
    1. 精确匹配 → 若是分卷则展开所有分卷
    2. 归一化后模糊匹配 → 若是分卷则展开所有分卷
    3. 多文件分享 → 尝试匹配分卷
    4. 单文件 → 直接返回
    """
    target = str(target_name or "").strip()
    if not target or not files:
        return []

    # 排除目录
    non_dirs = [f for f in files if not _fget(f, "is_directory")]
    if not non_dirs:
        non_dirs = files  # 全部是目录

    # 单文件 → 直接返回
    if len(non_dirs) == 1:
        return non_dirs

    target_norm = _normalize(target)
    target_base = _normalize(os.path.basename(target))

    def _maybe_expand_split(matched_file) -> List[Dict[str, Any]]:
        """如果匹配到的文件是分卷压缩包的一部分，展开为所有分卷。"""
        fname = _fget(matched_file, "name", _fget(matched_file, "file_name", ""))
        base = _split_archive_base(str(fname))
        if not base:
            return [matched_file]
        parts = _collect_split_archive_parts(base, str(fname), non_dirs)
        if len(parts) >= 2:
            return parts
        return [matched_file]

    # 策略 1：精确匹配
    for f in non_dirs:
        if _fget(f, "name", "") == target or _fget(f, "file_name", "") == target:
            return _maybe_expand_split(f)

    # 策略 2：归一化匹配
    best_score = 0
    best_match = None

    for f in non_dirs:
        fname = _fget(f, "name", _fget(f, "file_name", ""))
        fnorm = _normalize(fname)
        fbase = _normalize(os.path.basename(fname))

        # 完全匹配
        if target_norm == fnorm or target_base == fbase:
            return _maybe_expand_split(f)

        # 包含匹配
        if target_norm in fnorm or fnorm in target_norm:
            score = min(len(target_norm), len(fnorm)) / max(len(target_norm), len(fnorm))
            if score > best_score:
                best_score = score
                best_match = f

        # 关键词匹配（取文件名中的字母/数字部分）
        target_words = set(re.findall(r"[a-z0-9]{3,}", target_norm))
        file_words = set(re.findall(r"[a-z0-9]{3,}", fnorm))
        common = target_words & file_words
        if common:
            score = len(common) / max(len(target_words | file_words), 1)
            if score > best_score:
                best_score = score
                best_match = f

    if best_match and best_score > 0.3:
        return _maybe_expand_split(best_match)

    # 策略 3：多文件 → 尝试匹配分卷
    base = _split_archive_base(target)
    if base:
        parts = _collect_split_archive_parts(base, target, non_dirs)
        if parts:
            return parts

    # 策略 4：回退，返回空让调用方决定
    return []


def _split_archive_base(filename: str) -> str:
    """检测分卷压缩包，返回基础名（不含分卷后缀）。

    匹配模式:
      game.7z.001 / game.7z.002 → "game.7z"
      game.part1.rar / game.part2.rar → "game"
      game.zip.001 → "game.zip"
      game.001 / game.002 → "game"
      game.z01 / game.z02 → "game"          ← split ZIP (.z01-.z99)
      game.7z.001 → "game.7z"

    如果不是分卷包，返回空字符串。
    """
    name = str(filename or "")
    # 模式 1: .xxx.001, .xxx.002 ... (例如 .7z.001, .zip.001, .rar.001)
    m = re.match(r"^(.+\.(?:7z|zip|rar|iso|gz|bz2|xz))\.(\d{2,4})$", name, re.I)
    if m:
        return m.group(1)
    # 模式 2: .partN.rar, .partNN.rar
    m = re.match(r"^(.+)\.part(\d+)\.(rar|7z|zip)$", name, re.I)
    if m:
        return m.group(1)
    # 模式 3: .rNN, .NNN (WinRAR 分卷)
    m = re.match(r"^(.+)\.r(\d{2,4})$", name, re.I)
    if m:
        return m.group(1)
    # 模式 4: .001, .002 (无扩展名分卷)
    m = re.match(r"^(.+)\.(\d{3,4})$", name)
    if m and not re.search(r"\.v\d+$", m.group(1), re.I):
        return m.group(1)
    # 模式 5: .z01, .z02 ... (split ZIP 分卷，主文件为 base.zip)
    m = re.match(r"^(.+)\.z(\d{2,4})$", name, re.I)
    if m:
        return m.group(1)
    return ""


# ── 文件夹匹配（PS3 等）─────────────────

def match_folder(
    target_name: str,
    files: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """从文件列表中匹配目标文件夹。"""
    target = str(target_name or "").strip()
    if not target:
        return None

    dirs = [f for f in files if _fget(f, "is_directory")]
    if not dirs:
        return None

    target_norm = _normalize(target)

    # 精确匹配
    for d in dirs:
        if _normalize(_fget(d, "name", "")) == target_norm:
            return d

    # 包含匹配
    for d in dirs:
        dnorm = _normalize(_fget(d, "name", ""))
        if target_norm in dnorm or dnorm in target_norm:
            return d

    return None


# ── 游戏分类 ───────────────────────────

def classify_game(game_id: str, platform: str, file_name: str) -> str:
    """分类游戏安装类型：pc | emulator_tool | emulator_rom"""
    gid = str(game_id or "").strip().lower()
    plat = normalize_platform(platform)

    if gid.endswith("_emulator"):
        return "emulator_tool"

    if plat in ROM_EXTENSIONS:
        return "emulator_rom"

    return "pc"


# ── ROM 导入快捷方式构建 ────────────────

def build_rom_launch_args(platform: str, rom_path: str) -> str:
    """根据平台构建模拟器启动参数。"""
    from emulator_adapters import get_adapter
    platform = normalize_platform(platform)
    adapter = get_adapter(platform)
    if any(c in rom_path for c in ('\x00', '\n', '\r')):
        raise ValueError('ROM 路径包含不支持的换行或空字符')
    # Templates use double quotes. Escape shell expansion and embedded quotes.
    rom_path = rom_path.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')
    if adapter and adapter.launch_args:
        return adapter.launch_args.replace("{rom}", rom_path)
    return f'"{rom_path}"'


def emulator_launch_options(platform: str, executable: str, rom_path: str = '') -> str:
    """Use the same portable configuration for emulator UI and game shortcuts."""
    import shlex
    root = os.path.dirname(executable)
    args = build_rom_launch_args(platform, rom_path) if rom_path else ''
    if platform == 'psp':
        args = '--memstick=' + shlex.quote(os.path.join(root, 'memstick')) + (' ' + args if args else '')
    if platform in ('ps3', 'fc', 'md'):
        args = 'env XDG_CONFIG_HOME=' + shlex.quote(os.path.join(root, 'config')) + ' %command%' + (' ' + args if args else '')
    return args


def find_emulator_appimage(platform: str, install_base: Optional[str] = None) -> Optional[str]:
    """Only inspect the requested platform. Never fall back to another emulator."""
    import json
    from emulator_manager import EmulatorManager
    home = os.environ.get("DECKY_USER_HOME", os.path.expanduser("~"))
    state_dir = os.path.join(home, ".local", "share", "NewFreedeck")
    if install_base is None:
        try:
            with open(os.path.join(state_dir, "settings.json")) as file:
                install_base = json.load(file).get("install_path")
        except (OSError, ValueError): pass
    return EmulatorManager(install_base or os.path.join(home, "FreedeckGames"), state_dir).executable(normalize_platform(platform))
