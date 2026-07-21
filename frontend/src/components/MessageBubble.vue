<template>
  <div class="message" :class="msg.role">
    <div class="avatar" :class="msg.role">
      <template v-if="msg.role === 'user'">
        <img v-if="user?.avatar" :src="user.avatar" alt="" class="avatar-img" />
        <IconUser v-else :size="18" :stroke="1.8" />
      </template>
      <img v-else :src="chuAvatar" alt="CHU" class="avatar-img" />
    </div>

    <div class="bubble-wrap">
      <!-- Thinking block (collapsible, expanded by default) -->
      <el-collapse v-if="msg.thinking && displaySettings.showThinking" v-model="thinkingActive" class="chu-cards chu-cards--think">
        <el-collapse-item name="think">
          <template #title>
            <span v-if="thinkingInProgress" class="card-label">
              <img :src="chuThinking" class="think-spin" alt="" /> 思考中…
            </span>
            <span v-else class="card-label"><IconBulb :size="14" :stroke="1.7" /> 思考过程</span>
          </template>
          <div class="markdown-body thinking-content" v-html="renderedThinking" />
        </el-collapse-item>
      </el-collapse>

      <!-- Tool calls -->
      <el-collapse v-if="renderedTools.length && displaySettings.showTools" class="chu-cards chu-cards--tool">
        <el-collapse-item v-for="(t, i) in renderedTools" :key="i">
          <template #title>
            <span class="card-label"><IconTool :size="14" :stroke="1.7" /> {{ t.name }}</span>
          </template>
          <div class="markdown-body tool-result" v-html="t.html" />
        </el-collapse-item>
      </el-collapse>

      <!-- Human-in-the-loop question (e.g. pick a PPT template style) -->
      <HitlPrompt
        v-if="msg.hitl"
        :prompt="msg.hitl.prompt"
        :options="msg.hitl.options"
        :preview="msg.hitl.preview"
        @choose="onHitlChoose"
      />

      <!-- Generated PPT decks (clickable) -->
      <a
        v-for="(d, i) in pptDecks"
        :key="'ppt-' + i"
        class="ppt-card"
        :href="`/api/files/${encodeURIComponent(d.filename)}`"
        target="_blank"
        rel="noopener"
      >
        <span class="ppt-icon"><IconPresentation :size="22" :stroke="1.6" /></span>
        <span class="ppt-meta">
          <span class="ppt-name">{{ d.filename }}</span>
          <span class="ppt-sub">{{ d.slides }} 张幻灯片 · 点击打开</span>
        </span>
        <IconExternalLink :size="17" :stroke="1.7" class="ppt-open" />
      </a>

      <!-- Plan-execute view -->
      <PlanView
        v-if="msg.plan?.length || msg.phase === 'planning'"
        :plan="msg.plan"
        :step-streams="msg.stepStreams || []"
        :active-step="msg.activeStep ?? -1"
        :phase="msg.phase || ''"
        :done="msg.done"
      />

      <!-- Main content -->
      <div v-if="msg.content" class="bubble" :class="msg.role">
        <template v-if="msg.role === 'user'">
          <span class="user-text">{{ msg.content }}</span>
        </template>
        <template v-else>
          <div class="markdown-body" v-html="renderedContent" />
          <span v-if="!msg.done" class="cursor">▋</span>
        </template>
      </div>

      <!-- 底部：复制（仅用户问题）+ 时间 + 本轮耗时，合并为一行 -->
      <div
        v-if="msg.time || showElapsed || (msg.role === 'user' && msg.content)"
        class="msg-meta"
      >
        <el-tooltip v-if="msg.role === 'user' && msg.content" content="复制" placement="top" :show-after="300">
          <button type="button" class="copy-btn" aria-label="复制问题" @click="copyQuestion">
            <IconCopy :size="14" :stroke="1.7" />
          </button>
        </el-tooltip>
        <span v-if="msg.time">{{ msg.time }}</span>
        <span
          v-if="showElapsed"
          class="elapsed"
          :class="{ ticking: !msg.done }"
        >
          <IconClock :size="12" :stroke="1.7" />
          {{ msg.done ? '耗时 ' : '' }}{{ fmt(msg.elapsed) }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  IconUser, IconBulb, IconTool, IconPresentation, IconExternalLink, IconCopy, IconClock,
} from '@tabler/icons-vue'
import PlanView from './PlanView.vue'
import HitlPrompt from './HitlPrompt.vue'
import { collectFaviconsFromToolResults, renderMarkdown } from '../utils/markdown.js'
import { displaySettings } from '../composables/displaySettings.js'
import chuAvatar from '../assets/bot-avatar.png'
import chuThinking from '../assets/chu-thinking.svg'

const props = defineProps({
  msg: { type: Object, required: true },
  user: { type: Object, default: null },
})

// Forward the user's HITL choice up to the App-supplied handler attached on the
// message (it has the sessionId + pending request id in scope). Clearing
// msg.hitl removes the prompt; the open SSE stream then resumes on its own.
function onHitlChoose(value) {
  props.msg.respondHitl?.(value)
}

// Per-round elapsed time: shown only on assistant messages that carry timing
// (older messages without `elapsed` are skipped — backward compatible).
const showElapsed = computed(() =>
  props.msg.role === 'assistant' && typeof props.msg.elapsed === 'number'
)
function fmt(sec) {
  const s = sec || 0
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const r = Math.round(s % 60)
  return `${m}分${String(r).padStart(2, '0')}秒`
}

