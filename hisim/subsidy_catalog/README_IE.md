# IE.json — an AI-generated placeholder catalogue

**Status: AI-GENERATED PLACEHOLDER (2026-09-19), NEEDS TO BE EXAMINED.**

`IE.json` encodes the Irish SEAI home energy grants in the engine's catalogue language. Every
number in it was transcribed by an AI agent from SEAI web pages read on 2026-09-19; **no person
has verified a single scheme against the programme terms and conditions.** Until someone has, a
euro amount this catalogue produces is a plausible figure, not a promise.

The marking is in the data itself, in three places, so that no consumer can miss it:

* every scheme's `display_name` ends with `[AI draft — needs examination]`, which is the string
  the report and `economics_result.json`'s `subsidies[]` rows show a user;
* every scheme's `legal_basis` starts with
  `AI-GENERATED PLACEHOLDER (2026-09-19), NEEDS TO BE EXAMINED —`;
* every source entry in `sources.json` (ids `src_seai_*_2026`) carries `retrieved: "2026-09-19"`
  and a `notes` field starting `AI-generated transcription of the page text, not legally
  verified; needs to be examined.`, followed by what that page's transcription left out.

The file also carries `"review_status": "AI_GENERATED_NEEDS_EXAMINATION"` at the top and
`"catalog_snapshot_date": "2026-09-19"`. The loader ignores unknown top-level keys, so the
review status is data for a reader rather than something the engine acts on.

## How it was produced

The SEAI pages were fetched through a headless browser; the extracted page text is the evidence
for every number. Ten pages were used, one `sources.json` entry each: attic insulation, wall
insulation, windows and doors, heat pump systems, solar PV, solar water heating, the One Stop
Shop grant tables, the Warmer Homes Scheme, the Home Energy Upgrade Loan and the landlord
supports page. The transcription and the mapping decisions below were written down first (in
`roadmap/renovisor/implementation/step11_irish_subsidy_catalogue.md`) and the file was generated
from them.

## Mapping decisions

1. **Dwelling type.** Nearly every SEAI grant is a fixed amount banded by dwelling type, so the
   eligibility context gained `building.dwelling_type` (`DwellingType`: `DETACHED`,
   `SEMI_DETACHED_OR_END_TERRACE`, `MID_TERRACE`, `APARTMENT`). One scheme per band where the
   amount differs; where two bands pay the same (the heat pump unit grant for all three house
   types, rafter insulation for detached and semi-detached) one scheme covers them with an `in`
   leaf. The RenoVisor translator fills the field from the request's `building_type`:
   `detached_sfh` and `bungalow` → `DETACHED`, `semi_detached_sfh` →
   `SEMI_DETACHED_OR_END_TERRACE`, `terraced_sfh` → `MID_TERRACE` (**approximated**: the request
   cannot say whether a terraced house is at the end of its terrace, which is the better-paid
   band; the mapping report says so), `apartment` → `APARTMENT`, `other` → unanswered.
2. **Two applicant booleans.** `applicant.receives_means_tested_benefit` is SEAI's "qualifying
   welfare payment" and `applicant.first_time_buyer` is its first-time-buyer support. SEAI pays
   the higher attic grant for *either*, so one enhanced attic scheme per band carries an `any`
   of the two; the higher cavity grant is the welfare boolean alone. Both default to unanswered,
   which makes the enhanced schemes *undetermined* rather than denied.
3. **The route.** `applicant.managed_full_retrofit` is "the works are delivered as one managed
   complete upgrade" — Ireland's One Stop Shop, a KfW-style full-refurbishment programme
   elsewhere. Rafter insulation, floor insulation, mechanical ventilation, air tightness and the
   loan require it; every other scheme ignores it, because the amounts are the same on both
   routes.
4. **Cavity versus dry lining.** Both are `WALL_INTERNAL_INSULATION` in HiSim's asset classes, so
   they are told apart by the measure's `placement` technical attribute: the cavity grant
   requires `placement == "external_wall_cavity"`, the dry-lining grant requires `!=`. The
   translator publishes `placement` for every envelope subject it builds, so a measure whose
   placement is unknown makes both undetermined rather than firing the wrong one.
5. **The heat pump bundle** is three schemes, as the page describes it: the unit grant (6,500 €,
   4,500 € for apartments), the central heating grant on the heat-distribution classes (2,000 €,
   1,000 € for apartments, only where the existing generator is not already a heat pump) and the
   4,000 € Renewable Heat Bonus on a REPLACE of an oil, gas, solid-fuel or electric system. The
   sums check out against the page's bundle totals of 12,500 € and 9,500 €.
