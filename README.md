# Plateau Dashboard

平台工作台。使用前后端框架搭建，后续逐步扩展玩法图谱 / 文本检索 / AI 助手等模块。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + Vite + TypeScript + Element Plus + Vue Router + Pinia |
| 后端 | Python 3.10 + FastAPI + uvicorn |
| 契约 | `contracts/api.json` 单一真源，前后端各自独立验证 |

## 目录结构

```
plateau-dashboard/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI 入口（挂 /api 路由 + 生产态托管前端 dist）
│   │   ├── core/            # 领域内核：统一错误契约、DB 端口、Agent 端口（stdlib only）
│   │   ├── routers/         # 每模块一个路由文件（runtime.py 运行时聚合）
│   │   └── runtime/         # 底座：日志、端口分配、分层探活、车道隔离（与业务解耦）
│   ├── requirements.txt
│   └── run.py               # python run.py 启动
├── contracts/
│   └── api.json             # 前后端契约单一真源（端点 / 车道 / 错误码 / 架构约束）
├── desktop/                 # 桌面包装配层：多服务编排 + Tauri 壳
│   ├── launcher.py          # 单 owner 启动 / 探活 / 关停 dashboard + 已装 sidecar 插件
│   ├── services.json        # 包清单（产品元数据 + 服务清单，仅 dashboard）
│   ├── package.json         # Tauri CLI 入口
│   └── src-tauri/           # Rust 薄壳：spawn sidecar / 探活导航 / 关闭清理
│       ├── src/             # lib.rs / main.rs / spawn.rs / updater.rs（自动更新）
│       ├── loading/         # 首屏 loading 页（启动失败时展示原因）
│       ├── icons/           # 应用图标
│       └── Cargo.toml / Cargo.lock / build.rs / tauri.conf.json
├── frontend/
│   ├── src/
│   │   ├── layouts/WorkbenchLayout.vue  # 工作台外壳（侧边栏 + 头部 + 内容区）
│   │   ├── views/ModulePlaceholder.vue  # 模块占位页（graph / text / ai 建设中）
│   │   ├── router/                      # 路由（后续模块在此注册）
│   │   ├── stores/                      # Pinia 状态
│   │   └── api/                         # 请求封装（契约类型 + mock + http）
│   ├── package.json
│   ├── vite.config.ts                   # 开发代理，目标来自 VITE_API_TARGET
│   └── index.html
├── packaging/               # PyInstaller spec + 冻结构建/组装脚本（build.py）
├── scripts/
│   ├── ci_cheap.py               # 零依赖静态守卫（契约 / 解耦 / 车道 / 端口 / 日志 / 更新通道）
│   ├── sync_version.py           # 版本号同步（VERSION + 6 处派生）
│   ├── bump_version.py           # 升版本 + 记录更新内容 + 回写派生点
│   ├── build_update_manifest.py  # 从 VERSION 生成 / 校验 updater 的 latest.json
│   └── make_icon.py              # 生成多尺寸应用图标
├── backend/app/adapters/        # 存储端口实现（SQLite + 版本化迁移）
├── CHANGELOG.md                 # 每次改动的更新内容
└── VERSION                  # 版本单一真源
```

## 快速开始

### 1. 后端

```bash
cd backend
pip install -r requirements.txt
python run.py
# 端口按 profile 哈希派生（8100..8999），优先读取环境变量 PORT
```

验证：`curl http://127.0.0.1:<port>/health/liveness` → `{"status":"alive",...}`

### 2. 前端（开发模式）

```bash
cd frontend
npm install
npm run dev
# 访问 http://127.0.0.1:5173（/api、/health、/auth、/v1 自动代理到 VITE_API_TARGET，默认 http://127.0.0.1:8580）
```

离线独立开发：`VITE_API_MOCK=1 npm run dev`，完全不连后端。

### 3. 生产模式（单端口）

```bash
cd frontend && npm run build
cd ../backend && python run.py
# FastAPI 直接托管 frontend/dist，深链回退 index.html
```

### 4. 源码态一键启动

```bash
python desktop/launcher.py            # 常驻，Ctrl+C 有序关停
python desktop/launcher.py --dry-run  # 只打印解析结果
```

### 5. 构建 Windows 桌面包（NSIS 安装包）

依赖：Python 3.10 + PyInstaller、Rust、Node.js、`@tauri-apps/cli`。

