<script setup lang="ts">
/**
 * PoliciesTab — Server Detail / Policies (Phase 9 P9-10/11, docs/07 §6.6b).
 *
 * 8 policy_key rows (docs/02 §5.18) with per-key schema-aware threshold
 * editors (FU-37). 3-profile preset switch + cap×policy banner when
 * severity=auto_kill but the corresponding capability is off (P9-11,
 * backend co-invariant P8-7 will downgrade at runtime).
 *
 * The component is self-contained: it owns its load loop + dirty
 * tracking per row. The parent (ServerDetail) just passes the
 * server id + the capability list it already loads.
 */

import { computed, onMounted, reactive, ref, watch } from 'vue';
import {
  NAlert,
  NButton,
  NInputNumber,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  useMessage,
  type SelectOption,
} from 'naive-ui';
import { AlertTriangle } from 'lucide-vue-next';
import { extractDetail } from '@/utils/extractDetail';

import {
  listPolicy,
  POLICY_KEYS,
  switchProfile,
  THRESHOLD_SHAPES,
  updatePolicy,
  type AgentPolicyRead,
  type PolicyKey,
  type PolicyProfile,
  type PolicySeverity,
  type ThresholdShape,
} from '@/api/policies';
import type { CapabilityRead } from '@/api/servers';

const props = defineProps<{
  serverId: number;
  capabilities: CapabilityRead[];
  canEdit: boolean;
}>();

const message = useMessage();

const rows = ref<AgentPolicyRead[]>([]);
const loading = ref(false);

const profile = ref<PolicyProfile>('standard');
const profileBusy = ref(false);

interface RowDraft {
  enabled: boolean;
  severity: PolicySeverity;
  grace_period_seconds: number | null;
  notify_admin: boolean;
  threshold_value: Record<string, unknown> | null;
}

const drafts = reactive<Record<string, RowDraft>>({});
const rowBusy = reactive<Record<string, boolean>>({});

const severityOptions: SelectOption[] = [
  { value: 'log_only', label: 'log_only' },
  { value: 'notify', label: 'notify' },
  { value: 'warn', label: 'warn' },
  { value: 'auto_kill', label: 'auto_kill' },
];

const profileOptions: SelectOption[] = [
  { value: 'permissive', label: '宽松' },
  { value: 'standard', label: '标准' },
  { value: 'strict', label: '严格' },
];

function _killCapDisabled(): boolean {
  const cap = props.capabilities.find((c) => c.capability_key === 'gpu.kill_process');
  return cap ? !cap.is_enabled : false;
}

function _seedDraft(row: AgentPolicyRead): RowDraft {
  return {
    enabled: row.enabled,
    severity: row.severity,
    grace_period_seconds: row.grace_period_seconds,
    notify_admin: row.notify_admin,
    threshold_value: row.threshold_value ? { ...row.threshold_value } : null,
  };
}

function _isDirty(key: string, row: AgentPolicyRead): boolean {
  const d = drafts[key];
  if (!d) return false;
  if (d.enabled !== row.enabled) return true;
  if (d.severity !== row.severity) return true;
  if (d.grace_period_seconds !== row.grace_period_seconds) return true;
  if (d.notify_admin !== row.notify_admin) return true;
  return JSON.stringify(d.threshold_value) !== JSON.stringify(row.threshold_value);
}

async function reload(): Promise<void> {
  loading.value = true;
  try {
    const fetched = await listPolicy(props.serverId);
    rows.value = fetched;
    for (const r of fetched) {
      drafts[r.policy_key] = _seedDraft(r);
    }
  } catch (err) {
    message.error(extractDetail(err, '加载策略失败'));
  } finally {
    loading.value = false;
  }
}

function _findRow(key: string): AgentPolicyRead | undefined {
  return rows.value.find((r) => r.policy_key === key);
}

