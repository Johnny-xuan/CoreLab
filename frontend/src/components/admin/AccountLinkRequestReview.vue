<script setup lang="ts">
/**
 * AccountLinkRequestReview — Phase K K-5 admin review card.
 *
 * The narrow approve/deny buttons in the inbox are still there, but for
 * the high-trust path admin clicks "Review" and gets the full picture
 * before deciding:
 *   - Is this user new to this PA, or coming back?
 *   - How have their past requests gone (denial rate)?
 *   - Which other servers will inherit access if we push this key?
 *   - What exactly happens on approve?
 *
 * The phase_K_plan §7 mockup is what we're realizing here.
 */
import { computed, onMounted, ref } from 'vue';
import { NAlert, NButton, NCard, NInput, NSpin, NTag, useMessage } from 'naive-ui';

import * as alrApi from '@/api/accountLinkRequests';
import type { AccountLinkRequestRead, RequestContext } from '@/api/accountLinkRequests';

const props = defineProps<{ request: AccountLinkRequestRead }>();
const emit = defineEmits<{ done: []; cancel: [] }>();
const message = useMessage();

const loading = ref(true);
const ctx = ref<RequestContext | null>(null);
const denyNote = ref('');
const approveNote = ref('');
const submitting = ref(false);

onMounted(async () => {
  try {
    ctx.value = await alrApi.fetchContext(props.request.id);
  } catch (e) {
    message.error(`加载审核信号失败: ${String(e)}`);
  } finally {
    loading.value = false;
  }
});

const renewalTag = computed(() => {
  if (ctx.value === null) return null;
  return ctx.value.is_first_time_for_this_pa
    ? { label: '🟡 首次申请', type: 'warning' as const }
    : { label: '🟢 续期', type: 'success' as const };
});

async function approve(): Promise<void> {
  submitting.value = true;
  try {
    await alrApi.approve(props.request.id, { decision_note: approveNote.value || null });
    message.success('已批准,公钥推送中');
    emit('done');
  } catch (e) {
    message.error(`approve 失败: ${String(e)}`);
  } finally {
    submitting.value = false;
  }
}

