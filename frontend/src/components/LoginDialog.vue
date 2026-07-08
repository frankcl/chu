<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    width="380px"
    align-center
    append-to-body
    class="login-dialog"
  >
    <template #header>
      <div class="login-head">
        <img :src="chuLogo" alt="Chu" class="login-logo" />
        <div class="login-titles">
          <h2 class="login-title">登录 Chu</h2>
          <p class="login-tagline">陪你一起成长</p>
        </div>
      </div>
    </template>

    <div class="login-body" @keyup.enter="onSubmit">
      <el-input v-model="form.username" placeholder="用户名" size="large" autocomplete="username" clearable>
        <template #prefix><IconUser :size="17" :stroke="1.7" /></template>
      </el-input>
      <el-input
        v-model="form.password"
        type="password"
        placeholder="密码"
        size="large"
        autocomplete="current-password"
        show-password
      >
        <template #prefix><IconLock :size="17" :stroke="1.7" /></template>
      </el-input>
      <div class="login-captcha-row">
        <el-input v-model="form.captcha" placeholder="验证码" size="large" autocomplete="off" class="login-captcha-input">
          <template #prefix><IconShieldCheck :size="17" :stroke="1.7" /></template>
        </el-input>
        <img :src="captchaImage" class="login-captcha-img" title="点击刷新验证码" alt="验证码" @click="refreshCaptcha" />
      </div>
    </div>

    <template #footer>
      <el-button type="primary" size="large" class="login-submit" :loading="loggingIn" @click="onSubmit">
        登录
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { IconUser, IconLock, IconShieldCheck } from '@tabler/icons-vue'
import { applyCaptcha, passwordLogin } from '../api/auth.js'
import { drawCaptcha } from '../utils/captcha.js'
import chuLogo from '../assets/chu-icon.png'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'success'])

const form = ref({ username: '', password: '', captcha: '' })
const captchaImage = ref('')
const loggingIn = ref(false)

async function refreshCaptcha() {
  try {
    captchaImage.value = drawCaptcha(await applyCaptcha())
  } catch (e) {
    ElMessage.error(e.message || '验证码申请失败')
  }
}

async function onSubmit() {
  if (loggingIn.value) return
  loggingIn.value = true
  try {
    await passwordLogin(form.value.username, form.value.password, form.value.captcha)
    emit('success')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(e.message || '登录失败')
    form.value.captcha = ''
    refreshCaptcha()
  } finally {
    loggingIn.value = false
  }
}

// 打开弹框时重置表单并取一张新验证码。
watch(() => props.modelValue, (open) => {
  if (open) {
    form.value = { username: '', password: '', captcha: '' }
    refreshCaptcha()
  }
})
</script>

<style scoped>
.login-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.login-logo {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  object-fit: cover;
  background: #fff;
  box-shadow: 0 1px 3px rgba(60, 56, 50, 0.12);
}
.login-title {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 500;
  color: var(--ink);
  line-height: 1.2;
}
.login-tagline {
  font-size: 12.5px;
  color: var(--ink-soft);
  margin-top: 3px;
}
.login-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.login-captcha-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.login-captcha-input {
  flex: 1;
}
.login-captcha-img {
  height: 40px;
  width: 108px;
  object-fit: cover;
  border: 1px solid var(--line);
  border-radius: 8px;
  cursor: pointer;
  flex-shrink: 0;
}
.login-submit {
  width: 100%;
}
</style>
