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
            v-if="s.thinking && displaySettings.showThinking"
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
              <div class="markdown-body thinking-content" v-html="renderMarkdown(s.thinking)" />
            </el-collapse-item>
          </el-collapse>
          <div v-if="s.text" class="markdown-body step-text" v-html="renderMarkdown(s.text)" />
          <div v-if="s.tools.length && displaySettings.showTools" class="step-tools">
            <span v-for="(t, j) in s.tools" :key="j" class="tool-chip">
              <IconTool :size="12" :stroke="1.7" /> {{ t.name }}
            </span>
          </div>
        </template>
      </el-step>
    </el-steps>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { IconClipboardList, IconPencil, IconListCheck, IconBulb, IconTool } from '@tabler/icons-vue'
import { renderMarkdown } from '../utils/markdown.js'
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
  return props.plan.map(task => ({ task, text: '', tools: [] }))
})

function stepStatus(i) {
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
  // While the step has no text yet, the model is still thinking; once text
  // starts flowing the thinking phase has ended for this step.
  const s = renderSteps.value[i]
  const inProgress = (i + 1 === props.activeStep) && !s.text
  return inProgress ? '思考中…' : '思考过程'
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
  font-size: 11px;
  padding: 2px 9px;
  border-radius: 20px;
  background: var(--clay-tint);
  color: var(--clay-deep);
  border: 1px solid #ecd8cd;
}
</style>
