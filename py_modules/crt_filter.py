"""Freedeck CRT 滤镜模块（从 v0.6 tianyi_service.py 移植）。

机制：利用 SteamOS gamescope 合成器原生的 ReShade 支持——
wrapper 脚本启动模拟器前，用 xprop 设置 X11 根窗口属性
GAMESCOPE_RESHADE_EFFECT 为 shader 文件名，gamescope 即对全部画面
套用该 shader（作用于合成层，Vulkan/OpenGL 模拟器通用）。

运行时开关：state 目录下 emulator_crt_enabled.flag 文件；
wrapper 每秒轮询该文件，开关切换无需重新导入即时生效；
关闭时旧 wrapper 启动也会自动跳过滤镜。
"""
from __future__ import annotations

import os

CRT_PLATFORMS = frozenset({
    "switch", "ps1", "ps2", "ps3", "psp",
    "3ds", "gba", "nds", "md", "fc",
})

SHADER_FILENAME = "freedeck-crt-prototype.fx"
WRAPPER_FILENAME = "freedeck-crt-launcher.sh"
WRAPPER_MARKER = "Freedeck CRT Shortcut Wrapper"
WRAPPER_EMULATOR_PATH_MARKER = "# FREDECK_CRT_EMULATOR_PATH="
WRAPPER_SHADER_MARKER = "# FREDECK_CRT_SHADER_NAME="
FLAG_FILENAME = "emulator_crt_enabled.flag"

# gamescope 读取 reshade shader 的用户目录（SteamOS 约定）
_USER_SHADER_DIR = os.path.join(
    os.path.expanduser("~"), ".local", "share", "gamescope", "reshade", "Shaders"
)


def platform_supported(platform: str) -> bool:
    return str(platform or "").strip().lower() in CRT_PLATFORMS


def flag_path(state_dir: str) -> str:
    return os.path.join(state_dir, FLAG_FILENAME)


def write_flag(state_dir: str, enabled: bool) -> str:
    """写入 CRT 运行时开关文件，返回路径。"""
    p = flag_path(state_dir)
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("1" if enabled else "0")
    except Exception:
        pass
    return p


def install_shader(plugin_dir: str) -> dict:
    """把插件内置 shader 部署到用户 gamescope reshade 目录。"""
    src = os.path.join(
        plugin_dir, "defaults", "gamescope", "reshade", "Shaders", SHADER_FILENAME
    )
    if not os.path.isfile(src):
        return {"ok": False, "target_path": "", "message": f"内置 shader 缺失: {src}"}
    try:
        os.makedirs(_USER_SHADER_DIR, exist_ok=True)
        dst = os.path.join(_USER_SHADER_DIR, SHADER_FILENAME)
        with open(src, "rb") as f:
            data = f.read()
        with open(dst, "wb") as f:
            f.write(data)
        return {"ok": True, "target_path": dst, "message": ""}
    except Exception as e:
        return {"ok": False, "target_path": "", "message": str(e)}


def wrapper_path_for(root_dir: str) -> str:
    return os.path.join(root_dir, WRAPPER_FILENAME)


