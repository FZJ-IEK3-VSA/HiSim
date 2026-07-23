import { defineConfig } from 'vitest/config'

// Tier 1 (unit) round-trip tests. Runs in a plain Node environment — the import/export
// logic under test has no DOM dependencies. The Playwright E2E specs (tests/e2e/*.spec.ts)
// are excluded here; run them with `npm run test:e2e`.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
    exclude: ['tests/e2e/**', 'node_modules/**', 'dist/**'],
  },
})
