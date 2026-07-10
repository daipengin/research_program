# research_program

LoRa 向け非同期化アルゴリズムのシミュレーションと、投稿予定論文に使うグラフ生成を行う Python プロジェクトです。現在の中心設計は **graph-first workflow** です。先に「作りたいグラフ」をジョブとして定義し、そのグラフに必要なシミュレーション、ログ保存、分析、集計、PDF 描画を 1 つの graph folder に閉じ込めます。

この README は操作マニュアルではなく、今後の機能拡張時に AI コーディングエージェントと開発者が参照するための実装ベースの技術文書です。コードから読み取れない仕様は「要確認」と明記します。

## 1. 概要

このプログラムは、複数デバイスの周期的な送信タイミングをイベントシミュレーションし、結合関数による同期/非同期化のふるまいを評価します。LoRa airtime と carrier sense を含む `per_measurement` モードでは、送信ログから Packet Error Rate(PER) 系の指標を作り、K(coupling strength) に対する論文用 PDF グラフを生成します。

graph-first workflow の設計思想:

- 1 graph job が 1 つの解析目的と代表 PDF に対応する。
- job 作成時に `outputs/graph_runs/<graph_type>/<graph_id>/` を作り、入力、状態、raw log、集計 DB、PDF を同じ folder に保存する。
- 先に graph type、K sweep、run 数、simulation_base、plot_settings を固定し、実行後に `status.json` と SQLite から再開・確認・再描画できるようにする。
- raw simulation log と graph-level aggregate を分ける。通常 raw log は `raw_run.sqlite`、Interval PER の集計は `interval_per.sqlite`、job metadata と他 graph type の集計は `graph_data.sqlite` に置く。
- job 完了時には、論文用に追跡したい最終成果物だけを `results/<graph_type>/<graph_id>/` にも書き出す。ここには K ごとの集計 CSV と代表 PDF だけを置き、raw run DB や send log は置かない。
- 現行実装の graph type は `interval_per_vs_k`、`convergence_cycle_vs_k`、`phase_gap_error_vs_k`。

## 2. アーキテクチャ図とデータフロー

```text
Web GUI / CLI
  |
  | create_*_job(params)
  v
outputs/graph_runs/<graph_type>/<graph_id>/
  manifest.json
  status.json
  requests.json
  graph_data.sqlite
  raw_run.sqlite              # new_simulation の raw run store
  interval_per.sqlite         # Interval PER 用の分離 DB
  figures/*.pdf
  logs/
  |
  | run_*_job(graph_dir)
  v
SimulationRequest
  |
  | build_run_configs(seed, ranges_factory)
  v
RunConfig[]
  |
  | EventScheduler + Oscillator
  v
send_log / asleep_log / carrier_sense_log / run metadata
  |
  | SQLiteEventLogger.flush_derived_data()
  v
calculated_cycle_data / phase_gap_error
  |
  | graph_workflow.execution の集計関数
  v
run_interval_per / run_convergence_cycles / run_phase_gap_error_points
  |
  | rebuild_*_aggregate()
  v
aggregate_interval_per / aggregate_convergence_cycles / aggregate_phase_gap_error_points
  |
  | render_*_pdf()
  v
figures/<graph>.pdf + outputs table + manifest outputs
  |
  | _publish_paper_results()
  v
results/<graph_type>/<graph_id>/
  final_values.csv             # K ごとの最終集計値。mean/median/q1/q3 など
  <graph>.pdf                  # 代表 PDF のコピー
```

### graph folder

`src/research_program/graph_workflow/storage.py` が作成します。

```text
outputs/graph_runs/<graph_type>/<graph_id>/
  manifest.json       # job の正規入力、graph_key、status、outputs、run_summary
  status.json         # UI が読む実行状態。cancel_requested もここに入る
  requests.json       # request_id / graph_type / graph_key / params / created_at
  graph_data.sqlite   # job metadata、runs、plot settings、outputs、history、各種集計
  raw_run.sqlite      # run-level raw log。SQLite run store
  interval_per.sqlite # Interval PER の run_interval_per と aggregate_interval_per
  figures/            # representative_pdf など
  logs/               # 現状コードでは作成されるが主要ログ保存先ではない
```

`graph_id` は `YYYYMMDD_HHMMSS_<uuid8>`。`graph_key` は graph type により異なり、Interval PER は `{"coupling_function": ...}`、convergence/phase-gap は `coupling_function` と `source_mode`、必要に応じて `source_graph_id` を持ちます。

