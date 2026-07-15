# 現行実装調査レポート

コード変更は行っていません。`results/reanalysis/{function}_phase_error.csv` の生成元、元データを作るシミュレータ、SQLite/CSV集計経路を調査しました。

## 1. 位相残差メトリクスの現行実装

### 生成経路

- `scripts/reanalyze_existing_runs.py:366` `read_phase_errors_for_runs()`
  - `raw_run.sqlite` の `phase_gap_error` テーブルから、サイクルごとの `mean_abs_diff_from_ideal_phase_gap` を読みます。
- `scripts/reanalyze_existing_runs.py:517` `final_window_phase_error()`
  - 最終10サイクルのサイクル別誤差を平均し、run単位の残差にします。
- `scripts/reanalyze_existing_runs.py:615` `aggregate_phase_error_function()`
  - run単位残差の中央値・Q1・Q3をKごとに集計し、`results/reanalysis/kuramoto_phase_error.csv` などを生成します。

したがって、CSVの `residual_median` は「隣接ペア誤差そのもの」ではなく、次の二段集計です。

1. 各サイクル：隣接ペア絶対誤差の平均
2. 各run：最終10サイクルの平均
3. 各K：run間の中央値・四分位

### a. 隣接ペアの定義

- `src/research_program/analysis/calculate_phase_gap_error.py:135` `compute_mean_abs_gap_error_per_cycle()`
  - 各サイクル・各デバイスについて最初の送信だけを選択します。
- `src/research_program/analysis/calculate_phase_gap_error.py:156`
  - 送信時刻から位相を計算した後、`phases.sort()` で位相順に再ソートします。
- `src/research_program/analysis/calculate_phase_gap_error.py:161`
  - 通常の隣接差に加え、最後尾→先頭のwrap-around差も追加します。

```python
phases.sort()
diffs = np.diff(phases)
wrap_diff = (phases[0] + 2.0 * math.pi) - phases[-1]
all_diffs = np.concatenate([diffs, [wrap_diff]])
```

デバイスID順ではなく、各サイクルの実送信位相順です。wrap-aroundも含みます。

ただし、送信スキップでデバイスが欠けても、2台以上いれば残った送信だけで計算します。常にNペア揃うことは保証されません。

### b. 「理想値との差」

- `src/research_program/analysis/calculate_phase_gap_error.py:127`
  - 理想値は常に `2π / num_devices`。
- `src/research_program/analysis/calculate_phase_gap_error.py:165`
  - `mean(abs(all_diffs - ideal_gap))` です。

符号付き平均でもRMSでもなく、「各隣接ペア偏差の絶対値の算術平均」です。

### c. 位相差の測定ソース

- `src/research_program/io/send_log.py:21` `add_detection_time_column()`
  - `transmission_end_time` があれば、それを `detection_time` として使用します。
- `src/research_program/analysis/calculate_phase_gap_error.py:157`
  - 内部位相変数ではなく、実送信ログ時刻から次式で換算します。

```python
phases = 2π * ((detection_time - cycle_start) / cycle_length)
```

ただし分母は公称周期Tではなく、基準デバイスの連続送信から求めた当該サイクル長です。サイクル境界も基準デバイスの実送信終了時刻を基に作られます（`src/research_program/analysis/calculate_cycle_data.py:124`）。

全デバイスでエアタイムが同じなら、送信開始時刻差と終了時刻差は同じですが、新定義が明示的に `2π/T` を使うなら現行の「実測cycle length」は差分です。

### d. ソート順の固定・再計算

`src/research_program/analysis/calculate_phase_gap_error.py:146` のサイクルループ内で毎回 `phases.sort()` するため、初回順序の固定ではありません。

### 新定義との差分

一致する点：

- 位相順ソート
- wrap-aroundを含む
- 隣接差と `2π/N` の偏差
- 偏差の絶対値平均
- 実送信ログを使用

異なる、または明確化が必要な点：

