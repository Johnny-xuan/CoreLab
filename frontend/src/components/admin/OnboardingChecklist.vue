<script setup lang="ts">
/**
 * OnboardingChecklist — Phase M M-2.6 first-run guide for the lab admin.
 *
 * Lives at the top of Dashboard.vue (and LabOverview.vue) when the lab
 * is freshly bootstrapped. Each step is a row with a state dot, a
 * label, a short hint, and a CTA. As soon as the backend
 * ``/admin/onboarding-status`` reports a step as done, the dot turns
 * green and the row dims. When every step is done the panel collapses
 * itself away — Dashboard reverts to its normal data view.
 *
 * The component polls the status endpoint every 5s while mounted so the
 * "agent online" / "first reservation" rows tick on their own without
 * the user having to refresh the page.
 */

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NButton, NIcon } from 'naive-ui';
import { Check, ChevronRight, Circle, Compass, Sparkles } from 'lucide-vue-next';

import { getOnboardingStatus, type OnboardingStatus } from '@/api/labOverview';
import { TOUR_SEEN_KEY, resetTourSeen, startOnboardingTour } from '@/composables/useOnboardingTour';

const route = useRoute();
const router = useRouter();

// Phase M demo aid — `?onboarding=preview` forces the checklist to
// render even when the lab is fully bootstrapped (so you can show off
// the panel without resetting the DB). The data still comes from the
// live endpoint, only the visibility guard is bypassed.
const previewMode = computed(() => route.query.onboarding === 'preview');

// Phase M M-4 — `?tour=preview` resets the "seen" flag and force-starts
// the Driver.js spotlight tour. Useful for演示 + screenshots without
// having to clear localStorage by hand.
const tourPreviewMode = computed(() => route.query.tour === 'preview');

