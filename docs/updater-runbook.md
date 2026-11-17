# Plateau 自动更新：验收手册与正式发布流程

> 桌面包阶段收尾文档。本文只做记录：**第 1 节的端到端验收当前尚未执行**，留待需要时按步骤复现。
> 快速上手见 `README.md` 的「自动更新」章节，本文是完整验收脚本 + 正式发布 SOP。

## 0. 当前状态基线

| 项 | 状态 |
|---|---|
| 实现 | 提交 `e538620`，纯 Rust 实现（`desktop/src-tauri/src/updater.rs`）+ 发布脚本（`scripts/build_update_manifest.py`） |
| 更新源 | 本机 `http://127.0.0.1:17865`（联调态）；正式源待自行部署 |
| 签名 | `~/.tauri/plateau.key`（私钥，不入库）/ 公钥内置于 `tauri.conf.json` |
| 端口为何是 17865 | 8765 被 `UE4Editor.exe` 占用，且 8100–8999 是 dashboard 端口派生区间，需避开 |

### 已完成的自动化验证（记录）

| 检查 | 结果 |
|---|---|
| `cargo check` | 零错误 |
| `npx tauri build`（带 `TAURI_SIGNING_PRIVATE_KEY`） | `Plateau_1.0.0_x64-setup.exe` 65.64 MiB + `.sig` |
| `python scripts/ci_cheap.py` | 全绿（含 8 条更新通道守卫） |
| `python scripts/sync_version.py` | 六处版本一致 `1.0.0` |
| `python scripts/build_update_manifest.py --verify` | manifest 与 `VERSION` / `.sig` / URL 一致 |
| 本地服务器三个资源 | HTTP 200：`latest.json` 693B、`.sig` 436B、`.exe` 68829519B |
| 隔离 `PLATEAU_HOME` 启动 release 桌面壳 | `launcher.ready.json` 存在、`desktop-error.txt` 不存在、`desktop-update.log` 输出「已是最新版本」 |

**结论**：release 构建里本地 HTTP 端点可用、静态 manifest 能解析、版本比较正常、后台检查不阻塞冷启动。

### 尚未验证（本文第 1 节）

发现新版本 → 原生确认弹窗 → 下载 65 MiB → 停本地服务 → NSIS `/P /R` 覆盖安装 → 自动重启 → 用户数据保留。需要人工点击弹窗，故未自动化。

### 配置速查

| 位置 | 值 | 说明 |
|---|---|---|
| `bundle.createUpdaterArtifacts` | `true` | v2 格式，Windows 直接产出 `setup.exe` + `.sig` |
| `plugins.updater.pubkey` | 公钥字面量 | 不能写文件路径 |
| `plugins.updater.endpoints` | `http://127.0.0.1:17865/latest.json` | 联调态 |
| `dangerousInsecureTransportProtocol` | `true` | **仅**放宽传输协议，签名校验仍强制；`ci_cheap.py` 会拦截它配公网端点 |
| `plugins.updater.windows.installMode` | `passive` | 对应 NSIS `/P /R`（进度条 + 装完自动重启） |

---

## 1. 完整验收步骤（待人工执行）

### 1.1 前置

- Windows，已装 Python 3.10 / Node / Rust，工具链可用。
- 私钥在 `~/.tauri/plateau.key`，且**已备份**。
- 建议用隔离目录，避免污染真实 `~/.plateau-dashboard`：

```powershell
$env:PLATEAU_HOME = "$env:TEMP\plateau-update-e2e"
```

### 1.2 准备两个版本

updater 默认只接受 `version > current`，所以必须有一旧一新两个包。

