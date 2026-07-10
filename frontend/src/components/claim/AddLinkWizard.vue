<script setup lang="ts">
/**
 * AddLinkWizard — Phase K wizard for the user-side "claim a Linux account" flow.
 *
 * Step 1 — pick (server, linux_username) from already-registered PAs.
 *          The user cannot type a free-form username; only PAs the agent
 *          (or admin) has already registered are selectable. This keeps
 *          claim attempts auditable and prevents users from forging
 *          PA rows by guessing names.
 *
 * Step 2 — pick path. We attempt a reverse_lookup against the chosen PA.
 *          When the user's pubkey is already in authorized_keys we route
 *          straight to情形 1 (SSH challenge); otherwise we present the
 *          three paths (情形 1 still possible, 情形 2 PAM password, 情形 4
 *          admin push-key request).
 *
 * Step 3 — execute the selected verifier.
 */
import { computed, onMounted, ref } from 'vue';
import { NAlert, NButton, NInput, NSelect, NSpin, NSteps, NStep, NTag, useMessage } from 'naive-ui';

import * as accountLinksApi from '@/api/accountLinks';
import * as alrApi from '@/api/accountLinkRequests';
import * as paApi from '@/api/physicalAccounts';
import * as serversApi from '@/api/servers';
import * as sshKeysApi from '@/api/sshKeys';
import type { PhysicalAccountRead, ReverseLookupResponse } from '@/api/physicalAccounts';
import type { ServerRead } from '@/api/servers';
import type { SshKeyRead } from '@/api/sshKeys';

const emit = defineEmits<{ done: []; cancel: [] }>();
const message = useMessage();

type Path = 'ssh' | 'pam' | 'request';

const step = ref(0);
const loadingServers = ref(true);
const servers = ref<ServerRead[]>([]);
const selectedServerId = ref<number | null>(null);
const loadingPas = ref(false);
const pas = ref<PhysicalAccountRead[]>([]);
const selectedPa = ref<PhysicalAccountRead | null>(null);

const reverseResult = ref<ReverseLookupResponse | null>(null);
const myKeys = ref<SshKeyRead[]>([]);
const detecting = ref(false);

const chosenPath = ref<Path | null>(null);
const selectedKeyId = ref<number | null>(null);
const submitting = ref(false);
const challenge = ref<{ challenge_id: string; nonce: string; sign_command: string } | null>(null);
const signatureArmored = ref('');
const pamPassword = ref('');
const requestNote = ref('');

onMounted(async () => {
  try {
    const [s, k] = await Promise.all([serversApi.listServers(), sshKeysApi.listMyKeys()]);
    servers.value = s;
    myKeys.value = k.filter((x) => x.is_active);
  } catch (e) {
    message.error(`加载失败: ${String(e)}`);
  } finally {
    loadingServers.value = false;
  }
});

const serverOptions = computed(() =>
  servers.value.map((s) => ({ label: s.display_name ?? s.hostname, value: s.id })),
);

async function onServerSelected(id: number): Promise<void> {
  selectedServerId.value = id;
  pas.value = [];
  selectedPa.value = null;
  loadingPas.value = true;
  try {
    pas.value = (await paApi.listPas(id)).filter((p) => p.is_active);
  } catch (e) {
    message.error(`加载 PA 列表失败: ${String(e)}`);
  } finally {
    loadingPas.value = false;
  }
}

const paOptions = computed(() => pas.value.map((p) => ({ label: p.linux_username, value: p.id })));

function selectPa(id: number): void {
  selectedPa.value = pas.value.find((p) => p.id === id) ?? null;
}

async function goToStep2(): Promise<void> {
  if (selectedServerId.value === null || selectedPa.value === null) return;
  step.value = 1;
  detecting.value = true;
  try {
    reverseResult.value = await paApi.reverseLookupViaPa(
      selectedServerId.value,
      selectedPa.value.id,
    );
  } catch {
    reverseResult.value = null;
  } finally {
    detecting.value = false;
  }
}

