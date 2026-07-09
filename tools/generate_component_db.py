#!/usr/bin/env python3
"""Generate component_db.json, enum_db.json, and catalog_db.json for the HiSim scenario editor.

Run from the repository root:
    python tools/generate_component_db.py

Outputs:
    system_setups/editor/public/data/component_db.json
    system_setups/editor/public/data/enum_db.json
    system_setups/editor/public/data/catalog_db.json

Exits non-zero if any component fails to introspect, so CI can catch regressions.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import importlib
import inspect
import json
import os
import pkgutil
import sys
from typing import Any, Dict, List, Optional, Union, get_type_hints

# ---------------------------------------------------------------------------
# Path setup — repo root is one level up from tools/
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from hisim import loadtypes as lt  # noqa: E402
from hisim.component import Component, ConfigBase  # noqa: E402
from hisim.dynamic_component import DynamicComponent  # noqa: E402
from hisim.postprocessingoptions import PostProcessingOptions  # noqa: E402
from hisim.simulationparameters import SimulationParameters  # noqa: E402

OUTPUT_DIR = os.path.join(REPO_ROOT, "system_setups", "editor", "public", "data")
COMPONENTS_PKG = "hisim.components"
COMPONENTS_PATH = os.path.join(REPO_ROOT, "hisim", "components")

# ---------------------------------------------------------------------------
# Category mapping  (module segment → UI category label)
# ---------------------------------------------------------------------------

_CATEGORY_MAP: Dict[str, str] = {
    "weather": "Weather",
    "weather_data_import": "Weather",
    "building": "Building",
    "generic_pv_system": "PV",
    "generic_windturbine": "PV",
    "generic_heat_pump": "Heating",
    "generic_heat_pump_modular": "Heating",
    "advanced_heat_pump_hplib": "Heating",
    "more_advanced_heat_pump_hplib": "Heating",
    "generic_boiler": "Heating",
    "generic_heat_source": "Heating",
    "simple_heat_source": "Heating",
    "heat_distribution_system": "Heating",
    "generic_hot_water_storage_modular": "Heating",
    "generic_heat_water_storage": "Heating",
    "simple_water_storage": "Heating",
    "generic_electric_heating": "Heating",
    "idealized_electric_heater": "Heating",
    "generic_district_heating": "Heating",
    "solar_thermal_system": "Heating",
    "air_conditioner": "Heating",
    "generic_chp": "CHP",
    "advanced_fuel_cell": "CHP",
    "advanced_fuel_cell_controller": "CHP",
    "generic_fuel_cell": "CHP",
    "generic_rsoc": "CHP",
    "dual_circuit_system": "CHP",
    "electricity_meter": "Metering",
    "gas_meter": "Metering",
    "fuel_meter": "Metering",
    "heating_meter": "Metering",
    "advanced_battery_bslib": "Storage",
    "generic_battery": "Storage",
    "generic_hydrogen_storage": "Storage",
    "generic_electrolyzer": "Electrolyzer",
    "generic_electrolyzer_and_h2_storage": "Electrolyzer",
    "generic_electrolyzer_h2": "Electrolyzer",
    "generic_car": "EV",
    "advanced_ev_battery_bslib": "EV",
    "generic_ev_charger": "EV",
    "loadprofilegenerator_utsp_connector": "Occupancy",
    "generic_smart_device": "SmartDevice",
    "csvloader": "Utility",
    "random_numbers": "Utility",
    "sumbuilder": "Utility",
    "transformer_rectifier": "Utility",
    "generic_price_signal": "Utility",
    "configuration": "Utility",
    "example_component": "Example",
    "example_storage": "Example",
    "example_template": "Example",
    "example_transformer": "Example",
}


def _module_category(module_name: str) -> str:
    segment = module_name.split(".")[-1]
    if segment in _CATEGORY_MAP:
        return _CATEGORY_MAP[segment]
    if segment.startswith(("controller_", "night_setback")):
        return "Control"
    return segment.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Minimal SimulationParameters for introspection
# ---------------------------------------------------------------------------

def _make_sim_params() -> SimulationParameters:
    return SimulationParameters(
        start_date=datetime.datetime(2021, 1, 1),
        end_date=datetime.datetime(2021, 1, 2),
        seconds_per_timestep=3600,
        result_directory="",
        log_connections=False,
    )


# ---------------------------------------------------------------------------
# Type annotation helpers
# ---------------------------------------------------------------------------

def _module_globals(obj: type) -> Dict[str, Any]:
    module = sys.modules.get(obj.__module__)
    return vars(module) if module else {}


def _resolve_hints(obj: type, attr: str = "") -> Dict[str, Any]:
    """Return get_type_hints result, with manual eval fallback for string annotations."""
    globalns = _module_globals(obj)
    target = getattr(obj, attr) if attr else obj
    try:
        return get_type_hints(target, globalns=globalns)
    except Exception:
        raw = getattr(target, "__annotations__", {})
        resolved: Dict[str, Any] = {}
        for name, hint in raw.items():
            if isinstance(hint, str):
                try:
                    resolved[name] = eval(hint, globalns)  # noqa: S307
                except Exception:
                    resolved[name] = hint
            else:
                resolved[name] = hint
        return resolved


def _type_to_str(hint: Any) -> str:
    if hint is None or hint is type(None):
        return "None"
    if isinstance(hint, str):
        return hint
    origin = getattr(hint, "__origin__", None)
    args = getattr(hint, "__args__", None) or ()
    if origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return f"Optional[{_type_to_str(non_none[0])}]"
        return "Union[" + ", ".join(_type_to_str(a) for a in args) + "]"
    if origin is list:
        return f"List[{_type_to_str(args[0])}]" if args else "List"
    if origin is dict:
        return f"Dict[{_type_to_str(args[0])}, {_type_to_str(args[1])}]" if len(args) == 2 else "Dict"
    if hasattr(hint, "__name__"):
        return hint.__name__
    return str(hint)


def _is_optional(hint: Any) -> bool:
    origin = getattr(hint, "__origin__", None)
    args = getattr(hint, "__args__", None) or ()
    return origin is Union and type(None) in args


def _unwrap_optional(hint: Any) -> Any:
    origin = getattr(hint, "__origin__", None)
    args = getattr(hint, "__args__", None) or ()
    if origin is Union:
        non_none = [a for a in args if a is not type(None)]
        return non_none[0] if len(non_none) == 1 else hint
    return hint


def _enum_class_name(hint: Any) -> Optional[str]:
    inner = _unwrap_optional(hint)
    try:
        if isinstance(inner, type) and issubclass(inner, enum.Enum):
            return inner.__name__
    except TypeError:
        pass
    return None


def _serialize_value(val: Any) -> Any:
    """Recursively convert a value to a JSON-serialisable form."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float, str)):
        return val
    if isinstance(val, enum.Enum):
        return val.value
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {str(k): _serialize_value(v) for k, v in val.items()}
    if dataclasses.is_dataclass(val) and not isinstance(val, type):
        return {f.name: _serialize_value(getattr(val, f.name)) for f in dataclasses.fields(val)}
    return str(val)


