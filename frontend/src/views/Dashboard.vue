<script setup lang="ts">
/**
 * Dashboard — Vercel-inspired overview landing.
 *
 * Layout mirrors the Vercel project dashboard:
 *   - Page header (title + greeting subtitle)
 *   - Two-column grid: left = Usage rollup (last 30 days), right = Quick actions
 *   - Below: Recent activity (last few audit-log rows)
 *
 * Usage + recent activity hydrate from existing read endpoints. Errors are
 * swallowed silently — this page should never crash on a partial backend.
 */
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { NButton, NCard, NTag } from 'naive-ui';
import {
  Activity,
  Bell,
  Calendar,
  ChevronRight,
  KeyRound,
  Server as ServerIcon,
} from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import OnboardingChecklist from '@/components/admin/OnboardingChecklist.vue';
import { useAuthStore } from '@/stores/auth';
import { useWorkspaceStore } from '@/stores/workspace';
import { getMyUsage, type UsageResponse } from '@/api/usage';
import { listAuditLogs, type AuditLogRead } from '@/api/auditLogs';
import { listAlerts, type AlertEventRead } from '@/api/alerts';
import type { RouteLocationRaw } from 'vue-router';

const auth = useAuthStore();
const router = useRouter();
const workspace = useWorkspaceStore();

const usage = ref<UsageResponse | null>(null);
const recentAudits = ref<AuditLogRead[]>([]);
const openAlerts = ref<AlertEventRead[]>([]);
const loadingUsage = ref(false);
const loadingActivity = ref(false);
const workspaceLoaded = ref(false);

function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

onMounted(async () => {
  loadingUsage.value = true;
  loadingActivity.value = true;
  // Run independently so a single 404 doesn't block the others.
  void getMyUsage(currentMonth())
    .then((u) => {
      usage.value = u;
    })
    .catch(() => undefined)
    .finally(() => {
      loadingUsage.value = false;
    });
  void listAuditLogs({ page: 1, size: 5 })
    .then((r) => {
      recentAudits.value = r.items;
    })
    .catch(() => undefined);
  void listAlerts({ limit: 50 })
    .then((items) => {
      openAlerts.value = items.filter((a) => !a.is_resolved);
    })
    .catch(() => undefined)
    .finally(() => {
      loadingActivity.value = false;
    });
  // Drives the first-run onboarding card: a brand-new user has zero links.
  void workspace
    .refresh()
    .catch(() => undefined)
    .finally(() => {
      workspaceLoaded.value = true;
    });
});

// Show the user-facing onboarding card only once we know they truly have no
// linked Linux account (and they aren't a lab_admin, who gets the lab setup
// checklist instead).
const showUserOnboarding = computed(
  () =>
    workspaceLoaded.value && auth.user?.role !== 'lab_admin' && workspace.workspaces.length === 0,
);

interface QuickAction {
  key: string;
  title: string;
  desc: string;
  icon: typeof ServerIcon;
  route: RouteLocationRaw;
}

const quickActions = computed<QuickAction[]>(() => {
  const currentPaId = workspace.current?.pa.id ?? null;
  const reserveRoute: RouteLocationRaw =
    currentPaId !== null
      ? { name: 'pa-reserve', params: { pa_id: currentPaId } }
      : { name: 'claim-account' };
  const reserveTitle = currentPaId !== null ? '预约 GPU' : '关联 Linux 账号';
  const reserveDesc =
    currentPaId !== null ? '打开网格,选 GPU + 时段' : '预约前先关联一个 Linux 账号';
  const reservationsRoute: RouteLocationRaw =
    currentPaId !== null
      ? { name: 'pa-reservations', params: { pa_id: currentPaId } }
      : { name: 'all-reservations' };
  const base: QuickAction[] = [
    {
      key: 'reservations',
      title: '我的预约',
      desc: '查看当前生效的 GPU 预约',
      icon: Calendar,
      route: reservationsRoute,
    },
    {
      key: 'reserve',
      title: reserveTitle,
      desc: reserveDesc,
      icon: ServerIcon,
      route: reserveRoute,
    },
  ];
  // Dashboard quick actions stay personal-scoped on purpose. Lab-admin
  // governance (audit log, user management) lives in the 管理区 sidebar —
  // duplicating it here blurred Phase L's 观察 ≠ 治理 / personal ≠ admin
  // separation, so those two cards were removed.
  return base;
});

// Pure-display: time-of-day greeting for the welcome hero. No business
// logic — just picks a phrase from the current hour.
const greeting = computed(() => {
  const h = new Date().getHours();
  if (h >= 5 && h < 11) return '早上好';
  if (h >= 11 && h < 13) return '中午好';
  if (h >= 13 && h < 18) return '下午好';
  if (h >= 18 && h < 23) return '晚上好';
  return '夜深了';
});

