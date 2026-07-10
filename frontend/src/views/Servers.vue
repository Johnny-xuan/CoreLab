<script setup lang="ts">
/**
 * Servers — v2 机架卡片墙:每台节点一张「机架卡」,整卡点击 → 详情。
 *
 * + Invite (lab_admin) shows the one-shot install snippet modal. The
 * form leads with **display_name** (required, human-meaningful) and
 * makes **hostname** optional — the agent auto-reports its hostname on
 * first heartbeat, so admins should rarely need to type it. (P-06.)
 */

import { computed, onMounted, ref } from 'vue';
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSpin,
  useMessage,
  type FormInst,
} from 'naive-ui';
import { useRouter } from 'vue-router';
import { extractDetail } from '@/utils/extractDetail';
import { Clipboard, Plus, Server, ServerCog } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import { useAuthStore } from '@/stores/auth';
import { createServer, listServers, type ServerRead } from '@/api/servers';
import { timeAgo, formatDateTime } from '@/utils/timeago';

const auth = useAuthStore();
const router = useRouter();
const message = useMessage();

const servers = ref<ServerRead[]>([]);
const loading = ref(false);
const inviteOpen = ref(false);
const inviteSubmitting = ref(false);
const inviteFormRef = ref<FormInst | null>(null);
const invitePayload = ref({ hostname: '', display_name: '' });
const inviteResult = ref<{
  enrollmentToken: string;
  installSnippet: string;
  serverId: number;
} | null>(null);

async function refresh(): Promise<void> {
  loading.value = true;
  try {
    servers.value = await listServers();
  } catch (err) {
    message.error(extractDetail(err, '加载失败'));
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);

// ── Pagebar summary (presentational only — drives the inline counts in
// the shared .cl-pagebar meta). Pure derived counts; no effect on data /
// behaviour. ───────────────────────────────────────────────────────────
const totalCount = computed(() => servers.value.length);
const onlineCount = computed(() => servers.value.filter((s) => s.status === 'online').length);
const maintenanceCount = computed(
  () => servers.value.filter((s) => s.status === 'maintenance').length,
);
const offlineCount = computed(
  () => servers.value.filter((s) => s.status === 'offline' || s.status === 'pending').length,
);

// ── Card presentation helpers (纯展示,不碰数据) ─────────────────────

/** 装饰用机架槽位数 — 列表接口没有 GPU 数,固定 4 槽。 */
const SLOT_COUNT = 4;

/** 状态点的样式类:online 呼吸绿点,其余静态色点。 */
function statusDotClass(status: ServerRead['status']): string {
  if (status === 'online') return 'st-dot st-dot-online cl-pulse';
  if (status === 'maintenance') return 'st-dot st-dot-maint';
  if (status === 'pending') return 'st-dot st-dot-pending';
  return 'st-dot st-dot-off';
}

/** 卡片主标题:优先显示名称,缺省回退主机名 / ID。 */
function cardTitle(srv: ServerRead): string {
  return srv.display_name || srv.hostname || `服务器 #${srv.id}`;
}

// 整卡跳转 — 与旧表格 row-props 的 onClick 完全同一函数体。
function openRow(row: ServerRead): void {
  void router.push({ name: 'server-detail', params: { id: row.id } });
}

function openInvite(): void {
  invitePayload.value = { hostname: '', display_name: '' };
  inviteResult.value = null;
  inviteOpen.value = true;
}

// display_name is now the required, human-meaningful field. hostname is
// optional — the agent reports its real hostname on first heartbeat.
const inviteRules = {
  display_name: [{ required: true, message: '请输入显示名称', trigger: 'blur' }],
};

async function submitInvite(): Promise<void> {
  if (!inviteFormRef.value) return;
  try {
    await inviteFormRef.value.validate();
  } catch {
    return;
  }
  inviteSubmitting.value = true;
  try {
    // Backend still requires `hostname`; if the admin left it blank we
    // synthesise a placeholder from display_name (slugified) — the agent
    // will overwrite it on its first heartbeat.
    const fallbackHost =
      invitePayload.value.display_name
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9-]+/g, '-') || 'pending';
    const resp = await createServer({
      hostname: invitePayload.value.hostname.trim() || fallbackHost,
      display_name: invitePayload.value.display_name || null,
    });
    inviteResult.value = {
      enrollmentToken: resp.enrollment_token,
      installSnippet: resp.install_snippet,
      serverId: resp.server.id,
    };
    await refresh();
    message.success('服务器已创建,请复制下面的安装命令在目标机器上执行。');
  } catch (err) {
    message.error(extractDetail(err, '创建失败'));
  } finally {
    inviteSubmitting.value = false;
  }
}

