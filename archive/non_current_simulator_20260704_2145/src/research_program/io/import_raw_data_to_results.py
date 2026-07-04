from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional


RAW_DATA_DIR = Path("data/raw/real")
RESULTS_DIR = Path("data/runs")

DEFAULT_COUPLING_STRENGTH = 0
DEFAULT_STRENGTH_RATIO = 0.0001
DEFAULT_COUPLING_FUNCTION = "N"
DEFAULT_CYCLE_TIME_MS = 30000
DEFAULT_LISTENING_RATE = 25
DEFAULT_TAGS = ["hex", "sec", "real", "same"]


def parse_coupling_strength(filename_stem: str) -> int:
    """
    例:
        kura_30mac_250K_15sec -> 250
    """
    m = re.search(r"(\d+)K(?:_|$)", filename_stem, flags=re.IGNORECASE)
    if m is None:
        return DEFAULT_COUPLING_STRENGTH
    return int(m.group(1))


def parse_coupling_function(filename_stem: str) -> str:
    """
    例:
        kura_xxx -> KURAMOTO
        LIN_xxx  -> LINEAR
    """
    lower_name = filename_stem.lower()

    if lower_name.startswith("kura"):
        return "KURAMOTO"

    if lower_name.startswith("lin"):
        return "LINEAR"
    
    if lower_name.startswith("none"):
        return "NONE"

    return DEFAULT_COUPLING_FUNCTION


def parse_cycle_time_ms(filename_stem: str) -> int:
    """
    例:
        kura_30mac_250K_15sec -> 15000 ms
    """
    m = re.search(r"(\d+)sec(?:_|$)", filename_stem, flags=re.IGNORECASE)
    if m is None:
        return DEFAULT_CYCLE_TIME_MS
    return int(m.group(1)) * 1000


def parse_num_devices(filename_stem: str) -> Optional[int]:
    """
    例:
        kura_30mac_250K_15sec -> 30
    """
    m = re.search(r"(\d+)mac(?:_|$)", filename_stem, flags=re.IGNORECASE)
    if m is None:
        return None
    return int(m.group(1))


def build_tags(filename_stem: str) -> list[str]:
    tags = list(DEFAULT_TAGS)

    num_devices = parse_num_devices(filename_stem)
    if num_devices is not None:
        tags.append(f"{num_devices}dai")

    return tags


def convert_send_log(src_csv_path: Path, dst_csv_path: Path) -> None:
    """
    元CSVを send_log.csv としてコピーし，
    1行目だけ
      SrcName,Time,Message
    から
      oscillator_id,time,Message
    に変更する。
    """
    with src_csv_path.open("r", newline="", encoding="utf-8") as f_in:
        reader = csv.reader(f_in)
        rows = list(reader)

    if not rows:
        raise ValueError(f"empty csv: {src_csv_path}")

    header = rows[0]

    if len(header) >= 3:
        # 指定の列名に修正
        rows[0][0] = "oscillator_id"
        rows[0][1] = "time"
        rows[0][2] = "Message"
    else:
        raise ValueError(f"unexpected header format: {src_csv_path} -> {header}")

    with dst_csv_path.open("w", newline="", encoding="utf-8") as f_out:
        writer = csv.writer(f_out)
        writer.writerows(rows)


def write_metadata(dst_metadata_path: Path, run_id: str, filename_stem: str) -> None:
    coupling_strength = parse_coupling_strength(filename_stem)
    coupling_function = parse_coupling_function(filename_stem)
    cycle_time = parse_cycle_time_ms(filename_stem)
    listening_rate = DEFAULT_LISTENING_RATE
    strength_ratio = DEFAULT_STRENGTH_RATIO
    tags = build_tags(filename_stem)

    ranges_text = ""

    with dst_metadata_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "run_id",
                "coupling_strength",
                "strength_ratio",
                "coupling_function",
                "cycle_time",
                "listening_rate",
                "tags",
                "ranges",
            ]
        )
        writer.writerow(
            [
                run_id,
                coupling_strength,
                strength_ratio,
                coupling_function,
                cycle_time,
                listening_rate,
                ";".join(tags),
                ranges_text,
            ]
        )


def process_one_file(src_csv_path: Path, results_dir: Path) -> Path:
    filename_stem = src_csv_path.stem
    run_id = filename_stem

    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    dst_send_log_path = run_dir / "send_log.csv"
    dst_metadata_path = run_dir / "metadata.csv"

    convert_send_log(src_csv_path, dst_send_log_path)
    write_metadata(dst_metadata_path, run_id=run_id, filename_stem=filename_stem)

    return run_dir


def main() -> None:
    if not RAW_DATA_DIR.exists():
        raise FileNotFoundError(f"raw data folder not found: {RAW_DATA_DIR}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(RAW_DATA_DIR.glob("*.csv"))
    if not csv_files:
        print(f"no csv files found in {RAW_DATA_DIR}")
        return

    for src_csv_path in csv_files:
        try:
            run_dir = process_one_file(src_csv_path, RESULTS_DIR)
            print(f"converted: {src_csv_path} -> {run_dir}")
        except Exception as e:
            print(f"failed: {src_csv_path} ({e})")


if __name__ == "__main__":
    main()
