# 构建与架构

## 工具

前端使用 Node.js 22+、npm、TypeScript、Rollup、@decky/ui 和 @decky/api。后端使用 Python 3.11+ 与 aiohttp，可选 Rust cdylib 用于部分原生功能。目标环境是 x86_64 Linux / SteamOS。

```sh
npm ci
npm run typecheck
npm test
npm run build
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -v
```

测试替换 Steam 与云服务调用，使用临时文件，不需要真实账号或商业游戏。Rust 核心缺失时部分功能使用 Python 回退；下载与解压仍需下面的独立工具。

## 运行依赖与打包

仓库不提交本机预编译程序。自行从官方源码构建适用于 SteamOS 的 `aria2c` 和 `7zz`，分别放到 `defaults/bin/aria2c` 与 `defaults/bin/7zz` 并赋予执行权限。不要使用来源不明的二进制；Ubuntu 构建结果也不当然兼容 SteamOS 的 libc。

- aria2：https://github.com/aria2/aria2
- 7-Zip：https://www.7-zip.org/download.html

可选原生核心：安装 Rust 与 C 工具链后运行 `bash build_rust.sh`。脚本会把构建结果复制到 `py_modules/libnewfreedeck_core.so`；仓库忽略本机链接库和 Cargo 临时配置。

云存档匹配需要 Ludusavi 清单。项目原有大体积清单未纳入 Git，可从 https://github.com/mtkennerly/ludusavi-manifest 核对版本和许可后准备 `defaults/ludusavi_manifest.yaml` / JSON；不使用该功能时无需把清单混进源码。

完成运行依赖和前端构建后：

```sh
python3 tools/package_plugin.py /tmp/DeckHarbor.v.0.4.3.zip
```

脚本校验运行依赖、ZIP CRC 和逐文件内容，并生成 SHA-256 文件。插件包与源码包不同；外发二进制前还需完成第三方分发许可及相应源码准备。本仓库首次公开不上传旧本机 ZIP。

## 可选服务配置

公开源码没有内置服务凭据：

| 环境变量 | 用途 |
| --- | --- |
| DECKHARBOR_SGDB_API_KEY | 可选 SteamGridDB 封面查询；为空时跳过该层 |
| DECKHARBOR_BAIDU_APP_KEY | 用户自己注册的百度 OAuth 应用标识 |
| DECKHARBOR_BAIDU_SECRET_KEY | 对应应用 Secret |

`.env.example` 仅作说明，Decky 不会自动加载它。高级用户需把变量放入实际插件加载进程的环境，并重启相应服务；配置文件要限制权限，不要写入源码、截图或日志。百度应用回调需与代码中的 `http://127.0.0.1:20065/baidu/oauth/callback` 匹配。

## 代码结构

| 路径 | 用途 |
| --- | --- |
| src/ | 页面、原生控件、路由、Steam 导入与下载状态 |
| main.py | Decky RPC、后端编排与插件生命周期 |
| py_modules/ | 缓存、来源、游戏库、模拟器、下载及云服务模块 |
| defaults/ | 随包来源元数据、平台图标与配置模板 |
| newfreedeck_core/ | 可选 Rust 核心源码 |
| tests/ | 前后端离线回归测试 |
| tools/ui-review/ | 隔离 Steam 原生界面检查与截图导出 |

旧状态路径、事件名、路由与 ABI 标识继续保留以兼容 NewFreedeck。新的公开身份使用 DeckHarbor。

## 截图与界面验证

详见 tools/ui-review/README.md。使用 `build-preview.cjs --docs` 载入真实组件与公开条目演示，再用 `export-docs.mjs` 导出 DOM，在独立浏览器配置中渲染。不要导出 Steam 私人页面或真实登录数据。预览截图不能替代游戏模式与实体手柄测试。
