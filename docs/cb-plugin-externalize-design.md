# cb 彻底插件化 —— 详细设计清单（方案 B：dashboard 统一网关）

> 状态：已实施（P0~P5 已完成，P6 部署加固待后续）。
> 决策：cb 从桌面包彻底外置，改为 sidecar 插件，从 `plateau-registry` 安装；
> dashboard 内核保留「能力端点」，按能力路由转发/编排到已装插件。

## 0. 评审结论（2026-11-18）

| # | 问题 | 结论 |
|---|---|---|
| 1 | 安装生效时机 | 首版「安装后重启 Plateau」生效；热插拔待系统稳定后再设计 |
| 2 | /v1/* 转发范围 | 补全转发；**内核本轮只做 cb 转发（透明代理），不实现智能体编排** |
| 3 | registry 地址 | dev 默认 `http://127.0.0.1:8700`，devcloud 走 `PLATEAU_REGISTRY_URL` |
| 4 | CLI 登录态 | 落 `profile/plugins/cb-openai/config/`（版本无关，升级不丢） |
| 5 | 插件签名 | 复用安装包同一把 `plateau.key`（Ed25519） |

> **关键定位**：cb 是「大模型服务」，不是「智能体」。它只暴露 OpenAI 兼容的
> `/v1/models`、`/v1/chat/completions` 与 `/auth/*`（CLI 登录 gate）。智能体是
> 后续在 cb 之上、用 mcp / skill 等能力组建的更高层（对应 `core/agent.py` 的
> `Orchestrator` / `MCPToolAdapter` / `SkillRegistry`），本轮不落地。

---

## 1. 目标

- 桌面包 = Tauri 壳 + launcher + **dashboard（唯一 required）** + 前端 SPA。
- cb 不再随包内置，改为 **sidecar 插件**：从 registry 下载 → 落本机 → launcher 动态拉起。
- dashboard 内核保留 `/api/agent/*` 等「能力端点」，由「provider 发现」对接已装 cb。
- 前端菜单/路由数据驱动，AI 助手在「未装 cb」时显示为可安装入口。

---

## 2. 现状与耦合点盘点

### 2.1 服务清单与编排

- `desktop/services.json`：`dashboard`（required）+ `cb`（required: false）。
- `desktop/launcher.py` 的 cb 专用胶水：
  - `_cb_home_default()`：源码态 → `RuoYi-Vue3-FastAPI/cb`，冻结态 → `binaries/cb`。
  - 注入 `PLATEAU_CB_HEALTH_URL`、`CODEBUDDY_CODE_PATH`、`PLATEAU_CB_HOME`。
  - `cb_config_dir = <home>/profiles/<profile>/cb-config`。
  - spawn 循环里对 `name == "cb"` 有独立分支。

### 2.2 后端聚合与代理（`backend/app/routers/runtime.py`）

- `_cb_origin()` 从 `PLATEAU_CB_HEALTH_URL` 推导 cb origin。
- `/api/runtime/status` 拉 cb 的 `/health` + `/auth/status`。
- `/api/runtime/auth/login` 转发 cb 的 `/auth/login`。
- 降级态已存在：`standalone`（cb_not_configured）/ `degraded`（cb_unavailable）。

### 2.3 契约（`contracts/api.json`）

- `agent` 端点全部 `implemented: false`：`/api/agent/models`、`/api/agent/runs/stream`、`/api/agent/runs/{id}/cancel`、`/api/agent/sessions/{id}/events`。
- `architecture.agent.enabled = false`，`provider = "cb-openai"`。
- `core/agent.py` 已声明 `CBOpenAIProvider` + `ModelProvider`/`SessionStore`/`Orchestrator` 等端口（纯契约，无实现）。

### 2.4 前端

- `frontend/src/router/index.ts`：`ai` 路由硬编码，指向 `ModulePlaceholder`。
- `frontend/src/layouts/WorkbenchLayout.vue`：「AI 助手」`el-menu-item` 硬编码。
- `frontend/src/api/contract.ts`：`ENDPOINTS.agent*`、`graphNodes`、`search` 已定义但后端未实现。

### 2.5 打包与 CI（「彻底外置」必改）

- `packaging/build.py`：`_TARGETS = ("launcher", "dashboard", "cb")`；`_FROZEN` 含 cb；`verify_assembly()` 强校验 `binaries/cb/cb.exe` + `binaries/cb/bin/codebuddy-headless.exe`（SDK CLI）；`assemble()` 把 SDK CLI 复制进 `binaries/cb/bin/`。
- `scripts/ci_cheap.py` 第 83 行：`check("服务清单仅 dashboard + cb", names == ["cb", "dashboard"])` —— **硬编码了 cb 必须内置**，是外置后第一处要改的守卫。

### 2.6 cb 仓库事实（`RuoYi-Vue3-FastAPI/cb`）

- 入口 `server.py`：**标准库 `http.server`**（非 FastAPI），参数 `-p/--port`、`--host`、`--token`、`--config-dir`、`--login`、`--login-timeout`。
- 路由：`/`、`/health`、`/v1/models[/{id}]`、`/auth/status`、`/auth/login`、`/v1/chat/completions`（SSE）、`/v1/responses`、`/v1/completions`、OPTIONS。
  **没有 `/api/agent`** —— 这决定了第 8 节「能力网关」要做翻译，不是透传。
- 唯一第三方依赖：`codebuddy_agent_sdk`（其包内 `bin/` 自带 `codebuddy-headless[.exe]`，装 SDK 即得 CLI）。
- 会话 registry 纯内存，不落盘；CLI 登录票据落 `CODEBUDDY_CONFIG_DIR`。
- 最小部署：`server.py` + `cbopenai/*.py` + venv（`codebuddy_agent_sdk`）。

---

## 3. 目标架构

```
┌─ 桌面包（NSIS 安装）────────────────────────────┐
│  Tauri 壳 ─ launcher ─ dashboard（唯一 required）│
│                     └─ 前端 SPA（dist）          │
└──────────────────────────────────────────────────┘
                    │ 能力路由（同源 /api/*）
                    │   内核实现：/api/search /api/graph /api/storage/kv
                    │   编排+provider：/api/agent/* ─┐
                    ▼                                │ 调用 sidecar
┌─ 本机插件（profile/plugins/…）──────────────────┐│
│  cb-openai（sidecar，requires=[]，provides=model）◄┘
└──────────────────────────────────────────────────┘
                    ▲ 下载（catalog + 包体，sha256 校验）
┌─ plateau-registry（devcloud）───────────────────┐
│  /plugins/catalog.json + /plugins/{id}/{ver}/…  │
└──────────────────────────────────────────────────┘
```

关键分层（比上一轮更精确）：

| 概念 | 谁提供 | 例子 |
|---|---|---|
| **能力端点**（Capability） | dashboard 内核 | `/api/search`、`/api/graph`、`/api/storage/kv`、`/api/model/*`（转发层） |
| **大模型服务**（sidecar 插件） | 已装插件 | cb = OpenAI 兼容大模型服务 + CLI 登录 gate（**不是智能体**） |

**核心结论（评审后修订）**：cb 是「大模型服务」，暴露 `/v1/models`、`/v1/chat/completions`、
`/auth/*`。dashboard 内核本轮**只做同源转发**（把稳定内核路径映射到 cb 的 `/v1/*`、`/auth/*`），
不实现智能体编排。智能体是后续在 cb 之上用 mcp / skill 组建的更高层，本轮不落地。

---

## 4. 插件数据模型

### 4.1 插件 manifest（`plugin.json`）

与 registry 侧已定义的保持一致，补充 sidecar 启动所需字段：

```jsonc
{
  "schema": 1,
  "id": "cb-openai",
  "name": "CB大模型",
  "version": "2.0.0",
  "kind": "sidecar",                 // frontend | sidecar
  "requires_core": "1.0.0",          // 最低内核版本
  "requires": [],                    // 能力依赖（search|graph|storage.kv|model）；cb 不依赖内核能力
  "provides": "model",               // 纯服务插件：只提供「大模型服务」，无菜单/页面
  "files": [ { "path": "…", "sha256": "…" } ],
  "signature": "",                   // 部署前接 Ed25519
  "service": {
    "command": ["{python}", "server.py", "-p", "{port}", "--host", "127.0.0.1", "--config-dir", "{config_dir}"],
    "cwd": "{plugin_dir}",
    "env": { "CB_REQUEST_TIMEOUT": "1800", "CB_IDLE_TIMEOUT": "300" },
    "health_path": "/health",
    "ready_path": "/health",
    "required": false
  }
}
```

### 4.2 启动占位符（launcher 统一替换）

| 占位符 | 解析为 | 说明 |
|---|---|---|
| `{port}` | launcher 分配的端口 | 复用 `ports.resolve_port()` |
| `{plugin_dir}` | `profile/plugins/{id}/{version}/` | 包体根目录 |
| `{config_dir}` | `profile/plugins/{id}/config/` | **版本无关**，升级不丢 CLI 登录态 |
| `{python}` | 插件自带 venv 的 python（Windows 下 `.venv/Scripts/python.exe`） | 源码态回退系统 python |

### 4.3 本地插件状态（SQLite，复用现有 KV）

- 元数据落 `plateau_kv` 的 `plugins` collection（**零 migration**，只是新 collection 名）：
  - key = `plugin_id`，payload = `{version, kind, requires, provides, enabled, installed_at, sha256, manifest_path}`。
- 包体落 `profile/plugins/{id}/{version}/`；`config_dir` 落 `profile/plugins/{id}/config/`。
- 与 `launcher.ready.json`、`plateau.db` 同源（`PLATEAU_HOME` 目录之外，升级不丢）。

---

## 5. 客户端插件运行时（dashboard 后端）

新增 `backend/app/routers/plugins.py`，端点入契约：

| 方法 | 路径 | 车道 | 说明 |
|---|---|---|---|
| GET | `/api/plugins` | best_effort | 本机已装插件清单（读 SQLite） |
| GET | `/api/plugins/capabilities` | best_effort | 内核能力清单 + 各自 provider 状态 |
| POST | `/api/plugins/install` | control | `{id, version}`：拉 catalog → 下载包体 → 校验 sha256 → 落盘 → 写 SQLite |
| POST | `/api/plugins/uninstall` | control | 停 sidecar → 删包体 → 删 SQLite |
| GET | `/api/plugins/registry` | best_effort | 透传 registry `catalog.json`（供前端「可安装」列表） |

- registry 地址由环境变量 `PLATEAU_REGISTRY_URL` 注入（launcher 设置，默认 `http://127.0.0.1:8700`，部署后指向 devcloud）。
- 安装/卸载后返回「需重启 launcher 生效」或「已热生效」标志（sidecar 进程要 launcher 拉起，故安装动作**只落盘 + 通知 launcher**，真正 spawn 由 launcher 的监听循环完成，见第 6 节）。
- 错误码新增 `PLUGIN_CAPABILITY_UNSATISFIED`、`PLUGIN_NOT_FOUND`、`PLUGIN_CHECKSUM_MISMATCH`，同步 `contracts/api.json` 的 `error_codes`。

---

## 6. launcher 动态服务注册

`desktop/launcher.py` 改动要点：

1. **移除 cb 专用胶水**：删 `_cb_home_default()`、cb 分支、`PLATEAU_CB_HEALTH_URL`/`CODEBUDDY_CODE_PATH`/`PLATEAU_CB_HOME` 注入、`cb_config_dir`。
2. **读已装 sidecar 插件**：扫描 `profile/plugins/*/{version}/plugin.json`，筛出 `kind=sidecar && enabled`，把 `service` 块合并进 `services.json` 的服务清单（合成服务名 `plugin-<id>`）。
3. **端口分配**：对每个 sidecar 用 `ports.resolve_port(f"plugin-{id}")`，替换 `{port}` 占位符。
4. **spawn**：复用现有 `subprocess.Popen` + stdout 泵 + 分层探活（`health_path`/`ready_path`）。sidecar 拉取失败 = fail-open（不阻塞 dashboard）。
5. **端口回传**：把 sidecar 端口写入 `runtime_state.json` 的 `ports`（现有 `ports.resolve_port` 已落盘），dashboard 后端据此做 provider 发现。
6. **安装/卸载响应**：新增一个轻量触发——launcher 轮询 `profile/plugins/` 的变更标记（或复用 `launcher.stop` 类似机制），安装后拉起、卸载后停掉对应 sidecar。首版可简化为「安装后需重启 Plateau」，二版做热生效。

> 说明：现有 `resolve_port()` 已支持 `PLATEAU_<SERVICE>_PORT` 环境变量覆盖 + `runtime_state.json` 复用 + `+1..+8` 回退，sidecar 端口可直接复用，无需新机制。

---

## 7. dashboard 转发层（model provider 路由，多 provider，不做智能体编排）

新增 `backend/app/routers/model.py`（转发层），改动要点：

1. **model provider 目录**：内核维护 `{name, base_url, source, auth}` 目录，来源两类：
   - `sidecar`：已装插件中 `provides: "model"` 的（cb），base_url = `http://127.0.0.1:{port}`，端口由 launcher 分配后写入 `runtime_state.json`。
   - `external`：配置的外部 OpenAI 兼容服务（如 Ollama `http://127.0.0.1:11434`），走环境变量 / 配置文件。
