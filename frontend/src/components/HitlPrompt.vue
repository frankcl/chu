<template>
  <div class="hitl">
    <div class="hitl-head">
      <IconHandStop :size="15" :stroke="1.7" />
      <span class="hitl-q">{{ prompt }}</span>
    </div>

    <!-- Rich preview cards (e.g. PPT template themes) -->
    <div v-if="showThemePreview" class="hitl-previews">
      <ThemePreviewCard
        v-for="t in previewThemes"
        :key="t.name"
        :theme="t"
        :chosen="chosen === t.name"
        :disabled="answered"
        @select="pick"
      />
    </div>

    <!-- Plain option buttons (default) -->
    <div v-else class="hitl-options">
      <button
        v-for="opt in options"
        :key="opt"
        type="button"
        class="hitl-opt"
        :class="{ chosen: chosen === opt }"
        :disabled="answered"
        @click="pick(opt)"
      >
        {{ opt }}
      </button>
    </div>

    <p v-if="answered" class="hitl-done">已选择：{{ chosenLabel }}</p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { IconHandStop } from '@tabler/icons-vue'
import ThemePreviewCard from './ThemePreviewCard.vue'
import { getPptThemes } from '../api/chat.js'

const props = defineProps({
  prompt: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  preview: { type: String, default: null },   // e.g. 'ppt-theme'
})
const emit = defineEmits(['choose'])

// Latch on first click so a question can't be answered twice.
const answered = ref(false)
const chosen = ref(null)

function pick(opt) {
  if (answered.value) return
  answered.value = true
  chosen.value = opt
  emit('choose', opt)
}

// ── PPT theme previews ────────────────────────────────────────────────────────
const allThemes = ref([])
// Only the offered options that are known PPT themes, in the prompt's order.
const previewThemes = computed(() => {
  const byName = new Map(allThemes.value.map(t => [t.name, t]))
  return props.options.map(name => byName.get(name)).filter(Boolean)
})
// Show preview cards when the backend hints `ppt-theme`, OR (robust fallback, in
// case the model omitted the hint) when every option is a known theme name.
const showThemePreview = computed(() =>
  previewThemes.value.length > 0 &&
  (props.preview === 'ppt-theme' || previewThemes.value.length === props.options.length)
)
const chosenLabel = computed(() => {
  const t = allThemes.value.find(x => x.name === chosen.value)
  return t ? t.label : chosen.value
})

// Load the palettes once (memoized in chat.js) so we can both render the mockups
// and detect theme-name options; failure degrades silently to plain buttons.
onMounted(async () => {
  try {
    allThemes.value = await getPptThemes()
  } catch {
    allThemes.value = []
  }
})
</script>

<style scoped>
.hitl {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-left: 3px solid var(--clay);
  border-radius: 12px;
  background: var(--surface);
  box-shadow: 0 1px 4px rgba(60, 56, 50, 0.04);
}
.hitl-head {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 10px;
}
.hitl-head svg {
  color: var(--clay);
  flex-shrink: 0;
}
.hitl-previews {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}
.hitl-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.hitl-opt {
  padding: 6px 14px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--sand);
  color: var(--ink);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, color 0.15s, transform 0.12s;
}
.hitl-opt:hover:not(:disabled) {
  border-color: var(--clay);
  background: var(--clay-tint);
  color: var(--clay-deep);
  transform: translateY(-1px);
}
.hitl-opt:disabled {
  cursor: default;
  opacity: 0.55;
}
.hitl-opt.chosen {
  border-color: var(--clay);
  background: var(--clay);
  color: #fff;
  opacity: 1;
}
.hitl-done {
  margin-top: 9px;
  font-size: 11.5px;
  color: var(--ink-soft);
}
</style>
