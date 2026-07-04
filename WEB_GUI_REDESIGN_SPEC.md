# Web GUI Redesign Specification

作成日: 2026-07-04

## 1. 目的

現在のシミュレーション、解析、描画ロジックは維持したまま、Web GUIの構成とデータ保存単位を整理する。

従来の「シミュレーションを回す」「既存runからグラフを作る」「作成済みグラフを再描画する」という機能分離ではなく、研究作業で実際に扱う成果物である「グラフ」を中心にした操作体系へ移行する。

基本方針:

- シミュレーションの中身、run生成ロジック、PER計算、既存プロット計算式は変更しない。
- Web GUIは4ページ構成に整理する。
- 1ジョブは原則として1つの完成グラフを作成する。
- データ保存はグラフ種ごとに分類し、その下に1つの完成グラフごとのフォルダを作る。
- 保存形式はCSVファイル大量生成ではなく、SQLiteを基本にしてファイル数を減らす。
- 新しいgraph-firstワークフローでは、既存runデータや既存キャッシュを使ってグラフを作り直すことはしない。必要なデータはジョブ作成後に新しく生成する。
- グラフ確認ページから、データ追加、削除、再描画を行えるようにする。

## 2. 対象範囲

対象:

- Streamlit Web GUIの画面構成
- ジョブの作成、状態確認、履歴管理
- 結果・グラフ確認ページ
- グラフ単位の保存ディレクトリ構造
- 既存シミュレーション/解析/描画機能を呼び出す新しいワークフロー

対象外:

- シミュレーションモデルの数式変更
- coupling functionの中身変更
- LoRa airtime計算の変更
- 既存runデータ形式そのものの破壊的変更
- 既存CLIの即時廃止

## 3. 新しいWeb GUI構成

Web GUIは以下の4ページに整理する。

```text
1. ジョブ追加
2. ジョブ確認
3. 結果・グラフ確認
4. その他管理
```

### 3.1 ジョブ追加

目的:

作成したい成果物を先に選び、その成果物に必要なシミュレーションや解析をジョブとして追加する。

主な機能:

- 作成するグラフ種の選択
- グラフ種ごとの入力フォーム表示
- 必要なシミュレーション条件の入力
- 必要なK範囲、結合関数、run数、時間範囲などの入力
- 予想される条件数、総run数、出力先フォルダの表示
- ジョブ登録

初期対応グラフ種:

- Interval PER vs K by coupling function

将来追加するグラフ種の例:

- PER vs K by coupling function
- PER timing x K heatmap
- PER comparison by devices and interval
- Phase-difference graphs

ジョブ追加時の原則:

- 1ジョブで作る完成グラフは1つだけとする。
- 1ジョブ内で複数のK値や複数runを扱ってよい。
- 1ジョブ内で複数の結合関数を混ぜない。
- UI上で複数の結合関数をまとめて選んだ場合は、結合関数ごとに別ジョブへ分割する。
- 複数の異なるグラフ種を同じジョブに混ぜない。

例:

```text
Interval PER vs Kで LINEAR と NewSIN を選ぶ
=> LINEAR用ジョブを1つ作る
=> NewSIN用ジョブを1つ作る
=> graph folderも2つ作る
```

### 3.2 ジョブ確認

目的:

進行中、完了、失敗したジョブを一覧で確認する。

主な機能:

- ジョブ一覧
- 状態表示
- 進捗率
- 開始時刻
- 経過時間
- 推定残り時間
- 推定終了時刻
- 現在処理中の条件またはrun
- 完了run数/総run数
- エラー内容
- ジョブログ表示
- 完了済みジョブの出力先フォルダへの導線
- 進行中ジョブの中止
- queuedジョブの取り消し

ジョブ状態:

```text
queued
cancel_requested
running_simulations
running_analysis
rendering_graph
completed
failed
cancelled
```

ジョブ中止の考え方:

- `queued` のジョブは、まだ実行プロセスが始まっていないため即座に `cancelled` にできる。
- `running_simulations`, `running_analysis`, `rendering_graph` のジョブは、ユーザー操作で `cancel_requested` にする。
- 実行プロセスは `cancel_requested` を検知した時点で、現在実行中runを即時停止する。
- 中止したrunの一時フォルダや途中生成物は削除する。
- 中止したジョブのgraph folderは完全削除する。
- 中止ジョブは結果・グラフ確認ページには表示しない。
- 中止後に同じ条件で作りたい場合は、新しいジョブとして最初から作成する。

