"""HiSim runner: wraps ``run_one.run_single`` (spec §4.4) — no behavioural change.

Payload contract: ``{"scenario": "<*.scenario.json>", "sim_params": "<*.simulation.json>"}``.
"""


class HiSimRunner:
    """Runs one HiSim simulation per job inside a warm child."""

    name = "hisim"

    def warmup(self) -> None:
        """Import the full simulator once (in the spawner, so children inherit it)."""
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
        """Run one simulation; raises on any simulation failure."""
        from hpc_harness.run_one import run_single  # pylint: disable=import-outside-toplevel

        run_single(
            scenario_path=payload["scenario"],
            sim_params_path=payload["sim_params"],
            result_dir=result_dir,
        )