# ---------------------------------------------------------------------------
# Default value synthesis  (used when no-arg factory method is unavailable)
# ---------------------------------------------------------------------------

def _synthetic_default(hint: Any, field_name: str = "") -> Any:
    """Return a plausible minimal value for *hint* — enough to construct the config."""
    if hint is None:
        return None
    if _is_optional(hint):
        return None
    origin = getattr(hint, "__origin__", None)
    if origin is not None:
        return None  # generic container — use None and hope the component tolerates it
    if not isinstance(hint, type):
        # Could not resolve to a concrete type (e.g. still a string); fall back
        if field_name == "building_name":
            return "BUI1"
        if field_name == "name":
            return "default"
        return ""
    if issubclass(hint, bool):
        return False
    if issubclass(hint, int):
        return 0
    if issubclass(hint, float):
        return 0.0
    if issubclass(hint, str):
        if field_name == "building_name":
            return "BUI1"
        if field_name == "name":
            return "default"
        if field_name == "result_dir_path":
            return "<<utils.HISIMPATH['utsp_results']>>"
        return ""
    if issubclass(hint, enum.Enum):
        members = [m for m in hint if isinstance(m.value, str) or not isinstance(m.value, list)]
        return members[0] if members else None
    return None


# ---------------------------------------------------------------------------
# Config class + default config discovery
# ---------------------------------------------------------------------------