中止操作時のUI:

- 中止ボタンは `queued`, `running_simulations`, `running_analysis`, `rendering_graph`, `cancel_requested` のジョブに表示する。
- `cancel_requested` のジョブでは「中止要求済み」と表示し、連打できないようにする。
- 中止前に確認ダイアログを出し、ジョブのgraph folderと中止中runのフォルダが完全削除されることを明示する。
- 中止理由を任意で入力できるようにして、ジョブ管理側の履歴へ保存する。

終了予定時間の考え方:

- 完了済みrunの平均処理時間から残りrun時間を推定する。
- 解析・描画時間は過去の同種ジョブ実績があれば加味する。
- 実績がない場合は、シミュレーション完了後に「描画中」として別表示する。
- 推定値は目安であり、UI上でもEstimatedとして表示する。

### 3.3 結果・グラフ確認

目的:

作成済みのグラフ成果物を確認し、必要に応じてデータ削除、データ追加、再描画を行う。

主な機能:

- グラフ種ごとの成果物一覧
- グラフ作成ジョブ単位のフォルダ選択
- PDF/画像プレビュー
- パラメーター範囲の確認
- 平均run数の確認
- 集計条件の確認
- 集計済みデータの確認
- manifest/statusの確認
- 使用したシミュレーション条件の確認
- データ削除
- データ追加
- 再描画

結果・グラフ確認ページでは、run一覧を表示しない。run一覧の読み込みは大量run時に重くなるため、通常表示では避ける。

代わりに表示する情報:

- graph_type
- graph_key
- coupling function
- K範囲
- K値一覧またはK値数
- 各K値あたりのrun数
- 平均に使ったrun数
- interval start/end
- 集計単位
- 集計済みデータの作成日時
- 集計済みデータの件数
- 描画設定
- 代表PDF/画像
- graph folder容量
- ジョブ状態

再描画の考え方:

- 再描画は、基本的に集計済みデータを使う。
- 通常の再描画ではシミュレーション生データを読み込まない。
- 通常の再描画で変更できるのは、軸範囲、フォント、マーカー、エラーバー、タイトル有無などの描画設定だけとする。
- 再描画結果は同じgraph folder内の代表出力を上書きしてよい。
- intervalなど集計条件が変わる場合は、再描画ではなく「別集計データの作成」として扱う。
- 集計条件ごとに集計済みデータを作成し、あとから簡単に選択・確認・変更できるようにする。
- 描画時は選択された集計済みデータを使い、代表PDF/画像を上書き更新する。
- 古い描画履歴を細かく残すことより、現在選んでいる集計条件と描画結果を分かりやすく確認できることを優先する。

データ追加の考え方:

- 既存グラフフォルダに対して、同じ完成グラフ・同じ基本条件でrunを追加できる。
- 追加できる例:
  - run数を増やす
  - K値を追加する
  - 同じcoupling function内でK値を追加する
- 追加できない例:
  - 別グラフ種のデータを混ぜる
  - 別coupling functionのデータを同じ完成グラフフォルダに混ぜる
  - グラフの意味が変わるほど基本条件が異なるrunを無警告で混ぜる
- 条件が異なる場合は警告を出し、別ジョブとして作ることを推奨する。
- データ追加を行った場合、既存の集計済みデータはすべて物理削除する。
- run数が変わると平均値、標準偏差、最小値、最大値、countが変わるため、古い集計データを再利用しない。
- データ追加後は、必要な集計条件ごとに集計データを作り直す。
- 集計データの再作成は、可能な限りシミュレーション生データ全体ではなく、run単位の軽量な中間データを使う。

データ削除の考え方:

- グラフフォルダ単位の削除
- グラフフォルダ内の条件単位データ削除
- グラフフォルダ内の再描画結果削除
- 削除前に対象ファイル数、容量、パスを表示する。
- 削除は完全削除とする。一時アーカイブやtrashフォルダ移動は行わない。

### 3.4 その他管理

目的:

成果物作成以外の管理機能をまとめる。

