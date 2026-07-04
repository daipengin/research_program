# research_program

Graph-first workflow for oscillator network simulations and PER graph generation.

現在の実装は、Web GUIから「作りたいグラフ」を先に決め、そのグラフに必要なシミュレーション、集計、PDF描画を1つのジョブとして実行する構成です。旧来の「先に大量のrunを作って、あとからrunを選んでグラフ化する」構成はアーカイブ済みです。

## 現在の対象

実装済みのグラフ種:

- Interval PER vs K

設計上の基本単位:

- 1 job = 1 completed graph
- 1 graph folder = 1 jobの保存先
- Interval PER vs Kでは、1 job内で複数K値と複数runを実行し、Kごとの平均PERを描画する
- coupling functionを複数比較したい場合は、coupling functionごとに別job、別graph folderを作成する

## 起動方法

```powershell
uv run streamlit run src/research_program/web/app.py
```

または:

```powershell
run_streamlit_app.bat
```

CLIで現在のgraph-first workspace概要を確認する場合:

```powershell
uv run research-program
```

## 現在のディレクトリ構成

```text
src/research_program/
  web/              Streamlit Web GUI
  graph_workflow/   graph-first job作成、保存、実行、削除
  simulation/       シミュレーター本体
  analysis/         周期データ作成などの軽量分析処理
  plotting/         Interval PER集計に必要な既存補助関数
  io/               SQLite run保存、send_log正規化
  config/           パスとplot設定

outputs/
  graph_runs/       graph-first jobの保存先
  settings/         Web GUIで最後に使ったパラメーター

archive/
  legacy_programs_*/
  non_current_simulator_*/
  docs_legacy_*/    古いREADMEなど
```

`data/` と `outputs/` は生成データ領域です。`.gitignore` により、シミュレーション結果やSQLiteファイルはgit同期対象から外しています。

## Web GUI

現在のWeb GUIは4ページ構成です。

```text
1. ジョブ追加
2. ジョブ確認
3. 結果・グラフ確認
4. その他管理
```

### ジョブ追加

Interval PER vs K用のjobを追加します。

入力できる主な項目:

- coupling function
- K start / K stop / K step
- runs per K
- interval start / interval end
- simulation duration
- cycle time
- seed
- device count
- listening rate
- strength ratio
- max workers
- carrier sense duration
- LoRa settings
- plot settings

時間入力は `ms`, `sec`, `min` を選べます。内部保存はmsです。

`max workers = 0` の場合は自動最大化です。実際のworker数は `min(CPU論理コア数, そのKで実行するrun数)` になります。

ジョブ追加前に `Preview airtime and run count` で以下を確認できます。

- LoRa airtime
- symbol time
- LDRO
- K点数
- total runs
- interval
- simulation duration

`Add job` を押すと、graph folderを作成し、シミュレーション、SQLite保存、集計、PDF描画を順に実行します。

### ジョブ確認

作成済みjobを一覧表示します。

表示する主な情報:

- graph_id
- graph_key
- status
- completed runs / total runs
- aggregate count
- updated time
- graph folder path

操作:

- `Refresh status`: 表示更新
- `Run`: queued jobを実行
- `Cancel`: queued/running jobに中止要求
- `Delete history/data`: graph folderを完全削除

現在の中止処理は、同期実行中のrunをOSレベルで即時killする方式ではありません。実行側が中止要求を確認し、run完了直後または次run開始前に停止します。中止されたrunは `raw_run.sqlite` から削除します。

### 結果・グラフ確認

完了済みまたは確認可能なgraph folderを選択して、結果を確認します。

表示する主な情報:

- status
- total runs
- aggregate sets
- graph_type
- graph_key
- K範囲
- K点数
- runs per K
- interval start/end
- per method
- raw run store
- aggregate data table
- representative PDF

操作:

- PDF download
- aggregate済みデータからPDF再描画
- graph folder完全削除

通常の再描画ではシミュレーション生データを読み直さず、`graph_data.sqlite` の集計済みデータを使います。再描画結果は代表PDFを上書きします。

### その他管理

保存形式とサーバー環境を確認します。

表示する主な情報:

- graph folder数
- graph SQLite file数
- raw SQLite file数
- storage layout
- CPU core数
- memory
- disk free
- platform / Python / machine / processor

## 保存形式

graph-first jobは以下に保存します。

```text
outputs/graph_runs/<graph_type>/<graph_id>/
  manifest.json
  status.json
  requests.json
  graph_data.sqlite
  raw_run.sqlite
  figures/
  logs/
```

### graph_data.sqlite

集約、描画、job管理用のSQLiteです。主なテーブル:

```text
graph_meta
simulation_requests
runs
run_cycle_counts
run_interval_per
aggregate_sets
aggregate_interval_per
plot_settings
outputs
history
```

役割:

