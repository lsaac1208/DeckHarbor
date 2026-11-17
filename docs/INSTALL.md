# 安装与升级

## 环境与安装包

需要 Steam Deck / x86_64 SteamOS、Steam 和 Decky Loader。建议先在桌面模式完成安装，再回游戏模式检查操作。

插件 ZIP 的根目录应为 `DeckHarbor/`，包含 `plugin.json`、`main.py`、`dist/`、`py_modules/` 和运行资源。GitHub 自动生成的源码 ZIP 缺少构建结果和运行依赖，不能直接安装。

在 Decky 设置中开启开发者选项，选择从 ZIP 安装。菜单名称随 Decky 版本变化。安装后在插件列表中找到 DeckHarbor。

本仓库首次公开发布以源码和文档为主；没有 Release 安装包时，请按 [构建文档](DEVELOPMENT.md) 自行构建，不要把源码包当成成品包。

## 从 NewFreedeck 升级

1. 等待下载和解压结束，退出模拟器游戏。
2. 备份 `~/.local/share/NewFreedeck`。其中可能包含登录凭据，不要上传到 Issues。
3. 使用 Decky 卸载 NewFreedeck，保留游戏、存档与数据目录。
4. 安装 DeckHarbor ZIP，核对“我的游戏”、下载路径和模拟器关联。

新版本沿用旧数据位置，不需移动游戏或重建 Steam 快捷方式。两个插件同时运行会重复注册内部路由、库标签和后台任务，请只启用其中一个。

## 数据位置

| 内容 | 默认位置 |
| --- | --- |
| 插件 | `~/homebrew/plugins/DeckHarbor`，以 Decky 配置为准 |
| 设置、缓存与游戏记录 | `~/.local/share/NewFreedeck` |
| 游戏与模拟器 | `~/FreedeckGames`，可在设置修改 |
| 日志 | `~/.local/share/NewFreedeck/plugin.log` |

云存档仍沿用旧版位置。保留旧路径是兼容策略。

## 卸载与回退

通过 Decky 卸载插件。插件卸载与游戏文件删除是不同操作：需要保留游戏时不要手动删除游戏目录。回退前先卸载新版，再安装旧版；恢复备份时核对版本兼容性。

本项目不会替你安装 Decky、修改 SteamOS 系统分区或自动下载 BIOS / 商业 ROM。
