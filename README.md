# research_program

研究で扱うシミュレーションデータ作成、PER解析、グラフ作成、Web可視化をまとめたプロジェクトです。

実行するPythonコードは `src/research_program` の中に統合されています。

## ディレクトリ構成

```text
configs/
  data_format/      runデータ形式の定義
  experiments/      シミュレーション条件
  web/              Web画面の設定
data/
  raw/simulation/   シミュレーション用の元データ
  runs/             共通形式に変換・生成されたrunデータ
  aggregated/       統計処理後の集約データ
  archives/         いま使わない機能や一時退避データ
outputs/
  figures/          作成したグラフ画像
  reports/          ログ、レポート、run一覧インデックス
  settings/         Web UIで最後に使ったパラメーターや描画設定
src/research_program/
  simulation/       シミュレータ本体
  io/               run探索、画像探索、SQLite/互換データ入出力
  analysis/         周期データ作成、PER集計用の統計処理
  plotting/         グラフ作成
  pipelines/        一連の処理をまとめた実行入口
  web/              StreamlitによるWeb UI
```

## データ形式

runデータ形式は [configs/data_format/run_v1.toml](configs/data_format/run_v1.toml) に定義しています。

1つの実験またはシミュレーション結果は次の形式で保存します。

```text
data/runs/<run_id>/
  metadata.csv
  send_log.csv
  calculated_Cycle_data.csv
```

`metadata.csv` と `send_log.csv` は必須です。シミュレーション時のデフォルト出力もこの2つです。`asleep_log.csv` と `carrier_sense_log.csv` は設定で有効にした場合だけ保存します。`calculated_Cycle_data.csv` はPERグラフ用に作成される派生データです。現在の通常導線ではSQLite保存を基本にし、CSV runディレクトリは互換用として扱います。

## シミュレータ

シミュレータ本体は `src/research_program/simulation/` にあります。振動子、結合関数、イベントスケジューラ、複数条件の実行処理をこの中で扱います。

主な設定項目は [configs/experiments/default_simulation.toml](configs/experiments/default_simulation.toml) で変更できます。

- `coupling_function`: 結合関数。例: `KURAMOTO`, `LINEAR`, `NewSIN`, `NONE`
- `coupling_strength`: 結合強度
- `strength_ratio`: 位相補正量の倍率
- `cycle_time`: 1周期の長さ
- `listening_rate`: 受信待機時間の割合
- `device_count`: 振動子数
- `duration`: シミュレーション時間
- `start_timing_mode`: 開始タイミング。`random` または `fixed`
- `start_step_count`, `start_step`: ランダム開始時刻の候補範囲。`0` から `start_step_count * start_step` までを `start_step` 間隔で扱います。
- `fixed_start_times`: 固定開始時刻を手入力する場合の開始時刻リスト
- `fixed_start_interval`, `fixed_start_offset`: 固定開始時刻を一定間隔プリセットで作る場合の間隔とオフセット
- `simulation_mode`: シミュレーションモード。`standard` または `per_measurement`
- `carrier_sense_duration_ms`: PER測定時のキャリアセンス時間。`0` は0msとして扱い、送信前のキャリアセンス区間を見ません。
- `lora_payload_bytes`: LoRa送信時間計算に使うペイロード長
- `lora_spreading_factor`: LoRa送信時間計算に使うSF
- `lora_bandwidth_hz`: LoRa送信時間計算に使う帯域幅
- `lora_coding_rate_denominator`: LoRa符号化率の分母。`5` は `4/5`、`8` は `4/8`
- `lora_preamble_symbols`: LoRaプリアンブル長
- `lora_explicit_header`, `lora_crc_enabled`, `lora_low_data_rate_optimize`: LoRa送信時間計算に使うヘッダー、CRC、LDRO設定
- `save_asleep_log`: `asleep_log.csv` を保存するか。デフォルトは `false` です。
- `save_carrier_sense_log`: `carrier_sense_log.csv` を保存するか。デフォルトは `false` です。
- `num_runs`: 同じ条件で作成するrun数
- `seed`: 乱数シード
- `max_workers`: 並列実行数。`0` にすると実行数とCPU数から自動で最大値を使います。
- `tags`: runに付与するタグ。`20dai` のような台数タグは `device_count` から自動で付与・更新されます。開始タイミングに応じて `start_random` または `start_fixed` も自動で付与されます。