### results folder

`results/` は論文用の成果物を git 管理するための公開先です。job 完了時に `graph_workflow.execution._publish_paper_results()` が以下だけをコピー/生成します。

```text
results/<graph_type>/<graph_id>/
  final_values.csv
  <representative_pdf_name>.pdf
```

`final_values.csv` は K ごとの最終数値だけを含みます。Interval PER では `per_percent_mean`, `per_percent_median`, `per_percent_q1`, `per_percent_q3`, `per_percent_std`, `per_percent_min`, `per_percent_max`, packet count などを保存します。convergence では収束 cycle の平均・中央値・四分位と convergence rate、phase-gap error では error/ratio の平均・中央値・四分位などを保存します。

`results/` には `raw_run.sqlite`、SQLite WAL/SHM、`send_log`、`asleep_log`、`carrier_sense_log`、run ごとの生データをコピーしてはいけません。これらは `outputs/graph_runs/<graph_type>/<graph_id>/` 側に残す生成データです。

### JSON ファイル

`manifest.json`:

- `schema_version`
- `graph_id`, `graph_type`, `graph_key`
- `created_at`, `updated_at`, `status`
- `input`: job 作成時の params 全体
- `simulation_base`: params 内の simulation base
- `sweep`: `k_values`, `runs_per_k`
- `outputs`: 完了時に `representative_pdf`
- `run_summary`: `total_runs`, `completed_runs`
- `history`: job 作成時の簡易履歴

`status.json`:

- `job_id`, `status`
- `cancel_requested`, `cancel_requested_at`, `cancel_reason`
- `total_runs`, `completed_runs`, `current_run_id`
- `started_at`, `updated_at`, `finished_at`, `estimated_finish_at`
- `error`

`requests.json`:

- `request_id`, `graph_type`, `graph_key`, `params`, `created_at`

### graph_data.sqlite

`storage.SCHEMA_SQL` で初期化される graph-level DB です。主要テーブル:

- `graph_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)`: `schema_version`、`graph_id`、`input_params`、`storage_policy` などを JSON 文字列で保存。
- `simulation_requests(request_id, graph_type, graph_key, params_json, created_at)`: job request。
- `runs(run_id, request_id, coupling_strength, repeat_index, status, raw_path, metadata_json, created_at, updated_at)`: graph workflow が把握する run 単位の実行記録。`raw_path` は通常 `raw_run.sqlite::<run_id>`。
- `run_cycle_counts(run_id, cycle_index, expected_packets, actual_packets, cumulative_expected_packets, cumulative_actual_packets)`: run ごとの cycle packet count。
- `run_interval_per(aggregate_set_id, run_id, coupling_function, coupling_strength, interval_start_ms, interval_end_ms, interval_cycle_count, expected_packets, actual_packets, per_percent)`: run ごとの Interval PER。現行 Interval PER job では主に `interval_per.sqlite` 側に保存。
- `aggregate_sets(aggregate_set_id, label, interval_start_ms, interval_end_ms, per_method, run_filter_json, created_at)`: 集計条件。
- `aggregate_interval_per(aggregate_set_id, coupling_function, coupling_strength, per_percent_mean, per_percent_std, per_percent_min, per_percent_max, expected_packets_sum, actual_packets_sum, count)`: K ごとの PER 集計。
- `run_convergence_cycles(...)` / `aggregate_convergence_cycles(...)`: phase gap の変化量が閾値以下で安定した cycle を扱う。
- `run_phase_gap_error_points(...)` / `aggregate_phase_gap_error_points(...)`: 指定 cycle または最後の有効 cycle の phase-gap error を扱う。
- `plot_settings(settings_id, aggregate_set_id, settings_json, updated_at)`: 現在の描画設定。
- `outputs(output_id, aggregate_set_id, output_type, relative_path, updated_at)`: 代表 PDF の相対パス。
- `history(history_id, event_type, detail_json, created_at)`: job 作成、集計、描画、キャンセル、失敗などの履歴。

### raw_run.sqlite

`src/research_program/io/sqlite_runs.py` が初期化する run-level DB です。SQLite store 判定は拡張子 `.sqlite`, `.sqlite3`, `.db`。

`runs` metadata columns:

