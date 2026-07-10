<script setup lang="ts">
/**
 * Setup wizard — 3 steps (Lab → Admin → Confirm).
 *
 * Reachable only when /setup/status returns initialized=false; the
 * router guard redirects to /login otherwise. On success, flushes the
 * router's initialized cache so subsequent navigations see the new
 * state.
 *
 * Visual (design v2): centered card on grid background with a faint
 * brand-blue radial, matching Login.vue. Brand badge ripples slowly
 * (cl-ripple) for a "powering up" feel; step chips use brand blue for
 * the active step and --c-success + check for completed steps. Step
 * panels fade in on switch via CSS only (v-show display toggle replays
 * the animation). Keeps slug-auto-derive + Chinese helper paragraphs
 * from agent-D's UX pass.
 */

import { computed, ref, watch } from 'vue';
import { NButton, NForm, NFormItem, NInput, type FormInst, useMessage } from 'naive-ui';
import { useRouter } from 'vue-router';
import { AxiosError } from 'axios';

import { setupInit, suggestSlug } from '@/api/setup';
import { flushSetupCache } from '@/router';

const router = useRouter();
const message = useMessage();

const stepIndex = ref(0);
const submitting = ref(false);

const labFormRef = ref<FormInst | null>(null);
const adminFormRef = ref<FormInst | null>(null);

const lab = ref({
  lab_name: '',
  lab_slug: '',
});

const admin = ref({
  admin_username: '',
  admin_email: '',
  admin_display_name: '',
  admin_password: '',
  admin_password_confirm: '',
});

const labRules = {
  lab_name: [{ required: true, message: '请输入实验室名称', trigger: 'blur' }],
  // Phase M M-2.3 — slug 不再必填。空字段时后端用 derive_slug(lab_name) 兜底;
  // 用户填了内容则按 LabSlugPattern 校验。
  lab_slug: [
    {
      validator: (_rule: unknown, value: string) =>
        value === '' || value === undefined || /^[a-z][a-z0-9-]{1,49}$/.test(value),
      message: '只允许小写字母、数字、短横线,且以字母开头',
      trigger: 'blur',
    },
  ],
};

// Phase M M-2.3 — auto-suggest a slug via the backend ``/setup/suggest-slug``
// endpoint. Calling the server keeps the suggestion algorithm in lockstep
// with the fallback the server applies if the request omits ``lab_slug``;
// in particular, pure-CJK lab names get a ``lab-{8hex}`` fallback there,
// not an empty string.
const slugManuallyEdited = ref(false);
const slugAutoFromCjk = ref(false);
let slugDebounce: ReturnType<typeof setTimeout> | null = null;

watch(
  () => lab.value.lab_name,
  (newName) => {
    if (slugManuallyEdited.value) return;
    const trimmed = newName.trim();
    if (slugDebounce !== null) clearTimeout(slugDebounce);
    if (trimmed.length === 0) {
      lab.value.lab_slug = '';
      slugAutoFromCjk.value = false;
      return;
    }
    // Debounce 250ms so we don't fire one HTTP call per keystroke.
    slugDebounce = setTimeout(() => {
      void suggestSlug(trimmed)
        .then((s) => {
          if (slugManuallyEdited.value) return; // user typed in the slug field meanwhile
          lab.value.lab_slug = s;
          // Detect "CJK / emoji input → server fell back to lab-xxxxxxxx"
          // so the UI can explain why the slug looks like a random one.
          slugAutoFromCjk.value = /^lab-[0-9a-f]{8}$/.test(s);
        })
        .catch(() => {
          // Network blip — leave the field as-is; user can still type one.
        });
    }, 250);
  },
);

function onSlugInput(value: string): void {
  slugManuallyEdited.value = true;
  slugAutoFromCjk.value = false;
  lab.value.lab_slug = value;
}

