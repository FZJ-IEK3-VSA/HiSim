# Step 11 — an Irish subsidy catalogue as an AI-generated placeholder (todo H8, option 1)

**Status:** implementation specification, 2026-09-19. **Owner decision (2026-09-19):** "go with
option 1", i.e. the SEAI grants are encoded in the engine's catalogue language
(`hisim/subsidy_catalog/IE.json`), and every entry is marked **"AI-generated, needs to be
examined"** so the MVP has placeholders that a person later verifies scheme by scheme.
**Builds on:** `renovisor-staged-economics` at `3cbac31d` (step 10): the staged evaluator runs
`subsidy_mode: NONE` for a country without a catalogue, the translator builds the
`EconomicContext` (`hisim/renovisor/economics.py`), the catalogue loader and validator are
`hisim/economics/subsidies/catalog.py` and `hisim/economics/validation.py`, and
`python -m hisim.economics validate` is the data-file CI gate.

Ground rules as in step 10 §0 (worktree `/home/noah/hisim/HiSim-renovisor`, no commits, class-scope
constants, plain complete docstrings, 120 columns, flake8/mypy/pylint-critical/prospector clean on
every file touched, the three CI mypy runs). New tests `@pytest.mark.base`.

**Source.** The SEAI pages were read on 2026-09-19 through a headless browser; the extracted text
of every page is in `/home/noah/seai_shots/text/*.txt` (read them; they are the evidence for every
number below). The table in §2 is the transcription; where this file and the page text disagree,
the page text wins and the disagreement goes in the report.

---

## 1. The marking — what "AI-generated, needs to be examined" means in data

Every scheme carries the marker in three places, so that no consumer can miss it:

1. `display_name` ends with ` [AI draft — needs examination]`. The report and the
   `economics_result.json` `subsidies[]` rows show the display name, so the frontend user sees it.
2. `legal_basis` starts with `AI-GENERATED PLACEHOLDER (2026-09-19), NEEDS TO BE EXAMINED — ` and
   then names the programme (e.g. `SEAI Better Energy Homes, attic insulation grant`).
3. The source entries in `hisim/subsidy_catalog/sources.json` (one per SEAI page used, kind
   `WEBPAGE` if the source kinds allow it, else the closest existing kind — say which) carry
   `retrieved: "2026-09-19"` and a `notes` field beginning `AI-generated transcription of the page
   text, not legally verified; needs to be examined.` List, in the notes, what the transcription
   left out (§4).

Additionally: `catalog_snapshot_date: "2026-09-19"`; and a `hisim/subsidy_catalog/README_IE.md`
(short) saying the catalogue is a placeholder, how it was produced, the mapping decisions of §3 and
the review checklist of §4. If the loader tolerates a top-level `"review_status"` key (check —
`raw.get("schemes")` suggests it does), add `"review_status": "AI_GENERATED_NEEDS_EXAMINATION"` at
the top of `IE.json` too; if it refuses, leave it out and say so.

## 2. The grants, as the pages state them (2026-09-19)

Applicant eligibility common to the Better Energy Homes (BEH) individual grants: owner-occupiers,
landlords (non-private and commercial for insulation/controls; private and commercial for heat
pump; "all homeowners including private landlords" for PV), companies and owners' management
companies, charities, approved housing bodies. Tenants cannot apply. Grants are fixed amounts per
dwelling type, paid after works; through a One Stop Shop (OSS) they are deducted upfront. "You
cannot receive a grant which exceeds the total cost of works."

