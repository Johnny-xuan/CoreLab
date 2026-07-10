<script setup lang="ts">
/**
 * AdminUserDetail — Phase L L-1 entity-detail page for one user.
 *
 * Lab-admin only. Renders the full picture: identity, stat cards, and six
 * tabs (Overview / Access / Reservations / SSH keys / Activity / Danger
 * zone). Mono-heavy, dense, cool — visually distinct from the user-facing
 * pages (which are workbenches; this is a console).
 *
 * Data loading is staggered:
 *   - profile-summary on mount (drives header + 4 stat cards + Overview /
 *     Access / SSH keys tabs).
 *   - /users/:id/reservations the first time Reservations tab opens.
 *   - audit-logs the first time Activity tab opens (then paginates).
 */

import { computed, h, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDropdown,
  NEmpty,
  NIcon,
  NInput,
  NPagination,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui';
import {
  ArrowLeft,
  Copy,
  MoreHorizontal,
  AlertTriangle,
  ExternalLink,
  Pencil,
  Send,
} from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import UserDangerZone from '@/components/admin/UserDangerZone.vue';
import OneTimeLinkModal from '@/components/admin/OneTimeLinkModal.vue';
import {
  getUserProfileSummary,
  getUserReservations,
  resendInvite,
  updateUser,
  type ProfileGpuRanking,
  type ProfileLinkItem,
  type ProfileRecentAudit,
  type ProfileSshKey,
  type UserProfileSummary,
  type UserReservationItem,
  type UserReservationsResponse,
} from '@/api/users';
import { listAuditLogs, type AuditLogRead } from '@/api/auditLogs';
import { extractDetail } from '@/utils/extractDetail';

const route = useRoute();
const router = useRouter();
const message = useMessage();

const userId = computed(() => Number(route.params.id));

const profile = ref<UserProfileSummary | null>(null);
const profileLoading = ref(false);
const loadError = ref<string | null>(null);

const activeTab = ref<string>('overview');

// Inline identity edit (admin-proxy edit of display_name / email).
const editing = ref(false);
const editSubmitting = ref(false);
const editForm = ref({ display_name: '', email: '' });

// Resend-invite (pending users) → one-time registration link modal.
const resendSubmitting = ref(false);
const inviteLink = ref<string | null>(null);
const inviteModalOpen = ref(false);

const reservations = ref<UserReservationsResponse | null>(null);
const reservationsLoading = ref(false);

const auditRows = ref<AuditLogRead[]>([]);
const auditTotal = ref(0);
const auditPage = ref(1);
const auditSize = ref(20);
const auditLoading = ref(false);

async function loadProfile(): Promise<void> {
  profileLoading.value = true;
  loadError.value = null;
  try {
    profile.value = await getUserProfileSummary(userId.value);
  } catch (err) {
    loadError.value = extractDetail(err, '加载用户失败');
  } finally {
    profileLoading.value = false;
  }
}

async function loadReservations(): Promise<void> {
  if (reservations.value !== null) return;
  reservationsLoading.value = true;
  try {
    reservations.value = await getUserReservations(userId.value);
  } catch (err) {
    message.error(extractDetail(err, '加载预约失败'));
  } finally {
    reservationsLoading.value = false;
  }
}

async function loadAudit(): Promise<void> {
  auditLoading.value = true;
  try {
    const resp = await listAuditLogs({
      actor_user_id: userId.value,
      page: auditPage.value,
      size: auditSize.value,
    });
    auditRows.value = resp.items;
    auditTotal.value = resp.total;
  } catch (err) {
    message.error(extractDetail(err, '加载审计日志失败'));
  } finally {
    auditLoading.value = false;
  }
}

onMounted(loadProfile);

watch(activeTab, (tab) => {
  if (tab === 'reservations' && reservations.value === null) loadReservations();
  if (tab === 'activity' && auditRows.value.length === 0) loadAudit();
});

watch(auditPage, () => {
  if (activeTab.value === 'activity') loadAudit();
});

function goBack(): void {
  if (window.history.length > 1) router.back();
  else router.push({ name: 'admin-users' });
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return '从未';
  const d = new Date(iso).getTime();
  const now = Date.now();
  const diff = now - d;
  if (diff < 60_000) return `${Math.floor(diff / 1000)} 秒前`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return `${Math.floor(diff / 86_400_000)} 天前`;
}

function sourceTagColor(src: string): 'info' | 'primary' | 'warning' | 'success' | 'default' {
  switch (src) {
    case 'ssh_challenge':
      return 'info';
    case 'password_pam':
      return 'primary';
    case 'admin_declared':
      return 'warning';
    case 'admin_prepared_then_ssh':
      return 'success';
    default:
      return 'default';
  }
}

function copy(text: string, label: string): void {
  navigator.clipboard.writeText(text).then(() => message.success(`已复制${label}`));
}

// Three-way account state for the header: disabled / pending-activation / active.
// pulse/pulseColor are purely presentational — only the active state breathes.
const accountStatus = computed<{
  label: string;
  dotClass: string;
  pulse: boolean;
  pulseColor: string;
}>(() => {
  const u = profile.value?.user;
  const base = { pulse: false, pulseColor: '' };
  if (!u) return { label: '—', dotClass: 'dot dot-mute', ...base };
  if (!u.is_active) return { label: '已停用', dotClass: 'dot dot-mute', ...base };
  if (!u.is_activated) return { label: '待激活', dotClass: 'dot dot-warn', ...base };
  return {
    label: '启用',
    dotClass: 'dot dot-ok',
    pulse: true,
    pulseColor: 'color-mix(in srgb, var(--c-success) 50%, transparent)',
  };
});

// Avatar glyph for the identity mark — first letter(s), no data dependency
// beyond the already-loaded profile.
const userInitials = computed<string>(() => {
  const u = profile.value?.user;
  if (!u) return '·';
  const src = (u.display_name || u.username || '').trim();
  return src ? src.slice(0, 1).toUpperCase() : '·';
});

function startEdit(): void {
  if (!profile.value) return;
  editForm.value = {
    display_name: profile.value.user.display_name,
    email: profile.value.user.email,
  };
  editing.value = true;
}

function cancelEdit(): void {
  editing.value = false;
}

async function saveEdit(): Promise<void> {
  if (!profile.value) return;
  editSubmitting.value = true;
  try {
    await updateUser(profile.value.user.id, {
      display_name: editForm.value.display_name.trim(),
      email: editForm.value.email.trim(),
    });
    message.success('用户资料已更新');
    editing.value = false;
    await loadProfile();
  } catch (err) {
    message.error(extractDetail(err, '更新失败'));
  } finally {
    editSubmitting.value = false;
  }
}

async function doResendInvite(): Promise<void> {
  if (!profile.value) return;
  resendSubmitting.value = true;
  try {
    const resp = await resendInvite(profile.value.user.id);
    inviteLink.value = resp.activation_url;
    inviteModalOpen.value = true;
    message.success('新的注册链接已生成,请复制后转交给用户');
  } catch (err) {
    message.error(extractDetail(err, '重发失败'));
  } finally {
    resendSubmitting.value = false;
  }
}

const headerMenuOptions = computed(() => {
  if (!profile.value) return [];
  return [
    {
      label: '复制邮箱',
      key: 'copy-email',
      icon: () => h(NIcon, null, { default: () => h(Copy, { size: 14 }) }),
    },
    {
      label: '复制用户链接',
      key: 'copy-url',
      icon: () => h(NIcon, null, { default: () => h(Copy, { size: 14 }) }),
    },
    {
      label: '查看该用户的审计日志',
      key: 'open-audit',
      icon: () => h(NIcon, null, { default: () => h(ExternalLink, { size: 14 }) }),
    },
  ];
});

function onHeaderMenu(key: string): void {
  if (!profile.value) return;
  if (key === 'copy-email') copy(profile.value.user.email, '邮箱');
  if (key === 'copy-url') copy(window.location.href, '链接');
  if (key === 'open-audit') {
    activeTab.value = 'activity';
  }
}

const topGpuMax = computed(() => {
  if (!profile.value?.top_gpu_7d?.length) return 1;
  return Math.max(...profile.value.top_gpu_7d.map((g) => g.hours), 1);
});

const gpuRow = (g: ProfileGpuRanking) =>
  h('div', { class: 'gpu-row' }, [
    h('span', { class: 'gpu-row-label' }, `${g.server_hostname} / GPU ${g.gpu_index}`),
    h('span', { class: 'gpu-row-hours mono' }, `${g.hours.toFixed(1)}h`),
    h('span', { class: 'gpu-row-bar' }, [
      h('span', {
        class: 'gpu-row-bar-fill',
        style: { width: `${(g.hours / topGpuMax.value) * 100}%` },
      }),
    ]),
  ]);

// ============== Access tab columns ==============
const activeLinkColumns = computed<DataTableColumns<ProfileLinkItem>>(() => [
  {
    title: '关联',
    key: 'link_id',
    width: 60,
    render: (row) => h('span', { class: 'mono dim' }, `#${row.link_id}`),
  },
  { title: '服务器', key: 'server_hostname', width: 140 },
  {
    title: 'Linux 用户',
    key: 'linux_username',
    width: 140,
    render: (row) => h('span', { class: 'mono' }, row.linux_username),
  },
  {
    title: '来源',
    key: 'source',
    width: 160,
    render: (row) =>
      h(
        NTag,
        { size: 'small', type: sourceTagColor(row.source), bordered: false },
        { default: () => row.source },
      ),
  },
  {
    title: '建立时间',
    key: 'established_at',
    render: (row) => h('span', { class: 'mono dim' }, fmtDateTime(row.established_at)),
  },
]);

const revokedLinkColumns = computed<DataTableColumns<ProfileLinkItem>>(() => [
  {
    title: '关联',
    key: 'link_id',
    width: 60,
    render: (row) => h('span', { class: 'mono dim' }, `#${row.link_id}`),
  },
  { title: '服务器', key: 'server_hostname', width: 140 },
  {
    title: 'Linux 用户',
    key: 'linux_username',
    width: 140,
    render: (row) => h('span', { class: 'mono dim' }, row.linux_username),
  },
  {
    title: '来源',
    key: 'source',
    width: 160,
    render: (row) =>
      h(NTag, { size: 'small', type: 'default', bordered: false }, { default: () => row.source }),
  },
  {
    title: '撤销时间',
    key: 'revoked_at',
    render: (row) => h('span', { class: 'mono dim' }, fmtDateTime(row.revoked_at)),
  },
]);

// ============== Reservations tab columns ==============
function reservationStatusDotClass(status: string): string {
  if (status === 'active') return 'dot dot-ok';
  if (status === 'scheduled') return 'dot dot-mute';
  if (status === 'completed') return 'dot dot-mute';
  if (status === 'cancelled' || status === 'failed') return 'dot dot-bad';
  return 'dot dot-mute';
}

const upcomingResColumns = computed<DataTableColumns<UserReservationItem>>(() => [
  {
    title: '时间',
    key: 'start_at',
    render: (row) =>
      h('span', { class: 'mono' }, `${fmtDateTime(row.start_at)} → ${fmtDateTime(row.end_at)}`),
  },
  {
    title: 'GPU',
    key: 'gpu',
    width: 200,
    render: (row) =>
      h(
        'span',
        { class: 'mono dim' },
        row.gpu_index !== null
          ? `${row.server_hostname} / GPU ${row.gpu_index}`
          : `${row.server_hostname} · cron`,
      ),
  },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) =>
      h('span', { class: 'status-cell' }, [
        h('span', { class: reservationStatusDotClass(row.status) }),
        h('span', { class: 'status-text' }, row.status),
      ]),
  },
]);

