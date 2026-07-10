<script setup lang="ts">
/**
 * AdminDomain — Phase M v5 (post-review): URL & tunnel management page.
 *
 * Replaces the PublicAccessCard that previously lived on Lab Overview.
 * Lab Overview is now strictly observation; this page is the treatment
 * surface — add custom domains, remove stale URLs, see tunnel state.
 *
 * **Tunnel toggle is intentionally NOT here.** Enabling / disabling /
 * upgrading the Cloudflare Tunnel touches host docker compose state
 * and changes how the platform is reachable; that's a CLI / install.sh
 * responsibility. This page only displays the current tunnel mode and
 * surfaces the exact shell commands an admin should paste on the host.
 *
 * Lives at ``/admin/domain``. Lab-admin only.
 */

import { computed, onMounted, onUnmounted, ref } from 'vue';
import { NAlert, NButton, NCard, NIcon, NInput, NModal, NSpin, NTag, useMessage } from 'naive-ui';
import { Copy, Globe, PlusCircle, RefreshCw, ShieldCheck, Trash2 } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import {
  addPublicUrl,
  listPublicUrls,
  probePublicUrlsNow,
  removePublicUrl,
  verifyDomain,
  type PublicUrlEntry,
  type PublicUrlsResponse,
  type TunnelMode,
} from '@/api/publicUrls';
import { extractDetail } from '@/utils/extractDetail';

const message = useMessage();

const data = ref<PublicUrlsResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
let pollTimer: ReturnType<typeof setInterval> | null = null;

async function refresh(silent = false): Promise<void> {
  if (!silent) loading.value = true;
  error.value = null;
  try {
    data.value = await listPublicUrls();
  } catch (e) {
    error.value = extractDetail(e, '无法加载域名列表。');
  } finally {
    loading.value = false;
  }
}

async function probeNow(): Promise<void> {
  loading.value = true;
  try {
    data.value = await probePublicUrlsNow();
    message.success('已重新探测可达性。');
  } catch (e) {
    message.error(extractDetail(e, '探测失败。'));
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void refresh();
  pollTimer = setInterval(() => void refresh(true), 60_000);
});
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});

const urls = computed<PublicUrlEntry[]>(() => data.value?.urls ?? []);
const tunnelMode = computed<TunnelMode>(() => data.value?.tunnel_mode ?? 'none');
const tunnelTokenSet = computed<boolean>(() => data.value?.tunnel_token_set ?? false);

// Hero summary — drives the chips next to the radar mark.
const totalCount = computed(() => urls.value.length);
const reachableCount = computed(() => urls.value.filter((u) => u.reachable === true).length);
const unreachableCount = computed(() => urls.value.filter((u) => u.reachable === false).length);
const tunnelShort = computed(() => {
  if (tunnelMode.value === 'cloudflare_quick') return 'Quick Tunnel';
  if (tunnelMode.value === 'cloudflare_named') return 'Named Tunnel';
  return '仅局域网';
});

function kindLabel(kind: PublicUrlEntry['kind']): string {
  return (
    {
      lan: '局域网',
      public_ip: '公网 IP',
      custom_domain: '自定义域名',
      cloudflare_quick: 'Cloudflare Quick',
      cloudflare_named: 'Cloudflare Named',
    }[kind] ?? kind
  );
}

function kindColor(kind: PublicUrlEntry['kind']): 'default' | 'success' | 'info' | 'warning' {
  switch (kind) {
    case 'lan':
      return 'default';
    case 'public_ip':
      return 'info';
    case 'custom_domain':
      return 'success';
    case 'cloudflare_quick':
      return 'warning';
    case 'cloudflare_named':
      return 'success';
    default:
      return 'default';
  }
}

function reachableDotClass(entry: PublicUrlEntry): string {
  if (entry.reachable === true) return 'dot dot-ok';
  if (entry.reachable === false) return 'dot dot-bad';
  if (entry.last_reachable_at) return 'dot dot-stale';
  return 'dot dot-unknown';
}

