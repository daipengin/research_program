from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

from research_program.simulation2.config import Simulation2Config
from research_program.simulation2.scheduler import EventScheduler


NODE_COUNT = 5
LISTENING_RATIO = 1.0 / NODE_COUNT
CYCLE_TIME_MS = 1_000.0
CYCLE_COUNT = 30
ALPHAS = (0.25, 0.5, 0.9)
REFERENCE_NODE_ID = 0
CARRIER_SENSE_DURATION_MS = 5.0
TRANSMISSION_DURATION_MS = 20.0

# Initial listening starts are deliberately clustered so that the three alpha
# values produce visibly different convergence transients. With r=1/5, the
# corresponding initial SEND phases are 0.20, 0.22, 0.25, 0.29 and 0.34 cycles.
INITIAL_LISTEN_CYCLES = (0.00, 0.02, 0.05, 0.09, 0.14)

OUTPUT_DIR = (
    Path("outputs")
    / "simulation2_test"
    / "pco_d_phase_difference_cs5_tx20"
)


def run_case(alpha: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    scheduler = EventScheduler(
        config=Simulation2Config(
            listening_ratio=LISTENING_RATIO,
            cycle_time_ms=CYCLE_TIME_MS,
            alpha=alpha,
            carrier_sense_duration_ms=CARRIER_SENSE_DURATION_MS,
            transmission_duration_ms=TRANSMISSION_DURATION_MS,
        ),
        initial_listen_times={
            node_id: start_cycle * CYCLE_TIME_MS
            for node_id, start_cycle in enumerate(INITIAL_LISTEN_CYCLES)
        },
    )
    scheduler.run(until_ms=CYCLE_COUNT * CYCLE_TIME_MS)

    send_df = pd.DataFrame(
        {
            "alpha": alpha,
            "node_id": transmission.source_id,
            "send_time_ms": transmission.start,
        }
        for transmission in scheduler.medium.transmissions
    )
    send_df = send_df.sort_values(["node_id", "send_time_ms"]).reset_index(drop=True)
    send_df["send_index"] = send_df.groupby("node_id").cumcount()

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
            "blocking_start_ms": (
                None
                if result.blocking_transmission is None
                else result.blocking_transmission.start
            ),
            "blocking_end_ms": (
                None
                if result.blocking_transmission is None
                else result.blocking_transmission.end
            ),
        }
        for result in scheduler.medium.carrier_sense_results
    )
    attempt_df = attempt_df.sort_values(["node_id", "attempt_time_ms"]).reset_index(drop=True)
    attempt_df["attempt_index"] = attempt_df.groupby("node_id").cumcount()
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
    alpha_colors = {
        0.25: "#0072B2",
        0.5: "#E69F00",
        0.9: "#009E73",
    }
    node_styles = {
        1: "-",
        2: "--",
        3: "-.",
        4: ":",
    }

    fig, ax = plt.subplots(figsize=(12.0, 6.2))
    for (alpha, node_id), group in phase_df.groupby(["alpha", "node_id"], sort=True):
        ax.plot(
            group["elapsed_cycles"],
            group["phase_difference_rad"],
            color=alpha_colors[float(alpha)],
            linestyle=node_styles[int(node_id)],
            linewidth=1.7,
            marker="o",
            markersize=3.0,
            alpha=0.9,
        )

    phase_ticks = np.linspace(0.0, 2.0 * np.pi, NODE_COUNT + 1)
    phase_labels = [
        "0",
        r"$2\pi/5$",
        r"$4\pi/5$",
        r"$6\pi/5$",
        r"$8\pi/5$",
        r"$2\pi$",
    ]
    ax.set_xlim(0.0, CYCLE_COUNT)
    ax.set_ylim(0.0, 2.0 * np.pi)
    ax.set_xticks(np.arange(0, CYCLE_COUNT + 1, 2))
    ax.set_yticks(phase_ticks, phase_labels)
    ax.set_xlabel(r"Elapsed time $t/T$ [cycles]")
    ax.set_ylabel("Send-attempt phase difference from node 0 [rad]")
    ax.set_title(
        r"PCO-D send-attempt phase difference ($N=5$, $r=1/5$, CS=5 ms, Tx=20 ms)"
    )
    ax.grid(True, alpha=0.25)

    alpha_legend = [
        Line2D([0], [0], color=color, linewidth=2.2, label=rf"$\alpha={alpha:g}$")
        for alpha, color in alpha_colors.items()
    ]
    node_legend = [
        Line2D([0], [0], color="0.25", linestyle=style, linewidth=1.8, label=f"node {node_id}")
        for node_id, style in node_styles.items()
    ]
    first_legend = ax.legend(
        handles=alpha_legend,
        title="Coupling strength",
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
    )
    ax.add_artist(first_legend)
    ax.legend(
        handles=node_legend,
        title="Compared with node 0",
        loc="upper left",
        bbox_to_anchor=(1.01, 0.62),
    )

    fig.tight_layout(rect=(0.0, 0.0, 0.79, 1.0))
    fig.savefig(output_path, dpi=220)
    fig.savefig(output_path.with_suffix(".pdf"))
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    case_results = [run_case(alpha) for alpha in ALPHAS]
    attempt_df = pd.concat([item[0] for item in case_results], ignore_index=True)
    send_df = pd.concat([item[1] for item in case_results], ignore_index=True)
    phase_df = phase_differences_from_reference(attempt_df)

    attempt_df.to_csv(OUTPUT_DIR / "carrier_sense_attempts.csv", index=False)
    send_df.to_csv(OUTPUT_DIR / "send_times.csv", index=False)
    phase_df.to_csv(OUTPUT_DIR / "phase_differences.csv", index=False)
    carrier_sense_summary = (
        attempt_df.groupby(["alpha", "node_id", "action"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    carrier_sense_summary.to_csv(
        OUTPUT_DIR / "carrier_sense_summary.csv",
        index=False,
    )
    plot_phase_differences(phase_df, OUTPUT_DIR / "pco_d_phase_differences.png")

    print(f"saved: {OUTPUT_DIR.resolve()}")
    attempt_counts = attempt_df.groupby(["alpha", "node_id"]).size().unstack(fill_value=0)
    send_counts = send_df.groupby(["alpha", "node_id"]).size().unstack(fill_value=0)
    skip_counts = (
        attempt_df.loc[attempt_df["action"] == "skip_busy"]
        .groupby(["alpha", "node_id"])
        .size()
        .unstack(fill_value=0)
        .reindex(index=ALPHAS, columns=range(NODE_COUNT), fill_value=0)
    )
    print("attempt counts")
    print(attempt_counts)
    print("successful send counts")
    print(send_counts)
    print("busy skip counts")
    print(skip_counts)


if __name__ == "__main__":
    main()
