from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from research_program.simulation2.config import Simulation2Config
from research_program.simulation2.scheduler import EventScheduler


NODE_COUNT = 50
LISTENING_RATIO = 0.02
CYCLE_TIME_MS = 10_000.0
CYCLE_COUNT = 30
ALPHAS = (0.25, 0.5, 0.9)
REFERENCE_NODE_ID = 0
CARRIER_SENSE_DURATION_MS = 5.0
TRANSMISSION_DURATION_MS = 20.0
INITIAL_PHASE_SEED = 20260717

OUTPUT_DIR = (
    Path("outputs")
    / "simulation2_test"
    / "pco_d_phase_difference_n50_r002_t10000_cs5_tx20"
)


def initial_listen_times() -> dict[int, float]:
    """Return reproducible, non-uniform initial phases in one nominal cycle."""

    rng = np.random.default_rng(INITIAL_PHASE_SEED)
    start_times = np.empty(NODE_COUNT, dtype=float)
    start_times[0] = 0.0
    start_times[1:] = rng.uniform(0.0, CYCLE_TIME_MS, size=NODE_COUNT - 1)
    return {node_id: float(start_time) for node_id, start_time in enumerate(start_times)}


def run_case(alpha: float, starts: dict[int, float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    scheduler = EventScheduler(
        config=Simulation2Config(
            listening_ratio=LISTENING_RATIO,
            cycle_time_ms=CYCLE_TIME_MS,
            alpha=alpha,
            carrier_sense_duration_ms=CARRIER_SENSE_DURATION_MS,
            transmission_duration_ms=TRANSMISSION_DURATION_MS,
        ),
        initial_listen_times=starts,
    )
    scheduler.run(until_ms=CYCLE_COUNT * CYCLE_TIME_MS)

    attempt_df = pd.DataFrame(
        {
            "alpha": alpha,
            "node_id": result.source_id,
            "attempt_time_ms": result.time,
            "carrier_sense_start_ms": result.window_start,
            "carrier_sense_end_ms": result.window_end,
            "action": "skip_busy" if result.is_busy else "send_clear",
            "blocking_node_id": (
                None
                if result.blocking_transmission is None
                else result.blocking_transmission.source_id
            ),
        }
        for result in scheduler.medium.carrier_sense_results
    )
    attempt_df = attempt_df.sort_values(["node_id", "attempt_time_ms"]).reset_index(drop=True)
    attempt_df["attempt_index"] = attempt_df.groupby("node_id").cumcount()

    send_df = pd.DataFrame(
        {
            "alpha": alpha,
            "node_id": transmission.source_id,
            "send_time_ms": transmission.start,
            "transmission_end_ms": transmission.end,
        }
        for transmission in scheduler.medium.transmissions
    )
    return attempt_df, send_df


def phase_differences_from_reference(attempt_df: pd.DataFrame) -> pd.DataFrame:
    reference_df = (
        attempt_df.loc[
            attempt_df["node_id"] == REFERENCE_NODE_ID,
            ["alpha", "attempt_index", "attempt_time_ms"],
        ]
        .rename(columns={"attempt_time_ms": "reference_attempt_time_ms"})
    )
    phase_df = attempt_df.merge(
        reference_df,
        on=["alpha", "attempt_index"],
        how="inner",
        validate="many_to_one",
    )
    phase_df = phase_df.loc[phase_df["node_id"] != REFERENCE_NODE_ID].copy()
    phase_df["elapsed_cycles"] = phase_df["reference_attempt_time_ms"] / CYCLE_TIME_MS
    phase_df["phase_difference_rad"] = np.mod(
        2.0
        * np.pi
        * (phase_df["attempt_time_ms"] - phase_df["reference_attempt_time_ms"])
        / CYCLE_TIME_MS,
        2.0 * np.pi,
    )
    return phase_df.sort_values(["alpha", "node_id", "attempt_index"]).reset_index(drop=True)


def plot_phase_differences(phase_df: pd.DataFrame, output_path: Path) -> None:
    fig, axes = plt.subplots(
        nrows=len(ALPHAS),
        ncols=1,
        figsize=(11.5, 9.0),
        sharex=True,
        sharey=True,
        constrained_layout=True,
    )
    scatter = None
    for axis, alpha in zip(axes, ALPHAS, strict=True):
        group = phase_df.loc[phase_df["alpha"] == alpha]
        scatter = axis.scatter(
            group["elapsed_cycles"],
            group["phase_difference_rad"],
            c=group["node_id"],
            cmap="turbo",
            vmin=1,
            vmax=NODE_COUNT - 1,
            s=9,
            alpha=0.7,
            linewidths=0,
        )
        axis.set_ylabel(rf"$\alpha={alpha:g}$\nphase [rad]")
        axis.set_ylim(0.0, 2.0 * np.pi)
        axis.set_yticks(
            [0.0, np.pi / 2.0, np.pi, 3.0 * np.pi / 2.0, 2.0 * np.pi],
            ["0", r"$\pi/2$", r"$\pi$", r"$3\pi/2$", r"$2\pi$"],
        )
        axis.grid(True, alpha=0.22)

    axes[-1].set_xlim(0.0, CYCLE_COUNT)
    axes[-1].set_xticks(np.arange(0, CYCLE_COUNT + 1, 5))
    axes[-1].set_xlabel(r"Elapsed time $t/T$ [cycles]")
    fig.suptitle(
        "PCO-D send-attempt phase difference from node 0 "
        r"($N=50$, $r=0.02$, $T=10000$ ms, CS=5 ms, Tx=20 ms)"
    )
    if scatter is not None:
        colorbar = fig.colorbar(scatter, ax=axes, shrink=0.86, pad=0.015)
        colorbar.set_label("Node ID")
    fig.savefig(output_path, dpi=220)
    fig.savefig(output_path.with_suffix(".pdf"))
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    starts = initial_listen_times()
    pd.DataFrame(
        {"node_id": list(starts), "initial_listen_time_ms": list(starts.values())}
    ).to_csv(OUTPUT_DIR / "initial_listen_times.csv", index=False)

    results = [run_case(alpha, starts) for alpha in ALPHAS]
    attempt_df = pd.concat([result[0] for result in results], ignore_index=True)
    send_df = pd.concat([result[1] for result in results], ignore_index=True)
    phase_df = phase_differences_from_reference(attempt_df)
    summary_df = (
        attempt_df.groupby(["alpha", "action"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )

    attempt_df.to_csv(OUTPUT_DIR / "carrier_sense_attempts.csv", index=False)
    send_df.to_csv(OUTPUT_DIR / "send_times.csv", index=False)
    phase_df.to_csv(OUTPUT_DIR / "phase_differences.csv", index=False)
    summary_df.to_csv(OUTPUT_DIR / "carrier_sense_summary.csv", index=False)
    plot_phase_differences(phase_df, OUTPUT_DIR / "pco_d_phase_differences.png")

    print(f"saved: {OUTPUT_DIR.resolve()}")
    print("carrier-sense summary")
    print(summary_df.to_string(index=False))
    print("send counts by alpha")
    print(send_df.groupby("alpha").size().to_string())


if __name__ == "__main__":
    main()