- 現行は送信終了時刻を使用。
- rad換算の分母が公称Tではなく基準デバイスの実測サイクル長。
- スキップでN台揃わなくても残存M台でMペアを計算し、理想値だけは `2π/N` のまま。
- CSVの残差値は最終10サイクル平均後、さらにrun間集計された値。
- 最悪ペア偏差は現在計算・保存していない。

**判定：軽微な変更が必要（公称Tによる換算、N送信未満の扱い、最大偏差列の追加）**

---

## 2. キャリアセンス（CS）の実装

### a. 実行タイミング・持続時間

- `src/research_program/simulation/oscillator.py:52` `on_add()`
- `src/research_program/simulation/oscillator.py:139` `on_awake()`

送信イベントはawake開始から `awake_half_duration` 後に発生します。

- `src/research_program/simulation/scheduler.py:804` `_carrier_sense_window()`

CS窓は送信予定時刻を `current_time` として、過去方向の

```text
[max(awake開始時刻, current_time - CS時間), current_time]
```

です。イベントとして5 ms待機するのではなく、送信時刻直前の過去区間を検査します。

現行再解析対象runの設定は、代表例の `outputs/graph_runs/interval_per_vs_k/20260706_190633_ea058e11/manifest.json:197` にあるとおり：

- `cycle_time = 10000 ms`
- `carrier_sense_duration_ms = 5.0`
- payload 50 bytes、SF7、BW 500 kHz

です。一方、新規ジョブのコード上のデフォルトは0 msです（`src/research_program/web/settings.py:50`）。

### b. 送信スキップ条件

- `src/research_program/simulation/scheduler.py:811` `_find_blocking_transmission()`
- `src/research_program/simulation/scheduler.py:826`

他デバイスのエアタイム区間 `[tx_start, tx_end)` とCS窓が次の厳密不等号で重なるとスキップします。

```python
tx_start < carrier_sense_end and tx_end > carrier_sense_start
```

判定対象の他送信区間はエアタイムだけです（`src/research_program/simulation/scheduler.py:833`）。CS時間は送信占有区間に含まれません。

注意点：

- 同一時刻に開始する送信は `tx_start == carrier_sense_end` となるため、CSでは検出されません。
- 衝突後の復号失敗を判定する機構はありません。
- CS窓と既存エアタイムが重なった場合の「送信抑止」だけがPER相当の損失になります。

### c. スキップ時のPER・ログ

- `src/research_program/simulation/scheduler.py:880`
  - `on_skip_send()` を呼び、`send_log` には追加しません。
- `src/research_program/simulation/scheduler.py:890`
  - CSログ有効時だけ `action="skip_busy"` と妨害送信情報を保存します。
- `src/research_program/graph_workflow/execution.py:1917`
  - 後段では各サイクルの `send_log` 件数をactual packet数とします。
- `src/research_program/graph_workflow/execution.py:1949`
  - 期待数は毎サイクルN固定、実数は送信ログ件数です。
- `scripts/reanalyze_existing_runs.py:570`
  - `PER = (1 - actual / expected) × 100`。

従ってスキップは：

- PER分母：期待送信として含まれる。
- 成功packet数：含まれない。
- packet error数：`expected - actual` として間接的に増える。
- `send_log`：行なし。
- `carrier_sense_log`：保存設定が有効なら明示的な `skip_busy` 行あり。

graph workflowは現在 `save_carrier_sense_log` を渡していないため、デフォルトfalseです（`src/research_program/simulation/runner.py:86`、`src/research_program/graph_workflow/execution.py:1693`）。既存runではスキップを欠落送信として推定できますが、明示ログは通常残りません。

### d. 5 ms固定設定と「CS+エアタイム」

CS時間はハードコードではなく `SimulationRequest.carrier_sense_duration_ms` パラメータです（`src/research_program/simulation/runner.py:77`）。UIからも設定できます（`src/research_program/web/pages/job_add.py:212`）。

ただし現在はCSを送信占有時間に加算していません。「合計25 msを衝突・許容揺らぎの基準に使う」には、次のいずれかを選ぶ必要があります。