主な機能:

- データ形式の確認
- サーバー環境の確認
- CPU/GPU/メモリ確認
- graph_data.sqliteのschema確認
- キャッシュ確認
- 不要データの整理
- 設定ファイル確認
- バージョン/実行環境情報の表示

このページに含める既存機能:

- Data format
- Server
- Maintenance

## 4. ジョブ設計

### 4.1 ジョブの基本単位

1ジョブは1つの完成グラフを作成する。

ここでいう完成グラフとは、最終的に確認・論文・発表などで1枚の図として扱う単位である。

Interval PER vs K by coupling functionの場合、coupling functionごとに別PDFを作るため、coupling functionごとに別ジョブ・別フォルダとする。

例:

```text
job_id = 20260704_153000_ab12cd34
graph_type = interval_per_vs_k_by_coupling_function
graph_key = LINEAR
```

1ジョブが持つもの:

- graph_type
- graph_key
- graph_id/job_id
- 入力条件
- シミュレーション要求一覧
- 進行状況
- 出力フォルダ
- 結果ファイル一覧
- エラー情報

### 4.2 ジョブ種別

初期実装では、ジョブはグラフ作成ジョブとして扱う。

内部ステップ:

```text
1. validate_request
2. prepare_graph_folder
3. run_simulations
4. preprocess
5. aggregate
6. render_graph
7. finalize_manifest
```

将来的には、ジョブ種別を分けてもよい。

```text
graph_creation
graph_redraw
data_append
data_delete
maintenance
```

ただしUI上は「ジョブ確認」に統合して表示する。

## 5. データ保存形式

### 5.1 基本方針

データはグラフ種ごとに分類し、その下に完成グラフごとのフォルダとして保存する。

```text
outputs/graph_runs/
  <graph_type>/
    <graph_id>/
      manifest.json
      status.json
      requests.json
      graph_data.sqlite
      raw/
      figures/
      logs/
```

`graph_type` はグラフ種を表す安定した名前にする。
`graph_id` は1つの完成グラフに対応する。

例:

```text
interval_per_vs_k/
per_vs_k/
per_timing_k_heatmap/
compare_per_by_devices_interval/
```

### 5.2 Interval PER vs Kの保存形式

Interval PER vs Kでは、1つのgraph folderは1つのcoupling functionのグラフだけを持つ。

例:

```text
LINEARのInterval PER vs K      => graph folder 1つ
NewSINのInterval PER vs K      => graph folder 1つ
LINEAR + NewSINをまとめて作成  => job 2つ、graph folder 2つ
```

```text
outputs/graph_runs/interval_per_vs_k/<graph_id>/
  manifest.json
  status.json
  requests.json
  graph_data.sqlite
  raw/
    runs/
  figures/
    per_by_k_interval_<start>_to_<end>ms.pdf
  logs/
    job.log
```

保存は `graph_data.sqlite` を基本とする。CSVはエクスポート用、互換用、デバッグ用の任意出力とし、通常運用では必須にしない。

`graph_data.sqlite` に保存する主なテーブル:

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

各テーブルの役割:

| table | 役割 |
| --- | --- |
| graph_meta | graph_type, graph_key, 作成日時、基本条件など |
| simulation_requests | K値、run数、seedなど、実行したシミュレーション要求 |
| runs | run_id、K値、状態、保存場所、軽量メタデータ |
| run_cycle_counts | runごとのcycle別送信数や累積送信数など、再集計用の軽量中間データ |
| run_interval_per | 特定intervalに対するrun単位PER |
| aggregate_sets | intervalなど集計条件の定義 |
| aggregate_interval_per | Kごとの平均PER、標準偏差、countなど |
| plot_settings | 現在の描画設定 |
| outputs | 代表PDF/画像のパス、更新時刻 |
| history | ジョブ作成、データ追加、集計作成、再描画、削除などの履歴 |

シミュレーション生データの扱い:

- send_logのような重い生データは、`raw/` 以下のgraph folder内ファイルとして保存する。
- `graph_data.sqlite` にはrunメタデータ、生データへの相対参照パス、軽量中間データ、集計済みデータを保存する。
- 結果・グラフ確認ページでは生データを直接一覧表示しない。
- 集計データの再作成では、可能な限り `run_cycle_counts` や `run_interval_per` を使い、生のsend_log読み込みを避ける。
- `raw/` は再解析やデバッグ用に残すが、通常の結果確認、集計条件変更、再描画では読まない。

