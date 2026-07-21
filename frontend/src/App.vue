<template>
  <div class="app">
    <SideBar
      :sessions="sessionList"
      :current-id="currentSessionId"
      :authed="authed"
      :user="user"
      @new-chat="newChat"
      @switch="switchSession"
      @delete="deleteChat"
      @toggle-top="toggleTop"
      @login="loginVisible = true"
      @logout="onLogout"
      @clear-all="clearAllChats"
      @open-settings="settingsVisible = true"
    />

    <div class="main">
      <ChatWindow :messages="currentMessages" :user="user" />
      <ChatInput :streaming="streaming" :mode="mode" @update:mode="setMode" @send="onSend" @stop="onStop" />
    </div>
  </div>

  <LoginDialog v-model="loginVisible" @success="onLoginSuccess" />
  <SettingsDialog v-model="settingsVisible" :user="user" @logout="onLogout" />
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import SideBar from './components/SideBar.vue'
import ChatWindow from './components/ChatWindow.vue'
import ChatInput from './components/ChatInput.vue'
import LoginDialog from './components/LoginDialog.vue'
import SettingsDialog from './components/SettingsDialog.vue'
import {
  createSession, deleteSession, streamChat, cancelChat, generateTitle, respondHitl,
} from './api/chat.js'
import {
  listConversations, getConversation, deleteConversation, clearAllConversations,
  updateConversationTitle, regroupMessages, setConversationTop,
} from './api/history.js'
import { logout, forceRelogin, setAuthRequiredHandler, fetchMe } from './api/auth.js'
import { formatDateTime, formatDate } from './utils/datetime.js'

// 登录态：cookie/session 模式下由 onMounted 探测 /api/auth/me 决定；默认未登录。
const authed = ref(false)
const loginVisible = ref(false)
// 用户配置弹窗（用户信息 / Token 使用情况）的显隐。
const settingsVisible = ref(false)
// 当前登录用户信息 { name, username, avatar }，用于侧边栏与消息头像展示。
const user = ref(null)

// 拉取当前用户信息；失败静默（不阻断使用）。
async function loadUser() {
  try {
    user.value = await fetchMe()
  } catch {
    user.value = null
  }
}

// 从后端加载当前用户的对话历史，填充侧边栏（刷新后历史仍在）。
// 历史项的 messages 懒加载：切到该会话时再拉取 + 重建运行时记忆。
async function loadConversations() {
  try {
    const convs = await listConversations()
    sessions.value = convs.map(c => ({
      id: c.id,          // = 后端 conversation/session id（= thread_id）
      sessionId: null,   // 运行时 session 尚未创建；切换时再建并绑定
      title: c.title || '未命名对话',
      updateTime: c.update_time,   // 最后活跃时间（ms），列表展示日期用
      top: !!c.top,                // 是否置顶
      mode: mode.value,
      messages: [],
      loaded: false,     // messages 是否已从历史拉取
    }))
  } catch {
    // 历史功能未启用（未配 MySQL）或加载失败：静默，不阻断新对话。
  }
}
// 未登录直接发消息时暂存该消息，登录成功后自动补发。
const pendingText = ref('')
// token 续期由 shield 服务端在每个请求内自动完成，前端无需主动续期定时器。

function onLoginSuccess() {
  authed.value = true
  loginVisible.value = false
  loadUser()
  loadConversations()
  // 补发未登录时想发的那条消息。
  if (pendingText.value) {
    const text = pendingText.value
    pendingText.value = ''
    onSend(text)
  }
}

async function onLogout() {
  await logout()               // 失效服务端会话并清 sid cookie
  authed.value = false
  user.value = null
  settingsVisible.value = false
  sessions.value = []          // 清空侧边栏（历史仍在库，仅前端不显示）
  currentSessionId.value = null
}

onMounted(async () => {
  // 鉴权失效（请求被 shield 拦截）时：清态并弹登录框，不刷新页面。
  setAuthRequiredHandler(() => {
    authed.value = false
    loginVisible.value = true
  })
  // 探测登录态：/api/auth/me 成功即已登录（cookie 有效），失败则保持未登录。
  try {
    user.value = await fetchMe()
    authed.value = true
    loadConversations()
  } catch {
    authed.value = false
  }
})

const mode = ref('react')
const streaming = ref(false)
const currentController = ref(null)

// Per-round elapsed timer. Only one stream is ever in flight (input is disabled
// while streaming), so a single ticker suffices. It updates msg.elapsed live and
// is frozen at the round's terminal point (done / error / stop).
let tickHandle = null
function stopTicker() {
  if (tickHandle) { clearInterval(tickHandle); tickHandle = null }
}
function startTicker(msg) {
  stopTicker()
  msg.startedAt = Date.now()
  tickHandle = setInterval(() => {
    msg.elapsed = (Date.now() - msg.startedAt) / 1000
  }, 100)
}
function finalizeTiming(msg) {
  if (msg?.startedAt) msg.elapsed = (Date.now() - msg.startedAt) / 1000
  stopTicker()
}

