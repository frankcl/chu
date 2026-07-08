<template>
  <div class="sidebar">
    <div class="sidebar-header">
      <img :src="chuLogo" alt="Chu" class="logo-avatar" />
      <div class="logo-text">
        <span class="logo">Chu</span>
        <span class="logo-sub">陪你一起成长</span>
      </div>
    </div>

    <button class="new-chat-btn" @click="emit('new-chat')">
      <IconPencilPlus :size="17" :stroke="1.8" />
      <span>新建对话</span>
    </button>

    <div class="section-label section-label--history">
      <span class="section-label-text">
        <IconHistory :size="13" :stroke="1.8" /> 历史对话
      </span>
      <button
        v-if="authed && sessions.length"
        class="clear-history-btn"
        aria-label="清除所有历史"
        title="清除所有对话历史"
        @click="emit('clear-all')"
      >
        <IconTrash :size="14" :stroke="1.7" />
      </button>
    </div>
    <div class="session-list">
      <template v-for="g in groups" :key="g.key">
        <div class="session-group-label">{{ g.label }}</div>
        <div
          v-for="s in g.items"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === currentId }"
          @click="emit('switch', s.id)"
        >
          <IconMessage2 :size="15" :stroke="1.7" class="session-icon" />
          <div class="session-main">
            <span class="session-title">{{ s.title }}</span>
            <span v-if="s.date" class="session-date">{{ s.date }}</span>
          </div>
          <el-tooltip :content="s.top ? '取消置顶' : '置顶'" placement="top" :show-after="300">
            <button
              class="pin-btn"
              :class="{ pinned: s.top }"
              :aria-label="s.top ? '取消置顶' : '置顶'"
              @click.stop="emit('toggle-top', s.id, !s.top)"
            >
              <IconPinnedOff v-if="s.top" :size="14" :stroke="1.7" />
              <IconPin v-else :size="14" :stroke="1.7" />
            </button>
          </el-tooltip>
          <el-tooltip content="删除对话" placement="top" :show-after="300">
            <button class="del-btn" aria-label="删除对话" @click.stop="emit('delete', s.id)">
              <IconTrash :size="15" :stroke="1.7" />
            </button>
          </el-tooltip>
        </div>
      </template>
    </div>

    <!-- 账户框：登录 / 用户信息 / 配置入口 合并为一个显示框 -->
    <button
      v-if="authed"
      class="account-box"
      title="账户与配置"
      @click="emit('open-settings')"
    >
      <img v-if="user && user.avatar" :src="user.avatar" alt="" class="user-avatar" />
      <span v-else class="user-avatar user-avatar--fallback">
        <IconUser :size="17" :stroke="1.7" />
      </span>
      <span class="user-name">{{ user ? (user.name || user.username) : '账户' }}</span>
      <IconSettings :size="16" :stroke="1.7" class="account-cog" />
    </button>
    <button v-else class="account-box account-box--login" @click="emit('login')">
      <span class="user-avatar user-avatar--fallback">
        <IconLogin :size="16" :stroke="1.8" />
      </span>
      <span class="user-name">登录</span>
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import {
  IconPencilPlus, IconMessage2, IconTrash, IconHistory,
  IconLogin, IconUser, IconSettings, IconPin, IconPinnedOff,
} from '@tabler/icons-vue'
import chuLogo from '../assets/chu-icon.png'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  currentId: { type: String, default: null },
  authed: { type: Boolean, default: false },
  user: { type: Object, default: null },
})
const emit = defineEmits(['new-chat', 'switch', 'delete', 'toggle-top', 'login', 'logout', 'clear-all', 'open-settings'])

// 历史对话分组：置顶优先，其余按最后活跃时间落入 最近7天 / 最近30天 / 大于30天；组内时间倒序。
const groups = computed(() => {
  const DAY = 86400000
  const now = Date.now()
  const pinned = [], d7 = [], d30 = [], older = []
  for (const s of props.sessions) {
    if (s.top) { pinned.push(s); continue }
    const age = now - (s.updateTime || now)
    if (age <= 7 * DAY) d7.push(s)
    else if (age <= 30 * DAY) d30.push(s)
    else older.push(s)
  }
  const byTimeDesc = (a, b) => (b.updateTime || 0) - (a.updateTime || 0)
  for (const arr of [pinned, d7, d30, older]) arr.sort(byTimeDesc)
  return [
    { key: 'top', label: '置顶', items: pinned },
    { key: 'd7', label: '最近 7 天', items: d7 },
    { key: 'd30', label: '最近 30 天', items: d30 },
    { key: 'older', label: '大于 30 天', items: older },
  ].filter(g => g.items.length)
})
</script>

