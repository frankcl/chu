<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    width="720px"
    align-center
    append-to-body
    class="settings-dialog"
    @open="onOpen"
  >
    <template #header>
      <h2 class="settings-title">设置</h2>
    </template>

    <div class="settings-body">
      <!-- 左侧 menu -->
      <nav class="settings-menu">
        <div class="menu-items">
          <button
            v-for="m in menus"
            :key="m.key"
            class="menu-item"
            :class="{ active: active === m.key }"
            @click="switchTo(m.key)"
          >
            <component :is="m.icon" :size="16" :stroke="1.7" />
            <span>{{ m.label }}</span>
          </button>
        </div>
        <!-- 退出登录：菜单右下角小图标，与菜单项分隔以防误触 -->
        <div class="menu-footer">
          <el-tooltip content="退出登录" placement="top" :show-after="300">
            <button class="logout-icon" aria-label="退出登录" @click="onLogout">
              <IconLogout :size="17" :stroke="1.8" />
            </button>
          </el-tooltip>
        </div>
      </nav>

      <!-- 右侧内容 -->
      <section class="settings-content">
        <!-- 用户信息 -->
        <div v-if="active === 'profile'" class="pane">
          <div class="profile-head">
            <img v-if="user && user.avatar" :src="user.avatar" alt="" class="profile-avatar" />
            <span v-else class="profile-avatar profile-avatar--fallback">
              <IconUser :size="30" :stroke="1.6" />
            </span>
            <div class="profile-meta">
              <div class="profile-name">
                {{ user ? (user.name || user.username) : '未登录' }}
                <span v-if="user && user.super_admin" class="admin-badge">管理员</span>
              </div>
              <div v-if="user && user.username" class="profile-account">@{{ user.username }}</div>
            </div>
          </div>

          <dl v-if="infoRows.length" class="info-list">
            <div v-for="row in infoRows" :key="row.label" class="info-row">
              <dt class="info-label">
                <component :is="row.icon" :size="15" :stroke="1.7" />
                <span>{{ row.label }}</span>
              </dt>
              <dd class="info-value">{{ row.value }}</dd>
            </div>
          </dl>
        </div>

        <!-- 对话配置 -->
        <div v-else-if="active === 'chat'" class="pane">
          <div class="setting-item">
            <div class="setting-text">
              <div class="setting-name">展示思考过程</div>
              <div class="setting-desc">对话过程中显示模型的思考过程</div>
            </div>
            <el-switch v-model="displaySettings.showThinking" />
          </div>
          <div class="setting-item">
            <div class="setting-text">
              <div class="setting-name">展示工具调用</div>
              <div class="setting-desc">对话过程中显示工具调用信息</div>
            </div>
            <el-switch v-model="displaySettings.showTools" />
          </div>
        </div>

        <!-- Token 使用情况 -->
        <div v-else class="pane">
          <div v-if="loading" class="pane-state">加载中…</div>
          <template v-else>
            <div class="usage-filter">
              <el-date-picker
                v-model="dateRange"
                type="daterange"
                unlink-panels
                value-format="YYYY-MM-DD"
                range-separator="至"
                start-placeholder="开始日期"
                end-placeholder="结束日期"
                :clearable="false"
                size="small"
              />
            </div>

            <div class="stat-row">
              <div class="stat-card">
                <div class="stat-label">总量</div>
                <div class="stat-value">{{ fmt(rangeTotal.total_tokens) }}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label"><i class="dot dot--in"></i>输入</div>
                <div class="stat-value">{{ fmt(rangeTotal.input_tokens) }}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label"><i class="dot dot--out"></i>输出</div>
                <div class="stat-value">{{ fmt(rangeTotal.output_tokens) }}</div>
              </div>
            </div>

            <div class="chart-head">
              <span class="chart-title">按日用量</span>
              <span class="legend">
                <button
                  type="button"
                  class="legend-item"
                  :class="{ off: !showInput }"
                  @click="showInput = !showInput"
                ><i class="dot dot--in"></i>输入</button>
                <button
                  type="button"
                  class="legend-item"
                  :class="{ off: !showOutput }"
                  @click="showOutput = !showOutput"
                ><i class="dot dot--out"></i>输出</button>
              </span>
            </div>

            <div v-if="!hasData" class="pane-state">该时间范围暂无用量数据</div>
            <div v-else class="chart-scroll">
              <svg
                class="chart"
                :width="chart.width"
                :height="chart.height"
                :viewBox="`0 0 ${chart.width} ${chart.height}`"
              >
                <!-- 网格线 + y 轴刻度 -->
                <g class="grid">
                  <line
                    v-for="(g, i) in chart.gridlines"
                    :key="i"
                    :x1="chart.padL" :x2="chart.width - chart.padR"
                    :y1="g.y" :y2="g.y"
                  />
                  <text
                    v-for="(g, i) in chart.gridlines"
                    :key="'t' + i"
                    class="y-label"
                    :x="chart.padL - 6" :y="g.y + 3"
                  >{{ g.label }}</text>
                </g>
                <!-- 柱：输入(下) + 输出(上) 堆叠 -->
                <g v-for="b in chart.bars" :key="b.date">
                  <rect
                    v-if="showInput"
                    class="bar bar--in"
                    :x="b.x" :y="b.inY" :width="chart.barW" :height="b.inH" rx="2"
                  >
                    <title>{{ b.date }} · 输入 {{ fmt(b.input) }}</title>
                  </rect>
                  <rect
                    v-if="showOutput"
                    class="bar bar--out"
                    :x="b.x" :y="b.outY" :width="chart.barW" :height="b.outH" rx="2"
                  >
                    <title>{{ b.date }} · 输出 {{ fmt(b.output) }}</title>
                  </rect>
                  <text
                    v-if="b.showLabel"
                    class="x-label"
                    :x="b.x + chart.barW / 2"
                    :y="chart.height - 8"
                    text-anchor="middle"
                  >{{ b.short }}</text>
                </g>
                <!-- 基线 -->
                <line
                  class="axis"
                  :x1="chart.padL" :x2="chart.width - chart.padR"
                  :y1="chart.baseY" :y2="chart.baseY"
                />
              </svg>
            </div>
          </template>
        </div>
      </section>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  IconUser, IconLogout, IconChartBar, IconMessageCog,
  IconMail, IconPhone, IconBuilding, IconBriefcase, IconCategory, IconMapPin, IconCalendar,
} from '@tabler/icons-vue'
import { fetchTokenStats } from '../api/stats.js'
import { displaySettings } from '../composables/displaySettings.js'
import { formatDate } from '../utils/datetime.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  user: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'logout'])