```bash
cd frontend && npm install && npm run build && cd ..
python packaging/build.py all --assemble
python packaging/build.py verify           # 校验两个 exe / 冻结清单 / 前端产物齐备
cd desktop && npm install
# Windows PowerShell：更新签名私钥必须作为真实环境变量注入（.env 文件不生效）
$env:TAURI_SIGNING_PRIVATE_KEY = "$env:USERPROFILE\.tauri\plateau.key"
npx tauri build
# 产物：desktop/src-tauri/target/release/bundle/nsis/
#   Plateau_1.0.0_x64-setup.exe      安装包
#   Plateau_1.0.0_x64-setup.exe.sig  updater 签名
```

cb 已外置为 sidecar 插件，不再随桌面包内置：用 `scripts/build_cb_plugin.py` 打包成
`cb-openai-<version>.zip` 上传 registry，桌面包通过 `/api/plugins/install` 安装后由 launcher 拉起。

桌面壳是单实例：重复打开会把已有窗口唤到前台。关闭窗口只隐藏到托盘，服务继续运行；
托盘菜单提供「显示窗口 / 打开日志目录 / 检查更新 / 退出 Plateau」，退出时通过 `launcher.stop`
触发有序关停（关停 dashboard 及已装 sidecar 插件，并清理 `launcher.lock` / `launcher.ready.json`）。

`build/`、`desktop/src-tauri/binaries/`、`desktop/src-tauri/target/`、`packaging/vendor/`
均为构建产物，不入库。图标由 `scripts/make_icon.py` 生成，可直接覆盖 `icons/icon.ico` 换成正式素材。
cb 插件打包脚本 `scripts/build_cb_plugin.py` 从 `PLATEAU_CB_REPO` 读取 cb 仓库。

### 6. 自动更新（当前：plateau-registry 正式端点）

更新源是 **plateau-registry**，`tauri.conf.json` 里 `plugins.updater.endpoints` 指向
`https://maxweili.devcloud.woa.com/registry/update/latest.json`（HTTPS，未开启 insecure 传输）。
**签名校验由插件强制执行、不可关闭**，被篡改的安装包会被拒绝。
`ci_cheap.py` 会拦截「insecure 开关 + 非 loopback 端点」的组合，防止联调配置被带回 http 明文。

#### 签名密钥

```powershell
npx tauri signer generate -w "$env:USERPROFILE\.tauri\plateau.key" --ci
```

- 公钥已写入 `tauri.conf.json` 的 `plugins.updater.pubkey`（可入库）。
- 私钥在 `~/.tauri/plateau.key`，**不入库**（`.gitignore` 已排除 `*.key`）。
  私钥丢失 = 所有已安装用户永远收不到后续更新，请立即备份到密码管理器；
  CI 用 `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` 两个 Secret。
- `cargo check` / `npx tauri dev` 不需要它；只有 `npx tauri build` 必须注入。

#### 本地联调更新服务器（可选）

正式发布走下方 `release.py`，本机服务器仅用于改 `tauri.conf.json` 端点后的本地联调：

```bash
# 版本只认 VERSION，避免 manifest 与安装包错版本
python scripts/build_update_manifest.py --notes "修复冷启动"
python scripts/build_update_manifest.py --verify

# 暴露 nsis 目录（含 setup.exe / .sig / latest.json），只绑定 loopback
python -m http.server 17865 --bind 127.0.0.1 --directory desktop/src-tauri/target/release/bundle/nsis
```

`latest.json` / `*.sig` / `update-site/` 都是发布产物，不入库。

#### 运行期行为

| 行为 | 说明 |
|---|---|
| 自动检查 | 启动 10s 后后台检查，24h 内不重复；打点文件在 profile 目录（安装目录外） |
| 发现更新 | 原生弹窗展示版本 + 更新说明，用户确认后才下载 |
| 下载 | 插件内部校验 `.sig`，通过后才停本地服务 |
| 安装 | NSIS `/P /R /UPDATE` 静默覆盖 + 自动重启，无需管理员（`installMode: currentUser`） |
| 失败 | 只写 `<profile>/logs/desktop-update.log`，不弹首屏失败页，服务保持可用 |
| 用户数据 | `~/.plateau-dashboard/profiles/` 在安装目录之外，升级不会清除 |

托盘「检查更新」是立即检查（跳过 24h 节流），无更新时提示「已是最新版本」。

| 环境变量 | 作用 |
|---|---|
| `PLATEAU_UPDATE_DISABLE=1` | 完全关闭更新检查（离线环境） |
| `PLATEAU_UPDATE_ENDPOINT=<url>` | 运行时覆盖端点，用于切换自建服务 / beta 渠道 |

#### 发布新版本