async function copySnippet(): Promise<void> {
  if (!inviteResult.value) return;
  try {
    await navigator.clipboard.writeText(inviteResult.value.installSnippet);
    message.success('已复制到剪贴板');
  } catch {
    message.error('无法访问剪贴板,请手动复制');
  }
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <Server :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            服务器
            <span class="cl-pagebar-meta">
              <span class="bar-count"
                >共 <span class="cl-num">{{ totalCount }}</span> 台</span
              >
              <template v-if="onlineCount">
                <span class="meta-dot" aria-hidden="true"></span>
                <span class="bar-count bar-online">
                  <span class="online-dot cl-pulse" aria-hidden="true" />
                  <span class="cl-num">{{ onlineCount }}</span> 在线
                </span>
              </template>
              <template v-if="maintenanceCount">
                <span class="meta-dot" aria-hidden="true"></span>
                <span class="bar-count bar-maint">
                  <span class="cl-num">{{ maintenanceCount }}</span> 维护
                </span>
              </template>
              <template v-if="offlineCount">
                <span class="meta-dot" aria-hidden="true"></span>
                <span class="bar-count">
                  <span class="cl-num">{{ offlineCount }}</span> 离线
                </span>
              </template>
            </span>
          </h1>
          <p class="cl-pagebar-sub">本实验室纳管的算力节点 —— 实时心跳与在线状态</p>
        </div>
        <div v-if="auth.isLabAdmin" class="cl-pagebar-actions">
          <NButton type="primary" @click="openInvite">
            <template #icon>
              <Plus :size="14" :stroke-width="2.25" />
            </template>
            添加服务器
          </NButton>
        </div>
      </header>

      <!-- ── v2 机架卡片墙:表格 → 卡片,整卡可点(原行点击跳转原样迁移) ── -->
      <NSpin :show="loading">
        <div v-if="servers.length" class="rack-wall">
          <article
            v-for="(srv, i) in servers"
            :key="srv.id"
            class="rack-card cl-enter cl-lift"
            :class="`is-${srv.status}`"
            :style="{ '--cl-delay': `${0.06 + Math.min(i, 10) * 0.045}s` }"
            role="link"
            tabindex="0"
            :aria-label="`${cardTitle(srv)}(${srv.status}),查看详情`"
            @click="openRow(srv)"
            @keydown.enter="openRow(srv)"
          >
            <header class="rack-card-top">
              <div class="rack-card-names">
                <div class="rack-card-name">{{ cardTitle(srv) }}</div>
                <div class="rack-card-host">{{ srv.hostname || '—' }}</div>
              </div>
              <span class="rack-card-status">
                <span :class="statusDotClass(srv.status)" aria-hidden="true" />
                {{ srv.status }}
              </span>
            </header>

            <!-- 机架槽位装饰条:online 品牌蓝→绿渐变呼吸,其余熄灭 -->
            <div class="rack-slots" aria-hidden="true">
              <span
                v-for="n in SLOT_COUNT"
                :key="n"
                class="rack-slot"
                :style="{ '--slot-delay': `${(n - 1) * 0.35}s` }"
              />
            </div>

            <footer class="rack-card-meta">
              <span class="rack-card-id cl-num">#{{ srv.id }}</span>
              <span
                class="rack-card-seen cl-num"
                :title="srv.last_heartbeat_at ? formatDateTime(srv.last_heartbeat_at) : undefined"
              >
                <span class="rack-card-seen-label">最近活跃</span>
                {{ timeAgo(srv.last_heartbeat_at) }}
              </span>
            </footer>
          </article>
        </div>

        <div v-else class="rack-empty cl-enter" style="--cl-delay: 0.08s">
          <template v-if="!loading">
            <Server :size="28" :stroke-width="1.5" class="rack-empty-icon" />
            <div class="rack-empty-title">暂无纳管的服务器</div>
            <div class="rack-empty-sub">
              {{
                auth.isLabAdmin
                  ? '点击右上角「添加服务器」生成安装命令,在目标机器上部署 agent。'
                  : '请联系实验室管理员添加算力节点。'
              }}
            </div>
          </template>
        </div>
      </NSpin>

      <NModal v-model:show="inviteOpen" preset="card" title="添加服务器" style="max-width: 36rem">
        <NForm
          v-if="inviteResult === null"
          ref="inviteFormRef"
          :model="invitePayload"
          :rules="inviteRules"
          label-placement="top"
          :show-require-mark="false"
        >
          <NFormItem label="显示名称" path="display_name">
            <NInput v-model:value="invitePayload.display_name" placeholder="GPU Server #1" />
            <template #feedback>
              <span class="helper-text">面向用户的名字,会出现在预约页 / 通知里。</span>
            </template>
          </NFormItem>
          <NFormItem label="主机名(可选)">
            <NInput v-model:value="invitePayload.hostname" placeholder="gpu-server-01" />
            <template #feedback>
              <span class="helper-text"> 可留空 — agent 安装后会自动上报真实主机名。 </span>
            </template>
          </NFormItem>
          <NButton type="primary" block :loading="inviteSubmitting" @click="submitInvite">
            <template #icon>
              <ServerCog :size="14" :stroke-width="2" />
            </template>
            生成安装命令
          </NButton>
        </NForm>

        <div v-else class="invite-result">
          <NAlert type="success" :show-icon="false" title="服务器已创建">
            安装命令只显示一次。请立刻复制到目标服务器上执行,然后启动 agent。
          </NAlert>
          <pre class="snippet">{{ inviteResult.installSnippet }}</pre>
          <div class="actions">
            <NButton @click="copySnippet">
              <template #icon>
                <Clipboard :size="14" :stroke-width="2" />
              </template>
              复制
            </NButton>
            <NButton @click="inviteOpen = false">关闭</NButton>
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
}

