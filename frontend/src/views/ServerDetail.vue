<script setup lang="ts">
/**
 * ServerDetail — six tabs (GPUs / Linux accounts / Server admins /
 * Capabilities / Policies / Link requests).
 *
 * - GPUs: live util_pct + memory_used_mb + temperature with a 5 s
 *   polling loop so the dashboard reflects agent telemetry. Each GPU
 *   gets a self-contained card with stat strip + memory progress bar.
 * - Linux accounts (PAs): table-style row with action buttons.
 * - Server admins: read-only grant list.
 * - Capabilities: 11 capability switches; dangerous ones require ≥10
 *   char notes before they can be enabled (422 otherwise).
 * - Policies / Link requests delegated to sub-components.
 *
 * Visual: Vercel-ish — black status chip / mono identifiers / 1px
 * subtle borders / 8px cards. 页头为无框身份头(GitHub 仓库页式):
 * 纯排版、无装饰盒子,小 Server 图标 + 标题 + 状态药丸(online 时
 * 药丸内绿点呼吸),汇总 chips 全部来自纯展示 computed,不触碰任何业务逻辑。
 */

import { computed, h, onMounted, onUnmounted, ref, watch } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import {
  NAlert,
  NButton,
  NCode,
  NDataTable,
  NInput,
  NModal,
  NSwitch,
  NTabPane,
  NTabs,
  NTag,
  useDialog,
  useMessage,
  type DataTableColumns,
} from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';
import { formatDateTime, timeAgo } from '@/utils/timeago';
import {
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  Cpu,
  KeyRound,
  RefreshCw,
  Server,
  ShieldAlert,
  UserPlus,
} from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import PoliciesTab from '@/components/policies/PoliciesTab.vue';
import LinkRequestsTab from '@/components/serverDetail/LinkRequestsTab.vue';
import OnboardUserDialog from '@/components/serverDetail/OnboardUserDialog.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import { validateDangerousNotes } from '@/utils/capabilityValidation';
import { useAuthStore } from '@/stores/auth';
import { useWorkspaceStore } from '@/stores/workspace';
import {
  approveServer,
  getServer,
  listAdmins,
  listCapabilities,
  listGpus,
  regenerateEnrollmentToken,
  updateCapability,
  type CapabilityRead,
  type GpuRead,
  type RegenerateEnrollmentTokenResponse,
  type ServerAdminGrantRead,
  type ServerRead,
} from '@/api/servers';
import * as paApi from '@/api/physicalAccounts';
import type { PhysicalAccountRead, ReverseLookupEntry } from '@/api/physicalAccounts';
import * as alApi from '@/api/accountLinks';
import { listMyKeys, type SshKeyRead } from '@/api/sshKeys';
import { listUsers, type UserRead } from '@/api/users';

const route = useRoute();
const message = useMessage();
const dialog = useDialog();
const auth = useAuthStore();

const serverId = computed(() => Number(route.params.id));
type ServerTab = 'gpus' | 'admins' | 'pas' | 'capabilities' | 'policies' | 'link-requests';
const VALID_TABS: readonly ServerTab[] = [
  'gpus',
  'admins',
  'pas',
  'capabilities',
  'policies',
  'link-requests',
];
const tab = ref<ServerTab>('gpus');

function _seedTabFromQuery(): void {
  const q = route.query.tab;
  if (typeof q === 'string' && (VALID_TABS as readonly string[]).includes(q)) {
    tab.value = q as ServerTab;
  }
}

const server = ref<ServerRead | null>(null);
const gpus = ref<GpuRead[]>([]);
const admins = ref<ServerAdminGrantRead[]>([]);
const caps = ref<CapabilityRead[]>([]);
const pas = ref<PhysicalAccountRead[]>([]);
const labUsers = ref<UserRead[]>([]);
const myKeys = ref<SshKeyRead[]>([]);
const workspace = useWorkspaceStore();

const capNotes = ref<Record<string, string>>({});
const capBusy = ref<Record<string, boolean>>({});

const regenResp = ref<RegenerateEnrollmentTokenResponse | null>(null);
const regenBusy = ref(false);
const regenModalOpen = ref(false);
const approveBusy = ref(false);

// PA-create form state
const createPaOpen = ref(false);
const onboardOpen = ref(false);
const createPaForm = ref({ linux_username: '', notes: '' });
const createPaSubmitting = ref(false);

// Claim modal state — supports both SSH key + PAM password paths
type ClaimMode = 'ssh' | 'pam';
const claimOpen = ref(false);
const claimMode = ref<ClaimMode>('ssh');
const claimPa = ref<PhysicalAccountRead | null>(null);
const claimKeyId = ref<number | null>(null);
const claimPassword = ref('');
const claimChallenge = ref<alApi.ChallengeIssued | null>(null);
const claimSignature = ref('');
const claimSubmitting = ref(false);

// Declare-owner modal state
const declareOpen = ref(false);
const declarePa = ref<PhysicalAccountRead | null>(null);
const declareOwnerId = ref<number | null>(null);
const declareReason = ref('');
const declareSubmitting = ref(false);
const declareShared = ref<ReverseLookupEntry[]>([]);
const declareAcknowledgedShared = ref(false);

let pollTimer: number | null = null;