async function deny(): Promise<void> {
  if (!denyNote.value.trim()) {
    message.warning('请写一句拒绝理由(留给用户看)');
    return;
  }
  submitting.value = true;
  try {
    await alrApi.deny(props.request.id, { decision_note: denyNote.value });
    message.success('已拒绝');
    emit('done');
  } catch (e) {
    message.error(`deny 失败: ${String(e)}`);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="review">
    <NSpin v-if="loading" />
    <template v-else-if="ctx !== null">
      <header class="hdr">
        <div>
          <h2>申请 #{{ ctx.request_id }}</h2>
          <p class="who">
            <strong>{{ ctx.requester_display_name }}</strong>
            <span class="muted">@{{ ctx.requester_username }}</span>
            <span class="muted"> → </span>
            <code>{{ ctx.linux_username }}</code>
            <span class="muted"> @ {{ ctx.server_display_name ?? ctx.server_hostname }}</span>
          </p>
        </div>
        <NTag v-if="renewalTag" :type="renewalTag.type" size="medium">{{ renewalTag.label }}</NTag>
      </header>

      <NCard size="small" title="申请理由">
        <p v-if="props.request.request_note" class="reason">{{ props.request.request_note }}</p>
        <p v-else class="muted">(申请人没填理由)</p>
      </NCard>

      <NCard size="small" title="用户历史">
        <div class="stats">
          <div class="stat">
            <span class="stat-num">{{ ctx.requester_stats.total }}</span>
            <span class="stat-label">累计申请</span>
          </div>
          <div class="stat">
            <span class="stat-num success">{{ ctx.requester_stats.approved }}</span>
            <span class="stat-label">通过</span>
          </div>
          <div class="stat">
            <span class="stat-num error">{{ ctx.requester_stats.denied }}</span>
            <span class="stat-label">拒绝</span>
          </div>
          <div class="stat">
            <span class="stat-num">{{ ctx.requester_stats.withdrawn }}</span>
            <span class="stat-label">撤回</span>
          </div>
        </div>
      </NCard>

      <NCard size="small" title="将要推送的 SSH 公钥">
        <p v-if="ctx.requester_active_keys.length === 0" class="warn">
          ⚠ 申请人没有有效的 SSH 公钥 — approve 会失败。让他先去 My profile 加一把。
        </p>
        <ul v-else class="keys">
          <li v-for="k in ctx.requester_active_keys" :key="k.id">
            <code>{{ k.key_type }}</code>
            <span class="muted">{{ k.fingerprint_sha256 }}</span>
            <span v-if="k.comment" class="comment">— {{ k.comment }}</span>
          </li>
        </ul>
      </NCard>

      <NCard size="small" title="横向感染面 — 这把公钥已经在以下账号上">
        <p v-if="ctx.lateral_surface.length === 0" class="muted">
          这些公钥目前还没被推到任何 server 上。
        </p>
        <ul v-else class="lateral">
          <li v-for="(s, i) in ctx.lateral_surface" :key="i">
            <code>{{ s.linux_username }}</code>
            <span class="muted">@ {{ s.server_hostname }}</span>
            <span class="muted"> · {{ new Date(s.pushed_at).toLocaleDateString() }}</span>
          </li>
        </ul>
      </NCard>

      <NAlert type="info" :show-icon="true" title="批准后会发生什么">
        <ul class="impact">
          <li>
            CoreLab → agent 推这把公钥到 <code>~{{ ctx.linux_username }}/.ssh/authorized_keys</code>
          </li>
          <li>申请人拿到 active account_link,可以在平台预约 / 跑脚本</li>
          <li>⚠ 申请人同时获得 <strong>绕开平台直接 ssh 登</strong> 这台账号的能力</li>
          <li>未来撤 link 时 ☑ 同时撤这把 key(默认勾选)</li>
        </ul>
      </NAlert>

      <NCard size="small" title="决定" class="decision">
        <div class="decision-grid">
          <div class="decision-side deny-side">
            <h4>拒绝</h4>
            <NInput
              v-model:value="denyNote"
              type="textarea"
              :autosize="{ minRows: 3 }"
              placeholder="必填:拒绝理由(用户能看到)"
            />
            <NButton type="error" :loading="submitting" @click="deny">拒绝</NButton>
          </div>
          <div class="decision-side approve-side">
            <h4>批准并推送</h4>
            <NInput
              v-model:value="approveNote"
              type="textarea"
              :autosize="{ minRows: 3 }"
              placeholder="可选:备注(记入审计日志)"
            />
            <NButton type="primary" :loading="submitting" @click="approve">批准并推送</NButton>
          </div>
        </div>
      </NCard>

      <div class="footer">
        <NButton text @click="emit('cancel')">关闭(不下决定)</NButton>
      </div>
    </template>
  </div>
</template>

<style scoped>
.review {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.hdr {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}
.hdr h2 {
  margin: 0 0 var(--space-1);
}
.who {
  margin: 0;
  color: var(--c-text-primary);
}
.who code {
  font-family: var(--font-mono);
  background: var(--c-bg-sunken);
  padding: 0 var(--space-1);
  border-radius: var(--radius-sm);
}
.muted {
  color: var(--c-text-secondary);
}
.warn {
  color: var(--c-text-warning, #c97d00);
  margin: 0;
}
.reason {
  margin: 0;
  font-size: var(--text-sm);
}
.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-3);
}
.stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2);
  background: var(--c-bg-sunken);
  border-radius: var(--radius-sm);
}
.stat-num {
  font-family: var(--font-mono);
  font-size: var(--text-xl);
  font-weight: 600;
}
.stat-num.success {
  color: var(--c-text-success, #2da44e);
}
.stat-num.error {
  color: var(--c-text-error, #cf222e);
}
.stat-label {
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}
.keys,
.lateral,
.impact {
  margin: 0;
  padding-left: var(--space-4);
  font-size: var(--text-sm);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.keys code,
.lateral code,
.impact code {
  font-family: var(--font-mono);
  background: var(--c-bg-sunken);
  padding: 0 var(--space-1);
  border-radius: var(--radius-sm);
}
.comment {
  color: var(--c-text-secondary);
  font-size: var(--text-xs);
}
.decision {
  border-color: var(--c-accent-primary);
}
.decision-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}
.decision-side {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.decision-side h4 {
  margin: 0;
}
.footer {
  display: flex;
  justify-content: flex-end;
}
</style>
