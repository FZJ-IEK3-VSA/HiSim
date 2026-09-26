# Step 13 — the staged command's parameter contract: one vocabulary, no default country

**Status:** implementation specification, 2026-09-20. **Trigger:** the backend's first real
staged run on image `v20260920-1` (HiSim main `b0c2f043`): `python -m hisim.economics staged
--parameters <the frontend's economics block>` (a) refused every key but `interest_rate`, because
the command reads the file as the engine's own `EconomicParameters` while
`economics-hisim-spec.md` §6 / `economics-backend-spec.md` §2.1 promise the frontend's shape
(`horizon_years`, `perspective_id`, `financing`, `subsidy_mode`); (b) with a parameters file that
names no country, priced an **Irish** house with **German** cost data, because
`EconomicParameters.country` defaults to `"DE"` and the file wins over the stages' stored country
(shared todos H19, C5, B29). The per-run economics on the same image prices IE correctly.

Ground rules as in step 10 §0 (worktree `/home/noah/hisim/HiSim-renovisor`, branch off current
`origin/main`, no commits, class-scope constants, plain complete docstrings, 120 columns;
flake8/mypy/pylint-critical/prospector clean on every touched file; the three CI mypy runs). New
tests `@pytest.mark.base`; run new test files once from `tests/` as CI does. **Invent no numbers.**

---

## 1. Decisions (HiSim-owned spec, settled here)

1. **The input shape of `--parameters` is the document's own `parameters` block**, so a reader can
   feed a document's assumptions back in unchanged. Accepted keys, all optional unless stated:

   | key | meaning | maps to |
   |---|---|---|
   | `horizon_years` (int ≥ 1) | observation period | `EconomicParameters.observation_period_in_years` |
   | `interest_rate` (number > −1) | nominal calculation interest | `interest_rate` |
   | `country` (ISO-2) | price data, subsidy catalogue | `country` — see rule 2 |
   | `price_basis_year` (int or null) | | `price_basis_year` |
   | `perspective_id` (string) | the perspective evaluated, default `brownfield_net` | the `--perspective` choice; the flag, when given, must agree or the run is refused |
   | `subsidy_mode` (`full` \| `none`) | whether the catalogue is applied | `apply_subsidies` (`full` → true, `none` → false); `undetermined` never becomes zero either way |
   | `financing` (`{kind: cash}` or `{kind: loan, financed_share?, nominal_interest_rate?, term_in_years?}`) | cash or one annuity loan per stage | overrides the perspective's `FinancingPlan` (cash → no plan; loan → `FinancingPlan(...)` with the given fields, engine defaults for the rest) |
   | `escalation` (`{general?, investment?, feed_in?, energy?: {<carrier>: rate}}`) | escalation rates; `energy.ELECTRICITY_FEED_IN` is refused (never read) since 2026-09-26 | the corresponding `EconomicParameters` fields |
   | `energy_prices` (`{<carrier>: {working_price_in_euro_per_kwh?, standing_charge_in_euro_per_year?}}`, each a number or `{min, best, max}`; `ELECTRICITY_FEED_IN` working price only) — added 2026-09-26, renovisorissues #52 | the year-1 price terms, working price all-in (carbon included); bounds (0, 2] EUR/kWh, feed-in [0, 1], standing charge [0, 5000] EUR/a | `EconomicParameters.energy_prices` |
   | `origins` | ignored on input (the document's statement of where each echoed rate and price came from) — added 2026-09-26 | — |
   | `subsidy_catalog` (string or null) | ignored on input except as documentation of what produced the document; the catalogue comes from the shipped directory or `--subsidy-catalog` | — |
   | `simulation_year` | ignored on input (it is a fact of the stages) | — |

   Anything else is refused by name with the list of accepted keys. The old engine shape
   (`observation_period_in_years`, `apply_subsidies`, …) is **not** accepted by `staged` any more:
   two vocabularies was the defect. `evaluate | explain | report` keep theirs (they re-price a
   plain run and are not part of the RenoVisor contract).
2. **No default country.** The plan's country is the one the stages were priced under
   (`read_stored_parameters`, already checked for agreement across stages). A `country` in the
   file must equal it, else the run is refused naming both; a file without `country` takes the
   stages'; stages without a stored country and a file without one are refused. `"DE"` never
   appears unless somebody wrote it. The same rule for `price_basis_year` when the stages state
   one (a file value that differs is refused, since the stored inputs were priced at it).
3. **Every parameter rejection is exit 2 with `problems.json`** (one problem per offending key,
   all at once; code `parameters.<key>.invalid` / `parameters.unknown_key` / `parameters.country.mismatch`
   …), like every other refused plan — never a bare traceback or an exit 2 without the file (B29).
4. **The document's `parameters` block gains `subsidy_mode` and `financing`** (the two inputs it
   did not echo), so input and output are the same shape; `economics_result.schema.json` follows.
   `subsidy_catalog` stays as it is.