const past30dResColumns = computed<DataTableColumns<UserReservationItem>>(() => [
  {
    title: '日期',
    key: 'start_at',
    width: 140,
    render: (row) => h('span', { class: 'mono dim' }, fmtDateTime(row.start_at)),
  },
  {
    title: 'GPU',
    key: 'gpu',
    width: 200,
    render: (row) =>
      h(
        'span',
        { class: 'mono dim' },
        row.gpu_index !== null
          ? `${row.server_hostname} / GPU ${row.gpu_index}`
          : `${row.server_hostname} · cron`,
      ),
  },
  {
    title: '时长',
    key: 'hours',
    width: 80,
    render: (row) => h('span', { class: 'mono' }, `${row.hours.toFixed(1)}h`),
  },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) =>
      h('span', { class: 'status-cell' }, [
        h('span', { class: reservationStatusDotClass(row.status) }),
        h('span', { class: 'status-text' }, row.status),
      ]),
  },
]);

const byServerMax = computed(() => {
  if (!reservations.value?.gpu_hours_by_server_30d?.length) return 1;
  return Math.max(...reservations.value.gpu_hours_by_server_30d.map((g) => g.hours), 1);
});

// ============== SSH keys tab ==============
const sshKeyColumns = computed<DataTableColumns<ProfileSshKey>>(() => [
  {
    title: '#',
    key: 'id',
    width: 50,
    render: (row) => h('span', { class: 'mono dim' }, `${row.id}`),
  },
  {
    title: '指纹',
    key: 'fingerprint',
    render: (row) =>
      h(
        'span',
        { class: 'mono fingerprint' },
        row.fingerprint_sha256.length > 24
          ? `${row.fingerprint_sha256.slice(0, 20)}…`
          : row.fingerprint_sha256,
      ),
  },
  {
    title: '类型',
    key: 'key_type',
    width: 90,
    render: (row) => h('span', { class: 'mono dim' }, row.key_type),
  },
  {
    title: '备注',
    key: 'comment',
    render: (row) => h('span', { class: 'dim' }, row.comment || '—'),
  },
  {
    title: '状态',
    key: 'is_active',
    width: 100,
    render: (row) =>
      h('span', { class: 'status-cell' }, [
        h('span', { class: row.is_active ? 'dot dot-ok' : 'dot dot-mute' }),
        h('span', { class: 'status-text' }, row.is_active ? '启用' : '停用'),
      ]),
  },
]);
const activeSshKeyCount = computed(
  () => profile.value?.ssh_keys.filter((key) => key.is_active).length ?? 0,
);
const inactiveSshKeyCount = computed(
  () => profile.value?.ssh_keys.filter((key) => !key.is_active).length ?? 0,
);

