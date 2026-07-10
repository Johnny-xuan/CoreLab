<script setup lang="ts">
/**
 * ManageServer — Phase K 切法 C per-server admin workspace.
 *
 * One page per granted server (sidebar entry "lab-gpu-01" routes here
 * with server_id param). Internal NTabs slice the deep management
 * surface into 4–5 panels. Server admin sees the first four; lab_admin
 * also sees Admins (grant/revoke delegation lives there since it's a
 * lab-level decision, not a per-server-admin one).
 *
 * Style mirrors ServerDetail's tab shell so navigating "Server status"
 * (read-only) vs "Manage: server-X" feels like the same surface,
 * scoped differently.
 *
 * v2 去同质化:boxed hero(渐变 + 网格纹理 + 光泽徽标)换成共享紧凑
 * `.cl-pagebar` 页头;信息(状态 tag / chips 计数 / hostname / 说明 /
 * 刷新)全部保留,数据获取与交互逻辑零改动。
 */
import { computed, h, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  NAlert,
  NButton,
  NDataTable,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  useDialog,
  useMessage,
  type DataTableColumns,
} from 'naive-ui';
import {
  ChevronLeft,
  Plus,
  RefreshCw,
  Settings,
  SlidersHorizontal,
  UserCog,
  UserPlus,
} from 'lucide-vue-next';
import { extractDetail } from '@/utils/extractDetail';
import { timeAgo } from '@/utils/timeago';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import AuthorizedKeyInventory from '@/components/serverDetail/AuthorizedKeyInventory.vue';
import OnboardUserDialog from '@/components/serverDetail/OnboardUserDialog.vue';
import ServerOverview from '@/components/manage/ServerOverview.vue';
import { useAuthStore } from '@/stores/auth';

import { getServer, listAdmins, listCapabilities, listGpus, updateCapability } from '@/api/servers';
import { apiClient } from '@/api/client';
import type { CapabilityRead, GpuRead, ServerAdminGrantRead, ServerRead } from '@/api/servers';
import * as paApi from '@/api/physicalAccounts';
import type {
  AuthorizedKeyInventoryEntry,
  AuthorizedKeyReadbackResponse,
  PhysicalAccountRead,
} from '@/api/physicalAccounts';
import { listUsers } from '@/api/users';
import type { UserRead } from '@/api/users';

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const message = useMessage();
const dialog = useDialog();

const serverId = computed(() => Number(route.params.server_id));
const tab = ref<'overview' | 'pas' | 'gpus' | 'keys' | 'caps' | 'activity' | 'admins'>('overview');

const server = ref<ServerRead | null>(null);
const gpus = ref<GpuRead[]>([]);
const pas = ref<PhysicalAccountRead[]>([]);
const keyEntries = ref<AuthorizedKeyInventoryEntry[]>([]);
const keyReadbacks = ref<Record<number, AuthorizedKeyReadbackResponse>>({});
const keyReadbackLoadingPaId = ref<number | null>(null);
const caps = ref<CapabilityRead[]>([]);
const admins = ref<ServerAdminGrantRead[]>([]);
const users = ref<UserRead[]>([]);
const loading = ref(false);

const onboardOpen = ref(false);
const registerPaOpen = ref(false);
const registerPaName = ref('');
const registerPaNotes = ref('');
const registerSubmitting = ref(false);

const grantOpen = ref(false);
const grantUserId = ref<number | null>(null);
const grantNotes = ref('');
const grantSubmitting = ref(false);