```text
run_id, coupling_strength, strength_ratio, coupling_function,
cycle_time, listening_rate, start_timing_mode, random_sampling_method,
random_seed, random_run_index, random_start_min, random_start_max,
start_step, start_step_count, random_start_candidate_count,
selected_start_times, simulation_mode,
save_asleep_log, save_carrier_sense_log,
carrier_sense_duration_ms, transmission_time_ms,
lora_payload_bytes, lora_spreading_factor, lora_bandwidth_hz,
lora_coding_rate_denominator, lora_preamble_symbols,
lora_explicit_header, lora_crc_enabled, lora_low_data_rate_optimize,
tags, ranges, created_at
```

`send_log` columns:

```text
run_id TEXT
time REAL                         # 送信開始時刻
oscillator_id TEXT
send_count INTEGER
transmission_end_time REAL        # per_measurement では time + LoRa airtime
transmission_time_ms REAL
```

`asleep_log` columns:

```text
run_id, current_time, next_time, oscillator_id
```

`carrier_sense_log` columns:

```text
run_id, time, oscillator_id, action,
carrier_sense_start, carrier_sense_end,
blocking_oscillator_id,
blocking_transmission_start, blocking_transmission_end
```

`action` は `send_clear` または `skip_busy`。

Derived tables:

- `calculated_cycle_data(run_id, cycle_index, cycle_start_time, is_original_cycle, reference_id)`
- `phase_gap_error(run_id, cycle_index, mean_abs_diff_from_ideal_phase_gap, mean_abs_diff_from_ideal_phase_gap_ratio)`

### 旧 CSV 互換

`scheduler.BufferedCsvEventLogger` と `sqlite_runs.export_run_to_directory()` は `metadata.csv`、`send_log.csv`、`asleep_log.csv`、`carrier_sense_log.csv`、`calculated_Cycle_data.csv`、`phase_gap_error.csv` 形式を扱います。現行 graph-first 通常経路は run ごとの CSV folder ではなく `raw_run.sqlite` を使います。

## 3. モジュールマップ

```text
src/research_program/
  cli.py
  graph_workflow/
  simulation/
  analysis/
  plotting/
  io/
  config/
  web/
```

### graph_workflow

責務: graph-first job の作成、保存、実行、集計、PDF 描画、削除、キャンセル。

- `storage.py`
  - constants: `GRAPH_RUNS_ROOT`, `GRAPH_TYPE_*`, `RAW_RUN_DB_NAME`, `INTERVAL_PER_DB_NAME`
  - job 作成: `create_interval_per_vs_k_job()`, `create_convergence_cycle_vs_k_job()`, `create_phase_gap_error_vs_k_job()`
  - 読み取り/管理: `load_graph_job()`, `list_graph_jobs()`, `get_storage_overview()`, `request_cancel_graph_job()`, `delete_graph_job()`, `ensure_interval_per_db()`
  - schema: `SCHEMA_SQL`
- `execution.py`
  - 実行: `run_interval_per_vs_k_job()`, `run_convergence_cycle_vs_k_job()`, `run_phase_gap_error_vs_k_job()`
  - 集計: `rebuild_interval_aggregate()`, `rebuild_convergence_aggregate()`, `rebuild_phase_gap_error_aggregate()`
  - 描画: `render_interval_per_vs_k_pdf()`, `render_convergence_cycle_vs_k_pdf()`, `render_phase_gap_error_vs_k_pdf()`
  - simulation 連携: `_simulation_request_for_k()`, `_initial_start_times_by_run()`

依存: `simulation.runner`, `io.sqlite_runs`, `analysis.calculate_cycle_data`, `plotting.plot_per_by_coupling_strength*`, `matplotlib`, `pandas`, `numpy`。

### simulation

責務: oscillator network のイベントシミュレーションと run log 生成。

- `coupling_functions.py`: `CouplingFunction` enum、各結合関数、`resolve_coupling_function()`
- `oscillator.py`: `Oscillator`。ADD/SEND/ASLEEP/AWAKE/RECEIVE/REMOVE に対する状態遷移と coupling delay を計算。
- `scheduler.py`: `EventScheduler`、`RunConfig`、logger、並列実行。イベント heap queue で時刻順に処理。
- `runner.py`: UI/graph workflow から使う `SimulationRequest` と `run_simulation_request()`。start timing、LoRa airtime、tags、max_workers を解決。
- `config_factory.py`: `build_run_configs()`。seed と ranges_factory から `RunConfig` 群を作る。
- `range_generators.py`: 初期送信時刻/ranges の生成。
- `lora_airtime.py`: LoRa airtime 計算。

