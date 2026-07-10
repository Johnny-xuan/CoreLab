<script setup lang="ts">
/**
 * Login view — Vercel-inspired sign-in card.
 *
 * Submits to /api/v1/auth/login via the auth store; on success follows
 * the ``redirect`` query param (set by the router guard) or falls back
 * to the dashboard.
 *
 * Visual: white card on near-white background, 1px subtle border, no
 * shadow. Brand mark above the title, footer with license + GitHub link.
 * Submit button is full-width black-on-white (the Vercel primary).
 */

import { onMounted, onUnmounted, ref } from 'vue';
import { NButton, NForm, NFormItem, NInput, type FormInst, useMessage } from 'naive-ui';
import { useRoute, useRouter } from 'vue-router';
import { AxiosError } from 'axios';

import { useAuthStore } from '@/stores/auth';

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();
const message = useMessage();

const formRef = ref<FormInst | null>(null);
const submitting = ref(false);

const formValue = ref({
  username: '',
  password: '',
});

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
};

async function handleSubmit(): Promise<void> {
  if (!formRef.value) return;
  try {
    await formRef.value.validate();
  } catch {
    return;
  }
  submitting.value = true;
  try {
    await auth.login(formValue.value.username, formValue.value.password);
    message.success('登录成功');
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : null;
    await router.replace(redirect ?? { name: 'dashboard' });
  } catch (err) {
    formValue.value.password = '';
    if (err instanceof AxiosError) {
      if (err.response?.status === 401) {
        message.error('用户名或密码错误');
      } else if (err.response?.status === 403) {
        message.error('账号已被停用');
      } else {
        message.error(`登录失败:${err.message}`);
      }
    } else {
      message.error('登录失败,请稍后再试');
    }
  } finally {
    submitting.value = false;
  }
}

/* Cursor-reactive line field behind the card. A sparse constellation of
 * slow-drifting nodes links nearby neighbours; near the cursor the nodes
 * reach out with brighter lines + a soft accent glow, so lines appear to
 * follow the mouse across the grid. Brand-accent colour, low opacity,
 * pointer-events:none (never blocks the form), and skipped entirely under
 * prefers-reduced-motion. */
const fxCanvas = ref<HTMLCanvasElement | null>(null);
let fxRaf = 0;
let fxCleanup: (() => void) | null = null;

function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace('#', '').trim();
  if (h.length === 3)
    h = h
      .split('')
      .map((c) => c + c)
      .join('');
  const n = Number.parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

onMounted(() => {
  const canvas = fxCanvas.value;
  const ctx = canvas?.getContext('2d');
  if (!canvas || !ctx) return;

  const accent =
    getComputedStyle(document.documentElement).getPropertyValue('--c-accent').trim() || '#0070f3';
  const [ar, ag, ab] = hexToRgb(accent);
  const rgba = (a: number): string => `rgba(${ar},${ag},${ab},${a})`;

  // Respect reduce-motion for the *autonomous* node drift only — the
  // cursor-following lines are driven by the user's own movement, so we
  // still render those (freezing the drift keeps the nodes still).
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const NODE_COUNT = 46;
  const LINK = 160;
  const REACH = 260;
  const nodes: { x: number; y: number; vx: number; vy: number }[] = [];
  const mouse = { x: -9999, y: -9999, active: false };
  let w = 0;
  let h = 0;

  function resize(): void {
    const r = canvas!.getBoundingClientRect();
    w = r.width;
    h = r.height;
    canvas!.width = Math.round(w * dpr);
    canvas!.height = Math.round(h * dpr);
    ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (nodes.length === 0) {
      for (let i = 0; i < NODE_COUNT; i++) {
        nodes.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.22,
          vy: (Math.random() - 0.5) * 0.22,
        });
      }
    }
  }

  function render(): void {
    ctx!.clearRect(0, 0, w, h);
    if (!reduce) {
      for (const n of nodes) {
        n.x += n.vx;
        n.y += n.vy;
        if (n.x <= 0 || n.x >= w) n.vx *= -1;
        if (n.y <= 0 || n.y >= h) n.vy *= -1;
      }
    }
    ctx!.lineWidth = 1;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i]!;
        const b = nodes[j]!;
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < LINK) {
          ctx!.strokeStyle = rgba((1 - d / LINK) * 0.14);
          ctx!.beginPath();
          ctx!.moveTo(a.x, a.y);
          ctx!.lineTo(b.x, b.y);
          ctx!.stroke();
        }
      }
    }
    if (mouse.active) {
      const grad = ctx!.createRadialGradient(mouse.x, mouse.y, 0, mouse.x, mouse.y, REACH);
      grad.addColorStop(0, rgba(0.14));
      grad.addColorStop(1, rgba(0));
      ctx!.fillStyle = grad;
      ctx!.fillRect(mouse.x - REACH, mouse.y - REACH, REACH * 2, REACH * 2);
      for (const n of nodes) {
        const d = Math.hypot(n.x - mouse.x, n.y - mouse.y);
        if (d < REACH) {
          const t = 1 - d / REACH;
          ctx!.strokeStyle = rgba(t * 0.55);
          ctx!.lineWidth = 1 + t * 0.5;
          ctx!.beginPath();
          ctx!.moveTo(mouse.x, mouse.y);
          ctx!.lineTo(n.x, n.y);
          ctx!.stroke();
          ctx!.fillStyle = rgba(t * 0.7);
          ctx!.beginPath();
          ctx!.arc(n.x, n.y, 1.6, 0, Math.PI * 2);
          ctx!.fill();
        }
      }
      ctx!.lineWidth = 1;
      // focal dot at the cursor
      ctx!.fillStyle = rgba(0.55);
      ctx!.beginPath();
      ctx!.arc(mouse.x, mouse.y, 2.4, 0, Math.PI * 2);
      ctx!.fill();
    }
  }

  function loop(): void {
    render();
    fxRaf = requestAnimationFrame(loop);
  }

  function onMove(e: MouseEvent): void {
    const r = canvas!.getBoundingClientRect();
    mouse.x = e.clientX - r.left;
    mouse.y = e.clientY - r.top;
    mouse.active = true;
  }
  function onLeave(): void {
    mouse.active = false;
  }

  resize();
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);
  window.addEventListener('mousemove', onMove, { passive: true });
  document.addEventListener('mouseleave', onLeave);
  window.addEventListener('blur', onLeave);

  fxRaf = requestAnimationFrame(loop);

  fxCleanup = (): void => {
    cancelAnimationFrame(fxRaf);
    ro.disconnect();
    window.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseleave', onLeave);
    window.removeEventListener('blur', onLeave);
  };
});

