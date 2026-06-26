# research_program 仕様書

作成日: 2026-06-23

## 1. 概要

`research_program` は、研究で扱う振動子ネットワークのシミュレーション、実機CSVデータの取り込み、周期・位相ギャップ誤差・PERの解析、グラフ生成、StreamlitによるWeb可視化を統合するPythonプロジェクトである。

主な利用者は、シミュレーション条件を変えながら複数runを生成し、実機データと同じrun形式で後処理・可視化したい研究作業者を想定する。

## 2. スコープ

本仕様書の対象は、リポジトリ内の次の範囲である。

- Pythonパッケージ: `src/research_program`
- CLIエントリポイント: `research-program`
- Streamlit Web UI: `src/research_program/web/app.py`
- 設定ファイル: `configs/`
- データ契約: `configs/data_format/run_v1.toml`
- 入出力ディレクトリ: `data/`, `outputs/`

自動テストは現状 `tests/.gitkeep` のみで、仕様上の振る舞いは実装コード、設定ファイル、README、CLIの実行結果から整理している。

## 3. 実行環境

| 項目 | 仕様 |
| --- | --- |
| Python | 3.12以上 |
| パッケージ名 | `research-program` |
| パッケージルート | `src/research_program` |
| パッケージ管理 | `uv` |
| CLI | `uv run research-program <command>` |
| Web UI | `uv run streamlit run src/research_program/web/app.py` |

主要依存ライブラリ:

- `numpy`
- `pandas`
- `matplotlib`
- `japanize-matplotlib`
- `streamlit`
- `pillow`
- `uuid6`

## 4. ディレクトリ構成

```text
configs/
  data_format/      runデータ形式の定義
  experiments/      シミュレーション設定
  web/              Web UI設定
data/
  raw/real/         実機の元CSVデータ
  raw/simulation/   シミュレーション用の元データ
  runs/             共通run形式のデータ
  aggregated/       集約済み解析データ
outputs/
  figures/          生成グラフ
  reports/          ジョブ状態、run index、前回設定など
src/research_program/
  analysis/         周期データ、位相ギャップ誤差、PER集約
  config/           パス、TOML、プロット設定
  io/               run探索、CSV契約、画像探索、削除
  pipelines/        旧来または補助的な一括実行入口
  plotting/         グラフ生成
  simulation/       シミュレータ本体
  web/              Streamlit Web UI
```

生成データは `.gitignore` により、原則としてGit管理対象外である。`.gitkeep` は保持する。

## 5. 用語

| 用語 | 意味 |
| --- | --- |
| run | 1回の実験またはシミュレーション結果を格納する単位。既定のシミュレーション出力では `data/run/simulation_runs.sqlite` のSQLite内に保存する。従来互換のCSV runディレクトリでは `data/runs/<run_id>/` に保存する。 |
| 振動子 / device | シミュレーション内の送信主体。`oscillator_id` で識別する。 |
| cycle | 参照振動子の送信時刻を基準に定義される周期番号。 |
| detection time | 解析で検知時刻として使う時刻。`transmission_end_time` があればそれを使い、なければ `time` を使う。 |
| PER | Packet Error Rate。一定周期幅で期待送信数に対する未受信割合として計算する。 |
| 位相ギャップ誤差 | 同一cycle内の送信位相間隔と理想間隔 `2π / N` の平均絶対誤差。 |

## 6. 設定ファイル

### 6.1 シミュレーション設定

既定ファイル: `configs/experiments/default_simulation.toml`

| セクション | 主な項目 | 仕様 |
| --- | --- | --- |
| `paths` | `data_format_config`, `output_runs_dir` | データ契約とrun出力先を指定する。 |
| `simulation` | `num_runs`, `seed`, `coupling_function`, `coupling_strength`, `strength_ratio` | run数、乱数、結合関数、結合強度、補正倍率を指定する。 |
| `simulation` | `cycle_time`, `listening_rate`, `device_count`, `duration` | 周期、待機率、振動子数、実行時間を指定する。単位は主にms。 |
| `simulation` | `start_timing_mode`, `start_step_count`, `start_step`, `fixed_start_times`, `fixed_start_interval`, `fixed_start_offset` | 開始時刻の生成方法を指定する。 |
| `simulation` | `simulation_mode`, `carrier_sense_duration_ms`, `lora_*` | PER測定モードとLoRa airtime計算条件を指定する。 |
| `simulation` | `tags`, `max_workers` | runタグと並列実行数を指定する。 |
| `sweep` | `enabled`, `coupling_functions`, `coupling_strength_values` | Web UIでの一括条件生成に利用する。 |

### 6.2 Web設定

既定ファイル: `configs/web/default.toml`