const adminRules = {
  admin_username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    {
      pattern: /^[a-z][a-z0-9_-]{2,63}$/,
      message: '小写字母开头,3-64 字符',
      trigger: 'blur',
    },
  ],
  admin_email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { pattern: /^[^@\s]+@[^@\s]+\.[^@\s]+$/, message: '邮箱格式不正确', trigger: 'blur' },
  ],
  admin_display_name: [{ required: true, message: '请输入显示名', trigger: 'blur' }],
  admin_password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
  admin_password_confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule: unknown, value: string) => value === admin.value.admin_password,
      message: '两次密码不一致',
      trigger: 'blur',
    },
  ],
};

interface StepDef {
  index: number;
  label: string;
  desc: string;
}
const steps: StepDef[] = [
  { index: 1, label: '实验室', desc: '实验室信息' },
  { index: 2, label: '管理员', desc: '管理员账号' },
  { index: 3, label: '确认', desc: '确认并初始化' },
];

const currentStep = computed(() => steps[stepIndex.value] ?? steps[0]);

async function nextStep(): Promise<void> {
  if (stepIndex.value === 0 && labFormRef.value !== null) {
    try {
      await labFormRef.value.validate();
    } catch {
      return;
    }
  }
  if (stepIndex.value === 1 && adminFormRef.value !== null) {
    try {
      await adminFormRef.value.validate();
    } catch {
      return;
    }
  }
  stepIndex.value += 1;
}

function prevStep(): void {
  if (stepIndex.value > 0) {
    stepIndex.value -= 1;
  }
}

