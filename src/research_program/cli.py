from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from research_program.config.loader import load_toml
from research_program.io.cleanup import (
    DEFAULT_TARGETS,
    TARGET_PATHS,
    cleanup_experiment_outputs,
    format_cleanup_result,
)
from research_program.io.data_contract import RunDataContract, load_data_contract
from research_program.io.run_store import discover_runs, records_to_frame
from research_program.simulation.runner import (
    request_from_config,
    run_simulation_request,
)


MODULE_COMMANDS = {
    "import-raw-data": "research_program.io.import_raw_data_to_results",
    "calculate-cycle-data": "research_program.analysis.calculate_cycle_data",
    "calculate-phase-gap-error": "research_program.analysis.calculate_phase_gap_error",
    "aggregate-phase-gap-error": "research_program.analysis.aggregate_phase_gap_error_stats",
    "compare-per": "research_program.analysis.compare_per_by_devices_and_interval",
    "plot-phase-diff": "research_program.plotting.visualize_phase_diff",
    "plot-phase-gap-error": "research_program.plotting.plot_phase_gap_error",
    "plot-per": "research_program.plotting.plot_PER",
    "plot-per-aligned": "research_program.plotting.plot_per_aligned",
    "plot-aggregated-phase-gap-error": "research_program.plotting.plot_aggregated_phase_gap_error",
    "plot-aggregated-phase-gap-error-overlay": "research_program.plotting.plot_aggregated_phase_gap_error_overlay",
    "plot-convergence-summary": "research_program.plotting.plot_convergence_summary",
}


def _contract_to_dict(contract: RunDataContract) -> dict[str, Any]:
    return {
        "version": contract.version,
        "description": contract.description,
        "layout": contract.layout,
        "files": {
            file_key: [
                {
                    "name": column.name,
                    "type": column.dtype,
                    "required": column.required,
                    "unit": column.unit,
                    "aliases": list(column.aliases),
                }
                for column in file_spec.columns
            ]
            for file_key, file_spec in contract.files.items()
        },
    }


def describe_data_format(args: argparse.Namespace) -> int:
    contract = load_data_contract(args.config)
    print(json.dumps(_contract_to_dict(contract), indent=2, ensure_ascii=False))
    return 0


def list_runs(args: argparse.Namespace) -> int:
    web_config = load_toml(args.web_config)
    contract = load_data_contract(web_config["paths"]["data_format_config"])
    records = discover_runs(web_config["paths"].get("runs_dirs", []), contract)
    df = records_to_frame(records)
    if df.empty:
        print("no runs found")
        return 0
    visible_columns = [
        column
        for column in [
            "run_id",
            "coupling_function",
            "coupling_strength",
            "cycle_time",
            "listening_rate",
            "tags",
            "status",
            "path",
        ]
        if column in df.columns
    ]
    with pd.option_context("display.max_colwidth", 120):
        print(df[visible_columns].to_string(index=False))
    return 0


def run_simulation(args: argparse.Namespace) -> int:
    simulation_config = load_toml(args.config)
    request = request_from_config(simulation_config)
    results = run_simulation_request(request)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def clear_experiment_outputs(args: argparse.Namespace) -> int:
    target_names = tuple(args.targets or DEFAULT_TARGETS)
    if args.include_raw_real and "raw_real" not in target_names:
        target_names = (*target_names, "raw_real")
    if args.include_raw_simulation and "raw_simulation" not in target_names:
        target_names = (*target_names, "raw_simulation")

    result = cleanup_experiment_outputs(
        target_names=target_names,
        dry_run=not args.yes,
    )
    print(format_cleanup_result(result))
    if not args.yes:
        print("\nThis was a dry run. Add --yes to delete these items.")
    return 0


def run_module_command(args: argparse.Namespace) -> int:
    import importlib

    module = importlib.import_module(args.module_name)
    module.main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-program")
    subparsers = parser.add_subparsers(dest="command", required=True)

    describe_parser = subparsers.add_parser("describe-data-format")
    describe_parser.add_argument(
        "--config",
        default=Path("configs/data_format/run_v1.toml"),
    )
    describe_parser.set_defaults(func=describe_data_format)

    list_parser = subparsers.add_parser("list-runs")
    list_parser.add_argument(
        "--web-config",
        default=Path("configs/web/default.toml"),
    )
    list_parser.set_defaults(func=list_runs)

    simulation_parser = subparsers.add_parser("run-simulation")
    simulation_parser.add_argument(
        "--config",
        default=Path("configs/experiments/default_simulation.toml"),
    )
    simulation_parser.set_defaults(func=run_simulation)

    cleanup_parser = subparsers.add_parser("clear-experiment-outputs")
    cleanup_parser.add_argument(
        "--target",
        dest="targets",
        action="append",
        choices=sorted(name for name in TARGET_PATHS if not name.startswith("raw_")),
        help="Cleanup target. Can be specified multiple times. Defaults to runs, aggregated, figures.",
    )
    cleanup_parser.add_argument(
        "--include-raw-real",
        action="store_true",
        help="Also delete files under data/raw/real.",
    )
    cleanup_parser.add_argument(
        "--include-raw-simulation",
        action="store_true",
        help="Also delete files under data/raw/simulation.",
    )
    cleanup_parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete files. Without this option, only a dry run is shown.",
    )
    cleanup_parser.set_defaults(func=clear_experiment_outputs)

    for command_name, module_name in MODULE_COMMANDS.items():
        command_parser = subparsers.add_parser(command_name)
        command_parser.set_defaults(func=run_module_command, module_name=module_name)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
