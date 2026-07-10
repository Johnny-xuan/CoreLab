<script setup lang="ts">
/**
 * LabOverview — Phase L L-5 entity-detail page for the lab itself.
 *
 * Lab_admin lands here when they drill into 管理区(lab). Aggregates the
 * "is today OK?" signals: server status, who's running, pending review
 * queue, this week's usage ranking, lateral SSH-key surface, recent
 * lab-wide events. Every chunk drills into the corresponding detail /
 * list page.
 */

import { computed, h, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NEmpty,
  NIcon,
  NSpin,
  type DataTableColumns,
} from 'naive-ui';
import { Activity, LayoutDashboard, RefreshCw } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import OnboardingChecklist from '@/components/admin/OnboardingChecklist.vue';
import { listServers, type ServerRead } from '@/api/servers';
import { listReservations, type ReservationRead } from '@/api/reservations';
import { listPending, type AccountLinkRequestRead } from '@/api/accountLinkRequests';
import { listAlerts, type AlertEventRead } from '@/api/alerts';
import { listAuditLogs, type AuditListResponse, type AuditLogRead } from '@/api/auditLogs';
import {
  getLabUsage7d,
  getSecurityMap,
  type LabUsageItem,
  type SecurityGrantEntry,
  type SecurityKeyEntry,
} from '@/api/labOverview';
import { extractDetail } from '@/utils/extractDetail';

const router = useRouter();

const servers = ref<ServerRead[]>([]);
const upcomingReservations = ref<ReservationRead[]>([]);
const pending = ref<AccountLinkRequestRead[]>([]);
const alerts = ref<AlertEventRead[]>([]);
const audit = ref<AuditLogRead[]>([]);
const usage = ref<LabUsageItem[]>([]);
const usageWindow = ref<{ start: string; end: string; total: number } | null>(null);
const securityKeys = ref<SecurityKeyEntry[]>([]);
const securityGrants = ref<SecurityGrantEntry[]>([]);
const securityTotals = ref<{ keys: number; grants: number }>({ keys: 0, grants: 0 });

const loading = ref(false);
const loadError = ref<string | null>(null);

async function loadAll(): Promise<void> {
  loading.value = true;
  loadError.value = null;
  try {
    const now = new Date();
    const in6h = new Date(now.getTime() + 6 * 3600_000);
    const [serversR, resR, pendingR, alertsR, auditR, usageR, secR] = await Promise.all([
      listServers(),
      listReservations({
        starts_after: now.toISOString(),
        ends_before: in6h.toISOString(),
        status_in: ['scheduled', 'active'],
      }).catch(() => [] as ReservationRead[]),
      listPending().catch(() => [] as AccountLinkRequestRead[]),
      listAlerts().catch(() => [] as AlertEventRead[]),
      listAuditLogs({ page: 1, size: 10 }).catch(
        (): AuditListResponse => ({
          items: [],
          page: 1,
          size: 10,
          total: 0,
          total_pages: 0,
        }),
      ),
      getLabUsage7d().catch(() => null),
      getSecurityMap().catch(() => null),
    ]);
    servers.value = serversR;
    upcomingReservations.value = resR;
    pending.value = pendingR;
    // The list endpoint doesn't honour a `resolved` filter, so keep only
    // unresolved alerts client-side — the stat card is labelled 未解决.
    alerts.value = alertsR.filter((a) => !a.is_resolved);
    audit.value = auditR.items;
    if (usageR) {
      usage.value = usageR.items;
      usageWindow.value = {
        start: usageR.window_start,
        end: usageR.window_end,
        total: usageR.total_hours,
      };
    }
    if (secR) {
      securityKeys.value = secR.keys.slice(0, 8);
      securityGrants.value = secR.grants.slice(0, 5);
      securityTotals.value = { keys: secR.total_active_keys, grants: secR.total_active_grants };
    }
  } catch (err) {
    loadError.value = extractDetail(err, '加载 lab 概览失败');
  } finally {
    loading.value = false;
  }
}

onMounted(loadAll);

const serversOnline = computed(() => servers.value.filter((s) => s.status === 'online').length);
const activeUsersNow = computed(() => {
  const usernames = new Set<string>();
  for (const r of upcomingReservations.value) {
    if (r.status === 'active') usernames.add(`u${r.user_id}`);
  }
  return usernames.size;
});

const usageMax = computed(() => Math.max(...usage.value.map((u) => u.hours), 1));