// sessions: [{ id, sessionId, title, mode, messages }]
const sessions = ref([])
const currentSessionId = ref(null)  // internal UUID for sidebar

const sessionList = computed(() =>
  sessions.value.map(s => ({
    id: s.id,
    title: s.title,
    date: s.updateTime ? formatDate(s.updateTime) : '',
    updateTime: s.updateTime,
    top: s.top,
  }))
)

const currentSession = computed(() =>
  sessions.value.find(s => s.id === currentSessionId.value)
)

const currentMessages = computed(() => currentSession.value?.messages ?? [])

function uid() {
  return Math.random().toString(36).slice(2, 10)
}

async function ensureSessionMode(session, targetMode) {
  if (!session || session.mode === targetMode) return
  const priorMode = session.mode
  const priorId = session.id
  const priorSessionId = session.sessionId
  session.mode = targetMode
  try {
    if (!session.messages.length) {
      const sessionId = await createSession(targetMode)
      if (priorSessionId) deleteSession(priorSessionId).catch(() => {})
      session.id = sessionId
      session.sessionId = sessionId
      currentSessionId.value = sessionId
      return
    }
    const sessionId = await createSession(targetMode, { conversation_id: session.id })
    session.sessionId = sessionId
  } catch (e) {
    session.id = priorId
    session.sessionId = priorSessionId
    session.mode = priorMode
    mode.value = priorMode
    ElMessage.error(`切换模式失败: ${e.message}`)
  }
}

function setMode(nextMode) {
  mode.value = nextMode
  const session = currentSession.value
  if (!session || streaming.value) return
  ensureSessionMode(session, nextMode)
}


async function newChat() {
  try {
    const sessionId = await createSession(mode.value)
    // 用后端 session_id 作为前端会话 id（= 首消息落库后的 conversation id）。
    sessions.value.unshift({
      id: sessionId,
      sessionId,
      title: `对话 ${sessions.value.length + 1}`,
      updateTime: Date.now(),
      top: false,
      mode: mode.value,
      messages: [],
      loaded: true,
    })
    currentSessionId.value = sessionId
  } catch (e) {
    ElMessage.error(`创建会话失败: ${e.message}`)
  }
}

// 切换会话：若是尚未加载的历史项，拉取消息并创建绑定该 conversation 的运行时
// session（后端据此从历史重建「对话记忆」），之后即可在原对话上继续。
async function switchSession(id) {
  currentSessionId.value = id
  const s = sessions.value.find(x => x.id === id)
  if (!s || s.loaded) return
  try {
    const [rows, sessionId] = await Promise.all([
      getConversation(id),
      createSession(mode.value, { conversation_id: id }),
    ])
    s.messages = regroupMessages(rows)
    s.sessionId = sessionId   // == id
    s.loaded = true
  } catch (e) {
    ElMessage.error(`加载对话失败: ${e.message}`)
  }
}

