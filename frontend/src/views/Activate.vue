<script setup lang="ts">
/**
 * Register/reset view — invite links reach /register?token=..., while
 * password reset links keep using /activate?token=...&mode=reset.
 * Validates the setup token, then lets the user complete registration
 * or set a replacement password.
 *
 * Visual mirrors Login.vue — same diamond brand mark + white card on
 * grid background.
 */

import { computed, onMounted, ref } from 'vue';
import { NButton, NForm, NFormItem, NInput, NSpin, type FormInst, useMessage } from 'naive-ui';
import { useRoute, useRouter } from 'vue-router';
import { AxiosError } from 'axios';

import { activate, validateActivationToken } from '@/api/setup';
import type { ActivateValidate } from '@/api/setup';

const route = useRoute();
const router = useRouter();
const message = useMessage();

const formRef = ref<FormInst | null>(null);
const token = computed(() => (typeof route.query.token === 'string' ? route.query.token : ''));

const loading = ref(true);
const submitting = ref(false);
const error = ref<string | null>(null);
const target = ref<ActivateValidate | null>(null);

const isReset = computed(() =>
  target.value !== null ? target.value.purpose === 'password_reset' : route.query.mode === 'reset',
);
const isRegistration = computed(() => !isReset.value);
const displayIdentity = computed(() => {
  if (formValue.value.display_name.trim() !== '') return formValue.value.display_name.trim();
  if (formValue.value.username.trim() !== '') return formValue.value.username.trim();
  return '新成员';
});

const formValue = ref({
  username: '',
  email: '',
  display_name: '',
  password: '',
  password_confirm: '',
  ssh_key_label: '',
  ssh_key_public_key: '',
});

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    {
      pattern: /^[a-z][a-z0-9_-]{2,63}$/,
      message: '小写字母开头,3-64 字符',
      trigger: 'blur',
    },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { pattern: /^[^@\s]+@[^@\s]+\.[^@\s]+$/, message: '邮箱格式不正确', trigger: 'blur' },
  ],
  display_name: [{ required: true, message: '请输入显示名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
  password_confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule: unknown, value: string) => value === formValue.value.password,
      message: '两次密码不一致',
      trigger: 'blur',
    },
  ],
  ssh_key_public_key: [
    {
      validator: (_rule: unknown, value: unknown) =>
        typeof value !== 'string' ||
        value.trim() === '' ||
        /^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+|sk-)/.test(value),
      message: '公钥格式不正确',
      trigger: 'blur',
    },
  ],
};

onMounted(async () => {
  if (token.value === '') {
    error.value = '注册链接缺少 token 参数';
    loading.value = false;
    return;
  }
  try {
    target.value = await validateActivationToken(token.value);
    formValue.value.username = target.value.username ?? '';
    formValue.value.email = target.value.email ?? '';
    formValue.value.display_name = target.value.display_name ?? '';
  } catch (err) {
    if (err instanceof AxiosError) {
      error.value = String(err.response?.data?.detail ?? err.message);
    } else {
      error.value = '激活链接校验失败';
    }
  } finally {
    loading.value = false;
  }
});

