from __future__ import annotations

import numpy as np
import pandas as pd


DETECTION_TIME_COLUMN = "detection_time"


def normalize_send_time_columns(send_df: pd.DataFrame, tags: list[str]) -> pd.DataFrame:
    df = send_df.copy()
    if "sec" not in tags:
        return df

    for column_name in ["time", "transmission_end_time"]:
        if column_name in df.columns:
            df[column_name] = pd.to_numeric(df[column_name], errors="coerce") * 1000.0
    return df


def add_detection_time_column(send_df: pd.DataFrame) -> pd.DataFrame:
    df = send_df.copy()
    start_times = pd.to_numeric(df["time"], errors="coerce")
    if "transmission_end_time" in df.columns:
        end_times = pd.to_numeric(df["transmission_end_time"], errors="coerce")
        df[DETECTION_TIME_COLUMN] = end_times.where(end_times.notna(), start_times)
    else:
        df[DETECTION_TIME_COLUMN] = start_times
    return df


def detection_time_values(send_df: pd.DataFrame) -> np.ndarray:
    if DETECTION_TIME_COLUMN in send_df.columns:
        source = send_df[DETECTION_TIME_COLUMN]
    else:
        source = send_df["time"]
    return pd.to_numeric(source, errors="coerce").to_numpy(dtype=np.float64)