// ============== Activity tab columns ==============
const auditColumns = computed<DataTableColumns<AuditLogRead>>(() => [
  {
    title: '时间',
    key: 'created_at',
    width: 170,
    render: (row) => h('span', { class: 'mono dim' }, fmtDateTime(row.created_at)),
  },
  {
    title: '操作',
    key: 'action',
    render: (row) => h('span', { class: 'mono' }, row.action),
  },
  {
    title: '目标',
    key: 'target',
    render: (row) => {
      if (!row.target_type) return h('span', { class: 'dim' }, '—');
      return h(
        'span',
        { class: 'mono dim' },
        `${row.target_type}${row.target_id !== null ? `#${row.target_id}` : ''}`,
      );
    },
  },
  {
    title: '结果',
    key: 'result',
    width: 90,
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
        h('span', { class: 'status-text' }, row.result),
      ]),
  },
]);

function recentAuditLine(a: ProfileRecentAudit): string {
  const target = a.target_type
    ? `${a.target_type}${a.target_id !== null ? `#${a.target_id}` : ''}`
    : '';
  return target ? `${a.action}  ${target}` : a.action;
}

function rowKey(row: { id?: number; link_id?: number; request_id?: number }): number {
  return row.id ?? row.link_id ?? row.request_id ?? 0;
}

