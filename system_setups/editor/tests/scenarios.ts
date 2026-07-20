// Shared helpers for locating and loading the shipped scenario JSONs and the
// generated component database. Used by both the Vitest and Playwright suites.
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'
import type { ComponentDb } from '../src/types'

const HERE = dirname(fileURLToPath(import.meta.url)) // system_setups/editor/tests
export const EDITOR_DIR = resolve(HERE, '..') //          system_setups/editor
export const SCENARIO_DIR = resolve(HERE, '..', '..') //  system_setups

/** All `*.scenario.json` files shipped in system_setups/, sorted for stable test order. */
export function listScenarioFiles(): string[] {
  return readdirSync(SCENARIO_DIR)
    .filter((f) => f.endsWith('.scenario.json'))
    .sort()
}

export function scenarioPath(file: string): string {
  return join(SCENARIO_DIR, file)
}

export function readScenario(file: string): string {
  return readFileSync(scenarioPath(file), 'utf-8')
}

/** Load the editor's generated component registry (public/data/component_db.json). */
export function loadComponentDb(): ComponentDb {
  const p = join(EDITOR_DIR, 'public', 'data', 'component_db.json')
  return JSON.parse(readFileSync(p, 'utf-8')) as ComponentDb
}