- メトリクスの許容幅だけ `CS + airtime` とする。
- シミュレータ内でも `[CS開始, 送信終了]` を占有区間として扱う。
- 送信イベント自体をCS終了後へ5 ms遅延させる。

**判定：要設計判断（CS+airtimeを解析上の閾値だけに使うか、シミュレーションの占有区間も変更するか）**

---

## 3. エアタイムの導出

- `src/research_program/simulation/lora_airtime.py:26` `calculate_lora_airtime_ms()`
  - payload、SF、BW、coding rate、preamble、header、CRC、low-data-rate optimizeからLoRa式で計算します。
- `src/research_program/simulation/runner.py:325`
  - `per_measurement` モードでは計算結果を `transmission_time_ms` に設定します。

定数ではありませんが、`SimulationRequest` からエアタイムを直接指定するフィールドもありません。

現行再解析runの `payload=50, SF7, BW=500 kHz` は実装式で **24.384 ms** です。CS 5 msは占有時間に足されないため、現在の送信区間は24.384 msです。

### エアタイム20 msの設定

標準的な既定値 `payload=16, SF7, BW=125 kHz` は51.456 msです。

同じpayload/SF/その他設定のまま、モデル上は次でちょうど20 msになります。

```text
payload=16 bytes
SF=7
BW=321600 Hz
CR=4/5
preamble=8
explicit header=true
CRC=true
→ airtime=20.000 ms
```

BW入力は正整数なら受理されます（`src/research_program/web/pages/job_add.py:236`）。ただし321.6 kHzを実機LoRa設定として採用可能かは別途確認が必要です。

一般的なBW候補だけを使う場合、近い例は：

- SF7、BW250 kHz、payload 9 bytes → 20.608 ms
- SF7、BW500 kHz、payload 37 bytes → 20.544 ms

厳密な20 msを必須とし、実機準拠のSF/BW/payloadでは作れない場合は、`transmission_time_ms` の直接指定機能追加が必要です。

**判定：要設計判断（LoRaパラメータ由来を維持するか、20 ms直接指定を追加するか）**

---

## 4. サイクル周期Tと台数Nの設定

### a. T=10 s → 5 s

コアシミュレータでは `SimulationRequest.cycle_time` 1項目です（`src/research_program/simulation/runner.py:62`）。

T依存値は固定値ではなく、次のようにTから導出されます（`src/research_program/simulation/oscillator.py:27`）。

- awake半幅
- sleep時間
- rad/ms変換係数 `2π/T`
- 結合遅延スケール

初期位相範囲もTから算出されます（`src/research_program/graph_workflow/execution.py:1664`）。基準デバイス欠落補間の閾値も `1.3×T` です（`src/research_program/analysis/calculate_cycle_data.py:96`）。

ただし、180サイクルを維持するには別パラメータの `duration_ms` を900,000 msへ変更する必要があります。PER集計区間の `interval_end_ms` も絶対時刻なので連動確認が必要です。

既存再解析スクリプトには以下のサイクル数固定があります。

- 最終10サイクル：`scripts/reanalyze_existing_runs.py:517`
- “3min” 指標がcycle 9～18固定：`scripts/reanalyze_existing_runs.py:293`

T=5 sでは後者は3分を表さないため、新計画で流用するなら修正が必要です。

### b. N=5, 10, 20, 50

`SimulationRequest.device_count` またはUIのdevice countで任意の正整数を指定できます（`src/research_program/web/pages/job_add.py:165`）。

調査した現行シミュレーション・解析経路に、N=50固定の配列はありません。

- oscillator辞書は動的：`src/research_program/simulation/scheduler.py:714`
- 初期時刻生成数は `device_count`：`src/research_program/graph_workflow/execution.py:1658`
- PER期待数もメタデータ上のN：`src/research_program/graph_workflow/execution.py:1952`
- 位相理想値も `2π/N`：`src/research_program/analysis/calculate_phase_gap_error.py:127`