const usageGpuHours = computed(() =>
  usage.value ? usage.value.gpu_hours_used.toFixed(2) : '0.00',
);
const usageReservations = computed(() => usage.value?.reservation_count ?? 0);
const usageAlerts = computed(() => usage.value?.alerts_received ?? 0);
const usageCompliance = computed(() => usage.value?.compliance_violations ?? 0);

function actionTimeAgo(iso: string | null): string {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

async function navigate(target: RouteLocationRaw): Promise<void> {
  await router.push(target);
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="hero cl-enter">
        <div class="hero-grid" aria-hidden="true" />
        <div class="hero-main">
          <div class="hero-eyebrow">
            <span class="pulse-wrap">
              <span class="pulse-dot cl-pulse" aria-hidden="true" />
            </span>
            <span class="pulse-text">实验室在线</span>
          </div>
          <h1 class="hero-title">
            {{ greeting }},<span class="hero-name">{{
              auth.user?.display_name ?? auth.user?.username
            }}</span>
          </h1>
          <p class="hero-sub">
            欢迎回到 CoreLab 控制台
            <span class="role-chip">{{ auth.user?.role }}</span>
          </p>
        </div>
      </header>

      <!-- Phase M M-2.7 — first-run onboarding checklist; auto-hides
           once every step has at least one row (so this only shows up
           on a fresh lab). lab_admin only — the endpoint is gated. -->
      <OnboardingChecklist v-if="auth.user?.role === 'lab_admin'" />

      <!-- First-run onboarding for a brand-new (non-admin) user who has not
           linked any Linux account yet. Auto-hides once they have one. -->
      <NCard
        v-if="showUserOnboarding"
        :bordered="true"
        class="card onboarding-card cl-enter"
        style="--cl-delay: 0.06s"
      >
        <div class="ob-head">
          <span class="ob-icon"><KeyRound :size="20" :stroke-width="1.75" /></span>
          <div>
            <div class="ob-title">欢迎使用 CoreLab 👋</div>
            <div class="ob-sub">还差一步,就能开始预约 GPU 了。</div>
          </div>
        </div>
        <ol class="ob-steps">
          <li class="ob-step ob-step-active">
            <span class="ob-step-no">1</span>
            <span class="ob-step-body">
              <span class="ob-step-title">关联一个 Linux 账号</span>
              <span class="ob-step-desc">
                让 CoreLab 知道你在目标服务器上对应哪个 Linux 用户 —— 这是预约 GPU 的前提。
              </span>
            </span>
            <NButton size="small" type="primary" @click="navigate({ name: 'claim-account' })">
              去关联
            </NButton>
          </li>
          <li class="ob-step">
            <span class="ob-step-no">2</span>
            <span class="ob-step-body">
              <span class="ob-step-title">预约 GPU</span>
              <span class="ob-step-desc">关联完成后,在网格里选好 GPU 和时段即可开跑。</span>
            </span>
          </li>
        </ol>
      </NCard>

      <div class="grid-two">
        <!-- Usage card ─────────────────────────────────────────────────── -->
        <NCard :bordered="true" class="card usage-card cl-enter" style="--cl-delay: 0.12s">
          <template #header>
            <div class="card-head">
              <span class="card-title">本月使用</span>
              <span class="filter-chip">
                <Activity :size="12" :stroke-width="2" />
                近 30 天
              </span>
            </div>
          </template>
          <ul class="usage-list">
            <li class="usage-row">
              <span class="dot dot-info cl-pulse" aria-hidden="true" />
              <span class="usage-label">GPU 时长(已用)</span>
              <span class="usage-value mono tabular">{{ usageGpuHours }}</span>
            </li>
            <li class="usage-row">
              <span class="dot dot-info cl-pulse" aria-hidden="true" />
              <span class="usage-label">活跃预约数</span>
              <span class="usage-value mono tabular">{{ usageReservations }}</span>
            </li>
            <li class="usage-row">
              <span class="dot dot-info cl-pulse" aria-hidden="true" />
              <span class="usage-label">告警(本月)</span>
              <span class="usage-value mono tabular">
                {{ usageAlerts }}
              </span>
            </li>
            <li class="usage-row">
              <span class="dot dot-warn" aria-hidden="true" />
              <span class="usage-label">合规违规</span>
              <span class="usage-value mono tabular">
                {{ usageCompliance }}
              </span>
            </li>
            <li class="usage-row">
              <span class="dot dot-info cl-pulse" aria-hidden="true" />
              <span class="usage-label">未解决告警</span>
              <span class="usage-value mono tabular">
                {{ openAlerts.length === 0 ? '—' : openAlerts.length }}
              </span>
            </li>
          </ul>
          <p v-if="!loadingUsage && usage === null" class="card-footer-note">
            本月用量暂时不可用,稍后刷新即可。
          </p>
        </NCard>

        <!-- Quick actions ──────────────────────────────────────────────── -->
        <NCard :bordered="true" class="card quick-card cl-enter" style="--cl-delay: 0.18s">
          <template #header>
            <div class="card-head">
              <span class="card-title">工作台</span>
              <span class="filter-chip">快捷操作</span>
            </div>
          </template>
          <div class="quick-list">
            <button
              v-for="action in quickActions"
              :key="action.key"
              class="quick-item cl-nudge"
              type="button"
              @click="navigate(action.route)"
            >
              <span class="quick-icon">
                <component :is="action.icon" :size="18" :stroke-width="1.75" />
              </span>
              <span class="quick-body">
                <span class="quick-title">{{ action.title }}</span>
                <span class="quick-desc">{{ action.desc }}</span>
              </span>
              <ChevronRight :size="14" :stroke-width="2" class="quick-caret" />
            </button>
          </div>
          <!-- 纯静态提示条,用于把新用户带向账号关联。 -->
          <p class="quick-tip">
            <span class="quick-tip-label">TIP</span>
            <span class="quick-tip-text">预约 GPU 前,需先关联一个 Linux 账号</span>
          </p>
        </NCard>
      </div>

      <!-- Recent activity ───────────────────────────────────────────────── -->
      <NCard :bordered="true" class="card activity-card cl-enter" style="--cl-delay: 0.24s">
        <template #header>
          <div class="card-head">
            <span class="card-title">近期审计事件</span>
            <RouterLink to="/lab/audit" class="card-link">
              查看全部
              <ChevronRight :size="14" :stroke-width="2" />
            </RouterLink>
          </div>
        </template>
        <ul v-if="recentAudits.length > 0" class="activity-list">
          <li v-for="row in recentAudits" :key="row.id" class="activity-row cl-nudge">
            <span class="activity-time mono tabular">
              {{ actionTimeAgo(row.created_at) }}
            </span>
            <span class="activity-actor">
              {{ row.actor?.username ?? 'system' }}
            </span>
            <NTag size="small" :bordered="false" type="default" class="activity-action">
              <code>{{ row.action }}</code>
            </NTag>
            <span class="activity-target">
              {{ row.target_type ?? '—' }}
              <span v-if="row.target_id !== null" class="mono">#{{ row.target_id }}</span>
            </span>
            <NTag
              size="small"
              :bordered="false"
              :type="row.result === 'ok' ? 'success' : 'error'"
              class="activity-result"
            >
              {{ row.result }}
            </NTag>
          </li>
        </ul>
        <div v-else-if="loadingActivity" class="activity-empty">
          <Bell :size="14" :stroke-width="1.75" />
          <span>加载中…</span>
        </div>
        <div v-else class="activity-empty">
          <Bell :size="14" :stroke-width="1.75" />
          <span>暂无活动</span>
        </div>
      </NCard>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  padding: var(--space-6) var(--space-8);
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

/* ── Welcome hero (门面 / 实验室在线脉搏) ─────────────────────────────── */
.hero {
  position: relative;
  overflow: hidden;
  padding: var(--space-6) var(--space-8);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  background:
    radial-gradient(
      130% 150% at 4% 0%,
      color-mix(in srgb, var(--c-accent) 9%, transparent),
      transparent 55%
    ),
    var(--c-bg-elevated);
}
/* 暗色:品牌蓝染底像屏幕漏光,撤掉 —— 深色面板回归纯色,
 * "实验室在线"的绿色脉搏就是这块卡片的全部光源。 */
[data-theme='dark'] .hero {
  background: var(--c-bg-elevated);
}
/* 网格纹理已撤——全站只留 AdminDomain 一处用纹理,避免每页同款装饰。 */
.hero-grid {
  display: none;
}
.hero-main {
  position: relative;
  z-index: 1;
  min-width: 0;
}
.hero-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.pulse-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--c-success);
  /* tint the breathing ring green to read as "online / healthy". */
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 55%, transparent);
}
.pulse-text {
  font-size: var(--text-xs);
  font-weight: 500;
  letter-spacing: var(--tracking-snug);
  color: var(--c-success);
}
.hero-title {
  font-size: var(--text-2xl);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  margin: 0;
  color: var(--c-text-primary);
}
.hero-name {
  color: var(--c-text-primary);
}
.hero-sub {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  margin: var(--space-1) 0 0;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
.role-chip {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--c-bg-sunken);
  color: var(--c-text-secondary);
  border: 1px solid var(--c-border-subtle);
}

