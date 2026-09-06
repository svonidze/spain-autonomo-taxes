import {defineConfig} from 'vitest/config';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  test: {
    include: ['frontend/tests/**/*.test.ts'],
    environment: 'jsdom',
    restoreMocks: true,
    clearMocks: true,
  },
});
