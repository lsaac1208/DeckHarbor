# 游戏来源与获取方式

核对日期：2026-11-17。随包元数据保存在 `defaults/community_games.json`，不包含游戏安装文件或 ROM。新增共 56 个来源条目，并非 56 款互不重复的游戏。

| 来源 | 条目 | 获取方式 | 示例 |
| --- | ---: | --- | --- |
| Steam 官方商店 | 10 | 打开商店页面获取，价格、地区可用性与 Deck 兼容性以商店为准 | Dota 2、Endless Sky、Helltaker、HoloCure |
| Flathub | 26 | 打开应用安装页；在 SteamOS 桌面模式通过 Discover / Flatpak 安装 | SuperTuxKart、OpenTTD、0 A.D.、Mindustry、Luanti |
| 4TU Homebrew App Store | 20 | 打开项目页获取 Switch 自制游戏 | 2048、OpenTTD、BrogueCE、SuperTux、Sudoku |

Steam 条目核对了商店游戏类型与免费状态；更新时若价格状态改变，详情不再标为免费。Linux 原生或 Windows 游戏都不等于已通过 Steam Deck 验证，HoloCure 等游戏需要另行确认 Proton 兼容性。

Flathub 条目核对了 Game 分类、免费许可证与非 EOL 状态。已停更的旧 Minetest 应用 ID 未采用，使用当前 Luanti 条目。安装结果不会由本插件自动记为“已安装”。

4TU 的这些条目是自制游戏，并非商业 Switch 游戏资源包。多数以 NRO 在支持 homebrew 的 Switch / hbmenu 环境中使用；在 Switch 模拟器中能否运行尚未验证。需要额外商业游戏数据的移植项目未纳入本次精选列表。

## 上游资料

- Steam 商店：https://store.steampowered.com/
- Steam 游戏详情接口：`https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese`
- Flathub：https://flathub.org/
- Flathub 元数据接口：`https://flathub.org/api/v2/appstream/{appID}`
- 4TU 目录：https://apps.fortheusers.org/switch
- 4TU 元数据：https://switch.cdn.fortheusers.org/repo.json
- Homebrew App Store 项目与使用环境：https://github.com/fortheusers/hb-appstore
- Switchbrew 应用说明：https://switchbrew.org/wiki/Homebrew_Applications

每个随包条目均保留来源、项目链接和已取得的作者、许可证信息。新增适配器只更新精选条目，不会自动收录整个第三方目录。

## 缓存与可用性

先读取旧聚合目录、随包元数据及分来源缓存，再独立后台更新。默认 30 分钟内复用缓存，失败后至少间隔 60 秒才自动重试；可在“来源”页手动更新。旧来源本次联网核对超时，因此仍保留本地内容，不能据此保证旧下载链接可用。

旧目录中的 85 条 `ns` 记录通过别名映射恢复到 Switch 平台。分组去重后为 81 项，加上 20 个自制游戏条目，基准数据中显示 101 项；实际数量随用户缓存而变化。

新来源不会调用网盘下载器，也不会自动安装或导入 Steam。游戏页面会说明获取方式，随后打开相应上游页面。
