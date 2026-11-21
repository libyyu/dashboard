# 更新记录

**仓库约定：每次代码改动都升版本号，并在这里记录更新内容。**

- 版本单一真源是 `VERSION`，用 `python scripts/bump_version.py --notes "..."` 升级并回写所有派生点；
  `python scripts/ci_cheap.py` 会拦截「改了代码却没升版本 / 没写更新内容」。
- 自动更新弹窗的「更新说明」默认读本文件最新版本条目（`scripts/build_update_manifest.py`）。
- 格式：`## [版本] - YYYY-MM-DD`，按「新增 / 修复 / 变更」分组。

## [1.0.32] - 2026-11-21

### 变更

- 更新源切到 devcloud 直连端口 :8700（绕开 iOA 对 80/443 Web 端口的未签名进程拦截）；registry latest.json 按请求入口回写下载地址

## [1.0.31] - 2026-11-21

### 变更

- 更新通道切 http 内网直连绕开安全软件对 updater 进程 TLS 栈的干扰（SEC_E_INVALID_HANDLE）；签名校验仍强制兜底；错误源链写入更新日志

## [1.0.30] - 2026-11-21

### 变更

- 更新检查失败时把完整错误源链（DNS/TCP/TLS 根因）写入 desktop-update.log，替代只有顶层的 error sending request

## [1.0.29] - 2026-11-21

### 变更

- UI 换回中性现代风：冷灰近白表面 + 靛蓝主色（Linear/Vercel 式克制风格），去掉 webfont 依赖改用系统字体栈，favicon 同步换靛蓝

## [1.0.28] - 2026-11-21

### 变更

- 修复自动更新检查失败：updater 从 rustls 切换到系统 TLS（Schannel），对齐浏览器/curl 的证书信任链

## [1.0.27] - 2026-11-21

### 变更

- 插件仓库卡片紧凑化：已安装插件详情（含 CLI 运行环境检查与修复）改为悬浮弹出显示

## [1.0.26] - 2026-11-21

### 变更

- 插件仓库改为「已安装 / 市场」两个页签；codebuddy-cli 运行环境检查与修复内嵌到已安装插件卡片上

## [1.0.25] - 2026-11-21

### 变更

- 插件前端路由按 routes[].entry 运行时懒加载，加载失败回退占位页
- changelog 自动同步进发版流：bump_version 升版本即同步 registry 的 changelog.json
- 新增一键发版脚本 scripts/release.py（bump → ci_cheap → 构建 → 校验 → 上传 registry）
- 自动更新切 https 正式端点（maxweili.devcloud.woa.com/registry），移除 insecure 联调开关
- 修复 sync_changelog.py --dry-run 在 Windows 控制台的 GBK 编码错误

## [1.0.24] - 2026-11-20

### 变更

- 运行时安装幂等改用入口文件 + --version 实测；环境检查面板增加登录引导

## [1.0.23] - 2026-11-20

### 变更

- CodeBuddy CLI 动态获取模型列表（37 个）；模型显示带 provider 前缀

## [1.0.22] - 2026-11-20

### 变更

- 插件商店 codebuddy-cli 环境自动检查

## [1.0.21] - 2026-11-20

### 变更

- codebuddy-cli 能力以插件安装为总开关：未装插件时 provider 一律不暴露、相关服务禁用（即使全局有 node/codebuddy）

## [1.0.20] - 2026-11-20

### 变更

- codebuddy-cli 优先使用全局公开版 codebuddy（@tencent-ai/codebuddy-code，自带 cli-external-link 登录、脱离 IDE 无感），插件内嵌 WorkBuddy 版兜底

## [1.0.19] - 2026-11-20

### 变更

- codebuddy-cli 接入登录引导：登录态缺失映射 PROVIDER_AUTH_REQUIRED，auth 端点支持保存/复用 API key，前端 CLI 登录弹窗填 key

## [1.0.18] - 2026-11-20

### 变更

- 插件商店支持一键更新（已装版本 < catalog latest 显示「更新至」按钮，后端更新时清理旧版本目录）

## [1.0.17] - 2026-11-20

### 变更

- 插件安装增加实时进度条；codebuddy-cli 插件 2.0.1 补齐 product.json/builtin（修复 Agent template unavailable）；修复前端两个既有类型错误