依存方向: `runner` -> `config_factory`/`range_generators`/`scheduler`/`lora_airtime`/`coupling_functions`; `scheduler` -> `oscillator`/`io.sqlite_runs`; `oscillator` -> `coupling_functions`。

### io

責務: raw run store と send log の正規化。

- `sqlite_runs.py`: run-level SQLite schema、insert/export/delete/list。WAL と `busy_timeout=60000` を設定。
- `send_log.py`: `time`/`transmission_end_time` の ms 正規化、`detection_time` 追加。`detection_time` は `transmission_end_time` があればそれを、なければ `time` を使う。

### analysis

責務: send log から cycle/phase 指標を作る。

- `calculate_cycle_data.py`
  - `build_cycle_starts()`: reference oscillator の送信時刻から cycle starts を作る。
  - `fill_reference_times()`: reference gap が `cycle_time * 1.3` 以上なら欠落 cycle を補間。
  - `choose_reference_id()`: `fix_ref_<id>` tag があればそれを使い、なければ最初の検出時刻の oscillator を reference にする。
- `calculate_phase_gap_error.py`
  - 各 cycle の最初の送信を使い、位相差列と理想 gap `2π/N` の平均絶対誤差を計算。

### plotting

責務: CSV/SQLite 由来データの集計、PER 系描画ヘルパ、ラベル。

- `plot_per_by_coupling_strength.py`: cycle assignment、PER series、全 run 集計、PDF 保存。
- `plot_per_by_coupling_strength_interval.py`: Interval PER の計算と cache、K ごとの集計、CSV/PDF 保存。
- `labels.py`: K 軸ラベル整形。

graph-first の PDF 描画本体は現在 `graph_workflow.execution.render_*_pdf()` にも実装されています。既存 plotting module は旧 CSV 経路や一部 helper としても使われます。

### web

責務: Streamlit GUI。

- `app.py`: `st.navigation` で Add Job / Jobs / Results / Coupling Check / Management を登録。
- `pages/job_add.py`: graph type ごとの job params 入力と `create_*_job()` 呼び出し。
- `pages/job_status.py`: `list_graph_jobs()`、`run_*_job()`、resume、cancel、delete。
- `pages/results.py`: aggregate table、PDF preview、再描画。
- `pages/coupling_check.py`: 結合関数カーブ確認。
- `pages/management.py`: storage/system overview。
- `utils.py`: duration unit 変換、K values 生成、last params 保存、aggregate 読み取り、PDF preview、plot_settings 保存。

### config

責務: path と plot config。

- `paths.py`: project relative path 解決。
- `plot_config.py`: plotting dataclass 群。環境変数 `RESEARCH_PROGRAM_PLOT_OVERRIDES` で一部上書き可能。

### cli.py

`uv run research-program` の entry point。graph-first workspace の root、job count、SQLite count、job 一覧を標準出力に表示します。job 作成や実行 CLI は現状ありません。

## 4. シミュレーターの仕様

### oscillator / scheduler / runner の関係

`runner.run_simulation_request()` が外部 API です。`SimulationRequest` を検証し、coupling function、tags、LoRa airtime、start timing、max_workers を解決して `RunConfig` 群を作ります。

`scheduler.run_simulation_case()` は 1 つの `RunConfig` を実行します。保存先が SQLite 拡張子なら `SQLiteEventLogger`、それ以外なら CSV logger を使います。

`EventScheduler` は `ScheduledEvent(time, insertion_order, event_id, event_type, source_id, session_id)` を heap queue で時刻順に処理します。同一時刻では insertion order で順序が決まります。`initialize_from_ranges()` は各 `(start_time, end_time, source_id)` について ADD と REMOVE を予約します。

`Oscillator` は各 source_id の状態を持ちます。主な遷移:

```text
ADD      -> on_add()    -> SEND at current + awake_half_duration
SEND     -> on_send()   -> ASLEEP at current + awake_half_duration
ASLEEP   -> on_asleep() -> AWAKE at current + asleep_duration + coupling delay
AWAKE    -> on_awake()  -> SEND at current + awake_half_duration
REMOVE   -> on_remove()
RECEIVE  -> active oscillators に on_receive()
```