def write_wrapper(root_dir: str, emulator_path: str, runtime_flag: str) -> str:
    """生成 Steam 快捷方式用的 CRT 包装脚本，返回 wrapper 路径（失败返回空串）。"""
    root_dir = os.path.realpath(os.path.expanduser(root_dir))
    emulator_path = os.path.realpath(os.path.expanduser(emulator_path))
    if not root_dir or not os.path.isdir(root_dir):
        return ""
    if not emulator_path or not os.path.isfile(emulator_path):
        return ""
    wrapper_path = wrapper_path_for(root_dir)
    script = "\n".join([
        "#!/usr/bin/env bash",
        "set -u",
        f"# {WRAPPER_MARKER}",
        f"{WRAPPER_EMULATOR_PATH_MARKER}{emulator_path}",
        f"{WRAPPER_SHADER_MARKER}{SHADER_FILENAME}",
        f'EMULATOR_PATH="{emulator_path}"',
        f'SHADER_NAME="{SHADER_FILENAME}"',
        f'RUNTIME_FLAG_PATH="{runtime_flag}"',
        'XPROP_BIN="$(command -v xprop || true)"',
        'uid="$(id -u)"',
        'runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$uid}"',
        'if [ -d "$runtime_dir" ]; then',
        '  export XDG_RUNTIME_DIR="$runtime_dir"',
        '  if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "$runtime_dir/bus" ]; then',
        '    export DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus"',
        "  fi",
        '  if [ -z "${GAMESCOPE_WAYLAND_DISPLAY:-}" ] && [ -S "$runtime_dir/gamescope-0" ]; then',
        '    export GAMESCOPE_WAYLAND_DISPLAY="gamescope-0"',
        "  fi",
        "fi",
        'if [ -z "${DISPLAY:-}" ] && [ -S /tmp/.X11-unix/X0 ]; then',
        '  export DISPLAY=":0"',
        "fi",
        'CRT_ACTIVE="0"',
        'MONITOR_PID=""',
        "crt_runtime_enabled() {",
        '  if [ -z "$RUNTIME_FLAG_PATH" ] || [ ! -f "$RUNTIME_FLAG_PATH" ]; then',
        "    return 1",
        "  fi",
        "  value=\"$(head -n 1 \"$RUNTIME_FLAG_PATH\" 2>/dev/null | tr -d '\\r\\n\\t ')\"",
        '  if [ -z "$value" ]; then',
        "    return 1",
        "  fi",
        '  case "${value,,}" in',
        "    0|false|off|disabled|no) return 1 ;;",
        "  esac",
        "  return 0",
        "}",
        "enable_crt() {",
        '  if [ -z "$XPROP_BIN" ] || [ -z "${DISPLAY:-}" ]; then',
        "    return 0",
        "  fi",
        "  if ! crt_runtime_enabled; then",
        "    return 0",
        "  fi",
        '  "$XPROP_BIN" -display "$DISPLAY" -root -f GAMESCOPE_RESHADE_EFFECT 8s -set GAMESCOPE_RESHADE_EFFECT "$SHADER_NAME" >/dev/null 2>&1 || true',
        '  CRT_ACTIVE="1"',
        "}",
        "disable_crt() {",
        '  if [ -z "$XPROP_BIN" ] || [ -z "${DISPLAY:-}" ]; then',
        "    return 0",
        "  fi",
        '  "$XPROP_BIN" -display "$DISPLAY" -root -remove GAMESCOPE_RESHADE_EFFECT >/dev/null 2>&1 || true',
        '  CRT_ACTIVE="0"',
        "}",
        "apply_crt_state() {",
        "  if crt_runtime_enabled; then",
        '    if [ "$CRT_ACTIVE" != "1" ]; then',
        "      enable_crt",
        "    fi",
        "  else",
        '    if [ "$CRT_ACTIVE" = "1" ]; then',
        "      disable_crt",
        "    fi",
        "  fi",
        "}",
        "monitor_crt_state() {",
        '  local child_pid="$1"',
        '  while kill -0 "$child_pid" >/dev/null 2>&1; do',
        "    apply_crt_state",
        "    sleep 1",
        "  done",
        "}",
        "stop_monitor() {",
        '  if [ -n "$MONITOR_PID" ]; then',
        '    kill "$MONITOR_PID" >/dev/null 2>&1 || true',
        '    wait "$MONITOR_PID" >/dev/null 2>&1 || true',
        '    MONITOR_PID=""',
        "  fi",
        "}",
        "cleanup_crt() {",
        "  stop_monitor",
        "  disable_crt",
        "  sleep 0.1",
        "  disable_crt",
        "}",
        'if [ ! -x "$EMULATOR_PATH" ]; then',
        '  echo "Freedeck CRT wrapper target missing: $EMULATOR_PATH" >&2',
        "  exit 1",
        "fi",
        "trap 'cleanup_crt' EXIT INT TERM",
        "apply_crt_state",
        '"$EMULATOR_PATH" "$@" &',
        "child_pid=$!",
        'monitor_crt_state "$child_pid" &',
        "MONITOR_PID=$!",
        'wait "$child_pid"',
        "status=$?",
        "exit $status",
        "",
    ])
    try:
        with open(wrapper_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(script)
        os.chmod(wrapper_path, 0o755)
        return wrapper_path
    except Exception:
        return ""


def resolve_wrapper_target(exe_path: str) -> str:
    """若 exe 是 CRT wrapper，返回其指向的真实模拟器路径；否则返回原路径。"""
    p = str(exe_path or "")
    if os.path.basename(p) != WRAPPER_FILENAME or not os.path.isfile(p):
        return p
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith(WRAPPER_EMULATOR_PATH_MARKER):
                    target = line[len(WRAPPER_EMULATOR_PATH_MARKER):].strip()
                    if target:
                        return target
                    break
    except Exception:
        pass
    return p


def lsfg_launch_prefix(platform: str) -> str:
    """LSFG 启动前缀（与 v0.6 一致）：~/lsfg 包装 %command%。"""
    plat = str(platform or "").strip().lower()
    if plat in ("switch", "ps1"):
        return "DISABLE_VKBASALT=1 ~/lsfg %command%"
    return "~/lsfg %command%"