const menus = [
  { key: 'profile', label: '用户信息', icon: IconUser },
  { key: 'chat', label: '对话配置', icon: IconMessageCog },
  { key: 'usage', label: 'Token用量', icon: IconChartBar },
]
const active = ref('profile')

const loading = ref(false)
const empty = { input_tokens: 0, output_tokens: 0, total_tokens: 0 }
const stats = ref({ total: { ...empty }, daily: [] })

// 时间范围筛选（单位：日），默认最近 7 天（含今天）；图例默认全选。
function lastNDays(n) {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - (n - 1))
  return [formatDate(start), formatDate(end)]
}
const dateRange = ref(lastNDays(7))
const showInput = ref(true)
const showOutput = ref(true)

function fmt(n) {
  return Number(n || 0).toLocaleString('zh-CN')
}

async function loadUsage() {
  loading.value = true
  try {
    stats.value = await fetchTokenStats()
  } catch (e) {
    stats.value = { total: { ...empty }, daily: [] }
    ElMessage.error(e.message || '加载用量失败')
  } finally {
    loading.value = false
  }
}

function switchTo(key) {
  active.value = key
  if (key === 'usage') loadUsage()
}

// 每次打开弹窗都回到用户信息页，避免展示上一次的用量；同时把用量筛选重置为默认。
function onOpen() {
  active.value = 'profile'
  dateRange.value = lastNDays(7)
  showInput.value = true
  showOutput.value = true
}