```powershell
$repo = 'c:/Users/maxweili/MyProjects/MyPlateau/plateau-dashboard'
$nsis = "$repo/desktop/src-tauri/target/release/bundle/nsis"
$site = "$repo/update-site"            # 已 gitignore
New-Item -ItemType Directory -Force $site | Out-Null

# 1) 备份基线 1.0.0 安装包（用于安装「旧版本」）
Copy-Item "$nsis/Plateau_1.0.0_x64-setup.exe" "$site/Plateau_1.0.0_x64-setup.exe"

# 2) 把 VERSION 改成 1.0.1（手工编辑），然后同步六处版本 + 守卫
python scripts/sync_version.py --update
python scripts/ci_cheap.py

# 3) 重新组装冻结资源（仅当 Python/前端源码有改动时需要）
python packaging/build.py --sdk-cli <path> all --assemble

# 4) 带签名构建 1.0.1
$env:TAURI_SIGNING_PRIVATE_KEY = "$env:USERPROFILE\.tauri\plateau.key"
cd desktop; npx tauri build; cd ..

# 5) 把新包与签名放进站点目录，生成指向 1.0.1 的 manifest
Copy-Item "$nsis/Plateau_1.0.1_x64-setup.exe" "$site/"
Copy-Item "$nsis/Plateau_1.0.1_x64-setup.exe.sig" "$site/"
python scripts/build_update_manifest.py `
  --setup "$site/Plateau_1.0.1_x64-setup.exe" `
  --url "http://127.0.0.1:17865/Plateau_1.0.1_x64-setup.exe" `
  --out "$site/latest.json" --notes "自动更新端到端验收"
python scripts/build_update_manifest.py --setup "$site/Plateau_1.0.1_x64-setup.exe" --verify
```

### 1.3 起本地更新服务器

```powershell
python -m http.server 17865 --bind 127.0.0.1 --directory $site
```

保持该窗口运行。另开终端继续。

### 1.4 安装旧版本并触发更新

```powershell
# 静默安装 1.0.0（installMode: currentUser，无需管理员）
Start-Process -FilePath "$site/Plateau_1.0.0_x64-setup.exe" -ArgumentList '/S' -Wait
# 启动 Plateau，等首屏 /console 出现（launcher 冷启动需数秒）
```

触发方式二选一：

- 托盘「检查更新」→ 立即检查（跳过 24h 节流）；
- 或等启动 10s 后自动检查（若 `last-update-check` 已存在，先删掉）。

预期弹出原生对话框：「发现新版本 1.0.1（当前 1.0.0）… 立即安装 / 稍后」。点「立即安装」。

### 1.5 验收清单

| # | 检查点 | 期望 |
|---|---|---|
| 1 | 弹窗 | 版本号、更新说明正确；点「稍后」不下载、不停服务 |
| 2 | 下载 | `<profile>/logs/desktop-update.log` 出现「下载完成 … 签名校验通过」 |
| 3 | 安装前进程 | 用进程列表确认 `launcher.exe` / `dashboard.exe` / `cb.exe` 已退出，安装目录无文件锁 |
| 4 | 安装界面 | NSIS 进度条（`/P`），无 UAC 弹窗 |
| 5 | 自动重启 | 安装完成后新 exe 自动拉起，首屏导航到 `/console` |
| 6 | 版本 | 运行时聚合 `/api/runtime/status` 显示 `1.0.1` |
| 7 | 状态文件 | `launcher.lock` / `launcher.ready.json` / `launcher.stop` 无残留，新 `run_id` 生效 |
| 8 | 用户数据 | 升级前后比对 `<profile>` 下日志、DB、SDK 缓存未被清除 |
| 9 | 单实例 | 再启动一个实例仍只聚焦原窗口，不重复拉 launcher |
| 10 | 开始菜单 | 只有一个 Plateau 项，无重复快捷方式 |
| 11 | 卸载 | 静默卸载成功（`uninstall_exit=0`），安装目录移除 |

### 1.6 失败注入（建议各跑一次）

| 场景 | 操作 | 期望 |
|---|---|---|
| 端点不可达 | 停掉 http.server 后启动 app | 首屏正常、无 `desktop-error.txt`，日志仅一条失败记录 |
| 签名不匹配 | 改 `latest.json` 的 `signature` 一个字符 | 拒绝安装，**不执行** `stop_launcher()`，服务继续可用 |
| 下载中断 | 下载途中停掉服务器 | 不进入安装阶段，旧版本继续跑 |
| 拒绝降级 | 把 manifest `version` 改成 `0.9.9` | 不提示更新 |
| manifest 残缺 | 删掉 `platforms` 或填一个不存在的平台键 | 整体校验失败，静默记录，不影响首屏 |

> 插件会**先整体校验 manifest 再比较版本**，所以只列真实存在的 `windows-x86_64`。

### 1.7 清理

```powershell
# 停服务器、卸载测试安装、删除隔离 profile
Remove-Item -Recurse -Force $env:PLATEAU_HOME
Remove-Item -Recurse -Force $site
# 把 VERSION 改回 1.0.0，再跑一次同步 + 守卫，避免仓库版本漂移
python scripts/sync_version.py --update
python scripts/ci_cheap.py
```

---

## 2. 后续正式发布流程

### 2.1 一次性准备

1. **备份私钥**。`~/.tauri/plateau.key` 是 `--ci` 生成的无密码密钥。私钥丢失 = 所有已安装用户永远收不到更新，且**当前实现不支持无缝密钥轮换**（见 2.5）。存密码管理器，CI 配两个 Secret：
   - `TAURI_SIGNING_PRIVATE_KEY`（私钥内容或路径）
   - `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`（本密钥为空）
2. **部署 HTTPS 静态托管**。任选：公开仓库的 GitHub Release asset、对象存储/CDN、自建 Nginx。要求：
   - `latest.json`、`setup.exe`、`setup.exe.sig` 三个文件可匿名 HTTPS 下载；
   - `latest.json` 的 URL 稳定（例如 `https://<host>/plateau/latest.json`），不要每次换路径。