async function refreshHeader(): Promise<void> {
  try {
    server.value = await getServer(serverId.value);
  } catch (err) {
    message.error(extractDetail(err, '加载失败'));
  }
}

async function refreshGpus(): Promise<void> {
  try {
    gpus.value = await listGpus(serverId.value);
  } catch (err) {
    message.error(extractDetail(err, '加载 GPU 失败'));
  }
}

async function refreshAdmins(): Promise<void> {
  try {
    admins.value = await listAdmins(serverId.value);
  } catch (err) {
    message.error(extractDetail(err, '加载管理员失败'));
  }
}

async function refreshCaps(): Promise<void> {
  try {
    caps.value = await listCapabilities(serverId.value);
    for (const c of caps.value) {
      if (!(c.capability_key in capNotes.value)) {
        capNotes.value[c.capability_key] = c.notes ?? '';
      }
    }
  } catch (err) {
    message.error(extractDetail(err, '加载 capability 失败'));
  }
}

onMounted(async () => {
  _seedTabFromQuery();
  await refreshHeader();
  await refreshGpus();
  pollTimer = window.setInterval(() => {
    void refreshHeader();
    void refreshGpus();
  }, 5_000);
});

onUnmounted(() => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
  }
});

watch(tab, async (v) => {
  if (v === 'admins') await refreshAdmins();
  if (v === 'capabilities') await refreshCaps();
  if (v === 'pas') await refreshPas();
});

async function refreshPas(): Promise<void> {
  try {
    pas.value = await paApi.listPas(serverId.value);
  } catch (err) {
    message.error(extractDetail(err, '加载 Linux 账号失败'));
  }
}

function openCreatePa(): void {
  createPaForm.value = { linux_username: '', notes: '' };
  createPaOpen.value = true;
}

async function submitCreatePa(): Promise<void> {
  if (!createPaForm.value.linux_username) {
    message.warning('linux_username 必填');
    return;
  }
  createPaSubmitting.value = true;
  try {
    await paApi.createPa(serverId.value, {
      linux_username: createPaForm.value.linux_username,
      source: 'admin_manual_register',
      notes: createPaForm.value.notes || null,
    });
    message.success('PhysicalAccount 已登记。');
    createPaOpen.value = false;
    await refreshPas();
  } catch (err) {
    message.error(extractDetail(err, '创建失败'));
  } finally {
    createPaSubmitting.value = false;
  }
}

async function openClaim(pa: PhysicalAccountRead, mode: ClaimMode): Promise<void> {
  claimPa.value = pa;
  claimMode.value = mode;
  claimChallenge.value = null;
  claimSignature.value = '';
  claimPassword.value = '';
  claimKeyId.value = null;
  if (mode === 'ssh' && myKeys.value.length === 0) {
    try {
      myKeys.value = await listMyKeys();
    } catch (err) {
      message.error(extractDetail(err, '加载 SSH key 失败'));
      return;
    }
  }
  const firstKey = myKeys.value[0];
  if (mode === 'ssh' && firstKey !== undefined) {
    claimKeyId.value = firstKey.id;
  }
  claimOpen.value = true;
}

async function mintChallenge(): Promise<void> {
  const pa = claimPa.value;
  if (pa === null || claimKeyId.value === null) return;
  claimSubmitting.value = true;
  try {
    claimChallenge.value = await alApi.createChallenge({
      server_id: pa.server_id,
      linux_username: pa.linux_username,
      ssh_public_key_id: claimKeyId.value,
    });
  } catch (err) {
    message.error(extractDetail(err, 'challenge 失败'));
  } finally {
    claimSubmitting.value = false;
  }
}

async function submitClaim(): Promise<void> {
  const pa = claimPa.value;
  if (pa === null) return;
  claimSubmitting.value = true;
  try {
    if (claimMode.value === 'ssh') {
      const ch = claimChallenge.value;
      if (ch === null) {
        message.warning('请先获取 challenge。');
        return;
      }
      await alApi.verifyChallenge({
        challenge_id: ch.challenge_id,
        signature_armored: claimSignature.value,
      });
    } else {
      await alApi.tryPassword({
        server_id: pa.server_id,
        linux_username: pa.linux_username,
        password: claimPassword.value,
      });
    }
    message.success('关联成功 —— 关联已建立。');
    claimOpen.value = false;
    await workspace.refresh();
  } catch (err) {
    message.error(extractDetail(err, '校验失败'));
  } finally {
    claimSubmitting.value = false;
  }
}

async function openDeclare(pa: PhysicalAccountRead): Promise<void> {
  declarePa.value = pa;
  declareOwnerId.value = null;
  declareReason.value = '';
  declareShared.value = [];
  declareAcknowledgedShared.value = false;
  if (labUsers.value.length === 0) {
    try {
      labUsers.value = await listUsers();
    } catch (err) {
      message.error(extractDetail(err, '加载用户失败'));
      return;
    }
  }
  try {
    const lookup = await paApi.reverseLookupViaPa(pa.server_id, pa.id);
    declareShared.value = lookup.linked_users;
  } catch {
    // best-effort
  }
  declareOpen.value = true;
}

