#!/usr/bin/env python3
"""NewFreedeck — 模拟器捆绑准备脚本。

从源目录抽取干净的模拟器本体/密钥/固件到 defaults/emulators/{platform}。
可重复执行：每次先清空目标目录再抽取。
阶段 2 配置整合时只需在 PLATFORMS 里加 include 规则。

用法: python3 tools/prepare_emulator_bundle.py [--dry-run]
"""

import fnmatch
import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(PROJECT_ROOT, "defaults", "emulators")

# include: 相对源目录的路径或 glob；以 / 结尾表示整个目录。
# exclude: glob，优先级高于 include。
PLATFORMS = [
    {"platform": "switch", "src": "/home/deck/Games/Eden_switch_emula",
     "include": ["Eden-Linux-*.AppImage", "prod.keys", "21.0.1/"],
     "exclude": ["21.0.1.rar", "New Folder", "*.bak*"]},
    {"platform": "ps1", "src": "/home/deck/Games/DuckStation_ps1_emulator",
     "include": ["DuckStation-x64.AppImage", "SCPH1001.BIN", "bios/", "portable.txt"],
     "exclude": ["*.bak*"]},
    {"platform": "ps2", "src": "/home/deck/Games/PCSX2_ps2_emulator",
     "include": ["pcsx2-*.AppImage", "ps2_bios.zip"],
     "exclude": ["*.config", "textures/"]},
    {"platform": "ps3", "src": "/home/deck/Games/RPCS3_ps3_emulator",
     "include": ["rpcs3-*.AppImage", "PS3UPDAT*.PUP", "freedeck-rpcs3-launcher.sh"],
     "exclude": ["*.bak*"]},
    {"platform": "psp", "src": "/home/deck/Games/PPSSPP_psp_emulator",
     "include": ["PPSSPP-*.AppImage"],
     "exclude": ["freedeck-ppsspp.ini*"]},
    {"platform": "3ds", "src": "/home/deck/Games/Lime3DS_3ds_emulator",
     "include": ["lime3ds.AppImage", "lime3ds-room.AppImage", "freedeck-3ds-launcher.sh"],
     "exclude": ["dist/", "scripting/", "README.md", "license.txt"]},
    {"platform": "gba", "src": "/home/deck/Games/mGBA_gba_emulator",
     "include": ["mGBA-*.appimage", "portable.ini"],
     "exclude": ["config.ini", "qt.ini", "saves/", "screenshots/", "states/", "storage/",
                 "library.sqlite3", "nointro.sqlite3"]},
    {"platform": "nds", "src": "/home/deck/Games/melonDS_nds_emulator",
     "include": ["melonDS-*.AppImage", "freedeck-melonds-launcher.sh"],
     "exclude": []},
    {"platform": "fc", "src": "/home/deck/Games/Nestopia_fc_emulator",
     "include": ["Nestopia-*.AppImage", "freedeck-nestopia-launcher.sh"],
     "exclude": []},
    {"platform": "md", "src": "/home/deck/FreedeckGames/Emulators/md",
     "include": ["blastem64-0.6.2/"],
     "exclude": ["*.tar"]},
]


def _match(rel: str, pattern: str) -> bool:
    """rel 是相对源目录的路径。目录模式 'x/' 匹配该目录及内部所有内容。"""
    if pattern.endswith("/"):
        base = pattern[:-1]
        return rel == base or rel.startswith(pattern)
    return fnmatch.fnmatch(rel, pattern)


def collect(entry: dict) -> list:
    """返回 [(rel_path, abs_path, is_dir)] 待拷贝清单。"""
    src = entry["src"]
    if not os.path.isdir(src):
        print(f"[skip] {entry['platform']}: 源目录不存在 {src}")
        return []
    picked = []
    for name in sorted(os.listdir(src)):
        rel = name
        included = any(_match(rel, p) for p in entry["include"])
        excluded = any(_match(rel, p) for p in entry["exclude"])
        if included and not excluded:
            picked.append((rel, os.path.join(src, name)))
    return picked


def main() -> int:
    dry = "--dry-run" in sys.argv
    total = 0
    for entry in PLATFORMS:
        dst = os.path.join(DEST, entry["platform"])
        picked = collect(entry)
        size = sum(
            sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(abs_p) for f in fs)
            if os.path.isdir(abs_p) else os.path.getsize(abs_p)
            for _, abs_p in picked
        )
        total += size
        print(f"[{entry['platform']}] {len(picked)} 项, {size / 1e6:.1f} MB")
        for rel, abs_p in picked:
            print(f"    {rel}")
        if not dry:
            shutil.rmtree(dst, ignore_errors=True)
            os.makedirs(dst, exist_ok=True)
            for rel, abs_p in picked:
                if os.path.isdir(abs_p):
                    shutil.copytree(abs_p, os.path.join(dst, rel))
                else:
                    shutil.copy2(abs_p, os.path.join(dst, rel))
            # 执行位：AppImage/脚本必须是可执行的（copy2 保留源权限，源可能是
            # 0644，不修的话真机上 Popen/AddShortcut 启动会失败）
            for r, _, fs in os.walk(dst):
                for f in fs:
                    if f.lower().endswith((".appimage", ".sh")):
                        p = os.path.join(r, f)
                        os.chmod(p, os.stat(p).st_mode | 0o111)
    print(f"合计: {total / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
