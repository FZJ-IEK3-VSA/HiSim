"""Runner for Python-based HiSim system setups (``system_setups/*.py``).

Executes ``hisim_main.main(path, my_simulation_parameters)`` — the same entry point
the system-setup tests and the golden-reference runner use. The harness supplies the
:class:`SimulationParameters` (duration/timestep from the payload) with
``result_directory`` forced to the job's staging dir, so every setup runs with
identical, comparable parameters regardless of what it would build for itself.

Payload contract::

    {
        "setup_module": "/abs/path/system_setups/basic_household.py",
        "duration": "one_week",          # one_day | one_week | three_months | full_year
        "year": 2021,
        "seconds_per_timestep": 60,
        "module_config": null            # optional setup-specific config file
    }

HiSim leans on process-global singletons, so sequential setups in one warm child can
leak state into each other — keep ``max_jobs_per_child`` low (e.g. 1-5) for this
runner if cross-contamination is suspected; child recycling then gives each setup a
fresh interpreter at an amortized warm-start cost.
"""


def _build_parameters(payload: dict):  # noqa: ANN202  (SimulationParameters, lazily imported)
    from hisim.simulationparameters import SimulationParameters  # pylint: disable=import-outside-toplevel

    factories = {
        "one_day": SimulationParameters.one_day_only,
        "one_week": SimulationParameters.one_week_only,
        "three_months": SimulationParameters.three_months_only,
        "full_year": SimulationParameters.full_year,
    }
    duration = payload.get("duration", "one_week")
    if duration not in factories:
        raise ValueError(f"Unknown duration {duration!r}; pick one of {sorted(factories)}")
    params = factories[duration](
        year=int(payload.get("year", 2021)),
        seconds_per_timestep=int(payload.get("seconds_per_timestep", 60)),
    )
    _apply_post_processing(params, payload.get("post_processing_options") or [])
    return params


def _apply_post_processing(params, names) -> None:  # noqa: ANN001
    """Append the named ``PostProcessingOptions`` (charts/reports/KPIs) not already enabled."""
    if not names:
        return
    from hisim.postprocessingoptions import PostProcessingOptions  # pylint: disable=import-outside-toplevel

    for name in names:
        try:
            option = PostProcessingOptions[name]
        except KeyError as exc:
            raise ValueError(f"Unknown PostProcessingOptions member {name!r}") from exc
        if option not in params.post_processing_options:
            params.post_processing_options.append(option)


class HiSimSetupRunner:
    """Runs one Python system setup per job inside a warm child."""

    name = "hisim_setup"

    def warmup(self) -> None:
        """Import the full simulator once (in the spawner, so children inherit it)."""
        from hpc_harness.runners.hisim_runner import log_hisim_environment  # pylint: disable=import-outside-toplevel

        log_hisim_environment()
        import hisim.hisim_main  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import

    def on_fork(self) -> None:
        """Reseed randomness per child so parallel sims never share a stream."""
        import random  # pylint: disable=import-outside-toplevel

        random.seed()
        try:
            import numpy.random  # pylint: disable=import-outside-toplevel

            numpy.random.seed()
        except ImportError:
            pass

    def run(self, payload: dict, result_dir: str) -> None:
        """Run one system setup; HiSim writes ``finished.flag`` on success (§4.8)."""
        from hisim import hisim_main  # pylint: disable=import-outside-toplevel

        parameters = _build_parameters(payload)
        parameters.result_directory = result_dir  # honoured by prepare_simulation_directory
        hisim_main.main(
            path_to_module=payload["setup_module"],
            my_simulation_parameters=parameters,
            my_module_config=payload.get("module_config"),
        )