2. **provider 无关转发（本轮唯一职责）**：转发层只认「OpenAI 兼容 base URL」，不关心是 cb 还是 Ollama。
   路径按 provider 路由，透传 + SSE 透传，不翻译语义、不做编排：

   | 内核路径（转发层） | 上游 |
   |---|---|
   | `GET /api/model/providers` | 返回 provider 目录 + default |
   | `GET /api/model/{provider}/models` | `{base_url}/v1/models` |
   | `GET /api/model/{provider}/models/{id}` | `{base_url}/v1/models/{id}` |
   | `POST /api/model/{provider}/chat/completions` | `{base_url}/v1/chat/completions`（SSE 透传） |
   | `GET /api/model/{provider}/auth/status` | `{base_url}/auth/status`（仅 auth=true 的 provider） |
   | `POST /api/model/{provider}/auth/login` | `{base_url}/auth/login`（仅 auth=true 的 provider） |

   - `_cb_origin()` 泛化为 `_model_providers()`；`/api/runtime/status` 的 `cb` 字段改查 default provider 的 `/health` + `/auth/status`。
3. **多 provider 扩展**：新增模型服务（Ollama 等）只需加一条 external provider 配置，内核转发层零改动。
   对应 `core/agent.py` 已有的 `ModelProvider` 抽象 + `CBOpenAIProvider`（未来加 `OllamaProvider`）。