function reachableLabel(entry: PublicUrlEntry): string {
  if (entry.reachable === true) return '可达';
  if (entry.reachable === false) return '不可达';
  if (entry.last_reachable_at) return `上次可达 ${relativeTime(entry.last_reachable_at)}`;
  return '尚未探测';
}

function relativeTime(iso: string | null): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  const now = Date.now();
  const seconds = Math.round((now - then) / 1000);
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时前`;
  return `${Math.round(seconds / 86400)} 天前`;
}

async function copyText(text: string, label = '已复制'): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    message.success(label);
  } catch {
    message.error('无法复制。');
  }
}

async function removeOne(entry: PublicUrlEntry): Promise<void> {
  if (!confirm(`确定从 lab 中移除 ${entry.url} 吗?`)) return;
  loading.value = true;
  try {
    data.value = await removePublicUrl(entry.url);
    message.success(`已移除 ${entry.url}`);
  } catch (e) {
    message.error(extractDetail(e, '移除失败。'));
  } finally {
    loading.value = false;
  }
}

// ── Add domain modal ─────────────────────────────────────────────────
const showAddDomain = ref(false);
const domainInput = ref('');
const verifyResult = ref<{ resolved: string[]; matches: boolean; expected: string[] } | null>(null);
const verifying = ref(false);
const addingDomain = ref(false);

function openAddDomain(): void {
  domainInput.value = '';
  verifyResult.value = null;
  showAddDomain.value = true;
}

async function runDomainVerify(): Promise<void> {
  if (!domainInput.value.trim()) return;
  verifying.value = true;
  verifyResult.value = null;
  try {
    const r = await verifyDomain(domainInput.value.trim());
    verifyResult.value = {
      resolved: r.resolved,
      matches: r.matches_expected,
      expected: r.expected_any,
    };
  } catch (e) {
    message.error(extractDetail(e, '域名查询失败。'));
  } finally {
    verifying.value = false;
  }
}

async function confirmAddDomain(): Promise<void> {
  const domain = domainInput.value.trim();
  if (!domain) return;
  addingDomain.value = true;
  try {
    const proto = 'https://';
    const url = domain.includes('://') ? domain : `${proto}${domain}`;
    data.value = await addPublicUrl({ url, kind: 'custom_domain' });
    message.success(`已添加 ${url}`);
    showAddDomain.value = false;
  } catch (e) {
    message.error(extractDetail(e, '添加失败。'));
  } finally {
    addingDomain.value = false;
  }
}

// ── Tunnel mode: READ-ONLY display + commands ────────────────────────
// install.sh / deploy is where mode actually changes — see CLI section
// rendered below. We only show the current state here.
const tunnelLabel = computed(() => {
  switch (tunnelMode.value) {
    case 'cloudflare_quick':
      return 'Cloudflare Quick Tunnel —— 随机 / 临时 URL';
    case 'cloudflare_named':
      return 'Cloudflare Named Tunnel —— 固定 URL';
    default:
      return '仅局域网 —— 无隧道';
  }
});

const tunnelTone = computed<'default' | 'warning' | 'success'>(() => {
  if (tunnelMode.value === 'cloudflare_quick') return 'warning';
  if (tunnelMode.value === 'cloudflare_named') return 'success';
  return 'default';
});

// Commands an admin would run on the host. Verbatim so a Copy button
// is trivially safe — no shell-interpolation surprises here.
const enableCommands = [
  'cd /path/to/corelab/deploy   # adjust to wherever you cloned',
  'docker compose --profile tunnel up -d cloudflared',
  '# then re-fresh this page in 30-60s — the new URL appears via the probe scheduler',
].join('\n');

const namedCommands = [
  'cd /path/to/corelab/deploy',
  '# (1) Edit docker-compose.yml: change cloudflared command to:',
  '#       command: tunnel --no-autoupdate run --token $CLOUDFLARED_TUNNEL_TOKEN',
  '# (2) export the token you copied from the Cloudflare dashboard:',
  'export CLOUDFLARED_TUNNEL_TOKEN=<paste-token-here>',
  '# (3) recreate the sidecar:',
  'docker compose --profile tunnel up -d cloudflared',
].join('\n');

const disableCommands = [
  'cd /path/to/corelab/deploy',
  'docker compose --profile tunnel stop cloudflared',
  'docker compose --profile tunnel rm -f cloudflared',
].join('\n');
</script>

<template>
  <AppLayout>
    <div class="console">
      <header class="hero" :class="{ scanning: loading }">
        <div class="hero-left">
          <div class="radar" aria-hidden="true">
            <span class="radar-ring" />
            <span class="radar-ring" />
            <span class="radar-ring" />
            <span class="radar-core"
              ><NIcon :size="22"><Globe :size="22" /></NIcon
            ></span>
          </div>
          <div class="hero-text">
            <h1 class="hero-title">域名</h1>
            <div class="hero-sub">可访问此 CoreLab 的各个 URL —— 实时探测每条线路的可达性</div>
            <div class="hero-chips">
              <span class="chip">
                <span class="chip-num">{{ totalCount }}</span> 个 URL
              </span>
              <span v-if="reachableCount" class="chip chip-ok">
                <span class="chip-dot" />{{ reachableCount }} 个可达
              </span>
              <span v-if="unreachableCount" class="chip chip-bad">
                <span class="chip-dot" />{{ unreachableCount }} 个不可达
              </span>
              <span class="chip">
                <NIcon :size="11"><ShieldCheck /></NIcon>{{ tunnelShort }}
              </span>
            </div>
          </div>
        </div>
        <NButton size="small" :loading="loading" class="probe-btn" @click="probeNow">
          <template #icon
            ><NIcon><RefreshCw :size="14" /></NIcon
          ></template>
          立即探测
        </NButton>
      </header>

      <NAlert v-if="error" type="error" :title="error" size="small" />
      <NSpin v-if="loading && !data" size="small" />

      <template v-else>
        <!-- ─── URLs ───────────────────────────────────────────── -->
        <NCard size="small" :bordered="false" class="block">
          <template #header>
            <span class="block-title">
              <NIcon :size="14"><Globe :size="14" /></NIcon>
              公开 URL
            </span>
          </template>

          <ol v-if="urls.length" class="url-list">
            <li v-for="entry in urls" :key="entry.url" class="url-row">
              <span :class="reachableDotClass(entry)" :title="reachableLabel(entry)" />
              <span class="url-main">
                <span class="url-text mono">{{ entry.url }}</span>
                <span class="url-meta dim">
                  <NTag :type="kindColor(entry.kind)" size="tiny" round>
                    {{ kindLabel(entry.kind) }}
                  </NTag>
                  <span class="meta-sep">·</span>
                  <span>{{ reachableLabel(entry) }}</span>
                  <template v-if="entry.primary">
                    <span class="meta-sep">·</span>
                    <span class="primary-flag">主</span>
                  </template>
                </span>
              </span>
              <span class="url-actions">
                <NButton text size="tiny" @click="copyText(entry.url, 'URL copied')">
                  <template #icon
                    ><NIcon><Copy :size="12" /></NIcon
                  ></template>
                </NButton>
                <NButton text size="tiny" type="error" @click="removeOne(entry)">
                  <template #icon
                    ><NIcon><Trash2 :size="12" /></NIcon
                  ></template>
                </NButton>
              </span>
            </li>
          </ol>
          <div v-else class="empty dim">
            暂无公开 URL。install.sh 会在首次部署时写入初始值;你也可以在下方添加自定义域名。
          </div>

          <template #footer>
            <NButton size="small" @click="openAddDomain">
              <template #icon
                ><NIcon><PlusCircle :size="14" /></NIcon
              ></template>
              添加自定义域名
            </NButton>
          </template>
        </NCard>

        <!-- ─── Tunnel (read-only + tutorial) ──────────────────── -->
        <NCard size="small" :bordered="false" class="block">
          <template #header>
            <span class="block-title">
              <NIcon :size="14"><ShieldCheck :size="14" /></NIcon>
              Cloudflare Tunnel
              <NTag :type="tunnelTone" size="tiny" round class="tunnel-tag">
                {{ tunnelLabel }}
              </NTag>
            </span>
          </template>

          <div class="tutorial-intro dim">
            隧道开关有意设计为 CLI 操作 —— 更改它会触及宿主机的 docker-compose 状态,
            并可能改变这个管理控制台自身的可访问方式。请在运行后端的机器上执行下方的命令片段。
          </div>

          <!-- Block A: enable (only when tunnel_mode == none) -->
          <div v-if="tunnelMode === 'none'" class="tut-block">
            <div class="tut-h mono">→ 启用 Cloudflare Quick Tunnel</div>
            <div class="tut-body dim">
              随机的 <span class="mono">trycloudflare.com</span> 主机名,sidecar 重启后会重置。
              无需任何 Cloudflare 账号 / DNS / 域名。适合校外访问 / 开发演示。
            </div>
            <pre class="cmd mono">{{ enableCommands }}</pre>
            <div class="cmd-bar">
              <NButton size="tiny" @click="copyText(enableCommands, '已复制命令')">
                <template #icon
                  ><NIcon><Copy :size="12" /></NIcon
                ></template>
                复制
              </NButton>
            </div>
          </div>

          <!-- Block B: upgrade-to-named (when on quick) -->
          <div v-if="tunnelMode === 'cloudflare_quick'" class="tut-block">
            <div class="tut-h mono">→ 升级为 Named Tunnel</div>
            <div class="tut-body dim">
              自选的固定 URL(在 Cloudflare 控制台中设置),没有 200 连接数上限。 需要一个 Cloudflare
              账号 + 一个 tunnel token。
            </div>
            <pre class="cmd mono">{{ namedCommands }}</pre>
            <div class="cmd-bar">
              <NButton size="tiny" @click="copyText(namedCommands, '已复制命令')">
                <template #icon
                  ><NIcon><Copy :size="12" /></NIcon
                ></template>
                复制
              </NButton>
              <a
                class="ext-link"
                href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/"
                target="_blank"
                rel="noopener noreferrer"
              >
                Cloudflare 文档 ↗
              </a>
            </div>
          </div>

          <!-- Block C: disable (whenever a tunnel is up) -->
          <div v-if="tunnelMode !== 'none'" class="tut-block">
            <div class="tut-h mono">→ 关闭隧道</div>
            <div class="tut-body dim">
              <strong>警告:</strong>如果你当前正通过隧道 URL 访问本页面,执行后将失去访问权限。
              在关闭前,请确认上方列表中已有局域网 URL 或其他可用线路。
            </div>
            <pre class="cmd mono">{{ disableCommands }}</pre>
            <div class="cmd-bar">
              <NButton size="tiny" @click="copyText(disableCommands, '已复制命令')">
                <template #icon
                  ><NIcon><Copy :size="12" /></NIcon
                ></template>
                复制
              </NButton>
            </div>
          </div>

          <div class="tutorial-state mono dim">
            db state: tunnel_mode=<span class="state-val">{{ tunnelMode }}</span> ·
            token_stored=<span class="state-val">{{ tunnelTokenSet ? 'yes' : 'no' }}</span>
          </div>
        </NCard>
      </template>
    </div>

    <!-- Add domain modal -->
    <NModal
      v-model:show="showAddDomain"
      preset="card"
      title="添加自定义域名"
      style="max-width: 560px"
    >
      <div class="ad-body">
        <p class="ad-help dim">
          将你域名的 A 记录指向本服务器的公网 IP,然后在此验证。 验证成功后,该域名会被添加到 lab
          的公开 URL 中。TLS 与反向代理配置由部署层处理; 使用内置 Caddy
          或校内网关时,请让入口把流量转发到 CoreLab backend。
        </p>
        <NInput
          v-model:value="domainInput"
          placeholder="lab.your-uni.edu.cn"
          @keydown.enter="runDomainVerify"
        />
        <div class="ad-actions">
          <NButton
            :loading="verifying"
            :disabled="!domainInput.trim()"
            size="small"
            @click="runDomainVerify"
          >
            验证 DNS
          </NButton>
        </div>
        <div v-if="verifyResult" class="ad-verify mono">
          <div>
            解析结果:
            <span v-if="verifyResult.resolved.length">{{ verifyResult.resolved.join(', ') }}</span>
            <span v-else>(无)</span>
          </div>
          <div v-if="verifyResult.expected.length" class="dim">
            期望(本机):{{ verifyResult.expected.join(', ') }}
          </div>
          <div v-if="verifyResult.matches" class="ok">✓ DNS 已指向本机。</div>
          <div v-else-if="verifyResult.resolved.length" class="warn">
            ⚠ DNS 可解析,但未指向本机的本地 IP。(NAT / 代理 的情况下这也可能是正常的 ——
            添加后请通过浏览该 URL 来验证。)
          </div>
        </div>
        <div class="ad-confirm">
          <NButton
            type="primary"
            :loading="addingDomain"
            :disabled="!verifyResult || !domainInput.trim()"
            @click="confirmAddDomain"
          >
            添加到 lab 公开 URL
          </NButton>
        </div>
      </div>
    </NModal>
  </AppLayout>
</template>

<style scoped>
.console {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5) var(--space-6);
  max-width: 980px;
  margin: 0 auto; /* was missing → the whole page hugged the left edge */
  width: 100%;
}

/* ── Hero (radar / signal theme) ──────────────────────────────────── */
.hero {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-5) var(--space-6);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  background:
    radial-gradient(
      130% 150% at 6% 0%,
      color-mix(in srgb, var(--c-accent) 8%, transparent),
      transparent 55%
    ),
    var(--c-bg-sunken);
  overflow: hidden;
  animation: card-enter 0.45s cubic-bezier(0.22, 1, 0.36, 1) both;
}
/* 暗色:撤大面积品牌蓝染底(漏光感);雷达身份由彩色环群 + 核心承担,
 * 网格纹理(::before)保留 —— 暗色下它读作示波器底纹。 */
[data-theme='dark'] .hero {
  background: var(--c-bg-sunken);
}
.hero::before {
  /* faint grid texture, echoing the login backdrop */
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(var(--c-border-subtle) 1px, transparent 1px),
    linear-gradient(90deg, var(--c-border-subtle) 1px, transparent 1px);
  background-size: 28px 28px;
  opacity: 0.4;
  -webkit-mask-image: radial-gradient(60% 120% at 8% 0%, #000, transparent 70%);
  mask-image: radial-gradient(60% 120% at 8% 0%, #000, transparent 70%);
  pointer-events: none;
}
.hero-left {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: var(--space-4);
  min-width: 0;
}

/* radar mark: concentric signal rings emitting from a globe */
.radar {
  position: relative;
  width: 56px;
  height: 56px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
}
.radar-core {
  position: relative;
  z-index: 2;
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--c-accent);
  background: color-mix(in srgb, var(--c-accent) 12%, var(--c-bg-elevated));
  border: 1px solid color-mix(in srgb, var(--c-accent) 30%, transparent);
}
.radar-ring {
  position: absolute;
  inset: 0;
  margin: auto;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 1px solid color-mix(in srgb, var(--c-accent) 45%, transparent);
  opacity: 0;
  animation: radar-pulse 3s ease-out infinite;
}
.radar-ring:nth-child(2) {
  animation-delay: 1s;
}
.radar-ring:nth-child(3) {
  animation-delay: 2s;
}
.hero.scanning .radar-ring {
  animation-duration: 1.1s;
}
@keyframes radar-pulse {
  0% {
    transform: scale(0.6);
    opacity: 0.55;
  }
  70% {
    opacity: 0.12;
  }
  100% {
    transform: scale(2.3);
    opacity: 0;
  }
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
}
.chip-ok {
  color: var(--c-success);
}
.chip-ok .chip-dot {
  background: var(--c-success);
  animation: dot-breathe 2.4s ease-in-out infinite;
}
.chip-bad {
  color: var(--c-error, #ef4444);
}
.chip-bad .chip-dot {
  background: var(--c-error, #ef4444);
}
.probe-btn {
  position: relative;
  z-index: 1;
  flex-shrink: 0;
}

.block {
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  animation: card-enter 0.45s cubic-bezier(0.22, 1, 0.36, 1) both;
  animation-delay: 0.07s;
}
@keyframes card-enter {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
.block-title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text-primary);
}
.tunnel-tag {
  margin-left: var(--space-1);
}

/* URL list */
.url-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.url-row {
  display: grid;
  grid-template-columns: 16px 1fr auto;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-2);
  border-radius: var(--radius-sm);
  transition:
    background 0.15s ease,
    transform 0.15s ease;
}
.url-row:hover {
  background: var(--c-bg-elevated);
  transform: translateX(3px);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot-ok {
  background: var(--c-success);
  animation: dot-breathe 2.4s ease-in-out infinite;
}
@keyframes dot-breathe {
  0%,
  100% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--c-success) 50%, transparent);
  }
  50% {
    box-shadow: 0 0 0 5px color-mix(in srgb, var(--c-success) 0%, transparent);
  }
}
.dot-bad {
  background: var(--c-error, #ef4444);
}
.dot-stale {
  background: var(--c-warning, #f59e0b);
}
.dot-unknown {
  background: var(--c-border-default);
}
.url-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.url-text {
  font-size: 13px;
  color: var(--c-text-primary);
  word-break: break-all;
}
.url-meta {
  font-size: 11px;
  display: flex;
  align-items: center;
  gap: var(--space-1);
}
.meta-sep {
  margin: 0 4px;
}
.primary-flag {
  color: var(--c-accent);
  font-weight: 500;
}
.url-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  opacity: 0.3;
  transition: opacity 0.15s ease;
}
.url-row:hover .url-actions {
  opacity: 1;
}
.empty {
  font-size: 12px;
  padding: var(--space-3);
  text-align: center;
}

/* Tunnel tutorial */
.tutorial-intro {
  font-size: 12px;
  line-height: 1.5;
  margin-bottom: var(--space-3);
}
.tut-block {
  margin: var(--space-3) 0;
  padding: var(--space-3);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  background: var(--c-bg-base, var(--c-bg-elevated));
}
.tut-h {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-text-primary);
  margin-bottom: var(--space-1);
}
.tut-body {
  font-size: 12px;
  line-height: 1.5;
  margin-bottom: var(--space-2);
}
.cmd {
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  max-height: 280px;
  overflow-y: auto;
}
.cmd-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
}
.ext-link {
  font-size: 11px;
  color: var(--c-text-tertiary);
  text-decoration: none;
}
.ext-link:hover {
  color: var(--c-accent);
}
.tutorial-state {
  font-size: 11px;
  margin-top: var(--space-3);
}
.state-val {
  color: var(--c-text-secondary);
}

/* Add-domain modal */
.dim {
  color: var(--c-text-tertiary);
}
.mono {
  font-family: var(--font-mono);
}
.ad-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.ad-help {
  font-size: 12px;
  line-height: 1.4;
  margin: 0;
}
.ad-actions {
  display: flex;
  justify-content: flex-start;
}
.ad-verify {
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ad-verify .ok {
  color: var(--c-success);
}
.ad-verify .warn {
  color: var(--c-warning, #f59e0b);
}
.ad-confirm {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

/* Respect reduced-motion, but keep the radar gently alive (slow + faint)
   rather than killing it outright — the page's whole identity is the
   "probe" motif. A user-triggered probe (.scanning) still pulses fast. */
@media (prefers-reduced-motion: reduce) {
  .radar-ring {
    animation-duration: 6s;
    opacity: 0.18;
  }
  .chip-ok .chip-dot,
  .dot-ok {
    animation: none;
  }
  .hero,
  .block {
    animation: none;
  }
  .url-row:hover {
    transform: none;
  }
}
</style>
