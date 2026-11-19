<template>
  <div class="chat-view">
    <div class="pd-panel chat-panel">
      <ChatSession class="chat-body" />
    </div>
  </div>
</template>

<script setup lang="ts">
import ChatSession from '../../components/chat/ChatSession.vue'
</script>

<style scoped>
.chat-view {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 0;
  margin-bottom: 0;
  overflow: hidden;
}
.chat-body { flex: 1; min-height: 0; }
</style>
