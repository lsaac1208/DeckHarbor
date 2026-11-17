"""Emulator inventory, local registration and reversible configuration operations."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import stat
import time
import uuid
from emulator_registry import EMULATOR_PLATFORMS
from emulator_deploy import detect_emulator_executable_recursive
from library_state import atomic_json

CONFIGS = {
    'switch': ['user/config/qt-config.ini'],
    'ps1': ['settings.ini', 'portable.txt'],
    'ps2': ['inis/PCSX2.ini', 'inis/PCSX2_ui.ini', 'portable.ini'],
    'ps3': ['config/rpcs3/config.yml'],
    'psp': ['memstick/PSP/SYSTEM/ppsspp.ini', 'portable.txt'],
    '3ds': ['user/config/qt-config.ini'], 'gba': ['config.ini', 'portable.ini'],
    'nds': ['melonDS.ini', 'melonDS.toml'],
    'fc': ['config/nestopia/nestopia.conf'], 'md': ['config/blastem/blastem.cfg'],
}
GUIDANCE = {
    'switch': '密钥放在 user/keys；固件请在模拟器中安装。',
    'ps1': 'BIOS 放在 bios；在 DuckStation 中选择正确区域的 BIOS。',
    'ps2': 'BIOS 放在 bios；在 PCSX2 中选择 BIOS。',
    'ps3': '请在 RPCS3 中安装固件和 PKG；目录游戏选择 PS3_GAME/USRDIR/EBOOT.BIN 导入。',
    'psp': '无需外置 BIOS。', '3ds': 'CIA 请先在模拟器中安装；直接启动可用 .3ds/.cci/.cxi。',
    'gba': '通常无需外置 BIOS；可在模拟器中指定可选 BIOS。',
    'nds': '可使用模拟器内置 BIOS；外置文件放在模拟器目录并在设置中选择。',
    'fc': 'FDS 游戏需要在模拟器内指定磁碟系统 BIOS。',
    'md': '使用 BlastEm 支持的 Mega Drive ROM；不支持的扩展硬件需另选模拟器。',
}


class EmulatorManager:
    def __init__(self, install_base, state_dir):
        self.base = Path(install_base).expanduser().resolve()
        self.state = Path(state_dir)
        self.registry = self.state / 'local_emulators.json'

    def info(self, platform):
        if platform not in EMULATOR_PLATFORMS:
            raise ValueError('未知模拟器平台')
        return EMULATOR_PLATFORMS[platform]

    def registrations(self):
        if not self.registry.exists(): return {}
        data = json.loads(self.registry.read_text())
        if not isinstance(data, dict): raise ValueError('本地模拟器登记文件格式异常')
        return data

    def executable(self, platform):
        self.info(platform)
        entry = self.registrations().get(platform, {})
        if entry:
            path = Path(entry['executable'])
            return str(path) if path.is_file() else None
        return detect_emulator_executable_recursive(str(self.base / 'Emulators' / platform))

    def runtime_root(self, platform):
        executable = self.executable(platform)
        return Path(executable).parent if executable else self.base / 'Emulators' / platform

    def register(self, platform, path):
        self.info(platform)
        executable = Path(path).expanduser().resolve(strict=True)
        if not executable.is_file(): raise ValueError('请选择模拟器可执行文件')
        with executable.open('rb') as file: header = file.read(4)
        if header != b'\x7fELF': raise ValueError('请选择 Linux AppImage 或 ELF 程序')
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        data = self.registrations()
        data[platform] = {'executable': str(executable), 'registered_at': time.time()}
        atomic_json(self.registry, data)
        return self.status(platform)

    def status(self, platform):
        info = self.info(platform)
        executable = self.executable(platform)
        root = self.runtime_root(platform)
        checks = [{'label': '模拟器程序', 'path': executable or str(root), 'ok': bool(executable)},
                  {'label': '执行权限', 'path': executable or '', 'ok': bool(executable and os.access(executable, os.X_OK))}]
        for rel in CONFIGS[platform]:
            if platform == 'nds' and rel == ('melonDS.ini' if (root / 'melonDS.toml').exists() else 'melonDS.toml'): continue
            path = root / rel
            checks.append({'label': '配置文件', 'path': str(path), 'ok': path.is_file() and (rel in ('portable.txt', 'portable.ini') or path.stat().st_size > 0)})
        # Presence checks deliberately do not claim that firmware has been validated.
        firmware = []
        candidates = {'switch': ['user/keys/prod.keys'], 'nds': ['bios7.bin', 'bios9.bin', 'firmware.bin']}.get(platform, [])
        if platform in ('ps1', 'ps2'):
            bios = root / 'bios'
            firmware = [str(p) for p in sorted(bios.glob('*')) if p.is_file() and p.suffix.lower() in ('.bin', '.rom', '.bios')] if bios.is_dir() else []
        else:
            firmware = [str(root / rel) for rel in candidates if (root / rel).is_file()]
        backup_root = self.state / 'emulator_config_backups' / platform
        backups = sorted(p.name for p in backup_root.iterdir() if (p / 'manifest.json').is_file()) if backup_root.exists() else []
        return {'platform': platform, 'name': info['display'], 'adapter': info['adapter'], 'icon': info['icon'],
                'installed': bool(executable), 'executable': executable or '', 'install_dir': str(root),
                'external': platform in self.registrations(), 'checks': checks, 'firmware_files': firmware,
                'guidance': GUIDANCE[platform], 'backup_count': len(backups), 'latest_backup': backups[-1] if backups else ''}

    def backup(self, platform):
        self.info(platform)
        root = self.runtime_root(platform).resolve()
        if str(root) in ('/', '/usr', '/usr/bin', '/bin', '/opt', '/usr/local/bin'):
            raise ValueError('请在模拟器独立目录中使用便携配置，系统共享目录不可写入预设')
        if not self.executable(platform): raise ValueError('请先安装或关联模拟器')
        name = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
        folder = self.state / 'emulator_config_backups' / platform / name
        folder.mkdir(parents=True)
        entries = []
        for rel in CONFIGS[platform]:
            path = root / rel
            if not path.resolve().is_relative_to(root): raise ValueError('配置路径超出模拟器目录')
            exists = path.is_file()
            entries.append({'path': rel, 'existed': exists})
            if exists:
                dest = folder / rel; dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
        atomic_json(folder / 'manifest.json', {'root': str(root), 'entries': entries})
        return name

    def configure(self, platform):
        from emulator_adapters import bootstrap_emulator
        backup = self.backup(platform)
        try:
            result = bootstrap_emulator(platform, str(self.runtime_root(platform)), {'executable': self.executable(platform)})
        except Exception as exc:
            result = {'ok': False, 'messages': [f'配置失败：{exc}；可恢复备份 {backup}']}
        result['backup'] = backup
        # Some adapters catch write failures themselves.
        result['ok'] = bool(result.get('ok')) and not any('失败' in m for m in result.get('messages', []))
        return result

    def restore(self, platform, backup):
        self.info(platform)
        if not backup or Path(backup).name != backup: raise ValueError('无效的备份编号')
        folder = self.state / 'emulator_config_backups' / platform / backup
        manifest = json.loads((folder / 'manifest.json').read_text())
        root = self.runtime_root(platform).resolve()
        if str(root) != manifest['root']: raise ValueError('模拟器位置已经改变，无法恢复该配置')
        entries = manifest['entries']
        for entry in entries:
            if entry['path'] not in CONFIGS[platform]: raise ValueError('备份包含未知配置文件')
            if not (root / entry['path']).resolve().is_relative_to(root): raise ValueError('配置路径超出目录')
            if entry['existed'] and not (folder / entry['path']).is_file(): raise ValueError('备份文件缺失')
        before_restore = self.backup(platform)
        for entry in entries:
            path = root / entry['path']
            if entry['existed']:
                path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(folder / entry['path'], path)
            elif path.is_file(): path.unlink()
        return {'ok': True, 'backup': before_restore, 'messages': ['配置已恢复，恢复前的配置也已备份']}

    def import_firmware(self, platform, path):
        self.info(platform)
        if not self.executable(platform): raise ValueError('请先安装或关联模拟器')
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file(): raise ValueError('请选择 BIOS 或密钥文件')
        name = source.name.lower()
        size = source.stat().st_size
        if platform in ('ps1', 'ps2'):
            if source.suffix.lower() not in ('.bin', '.rom', '.bios') or not 128 * 1024 <= size <= 16 * 1024 * 1024:
                raise ValueError('请选择有效大小的 BIOS 文件（.bin/.rom/.bios）')
            relative = Path('bios') / source.name
        elif platform == 'switch':
            if name not in ('prod.keys', 'title.keys') or not 0 < size <= 4 * 1024 * 1024:
                raise ValueError('请选择 prod.keys 或 title.keys')
            relative = Path('user/keys') / name
        elif platform == 'nds':
            if name not in ('bios7.bin', 'bios9.bin', 'firmware.bin') or not 0 < size <= 16 * 1024 * 1024:
                raise ValueError('请选择 bios7.bin、bios9.bin 或 firmware.bin')
            relative = Path(name)
        else:
            raise ValueError('该平台请在模拟器内安装固件或选择 BIOS')
        root = self.runtime_root(platform).resolve()
        target = root / relative
        if not target.resolve().is_relative_to(root): raise ValueError('目标路径超出模拟器目录')
        if target.resolve() == source: return {'ok': True, 'message': '文件已在正确位置'}
        if target.exists():
            backup = self.state / 'firmware_backups' / platform / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
            backup.mkdir(parents=True)
            shutil.copy2(target, backup / target.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.' + uuid.uuid4().hex[:6] + '.tmp')
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            if temporary.exists(): temporary.unlink()
        return {'ok': True, 'message': '已复制到 ' + str(target) + '；请在模拟器内验证并选择，原文件保留'}

    def deploy(self, platform, source):
        """Move a verified extracted bundle into an unused platform directory."""
        self.info(platform)
        if platform in self.registrations() or self.executable(platform):
            raise ValueError('该平台已有模拟器，请先移除或解除关联后重装')
        source = Path(source).resolve(strict=True)
        if not detect_emulator_executable_recursive(str(source)):
            raise ValueError('安装包中没有可用的模拟器程序，请选择本地 AppImage 关联')
        target = self.base / 'Emulators' / platform
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if any(target.iterdir()): raise ValueError('安装目录含有现有文件，请先在管理页移除并备份')
            target.rmdir()
        if target.is_symlink(): raise ValueError('安装目标不能是符号链接')
        shutil.move(str(source), str(target))
        executable = self.executable(platform)
        if not executable: raise ValueError('部署后未找到模拟器程序')
        path = Path(executable)
        path.chmod(path.stat().st_mode | 0o111)
        from emulator_deploy import mark_deployed
        mark_deployed(platform, str(self.base))
        return self.status(platform)

    def remove(self, platform):
        self.info(platform)
        data = self.registrations()
        if platform in data:
            del data[platform]; atomic_json(self.registry, data)
            return {'ok': True, 'message': '已解除关联，本地模拟器与存档保留', 'backup': ''}
        path = self.base / 'Emulators' / platform
        if path.is_symlink() or path.resolve() != self.base / 'Emulators' / platform:
            raise ValueError('模拟器目录包含符号链接，不能自动移除')
        if not path.exists(): return {'ok': True, 'message': '模拟器尚未安装', 'backup': ''}
        backup = self.state / 'removed_emulators' / (platform + '-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(backup))
        from emulator_deploy import mark_user_removed
        mark_user_removed(platform)
        return {'ok': True, 'message': '模拟器已移出安装目录，配置与存档保留在备份中', 'backup': str(backup)}
