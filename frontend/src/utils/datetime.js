/** 时间格式化工具：统一消息与对话历史的时间展示。 */

function pad(n) {
  return String(n).padStart(2, '0')
}

function toDate(v) {
  return v instanceof Date ? v : new Date(v)
}

/** 年月日时分：YYYY-MM-DD HH:MM。默认取当前时间。 */
export function formatDateTime(v = new Date()) {
  const d = toDate(v)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 年月日：YYYY-MM-DD。 */
export function formatDate(v) {
  const d = toDate(v)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
