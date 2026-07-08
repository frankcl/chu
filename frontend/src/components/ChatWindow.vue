<template>
  <div ref="container" class="chat-window" @scroll="onScroll">
    <div class="chat-column">
      <div v-if="!messages.length" class="empty-hint">
        <IconSparkles :size="34" :stroke="1.4" class="empty-icon" />
        <h1 class="empty-title">今天想聊点什么？</h1>
        <p class="empty-sub">在下方输入你的问题，开始一段对话。</p>
      </div>
      <MessageBubble v-for="msg in messages" :key="msg.id" :msg="msg" :user="user" />
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { IconSparkles } from '@tabler/icons-vue'
import MessageBubble from './MessageBubble.vue'

const props = defineProps({
  messages: { type: Array, default: () => [] },
  user: { type: Object, default: null },
})
const container = ref(null)

// Auto-scroll follows new output only while the user is parked at the bottom.
// Once they scroll up to read earlier output, we stop yanking them back down;
// scrolling back to the bottom re-enables following.
const NEAR_BOTTOM_PX = 40
const stickToBottom = ref(true)

function isNearBottom() {
  const el = container.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX
}

function onScroll() {
  stickToBottom.value = isNearBottom()
}

// A new message (length grows) — e.g. the user just sent one — always snaps
// to the bottom and re-enables following, even if they'd scrolled up before.
watch(
  () => props.messages.length,
  () => {
    stickToBottom.value = true
  }
)

watch(
  () => props.messages,
  async () => {
    if (!stickToBottom.value) return
    await nextTick()
    if (container.value) {
      container.value.scrollTop = container.value.scrollHeight
    }
  },
  { deep: true }
)
</script>

<style scoped>
.chat-window {
  flex: 1;
  overflow-y: auto;
  padding: 28px 24px 8px;
}
.chat-column {
  max-width: 760px;
  margin: 0 auto;
}
.empty-hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  min-height: 70vh;
  color: var(--ink-soft);
}
.empty-icon {
  color: var(--clay);
  margin-bottom: 16px;
}
.empty-title {
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 500;
  color: var(--ink);
  letter-spacing: 0.2px;
}
.empty-sub {
  margin-top: 8px;
  font-size: 14px;
  color: var(--ink-faint);
}
</style>