| SEAI measure | Detached | Semi-detached / end-terrace | Mid-terrace | Apartment | Built before | Notes from the page |
|---|---|---|---|---|---|---|
| Attic insulation | 2 000 | 1 500 | 1 400 | 1 100 | 2011 | first-time buyers (bought on/after 2025-01-01, home built ≤ 2010-12-31) or qualifying welfare payments: 2 500 / 1 900 / 1 800 / 1 400; not if attic previously grant-aided at this MPRN; apartments only top floor |
| Rafter insulation (OSS list only) | 3 000 | 3 000 | 2 000 | 1 500 | 2011 | |
| Cavity wall insulation | 1 800 | 1 300 | 850 | 700 | 2011 | qualifying welfare payments: 2 300 / 1 700 / 1 100 / 900; whole-surface only |
| Internal wall insulation (dry lining) | 4 500 | 3 500 | 2 000 | 1 500 | 2011 | whole-surface only |
| External wall insulation (the wrap) | 8 000 | 6 000 | 3 500 | 3 000 | 2011 | whole-surface only |
| Floor insulation (OSS list only) | 3 500 | 3 500 | 3 500 | 3 500 | 2011 | |
| Windows (complete upgrade) | 4 000 | 3 000 | 1 800 | 1 500 | 2011 | new windows U ≤ 1.4 W/m²K; replacing single/double glazing; attic and walls "good" or HLI ≤ 2.3 |
| External doors | 800 per door, max 2 doors | same | same | same | 2011 | |
| Heat pump system — unit grant | 6 500 | 6 500 | 6 500 | 4 500 | 2021 | air-to-water, ground-source, exhaust-air, water-to-water; replacing an existing non-grant-aided heat pump gets the unit grant only |
| Heat pump — central heating upgrade (radiators / underfloor) | 2 000 | 2 000 | 2 000 | 1 000 | 2021 | "where required"; not when replacing a heat pump |
| Heat pump — Renewable Heat Bonus | 4 000 | 4 000 | 4 000 | 4 000 | 2021 | swapping out oil/gas boiler, solid-fuel system or electric storage heating; not when replacing a heat pump |
| Heat pump air-to-air (unit) | 3 500 | 3 500 | 3 500 | 3 500 | 2021 | plus the 4 000 bonus = the 7 500 on the individual page; no hot water |
| Technical assessment (heat loss) | 200 | 200 | 200 | 200 | — | required for homes built before 2007 without a BER stating HLI ≤ 2.3 |
| Heating controls | 700 | 700 | 700 | 700 | 2011 | not with a heat pump grant |
| Solar PV | 700 €/kWp up to 2 kWp, then 200 €/kWp up to 4 kWp, capped at 1 800 | same | same | same | 2021 | pro rata (2.5 kWp → 1 500); not if PV previously grant-aided at this MPRN |
| Solar water heating | 1 200 | 1 200 | 1 200 | 1 200 | 2021 | |
| Mechanical ventilation (OSS only) | 1 500 | 1 500 | 1 500 | 1 500 | 2011 | |
| Air tightness (OSS only) | 1 000 | 1 000 | 1 000 | 1 000 | 2011 | |
| Home energy assessment (OSS only) | 350 | 350 | 350 | 350 | 2011 | |
| Project management (OSS only) | 2 000 | 1 600 | 1 200 | 800 | 2011 | |
| Warmer Homes Scheme | fully funded | | | | 2006 | owner-occupier on an eligible social welfare payment; measures chosen by SEAI's surveyor |
| Home Energy Upgrade Loan | 5 000–75 000 €, up to 10 years, "from as low as 3 %" | | | | — | works must be grant-aided and done by an OSS or community coordinator; ≥ 75 % of the loan on eligible measures; ≥ 20 % BER uplift |

OSS route (National Home Energy Upgrade Scheme): same amounts as above plus the OSS-only rows;
requires a post-works BER of B2 or better and either a heat pump installed or a primary-energy
uplift of 100 kWh/m²/yr. The landlord tax deduction (up to 10 000 € per property, works 2023–2025)
is expired and is **not** encoded.

## 3. Mapping onto the catalogue language — decisions

The catalogue can express: `applies_to.asset_classes` (ComponentType), `measure_kinds`
INSTALL/REPLACE, an eligibility tree over `applicant.*`, `building.*` (fixed vocabularies) and
`measure.technical_attributes.*` (free keys), benefits `LUMP_SUM | PER_UNIT | SHARE_OF_ELIGIBLE_COST
| BONUS_SHARE | SOFT_LOAN | ...`, an eligible-cost spec, cumulation groups and exclusions. Lump sums
are clamped to the eligible cost by the solver, which is the "never exceeds the cost of works" rule
for free.

1. **Dwelling type is a new context field.** `SubsidyBuildingContext.dwelling_type:
   Optional[DwellingType] = None` with `DwellingType(str, Enum)`: `DETACHED`,
   `SEMI_DETACHED_OR_END_TERRACE`, `MID_TERRACE`, `APARTMENT` (country-neutral: the BEG has no such
   axis, SEAI and the UK's schemes do). `None` = unanswered = UNDETERMINED, as for every other field.
   The translator fills it from the request's `building_type`: `detached_sfh` and `bungalow` →
   `DETACHED`, `semi_detached_sfh` → `SEMI_DETACHED_OR_END_TERRACE`, `terraced_sfh` → `MID_TERRACE`
   (an end-of-terrace cannot be told apart; note it in the mapping report as `approximated`),
   `apartment` → `APARTMENT`, `other` → `None`. One scheme per dwelling type where the amount
   differs, with a leaf `building.dwelling_type == <X>`; one scheme with no such leaf where the
   amount is flat. Keep DE/AT untouched.
2. **Two applicant booleans**, both `Optional[bool] = None` on `ApplicantProfile`:
   `receives_means_tested_benefit` (SEAI "qualifying welfare payments") and `first_time_buyer`
   (SEAI: bought a second-hand home on/after 2025-01-01 as a first home). The translator reads them
   from the request's `applicant` block when present (F7 — not in the vendored schema yet, so
   they stay `None` today) and never guesses.
