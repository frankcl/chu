/**
 * 用量统计接口。数据源为后端按用户累计的每场对话 token 消耗
 * （见 server.py /api/stats/tokens），用于配置弹窗的「Token 使用情况」页。
 */
import { fetchWithAuth } from './chat.js'

/**
 * 当前用户的 token 使用统计。
 * 返回：{ total: { input_tokens, output_tokens, total_tokens },
 *        daily: [{ date: 'YYYY-MM-DD', input_tokens, output_tokens, total_tokens }] }
 */
export async function fetchTokenStats() {
  const res = await fetchWithAuth(headers => fetch('/api/stats/tokens', { headers }))
  if (!res.ok) throw new Error(`加载用量失败: ${res.status}`)
  return res.json()
}