// 展示用的用户信息行：仅保留有值字段（判空后渲染）。
const infoRows = computed(() => {
  const u = props.user
  if (!u) return []
  const rows = [
    { label: '邮箱', value: u.email, icon: IconMail },
    { label: '手机', value: u.phone, icon: IconPhone },
    { label: '公司', value: u.company, icon: IconBuilding },
    { label: '职位', value: u.position, icon: IconBriefcase },
    { label: '行业', value: u.industry, icon: IconCategory },
    { label: '所在地', value: u.location, icon: IconMapPin },
    { label: '组织', value: u.tenant, icon: IconBuilding },
    {
      label: '注册时间',
      value: u.register_time ? new Date(u.register_time).toLocaleDateString('zh-CN') : null,
      icon: IconCalendar,
    },
  ]
  return rows.filter(r => r.value)
})

async function onLogout() {
  try {
    await ElMessageBox.confirm('确认退出登录？', '退出登录', {
      type: 'warning', confirmButtonText: '退出', cancelButtonText: '取消',
    })
  } catch {
    return  // 用户取消
  }
  emit('logout')
  emit('update:modelValue', false)
}

// 所选时间范围内的连续日序列（含零值日，保证时间轴连续）。
const rangeDaily = computed(() => {
  const [start, end] = dateRange.value || []
  if (!start || !end) return []
  const map = new Map((stats.value.daily || []).map(d => [d.date, d]))
  const out = []
  const cur = new Date(`${start}T00:00:00`)
  const last = new Date(`${end}T00:00:00`)
  // 上限保护，避免异常的超大范围拖垮渲染。
  for (let guard = 0; cur <= last && guard < 1100; guard++) {
    const date = formatDate(cur)
    const d = map.get(date)
    out.push({
      date,
      input_tokens: d?.input_tokens || 0,
      output_tokens: d?.output_tokens || 0,
      total_tokens: d?.total_tokens || 0,
    })
    cur.setDate(cur.getDate() + 1)
  }
  return out
})

// 范围内总计（统计卡展示所选范围，默认最近 7 天）。
const rangeTotal = computed(() => {
  const t = { ...empty }
  for (const d of rangeDaily.value) {
    t.input_tokens += d.input_tokens
    t.output_tokens += d.output_tokens
    t.total_tokens += d.total_tokens
  }
  return t
})

const hasData = computed(() => rangeDaily.value.some(d => d.input_tokens || d.output_tokens))

// ── 自研轻量 SVG 堆叠柱状图几何（依赖范围 + 图例选择）───────────
const chart = computed(() => {
  const daily = rangeDaily.value
  const inOn = showInput.value
  const outOn = showOutput.value
  const padL = 52, padR = 12, padT = 12, padB = 26
  const plotH = 190
  const barW = 22, gap = 12
  const height = padT + plotH + padB
  const baseY = padT + plotH
  const width = Math.max(padL + padR + daily.length * (barW + gap), 360)

  const maxVal = Math.max(
    1,
    ...daily.map(d => (inOn ? d.input_tokens : 0) + (outOn ? d.output_tokens : 0)),
  )
  // y 轴分 4 段刻度
  const gridlines = []
  for (let i = 0; i <= 4; i++) {
    const v = (maxVal / 4) * i
    gridlines.push({ y: baseY - (plotH * i) / 4, label: fmtShort(v) })
  }

  // 日期标签过密时稀疏显示
  const step = Math.ceil(daily.length / 12)
  const bars = daily.map((d, i) => {
    const inp = d.input_tokens
    const outp = d.output_tokens
    const inH = inOn ? (inp / maxVal) * plotH : 0
    const outH = outOn ? (outp / maxVal) * plotH : 0
    const x = padL + i * (barW + gap)
    return {
      date: d.date,
      short: d.date.slice(5),      // MM-DD
      input: inp,
      output: outp,
      x,
      inH,
      inY: baseY - inH,
      outH,
      outY: baseY - inH - outH,
      showLabel: i % step === 0,
    }
  })

  return { width, height, padL, padR, baseY, barW, gridlines, bars }
})

function fmtShort(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k'
  return String(Math.round(n))
}
</script>

<style scoped>
.settings-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 500;
  color: var(--ink);
}
.settings-body {
  display: flex;
  gap: 18px;
  min-height: 340px;
}