4. **智能体（后续，本轮不做）**：`/api/agent/runs/*`、`/api/agent/sessions/*` 属「智能体」层，
   消费 `model` + mcp/skill，待后续组建，不纳入本轮。
5. **契约拆分**：`contracts/api.json` 现有 4 个 `agent` 端点里，`agent.models` 改归转发层的
   `model.models`（本轮实现）；`agent.runs/*`、`agent.sessions/*` 保持 `implemented: false`（智能体，后续）。

---

## 8. 前端动态菜单/路由

1. 新增 `stores/plugins.ts`（仿 `stores/runtime.ts` 的轮询模式），拉 `/api/plugins` + `/api/plugins/registry`。
2. `WorkbenchLayout.vue` 侧边栏改数据驱动：`v-for` 已装插件的 `menu` + 内核菜单，「未装但 registry 有」的显示「安装」按钮。
3. `router/index.ts`：`ai` 硬编码路由移除，改为启动时对每个已装插件 `router.addRoute()` 动态注册（用 manifest 的 `routes` + `entry` 懒加载）。
4. `api/contract.ts`：新增 `Plugin`、`PluginList`、`Capability` 类型；`ENDPOINTS` 加 `plugins*`。
5. `ai` 未装时：侧边栏显示「AI 助手（未安装）」，点击 → 调 `/api/plugins/install`。