3. **改配置**（`desktop/src-tauri/tauri.conf.json`）：
   - `plugins.updater.endpoints` → `["https://<host>/plateau/latest.json"]`
   - **删除** `dangerousInsecureTransportProtocol`
   - 其余（`pubkey`、`installMode: passive`）保持不变
   - 改完必须重新 `npx tauri build`（配置编译进二进制）。
4. **CI Secret**：把第 1 步两个 Secret 配到发版 workflow；不要用 `.env` 文件（Tauri 不读）。

### 2.2 每次发版 SOP

```bash
# 1. 版本：只改 VERSION 这一个文件
python scripts/sync_version.py --update        # 回写 6 处
python scripts/ci_cheap.py                     # 全绿才继续

# 2. 组装 + 守卫
python packaging/build.py --sdk-cli <path> all --assemble
python packaging/build.py verify               # 8 项资源齐备

# 3. 带签名构建（PowerShell）
#    $env:TAURI_SIGNING_PRIVATE_KEY = ... ; npx tauri build
#    产物：Plateau_<ver>_x64-setup.exe + .exe.sig

# 4. 生成正式 manifest（版本自动取 VERSION，URL 必须 https）
python scripts/build_update_manifest.py \
  --url "https://<host>/plateau/Plateau_<ver>_x64-setup.exe" \
  --notes "<本次变更摘要>" \
  --out "<上传目录>/latest.json"
python scripts/build_update_manifest.py --verify

# 5. 上传三个文件到固定路径
#    Plateau_<ver>_x64-setup.exe
#    Plateau_<ver>_x64-setup.exe.sig
#    latest.json
```

发版顺序建议：先上传 `setup.exe` + `.sig`，**最后**覆盖 `latest.json`（避免 manifest 先到、包未到导致用户下载 404）。

若用 GitHub Releases 托管 `latest.json`，注意 `releases/latest/download/latest.json` 只解析到最新的 **non-draft / non-prerelease** Release；beta 版本要标为 prerelease 并用独立 URL。

### 2.3 CI 草案（当前未建 workflow）

- 触发：`workflow_dispatch`（沿用「push 只跑 cheap lane、重活手动触发」的约定）。
- Runner：Windows，装 Python / Node / Rust。
- 步骤：注入两个签名 Secret → 复用 2.2 的 1~5 步 → 上传三个 artifact 到托管位置。
- 门禁：`ci_cheap.py` + `sync_version.py` + `build_update_manifest.py --verify` 任一失败即中止。

