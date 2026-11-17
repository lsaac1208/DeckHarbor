"""NewFreedeck — 模拟器适配器（bootstrap_install + 配置模板）
参考 EmuDeck 的配置策略：
  - 完整 SDL 控制器映射 (STEAM_DECK_SDL_GUID)
  - 模拟主机语言/区域自动设置
  - 模拟器 UI 语言中文化
  - BIOS 文件部署与验证
"""

import os
import stat
import glob as _glob
from typing import Dict, Any, List, Optional, Callable

# ── 配置常量 ──────────────────────────

# Steam Deck SDL GUID（EmuDeck 验证过的值）
STEAM_DECK_SDL_GUID = "03000000de280000ff11000001000000"

# ── 语言/区域检测 ─────────────────────

def _detect_language() -> str:
    """检测系统语言代码。"""
    lang = os.environ.get("LANG", os.environ.get("LANGUAGE", ""))
    code = lang.split(".")[0].split("_")[0].lower() if lang else "en"
    return code if code else "en"


# ═══════════════════════════════════════════════
# 适配器基类
# ═══════════════════════════════════════════════

class EmulatorAdapter:
    """模拟器适配器基类。每个平台一个子类。"""

    emulator_id: str = ""
    display_name: str = ""
    platform: str = ""
    package_game_id: str = ""
    launch_args: str = '"{rom}"'
    bios_hints: List[str] = []
    portable_write_targets: List[str] = []

    # ── 通用工具 ──────────────────────────

    def find_appimage(self, root_dir: str) -> Optional[str]:
        from emulator_deploy import detect_emulator_executable_recursive
        return detect_emulator_executable_recursive(root_dir)

    def ensure_executable(self, path: str) -> bool:
        try:
            st = os.stat(path)
            os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            return True
        except Exception:
            return False

    def enable_portable_mode(self, root_dir: str) -> None:
        for portable_file in self.portable_write_targets:
            try:
                p = os.path.join(root_dir, portable_file)
                if not os.path.exists(p):
                    os.makedirs(os.path.dirname(p) or root_dir, exist_ok=True)
                    with open(p, "w") as f:
                        f.write("")
            except Exception as exc:
                raise OSError(f'便携标记写入失败: {p}') from exc

    def deploy_bios(self, root_dir: str, install_dir: str) -> List[str]:
        messages: List[str] = []
        if not self.bios_hints:
            return messages
        bios_dir = os.path.join(root_dir, "bios")
        os.makedirs(bios_dir, exist_ok=True)
        found = 0
        for hint in self.bios_hints:
            for root, _, files in os.walk(install_dir):
                for f in files:
                    if hint.lower() in f.lower():
                        src = os.path.join(root, f)
                        dst = os.path.join(bios_dir, f)
                        try:
                            if os.path.realpath(src) != os.path.realpath(dst):
                                import shutil
                                shutil.copy2(src, dst)
                            found += 1
                        except Exception:
                            pass
        if found > 0:
            messages.append(f"已部署 {found} 个 BIOS 文件到 {bios_dir}")
        elif self.bios_hints:
            messages.append(f"未找到 BIOS 文件（请手动放置到 {bios_dir}）")
        return messages

    def get_or_create_config_dir(self, root_dir: str) -> str:
        return root_dir

    def write_config(self, root_dir: str) -> List[str]:
        return []

    # ── Qt 配置辅助 ───────────────────────

    def _qt_set_str(self, cfg, section: str, key: str, value: str):
        r"""写入 QSettings INI 格式字符串，附带 \default=false。"""
        import configparser
        if isinstance(cfg, configparser.ConfigParser):
            if section not in cfg:
                cfg.add_section(section)
            cfg.set(section, key, str(value))
            # \default=false 防止模拟器重置
            dk = f"{key}\\default"
            if dk not in [k.lower() for k in cfg.options(section)]:
                cfg.set(section, dk, "false")

    def _qt_set_int(self, cfg, section: str, key: str, value: int):
        self._qt_set_str(cfg, section, key, str(value))

    # ── Qt 模拟器通用控制器 (Eden / Lime3DS) ──

    def _write_qt_player_bindings(self, cfg, player: int = 0):
        section = "Controls"
        if section not in cfg: cfg.add_section(section)
        prefix = f"player_{player}_" if self.platform == "switch" else ""
        cfg.set(section, f"{prefix}connected", "true")
        if self.platform == "switch":
            cfg.set(section, f"{prefix}type", "0")
        buttons = {"a": 1, "b": 0, "x": 3, "y": 2, "l": 9, "r": 10,
                   "up": 11, "down": 12, "left": 13, "right": 14,
                   "start": 6, "select": 4, "plus": 6, "minus": 4,
                   "lstick": 7, "rstick": 8, "home": 5}
        for name, code in buttons.items():
            cfg.set(section, f"{prefix}button_{name}", f'"engine:sdl,joystick:0,guid:{STEAM_DECK_SDL_GUID},port:0,button:{code}"')
        for name, axis in (("zl", 4), ("zr", 5)):
            cfg.set(section, f"{prefix}button_{name}", f'"engine:sdl,joystick:0,guid:{STEAM_DECK_SDL_GUID},port:0,axis:{axis},threshold:0.5,direction:+"')
        sticks = (("lstick", 0, 1), ("rstick", 2, 3)) if self.platform == "switch" else (("circle_pad", 0, 1), ("c_stick", 2, 3))
        for name, x, y in sticks:
            cfg.set(section, f"{prefix}analog_{name}", f'"engine:sdl,joystick:0,guid:{STEAM_DECK_SDL_GUID},port:0,axis_x:{x},axis_y:{y},deadzone:0.15"')

    def bootstrap_install(self, install_dir: str,
                          _settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行安装后初始化。返回 {ok: bool, messages: [...]}"""
        messages: List[str] = []
        appimage = (_settings or {}).get('executable') or self.find_appimage(install_dir)
        if not appimage:
            return {"ok": False, "messages": [f"未找到 {self.display_name} AppImage"]}

        if not self.ensure_executable(appimage):
            return {"ok": False, "messages": [f"{self.display_name} 执行权限设置失败"]}
        else:
            messages.append(f"{self.display_name} AppImage 已就绪")

        try:
            self.enable_portable_mode(install_dir)
        except Exception as e:
            messages.append(f"便携模式设置失败: {e}")

        # BIOS/key imports are explicit operations with backups. Configuration never
        # scans and overwrites firmware based on ambiguous filename fragments.

        config_msgs = self.write_config(install_dir)
        messages.extend(config_msgs)

        return {"ok": True, "messages": messages, "appimage": appimage}


# ═══════════════════════════════════════════════
# DuckStation (PS1)
# ═══════════════════════════════════════════════

class DuckStationAdapter(EmulatorAdapter):
    emulator_id = "duckstation"
    display_name = "DuckStation"
    platform = "ps1"
    package_game_id = "ps1_emulator"
    launch_args = '-batch -fullscreen "{rom}"'
    bios_hints = ["scph"]  # 修复：去掉错误的 "ps2" hint
    portable_write_targets = ["portable.txt"]

    def get_or_create_config_dir(self, root_dir: str) -> str:
        d = root_dir
        os.makedirs(d, exist_ok=True)
        return root_dir

    def write_config(self, root_dir: str) -> List[str]:
        lang = _detect_language()
        ui_lang = "zh-CN" if lang == "zh" else "en"
        settings_dir = root_dir
        os.makedirs(settings_dir, exist_ok=True)
        ini_path = os.path.join(settings_dir, "settings.ini")

        lines = []
        lines.append("[Main]")
        lines.append("MainWindowGeometry = AdnQywADAAAAAAHNAAAAAAAAABQAAAGMAAADNgAAARcAAAAAAAAAFAAAAYwAAAM2AAABFwAAAAAAAAAACgAGAAUAAAAHAAAACAAAAAABAAA=")
        lines.append(f"Language = {ui_lang}")
        lines.append("")

        lines.append("[ControllerPorts]")
        lines.append("MultitapMode = Disabled")
        lines.append("")

        lines.append("[InputSources]")
        lines.append("SDL = true")
        lines.append("")

        # 控制器1 按键映射（SDL 按钮码格式）
        lines.append("[Pad1]")
        lines.append("Type = AnalogController")
        bindings = {
            "Up": "SDL-0/DPadUp", "Down": "SDL-0/DPadDown",
            "Left": "SDL-0/DPadLeft", "Right": "SDL-0/DPadRight",
            "Start": "SDL-0/Start", "Select": "SDL-0/Back",
            "Triangle": "SDL-0/Y", "Circle": "SDL-0/B",
            "Cross": "SDL-0/A", "Square": "SDL-0/X",
            "L1": "SDL-0/LeftShoulder", "R1": "SDL-0/RightShoulder",
            "L2": "SDL-0/+LeftTrigger", "R2": "SDL-0/+RightTrigger",
            "L3": "SDL-0/LeftStick", "R3": "SDL-0/RightStick",
            "LUp": "SDL-0/-LeftY", "LDown": "SDL-0/+LeftY",
            "LLeft": "SDL-0/-LeftX", "LRight": "SDL-0/+LeftX",
            "RUp": "SDL-0/-RightY", "RDown": "SDL-0/+RightY",
            "RLeft": "SDL-0/-RightX", "RRight": "SDL-0/+RightX",
        }
        for k, v in bindings.items():
            lines.append(f"{k} = {v}")
        lines.append("")

        lines.append("[Hotkeys]")
        lines.append("Fullscreen = Keyboard/F11")
        lines.append("")

        lines.append("[General]")
        lines.append("UseOldMDECRoutines = false")
        lines.append("")

        lines.append("[Console]")
        lines.append("Region = Auto")
        lines.append("")

        lines.append("[BIOS]")
        lines.append(f"SearchDirectory = {os.path.join(root_dir, 'bios')}")
        lines.append("")

        lines.append("[Display]")
        lines.append("Fullscreen = true")
        lines.append("")
        lines.append("[GPU]")
        lines.append("Renderer = Vulkan")
        lines.append("ShowOSDMessages = true")
        lines.append("")

        lines.append("[Audio]")
        lines.append("Backend = Cubeb")

        try:
            with open(ini_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return [f"{self.display_name} 配置已写入（中文/Vulkan/Steam Deck 手柄）"]
        except Exception as e:
            return [f"{self.display_name} 配置写入失败: {e}"]


# ═══════════════════════════════════════════════
# PCSX2 (PS2)
# ═══════════════════════════════════════════════

class Pcsx2Adapter(EmulatorAdapter):
    emulator_id = "pcsx2"
    display_name = "PCSX2"
    platform = "ps2"
    package_game_id = "ps2_emulator"
    launch_args = '-fullscreen "{rom}"'
    bios_hints = ["scph", "ps2", "bios"]
    portable_write_targets = ["portable.ini"]

    def get_or_create_config_dir(self, root_dir: str) -> str:
        d = os.path.join(root_dir, "inis")
        os.makedirs(d, exist_ok=True)
        return root_dir

    def write_config(self, root_dir: str) -> List[str]:
        lang = _detect_language()
        ui_lang = "zh-CN" if lang == "zh" else "en"
        inis_dir = os.path.join(root_dir, "inis")
        os.makedirs(inis_dir, exist_ok=True)

        messages: List[str] = []

        # PCSX2.ini
        pcsx2_ini = os.path.join(inis_dir, "PCSX2.ini")
        lines = []
        lines.append("[UI]")
        lines.append("ConfirmShutdown = false")
        lines.append("InhibitScreensaver = true")
        lines.append("Fullscreen = true")
        lines.append(f"Language = {ui_lang}")
        lines.append("")
        lines.append("[Folders]")
        lines.append(f"Bios = {os.path.join(root_dir, 'bios')}")
        lines.append("")
        lines.append("[EmuCore]")
        lines.append("CdvdSource = Iso")
        lines.append("")
        lines.append("[EmuCore/GS]")
        lines.append("Renderer = -1")
        lines.append("")
        lines.extend(["[InputSources]", "SDL = true", "", "[Pad1]", "Type = DualShock2"])
        bindings = {
            "Up": "DPadUp", "Down": "DPadDown", "Left": "DPadLeft", "Right": "DPadRight",
            "Start": "Start", "Select": "Back", "Triangle": "Y", "Circle": "B", "Cross": "A", "Square": "X",
            "L1": "LeftShoulder", "R1": "RightShoulder", "L2": "+LeftTrigger", "R2": "+RightTrigger",
            "L3": "LeftStick", "R3": "RightStick", "LUp": "-LeftY", "LDown": "+LeftY",
            "LLeft": "-LeftX", "LRight": "+LeftX", "RUp": "-RightY", "RDown": "+RightY",
            "RLeft": "-RightX", "RRight": "+RightX",
        }
        lines.extend(f"{key} = SDL-0/{value}" for key, value in bindings.items())
        lines.append("")
        lines.append("[Achievements]")
        lines.append("Enabled = false")
        try:
            with open(pcsx2_ini, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            messages.append(f"{self.display_name} PCSX2.ini 已写入")
        except Exception as e:
            messages.append(f"{self.display_name} PCSX2.ini 失败: {e}")

        # PCSX2_ui.ini — 手柄 SDL 源
        ui_ini = os.path.join(inis_dir, "PCSX2_ui.ini")
        try:
            with open(ui_ini, "w", encoding="utf-8") as f:
                f.write(f"[InputSources]\n")
                f.write("SDL = true\n")
                f.write("\n[Folders]\n")
                f.write(f"Bios = {os.path.join(root_dir, 'bios')}\n")
            messages.append(f"{self.display_name} PCSX2_ui.ini 已写入")
        except Exception as e:
            messages.append(f"{self.display_name} PCSX2_ui.ini 失败: {e}")

        return messages


# ═══════════════════════════════════════════════
# RPCS3 (PS3)
# ═══════════════════════════════════════════════

class Rpcs3Adapter(EmulatorAdapter):
    emulator_id = "rpcs3"
    display_name = "RPCS3"
    platform = "ps3"
    package_game_id = "ps3_emulator"
    launch_args = '--no-gui --fullscreen "{rom}"'
    bios_hints = ["PS3UPDAT.PUP"]

    def write_config(self, root_dir: str) -> List[str]:
        # Steam launch options select this XDG_CONFIG_HOME for both UI and ROMs.
        from pathlib import Path
        import yaml
        config = Path(root_dir) / 'config/rpcs3/config.yml'
        config.parent.mkdir(parents=True, exist_ok=True)
        cfg = yaml.safe_load(config.read_text()) if config.exists() else {}
        cfg = cfg or {}
        cfg.setdefault('Video', {})['Renderer'] = 'Vulkan'
        config.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding='utf-8')
        return ['RPCS3 Vulkan 配置已写入；固件、语言和手柄请在模拟器内完成首次设置']


# ═══════════════════════════════════════════════
# PPSSPP (PSP)
# ═══════════════════════════════════════════════

class PpssppAdapter(EmulatorAdapter):
    emulator_id = "ppsspp"
    display_name = "PPSSPP"
    platform = "psp"
    package_game_id = "psp_emulator"
    launch_args = '"{rom}"'
    portable_write_targets = ["portable.txt"]

    def get_or_create_config_dir(self, root_dir: str) -> str:
        d = os.path.join(root_dir, "memstick", "PSP", "SYSTEM")
        os.makedirs(d, exist_ok=True)
        return root_dir

    def write_config(self, root_dir: str) -> List[str]:
        from pathlib import Path
        import configparser
        directory = Path(root_dir) / 'memstick/PSP/SYSTEM'
        directory.mkdir(parents=True, exist_ok=True)
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config.read(directory / 'ppsspp.ini', encoding='utf-8')
        values = {'General': {'Language': 'zh_CN' if _detect_language() == 'zh' else 'en_US'},
                  'Graphics': {'GraphicsBackend': '3', 'FullScreen': 'True'},
                  'SystemParam': {'Language': '11' if _detect_language() == 'zh' else '1'}}
        for section, entries in values.items():
            if not config.has_section(section): config.add_section(section)
            for key, value in entries.items(): config.set(section, key, value)
        with (directory / 'ppsspp.ini').open('w', encoding='utf-8') as file: config.write(file)
        # PPSSPP's SDL defaults already map a standard controller. Keep controls.ini.
        return ['PPSSPP 语言/Vulkan/全屏配置已写入；使用模拟器自带 SDL 手柄映射，保留自定义按键']


# ═══════════════════════════════════════════════
# Lime3DS (3DS)
# ═══════════════════════════════════════════════

class Lime3dsAdapter(EmulatorAdapter):
    emulator_id = "lime3ds"
    display_name = "Lime3DS"
    platform = "3ds"
    package_game_id = "3ds_emulator"
    launch_args = '"{rom}"'

    def get_or_create_config_dir(self, root_dir: str) -> str:
        d = os.path.join(root_dir, "user", "config")
        os.makedirs(d, exist_ok=True)
        return root_dir

    def write_config(self, root_dir: str) -> List[str]:
        lang = _detect_language()
        ui_lang = "zh" if lang == "zh" else "en"
        import configparser
        config_dir = os.path.join(root_dir, "user", "config")
        os.makedirs(config_dir, exist_ok=True)
        ini = os.path.join(config_dir, "qt-config.ini")

        c = configparser.ConfigParser(interpolation=None)
        c.optionxform = str
        try:
            c.read(ini, encoding="utf-8")
        except Exception:
            pass

        sections = ["UI", "Renderer", "System", "Controls", "Layout", "Audio", "Camera",
                     "Data%20Storage", "Debugging", "WebService"]
        for sec in sections:
            if sec not in c:
                c.add_section(sec)

        self._qt_set_str(c, "UI", "language", ui_lang)
        self._qt_set_str(c, "UI", "confirmClose", "false")
        self._qt_set_str(c, "UI", "singleWindowMode", "true")
        self._qt_set_str(c, "UI", "fullscreen", "true")
        self._qt_set_str(c, "Renderer", "resolution_factor", "2")
        self._qt_set_str(c, "System", "is_new_3ds", "true")
        self._qt_set_int(c, "System", "region_value", 2)
        self._qt_set_str(c, "System", "region_value\\default", "false")

        self._write_qt_player_bindings(c)

        try:
            with open(ini, "w", encoding="utf-8") as f:
                c.write(f)
            return [f"{self.display_name} 配置已写入（语言/2x/SDL 手柄，保留图形后端选择）"]
        except Exception as e:
            return [f"{self.display_name} 配置写入失败: {e}"]


# ═══════════════════════════════════════════════
# mGBA (GBA)
# ═══════════════════════════════════════════════

class MgbaAdapter(EmulatorAdapter):
    emulator_id = "mgba"
    display_name = "mGBA"
    platform = "gba"
    package_game_id = "gba_emulator"
    launch_args = '-f "{rom}"'
    portable_write_targets = ["portable.ini"]

    def write_config(self, root_dir: str) -> List[str]:
        from pathlib import Path
        import configparser
        path = Path(root_dir) / 'config.ini'
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config.read(path, encoding='utf-8')
        if not config.has_section('ports.qt'): config.add_section('ports.qt')
        config.set('ports.qt', 'fullscreen', '1')
        with path.open('w', encoding='utf-8') as file: config.write(file)
        return ['mGBA 便携/全屏配置已写入；保留模拟器自带 SDL 手柄映射']


# ═══════════════════════════════════════════════
# melonDS (NDS)
# ═══════════════════════════════════════════════

class MelondsAdapter(EmulatorAdapter):
    emulator_id = "melonds"
    display_name = "melonDS"
    platform = "nds"
    package_game_id = "nds_emulator"
    launch_args = '"{rom}"'
    bios_hints = ["firmware", "bios7", "bios9", "dsi_firmware"]

    def deploy_bios(self, root_dir: str, install_dir: str) -> List[str]:
        messages: List[str] = []
        if not self.bios_hints:
            return messages
        found = 0
        for hint in self.bios_hints:
            for root, _, files in os.walk(install_dir):
                for f in files:
                    if hint.lower() in f.lower():
                        src = os.path.join(root, f)
                        dst = os.path.join(root_dir, f)
                        try:
                            if os.path.realpath(src) != os.path.realpath(dst):
                                import shutil
                                shutil.copy2(src, dst)
                            found += 1
                        except Exception:
                            pass
        if found > 0:
            messages.append(f"已部署 {found} 个 NDS 固件/BIOS 文件")
        else:
            messages.append("NDS 固件/BIOS 未找到（请手动放置 firmware.bin 等文件）")
        return messages

    def write_config(self, root_dir: str) -> List[str]:
        from pathlib import Path
        # Legacy 0.9.x reads flat key=value lines; 1.x migrates these on first run.
        # Do not overwrite the TOML generated by a newer version.
        if (Path(root_dir) / 'melonDS.toml').exists():
            return ['检测到新版 melonDS.toml，保留现有设置；请在模拟器中调整手柄和显示']
        values = {'JoystickID': '0', 'ScreenLayout': '0', 'ScreenUseGL': '1',
                  'AudioVolume': '256', 'FirmwareLanguage': '1', 'ExternalBIOSEnable': '0',
                  'Joy_A': '1', 'Joy_B': '0', 'Joy_X': '3', 'Joy_Y': '2',
                  'Joy_L': '4', 'Joy_R': '5', 'Joy_Start': '7', 'Joy_Select': '6',
                  'Joy_Up': '257', 'Joy_Down': '260', 'Joy_Left': '264', 'Joy_Right': '258'}
        path = Path(root_dir) / 'melonDS.ini'
        existing = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if '=' in line and not line.startswith(('#', ';')):
                    key, value = line.split('=', 1); existing[key.strip()] = value.strip()
        # Preserve hardware-specific input when already configured.
        for key, value in values.items(): existing.setdefault(key, value)
        path.write_text(''.join(f'{key}={value}\n' for key, value in existing.items()), encoding='utf-8')
        return ['melonDS 0.9.x 配置已写入（新版首次启动可迁移）；触屏与手柄请在模拟器中检查']


# ═══════════════════════════════════════════════
# Nestopia (FC)
# ═══════════════════════════════════════════════

class NestopiaAdapter(EmulatorAdapter):
    emulator_id = "nestopia"
    display_name = "Nestopia"
    platform = "fc"
    package_game_id = "fc_emulator"
    launch_args = '"{rom}"'

    def write_config(self, root_dir: str) -> List[str]:
        from pathlib import Path
        import configparser
        path = Path(root_dir) / 'config/nestopia/nestopia.conf'
        path.parent.mkdir(parents=True, exist_ok=True)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(path, encoding='utf-8')
        if not cfg.has_section('frontend'): cfg.add_section('frontend')
        cfg.set('frontend', 'v_fullscreen', '1')
        with path.open('w', encoding='utf-8') as file: cfg.write(file)
        return ['Nestopia UE 1.53.x 全屏配置已写入；按键请在模拟器 Input 中绑定，保留已有 input.conf']


# ═══════════════════════════════════════════════
# BlastEm (MD)
# ═══════════════════════════════════════════════

class BlastemAdapter(EmulatorAdapter):
    emulator_id = "blastem"
    display_name = "BlastEm"
    platform = "md"
    package_game_id = "md_emulator"
    launch_args = '"{rom}"'

    def find_appimage(self, root_dir: str) -> Optional[str]:
        """md 本体是原生二进制 blastem（非 AppImage），特判查找。"""
        for cand in (os.path.join(root_dir, "blastem64-0.6.2", "blastem"),
                     os.path.join(root_dir, "blastem")):
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        return super().find_appimage(root_dir)

    def write_config(self, root_dir: str) -> List[str]:
        from pathlib import Path
        import re
        path = Path(root_dir) / 'config/blastem/blastem.cfg'
        source = path if path.exists() else Path(root_dir) / 'default.cfg'
        data = source.read_text() if source.exists() else ''
        if 'video {' not in data or '[video]' in data:
            data = """video {
 fullscreen on
 vsync on
 aspect 4:3
}
audio {
 rate 48000
}
io {
 devices {
  1 gamepad6.1
 }
}
bindings {
 keys {
  esc ui.exit
  enter gamepads.1.start
  up gamepads.1.up
  down gamepads.1.down
  left gamepads.1.left
  right gamepads.1.right
  a gamepads.1.a
  s gamepads.1.b
  d gamepads.1.c
 }
 pads {
  default {
   buttons {
    a gamepads.n.a
    b gamepads.n.b
    rightshoulder gamepads.n.c
    x gamepads.n.x
    y gamepads.n.y
    leftshoulder gamepads.n.z
    back gamepads.n.mode
    start gamepads.n.start
   }
   dpads {
    0 {
     up gamepads.n.up
     down gamepads.n.down
     left gamepads.n.left
     right gamepads.n.right
    }
   }
  }
 }
}
"""
        else:
            data = re.sub(r'(?m)^(\s*fullscreen\s+)off\s*$', r'\g<1>on', data)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')
        return ['BlastEm 原生格式配置已写入；优先保留发行包/已有按键设置']


# ═══════════════════════════════════════════════
# Eden (Switch)
# ═══════════════════════════════════════════════

class EdenAdapter(EmulatorAdapter):
    emulator_id = "eden"
    display_name = "Eden"
    platform = "switch"
    package_game_id = "switch_emulator"
    launch_args = '-f -g "{rom}"'

    def get_or_create_config_dir(self, root_dir: str) -> str:
        d = os.path.join(root_dir, "user", "config")
        os.makedirs(d, exist_ok=True)
        return root_dir

    def write_config(self, root_dir: str) -> List[str]:
        lang = _detect_language()
        ui_lang = "zh" if lang == "zh" else "en"

        # 语言/区域索引（参考 EmuDeck）
        lang_map = {"zh": 6, "ja": 0, "ko": 7, "en": 1}
        region_map = {"zh": 4, "ja": 0, "ko": 5, "en": 1}
        sys_lang_idx = lang_map.get(lang, 1)
        sys_region_idx = region_map.get(lang, 1)

        import configparser
        config_dir = os.path.join(root_dir, "user", "config")
        os.makedirs(config_dir, exist_ok=True)
        ini = os.path.join(config_dir, "qt-config.ini")

        c = configparser.ConfigParser(interpolation=None)
        c.optionxform = str
        try:
            c.read(ini, encoding="utf-8")
        except Exception:
            pass

        sections = ["UI", "Renderer", "System", "Controls", "Audio"]
        for sec in sections:
            if sec not in c:
                c.add_section(sec)

        # UI
        self._qt_set_str(c, "UI", "language", ui_lang)
        self._qt_set_str(c, "UI", "confirmClose", "false")
        self._qt_set_str(c, "UI", "singleWindowMode", "true")
        self._qt_set_str(c, "UI", "fullscreen", "true")

        # 渲染
        self._qt_set_str(c, "Renderer", "backend", "1")
        self._qt_set_str(c, "Renderer", "vulkan_device", "0")

        # System — 模拟主机语言/区域（P1）
        self._qt_set_int(c, "System", "language_index", sys_lang_idx)
        self._qt_set_str(c, "System", "language_index\\default", "false")
        self._qt_set_int(c, "System", "region_index", sys_region_idx)
        self._qt_set_str(c, "System", "region_index\\default", "false")
        self._qt_set_str(c, "System", "use_docked_mode", "true")

        # 控制器
        self._write_qt_player_bindings(c)

        try:
            with open(ini, "w", encoding="utf-8") as f:
                c.write(f)
            return [f"{self.display_name} 配置已写入（中文系统/中国区域/Vulkan/全屏/Steam Deck 手柄）"]
        except Exception as e:
            return [f"{self.display_name} 配置写入失败: {e}"]

    def deploy_bios(self, root_dir: str, install_dir: str) -> List[str]:
        """Eden 需要 prod.keys / title.keys。"""
        messages: List[str] = []
        keys_dir = os.path.join(root_dir, "user", "keys")
        os.makedirs(keys_dir, exist_ok=True)
        hints = ["prod.keys", "title.keys", "firmware"]
        found = 0
        for hint in hints:
            for root, _, files in os.walk(install_dir):
                for f in files:
                    if hint.lower() in f.lower():
                        src = os.path.join(root, f)
                        dst = os.path.join(keys_dir, f)
                        try:
                            if os.path.realpath(src) != os.path.realpath(dst):
                                import shutil
                                shutil.copy2(src, dst)
                            found += 1
                        except Exception:
                            pass
        if found > 0:
            messages.append(f"已部署 {found} 个 Switch 密钥文件")
        else:
            messages.append("Switch 模拟器需要 prod.keys / title.keys（请手动放置到 user/keys/）")
        return messages


# ═══════════════════════════════════════════════
# 注册表
# ═══════════════════════════════════════════════

_ADAPTERS: Dict[str, EmulatorAdapter] = {}

def _register():
    global _ADAPTERS
    for cls in [
        EdenAdapter, DuckStationAdapter, Pcsx2Adapter, Rpcs3Adapter,
        PpssppAdapter, Lime3dsAdapter, MgbaAdapter, MelondsAdapter,
        NestopiaAdapter, BlastemAdapter,
    ]:
        inst = cls()
        _ADAPTERS[inst.platform] = inst
        _ADAPTERS[inst.emulator_id] = inst
        _ADAPTERS[inst.package_game_id] = inst

_register()


def get_adapter(platform_or_id: str) -> Optional[EmulatorAdapter]:
    return _ADAPTERS.get(platform_or_id)


def bootstrap_emulator(platform: str, install_dir: str,
                       settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """执行模拟器安装后初始化。"""
    adapter = get_adapter(platform)
    if not adapter:
        return {"ok": False, "messages": [f"未找到 {platform} 模拟器适配器"]}
    return adapter.bootstrap_install(install_dir, settings)