onUnmounted(() => fxCleanup?.());
</script>

<template>
  <div class="login-page">
    <canvas ref="fxCanvas" class="fx-canvas" aria-hidden="true" />
    <div class="login-card">
      <header class="header">
        <img class="brand-mark" src="/logo.png" alt="CoreLab" />
        <h1 class="title">CoreLab</h1>
        <p class="subtitle">可自托管的 GPU 实验室管理平台</p>
      </header>

      <NForm
        ref="formRef"
        class="login-form"
        :model="formValue"
        :rules="rules"
        size="large"
        label-placement="top"
        :show-require-mark="false"
        @submit.prevent="handleSubmit"
      >
        <NFormItem path="username" label="用户名">
          <NInput
            v-model:value="formValue.username"
            placeholder="alice"
            autocomplete="username"
            :input-props="{ autocapitalize: 'off' }"
          />
        </NFormItem>
        <NFormItem path="password" label="密码">
          <NInput
            v-model:value="formValue.password"
            type="password"
            placeholder="••••••••"
            autocomplete="current-password"
            show-password-on="mousedown"
          />
        </NFormItem>
        <NButton type="primary" attr-type="submit" block :loading="submitting"> 登录 </NButton>
      </NForm>
    </div>

    <footer class="footer">
      <span>© 2026 CoreLab</span>
      <span class="footer-sep">·</span>
      <span>Apache 2.0</span>
      <span class="footer-sep">·</span>
      <a href="#" class="footer-link">GitHub <span aria-hidden="true">→</span></a>
    </footer>
  </div>
</template>

<style scoped>
.login-page {
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
.login-page::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 1;
  background: radial-gradient(ellipse at center, transparent 0%, var(--c-bg-base) 72%);
  pointer-events: none;
}

/* Cursor-reactive line field — sits above the static grid, below the
 * vignette + card. Never intercepts pointer events. */
.fx-canvas {
  position: absolute;
  inset: 0;
  z-index: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.login-card {
  position: relative;
  z-index: 2;
  width: 100%;
  max-width: 24rem;
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  padding: var(--space-8) var(--space-10);
  box-shadow: none;
}

.header {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  margin-bottom: var(--space-7);
}

.brand-mark {
  height: 44px;
  width: auto;
  margin-bottom: var(--space-4);
  display: block;
}

.title {
  font-family: var(--font-sans);
  font-size: var(--text-2xl);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  margin: 0 0 var(--space-1);
  color: var(--c-text-primary);
}

.subtitle {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  margin: 0;
  line-height: var(--leading-snug);
}

/* Even vertical rhythm: each field group is a consistent gap apart, and
 * the submit sits one slightly-larger step below the last field so the
 * action reads as deliberate rather than crammed or floating. Naive's
 * default feedback wrapper reserves ~24px under every field — we tighten
 * it so the inter-field gaps match the rest of the card's spacing. */
.login-form :deep(.n-form-item) {
  margin-bottom: var(--space-3);
}
.login-form :deep(.n-form-item .n-form-item-label) {
  padding-bottom: var(--space-1);
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
.login-form :deep(.n-form-item-feedback-wrapper) {
  min-height: var(--space-2);
}
.login-form :deep(.n-button) {
  margin-top: 0;
  height: 42px;
  font-weight: 500;
}

.footer {
  position: relative;
  z-index: 2;
  margin-top: var(--space-6);
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.footer-sep {
  color: var(--c-border-default);
}

.footer-link {
  color: var(--c-text-tertiary);
  text-decoration: none;
}
.footer-link:hover {
  color: var(--c-text-link);
  text-decoration: none;
}
</style>