/* 左侧 menu */
.settings-menu {
  flex-shrink: 0;
  width: 176px;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--line);
  padding-right: 14px;
}
.menu-items {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
/* 退出登录区：顶到底部、右对齐，与菜单项以分隔线 + 间距拉开，防止误触 */
.menu-footer {
  margin-top: auto;
  display: flex;
  justify-content: flex-end;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.logout-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: transparent;
  color: var(--ink-faint);
  cursor: pointer;
  transition: border-color 0.16s, color 0.16s, background 0.16s;
}
.logout-icon:hover {
  border-color: var(--el-color-danger);
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 11px;
  border-radius: 9px;
  border: none;
  background: transparent;
  color: var(--ink-soft);
  font-family: var(--font-body);
  font-size: 13.5px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.menu-item:hover {
  background: rgba(193, 95, 60, 0.07);
  color: var(--ink);
}
.menu-item.active {
  background: var(--clay-tint);
  color: var(--clay-deep);
  font-weight: 500;
}

/* 右侧内容 */
.settings-content {
  flex: 1;
  min-width: 0;
}
.pane {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.pane-state {
  color: var(--ink-faint);
  font-size: 13.5px;
  padding: 40px 0;
  text-align: center;
}

/* 用户信息页 */
.profile-head {
  display: flex;
  align-items: center;
  gap: 14px;
}
.profile-avatar {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  object-fit: cover;
  background: #fff;
  box-shadow: 0 1px 3px rgba(60, 56, 50, 0.12);
}
.profile-avatar--fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--clay-tint);
  color: var(--clay-deep);
  border: 1px solid #ecd8cd;
}
.profile-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 17px;
  font-weight: 600;
  color: var(--ink);
}
.admin-badge {
  font-size: 11px;
  font-weight: 500;
  padding: 1px 8px;
  border-radius: 20px;
  background: var(--clay-tint);
  color: var(--clay-deep);
  border: 1px solid #ecd8cd;
}
.profile-account {
  font-size: 13px;
  color: var(--ink-soft);
  margin-top: 3px;
}

/* 用户信息列表 */
.info-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.info-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 14px;
  border-radius: 10px;
  transition: background 0.15s;
}
.info-row:hover {
  background: var(--surface-warm);
}
.info-label {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-shrink: 0;
  width: 96px;
  font-size: 13px;
  color: var(--ink-soft);
}
.info-value {
  flex: 1;
  min-width: 0;
  font-size: 13.5px;
  color: var(--ink);
  word-break: break-word;
}

/* 对话配置 */
.setting-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border-radius: 11px;
  background: var(--surface-warm);
  border: 1px solid var(--line);
}
.setting-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
}
.setting-desc {
  margin-top: 3px;
  font-size: 12px;
  color: var(--ink-soft);
}

/* 用量：时间范围筛选 */
.usage-filter {
  display: flex;
}
.usage-filter :deep(.el-date-editor) {
  width: 100%;
  max-width: 320px;
}

/* 用量统计卡 */
.stat-row {
  display: flex;
  gap: 12px;
}
.stat-card {
  flex: 1;
  padding: 14px 16px;
  border-radius: 11px;
  background: var(--surface-warm);
  border: 1px solid var(--line);
}
.stat-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ink-soft);
}
.stat-value {
  margin-top: 6px;
  font-size: 22px;
  font-weight: 600;
  font-family: var(--font-display);
  color: var(--ink);
}
.dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 2px;
  flex-shrink: 0;
}
.dot--in { background: #4f7a6f; }
.dot--out { background: var(--clay); }

/* 图表 */
.chart-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.chart-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.legend {
  display: flex;
  gap: 14px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ink-soft);
  border: none;
  background: none;
  padding: 0;
  cursor: pointer;
  font-family: var(--font-body);
  transition: opacity 0.15s;
}
.legend-item.off {
  opacity: 0.4;
}
.chart-scroll {
  overflow-x: auto;
  padding-bottom: 4px;
}
.chart .grid line {
  stroke: var(--line-soft);
  stroke-width: 1;
}
.chart .axis {
  stroke: var(--line);
  stroke-width: 1;
}
.chart .y-label {
  fill: var(--ink-faint);
  font-size: 10px;
  text-anchor: end;
}
.chart .x-label {
  fill: var(--ink-faint);
  font-size: 10px;
}
.chart .bar--in { fill: #4f7a6f; }
.chart .bar--out { fill: var(--clay); }
</style>