async function submitDeclare(): Promise<void> {
  const pa = declarePa.value;
  if (pa === null || declareOwnerId.value === null) return;
  if (declareReason.value.trim().length < 20) {
    message.warning('理由必须 ≥20 字符。');
    return;
  }
  if (declareShared.value.length > 0 && !declareAcknowledgedShared.value) {
    message.warning('请勾选共享账号提示后再继续。');
    return;
  }
  declareSubmitting.value = true;
  try {
    await paApi.declareOwner(pa.server_id, pa.id, {
      owner_user_id: declareOwnerId.value,
      reason: declareReason.value.trim(),
    });
    message.success('已声明 owner。');
    declareOpen.value = false;
  } catch (err) {
    message.error(extractDetail(err, '声明失败'));
  } finally {
    declareSubmitting.value = false;
  }
}

const userOptions = computed(() =>
  labUsers.value.map((u) => ({ label: `${u.display_name} (${u.username})`, value: u.id })),
);
const keyOptions = computed(() =>
  myKeys.value.map((k) => ({
    label: `${k.comment ?? '(无标签)'} — ${k.fingerprint_sha256}`,
    value: k.id,
  })),
);

const adminColumns = computed<DataTableColumns<ServerAdminGrantRead>>(() => [
  {
    title: '授权 ID',
    key: 'id',
    width: 90,
    render: (row) => h('span', { class: 'mono tabular' }, `#${row.id}`),
  },
  {
    title: '用户',
    key: 'user_id',
    width: 120,
    render: (row) => h('span', { class: 'mono tabular' }, `user #${row.user_id}`),
  },
  { title: '备注', key: 'notes', render: (row) => row.notes ?? '—' },
  {
    title: '授权时间',
    key: 'granted_at',
    render: (row) =>
      h(
        'span',
        { class: 'tabular muted', title: formatDateTime(row.granted_at) },
        timeAgo(row.granted_at),
      ),
  },
]);

async function toggleCap(cap: CapabilityRead, enabled: boolean): Promise<void> {
  const notes = capNotes.value[cap.capability_key] ?? '';
  const v = validateDangerousNotes(notes, cap.is_dangerous, enabled);
  if (!v.ok) {
    message.warning('危险 capability 必须先填 ≥10 字符的 notes');
    return;
  }
  capBusy.value[cap.capability_key] = true;
  try {
    const updated = await updateCapability(serverId.value, cap.capability_key, {
      enabled,
      notes: notes.trim() || null,
    });
    Object.assign(
      caps.value.find((c) => c.capability_key === updated.capability_key) ?? {},
      updated,
    );
    message.success('已更新');
  } catch (err) {
    message.error(extractDetail(err, '更新失败'));
  } finally {
    capBusy.value[cap.capability_key] = false;
  }
}

function confirmRegenerate(): void {
  dialog.warning({
    title: '重新生成接入令牌?',
    content: '该服务器当前的安装命令将立即失效。' + '新令牌只显示一次 — 请立即复制安装命令。',
    positiveText: '重新生成',
    negativeText: '取消',
    onPositiveClick: async () => {
      regenBusy.value = true;
      try {
        regenResp.value = await regenerateEnrollmentToken(serverId.value);
        regenModalOpen.value = true;
        const n = regenResp.value.revoked_token_ids.length;
        message.success(n > 0 ? `已生成新令牌;吊销了 ${n} 个旧令牌。` : '已生成新令牌。');
      } catch (err) {
        message.error(extractDetail(err, '重新生成失败'));
      } finally {
        regenBusy.value = false;
      }
    },
  });
}

function confirmApprove(): void {
  if (!server.value) return;
  dialog.warning({
    title: '确认接入这台服务器?',
    content: '确认后,该 agent 将进入可信范围,可上报 GPU、账号扫描、脚本生命周期和治理事件。',
    positiveText: '确认接入',
    negativeText: '取消',
    onPositiveClick: async () => {
      approveBusy.value = true;
      try {
        server.value = await approveServer(serverId.value);
        await refreshGpus();
        message.success('服务器已确认接入');
      } catch (err) {
        message.error(extractDetail(err, '确认失败'));
      } finally {
        approveBusy.value = false;
      }
    },
  });
}

function confirmDangerousEnable(cap: CapabilityRead): void {
  dialog.warning({
    title: `开启危险 capability:${cap.capability_key}?`,
    content: '此操作将允许 agent 在该 server 上执行该操作。继续吗?',
    positiveText: '开启',
    negativeText: '取消',
    onPositiveClick: async () => {
      await toggleCap(cap, true);
    },
  });
}

function statusToneClass(s: ServerRead['status'] | undefined): string {
  if (s === 'online') return 'chip-success';
  if (s === 'offline') return 'chip-danger';
  if (s === 'pending') return 'chip-warn';
  return 'chip-default';
}

function memPct(g: GpuRead): number {
  if (g.memory_total_mb === null || g.memory_total_mb === 0) return 0;
  return Math.min(100, Math.round(((g.memory_used_mb ?? 0) / g.memory_total_mb) * 100));
}

function utilTone(g: GpuRead): string {
  const u = g.util_pct ?? 0;
  if (u > 90) return 'tone-danger';
  if (u > 60) return 'tone-warn';
  return 'tone-info';
}

// ── 页头汇总(纯展示 computed,不参与任何业务逻辑)──────────────────
const gpuCount = computed(() => gpus.value.length);

const avgUtil = computed<number | null>(() => {
  const vals = gpus.value
    .map((g) => g.util_pct)
    .filter((v): v is number => v !== null && v !== undefined);
  if (vals.length === 0) return null;
  return Math.round(vals.reduce((a, b) => a + b, 0) / vals.length);
});

