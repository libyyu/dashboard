# codebuddy-cli 插件化 + 共享 node 运行时 设计

> 状态：待评审（方案，未实施）
> 目标：codebuddy-cli **保持 subprocess 直调 CLI**（像终端跑 `codebuddy` 命令），
> 但 node + cli 完全由「插件安装 + 共享 node 运行时」提供，**不再探测宿主 IDE 环境**。
> 未装插件 → `available()` 为 `False`，模型列表查不到 codebuddy-cli。
> node 运行时做成 **共享、可复用**，包体从**官方源**下载，日后其他插件可直接依赖。

---

## 0. 背景与现状

dashboard 后端目前有三种 model provider 形态并列（`model.py` + 三个 adapter）：

| provider | 形态 | CLI 运行环境来源 |
|---|---|---|
| `cb-openai` | sidecar 插件（常驻 HTTP 面） | 插件自带 venv + `codebuddy_agent_sdk`（含 `codebuddy-headless.exe`） |
| `codebuddy-cli` | **builtin**（subprocess 直调 CLI 进程） | **探测宿主**：`~/.workbuddy/binaries/node` + `LOCALAPPDATA/Programs/WorkBuddy` 的 `codebuddy-lite-wb.mjs` |
| `openai-completions` | 注册表/外部 | — |

`codebuddy-cli` 与 `cb-openai` 的**本质区别**：前者是「直接起一个 CLI 进程、读 stream-json」，
后者是「常驻 HTTP 服务」。这条区别应当保留——codebuddy-cli 不做 sidecar HTTP，继续
subprocess 直调，只改「node + cli 从哪来」。

当前问题：`CodeBuddyCliProvider` 的 `_find_cli_entry()` / `_find_node_exe()` 依赖
**宿主已装 WorkBuddy/CodeBuddy IDE**。这既把运行环境绑定到宿主 IDE，也不可控。

**决策：不再探测宿主**。node + cli 唯一来源是插件安装的共享运行时 + cli 文件。
未装插件时，codebuddy-cli 视为不可用（模型列表不暴露）。

参考 plateau-atlas（`src/core/codebuddy_cli.py`）：把 `node.exe` + cli 作为 `vendor/`
随应用包分发，首次运行解压并缓存复用，不依赖宿主环境。

---

## 1. 目标

1. codebuddy-cli 继续 **subprocess 直调 CLI 进程**（`CodeBuddyCliProvider` 保留），
   不引入 sidecar HTTP。
2. node + cli 的**唯一来源**是「插件安装 + 共享 node 运行时」：
   - 安装 codebuddy-cli 插件时，自动装 **共享 node 运行时**（官方源下载）+ **cli 文件**；
   - 未装插件 → `available()` = `False`，模型列表查不到，**不探测宿主**。
3. node 运行时是第一类共享组件：独立目录、幂等安装、可被多个插件复用。

非目标（本轮不做）：智能体编排、热插拔（沿用「安装后重启生效」）、把 codebuddy-cli
改成常驻服务。

---

## 2. 核心概念：共享运行时（Runtime）

与「插件（plugin）」并列、但更底层的概念。插件是功能单元，运行时是插件运行所需的
基础环境（node / python 等）。

| 概念 | 谁 | 例子 |
|---|---|---|
| **Runtime** | 内核/launcher 管理的基础运行时 | `node` |
| **Plugin** | 从 registry 安装的功能单元 | `cb-openai`（sidecar）、`codebuddy-cli`（cli） |

设计要点：

- 目录：`<home>/profiles/<profile>/runtimes/{runtime_id}/{version}/`（版本可并存）。
- **元数据**：registry 托管 `runtime.json`（id / version / 按平台的下载 URL + sha256 +
  入口名 `node_bin`），**包体从官方源下载**，不经过 registry。
- 元数据：SQLite 新增 `runtimes` collection（复用现有 KV，零 migration）。
- 安装：幂等（同版本已装则跳过），随插件安装前置完成。
- 复用：插件在 manifest 声明依赖，安装前自动确保依赖的 runtime 已就绪。

### 2.1 目录结构

```
<home>/profiles/<profile>/
├── plugins/                     # 现有：插件包体
│   └── codebuddy-cli/2.0.0/cli/…   # 新增：cli 文件插件
└── runtimes/                    # 新增：共享运行时
    └── node/
        └── 22.23.5/
            ├── node.exe
            └── runtime.json
```

### 2.2 runtime manifest（`runtime.json`）