async function refreshAfterDanger(): Promise<void> {
  // After a danger-zone action mutates user state, reload both summary
  // and audit (the action emits a fresh audit row).
  await loadProfile();
  if (activeTab.value === 'activity') {
    auditPage.value = 1;
    await loadAudit();
  }
}
</script>

<template>
  <AppLayout>
    <div class="console">
      <!-- Top nav -->
      <div class="topnav">
        <NButton text size="small" @click="goBack">
          <template #icon
            ><NIcon><ArrowLeft :size="14" /></NIcon
          ></template>
          用户
        </NButton>
      </div>

      <!-- Loading / error -->
      <NSpin v-if="profileLoading && !profile" size="small" />
      <NAlert v-else-if="loadError" type="error" :title="loadError" />

      <template v-else-if="profile">
        <!-- ============ HEADER — frameless identity strip ============ -->
        <header class="hdr cl-enter">
          <div class="hdr-left">
            <!-- identity mark: initial-letter avatar, tinted by account state -->
            <div
              class="hdr-mark mono"
              :class="{
                'is-pending': profile.user.is_active && !profile.user.is_activated,
                'is-disabled': !profile.user.is_active,
              }"
              aria-hidden="true"
            >
              {{ userInitials }}
            </div>
            <div class="hdr-info">
              <div class="hdr-name-row">
                <div class="hdr-title">{{ profile.user.display_name }}</div>
                <span class="hdr-username mono dim">@{{ profile.user.username }}</span>
                <NTag size="small" :bordered="false" class="mono">
                  {{ profile.user.role }}
                </NTag>
                <NTag size="small" :bordered="false" round>
                  <span class="status-cell">
                    <span
                      :class="[accountStatus.dotClass, { 'cl-pulse': accountStatus.pulse }]"
                      :style="
                        accountStatus.pulse
                          ? { '--cl-pulse-color': accountStatus.pulseColor }
                          : undefined
                      "
                    />
                    <span class="status-text">{{ accountStatus.label }}</span>
                  </span>
                </NTag>
              </div>
              <div class="hdr-meta dim">
                <span class="mono">{{ profile.user.email }}</span>
                <span class="dot-sep">·</span>
                <span>lab #{{ profile.user.lab_id }}</span>
                <span class="dot-sep">·</span>
                <span>加入于 {{ fmtDateTime(profile.user.created_at) }}</span>
                <span class="dot-sep">·</span>
                <span>上次登录 {{ fmtRelative(profile.user.last_login_at) }}</span>
              </div>
            </div>
          </div>
          <div class="hdr-right">
            <NDropdown
              :options="headerMenuOptions"
              size="small"
              placement="bottom-end"
              @select="onHeaderMenu"
            >
              <NButton tertiary circle size="small">
                <template #icon
                  ><NIcon><MoreHorizontal :size="14" /></NIcon
                ></template>
              </NButton>
            </NDropdown>
          </div>
        </header>

        <!-- Pending-registration banner: invited but no password set yet. -->
        <NAlert
          v-if="profile.user.is_active && !profile.user.is_activated"
          type="info"
          :show-icon="false"
          class="pending-banner cl-enter"
        >
          <div class="pending-row">
            <span>该用户尚未完成注册(还没有设置密码)。可重新生成一个注册链接转交给 TA。</span>
            <NButton size="small" :loading="resendSubmitting" @click="doResendInvite">
              <template #icon
                ><NIcon><Send :size="14" /></NIcon
              ></template>
              重发邀请
            </NButton>
          </div>
        </NAlert>

        <!-- ============ STAT ROW ============ -->
        <div class="stat-row">
          <div class="stat-card cl-enter cl-lift" style="--cl-delay: 0.04s">
            <div class="stat-label">活跃关联</div>
            <div class="stat-value mono">{{ profile.active_links.length }}</div>
            <div class="stat-sub dim">已撤销 {{ profile.revoked_links.length }}</div>
          </div>
          <div class="stat-card cl-enter cl-lift" style="--cl-delay: 0.1s">
            <div class="stat-label">预约</div>
            <div class="stat-value mono">
              {{ profile.reservation_stats.active_count }} /
              {{ profile.reservation_stats.last_30d_count }}
            </div>
            <div class="stat-sub dim">活跃 / 近 30 天</div>
          </div>
          <div class="stat-card cl-enter cl-lift" style="--cl-delay: 0.16s">
            <div class="stat-label">待处理</div>
            <div class="stat-value mono">{{ profile.pending_requests.length }}</div>
            <div class="stat-sub dim">待审核</div>
          </div>
          <div class="stat-card cl-enter cl-lift" style="--cl-delay: 0.22s">
            <div class="stat-label">GPU·时 (7天)</div>
            <div class="stat-value mono">
              {{ profile.reservation_stats.gpu_hours_7d.toFixed(1) }}
            </div>
            <div class="stat-sub dim">
              近 30 天 {{ profile.reservation_stats.gpu_hours_30d.toFixed(1) }}h
            </div>
          </div>
        </div>

        <!-- ============ TABS ============ -->
        <NTabs v-model:value="activeTab" type="line" size="small" animated>
          <!-- ===== OVERVIEW ===== -->
          <NTabPane name="overview" tab="概览">
            <div class="grid-two">
              <NCard size="small" :bordered="false" class="cool-card cl-enter">
                <div class="section-title-row">
                  <div class="section-title">身份信息</div>
                  <NButton v-if="!editing" text size="tiny" class="edit-btn" @click="startEdit">
                    <template #icon
                      ><NIcon><Pencil :size="13" /></NIcon
                    ></template>
                    编辑
                  </NButton>
                </div>
                <div class="kv-grid">
                  <span class="kv-k">用户 #</span>
                  <span class="kv-v mono"
                    >#{{ profile.user.id }} · {{ profile.user.username }}</span
                  >
                  <span class="kv-k">显示名</span>
                  <span v-if="!editing" class="kv-v">{{ profile.user.display_name }}</span>
                  <NInput
                    v-else
                    v-model:value="editForm.display_name"
                    size="small"
                    placeholder="显示名"
                  />
                  <span class="kv-k">邮箱</span>
                  <span v-if="!editing" class="kv-v mono">{{ profile.user.email }}</span>
                  <NInput v-else v-model:value="editForm.email" size="small" placeholder="邮箱" />
                  <span class="kv-k">角色</span>
                  <span class="kv-v">{{ profile.user.role }}</span>
                  <span class="kv-k">Lab</span>
                  <span class="kv-v mono">#{{ profile.user.lab_id }}</span>
                  <span class="kv-k">加入时间</span>
                  <span class="kv-v mono">{{ fmtDateTime(profile.user.created_at) }}</span>
                  <span class="kv-k">上次登录</span>
                  <span class="kv-v mono">
                    {{ fmtDateTime(profile.user.last_login_at) }}
                    <span class="dim">({{ fmtRelative(profile.user.last_login_at) }})</span>
                  </span>
                </div>
                <div v-if="editing" class="edit-actions">
                  <NButton size="small" type="primary" :loading="editSubmitting" @click="saveEdit">
                    保存
                  </NButton>
                  <NButton size="small" :disabled="editSubmitting" @click="cancelEdit">
                    取消
                  </NButton>
                </div>
              </NCard>

              <NCard
                size="small"
                :bordered="false"
                class="cool-card cl-enter"
                style="--cl-delay: 0.08s"
              >
                <div class="section-title">近期活动</div>
                <NEmpty v-if="!profile.recent_audit.length" description="暂无近期活动" />
                <ul v-else class="audit-list">
                  <li v-for="a in profile.recent_audit" :key="a.id">
                    <span class="audit-time mono dim">{{ fmtDateTime(a.created_at) }}</span>
                    <span class="audit-line mono">{{ recentAuditLine(a) }}</span>
                  </li>
                </ul>
                <div class="card-footer">
                  <NButton text size="small" @click="activeTab = 'activity'">查看全部 →</NButton>
                </div>
              </NCard>
            </div>

            <NCard
              size="small"
              :bordered="false"
              class="cool-card mt cl-enter"
              style="--cl-delay: 0.12s"
            >
              <div class="section-title">GPU 使用时长排行 (近 7 天)</div>
              <NEmpty v-if="!profile.top_gpu_7d.length" description="近 7 天无 GPU 使用记录" />
              <div v-else class="gpu-rank">
                <component :is="gpuRow" v-for="g in profile.top_gpu_7d" :key="g.gpu_id" />
              </div>
            </NCard>
          </NTabPane>

          <!-- ===== ACCESS ===== -->
          <NTabPane name="access" tab="访问权限">
            <NCard size="small" :bordered="false" class="cool-card cl-enter">
              <div class="section-title">
                活跃关联
                <span class="dim">({{ profile.active_links.length }})</span>
              </div>
              <NDataTable
                :columns="activeLinkColumns"
                :data="profile.active_links"
                :row-key="rowKey"
                size="small"
                :bordered="false"
              />
            </NCard>
            <NCard
              size="small"
              :bordered="false"
              class="cool-card mt cl-enter"
              style="--cl-delay: 0.08s"
            >
              <div class="section-title">
                已撤销关联
                <span class="dim">({{ profile.revoked_links.length }})</span>
              </div>
              <NDataTable
                :columns="revokedLinkColumns"
                :data="profile.revoked_links"
                :row-key="rowKey"
                size="small"
                :bordered="false"
              />
            </NCard>
          </NTabPane>

          <!-- ===== RESERVATIONS ===== -->
          <NTabPane name="reservations" tab="预约">
            <NSpin v-if="reservationsLoading && !reservations" size="small" />
            <template v-else-if="reservations">
              <NCard size="small" :bordered="false" class="cool-card cl-enter">
                <div class="section-title">
                  即将开始
                  <span class="dim">({{ reservations.upcoming.length }})</span>
                </div>
                <NDataTable
                  :columns="upcomingResColumns"
                  :data="reservations.upcoming"
                  :row-key="(r) => r.id"
                  size="small"
                  :bordered="false"
                />
              </NCard>

              <NCard
                size="small"
                :bordered="false"
                class="cool-card mt cl-enter"
                style="--cl-delay: 0.08s"
              >
                <div class="section-title">
                  近 30 天
                  <span class="dim">
                    ({{ reservations.last_30d.length }} 次会话 ·
                    {{ reservations.gpu_hours_30d.toFixed(1) }} GPU·时)
                  </span>
                </div>
                <NDataTable
                  :columns="past30dResColumns"
                  :data="reservations.last_30d"
                  :row-key="(r) => r.id"
                  size="small"
                  :bordered="false"
                />
              </NCard>

              <NCard
                size="small"
                :bordered="false"
                class="cool-card mt cl-enter"
                style="--cl-delay: 0.12s"
              >
                <div class="section-title">各服务器 GPU·时 (近 30 天)</div>
                <NEmpty
                  v-if="!reservations.gpu_hours_by_server_30d.length"
                  description="近 30 天无 GPU 使用记录"
                />
                <div v-else class="gpu-rank">
                  <div
                    v-for="g in reservations.gpu_hours_by_server_30d"
                    :key="g.gpu_id"
                    class="gpu-row"
                  >
                    <span class="gpu-row-label">
                      {{ g.server_hostname }} / GPU {{ g.gpu_index }}
                    </span>
                    <span class="gpu-row-hours mono">{{ g.hours.toFixed(1) }}h</span>
                    <span class="gpu-row-bar">
                      <span
                        class="gpu-row-bar-fill"
                        :style="{ width: `${(g.hours / byServerMax) * 100}%` }"
                      />
                    </span>
                  </div>
                </div>
              </NCard>
            </template>
          </NTabPane>

          <!-- ===== SSH KEYS ===== -->
          <NTabPane name="ssh-keys" tab="SSH 密钥">
            <NCard size="small" :bordered="false" class="cool-card cl-enter">
              <div class="section-title">
                已在 CoreLab 注册的密钥
                <span class="dim">({{ profile.ssh_keys.length }})</span>
              </div>
              <NDataTable
                :columns="sshKeyColumns"
                :data="profile.ssh_keys"
                :row-key="(r) => r.id"
                size="small"
                :bordered="false"
              />
            </NCard>
            <NCard
              size="small"
              :bordered="false"
              class="cool-card mt cl-enter"
              style="--cl-delay: 0.08s"
            >
              <div class="section-title">访问收口</div>
              <div class="key-summary">
                <div class="key-metric">
                  <span class="key-metric-value mono">{{ activeSshKeyCount }}</span>
                  <span class="key-metric-label">启用密钥</span>
                </div>
                <div class="key-metric">
                  <span class="key-metric-value mono">{{ inactiveSshKeyCount }}</span>
                  <span class="key-metric-label">停用密钥</span>
                </div>
                <div class="key-guidance">
                  需要收回服务器访问时,优先撤销对应账号关联;CoreLab 会同步移除通过该关联推送到
                  authorized_keys 的公钥。上表用于核对该用户当前仍可用于 claim / 推送的注册密钥。
                </div>
              </div>
            </NCard>
          </NTabPane>

          <!-- ===== ACTIVITY ===== -->
          <NTabPane name="activity" tab="活动">
            <NCard size="small" :bordered="false" class="cool-card cl-enter">
              <div class="section-title">
                审计日志
                <span class="dim">(操作者 = user-{{ userId }})</span>
              </div>
              <NDataTable
                :columns="auditColumns"
                :data="auditRows"
                :row-key="(r) => r.id"
                :loading="auditLoading"
                size="small"
                :bordered="false"
              />
              <div class="pager">
                <NPagination
                  v-model:page="auditPage"
                  :item-count="auditTotal"
                  :page-size="auditSize"
                  size="small"
                  show-quick-jumper
                />
              </div>
            </NCard>
          </NTabPane>

          <!-- ===== DANGER ZONE ===== -->
          <NTabPane name="danger" tab="危险操作区">
            <template #tab>
              <span class="danger-tab">
                <NIcon><AlertTriangle :size="12" /></NIcon>
                危险操作区
              </span>
            </template>
            <UserDangerZone
              :user="profile.user"
              :links="profile.active_links"
              @changed="refreshAfterDanger"
            />
          </NTabPane>
        </NTabs>
      </template>

      <OneTimeLinkModal
        v-model:show="inviteModalOpen"
        title="注册链接"
        :url="inviteLink"
        description="链接只显示一次,用户打开后会完成资料、密码和 SSH 公钥配置。"
      />
    </div>
  </AppLayout>
