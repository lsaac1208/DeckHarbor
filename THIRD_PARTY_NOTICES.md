# 第三方组件与来源

| 内容 | 来源 / 许可说明 | 仓库处理 |
| --- | --- | --- |
| NewFreedeck 基础代码 | 用户提供的 v0.3 ZIP；项目总许可未附带 | 保留项目来源说明，详见 LICENSE.md |
| Python vdf | https://github.com/ValvePython/vdf ，MIT，Rossen Georgiev | 完整许可保留在 py_modules/vdf/LICENSE |
| Decky API / UI / Rollup | https://github.com/SteamDeckHomebrew ，以各包版本的许可证为准 | 依赖由 npm 安装，package-lock.json 记录版本 |
| React Icons / 各图标集 | https://github.com/react-icons/react-icons ，各图标集可能有独立许可 | npm 依赖，发布时需遵守对应许可 |
| aria2 | https://github.com/aria2/aria2 ，GPL-2.0-or-later，及构建时依赖的许可 | 仓库不提交原始包里的预编译二进制；发布二进制前应提供许可证及相应源码 |
| 7-Zip | https://www.7-zip.org/license.txt ，LGPL 与相关组件条款 | 仓库不提交本机 7zz；发布前核对具体构建的许可、源码和组件 |
| Ludusavi 游戏存档清单 | https://github.com/mtkennerly/ludusavi-manifest | 本机大体积清单未提交；独立准备清单时核对上游当前许可 |
| 模拟器图标与平台图片 | 原始包 / 对应模拟器项目的标识 | 保留资源；相关名称与图形权利归各自权利人，不表示合作或背书 |
| 游戏元数据与图标 | Steam、Flathub、4TU 及条目对应项目 | defaults/community_games.json 保留来源、开发者和已取得的许可信息；不附游戏程序或 ROM |

代码中的参考链接仍保留。有关 Steam、Flathub、4TU 的接口和获取方式见 docs/catalog-sources.md。

第三方标识只用于识别兼容平台和来源，不可把本项目说明理解为这些项目对 DeckHarbor 的认证。若需重新发布包含二进制和资源的安装包，请先逐项确认对应版本的分发条件。
