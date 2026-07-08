/**
 * 对话展示偏好（前端本地设置，持久化到 localStorage）。
 * 控制对话过程中是否展示「思考过程」与「工具调用」信息。
 * 由配置弹窗写入、MessageBubble / PlanView 读取，故用单例响应式对象共享，
 * 避免跨层 props 透传。
 */
import { reactive, watch } from 'vue'

const STORAGE_KEY = 'chu_display_settings'
const defaults = { showThinking: true, showTools: true }

function load() {
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') }
  } catch {
    return { ...defaults }
  }
}

export const displaySettings = reactive(load())

watch(
  displaySettings,
  (v) => localStorage.setItem(STORAGE_KEY, JSON.stringify(v)),
  { deep: true },
)