開始タイミングの考え方:

- `random`: 各runで、指定範囲内からデバイス数分の開始時刻をランダムに選びます。runごとに同じ開始時刻セットが重ならないようにします。
- `fixed`: 全runで同じ開始時刻を使います。Web UIでは、一定間隔プリセットまたは手入力を選べます。

ランダム開始では、候補集合 `0, start_step, 2 * start_step, ..., start_step_count * start_step` から、デバイス数ぶんを一様・重複なしで抽出して昇順に並べます。新しく作成するrunの `metadata.csv` には、`random_sampling_method`、`random_seed`、`random_run_index`、候補範囲、候補数、`selected_start_times` を保存します。これにより、どの候補集合から何が選ばれたかをrun単位で確認できます。

PER測定モード:

- `simulation_mode = "per_measurement"` にすると、送信は瞬間ではなくLoRa送信時間ぶんの占有区間として扱います。
- 送信予定時刻の直前に `carrier_sense_duration_ms` で指定したキャリアセンス区間を見て、他デバイスの送信区間と重なっていた場合、そのサイクルでは送信しません。`carrier_sense_duration_ms = 0` の場合、この区間は0msなのでスキップ判定は行いません。
- 実際に送ったものだけが `send_log.csv` に入ります。`save_carrier_sense_log = true` の場合、スキップは `carrier_sense_log.csv` に `skip_busy` として記録されます。
- 送信時間はLoRa airtime式から計算し、実効値は `metadata.csv` の `transmission_time_ms` に保存されます。

結合関数:

- `KURAMOTO`: `sin(phase_diff)` を使います。
- `LINEAR`: 既存の線形補正関数を使います。
- `NewSIN`: 既存のNewSIN補正関数を使います。
- `NONE`: 位相更新量を常に0にします。受信ログは残りますが、受信による次周期の補正は行いません。

LoRa送信時間の式:

```text
T_sym = 2^SF / BW
DE = 1 if T_sym >= 0.016 else 0   # lora_low_data_rate_optimize が auto の場合
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

PER測定モードでは、他ノードの受信・位相更新は送信開始時刻ではなく `transmission_end_time` で行います。キャリアセンスで送信をスキップした場合も、振動子内部の位相補正では、送れていた場合の `transmission_end_time` に相当する時刻を自分側の基準時刻として使います。周期データ、PER、位相差も `transmission_end_time` を検知時刻として使います。古いログなどで `transmission_end_time` が無い場合は `time` を使います。

CLIから実行する場合:

```powershell
uv run research-program run-simulation
```

Webから実行する場合:

```powershell
uv run streamlit run src/research_program/web/app.py
```

Web UIの `Simulation` ページから、結合関数、結合強度、周期、振動子数などを変えながらシミュレーションを実行できます。出力は標準で `data/run/simulation_runs.sqlite` に保存されます。

Web UIでは、シミュレーションを実行する前にパラメーター確認画面が表示されます。確認画面には、自動付与後のタグと実際に使用されるワーカー数も表示されます。`Sweep parameter ranges` を使うと、結合関数や主要な数値パラメータを一定範囲で変化させ、全組み合わせの結果を一括で作成できます。

Web UIから実行したシミュレーションは、バックグラウンドのジョブとして開始されます。ジョブ状態は `outputs/reports/simulation_jobs/` のJSONファイルに保存されるため、ページをリロードした後でも `Simulation` ページの `Running jobs` から進行状況を確認できます。シミュレーション実行中は、完了run数、経過時間、残り時間、終了予測時刻、直近に完了したrunがWeb上に表示されます。

Web UIで最後に成功したシミュレーション条件は `outputs/settings/last_simulation_request.json` に保存され、次回のシミュレーション画面の初期値として使われます。

## 統計処理

周期データなど、PERグラフに必要な前処理は `src/research_program/analysis/` にあります。

代表的な処理:

```powershell
uv run research-program calculate-cycle-data
```

処理の流れは次の通りです。

```text
send_log.csv
  -> calculated_Cycle_data.csv
