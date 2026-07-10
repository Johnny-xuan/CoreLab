import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config';

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'happy-dom',
      include: ['tests/**/*.spec.ts'],
      exclude: ['tests/e2e/**'],
      globals: true,
    },
  }),
);
