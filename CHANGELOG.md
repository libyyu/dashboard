# 更新记录

**仓库约定：每次代码改动都升版本号，并在这里记录更新内容。**

- 版本单一真源是 `VERSION`，用 `python scripts/bump_version.py --notes "..."` 升级并回写所有派生点；
  `python scripts/ci_cheap.py` 会拦截「改了代码却没升版本 / 没写更新内容」。
- 自动更新弹窗的「更新说明」默认读本文件最新版本条目（`scripts/build_update_manifest.py`）。
- 格式：`## [版本] - YYYY-MM-DD`，按「新增 / 修复 / 变更」分组。

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
