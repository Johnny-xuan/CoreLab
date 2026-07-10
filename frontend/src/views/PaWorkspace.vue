<script setup lang="ts">
/**
 * PaWorkspace — ``/me/accounts/:pa_id`` shell.
 *
 * Five tabs per docs/07-ui-design.md §6.2b: overview, reservations,
 * scripts, server status, and settings. Reservation/script routes now
 * live as concrete pages and this shell keeps the PA context stable.
 *
 * Visual: 无框身份头 —— mono ``user@server`` 标题(终端闪烁光标)+
 * source tag + 内联 meta + 细边框 chips,不用装饰盒子。Underline tabs,
 * NCard 8px radius / 1px border / no shadow for tab panels. Motion
 * drawn from the shared cl- vocabulary in styles/main.css.
 */

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  NAlert,
  NButton,
  NDescriptions,
  NDescriptionsItem,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';
import { ChevronRight } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import { useAuthStore } from '@/stores/auth';
import { useWorkspaceStore } from '@/stores/workspace';
import * as accountLinksApi from '@/api/accountLinks';
import { getServer, type ServerRead } from '@/api/servers';

const route = useRoute();
const router = useRouter();
const message = useMessage();
const dialog = useDialog();
const auth = useAuthStore();
const ws = useWorkspaceStore();

const paId = computed(() => Number(route.params.pa_id));
type WorkspaceTab = 'overview' | 'reserve' | 'my-reservations' | 'server' | 'settings';
const VALID_TABS: WorkspaceTab[] = ['overview', 'reserve', 'my-reservations', 'server', 'settings'];

function tabFromQuery(q: unknown): WorkspaceTab {
  if (typeof q === 'string' && (VALID_TABS as string[]).includes(q)) return q as WorkspaceTab;
  return 'overview';
}
const tab = ref<WorkspaceTab>(tabFromQuery(route.query.tab));
watch(
  () => route.query.tab,
  (q) => {
    tab.value = tabFromQuery(q);
  },
);
watch(tab, (next) => {
  if (next === 'overview') {
    if (route.query.tab !== undefined) {
      void router.replace({ query: { ...route.query, tab: undefined } });
    }
  } else if (route.query.tab !== next) {
    void router.replace({ query: { ...route.query, tab: next } });
  }
});
const server = ref<ServerRead | null>(null);
const loadingServer = ref(false);

const entry = computed(() => ws.workspaces.find((w) => w.pa.id === paId.value) ?? null);

// 刷新过一次之后才允许判定"工作区不存在"——避免瞬时请求失败把人踢出去。
const wsReady = ref(false);

onMounted(async () => {
  if (ws.workspaces.length === 0) {
    await ws.refresh().catch(() => undefined);
  }
  wsReady.value = true;
  if (entry.value === null) return; // 模板原地显示"未找到工作区",不再跳转
  ws.setCurrent(paId.value);
  await loadServer();
});

// 工作区列表晚到(或重试成功)时自动恢复,无需用户刷新。
watch(entry, async (e, prev) => {
  if (e !== null && prev === null) {
    ws.setCurrent(paId.value);
    await loadServer();
  }
});

watch(paId, async (id) => {
  if (entry.value === null) return;
  ws.setCurrent(id);
  await loadServer();
});

async function loadServer(): Promise<void> {
  const e = entry.value;
  if (e === null) return;
  loadingServer.value = true;
  try {
    server.value = await getServer(e.pa.server_id);
  } catch (err) {
    message.error(extractDetail(err, '服务器加载失败'));
  } finally {
    loadingServer.value = false;
  }
}

