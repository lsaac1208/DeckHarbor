# NewFreedeck 0.4.1

本版修复 SteamOS / Decky 环境中快捷面板、设置和游戏库的布局问题。

- 工具栏和卡片改用 Steam 原生 DialogButton、Dropdown；不再将整行 ButtonItem、DropdownItem 混入紧凑网格。局部修正 Steam 按钮盒模型，避免内边距撑大宽高。
- 快捷面板采用单列、自然高度布局。设置采用分类导航与单一内容滚动区，模拟器管理不再嵌套全屏布局。
- 本地库卡片、筛选器、长路径和标题自适应。目录封面网格按内容宽度计算列数，方向键使用相同列数；标题置于封面底部，避免侧边信息覆盖相邻游戏。
- 下载任务改为自然高度列表，展开后卡片不再重叠，长错误信息可换行。
- 标签页接入 Decky 手柄枚举和原生 Focusable；L1/R1 可从内容区域切换标签，全屏页面支持 B 返回。
- 登录页面使用一致的页面容器；设置读取失败显示重试入口。
- 移除页面中的原作者宣传、打赏图片与入口，插件作者显示“社区维护版”。保留第三方许可证和代码来源说明。

## 验证

- TypeScript 检查、12 项 Node 前端测试、Rollup 生产构建通过。
- 使用本机 Steam SharedJSContext 中真实 DFL 控件与 Steam CSS，后端请求和路由操作使用隔离测试数据。
- 布局检查覆盖 1280×800、1280×720、800×600，以及 300/360 像素快捷面板；包含各设置分类、目录标签、长标题/路径、展开下载列表和设置失败重试。
- 合成 Steam 手柄事件验证 L1/R1 标签切换和 B 返回。此项不等于实体手柄端到端验证。
- 当前 Steam 处于桌面模式，隐藏渲染页面截图不可用于视觉验收；未声称已经完成游戏模式截图或实体手柄实测。
- 本次为前端修复，保留 0.4.0 后端；未重新执行真实云盘下载、游戏启动与逐款模拟器测试。

## 核对的 Decky 官方资料

查阅日期：2026-11-17。

- [Decky Loader 说明](https://github.com/SteamDeckHomebrew/decky-loader)：插件位于快捷面板，开发时需构建、部署并重载。
- [官方插件模板](https://github.com/SteamDeckHomebrew/decky-plugin-template/blob/main/src/index.tsx)：QAM 的 PanelSection / PanelSectionRow 用法、路由跳转与关闭侧栏。
- [Decky UI](https://github.com/SteamDeckHomebrew/decky-frontend-lib)：使用 Steam React 原生组件，避免覆盖宿主 UI。
- [ButtonItem](https://github.com/SteamDeckHomebrew/decky-frontend-lib/blob/main/src/components/ButtonItem.ts)、[Dropdown](https://github.com/SteamDeckHomebrew/decky-frontend-lib/blob/main/src/components/Dropdown.tsx)：区分行组件与独立控件。
- [Focusable](https://github.com/SteamDeckHomebrew/decky-frontend-lib/blob/main/src/components/Focusable.ts)、[FooterLegend](https://github.com/SteamDeckHomebrew/decky-frontend-lib/blob/main/src/components/FooterLegend.ts)：焦点布局、返回事件、手柄按键枚举和底部提示。