- run探索対象: `data/runs`, `data/run`
- 集約データ探索対象: `data/aggregated`
- 画像探索対象: `outputs/figures`
- フィルタ対象の数値項目: `coupling_strength`, `strength_ratio`, `cycle_time`, `listening_rate`
- フィルタ対象のカテゴリ項目: `coupling_function`
- 画像拡張子: `.png`, `.jpg`, `.jpeg`, `.webp`, `.pdf`, `.svg`

### 6.3 プロット設定

プロット設定は `src/research_program/config/plot_config.py` のdataclassで定義する。Web UIとグラフ作成ジョブは、環境変数 `RESEARCH_PROGRAM_PLOT_OVERRIDES` にJSONを渡すことで、選択したグラフ種別ごとの設定値を一時的に上書きできる。

## 7. runデータ契約

データ契約のバージョンは `run_v1` である。定義元は `configs/data_format/run_v1.toml`。

### 7.1 runディレクトリ

```text
data/runs/<run_id>/
  metadata.csv
  send_log.csv
  calculated_Cycle_data.csv
  phase_gap_error.csv
  carrier_sense_log.csv
  asleep_log.csv
```

必須ファイル:

- `metadata.csv`
- `send_log.csv`

派生ファイル:

- `calculated_Cycle_data.csv`
- `phase_gap_error.csv`
- `carrier_sense_log.csv`

`asleep_log.csv` はシミュレーションが出力する内部ログで、データ契約上の派生ファイルには含まれない。

シミュレーションの既定出力はSQLiteであり、`data/run/simulation_runs.sqlite` に `runs`, `send_log`, `asleep_log`, `carrier_sense_log`, `calculated_cycle_data`, `phase_gap_error` テーブルとして保存する。`asleep_log` と `carrier_sense_log` は、シミュレーション設定で明示的に保存を有効にした場合だけ行を保存する。`calculated_cycle_data` と `phase_gap_error` はPER計算窓幅などグラフ固有パラメーターに依存しない派生データとして、シミュレーションrun完了後にSQLiteへ保存する。PER値そのものは窓幅に依存するため保存せず、グラフ作成時に `send_log` と `calculated_cycle_data` から再計算する。

従来互換として、出力先がディレクトリの場合は `data/runs/<run_id>/` 形式でCSVを保存する。出力先が `.sqlite`, `.sqlite3`, `.db` のいずれかの拡張子を持つ場合はSQLite run storeとして扱う。Web UIのrun探索はCSV runディレクトリとSQLite run storeの両方を対象にする。既存のグラフ処理はCSV runディレクトリを前提にしているため、SQLite runを対象にグラフ作成する場合はジョブ実行時に対象runだけを一時ディレクトリへ `metadata.csv`, `send_log.csv`, `calculated_Cycle_data.csv`, `phase_gap_error.csv` として展開する。

Web UIのシミュレーションフォームでは保存形式の既定をSQLiteにする。CSV保存は保存形式で `CSV` を選んだ場合だけ有効になり、出力先ディレクトリ配下へ従来形式のrunディレクトリを書き出す。

### 7.2 metadata.csv

主な列:

| 列 | 型 | 必須 | 仕様 |
| --- | --- | --- | --- |
| `run_id` | string | 必須 | run識別子。 |
| `coupling_strength` | integer | 必須 | 結合強度。 |
| `strength_ratio` | float | 任意 | 位相補正量の倍率。旧列名 `strengrh_ratio` も受け付ける。 |
| `coupling_function` | string | 必須 | `KURAMOTO`, `LINEAR`, `NewSIN`, `NONE` など。 |
| `cycle_time` | float | 必須 | 1周期の長さ。単位はms。 |
| `listening_rate` | float | 必須 | 1周期中の受信待機率。単位は%。 |
| `start_timing_mode` | string | 任意 | `random` または `fixed`。 |
| `selected_start_times` | list[integer] | 任意 | 実際に選ばれた開始時刻。`;` 区切り。 |
| `simulation_mode` | string | 任意 | `standard` または `per_measurement`。 |
| `save_asleep_log` | string | 任意 | `asleep_log.csv` を保存したか。 |
| `save_carrier_sense_log` | string | 任意 | `carrier_sense_log.csv` を保存したか。 |
| `carrier_sense_duration_ms` | float | 任意 | キャリアセンス時間。0は0msとして扱う。metadataには実効値を保存する。 |
| `transmission_time_ms` | float | 任意 | パケット占有時間。 |
| `lora_*` | integer/string | 任意 | LoRa airtime計算に使った条件。 |
| `tags` | list[string] | 任意 | `;` 区切りタグ。 |
| `ranges` | list[range] | 任意 | `start:end:device_id` を `|` 区切りで保存する。 |

### 7.3 send_log.csv