function confirmRevoke(): void {
  const e = entry.value;
  if (e === null) return;
  dialog.warning({
    title: '解除此关联?',
    content:
      '解除后,你将无法再通过 CoreLab 以该 Linux 账号的身份操作。' +
      '之后你可以重新完成验证流程来再次关联它。',
    positiveText: '解除关联',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await accountLinksApi.revokeLink(e.link.id, 'self');
        message.success('已解除关联。');
        await ws.refresh();
        await router.replace({ name: 'dashboard' });
      } catch (err) {
        message.error(extractDetail(err, '解除关联失败'));
      }
    },
  });
}

const sourceTagType = computed<'success' | 'warning' | 'default'>(() => {
  const src = entry.value?.link.source;
  if (src === 'ssh_challenge' || src === 'admin_prepared_then_ssh') return 'success';
  if (src === 'password_pam') return 'success';
  if (src === 'admin_declared') return 'warning';
  return 'default';
});

// ── 以下均为页头展示用的纯展示 computed —— 不触碰任何业务逻辑 ──────

/** 服务器状态 → 状态 chip 的色调。 */
const serverTone = computed<'ok' | 'bad' | 'warn' | 'unknown'>(() => {
  const s = server.value?.status;
  if (s === 'online') return 'ok';
  if (s === 'offline') return 'bad';
  if (s) return 'warn';
  return 'unknown';
});

/** 默认 shell 的短名(/usr/bin/zsh → zsh),无则不显示该 chip。 */
const shellName = computed<string | null>(() => {
  const sh = entry.value?.pa.default_shell;
  if (!sh) return null;
  const parts = sh.split('/').filter(Boolean);
  return parts[parts.length - 1] ?? sh;
});

/** 关联存续天数 —— 仅用于 hero chip 展示。 */
const linkedDays = computed<number | null>(() => {
  const iso = entry.value?.link.established_at;
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return null;
  return Math.max(0, Math.floor(ms / 86_400_000));
});

/** SSH 连接示例(纯展示,由已有数据拼接;服务器未加载完不显示)。 */
const sshHint = computed<string | null>(() => {
  const u = entry.value?.pa.linux_username;
  const h = server.value?.hostname;
  return u && h ? `ssh ${u}@${h}` : null;
});
</script>