async function deleteChat(id) {
  try {
    await ElMessageBox.confirm('删除后不可恢复，确定删除该对话？', '删除对话', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return  // 用户取消
  }
  // 后端一次性清理：运行时 session + 对话记忆(MemorySaver) + DB 历史。
  deleteConversation(id).catch(() => {})
  sessions.value = sessions.value.filter(x => x.id !== id)
  if (currentSessionId.value === id) {
    currentSessionId.value = sessions.value[0]?.id ?? null
  }
}

async function toggleTop(id, top) {
  const s = sessions.value.find(x => x.id === id)
  if (!s) return
  s.top = top   // 乐观更新
  try {
    await setConversationTop(id, top)
  } catch (e) {
    s.top = !top   // 失败回滚
    ElMessage.error(e.message || '操作失败')
  }
}

async function clearAllChats() {
  try {
    await ElMessageBox.confirm(
      '将永久删除你的全部对话历史，且不可恢复，确定清除？', '清除所有对话历史',
      { type: 'warning', confirmButtonText: '全部清除', cancelButtonText: '取消' },
    )
  } catch {
    return  // 用户取消（二次确认未通过）
  }
  try {
    await clearAllConversations()  // 后端清 DB + 运行时 session + MemorySaver
    sessions.value = []
    currentSessionId.value = null
    ElMessage.success('已清除全部对话历史')
  } catch (e) {
    ElMessage.error(e.message || '清除失败')
  }
}

async function onSend(text) {
  // 未登录：暂存消息、弹登录框，登录成功后自动补发（onLoginSuccess）。
  if (!authed.value) {
    pendingText.value = text
    loginVisible.value = true
    return
  }

  // Auto-create session on first message
  if (!currentSession.value) {
    await newChat()
    if (!currentSession.value) return
  }

  const session = currentSession.value
  await ensureSessionMode(session, mode.value)
  if (session.mode !== mode.value) return

  // Add user message
  session.messages.push({ id: uid(), role: 'user', content: text, done: true, time: formatDateTime() })

  // Update title from first message: show a truncated placeholder immediately,
  // then replace it with an LLM-summarized title (best-effort; keep the
  // placeholder if summarization fails).
  if (session.messages.filter(m => m.role === 'user').length === 1) {
    session.title = text.slice(0, 24) + (text.length > 24 ? '…' : '')
    generateTitle(text)
      .then(t => {
        if (!t) return
        session.title = t
        // 持久化 LLM 标题（覆盖后端首消息截断的临时标题）。会话此时已由聊天流落库。
        updateConversationTitle(session.sessionId, t).catch(() => {})
      })
      .catch(() => {})
  }

  // Add empty assistant message
  const assistantMsg = {
    id: uid(),
    role: 'assistant',
    content: '',
    thinking: '',
    tools: [],
    plan: [],
    steps: [],
    phase: '',          // '' | 'planning' | 'summarizing'
    activeStep: -1,     // 1-based; -1 = none in progress
    stepStreams: [],    // [{ task, text, thinking, tools: [], status }] aligned with plan
    hitl: null,         // { id, prompt, options } while a human choice is pending
    done: false,
    time: formatDateTime(),
    startedAt: 0,       // ms epoch when the round started (set by startTicker)
    elapsed: 0,         // seconds, updated live then frozen on completion
  }
  session.messages.push(assistantMsg)
  // Get the reactive proxy Vue wraps around the pushed object;
  // the original plain-object reference bypasses reactivity tracking.
  const msg = session.messages[session.messages.length - 1]
  // Responder for human-in-the-loop questions; captures this session + the
  // pending request id. MessageBubble calls it when the user picks an option.
  msg.respondHitl = (value) => {
    if (!msg.hitl) return
    const { id } = msg.hitl
    msg.hitl = null
    respondHitl(session.sessionId, id, value).catch(e => ElMessage.error(`提交选择失败: ${e.message}`))
  }
  streaming.value = true
  startTicker(msg)  // begin counting immediately so the initial wait is timed too

  currentController.value = streamChat(session.sessionId, text, {
    onThinking: t => { msg.thinking += t },
    onText:     t => { msg.content += t },
    onHeartbeat: () => {},
    onToolStart: (name, input) => {
      const result = input ? `运行中...\n\n${input}` : '运行中...'
      const pending = [...msg.tools].reverse()
        .find(t => t.name === name && t.status === 'running' && t.result === result)
      if (pending) {
        pending.status = 'running'
        pending.result = result
      } else {
        msg.tools.push({ name, result, status: 'running' })
      }
    },
    onTool:     (name, result, sourceFavicons) => {
      const pendingIndex = msg.tools.findIndex(t => t.name === name && t.status === 'running')
      if (pendingIndex >= 0) {
        msg.tools[pendingIndex].result = result
        msg.tools[pendingIndex].sourceFavicons = sourceFavicons || []
        msg.tools[pendingIndex].status = 'success'
        for (let i = msg.tools.length - 1; i > pendingIndex; i--) {
          if (msg.tools[i].name === name && msg.tools[i].status === 'running') {
            msg.tools.splice(i, 1)
          }
        }
      } else {
        msg.tools.push({ name, result, sourceFavicons: sourceFavicons || [], status: 'success' })
      }
    },
    onPlan:     steps => {
      msg.plan = steps
      msg.stepStreams = steps.map(task => ({
        task,
        text: '',
        thinking: '',
        thinkingActive: false,
        tools: [],
        status: 'wait',
      }))
      msg.phase = ''        // planning phase ends once plan arrives
    },
    onStep:     (step, result) => { msg.steps.push({ step, result }) },
    onReplan:   steps => { msg.plan = steps },
    onPhase:    p => { msg.phase = p },
    onStepStart: (n, _total, task) => {
      msg.activeStep = n
      if (msg.stepStreams[n - 1]) {
        msg.stepStreams[n - 1].task = task
        msg.stepStreams[n - 1].status = 'process'
        msg.stepStreams[n - 1].thinkingActive = true
      }
    },
    onStepToken: (n, text) => {
      if (msg.stepStreams[n - 1]) {
        msg.stepStreams[n - 1].text += text
        msg.stepStreams[n - 1].thinkingActive = false
      }
    },
    onStepThinking: (n, text) => {
      if (msg.stepStreams[n - 1]) {
        msg.stepStreams[n - 1].thinking += text
        msg.stepStreams[n - 1].thinkingActive = true
      }
    },
    onStepTool: (n, name, result, toolCallId, sourceFavicons) => {
      const step = msg.stepStreams[n - 1]
      if (!step) return
      let pendingIndex = toolCallId
        ? step.tools.findIndex(t => t.id === toolCallId)
        : -1
      if (pendingIndex < 0) {
        pendingIndex = step.tools.findIndex(t => t.name === name && t.status === 'running')
      }
      if (pendingIndex >= 0) {
        if (toolCallId) step.tools[pendingIndex].id = toolCallId
        step.tools[pendingIndex].result = result
        step.tools[pendingIndex].sourceFavicons = sourceFavicons || []
        step.tools[pendingIndex].status = 'success'
        for (let i = step.tools.length - 1; i > pendingIndex; i--) {
          if ((toolCallId && step.tools[i].id === toolCallId)
            || (step.tools[i].name === name && step.tools[i].status === 'running')) {
            step.tools.splice(i, 1)
          }
        }
      } else {
        step.tools.push({ id: toolCallId, name, result, sourceFavicons: sourceFavicons || [], status: 'success' })
      }
    },
    onStepToolStart: (n, name, input, toolCallId) => {
      const step = msg.stepStreams[n - 1]
      if (!step) return
      const result = input || ''
      let pending = toolCallId
        ? [...step.tools].reverse().find(t => t.id === toolCallId)
        : null
      if (!pending) {
        pending = [...step.tools].reverse()
          .find(t => t.name === name && t.status === 'running' && t.result === result)
      }
      if (pending) {
        if (toolCallId && !pending.id) pending.id = toolCallId
        pending.status = 'running'
        pending.result = result
      } else {
        step.tools.push({ id: toolCallId, name, result, status: 'running' })
      }
    },
    onStepDone: n => {
      if (msg.stepStreams[n - 1]) {
        msg.stepStreams[n - 1].status = 'success'
        msg.stepStreams[n - 1].thinkingActive = false
      }
    },
    onStepFailed: (n, message) => {
      if (msg.stepStreams[n - 1]) {
        msg.stepStreams[n - 1].status = 'error'
        msg.stepStreams[n - 1].thinkingActive = false
        msg.stepStreams[n - 1].tools
          .filter(t => t.status === 'running')
          .forEach(t => { t.status = 'error' })
        msg.stepStreams[n - 1].text += `${msg.stepStreams[n - 1].text ? '\n' : ''}[失败: ${message}]`
      }
    },
    onHitl:     (id, prompt, options, preview) => { msg.hitl = { id, prompt, options, preview } },
    onLimit:    (reason, m) => {
      const tag = reason === 'cancelled' ? '已停止' : `已中止: ${reason}`
      msg.content += (msg.content ? '\n' : '') + `[${tag}${m ? ` — ${m}` : ''}]`
    },
    onDone:     () => {
      msg.done = true
      msg.phase = ''
      msg.activeStep = -1
      msg.hitl = null
      finalizeTiming(msg)
      streaming.value = false
      currentController.value = null
    },
    onError:    m => {
      msg.content = msg.content || `[错误: ${m}]`
      msg.done = true
      finalizeTiming(msg)
      streaming.value = false
      currentController.value = null
      ElMessage.error(m)
    },
  })
}

function onStop() {
  const session = currentSession.value
  if (session) cancelChat(session.sessionId)
  if (currentController.value) {
    currentController.value.abort()
    currentController.value = null
  }
  // Aborting the fetch makes streamChat swallow the AbortError WITHOUT firing
  // onDone/onError, so the streaming flag would otherwise stay true forever.
  // `streaming` is app-global, so a stuck `true` disables the input across all
  // chats (including newly created ones). Reset it here, and close out the
  // in-flight assistant message so the UI reflects the stop.
  streaming.value = false
  const msg = session?.messages?.[session.messages.length - 1]
  if (msg && msg.role === 'assistant' && !msg.done) {
    msg.content += (msg.content ? '\n' : '') + '[已停止]'
    msg.done = true
    msg.phase = ''
    msg.activeStep = -1
    msg.hitl = null
    finalizeTiming(msg)
  } else {
    stopTicker()
  }
}
</script>

<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body, #app { height: 100%; }
</style>

<style scoped>
.app {
  display: flex;
  height: 100vh;
  overflow: hidden;
  font-family: var(--font-body);
  color: var(--ink);
}
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
