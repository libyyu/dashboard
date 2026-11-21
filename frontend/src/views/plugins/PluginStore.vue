<template>
  <div class="plugin-store">
    <div class="store-header">
      <h2 class="store-title">插件仓库</h2>
      <p class="store-sub">浏览并安装插件；安装 / 卸载后需重启 Plateau 生效</p>
    </div>

    <el-alert
      v-if="!registryReachable"
      title="插件仓库不可达，请确认 registry 服务（PLATEAU_REGISTRY_URL）已启动"
      type="warning"
      :closable="false"
      show-icon
      class="store-alert"
    />

    <el-alert
      v-if="plugins.error"
      :title="plugins.error"
      type="error"
      :closable="false"
      show-icon
      class="store-alert"
    />

    <el-tabs v-model="activeTab" class="store-tabs">
      <!-- 已安装：紧凑卡片，详情（含环境检查）hover 情报图标显示 -->
      <el-tab-pane label="已安装" name="installed">
        <el-empty
          v-if="!plugins.loading && installedPlugins.length === 0"
          description="尚未安装任何插件，去「市场」看看"
        />
        <div v-loading="plugins.loading" class="store-grid">
          <el-card v-for="p in installedPlugins" :key="p.id" class="store-card" shadow="hover">
            <div class="card-header">
              <span class="card-name">{{ p.name || p.id }}</span>
              <div class="header-tags">
                <el-tag
                  v-if="p.id === 'codebuddy-cli' && envCheck"
                  size="small"
                  :type="envCheck.available ? 'success' : 'danger'"
                  effect="plain"
                >
                  {{ envCheck.available ? '环境正常' : '环境不完整' }}
                </el-tag>
                <el-tag v-if="!p.enabled" size="small" type="info" effect="plain">已停用</el-tag>
                <el-tag size="small" :type="kindTagType(p.kind)" effect="plain">
                  {{ p.kind || 'frontend' }}
                </el-tag>
                <el-popover placement="bottom-end" :width="360" trigger="hover">
                  <template #reference>
                    <span class="info-dot"><el-icon><InfoFilled /></el-icon></span>
                  </template>
                  <div class="psx-detail">
                    <div class="psx-row"><span class="psx-label">插件 ID</span><span class="psx-value">{{ p.id }}</span></div>
                    <div class="psx-row"><span class="psx-label">已安装</span><span class="psx-value">v{{ p.version }}</span></div>
                    <div class="psx-row">
                      <span class="psx-label">最新版本</span>
                      <span class="psx-value">{{ latestVersion(p.id) ?? '—' }}</span>
                    </div>
                    <div v-if="p.provides" class="psx-row">
                      <span class="psx-label">提供能力</span><span class="psx-value">{{ p.provides }}</span>
                    </div>
                    <template v-if="p.id === 'codebuddy-cli'">
                      <div class="psx-divider">运行环境</div>
                      <div v-loading="plugins.checkingEnv">
                        <ul v-if="envCheck" class="psx-checks">
                          <li
                            v-for="c in envCheck.checks"
                            :key="c.id"
                            class="psx-check"
                            :class="c.ok ? 'is-ok' : 'is-bad'"
                          >
                            <span class="psx-check-label">{{ c.label }}</span>
                            <span class="psx-check-detail">{{ c.detail }}</span>
                            <el-button
                              v-if="c.id === 'auth' && !c.ok"
                              size="small"
                              text
                              type="primary"
                              :loading="loginLoading"
                              @click="onLoginCli"
                            >
                              去登录
                            </el-button>
                          </li>
                        </ul>
                        <span v-else-if="!plugins.checkingEnv" class="psx-unknown">未检查</span>
                        <div class="psx-actions">
                          <el-button size="small" :disabled="plugins.checkingEnv" @click="onCheckEnv">
                            重新检查
                          </el-button>
                          <el-button
                            v-if="envCheck && !envCheck.available"
                            size="small"
                            type="primary"
                            :loading="plugins.installing === 'codebuddy-cli'"
                            @click="onRepair"
                          >
                            {{ envCheck.action === 'install' ? '安装 codebuddy-cli' : '修复' }}
                          </el-button>
                        </div>
                      </div>
                    </template>
                  </div>
                </el-popover>
              </div>
            </div>
            <div class="card-meta-inline">
              <span>v{{ p.version }}</span>
              <template v-if="p.provides">
                <span class="meta-dot">·</span>
                <span>{{ p.provides }}</span>
              </template>
            </div>
            <div class="card-actions">
              <el-button
                v-if="hasUpdateFor(p.id)"
                size="small"
                type="primary"
                :loading="plugins.installing === p.id"
                @click="onInstall(p.id, latestVersion(p.id))"
              >
                更新至 v{{ latestVersion(p.id) }}
              </el-button>
              <el-button
                size="small"
                text
                type="danger"
                :loading="plugins.installing === p.id"
                @click="onUninstall(p.id)"
              >
                卸载
              </el-button>
            </div>
            <div v-if="plugins.installing === p.id && plugins.progress" class="card-progress">
              <el-progress
                :percentage="progressPercent"
                :indeterminate="progressPercent === null"
                :show-text="false"
                :stroke-width="6"
                :duration="2"
              />
              <span class="progress-text">{{ plugins.progress.message || '安装中…' }}</span>
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- 市场：registry 可安装目录 -->
      <el-tab-pane label="市场" name="market">
        <el-empty
          v-if="!plugins.loading && catalogPlugins.length === 0"
          description="仓库暂无可用插件"
        />
        <div v-loading="plugins.loading" class="store-grid">
          <el-card v-for="p in catalogPlugins" :key="p.id" class="store-card" shadow="hover">
            <div class="card-header">
              <span class="card-name">{{ p.name || p.id }}</span>
              <el-tag size="small" :type="kindTagType(p.kind)" effect="plain">
                {{ p.kind || 'frontend' }}
              </el-tag>
            </div>
            <p class="card-desc">{{ p.description || '（无描述）' }}</p>
            <div class="card-meta-inline">
              <span>v{{ p.latest_version || '-' }}</span>
              <template v-if="p.requires && p.requires.length">
                <span class="meta-dot">·</span>
                <span>依赖 {{ p.requires.join('、') }}</span>
              </template>
            </div>
            <div class="card-actions">
              <template v-if="installed(p.id)">
                <el-tag size="small" type="success" effect="plain">
                  已安装 v{{ installed(p.id)!.version }}
                </el-tag>
                <el-button
                  v-if="hasUpdate(p)"
                  size="small"
                  type="primary"
                  :loading="plugins.installing === p.id"
                  @click="onInstall(p.id, p.latest_version)"
                >
                  更新至 v{{ p.latest_version }}
                </el-button>
                <el-button
                  size="small"
                  text
                  type="danger"
                  :loading="plugins.installing === p.id"
                  @click="onUninstall(p.id)"
                >
                  卸载
                </el-button>
              </template>
              <el-button
                v-else
                size="small"
                type="primary"
                :loading="plugins.installing === p.id"
                @click="onInstall(p.id)"
              >
                安装
              </el-button>
            </div>
            <div v-if="plugins.installing === p.id && plugins.progress" class="card-progress">
              <el-progress
                :percentage="progressPercent"
                :indeterminate="progressPercent === null"
                :show-text="false"
                :stroke-width="6"
                :duration="2"
              />
              <span class="progress-text">{{ plugins.progress.message || '安装中…' }}</span>
            </div>
          </el-card>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { InfoFilled } from '@element-plus/icons-vue'