| 列 | 型 | 必須 | 仕様 |
| --- | --- | --- | --- |
| `time` | float | 必須 | 送信開始時刻。通常はms。`sec` タグがある場合、解析時に秒からmsへ変換する。 |
| `oscillator_id` | string | 必須 | 振動子ID。`hex` タグがある場合、解析時に16進数として整数化する。 |
| `send_count` | integer | 任意 | 振動子ごとの送信回数。 |
| `transmission_end_time` | float | 任意 | パケット送信終了時刻。解析上の検知時刻として優先する。 |
| `transmission_time_ms` | float | 任意 | 送信占有時間。 |

### 7.4 carrier_sense_log.csv

PER測定モードで出力する。`standard` モードではヘッダーのみになる場合がある。

| 列 | 仕様 |
| --- | --- |
| `time` | 送信予定時刻。 |
| `oscillator_id` | 評価対象の振動子ID。 |
| `action` | `send_clear` または `skip_busy`。 |
| `carrier_sense_start`, `carrier_sense_end` | キャリアセンス区間。 |
| `blocking_oscillator_id` | `skip_busy` の原因になった振動子ID。 |
| `blocking_transmission_start`, `blocking_transmission_end` | 原因送信の占有区間。 |

### 7.5 calculated_Cycle_data.csv

| 列 | 仕様 |
| --- | --- |
| `cycle_index` | 1始まりの周期番号。 |
| `cycle_start_time` | 参照振動子の周期開始時刻。 |
| `is_original_cycle` | 実測またはログ由来の周期ならtrue、補間された周期ならfalse。 |
| `reference_id` | 周期基準にした振動子ID。 |

### 7.6 phase_gap_error.csv

| 列 | 仕様 |
| --- | --- |
| `cycle_index` | 1始まりの周期番号。 |
| `mean_abs_diff_from_ideal_phase_gap` | 理想位相間隔からの平均絶対誤差。単位はrad。 |
| `mean_abs_diff_from_ideal_phase_gap_ratio` | 上記誤差を理想位相間隔で割った値。 |

## 8. シミュレーション仕様

### 8.1 実行入口

CLI:

```powershell
uv run research-program run-simulation
```

既定では `configs/experiments/default_simulation.toml` を読み込み、`data/run/simulation_runs.sqlite` にrunを保存する。`output_runs_dir` にディレクトリを指定した場合は、従来通り `data/runs/<run_id>/` 形式でCSV保存する。

`output_runs_dir` が省略された場合、および `run_simulation_case()` / `run_simulations_in_parallel()` を出力先未指定で直接呼び出した場合の既定出力先も `data/run/simulation_runs.sqlite` である。

### 8.2 SimulationRequest

`SimulationRequest` はシミュレーション実行要求を表す内部データ構造である。

| 項目 | 仕様 |
| --- | --- |
| `num_runs` | 1以上。 |
| `seed` | ランダム開始時刻生成に使う乱数シード。 |
| `coupling_function` | `KURAMOTO`, `LINEAR`, `NewSIN`, `NONE`。 |
| `coupling_strength` | 整数の結合強度。 |
| `strength_ratio` | 次回覚醒時刻補正に掛ける倍率。 |
| `cycle_time` | 周期時間。ms。 |
| `listening_rate` | 待機率。% |
| `device_count` | 1以上。 |
| `duration` | 各振動子の有効期間。ms。 |
| `start_timing_mode` | `random` または `fixed`。 |
| `max_workers` | 0以下ならCPU数とrun数から自動決定。 |
| `simulation_mode` | `standard` または `per_measurement`。 |

### 8.3 run_id

各runには `uuid7` とrun indexを組み合わせたIDを割り当てる。

形式:

```text
<uuid7>_<index:04d>
```

### 8.4 タグ正規化

シミュレーション実行時、指定タグから次の既存タグを除去してから自動タグを追加する。

- `\d+dai`
- `device_count_\d+`
- `start_random`, `start_fixed`
- `mode_standard`, `mode_per_measurement`

追加されるタグ:

- `device_count_<device_count>`
- `<device_count>dai`
- `start_<start_timing_mode>`
- `mode_<simulation_mode>`

### 8.5 開始時刻

#### random

候補集合:

```text
0, start_step, 2 * start_step, ..., start_step_count * start_step
```

この集合から `device_count` 個を一様・重複なしで抽出し、昇順に並べる。1つの実行要求内では、runごとの開始時刻セットが重ならないよう最大1000回まで再試行する。

制約:

- `device_count <= start_step_count + 1`

metadataには、`random_sampling_method`, `random_seed`, `random_run_index`, `random_start_min`, `random_start_max`, `start_step`, `start_step_count`, `random_start_candidate_count`, `selected_start_times` を保存する。

#### fixed

`fixed_start_times` が指定されていればその値を使う。未指定の場合は、`fixed_start_offset + i * fixed_start_interval` で `device_count` 個を生成する。

制約:

- 開始時刻数は `device_count` と一致する必要がある。
- 開始時刻は0以上である必要がある。

### 8.6 イベントモデル

シミュレーションはイベント駆動で進行する。

主なイベント:

