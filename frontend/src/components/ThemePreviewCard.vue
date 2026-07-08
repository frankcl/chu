<template>
  <button
    type="button"
    class="tpc"
    :class="{ chosen, disabled }"
    :disabled="disabled"
    :title="theme.label"
    @click="$emit('select', theme.name)"
  >
    <!-- Mini slide mockup, drawn from the theme's real palette -->
    <div class="slide" :style="slideStyle">
      <div class="band" :style="bandStyle">
        <span class="band-title" :style="{ color: titleColor }">{{ theme.sample.title }}</span>
      </div>
      <div class="body">
        <p
          v-for="(line, i) in theme.sample.body"
          :key="i"
          class="line"
          :style="{ color: textColor }"
        >{{ line }}</p>
      </div>
      <div class="footer" :style="footerStyle">
        <span class="page" :style="{ color: footerTextColor }">1</span>
      </div>
    </div>
    <span class="label">
      {{ theme.label }}
      <IconCheck v-if="chosen" :size="13" :stroke="2.2" class="tick" />
    </span>
  </button>
</template>

<script setup>
import { computed } from 'vue'
import { IconCheck } from '@tabler/icons-vue'

const props = defineProps({
  theme: { type: Object, required: true },     // { name, label, colors{...}, sample{title, body[]} }
  chosen: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
defineEmits(['select'])

// Hex (no '#') -> CSS color; null/empty falls back to the provided default.
const hex = (v, fallback) => (v ? `#${v}` : fallback)
const c = computed(() => props.theme.colors || {})

// Background: gradient when bg2 is set, else solid; `default` (no bg) → plain white.
const slideStyle = computed(() => {
  const bg = c.value.background
  if (!bg) return { background: '#ffffff' }
  return c.value.bg2
    ? { background: `linear-gradient(160deg, #${bg}, #${c.value.bg2})` }
    : { background: `#${bg}` }
})
// Band: filled bar when the theme has one; `default` → no band, just a baseline rule.
const bandStyle = computed(() =>
  c.value.band
    ? { background: `#${c.value.band}` }
    : { background: 'transparent', borderBottom: '1px solid #e5e5e5' }
)
const titleColor = computed(() => hex(c.value.band_text || c.value.title, '#222'))
const textColor = computed(() => hex(c.value.text, '#444'))
const footerStyle = computed(() =>
  c.value.footer ? { background: `#${c.value.footer}` } : { background: 'transparent' }
)
const footerTextColor = computed(() =>
  c.value.footer ? hex(c.value.band_text, '#fff') : '#bbb'
)
</script>

<style scoped>
.tpc {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.12s;
}
.tpc:hover:not(.disabled) {
  border-color: var(--clay);
  box-shadow: 0 3px 12px rgba(193, 95, 60, 0.14);
  transform: translateY(-1px);
}
.tpc.chosen {
  border-color: var(--clay);
  box-shadow: 0 0 0 2px var(--clay-tint);
}
.tpc.disabled:not(.chosen) {
  cursor: default;
  opacity: 0.5;
}
/* 16:9 mini slide */
.slide {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  border-radius: 7px;
  overflow: hidden;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.06);
  display: flex;
  flex-direction: column;
}
.band {
  height: 30%;
  display: flex;
  align-items: center;
  padding: 0 8%;
}
.band-title {
  font-size: 11px;
  font-weight: 700;
  line-height: 1.1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.body {
  flex: 1;
  padding: 7% 8% 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.line {
  font-size: 8.5px;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.footer {
  height: 12%;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 0 8%;
}
.page {
  font-size: 7px;
  font-weight: 600;
}
.label {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  font-size: 12px;
  color: var(--ink-soft);
  padding-bottom: 2px;
}
.tpc.chosen .label {
  color: var(--clay-deep);
  font-weight: 600;
}
.tick {
  color: var(--clay);
}
</style>
