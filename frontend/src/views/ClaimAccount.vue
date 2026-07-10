<script setup lang="ts">
/**
 * ClaimAccount — ``/me/accounts/claim``.
 *
 * docs/07 §3.3 — Cross-PA "Claim new account" entry. The
 * entrypoint when a fresh user has zero workspaces yet, or
 * when an existing user wants to add another Linux account.
 *
 * Surfaces the two active claim paths (SSH challenge /
 * agent-discovered link request) and lets the user kick off
 * the SSH challenge flow inline. Existing link-request review
 * lives at /account-link-requests for the other side (admins).
 */

import { useRouter } from 'vue-router';
import { ArrowRight, KeyRound, Link2, Sparkles } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';

const router = useRouter();

interface ClaimOption {
  key: string;
  title: string;
  desc: string;
  hint: string;
  cta: string;
  routeName: string | null;
  query?: Record<string, string>;
  icon: typeof KeyRound;
}

const options: ClaimOption[] = [
  {
    key: 'ssh-challenge',
    title: '通过 SSH key challenge 关联',
    desc: '用你的 SSH key 对一个 challenge 签名,证明你拥有 CoreLab 托管服务器上的某个 Linux 账号。',
    hint: '推荐路径。向导里可以直接添加 SSH 公钥,不必先去个人资料。',
    cta: '打开关联向导',
    routeName: 'my-account-links',
    query: { add: '1' },
    icon: KeyRound,
  },
  {
    key: 'agent-discovery',
    title: 'agent 发现的 Linux 用户',
    desc: 'agent 首次连接时会扫描 /etc/passwd,列出已有的 Linux 用户,供管理员映射到 CoreLab 身份。',
    hint: '在向导里选中你的 Linux 账号提交申请,等管理员审批。',
    cta: '打开账号关联申请',
    routeName: 'my-account-links',
    query: { add: '1' },
    icon: Sparkles,
  },
];

async function pick(opt: ClaimOption): Promise<void> {
  if (opt.routeName === null) return;
  await router.push({ name: opt.routeName, query: opt.query });
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <Link2 :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">关联 Linux 账号</h1>
          <p class="cl-pagebar-sub">
            预约 GPU 需要 CoreLab 知道你在目标服务器上对应哪个 Linux
            用户。请在下面选择一种关联方式。
          </p>
        </div>
        <!-- 装饰:淡淡的「节点 — 链路 — 节点」连接线,呼应"关联"这个动作 -->
        <svg class="claim-deco" viewBox="0 0 128 24" fill="none" aria-hidden="true">
          <circle cx="8" cy="12" r="3.5" stroke="currentColor" stroke-width="1.5" />
          <path
            d="M15 12h41"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
            stroke-dasharray="2 5"
          />
          <circle cx="64" cy="12" r="4" fill="currentColor" />
          <path
            d="M72 12h41"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
            stroke-dasharray="2 5"
          />
          <circle cx="120" cy="12" r="3.5" stroke="currentColor" stroke-width="1.5" />
        </svg>
      </header>

      <ul class="options">
        <li
          v-for="(opt, i) in options"
          :key="opt.key"
          class="option cl-enter"
          :class="{ 'option--primary': opt.key === 'ssh-challenge' }"
          :style="{ '--cl-delay': `${0.08 + i * 0.07}s` }"
        >
          <button
            class="option-button"
            :class="{ 'cl-lift': opt.key === 'ssh-challenge' }"
            type="button"
            @click="pick(opt)"
          >
            <span v-if="opt.key === 'ssh-challenge'" class="option-flag">
              <span class="option-flag-dot cl-pulse" />
              推荐
            </span>
            <span class="option-icon">
              <component
                :is="opt.icon"
                :size="opt.key === 'ssh-challenge' ? 20 : 18"
                :stroke-width="1.75"
              />
            </span>
            <span class="option-body">
              <span class="option-title">{{ opt.title }}</span>
              <span class="option-desc">{{ opt.desc }}</span>
              <span class="option-hint">{{ opt.hint }}</span>
            </span>
            <span class="option-cta">
              <span>{{ opt.cta }}</span>
              <ArrowRight class="option-arrow cl-nudge" :size="14" :stroke-width="2" />
            </span>
          </button>
        </li>
      </ul>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  padding: var(--space-6) var(--space-8) var(--space-12);
  max-width: 800px;
  margin: 0 auto;
}