- `ADD_Oscillator_Event`
- `SEND_Event`
- `ASLEEP_Event`
- `AWAKE_Event`
- `REMOVE_Oscillator_Event`
- `RECEIVE_Event`

振動子の基本サイクル:

1. `ADD` または `AWAKE` で受信待機窓に入る。
2. `cycle_time * (listening_rate / 2) / 100` 後に `SEND` する。
3. `SEND` 後、同じ長さだけ待って `ASLEEP` する。
4. `ASLEEP` 時に受信ログをもとに次回覚醒時刻の補正量を計算する。
5. `cycle_time * (100 - listening_rate) / 100 + next_awake_delay` 後に `AWAKE` する。

補正量:

```text
phase_diff = 2π * (selected_receive_time - current_cycle_send_time) / cycle_time
coupling_value = coupling_function(phase_diff)
next_awake_delay = coupling_value * coupling_strength * cycle_time * strength_ratio
```

同一の受信待機窓内に複数受信がある場合は、当該cycleの送信時刻に最も近い受信時刻を使う。

### 8.7 結合関数

| 名前 | 仕様 |
| --- | --- |
| `KURAMOTO` | `sin(phase_diff)` |
| `LINEAR` | `((-phase_diff / π) mod 2) - 1` |
| `NewSIN` | `phase_diff mod 2π` が `π` 未満なら `1 - sin(phase_diff)`、それ以外なら `-1 - sin(phase_diff)` |
| `NONE` | 常に `0.0`。受信ログは残るが位相補正しない。 |

### 8.8 standardモード

`simulation_mode = "standard"` の場合:

- 送信は瞬間イベントとして扱う。
- `transmission_time_ms` は0。
- 送信後ただちに他のアクティブ振動子へ受信をブロードキャストする。
- キャリアセンスによるスキップは行わない。

### 8.9 per_measurementモード

`simulation_mode = "per_measurement"` の場合:

- 送信はLoRa airtimeぶんの占有区間として扱う。
- 送信予定時刻の直前にキャリアセンス区間を確認する。
- 他振動子の送信占有区間と重なれば、そのcycleの送信をスキップする。
- 実際に送信したものだけを `send_log.csv` に出力する。
- `save_carrier_sense_log = true` の場合、キャリアセンスの結果を `carrier_sense_log.csv` に出力する。既定では出力しない。
- 受信ブロードキャストは `transmission_end_time` で行う。
- 振動子内部の位相補正では、成功送信は実際の `transmission_end_time`、スキップ送信は送れていた場合の `transmission_end_time` に相当する時刻を自分側の基準時刻として使う。

キャリアセンス区間:

```text
carrier_sense_start = max(current_awake_start_time, current_time - effective_duration)
carrier_sense_end = current_time
```

`carrier_sense_duration_ms` は指定値をそのまま使う。0の場合、区間は `[current_time, current_time]` の空区間となり、スキップ判定は行わない。

### 8.10 LoRa airtime

LoRa airtimeは次式で計算する。

```text
T_sym = 2^SF / BW
DE = 1 if T_sym >= 0.016 else 0   # autoの場合
H = 0 if explicit_header else 1
CRC = 1 if crc_enabled else 0
CR = coding_rate_denominator - 4

T_preamble = (preamble_symbols + 4.25) * T_sym
payload_symbol_count =
  8 + max(ceil((8 * payload_bytes - 4 * SF + 28 + 16 * CRC - 20 * H)
               / (4 * (SF - 2 * DE))) * (CR + 4), 0)
T_payload = payload_symbol_count * T_sym
T_airtime_ms = (T_preamble + T_payload) * 1000
```

制約:

- `payload_bytes >= 0`
- `6 <= spreading_factor <= 12`
- `bandwidth_hz > 0`
- `5 <= coding_rate_denominator <= 8`
- `preamble_symbols >= 0`

## 9. 実機データ取り込み仕様

CLI:

```powershell
uv run research-program import-raw-data
```

入力:

- `data/raw/real/*.csv`

出力:

- `data/runs/<filename_stem>/metadata.csv`
- `data/runs/<filename_stem>/send_log.csv`

ファイル名から推定する値:

| パターン | 出力値 |
| --- | --- |
| `(\d+)K` | `coupling_strength` |
| 先頭が `kura` | `coupling_function = KURAMOTO` |
| 先頭が `lin` | `coupling_function = LINEAR` |
| 先頭が `none` | `coupling_function = NONE` |
| `(\d+)sec` | `cycle_time = 秒 * 1000` |
| `(\d+)mac` | `<N>dai` タグ |

既定値:

- `coupling_strength = 0`
- `strength_ratio = 0.0001`
- `coupling_function = N`
- `cycle_time = 30000`
- `listening_rate = 25`
- `tags = hex;sec;real;same;...`

