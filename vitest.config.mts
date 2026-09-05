import {defineConfig} from 'vitest/config';

export default defineConfig({
  test: {
    include: ['frontend/tests/**/*.test.ts'],
    environment: 'jsdom',
    restoreMocks: true,
    clearMocks: true,
  },
});