6. **Construction-year leaves** are `<= 2010` for the "before 2011" rows, `<= 2020` for the
   "before 2021" rows and `< 2006` for Warmer Homes; `< 2007` for the technical assessment.
7. **Cumulation.** Every grant is in group `IE_SEAI_GRANTS` with no combined rate cap, the loan
   in `IE_LOANS`. The group label only affects share-kind benefits, and the solver works one
   measure at a time, so two fixed-amount grants on different asset classes never interfere.
   `excludes` is used for the standard/enhanced attic pairs, the standard/welfare cavity pairs
   and for Warmer Homes, which excludes every other scheme in the file.

## Approximations this file contains

Two rules the catalogue language cannot state exactly. Both are flagged in the scheme's
`display_name` as well as here.

* **Solar PV is pro rata; the catalogue steps.** SEAI pays 700 €/kWp up to 2 kWp, then 200 €/kWp
  up to 4 kWp, capped at 1,800 €, *pro rata* — a 2.5 kWp array gets 1,500 €. No benefit kind
  expresses a per-unit amount with a cap, so the grant is encoded as four `LUMP_SUM` steps on
  `measure.technical_attributes.peak_power_in_kwp`: 1,800 € from 4 kWp, 1,600 € from 3 to 4 kWp,
  1,400 € from 2 to 3 kWp, 700 € from 1 to 2 kWp, nothing below 1 kWp. The steps are exact at 1,
  2, 3 and 4 kWp and understate everything in between (2.5 kWp yields 1,400 € instead of
  1,500 €). A `TIERED_PER_UNIT` benefit kind would remove the approximation; it is recorded as a
  HiSim todo.
* **The doors grant assumes two doors.** SEAI pays 800 € per door for at most two doors; the
  request carries no door count, so the scheme is a 1,600 € lump sum and its display name says
  `(assumes 2 doors)`. A one-door replacement is overstated by 800 €, which the solver's clamp
  to the eligible cost only partly corrects.

Two further points a reader should know about how the numbers land:

* **The technical assessment's 200 €** is declared against the `PLANNING` cost category only, and
  a lump sum is clamped to its own eligible-cost basis, so the award is whatever planning cost the
  run books for the heat pump, up to 200 €. Where a run books no planning cost, the award is zero.
* **Warmer Homes is a 100 % share** over every envelope and heating class. In reality SEAI's
  surveyor decides which measures are installed, so the catalogue offers the scheme on a wider
  measure list than the programme actually funds.

## What the transcription leaves out

Every line here is a check a reviewer has to make; none of it is in the file.

* MPRN rules: no attic grant and no PV grant where one was already paid for that meter point.
* The whole-surface rule for wall insulation (partial insulation is ineligible).
* The supplementary (second) wall insulation measure rules.
* The windows prerequisite that attic and walls are already "Good"/"Very Good" or that the heat
  loss indicator is 2.3 W/(K·m²) or lower, and the ventilation, fall-restrictor and escape-window
  requirements.
* The heat pump heat-loss requirement for homes built before 2007 (HLI ≤ 2.3 W/(K·m²), a valid
  BER or a technical assessment, the self-declaration form).
* The One Stop Shop **outcome** condition — a post-works BER of B plus either a heat pump or a
  primary-energy uplift of 100 kWh/m²/yr — which the engine cannot evaluate before the works and
  which is therefore **not encoded at all**.
* The exclusion of homes that already received SEAI grants or EEOS energy credits for the same
  measures.
* The different grant amounts for Approved Housing Bodies, and the traditional-homes (pre-1940)
  pilot.
* The landlord tax deduction of up to 10,000 € per property for works between 2023 and 2025,
  which has expired and is deliberately **not** encoded.
* Air-to-air heat pumps (3,500 € unit plus the bonus, 7,500 € in total, no hot water), heating
  controls (700 €), the home energy assessment (350 €) and project management (800–2,000 €) —
  none of them has a HiSim asset class or a RenoVisor measure, so none is encoded.
* De minimis / GBER state aid for landlords.
* The loan's conditions: at least 75 % of the loan on eligible measures, a projected BER uplift
  of at least 20 %, the 5,000–75,000 € band and the three-property cap for landlords. **The loan
  page states no interest rate at all** — it says only that rates are below market and differ by
  provider. The 3 % the scheme encodes is the "starting from as low as 3%" of the *landlord
  supports* page, which is the only rate on any page read.
* Applicant types the context has no member for: registered charities, holiday homes, approved
  housing bodies. They are folded into `OWNER_OCCUPIER` / `LANDLORD` /
  `CONDOMINIUM_ASSOCIATION`, which is what every scheme's actor leaf accepts.
