/**
 * Phase M M-4 — Driver.js spotlight tour for the onboarding checklist.
 *
 * `startOnboardingTour()` walks the user through the OnboardingChecklist
 * card row-by-row: half-opaque overlay, framed spotlight on the current
 * step's row + its CTA, popover with copy explaining what to do. Steps
 * adapt to where the lab currently is — if the agent is already online
 * we skip those rows and call attention to "Invite your first user"
 * instead. Used both as auto-start on a fresh lab (server count == 0,
 * once per browser) and as a manual "Tour" button on the checklist
 * header for replay.
 */

import { driver, type DriveStep } from 'driver.js';
import 'driver.js/dist/driver.css';

import type { OnboardingStatus } from '@/api/labOverview';

export const TOUR_SEEN_KEY = 'corelab.onboarding.tour.seen';

interface TourOptions {
  /** When true, ignore the localStorage "seen" flag and replay anyway. */
  force?: boolean;
  /** Current onboarding status — drives which steps light up. */
  status: OnboardingStatus | null;
}

function row(stepKey: string): string {
  // Each checklist row has data-tour-step="<key>" so the tour can pin
  // its spotlight even when DOM ordering changes.
  return `[data-tour-step="${stepKey}"]`;
}

function cta(stepKey: string): string {
  return `[data-tour-cta="${stepKey}"]`;
}

export function startOnboardingTour(opts: TourOptions): void {
  const { force = false, status } = opts;

  // First-pass dedupe — caller layers the "fresh lab + never seen"
  // check, but defend in depth so the manual "Tour" button can always
  // replay by passing force=true.
  if (!force && localStorage.getItem(TOUR_SEEN_KEY) === '1') return;

  const steps: DriveStep[] = [
    {
      element: '[data-tour="checklist"]',
      popover: {
        title: '欢迎使用 CoreLab 🎉',
        description:
          '这是你的 onboarding 大纲。跟着接下来几步把第一台 GPU server 接进来,把第一个用户邀请进来,平台就可以正常用了。',
        side: 'bottom',
        align: 'start',
      },
    },
    {
      element: row('server'),
      popover: {
        title: '第 1 步:加第一台 server',
        description:
          status && status.servers_count > 0
            ? `已有 ${status.servers_count} 台 server 注册,继续往下走。`
            : '在 Enrollment tokens 页面生成一个 token,UI 会给你一段 `curl … | bash` 的安装命令。',
        side: 'bottom',
      },
    },
    {
      element: cta('server'),
      popover: {
        title: '点这里 ↘',
        description: '进入 Servers 页面,Add server,生成 enrollment token。',
        side: 'left',
      },
    },
    {
      element: row('agent'),
      popover: {
        title: '第 2 步:agent 上线',
        description:
          status && status.online_servers_count > 0
            ? `${status.online_servers_count} 个 agent 已经在线 ✓。当 agent 第一次连上后端,这里会自动变绿。`
            : '把刚才那段 snippet 粘到 GPU 主机以 root 执行。agent 上线后 5 秒内这一步自动打钩(轮询的)。',
        side: 'bottom',
      },
    },
    {
      element: row('user'),
      popover: {
        title: '第 3 步:邀请第一个 user',
        description:
          status && status.users_count > 1
            ? '已经邀请过用户。可以继续给他们 onboard Linux 账号。'
            : '回到 Users 页面,点 Invite user,把生成的 activation URL 发给同学。',
        side: 'bottom',
      },
    },
    {
      element: cta('user'),
      popover: {
        title: '点这里 ↘',
        description: '打开 Users 页面,Invite user。',
        side: 'left',
      },
    },
    {
      element: row('link'),
      popover: {
        title: '第 4 步:第一个 Linux 账号绑定',
        description:
          '用户用 activation URL 设密码,在 server 上 ssh-challenge 自报家门,或者你直接 admin onboard。一旦 account_link 表里有第一行,这一步打钩。',
        side: 'bottom',
      },
    },
    {
      element: row('reservation'),
      popover: {
        title: '第 5 步:第一个预约',
        description:
          '用户预约一段 GPU 时间(可选附 script)。reservation 进 DB 那一刻 checklist 自动收起,Dashboard 回到正常数据视图。',
        side: 'bottom',
      },
    },
    {
      element: '[data-tour="checklist"]',
      popover: {
        title: '搞定 🎉',
        description:
          '随时可以从 checklist 右上角的 "Tour" 按钮重新打开这次走查。Dashboard 顶部隐藏后,可以在 /admin/overview 看 Lab Overview 的完整数据。',
        side: 'bottom',
        align: 'start',
      },
    },
  ];

  const tour = driver({
    showProgress: true,
    progressText: '{{current}} / {{total}}',
    nextBtnText: '下一步 →',
    prevBtnText: '← 上一步',
    doneBtnText: '完成',
    overlayOpacity: 0.6,
    allowClose: true,
    onDestroyStarted: () => {
      // Persist "seen" only on completion or explicit close — so a
      // mid-tour refresh doesn't accidentally consume the auto-start.
      localStorage.setItem(TOUR_SEEN_KEY, '1');
      tour.destroy();
    },
    steps,
  });

  // Slight delay so the popover doesn't paint over a still-loading
  // checklist row in production builds (the polled status arrives
  // asynchronously after the component mounts).
  setTimeout(() => tour.drive(), 150);
}

export function resetTourSeen(): void {
  localStorage.removeItem(TOUR_SEEN_KEY);
}