import { usePluginsStore } from '../../stores/plugins'
import { http } from '../../api/http'
import { ENDPOINTS } from '../../api/contract'

const plugins = usePluginsStore()

const activeTab = ref<'installed' | 'market'>('installed')

const installedPlugins = computed(() => plugins.installed)
const catalogPlugins = computed(() => plugins.catalog?.plugins ?? [])
const registryReachable = computed(() => plugins.catalog?.reachable ?? true)
const envCheck = computed(() => plugins.envCheck)

function installed(id: string) {
  return plugins.installed.find((p) => p.id === id)
}

function latestVersion(id: string): string | undefined {
  return plugins.catalog?.plugins.find((p) => p.id === id)?.latest_version
}

function versionTuple(v?: string): number[] {
  if (!v) return []
  return v.split('.').map((n) => {
    const x = parseInt(n, 10)
    return Number.isNaN(x) ? 0 : x
  })
}

function isNewer(a: string, b: string): boolean {
  const x = versionTuple(a)
  const y = versionTuple(b)
  const len = Math.max(x.length, y.length)
  for (let i = 0; i < len; i++) {
    const dx = x[i] ?? 0
    const dy = y[i] ?? 0
    if (dx !== dy) return dx > dy
  }
  return false
}

function hasUpdate(p: { id: string; latest_version?: string }) {
  const cur = installed(p.id)
  if (!cur || !p.latest_version) return false
  return isNewer(p.latest_version, cur.version)
}

function hasUpdateFor(id: string): boolean {
  const cur = installed(id)
  const latest = latestVersion(id)
  if (!cur || !latest) return false
  return isNewer(latest, cur.version)
}

const KIND_TAG_TYPES: Record<string, 'info' | 'warning' | 'danger'> = {
  frontend: 'info',
  sidecar: 'warning',
  cli: 'danger',
}