// === Hero summary (presentation-only derivations) ===
const totalServers = computed(() => servers.value.length);
const pendingCount = computed(() => pending.value.length);
const alertCount = computed(() => alerts.value.length);
const hasAlerts = computed(() => alertCount.value > 0);

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { hour: '2-digit', minute: '2-digit' });
}

// === NOW & NEXT 6h table ===
const nowNextColumns = computed<DataTableColumns<ReservationRead>>(() => [
  {
    title: '时间',
    key: 'when',
    width: 150,
    render: (row) =>
      h('span', { class: 'mono dim' }, `${fmtTime(row.start_at)} → ${fmtTime(row.end_at)}`),
  },
  {
    title: 'GPU',
    key: 'gpu',
    width: 130,
    render: (row) =>
      h('span', { class: 'mono' }, row.gpu_id !== null ? `gpu-${row.gpu_id}` : 'cron'),
  },
  {
    title: '用户',
    key: 'user',
    render: (row) => h('span', { class: 'mono dim' }, `u${row.user_id}`),
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) =>
      h('span', { class: 'status-cell' }, [
        h('span', {
          class: row.status === 'active' ? 'dot dot-ok cl-pulse' : 'dot dot-mute',
          style:
            row.status === 'active'
              ? '--cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent)'
              : undefined,
        }),
        h('span', { class: 'status-text' }, row.status),
      ]),
  },
]);

// === WORK QUEUE (pending requests) ===
const workQueueColumns = computed<DataTableColumns<AccountLinkRequestRead>>(() => [
  {
    title: '申请',
    key: 'id',
    width: 60,
    render: (row) => h('span', { class: 'mono dim' }, `#${row.id}`),
  },
  {
    title: '用户',
    key: 'requester',
    render: (row) => h('span', { class: 'mono' }, `u${row.requester_user_id}`),
  },
  {
    title: 'PA',
    key: 'linux_user',
    render: (row) => h('span', { class: 'mono dim' }, `→ pa-${row.physical_account_id}`),
  },
  {
    title: '',
    key: 'action',
    width: 90,
    render: () =>
      h(NButton, { size: 'tiny', text: true, type: 'primary' }, { default: () => '审核' }),
  },
]);

// === RECENT EVENTS ===
const recentEventsColumns = computed<DataTableColumns<AuditLogRead>>(() => [
  {
    title: '时间',
    key: 'created_at',
    width: 130,
    render: (row) => h('span', { class: 'mono dim' }, fmtDateTime(row.created_at)),
  },
  {
    title: '操作者',
    key: 'actor',
    width: 100,
    render: (row) => h('span', { class: 'mono dim' }, row.actor?.username ?? '—'),
  },
  {
    title: '操作',
    key: 'action',
    render: (row) => h('span', { class: 'mono' }, row.action),
  },
  {
    title: '结果',
    key: 'result',
    width: 70,
    render: (row) =>
      h('span', { class: 'status-cell' }, [
        h('span', {
          class:
            row.result === 'ok'
              ? 'dot dot-ok'
              : row.result === 'denied'
                ? 'dot dot-warn'
                : 'dot dot-bad',
        }),
      ]),
  },
]);

function goPending(): void {
  router.push({ name: 'account-link-requests' });
}
function goAlerts(): void {
  router.push({ name: 'lab-alerts' });
}
function goAudit(): void {
  router.push({ name: 'lab-audit' });
}
function goServers(): void {
  router.push({ name: 'servers' });
}
function goUser(id: number): void {
  router.push({ name: 'admin-user-detail', params: { id } });
}
function goManageAdmins(): void {
  // Server-admin delegations are managed per-server (Servers → a server →
  // Admins tab). The old cross-server AdminServerAdmins page was a redundant
  // stub and was removed; this footer link now lands on the Servers list.
  router.push({ name: 'servers' });
}
</script>