## [1.0.16] - 2026-11-20

### 变更

- 更新源切换到 plateau-registry：updater 端点改为 http://127.0.0.1:8700/update/latest.json，registry 兼容旧包根路径 /latest.json

## [1.0.15] - 2026-11-19

### 变更
- 侧边栏改为可拖拽调宽：拖右缘调整宽度（72~360px），低于 128px 自动切换图标模式（折叠 rail，悬停出 tooltip）；移除导航样式设置与「胶囊」类别（宽度即模式，无需显式切换），宽度持久化。
- AI 助手新增「未配置模型服务」空态引导：无可用 provider 时展示三角标 + 说明 + 「去插件仓库安装」/「重新检测」按钮。
- 插件仓库安装/卸载补充成功提示（「重启 Plateau 后生效」）与失败提示。

## [1.0.14] - 2026-11-19

### 新增
- 主题切换：浅色 / 深色两套「地形图集」配色（`[data-theme]` 覆盖全部 token + Element Plus 变量），头部新增设置弹层，偏好落 localStorage 跨会话保留。
- 侧边导航三种样式可切换（设置弹层内选择，同样持久化）：列表（现状）、图标栏（折叠 rail，悬停出 tooltip）、胶囊（浮动圆角 dock，激活项朱红底）；侧边栏宽度随样式平滑过渡。

## [1.0.13] - 2026-11-19

### 变更
- 视觉对齐 `plateau-registry` 的「地形图集（Topographic Atlas）」设计系统：暖纸底 + 深墨 + 测量朱红 + 地形青配色（oklch 定义）；展示字 Fraunces / 正文 Outfit / 等宽 JetBrains Mono（离线回退 Georgia / 系统无衬线 / ui-monospace）。
- 侧边栏改浅色暖纸、与内容区同源：三角朱红标 + 衬线「Plateau」字标 + 等宽「DASHBOARD」副标；菜单项改为圆角、悬停暖纸、激活项朱红左侧指示条 + 暖朱红底色；分组标题改等宽大写字距（地形青）。
- 头部改半透明毛玻璃、衬线标题；卡片/面板/对话气泡等全局组件随 token 自动继承新配色。

## [1.0.12] - 2026-11-19

### 变更
- 全局视觉翻新（克制精致的现代专业风，浅色为主 + 深色侧边栏对比）：统一设计 token（品牌靛蓝、偏冷灰中性色、三档圆角、柔和阴影、ease-out 动效、`--pd-*` 变量 + Element Plus 主题变量全局覆盖）；细化侧边栏（药丸式菜单项、左侧高亮指示、分组标题字距）、头部、卡片/面板（hairline 边框 + 柔和阴影）。
- 对话气泡与空态重设计：用户/助手气泡精细化（14px 圆角、浅阴影、危险色对齐 token），空态图标改为品牌色圆底标记。
- 模型弹层修复与增强：模型下拉改为按服务分组单列表，新增「刷新」按钮与加载态（模型拉取失败时不再只剩默认项）；思考强度滑块标注字号/间距修正，新增「部分模型不支持思考，将自动忽略」提示。

## [1.0.11] - 2026-11-19

### 变更
- 模型选择弹层简化为单列表：模型按服务分组（provider 作分组头，`el-option-group`），选中即同时确定服务与模型，去掉「服务 / 模型」两个下拉；模型列表改为启动/刷新时全量并行预取（单个 provider 失败 fail-open 为空分组）。
- 新增思考强度滑块：弹层内「思考强度」低/中/高三档（默认中），触发按钮以主色标注当前档位；`reasoning_effort` 经 `/api/agent/runs/stream` 透传到 provider 上游（cb `/v1/agent/events` 与 OpenAI 兼容 `/v1/chat/completions` 均透传，缺省不带）。

## [1.0.10] - 2026-11-19