</template>

<style scoped>
.console {
  padding: var(--space-4) var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  color: var(--c-text-primary);
  max-width: 1080px;
  margin: 0 auto;
  width: 100%;
}

.topnav {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 12px;
}

/* HEADER — frameless identity strip: pure typography on the page
   background, closed by a hairline rule. (De-homogenised from the old
   boxed hero — no grid texture, no radial wash, no ripple rings.) */
.hdr {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-2) 0 var(--space-4);
  border-bottom: 1px solid var(--c-border-subtle);
}
.hdr-left {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  min-width: 0;
}
.hdr-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}
.hdr-right {
  flex-shrink: 0;
}

/* identity mark: initial-letter avatar; tint follows the three account
   states — active (accent) / pending (warning) / disabled (muted). */
.hdr-mark {
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: var(--radius-full);
  font-size: 18px;
  font-weight: 600;
  color: var(--c-accent);
  background: color-mix(in srgb, var(--c-accent) 12%, var(--c-bg-elevated));
  border: 1px solid color-mix(in srgb, var(--c-accent) 30%, transparent);
}
.hdr-mark.is-pending {
  color: var(--c-warning);
  background: color-mix(in srgb, var(--c-warning) 12%, var(--c-bg-elevated));
  border-color: color-mix(in srgb, var(--c-warning) 30%, transparent);
}
.hdr-mark.is-disabled {
  color: var(--c-text-disabled);
  background: color-mix(in srgb, var(--c-text-disabled) 12%, var(--c-bg-elevated));
  border-color: color-mix(in srgb, var(--c-text-disabled) 30%, transparent);
}