async function submit(): Promise<void> {
  if (!formRef.value) return;
  try {
    await formRef.value.validate();
  } catch {
    return;
  }
  submitting.value = true;
  try {
    await activate(
      token.value,
      isReset.value
        ? { password: formValue.value.password }
        : {
            username: formValue.value.username.trim(),
            email: formValue.value.email.trim(),
            display_name: formValue.value.display_name.trim(),
            password: formValue.value.password,
            ssh_key_label: formValue.value.ssh_key_label.trim() || undefined,
            ssh_key_public_key: formValue.value.ssh_key_public_key.trim() || undefined,
          },
    );
    message.success(isReset.value ? '密码已重置,请登录' : '注册完成,请登录');
    await router.replace({ name: 'login' });
  } catch (err) {
    const verb = isReset.value ? '重置' : '注册';
    if (err instanceof AxiosError) {
      message.error(`${verb}失败:${String(err.response?.data?.detail ?? err.message)}`);
    } else {
      message.error(`${verb}失败,请稍后再试`);
    }
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="activate-page">
    <div class="activate-card">
      <header class="header">
        <img class="brand-mark" src="/logo.png" alt="CoreLab" />
        <h1 class="title">{{ isReset ? '重置密码' : '完成注册' }}</h1>
        <p v-if="target !== null" class="subtitle">
          <template v-if="isReset">
            为 <strong>{{ displayIdentity }}</strong>
            <span v-if="formValue.username" class="mono">(@{{ formValue.username }})</span>
            设置新密码
          </template>
          <template v-else> 填写账号信息并绑定 SSH 公钥 </template>
        </p>
      </header>

      <NSpin v-if="loading" />

      <div v-else-if="error !== null" class="error">
        <p>{{ error }}</p>
        <NButton block @click="router.replace({ name: 'login' })">返回登录</NButton>
      </div>

      <NForm
        v-else-if="target !== null"
        ref="formRef"
        :model="formValue"
        :rules="rules"
        size="medium"
        label-placement="top"
        :show-require-mark="false"
        @submit.prevent="submit"
      >
        <template v-if="isRegistration">
          <div class="section-title">账号</div>
          <NFormItem path="username" label="用户名">
            <NInput v-model:value="formValue.username" placeholder="alice" />
          </NFormItem>
          <NFormItem path="email" label="邮箱">
            <NInput v-model:value="formValue.email" placeholder="alice@example.com" />
          </NFormItem>
          <NFormItem path="display_name" label="显示名">
            <NInput v-model:value="formValue.display_name" placeholder="Alice Wang" />
          </NFormItem>
        </template>

        <div class="section-title">{{ isReset ? '密码' : '登录密码' }}</div>
        <NFormItem path="password" label="新密码">
          <NInput
            v-model:value="formValue.password"
            type="password"
            show-password-on="mousedown"
            placeholder="••••••••"
          />
        </NFormItem>
        <NFormItem path="password_confirm" label="确认密码">
          <NInput
            v-model:value="formValue.password_confirm"
            type="password"
            show-password-on="mousedown"
            placeholder="••••••••"
          />
        </NFormItem>
        <template v-if="isRegistration">
          <div class="section-title">SSH 公钥</div>
          <NFormItem path="ssh_key_label" label="标签(可选)">
            <NInput v-model:value="formValue.ssh_key_label" placeholder="MacBook / Workstation" />
          </NFormItem>
          <NFormItem path="ssh_key_public_key" label="公钥(可选)">
            <NInput
              v-model:value="formValue.ssh_key_public_key"
              type="textarea"
              :rows="4"
              placeholder="ssh-ed25519 AAAA... user@host"
            />
          </NFormItem>
        </template>
        <NButton type="primary" attr-type="submit" block :loading="submitting">
          {{ isReset ? '重置密码' : '完成注册' }}
        </NButton>
      </NForm>
    </div>
  </div>
</template>

<style scoped>
.activate-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: var(--space-6) var(--space-4);
  background: var(--c-bg-base);
  background-image:
    linear-gradient(var(--c-border-subtle) 1px, transparent 1px),
    linear-gradient(90deg, var(--c-border-subtle) 1px, transparent 1px);
  background-size: 48px 48px;
  background-position: center center;
  position: relative;
}
.activate-page::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(ellipse at center, transparent 0%, var(--c-bg-base) 70%);
  pointer-events: none;
}

.activate-card {
  position: relative;
  width: 100%;
  max-width: 30rem;
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  padding: var(--space-8) var(--space-7);
}

.header {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  margin-bottom: var(--space-6);
}
.brand-mark {
  height: 56px;
  width: auto;
  margin-bottom: var(--space-4);
  display: block;
}
.title {
  font-family: var(--font-sans);
  font-size: var(--text-2xl);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  margin: 0 0 var(--space-2);
  color: var(--c-text-primary);
}
.subtitle {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  margin: 0;
  line-height: var(--leading-snug);
}
.subtitle .mono {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  margin-left: var(--space-1);
  color: var(--c-text-tertiary);
}

.error {
  display: grid;
  gap: var(--space-3);
  text-align: center;
}
.error p {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
.section-title {
  margin: var(--space-5) 0 var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--c-border-subtle);
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--c-text-secondary);
}
.section-title:first-child {
  margin-top: 0;
  padding-top: 0;
  border-top: 0;
}
</style>
