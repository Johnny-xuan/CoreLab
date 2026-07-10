<script setup lang="ts">
/**
 * AppLayout — authenticated application shell.
 *
 * Sidebar + Topbar shell from docs/07-ui-design.md §3.1/§3.2.
 * Topbar shows the current user + a logout dropdown. Sidebar items
 * route to user workspaces, server views, admin pages, and lab-level
 * governance surfaces.
 */
import { NDropdown, NLayout, NLayoutHeader, NLayoutSider, NMenu, useMessage } from 'naive-ui';
import { computed, h, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import type { FunctionalComponent } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import type { DropdownOption, MenuOption } from 'naive-ui';
import {
  Activity,
  BarChart3,
  BellRing,
  CalendarCheck,
  CalendarRange,
  Cpu,
  Globe,
  Inbox,
  KeyRound,
  LayoutDashboard,
  Layers,
  Link as LinkIcon,
  ScrollText,
  Server,
  Terminal,
  UserPlus,
  Users as UsersIcon,
} from 'lucide-vue-next';

import NotificationBell from '@/components/NotificationBell.vue';
import Brand from '@/layouts/Brand.vue';
import ThemeToggle from '@/layouts/ThemeToggle.vue';
import { useSidebarState } from '@/composables/useSidebarState';
import { useAuthStore } from '@/stores/auth';
import { useWorkspaceStore } from '@/stores/workspace';
import { useWsStore } from '@/stores/ws';

const auth = useAuthStore();
const workspace = useWorkspaceStore();
const wsHub = useWsStore();
const router = useRouter();
const route = useRoute();
const message = useMessage();

// Hoisted to module scope — see composables/useSidebarState.ts for why.
// Each route mounts a fresh AppLayout, so a local ref would reset the
// collapsed state to false on every navigation.
const { sidebarCollapsed, sidebarScrollTop } = useSidebarState();

// Sidebar layout: one flat NMenu, sections rendered as NMenu groups
// (type: 'group'). Group children stay inline-visible — no drill, no
// collapse-to-hide. Group titles are static captions; only the leaf
// nav items react to clicks. We previously experimented with a
// Vercel-style sub-sidebar push-pop (the user explicitly asked to
// revert it — switching between sub-tabs felt heavy).

// Sidebar scroll position survives navigation via sidebarScrollTop
// (module-scoped). We attach a scroll listener on the .n-layout-sider's
// internal scroll container, save the offset on scroll, then restore
// it once on mount before paint so the user never sees the jump-to-top
// flicker that the previous behaviour caused.
const sidebarHostRef = ref<InstanceType<typeof NLayoutSider> | null>(null);
let scrollEl: HTMLElement | null = null;
let detachScroll: (() => void) | null = null;
function onScroll(): void {
  if (scrollEl !== null) sidebarScrollTop.value = scrollEl.scrollTop;
}
async function attachScrollWatcher(): Promise<void> {
  await nextTick();
  const host = sidebarHostRef.value as unknown as { $el?: HTMLElement } | null;
  const root = host?.$el ?? null;
  if (root === null) return;
  scrollEl = root.querySelector('.n-layout-sider-scroll-container');
  if (scrollEl === null) return;
  scrollEl.scrollTop = sidebarScrollTop.value;
  scrollEl.addEventListener('scroll', onScroll, { passive: true });
  detachScroll = () => scrollEl?.removeEventListener('scroll', onScroll);
}
onUnmounted(() => detachScroll?.());

onMounted(async () => {
  void attachScrollWatcher();
  if (auth.isAuthenticated && workspace.workspaces.length === 0) {
    await workspace.refresh().catch(() => {
      // best-effort — switcher just renders empty.
    });
  }
  if (auth.isAuthenticated) {
    void wsHub.connect();
    void auth.loadGrants();
  }
});

// Refresh the workspace list whenever the user logs back in.
watch(
  () => auth.isAuthenticated,
  async (isAuth) => {
    if (isAuth) {
      await workspace.refresh().catch(() => undefined);
      void wsHub.connect();
      void auth.loadGrants();
    } else {
      workspace.clear();
      wsHub.reset();
    }
  },
);

const workspaceDropdownOptions = computed<DropdownOption[]>(() => {
  const options: DropdownOption[] = [];
  if (workspace.workspaces.length === 0) {
    options.push({ key: 'empty', label: '(尚未关联 Linux 账号)', disabled: true });
  } else {
    for (const w of workspace.workspaces) {
      options.push({
        key: `pa-${w.pa.id}`,
        label: `${w.pa.linux_username} @ server #${w.pa.server_id}`,
      });
    }
  }
  options.push({ type: 'divider', key: 'd1' });
  options.push({ key: 'claim', label: '+ 关联新的 Linux 账号' });
  return options;
});

async function handleWorkspaceMenu(key: string): Promise<void> {
  if (key === 'claim') {
    await router.push({ name: 'claim-account' });
    return;
  }
  if (!key.startsWith('pa-')) return;
  const paId = Number(key.slice(3));
  workspace.setCurrent(paId);
  await router.push({ name: 'pa-workspace', params: { pa_id: paId } });
}

const currentWorkspaceLabel = computed(() => {
  const w = workspace.current;
  if (w === null) return '工作区';
  return `${w.pa.linux_username} @ #${w.pa.server_id}`;
});

/* lucide-vue-next exports each icon as a Vue FunctionalComponent —
 * see node_modules/lucide-vue-next/dist/lucide-vue-next.d.ts. Using
 * the type alias avoids the "typeof Cpu" hack and keeps the icon param
 * accurate for renderIcon/navItem. */
type IconCmp = FunctionalComponent;

function renderIcon(icon: IconCmp) {
  return () => h(icon, { size: 16, 'stroke-width': 1.5 });
}

interface MenuRoute {
  name: string;
  params?: Record<string, string | number>;
  query?: Record<string, string>;
}

function navItem(
  key: string,
  label: string,
  icon: IconCmp,
  route: MenuRoute,
  routes: Map<string, MenuRoute>,
): MenuOption {
  routes.set(key, route);
  return { key, icon: renderIcon(icon), label };
}

// Sidebar layout (Vercel-flat — one column, dividers between sections):
//
//   ┌──────────────────────────────────┐
//   │ Dashboard                        │
//   │ ────────────────                 │  ← divider
//   │ Reserve                          │  (workspace, only when PA selected)
//   │ Reservations                     │
//   │ Scripts                          │
//   │ Server Status                    │
//   │ Account & Settings               │
//   │ ────────────────                 │  ← divider
//   │ My account links                 │  (个人区)
//   │ All Reservations                 │
//   │ Usage Stats                      │
//   │ ────────────────                 │  ← divider
//   │ Link requests                    │  (管理区 server, if server-admin)
//   │ Servers I manage                 │
//   │ ────────────────                 │  ← divider
//   │ Overview                         │  (管理区 lab, if lab_admin)
//   │ Users · Server admins · …        │
//   └──────────────────────────────────┘
//
// No section captions, no nested submenus. Each section is a flat run
// of nav items; sections are visually separated only by a faint 1px
// divider (NMenu type: 'divider'). Active item gets a soft tinted bg
// rounded inside the rail's inner padding. Icon stroke is thin (1.5)
// to keep the visual weight low — see renderIcon above.

function divider(key: string): MenuOption {
  return { type: 'divider', key, props: { style: { margin: '6px 12px' } } };
}

function buildMenu(routes: Map<string, MenuRoute>): MenuOption[] {
  const items: MenuOption[] = [
    navItem('dashboard', '仪表盘', LayoutDashboard, { name: 'dashboard' }, routes),
  ];

  // 工作区 — flat row of PA-scoped pages.
  // 没有任何关联时给一个占位入口:让新人知道"工作区"要先关联 Linux 账号才解锁,
  // 而不是整段静默消失(主公实测会以为预约/服务器状态功能不见了)。
  const currentPa = workspace.current?.pa ?? null;
  if (currentPa === null && workspace.workspaces.length === 0) {
    items.push(divider('div-workspace'));
    items.push(
      navItem('claim-account', '解锁工作区(关联账号)', UserPlus, { name: 'claim-account' }, routes),
    );
  }
  if (currentPa !== null) {
    const paParams = { pa_id: currentPa.id };
    items.push(divider('div-workspace'));
    items.push(
      navItem('pa-reserve', '预约', Cpu, { name: 'pa-reserve', params: paParams }, routes),
    );
    items.push(
      navItem(
        'pa-reservations',
        '预约记录',
        CalendarCheck,
        { name: 'pa-reservations', params: paParams },
        routes,
      ),
    );
    items.push(
      navItem('pa-scripts', '脚本', Terminal, { name: 'pa-scripts', params: paParams }, routes),
    );
    items.push(
      navItem(
        'pa-server-status',
        '服务器状态',
        Activity,
        { name: 'pa-server-status', params: paParams },
        routes,
      ),
    );
    items.push(
      navItem(
        'pa-workspace',
        '账号与设置',
        Layers,
        { name: 'pa-workspace', params: paParams },
        routes,
      ),
    );
  }

  // 个人区
  items.push(divider('div-personal'));
  items.push(
    navItem('my-account-links', '我的账号关联', LinkIcon, { name: 'my-account-links' }, routes),
  );
  items.push(
    navItem('all-reservations', '全部预约', CalendarRange, { name: 'all-reservations' }, routes),
  );
  items.push(navItem('my-usage', '用量统计', BarChart3, { name: 'my-usage' }, routes));

  // 管理区(server)
  if (auth.isServerAdminOfAny) {
    items.push(divider('div-server-admin'));
    items.push(
      navItem('server-admin-inbox', '关联申请', Inbox, { name: 'server-admin-inbox' }, routes),
    );
    items.push(
      navItem('managed-servers', '我管理的服务器', Server, { name: 'managed-servers' }, routes),
    );
  }

  // 管理区(lab)
  if (auth.isLabAdmin) {
    items.push(divider('div-lab-admin'));
    items.push(navItem('lab-overview', '概览', LayoutDashboard, { name: 'lab-overview' }, routes));
    items.push(navItem('admin-users', '用户', UsersIcon, { name: 'admin-users' }, routes));
    // Phase L review (post-v5): lab_admin needs a direct entry to every
    // server in the lab — previously this was only reachable via the
    // server_admin group ("Servers I manage"), which lab_admins who
    // don't grant themselves server_admin couldn't see.
    items.push(navItem('servers', '服务器', Server, { name: 'servers' }, routes));
    items.push(navItem('admin-domain', '域名', Globe, { name: 'admin-domain' }, routes));
    items.push(
      navItem(
        'admin-enrollment-tokens',
        '接入令牌',
        KeyRound,
        { name: 'admin-enrollment-tokens' },
        routes,
      ),
    );
    items.push(navItem('lab-audit', 'Lab 审计日志', ScrollText, { name: 'lab-audit' }, routes));
    items.push(navItem('lab-alerts', 'Lab 告警', BellRing, { name: 'lab-alerts' }, routes));
  }

  void UserPlus; // reserved for K-3 wizard surface — silence unused-import
  return items;
}

const menuState = computed<{ options: MenuOption[]; routes: Map<string, MenuRoute> }>(() => {
  const routes = new Map<string, MenuRoute>();
  const options = buildMenu(routes);
  return { options, routes };
});

// The menu is already flat; collapsed state just hides labels behind
// the icons. Dividers stay (they render as 1px rules either way).
const menuOptions = computed<MenuOption[]>(() => menuState.value.options);

async function onMenuSelect(key: string): Promise<void> {
  const r = menuState.value.routes.get(key);
  if (r === undefined) return;
  await router.push({ name: r.name, params: r.params ?? {}, query: r.query ?? {} });
}

const activeKey = computed(() => (route.name as string | undefined) ?? null);

const userDropdownOptions = computed<DropdownOption[]>(() => [
  { key: 'profile', label: '个人资料' },
  { type: 'divider', key: 'div-1' },
  { key: 'logout', label: '退出登录' },
]);

async function handleUserMenu(key: string): Promise<void> {
  if (key === 'profile') {
    await router.push({ name: 'my-profile' });
  } else if (key === 'logout') {
    await auth.logout();
    message.success('已退出登录');
    await router.replace({ name: 'login' });
  }
}
</script>

<template>
  <NLayout has-sider class="app-shell">
    <NLayoutSider
      ref="sidebarHostRef"
      v-model:collapsed="sidebarCollapsed"
      :width="240"
      collapse-mode="width"
      :collapsed-width="64"
      bordered
      show-trigger
      class="sidebar"
    >
      <div v-if="!sidebarCollapsed" class="sidebar-brand">
        <Brand />
      </div>
      <NMenu
        :options="menuOptions"
        :value="activeKey"
        :collapsed="sidebarCollapsed"
        :indent="14"
        :collapsed-width="64"
        :collapsed-icon-size="20"
        @update:value="onMenuSelect"
      />
    </NLayoutSider>

    <NLayout>
      <NLayoutHeader bordered class="topbar">
        <div class="breadcrumb">{{ (route.meta.title as string) ?? 'CoreLab' }}</div>
        <div class="actions">
          <ThemeToggle />
          <NotificationBell v-if="auth.isAuthenticated" />
          <NDropdown
            v-if="auth.isAuthenticated"
            trigger="click"
            :options="workspaceDropdownOptions"
            @select="handleWorkspaceMenu"
          >
            <button class="workspace-chip" type="button">
              <span class="workspace-chip-label">{{ currentWorkspaceLabel }}</span>
              <span class="workspace-chip-caret">▾</span>
            </button>
          </NDropdown>
          <NDropdown
            v-if="auth.user"
            trigger="click"
            :options="userDropdownOptions"
            @select="handleUserMenu"
          >
            <button class="user-chip" type="button">
              <span class="user-chip-name">{{ auth.user.display_name }}</span>
              <span class="user-chip-role">({{ auth.user.role }})</span>
            </button>
          </NDropdown>
        </div>
      </NLayoutHeader>
      <main class="main">
        <slot />
      </main>
    </NLayout>
  </NLayout>
</template>

<style scoped>
.app-shell {
  /* Pin the shell to the viewport so the sidebar and the main column
   * scroll independently — sidebar stays put while page content scrolls. */
  height: 100vh;
  overflow: hidden;
}

.sidebar {
  background: var(--c-bg-elevated);
}
/* Naive paints a 1px divider element (.n-layout-sider__border) at the
 * sidebar's right edge — at the default rgba(255,255,255,0.09) it reads
 * as a hard line in dark mode. We rely on the bg contrast between
 * sidebar (elevated) and main (base) for separation instead. */
.sidebar :deep(.n-layout-sider__border) {
  background-color: var(--c-border-subtle);
  opacity: 0.35;
}
/* Native scrollbar on the sidebar's scroll container is 15px wide with
 * a high-contrast thumb — way too loud at 64px collapsed width and in
 * dark mode. Thin track + subtle thumb that tracks the design tokens. */
.sidebar :deep(.n-layout-sider-scroll-container) {
  scrollbar-width: thin;
  scrollbar-color: color-mix(in oklab, var(--c-border-default) 60%, transparent) transparent;
}
.sidebar :deep(.n-layout-sider-scroll-container)::-webkit-scrollbar {
  width: 6px;
}
.sidebar :deep(.n-layout-sider-scroll-container)::-webkit-scrollbar-track {
  background: transparent;
}
.sidebar :deep(.n-layout-sider-scroll-container)::-webkit-scrollbar-thumb {
  background: color-mix(in oklab, var(--c-border-default) 50%, transparent);
  border-radius: 3px;
}
.sidebar :deep(.n-layout-sider-scroll-container)::-webkit-scrollbar-thumb:hover {
  background: var(--c-border-default);
}
/* Vercel-flat sidebar styling — Phase L visual polish.
 *
 * Goals: airy item padding, thin divider rules between sections, soft
 * tinted active background (no left-bar rail), thin lucide icons. Most
 * overrides are :deep() into Naive's NMenu because NMenu's CSS-in-JS
 * vars don't expose every dimension we need (item-height, the active
 * pill shape, divider colour). */
.sidebar :deep(.n-menu) {
  padding: 6px 8px;
}
/* Each nav row: ~38px tall, label centred, generous left padding so
 * the icon sits inside the soft pill when active. */
.sidebar :deep(.n-menu-item) {
  height: auto;
}
.sidebar :deep(.n-menu-item-content) {
  padding: 9px 12px !important;
  border-radius: 6px;
  margin: 1px 0;
  font-size: 13.5px;
  color: var(--c-text-secondary);
  transition:
    background 0.12s,
    color 0.12s;
}
.sidebar :deep(.n-menu-item-content__icon) {
  margin-right: 12px !important;
}
/* Hover — soft tinted bg. */
.sidebar :deep(.n-menu-item-content:not(.n-menu-item-content--selected):hover) {
  background: color-mix(in oklab, var(--c-border-subtle) 65%, transparent);
  color: var(--c-text-primary);
}
/* Active — slightly stronger tint + crisp text + heavier icon. The
 * crucial bit: kill the default left-bar rail by zeroing the ::before
 * pseudo Naive paints there. */
.sidebar :deep(.n-menu-item-content--selected) {
  background: var(--c-border-subtle);
  color: var(--c-text-primary) !important;
  font-weight: 500;
}
.sidebar :deep(.n-menu-item-content--selected::before),
.sidebar :deep(.n-menu-item-content--selected::after) {
  background: transparent !important;
  border-color: transparent !important;
}
.sidebar :deep(.n-menu-item-content--selected .n-menu-item-content__icon),
.sidebar :deep(.n-menu-item-content--selected svg) {
  color: var(--c-text-primary) !important;
}
/* Divider between sections — extremely subtle 1px rule. */
.sidebar :deep(.n-menu-divider) {
  background: var(--c-border-subtle);
  opacity: 0.7;
  height: 1px;
}
/* Hide the n-layout-sider show-trigger triangle inside the menu's
 * collapsed state — we only want it where Naive places it on the
 * sider border. */
.sidebar :deep(.n-menu--collapsed .n-menu-item-content) {
  padding: 9px 0 !important;
  justify-content: center;
}
.sidebar :deep(.n-menu--collapsed .n-menu-item-content__icon) {
  margin-right: 0 !important;
}

/* .brand / .brand-mark / .brand-name now live in Brand.vue. */
.sidebar-brand {
  padding: 16px 16px 8px;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 var(--space-6);
  background: var(--c-bg-elevated);
}

.breadcrumb {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}

.actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.workspace-chip {
  font-size: var(--text-xs);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-full);
  background: var(--c-bg-base);
  color: var(--c-text-primary);
  border: 1px solid var(--c-border-default);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-family: var(--font-mono, monospace);
}
.workspace-chip-label {
  color: var(--c-text-primary);
}
.workspace-chip-caret {
  color: var(--c-text-tertiary);
}
.user-chip {
  font-size: var(--text-xs);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-full);
  background: var(--c-bg-sunken);
  color: var(--c-text-secondary);
  border: none;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-family: inherit;
}
.user-chip-name {
  color: var(--c-text-primary);
  font-weight: 500;
}
.user-chip-role {
  color: var(--c-text-tertiary);
}
/* .theme-chip lives in ThemeToggle.vue. */

.main {
  height: calc(100vh - 56px);
  overflow-y: auto;
  background: var(--c-bg-base);
}
</style>