元CSVの1行目は、少なくとも3列あることを要求する。先頭3列は `oscillator_id`, `time`, `Message` に変換する。

## 10. 解析仕様

### 10.1 共通前処理

送信ログの時刻:

- `sec` タグがある場合、`time` と `transmission_end_time` を1000倍してmsへ変換する。
- `transmission_end_time` が存在し値が入っている場合、解析用の `detection_time` はそれを使う。
- `transmission_end_time` がない場合、または欠損の場合は `time` を使う。

振動子ID:

- `hex` タグがある場合、`oscillator_id` を16進数として整数化する。
- それ以外は10進整数として整数化する。

### 10.2 周期データ作成

CLI:

```powershell
uv run research-program calculate-cycle-data
```

入力:

- `data/runs/*/metadata.csv`
- `data/runs/*/send_log.csv`

出力:

- `data/runs/*/calculated_Cycle_data.csv`

参照振動子:

- `tags` に `fix_ref_<N>` があれば `N` を使う。
- なければ、最初の `detection_time` を持つ行の `oscillator_id` を使う。

欠損周期の補間:

- 参照振動子の連続送信間隔が `cycle_time * 1.3` 以上なら、周期欠損とみなす。
- `round(gap / cycle_time) - 1` 個の周期開始時刻を補間する。

### 10.3 位相ギャップ誤差作成

CLI:

```powershell
uv run research-program calculate-phase-gap-error
```

入力:

- `metadata.csv`
- `send_log.csv`
- `calculated_Cycle_data.csv`

`calculated_Cycle_data.csv` がなければ自動作成を試みる。

出力:

- `phase_gap_error.csv`

計算:

1. 各送信を `cycle_start_time` の区間へ割り当てる。
2. 同一cycle内で同じ振動子が複数回送信した場合、最初の1回だけ使う。
3. 位相を `2π * ((detection_time - cycle_start_time) / cycle_length)` で計算し、`[0, 2π)` に正規化する。
4. 位相を昇順に並べ、隣接差分と最後から最初へのwrap差分を計算する。
5. 理想間隔 `2π / device_count` との差の絶対値平均を求める。
6. その値を理想間隔で割り、比率列も出力する。

`device_count` は `<N>dai` タグから取得する。このタグがないrunは位相ギャップ誤差計算に失敗する。

### 10.4 位相ギャップ誤差の集約

CLI:

```powershell
uv run research-program aggregate-phase-gap-error
```

入力:

- `data/runs/*/metadata.csv`
- `data/runs/*/phase_gap_error.csv`

`phase_gap_error.csv` がなければ自動作成を試みる。

出力:

- `data/aggregated/<coupling_function>_<coupling_strength>.csv`

集約単位:

- `coupling_function`
- `coupling_strength`
- `cycle_index`

出力統計:

- `count`
- `mean`
- `min`
- `max`
- `median`
- `std`
- `q25`
- `q75`

### 10.5 PER計算

PERは指定された周期窓幅で、次の式により計算する。

```text
expected_packets = device_count * window_width_cycles
success_ratio = actual_send_count_in_window / expected_packets
PER[%] = (1 - success_ratio) * 100
```

K-axis plots label the tick values as `tick × -0.0001` to show the simulation experiment parameter `strength_ratio = -0.0001`.

PERは0未満にならないようクリップする。

## 11. グラフ生成仕様

グラフ生成は主に `outputs/figures/` 配下へPDFとして保存する。Web UIの画像一覧はPDF以外にPNG/JPEG/WebP/SVGも扱える。

| CLIコマンド | 主な入力 | 主な出力 | 内容 |
| --- | --- | --- | --- |
| `plot-phase-diff` | `send_log.csv`, `calculated_Cycle_data.csv` | `outputs/figures/phase_diff_graphs/*.pdf` | 参照振動子との位相差。 |
| `plot-phase-gap-error` | `phase_gap_error.csv` | `outputs/figures/phase_gap_error_graphs/*.pdf` | 位相ギャップ誤差と比率。 |
| `plot-per` | `send_log.csv`, `calculated_Cycle_data.csv` | `outputs/figures/per_graphs/*.pdf` | runごとのPER推移とPER変化量。 |
| `plot-per-aligned` | 複数runのPER | `outputs/figures/per_aligned_graphs/*` | 基準cycleでそろえたPER比較。 |
| `compare-per` | 複数runのPER | `outputs/figures/compare_per_graphs/*` | デバイス数・送信間隔ごとのPER比較。 |
| `compare-per-by-coupling-strength` | 複数runのPER | `outputs/figures/per_by_coupling_strength_graphs/*` | 指定時刻のPERを結合強度ごとに比較。 |
| `plot-per-timing-k-heatmap` | 複数runのPER | `outputs/figures/per_timing_k_heatmaps/*.pdf` | PER timingと結合強度KごとのPERヒートマップ。`show_per_contour_line = true` の場合、各Kについて `per_contour_level` [%] 以下になる最小PER timingをマーカーで重ね描きする。 |
| `plot-aggregated-phase-gap-error` | `data/aggregated/*.csv` | `outputs/figures/aggregated_stats_graphs/*.pdf` | 集約済み位相ギャップ誤差。 |
| `plot-aggregated-phase-gap-error-overlay` | `data/aggregated/*.csv` | `outputs/figures/aggregated_stats_overlay_graphs/*` | 集約済み位相ギャップ誤差の重ね描き。 |
| `plot-convergence-summary` | `data/aggregated/*.csv` | `outputs/figures/convergence_graphs/*` | 収束cycleと収束後変動の要約。 |