async function saveRow(key: string): Promise<void> {
  const original = _findRow(key);
  const draft = drafts[key];
  if (!original || !draft) return;
  rowBusy[key] = true;
  try {
    const resp = await updatePolicy(props.serverId, key, {
      enabled: draft.enabled !== original.enabled ? draft.enabled : null,
      severity: draft.severity !== original.severity ? draft.severity : null,
      grace_period_seconds:
        draft.grace_period_seconds !== original.grace_period_seconds
          ? draft.grace_period_seconds
          : null,
      notify_admin: draft.notify_admin !== original.notify_admin ? draft.notify_admin : null,
      threshold_value: draft.threshold_value,
    });
    const idx = rows.value.findIndex((r) => r.policy_key === key);
    if (idx >= 0) rows.value[idx] = resp.policy;
    drafts[key] = _seedDraft(resp.policy);
    if (resp.capability_warning) {
      message.warning(resp.capability_warning);
    } else {
      message.success(`策略 ${key} 已更新`);
    }
  } catch (err) {
    message.error(extractDetail(err, `更新 ${key} 失败`));
  } finally {
    rowBusy[key] = false;
  }
}

function resetRow(key: string): void {
  const row = _findRow(key);
  if (row) drafts[key] = _seedDraft(row);
}

async function applyProfile(): Promise<void> {
  profileBusy.value = true;
  try {
    const resp = await switchProfile(props.serverId, profile.value);
    message.success(`profile ${resp.profile} 已应用 (${resp.rows_changed} 行)`);
    await reload();
  } catch (err) {
    message.error(extractDetail(err, '切换 profile 失败'));
  } finally {
    profileBusy.value = false;
  }
}

function shapeFor(key: string): ThresholdShape {
  return THRESHOLD_SHAPES[key as PolicyKey] ?? { kind: 'none' };
}

function thresholdValue(key: string, field: string): number | null {
  const d = drafts[key];
  if (!d?.threshold_value) return null;
  const v = d.threshold_value[field];
  return typeof v === 'number' ? v : null;
}

function setThresholdField(key: string, field: string, value: number | null): void {
  const d = drafts[key];
  if (!d) return;
  const shape = shapeFor(key);
  if (shape.kind === 'none') return;
  const next: Record<string, unknown> = d.threshold_value ? { ...d.threshold_value } : {};
  if (value === null) {
    delete next[field];
  } else {
    next[field] = value;
  }
  if (shape.kind === 'pct') next.unit = 'pct';
  if (shape.kind === 'celsius') next.unit = 'celsius';
  d.threshold_value = next;
}

const killCapOff = computed(() => _killCapDisabled());

const rowsByKey = computed(() => {
  const map: Record<string, AgentPolicyRead> = {};
  for (const r of rows.value) map[r.policy_key] = r;
  return map;
});

watch(
  () => props.serverId,
  () => {
    void reload();
  },
);

onMounted(() => {
  void reload();
});

defineExpose({ reload });
</script>