async function refresh(): Promise<void> {
  loading.value = true;
  try {
    const [s, g, p, keys, c, a] = await Promise.all([
      getServer(serverId.value),
      listGpus(serverId.value).catch(() => [] as GpuRead[]),
      paApi.listPas(serverId.value).catch(() => [] as PhysicalAccountRead[]),
      paApi
        .listAuthorizedKeyEntries(serverId.value)
        .catch(() => [] as AuthorizedKeyInventoryEntry[]),
      listCapabilities(serverId.value).catch(() => [] as CapabilityRead[]),
      listAdmins(serverId.value).catch(() => [] as ServerAdminGrantRead[]),
    ]);
    server.value = s;
    gpus.value = g;
    pas.value = p;
    keyEntries.value = keys;
    keyReadbacks.value = {};
    caps.value = c;
    admins.value = a;
    if (auth.isLabAdmin) {
      users.value = (await listUsers().catch(() => [])).filter((u) => u.is_active);
    }
  } catch (err) {
    message.error(extractDetail(err, '加载服务器失败'));
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);
watch(serverId, refresh);

const serverLabel = computed(
  () => server.value?.display_name ?? server.value?.hostname ?? `server #${serverId.value}`,
);

// ── 页头汇总(纯展示派生,不碰任何业务逻辑) ──
const statusOnline = computed(() => server.value?.status === 'online');
const capsEnabledCount = computed(() => caps.value.filter((c) => c.is_enabled).length);

// ── PA tab ───────────────────────────────────────
async function submitRegisterPa(): Promise<void> {
  if (!registerPaName.value.match(/^[a-z_][a-z0-9_-]{0,31}$/)) {
    message.warning('linux_username 必须匹配 ^[a-z_][a-z0-9_-]{0,31}$');
    return;
  }
  registerSubmitting.value = true;
  try {
    await paApi.createPa(serverId.value, {
      linux_username: registerPaName.value,
      notes: registerPaNotes.value || null,
    });
    message.success('已登记');
    registerPaOpen.value = false;
    registerPaName.value = '';
    registerPaNotes.value = '';
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '登记失败'));
  } finally {
    registerSubmitting.value = false;
  }
}

const paColumns = computed<DataTableColumns<PhysicalAccountRead>>(() => [
  {
    title: 'Linux 用户',
    key: 'linux_username',
    render: (row) => h('span', { class: 'mono target' }, row.linux_username),
  },
  {
    title: '来源',
    key: 'source',
    width: 180,
    render: (row) => h(NTag, { size: 'small', bordered: false }, () => row.source),
  },
  {
    title: 'UID',
    key: 'uid',
    width: 80,
    render: (row) => h('span', { class: 'mono muted' }, row.uid?.toString() ?? '—'),
  },
  {
    title: '上次发现',
    key: 'last_seen_at',
    width: 130,
    render: (row) =>
      row.last_seen_at === null
        ? h(
            'span',
            { class: 'muted', title: '从未被 agent 扫描发现(手工登记或 agent 未上线)' },
            '—',
          )
        : h('span', { class: 'muted', title: row.last_seen_at }, timeAgo(row.last_seen_at)),
  },
  {
    title: '状态',
    key: 'is_active',
    width: 100,
    render: (row) =>
      h(
        NTag,
        { type: row.is_active ? 'success' : 'default', size: 'small', bordered: false },
        () => (row.is_active ? '启用' : '停用'),
      ),
  },
]);

async function retryKeyEntry(entry: AuthorizedKeyInventoryEntry): Promise<void> {
  try {
    const result = await paApi.retryAuthorizedKeyPush(entry.physical_account_id, entry.entry_id);
    const ok = result.key_push_outcome.ok === true;
    message[ok ? 'success' : 'warning'](ok ? '已重推 authorized_keys' : '重推未成功');
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '重推失败'));
  }
}

async function readAuthorizedKeys(entry: AuthorizedKeyInventoryEntry): Promise<void> {
  keyReadbackLoadingPaId.value = entry.physical_account_id;
  try {
    const result = await paApi.readAuthorizedKeysFromHost(entry.physical_account_id);
    keyReadbacks.value = {
      ...keyReadbacks.value,
      [entry.physical_account_id]: result,
    };
    message[result.ok ? 'success' : 'warning'](
      result.ok ? '已读取 host authorized_keys' : 'host 读取未成功',
    );
  } catch (err) {
    message.error(extractDetail(err, '读取 host authorized_keys 失败'));
  } finally {
    keyReadbackLoadingPaId.value = null;
  }
}

// ── GPUs tab ─────────────────────────────────────
function openGpu(gpu: GpuRead): void {
  router.push({
    name: 'manage-server-gpu',
    params: { server_id: serverId.value, gpu_index: gpu.gpu_index },
  });
}

const gpuColumns = computed<DataTableColumns<GpuRead>>(() => [
  {
    title: '编号',
    key: 'gpu_index',
    width: 70,
    render: (row) => h('span', { class: 'mono target' }, `GPU ${row.gpu_index}`),
  },
  {
    title: '型号',
    key: 'model',
    render: (row) => h('span', { class: 'mono' }, row.model ?? 'unknown'),
  },
  {
    title: '显存',
    key: 'memory_total_mb',
    width: 110,
    render: (row) =>
      h(
        'span',
        { class: 'mono muted' },
        row.memory_total_mb ? `${(row.memory_total_mb / 1024).toFixed(0)} GB` : '—',
      ),
  },
  {
    title: '利用率 %',
    key: 'util_pct',
    width: 90,
    render: (row) =>
      h('span', { class: 'mono muted' }, row.util_pct !== null ? `${row.util_pct}%` : '—'),
  },
  {
    title: '最近采样',
    key: 'last_updated_at',
    render: (row) =>
      h(
        'span',
        { class: 'mono muted' },
        row.last_updated_at ? new Date(row.last_updated_at).toLocaleString() : '—',
      ),
  },
]);