<template>
  <AppLayout>
    <div class="console">
      <header class="hero" :class="{ alerting: hasAlerts }">
        <div class="hero-left">
          <div class="gauge" aria-hidden="true">
            <span class="gauge-core">
              <NIcon :size="22"><LayoutDashboard :size="22" /></NIcon>
            </span>
          </div>
          <div class="hero-text">
            <h1 class="hero-title">实验室概览</h1>
            <div class="hero-sub">全实验室运行态势一览 —— 服务器、用户、预约与告警的实时观测台</div>
            <div class="hero-chips">
              <span class="chip">
                <NIcon :size="11"><Activity /></NIcon>
                <span class="chip-num">{{ serversOnline }}/{{ totalServers }}</span> 台在线
              </span>
              <span class="chip">
                <span class="chip-num">{{ activeUsersNow }}</span> 用户活跃
              </span>
              <span class="chip">
                <span class="chip-num">{{ upcomingReservations.length }}</span> 即将预约
              </span>
              <span v-if="pendingCount" class="chip chip-warn">
                <span class="chip-num">{{ pendingCount }}</span> 待审核
              </span>
              <span class="chip" :class="{ 'chip-bad': hasAlerts, 'chip-ok': !hasAlerts }">
                <span class="chip-dot" :class="{ 'cl-pulse': hasAlerts }" />
                {{ hasAlerts ? `${alertCount} 未解决告警` : '无未解决告警' }}
              </span>
            </div>
          </div>
        </div>
        <NButton size="small" :loading="loading" class="refresh-btn" @click="loadAll">
          <template #icon
            ><NIcon><RefreshCw :size="14" /></NIcon
          ></template>
          刷新
        </NButton>
      </header>

      <!-- Phase M M-2.7 — onboarding checklist; auto-hides when done. -->
      <OnboardingChecklist />

      <NSpin v-if="loading && !servers.length" size="small" />
      <NAlert v-else-if="loadError" type="error" :title="loadError" />

      <template v-else>
        <!-- 4 STAT CARDS -->
        <div class="stat-row">
          <div
            class="stat-card clickable cl-enter cl-lift"
            style="--cl-delay: 0s"
            @click="goServers"
          >
            <div class="stat-label">服务器</div>
            <div class="stat-value mono">{{ serversOnline }} / {{ servers.length }}</div>
            <div class="stat-sub dim">在线</div>
          </div>
          <div class="stat-card cl-enter cl-lift" style="--cl-delay: 0.08s">
            <div class="stat-label">活跃用户</div>
            <div class="stat-value mono">{{ activeUsersNow }}</div>
            <div class="stat-sub dim">正在运行</div>
          </div>
          <div
            class="stat-card clickable cl-enter cl-lift"
            style="--cl-delay: 0.16s"
            @click="goPending"
          >
            <div class="stat-label">待处理</div>
            <div class="stat-value mono">{{ pending.length }}</div>
            <div class="stat-sub dim">待审核</div>
          </div>
          <div
            class="stat-card clickable cl-enter cl-lift"
            :class="{ 'stat-card-alert': hasAlerts }"
            style="--cl-delay: 0.24s"
            @click="goAlerts"
          >
            <div class="stat-label">告警</div>
            <div class="stat-value mono">{{ alerts.length }}</div>
            <div class="stat-sub dim">未解决</div>
          </div>
        </div>

        <div class="two-col">
          <!-- NOW & NEXT 6h -->
          <NCard
            size="small"
            :bordered="false"
            class="cool-card cl-enter cl-lift"
            style="--cl-delay: 0.3s"
          >
            <div class="section-title">当前 & 未来 6 小时</div>
            <NEmpty v-if="!upcomingReservations.length" description="未来 6 小时内没有预约" />
            <NDataTable
              v-else
              :columns="nowNextColumns"
              :data="upcomingReservations"
              :row-key="(r) => r.id"
              size="small"
              :bordered="false"
            />
          </NCard>

          <!-- WORK QUEUE -->
          <NCard
            size="small"
            :bordered="false"
            class="cool-card cl-enter cl-lift"
            style="--cl-delay: 0.36s"
          >
            <div class="section-title">
              工作队列
              <span class="dim">({{ pending.length }} 待处理)</span>
            </div>
            <NEmpty v-if="!pending.length" description="没有待审核的申请" />
            <NDataTable
              v-else
              :columns="workQueueColumns"
              :data="pending"
              :row-key="(r) => r.id"
              :row-props="() => ({ style: 'cursor: pointer', onClick: () => goPending() })"
              size="small"
              :bordered="false"
            />
            <div class="card-footer">
              <NButton text size="small" @click="goPending">全部申请 →</NButton>
            </div>
          </NCard>
        </div>

        <div class="two-col">
          <!-- USAGE THIS WEEK -->
          <NCard
            size="small"
            :bordered="false"
            class="cool-card cl-enter cl-lift"
            style="--cl-delay: 0.42s"
          >
            <div class="section-title">
              本周用量
              <span v-if="usageWindow" class="dim">
                (合计 {{ usageWindow.total.toFixed(1) }} GPU·时)
              </span>
            </div>
            <NEmpty v-if="!usage.length" description="近 7 天无 GPU 使用记录" />
            <div v-else class="gpu-rank">
              <div
                v-for="(u, i) in usage"
                :key="u.user_id"
                class="gpu-row cl-nudge cl-enter"
                :style="{ '--cl-delay': `${0.5 + i * 0.04}s` }"
                @click="goUser(u.user_id)"
              >
                <span class="gpu-row-label mono">{{ u.username }}</span>
                <span class="gpu-row-hours mono">{{ u.hours.toFixed(1) }}h</span>
                <span class="gpu-row-bar">
                  <span
                    class="gpu-row-bar-fill"
                    :style="{ width: `${(u.hours / usageMax) * 100}%` }"
                  />
                </span>
              </div>
            </div>
          </NCard>

          <!-- SECURITY MAP -->
          <NCard
            size="small"
            :bordered="false"
            class="cool-card cl-enter cl-lift"
            style="--cl-delay: 0.48s"
          >
            <div class="section-title">
              安全分布图
              <span class="dim">
                ({{ securityTotals.keys }} 个密钥 · {{ securityTotals.grants }} 项授权)
              </span>
            </div>
            <div class="sec-block">
              <div class="sec-block-title">lab 中活跃的 SSH 密钥</div>
              <NEmpty v-if="!securityKeys.length" description="没有活跃密钥" />
              <ul v-else class="sec-list">
                <li v-for="k in securityKeys" :key="k.ssh_key_id">
                  <span class="mono">{{ k.username }}</span>
                  <span class="dim">
                    {{ k.comment ?? k.key_type }} → {{ k.server_count }} 台服务器
                  </span>
                </li>
              </ul>
            </div>
            <div class="sec-block">
              <div class="sec-block-title">服务器管理员授权</div>
              <NEmpty v-if="!securityGrants.length" description="没有委派的管理员" />
              <ul v-else class="sec-list">
                <li v-for="g in securityGrants" :key="g.user_id">
                  <span class="mono">{{ g.username }}</span>
                  <span class="dim"> 管理 {{ g.server_hostnames.join(', ') }} </span>
                </li>
              </ul>
            </div>
            <div class="card-footer">
              <NButton text size="small" @click="goManageAdmins">管理管理员 →</NButton>
            </div>
          </NCard>
        </div>

        <!-- RECENT EVENTS -->
        <NCard
          size="small"
          :bordered="false"
          class="cool-card cl-enter cl-lift"
          style="--cl-delay: 0.54s"
        >
          <div class="section-title">近期事件</div>
          <NEmpty v-if="!audit.length" description="审计日志为空" />
          <NDataTable
            v-else
            :columns="recentEventsColumns"
            :data="audit"
            :row-key="(r) => r.id"
            size="small"
            :bordered="false"
          />
          <div class="card-footer">
            <NButton text size="small" @click="goAudit">审计日志 →</NButton>
          </div>
        </NCard>
      </template>
    </div>
  </AppLayout>
