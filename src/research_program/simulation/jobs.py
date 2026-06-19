from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any
import uuid

from research_program.config.paths import resolve_project_path
from research_program.simulation.runner import SimulationRequest, run_simulation_request


SIMULATION_JOB_DIR = Path("outputs/reports/simulation_jobs")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        last_error: PermissionError | None = None
        for attempt in range(30):
            try:
                tmp_path.replace(path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(min(0.05 * (attempt + 1), 0.5))
        if last_error is not None:
            raise last_error
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def simulation_request_to_dict(request: SimulationRequest) -> dict[str, Any]:
    return {
        "num_runs": request.num_runs,
        "seed": request.seed,
        "coupling_function": request.coupling_function,
        "coupling_strength": request.coupling_strength,
        "strength_ratio": request.strength_ratio,
        "cycle_time": request.cycle_time,
        "listening_rate": request.listening_rate,
        "device_count": request.device_count,
        "duration": request.duration,
        "start_step_count": request.start_step_count,
        "start_step": request.start_step,
        "tags": list(request.tags),
        "output_root": str(request.output_root),
        "max_workers": request.max_workers,
        "start_timing_mode": request.start_timing_mode,
        "fixed_start_times": list(request.fixed_start_times),
        "fixed_start_interval": request.fixed_start_interval,
        "fixed_start_offset": request.fixed_start_offset,
        "simulation_mode": request.simulation_mode,
        "carrier_sense_duration_ms": request.carrier_sense_duration_ms,
        "lora_payload_bytes": request.lora_payload_bytes,
        "lora_spreading_factor": request.lora_spreading_factor,
        "lora_bandwidth_hz": request.lora_bandwidth_hz,
        "lora_coding_rate_denominator": request.lora_coding_rate_denominator,
        "lora_preamble_symbols": request.lora_preamble_symbols,
        "lora_explicit_header": request.lora_explicit_header,
        "lora_crc_enabled": request.lora_crc_enabled,
        "lora_low_data_rate_optimize": request.lora_low_data_rate_optimize,
    }


def simulation_request_from_dict(data: dict[str, Any]) -> SimulationRequest:
    return SimulationRequest(
        num_runs=int(data["num_runs"]),
        seed=int(data["seed"]),
        coupling_function=str(data["coupling_function"]),
        coupling_strength=int(data["coupling_strength"]),
        strength_ratio=float(data["strength_ratio"]),
        cycle_time=int(data["cycle_time"]),
        listening_rate=int(data["listening_rate"]),
        device_count=int(data["device_count"]),
        duration=int(data["duration"]),
        start_step_count=int(data["start_step_count"]),
        start_step=int(data["start_step"]),
        tags=tuple(str(tag) for tag in data.get("tags", [])),
        output_root=resolve_project_path(data.get("output_root", "data/runs")),
        max_workers=int(data.get("max_workers", 0)),
        start_timing_mode=str(data.get("start_timing_mode", "random")),  # type: ignore[arg-type]
        fixed_start_times=tuple(int(value) for value in data.get("fixed_start_times", [])),
        fixed_start_interval=int(data.get("fixed_start_interval", data.get("start_step", 10))),
        fixed_start_offset=int(data.get("fixed_start_offset", 0)),
        simulation_mode=str(data.get("simulation_mode", "standard")),  # type: ignore[arg-type]
        carrier_sense_duration_ms=float(data.get("carrier_sense_duration_ms", 0.0)),
        lora_payload_bytes=int(data.get("lora_payload_bytes", 16)),
        lora_spreading_factor=int(data.get("lora_spreading_factor", 7)),
        lora_bandwidth_hz=int(data.get("lora_bandwidth_hz", 125_000)),
        lora_coding_rate_denominator=int(data.get("lora_coding_rate_denominator", 5)),
        lora_preamble_symbols=int(data.get("lora_preamble_symbols", 8)),
        lora_explicit_header=bool(data.get("lora_explicit_header", True)),
        lora_crc_enabled=bool(data.get("lora_crc_enabled", True)),
        lora_low_data_rate_optimize=data.get("lora_low_data_rate_optimize"),
    )


def create_simulation_job(
    requests: list[SimulationRequest],
    job_dir: str | Path = SIMULATION_JOB_DIR,
) -> tuple[str, Path]:
    resolved_job_dir = resolve_project_path(job_dir)
    job_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    job_path = resolved_job_dir / f"{job_id}.json"
    total_runs = sum(request.num_runs for request in requests)
    payload = {
        "job_id": job_id,
        "status": "queued",
        "pid": None,
        "created_at": _now_iso(),
        "started_at": None,
        "updated_at": _now_iso(),
        "finished_at": None,
        "total_conditions": len(requests),
        "current_condition": 0,
        "total_runs": total_runs,
        "completed_runs": 0,
        "current_run_id": "",
        "error": "",
        "requests": [simulation_request_to_dict(request) for request in requests],
        "results": [],
    }
    _atomic_write_json(job_path, payload)
    return job_id, job_path


def load_simulation_job_status(job_path: str | Path) -> dict[str, Any]:
    path = Path(job_path)
    last_error: OSError | json.JSONDecodeError | None = None
    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.02 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return {}


def load_simulation_job_statuses(
    job_dir: str | Path = SIMULATION_JOB_DIR,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    resolved_job_dir = resolve_project_path(job_dir)
    if not resolved_job_dir.exists():
        return []

    statuses: list[dict[str, Any]] = []
    for path in resolved_job_dir.glob("*.json"):
        try:
            status = load_simulation_job_status(path)
        except (OSError, json.JSONDecodeError, PermissionError):
            continue
        status["_path"] = str(path)
        statuses.append(status)

    statuses.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return statuses[:limit] if limit is not None else statuses


def run_simulation_job_file(job_path: str | Path) -> int:
    path = Path(job_path)
    payload = load_simulation_job_status(path)
    requests = [simulation_request_from_dict(item) for item in payload.get("requests", [])]
    total_runs = sum(request.num_runs for request in requests)
    results: list[dict[str, Any]] = []

    payload.update(
        {
            "status": "running",
            "pid": os.getpid(),
            "started_at": payload.get("started_at") or _now_iso(),
            "updated_at": _now_iso(),
            "total_conditions": len(requests),
            "total_runs": total_runs,
            "completed_runs": 0,
            "current_condition": 0,
            "current_run_id": "",
            "error": "",
            "results": [],
        }
    )
    _atomic_write_json(path, payload)

    completed_runs = 0
    try:
        for condition_index, request in enumerate(requests, start=1):
            payload["current_condition"] = condition_index
            payload["updated_at"] = _now_iso()
            _atomic_write_json(path, payload)

            def on_progress(completed: int, total: int, result: dict[str, Any]) -> None:
                nonlocal completed_runs
                completed_runs += 1
                result_row = {
                    "condition_index": condition_index,
                    "condition_count": len(requests),
                    **result,
                }
                results.append(result_row)
                payload.update(
                    {
                        "status": "running",
                        "completed_runs": completed_runs,
                        "current_run_id": str(result.get("run_id", "")),
                        "updated_at": _now_iso(),
                        "results": results,
                    }
                )
                _atomic_write_json(path, payload)

            run_simulation_request(request, progress_callback=on_progress)

        payload.update(
            {
                "status": "completed",
                "completed_runs": completed_runs,
                "current_run_id": "",
                "updated_at": _now_iso(),
                "finished_at": _now_iso(),
                "results": results,
            }
        )
        _atomic_write_json(path, payload)
        return 0
    except Exception as exc:
        payload.update(
            {
                "status": "failed",
                "updated_at": _now_iso(),
                "finished_at": _now_iso(),
                "error": f"{exc}\n{traceback.format_exc()}",
                "results": results,
            }
        )
        _atomic_write_json(path, payload)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m research_program.simulation.jobs <job-json>", file=sys.stderr)
        return 2
    return run_simulation_job_file(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