3. **The route** (self-managed BEH vs One Stop Shop) is `ApplicantProfile.managed_full_retrofit:
   Optional[bool] = None` ("the works are delivered as one managed complete upgrade — Ireland's One
   Stop Shop route, a KfW-style full-refurbishment programme elsewhere"). OSS-only rows require
   `== true`; shared rows require nothing on it. The B2/uplift outcome condition cannot be
   evaluated by the engine and is **not** encoded — say so in `README_IE.md`.
4. **Asset classes.** Attic → `TOP_CEILING_UPPER_INSULATION`; rafter → `ROOF_INSULATION_BETWEEN_JOISTS`,
   `ROOF_INSULATION_OVER_JOISTS`, `WARM_ROOF_INSULATION`; internal → `WALL_INTERNAL_INSULATION` with
   the leaf `measure.technical_attributes.placement != "external_wall_cavity"`; **cavity** →
   `WALL_INTERNAL_INSULATION` with `placement == "external_wall_cavity"` (the translator maps the
   cavity placement onto that class, so the placement attribute is what tells them apart; the
   translator therefore adds `placement` to every envelope subject's technical attributes — do
   that in `hisim/renovisor/economics.py`); external → `WALL_EXTERNAL_INSULATION`; floor →
   `BASEMENT_CEILING_BOTTOM_INSULATION`, `BASEMENT_WALLS_INTERNAL_INSULATION`,
   `GROUND_EXTERNAL_INSULATION`; windows → `WINDOWS_TRIPLE_GLAZED` with
   `measure.technical_attributes.achieved_u_value_in_watt_per_m2_per_kelvin <= 1.4`; doors →
   `EXTERIOR_DOOR` as `LUMP_SUM 1600` (assumption: two doors, flagged in the display name suffix
   `(assumes 2 doors)`); heat pump unit → `HEAT_PUMP` INSTALL+REPLACE; central heating →
   `HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING`, `HEAT_DISTRIBUTION_SYSTEM_RADIATOR`,
   `HEAT_DISTRIBUTION_SYSTEM_LOW_TEMPERATURE_RADIATOR` with `building.existing_heating.asset_class
   != HEAT_PUMP`; Renewable Heat Bonus → `HEAT_PUMP` REPLACE with
   `building.existing_heating.energy_carrier in [NATURAL_GAS, HEATING_OIL, PELLETS, WOOD_CHIPS,
   ELECTRICITY]` and `building.existing_heating.asset_class != HEAT_PUMP`; technical assessment →
   `HEAT_PUMP` with `building.construction_year < 2007`, categories `PLANNING`; solar thermal →
   `SOLAR_THERMAL_SYSTEM`; ventilation → `VENTILATION_SYSTEM`; air tightness → `AIR_SEALING`. **Not
   encoded** (no asset class or no RenoVisor measure): air-to-air heat pumps, heating controls,
   home energy assessment, project management, the Warmer Homes Scheme (a 100 % grant whose measure
   list SEAI decides — encode it as `SHARE_OF_ELIGIBLE_COST rate 1.0` over all envelope and heating
   classes with `applicant.receives_means_tested_benefit == true`, `applicant.actor ==
   OWNER_OCCUPIER`, `construction_year < 2006`, excluding every other IE scheme, ONLY if the
   solver's exclusion handling makes that clean; otherwise leave it out and list it in §4). The
   loan → `SOFT_LOAN interest_rate 0.03 term 10` on all encoded classes with
   `managed_full_retrofit == true`, cumulation group `IE_LOANS`, no exclusions (it stacks on the
   grants).
5. **Solar PV tiering** is not expressible with one `PER_UNIT` (no cap on the benefit). Encode
   four `LUMP_SUM` steps on `measure.technical_attributes.peak_power_in_kwp` (the translator adds
   that attribute for the PV subject from the realized `PVSystem` power): `>= 4` → 1 800;
   `>= 3 and < 4` → 1 600; `>= 2 and < 3` → 1 400; `>= 1 and < 2` → 700. Below 1 kWp: nothing.
   Say in `README_IE.md` that the true rule is pro rata and that a `TIERED_PER_UNIT` benefit kind
   would remove the approximation; record that as a new HiSim todo (§6).
6. **Construction-year leaves**: `building.construction_year <= 2010` for the 2011 rows,
   `<= 2020` for the 2021 rows, `< 2006` for Warmer Homes.
7. **Actor leaf** everywhere: `applicant.actor in [OWNER_OCCUPIER, LANDLORD,
   CONDOMINIUM_ASSOCIATION]`.
8. **Cumulation.** All grants are fixed amounts that add up across measures; each measure gets one
   grant. Put every grant scheme in group `IE_SEAI_GRANTS` with `combined_rate_cap: null`. Use
   `excludes` for: welfare/first-time-buyer attic variants exclude the standard attic variant of
   the same dwelling type (and vice versa); welfare cavity excludes standard cavity; the OSS
   `managed_full_retrofit` rows exclude nothing. Check with the solver that two `LUMP_SUM` schemes
   on different asset classes do not interfere; if the group semantics only make sense for share
   kinds, use one group per measure instead and say so.
9. **Validity**: `valid_from: "2026-02-03"` for the rows the pages date to 3 February 2026 (heat
   pump, cavity, attic increases), `"2026-01-01"` otherwise; `valid_to: null`.
10. **Eligible cost**: `categories [INVESTMENT, PLANNING, REMOVAL]`, `basis GROSS`, no caps,
    `proration NONE` (SEAI grants are per dwelling, not per m²).
11. **Questions file** `questions_IE.json` in **en and de** (the validator requires both), one
    entry per context field any IE scheme references, including the three new fields. Plain wording;
    mark the file header comment as AI-generated too.
12. **Wire it in.** `hisim/renovisor/run.py` (and `staged`) set `subsidy_catalog_path` to the
    shipped `hisim/subsidy_catalog` directory **when `<CC>.json` exists there**, so Ireland now
    evaluates subsidies instead of `NONE`; the `economics_result.json` `subsidies[]` rows then carry
    `awarded | ineligible | undetermined` with the display name (and its marker). A request without
    an `applicant` block leaves the two booleans and the route `None`, so the welfare, first-time
    buyer, OSS-only and loan rows come out `undetermined` and the standard rows decide on dwelling
    type, construction year and the existing heating — exactly what the E-spec §5.7 asks for.

## 4. What the transcription leaves out (goes into `README_IE.md` and the sources' notes)

MPRN "no previous grant for this measure" rules; the whole-surface rule for walls; the second-wall
measure rules; window "good insulation / HLI ≤ 2.3" prerequisite; heat pump HLI ≤ 2.3 requirement
for pre-2007 homes; the OSS B2/uplift outcome; approved-housing-body amounts (different, not
transcribed); traditional-homes pilot; the landlord tax deduction (expired 2025); air-to-air heat
pumps; heating controls; home energy assessment and project management grants; de minimis / GBER
for landlords; the loan's 75 % rule and provider-specific rates. Every one of these is a line in
the review checklist a person ticks.

## 5. Tests

- `tests/test_economics_subsidies.py`: the country loops over `("DE", "AT")` gain `"IE"` where they
  test loading and validation; `python -m hisim.economics validate` exits 0 with the new files.
- New `tests/economics/test_ie_catalog.py` (`base`): every IE scheme's display name ends with the
  marker and its legal basis starts with it; every referenced context field has a question in en
  and de; the detached-house attic grant is awarded at 2 000 € for a 1990 detached owner-occupier
  and `undetermined` when `dwelling_type` is `None`; the Renewable Heat Bonus is awarded when an
  oil boiler is replaced and ineligible when a heat pump is; the PV steps give 1 400 / 1 600 / 1 800
  at 2, 3 and 4 kWp; cavity and internal wall grants do not both fire on one subject.
- `tests/renovisor/test_economics_context.py`: `dwelling_type`, `placement` and `peak_power_in_kwp`
  arrive in the context; `other` gives `None`.
- End-to-end (`tests/renovisor/test_staged_economics.py`): on the vendored mockup package the
  `subsidies[]` rows are no longer all `undetermined`; at least the heat pump unit grant is
  `awarded`; every row's `scheme` display name carries the marker.

## 6. Bookkeeping

Append under H8 in `/home/contract-proposals/todos.md` (re-read immediately before, one edit,
other agents' items untouched): a dated "HiSim implementer" note listing the file, the marker,
the three new context fields (and that the frontend's `applicant` block should carry
`receives_means_tested_benefit`, `first_time_buyer`, `managed_full_retrofit` — for the frontend
agent, F7), the decisions of §3 and the omissions of §4. Add a new H-item "TIERED_PER_UNIT
benefit kind for pro-rata grants (SEAI solar PV)". Do not tick H8: it is done when a person has
examined the entries, and the note says so.

## 7. Done means

`validate` exits 0; the tests of §5 pass together with `tests/renovisor`, `tests/economics`,
`tests/test_economics_subsidies.py`, `tests/test_economics_subsidy_integration.py`,
`tests/test_economics_subsidy_solver.py`, `tests/test_economics_bridge.py`; the three CI mypy
runs, pylint critical-only and prospector are clean on every touched file; the docs build has no
new warning. The final report lists every scheme id with its amount and its page source, every
mapping decision beyond §3, and every omission.