async function submit(): Promise<void> {
  submitting.value = true;
  try {
    await setupInit({
      lab_name: lab.value.lab_name,
      lab_slug: lab.value.lab_slug,
      admin_username: admin.value.admin_username,
      admin_email: admin.value.admin_email,
      admin_display_name: admin.value.admin_display_name,
      admin_password: admin.value.admin_password,
    });
    flushSetupCache();
    message.success('初始化成功,请登录');
    await router.replace({ name: 'login' });
  } catch (err) {
    if (err instanceof AxiosError) {
      const detail = err.response?.data?.detail ?? err.message;
      message.error(`初始化失败:${String(detail)}`);
    } else {
      message.error('初始化失败,请稍后再试');
    }
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="setup-page">
    <div class="setup-card cl-enter">
      <header class="header cl-enter" style="--cl-delay: 0.08s">
        <div class="brand-badge">
          <span class="ripple" aria-hidden="true" />
          <span class="ripple ripple-late" aria-hidden="true" />
          <img class="brand-mark" src="/logo.png" alt="CoreLab" />
        </div>
        <h1 class="title">CoreLab</h1>
        <p class="subtitle">初始化你的 GPU 实验室</p>
        <p class="step-meta">
          第 {{ currentStep!.index }} / {{ steps.length }} 步 ·
          <span class="subtitle-label">{{ currentStep!.label }}</span>
        </p>
      </header>

      <ol class="steps-bar cl-enter" style="--cl-delay: 0.16s">
        <li
          v-for="(s, i) in steps"
          :key="s.label"
          :class="['step-pill', { active: i === stepIndex, done: i < stepIndex }]"
        >
          <span :class="['step-num', 'mono', 'tabular', { 'cl-pulse': i === stepIndex }]">
            {{ i < stepIndex ? '✓' : s.index }}
          </span>
          <span class="step-label">{{ s.label }}</span>
        </li>
      </ol>

      <div v-show="stepIndex === 0" class="step-panel">
        <NForm
          ref="labFormRef"
          :model="lab"
          :rules="labRules"
          size="medium"
          label-placement="top"
          :show-require-mark="false"
        >
          <NFormItem path="lab_name" label="实验室名称">
            <NInput v-model:value="lab.lab_name" placeholder="如:Example GPU Lab" />
          </NFormItem>
          <NFormItem path="lab_slug" label="实验室短标识 (Lab slug)">
            <div class="slug-field">
              <NInput
                :value="lab.lab_slug"
                placeholder="如:example-gpu"
                @update:value="onSlugInput"
              />
              <p v-if="slugAutoFromCjk" class="field-hint hint-warn">
                输入的 Lab 名称无法直接转成英文标识,已自动生成
                <code>{{ lab.lab_slug }}</code
                >。可以改成你想要的英文,例如 <code>zhang-lab</code>。
              </p>
              <p v-else class="field-hint">
                用于 URL 路径,只能是小写英文字母、数字和短横线,例如
                <code>zhang-lab</code>。
              </p>
            </div>
          </NFormItem>
        </NForm>
      </div>

      <div v-show="stepIndex === 1" class="step-panel">
        <NForm
          ref="adminFormRef"
          :model="admin"
          :rules="adminRules"
          size="medium"
          label-placement="top"
          :show-require-mark="false"
        >
          <NFormItem path="admin_username" label="管理员用户名">
            <NInput v-model:value="admin.admin_username" placeholder="如:alice" />
          </NFormItem>
          <NFormItem path="admin_email" label="邮箱">
            <NInput v-model:value="admin.admin_email" placeholder="如:alice@example.com" />
          </NFormItem>
          <NFormItem path="admin_display_name" label="显示名">
            <NInput v-model:value="admin.admin_display_name" placeholder="如:Alice Wang" />
          </NFormItem>
          <NFormItem path="admin_password" label="密码">
            <NInput
              v-model:value="admin.admin_password"
              type="password"
              show-password-on="mousedown"
              placeholder="••••••••"
            />
          </NFormItem>
          <NFormItem path="admin_password_confirm" label="确认密码">
            <NInput
              v-model:value="admin.admin_password_confirm"
              type="password"
              show-password-on="mousedown"
              placeholder="••••••••"
            />
          </NFormItem>
        </NForm>
      </div>

      <div v-show="stepIndex === 2" class="confirm step-panel">
        <dl class="kv">
          <dt>实验室名称</dt>
          <dd>{{ lab.lab_name }}</dd>
          <dt>短标识</dt>
          <dd>
            <code>{{ lab.lab_slug }}</code>
          </dd>
          <dt>管理员</dt>
          <dd>
            {{ admin.admin_display_name }} <span class="mono">@{{ admin.admin_username }}</span>
          </dd>
          <dt>邮箱</dt>
          <dd class="mono">{{ admin.admin_email }}</dd>
        </dl>
        <p class="warn-note">初始化后将不可重复执行,请确认信息无误。</p>
      </div>

      <div class="actions">
        <NButton v-if="stepIndex > 0" @click="prevStep">上一步</NButton>
        <div class="actions-spacer" />
        <NButton v-if="stepIndex < 2" type="primary" @click="nextStep">下一步</NButton>
        <NButton v-else type="primary" :loading="submitting" @click="submit">初始化</NButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.setup-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: var(--space-6) var(--space-4);
  background: var(--c-bg-base);
  /* 同 Login 的细 grid 纹理,顶部叠一层极淡的品牌蓝 radial,做"启动中"氛围。 */
  background-image:
    radial-gradient(
      ellipse 85% 60% at 50% 0%,
      color-mix(in srgb, var(--c-accent) 6%, transparent),
      transparent 70%
    ),
    linear-gradient(var(--c-border-subtle) 1px, transparent 1px),
    linear-gradient(90deg, var(--c-border-subtle) 1px, transparent 1px);
  background-size:
    100% 100%,
    48px 48px,
    48px 48px;
  background-position:
    center top,
    center center,
    center center;
  position: relative;
}
/* 暗色:撤顶部品牌蓝 radial(漏光感),只留 grid 纹理;
 * "启动中"氛围由 logo 的 ripple 环承担。 */
[data-theme='dark'] .setup-page {
  background-image:
    linear-gradient(var(--c-border-subtle) 1px, transparent 1px),
    linear-gradient(90deg, var(--c-border-subtle) 1px, transparent 1px);
  background-size:
    48px 48px,
    48px 48px;
  background-position:
    center center,
    center center;
}
.setup-page::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(ellipse at center, transparent 0%, var(--c-bg-base) 70%);
  pointer-events: none;
}

.setup-card {
  position: relative;
  width: 100%;
  max-width: 32rem;
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  padding: var(--space-8) var(--space-7);
  box-shadow: var(--shadow-md);
}

.header {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  margin-bottom: var(--space-6);
}
/* 品牌徽标外圈缓慢扩散的 ripple(品牌蓝、低透明度)——启动仪式感。
   复用 main.css 的 @keyframes cl-ripple;基础 opacity:0 避免延迟期闪现。 */