```jsonc
{
  "schema": 1,
  "id": "node",
  "version": "22.23.5",
  "kind": "runtime",
  "platforms": {
    "win-x64": {
      "node_bin": "node.exe",
      "download": {
        "url": "https://nodejs.org/dist/v22.23.5/node-v22.23.5-win-x64.zip",
        "sha256": "…"
      }
    },
    "darwin-x64": {
      "node_bin": "node",
      "download": {
        "url": "https://nodejs.org/dist/v22.23.5/node-v22.23.5-darwin-x64.tar.gz",
        "sha256": "…"
      }
    },
    "linux-x64": {
      "node_bin": "node",
      "download": {
        "url": "https://nodejs.org/dist/v22.23.5/node-v22.23.5-linux-x64.tar.xz",
        "sha256": "…"
      }
    }
  }
}
```

- `platforms` 键 = `{os}-{arch}`（`win-x64` / `darwin-x64` / `darwin-arm64` / `linux-x64` …），
  安装时按当前平台取对应块的 `node_bin` 与 `download`。
- `node_bin` 是**平台相关的可执行名**（Windows 为 `node.exe`，macOS/Linux 为无后缀的
  `node`），不硬编码，由 `platforms` 块按平台声明。
- 包体格式随平台：Windows `.zip`、macOS `.tar.gz`、Linux `.tar.xz`；解压后统一落到
  `runtimes/node/{version}/`，入口即该平台块的 `node_bin`。
- 先实现 `win-x64`（当前桌面包目标平台），其余平台按同样结构补 `url`/`sha256` 即可。

---

## 3. 插件 kind 扩展：新增 `cli`

现有 `_KINDS = ("frontend", "sidecar")`，新增 `cli`：

| kind | 含义 | 谁拉起 | 例子 |
|---|---|---|---|
| `frontend` | 前端页面插件 | 内核挂载路由 | — |
| `sidecar` | 常驻 HTTP 服务 | launcher spawn + 探活 | `cb-openai` |
| `cli` | 提供本地 CLI 文件，供内核 subprocess 直调，**不常驻** | 无人拉起，内核按需调 | `codebuddy-cli` |

`cli` 插件不写 `service` 块（没有 `command/health_path`），launcher 的
`_scan_sidecar_plugins` 也不扫描它；它只是「把文件装到 `plugins/{id}/{version}/`」，
由内核 provider 按约定路径定位后 subprocess 调用。

---

## 4. 插件依赖声明：`runtimes`

### 4.1 manifest 新增 `runtimes` 字段

```jsonc
{
  "schema": 1,
  "id": "codebuddy-cli",
  "name": "CodeBuddy CLI（本地）",
  "kind": "cli",                   // 非 sidecar：不常驻
  "provides": "model",
  "runtimes": ["node"],            // 依赖的共享运行时；可选 "node@22" 主版本约束
  "requires": [],                  // 能力依赖（沿用现有白名单，语义不变）
  "files": [ … ]
}
```

- `runtimes` 与 `requires` 语义分离：`requires` 是「功能能力」，`runtimes` 是「运行环境」。
- 安装插件前，先解析 `runtimes`，逐个确保已装（见 §5）。

---

## 5. 安装流程（插件 + 运行时）

`plugins.py` 的 `_do_install` 扩展为「先运行时，后插件」：

1. 解析插件 `runtimes` 依赖；
2. 对每个依赖 runtime：
   - 已装同版本 → 跳过；
   - 未装 → 从 registry 读 `runtime.json`，按当前平台取 `platforms[{os}-{arch}]` 的
     `node_bin` + `download`（官方 URL + sha256）→ 从官方源下载 → sha256 校验 →
     解压到 `runtimes/{id}/{version}/` → 写 `runtimes` collection；
3. 再走现有插件安装（manifest → files → 落盘 `plugins/{id}/{version}/` → 写 plugins collection）。

runtime 下载/解压/落盘复用现有插件安装基础设施（`_request_bytes`、`_SHA256_RE`、路径穿越守卫），
仅「下载源 = 官方 URL、落盘根目录 = `runtimes/`」两点与插件安装不同。

---

## 6. codebuddy-cli 插件设计

### 6.1 包体清单（最小可运行）

```
codebuddy-cli/2.0.0/
├── plugin.json                  # manifest（见 §4.1）
└── cli/
    ├── codebuddy-lite-wb.mjs    # CLI 入口（ESM）
    └── dist/…                   # CLI 的 dist 依赖（原 app.asar.unpacked/cli/dist）
```

无 `server.py`、无 `.venv`、无常驻进程。

### 6.2 运行方式（不变）

`CodeBuddyCliProvider` 继续 subprocess 直调：
`{node_bin} cli/codebuddy-lite-wb.mjs --print --output-format stream-json …`，
与现状完全一致，只是 `{node_bin}` 与 cli 入口的查找位置变了。

---

