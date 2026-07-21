<template>
  <div class="plan-view">
    <div v-if="phase === 'planning'" class="phase-tag">
      <span class="spinner" /> <IconClipboardList :size="15" :stroke="1.7" /> 正在规划…
    </div>
    <div v-else-if="phase === 'summarizing'" class="phase-tag">
      <span class="spinner" /> <IconPencil :size="15" :stroke="1.7" /> 正在汇总…
    </div>
    <div v-else-if="plan.length" class="plan-title">
      <IconListCheck :size="16" :stroke="1.7" /> 执行计划（{{ plan.length }} 步）
    </div>

    <el-steps
      v-if="renderSteps.length"
      :active="activeStep > 0 ? activeStep - 1 : 0"
      direction="vertical"
    >
      <el-step
        v-for="(s, i) in renderSteps"
        :key="i"
        :status="stepStatus(i)"
        :title="s.task"
      >
        <template #description>
          <el-collapse
            v-if="showThinkingBlock(s) && displaySettings.showThinking"
            :model-value="thinkActive(i)"
            @update:model-value="v => setThinkActive(i, v)"
            class="chu-cards chu-cards--think step-thinking"
          >
            <el-collapse-item name="think">
              <template #title>
                <span class="thinking-label">
                  <img v-if="thinkingLabel(i) === '思考中…'" :src="chuThinking" class="think-spin" alt="" />
                  <IconBulb v-else :size="13" :stroke="1.7" /> {{ thinkingLabel(i) }}
                </span>
              </template>
              <div v-if="s.thinking" class="markdown-body thinking-content" v-html="renderMarkdown(s.thinking)" />
            </el-collapse-item>
          </el-collapse>
          <div v-if="s.tools?.length && displaySettings.showTools" class="step-tools">
            <span
              v-for="(t, j) in s.tools"
              :key="j"
              class="tool-chip"
              :class="`tool-chip--${t.status || 'success'}`"
            >
              <IconTool :size="12" :stroke="1.7" /> {{ t.name }}{{ toolStatusLabel(t) }}
            </span>
          </div>
          <div v-if="s.text" class="markdown-body step-text" v-html="renderStepText(s)" />
        </template>
      </el-step>
    </el-steps>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { IconClipboardList, IconPencil, IconListCheck, IconBulb, IconTool } from '@tabler/icons-vue'
import { collectFaviconsFromToolResults, renderMarkdown } from '../utils/markdown.js'
import { displaySettings } from '../composables/displaySettings.js'
import chuThinking from '../assets/chu-thinking.svg'

const props = defineProps({
  plan: { type: Array, default: () => [] },
  stepStreams: { type: Array, default: () => [] },
  activeStep: { type: Number, default: -1 },
  phase: { type: String, default: '' },
  done: { type: Boolean, default: false },
})

// Fall back to plan titles when stepStreams hasn't initialized yet
const renderSteps = computed(() => {
  if (props.stepStreams.length) return props.stepStreams
  return props.plan.map(task => ({ task, text: '', thinking: '', tools: [], status: 'wait' }))
})

function stepStatus(i) {
  const explicit = renderSteps.value[i]?.status
  if (explicit && explicit !== 'wait') return explicit
  const oneBased = i + 1
  if (props.done) return 'success'
  if (props.activeStep > 0) {
    if (oneBased < props.activeStep) return 'success'
    if (oneBased === props.activeStep) return 'process'
  }
  return 'wait'
}

// Per-step thinking blocks start expanded; once the user toggles one we
// remember their choice (without affecting the other steps).
const thinkState = reactive({})
function thinkActive(i) {
  return thinkState[i] ?? ['think']
}
function setThinkActive(i, v) {
  thinkState[i] = v
}

function thinkingLabel(i) {
  const s = renderSteps.value[i]
  const inProgress = !!s?.thinkingActive
  return inProgress ? '思考中…' : '思考过程'
}

function showThinkingBlock(s) {
  return !!(s?.thinking || s?.thinkingActive || s?.status === 'success' || s?.status === 'error')
}

function toolStatusLabel(t) {
  if (t.status === 'running') return '（运行中）'
  if (t.status === 'error') return '（失败）'
  return '（完成）'
}

function renderStepText(step) {
  return renderMarkdown(step?.text, {
    faviconsByUrl: collectFaviconsFromToolResults(step?.tools || []),
  })
}
</script>

<style scoped>
.plan-view {
  margin: 8px 0;
  padding: 14px 16px;
  background: var(--surface-warm);
  border: 1px solid var(--line);
  border-left: 3px solid var(--clay);
  border-radius: 14px;
  font-size: 13px;
  max-width: 100%;
  overflow-x: hidden;
  overflow-y: visible;
  padding-bottom: 18px;
}
.plan-view :deep(.el-steps) {
  max-width: 100%;
}
.plan-view :deep(.el-step__main) {
  min-width: 0;
  max-width: 100%;
}
.plan-view :deep(.el-step__title) {
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.45;
}
.plan-view :deep(.el-step__description) {
  max-width: 100%;
  min-width: 0;
  overflow-wrap: anywhere;
}
.step-thinking {
  margin: 4px 0;
}
.thinking-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--ink-soft);
}
.think-spin {
  width: 13px;
  height: 13px;
  animation: spin 0.9s linear infinite;
}
.thinking-content {
  font-size: 12px;
  color: var(--el-text-color-regular);
  max-height: 240px;
  overflow-y: auto;
  overflow-wrap: anywhere;
}
.thinking-empty {
  font-size: 12px;
  color: var(--ink-soft);
  padding: 2px 0;
}
.plan-title {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--ink);
}
.phase-tag {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--clay);
}
.spinner {
  display: inline-block;
  width: 11px;
  height: 11px;
  border: 2px solid var(--clay);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.step-text {
  font-size: 12px;
  color: var(--el-text-color-regular);
  margin-top: 4px;
  max-width: 100%;
  overflow-wrap: anywhere;
  padding-bottom: 4px;
}
.step-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 5px;
}
.tool-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  font-size: 11px;
  padding: 2px 9px;
  border-radius: 20px;
  background: var(--clay-tint);
  color: var(--clay-deep);
  border: 1px solid #ecd8cd;
  overflow-wrap: anywhere;
  white-space: normal;
}
.tool-chip--running {
  background: #fff7e8;
  color: #9a5b00;
  border-color: #f0d49a;
}
.tool-chip--success {
  background: #eef8f0;
  color: #2f6b3f;
  border-color: #cbe8d1;
}
.tool-chip--error {
  background: #fff0f0;
  color: #a63a3a;
  border-color: #efc8c8;
}
</style>