const status = ref<OnboardingStatus | null>(null);
const collapsed = ref(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;

async function refresh(): Promise<void> {
  try {
    status.value = await getOnboardingStatus();
  } catch {
    // best-effort poll — silent on network blips
  }
}

onMounted(() => {
  void refresh();
  pollTimer = setInterval(refresh, 5000);
});
onUnmounted(() => {
  if (pollTimer !== null) clearInterval(pollTimer);
});

// When everything turns true, collapse the panel and stop polling so we
// don't keep hammering the endpoint after the user is past onboarding.
watch(
  () => status.value?.all_done ?? false,
  (done) => {
    if (done && pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  },
);

interface Step {
  key: string;
  title: string;
  hint: string;
  done: boolean;
  cta?: { label: string; onClick: () => void };
}

const steps = computed<Step[]>(() => {
  const s = status.value;
  // Until first poll lands, treat everything as pending — UI still
  // looks like a checklist instead of jumping in once data arrives.
  const sc = s?.servers_count ?? 0;
  const oc = s?.online_servers_count ?? 0;
  const uc = s?.users_count ?? 0;
  const lc = s?.links_count ?? 0;
  const rc = s?.reservations_count ?? 0;

  return [
    {
      key: 'lab',
      title: '创建你的 Lab',
      hint: '你已经在这里了 — Lab 和管理员账号已在初始化向导中创建。',
      done: true,
    },
    {
      key: 'server',
      title: '添加第一台服务器',
      hint:
        sc > 0 ? `已注册 ${sc} 台服务器。` : '为一台 GPU 主机生成接入令牌,安装命令片段就会出现。',
      done: sc > 0,
      cta: {
        label: sc > 0 ? '管理服务器' : '添加服务器',
        onClick: () => router.push({ name: 'servers' }),
      },
    },
    {
      key: 'agent',
      title: '让第一个 agent 上线',
      hint:
        oc > 0
          ? `已有 ${oc} 个 agent 上报。`
          : '以 root 身份在 GPU 主机上粘贴安装命令片段。agent 连上后下方的状态点会变绿。',
      done: oc > 0,
    },
    {
      key: 'user',
      title: '邀请你的第一个用户',
      hint: uc > 1 ? `除初始管理员外已邀请 ${uc - 1} 个用户。` : '在「用户」页面发送一个激活链接。',
      done: uc > 1,
      cta: {
        label: uc > 1 ? '查看用户' : '邀请用户',
        onClick: () => router.push({ name: 'admin-users' }),
      },
    },
    {
      key: 'link',
      title: '关联第一个 Linux 账号',
      hint:
        lc > 0
          ? `已有 ${lc} 个有效关联。`
          : '用户在服务器上认领一个 Linux 账号 — 通过 SSH 验证、密码 PAM,或由管理员直接声明。',
      done: lc > 0,
    },
    {
      key: 'reservation',
      title: '创建第一个预约',
      hint:
        rc > 0
          ? `Lab 内已有 ${rc} 个预约。`
          : '用户预约一个 GPU 时段。完成这一步后清单会收起,仪表盘恢复为正常的数据视图。',
      done: rc > 0,
    },
  ];
});

const completedCount = computed(() => steps.value.filter((s) => s.done).length);
const totalCount = computed(() => steps.value.length);
const progressPct = computed(() =>
  totalCount.value === 0 ? 0 : Math.round((completedCount.value / totalCount.value) * 100),
);

const shouldShow = computed(() => {
  if (status.value === null) return false; // pre-load: nothing
  if (collapsed.value) return false;
  if (previewMode.value) return true; // demo override
  if (tourPreviewMode.value) return true; // tour replay needs the panel visible
  return !status.value.all_done;
});

// Phase M M-4 — auto-start tour rules:
//   * ?tour=preview        → always replay (resets the seen flag first)
//   * lab is fresh (servers_count === 0) and user has never seen it
// The Tour button on the header always replays manually.
const autoStartFired = ref(false);
watch([status, shouldShow], ([s, visible]) => {
  if (autoStartFired.value) return;
  if (!s || !visible) return;
  const isFreshLab = s.servers_count === 0;
  const haveSeen = localStorage.getItem(TOUR_SEEN_KEY) === '1';
  if (tourPreviewMode.value) {
    autoStartFired.value = true;
    resetTourSeen();
    void nextTick(() => startOnboardingTour({ status: s, force: true }));
    return;
  }
  if (isFreshLab && !haveSeen) {
    autoStartFired.value = true;
    void nextTick(() => startOnboardingTour({ status: s, force: false }));
  }
});

function manuallyStartTour(): void {
  startOnboardingTour({ status: status.value, force: true });
}
</script>

<template>
  <section v-if="shouldShow" class="checklist" data-tour="checklist">
    <header class="hdr">
      <div class="hdr-left">
        <NIcon :size="14" class="hdr-icon"><Sparkles :size="14" /></NIcon>
        <span class="hdr-title">开始使用 CoreLab</span>
        <span class="hdr-progress mono">
          {{ completedCount }} / {{ totalCount }} · {{ progressPct }}%
        </span>
      </div>
      <div class="hdr-actions">
        <NButton text size="tiny" @click="manuallyStartTour">
          <template #icon>
            <NIcon><Compass :size="12" /></NIcon>
          </template>
          导览
        </NButton>
        <NButton text size="tiny" @click="collapsed = true">隐藏</NButton>
      </div>
    </header>

    <div class="progress-bar">
      <span class="progress-bar-fill" :style="{ width: `${progressPct}%` }" />
    </div>

    <ol class="steps">
      <li
        v-for="s in steps"
        :key="s.key"
        :class="['step', { done: s.done }]"
        :data-tour-step="s.key"
      >
        <span class="step-dot">
          <NIcon v-if="s.done" :size="12" class="dot-icon dot-icon-done">
            <Check :size="12" />
          </NIcon>
          <NIcon v-else :size="12" class="dot-icon dot-icon-pending">
            <Circle :size="12" />
          </NIcon>
        </span>
        <span class="step-body">
          <span class="step-title">{{ s.title }}</span>
          <span class="step-hint dim">{{ s.hint }}</span>
        </span>
        <span class="step-cta">
          <NButton
            v-if="s.cta && !s.done"
            size="small"
            type="primary"
            :data-tour-cta="s.key"
            @click="s.cta.onClick"
          >
            {{ s.cta.label }}
            <template #icon>
              <NIcon><ChevronRight :size="14" /></NIcon>
            </template>
          </NButton>
          <NButton
            v-else-if="s.cta && s.done"
            text
            size="small"
            :data-tour-cta="s.key"
            @click="s.cta.onClick"
          >
            {{ s.cta.label }}
          </NButton>
        </span>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.checklist {
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-4) var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.hdr {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.hdr-left {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.hdr-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
.hdr-icon {
  color: var(--c-accent);
}
.hdr-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text-primary);
}
.hdr-progress {
  font-size: 11px;
  color: var(--c-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.progress-bar {
  height: 4px;
  background: var(--c-border-subtle);
  border-radius: 2px;
  overflow: hidden;
}
.progress-bar-fill {
  display: block;
  height: 100%;
  background: var(--c-accent);
  transition: width 0.3s ease;
}

.steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.step {
  display: grid;
  grid-template-columns: 20px 1fr auto;
  gap: var(--space-3);
  align-items: center;
  padding: var(--space-2) var(--space-1);
  border-radius: var(--radius-sm);
}
.step.done {
  opacity: 0.65;
}

.step-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-default);
}
.step.done .step-dot {
  background: var(--c-success);
  border-color: var(--c-success);
}
.dot-icon-done {
  color: var(--c-text-inverse);
}
.dot-icon-pending {
  color: var(--c-text-tertiary);
}

.step-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.step-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--c-text-primary);
}
.step-hint {
  font-size: 12px;
  line-height: 1.4;
}

.step-cta {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
}

.dim {
  color: var(--c-text-tertiary);
}
.mono {
  font-family: var(--font-mono);
}
</style>