時間単位は実装上 ms として扱われます。`metadata.tags` に `sec` がある旧 CSV 解析では analysis 側で ms に変換しますが、graph-first の `SimulationRequest` は `duration_ms`、`carrier_sense_duration_ms`、LoRa airtime ms を使います。

### awake/asleep と coupling delay

`Oscillator.__init__()`:

- `awake_half_duration = cycle_time * (listening_rate / 2) / 100`
- `asleep_duration = cycle_time * (100 - listening_rate) / 100`
- `phase_scale = 2π / cycle_time`
- `coupling_delay_scale = coupling_strength * cycle_time * strength_ratio`

awake window 中に受信した時刻は `received_times_in_awake` に保存されます。`on_asleep()` では、現在 cycle の送信基準時刻 `current_cycle_send_time` に最も近い受信時刻を選び、

```text
phase_diff = phase_scale * (selected_receive_time - current_cycle_send_time)
coupling_value = coupling_function(phase_diff)
next_awake_delay = coupling_value * coupling_delay_scale
```

として次の AWAKE 時刻をずらします。受信がない、または current cycle の送信基準がない場合、delay は 0 です。

### carrier sense と LoRa airtime

`simulation_mode == "per_measurement"` のときだけ carrier sense と transmission time が有効です。`standard` ではどちらも 0 扱いです。

SEND event では以下の流れです。

1. carrier sense window は `[current_time - carrier_sense_duration_ms, current_time]`。ただし oscillator の awake start より前には広げません。
2. `_transmission_intervals` から、window と重なり、かつ自分以外の送信を探します。
3. 見つかった場合は送信せず `on_skip_send()` を呼び、`carrier_sense_log.action = "skip_busy"` を記録します。`phase_reference_time` は `current_time + transmission_time_ms`。
4. 見つからない場合は `on_send()`、`send_log`、`carrier_sense_log.action = "send_clear"` を記録します。
5. `transmission_time_ms > 0` なら RECEIVE event を `transmission_end_time` に予約し、0 なら同時刻に broadcast します。

送信成功/失敗という PER の失敗判定を直接 log しているわけではありません。PER は「対象 interval の期待 packet 数」と `send_log` に残った実送信数から後段で計算します。

### initial_start_times_by_run

graph-first の `_initial_start_times_by_run(params)` は K 比較で初期条件をそろえるための仕組みです。

- `runs_per_k` 個の start time セットを job 開始時に作る。
- seed は `params["simulation_base"]["seed"]`。
- device 数は `simulation_base.device_count`。
- 範囲は `initial_phase_start_percent` から `initial_phase_end_percent` を `cycle_time` に掛けて ms に変換した `[start_ms, end_ms)`。
- 各 run index について `rng.randrange(start_ms, end_ms)` を device_count 回呼ぶ。重複あり。生成後に sort する。
- `run_interval_per_vs_k_job()` は各 K の同じ `repeat_index` に同じ start time セットを渡す。

`runner._random_cycle_ms_with_replacement_ranges_factory()` は `SimulationRequest.initial_start_times_by_run` がある場合、その run index の tuple を使って `generate_ranges_from_start_times()` を呼びます。tuple 長が `device_count` と一致しない場合は例外です。

`start_timing_mode` の実装:

- `random`: `0, start_step, ..., start_step_count * start_step` から重複なしで device_count 個選ぶ。run 間で同じ start set が出ないよう最大 1000 回試す。
- `random_cycle_ms_with_replacement`: `0 <= start < cycle_time` の 1 ms 刻みから重複ありで選ぶ。graph-first のデフォルト。
- `fixed`: `fixed_start_times` があればそれを使い、なければ `fixed_start_offset + i * fixed_start_interval`。

## 5. 結合関数一覧

実装は `src/research_program/simulation/coupling_functions.py` です。引数 `phase_diff` は radian。Python の `%` は剰余なので、`linear_*` は `((-phase_diff * p / π) % 2) - 1` と等価です。