旧 `start_timing_mode="random"` では候補点数以上のNを禁止しますが、現行graph workflowの `random_cycle_ms_with_replacement` ではこの制約を受けません。

**判定：軽微な変更が必要（T、duration、集計区間をセットで変更。“3min”固定cycle窓は流用しない）**

---

## 5. 初期位相の制御

### a. 外部ファイル・引数

- `src/research_program/simulation/runner.py:73`
  - `SimulationRequest.initial_start_times_by_run` にrun別tupleを直接渡せます。
- `src/research_program/simulation/runner.py:246`
  - 指定があれば乱数生成せず、そのrun indexの値を使用します。
- `src/research_program/simulation/runner.py:255`
  - 各tupleの長さは `device_count` と完全一致が必要です。

外部CSV/JSONを自動ロードする機能やCLI引数はありません。呼び出し側がファイルを読み、tupleへ変換して渡すことは可能です。`request_from_config()` も現在このフィールドを読み取りません。

### b. 50試行分を全パラメータで再利用できるか

同一N内では現行graph workflowが既に対応しています。

- `src/research_program/graph_workflow/execution.py:90`
  - Kループ前に50試行分を生成。
- `src/research_program/graph_workflow/execution.py:135`
  - 各Kの同じ `repeat_index` に同じ初期セットを渡します。
- `src/research_program/graph_workflow/execution.py:1655`
  - 重複ありの `randrange(start_ms, end_ms)`。
- T=5 s、範囲0～100%なら値は0～4999 ms、1 ms単位です。

関数間も同じN・seed・範囲・run数なら同じセットが再生成されます。

Nを跨ぐ共通化には注意が必要です。現行helperはN個生成後にソートするため、N=50のソート済み配列の「先頭N」を使うと、小さいNほど早い時刻へ偏ります。

自然な選択肢は次の二つです。

1. 推奨：各runについて未ソートの50個の乱数をmasterとして事前生成し、N条件ではデバイスID順の先頭N個を渡す。これならN間でnestedな共通初期条件になります。
2. 各Nごとに同じseedから独立にN個生成する。同一Nの関数・K間では共通ですが、run 2以降はN間でprefix関係になりません。

現行runnerは外部tupleの順序を保存するため、選択肢1はプログラムAPI上可能です。ただしgraph workflowにmasterファイル読込とN別sliceを組み込む軽微な拡張が必要です。

### c. run番号とシード

- `src/research_program/simulation/config_factory.py:37`
  - `random.Random(seed)` を1個生成します。
- `src/research_program/simulation/config_factory.py:40`
  - run index 0,1,…の順に同じ乱数列を消費します。
- `src/research_program/simulation/config_factory.py:50`
  - メタデータにはbase seedとrun indexを別々に保存します。

「runごとに seed+run番号で再seed」する方式ではありません。対応関係は「base seedから始まる単一乱数列の第runブロック」です。

graph workflowは各Kを1runずつ起動するため、runner側の `random_run_index` は毎回0になりますが、実際の初期値は事前生成tupleで固定されています。再現性確認には `repeat_index` と `selected_start_times` を使うのが確実です。

**判定：軽微な変更が必要（50台masterセットの外部読込・N別prefix適用。ソート前のデバイスID順を維持する）**

---

## 6. ログ・出力の拡張性

### a. 実送信時刻とスキップ

`send_log` のスキーマは `src/research_program/io/sqlite_runs.py:121` です。

- 実送信開始時刻 `time`
- デバイスID
- デバイス別送信回数
- 送信終了時刻
- エアタイム

を取得できます。

`carrier_sense_log` は `src/research_program/io/sqlite_runs.py:141` にあり、保存を有効化すれば：

- `send_clear`
- `skip_busy`
- CS開始・終了
- blocking device
- blocking送信区間

を取得できます。

`calculated_cycle_data` はサイクル境界・基準デバイスだけで、各デバイス時刻やskip情報はありません（`src/research_program/io/sqlite_runs.py:155`）。