## 2. Implementation

- `hisim/economics/staged_parameters.py` (new): `StagedParameters` — parse the mapping of §1
  (`from_mapping(raw, stored: Optional[EconomicParameters])` returning the engine parameters, the
  perspective id, the financing override and the list of problems), `to_document_block(...)` used
  by `StagedDocument._parameters_block` so the two directions share one table of keys
  (`ParameterKeys` class with the names above). `SubsidyModeName` and `FinancingKindName` are
  `Enum`s.
- `StagedCli.parameters` uses it; `StagedCli.perspective` reconciles `--perspective` with
  `perspective_id`; the financing override is applied by `replace(perspective, financing=...)`
  before evaluation (check how `Perspective` carries `financing` and how the engine reads it — no
  engine change).
- `_cmd_staged`: a `StagedEvaluationError` raised for parameters carries the problem list and is
  written as `problems.json` (extend the existing exit-2 path; today it writes the file for plan
  problems — verify a parameter error goes the same way).
- `hisim/economics/__main__.py` module docstring and the `staged` `--help` text state the shape;
  `hisim/renovisor/how_to_use.md` and `hisim/economics/README.md` show the example
  `{"horizon_years": 20, "interest_rate": 0.03, "perspective_id": "brownfield_net",
  "financing": {"kind": "cash"}, "subsidy_mode": "full"}` and say the country comes from the
  stages.
- Shared specs (`/home/contract-proposals/economics-hisim-spec.md` §6 and
  `economics-backend-spec.md` §2.1 example, §5): state the accepted keys exactly as §1, that
  `country` is taken from the stages and only checked when given, and correct §5: a change of
  interest rate, horizon, perspective, financing or subsidy mode **is** a new economics job (they
  are all inputs now). One edit each, re-read immediately before.

## 3. Tests (`tests/economics/test_staged_cli.py`, `test_staged_parameters.py`)

- The backend's exact example block (from `economics-backend-spec.md` §2.1, with `perspective_id`
  `brownfield_net`) is accepted and priced; the document's `parameters` block round-trips: feeding
  it back as `--parameters` yields the same parameters.
- An Irish pair of stages with a file that names no country is priced as IE (assert the
  parameters' country and that no DE lookup happened — e.g. the document's `parameters.country`);
  a file naming `DE` over IE stages is refused with `parameters.country.mismatch` in
  `problems.json`, exit 2; stages without stored parameters and a file without country → refused.
- Every unknown key, a bad `subsidy_mode`, a bad `financing.kind`, a horizon of 0 → exit 2 with
  `problems.json` listing each, and no `economics_result.json` written.
- `--perspective` disagreeing with `perspective_id` → refused; agreeing → fine; `financing.kind:
  loan` produces `financing.loans[]` in the document for every buying stage.
- The end-to-end `tests/renovisor/test_staged_economics.py` passes a parameters file in the new
  shape (adjust the fixture) and still runs the vendored mockup pair.

## 4. Bookkeeping

Under H19, C5 and B29 in `/home/contract-proposals/todos.md` (re-read immediately before; one
edit each; other agents' items untouched): a dated HiSim note saying what landed and that the
old engine shape is no longer accepted by `staged`. Done means: the tests of §3 plus
`tests/economics`, `tests/renovisor/test_staged_economics.py`; the three CI mypy runs, pylint
critical-only and prospector clean on every touched file; the final report lists the accepted
keys, every refusal code, and anything left undone.