一键发版脚本 `scripts/release.py` 把发版流程串起来（升版本 → 守卫 → 构建 → 校验 → 上传）：

```powershell
$env:TAURI_SIGNING_PRIVATE_KEY = "$env:USERPROFILE\.tauri\plateau.key"
$env:PLATEAU_ADMIN_TOKEN = "<registry 管理 token>"
python scripts/release.py --notes "修复 xxx"             # 全流程：bump → ci_cheap → 构建 → 上传
python scripts/release.py --notes "..." --skip-build     # 已有安装包，只跑守卫 + 上传
```

上传走 registry 的 `POST /api/admin/update/assets`（传 `setup.exe` + `.sig`），registry 自动生成
`latest.json` 并把安装包暴露到 `https://maxweili.devcloud.woa.com/registry/update/<安装包>`。

回滚：默认只允许 `version > 当前版本`，禁止降级。紧急回滚请**发布更高版本号、内容回退到上个 tag 的包**
（例如 1.0.2 的内容 = 1.0.0）；单点回退可手动运行旧安装包 `/S`。

完整的端到端验收步骤与正式发布 SOP 见 [`docs/updater-runbook.md`](docs/updater-runbook.md)（本次未执行人工验收）。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health/liveness` | 存活探针 |
| GET | `/health/ready` | 就绪探针（未就绪 503 带 phase） |
| GET | `/health` | 综合健康（可选依赖不可用降级 degraded） |
| GET | `/api/ping` | 服务标识 |
| GET | `/api/logs/sources` | 日志源列表 |
| GET | `/api/logs/tail` | 日志尾部（level 过滤 + request-id 下钻） |
| GET | `/api/runtime/status` | 运行时聚合（dashboard / model provider / 登录态 / 端口 / 版本） |
| GET | `/api/runtime/services` | 服务清单 |
| GET | `/api/runtime/storage` | 数据层自检（dialect / schema 版本 / 集合行数） |
| POST | `/api/runtime/auth/login` | 登录代理（转发 model provider） |

## 数据层

`DATABASE_URL` 由 launcher 注入，指向 `<PLATEAU_HOME>/profiles/<profile>/plateau.db`（**安装目录之外**，自动更新覆盖不会丢数据）。

- `backend/app/core/storage.py` 只有 `Storage` 端口与 `DatabaseConfig`，不含 SQL / 驱动。
- `backend/app/adapters/sqlite_storage.py` 用 stdlib `sqlite3` 实现，`migrations.py` 集中版本化 DDL；零外部依赖，兼容 PyInstaller 冻结包。
- 未配置 `DATABASE_URL` 时 `/api/runtime/storage` 返回 `NOT_CONFIGURED`（503），其余功能不受影响。

## 版本与更新记录

**约定：每次代码改动都升版本号，并在 `CHANGELOG.md` 记录更新内容。**

```bash
python scripts/bump_version.py --notes "修复 xxx"    # patch +1，自动回写 6 处派生版本
python scripts/bump_version.py --minor --notes "新增 xxx"
python scripts/bump_version.py --check               # CI 守卫，不写文件
```

`VERSION` 是唯一真源，`sync_version.py` 回写 `frontend/package.json`、`desktop/services.json`、
`desktop/package.json`、`tauri.conf.json`、`contracts/api.json`、`Cargo.toml` 六处。
`ci_cheap.py` 会拦截「版本不一致」和「CHANGELOG 缺当前版本条目」；
自动更新弹窗的更新说明默认从 `CHANGELOG.md` 最新版本条目读取。

## 扩展方式

新增一个工作台模块：

1. 在 `frontend/src/views/` 下新建视图（参考 `console/ConsoleOverview.vue` 与占位页 `ModulePlaceholder.vue`）。
2. 在 `frontend/src/router/index.ts` 的 `children` 里注册路由。
3. 在 `frontend/src/layouts/WorkbenchLayout.vue` 侧边栏加一个 `el-menu-item`。
4. 后端按 `backend/app/routers/` 每模块一个文件，在 `main.py` 里 `include_router`，并把端点同步进 `contracts/api.json`。

## 验证

```bash
python scripts/ci_cheap.py                          # 零依赖静态守卫（含契约 / 更新通道 / 数据层不变量）
python scripts/bump_version.py --check              # 七处版本一致 + CHANGELOG 已记录
python scripts/build_update_manifest.py --verify    # manifest 与 VERSION / .sig 一致
cd frontend && npx tsc --noEmit && npm run build    # 前端独立验证
cd desktop/src-tauri && cargo check                 # Rust 桌面壳
```