| Enum | 実装関数 | 数式 | 論文式番号 | 備考 |
| --- | --- | --- | --- | --- |
| `KURAMOTO` | `kuramoto_coupling` | `sin(phase_diff)` | 式(4)相当 | 実験対象 |
| `LINEAR` | `linear_coupling` | `((-phase_diff / π) % 2) - 1` | 式(5)相当 | 実験対象 |
| `LINEAR_4` | `linear_4_coupling` | `((-4 * phase_diff / π) % 2) - 1` | 式(6)相当 | 実験対象 |
| `LINEAR_16` | `linear_16_coupling` | `((-16 * phase_diff / π) % 2) - 1` | 要確認 | 試作関数。投稿予定実験に使うかはコードからは確認不可 |
| `NewSIN` | `NewSin_coupling` | `phase_diff %= 2π`; `<π` なら `1 - sin(phase_diff)`、それ以外は `-1 - sin(phase_diff)` | 要確認 | 試作関数。投稿予定実験に使うかはコードからは確認不可 |
| `expSIN` | `expsin_coupling` | `phase_diff %= 2π`; `<π` なら `exp(-phase_diff) * sin(phase_diff)`、それ以外は `exp(phase_diff - 2π) * sin(phase_diff)` | 要確認 | 試作関数。関数内の `pow = 4` は未使用 |
| `exp_4` | `exp_4_coupling` | `phase_diff %= 2π`; `<π` なら `exp(-4 * phase_diff)`、それ以外は `-exp(4 * (phase_diff - 2π))` | 要確認 | 試作関数 |
| `NONE` | `none_coupling` | `0.0` | 要確認 | coupling なしの baseline/確認用 |

注意: `available_coupling_functions()` と Web の coupling check は enum 全体を候補に出します。論文実験で使う関数を UI 上で制限する仕様は、現行コードからは確認できません。

## 6. 新しいグラフ種の追加手順

既存の `Interval PER vs K` を例にした、graph-first での追加箇所です。

1. graph type constant を追加する。
   - 例: `storage.py` の `GRAPH_TYPE_INTERVAL_PER_VS_K = "interval_per_vs_k"`。
   - `graph_workflow/__init__.py` の import と `__all__` に公開する。

2. schema を決める。
   - run 単位の中間値テーブルを `run_<metric>` として設計する。
   - K ごとの集計テーブルを `aggregate_<metric>` として設計する。
   - Interval PER では `run_interval_per` と `aggregate_interval_per`。
   - raw log を新規に増やす必要がある場合は `io/sqlite_runs.py` の schema と logger も更新する。

3. job 作成関数を追加する。
   - Interval PER の例は `create_interval_per_vs_k_job(params)`。
   - graph folder、`figures/`、`logs/`、`graph_data.sqlite`、必要な split DB、`raw_run.sqlite` を初期化する。
   - `manifest.json`、`status.json`、`requests.json` を作る。
   - `simulation_requests`、`aggregate_sets`、`plot_settings`、`graph_meta` を保存する。
   - `total_runs` は通常 `len(k_values) * runs_per_k`。既存 graph から再利用する graph type では selected count を使う実装もある。

4. simulation request 生成を実装または再利用する。
   - Interval PER は `_simulation_request_for_k()` を使い、`simulation_mode="per_measurement"` 固定、LoRa airtime と carrier sense を有効化する。
   - 初期条件を K 間でそろえるなら `_initial_start_times_by_run()` と同等の仕組みを用意する。

5. job 実行関数を追加する。
   - Interval PER の例は `run_interval_per_vs_k_job(graph_dir)`。
   - `status.json` を `running_simulations` -> `running_analysis` -> `rendering_graph` -> `completed` に更新する。
   - `progress_callback` で run 完了ごとに raw log、graph-level `runs`、中間テーブルを保存する。
   - cancel は `status.json.cancel_requested` を run 間または callback 内で確認する。

6. analysis 層を追加する。
   - raw `send_log` / `calculated_cycle_data` / `phase_gap_error` から run 単位の指標を計算する。
   - Interval PER では `_save_run_intermediate_and_interval_from_raw_sqlite()` が raw SQLite から `send_log` と `calculated_cycle_data` を読み、`compute_interval_per_from_cycle_counts()` で PER を作る。
   - 汎用 CSV 解析が必要なら `analysis/` または `plotting/` に helper を置く。

7. aggregate 関数を追加する。
   - Interval PER の例は `rebuild_interval_aggregate()`。
   - `run_interval_per` を `aggregate_set_id, coupling_function, coupling_strength` で groupby し、mean/std/min/max/count などを保存する。

8. plotting 関数を追加する。
   - Interval PER の例は `render_interval_per_vs_k_pdf()`。
   - aggregate table だけを読み、raw simulation を再読込せず PDF を生成する。
   - `outputs` table に `representative_pdf` を保存する。
   - job 完了時に `_publish_paper_results()` から `results/<graph_type>/<graph_id>/final_values.csv` と代表 PDF が出るよう、graph type に対応する `_paper_results_frame()` dispatch を追加する。

