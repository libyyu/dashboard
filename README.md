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
│   ├── launcher.py          # 单 owner 启动 / 探活 / 关停 dashboard + cb
│   ├── services.json        # 包清单（产品元数据 + 服务清单，仅 dashboard 与 cb）
│   ├── package.json         # Tauri CLI 入口
│   └── src-tauri/           # Rust 薄壳：spawn sidecar / 探活导航 / 关闭清理
│       ├── src/             # lib.rs / main.rs / spawn.rs
│       ├── loading/         # 首屏 loading 页（启动失败时展示原因）
│       ├── icons/           # 应用图标
│       └── Cargo.toml / Cargo.lock / build.rs / tauri.conf.json
├── frontend/
│   ├── src/
│   │   ├── layouts/WorkbenchLayout.vue  # 工作台外壳（侧边栏 + 头部 + 内容区）
│   │   ├── views/Home.vue               # 首页占位（模块建设中）
│   │   ├── router/                      # 路由（后续模块在此注册）
│   │   ├── stores/                      # Pinia 状态
│   │   └── api/                         # 请求封装（契约类型 + mock + http）
│   ├── package.json
│   ├── vite.config.ts                   # 开发代理，目标来自 VITE_API_TARGET
│   └── index.html
├── packaging/               # PyInstaller spec + 冻结构建/组装脚本（build.py）
├── scripts/
│   ├── ci_cheap.py          # 零依赖静态守卫（契约 / 解耦 / 车道 / 端口 / 日志）
│   └── sync_version.py      # 版本号同步
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
python packaging/build.py --sdk-cli <codebuddy-headless.exe 路径> all --assemble
python packaging/build.py verify           # 校验三个 exe / 冻结清单 / 前端产物 / SDK CLI 齐备
cd desktop && npm install && npx tauri build
# 产物：desktop/src-tauri/target/release/bundle/nsis/Plateau_1.0.0_x64-setup.exe
```

`codebuddy-headless.exe` 是桌面包的必需资源：首次通过 `--sdk-cli`（或 `PLATEAU_SDK_CLI`）提供后
会缓存到 `packaging/vendor/`，组装时复制到 `binaries/cb/bin/`，随 NSIS 包一起分发；
launcher 启动时会注入 `CODEBUDDY_CODE_PATH` 指向它。缺少时 `assemble` 直接失败，不产出残缺包。

桌面壳是单实例：重复打开会把已有窗口唤到前台。关闭窗口只隐藏到托盘，服务继续运行；
托盘菜单提供「显示窗口 / 打开日志目录 / 退出 Plateau」，退出时通过 `launcher.stop`
触发有序关停（关停 dashboard、cb 并清理 `launcher.lock` / `launcher.ready.json`）。

`build/`、`desktop/src-tauri/binaries/`、`desktop/src-tauri/target/`、`packaging/vendor/`
均为构建产物，不入库。图标由 `scripts/make_icon.py` 生成，可直接覆盖 `icons/icon.ico` 换成正式素材。
cb 是外部独立仓库，通过 `PLATEAU_CB_REPO` 指定路径。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health/liveness` | 存活探针 |
| GET | `/health/ready` | 就绪探针（未就绪 503 带 phase） |
| GET | `/health` | 综合健康（可选依赖不可用降级 degraded） |
| GET | `/api/ping` | 服务标识 |
| GET | `/api/logs/sources` | 日志源列表 |
| GET | `/api/logs/tail` | 日志尾部（level 过滤 + request-id 下钻） |
| GET | `/api/runtime/status` | 运行时聚合（dashboard / cb / 登录态 / 端口 / 版本） |
| GET | `/api/runtime/services` | 服务清单 |
| POST | `/api/runtime/auth/login` | 登录代理（转发 cb） |

## 扩展方式

新增一个工作台模块：

1. 在 `frontend/src/views/` 下新建视图（参考 `Home.vue`）。
2. 在 `frontend/src/router/index.ts` 的 `children` 里注册路由。
3. 在 `frontend/src/layouts/WorkbenchLayout.vue` 侧边栏加一个 `el-menu-item`。
4. 后端按 `backend/app/routers/` 每模块一个文件，在 `main.py` 里 `include_router`，并把端点同步进 `contracts/api.json`。

## 验证

```bash
python scripts/ci_cheap.py   # 后端静态守卫（契约 / 解耦 / 车道 / 端口 / 日志）
cd frontend && npx tsc --noEmit && npm run build   # 前端独立验证
```