`plot-per-timing-k-heatmap` のPER levelマーカーオプションは、`PerTimingCouplingStrengthHeatmapConfig` で指定する。`show_per_contour_line` はマーカー表示のON/OFF、`per_contour_level` は閾値N[%]、`per_contour_color` はマーカー色、`per_level_marker_size` はマーカーサイズ、`per_level_marker_style` はマーカー形状、`show_per_contour_label` は凡例表示、`per_contour_label_font_size` は凡例フォントサイズである。各Kについて、集計済みPERがN%以下になる最小のPER timingだけを1点描画し、点同士は線で結ばない。`show_min_per_timing_annotation = true` の場合は、そのマーカー群のうちPER timingが最小になる点を星印で強調し、K値とtimingを注釈表示する。同じtimingの点が複数ある場合はKが小さい点を採用する。例えばPER 0%以下になる最小timingを描く場合は `show_per_contour_line = true`, `per_contour_level = 0.0` を指定する。このオプションは表示だけを変更するため、同じtiming範囲・step・PER窓幅で作成済みの集計CSVがある場合は、Web UIの再描画または `RESEARCH_PROGRAM_STYLE_ONLY_REDRAW=1` で再集計せずに反映できる。

位相差グラフはデフォルトでは `send_log.csv` の実送信時刻のみを使う。`VISUALIZE_PHASE_DIFF_CONFIG.include_skipped_send_times = true` の場合だけ、`carrier_sense_log.csv` の `action = skip_busy` 行を送信予定時刻として合成して位相差計算に含める。

グラフ生成時に必要な前処理:

| グラフ | 前処理 |
| --- | --- |
| 位相差 | `calculate-cycle-data` |
| 位相ギャップ誤差 | `calculate-phase-gap-error` |
| PER | `calculate-cycle-data` |
| 集約系グラフ | `calculate-phase-gap-error`, `aggregate-phase-gap-error` |

## 12. CLI仕様

CLIエントリポイント:

```powershell
uv run research-program --help
```

コマンド一覧:

| コマンド | 仕様 |
| --- | --- |
| `describe-data-format` | データ契約をJSONで出力する。 |
| `list-runs` | Web設定のrun探索対象からrun一覧を表示する。 |
| `run-simulation` | TOML設定からシミュレーションを実行する。 |
| `clear-experiment-outputs` | 生成データ削除。既定はドライラン。 |
| `import-raw-data` | 実機CSVをrun形式へ変換する。 |
| `calculate-cycle-data` | 周期データを作成する。 |
| `calculate-phase-gap-error` | 位相ギャップ誤差を作成する。 |
| `aggregate-phase-gap-error` | 位相ギャップ誤差を集約する。 |
| `compare-per` | デバイス数・送信間隔別PER比較を作成する。 |
| `compare-per-by-coupling-strength` | 結合強度別PER比較を作成する。 |
| `plot-per-timing-k-heatmap` | PER timingと結合強度KごとのPERヒートマップを作成する。必要に応じて、各KでPERがN%以下になる最小timingをマーカーで重ね描きする。 |
| `plot-phase-diff` | 位相差グラフを作成する。 |
| `plot-phase-gap-error` | 位相ギャップ誤差グラフを作成する。 |
| `plot-per` | PERグラフを作成する。 |
| `plot-per-aligned` | 基準cycle整列PERグラフを作成する。 |
| `plot-aggregated-phase-gap-error` | 集約済み位相ギャップ誤差グラフを作成する。 |
| `plot-aggregated-phase-gap-error-overlay` | 集約済み位相ギャップ誤差の重ね描きを作成する。 |
| `plot-convergence-summary` | 収束要約グラフを作成する。 |

## 13. Web UI仕様

起動:

```powershell
uv run streamlit run src/research_program/web/app.py
```

Windows用補助起動ファイル:

```text
run_streamlit_app.bat
```