// ── Capabilities tab ─────────────────────────────
async function toggleCap(cap: CapabilityRead): Promise<void> {
  const nextEnabled = !cap.is_enabled;
  const needsNotes = cap.is_dangerous && nextEnabled;
  let notes: string | null = cap.notes;
  if (needsNotes) {
    const input = window.prompt(
      `${cap.capability_key} 是危险 capability — 写 ≥ 10 字符理由(记入审计日志)`,
      cap.notes ?? '',
    );
    if (input === null) return;
    if (input.trim().length < 10) {
      message.warning('理由不足 10 字符');
      return;
    }
    notes = input.trim();
  }
  try {
    await updateCapability(serverId.value, cap.capability_key, {
      enabled: nextEnabled,
      notes,
    });
    message.success(`${cap.capability_key} 已${nextEnabled ? '开启' : '关闭'}`);
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '更新失败'));
  }
}

const capColumns = computed<DataTableColumns<CapabilityRead>>(() => [
  {
    title: 'Capability',
    key: 'capability_key',
    render: (row) =>
      h('div', { class: 'cell-cap' }, [
        h('span', { class: 'mono target' }, row.capability_key),
        row.is_dangerous
          ? h(NTag, { type: 'warning', size: 'tiny', bordered: false }, () => '危险')
          : null,
      ]),
  },
  {
    title: '状态',
    key: 'is_enabled',
    width: 110,
    render: (row) =>
      h(
        NTag,
        { type: row.is_enabled ? 'success' : 'default', size: 'small', bordered: false },
        () => (row.is_enabled ? '已开启' : '已关闭'),
      ),
  },
  { title: '备注', key: 'notes', render: (row) => row.notes ?? '—' },
  {
    title: '',
    key: 'actions',
    width: 120,
    render: (row) =>
      h(NButton, { size: 'tiny', onClick: () => toggleCap(row) }, () =>
        row.is_enabled ? '关闭' : '开启',
      ),
  },
]);

// ── Admins tab (lab_admin only) ──────────────────
const userById = computed(() => new Map(users.value.map((u) => [u.id, u])));
function userLabel(id: number): string {
  const u = userById.value.get(id);
  return u !== undefined ? `${u.display_name} @${u.username}` : `user #${id}`;
}
const userOptions = computed(() =>
  users.value
    .filter((u) => !admins.value.some((g) => g.user_id === u.id))
    .map((u) => ({ label: `${u.display_name} (@${u.username})`, value: u.id })),
);

async function submitGrant(): Promise<void> {
  if (grantUserId.value === null) return;
  grantSubmitting.value = true;
  try {
    await apiClient.post(`/servers/${serverId.value}/admins`, {
      user_id: grantUserId.value,
      notes: grantNotes.value || null,
    });
    message.success('已授权');
    grantOpen.value = false;
    grantUserId.value = null;
    grantNotes.value = '';
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '授权失败'));
  } finally {
    grantSubmitting.value = false;
  }
}

function revokeGrant(g: ServerAdminGrantRead): void {
  dialog.warning({
    title: '撤销 server admin?',
    content: `撤销 ${userLabel(g.user_id)} 对 ${serverLabel.value} 的 admin 权限。`,
    positiveText: '撤销',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await apiClient.delete(`/servers/${serverId.value}/admins/${g.user_id}`);
        message.success('已撤销');
        await refresh();
      } catch (err) {
        message.error(extractDetail(err, '撤销失败'));
      }
    },
  });
}

const adminColumns = computed<DataTableColumns<ServerAdminGrantRead>>(() => [
  {
    title: '用户',
    key: 'user_id',
    render: (row) => h('span', { class: 'mono target' }, userLabel(row.user_id)),
  },
  {
    title: '授权时间',
    key: 'granted_at',
    width: 160,
    render: (row) => h('span', { class: 'mono muted' }, new Date(row.granted_at).toLocaleString()),
  },
  { title: '备注', key: 'notes', render: (row) => row.notes ?? '—' },
  {
    title: '',
    key: 'actions',
    width: 100,
    render: (row) =>
      h(
        NButton,
        { size: 'tiny', quaternary: true, type: 'error', onClick: () => revokeGrant(row) },
        () => '撤销',
      ),
  },
]);
</script>

