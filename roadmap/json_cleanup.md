# Mainline cleanup sweeps for JSON scenario v2

Status: worklist, drafted 2026-08-19 from the spike's escape hatches (see
`roadmap/json_v2_spike_findings.md`). Every entry here removes one workaround from the v2
translation path by fixing the underlying mainline inconsistency instead. Precedents:
the LoadTypes canonicalization (PRs #565/#567), string-valued enums (PR #566) and the
structured component identity (PR #570) were exactly this kind of sweep.

Each sweep is its own PR against main, behind the existing gates (golden parity,
scenario-JSON freshness, quality). Order below is the recommended order.

## 1. Serialization-framework unification: remove `dataclass_wizard` (IN PROGRESS, branch `remove_jsonwizard`)

**Hack it deletes:** the v2 codec's `normalize_keys` camelCase shim (spike finding 8)
exists because configs mix two serialization frameworks: a plain-`JSONWizard` config dumps
camelCase while a `@dataclass_json` one dumps snake_case.

**Finding:** `JSONWizard` is inherited in ~30 places but load-bearing in almost none:

- `ConfigBase` — real use, but only for the few config classes missing the
  `@dataclass_json` decorator (64 of 68 component modules already have it, and every
  decorated class shadows the wizard methods anyway). No committed scenario JSON contains
  a camelCase config key, so the switch is format-neutral for the 23 shipped files.
- `SimulationParameters` — `to_dict()` (camelCase) is called in exactly one place,
  `get_filtered_simulation_parameters`, which immediately `humps.decamelize`s it. A
  direct snake_case export deletes that humps usage too. The `.simulation.json`
  read path (`SimulationParameters(**data)`) never used the wizard.
- `KpiEntry` — real use with **external format exposure**: `to_dict()` emits camelCase
  keys (`nameOfSourceComponent`) that land in the webtool KPI JSON. Preserve the wire
  format byte-for-byte via `@dataclass_json(letter_case=LetterCase.CAMEL)`.
- Vestigial (no serialization callers at all): `SystemSetupConfigBase`, `KpiGenerator`,
  `EconomicParameters`, 3× `CalculationRequest` (their `get_cache_key` is plain string
  concatenation), and ~20 unit marker classes in `hisim/units.py`.

**Steps:** decorate the undecorated `ConfigBase` subclasses; drop the `JSONWizard` base
everywhere; rewrite `get_filtered_simulation_parameters` without wizard+humps; pin the
KpiEntry wire format; update `tests/test_component.py`'s camelCase assertions, the
`weather.py`/`json_executor.py` comments and `scripts/check_config_attrs.py`'s
dynamic-base notes; remove `dataclass_wizard==0.34.0` from `requirements.txt`.
Afterwards, on `json_v2`: delete `ScenarioConfigCodec.normalize_keys`.

**Findings from execution:** every real config class was already decorated — the
undecorated modules carry no configs at all, so the base was fully vestigial on the config
path and the shipped scenario JSONs contain zero camelCase keys (format-neutral switch).
The KpiEntry tag needs a field-level encoder because its dicts are `json.dump`ed raw.
The one non-trivial consequence: the wizard's missing type stubs had made the whole
ConfigBase hierarchy `Any`-based for mypy, structurally masking ~300 latent typing issues.
This sweep restores that historical checking level *explicitly* (a checking-only
`__getattr__`/`__setattr__` and serialization-API stubs on `ConfigBase`, `follow_imports =
skip` for `dataclasses_json`, `override` in `disable_error_code`) — see item 6 for the
follow-up that lifts the blindness for real.

## 6. Mypy visibility for config classes (follow-up to sweep 1)

Removing the wizard's `Any` base revealed ~300 latent typing findings that sweep 1
deliberately re-masked to stay reviewable: LSP-violating `get_cost_capex(config:
SpecificConfig)` overrides (~75), subclass-field reads/writes on `ConfigBase`-typed slots
(~280), and a handful of genuine type lies (`my_module_config` holding a dict, the
`List[int]` annotation on `post_processing_options` that actually holds enum members).
The real fix is a generic `Component[TConfig]` so `self.config` is precisely typed per
component, then: delete the checking-only escape hatches on `ConfigBase`, re-enable the
`override` error code, and drop `follow_imports = skip` for `dataclasses_json` (restoring
the injected-API stubs problem — solve via a small typing shim or upstream stubs).

## 2. Purge or repair the unbuildable components

The v2 contract test's `CONSTRUCTION_SKIPS` table (`tests/test_scenario_v2_contract.py`,
branch `json_v2`) lists 14 components that cannot be built from their own default config.
**None of them is used by any system setup** (checked 2026-08-19).

- Zombies — default connections import modules that were already deleted; these cannot
  have worked since the modular-household removal. Delete:
  `controller_l1_building_heating.L1BuildingHeatController`,
  `controller_l1_heatpump.L1HeatPumpController`,
  `controller_l1_generic_runtime.L1GenericRuntimeController`.
- Defective — outputs missing mandatory descriptions, or default configs naming database
  entries that do not exist. Delete or fix: `generic_battery.GenericBattery`,
  `generic_battery.BatteryController`, `generic_ev_charger.EVCharger`,
  `generic_ev_charger.EVChargerController`, `generic_ev_charger.Vehicle`,
  `generic_ev_charger.VehiclePure`.
- Legitimately data-dependent (keep, with skip): `generic_smart_device.SmartDevice`
  (UTSP flexibility reports), `VehiclePure` if kept (LPG export).

**Payoff:** "every component in `hisim/components` builds from JSON" becomes true by
construction instead of by exception table.

## 3. Default-config factory canonicalization

**Hack it deletes:** `DEFAULT_FACTORY_PATTERN = re.compile(r"^get_.*default", re.IGNORECASE)`
plus required-parameter-count ranking in the v2 executor (spike finding 10). There are 64
factory spellings beyond plain `get_default_config` (`get_default_config_ems`,
`get_default_generic_advanced_hp_lib`, ...).

**Sweep:** every `ConfigBase` subclass gets exactly one canonical, fully-defaulted
`get_default_config()` classmethod; variant factories stay as extras but the canonical one
is the contract. Fold in the `DEFAULT_CONFIG_ARGUMENTS` hack: the two heat-pump-controller
factories with an `Any`-annotated `heat_distribution_system_type` parameter get the real
enum annotation with a default.

## 4. Constructor-signature uniformity

`SimpleController` and `ExtendedController` take a positional `name` argument (doubly
anachronistic post-ComponentID — the name lives in `config.component_id`);
`SmartController` needs a `controller_type_to_field_names` dict that is not part of its
config. All three break the `cls(config=..., my_simulation_parameters=...)` contract that
v2's default `build_from_scenario` assumes. Normalize the three signatures; this removes
the pressure on the `config_class_overrides` escape hatch.

## 5. EMS KPI port-name parsing (spike finding 26)

`get_component_kpi_entries` matches class-name substrings and `"SH"`/`"DHW"` fragments
inside `output.field_name` (`controller_l2_energy_management_system.py:1309`; verified
still true after the identity merge). It only works because scenario ids happen to equal
class names today. Migrate it onto v1's `my_component_inputs` bookkeeping **before** the
C-track EMS cutover, so the cutover's golden diff stays purely mechanical.

## Not worth pre-sweeping

- `source_weight` config fields and the legacy `DynamicConnection*` dataclasses — v1
  setups still consume them; deletion is already scheduled for the v2 E-phase.
- Optional-dependency import failures (`import_failures` in the contract test) — an
  environment property, not a contract violation.
