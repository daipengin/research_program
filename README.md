# research_program

研究で扱うシミュレーションデータ作成、実機データ処理、統計処理、グラフ作成、Web可視化をまとめたプロジェクトです。

実行するPythonコードは `src/research_program` の中に統合されています。

## ディレクトリ構成

```text
configs/
  data_format/      runデータ形式の定義
  experiments/      シミュレーション条件
  web/              Web画面の設定
data/
  raw/real/         実機の元CSVデータ
  raw/simulation/   シミュレーション用の元データ
  runs/             共通形式に変換・生成されたrunデータ
  aggregated/       統計処理後の集約データ
outputs/
  figures/          作成したグラフ画像
  reports/          ログやレポート
src/research_program/
  simulation/       シミュレータ本体
  io/               CSV読み書き、run探索、画像探索
  analysis/         周期データ作成、位相誤差計算、統計処理
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
  phase_gap_error.csv
```

`metadata.csv` と `send_log.csv` は必須です。`calculated_Cycle_data.csv` と `phase_gap_error.csv` は後処理で作成される派生データです。

## シミュレータ

シミュレータ本体は `src/research_program/simulation/` にあります。振動子、結合関数、イベントスケジューラ、複数条件の実行処理をこの中で扱います。

主な設定項目は [configs/experiments/default_simulation.toml](configs/experiments/default_simulation.toml) で変更できます。

- `coupling_function`: 結合関数。例: `KURAMOTO`, `LINEAR`, `NewSIN`
- `coupling_strength`: 結合強度
- `strength_ratio`: 位相補正量の倍率
- `cycle_time`: 1周期の長さ
- `listening_rate`: 受信待機時間の割合
- `device_count`: 振動子数
- `duration`: シミュレーション時間
- `num_runs`: 同じ条件で作成するrun数
- `seed`: 乱数シード

CLIから実行する場合:

```powershell
uv run research-program run-simulation
```

Webから実行する場合:

```powershell
uv run streamlit run src/research_program/web/app.py
```

Web UIの `Simulation` タブから、結合関数、結合強度、周期、振動子数などを変えながらシミュレーションを実行できます。出力は標準で `data/runs/` に保存されます。

## 実機データの取り込み

実機CSVは `data/raw/real/` に置きます。共通run形式へ変換するには次を実行します。

```powershell
uv run research-program import-raw-data
```

変換後のデータは `data/runs/<run_id>/` に保存され、シミュレーション結果と同じ後処理・可視化の対象になります。

## 統計処理

周期データ、位相ギャップ誤差、集約統計は `src/research_program/analysis/` にあります。

代表的な処理:

```powershell
uv run research-program calculate-cycle-data
uv run research-program calculate-phase-gap-error
uv run research-program aggregate-phase-gap-error
```

処理の流れは次の通りです。

```text
send_log.csv
  -> calculated_Cycle_data.csv
  -> phase_gap_error.csv
  -> data/aggregated/*.csv
```

## グラフ作成

グラフ作成処理は `src/research_program/plotting/` にあります。`data/runs/` や `data/aggregated/` のデータを読み、結果を `outputs/figures/` に保存します。

代表的なグラフ作成コマンド:

```powershell
uv run research-program plot-phase-diff
uv run research-program plot-phase-gap-error
uv run research-program plot-per
uv run research-program plot-per-aligned
uv run research-program plot-aggregated-phase-gap-error
uv run research-program plot-aggregated-phase-gap-error-overlay
uv run research-program plot-convergence-summary
```

作成できる主なグラフ:

- 周期ごとの位相差グラフ
- 位相ギャップ誤差グラフ
- PERグラフ
- 複数runを基準周期でそろえたPER比較グラフ
- 結合関数・結合強度ごとの集約統計グラフ
- 収束傾向の比較グラフ

Web UIの `Runs` タブでは、条件に合うrun数を確認し、その条件に合うrunだけを使ってグラフを作成できます。`Figures` タブでは、作成済みの画像やPDFを一覧表示し、任意の形式でダウンロードできます。

## Web UI

```powershell
uv run streamlit run src/research_program/web/app.py
```

Web UIでは次を行えます。

- シミュレーション条件を変更して実行
- runデータをパラメータやタグで絞り込み
- 条件に合うrun数を表示
- 絞り込んだrunだけでグラフ作成
- 作成済み画像やPDFの一覧表示
- 結果画像のプレビューとダウンロード
- 実験結果データや画像の削除

## 実験結果の削除

Web UIの `Maintenance` タブから、実験で作成したデータや画像を削除できます。

デフォルトの削除対象:

- `data/runs`
- `data/aggregated`
- `outputs/figures`

`data/raw/real` は実機の元データなので、デフォルトでは削除対象にしていません。Web UIで明示的に選択した場合だけ削除できます。

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

## CLI一覧

```powershell
uv run research-program --help
```

主要コマンド:

```powershell
uv run research-program describe-data-format
uv run research-program list-runs
uv run research-program run-simulation
uv run research-program import-raw-data
uv run research-program calculate-cycle-data
uv run research-program calculate-phase-gap-error
uv run research-program aggregate-phase-gap-error
uv run research-program clear-experiment-outputs
uv run research-program plot-phase-diff
uv run research-program plot-phase-gap-error
uv run research-program plot-per
uv run research-program plot-per-aligned
```