def _find_config_class(comp_class: type) -> Optional[type]:
    """Find the ConfigBase subclass declared in comp_class.__init__'s type hints."""
    hints = _resolve_hints(comp_class, "__init__")
    for param, hint in hints.items():
        if param == "return":
            continue
        try:
            # Require a proper subclass, not ConfigBase itself — bare ConfigBase
            # annotations (e.g. my_config: cp.ConfigBase) can't be meaningfully
            # introspected for per-component defaults.
            if isinstance(hint, type) and issubclass(hint, ConfigBase) and hint is not ConfigBase:
                return hint
        except TypeError:
            pass
    return None


def _find_default_config(config_class: type) -> Optional[Any]:
    """Return a usable instance of *config_class* using the best available strategy."""

    # Collect get_default_* factory methods. Accept both classmethods
    # (inspect.ismethod → bound method) and staticmethods (inspect.isfunction →
    # plain function); many configs declare get_default_config as a staticmethod.
    def _is_factory(obj: Any) -> bool:
        return inspect.ismethod(obj) or inspect.isfunction(obj)

    candidates = [
        (name, getattr(config_class, name))
        for name in dir(config_class)
        if "default" in name.lower() and _is_factory(getattr(config_class, name, None))
    ]

    for _name, method in candidates:
        sig = inspect.signature(method)
        params = [
            p for p in sig.parameters.values()
            if p.name not in ("cls", "self") and p.default is inspect.Parameter.empty
        ]

        if not params:
            # No required positional args — try directly
            for kwargs in ({}, {"building_name": "BUI1"}):
                try:
                    result = method(**kwargs)
                    if isinstance(result, config_class):
                        return result
                except Exception:
                    pass
        else:
            # Synthesise values for each required arg
            hints = _resolve_hints(config_class)
            synthetic: Dict[str, Any] = {}
            for p in params:
                hint = hints.get(p.name)
                if hint is None and p.annotation is not inspect.Parameter.empty:
                    try:
                        hint = eval(str(p.annotation), _module_globals(config_class))  # noqa: S307
                    except Exception:
                        hint = None
                synthetic[p.name] = _synthetic_default(hint, p.name)

            for kwargs in (synthetic, {**synthetic, "building_name": "BUI1"}):
                try:
                    result = method(**kwargs)
                    if isinstance(result, config_class):
                        return result
                except Exception:
                    pass

    # Last resort: construct the dataclass directly with synthetic field values
    if dataclasses.is_dataclass(config_class):
        hints = _resolve_hints(config_class)
        kwargs = {}
        for f in dataclasses.fields(config_class):
            if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                continue
            hint = hints.get(f.name)
            kwargs[f.name] = _synthetic_default(hint, f.name)
        try:
            result = config_class(**kwargs)
            if isinstance(result, config_class):
                return result
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# Config field metadata
# ---------------------------------------------------------------------------

def _get_config_fields(config_class: type, default_config: Any) -> List[Dict]:
    if not dataclasses.is_dataclass(config_class):
        return []
    hints = _resolve_hints(config_class)
    fields = []
    for f in dataclasses.fields(config_class):
        hint = hints.get(f.name)
        fields.append({
            "name": f.name,
            "type": _type_to_str(hint) if hint is not None else (
                f.type if isinstance(f.type, str) else str(f.type)
            ),
            "is_optional": _is_optional(hint) if hint is not None else False,
            "enum_class": _enum_class_name(hint) if hint is not None else None,
            "default": _serialize_value(getattr(default_config, f.name, None)),
        })
    return fields