<template>
  <AppLayout>
    <div class="page">
      <NSpin v-if="loading && server === null" />
      <template v-else-if="server !== null">
        <div class="page-header-top cl-enter">
          <NButton size="tiny" quaternary @click="$router.push({ name: 'managed-servers' })">
            <template #icon>
              <ChevronLeft :size="14" :stroke-width="2" />
            </template>
            我管理的服务器
          </NButton>
        </div>

        <header class="cl-pagebar cl-enter" style="--cl-delay: 0.08s">
          <div class="cl-pagebar-icon">
            <SlidersHorizontal :size="20" :stroke-width="1.75" />
            <span
              class="status-dot"
              :class="statusOnline ? 'on cl-pulse' : ''"
              aria-hidden="true"
            />
          </div>
          <div class="cl-pagebar-body">
            <h1 class="cl-pagebar-title">
              <span class="title-text">
                <span class="muted-pre">管理:</span> {{ serverLabel }}
              </span>
              <NTag
                :type="server.status === 'online' ? 'success' : 'default'"
                size="small"
                bordered
              >
                {{ server.status }}
              </NTag>
              <span class="cl-pagebar-meta">
                <span
                  ><span class="meta-num cl-num">{{ gpus.length }}</span> 个 GPU</span
                >
                <span class="meta-sep" aria-hidden="true">·</span>
                <span
                  ><span class="meta-num cl-num">{{ pas.length }}</span> 个 Linux 账号</span
                >
                <span class="meta-sep" aria-hidden="true">·</span>
                <span>
                  <span class="meta-num cl-num">{{ capsEnabledCount }}/{{ caps.length }}</span>
                  capability 已开启
                </span>
                <template v-if="admins.length">
                  <span class="meta-sep" aria-hidden="true">·</span>
                  <span
                    ><span class="meta-num cl-num">{{ admins.length }}</span> 位管理员</span
                  >
                </template>
              </span>
            </h1>
            <p class="cl-pagebar-sub">
              <span class="mono">{{ server.hostname }}</span>
              <span class="muted">
                · 这台服务器的管理操作台 —— Linux 账号、GPU、capability 与管理员委派</span
              >
            </p>
          </div>
          <div class="cl-pagebar-actions">
            <NButton size="small" :loading="loading" @click="refresh">
              <template #icon>
                <RefreshCw :size="13" :stroke-width="1.75" />
              </template>
              刷新
            </NButton>
          </div>
        </header>

        <div class="tabs-wrap cl-enter" style="--cl-delay: 0.16s">
          <NTabs v-model:value="tab" type="line" animated size="small">
            <!-- ── Overview ─────────────────────────── -->
            <NTabPane name="overview" tab="概览">
              <ServerOverview :server-id="serverId" :server="server" :gpus="gpus" />
            </NTabPane>

            <!-- ── Linux accounts ───────────────────── -->
            <NTabPane name="pas" :tab="`Linux 账号 (${pas.length})`">
              <div class="bar">
                <p class="bar-hint">
                  这台 server 上已登记的 Linux 账号。Onboard 一次性建账号 + 推用户公钥(情形 3)。
                </p>
                <div class="bar-actions">
                  <NButton size="small" @click="registerPaOpen = true">
                    <template #icon>
                      <UserPlus :size="13" :stroke-width="1.75" />
                    </template>
                    登记 PA
                  </NButton>
                  <NButton size="small" type="primary" @click="onboardOpen = true">
                    <template #icon>
                      <Plus :size="14" :stroke-width="2.25" />
                    </template>
                    Onboard 用户
                  </NButton>
                </div>
              </div>
              <div v-if="pas.length === 0" class="empty-wrap cl-lift">
                <CleanEmpty
                  :icon="UserPlus"
                  title="还没有 Linux 账号"
                  description="点「Onboard 用户」一站式创建,或「登记 PA」登记一个已存在的系统用户。"
                  compact
                />
              </div>
              <div v-else class="table-wrap cl-lift">
                <NDataTable
                  :columns="paColumns"
                  :data="pas"
                  :bordered="false"
                  :single-line="false"
                  size="small"
                />
              </div>
            </NTabPane>

            <!-- ── Authorized keys ─────────────────── -->
            <NTabPane name="keys" :tab="`Keys (${keyEntries.length})`">
              <AuthorizedKeyInventory
                :entries="keyEntries"
                :loading="loading"
                :readbacks="keyReadbacks"
                :readback-loading-pa-id="keyReadbackLoadingPaId"
                @retry="retryKeyEntry"
                @readback="readAuthorizedKeys"
              />
            </NTabPane>

            <!-- ── GPUs ─────────────────────────────── -->
            <NTabPane name="gpus" :tab="`GPUs (${gpus.length})`">
              <p class="page-blurb">
                每张 GPU 的使用账本 — 行点击进详情(谁在用 / 最近 7d 排行 / 最近脚本)。
              </p>
              <div v-if="gpus.length === 0" class="empty-wrap cl-lift">
                <CleanEmpty
                  :icon="Settings"
                  title="还没有 GPU"
                  description="agent 上线后会通过 capability seed 自动报告 GPU 列表;请确认 agent 是否在线。"
                  compact
                />
              </div>
              <div v-else class="table-wrap cl-lift">
                <NDataTable
                  :columns="gpuColumns"
                  :data="gpus"
                  :row-props="(row) => ({ style: 'cursor: pointer', onClick: () => openGpu(row) })"
                  :bordered="false"
                  :single-line="false"
                  size="small"
                />
              </div>
            </NTabPane>

            <!-- ── Capabilities ─────────────────────── -->
            <NTabPane name="caps" :tab="`Capability (${caps.length})`">
              <div class="bar">
                <p class="bar-hint">
                  agent 端 capability 总开关 — 危险 capability(useradd / kill_process)开启时 必须填
                  ≥ 10 字符理由,记入 audit log。
                </p>
              </div>
              <div v-if="caps.length === 0" class="empty-wrap cl-lift">
                <CleanEmpty
                  :icon="Settings"
                  title="还没有 capability"
                  description="agent 上线时会自动 seed 10 条,刷新看看。"
                  compact
                />
              </div>
              <div v-else class="table-wrap cl-lift">
                <NDataTable
                  :columns="capColumns"
                  :data="caps"
                  :bordered="false"
                  :single-line="false"
                  size="small"
                />
              </div>
            </NTabPane>

            <!-- ── Activity ─────────────────────────── -->
            <NTabPane name="activity" :tab="`活动`">
              <NAlert type="info" :show-icon="true">
                这台 server 的审计日志与告警时间线集中在 Lab 级页面查看:
                <RouterLink :to="{ name: 'lab-audit', query: { target_server_id: serverId } }">
                  审计日志
                </RouterLink>
                /
                <RouterLink :to="{ name: 'lab-alerts', query: { server_id: serverId } }">
                  告警
                </RouterLink>
              </NAlert>
            </NTabPane>

            <!-- ── Admins (lab_admin only) ──────────── -->
            <NTabPane v-if="auth.isLabAdmin" name="admins" :tab="`管理员 (${admins.length})`">
              <div class="bar">
                <p class="bar-hint">
                  这台 server 的 server admin 列表 — 委派给谁,谁就能进 ManageServer 写。lab_admin
                  隐式管所有 server,这里只委派 role=user 的 user。
                </p>
                <NButton size="small" type="primary" @click="grantOpen = true">
                  <template #icon>
                    <UserCog :size="13" :stroke-width="1.75" />
                  </template>
                  授予管理员
                </NButton>
              </div>
              <div v-if="admins.length === 0" class="empty-wrap cl-lift">
                <CleanEmpty
                  :icon="UserCog"
                  title="还没有委派的管理员"
                  description="目前只有 lab admin 隐式管这台。点「授予管理员」委派给某个 user。"
                  compact
                />
              </div>
              <div v-else class="table-wrap cl-lift">
                <NDataTable
                  :columns="adminColumns"
                  :data="admins"
                  :bordered="false"
                  :single-line="false"
                  size="small"
                />
              </div>
            </NTabPane>
          </NTabs>
        </div>

        <!-- ── modals ─────────────────────────────── -->
        <OnboardUserDialog
          :server-id="serverId"
          :show="onboardOpen"
          @done="((onboardOpen = false), refresh())"
          @cancel="onboardOpen = false"
        />

        <NModal
          v-model:show="registerPaOpen"
          preset="card"
          title="登记 Linux 账号"
          style="max-width: 28rem"
        >
          <p class="modal-blurb">
            登记一个 server 上 <strong>已经存在</strong> 但 CoreLab 还没记录的 Linux
            账号。如果账号还不存在,改用「Onboard 用户」。
          </p>
          <div class="form-row">
            <label>linux_username</label>
            <NInput v-model:value="registerPaName" placeholder="yang_lab" />
          </div>
          <div class="form-row">
            <label>notes(可选)</label>
            <NInput v-model:value="registerPaNotes" placeholder="例如:实验组共享账号" />
          </div>
          <template #footer>
            <NSpace justify="end">
              <NButton @click="registerPaOpen = false">取消</NButton>
              <NButton type="primary" :loading="registerSubmitting" @click="submitRegisterPa">
                登记
              </NButton>
            </NSpace>
          </template>
        </NModal>

        <NModal
          v-model:show="grantOpen"
          preset="card"
          :title="`授予服务器管理员 — ${serverLabel}`"
          style="max-width: 28rem"
        >
          <p class="modal-blurb">
            被授权的 user 将获得这台 server 的 ManageServer 写权(PA / capabilities / 关联申请审批)。
          </p>
          <div class="form-row">
            <label>用户</label>
            <NSelect
              v-model:value="grantUserId"
              :options="userOptions"
              placeholder="选 lab 内的一个 user"
            />
          </div>
          <div class="form-row">
            <label>备注(审计)</label>
            <NInput
              v-model:value="grantNotes"
              type="textarea"
              :autosize="{ minRows: 2 }"
              placeholder="例:负责日常维护"
            />
          </div>
          <template #footer>
            <NSpace justify="end">
              <NButton @click="grantOpen = false">取消</NButton>
              <NButton
                type="primary"
                :loading="grantSubmitting"
                :disabled="grantUserId === null"
                @click="submitGrant"
              >
                授予
              </NButton>
            </NSpace>
          </template>
        </NModal>
      </template>
    </div>
  </AppLayout>
