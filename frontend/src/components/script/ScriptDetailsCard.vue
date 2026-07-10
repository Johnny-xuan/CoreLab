<script setup lang="ts">
/**
 * ScriptDetailsCard — read-only summary of a script attached to a reservation.
 *
 * Surfaces every script_* metadata field the platform persists. Live
 * "elapsed" tick refreshes once a second while the script is running.
 *
 * The platform stores a bounded recent-output tail for quick inspection.
 * The complete host log path remains visible for SSH tail/cat when needed.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { Copy, FileText, RefreshCw, Terminal as TerminalIcon } from 'lucide-vue-next';
import { useMessage } from 'naive-ui';

import {
  getReservationScriptLog,
  type ReservationRead,
  type ReservationScriptLogRead,
} from '@/api/reservations';
import { extractDetail } from '@/utils/extractDetail';
import ScriptStatusBadge from './ScriptStatusBadge.vue';
import {
  buildSshCatCommand,
  buildSshTailCommand,
  deriveScriptUIStatus,
  elapsedSeconds,
  formatBytes,
  formatDuration,
  formatLocalDateTimeShort,
} from './scriptHelpers';

interface Props {
  reservation: ReservationRead;
  /** Optional SSH context — when both are supplied the card renders a
   * "复制 SSH 命令" button that pastes the right `ssh user@host` line. */
  linuxUsername?: string | null;
  hostname?: string | null;
  /** Truncate the script body at this many chars in the preview. The full
   * body is reachable via the expand toggle. */
  previewChars?: number;
}

const props = withDefaults(defineProps<Props>(), {
  linuxUsername: null,
  hostname: null,
  previewChars: 200,
});

const message = useMessage();

const uiStatus = computed(() => deriveScriptUIStatus(props.reservation));

// Live "已运行 17min" ticker. Only the running state needs it; we still
// attach the interval unconditionally so a status flip mid-mount doesn't
// leave a stale value, but skip work when not running.
const nowTick = ref<number>(Date.now());
let timer: ReturnType<typeof setInterval> | null = null;
onMounted(() => {
  timer = setInterval(() => {
    if (uiStatus.value === 'running') nowTick.value = Date.now();
  }, 1000);
});
onBeforeUnmount(() => {
  if (timer !== null) clearInterval(timer);
});

const elapsed = computed<string>(() => {
  const r = props.reservation;
  if (r.script_started_at === null) return '—';
  // touch nowTick so Vue tracks our live dep
  void nowTick.value;
  const endIso = r.script_finished_at ?? null;
  return formatDuration(elapsedSeconds(r.script_started_at, endIso));
});

const exitDisplay = computed<string>(() => {
  const e = props.reservation.script_exit_code;
  if (e === null) return '—';
  return String(e);
});

const triggerAt = computed<string>(() =>
  formatLocalDateTimeShort(
    props.reservation.script_scheduled_start_at ?? props.reservation.start_at,
  ),
);
const triggerHint = computed<string>(() =>
  props.reservation.script_scheduled_start_at === null ? '与预约同时启动' : '到点定时启动',
);

const maxRuntimeText = computed<string>(() => {
  const s = props.reservation.script_max_runtime_seconds;
  if (s === null) return '跑到预约结束';
  return formatDuration(s);
});

const expanded = ref<boolean>(false);
const scriptBody = computed<string>(() => props.reservation.script ?? '');
const scriptTruncated = computed<boolean>(() => scriptBody.value.length > props.previewChars);
const scriptDisplay = computed<string>(() =>
  !expanded.value && scriptTruncated.value
    ? `${scriptBody.value.slice(0, props.previewChars)}…`
    : scriptBody.value,
);

const sshAvailable = computed<boolean>(
  () => props.linuxUsername !== null && props.hostname !== null,
);

// Normalize the path so null/undefined collapse to a single "no path" sentinel.
const logPath = computed<string | null>(() => props.reservation.script_log_path ?? null);
const logOpen = ref<boolean>(false);
const logLoading = ref<boolean>(false);
const logError = ref<string | null>(null);
const platformLog = ref<ReservationScriptLogRead | null>(null);
const platformLogText = computed<string>(() => platformLog.value?.text ?? '');
const platformLogSize = computed<string>(() =>
  formatBytes(platformLog.value?.output_size_bytes ?? props.reservation.script_output_size_bytes),
);

const sshCommand = computed<string>(() => {
  if (!sshAvailable.value || logPath.value === null) return '';
  const tail = uiStatus.value === 'running';
  return tail
    ? buildSshTailCommand(props.linuxUsername as string, props.hostname as string, logPath.value)
    : buildSshCatCommand(props.linuxUsername as string, props.hostname as string, logPath.value);
});

async function copyToClipboard(text: string, label: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    message.success(`${label} 已复制`);
  } catch {
    message.error('复制失败 — 浏览器拒绝了 clipboard 权限');
  }
}

async function loadPlatformLog(): Promise<void> {
  logOpen.value = true;
  logLoading.value = true;
  logError.value = null;
  try {
    platformLog.value = await getReservationScriptLog(props.reservation.id);
  } catch (err) {
    logError.value = extractDetail(err, '日志加载失败');
  } finally {
    logLoading.value = false;
  }
}
</script>