# ---------------------------------------------------------------------------
# Main introspection
# ---------------------------------------------------------------------------

def _introspect(comp_class: type, sim_params: SimulationParameters) -> Dict:
    """Instantiate *comp_class* with a minimal config and extract all editor metadata."""
    config_class = _find_config_class(comp_class)
    if config_class is None:
        raise RuntimeError("No ConfigBase subclass found in __init__ type hints")

    default_config = _find_default_config(config_class)
    if default_config is None:
        raise RuntimeError(f"Could not obtain a default config from {config_class.__name__}")

    comp = comp_class(my_simulation_parameters=sim_params, config=default_config)

    input_ports = [
        {
            "field_name": inp.field_name,
            "load_type": inp.loadtype.value if isinstance(inp.loadtype, enum.Enum) else str(inp.loadtype),
            "unit": inp.unit.value if isinstance(inp.unit, enum.Enum) else str(inp.unit),
            "mandatory": inp.is_mandatory,
        }
        for inp in comp.inputs
    ]

    output_ports = [
        {
            "field_name": out.field_name,
            "load_type": out.load_type.value if isinstance(out.load_type, enum.Enum) else str(out.load_type),
            "unit": out.unit.value if isinstance(out.unit, enum.Enum) else str(out.unit),
            "postprocessing_flag": _serialize_value(out.postprocessing_flag),
            "sankey_flow_direction": out.sankey_flow_direction,
            "output_description": out.output_description,
        }
        for out in comp.outputs
    ]

    default_connections = {
        src_cls: [
            {
                "target_input_name": c.target_input_name,
                "source_output_name": c.source_output_name,
            }
            for c in conns
        ]
        for src_cls, conns in comp.default_connections.items()
    }

    # First line of the class docstring as human-readable display name
    doc = inspect.getdoc(comp_class) or ""
    display_name = doc.split("\n")[0].strip() or comp_class.__name__

    return {
        "component_full_classname": f"{comp_class.__module__}.{comp_class.__name__}",
        "config_full_classname": f"{config_class.__module__}.{config_class.__name__}",
        "display_name": display_name,
        "category": _module_category(comp_class.__module__),
        "is_dynamic": issubclass(comp_class, DynamicComponent),
        "default_config": _serialize_value(default_config),
        "config_fields": _get_config_fields(config_class, default_config),
        "input_ports": input_ports,
        "output_ports": output_ports,
        "default_connections": default_connections,
    }


# ---------------------------------------------------------------------------
# Module and class discovery
# ---------------------------------------------------------------------------

def _collect_component_classes() -> List[type]:
    """Import every hisim.components.* module and return all Component subclasses."""
    seen: set = set()
    classes: List[type] = []

    for _finder, module_name, _ispkg in pkgutil.iter_modules([COMPONENTS_PATH]):
        full_name = f"{COMPONENTS_PKG}.{module_name}"
        try:
            module = importlib.import_module(full_name)
        except Exception as exc:
            print(f"  [SKIP ] Cannot import {full_name}: {exc}", file=sys.stderr)
            continue

        for _attr, obj in inspect.getmembers(module, inspect.isclass):
            if (
                obj.__module__ == full_name
                and issubclass(obj, Component)
                and obj is not Component
                and obj is not DynamicComponent
                and id(obj) not in seen
            ):
                seen.add(id(obj))
                classes.append(obj)

    # Drop abstract base components: a collected component that other collected
    # components subclass *and* that declares no concrete config (its __init__ takes a
    # bare cp.ConfigBase) is an abstract base, not a standalone editor node — e.g.
    # SimpleWaterStorage, the shared base of SimpleHotWaterStorage/SimpleDHWStorage.
    concrete: List[type] = []
    for cls in classes:
        is_base_of_another = any(other is not cls and issubclass(other, cls) for other in classes)
        if is_base_of_another and _find_config_class(cls) is None:
            print(f"  [SKIP ] {cls.__module__}.{cls.__name__}: abstract base component (no concrete config)",
                  file=sys.stderr)
            continue
        concrete.append(cls)

    return concrete