</template>

<style scoped>
/* 页面骨架交给全局 .page;页头是共享 .cl-pagebar(自带下边距 + 分隔线),
   这里只补返回链接的间距和本页特有的小件。 */
.page-header-top {
  display: flex;
  align-items: center;
  margin-bottom: var(--space-2);
}

/* ── 页头(紧凑 .cl-pagebar) ──────────────────────────────────── */
/* 标题行内容多(前缀 + 名称 + 状态 tag + meta 计数),窄屏允许换行 */
.cl-pagebar-title,
.cl-pagebar-meta {
  flex-wrap: wrap;
}
.title-text {
  min-width: 0;
}
.muted-pre {
  color: var(--c-text-tertiary);
  font-weight: 400;
}
.meta-num {
  font-weight: 600;
  color: var(--c-text-secondary);
}
.meta-sep {
  color: var(--c-text-disabled);
}
/* 图标块右上角的小状态点 —— 替代旧 hero 的 LED,在线时 cl-pulse 呼吸 */
.cl-pagebar-icon {
  position: relative;
}
.status-dot {
  position: absolute;
  top: -3px;
  right: -3px;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--c-text-disabled);
  border: 2px solid var(--c-bg-base);
}
.status-dot.on {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 45%, transparent);
}
.page-blurb {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
.mono {
  font-family: var(--font-mono);
}
.muted {
  color: var(--c-text-tertiary);
}
.tabs-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  padding: var(--space-2) var(--space-5) var(--space-5);
  box-shadow: var(--shadow-sm);
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) 0;
}
.bar-actions {
  display: flex;
  gap: var(--space-2);
}
.bar-hint {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  max-width: 720px;
}
.table-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.empty-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
}
.modal-blurb {
  margin: 0 0 var(--space-3);
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  line-height: var(--leading-snug);
}
.form-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}
.form-row label {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
code {
  font-family: var(--font-mono);
  background: var(--c-bg-sunken);
  padding: 0 var(--space-1);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
}
/* reduced-motion:cl-enter / cl-pulse 的退化由 main.css 全局兜底,本页无循环动效。 */
</style>

<style>
.page .table-wrap .n-data-table .n-data-table-th,
.page .table-wrap .n-data-table .n-data-table-td {
  font-size: var(--text-sm);
  padding: 10px 12px;
}
.page .table-wrap .n-data-table .n-data-table-td {
  transition: background-color 0.15s ease;
}
.page .table-wrap .n-data-table .n-data-table-tr:hover .n-data-table-td {
  background-color: color-mix(in srgb, var(--c-accent) 4%, var(--c-bg-elevated));
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
.page .table-wrap .target {
  color: var(--c-text-primary);
}
.page .table-wrap .cell-cap {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
</style>