/* ── pagebar extras(共享 .cl-pagebar-meta 里的内联计数) ──────────── */
.bar-count {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.meta-dot {
  flex: none;
  width: 3px;
  height: 3px;
  border-radius: var(--radius-full);
  background: var(--c-border-strong);
}
.bar-online {
  color: var(--c-success);
}
.bar-maint {
  color: var(--c-warning);
}
.online-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 45%, transparent);
}

/* ── v2 机架卡片墙 ─────────────────────────────────────────────────── */
.rack-wall {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr));
  gap: var(--space-4);
}

.rack-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  cursor: pointer;
}
.rack-card:hover,
.rack-card:focus-visible {
  border-color: var(--c-accent);
}

.rack-card-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}
.rack-card-names {
  min-width: 0;
}
.rack-card-name {
  font-size: var(--text-sm);
  font-weight: 600;
  letter-spacing: var(--tracking-snug);
  color: var(--c-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rack-card-host {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rack-card-status {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  line-height: 1.6;
}
.is-online .rack-card-status {
  color: var(--c-success);
}
.is-maintenance .rack-card-status {
  color: var(--c-warning);
}
.is-pending .rack-card-status {
  color: var(--c-info);
}

/* 状态点(沿用 v1 dot 语汇;offline 灰、pending info 蓝) */
.st-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.st-dot-online {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}
.st-dot-maint {
  background: var(--c-warning);
}
.st-dot-off {
  background: var(--c-border-strong);
}
.st-dot-pending {
  background: var(--c-info);
}

/* 机架槽位条:凹陷导轨 + 4 根竖条 + 右侧散热孔纹理(纯装饰) */
.rack-slots {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: var(--space-2);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
}
.rack-slots::after {
  content: '';
  flex: 1;
  align-self: stretch;
  margin-left: var(--space-2);
  border-radius: 2px;
  background: repeating-linear-gradient(90deg, var(--c-border-subtle) 0 1px, transparent 1px 6px);
  opacity: 0.8;
}
.rack-slot {
  width: 9px;
  height: 22px;
  border-radius: 2px;
  background: var(--c-border-default);
  opacity: 0.75;
}
.is-online .rack-slot {
  background: linear-gradient(180deg, var(--c-accent), var(--c-success));
  opacity: 1;
  animation: slot-glow 2.4s ease-in-out infinite;
  animation-delay: var(--slot-delay, 0s);
}
.is-maintenance .rack-slot {
  background: var(--c-warning);
  opacity: 0.55;
}
@keyframes slot-glow {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 1;
  }
}

.rack-card-meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}
.rack-card-id {
  color: var(--c-text-secondary);
}
.rack-card-seen {
  color: var(--c-text-secondary);
}
.rack-card-seen-label {
  font-family: var(--font-sans);
  color: var(--c-text-tertiary);
  margin-right: 2px;
}

/* ── 空态 ──────────────────────────────────────────────────────────── */
.rack-empty {
  min-height: 200px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-8);
  border: 1px dashed var(--c-border-default);
  border-radius: var(--radius-lg);
  background: var(--c-bg-elevated);
  text-align: center;
}
.rack-empty-icon {
  color: var(--c-text-tertiary);
}
.rack-empty-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--c-text-primary);
}
.rack-empty-sub {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  max-width: 32em;
}

.helper-text {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}

.invite-result {
  display: grid;
  gap: var(--space-3);
}
.snippet {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  background: var(--c-bg-code);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  margin: 0;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

/* Reduced motion — the online-pulse dots already degrade via the global
   .cl-pulse rule. */
@media (prefers-reduced-motion: reduce) {
  /* 槽位呼吸是循环动效:退化为静态点亮的渐变条。 */
  .is-online .rack-slot {
    animation: none;
    opacity: 0.85;
  }
}
</style>