// Copy the user's question text to the clipboard. Prefer the async Clipboard
// API; fall back to a hidden textarea + execCommand for non-secure contexts.
async function copyQuestion() {
  const text = props.msg.content ?? ''
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      if (!ok) throw new Error('execCommand copy failed')
    }
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

// Thinking block starts expanded; user can still collapse it.
const thinkingActive = ref(['think'])

// The model is still in the thinking phase while it has emitted thinking text
// but no answer content yet (and the round isn't done). Drives the animated
// "思考中…" label; once content starts flowing it falls back to "思考过程".
const thinkingInProgress = computed(() =>
  props.msg.thinking && !props.msg.content && !props.msg.done
)

const renderedContent = computed(() =>
  props.msg.role === 'user' ? '' : renderMarkdown(props.msg.content, { faviconsByUrl: sourceFavicons.value })
)
const renderedThinking = computed(() => renderMarkdown(props.msg.thinking))
const renderedTools = computed(() =>
  (props.msg.tools || []).map(t => ({
    name: t.status === 'running' ? `${t.name}（运行中）` : t.name,
    html: renderMarkdown(t.result),
  }))
)
const sourceFavicons = computed(() => {
  const tools = [
    ...(props.msg.tools || []),
    ...((props.msg.stepStreams || []).flatMap(step => step.tools || [])),
  ]
  return collectFaviconsFromToolResults(tools)
})

// Detect generated .pptx decks in tool results and surface them as clickable
// cards. A tool result wraps the script's stdout, so we scan for the embedded
// JSON payload ({ok, filename, slides}) produced by ppt/build.py.
function extractPpt(result) {
  if (typeof result !== 'string') return null
  const m = result.match(/\{[\s\S]*\}/)
  if (!m) return null
  try {
    const o = JSON.parse(m[0])
    if (o && o.ok && typeof o.filename === 'string' && o.filename.toLowerCase().endsWith('.pptx')) {
      return { filename: o.filename, slides: o.slides ?? '' }
    }
  } catch {
    // not JSON / not a ppt payload — ignore
  }
  return null
}

const pptDecks = computed(() =>
  (props.msg.tools || []).map(t => extractPpt(t.result)).filter(Boolean)
)
</script>

<style scoped>
.message {
  display: flex;
  gap: 12px;
  margin-bottom: 22px;
}
.message.user {
  flex-direction: row-reverse;
}
.avatar {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.avatar.user {
  border-radius: 50%;
  background: var(--clay-tint);
  color: var(--clay-deep);
  border: 1px solid #ecd8cd;
}
.avatar-img {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  object-fit: cover;
  background: #fff;
  box-shadow: 0 1px 3px rgba(60, 56, 50, 0.14);
}
.bubble-wrap {
  max-width: 80%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.message.user .bubble-wrap {
  align-items: flex-end;
}
.bubble {
  padding: 11px 15px;
  border-radius: 16px;
  word-break: break-word;
  font-size: 14.5px;
  line-height: 1.6;
}
.bubble.user {
  background: var(--sand);
  color: var(--ink);
  border: 1px solid var(--line);
  border-bottom-right-radius: 5px;
}
.bubble.assistant {
  background: var(--surface);
  color: var(--ink);
  border: 1px solid var(--line);
  border-bottom-left-radius: 5px;
  box-shadow: 0 1px 4px rgba(60, 56, 50, 0.04);
}
.user-text {
  white-space: pre-wrap;
  line-height: 1.6;
}
.msg-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--ink-faint);
  padding: 0 4px;
}
.elapsed {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.elapsed.ticking {
  color: var(--clay);
  font-variant-numeric: tabular-nums;
}
.chu-cards {
  width: 100%;
}
.card-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--ink-soft);
}
.thinking-content {
  font-size: 12.5px;
  color: var(--el-text-color-regular);
  max-height: 300px;
  overflow-y: auto;
}
.tool-result {
  font-size: 12.5px;
  max-height: 200px;
  overflow-y: auto;
  color: var(--el-text-color-regular);
}
.ppt-card {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 11px 14px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface);
  text-decoration: none;
  color: var(--ink);
  box-shadow: 0 1px 4px rgba(60, 56, 50, 0.04);
  transition: border-color 0.16s, box-shadow 0.16s, transform 0.16s;
}
.ppt-card:hover {
  border-color: var(--clay);
  box-shadow: 0 3px 12px rgba(193, 95, 60, 0.14);
  transform: translateY(-1px);
}
.ppt-icon {
  display: flex;
  flex-shrink: 0;
  color: var(--clay);
}
.ppt-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}
.ppt-name {
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ppt-sub {
  font-size: 11px;
  color: var(--ink-soft);
}
.ppt-open {
  color: var(--clay);
  flex-shrink: 0;
}
.cursor {
  display: inline-block;
  animation: blink 0.9s step-end infinite;
  margin-left: 1px;
  color: var(--clay);
}
@keyframes blink {
  50% { opacity: 0; }
}
.think-spin {
  width: 14px;
  height: 14px;
  vertical-align: -2px;
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
.copy-btn {
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  padding: 2px;
  cursor: pointer;
  border-radius: 7px;
  color: var(--ink-faint);
  opacity: 0;
  transition: opacity 0.15s, color 0.15s, background 0.15s;
}
.message.user:hover .copy-btn,
.copy-btn:focus-visible {
  opacity: 1;
}
.copy-btn:hover {
  color: var(--clay);
  background: var(--clay-tint);
}
</style>