### 5.3 manifest.json

`manifest.json` はグラフフォルダの代表メタデータである。

必須項目:

```json
{
  "schema_version": 1,
  "graph_id": "20260704_153000_ab12cd34",
  "graph_type": "interval_per_vs_k_by_coupling_function",
  "graph_key": {
    "coupling_function": "LINEAR"
  },
  "created_at": "2026-07-04T06:30:00Z",
  "updated_at": "2026-07-04T06:40:00Z",
  "status": "completed",
  "input": {},
  "simulation_base": {},
  "sweep": {},
  "outputs": {},
  "run_summary": {},
  "history": []
}
```

### 5.4 status.json

`status.json` はジョブ進行状況を表す。

主な項目:

```json
{
  "job_id": "20260704_153000_ab12cd34",
  "status": "running_simulations",
  "cancel_requested": false,
  "cancel_requested_at": null,
  "cancel_reason": "",
  "total_runs": 200,
  "completed_runs": 45,
  "current_run_id": "...",
  "started_at": "...",
  "updated_at": "...",
  "finished_at": null,
  "estimated_finish_at": "...",
  "error": ""
}
```

## 6. 初期対応: Interval PER vs K by coupling function

### 6.1 入力項目

Graph input:

- coupling function
- K start
- K stop
- K step
- interval start
- interval end
- runs per K

### 6.2 1グラフに必要なシミュレーション単位

Interval PER vs Kは、1つのcoupling functionについて、横軸Kごとの平均PERを描くグラフである。

そのため、1つの完成グラフを作るために以下が必要になる。

- 複数のK値
- 各K値ごとの複数run
- 各runのinterval PER
- K値ごとのPER平均
- K値ごとのばらつき

つまり、1ジョブは1つの完成グラフだけを作るが、そのジョブ内では複数Kと複数runを実行する。

例:

```text
graph_type = interval_per_vs_k_by_coupling_function
graph_key.coupling_function = LINEAR
K values = 0, 5, 10, 15, 20
runs per K = 10

=> 1ジョブ
=> 1 graph folder
=> 5 K values x 10 runs = 50 simulations
=> Kごとに10 runのinterval PERを平均
=> LINEARのInterval PER vs Kグラフを1枚作成
```

集計済みデータには、少なくとも以下を保存する。

```text
aggregate_set_id
coupling_function
coupling_strength
interval_start_ms
interval_end_ms
per_percent_mean
per_percent_std
per_percent_min
per_percent_max
expected_packets_sum
actual_packets_sum
interval_cycle_count_mean
count
```

`aggregate_set_id` は集計条件を表すIDである。interval start/endなどが異なる場合は、別の `aggregate_set_id` として保存する。

Interval PER vs Kでは、平均PERの数値が変わる条件を `aggregate_set` の粒度とする。

別 `aggregate_set` にする条件:

- interval start/end
- PER計算方法
- 集計対象run条件
- 除外run条件
- Kごとの平均値、標準偏差、countが変わる条件

別 `aggregate_set` にしない条件:

- x軸/y軸の表示範囲
- フォント
- マーカー
- 色
- error barの表示/非表示
- titleやlabelの表示設定

これらの見た目だけの変更は `plot_settings` として扱い、選択中の `aggregate_set` から代表PDF/画像を上書き再描画する。

例:

```text
aggregate_set_id = interval_0_to_300000
aggregate_set_id = interval_300000_to_2000000
```

これにより、同じgraph folder内で複数の集計条件を保持し、結果・グラフ確認ページから選択して描画できる。

Simulation base:

- seed
- duration
- device count
- cycle time
- listening rate
- start timing mode
- start timing parameters
- simulation mode
- PER measurement settings
- LoRa settings
- max workers

Plot settings:

- x range
- y range
- figure size
- font sizes
- marker settings
- error bar settings
- min PER annotation

### 6.3 処理フロー