<template>
  <AppLayout>
    <div class="page">
      <NSpin v-if="entry === null && !wsReady" />
      <div v-else-if="entry === null" class="ws-missing cl-enter">
        <h2>未找到这个工作区</h2>
        <p>该 Linux 账号可能尚未关联到你的用户、已被解除,或链接里的编号已过期 (PA #{{ paId }})。</p>
        <div class="ws-missing-actions">
          <NButton type="primary" @click="router.push({ name: 'claim-account' })">
            关联 Linux 账号
          </NButton>
          <NButton quaternary @click="router.push({ name: 'dashboard' })">回到仪表盘</NButton>
        </div>
      </div>
      <template v-else>
        <!-- ─── 页头:无框身份区(mono 排版,无装饰盒)──────────── -->
        <header class="hero cl-enter">
          <div class="title-row">
            <code class="title-user">{{ entry.pa.linux_username }}</code>
            <span class="title-at">@</span>
            <span class="title-server">
              {{ server?.display_name || server?.hostname || `服务器 #${entry.pa.server_id}` }}
            </span>
            <NTag :type="sourceTagType" size="small" :bordered="false">
              {{ entry.link.source }}
            </NTag>
          </div>
          <div class="hero-sub">这台服务器上属于你的个人工位 —— 账号信息、连接与预约入口</div>
          <p class="page-subtitle">
            <span class="mono tabular">PA #{{ entry.pa.id }}</span>
            <span class="sep">·</span>
            <span>关联于 {{ entry.link.established_at.replace('T', ' ').slice(0, 16) }}</span>
          </p>
          <div class="hero-chips">
            <span v-if="server !== null" class="chip" :class="`chip-${serverTone}`">
              <span class="chip-dot cl-pulse" />
              <span class="mono">{{ server.status }}</span>
            </span>
            <span v-else class="chip">
              <span class="chip-dot" />
              状态获取中
            </span>
            <span v-if="entry.pa.uid !== null && entry.pa.uid !== undefined" class="chip">
              UID <span class="chip-num mono tabular">{{ entry.pa.uid }}</span>
            </span>
            <span v-if="shellName" class="chip mono">{{ shellName }}</span>
            <span v-if="linkedDays !== null" class="chip">
              已关联 <span class="chip-num tabular">{{ linkedDays }}</span> 天
            </span>
          </div>
        </header>

        <div class="tabs-wrap cl-enter" style="--cl-delay: 0.08s">
          <NTabs v-model:value="tab" type="line" animated size="small">
            <NTabPane name="overview" tab="概览">
              <section class="panel cl-enter cl-lift">
                <div v-if="sshHint" class="ssh-card cl-enter" style="--cl-delay: 0.04s">
                  <span class="ssh-prompt mono" aria-hidden="true">$</span>
                  <code class="ssh-cmd mono">{{ sshHint }}</code>
                  <span class="ssh-note dim">SSH 连接示例</span>
                </div>
                <NDescriptions
                  :column="2"
                  size="small"
                  bordered
                  class="cl-enter"
                  style="--cl-delay: 0.08s"
                >
                  <NDescriptionsItem label="Linux 用户名">
                    <span class="mono">{{ entry.pa.linux_username }}</span>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="UID">
                    <span class="mono">{{ entry.pa.uid ?? '—' }}</span>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="家目录">
                    <span class="mono">{{ entry.pa.home_directory ?? '—' }}</span>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="Shell">
                    <span class="mono">{{ entry.pa.default_shell ?? '—' }}</span>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="关联来源">
                    {{ entry.link.source }}
                  </NDescriptionsItem>
                  <NDescriptionsItem label="建立时间">
                    <span class="mono">{{ entry.link.established_at.replace('T', ' ') }}</span>
                  </NDescriptionsItem>
                </NDescriptions>
                <NAlert
                  v-if="entry.link.source === 'admin_declared'"
                  type="warning"
                  :show-icon="false"
                  class="alert-banner cl-enter"
                  style="--cl-delay: 0.12s"
                >
                  <strong>管理员声明的关联。</strong> 可用于反查和通知, 但在你通过 SSH
                  验证升级此关联之前,平台不会代你运行脚本或下发密钥。
                </NAlert>
              </section>
            </NTabPane>

            <NTabPane name="reserve" tab="预约">
              <section class="panel cl-enter cl-lift">
                <p class="panel-blurb">预约 grid 在独立的全宽路由展开。</p>
                <NButton
                  type="primary"
                  size="small"
                  @click="router.push({ name: 'pa-reserve', params: { pa_id: paId } })"
                >
                  打开预约网格
                  <template #icon>
                    <ChevronRight :size="13" :stroke-width="2" />
                  </template>
                </NButton>
              </section>
            </NTabPane>

            <NTabPane name="my-reservations" tab="我的预约">
              <section class="panel cl-enter cl-lift">
                <p class="panel-blurb">跨 PA 查看全部预约,前往「全部预约」。</p>
                <NButton size="small" @click="router.push({ name: 'all-reservations' })">
                  打开全部预约
                  <template #icon>
                    <ChevronRight :size="13" :stroke-width="2" />
                  </template>
                </NButton>
              </section>
            </NTabPane>

            <NTabPane name="server" tab="服务器状态">
              <section class="panel cl-enter cl-lift">
                <NSpin v-if="loadingServer" />
                <NDescriptions v-else-if="server !== null" :column="2" size="small" bordered>
                  <NDescriptionsItem label="主机名">
                    <span class="mono">{{ server.hostname }}</span>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="状态">
                    <NTag
                      :type="
                        server.status === 'online'
                          ? 'success'
                          : server.status === 'offline'
                            ? 'error'
                            : 'warning'
                      "
                      size="small"
                      :bordered="false"
                    >
                      {{ server.status }}
                    </NTag>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="操作系统">{{
                    server.os_info ?? '—'
                  }}</NDescriptionsItem>
                  <NDescriptionsItem label="Agent">
                    <span class="mono">{{ server.agent_version ?? '—' }}</span>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="最近在线">
                    <span class="mono">{{ server.last_heartbeat_at ?? '—' }}</span>
                  </NDescriptionsItem>
                  <NDescriptionsItem label="最长预约时长">
                    <span class="mono">{{ server.max_reservation_hours ?? '∞' }} h</span>
                  </NDescriptionsItem>
                </NDescriptions>
                <div class="panel-actions">
                  <NButton
                    size="small"
                    @click="
                      $router.push({ name: 'server-detail', params: { id: entry.pa.server_id } })
                    "
                  >
                    打开服务器详情
                    <template #icon>
                      <ChevronRight :size="13" :stroke-width="2" />
                    </template>
                  </NButton>
                </div>
              </section>
            </NTabPane>

            <NTabPane name="settings" tab="设置">
              <section class="panel panel-danger cl-enter cl-lift">
                <p class="panel-blurb">
                  你可以解除此关联,以撤销平台对
                  <code>{{ entry.pa.linux_username }}</code>
                  的托管访问。通过 CoreLab 下发的 SSH 公钥会被移除;你手动写入
                  <code>authorized_keys</code> 的公钥则保持不变。
                </p>
                <NButton type="error" :disabled="!auth.isAuthenticated" @click="confirmRevoke">
                  解除关联
                </NButton>
              </section>
            </NTabPane>
          </NTabs>
        </div>
      </template>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  padding: var(--space-6) var(--space-8);
  max-width: 1080px;
  margin: 0 auto; /* 居中 —— 漏掉会贴左 */
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

/* ── 页头(无框身份区:纯排版,不靠盒子)──────────────────────── */
.hero {
  padding: var(--space-2) 0 var(--space-1);
  min-width: 0;
}
.title-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.title-user {
  font-family: var(--font-mono);
  font-size: var(--text-2xl);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  color: var(--c-text-primary);
  background: transparent;
  padding: 0;
}
.title-user::after {
  /* 终端光标 —— "我的工位" 在线感 */
  content: '';
  display: inline-block;
  width: 8px;
  height: 1em;
  margin-left: var(--space-1);
  vertical-align: -0.12em;
  background: var(--c-accent);
  animation: cursor-blink 1.15s steps(2, start) infinite;
}
@keyframes cursor-blink {
  0%,
  49% {
    opacity: 0.85;
  }
  50%,
  100% {
    opacity: 0;
  }
}
.title-at {
  font-family: var(--font-mono);
  font-size: var(--text-xl);
  color: var(--c-text-tertiary);
}
.title-server {
  font-family: var(--font-sans);
  font-size: var(--text-xl);
  font-weight: 500;
  color: var(--c-text-secondary);
  letter-spacing: var(--tracking-tight);
}
.hero-sub {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  margin-top: var(--space-1);
}
.page-subtitle {
  font-size: var(--c-text-2xs);
  color: var(--c-text-tertiary);
  margin: var(--space-1) 0 0;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
.sep {
  color: var(--c-border-default);
}

/* 汇总 chips —— 细边框小药丸,无填充底色 */
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
}
.chip-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--c-border-default);
  --cl-pulse-color: transparent;
}
.chip-ok {
  color: var(--c-success);
}
.chip-ok .chip-dot {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}
.chip-bad {
  color: var(--c-danger);
}
.chip-bad .chip-dot {
  background: var(--c-danger);
  --cl-pulse-color: color-mix(in srgb, var(--c-danger) 45%, transparent);
}
.chip-warn {
  color: var(--c-warning);
}
.chip-warn .chip-dot {
  background: var(--c-warning);
  --cl-pulse-color: color-mix(in srgb, var(--c-warning) 45%, transparent);
}