---

## 9. cb 插件包（registry 侧 + 打包）

### 9.1 包体清单（最小可运行）

```
cb-openai/2.0.0/
├── plugin.json                  # manifest（见 4.1，纯服务：无 menu/routes）
├── server.py                    # cb 入口
├── cbopenai/                    # 整个业务包（去掉 tests / *.bak-imagefix）
└── .venv/                       # 自带 venv，装 codebuddy_agent_sdk（含 bin/codebuddy-headless.exe）
```

### 9.2 打包脚本（新）

- 新增 `plateau-registry` 侧或 `plateau-dashboard/scripts/` 的 `build_cb_plugin.py`：
  1. 从 `PLATEAU_CB_REPO`（cb 仓库）拷贝 `server.py` + `cbopenai/`；
  2. 建 venv → `pip install -r requirements.txt`（`codebuddy_agent_sdk`，自带 CLI）；
  3. 生成 `plugin.json`（`files` 回写 sha256）；
  4. 打包成 zip / 目录，发布到 registry 的 `/plugins/cb-openai/{version}/`。
- 原 `packaging/build.py` 的 `cb` target 与 SDK CLI 组装逻辑**删除**（SDK CLI 改随插件 venv 分发，不再进桌面包）。

### 9.3 发布

- 沿用 registry 的 `/api/admin/plugins` 上传（或直接静态托管 + 手工上传 `plugin.json` + 包体）。

---

## 10. 「彻底外置」逐文件改动清单