const totalMemGb = computed<number | null>(() => {
  const vals = gpus.value
    .map((g) => g.memory_total_mb)
    .filter((v): v is number => v !== null && v !== undefined);
  if (vals.length === 0) return null;
  return Math.round(vals.reduce((a, b) => a + b, 0) / 1024);
});
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header cl-enter">
        <div class="page-header-top">
          <NButton size="tiny" quaternary @click="$router.push({ name: 'servers' })">
            <template #icon>
              <ChevronLeft :size="14" :stroke-width="2" />
            </template>
            服务器
          </NButton>
        </div>
        <div class="page-header-main">
          <div class="header-text">
            <div class="header-title-row">
              <Server class="title-icon" :size="18" :stroke-width="1.75" aria-hidden="true" />
              <h1 class="page-title">
                {{ server?.display_name || server?.hostname || `Server #${serverId}` }}
              </h1>
              <span v-if="server" :class="['status-chip', statusToneClass(server.status)]">
                <span class="status-dot" />
                {{ server.status }}
              </span>
            </div>
            <div class="header-sub">
              单节点档案 —— GPU 遥测每 5 秒自动刷新,集中管理 Linux 账号、capability 与策略
            </div>
            <div class="header-chips">
              <span class="chip">
                <span class="chip-num">{{ gpuCount }}</span> 张 GPU
              </span>
              <span v-if="avgUtil !== null" class="chip">
                利用率均值 <span class="chip-num">{{ avgUtil }}%</span>
              </span>
              <span v-if="totalMemGb !== null" class="chip">
                显存合计 <span class="chip-num">{{ totalMemGb }} GB</span>
              </span>
              <span v-if="server?.agent_version" class="chip">
                <span class="mono">agent {{ server.agent_version }}</span>
              </span>
            </div>
          </div>
          <div v-if="auth.isLabAdmin" class="header-actions">
            <NButton
              v-if="server?.status === 'pending' && server.last_heartbeat_at"
              size="small"
              type="primary"
              :loading="approveBusy"
              @click="confirmApprove"
            >
              <template #icon>
                <CheckCircle2 :size="13" :stroke-width="1.9" />
              </template>
              确认接入
            </NButton>
            <NButton size="small" :loading="regenBusy" @click="confirmRegenerate">
              <template #icon>
                <RefreshCw :size="13" :stroke-width="1.75" />
              </template>
              重新生成令牌
            </NButton>
          </div>
        </div>
        <dl v-if="server" class="meta-grid">
          <div class="meta-item">
            <dt>主机名</dt>
            <dd class="mono">{{ server.hostname || '—' }}</dd>
          </div>
          <div class="meta-item">
            <dt>服务器 ID</dt>
            <dd class="mono tabular">#{{ server.id }}</dd>
          </div>
          <div class="meta-item">
            <dt>操作系统</dt>
            <dd class="mono">{{ server.os_info ?? '—' }}</dd>
          </div>
          <div class="meta-item">
            <dt>Agent</dt>
            <dd class="mono">{{ server.agent_version ?? '—' }}</dd>
          </div>
          <div class="meta-item">
            <dt>最后在线</dt>
            <dd class="tabular" :title="formatDateTime(server.last_heartbeat_at)">
              {{ timeAgo(server.last_heartbeat_at) }}
            </dd>
          </div>
          <div class="meta-item">
            <dt>接入确认</dt>
            <dd class="tabular" :title="formatDateTime(server.approved_at)">
              {{ server.approved_at ? timeAgo(server.approved_at) : '未确认' }}
            </dd>
          </div>
          <div class="meta-item">
            <dt>最长预约时长</dt>
            <dd class="mono">
              <span class="cl-num">{{ server.max_reservation_hours ?? '∞' }}</span> h
            </dd>
          </div>
        </dl>
      </header>

      <div class="tabs-wrap cl-enter" style="--cl-delay: 0.08s">
        <NTabs v-model:value="tab" type="line" animated size="small">
          <!-- ── GPUs ───────────────────────────────────────────────────── -->
          <NTabPane name="gpus" :tab="`GPUs (${gpus.length})`">
            <div v-if="gpus.length === 0" class="empty-wrap">
              <CleanEmpty
                :icon="Cpu"
                title="尚未上报 GPU"
                description="agent 上报首次 nvidia-smi 采样后,这里会自动填充。"
                compact
              />
            </div>
            <div v-else class="gpu-grid">
              <article
                v-for="(g, i) in gpus"
                :key="g.id"
                class="gpu-card cl-enter cl-lift"
                :style="{ '--cl-delay': `${Math.min(i, 6) * 0.06}s` }"
              >
                <header class="gpu-card-head">
                  <div class="gpu-id">
                    <span class="gpu-index mono tabular">#{{ g.gpu_index }}</span>
                    <span class="gpu-model">{{ g.model ?? '未知 GPU' }}</span>
                  </div>
                  <span class="gpu-uuid mono" :title="g.uuid ?? ''">
                    {{ g.uuid ? g.uuid.slice(0, 18) + '…' : '—' }}
                  </span>
                </header>
                <div class="stat-strip">
                  <div class="stat">
                    <span class="stat-label">利用率</span>
                    <span :class="['stat-value', 'mono', 'tabular', utilTone(g)]">
                      {{ g.util_pct ?? '—' }}<span class="stat-unit">%</span>
                    </span>
                  </div>
                  <div class="stat">
                    <span class="stat-label">显存</span>
                    <span class="stat-value mono tabular">
                      {{ g.memory_used_mb ?? '—' }}
                      <span class="stat-unit">/ {{ g.memory_total_mb ?? '—' }} MB</span>
                    </span>
                  </div>
                  <div class="stat">
                    <span class="stat-label">温度</span>
                    <span class="stat-value mono tabular">
                      {{ g.temperature_c ?? '—' }}<span class="stat-unit">°C</span>
                    </span>
                  </div>
                  <div class="stat">
                    <span class="stat-label">更新于</span>
                    <span class="stat-value mono tabular muted">
                      {{ g.last_updated_at ? g.last_updated_at.slice(11, 19) : '—' }}
                    </span>
                  </div>
                </div>
                <div
                  class="mem-bar"
                  role="progressbar"
                  :aria-valuenow="memPct(g)"
                  aria-valuemin="0"
                  aria-valuemax="100"
                >
                  <div class="mem-bar-fill" :style="{ width: memPct(g) + '%' }" />
                </div>
              </article>
            </div>
          </NTabPane>

          <!-- ── Server admins ─────────────────────────────────────────── -->
          <NTabPane name="admins" :tab="`服务器管理员 (${admins.length})`">
            <div v-if="admins.length === 0" class="empty-wrap">
              <CleanEmpty
                :icon="ShieldAlert"
                title="暂无服务器管理员"
                description="lab_admin 可通过 API 授予按服务器的管理员权限。"
                compact
              />
            </div>
            <div v-else class="table-wrap">
              <NDataTable
                :columns="adminColumns"
                :data="admins"
                :bordered="false"
                :single-line="false"
                size="small"
              />
            </div>
          </NTabPane>

          <!-- ── Physical accounts (Linux users) ────────────────────────── -->
          <NTabPane name="pas" :tab="`Linux 账号 (${pas.length})`">
            <div class="pas-bar">
              <p class="bar-hint">
                CoreLab 已知的该服务器上的 Linux 账号。关联其中一个即可以其身份操作;lab_admin
                可预先登记一个 PA,或在无需验证的情况下声明 owner。
              </p>
              <NButton v-if="auth.isLabAdmin" size="small" @click="openCreatePa">
                <template #icon>
                  <UserPlus :size="13" :stroke-width="1.75" />
                </template>
                登记 PA
              </NButton>
              <NButton
                v-if="auth.isLabAdmin"
                size="small"
                type="primary"
                @click="onboardOpen = true"
              >
                <template #icon>
                  <UserPlus :size="13" :stroke-width="1.75" />
                </template>
                Onboard 用户(情形 3)
              </NButton>
            </div>
            <div v-if="pas.length === 0" class="empty-wrap">
              <CleanEmpty
                :icon="UserPlus"
                title="还没有 physical account"
                description="agent 下次扫描时会发现系统用户;管理员也可预先登记一个。"
                compact
              />
            </div>
            <div v-else class="pa-list">
              <article v-for="pa in pas" :key="pa.id" class="pa-row cl-nudge">
                <div class="pa-left">
                  <code class="pa-name mono">{{ pa.linux_username }}</code>
                  <NTag size="small" :bordered="false" type="default">{{ pa.source }}</NTag>
                </div>
                <div class="pa-actions">
                  <NButton size="tiny" quaternary @click="openClaim(pa, 'ssh')">
                    关联(SSH)
                  </NButton>
                  <NButton size="tiny" quaternary @click="openClaim(pa, 'pam')">
                    关联(密码)
                  </NButton>
                  <NButton v-if="auth.isLabAdmin" size="tiny" quaternary @click="openDeclare(pa)">
                    声明 owner
                  </NButton>
                </div>
              </article>
            </div>
          </NTabPane>

          <!-- ── Capabilities ──────────────────────────────────────────── -->
          <NTabPane name="capabilities" :tab="`Capability (${caps.length})`">
            <div class="cap-bar">
              <p class="bar-hint">
                危险 capability 必须先填 ≥10 字符 notes 才能开启。修改记入 audit log。
              </p>
              <RouterLink
                :to="{ name: 'lab-audit', query: { target_server_id: serverId } }"
                class="audit-link"
              >
                查看本 server 审计日志
                <ChevronRight :size="13" :stroke-width="2" />
              </RouterLink>
            </div>
            <div v-if="caps.length === 0" class="empty-wrap">
              <CleanEmpty
                :icon="KeyRound"
                title="尚未上报 capability"
                description="agent 启动并注册后,这里会自动填充。"
                compact
              />
            </div>
            <div v-else class="cap-list">
              <article v-for="c in caps" :key="c.capability_key" class="cap-row cl-nudge">
                <div class="cap-name">
                  <code class="mono">{{ c.capability_key }}</code>
                  <NTag v-if="c.is_dangerous" size="small" type="error" :bordered="false">
                    危险
                  </NTag>
                </div>
                <NInput
                  v-model:value="capNotes[c.capability_key]"
                  placeholder="可选:授权理由 / 审批备注"
                  size="small"
                  :disabled="!auth.isLabAdmin"
                  class="cap-notes"
                />
                <NSwitch
                  :value="c.is_enabled"
                  :loading="capBusy[c.capability_key]"
                  :disabled="!auth.isLabAdmin"
                  @update:value="
                    (v: boolean) =>
                      v && c.is_dangerous ? confirmDangerousEnable(c) : toggleCap(c, v)
                  "
                />
              </article>
            </div>
          </NTabPane>

          <!-- ── Policies ──────────────────────────────────────────────── -->
          <NTabPane name="policies" tab="策略">
            <PoliciesTab :server-id="serverId" :capabilities="caps" :can-edit="auth.isLabAdmin" />
          </NTabPane>

          <!-- ── Link requests ─────────────────────────────────────────── -->
          <NTabPane name="link-requests" tab="关联申请">
            <LinkRequestsTab :server-id="serverId" :can-edit="auth.isLabAdmin" />
          </NTabPane>
        </NTabs>
      </div>

      <NAlert
        v-if="!auth.isLabAdmin"
        type="info"
        :show-icon="false"
        class="admin-hint cl-enter"
        style="--cl-delay: 0.16s"
      >
        Capability + grant 修改需要 lab_admin 角色。
      </NAlert>

      <!-- ── Modals ───────────────────────────────────────────────────── -->
      <OnboardUserDialog
        :server-id="serverId"
        :show="onboardOpen"
        @done="
          onboardOpen = false;
          refreshPas();
        "
        @cancel="onboardOpen = false"
      />

      <NModal
        v-model:show="createPaOpen"
        preset="card"
        title="登记 physical account"
        style="max-width: 28rem"
      >
        <p class="modal-blurb">
          用于登记一个服务器上已经存在、但 CoreLab 还没发现的 Linux 账号。<code>source</code> 默认为
          <code>admin_manual_register</code>。
        </p>
        <div class="form-row">
          <label>linux_username</label>
          <NInput v-model:value="createPaForm.linux_username" placeholder="如:yang_lab" />
        </div>
        <div class="form-row">
          <label>notes(可选)</label>
          <NInput v-model:value="createPaForm.notes" placeholder="可选:如「实验组共享账号」" />
        </div>
        <div class="modal-actions">
          <NButton @click="createPaOpen = false">取消</NButton>
          <NButton type="primary" :loading="createPaSubmitting" @click="submitCreatePa">
            登记
          </NButton>
        </div>
      </NModal>

      <NModal
        v-model:show="claimOpen"
        preset="card"
        :title="claimMode === 'ssh' ? '通过 SSH challenge 关联' : '通过密码(PAM)关联'"
        style="max-width: 38rem"
      >
        <p v-if="claimPa !== null" class="modal-blurb">
          你正在关联 server #{{ claimPa.server_id }} 上的 <code>{{ claimPa.linux_username }}</code
          >。成功后会创建一条归属于你的 <code>account_link</code> 记录。
        </p>
        <template v-if="claimMode === 'ssh'">
          <div class="form-row">
            <label>SSH key</label>
            <select v-model.number="claimKeyId" class="select-native">
              <option v-for="opt in keyOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </div>
          <NButton
            :loading="claimSubmitting"
            :disabled="claimKeyId === null"
            @click="mintChallenge"
          >
            1. 获取 challenge
          </NButton>
          <template v-if="claimChallenge !== null">
            <p class="modal-blurb" style="margin-top: var(--space-3)">
              在你的本地终端运行下面的命令 — 它会输出你需要粘贴到下方的
              <code>signature_armored</code>。
            </p>
            <NCode :code="claimChallenge.sign_command" :hljs="undefined" word-wrap />
            <div class="form-row">
              <label>signature_armored</label>
              <NInput
                v-model:value="claimSignature"
                type="textarea"
                :rows="6"
                placeholder="-----BEGIN SSH SIGNATURE-----..."
              />
            </div>
            <div class="modal-actions">
              <NButton @click="claimOpen = false">取消</NButton>
              <NButton
                type="primary"
                :loading="claimSubmitting"
                :disabled="!claimSignature"
                @click="submitClaim"
              >
                2. 校验并关联
              </NButton>
            </div>
          </template>
        </template>
        <template v-else>
          <div class="form-row">
            <label>password</label>
            <NInput
              v-model:value="claimPassword"
              type="password"
              show-password-on="click"
              placeholder="仅用于校验,不会保存"
            />
          </div>
          <div class="modal-actions">
            <NButton @click="claimOpen = false">取消</NButton>
            <NButton
              type="primary"
              :loading="claimSubmitting"
              :disabled="!claimPassword"
              @click="submitClaim"
            >
              通过 PAM 校验
            </NButton>
          </div>
        </template>
      </NModal>

      <NModal
        v-model:show="declareOpen"
        preset="card"
        title="声明 PA owner(admin_declared)"
        style="max-width: 32rem"
      >
        <p class="modal-blurb">
          写入一条 <code>admin_declared</code> 的 account_link — 在反查 + 通知中可见, 但 owner
          在通过 SSH challenge 升级前无法以其身份操作。理由必须 ≥ 20 个字符。
        </p>
        <NAlert
          v-if="declareShared.length > 0"
          type="warning"
          :show-icon="false"
          style="margin-bottom: var(--space-3)"
        >
          <strong>共享账号警告。</strong> 该 PA 已经有 {{ declareShared.length }} 个其它生效中的
          link。多个用户共用一个 Linux 账号
          会削弱个体层面的可追责性。继续前请确认运维策略允许这样做。
          <label class="ack-line">
            <input v-model="declareAcknowledgedShared" type="checkbox" />
            我理解审计上的局限性。
          </label>
        </NAlert>
        <div class="form-row">
          <label>owner</label>
          <select v-model.number="declareOwnerId" class="select-native">
            <option :value="null" disabled>选择一个 Lab 用户…</option>
            <option v-for="opt in userOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
        </div>
        <div class="form-row">
          <label>理由(≥ 20 个字符)</label>
          <NInput
            v-model:value="declareReason"
            type="textarea"
            :rows="3"
            placeholder="例如:yang 组共享训练账号;在系统管理员交接时声明。"
          />
        </div>
        <div class="modal-actions">
          <NButton @click="declareOpen = false">取消</NButton>
          <NButton
            type="primary"
            :loading="declareSubmitting"
            :disabled="declareOwnerId === null"
            @click="submitDeclare"
          >
            声明
          </NButton>
        </div>
      </NModal>

      <NModal
        v-model:show="regenModalOpen"
        preset="card"
        title="新的接入令牌"
        style="max-width: 36rem"
      >
        <div v-if="regenResp" class="regen-result">
          <NAlert type="success" :show-icon="false" title="令牌已重新生成">
            把下面的安装命令复制到服务器终端执行。此处显示的令牌是你唯一一次看到它明文的机会。
          </NAlert>
          <NCode :code="regenResp.install_snippet" :hljs="undefined" word-wrap />
          <p class="regen-meta">
            过期时间 <span class="mono tabular">{{ formatDateTime(regenResp.expires_at) }}</span> —
            已吊销 {{ regenResp.revoked_token_ids.length }} 个旧令牌。
          </p>
          <div class="modal-actions">
            <NButton @click="regenModalOpen = false">关闭</NButton>
          </div>
        </div>
      </NModal>
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
  gap: var(--space-4);
}