.hdr-name-row {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
  min-width: 0;
}
.hdr-title {
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.2;
}
.hdr-username {
  font-size: 13px;
}
.hdr-meta {
  font-size: 11px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.dot-sep {
  opacity: 0.6;
}

/* STAT ROW */
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

/* CARDS */
.cool-card {
  background: var(--c-bg-sunken) !important;
  border: 1px solid var(--c-border-subtle) !important;
}
.cool-card :deep(.n-card__content) {
  padding: var(--space-3) var(--space-4);
}
.mt {
  margin-top: var(--space-3);
}
.section-title {
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--c-text-tertiary);
  font-weight: 600;
  margin-bottom: var(--space-3);
  text-transform: uppercase;
}
.section-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}
.section-title-row .section-title {
  margin-bottom: var(--space-3);
}
.edit-btn {
  font-size: 11px;
}
.edit-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-3);
  justify-content: flex-end;
}

/* Pending-activation banner */
.pending-banner :deep(.n-alert-body) {
  padding: var(--space-2) var(--space-3);
}
.pending-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: 12px;
}

/* OVERVIEW two-up */
.grid-two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}
@media (max-width: 920px) {
  .grid-two,
  .stat-row {
    grid-template-columns: 1fr 1fr;
  }
}
@media (max-width: 600px) {
  .grid-two,
  .stat-row {
    grid-template-columns: 1fr;
  }
}