9. Web GUI に追加する。
   - `web/pages/job_add.py`: graph type 選択、params form、preview、`create_*_job()` 呼び出し。
   - `web/pages/job_status.py`: `run_graph_job()` の dispatch に追加。
   - `web/pages/results.py`: aggregate table 読み取り、PDF preview、redraw dispatch に追加。
   - `web/utils.py`: aggregate read helper、last params 保存先が必要なら追加。
   - `web/settings.py`: default params と `outputs/settings/*.json` の path を追加。

10. CLI/管理表示を確認する。
   - `cli.py` は `list_graph_jobs()` を使うため、storage の list 対象になっていれば一覧には出る。
   - `management.py` は storage overview に依存するため、DB 数などが自然に反映される。

## 7. 実行方法

セットアップ:

```powershell
uv sync
```

Web GUI 起動:

```powershell
uv run streamlit run src/research_program/web/app.py
```

または:

```powershell
run_streamlit_app.bat
```

CLI:

```powershell
uv run research-program
```

既存 run の棚卸し CSV 生成: `uv run python scripts\inventory_existing_runs.py`

現行 CLI は workspace overview と job 一覧表示のみです。job 作成・実行は Web GUI または Python API 経由です。

Python API の主要入口:

```python
from pathlib import Path
from research_program.graph_workflow import (
    create_interval_per_vs_k_job,
    run_interval_per_vs_k_job,
)

job = create_interval_per_vs_k_job(params)
result = run_interval_per_vs_k_job(Path(job.path))
```

`params` の完全な構造は `web/settings.py` の default params と `web/pages/job_add.py` の form 実装を確認してください。コードから読み取れる限り、graph-first の simulation base は `seed`, `cycle_time`, `duration_ms`, `device_count`, `listening_rate`, `strength_ratio`, `max_workers`, `start_timing_mode`, `initial_phase_*`, LoRa 設定、carrier sense 設定を含みます。

## 8. 変更してはいけない領域と gitignore 方針

`archive/` 以下は現行実装ではなく、旧 README、旧 CLI、旧 plotting、旧 config などの退避先です。機能追加や修正では原則として編集しません。現在の実装は `src/research_program/` と root の起動ファイルを対象にします。

`data/` と `outputs/` は生成データ領域です。`.gitignore` では以下を除外しています。

- `data/runs/*`, `data/run/*`, `data/aggregated/*`, `data/archives/*`
- `outputs/figures/*`, `outputs/reports/*`, `outputs/cache/*`, `outputs/settings/**`, `outputs/graph_runs/**`
- `*.sqlite`, `*.sqlite3`, `*.sqlite-wal`, `*.sqlite-shm`, `*.db`

例外として `.gitkeep` や一部 directory placeholder は追跡対象です。シミュレーション結果、graph folder、SQLite DB、cache、GUI の last settings は git に入れない方針です。

`results/` は例外です。論文用に共有・レビューする最終成果物だけを git 管理します。

- `results/**/*.csv`: 追跡対象。K ごとの最終集計値のみ。
- `results/**/*.pdf`: 追跡対象。代表グラフ PDF のコピー。
- `results/` 以下のそれ以外のファイル: 除外対象。
- raw data (`raw_run.sqlite`, `send_log`, `asleep_log`, `carrier_sense_log`, run-level export) は `results/` に置かない。

## 要確認事項

- 投稿予定論文の式番号としてコードから確実に対応が指定されているのは、ユーザー指定に基づく `KURAMOTO=式(4)相当`、`LINEAR=式(5)相当`、`LINEAR_4=式(6)相当`。コード自体には式番号の metadata はありません。
- `LINEAR_16`, `NewSIN`, `expSIN`, `exp_4`, `NONE` を投稿予定実験から除外するか、UI で非表示にするかはコードからは確認できません。
- `logs/` directory は job 作成時に作られますが、現行主要経路で詳細ログを書き込む実装は確認できません。
- `estimated_finish_at` は `status.json` にありますが、現行実行コードで推定時刻を計算して更新する実装は確認できません。
Paper figures: `uv run python scripts/make_paper_figures.py` generates the coupling-function, initial-phase-demo, PER, TTU, usable-rate, and two-phase PER vector PDFs plus matching plot-data CSVs in `results/paper_figures/`.