/* ── 无框身份头 —— GitHub 仓库页式,靠排版而非装饰盒子 ─────────────── */
.page-header {
  display: flex;
  flex-direction: column;
  /* main.css 的全局 .page-header(横向 pagebar 原语)带 align-items: flex-start;
     在本页的纵向页头里泄漏进来会让子项收缩成 fit-content —— meta 条因此塌成
     竖长窄卡。显式 stretch 让 top / main / meta 条都占满页头宽度。 */
  align-items: stretch;
  gap: var(--space-3);
}
.page-header-top {
  display: flex;
  align-items: center;
}
.page-header-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}
.header-text {
  min-width: 0;
}
.header-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-shrink: 0;
}
.header-title-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.title-icon {
  flex-shrink: 0;
  color: var(--c-text-tertiary);
}
.page-title {
  font-size: var(--text-2xl);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  margin: 0;
  color: var(--c-text-primary);
}
.header-sub {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  margin-top: var(--space-1);
}
.header-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
.chip {
  /* 细边框小药丸 —— 无底色,字号 token 名为 --c-text-2xs(--text-2xs 不存在)。 */
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--c-text-2xs);
  color: var(--c-text-secondary);
  background: transparent;
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-full);
  padding: 2px 10px;
}
.chip-num {
  font-weight: 600;
  color: var(--c-text-primary);
  font-variant-numeric: tabular-nums;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  height: 22px;
  font-size: var(--text-xs);
  /* tokens.css 的胶囊圆角 token 是 --radius-full(--radius-pill 不存在)。 */
  border-radius: var(--radius-full);
  border: 1px solid var(--c-border-subtle);
  background: var(--c-bg-elevated);
  font-family: var(--font-mono);
  text-transform: lowercase;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--c-text-tertiary);
}
.chip-success {
  color: var(--c-success);
  border-color: color-mix(in srgb, var(--c-success) 30%, transparent);
  background: var(--c-success-bg);
}
.chip-success .status-dot {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
  animation: cl-breathe 2.4s ease-in-out infinite;
}
.chip-danger {
  color: var(--c-danger);
  border-color: color-mix(in srgb, var(--c-danger) 30%, transparent);
  background: var(--c-danger-bg);
}
.chip-danger .status-dot {
  background: var(--c-danger);
}
.chip-warn {
  color: var(--c-warning);
  border-color: color-mix(in srgb, var(--c-warning) 30%, transparent);
  background: var(--c-warning-bg);
}
.chip-warn .status-dot {
  background: var(--c-warning);
  --cl-pulse-color: color-mix(in srgb, var(--c-warning) 45%, transparent);
  animation: cl-breathe 3.2s ease-in-out infinite;
}
.chip-default {
  color: var(--c-text-secondary);
}

