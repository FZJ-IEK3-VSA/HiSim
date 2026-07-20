// Tier 2 — full UI round-trip driven through a real headless browser.
//
// For every shipped scenario JSON this drives the actual editor exactly as a user would:
// click "Open JSON" and pick the file through the real file chooser, wait for the canvas
// to render, click "Save JSON" and capture the download, then compare. This exercises the
// React components, the Zustand store, and the auto-validate-on-open behaviour — the wiring
// the Tier 1 unit tests cannot see.
//
// The semantic comparison reuses tests/roundtrip-core.ts, so both tiers judge fidelity by
// the same rules. Dynamic-port scenarios are held to the universal invariants only
// (see tests/README.md).

import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { listScenarioFiles, readScenario, scenarioPath } from '../scenarios'
import { diffScenario, usesDynamicPorts } from '../roundtrip-core'

const files = listScenarioFiles()

test.describe('editor UI round-trip (Open JSON → Save JSON)', () => {
  for (const file of files) {
    test(file, async ({ page }) => {
      const original = readScenario(file)
      const dynamic = usesDynamicPorts(original)

      await page.goto('/')
      // Palette shows "Loading…" until the component database has been fetched and applied;
      // wait for it to clear so "Open JSON" actually has a registry to import against.
      await expect(page.getByText(/Loading/)).toBeHidden({ timeout: 30_000 })

      // ── Open the scenario through the real (hidden) file input ────────────
      const [chooser] = await Promise.all([
        page.waitForEvent('filechooser'),
        page.getByRole('button', { name: 'Open JSON' }).click(),
      ])
      await chooser.setFiles(scenarioPath(file))

      // Import succeeded → at least one component card is rendered on the canvas.
      await expect(page.locator('.react-flow__node').first()).toBeVisible({ timeout: 15_000 })

      // ── Save the scenario and capture the download ───────────────────────
      const [download] = await Promise.all([
        page.waitForEvent('download'),
        page.getByRole('button', { name: 'Save JSON' }).click(),
      ])
      const saved = readFileSync(await download.path(), 'utf-8')

      // Universal: the saved file is valid JSON and preserves the component set.
      const inComps = (JSON.parse(original).components ?? []).length
      const outComps = (JSON.parse(saved).components ?? []).length
      expect(outComps).toBe(inComps)

      if (!dynamic) {
        // Full semantic fidelity for dynamic-free scenarios.
        expect(diffScenario(original, saved)).toEqual([])
        // Auto-validate-on-open must not report errors in the status bar.
        await expect(page.locator('footer')).not.toContainText('error')
      }
    })
  }
})