| # | 文件 | 改动 |
|---|---|---|
| 1 | `desktop/services.json` | 删除 `cb` 条目，只留 `dashboard` |
| 2 | `desktop/launcher.py` | 删 cb 胶水；加「读已装 sidecar 插件 → 合并清单 → 分端口 → 拉起」 |
| 3 | `backend/app/routers/plugins.py`（新） | `/api/plugins` 系列 |
| 4 | `backend/app/routers/model.py`（新）+ `runtime.py` | 转发层 `/api/model/*` → cb `/v1/*`、`/auth/*`；`_cb_origin()` → `_model_origin()`；status/auth 改查 provider |
| 5 | `backend/app/main.py` | `include_router(plugins.router)`；`extended_routers_registered` 语义改为「插件运行时已挂载」 |
| 6 | `backend/app/adapters/sqlite_storage.py` | 无改动（KV 复用）；仅约定 `plugins` collection |
| 7 | `contracts/api.json` | 加 `/api/plugins*` 端点 + 新错误码 |
| 8 | `frontend/src/api/contract.ts` | 加 `Plugin`/`Capability` 类型 + `ENDPOINTS.plugins*` |
| 9 | `frontend/src/stores/plugins.ts`（新） | 插件 store |
| 10 | `frontend/src/layouts/WorkbenchLayout.vue` | 菜单数据驱动 |
| 11 | `frontend/src/router/index.ts` | 删 `ai` 硬编码，改动态 `addRoute` |
| 12 | `packaging/build.py` | `_TARGETS` 去掉 cb；删 `_FROZEN["cb"]`、`verify_assembly` 的 cb.exe/SDK CLI 校验、`resolve_sdk_cli`/`cache_sdk_cli`/SDK CLI 组装 |
| 13 | `scripts/ci_cheap.py` | 「服务清单仅 dashboard + cb」→「仅 dashboard」；新增插件不变量守卫 |
| 14 | `scripts/build_cb_plugin.py`（新） | cb 插件打包脚本 |
| 15 | `desktop/src-tauri/tauri.conf.json` | 删 `bundle.resources` 里的 `binaries/cb`（updater 逻辑不动） |

---

## 11. CI 与版本守卫改动

- `ci_cheap.py` 第 83 行改：`names == ["dashboard"]`。
- 新增守卫：
  - 契约里 `/api/plugins*` 端点已实现 ↔ 后端真实注册（复用 `collect_routes`）。
  - `services.json` 不含 `cb`（反向断言，防止 cb 又被内置回来）。
  - 插件 manifest `requires` 只含白名单（可复用 registry 侧的校验逻辑）。
  - `PLATEAU_REGISTRY_URL` 由 launcher 注入（对齐现有「launcher 注入、backend 只读环境变量」解耦约定）。
- `bump_version.py` / `sync_version.py`：新增文件（`plugins.py`、`build_cb_plugin.py`）无版本派生点，无需动 6 处同步。

---

## 12. 分阶段实施计划

| 阶段 | 内容 | 可独立验证 |
|---|---|---|
| P0 契约 | `contracts/api.json` 加 `/api/plugins*` + 错误码 + 前端类型 | `ci_cheap.py` 契约侧通过 |
| P1 本地插件运行时 | `/api/plugins`（list/install/uninstall/registry）+ SQLite `plugins` collection | 无 cb 也能装一个纯 frontend 测试插件 |
| P2 launcher 动态注册 | 读已装 sidecar → 合并清单 → 分端口 → 拉起 | 用一个假 sidecar 服务验证拉起/探活/关停 |
| P3 能力网关 | `_provider_origin("agent")` + auth/login/status 改查 provider | 装 cb 插件后 `/api/runtime/auth/login` 通 |
| P4 前端动态化 | plugins store + 菜单/路由数据驱动 | `VITE_API_MOCK=1` 前端离线可跑 |
| P5 cb 打包 + 彻底外置 | `build_cb_plugin.py` + `build.py` 删 cb + `services.json` 删 cb + CI | `build.py verify` + `ci_cheap.py` 全绿 |
| P6 部署加固 | 插件 Ed25519 签名 + `/api/admin/*` 鉴权 + registry 指向 devcloud | 端到端装→跑通 |

> P1~P4 可并行推进（契约先行）；P5 是「彻底外置」的收口，依赖 P2/P3。

---

## 13. 决议记录

全部 5 项已决议，见第 0 节「评审结论」表。要点回顾：安装后重启生效；内核本轮只转发不编排；
registry 走环境变量；CLI 登录态版本无关；插件签名复用 `plateau.key`。
