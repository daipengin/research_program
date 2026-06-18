# research_program

研究で扱うシミュレーション、実機データ処理、Web可視化のためのプロジェクトです。

## 構成

```text
configs/
  data_format/      run データ形式の定義
  experiments/      シミュレーション条件
  web/              Web画面が読む設定
data/
  raw/              実機・シミュレーションの元データ
  runs/             共通形式に変換済みの run データ
  aggregated/       統計処理後の集約データ
outputs/
  figures/          Webやスクリプトから生成した画像
  reports/
src/research_program/
  simulation/       シミュレーション実行
  io/               CSV、metadata、画像、データ形式
  analysis/         統計処理
  plotting/         グラフ生成
  web/              Streamlit Web UI
```

既存の `make_simulation_data/` は移行元として残しています。新しいWeb画面は、標準の `data/runs/` と既存の `make_simulation_data/results/` の両方を読めます。

## データ形式

run データ形式は [configs/data_format/run_v1.toml](configs/data_format/run_v1.toml) に明示しています。

1つの run は次のようなディレクトリです。

```text
data/runs/<run_id>/
  metadata.csv
  send_log.csv
  calculated_Cycle_data.csv
  phase_gap_error.csv
```

`metadata.csv` と `send_log.csv` が必須です。`calculated_Cycle_data.csv` と `phase_gap_error.csv` は後処理で作る派生データです。

## Web UI

```powershell
uv run streamlit run src/research_program/web/app.py
```

Web UI では次を扱えます。

- シミュレーション条件を変更して実行
- run データを条件で絞り込み
- 条件に合う run 数を表示
- 絞り込んだ run だけで phase gap error グラフを生成
- 生成グラフを `png`, `pdf`, `svg` でダウンロード
- 既存の結果画像を一覧表示、プレビュー、ダウンロード

## CLI

```powershell
uv run research-program describe-data-format
uv run research-program list-runs
uv run research-program run-simulation
```

シミュレーション条件は [configs/experiments/default_simulation.toml](configs/experiments/default_simulation.toml) で変更できます。
