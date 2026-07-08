<template>
  <div class="chat-input">
    <div class="input-toolbar">
      <el-tooltip
        :content="mode === 'plan-execute' ? '取消计划执行模式' : '开启计划执行模式'"
        placement="top"
        :show-after="300"
      >
        <button
          class="cmd-btn"
          :class="{ active: mode === 'plan-execute' }"
          :aria-pressed="mode === 'plan-execute'"
          @click="emit('update:mode', mode === 'plan-execute' ? 'react' : 'plan-execute')"
        >
          <IconListCheck :size="15" :stroke="1.8" />
          <span>Plan</span>
        </button>
      </el-tooltip>
    </div>
    <div class="input-shell">
      <el-input
        v-model="text"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 6 }"
        placeholder="发送消息… (Enter 发送，Shift+Enter 换行)"
        :disabled="streaming"
        @keydown.enter.exact.prevent="send"
      />
      <button
        v-if="!streaming"
        class="action-btn send"
        :disabled="!text.trim()"
        aria-label="发送"
        @click="send"
      >
        <IconArrowUp :size="19" :stroke="2" />
      </button>
      <button
        v-else
        class="action-btn stop"
        aria-label="停止"
        @click="stop"
      >
        <IconPlayerStopFilled :size="17" />
      </button>
    </div>
    <div class="input-hint">Enter 发送 · Shift+Enter 换行</div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { IconArrowUp, IconPlayerStopFilled, IconListCheck } from '@tabler/icons-vue'

const props = defineProps({
  streaming: Boolean,
  mode: { type: String, default: 'react' },
})
const emit = defineEmits(['send', 'stop', 'update:mode'])
const text = ref('')

function send() {
  const msg = text.value.trim()
  if (!msg || props.streaming) return
  emit('send', msg)
  text.value = ''
}

function stop() {
  emit('stop')
}
</script>

<style scoped>
.chat-input {
  padding: 14px 24px 18px;
  background: linear-gradient(180deg, rgba(250, 249, 245, 0) 0%, var(--paper) 38%);
}
.input-shell {
  position: relative;
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  align-items: flex-end;
}
.input-shell :deep(.el-textarea) {
  flex: 1;
}
.input-shell :deep(.el-textarea__inner) {
  padding: 11px 52px 11px 16px;
  border-radius: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  box-shadow: 0 2px 14px rgba(60, 56, 50, 0.06);
  color: var(--ink);
  font-family: var(--font-body);
  font-size: 14.5px;
  /* integer line-height so autosize lands on exact pixel heights — avoids a
     1px sub-pixel overflow that would show a resting scrollbar */
  line-height: 24px;
  resize: none;
  overflow-x: hidden;
  transition: border-color 0.16s, box-shadow 0.16s;
  /* Firefox: thin, soft scrollbar */
  scrollbar-width: thin;
  scrollbar-color: rgba(120, 110, 95, 0.32) transparent;
}
/* WebKit: slim rounded thumb, transparent track inset from the rounded
   corners so the scrollbar never pokes past the input's 16px radius */
.input-shell :deep(.el-textarea__inner)::-webkit-scrollbar {
  width: 6px;
}
.input-shell :deep(.el-textarea__inner)::-webkit-scrollbar-track {
  background: transparent;
  margin: 14px 0;
}
.input-shell :deep(.el-textarea__inner)::-webkit-scrollbar-thumb {
  background: rgba(120, 110, 95, 0.3);
  border-radius: 6px;
}
.input-shell :deep(.el-textarea__inner):hover::-webkit-scrollbar-thumb {
  background: rgba(120, 110, 95, 0.45);
}
.input-shell :deep(.el-textarea__inner:hover) {
  border-color: #d8d2c5;
}
.input-shell :deep(.el-textarea__inner:focus) {
  border-color: var(--clay);
  box-shadow: 0 2px 16px rgba(193, 95, 60, 0.14);
}
.input-shell :deep(.el-textarea__inner::placeholder) {
  color: var(--ink-faint);
}
.action-btn {
  position: absolute;
  right: 8px;
  bottom: 7px;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  cursor: pointer;
  color: #fff;
  transition: background 0.16s, transform 0.12s, opacity 0.16s;
}
.action-btn.send {
  background: var(--clay);
}
.action-btn.send:hover:not(:disabled) {
  background: var(--clay-deep);
  transform: translateY(-1px);
}
.action-btn.send:disabled {
  background: #ddd6ca;
  cursor: not-allowed;
}
.action-btn.stop {
  background: var(--el-color-danger);
}
.action-btn.stop:hover {
  background: var(--el-color-danger-dark-2);
  transform: translateY(-1px);
}
.input-toolbar {
  max-width: 760px;
  margin: 0 auto 9px;
  padding-left: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.cmd-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 11px;
  border-radius: 9px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink-soft);
  font-family: var(--font-body);
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color 0.16s, color 0.16s, background 0.16s, box-shadow 0.16s;
}
.cmd-btn:hover {
  border-color: var(--clay);
  color: var(--clay);
  box-shadow: 0 2px 8px rgba(193, 95, 60, 0.1);
}
.cmd-btn.active {
  background: var(--clay);
  border-color: var(--clay);
  color: #fff;
}
.cmd-btn.active:hover {
  background: var(--clay-deep);
  border-color: var(--clay-deep);
  color: #fff;
}
.input-hint {
  max-width: 760px;
  margin: 7px auto 0;
  text-align: center;
  font-size: 11px;
  color: var(--ink-faint);
}
</style>