### 变更
- 对话组件通用化：`ChatSession` 抽为可复用会话组件，新增 `placeholder` / `inputHint` / `sessionId` 可选 props，任何需要对话能力的页面直接 `<ChatSession />` 即可；页面（ChatView）只保留外壳与标题。
- 模型选择移入输入组件（对齐通用智能体交互）：新增 `useModelSelection` composable（providers/models 加载、切换、登录引导）；`ChatInput` 工具条内嵌「服务 / 模型」弹层选择器与「去登录」按钮，移除会话顶部 provider 栏；`send` 事件改为携带 `{ text, provider, model }`。

## [1.0.9] - 2026-11-19

### 新增
- AI 助手会话顶部新增模型 provider / model 选择栏：provider 下拉读 `/api/model/providers`（注册表 KV ∪ 插件扫描），model 下拉读 `/api/model/{provider}/models`，发送时携带 `provider` / `model`。
- 登录引导：provider 栏对 `auth=login` 的供应商显示「去登录」按钮；错误气泡在错误码 `PROVIDER_AUTH_REQUIRED` 时展示「去登录」，点击调 `/api/model/{provider}/auth/login`，成功后刷新 provider 目录。

### 变更
- 前端 mock 补充 `/api/model/cb-openai/auth/login` 返回，离线模式（`VITE_API_MOCK=1`）下登录引导不落空。

### 修复
- 修复后端循环导入导致 dashboard 无法启动：`model.py` ↔ `runtime.py` 互相 import（model 引 runtime 的 `_get_storage`，runtime 引 model 的 `_default_provider`）；抽出共享单例工厂 `app/adapters/storage_factory.py::get_storage`，`runtime` / `model` / `agent` 统一引用。

## [1.0.8] - 2026-11-19

### 新增
- AI 助手对话模块（网页版）：前端 `components/chat/` 组件库（ChatSession / ChatInput / ChatMessageList / ChatMessageItem / ChatThinking / ChatToolCall）+ `useChatStream` 流式 composable（eventsource-parser 切帧 / 增量拼接 / 取消 / 离线 mock），注册 `/chat` 路由与侧边栏「AI 助手」菜单。
- `api/http.ts` 导出 `BASE_URL` / `MOCK` / `newRequestId`，供流式 fetch 复用。

### 修复
- `SessionStore.append_event` 自死锁：持锁时再次调用同样加锁的 `ensure_session`（`threading.Lock` 不可重入），导致 `/api/agent/runs/stream` 连首帧都不吐、前端一直转圈；抽出 `_ensure_session_unlocked` 供锁内调用。
- `seq` 恒为 0：`int(x or -1)` 把合法首帧 `seq=0` 当成缺失，导致 SSE `id` 重复、`Last-Event-ID` 断链、历史回放丢首帧；改用 `_int_or` 并同步修正 4 处调用点。

### 变更
- AI 助手依赖 cb 插件的 `/v1/agent/events` 原生统一事件端点：cb 源码仓已实现，但 registry 上的 `cb-openai 2.0.0` 包体仍是旧版（缺该路由），需重新打包发布；本机已手动同步源码仓 `server.py` + `cbopenai/service.py` 用于网页版联调。

## [1.0.7] - 2026-11-18

### 变更

- 修复 WebView 缓存旧 JS：SPA 壳(index.html)强制 no-cache/no-store/must-revalidate，assets 哈希产物走 immutable 长期缓存，解决重新部署后界面仍停留在旧版本的问题

## [1.0.6] - 2026-11-18

### 变更

- 插件 ID 从 cb-agent 改为 cb-openai：cb 是 OpenAI 兼容大模型服务，不是智能体；registry 目录、catalog、manifest、打包脚本、端口断言、前端 mock 与设计文档全量同步
- 已装旧 ID 的存量数据在本机不存在，无需迁移；旧路径 /plugins/cb-agent/* 停止服务

## [1.0.5] - 2026-11-18

### 变更

- cb 插件改名「CB大模型」并移除 menu/routes：sidecar 纯大模型服务不再提供菜单与页面；前端 registerPluginRoutes 跳过无 routes 的纯服务插件
- 打通 model 能力模型：requires 白名单纳入 model（插件能力），provides 校验白名单为 model，registry 与 dashboard 后端能力清单对齐
- 安装时新增依赖满足检查：requires 含 model 但本机无 enabled 插件提供时返回 PLUGIN_CAPABILITY_UNSATISFIED
- /api/plugins/capabilities 新增 model 能力条目（provider=sidecar，satisfied 按已装插件与端口动态判定）
- 开发态 registry_server.py 手工 catalog 路径与 FastAPI 服务统一为 root/catalog.json

## [1.0.4] - 2026-11-18

### 新增
- P2 launcher 动态注册：`desktop/launcher.py` 扫描 `profile/plugins/*/{version}/plugin.json`（筛 `kind=sidecar && enabled`），合成服务名 `plugin-<id>` 并入服务清单，按 `ports.resolve_port` 分端口、展开 `{port}/{plugin_dir}/{config_dir}/{python}` 占位符，复用现有 spawn + stdout 泵 + 分层探活，sidecar 失败 fail-open。
- launcher 注入 `PLATEAU_REGISTRY_URL`（默认 `http://127.0.0.1:8700`）；`ci_cheap.py` 新增「launcher 注入插件 registry 地址」守卫。