const sshKeyOptions = computed(() =>
  myKeys.value.map((k) => ({
    label: `${k.key_type} · ${k.fingerprint_sha256.slice(0, 24)}…${k.comment ? ` (${k.comment})` : ''}`,
    value: k.id,
  })),
);

async function chooseSshPath(): Promise<void> {
  chosenPath.value = 'ssh';
  step.value = 2;
  // A brand-new user often has no registered key yet — open the inline
  // add-key form straight away so they never have to leave the wizard.
  showAddKey.value = myKeys.value.length === 0;
}

// Inline "add a key" inside the SSH-challenge step (removes the round-trip
// to 个人资料 that used to block users with no registered key).
const showAddKey = ref(false);
const newKeyText = ref('');
const newKeyLabel = ref('');
const addingKey = ref(false);

async function submitNewKey(): Promise<void> {
  if (!newKeyText.value.trim()) return;
  addingKey.value = true;
  try {
    const created = await sshKeysApi.addMyKey({
      public_key: newKeyText.value.trim(),
      label: newKeyLabel.value.trim() || undefined,
    });
    myKeys.value = [...myKeys.value, created];
    selectedKeyId.value = created.id;
    newKeyText.value = '';
    newKeyLabel.value = '';
    showAddKey.value = false;
    message.success('公钥已添加');
  } catch (e) {
    message.error(`添加公钥失败: ${String(e)}`);
  } finally {
    addingKey.value = false;
  }
}

async function startChallenge(): Promise<void> {
  if (selectedServerId.value === null || selectedPa.value === null || selectedKeyId.value === null)
    return;
  submitting.value = true;
  try {
    const issued = await accountLinksApi.createChallenge({
      server_id: selectedServerId.value,
      linux_username: selectedPa.value.linux_username,
      ssh_public_key_id: selectedKeyId.value,
    });
    challenge.value = {
      challenge_id: issued.challenge_id,
      nonce: issued.nonce,
      sign_command: issued.sign_command,
    };
  } catch (e) {
    message.error(`挑战创建失败: ${String(e)}`);
  } finally {
    submitting.value = false;
  }
}

async function submitVerify(): Promise<void> {
  if (challenge.value === null) return;
  submitting.value = true;
  try {
    await accountLinksApi.verifyChallenge({
      challenge_id: challenge.value.challenge_id,
      signature_armored: signatureArmored.value,
    });
    message.success('账号已绑定 (SSH 挑战验证)');
    emit('done');
  } catch (e) {
    message.error(`验证失败: ${String(e)}`);
  } finally {
    submitting.value = false;
  }
}

async function submitPam(): Promise<void> {
  if (selectedServerId.value === null || selectedPa.value === null) return;
  submitting.value = true;
  try {
    await accountLinksApi.tryPassword({
      server_id: selectedServerId.value,
      linux_username: selectedPa.value.linux_username,
      password: pamPassword.value,
    });
    message.success('账号已绑定 (PAM 密码)');
    emit('done');
  } catch (e) {
    message.error(`密码验证失败: ${String(e)}`);
  } finally {
    submitting.value = false;
  }
}

