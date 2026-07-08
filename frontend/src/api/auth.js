/**
 * Hylian 密码登录接入（shield cookie/session 模式）。
 *
 * 流程：申请验证码（后端代理 hylian /api/captcha/apply）→ 用户填写账号密码+验证码
 * → 提交后端 /api/auth/login（后端转发 hylian passwordLogin，把 token 种进服务端
 * shield 会话）→ 浏览器仅持有 httpOnly 的 sid cookie（HYLIAN_SESSION），之后所有
 * 受保护请求靠该 cookie 鉴权。前端不再持有/传递 token，token 由 shield 服务端自动续期。
 */

/** 申请验证码，返回验证码明文（由 UI 绘制成图片）。同一 session 的 JSESSIONID
 *  由后端通过 cookie 维持，登录时自动复用。 */
export async function applyCaptcha() {
  const res = await fetch('/api/auth/captcha', { credentials: 'include' })
  if (!res.ok) throw new Error(`验证码申请失败: ${res.status}`)
  return (await res.json()).captcha
}

/** 密码登录。成功后服务端已种好 shield 会话（sid cookie），返回 true；失败抛出带后端提示的错误。 */
export async function passwordLogin(username, password, captcha) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ username, password, captcha }),
  })
  if (!res.ok) {
    let msg = `登录失败: ${res.status}`
    try { msg = (await res.json()).detail || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return true
}

/** 拉取当前登录用户信息。未登录/过期时抛错（shield 会 303→applyCode，用 redirect:manual
 *  拦成 opaqueredirect 检测；或 401）。
 *  返回：{ username, name, avatar, email, phone, company, position, industry,
 *         location, tenant, super_admin, register_time }（后若干项可能为 null）。 */
export async function fetchMe() {
  const res = await fetch('/api/auth/me', { credentials: 'include', redirect: 'manual' })
  if (res.type === 'opaqueredirect' || res.status === 401) {
    throw new Error('未登录或登录已过期')
  }
  if (!res.ok) throw new Error(`获取用户信息失败: ${res.status}`)
  return res.json()
}

/** 登出（两步）：
 *  1) POST /api/auth/logout —— 后端清本地 shield 会话与本域 cookie，返回 hylian logout URL；
 *  2) 浏览器带凭证命中 hylian logout —— 带上 .manong.xin 的 TICKET cookie，由 hylian 清掉
 *     自身域下的 TICKET/TOKEN cookie 及服务端 ticket/token（hylian 全局 CORS 回显 Origin
 *     且允许凭证，GET 简单请求无预检）。两步均 best-effort，失败不阻断前端回到登录态。 */
export async function logout() {
  let logoutUrl = null
  try {
    const res = await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
    if (res.ok) logoutUrl = (await res.json()).logout_url
  } catch {
    // 后端清理失败也继续尝试 hylian 登出并清前端态。
  }
  if (logoutUrl) {
    try {
      await fetch(logoutUrl, { credentials: 'include' })
    } catch {
      // best-effort：hylian 登出失败不阻断前端回到登录。
    }
  }
}

// 鉴权失效时的处理回调，由 App 注册（弹登录框，不刷新页面）。
let _onAuthRequired = null

/** 注册鉴权失效处理器（如：清登录态并弹出登录框）。 */
export function setAuthRequiredHandler(fn) {
  _onAuthRequired = fn
}

/** 不可恢复的鉴权失败（对话/请求中被 shield 拦截）：交给已注册的处理器（弹登录框），
 *  不整页刷新，保留当前会话与页面状态。 */
export function forceRelogin() {
  _onAuthRequired?.()
}