# ---------------------------------------------------------------------------
# Enum registry
# ---------------------------------------------------------------------------

def _build_enum_db() -> Dict:
    def str_values(e: type) -> List[str]:
        # ComponentType(str, Enum) coerces list values (e.g. HEATERS) to their
        # repr string — exclude those by rejecting values that start with "[".
        return [m.value for m in e if isinstance(m.value, str) and not m.value.startswith("[")]

    return {
        "load_types": str_values(lt.LoadTypes),
        "units": str_values(lt.Units),
        "component_types": str_values(lt.ComponentType),
        "in_and_output_types": str_values(lt.InandOutputType),
        "building_codes": str_values(lt.BuildingCodes),
        "locations": str_values(lt.Locations),
        "post_processing_options": [
            {"name": m.name, "value": m.value} for m in PostProcessingOptions
        ],
    }


# ---------------------------------------------------------------------------
# Domain catalogs (weather datasets, heat pump models, PV modules/inverters)
# ---------------------------------------------------------------------------

def _build_catalog_db(now: str) -> Dict:
    """Build catalog_db.json with domain-specific data for the editor's field dropdowns."""
    import glob as _glob

    # ── Weather datasets ───────────────────────────────────────────────────
    weather_root = os.path.join(REPO_ROOT, "hisim", "inputs", "weather")
    inputs_root = os.path.join(REPO_ROOT, "hisim", "inputs")
    weather_datasets = []
    for dat in sorted(_glob.glob(os.path.join(weather_root, "**", "*.dat"), recursive=True)):
        dat_fwd = dat.replace(os.sep, "/")
        if "/data_raw/" in dat_fwd:
            continue  # skip unprocessed raw files
        no_ext = os.path.splitext(dat)[0]
        rel = os.path.relpath(no_ext, inputs_root).replace(os.sep, "/")
        name = os.path.splitext(os.path.basename(dat))[0]
        if "/NSRDB/" in dat_fwd:
            label = f"{name} (NSRDB)"
        elif "1995-2012" in dat_fwd:
            label = f"{name.replace('_', ' ').title()} (TRY 1995–2012)"
        elif "2015-2045" in dat_fwd:
            label = f"{name.replace('_', ' ').title()} (TRY 2015–2045)"
        else:
            label = name.replace("_", " ").title()
        weather_datasets.append({"label": label, "path": f"<<utils.get_input_directory()>>/{rel}"})

    # ── Heat pump models ───────────────────────────────────────────────────
    heat_pump_models: List[Dict] = []
    try:
        from hisim import utils as _utils  # noqa: PLC0415
        hp_db = _utils.load_smart_appliance("Heat Pump")
        for entry in hp_db:
            heat_pump_models.append({"manufacturer": entry["Manufacturer"], "name": entry["Name"]})
    except Exception as exc:
        print(f"  [WARN] Could not build heat pump catalog: {exc}", file=sys.stderr)

    # ── PV modules & inverters (from local CSV files) ──────────────────────
    pv_modules: Dict[str, List[str]] = {}
    pv_inverters: Dict[str, List[str]] = {}
    try:
        import pandas as _pd  # noqa: PLC0415
        from hisim import utils as _utils  # noqa: PLC0415, F811
        pv_paths = _utils.HISIMPATH.get("photovoltaic", {})

        # Sandia modules → database enum value 1 (525 entries)
        sandia_path = pv_paths.get("sandia_modules_new")
        if sandia_path and os.path.exists(sandia_path):
            df = _pd.read_csv(sandia_path)
            if "Name" in df.columns:
                pv_modules["1"] = sorted(df["Name"].dropna().unique().tolist())

        # CEC modules → database enum value 3 (20k+ entries, used via datalist)
        cec_mod_path = pv_paths.get("cec_modules")
        if cec_mod_path and os.path.exists(cec_mod_path):
            df = _pd.read_csv(cec_mod_path)
            if "Name" in df.columns:
                pv_modules["3"] = sorted(df["Name"].dropna().unique().tolist())

        # CEC inverters → database enum value 4
        cec_inv_path = pv_paths.get("cec_inverters")
        if cec_inv_path and os.path.exists(cec_inv_path):
            df = _pd.read_csv(cec_inv_path)
            if "Name" in df.columns:
                pv_inverters["4"] = sorted(df["Name"].dropna().unique().tolist())
    except Exception as exc:
        print(f"  [WARN] Could not build PV catalog: {exc}", file=sys.stderr)

    # ── Predefined LPG load profiles ──────────────────────────────────────
    predefined_load_profiles: List[str] = []
    try:
        from hisim import utils as _utils  # noqa: PLC0415, F811
        occupancy = _utils.HISIMPATH.get("occupancy", {})
        predefined_load_profiles = sorted(occupancy.keys())
    except Exception as exc:
        print(f"  [WARN] Could not build predefined load profile catalog: {exc}", file=sys.stderr)

    # ── Config field overrides for new nodes ──────────────────────────────
    # Replaces absolute OS-specific paths in default_config with HiSim path placeholders.
    # Applied in the editor when the user drops a new component onto the canvas.
    config_overrides: Dict[str, Dict] = {
        "hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig": {
            "result_dir_path": "<<utils.HISIMPATH['utsp_results']>>",
        },
    }

    return {
        "generated_at": now,
        "weather_datasets": weather_datasets,
        "heat_pump_models": heat_pump_models,
        "pv_modules": pv_modules,
        "pv_inverters": pv_inverters,
        "predefined_load_profiles": predefined_load_profiles,
        "config_overrides": config_overrides,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    sim_params = _make_sim_params()

    print("Collecting Component subclasses ...")
    classes = _collect_component_classes()
    print(f"Found {len(classes)} classes.\n")

    components: List[Dict] = []
    failures: List[Dict] = []

    for cls in classes:
        label = f"{cls.__module__}.{cls.__name__}"
        try:
            data = _introspect(cls, sim_params)
            components.append(data)
            print(f"  [OK  ] {label}")
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            failures.append({"class": label, "error": msg})
            print(f"  [FAIL] {label}: {msg}", file=sys.stderr)

    components.sort(key=lambda c: (c["category"], c["display_name"]))

    now = datetime.datetime.now().isoformat()
    component_db: Dict = {
        "generated_at": now,
        "components": components,
        "failures": failures,
    }
    enum_db = _build_enum_db()
    enum_db["generated_at"] = now
    catalog_db = _build_catalog_db(now)

    comp_path = os.path.join(OUTPUT_DIR, "component_db.json")
    enum_path = os.path.join(OUTPUT_DIR, "enum_db.json")
    catalog_path = os.path.join(OUTPUT_DIR, "catalog_db.json")

    with open(comp_path, "w", encoding="utf-8") as fh:
        json.dump(component_db, fh, indent=2, ensure_ascii=False)

    with open(enum_path, "w", encoding="utf-8") as fh:
        json.dump(enum_db, fh, indent=2, ensure_ascii=False)

    with open(catalog_path, "w", encoding="utf-8") as fh:
        json.dump(catalog_db, fh, indent=2, ensure_ascii=False)

    ok_count = len(components)
    fail_count = len(failures)
    print(f"\nWrote {comp_path}")
    print(f"      {ok_count} components OK, {fail_count} failed")
    print(f"Wrote {enum_path}")
    print(f"Wrote {catalog_path}")
    print(f"      {len(catalog_db['weather_datasets'])} weather datasets, "
          f"{len(catalog_db['heat_pump_models'])} heat pump models, "
          f"{sum(len(v) for v in catalog_db['pv_modules'].values())} PV modules, "
          f"{sum(len(v) for v in catalog_db['pv_inverters'].values())} PV inverters")

    if failures:
        print(f"\n{fail_count} component(s) failed — fix or investigate:")
        for f in failures:
            print(f"  - {f['class']}: {f['error']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
