<template>
  <div class="saas-hero-bg login-wrap">
    <el-card class="login-card">
      <div class="login-head">
        <div class="login-mark"><el-icon :size="22"><Box /></el-icon></div>
        <h2>115 自动转存助手</h2>
        <p>Telegram 频道资源 · 自动分类转存</p>
      </div>
      <el-form @submit.prevent="submit">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" placeholder="密码" type="password" show-password size="large" />
        </el-form-item>
        <el-button type="primary" size="large" class="login-btn" :loading="loading" native-type="submit">
          登 录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, errMsg, markLoggedIn } from '../api'

const router = useRouter()
const form = reactive({ username: 'admin', password: '' })
const loading = ref(false)

const submit = async () => {
  loading.value = true
  try {
    await api.post('/api/auth/login', form)
    // 登录态由服务端 Set-Cookie（HttpOnly）建立，前端只记非敏感的会话标记供路由守卫使用
    markLoggedIn()
    ElMessage.success('登录成功')
    router.push('/dashboard')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap { height: 100%; }
.login-card {
  width: min(390px, calc(100vw - 32px));
  border-radius: 16px;
  border: none;
  box-shadow: 0 24px 70px rgba(5, 18, 48, .45) !important;
  padding: 10px 8px;
}
.login-head { text-align: center; margin-bottom: 22px; }
.login-mark {
  width: 52px; height: 52px; border-radius: 14px; margin: 0 auto 12px;
  background: var(--grad-brand);
  display: flex; align-items: center; justify-content: center; color: #fff;
  box-shadow: 0 8px 24px rgba(79, 107, 255, .42);
}
.login-head h2 { margin: 0 0 4px; font-size: var(--fs-page); color: var(--saas-text-main); }
.login-head p { color: var(--saas-text-sub); font-size: var(--fs-body); margin: 0; }
.login-btn { width: 100%; margin-top: 4px; }
</style>
