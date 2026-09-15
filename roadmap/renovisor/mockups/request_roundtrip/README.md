# Request round trip — what the RenoVisor page sends, and what comes back

A worked example of the whole path under the **v0.4 draft** of the contract (branch
`hisim-alignment` of `renovisor-api-contract`), for an Irish 1988 detached house with a gas boiler
whose owner asks for a deep retrofit with a heat pump. Files are numbered in the order they travel.

| # | File | Who produces it | What it is |
|---|---|---|---|
| 01 | `01_post_homeinventories.request.json` | the page | `POST /homeinventories` body: one `HomeInventoryInput`. Every enum value is a HiSim member name (C3); `null` on a leaf means "not stated" (HiSim reads the TABULA archetype or reports the field as defaulted); a whole block the house does not have — PV, battery, solar thermal, ventilation, air conditioning — is **omitted**, not `null`, because the schema types the blocks as objects. Validated against the v0.4 schema. |
| 02 | `02_post_homeinventories.response.json` | the C# backend | the content-hash id the page keeps; the body is 01 plus `id`. |
| 03 | `03_post_packages.request.json` | the page | `POST /homeinventories/{hi_id}/packages` body: one `PackageDefinition`. Measures in the generic Q1 shape (`measure_id` + `options`), the Experts option `thickness_in_mm` stated once and left to the target-driven default once; grants, a three-phase schedule referencing the measure ids, financing. Validated against the v0.4 schema. |
| 04 | `04_post_packages.response.json` | the C# backend | the content-hash `pkg_id`. |
| 05 | `05_container_input/` | the C# backend | the three files it writes into the container's input directory: `home_inventory.json` (= 01), `package.json` (the `measures` list of 03 — grants, schedule and financing never reach HiSim), `simulation.yaml` (period, resolution, post-processing; A18). Run with `python -m hisim.renovisor calculate 05_container_input <out>`. |
| 06 | `06_container_output.*.json` | HiSim | **verbatim from a real run** of the committed test example (`tests/renovisor/example_*`, one January day, `RENOVISOR_IMAGE_DIGEST` set to a placeholder): `result.json` with every KPI and cost as `{value, provenance, source[, period]}`, `calculation.json`, and the translation report abridged to its first field lines and all measure lines. Not from 05, because the vendored contract HiSim reads is still v0.3 and 05 is v0.4 (see the refresh note in `challenges.md` §11 and the step-7 report). |
| 07 | `07_get_detailed_simulation.*.response.json` | the C# backend | what the page gets from `GET …/packages/{pkg_id}/detailed-simulation`: `running` while pending (HTTP 202), `finished` with the package echoed and HiSim's payload embedded (HTTP 200, cacheable), `refused` with a reason code and field path when HiSim cannot simulate the package (here: a material with no database row). |

Reading 06/07: `PARTIAL` on the one-day example means "scaled to a year from one January day" for
the two period-bound KPIs, "German grid factors" for emissions, and "AI-estimate device prices plus
material-only envelope cost" for every cost; `MOCKED` values are the contract's own examples; the
three entries under `missing` are the grant (no Irish subsidy catalogue yet), payback (needs the
base run) and property value (no model). `embodied_co2_in_kg` is negative because the materials
database nets wood fibre's biogenic storage into its footprint — question 11 of `REQUESTS.md`.