</template>

<style scoped>
.console {
  padding: var(--space-5) var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  color: var(--c-text-primary);
  max-width: 1100px;
  margin: 0 auto;
  width: 100%;
}

/* ── Hero (observation-dashboard theme) ───────────────────────────────
   去同质化:品牌蓝 radial 渐变已撤(Dashboard 留用)、网格纹理已撤
   (全站只留 AdminDomain)、波纹环已撤。本页独有装饰是右侧一组
   "读数刻度线"——几根细竖线向左渐隐,呼应观测台读数的身份;
   纯静态 CSS,无动效,天然兼容 reduced-motion。 */
.hero {
  /* 刻度线颜色:常态用中性边框色,告警态(下方 .alerting)染红 */
  --scale-tint: var(--c-border-default);
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-5) var(--space-6);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  background: var(--c-bg-sunken);
  overflow: hidden;
  animation: cl-fade-up 0.45s cubic-bezier(0.22, 1, 0.36, 1) both;
}
.hero::after {
  /* 读数刻度线:右缘几根 1px 细竖线,向左渐隐 */
  content: '';
  position: absolute;
  inset: 0 0 0 auto;
  width: 220px;
  background: repeating-linear-gradient(
    90deg,
    transparent 0,
    transparent 21px,
    var(--scale-tint) 21px,
    var(--scale-tint) 22px
  );
  opacity: 0.5;
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 85%);
  mask-image: linear-gradient(90deg, transparent, #000 85%);
  pointer-events: none;
}
.hero.alerting {
  /* 有未解决告警时,刻度线随之染红(与 chips / 告警卡的升级红一致) */
  --scale-tint: color-mix(in srgb, var(--c-danger) 40%, var(--c-border-default));
}
.hero-left {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: var(--space-4);
  min-width: 0;
}

