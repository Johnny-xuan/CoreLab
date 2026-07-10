<script setup lang="ts">
/**
 * Brand — CoreLab logo + wordmark for the sidebar header.
 *
 * Was an inline ``h()`` render-function in AppLayout.vue; promoted to
 * an SFC so the markup reads as template and the brand is reusable in
 * other shells (login screen, error fallback, etc.).
 */
</script>

<template>
  <div class="brand">
    <img src="/logo.png" alt="CoreLab" class="brand-mark" />
    <span class="brand-name">CoreLab</span>
  </div>
</template>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
}
.brand-mark {
  height: 26px;
  width: auto;
  max-width: 32px;
  object-fit: contain;
  display: block;
  flex-shrink: 0;
}
.brand-name {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--c-text-primary);
}
</style>