```

## グラフ作成

グラフ作成処理は `src/research_program/plotting/` にあります。`data/runs/` や `data/aggregated/` のデータを読み、結果を `outputs/figures/` に保存します。既定の保存形式はPDFです。

通常導線のグラフは、原則としてグラフ1枚につき同じstemの描画用CSVを1つ保存します。例えば `outputs/figures/compare_per_graphs/LINEAR_cycle_90.pdf` には `LINEAR_cycle_90.csv` が対応します。複数手法を重ねるグラフも、重ね描き用CSVに加えて、各手法の個別グラフと個別CSVを保存できる構成です。

代表的なグラフ作成コマンド:

```powershell
uv run research-program plot-phase-diff
uv run research-program plot-per
uv run research-program plot-per-aligned
uv run research-program compare-per
uv run research-program compare-per-by-coupling-strength
uv run research-program compare-per-by-coupling-strength-interval
uv run research-program plot-per-timing-k-heatmap
```

作成できる主なグラフ:

- 周期ごとの位相差グラフ
- PERグラフ
- 複数runを基準周期でそろえたPER比較グラフ
- 台数・送信間隔ごとのPER比較グラフ
- 結合関数・結合強度ごとのPER集計グラフ
- 任意の時間区間で数えたPER vs Kグラフ
- PER timing × K ヒートマップ

`compare-per-by-coupling-strength-interval` は、横軸K、縦軸PERのグラフを、任意の時間区間で計算したPERから作成します。対象区間は `[interval_start_ms, interval_end_ms)` で、区間内に開始する周期を対象にし、期待パケット数を `対象周期数 × デバイス数`、到着パケット数を対象周期に割り当たった `send_log.csv` の行数としてPERを計算します。例えば500s〜2000sなら、`interval_start_ms = 500000.0`、`interval_end_ms = 2000000.0` を指定します。描画用CSVとPDFは `outputs/figures/per_by_coupling_strength_interval_graphs/` に、結合関数ごとに同じstemで保存されます。

位相差グラフはデフォルトでは `send_log.csv` の実送信時刻だけを使います。キャリアセンスでスキップした送信予定時刻も含めたい場合は、グラフ設定の `include_skipped_send_times` を有効にします。その場合は `carrier_sense_log.csv` も保存しておく必要があります。

Web UIの `Runs` ページでは、条件に合うrun数を確認できます。`Graph creation` ページでは、上記の画像データをWeb上から選択して作成できます。対象runをパラメーターやタグで絞り込み、既定ではフィルタ後のrunを全て対象にするため、大量のrunを個別選択リストへ展開しません。個別選択が必要な場合だけ、run IDやパスで候補を絞ってから選択できます。必要に応じて、周期データの前処理も同時に実行できます。グラフ作成はバックグラウンドジョブとして開始され、ジョブ状態は `outputs/reports/graph_creation_jobs/` に保存されます。ページをリロードした後でも `Graph creation` ページの `Running graph jobs` から、完了コマンド数、経過時間、残り時間、終了予測時刻を確認できます。`Graph parameters` では、選択したグラフ種類ごとにx軸・y軸範囲、PER計算窓幅、基準周期、画像サイズなどをWeb上から変更できます。PER timingや区間開始・終了などの時間入力は、UI上でms・秒・分を選択でき、内部設定と描画用CSVには従来どおりmsで保存されます。ここで変更してグラフ作成に使った値は `outputs/settings/last_graph_plot_overrides.json` に保存され、次回以降の初期値として再利用されます。

### 再描画

Web UIの `Graph creation > Redraw` では、作成済みの描画用CSVを使って再描画できます。`compare-per`、`compare-per-by-coupling-strength`、`compare-per-by-coupling-strength-interval`、`plot-per-timing-k-heatmap` は、対応するCSVが残っていれば再集計せずにスタイルや表示範囲だけを反映できます。CLIでは次のように実行できます。

```powershell
$env:RESEARCH_PROGRAM_STYLE_ONLY_REDRAW = "1"
uv run research-program plot-per-timing-k-heatmap
```

`Interval PER vs K by coupling function` は、Web UIの `Graph redraw` から区間開始・終了を変更して再描画できます。この場合は既存CSVだけではなくrunログから区間PERを再集計し、指定した時間範囲に対応する新しい描画用CSVとPDFを保存します。

### アーカイブ済み機能

実機CSV取り込みと位相ギャップ誤差系の解析・描画は、現在の通常導線から外しています。必要になった場合だけ、アーカイブ用コマンドとして実行できます。

```powershell
uv run research-program archive-import-raw-data
uv run research-program archive-calculate-phase-gap-error
uv run research-program archive-aggregate-phase-gap-error
uv run research-program archive-plot-phase-gap-error
uv run research-program archive-plot-aggregated-phase-gap-error
uv run research-program archive-plot-aggregated-phase-gap-error-overlay
uv run research-program archive-plot-convergence-summary
```

## Web UI

```powershell
uv run streamlit run src/research_program/web/app.py
```

Web UIでは次を行えます。

- シミュレーション条件を変更して実行
- パラメータ範囲を一括実行
- シミュレーションの進行度と終了予測を表示。ページリロード後も進行中ジョブを確認
- runデータをパラメータやタグで絞り込み
- 条件に合うrun数を表示
- 絞り込んだrunだけでグラフ作成
- 全種類の画像データを選択して作成
- 画像作成に使う対象runを選択。既定ではフィルタ結果を一括対象にして高速化
- グラフ作成時の軸範囲や主要パラメーターを変更
- 画像作成に必要な前処理を実行
- 作成済み描画用CSVからPER系グラフを再描画
- 画像作成の進行度と終了予測を表示。ページリロード後も進行中ジョブを確認
- 作成済み画像やPDFの一覧表示
- 結果画像のプレビューとダウンロード。PDFはラスター化して表示・ダウンロード。選択中の画像に対応する実験パラメーター、複数runの範囲、初期位相の候補範囲と選択範囲も表示
- サーバー環境のCPUコア数、メモリ容量、GPU情報を確認
- 実験結果データや画像の削除

Web UIの高速化:

- 画面はページ切り替え式です。選択中のページで必要なデータだけ読み込みます。
- `data/runs` の一覧は `outputs/reports/run_index.json` にインデックスとして保存します。
- runフォルダ構成が変わっていない場合は、`metadata.csv` を全件読み直さずインデックスから復元します。
- `run一覧を更新(Refresh runs)` は通常更新、`runインデックス再構築(Rebuild run index)` はmetadataを手で編集した後などに使う深い再スキャンです。
- 画像一覧も短時間キャッシュし、`画像一覧を更新(Refresh figures)` で明示的に更新できます。

## 実験結果の削除

Web UIの `Maintenance` タブから、実験で作成したデータや画像を削除できます。

`outputs/reports` はジョブ状態やrunインデックスなどのログ・レポート用です。前回使ったシミュレーション条件や描画設定は `outputs/settings` に保存するため、reportsを削除しても消えません。

デフォルトの削除対象:

- `data/runs`
- `data/aggregated`
- `outputs/figures`

`data/raw/real` はアーカイブ済みの実機元データ置き場です。デフォルトでは削除対象にしていません。Web UIで明示的に選択した場合だけ削除できます。

CLIから確認する場合:

```powershell
uv run research-program clear-experiment-outputs
```

このコマンドはデフォルトではドライランです。実際に削除する場合だけ `--yes` を付けます。

```powershell
uv run research-program clear-experiment-outputs --yes
```

削除対象を絞る例:

```powershell
uv run research-program clear-experiment-outputs --target figures
uv run research-program clear-experiment-outputs --target runs --target aggregated --yes
```

### 一時アーカイブ

Web UIの `Maintenance` では、フィルタしたrunを削除せずに `data/archives/temp/<archive_id>/runs/` へ一時アーカイブできます。アーカイブ時には元のパスを記録した `manifest.json` を保存し、同じ画面から復元できます。

## CLI一覧

```powershell
uv run research-program --help
```

主要コマンド:

```powershell
uv run research-program describe-data-format
uv run research-program list-runs
uv run research-program run-simulation
uv run research-program calculate-cycle-data
uv run research-program clear-experiment-outputs
uv run research-program plot-phase-diff
uv run research-program plot-per
uv run research-program plot-per-aligned
uv run research-program compare-per
uv run research-program compare-per-by-coupling-strength
uv run research-program compare-per-by-coupling-strength-interval
uv run research-program plot-per-timing-k-heatmap
```