/* IDENTITY kv grid */
.kv-grid {
  display: grid;
  grid-template-columns: 110px 1fr;
  row-gap: 6px;
  column-gap: var(--space-3);
  font-size: 12px;
  align-items: baseline;
}
.kv-k {
  color: var(--c-text-tertiary);
}
.kv-v {
  color: var(--c-text-primary);
}

/* Audit list */
.audit-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.audit-list li {
  font-size: 12px;
  display: grid;
  grid-template-columns: 130px 1fr;
  gap: var(--space-2);
  align-items: baseline;
}
.audit-time {
  font-size: 11px;
}
.audit-line {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.card-footer {
  margin-top: var(--space-3);
  text-align: right;
}

/* GPU ranking */
.gpu-rank {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
}
.gpu-row {
  display: grid;
  grid-template-columns: 180px 60px 1fr;
  gap: var(--space-3);
  align-items: center;
}
.gpu-row-label {
  color: var(--c-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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
  position: relative;
}
.gpu-row-bar-fill {
  display: block;
  height: 100%;
  background: var(--c-accent);
}

/* Status dot */
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
  font-variant-numeric: tabular-nums;
}

.mono {
  font-family: var(--font-mono);
}
.dim {
  color: var(--c-text-tertiary);
}
.fingerprint {
  font-size: 11px;
}

.key-summary {
  display: grid;
  grid-template-columns: minmax(90px, auto) minmax(90px, auto) 1fr;
  gap: var(--space-3);
  align-items: stretch;
}
.key-metric,
.key-guidance {
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  background: var(--c-bg-elevated);
}
.key-metric {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: var(--space-3);
}
.key-metric-value {
  font-size: 22px;
  font-weight: 650;
  color: var(--c-text-primary);
}
.key-metric-label {
  font-size: 11px;
  color: var(--c-text-tertiary);
}
.key-guidance {
  padding: var(--space-3);
  font-size: 12px;
  line-height: 1.55;
  color: var(--c-text-secondary);
}
@media (max-width: 760px) {
  .key-summary {
    grid-template-columns: 1fr 1fr;
  }
  .key-guidance {
    grid-column: 1 / -1;
  }
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--space-3);
}

.danger-tab {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--c-danger);
}

/* Tabs visual density */
:deep(.n-tabs-tab-pad) {
  padding: 6px 12px;
}
:deep(.n-tabs-tab) {
  font-size: 12px;
}

/* Table rows: subtle hover highlight to make the dense grids feel alive. */
.cool-card :deep(.n-data-table-tr) {
  transition: background 0.14s ease;
}
.cool-card :deep(.n-data-table-tbody .n-data-table-tr:hover .n-data-table-td) {
  background-color: color-mix(in srgb, var(--c-accent) 5%, transparent);
}
</style>
