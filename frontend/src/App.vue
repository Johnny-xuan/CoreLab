<script setup lang="ts">
/**
 * App.vue — global provider shell.
 *
 * NConfigProvider hoists Naive UI's locale + theme. The themeOverrides
 * bend Naive's defaults toward the Vercel-inspired design system:
 * - primary CTAs go monochrome (#0a0a0a → #1f1f1f → #2a2a2a)
 * - info / link accent becomes Vercel blue (#0070f3)
 * - radii align with tokens (6px small, 4px tiny)
 * - typography flows through the Geist family vars
 *
 * Overrides apply equally in light and dark mode — Naive merges them on
 * top of whichever theme is active, so the same constants land on both.
 */
import {
  NConfigProvider,
  NDialogProvider,
  NLoadingBarProvider,
  NMessageProvider,
  NNotificationProvider,
  darkTheme,
  dateZhCN,
  zhCN,
  type GlobalThemeOverrides,
} from 'naive-ui';
import { computed } from 'vue';

import { useThemePref } from '@/composables/useThemePref';

const { isDark } = useThemePref();
const theme = computed(() => (isDark.value ? darkTheme : null));

// Primary CTA + Card-border tokens flip with the theme.
// In light: Vercel-style black-on-white CTAs.
// In dark : white-on-dark CTAs so the primary highlights (selected menu
//           item, primary buttons) remain visible against the dark shell.
const themeOverrides = computed<GlobalThemeOverrides>(() => {
  const dark = isDark.value;
  return {
    common: {
      primaryColor: dark ? '#fafafa' : '#0a0a0a',
      primaryColorHover: dark ? '#e4e4e7' : '#1f1f1f',
      primaryColorPressed: dark ? '#d4d4d8' : '#2a2a2a',
      primaryColorSuppl: dark ? '#e4e4e7' : '#1f1f1f',
      infoColor: dark ? '#338cff' : '#0070f3',
      infoColorHover: dark ? '#66a8ff' : '#338cff',
      infoColorPressed: dark ? '#0070f3' : '#0060d4',
      infoColorSuppl: dark ? '#66a8ff' : '#338cff',
      successColor: '#0cce6b',
      warningColor: '#f5a623',
      errorColor: '#e00027',
      borderRadius: '6px',
      borderRadiusSmall: '4px',
      fontFamily:
        "'Geist', 'PingFang SC', 'Microsoft YaHei', 'Hiragino Sans GB', system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
      fontFamilyMono:
        "'Geist Mono', ui-monospace, 'JetBrains Mono', SFMono-Regular, Menlo, Consolas, monospace",
      fontSize: '14px',
      fontSizeMedium: '14px',
      fontSizeSmall: '13px',
      fontSizeTiny: '12px',
    },
    Button: {
      textColorPrimary: dark ? '#0a0a0a' : '#ffffff',
      fontWeight: '500',
    },
    Card: {
      borderRadius: '8px',
      borderColor: dark ? '#27272a' : '#eaeaea',
    },
    Input: {
      borderRadius: '6px',
    },
    Tag: {
      borderRadius: '4px',
    },
    Menu: {
      // Selected item: tint + accent stripe (NOT the primary fill, which
      // would be invisible in dark). Reads as "active" without blending in.
      itemTextColorActive: dark ? '#fafafa' : '#0a0a0a',
      itemTextColorActiveHover: dark ? '#fafafa' : '#0a0a0a',
      itemTextColorActiveCollapsed: dark ? '#fafafa' : '#0a0a0a',
      itemColorActive: dark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)',
      itemColorActiveHover: dark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)',
      itemColorActiveCollapsed: dark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)',
      itemIconColorActive: dark ? '#fafafa' : '#0a0a0a',
      itemIconColorActiveHover: dark ? '#fafafa' : '#0a0a0a',
      itemIconColorActiveCollapsed: dark ? '#fafafa' : '#0a0a0a',
    },
  };
});
</script>

<template>
  <NConfigProvider
    :theme="theme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <NLoadingBarProvider>
      <NDialogProvider>
        <NNotificationProvider>
          <NMessageProvider>
            <RouterView />
          </NMessageProvider>
        </NNotificationProvider>
      </NDialogProvider>
    </NLoadingBarProvider>
  </NConfigProvider>
</template>