- jobとgraphのメタデータ
- K値、run数、入力パラメーター
- run単位の軽量メタデータ
- cycleごとの送信数
- interval PER
- Kごとの平均PER、標準偏差、min/max、count
- plot settings
- 代表PDFのパス
- history

### raw_run.sqlite

シミュレーション生データ保存用のSQLiteです。`graph_data.sqlite` とは分離しています。

主な内容:

- run metadata
- send_log
- asleep_log
- carrier_sense_log
- calculated_cycle_data
- phase_gap_error

現在の通常導線では、runごとのCSVフォルダを作らず、`raw_run.sqlite` に保存します。これによりファイル数を減らします。

## Interval PER vs K の処理

処理フロー:

```text
1. graph folder作成
2. manifest/status/requests作成
3. graph_data.sqlite初期化
4. raw_run.sqlite初期化
5. K値ごとにruns per K回シミュレーション
6. raw_run.sqliteへrun保存
7. graph_data.sqliteへrun metadata保存
8. cycle count作成
9. interval PER作成
10. Kごとの平均PER集計
11. representative PDF描画
12. outputs/history/status/manifest更新
```

PER計算:

- 対象intervalに入るcycleを抽出
- expected packets = interval cycle count x device count
- actual packets = 対象cycleに割り当たったsend数
- PER[%] = `(1 - actual / expected) * 100`

Kごとに複数runのPERを集約し、以下を保存します。

- mean
- std
- min
- max
- expected packet sum
- actual packet sum
- count

## 初期タイミング

通常の `random` start timing:

- `0, start_step, 2 * start_step, ..., start_step_count * start_step` から重複なしで選択

Interval PER vs K用の現在のデフォルト:

- `random_cycle_ms_with_replacement`
- 1 cycle内の `0` から `cycle_time - 1` msまでを1ms刻みで候補にする
- device_count個を重複ありでランダム選択

この選び方はInterval PER vs Kのgraph workflow側で指定しています。他のグラフ種を追加する場合は、そのグラフ種ごとに初期タイミング方針を決めます。

## coupling function

現在の選択肢は、シミュレーター側の `CouplingFunction` enumから取得します。

- `KURAMOTO`
- `LINEAR`
- `NewSIN`
- `NONE`

## LoRa / PER measurement

LoRa airtimeは以下の入力から計算します。

- payload bytes
- spreading factor
- bandwidth Hz
- coding rate denominator
- preamble symbols
- explicit header
- CRC enabled
- low data rate optimize

Interval PER vs Kでは `simulation_mode = "per_measurement"` 固定です。LoRa airtimeを送信時間として使います。`standard` はこのgraph workflowでは選択できません。

## 現在アーカイブ済みの旧機能

旧READMEや旧Web GUI/旧CLI系のコードは `archive/` に退避しています。

代表例:

- 旧run選択型Web GUI
- 旧graph creationページ
- 旧run一覧/index機能
- 旧plot CLI群
- 旧cleanup/archive CLI群
- 旧configs
- 旧仕様書の一部

## 実装と設計の差分・未実装

このREADMEは現在の実装を基準にしています。ただし、`WEB_GUI_REDESIGN_SPEC.md` にある将来構想のうち、以下は未実装または一部実装です。

### 未実装

- jobを別プロセスで実行し、Web UIを閉じても完全にバックグラウンド継続する仕組み
- running中runのOSレベル即時停止
- 中止時にgraph folder全体を自動で完全削除する処理
- 終了予定時刻、残り時間、平均run時間の推定表示
- job historyの詳細表示
- 複数aggregate_setの選択UI
- interval変更など、集計条件変更による新しいaggregate_set作成UI
- データ追加UI
- データ追加時に既存aggregateを物理削除して再集計する処理
- graph folder内の条件単位データ削除
- graph_data.sqlite schemaのWeb上詳細表示
- cache確認/整理UI
- GPU情報表示
- PDFのWeb埋め込みプレビュー
- 代表PDF以外の画像形式出力
- 他グラフ種

### 一部実装

- job cancel
  - UIとstatus更新は実装済み
  - 実行中runの即時killは未実装
  - run完了後または次run前の停止
- redraw
  - 集計済みデータからPDF上書きは実装済み
  - aggregate_set選択やinterval変更再集計は未実装
- その他管理
  - CPU/memory/disk/platform表示は実装済み
  - data format詳細やmaintenance操作は未実装
- raw data保存
  - `raw_run.sqlite` 保存は実装済み
  - 旧CSV run folderの通常出力は使わない

## 開発メモ

現行コードで重要なファイル:

```text
src/research_program/web/app.py
src/research_program/graph_workflow/storage.py
src/research_program/graph_workflow/execution.py
src/research_program/simulation/runner.py
src/research_program/simulation/scheduler.py
src/research_program/io/sqlite_runs.py
```

動作確認:

```powershell
uv run python -m compileall src\research_program
uv run research-program
uv run streamlit run src/research_program/web/app.py
```
