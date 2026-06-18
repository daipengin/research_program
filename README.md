# research_program

Research tools for simulation, data processing, statistics, plotting, and web visualization.

All runnable Python code now lives under `src/research_program`. The project does not depend on `make_simulation_data`; that directory can be removed after you confirm you no longer need it as an archive of the old layout.

## Layout

```text
configs/
  data_format/      Run data format contracts
  experiments/      Simulation parameter files
  web/              Streamlit configuration
data/
  raw/real/         Raw real-device CSV files
  raw/simulation/   Raw simulation inputs, if needed
  runs/             Standard run directories
  aggregated/       Aggregated statistics
outputs/
  figures/          Generated and imported figures
  reports/          Logs and reports
src/research_program/
  simulation/       Oscillators, coupling functions, scheduler, runner
  io/               CSV, metadata, run discovery, figure discovery
  analysis/         Cycle data, phase-gap error, aggregation, PER comparison
  plotting/         Matplotlib plotting scripts
  pipelines/        Higher-level workflows
  web/              Streamlit Web UI
```

## Run Data Format

The run data contract is defined in [configs/data_format/run_v1.toml](configs/data_format/run_v1.toml).

```text
data/runs/<run_id>/
  metadata.csv
  send_log.csv
  calculated_Cycle_data.csv
  phase_gap_error.csv
```

`metadata.csv` and `send_log.csv` are required. `calculated_Cycle_data.csv` and `phase_gap_error.csv` are derived files.

## Web UI

```powershell
uv run streamlit run src/research_program/web/app.py
```

The Web UI supports:

- running simulations with editable parameters
- filtering runs by parameters and tags
- showing how many runs match the filters
- plotting only the filtered runs
- downloading generated graphs as `png`, `pdf`, or `svg`
- browsing, previewing, and downloading result figures

## CLI

```powershell
uv run research-program describe-data-format
uv run research-program list-runs
uv run research-program run-simulation
uv run research-program import-raw-data
uv run research-program calculate-cycle-data
uv run research-program calculate-phase-gap-error
uv run research-program aggregate-phase-gap-error
uv run research-program plot-phase-diff
uv run research-program plot-phase-gap-error
uv run research-program plot-per
uv run research-program plot-per-aligned
```

Simulation defaults are in [configs/experiments/default_simulation.toml](configs/experiments/default_simulation.toml).