Web UI起動時は、`data/`, `outputs/` と標準サブディレクトリを自動作成する。対象は `data/runs`, `data/run`, `data/aggregated`, `data/archives/temp`, `data/raw/real`, `data/raw/simulation`, `outputs/figures`, `outputs/reports`, `outputs/reports/simulation_jobs`, `outputs/reports/graph_creation_jobs` である。加えて、`configs/web/default.toml` の `runs_dirs`, `aggregated_dirs`, `figure_dirs` に指定されたディレクトリも存在しなければ作成する。Gitで空ディレクトリを保持する対象には `.gitkeep` も作成する。既存ディレクトリや既存ファイルは上書きしない。

### 13.1 ページ

| ページ | 仕様 |
| --- | --- |
| Runs | run一覧の探索、条件フィルタ、件数表示、簡易位相ギャップ誤差グラフ作成。 |
| Simulation | シミュレーション条件入力、一括sweep、実行前確認、バックグラウンド実行、ジョブ監視。 |
| Graph creation | グラフ種別選択、対象run選択、前処理計画、プロット設定上書き、バックグラウンド実行、ジョブ監視。 |
| Figures | 画像一覧、フィルタ、プレビュー、ダウンロード。PDFはPNG/JPEG/WebPへラスター化して表示・保存できる。選択画像に対応する実験パラメーター、複数runの値域、初期位相の候補範囲と選択範囲も表示する。 |
| Server | サーバー環境のOS、CPUコア数、メモリ容量、GPU情報を表示する。NVIDIA GPUでは `nvidia-smi` から使用率、VRAM、電力、クロックも表示する。 |
| Maintenance | 生成データ削除、`NONE` run削除。 |
| Data format | データ契約の表示。 |

### 13.2 run探索

- run一覧は `outputs/reports/run_index.json` にインデックス化する。
- runルートのmtimeとmetadataのmtime/sizeが変わっていなければ、metadataを読み直さずインデックスから復元する。
- 通常更新と深い再スキャンをUIで選べる。

### 13.3 シミュレーション実行

- Web UIはユーザー入力から `SimulationRequest` を作成する。
- 実行前に自動付与タグ、実効キャリアセンス時間、LoRa airtime、実効ワーカー数などを確認表示する。
- 実行した条件は `outputs/reports/last_simulation_request.json` に保存し、次回初期値として使う。
- 実行はバックグラウンドジョブとして開始する。

### 13.4 グラフ作成

- 全グラフ一括作成、またはグラフ種別ごとのページ作成ができる。
- 対象runはフィルタ結果全体、または個別選択で指定できる。
- 一部runだけを対象にする場合、ジョブは一時作業ディレクトリへ対象runをコピーし、環境変数で入力ディレクトリを切り替える。
- プロット設定の上書き値は `outputs/reports/last_graph_plot_overrides.json` に保存される。

## 14. バックグラウンドジョブ仕様

### 14.1 シミュレーションジョブ

保存先:

```text
outputs/reports/simulation_jobs/<job_id>.json
```

主な状態:

- `queued`
- `running`
- `completed`
- `failed`

主な項目:

- `job_id`
- `pid`
- `created_at`, `started_at`, `updated_at`, `finished_at`
- `total_conditions`
- `total_runs`
- `completed_runs`
- `current_condition`
- `current_run_id`
- `requests`
- `results`
- `error`

`results` の各run結果には、従来の `run_id`, `output_dir`, `elapsed_sec` に加えて、保存形式と保存時間・出力量の計測値を含める。`storage_kind` は `sqlite` または `directory` である。`simulation_elapsed_sec` はイベント処理本体の時間、`save_elapsed_sec` はSQLite/CSV保存時間、`total_elapsed_sec` はその合計である。`send_log_rows`, `asleep_log_rows`, `carrier_sense_log_rows`, `metadata_rows`, `total_event_log_rows`, `total_csv_data_rows` はヘッダーを除いたデータ行数相当である。CSV保存では `send_log_bytes`, `asleep_log_bytes`, `carrier_sense_log_bytes`, `metadata_bytes`, `total_output_bytes` にファイルサイズを入れる。SQLite保存では `sqlite_store_bytes` と `total_output_bytes` にSQLite storeのサイズを入れる。

### 14.2 グラフ作成ジョブ

保存先:

```text
outputs/reports/graph_creation_jobs/<job_id>.json
```

主な状態:

- `queued`
- `running`
- `completed`
- `completed_with_errors`
- `failed`

主な項目:

- `commands`
- `selected_graph_commands`
- `total_commands`
- `completed_commands`
- `current_command`
- `selected_run_paths`
- `env_overrides`
- `generated_or_updated_figures`
- `results`
- `error`

グラフ作成ジョブは `sys.executable -m research_program.cli <command>` をサブプロセスで実行する。ジョブ実行時は `PYTHONPATH` に `src` を追加し、`MPLBACKEND=Agg` を設定する。

## 15. 環境変数