従って新メトリクスに必要な情報は取得可能ですが、明示的なskip時系列を必要とするなら `save_carrier_sense_log=True` をgraph workflowから渡す必要があります。

### b. 平均偏差・最大偏差を追加する最小変更箇所

最小変更候補は既存のサイクル別 `phase_gap_error` 経路です。

- 計算：`src/research_program/analysis/calculate_phase_gap_error.py:119`
- run終了時の派生データ生成：`src/research_program/simulation/scheduler.py:581`
- 保存テーブル：`src/research_program/io/sqlite_runs.py:165`

提案する追加列：

```text
mean_abs_diff_from_ideal_phase_gap
max_abs_diff_from_ideal_phase_gap
observed_device_count
expected_device_count
has_all_device_sends
skipped_device_count
```

計算中の `np.abs(all_diffs - ideal_gap)` を一度配列化し、`mean()` と `max()` を保存すればよいため、計算量増加はごく小さいです。

ただし「スキップcycleを無効値にする」「残存送信だけで計算する」「予定時刻を補う」のどれを採用するかは先に決める必要があります。

**判定：軽微な変更が必要（既存phase_gap_errorテーブルと計算関数への列追加、CSログ保存の有効化）**

---

## 7. 対象関数の限定

- `src/research_program/simulation/coupling_functions.py:8` `CouplingFunction`
  - `KURAMOTO` = Kuramoto based
  - `LINEAR` = frog chorus based
  - `LINEAR_4` = modified frog chorus based
  - `NewSIN` = 1−sin系
- `src/research_program/simulation/runner.py:166` `_resolve_coupling_function()`
  - enum名または値で1関数を選びます。
- `src/research_program/web/pages/job_add.py:109`
  - UIは1ジョブにつき1関数のselectboxです。

したがって2関数だけを回すには、同一設定で次の2ジョブを作成します。

```text
coupling_function = "KURAMOTO"
coupling_function = "LINEAR"
```

`LINEAR_4` と `NewSIN` のジョブを作らなければ除外できます。

指定された11個のKは等間隔ではないため、UIの `K start/stop/step` では直接表現できません。job paramsの `k_values` は任意リストを受け付けるので、プログラム経由で

```python
[1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000]
```

を渡すのが自然です。実効結合係数は `strength_ratio=-1e-4` と組み合わせます。

**判定：軽微な変更が必要（2ジョブ作成。非等間隔KリストはUIではなくjob params/APIから指定）**

---

## 8. 計算量の見積もり

条件数は：

```text
4 N × 2関数 × 11 K = 88条件
88条件 × 50試行 = 4,400 runs
```

T=5 sで180サイクルなら、simulation durationは：

```text
5,000 ms × 180 = 900,000 ms
```

既存実績の `N=50、T=10 s、約180サイクル、1 run ≈ 1秒` と比較すると、Tを半分にしてもサイクル数が同じならイベント数はほぼ同じです。したがってN=50では引き続き約1秒/runが一次見積もりです。

### 保守的上限

全Nを1秒/runとして扱うと：

```text
4,400秒 ≈ 73.3分
```

### Nにほぼ比例すると仮定した見積もり

各Nについて1,100 runsあるため：

```text
1,100 × (5+10+20+50)/50
= 1,870秒
≈ 31.2分（直列）
```

理想的な並列化なら：

- 2 workers：約16分
- 4 workers：約8分
- 8 workers：約4分

ただしSQLite書込み、プロセス起動、派生データ計算の固定オーバーヘッドがあるため、実運用上は **4 workersで10～20分程度、直列で30～75分程度** を初期見積もりにするのが安全です。

最大偏差の追加は既に作るN個の隣接偏差に対する `max()` だけなので、実行時間への影響は無視できる程度です。明示的なCSログを全件保存すると、計算よりI/OとDB容量の増加が支配的になる可能性があります。

**判定：新方針にそのまま使える（4,400 runs。直列約30～75分、並列時はworker数に応じ短縮）**