<style scoped>
.sidebar {
  width: 248px;
  flex-shrink: 0;
  background: var(--paper-deep);
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  padding: 18px 12px;
  gap: 14px;
}
.sidebar-header {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 2px 6px 14px;
  border-bottom: 1px solid var(--line);
}
.logo-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  object-fit: cover;
  background: #fff;
  box-shadow: 0 1px 3px rgba(60, 56, 50, 0.12);
}
.logo-text {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}
.logo {
  font-family: var(--font-display);
  font-size: 23px;
  font-weight: 500;
  letter-spacing: 0.5px;
  color: var(--ink);
}
.logo-sub {
  font-size: 11px;
  color: var(--ink-soft);
  margin-top: 1px;
}
.new-chat-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  width: 100%;
  padding: 9px 12px;
  border-radius: 11px;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  font-family: var(--font-body);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: border-color 0.16s, color 0.16s, background 0.16s, box-shadow 0.16s;
}
.new-chat-btn:hover {
  border-color: var(--clay);
  color: var(--clay);
  box-shadow: 0 2px 8px rgba(193, 95, 60, 0.1);
}
.section-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-faint);
  padding: 0 6px;
  text-transform: uppercase;
  letter-spacing: 0.7px;
}
.section-label--history {
  justify-content: space-between;
}
.section-label--history .section-label-text {
  font-size: 14px;
  letter-spacing: 0.3px;
}
.session-group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-faint);
  padding: 6px 6px 2px;
  letter-spacing: 0.5px;
}
.section-label-text {
  display: flex;
  align-items: center;
  gap: 6px;
}
.clear-history-btn {
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  color: var(--ink-faint);
  cursor: pointer;
  padding: 2px;
  border-radius: 6px;
  transition: color 0.15s, background 0.15s;
}
.clear-history-btn:hover {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}
.session-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0 -4px;
  padding: 0 4px;
}
.session-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 10px;
  border-radius: 9px;
  cursor: pointer;
  font-size: 13px;
  color: var(--ink-soft);
  transition: background 0.15s, color 0.15s;
}
.session-icon {
  flex-shrink: 0;
  opacity: 0.7;
}
.session-item:hover {
  background: rgba(193, 95, 60, 0.07);
  color: var(--ink);
}
.session-item.active {
  background: var(--surface);
  color: var(--ink);
  font-weight: 500;
  box-shadow: 0 1px 3px rgba(60, 56, 50, 0.07);
}
.session-item.active .session-icon {
  color: var(--clay);
  opacity: 1;
}
/* 选中项左侧陶橙竖条点缀 */
.session-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 7px;
  bottom: 7px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--clay);
}
.session-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.session-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-date {
  font-size: 11px;
  color: var(--ink-faint);
}
.del-btn {
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  color: var(--ink-faint);
  cursor: pointer;
  padding: 2px;
  border-radius: 6px;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s, background 0.15s;
}
.session-item:hover .del-btn { opacity: 1; }
.del-btn:hover {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}
.pin-btn {
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  color: var(--ink-faint);
  cursor: pointer;
  padding: 2px;
  border-radius: 6px;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s, background 0.15s;
}
.session-item:hover .pin-btn { opacity: 1; }
.pin-btn:hover {
  color: var(--clay);
  background: var(--clay-tint);
}
.pin-btn.pinned {
  color: var(--clay);
}
.account-box {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border-radius: 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  cursor: pointer;
  text-align: left;
  font-family: var(--font-body);
  transition: border-color 0.16s, color 0.16s, box-shadow 0.16s;
}
.account-box:hover {
  border-color: var(--clay);
  box-shadow: 0 2px 8px rgba(193, 95, 60, 0.1);
}
.account-box:hover .account-cog {
  color: var(--clay);
}
.account-cog {
  flex-shrink: 0;
  color: var(--ink-faint);
  transition: color 0.16s;
}
.account-box--login .user-name {
  font-weight: 500;
}
.account-box--login:hover .user-name {
  color: var(--clay);
}
.user-avatar {
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  object-fit: cover;
  background: #fff;
  box-shadow: 0 1px 3px rgba(60, 56, 50, 0.12);
}
.user-avatar--fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--clay-tint);
  color: var(--clay-deep);
  border: 1px solid #ecd8cd;
}
.user-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13.5px;
  font-weight: 500;
  color: var(--ink);
}
</style>