async function submitRequest(): Promise<void> {
  if (selectedPa.value === null) return;
  submitting.value = true;
  try {
    await alrApi.createRequest({
      physical_account_id: selectedPa.value.id,
      request_note: requestNote.value || null,
    });
    message.success('申请已提交,等管理员审批');
    emit('done');
  } catch (e) {
    message.error(`提交申请失败: ${String(e)}`);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="wizard">
    <h2>添加 Linux 账号关联</h2>

    <NSteps :current="step + 1" size="small">
      <NStep title="选服务器 / 账号" />
      <NStep title="选择验证方式" />
      <NStep title="完成验证" />
    </NSteps>

    <!-- ── Step 1 ─────────────────────────────────── -->
    <section v-if="step === 0" class="body">
      <NSpin v-if="loadingServers" />
      <template v-else>
        <p class="hint">
          只能从已登记的 Linux 账号列表里选 — agent / 管理员还没注册的账号不会出现。
        </p>
        <label>服务器</label>
        <NSelect
          v-model:value="selectedServerId"
          :options="serverOptions"
          placeholder="选择服务器"
          @update:value="onServerSelected"
        />
        <label v-if="selectedServerId !== null">该服务器上的 Linux 账号</label>
        <NSpin v-if="loadingPas" size="small" />
        <NSelect
          v-else-if="selectedServerId !== null"
          :value="selectedPa?.id ?? null"
          :options="paOptions"
          placeholder="选择 Linux 账号"
          @update:value="selectPa"
        />
        <div class="actions">
          <NButton @click="emit('cancel')">取消</NButton>
          <NButton type="primary" :disabled="selectedPa === null" @click="goToStep2">
            下一步
          </NButton>
        </div>
      </template>
    </section>

    <!-- ── Step 2 ─────────────────────────────────── -->
    <section v-else-if="step === 1" class="body">
      <p>
        目标:
        <NTag size="small">{{ selectedPa?.linux_username }} @ 服务器 #{{ selectedServerId }}</NTag>
      </p>
      <NSpin v-if="detecting" size="small" />
      <NAlert
        v-else-if="reverseResult !== null && reverseResult.linked_users.length > 0"
        type="info"
        :show-icon="true"
      >
        这个账号已经被绑定给其他平台用户 ({{ reverseResult.linked_users.length }}
        人)。如果你也确实是这台账号的用户,可以继续走 SSH challenge 或密码验证 —
        否则建议改申请管理员推 key,避免误绑。
      </NAlert>
      <p class="hint">请选择如何证明你拥有这个账号的访问权 —</p>
      <div class="path-grid">
        <button class="path-card" :class="{ on: chosenPath === 'ssh' }" @click="chooseSshPath">
          <h3>我已能 ssh 登</h3>
          <p>用你已经在 authorized_keys 里的公钥签一个 nonce,平台核对签名后立即绑定。</p>
          <NTag size="tiny" type="success">即时</NTag>
        </button>
        <button
          class="path-card"
          :class="{ on: chosenPath === 'pam' }"
          @click="((chosenPath = 'pam'), (step = 2))"
        >
          <h3>我知道账号密码</h3>
          <p>输入这个 Linux 账号的密码,平台通过 PAM 校验后绑定。</p>
          <NTag size="tiny" type="info">即时</NTag>
        </button>
        <button
          class="path-card"
          :class="{ on: chosenPath === 'request' }"
          @click="((chosenPath = 'request'), (step = 2))"
        >
          <h3>我无权访问,申请推 key</h3>
          <p>提交申请,管理员同意后会把你的公钥推进 authorized_keys。</p>
          <NTag size="tiny" type="warning">需审批</NTag>
        </button>
      </div>
      <div class="actions">
        <NButton @click="step = 0">上一步</NButton>
      </div>
    </section>

    <!-- ── Step 3 ─────────────────────────────────── -->
    <section v-else class="body">
      <!-- 情形 1: SSH challenge -->
      <template v-if="chosenPath === 'ssh'">
        <h3>SSH 挑战验证</h3>

        <NAlert v-if="myKeys.length === 0 && !showAddKey" type="warning" :show-icon="true">
          你还没有注册任何 SSH 公钥。
          <NButton text type="primary" size="small" @click="showAddKey = true">
            现在添加一个 →
          </NButton>
        </NAlert>

        <template v-if="myKeys.length > 0">
          <p>选你要用来证明身份的公钥:</p>
          <NSelect
            v-model:value="selectedKeyId"
            :options="sshKeyOptions"
            placeholder="选择已注册的 SSH 公钥"
          />
          <NButton v-if="!showAddKey" text size="small" type="primary" @click="showAddKey = true">
            + 添加新公钥
          </NButton>
        </template>

        <div v-if="showAddKey" class="addkey">
          <label>粘贴你的 SSH 公钥</label>
          <NInput
            v-model:value="newKeyText"
            type="textarea"
            :autosize="{ minRows: 3 }"
            placeholder="ssh-ed25519 AAAA... you@host"
          />
          <NInput v-model:value="newKeyLabel" placeholder="备注(可选),例如:我的笔记本电脑" />
          <div class="actions">
            <NButton v-if="myKeys.length > 0" @click="showAddKey = false">收起</NButton>
            <NButton
              type="primary"
              :loading="addingKey"
              :disabled="!newKeyText.trim()"
              @click="submitNewKey"
            >
              添加公钥
            </NButton>
          </div>
        </div>

        <NButton
          v-if="challenge === null"
          type="primary"
          :loading="submitting"
          :disabled="selectedKeyId === null"
          @click="startChallenge"
        >
          生成 nonce
        </NButton>
        <template v-if="challenge !== null">
          <p>在你的本地终端执行下面这条命令,把输出粘贴到下方:</p>
          <pre class="cmd">{{ challenge.sign_command }}</pre>
          <NInput
            v-model:value="signatureArmored"
            type="textarea"
            :autosize="{ minRows: 6 }"
            placeholder="-----BEGIN SSH SIGNATURE-----..."
          />
          <NButton
            type="primary"
            :loading="submitting"
            :disabled="!signatureArmored"
            @click="submitVerify"
          >
            提交签名
          </NButton>
        </template>
      </template>

      <!-- 情形 2: PAM password -->
      <template v-else-if="chosenPath === 'pam'">
        <h3>PAM 密码</h3>
        <NAlert type="warning" :show-icon="true">
          密码只用来一次性向 agent 端 PAM 验证,平台不存储 — 但你仍应避免在不可信终端输入。
        </NAlert>
        <NInput
          v-model:value="pamPassword"
          type="password"
          show-password-on="click"
          placeholder="Linux 账号密码"
        />
        <NButton type="primary" :loading="submitting" :disabled="!pamPassword" @click="submitPam">
          验证并绑定
        </NButton>
      </template>

      <!-- 情形 4: request admin push -->
      <template v-else-if="chosenPath === 'request'">
        <h3>申请管理员推 key</h3>
        <p>简单说明一下你为什么需要这台账号的访问权 — 管理员审批时会看到。</p>
        <NInput
          v-model:value="requestNote"
          type="textarea"
          :autosize="{ minRows: 4 }"
          placeholder="例如:跟导师 X 一起跑 Y 项目 / 这台 server 上的 ABC 项目..."
        />
        <NButton type="primary" :loading="submitting" @click="submitRequest"> 提交申请 </NButton>
      </template>

      <div class="actions">
        <NButton @click="((step = 1), (chosenPath = null), (challenge = null))">上一步</NButton>
        <NButton text @click="emit('cancel')">取消</NButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.wizard {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
.wizard h2 {
  margin: 0;
}
.body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.hint {
  margin: 0;
  color: var(--c-text-secondary);
  font-size: var(--text-sm);
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
.path-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
}
.path-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-default);
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
  transition:
    border-color 0.1s ease,
    background 0.1s ease;
}
.path-card:hover {
  border-color: var(--c-accent-primary);
}
.path-card.on {
  border-color: var(--c-accent-primary);
  background: var(--c-bg-sunken);
}
.path-card h3 {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: 600;
}
.path-card p {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}
.cmd {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  padding: var(--space-3);
  background: var(--c-bg-sunken);
  border-radius: var(--radius-sm);
  overflow-x: auto;
}
.addkey {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  background: var(--c-bg-sunken);
}
.addkey label {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
</style>
