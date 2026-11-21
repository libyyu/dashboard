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

    <div v-loading="plugins.checkingEnv" class="env-panel">
      <div class="env-row">
        <span class="env-title">CodeBuddy CLI 运行环境</span>
        <el-tag
          v-if="envCheck"
          size="small"
          :type="envCheck.available ? 'success' : 'danger'"
          effect="plain"
        >
          {{ envCheck.available ? '正常' : '不完整' }}
        </el-tag>
        <span v-else-if="!plugins.checkingEnv" class="env-unknown">未检查</span>
        <span class="env-spacer" />
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
      <ul v-if="envCheck" class="env-checks">
        <li
          v-for="c in envCheck.checks"
          :key="c.id"
          class="env-check"
          :class="c.ok ? 'is-ok' : 'is-bad'"
        >
          <span class="env-check-label">{{ c.label }}</span>
          <span class="env-check-detail">{{ c.detail }}</span>
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
    </div>

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
        <div class="card-meta">
          <span class="meta-item">版本 {{ p.latest_version || '-' }}</span>
          <span v-if="p.requires && p.requires.length" class="meta-item">
            依赖 {{ p.requires.join('、') }}
          </span>
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
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginsStore } from '../../stores/plugins'
import { http } from '../../api/http'
import { ENDPOINTS } from '../../api/contract'

const plugins = usePluginsStore()

const catalogPlugins = computed(() => plugins.catalog?.plugins ?? [])
const registryReachable = computed(() => plugins.catalog?.reachable ?? true)
const envCheck = computed(() => plugins.envCheck)

function installed(id: string) {
  return plugins.installed.find((p) => p.id === id)
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
  const codebuddyCli = plugins.catalog?.plugins.find((p) => p.id === 'codebuddy-cli')
  const version = codebuddyCli?.latest_version ?? ''
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
.store-sub { color: var(--pd-muted); font-size: 13px; margin: 0 0 18px; }
.store-alert { margin-bottom: 16px; }

.store-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.store-card :deep(.el-card__body) { padding: 16px; }
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.card-name { font-size: 15px; font-weight: 640; }
.card-desc {
  color: var(--pd-muted);
  font-size: 13px;
  line-height: 1.5;
  min-height: 40px;
  margin: 0 0 12px;
}
.card-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #7a849c;
  margin-bottom: 12px;
}
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

.env-panel {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 16px;
  background: var(--el-fill-color-blank);
}
.env-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.env-title {
  font-size: 14px;
  font-weight: 620;
}
.env-unknown {
  font-size: 12px;
  color: var(--pd-muted);
}
.env-spacer { flex: 1; }
.env-checks {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.env-check {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}
.env-check-label {
  min-width: 120px;
  color: #7a849c;
}
.env-check-detail {
  color: var(--pd-muted);
  word-break: break-all;
}
.env-check.is-ok .env-check-label { color: var(--el-color-success); }
.env-check.is-bad .env-check-label { color: var(--el-color-danger); }
</style>