.grid-two {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-4);
  /* 两卡同 track 拉伸,保证底边对齐。 */
  align-items: stretch;
}
@media (max-width: 900px) {
  .grid-two {
    grid-template-columns: 1fr;
  }
}

.card {
  background: var(--c-bg-elevated);
}
.card :deep(.n-card__content) {
  padding: var(--space-5);
}
.card :deep(.n-card-header) {
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--c-border-subtle);
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.card-title {
  font-size: var(--text-base);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  color: var(--c-text-primary);
}
.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  height: 24px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-md);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  font-size: var(--text-2xs);
  color: var(--c-text-secondary);
}
.card-link {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: var(--text-xs);
  color: var(--c-text-link);
  text-decoration: none;
}
.card-link:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}

/* ── Usage list ───────────────────────────────────────────────────────── */
.usage-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.usage-row {
  display: grid;
  grid-template-columns: 8px 1fr auto;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--c-border-subtle);
}
.usage-row:last-child {
  border-bottom: none;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--c-info);
}
.dot-info {
  /* soft accent-blue breathing ring; kept restrained. */
  --cl-pulse-color: color-mix(in srgb, var(--c-info) 35%, transparent);
}
.dot-warn {
  background: var(--c-warning);
}
.usage-label {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
.usage-value {
  font-size: var(--text-sm);
  color: var(--c-text-primary);
  font-weight: 500;
}
.card-footer-note {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}

/* ── First-run onboarding card ────────────────────────────────────────── */
.onboarding-card {
  border-color: var(--c-border-default);
}
.ob-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
.ob-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  color: var(--c-accent);
  flex-shrink: 0;
}
.ob-title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--c-text-primary);
}
.ob-sub {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  margin-top: 2px;
}
.ob-steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.ob-step {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  background: var(--c-bg-elevated);
}
.ob-step-active {
  border-color: var(--c-accent);
  background: var(--c-bg-sunken);
}
.ob-step-no {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--c-text-secondary);
  flex-shrink: 0;
}
.ob-step-active .ob-step-no {
  background: var(--c-accent);
  border-color: var(--c-accent);
  color: #fff;
}
.ob-step-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}
.ob-step-title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--c-text-primary);
}
.ob-step-desc {
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  line-height: 1.5;
}