function kindTagType(kind?: string) {
  return KIND_TAG_TYPES[kind ?? ''] ?? 'info'
}

// 进度百分比：total 已知时显示确定进度，未知时走 indeterminate 动画
const progressPercent = computed<number | null>(() => {
  const p = plugins.progress
  if (!p || !p.total) return null
  return Math.min(100, Math.round(((p.current ?? 0) / p.total) * 100))
})

async function onInstall(id: string, version = '') {
  const updating = version !== '' && installed(id) !== undefined
  const ok = await plugins.install(id, version)
  if (ok) ElMessage.success(updating ? '已更新，重启 Plateau 后生效' : '已安装，重启 Plateau 后生效')
  else ElMessage.error(plugins.error || (updating ? '更新失败' : '安装失败'))
}

async function onUninstall(id: string) {
  const ok = await plugins.uninstall(id)
  if (ok) ElMessage.success('已卸载，重启 Plateau 后生效')
  else ElMessage.error(plugins.error || '卸载失败')
}

async function onCheckEnv() {
  await plugins.checkEnv()
}

async function onRepair() {
  // 修复 = 重装 codebuddy-cli（插件安装前置确保 node 运行时，缺哪个补哪个）
  const version = latestVersion('codebuddy-cli') ?? ''
  const ok = await plugins.install('codebuddy-cli', version)
  if (ok) ElMessage.success('修复完成，重启 Plateau 后生效')
  else ElMessage.error(plugins.error || '修复失败')
}

const loginLoading = ref(false)

async function onLoginCli() {
  // 登录引导：本地 CLI 弹窗让用户粘贴 API key，保存后重查环境（auth 项变为已登录）
  let value = ''
  try {
    const result = await ElMessageBox.prompt(
      '请输入 CodeBuddy 的 API Key（可在 CodeBuddy 账户 / 控制台获取）',
      '登录 CodeBuddy CLI',
      {
        inputType: 'password',
        confirmButtonText: '保存并登录',
        cancelButtonText: '取消',
      },
    )
    value = (result.value ?? '').trim()
  } catch {
    return // 用户取消
  }
  if (!value) return
  loginLoading.value = true
  try {
    await http(ENDPOINTS.modelAuthLogin.replace('{provider}', 'codebuddy-cli'), {
      method: 'POST',
      body: JSON.stringify({ api_key: value }),
    })
    ElMessage.success('已保存 API Key')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '登录失败')
  } finally {
    loginLoading.value = false
  }
  await plugins.checkEnv()
}
</script>

<style scoped>
.plugin-store { max-width: 1080px; }
.store-header { margin-bottom: 4px; }
.store-title { font-size: 20px; font-weight: 650; margin: 0 0 6px; }
.store-sub { color: var(--pd-muted); font-size: 13px; margin: 0 0 14px; }
.store-alert { margin-bottom: 16px; }
.store-tabs :deep(.el-tabs__header) { margin-bottom: 16px; }

.store-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
}
.store-card :deep(.el-card__body) { padding: 14px 16px; }
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.header-tags { display: flex; align-items: center; gap: 6px; }
.card-name { font-size: 15px; font-weight: 640; }
.info-dot {
  display: inline-flex;
  align-items: center;
  color: var(--pd-muted);
  cursor: help;
  font-size: 14px;
  line-height: 1;
}
.info-dot:hover { color: var(--el-color-primary); }
.card-desc {
  color: var(--pd-muted);
  font-size: 13px;
  line-height: 1.5;
  margin: 0 0 10px;
}
.card-meta-inline {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #7a849c;
  margin-bottom: 12px;
}
.meta-dot { color: #b6bdcd; }
.card-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.card-progress {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 10px;
}
.progress-text {
  font-size: 12px;
  color: var(--pd-muted);
}
</style>

<!-- popover 内容渲染在 body（teleport），scoped 样式不生效，用全局样式 + psx- 前缀 -->
<style>
.psx-detail { display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
.psx-row { display: flex; align-items: baseline; gap: 10px; }
.psx-label { flex: 0 0 64px; color: #7a849c; }
.psx-value { color: var(--pd-muted); word-break: break-all; }
.psx-divider {
  margin-top: 4px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
  font-weight: 620;
  color: var(--pd-muted);
}
.psx-checks {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.psx-check { display: flex; align-items: baseline; gap: 8px; }
.psx-check-label { flex: 0 0 110px; color: #7a849c; }
.psx-check-detail { color: var(--pd-muted); word-break: break-all; flex: 1; }
.psx-check.is-ok .psx-check-label { color: var(--el-color-success); }
.psx-check.is-bad .psx-check-label { color: var(--el-color-danger); }
.psx-unknown { color: var(--pd-muted); }
.psx-actions { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
</style>