## 7. CodeBuddyCliProvider 改造（只改「从哪找」，不改「怎么跑」）

保留现有 subprocess / stream-json 解析 / 取消协议逻辑，只改两个查找函数，**并移除宿主探测**：

### 7.1 `_find_node_exe()`

只从共享运行时找：`runtimes/node/{最新版本}/node.exe`（目录扫描，取最高版本）。
**不再**扫描 `~/.workbuddy/binaries/node/versions/*/node.exe`，**不再** `shutil.which("node")`。

### 7.2 `_find_cli_entry()`

只从插件目录找：`plugins/codebuddy-cli/{最新版本}/cli/` 下的 CLI 入口
（`codebuddy-lite-wb.mjs` → `codebuddy-headless.mjs` → … 按既有 `_CLI_RELATIVE_ENTRIES` 顺序）。
**不再**探测 `LOCALAPPDATA/Programs/WorkBuddy`、`CodeBuddy` 安装目录。

路径由 `ports.home_dir()/profiles/{profile}/` 派生（与 `plugins.py` 的 `_plugins_root()`、
launcher 的 `plugins_root` 同一约定），dashboard 进程可直接算，无需 launcher 注入。

### 7.3 `available()` 语义

= 「codebuddy-cli 插件已装（cli 入口文件存在）**且** 共享 node 运行时已装（node.exe 存在）」。
两者缺一即 `False` → `model.py` 不暴露 codebuddy-cli → 模型列表查不到。

---

## 8. model.py 暴露逻辑

- 保留 `codebuddy-cli` 的 builtin 分支，`available()` 按 §7.3 判断；`source` 由
  `"builtin"` 改为 `"plugin"`（现在唯一来源就是插件）。
- 不新增 `_cli_providers()` 扫描（codebuddy-cli 是唯一 cli provider，直接复用现有分支）。
- 前端「未装」态：codebuddy-cli 插件上传 registry 后，走现有 `/api/plugins/registry`
  + `/api/plugins/install` 即可发现并安装（与 cb 同一条链路，无需新增入口）。

---

## 9. launcher 改动

- `cli` 插件不参与 `_scan_sidecar_plugins`（无 service 块、无常驻进程），launcher 无需拉起。
- node 运行时路径由 dashboard 按约定目录扫描（§7.1），launcher 只需保证
  `PLATEAU_HOME`/`PLATEAU_PROFILE` 注入正确（现状已具备），**无新增胶水**。

---

## 10. 打包脚本

- 新增 `scripts/build_codebuddy_cli_plugin.py`：收集 `cli/`（mjs + dist）→ 生成
  `plugin.json`（回写 sha256，kind=cli，runtimes=["node"]）→ 打包 zip 上传 registry。
- node 运行时**不打包**：registry 只托管 `runtimes/node/{version}/runtime.json`
  （官方下载 URL + sha256 元数据），包体由安装流程从官方源拉取。
- 清理 `packaging/vendor/codebuddy-headless.exe` 历史残留（README 已标「构建产物不入库」）。
- `codebuddy_cli_provider.py` 里的 `_install_roots()` / 宿主探测逻辑一并删除。

---

## 11. 分阶段实施

| 阶段 | 内容 | 可独立验证 |
|---|---|---|
| R1 运行时 | registry 加 `/runtimes/*` 元数据 + 内核 runtime 官方源下载/安装/元数据 | 装一个假 runtime 落盘正确 |
| R2 依赖解析 | manifest `runtimes` 校验 + 安装前置确保 runtime | 装依赖 node 的插件先装 node |
| R3 codebuddy-cli 插件 | `build_codebuddy_cli_plugin.py` + registry 托管 node runtime.json | 插件包可上传，node 元数据就位 |
| R4 provider 改造 | `_find_node_exe` / `_find_cli_entry` 改只读共享/插件路径 + 删宿主探测 | 装插件后 CLI 出流；未装插件 `available()=False` |
| R5 收口 | `available()` 语义 + model.py source + vendor 清理 | `build.py verify` + `ci_cheap.py` 全绿 |

---

## 12. 待确认点

| # | 问题 | 结论 |
|---|---|---|
| 1 | CLI 形态 | 沿用 `codebuddy-lite-wb.mjs`（node 脚本，需共享 node） |
| 2 | node 版本策略 | 固定基线（如 22.23.5），目录按版本隔离，支持后续多版本并存 |
| 3 | node 分发来源 | **已决议：官方源下载（nodejs.org），registry 只托管元数据** |
| 4 | 未装插件行为 | **已决议：不探测宿主，`available()=False`，模型列表查不到** |
| 5 | `source` 字段 | 统一显示 `plugin`（唯一来源即插件） |