/* 横向 meta 条 —— 页头底部一行,六项「小灰 label 在上 + 值在下」横排占满宽度。
   无卡片底色,仅上方 hairline + 项间 hairline 竖分隔。 */
.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0;
  width: 100%;
  margin: var(--space-2) 0 0;
  padding: var(--space-3) 0 0;
  border-top: 1px solid var(--c-border-subtle);
}
.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 var(--space-4);
  border-right: 1px solid var(--c-border-subtle);
}
.meta-item:first-child {
  padding-left: 0;
}
.meta-item:last-child {
  border-right: none;
}
.meta-item dt {
  /* tokens.css 里该字号 token 的名字是 --c-text-2xs(--text-2xs 不存在,
     直接引用会回退成继承字号,小 label 就不小了)。 */
  font-size: var(--c-text-2xs);
  color: var(--c-text-tertiary);
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
  margin: 0;
}
.meta-item dd {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--c-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mono {
  font-family: var(--font-mono);
}
.tabular {
  font-variant-numeric: tabular-nums;
}
.muted {
  color: var(--c-text-secondary);
}

.tabs-wrap {
  background: var(--c-bg-elevated);
}

/* ── GPU cards ───────────────────────────────────────────────────────── */
.gpu-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-3);
}
.gpu-card {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.gpu-card:hover {
  border-color: color-mix(in srgb, var(--c-accent) 30%, var(--c-border-subtle));
}
.gpu-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
.gpu-id {
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-2);
  min-width: 0;
}
.gpu-index {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.gpu-model {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--c-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gpu-uuid {
  font-size: var(--text-2xs);
  color: var(--c-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
}
.stat-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--c-bg-sunken);
  border-radius: var(--radius-md);
  border: 1px solid var(--c-border-subtle);
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.stat-label {
  font-size: var(--text-2xs);
  color: var(--c-text-tertiary);
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
}
.stat-value {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--c-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stat-unit {
  font-weight: 400;
  color: var(--c-text-tertiary);
  margin-left: 2px;
  font-size: var(--text-xs);
}
.tone-info {
  color: var(--c-text-primary);
}
.tone-warn {
  color: var(--c-warning);
}
.tone-danger {
  color: var(--c-danger);
}

.mem-bar {
  height: 6px;
  background: var(--c-bg-sunken);
  border-radius: var(--radius-pill);
  overflow: hidden;
  border: 1px solid var(--c-border-subtle);
}
.mem-bar-fill {
  height: 100%;
  background: var(--c-text-primary);
  transition: width 200ms ease;
}

/* ── Tables / lists shared ───────────────────────────────────────────── */
.table-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
  margin-top: var(--space-3);
}
.empty-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  margin-top: var(--space-3);
}

/* ── PA list ─────────────────────────────────────────────────────────── */
.pas-bar,
.cap-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
}
.bar-hint {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  line-height: var(--leading-snug);
  flex: 1;
}
.audit-link {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: var(--text-xs);
  color: var(--c-text-link);
  text-decoration: none;
  white-space: nowrap;
}
.audit-link:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}