<template>
  <div class="policies-tab">
    <header class="profile-bar">
      <div class="profile-left">
        <span class="profile-label">当前策略预设</span>
        <NSelect
          v-model:value="profile"
          :options="profileOptions"
          size="small"
          style="width: 160px"
          :disabled="!canEdit"
        />
        <NButton
          size="small"
          type="primary"
          :loading="profileBusy"
          :disabled="!canEdit"
          @click="applyProfile"
        >
          应用预设
        </NButton>
      </div>
    </header>

    <NAlert type="info" :show-icon="false" class="info-banner">
      Capability + Policy 协同:policy 配 auto_kill 但对应 capability 关闭时,agent 实际不行动(降级到
      warn,P8-7)。
    </NAlert>

    <NAlert v-if="killCapOff" type="warning" :show-icon="false" class="warn-banner">
      <span class="warn-banner-row">
        <AlertTriangle :size="14" :stroke-width="1.75" class="warn-banner-icon" />
        <span>
          <code>gpu.kill_process</code> capability 当前关闭 — 所有 severity=auto_kill
          的策略在运行时会被自动降级为 warn(P9-11)。
        </span>
      </span>
    </NAlert>

    <section v-if="loading" class="loading">加载中…</section>

    <section v-else class="rows">
      <article
        v-for="key in POLICY_KEYS"
        :key="key"
        class="policy-row"
        :class="{ dirty: rowsByKey[key] && _isDirty(key, rowsByKey[key]) }"
      >
        <header class="row-head">
          <code class="row-key">{{ key }}</code>
          <NTag v-if="!rowsByKey[key]?.enabled" size="small">已禁用</NTag>
          <NTag
            v-if="
              drafts[key]?.severity === 'auto_kill' &&
              killCapOff &&
              key !== 'gpu_temp_high' &&
              key !== 'memory_overuse'
            "
            size="small"
            type="warning"
          >
            capability 关闭,已降级为 warn
          </NTag>
        </header>

        <div v-if="drafts[key]" class="row-body">
          <label class="field">
            <span class="field-label">启用</span>
            <NSwitch v-model:value="drafts[key].enabled" :disabled="!canEdit" />
          </label>

          <label class="field">
            <span class="field-label">严重级别</span>
            <NSelect
              v-model:value="drafts[key].severity"
              :options="severityOptions"
              size="small"
              :disabled="!canEdit"
            />
          </label>

          <label class="field">
            <span class="field-label">宽限期(秒)</span>
            <NInputNumber
              v-model:value="drafts[key].grace_period_seconds"
              :min="0"
              size="small"
              :disabled="!canEdit"
              clearable
            />
          </label>

          <label class="field">
            <span class="field-label">通知管理员</span>
            <NSwitch v-model:value="drafts[key].notify_admin" :disabled="!canEdit" />
          </label>

          <!-- threshold: schema-aware -->
          <template v-if="shapeFor(key).kind === 'none'">
            <span class="field field-flat field-muted">无阈值</span>
          </template>

          <label v-else-if="shapeFor(key).kind === 'pct'" class="field">
            <span class="field-label">阈值 %(0-100)</span>
            <NInputNumber
              :value="thresholdValue(key, 'value')"
              :min="0"
              :max="100"
              size="small"
              :disabled="!canEdit"
              @update:value="(v: number | null) => setThresholdField(key, 'value', v)"
            />
          </label>

          <label v-else-if="shapeFor(key).kind === 'celsius'" class="field">
            <span class="field-label">阈值 °C(0-200)</span>
            <NInputNumber
              :value="thresholdValue(key, 'value')"
              :min="0"
              :max="200"
              size="small"
              :disabled="!canEdit"
              @update:value="(v: number | null) => setThresholdField(key, 'value', v)"
            />
          </label>

          <template v-else-if="shapeFor(key).kind === 'gpu_hang'">
            <label class="field">
              <span class="field-label">util_zero_seconds (≥10)</span>
              <NInputNumber
                :value="thresholdValue(key, 'util_zero_seconds')"
                :min="10"
                size="small"
                :disabled="!canEdit"
                @update:value="(v: number | null) => setThresholdField(key, 'util_zero_seconds', v)"
              />
            </label>
            <label class="field">
              <span class="field-label">mem_floor_mb (≥0)</span>
              <NInputNumber
                :value="thresholdValue(key, 'mem_floor_mb')"
                :min="0"
                size="small"
                :disabled="!canEdit"
                @update:value="(v: number | null) => setThresholdField(key, 'mem_floor_mb', v)"
              />
            </label>
          </template>
        </div>

        <footer class="row-actions">
          <NSpace>
            <NButton
              size="small"
              type="primary"
              :disabled="!canEdit || !rowsByKey[key] || !_isDirty(key, rowsByKey[key])"
              :loading="rowBusy[key]"
              @click="saveRow(key)"
            >
              保存
            </NButton>
            <NButton
              size="small"
              :disabled="!canEdit || !rowsByKey[key] || !_isDirty(key, rowsByKey[key])"
              @click="resetRow(key)"
            >
              重置
            </NButton>
          </NSpace>
        </footer>
      </article>
    </section>
  </div>
</template>

<style scoped>
.policies-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.profile-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
}
.profile-left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.profile-label {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
.info-banner,
.warn-banner {
  font-size: var(--text-sm);
}
.warn-banner-row {
  display: inline-flex;
  align-items: flex-start;
  gap: var(--space-2);
}
.warn-banner-icon {
  color: var(--c-warning);
  margin-top: 2px;
  flex-shrink: 0;
}
.loading {
  color: var(--c-text-tertiary);
  padding: var(--space-3);
  font-size: var(--text-sm);
}
.rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.policy-row {
  padding: var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.policy-row.dirty {
  border-color: var(--c-text-primary);
}
.row-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.row-key {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--text-sm);
  font-weight: 600;
}
.row-body {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--space-3);
}
.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.field-label {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.field-flat {
  justify-content: center;
  align-items: flex-start;
}
.field-muted {
  color: var(--c-text-tertiary);
  font-size: var(--text-sm);
  padding-top: var(--space-3);
}
.row-actions {
  display: flex;
  justify-content: flex-end;
}
</style>