/* ── 头部装饰:淡蓝链路线,小屏隐藏 ─────────────────────────── */
.claim-deco {
  flex: none;
  align-self: center;
  width: 128px;
  height: 24px;
  color: var(--c-accent);
  opacity: 0.3;
}
@media (max-width: 720px) {
  .claim-deco {
    display: none;
  }
}

/* ── 路径卡列表 ────────────────────────────────────────────── */
.options {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.option-button {
  position: relative;
  width: 100%;
  display: flex;
  align-items: stretch;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  text-align: left;
  cursor: pointer;
  font-family: var(--font-sans);
  /* 把 transform / box-shadow 一并列入,避免 scoped 规则
     覆盖掉 .cl-lift 的过渡声明 */
  transition:
    border-color 0.16s ease,
    background 0.16s ease,
    transform 0.16s ease,
    box-shadow 0.16s ease;
}
.option-button:hover:not(:disabled) {
  border-color: var(--c-border-default);
}
.option:not(.option--primary) .option-button:hover:not(:disabled) {
  background: var(--c-bg-sunken);
}

/* 统一 40px 圆角方块图标位:品牌蓝 color-mix 底 */
.option-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--c-accent) 9%, transparent);
  border: 1px solid color-mix(in srgb, var(--c-accent) 22%, transparent);
  color: var(--c-accent);
  flex-shrink: 0;
}

.option-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}
.option-title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--c-text-primary);
}
.option-desc {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  line-height: 1.5;
}
.option-hint {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  margin-top: var(--space-1);
}

.option-cta {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: var(--c-text-link);
  font-weight: 500;
  align-self: center;
  white-space: nowrap;
}
/* 卡片 hover 时箭头右移(过渡由 .cl-nudge 提供) */
.option-button:hover:not(:disabled) .option-arrow {
  transform: translateX(3px);
}

/* ── 推荐路径(主卡)视觉加权 ───────────────────────────────── */
.option--primary {
  margin-bottom: var(--space-2);
}
.option--primary .option-button {
  padding: var(--space-5) var(--space-6);
  border-color: color-mix(in srgb, var(--c-accent) 38%, var(--c-border-subtle));
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--c-accent) 7%, var(--c-bg-elevated)) 0%,
    var(--c-bg-elevated) 70%
  );
}
.option--primary .option-button:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--c-accent) 60%, var(--c-border-subtle));
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--c-accent) 10%, var(--c-bg-elevated)) 0%,
    var(--c-bg-elevated) 75%
  );
}
.option--primary .option-icon {
  background: color-mix(in srgb, var(--c-accent) 12%, transparent);
  border-color: color-mix(in srgb, var(--c-accent) 30%, transparent);
}
.option--primary .option-title {
  font-size: var(--text-lg);
}
.option--primary .option-cta {
  font-size: var(--text-sm);
}

/* 「推荐」小标签:骑在主卡右上角边框上 */
.option-flag {
  position: absolute;
  top: -10px;
  right: var(--space-5);
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 2px var(--space-3);
  font-size: var(--c-text-2xs);
  font-weight: 600;
  letter-spacing: 0.02em;
  line-height: 1.6;
  color: var(--c-accent);
  background: var(--c-bg-elevated);
  border: 1px solid color-mix(in srgb, var(--c-accent) 45%, transparent);
  border-radius: var(--radius-full);
  box-shadow: var(--shadow-sm);
}
.option-flag-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--c-accent);
}

@media (prefers-reduced-motion: reduce) {
  .option-button:hover:not(:disabled) .option-arrow {
    transform: none;
  }
}
</style>
