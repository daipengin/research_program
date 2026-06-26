from __future__ import annotations

from dataclasses import replace

from research_program.simulation.coupling_functions import CouplingFunction
from research_program.simulation.scheduler import RunConfig, run_simulations_in_parallel, default_max_workers
from research_program.simulation.config_factory import build_run_configs
from research_program.simulation.range_generators import generate_ranges_same_duration_from_unique_starts

from research_program.analysis.calculate_cycle_data import main as calculate_cycle_data_main
from research_program.analysis.calculate_phase_gap_error import main as calculate_phase_gap_error_main

from research_program.analysis.aggregate_phase_gap_error_stats import main as aggregate_phase_gap_error_stats_main
from research_program.plotting.plot_aggregated_phase_gap_error_overlay import main as plot_aggregated_phase_gap_error_overlay_main


NUM_RUNS_PER_GROUP = 1
COUPLING_STRENGTH_VALUES = range(10, 11,10)


def my_ranges_factory(rng, index):
    """
    各 RunConfig ごとに ranges を作る関数。
    index を使えばケースごとに条件を変えられる。
    """
    return generate_ranges_same_duration_from_unique_starts(
        rng=rng,
        n=100,                  # n×step の範囲から選択。k以上である必要あり
        step=10,                # 幅
        k=20,                   # 台数
        duration=1000 * 30000,  # シミュレーション期間
        start_device_id=0,
    )


if __name__ == "__main__":
    base_config = RunConfig(
        run_id="",
        ranges=[],
        coupling_strength=710,
        strength_ratio=-0.0001,
        coupling_function=CouplingFunction.LINEAR,
        cycle_time=30000,
        listening_rate=25,
        tags=["auto", "generated", "20dai"],
    )

    all_configs = []

    coupling_functions = list(CouplingFunction)

    for coupling_function in coupling_functions:
        if coupling_function is not CouplingFunction.LINEAR:
            continue
            pass
        for coupling_strength in COUPLING_STRENGTH_VALUES:
            group_base_config = replace(
                base_config,
                coupling_function=coupling_function,
                coupling_strength=coupling_strength,
                tags=[
                    "auto",
                    "generated",
                    "20dai",
                    "fix_ref_9",
                    coupling_function.value,
                    f"strength_{coupling_strength}",
                ],
            )

            group_seed = 12345 + coupling_strength * 1000 + coupling_functions.index(coupling_function)

            group_configs = build_run_configs(
                num_configs=NUM_RUNS_PER_GROUP,
                seed=group_seed,
                base_config=group_base_config,
                ranges_factory=my_ranges_factory,
            )

            all_configs.extend(group_configs)

    print(f"total configs: {len(all_configs)}")

    results = run_simulations_in_parallel(
        configs=all_configs,
        output_root="data/run/simulation_runs.sqlite",
        max_workers=default_max_workers(len(all_configs)),
        verbose=False,
    )

    #print("\nsimulation summary")
    #for result in results:
    #    print(result)

    #calculate_cycle_data_main()

    #calculate_phase_gap_error_main()

    #print("\nstart aggregation")
    #aggregate_phase_gap_error_stats_main()

    #print("\nstart overlay plot")
    #plot_aggregated_phase_gap_error_overlay_main()

    print("\nall pipeline finished")

# main.py
# calculate_cycle_data.py
# calculate_phase_gap_error.py
