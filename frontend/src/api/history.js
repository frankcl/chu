/**
 * 对话历史接口。历史分用户存储于后端（MySQL），与「对话记忆」（LangGraph
 * checkpointer）区分：这里拿到的是全量可浏览记录，用于侧边栏列表与查看/继续。
 */
import { fetchWithAuth } from './chat.js'
import { formatDateTime } from '../utils/datetime.js'

/** 当前用户的对话列表（按 update_time 倒序）：[{ id, title, update_time }]。 */
export async function listConversations() {
  const res = await fetchWithAuth(headers => fetch('/api/conversations', { headers }))
  if (!res.ok) throw new Error(`加载历史失败: ${res.status}`)
  return (await res.json()).conversations
}

/** 某场对话的全部消息（全量保真，按 seq 升序）：[{ seq, role, type, content, extra }]。 */
export async function getConversation(id) {
  const res = await fetchWithAuth(headers => fetch(`/api/conversations/${id}`, { headers }))
  if (!res.ok) throw new Error(`加载对话失败: ${res.status}`)
  return (await res.json()).messages
}

/** 持久化 LLM 概括出的会话标题。 */
export async function updateConversationTitle(id, title) {
  await fetchWithAuth(headers => fetch(`/api/conversations/${id}/title`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify({ title }),
  }))
}

/** 置顶 / 取消置顶一场对话。 */
export async function setConversationTop(id, top) {
  await fetchWithAuth(headers => fetch(`/api/conversations/${id}/top`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify({ top }),
  }))
}

/** 删除一场对话历史。 */
export async function deleteConversation(id) {
  await fetchWithAuth(headers => fetch(`/api/conversations/${id}`, { method: 'DELETE', headers }))
}

/** 清除当前用户的全部对话历史（DB + 运行时记忆）。 */
export async function clearAllConversations() {
  const res = await fetchWithAuth(headers => fetch('/api/conversations', { method: 'DELETE', headers }))
  if (!res.ok) throw new Error(`清除失败: ${res.status}`)
  return res.json()
}

/**
 * 把后端 typed 消息（role × type）重组为前端 MessageBubble 所需的消息数组。
 * 规则：遇到 role=user 开一条用户消息；其后的 assistant 各类型（text/thinking/
 * tool/plan/step）归入同一条 AI 消息，按 type 填到对应字段。
 */
export function regroupMessages(rows) {
  const messages = []
  let ai = null
  const uid = () => Math.random().toString(36).slice(2, 10)

  const newAi = () => ({
    id: uid(), role: 'assistant', content: '', thinking: '',
    tools: [], plan: [], steps: [], stepStreams: [], phase: '', activeStep: -1,
    hitl: null, done: true, time: '',
  })

  for (const m of rows) {
    if (m.role === 'user') {
      messages.push({
        id: uid(), role: 'user', content: m.content, done: true,
        time: formatDateTime(m.create_time),
      })
      ai = null
      continue
    }
    // assistant 各类型：首行时间即该轮起始时间
    if (!ai) { ai = newAi(); ai.time = formatDateTime(m.create_time); messages.push(ai) }
    const extra = m.extra || {}
    if (m.type === 'text') ai.content += m.content || ''
    else if (m.type === 'thinking') ai.thinking += m.content || ''
    else if (m.type === 'tool') ai.tools.push({ name: extra.name, result: m.content || '' })
    else if (m.type === 'plan') {
      ai.plan = extra.steps || []
      ai.stepStreams = (extra.steps || []).map(task => ({ task, text: '', thinking: '', tools: [] }))
    } else if (m.type === 'step') {
      const i = (extra.step_num || 1) - 1
      ai.stepStreams[i] = {
        task: extra.task || '', text: m.content || '',
        thinking: extra.thinking || '', tools: extra.tools || [],
      }
    }
  }
  return messages
}