/* ── Tabs / panels ─────────────────────────────────────────────────── */
.tabs-wrap {
  background: var(--c-bg-elevated);
}
.panel {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  padding: var(--space-5);
  margin-top: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.panel:hover {
  border-color: var(--c-border-default);
}
.panel-danger {
  border-color: color-mix(in srgb, var(--c-danger) 22%, var(--c-border-subtle));
}
.panel-danger:hover {
  border-color: color-mix(in srgb, var(--c-danger) 40%, var(--c-border-subtle));
}
.panel-blurb {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  line-height: var(--leading-snug);
}
.panel-blurb code {
  font-family: var(--font-mono);
  font-size: 0.95em;
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  padding: 1px 4px;
  border-radius: var(--radius-sm);
}
.panel-actions {
  display: flex;
  justify-content: flex-start;
}
.alert-banner {
  margin-top: var(--space-2);
}

/* 描述表行 hover 高亮(只动背景,克制) */
.panel :deep(.n-descriptions-table-wrapper th),
.panel :deep(.n-descriptions-table-wrapper td) {
  transition: background-color 0.15s ease;
}
.panel :deep(.n-descriptions-table-wrapper tr:hover th),
.panel :deep(.n-descriptions-table-wrapper tr:hover td) {
  background-color: color-mix(in srgb, var(--c-accent) 4%, var(--c-bg-elevated));
}

/* ── SSH 连接示例(mono 卡片 + hover 光泽)──────────────────────── */
.ssh-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg-code);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: border-color 0.16s ease;
}
.ssh-card:hover {
  border-color: color-mix(in srgb, var(--c-accent) 45%, var(--c-border-subtle));
}
.ssh-card::after {
  /* hover 时一道光泽扫过 —— 复用全局 cl-sheen keyframes */
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    105deg,
    transparent 40%,
    color-mix(in srgb, var(--c-accent) 10%, transparent) 50%,
    transparent 60%
  );
  background-size: 220% 100%;
  background-position: -150% 0;
  background-repeat: no-repeat;
  opacity: 0;
  pointer-events: none;
}
.ssh-card:hover::after {
  opacity: 1;
  animation: cl-sheen 0.9s ease both;
}
.ssh-prompt {
  color: var(--c-accent);
  font-weight: 600;
  flex-shrink: 0;
}
.ssh-cmd {
  font-size: var(--text-sm);
  color: var(--c-text-primary);
  background: transparent;
  padding: 0;
  word-break: break-all;
}
.ssh-note {
  margin-left: auto;
  flex-shrink: 0;
  font-size: var(--c-text-2xs);
}

/* utilities */
.mono {
  font-family: var(--font-mono);
}
.tabular {
  font-variant-numeric: tabular-nums;
}
.dim {
  color: var(--c-text-tertiary);
}

/* 减少动效:终端光标放缓变柔(不全停 —— 身份感保留),
   hover 光泽这类装饰直接退化;cl- 原语本身已在 main.css 全局处理。 */
@media (prefers-reduced-motion: reduce) {
  .title-user::after {
    animation: cursor-blink 3.4s ease-in-out infinite;
  }
  .ssh-card:hover::after {
    animation: none;
    opacity: 0;
  }
}

/* 工作区缺失的原地状态(替代旧的"踢回仪表盘") */
.ws-missing {
  max-width: 480px;
  margin: var(--space-16) auto;
  padding: var(--space-8);
  text-align: center;
  border: 1px dashed var(--c-border-default);
  border-radius: var(--radius-lg);
  background: var(--c-bg-elevated);
}
.ws-missing h2 {
  margin: 0 0 var(--space-3);
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--c-text-primary);
}
.ws-missing p {
  margin: 0 0 var(--space-5);
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
.ws-missing-actions {
  display: flex;
  justify-content: center;
  gap: var(--space-3);
}
</style>
