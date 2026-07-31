// Tier 2 — browser coverage for the two features that only exist as wiring between the
// store and the UI, and so cannot be reached from the Node-level suites:
//
//   * "Suggest components" — proposing a component and adding it to the canvas;
//   * the pre-export guard — refusing to save silently on a stale validation result.
//
// Both start from a shipped scenario with one component deleted, which is the situation
// they exist for: a scenario that is not finished yet.

import { test, expect, type Page } from '@playwright/test'
import { scenarioPath } from '../scenarios'

const SCENARIO = 'basic_household.scenario.json'

/** Open a shipped scenario through the editor's real file chooser. */
async function openScenario(page: Page) {
  await page.goto('/')
  // The palette shows "Loading…" until the component database has been fetched.
  await expect(page.getByText(/Loading/)).toBeHidden({ timeout: 30_000 })

  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByRole('button', { name: 'Open JSON' }).click(),
  ])
  await chooser.setFiles(scenarioPath(SCENARIO))
  await expect(page.locator('.react-flow__node').first()).toBeVisible({ timeout: 15_000 })
}

/**
 * The card whose *header* is exactly this instance name.
 *
 * Matching on the card's whole text would be ambiguous: the ElectricityMeter's dynamic port
 * labels contain the names of the components feeding it (`Input_PVSystem_ElectricityOutput_0`).
 */
const card = (page: Page, name: string) =>
  page.locator('.react-flow__node').filter({ has: page.getByText(name, { exact: true }) })

/** Delete a card by its instance name, via the right-click context menu. */
async function deleteNode(page: Page, name: string) {
  const node = card(page, name)
  await expect(node).toHaveCount(1)
  await node.click({ button: 'right' })
  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(node).toHaveCount(0)
}

test.describe('suggest components', () => {
  test('proposes the deleted component and puts it back', async ({ page }) => {
    await openScenario(page)
    await deleteNode(page, 'Weather')

    // Deleting the weather source leaves every solar input on the PV system unconnected,
    // so validation now has something to say — and the suggestion is unambiguous.
    // `exact` matters: role-name matching is substring by default, and the status bar's
    // "validation out of date — validate" button would match too.
    await page.getByRole('button', { name: 'Validate', exact: true }).click()
    await expect(page.locator('footer')).toContainText('error')

    await page.getByRole('button', { name: 'Suggest components' }).first().click()
    await expect(page.getByText('Would fill an unconnected input')).toBeVisible()

    // The proposal names both the component and a port pairing it would connect.
    await expect(page.getByText('Weather.TemperatureOutside').first()).toBeVisible()

    await page.getByRole('button', { name: '+ Add' }).first().click()

    // Back on the canvas, wired up, and validation re-run: the errors are gone.
    await page.getByRole('button', { name: 'Close' }).click()
    await expect(card(page, 'Weather')).toHaveCount(1)
    await expect(page.locator('footer')).not.toContainText('error')
  })
})

test.describe('export guard', () => {
  test('warns before saving with an out-of-date validation result', async ({ page }) => {
    await openScenario(page)
    // Opening validates automatically, so the result is current — until this edit.
    await deleteNode(page, 'PVSystem')
    await expect(page.locator('footer')).toContainText('validation out of date')

    await page.getByRole('button', { name: 'Save JSON' }).click()
    await expect(page.getByText('Validation is out of date')).toBeVisible()

    // Cancelling writes nothing.
    const download = page.waitForEvent('download', { timeout: 2_000 }).catch(() => null)
    await page.getByRole('button', { name: 'Cancel' }).click()
    expect(await download).toBeNull()
    await expect(page.getByText('Validation is out of date')).toBeHidden()
  })

  test('exports without asking when validation is current and clean', async ({ page }) => {
    await openScenario(page)
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'Save JSON' }).click(),
    ])
    expect(download.suggestedFilename()).toContain('.scenario.json')
  })
})