/* 仪表徽标:只保留静态圆徽,波纹环(cl-ripple)已撤 */
.gauge {
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
}
.gauge-core {
  position: relative;
  z-index: 1;
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--c-accent);
  background: color-mix(in srgb, var(--c-accent) 12%, var(--c-bg-elevated));
  border: 1px solid color-mix(in srgb, var(--c-accent) 30%, transparent);
}
/* 暗色:与全站页头图标砖同规——退中性灰,蓝色让位给状态信号。 */
[data-theme='dark'] .gauge-core {
  color: var(--c-text-secondary);
  background: var(--c-bg-sunken);
  border-color: var(--c-border-default);
}

.hero-text {
  min-width: 0;
}
.hero-title {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin: 0;
  color: var(--c-text-primary);
}
.hero-sub {
  font-size: 12px;
  color: var(--c-text-tertiary);
  margin-top: 2px;
}
.hero-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--c-text-secondary);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: 999px;
  padding: 3px 10px;
}
.chip-num {
  font-weight: 600;
  color: var(--c-text-primary);
  font-variant-numeric: tabular-nums;
}
.chip-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.chip-ok {
  color: var(--c-success);
}
.chip-warn {
  color: var(--c-warning);
}
.chip-bad {
  color: var(--c-danger);
}
.chip-bad .chip-dot {
  --cl-pulse-color: color-mix(in srgb, var(--c-danger) 55%, transparent);
}
.refresh-btn {
  position: relative;
  z-index: 1;
  flex-shrink: 0;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-2);
}
.stat-card {
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stat-card.clickable {
  cursor: pointer;
  transition: border-color 0.1s;
}
.stat-card.clickable:hover {
  border-color: var(--c-border-default);
}
.stat-card-alert {
  border-color: color-mix(in srgb, var(--c-danger) 45%, transparent);
  background: color-mix(in srgb, var(--c-danger) 5%, var(--c-bg-sunken));
}
.stat-card-alert .stat-value {
  color: var(--c-danger);
}
.stat-label {
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--c-text-tertiary);
  font-weight: 500;
}
.stat-value {
  font-size: 22px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.stat-sub {
  font-size: 11px;
}

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}
@media (max-width: 920px) {
  .two-col,
  .stat-row {
    grid-template-columns: 1fr 1fr;
  }
}
@media (max-width: 600px) {
  .two-col,
  .stat-row {
    grid-template-columns: 1fr;
  }
}

.cool-card {
  background: var(--c-bg-sunken) !important;
  border: 1px solid var(--c-border-subtle) !important;
}
.cool-card :deep(.n-card__content) {
  padding: var(--space-3) var(--space-4);
}
.section-title {
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--c-text-tertiary);
  font-weight: 600;
  margin-bottom: var(--space-3);
  text-transform: uppercase;
}
.card-footer {
  margin-top: var(--space-3);
  text-align: right;
}

.gpu-rank {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
}
.gpu-row {
  display: grid;
  grid-template-columns: 120px 60px 1fr;
  gap: var(--space-3);
  align-items: center;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
}
.gpu-row:hover {
  background: var(--c-border-subtle);
}
.gpu-row-hours {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.gpu-row-bar {
  height: 8px;
  background: var(--c-border-subtle);
  border-radius: 2px;
  overflow: hidden;
}
.gpu-row-bar-fill {
  display: block;
  height: 100%;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--c-accent) 70%, transparent),
    var(--c-accent)
  );
  border-radius: 2px;
  transition: width 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.sec-block {
  margin-bottom: var(--space-3);
}
.sec-block-title {
  font-size: 11px;
  color: var(--c-text-secondary);
  font-weight: 500;
  margin-bottom: 4px;
}
.sec-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}
.sec-list li {
  display: flex;
  gap: var(--space-2);
}

.status-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}
.dot-ok {
  background: var(--c-success);
}
.dot-warn {
  background: var(--c-warning);
}
.dot-bad {
  background: var(--c-danger);
}
.dot-mute {
  background: var(--c-text-disabled);
}
.status-text {
  font-size: 11px;
}

.mono {
  font-family: var(--font-mono);
}
.dim {
  color: var(--c-text-tertiary);
}

/* 尊重 reduced-motion:hero 入场与条形过渡停用。
   独有装饰(读数刻度线)为纯静态 CSS,无动效,无需退化。 */
@media (prefers-reduced-motion: reduce) {
  .hero {
    animation: none;
  }
  .gpu-row-bar-fill {
    transition: none;
  }
}
</style>
