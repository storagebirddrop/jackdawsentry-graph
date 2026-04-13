import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Default environment for pure-function tests.
    // Store tests override this per-file with @vitest-environment jsdom.
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
});