/* ── Quick actions ────────────────────────────────────────────────────── */
/* 卡内纵向布局:操作列表吃满剩余高度,提示条贴底 — 与左侧用量卡底边对齐。 */
.quick-card {
  display: flex;
  flex-direction: column;
}
.quick-card :deep(.n-card__content) {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.quick-list {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}
.quick-item {
  /* 每行均分剩余高度,避免列表下方出现整块空白。 */
  flex: 1 1 0;
  min-height: 56px;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-3) var(--space-2);
  background: transparent;
  border: none;
  border-radius: var(--radius-md);
  text-align: left;
  cursor: pointer;
  font-family: var(--font-sans);
}
/* hairline 分隔 — 行间细线,首行不画。 */
.quick-item + .quick-item {
  border-top: 1px solid var(--c-border-subtle);
}
.quick-item:hover {
  background: var(--c-bg-sunken);
}
.quick-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  color: var(--c-text-primary);
  flex-shrink: 0;
}
.quick-item:hover .quick-icon {
  background: var(--c-bg-elevated);
}
.quick-item:hover .quick-caret {
  color: var(--c-text-secondary);
}
.quick-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.quick-title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--c-text-primary);
}
.quick-desc {
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}
.quick-caret {
  color: var(--c-text-tertiary);
  flex-shrink: 0;
}
/* 底部静态提示条 — 低调 mono 小字,纯展示。 */
.quick-tip {
  margin: var(--space-3) 0 0;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px dashed var(--c-border-subtle);
  border-radius: var(--radius-md);
  background: var(--c-bg-sunken);
}
.quick-tip-label {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 500;
  letter-spacing: var(--tracking-caps);
  color: var(--c-text-tertiary);
  flex-shrink: 0;
}
.quick-tip-text {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  min-width: 0;
}

/* ── Activity ─────────────────────────────────────────────────────────── */
.activity-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.activity-row {
  display: grid;
  grid-template-columns: 92px 110px auto 1fr auto;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--c-border-subtle);
  font-size: var(--text-sm);
}
.activity-row:last-child {
  border-bottom: none;
}
.activity-row:hover {
  background: var(--c-bg-sunken);
}
.activity-time {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.activity-actor {
  color: var(--c-text-primary);
  font-weight: 500;
}
.activity-action :deep(code) {
  background: transparent;
  border: none;
  padding: 0;
  font-size: var(--text-xs);
}
.activity-target {
  color: var(--c-text-secondary);
  font-size: var(--text-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-empty {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) 0;
  color: var(--c-text-tertiary);
  font-size: var(--text-sm);
}
</style>