<template>
  <article class="card">
    <header class="card-head">
      <ScriptStatusBadge :status="uiStatus" size="md" />
      <span class="elapsed mono">{{ elapsed }}</span>
    </header>

    <dl class="grid">
      <div class="cell">
        <dt>触发时间</dt>
        <dd>
          <span class="mono">{{ triggerAt }}</span>
          <span class="hint">{{ triggerHint }}</span>
        </dd>
      </div>
      <div class="cell">
        <dt>最长运行</dt>
        <dd class="mono">{{ maxRuntimeText }}</dd>
      </div>
      <div class="cell">
        <dt>退出码</dt>
        <dd
          class="mono"
          :class="{
            danger: reservation.script_exit_code !== null && reservation.script_exit_code !== 0,
          }"
        >
          {{ exitDisplay }}
        </dd>
      </div>
      <div class="cell">
        <dt>日志大小</dt>
        <dd class="mono">{{ platformLogSize }}</dd>
      </div>
    </dl>

    <section v-if="scriptBody.length > 0" class="body-section">
      <header class="body-head">
        <span class="body-label">脚本内容</span>
        <button v-if="scriptTruncated" type="button" class="link-btn" @click="expanded = !expanded">
          {{ expanded ? '收起' : '展开全部' }}
        </button>
      </header>
      <pre class="body-code">{{ scriptDisplay }}</pre>
    </section>

    <section v-if="logPath !== null" class="log-section">
      <header class="body-head">
        <span class="body-label">日志路径(agent 主机)</span>
        <button type="button" class="link-btn" :disabled="logLoading" @click="loadPlatformLog">
          <RefreshCw v-if="logOpen" :size="11" :stroke-width="1.75" />
          <FileText v-else :size="11" :stroke-width="1.75" />
          {{ logOpen ? '刷新日志' : '查看日志' }}
        </button>
      </header>
      <div class="log-path-row">
        <code class="log-path">{{ logPath }}</code>
        <button
          type="button"
          class="link-btn"
          @click="copyToClipboard(logPath as string, '日志路径')"
        >
          <Copy :size="11" :stroke-width="1.75" />
          复制路径
        </button>
      </div>
      <div v-if="sshAvailable && sshCommand !== ''" class="ssh-row">
        <code class="ssh-cmd">{{ sshCommand }}</code>
        <button type="button" class="link-btn" @click="copyToClipboard(sshCommand, 'SSH 命令')">
          <TerminalIcon :size="11" :stroke-width="1.75" />
          复制 SSH 命令
        </button>
      </div>
      <div v-if="logOpen" class="platform-log">
        <div v-if="logLoading" class="log-state">加载中…</div>
        <div v-else-if="logError !== null" class="log-state danger">{{ logError }}</div>
        <template v-else>
          <div v-if="platformLog?.truncated" class="log-state warn">已截断，只显示最近输出</div>
          <pre v-if="platformLogText.length > 0" class="body-code log-code">{{
            platformLogText
          }}</pre>
          <div v-else class="log-state">暂无平台内日志</div>
          <button
            v-if="platformLogText.length > 0"
            type="button"
            class="link-btn log-copy"
            @click="copyToClipboard(platformLogText, '日志内容')"
          >
            <Copy :size="11" :stroke-width="1.75" />
            复制日志
          </button>
        </template>
      </div>
    </section>
  </article>
</template>

<style scoped>
.card {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
.elapsed {
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}

.grid {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--space-2) var(--space-4);
}
.cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.cell dt {
  font-size: 10px;
  color: var(--c-text-tertiary);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.cell dd {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--c-text-primary);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.cell dd.danger {
  color: var(--c-danger, #dc2626);
  font-weight: 600;
}
.hint {
  font-size: 10px;
  color: var(--c-text-tertiary);
}

.mono {
  font-family: var(--font-mono, ui-monospace, monospace);
}

.body-section,
.log-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.body-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.body-label {
  font-size: 10px;
  color: var(--c-text-tertiary);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.body-code {
  margin: 0;
  background: var(--c-bg-code, var(--c-bg-sunken));
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--c-text-primary);
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow-y: auto;
}

.log-path-row,
.ssh-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.log-path,
.ssh-cmd {
  flex: 1 1 auto;
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  padding: 4px var(--space-2);
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 11px;
  color: var(--c-text-primary);
  overflow-x: auto;
  white-space: nowrap;
}

.link-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: transparent;
  border: 1px solid var(--c-border-default);
  border-radius: var(--radius-sm);
  color: var(--c-text-secondary);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
}
.link-btn:disabled {
  opacity: 0.55;
  cursor: wait;
}
.link-btn:hover {
  background: var(--c-bg-sunken);
  color: var(--c-text-primary);
}
.link-btn:disabled:hover {
  background: transparent;
  color: var(--c-text-secondary);
}

.platform-log {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.log-code {
  max-height: 320px;
}
.log-copy {
  align-self: flex-start;
}
.log-state {
  font-size: 11px;
  color: var(--c-text-tertiary);
}
.log-state.warn {
  color: var(--c-warning, #a16207);
}
.log-state.danger {
  color: var(--c-danger, #dc2626);
}
</style>
