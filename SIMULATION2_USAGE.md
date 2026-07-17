# simulation2 利用ガイド

`simulation2` は、既存の `research_program.simulation` を変更せずに、新しい
Oscillator アルゴリズムを試作・検証するためのイベントシミュレータです。
現在は `PCO-D` アルゴリズムを実装しています。

## 現在の構成

```text
src/research_program/simulation2/
├─ algorithms/
│  ├─ base.py        # アルゴリズム共通インターフェース
│  ├─ registry.py    # アルゴリズム名からの生成
│  └─ pco_d.py       # PCO-D の状態遷移と位相更新式
├─ config.py         # Simulation2Config
├─ events.py         # イベント種別とイベントキュー要素
├─ medium.py         # 送信区間とキャリアセンス
├─ oscillator.py     # ノードごとの可変状態
└─ scheduler.py      # 時刻順イベント実行
```

既存の `simulation`、Web画面、SQLite保存、graph workflow とはまだ接続していません。
`simulation2` は独立した実験用の実装です。

## PCO-D の状態遷移

各ノードは、次の順序で動作します。

```text
リスニング
  → 送信予定時刻でキャリアセンス
  → clear: 送信
  → busy: 送信をスキップ
  → スリープ
  → 次のリスニング
```

送信中のパケットは、送信終了時刻に他ノードへ届くモデルです。受信したノードが
リスニング中なら、予定送信時刻を遅らせます。スリープ中や送信中のノードは受信を
位相更新に使いません。

## PCO-D の位相更新

受信時点で、現在の予定送信時刻までの残り時間を `R` とします。

```text
R = planned_send_time - current_time
R' = (1 - α)R + αrT
new_send_time = current_time + R'
```

- `r`: リスニング割合
- `T`: 名目1周期時間（ms）
- `α`: 変化強度。`0 <= α <= 1`

キャリアセンスがbusyだった場合は、送信予定時刻で仮想的に受信したものとして扱います。
この時点では `R=0` なので、得られる変化は `αrT` です。送信は行わず、得られた値だけ
その周期のスリープを延長します。

```text
CS busy 時のスリープ延長 = αrT
```

## 設定

`Simulation2Config` に設定を渡します。

```python
from research_program.simulation2.config import Simulation2Config

config = Simulation2Config(
    algorithm="PCO-D",
    listening_ratio=0.2,             # r
    cycle_time_ms=1_000.0,           # T [ms]
    alpha=0.5,                       # α
    carrier_sense_duration_ms=5.0,   # CS時間 [ms]
    transmission_duration_ms=20.0,   # 送信時間 [ms]
)
```

時間の関係は以下です。送信時間は名目周期 `T` に含めます。

```text
リスニング時間 = rT
スリープ時間   = T - rT - 送信時間
```

そのため、`送信時間 > T - rT` となる設定は使用できません。

## 最小実行例

`initial_listen_times` は `{ノードID: 最初にリスニングを始める時刻[ms]}` です。
ノード0を基準にし、他ノードに異なる初期時刻を与えます。

```python
from research_program.simulation2 import EventScheduler, Simulation2Config

config = Simulation2Config(
    listening_ratio=1 / 5,
    cycle_time_ms=1_000.0,
    alpha=0.5,
    carrier_sense_duration_ms=5.0,
    transmission_duration_ms=20.0,
)

scheduler = EventScheduler(
    config=config,
    initial_listen_times={
        0: 0.0,
        1: 30.0,
        2: 70.0,
        3: 120.0,
        4: 180.0,
    },
)
scheduler.run(until_ms=30_000.0)

# 実送信区間
for transmission in scheduler.medium.transmissions:
    print(transmission.source_id, transmission.start, transmission.end)

# キャリアセンス結果
for result in scheduler.medium.carrier_sense_results:
    action = "skip_busy" if result.is_busy else "send_clear"
    print(result.source_id, result.time, action)
```

`run()` は周期動作を自動停止しないため、必ず `until_ms` を指定してください。

## グラフ生成スクリプト

以下のスクリプトは、送信試行時刻からノード0との位相差を作ります。送信が
キャリアセンスでスキップされた周期も、Oscillator の位相を追うために送信試行として
グラフへ残します。

```powershell
$env:MPLBACKEND='Agg'
uv run python scripts/run_simulation2_pco_d_phase_test.py
```

| スクリプト | 条件 | 出力先 |
| --- | --- | --- |
| `run_simulation2_pco_d_phase_test.py` | N=5, r=1/5, T=1000 ms, CS=5 ms, 送信20 ms, 30周期 | `outputs/simulation2_test/pco_d_phase_difference_cs5_tx20/` |
| `run_simulation2_pco_d_n50_phase_test.py` | N=50, r=0.02, T=10000 ms, CS=5 ms, 送信20 ms, 30周期 | `outputs/simulation2_test/pco_d_phase_difference_n50_r002_t10000_cs5_tx20/` |

どちらの出力先にも、主に次のファイルが作られます。

- `pco_d_phase_differences.png` / `.pdf`: 位相差グラフ
- `phase_differences.csv`: グラフに用いた位相差
- `carrier_sense_attempts.csv`: 各送信試行のCS判定
- `carrier_sense_summary.csv`: clear / busyスキップの集計
- `send_times.csv`: 実際に送信した時刻

N=50用スクリプトでは、再現用に `initial_listen_times.csv` も保存します。

## 位相差グラフの読み方

位相差は、各ノードの第`k`回送信試行とノード0の第`k`回送信試行の時刻差から計算し、
`[0, 2π)` へ折り返します。

```text
phase_difference = 2π × (attempt_time_i - attempt_time_0) / T
```

送信時間がある場合、受信は送信終了時刻に発生するため、実際の周期が名目周期 `T` より
長くなることがあります。その場合、`T/N` に対する位相差は少し大きく見えても、実際に
伸びた周期に対しては等間隔になっている場合があります。

## 現時点の注意点

- CSは、予定送信時刻の直前 `carrier_sense_duration_ms` を確認します。
- 同時刻に開始した送信は、CSでは相互に検出しません。
- 送信衝突・受信失敗・PERは、`simulation2` には未実装です。
- `simulation2` の出力はメモリ上のリストです。SQLite・CSVへの自動保存は行いません。
- 新しいアルゴリズムは `algorithms/` にファイルを追加し、`registry.py` へ登録します。

## テスト

```powershell
uv run python -m unittest discover -s tests -p "test_*.py"
```

PCO-Dの単体テストは `tests/test_simulation2_pco_d.py` にあります。