### 2.4 灰度与渠道

| 手段 | 做法 |
|---|---|
| 灰度 | 先只把 `latest.json` 换成新版本给少量机器（用 `PLATEAU_UPDATE_ENDPOINT` 指向灰度 manifest），确认无异常后覆盖正式 `latest.json` |
| beta | 独立 manifest URL，客户端设 `PLATEAU_UPDATE_ENDPOINT=<beta url>`；beta  Release 标 prerelease |
| 关闭检查 | `PLATEAU_UPDATE_DISABLE=1`（离线 / 内网环境） |
| 手动检查 | 托盘「检查更新」跳过 24h 节流 |

### 2.5 回滚

- **默认禁止降级**：插件比较 `release.version > current_version`。把 `latest.json` 指回旧版本只会让检查失败，**不能**让已升级用户降级。
- **紧急回滚（推荐）**：发布一个**更高版本号、内容回退到上个 tag** 的包（例如 1.0.2 的内容 = 1.0.0），所有用户「向前回滚」。
- **单点回退**：手动运行旧版安装包 `/S`（currentUser 免管理员），用户数据目录不受影响。
- **数据兼容**：DB schema 必须只向前兼容。建议在 `<profile>` 下加 `schema_version` 标记，旧版本遇到更高 schema 时明确报错，避免静默损坏。

### 2.6 密钥轮换（当前不支持）

当前二进制只内置一个公钥，没有 `updater_builder().pubkey(...)` 的运行时切换逻辑。**私钥一旦泄露或丢失，无法无缝换钥**，只能线下分发新安装包让用户手动重装。若将来需要轮换，要先发布一个「同时内置新旧公钥、按签名来源选择」的过渡版本，全量覆盖后再切到新密钥签名。

---

## 3. 参考

### 环境变量

| 变量 | 作用 |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | 构建时签名私钥（路径或内容），仅 `npx tauri build` 需要 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 私钥密码，本密钥为空 |
| `PLATEAU_UPDATE_ENDPOINT` | 运行时覆盖更新端点（灰度 / beta / 切换自建服务） |
| `PLATEAU_UPDATE_DISABLE=1` | 完全关闭更新检查 |
| `PLATEAU_HOME` | 隔离测试或迁移用户数据目录 |

### 制品与位置

| 制品 | 位置 | 入库 |
|---|---|---|
| `Plateau_<ver>_x64-setup.exe` | `desktop/src-tauri/target/release/bundle/nsis/` | 否 |
| `Plateau_<ver>_x64-setup.exe.sig` | 同上 | 否（`*.sig`） |
| `latest.json` | 同上 / `update-site/` | 否 |
| `~/.tauri/plateau.key` | 用户目录 | 否（`*.key`） |
| 公钥 | `tauri.conf.json` 的 `plugins.updater.pubkey` | 是（可公开） |
| `<profile>/logs/desktop-update.log` | 用户数据目录 | 否 |
| `<profile>/last-update-check` | 用户数据目录（安装目录之外） | 否 |

### 设计约束（改动代码前请确认）

1. 检查更新**不进** `spawn::boot()` 链路；失败只写 `desktop-update.log`，不调用 `report_failure()`。
2. **不覆盖** `updater_builder()` 默认的 `on_before_exit`（它负责 `cleanup_before_exit`）。采用 `download()`（内部已 `verify_signature`）→ `spawn::stop_launcher()` → `install()` 三段式。
3. Windows 上 `install()` 内部会 `ShellExecuteW(setup.exe /P /R /UPDATE)` + `exit(0)`，NSIS 的 `/R` 负责重启，**不要**再手动 `app.restart()`。
4. `last-update-check` 必须写在 `<profile>`（安装目录之外），否则升级覆盖时被清掉。
5. `ci_cheap.py` 会拦截「`dangerousInsecureTransportProtocol` + 非 loopback 端点」，防止联调配置带到公网。
