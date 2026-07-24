import { forceRelogin } from './auth.js'

/**
 * shield cookie/session 模式：鉴权靠 httpOnly sid cookie（同源经 Vite 代理自动携带），
 * 不再带 Authorization 头、不做 token 续期（shield 服务端自动续期）。
 * `doFetch()` 必须真正发起一次带 credentials:'include'、redirect:'manual' 的 fetch；
 * 命中 shield 的未登录跳转（opaqueredirect）或 401 时，弹登录框并抛错。
 */
export async function fetchWithAuth(doFetch) {
  const res = await doFetch()
  if (res.type === 'opaqueredirect' || res.status === 401) {
    forceRelogin()
    throw new Error('未登录或登录已过期')
  }
  return res
}

export async function createSession(mode, harnessOverrides = {}) {
  const res = await fetchWithAuth(() => fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    redirect: 'manual',
    body: JSON.stringify({ mode, ...harnessOverrides }),
  }))
  if (!res.ok) throw new Error(`Failed to create session: ${res.status}`)
  return (await res.json()).session_id
}

export async function deleteSession(sessionId) {
  await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE', credentials: 'include' })
}

/** Fetch PPT template preview palettes (colors + sample text), memoized. */
let _pptThemesCache = null
export async function getPptThemes() {
  if (_pptThemesCache) return _pptThemesCache
  const res = await fetch('/api/ppt/themes', { credentials: 'include' })
  if (!res.ok) throw new Error(`Failed to load themes: ${res.status}`)
  _pptThemesCache = (await res.json()).themes
  return _pptThemesCache
}

/** Ask the backend LLM to summarize a message into a short session title. */
export async function generateTitle(message) {
  const res = await fetchWithAuth(() => fetch('/api/title', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    redirect: 'manual',
    body: JSON.stringify({ message }),
  }))
  if (!res.ok) throw new Error(`Failed to generate title: ${res.status}`)
  return (await res.json()).title
}

export async function cancelChat(sessionId) {
  try {
    await fetch(`/api/chat/${sessionId}/cancel`, { method: 'POST', credentials: 'include' })
  } catch {
    // best-effort; the AbortController on the stream is the primary stop signal
  }
}

/**
 * Deliver the user's answer to a pending human-in-the-loop (HITL) question.
 * The original streamChat SSE stays open and resumes emitting after this POST,
 * so there is no new stream to open here.
 */
export async function respondHitl(sessionId, id, value) {
  const res = await fetch(`/api/chat/${sessionId}/respond`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ id, value }),
  })
  if (!res.ok) throw new Error(`Failed to respond: ${res.status}`)
  return res.json()
}

/**
 * Stream a chat message.
 * callbacks: { onThinking, onText, onTool, onToolStart, onPlan, onStep, onReplan, onHitl, onDone, onError }
 * Returns an AbortController so the caller can cancel.
 */
export function streamChat(sessionId, message, callbacks) {
  const controller = new AbortController()
  let activeSessionId = sessionId

  const openStream = () => fetch(`/api/chat/${activeSessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    redirect: 'manual',
    body: JSON.stringify({ message }),
    signal: controller.signal,
  })

  ;(async () => {
    try {
      let res = await openStream()
      if (res.type === 'opaqueredirect' || res.status === 401) {
        // 未登录/会话过期：shield 拦截，弹登录框。
        forceRelogin()
        callbacks.onError?.('未登录或登录已过期')
        return
      }
      // Runtime memory has a sliding TTL while persistent history does not.
      // Recreate the runtime binding and retry exactly once; the 404 happens
      // before the user message is persisted, so this cannot duplicate a turn.
      if (res.status === 404 && callbacks.recoverSession) {
        activeSessionId = await callbacks.recoverSession()
        res = await openStream()
      }
      if (!res.ok) {
        const err = await res.text()
        callbacks.onError?.(err)
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE lines: "data: {...}\n\n"
        const lines = buffer.split('\n')
        buffer = lines.pop() // keep incomplete last line

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (!raw) continue
          let event
          try {
            event = JSON.parse(raw)
          } catch {
            continue
          }

          switch (event.type) {
            case 'thinking':   callbacks.onThinking?.(event.content); break
            case 'text':       callbacks.onText?.(event.content); break
            case 'heartbeat':  callbacks.onHeartbeat?.(); break
            case 'tool_start': callbacks.onToolStart?.(event.name, event.input); break
            case 'tool':       callbacks.onTool?.(event.name, event.result, event.source_favicons); break
            case 'plan':       callbacks.onPlan?.(event.steps); break
            case 'step':       callbacks.onStep?.(event.step, event.result); break
            case 'replan':     callbacks.onReplan?.(event.steps); break
            case 'phase':      callbacks.onPhase?.(event.phase); break
            case 'step_start': callbacks.onStepStart?.(event.step_num, event.total, event.task); break
            case 'step_token':    callbacks.onStepToken?.(event.step_num, event.text); break
            case 'step_thinking': callbacks.onStepThinking?.(event.step_num, event.text); break
            case 'step_tool_start': callbacks.onStepToolStart?.(event.step_num, event.name, event.input, event.tool_call_id); break
            case 'step_tool':     callbacks.onStepTool?.(event.step_num, event.name, event.result, event.tool_call_id, event.source_favicons); break
            case 'step_done':     callbacks.onStepDone?.(event.step_num); break
            case 'step_failed':   callbacks.onStepFailed?.(event.step_num, event.message); break
            case 'hitl':       callbacks.onHitl?.(event.id, event.prompt, event.options, event.preview); break
            case 'limit':      callbacks.onLimit?.(event.reason, event.message); break
            case 'done':       callbacks.onDone?.(); return
            case 'error':      callbacks.onError?.(event.message); return
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') callbacks.onError?.(err.message)
    }
  })()

  return controller
}