.brand-badge {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 88px;
  height: 88px;
  margin-bottom: var(--space-3);
}
.brand-mark {
  position: relative;
  z-index: 1;
  height: 56px;
  width: auto;
  display: block;
}
.ripple {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 64px;
  height: 64px;
  margin: -32px 0 0 -32px;
  border: 1px solid color-mix(in srgb, var(--c-accent) 45%, transparent);
  border-radius: var(--radius-full);
  opacity: 0;
  pointer-events: none;
  animation: cl-ripple 3.2s ease-out infinite;
}
.ripple-late {
  animation-delay: 1.6s;
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
  margin: 0 0 var(--space-2);
  line-height: var(--leading-snug);
}
.step-meta {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  margin: 0;
}
.subtitle-label {
  color: var(--c-text-primary);
  font-weight: 500;
}

.steps-bar {
  list-style: none;
  padding: 0;
  margin: 0 0 var(--space-6);
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-2);
}
.step-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  background: var(--c-bg-elevated);
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  transition:
    border-color 120ms ease,
    color 120ms ease,
    background 120ms ease;
}
.step-pill .step-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--c-border-default);
  font-size: var(--text-2xs);
  color: var(--c-text-tertiary);
}
.step-pill.active {
  border-color: color-mix(in srgb, var(--c-accent) 55%, transparent);
  color: var(--c-text-primary);
  background: color-mix(in srgb, var(--c-accent) 4%, var(--c-bg-elevated));
}
.step-pill.active .step-num {
  background: var(--c-accent);
  color: var(--c-text-inverse);
  border-color: var(--c-accent);
}
.step-pill.done {
  color: var(--c-text-secondary);
}
.step-pill.done .step-num {
  background: color-mix(in srgb, var(--c-success) 14%, transparent);
  color: var(--c-success);
  border-color: color-mix(in srgb, var(--c-success) 35%, transparent);
}

/* 每步内容区淡入 — v-show 切换 display 时动画自动重放,无需改切换逻辑。 */
@keyframes step-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
.step-panel {
  animation: step-in 0.28s ease both;
}

.actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-6);
}
.actions-spacer {
  flex: 1;
}
.actions :deep(.n-button) {
  transition:
    transform 0.16s ease,
    box-shadow 0.16s ease;
}
.actions :deep(.n-button--primary-type:not(.n-button--disabled):hover) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}

.confirm .kv {
  display: grid;
  grid-template-columns: 8rem 1fr;
  row-gap: var(--space-3);
  column-gap: var(--space-3);
  margin: 0;
  padding: var(--space-4);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
}
.confirm dt {
  color: var(--c-text-tertiary);
  font-size: var(--text-xs);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
}
.confirm dd {
  margin: 0;
  color: var(--c-text-primary);
  font-size: var(--text-sm);
}
.confirm dd code,
.confirm dd .mono {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}
.confirm .warn-note {
  margin: var(--space-4) 0 0;
  color: var(--c-text-tertiary);
  font-size: var(--text-xs);
  text-align: center;
}

.slug-field {
  width: 100%;
}
.field-hint {
  margin: var(--space-1) 0 0;
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  line-height: var(--leading-snug);
}
.field-hint code {
  font-family: var(--font-mono);
  font-size: 0.95em;
  background: var(--c-bg-sunken);
  padding: 1px 4px;
  border-radius: var(--radius-sm);
}
.hint-warn {
  color: var(--c-warning);
}

/* 循环/位移动效的 reduced-motion 退化:ripple 按 main.css 的约定
   "保活但更慢更淡";面板淡入与按钮位移直接关闭。cl-enter / cl-pulse
   已由 main.css 全局处理。 */
@media (prefers-reduced-motion: reduce) {
  .ripple {
    animation-duration: 10s;
    border-color: color-mix(in srgb, var(--c-accent) 22%, transparent);
  }
  .step-panel {
    animation: none;
  }
  .actions :deep(.n-button--primary-type:not(.n-button--disabled):hover) {
    transform: none;
    box-shadow: none;
  }
}
</style>
