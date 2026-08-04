# Lifecycle Economics v1 Runbook

This runbook is the step-by-step guide for the reduced-scope v1 lifecycle-cost engine. It is meant to help users understand what the engine does, what inputs it needs, and how to execute it without reading the full specification.

For product scope, read the package overview in [README.md](README.md). For the full long-term design and non-v1 concepts, see [../../cost_spec.md](../../cost_spec.md).

---

## 1. What this v1 engine is for

The v1 lifecycle-cost engine supports the following first-release questions:

- What is the total discounted cost or benefit of a system over the next 20 years?
- What is the annualized equivalent cost (EAC)?
- How do replacement, maintenance, residual value and subsidy effects change the result?
- What is the low/base/high uncertainty band for the key result?
- How does one configuration compare to another?

The v1 scope intentionally does not model:

- landlord/tenant/owner allocation logic
- separate financing-heavy debt analysis
- sophisticated multi-actor subsidy stacking
- macroeconomic versus financial perspective splits
- full source-provenance reporting for every intermediate value

---

## 2. What inputs are required

At minimum, the evaluator needs:

- a simulation result directory with the relevant factual and energy-flow inputs
- a set of economic parameters
- optionally a subsidy catalog for subsidy-aware calculations

The core economic inputs are declared in `EconomicParameters` and are defined in [parameters.py](parameters.py).

Typical required values:

- `observation_period_in_years`: e.g. 20
- `interest_rate`: e.g. 0.03
- `inflation_rate`: e.g. 0.02
- `general_price_escalation_rate`: e.g. 0.02
- `investment_price_escalation_rate`: e.g. 0.02
- `country`: e.g. "DE"
- `price_basis_year`: e.g. 2024

The engine then projects annual cash flows, applies discounting, and returns a result band for low/base/high scenarios.

---

## 3. Quick start: execute the CLI

The CLI entry point is [__main__.py](__main__.py).

### 3.1 Re-evaluate stored inputs

```bash
python -m hisim.economics evaluate <results_dir>
```

This reads an existing result bundle and re-prices it under the current economic assumptions.

### 3.2 Use custom economic parameters

```bash
python -m hisim.economics evaluate <results_dir> --parameters my_economic_params.json
```

The JSON file should contain a valid `EconomicParameters` dict, for example:

```json
{
  "observation_period_in_years": 20,
  "interest_rate": 0.03,
  "inflation_rate": 0.02,
  "general_price_escalation_rate": 0.02,
  "investment_price_escalation_rate": 0.02,
  "country": "DE",
  "price_basis_year": 2024,
  "co2_price_scenario": "none"
}
```

### 3.3 Write a human-readable report

```bash
python -m hisim.economics report <results_dir>
```

This writes:

- `cost_summary.md`
- `lifecycle_report.html`
- CSV and PNG outputs for the report

### 3.4 Explain a result value

```bash
python -m hisim.economics explain <results_dir> --value "greenfield_net/equivalent_annual_cost_in_euro"
```

This traces a selected result back through the economic input chain.

### 3.5 Validate data integrity

```bash
python -m hisim.economics validate
```

Use this after changing data files or subsidy catalog entries.

---

## 4. Python usage

For direct analysis in Python, construct `EconomicParameters` and pass it to `EconomicEvaluator`.

```python
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs
from hisim.economics.database import CostDatabase
from hisim.economics.parameters import EconomicParameters

params = EconomicParameters(
    observation_period_in_years=20,
    interest_rate=0.03,
    inflation_rate=0.02,
    general_price_escalation_rate=0.02,
    investment_price_escalation_rate=0.02,
    country="DE",
    price_basis_year=2024,
)

database = CostDatabase()
evaluator = EconomicEvaluator(database, params)

inputs = EvaluationInputs(
    simulation_year=2024,
    simulated_period_fraction=1.0,
)

result = evaluator.evaluate(inputs, perspective)
print(result.total_npv_in_euro)
print(result.equivalent_annual_cost_in_euro)
```

The result object contains uncertainty-band values, usually in the format low/base/high or minimum/average/maximum depending on the object.

---

## 5. Understanding the outputs

The v1 engine outputs a set of lifecycle economics values that are intended to be easy to understand:

- `total_npv_in_euro`: discounted total cost or benefit over the observation period
- `equivalent_annual_cost_in_euro`: annualized headline KPI
- category breakdowns such as investment, replacement, residual value, energy cost, maintenance, subsidy
- uncertainty band values for the headline result

The uncertainty model is implemented in [uncertainty.py](uncertainty.py). All major monetary outputs are propagated as a low/base/high triplet, so a result is not just a single point estimate.

---

## 6. v1 interpretation guide

### 6.1 Real vs nominal rates

For first release, the engine exposes both:

- nominal `interest_rate`
- inflation `inflation_rate`

The derived real rate is calculated according to:

```python
real_rate = (1 + nominal_rate) / (1 + inflation_rate) - 1.0
```

This is the standard conversion used for real discounting.

### 6.2 Cash-flow sign conventions

The engine keeps the direction of money explicit:

- investments and costs are typically positive cash outflows
- revenues and feed-in are negative cash flows from the cost perspective
- residual value is treated as a terminal benefit and reduces lifecycle cost

### 6.3 Uncertainty bands

For v1, uncertainty is intentionally simple and transparent:

- low / base / high world
- propagated slot-wise through the lifecycle timeline
- useful for comparison, sensitivity and reporting

Do not treat the result band as a statistical distribution. It is an economic envelope for the key cost result.

---

## 7. Recommended workflow for a user

### Option A: quick check in tests

```bash
python -m pytest tests/test_economics_engine.py -q
```

This is the fast validation path for the v1 lifecycle model.

### Option B: evaluate one project

1. Prepare a results directory from the simulation
2. Create the economic parameter JSON
3. Run `python -m hisim.economics evaluate ...`
4. Run `python -m hisim.economics report ...`
5. Review the generated markdown and HTML report

### Option C: compare two variants

Use the report command with a comparison target:

```bash
python -m hisim.economics report <results_dir> --compare <reference_results_dir>
```

This helps compare alternative system setups using the same v1 cash-flow model.

---

## 8. Common issues and fixes

### The script fails with a missing argument

Check the CLI usage in [__main__.py](__main__.py). The subcommands are strict and the value path or result directory must be supplied.

### Inflation or discounting looks wrong

Verify the parameter values in `EconomicParameters` and confirm that:

- `interest_rate` is nominal
- `inflation_rate` is the price inflation assumption
- the real rate is derived from the conversion above

### The outputs appear too advanced or complex

That is expected for the full engine, but for the v1 product the user-facing interpretation should focus on:

- NPV
- EAC
- replacement and residual effects
- subsidy effects
- uncertainty band

If you need the broader advanced actor, policy and financing architecture, use the spec and future-work documents as background reading, not as the v1 user guide.

---

## 9. Where to read more

- [README.md](README.md) — v1 package overview
- [parameters.py](parameters.py) — parameter definitions and public API
- [__main__.py](__main__.py) — exact script interface
- [tests/test_economics_engine.py](tests/test_economics_engine.py) — v1 test expectations
- [../../cost_spec.md](../../cost_spec.md) — full design context, not the v1 user guide

---

## 10. Summary

For a first user-facing lifecycle model, the engine should be understood as a simple but transparent discounted cash-flow tool:

- discount rate
- inflation
- NPV
- EAC
- replacements
- maintenance
- residual value
- subsidies
- uncertainty band

Everything beyond that belongs to a later stage of the economics package and should not be required for the first release.
