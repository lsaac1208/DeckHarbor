# NewFreedeck 0.4.0

本版集中完成已安装库主页与模拟器中心的功能流程，并保留 0.3.1 的资源搜索、分页、下载及卸载修复。

## 库主页

- 已安装游戏搜索、平台筛选、收藏、最近游玩、正在运行及名称/时长/安装时间排序。
- Steam 启动、Steam 详情、快捷方式修复、移除记录及受目录保护的文件卸载。
- 收藏与游玩记录落盘保存；时长从本版启用后累计，不代表 Steam 历史总时长。按进程约每 10 秒观察，休眠间隔不计时，慢速云同步不阻塞计时。
- 文件移动或存储设备未挂载时保留记录并标记不可用；运行中拒绝移除/卸载。
- 快捷面板、完整库主页、Steam 库中的可选 Freedeck 标签，以及通向发现页/具体模拟器平台的入口。

## 模拟器中心

- PS1、PS2、PS3、PSP、Switch、3DS、GBA、NDS、FC、MD 统一管理。
- 下载与解压后部署到对应平台目录；严格限定当前平台，避免选择其他平台程序或联机服务器。直接下载的 AppImage 只复制任务自己的文件，不移动整个下载目录。
- 关联本地 Linux ELF/AppImage；导入本地 ROM，Steam 创建快捷方式成功后才提交库记录。重复点击不会重复创建，失败时尝试清理新建快捷方式。
- 模拟器通过 Steam 启动；游戏和模拟器界面使用同一配置目录。PSP 显式设置 memstick，RPCS3/Nestopia/BlastEm 显式设置独立 XDG 配置目录。
- 应用配置前备份，支持恢复最近备份，恢复前也会保存当前配置；便携标记纳入备份。只生成配置，不自动复制或覆盖 BIOS/密钥。
- 导入用户自己的 BIOS/密钥，覆盖前另存备份。仅检查文件名称/大小/存在性，实际有效性由模拟器判断。
- 管理的模拟器移入备份保留存档；本地关联程序仅解除关联。安装中或运行中阻止冲突操作。

## 修正的预设

- DuckStation：便携根目录 settings.ini、Pad1 SDL 摇杆键名、GPU 渲染配置。
- PCSX2：便携目录、SDL/DualShock2 输入和自动渲染器选择。
- PPSSPP：GraphicsBackend/FullScreen 字段、memstick 内配置，保留默认或已有 SDL 按键。
- mGBA：config.ini 的 ports.qt 配置，保留默认 SDL 映射。
- BlastEm：使用原生大括号格式，优先保留发行包/已有设置。
- Nestopia UE 1.53.x：实际读取的 nestopia.conf 和 frontend 字段，按键保留由模拟器管理。
- melonDS：0.9.x 扁平 INI；新版本首次运行可迁移，已有 TOML 保留。
- RPCS3：独立配置目录和 Vulkan；固件、语言、控制器首次设置在模拟器中完成。
- Switch/3DS：Qt 配置大小写、SDL 参数格式和平台按键前缀。

## 使用与验证边界

本次验证为临时目录下的后端回归、模拟 Steam API 的前端测试、TypeScript 检查和生产构建；本轮未部署到 Decky（最终检查发现现有安装目录版本为 0.3.1），未进行游戏模式端到端、实体手柄、真实云盘下载或逐款 ROM 实测。Steam 原生库标签注入依赖客户端内部结构，后续 Steam 更新可能需要适配。

下载依赖原有目录服务/云盘登录和可用分享。关联的程序应与所选平台的默认适配器兼容；不同模拟器或大版本的设置格式并不通用。需要 BIOS/固件/密钥的平台仍需用户提供自己的文件，并在模拟器内完成验证。PS3 PKG、3DS CIA 等安装格式需先由模拟器安装，插件直接导入可启动 ROM/EBOOT 文件。

配置备份位于插件数据目录 emulator_config_backups；移除模拟器的完整备份位于 removed_emulators。导入本地 ROM 保留原文件，存储位置改变后需重新导入；改变模拟器配置目录时，旧存档保留在旧目录，需要在模拟器内迁移。

## 配置依据

核对本机模拟器生成的文件，以及上游 mGBA core/config、melonDS qt_sdl/Config、Nestopia 1.53.2 fltkui/setmanager、PPSSPP Core/Config 与 UI/NativeApp、RPCS3 Utilities/File 源码。配置文件生成测试仅证明路径/格式和恢复操作，不等同于所有版本的运行兼容性验证。

## 本次构建结果（2026-11-16）

- Python unittest：43 项通过（全部使用临时目录/模拟服务）。
- Node 前端测试：12 项通过（包括列表筛选、重复导入、失败回滚、64 位 Steam GameID）。
- TypeScript `tsc --noEmit`：通过。
- Rollup 生产构建：通过；20 个顶层 Python 模块语法检查通过。
- 打包器逐文件比对原文件，并校验 ZIP CRC，输出 SHA-256 文件。
