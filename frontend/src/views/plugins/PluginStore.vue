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

    <el-empty
      v-if="!plugins.loading && catalogPlugins.length === 0"
      description="仓库暂无可用插件"
    />

    <div v-loading="plugins.loading" class="store-grid">
      <el-card v-for="p in catalogPlugins" :key="p.id" class="store-card" shadow="hover">
        <div class="card-header">
          <span class="card-name">{{ p.name || p.id }}</span>
          <el-tag size="small" :type="p.kind === 'sidecar' ? 'warning' : 'info'" effect="plain">
            {{ p.kind === 'sidecar' ? 'sidecar' : 'frontend' }}
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
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { usePluginsStore } from '../../stores/plugins'

const plugins = usePluginsStore()

const catalogPlugins = computed(() => plugins.catalog?.plugins ?? [])
const registryReachable = computed(() => plugins.catalog?.reachable ?? true)

function installed(id: string) {
  return plugins.installed.find((p) => p.id === id)
}

async function onInstall(id: string) {
  const ok = await plugins.install(id)
  if (ok) ElMessage.success('已安装，重启 Plateau 后生效')
  else ElMessage.error(plugins.error || '安装失败')
}

async function onUninstall(id: string) {
  const ok = await plugins.uninstall(id)
  if (ok) ElMessage.success('已卸载，重启 Plateau 后生效')
  else ElMessage.error(plugins.error || '卸载失败')
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
</style>