```text
1. ユーザーがInterval PER vs Kを選ぶ
2. GUIが必要条件フォームを表示する
3. ユーザーがcoupling function、K範囲、run数、intervalなどを入力する
4. GUIが1グラフあたりの条件数とrun数を見積もる
5. 複数coupling functionが選ばれている場合は、coupling functionごとにジョブを分割する
6. ユーザーがジョブを追加する
7. 1ジョブごとにgraph folderを1つ作成する
8. K x repeat条件でシミュレーションを実行する
9. raw/に重い生データを保存し、graph_data.sqliteにrunメタデータと相対参照パスを保存する
10. runごとのcycle countや累積送信数など、再集計用の軽量中間データを保存する
11. 指定intervalをaggregate_setとして登録する
12. runごとのinterval PERを集計する
13. Kごとに複数runのPER平均とばらつきを計算する
14. 集計済みデータをSQLiteへ保存する
15. 選択中の集計済みデータからPDF/画像を描画する
16. figures/の代表出力を上書きする
17. manifest/statusを更新する
18. 結果・グラフ確認ページに表示する
```

### 6.4 再描画フロー

```text
1. 結果・グラフ確認ページでgraph folderを選ぶ
2. 現在のPDF/画像、パラメーター範囲、平均run数、集計条件を表示する
3. 使用するaggregate_setを選ぶ
4. plot settingsを変更する
5. 選択中の集計済みデータから再描画する
6. figures/の代表出力を上書きする
7. manifest/historyへ再描画履歴を追加する
```

### 6.5 集計条件変更フロー

```text
1. 結果・グラフ確認ページでgraph folderを選ぶ
2. 新しいintervalなどの集計条件を入力する
3. 同じ集計条件のaggregate_setが既にあればそれを選択する
4. なければ新しいaggregate_setを作成する
5. run_cycle_countsなどの軽量中間データからrun単位PERを作る
6. Kごとの平均PERとばらつきを作る
7. graph_data.sqliteに保存する
8. 新しいaggregate_setを選択状態にして描画する
```

## 7. 既存機能との関係

既存の機能はすぐには削除しない。

移行方針:

- 既存のSimulationページは、当面は単独run生成用として残す。
- 既存のGraph creationページは、当面は旧式のrun選択型グラフ作成として残す。
- 新しい4ページ構成が安定したら、旧ページを段階的に統合または非表示にする。
- CLIは既存研究作業の再現性のため維持する。

最終的なWeb GUIでは、旧ページの機能を以下へ統合する。

| 旧機能 | 新ページ |
| --- | --- |
| Simulation | ジョブ追加 |
| Graph creation | ジョブ追加 |
| Graph redraw | 結果・グラフ確認 |
| Figures | 結果・グラフ確認 |
| Graph job status | ジョブ確認 |
| Simulation job status | ジョブ確認 |
| Data format | その他管理 |
| Server | その他管理 |
| Maintenance | その他管理 |

## 8. 実装ステップ案

### Phase 1: Interval PER vs K専用の新導線

- トップレベル4ページを作る。
- ジョブ追加にInterval PER vs Kを実装する。
- ジョブ確認でGraph-firstジョブを表示する。
- 結果・グラフ確認でGraph-first成果物を表示する。
- 同じgraph folder内の集計済みデータから再描画できるようにする。
- intervalなど集計条件が異なる場合はaggregate_setを追加作成できるようにする。

### Phase 2: 保存形式の整理

- `graph_data.sqlite`, `raw/`, `figures/`, `logs/` を明確に分離する。
- manifest schemaを固定する。
- redraw履歴をSQLiteのhistoryとmanifestに保存する。
- 削除/追加の履歴もSQLiteのhistoryとmanifestに保存する。
- 新規graph-first成果物はSQLite中心の保存に統一する。
- 結果・グラフ確認ページでrun一覧を読まない設計にする。
- 集計条件ごとのaggregate_set管理を実装する。

### Phase 3: 既存ページ統合

- Simulation jobとGraph jobを統合ジョブ一覧へ移す。
- Figuresページを結果・グラフ確認へ統合する。
- Maintenance/Data format/Serverをその他管理へ統合する。

### Phase 4: 他グラフ種追加

- PER vs K
- PER timing x K heatmap
- Compare PER by devices and interval
- その他必要な研究用グラフ

## 9. 未決事項

- 旧Web GUIページをいつ非表示にするか。