## [1.0.3] - 2026-11-18

### 新增
- P1 本地插件运行时：新增 `backend/app/routers/plugins.py`，落地 `/api/plugins`（本机清单）、`/api/plugins/capabilities`（能力清单）、`/api/plugins/install`、`/api/plugins/uninstall`、`/api/plugins/registry`（catalog 透传）五个端点，复用 SQLite `plateau_kv` 的 `plugins` collection（零 migration）；安装含 manifest 校验 + 下载 + sha256 校验 + 落盘 + 写库。
- `contracts/api.json` 五个插件端点 `implemented` 置 true；`ci_cheap.py` 的 `backend_files` 纳入 `plugins.py`，使契约端点注册校验覆盖插件运行时。

## [1.0.2] - 2026-11-18

### 新增
- cb 彻底插件化 P0 契约：`contracts/api.json` 新增 `/api/plugins`、`/api/plugins/capabilities`、`/api/plugins/install`、`/api/plugins/uninstall`、`/api/plugins/registry` 五个端点（install/uninstall 归 CONTROL，其余 BEST_EFFORT，均为 `implemented:false` 待后续阶段落地）。
- 新增错误码 `PLUGIN_NOT_FOUND` / `PLUGIN_CHECKSUM_MISMATCH` / `PLUGIN_CAPABILITY_UNSATISFIED`，同步 `core/errors.py`、`traffic_lanes.py` 车道分类与前端 `contract.ts` 的 Plugin/Capability 类型与 ENDPOINTS。

## [1.0.1] - 2026-11-17

### 新增
- 版本升级与更新记录机制：`CHANGELOG.md` + `scripts/bump_version.py`，`sync_version.py` 同步范围补上 `contracts/api.json`（VERSION + 6 处）。
- P1 数据层：新增 `backend/app/adapters/`（`SqliteStorage` + 版本化迁移），零外部依赖，兼容 PyInstaller 冻结包；
  launcher 注入 `DATABASE_URL`，新增 `GET /api/runtime/storage` 用于验收。
- 考察 `plateau-atlas-decompiled`：确认它是文本检索的**参考实现**而非数据源，真实文本来自本地 P4 工作区。

### 修复
- P0 基线校正：README 里不存在的 `views/Home.vue` 改为 `ModulePlaceholder.vue`；
  `ci_cheap.py` 的契约端点校验由「子串包含」改为 AST 精确匹配，并对 `implemented:false` 加反向断言；
  `Agent 端口齐备` 的假绿灯改为「仅定义契约（尚无实现）」。

### 变更
- 契约 `database.enabled` 置为 true，迁移规则由 Alembic 改为「adapter 内版本化迁移脚本」（业务层不写 DDL，且不引入 SQLAlchemy 依赖）。
- 契约版本随 `VERSION` 一起纳入同步与守卫。

## [1.0.0] - 2026-11-17

### 新增
- 初始源码基线：FastAPI 运行时 / 日志 / 契约 / Vue 控制台 / Tauri 桌面壳。
- 桌面包：NSIS 安装包、SDK CLI 随包、单实例、托盘优雅退出、自动更新（本地服务器）。

### 修复
- 冷启动无限转圈、stale lock 接管、陈旧端口、日志编码与丢失。
- 子进程 stdout GBK 乱码、WebView2 首帧白屏。