| 環境変数 | 用途 |
| --- | --- |
| `RESEARCH_PROGRAM_RUNS_DIR` | 解析・グラフ生成のrun入力先を上書きする。 |
| `RESEARCH_PROGRAM_AGGREGATED_DIR` | 集約データの入出力先を上書きする。 |
| `RESEARCH_PROGRAM_FORCE_RECALCULATE` | `1` の場合、既存CSVを優先せず再計算する処理がある。 |
| `RESEARCH_PROGRAM_PLOT_OVERRIDES` | プロット設定dataclassの一部値をJSONで上書きする。 |

## 16. 削除仕様

CLI:

```powershell
uv run research-program clear-experiment-outputs
```

既定ではドライランであり、実際に削除するには `--yes` が必要。

既定削除対象:

- `data/runs`
- `data/aggregated`
- `outputs/figures`

追加で指定可能:

- `outputs/reports`
- `data/raw/real`
- `data/raw/simulation`

安全仕様:

- プロジェクトルート自体は削除しない。
- プロジェクト外パスは削除しない。
- `.gitkeep` は削除しない。
- `data/raw/real` は既定削除対象ではない。
- Web UIの `NONE` run削除はmetadataの `coupling_function` が `NONE` のrunのみを対象にし、削除前に確認入力を要求する。

## 17. 代表的なワークフロー

### 17.1 シミュレーションからグラフ作成

```powershell
uv run research-program run-simulation
uv run research-program calculate-cycle-data
uv run research-program calculate-phase-gap-error
uv run research-program aggregate-phase-gap-error
uv run research-program plot-phase-diff
uv run research-program plot-phase-gap-error
uv run research-program plot-per
uv run research-program plot-aggregated-phase-gap-error-overlay
```

### 17.2 実機データ取り込みから解析

```powershell
uv run research-program import-raw-data
uv run research-program calculate-cycle-data
uv run research-program calculate-phase-gap-error
uv run research-program plot-per
```

### 17.3 Web UIでの運用

1. `uv run streamlit run src/research_program/web/app.py` で起動する。
2. `Simulation` ページで条件を設定してrunを作成する。
3. `Runs` ページで条件に合うrunを確認する。
4. `Graph creation` ページで必要な前処理とグラフ作成を実行する。
5. `Figures` ページで生成物を確認・ダウンロードする。
6. 不要な生成物は `Maintenance` から削除する。

## 18. エラー・制約

- `run-simulation` は `num_runs < 1`, `device_count < 1`, 未対応の `simulation_mode`, ランダム開始候補不足、負の `carrier_sense_duration_ms` をエラーにする。
- `fixed_start_times` は `device_count` 個でなければエラーになる。
- `LoRaAirtimeConfig` はSF、帯域幅、符号化率、プリアンブル長などを検証する。
- `calculate-phase-gap-error` は `<N>dai` タグがないrunでデバイス数を取得できない。
- 実機CSV取り込みはUTF-8 CSVを前提にしている。
- 多くの後処理は `data/runs` 直下の各ディレクトリをrunとして扱う。
- Windows環境ではバックグラウンドジョブ実行中のJSON置換で一時的なPermissionErrorが起き得るため、atomic writeはリトライする。

## 19. 拡張仕様

### 19.1 結合関数追加

結合関数を追加する場合は、少なくとも次を更新する。

- `src/research_program/simulation/coupling_functions.py`
  - `CouplingFunction`
  - `COUPLING_FUNCTION_MAP`
- Web UIの `COUPLING_FUNCTION_OPTIONS`
- READMEまたは本仕様書
- 必要に応じてプロット表示名、色、フィルタ設定

### 19.2 runデータ列追加

runデータ形式を拡張する場合は、次を更新する。

- `configs/data_format/run_v1.toml`
- 読み取り処理がある場合は `io/run_store.py` や解析モジュール
- Web UIの表示ラベルが必要なら `RUN_COLUMN_LABELS` や `CONTRACT_COLUMN_LABELS`

### 19.3 グラフ種別追加

グラフを追加する場合は、次を更新する。

- `src/research_program/plotting/` または `analysis/` に実行モジュールを追加
- `src/research_program/cli.py` の `MODULE_COMMANDS`
- `src/research_program/web/app.py` の `GRAPH_CREATION_COMMANDS`
- 必要なら `GRAPH_PREPROCESS_REQUIREMENTS`
- 必要なら `PLOT_CONFIG_BY_GRAPH_COMMAND`
- 出力ディレクトリに対応する表示ラベル、スコープ、説明

## 20. 検証コマンド

仕様書作成時点で確認した基本コマンド:

```powershell
uv run research-program --help
uv run research-program describe-data-format
```

追加で動作確認する場合の例:

```powershell
uv run research-program list-runs
uv run research-program run-simulation
uv run research-program calculate-cycle-data
uv run research-program calculate-phase-gap-error
uv run research-program aggregate-phase-gap-error
```

生成物を削除する場合は、先にドライランを確認する。

```powershell
uv run research-program clear-experiment-outputs
uv run research-program clear-experiment-outputs --yes
```