.pa-list,
.cap-list {
  display: flex;
  flex-direction: column;
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  margin-top: var(--space-3);
  overflow: hidden;
}
.pa-row,
.cap-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--c-border-subtle);
}
.cap-row {
  display: grid;
  grid-template-columns: minmax(14rem, 1fr) 2fr auto;
}
.pa-row:last-child,
.cap-row:last-child {
  border-bottom: none;
}
.pa-row:hover,
.cap-row:hover {
  background: var(--c-bg-sunken);
}
.pa-left {
  flex: 1;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.pa-name {
  font-size: var(--text-sm);
  color: var(--c-text-primary);
}
.pa-actions {
  display: flex;
  gap: var(--space-1);
}

.cap-name {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
}
.cap-notes {
  max-width: 36rem;
}

.admin-hint {
  margin-top: var(--space-3);
}

/* ── Modal shared ────────────────────────────────────────────────────── */
.modal-blurb {
  margin: 0 0 var(--space-3);
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  line-height: var(--leading-snug);
}
.modal-blurb code {
  font-family: var(--font-mono);
  font-size: 0.95em;
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  padding: 1px 4px;
  border-radius: var(--radius-sm);
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--c-border-subtle);
}
.form-row {
  display: grid;
  gap: var(--space-1);
  margin-bottom: var(--space-3);
}
.form-row label {
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}
.select-native {
  width: 100%;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  border: 1px solid var(--c-border-input);
  background: var(--c-bg-base);
  color: var(--c-text-primary);
  font-family: inherit;
  font-size: var(--text-sm);
}
.select-native:focus {
  outline: none;
  border-color: var(--c-border-focus);
}
.ack-line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
  font-size: var(--text-sm);
}
.regen-result {
  display: grid;
  gap: var(--space-3);
}
.regen-meta {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}

/* 减弱动效:状态药丸内的脉搏点静止。
   .cl-enter / .cl-pulse / .cl-lift / .cl-nudge 的退化由全局 main.css 处理。 */
@media (prefers-reduced-motion: reduce) {
  .chip-success .status-dot,
  .chip-warn .status-dot {
    animation: none;
  }
}
</style>

<style>
.page .table-wrap .n-data-table .n-data-table-th,
.page .table-wrap .n-data-table .n-data-table-td {
  font-size: var(--text-sm);
  padding: 10px 12px;
}
.page .table-wrap .n-data-table .n-data-table-th {
  background: var(--c-bg-sunken);
  color: var(--c-text-secondary);
  font-weight: 500;
  border-bottom: 1px solid var(--c-border-subtle);
}
.page .table-wrap .n-data-table .mono {
  font-family: var(--font-mono);
}
.page .table-wrap .n-data-table .muted {
  color: var(--c-text-secondary);
}
</style>
