from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from typing import Any
import uuid

from research_program.config.paths import resolve_project_path
from research_program.io.figures import discover_figures


GRAPH_CREATION_JOB_DIR = Path("outputs/reports/graph_creation_jobs")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"


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


def _load_json(path: Path) -> dict[str, Any]:
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


def _python_subprocess_env(env_overrides: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    pythonpath_parts = [str(SRC_ROOT)]
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env["MPLBACKEND"] = "Agg"
    env.update(env_overrides)
    return env


def _run_research_program_command(command_name: str, env_overrides: dict[str, str]) -> tuple[bool, str]:
    completed = subprocess.run(
        [sys.executable, "-m", "research_program.cli", command_name],
        cwd=PROJECT_ROOT,
        env=_python_subprocess_env(env_overrides),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = "\n".join(part.strip() for part in [completed.stdout, completed.stderr] if part.strip())
    return completed.returncode == 0, output or "完了しました(Completed)."


def _copy_selected_runs_to_temp(run_paths: list[str], temp_runs_dir: Path) -> None:
    temp_runs_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    for index, run_path_text in enumerate(run_paths):
        run_path = Path(run_path_text)
        run_dir_name = run_path.name
        if run_dir_name in used_names:
            run_dir_name = f"{run_dir_name}_{index:04d}"
        used_names.add(run_dir_name)
        shutil.copytree(run_path, temp_runs_dir / run_dir_name)


def _figure_snapshot(figure_dirs: list[str], extensions: list[str]) -> dict[str, tuple[int, int]]:
    return {
        str(asset.path.resolve()): (asset.path.stat().st_mtime_ns, asset.size_bytes)
        for asset in discover_figures(figure_dirs, extensions=extensions)
    }


def _changed_figure_count(
    figure_dirs: list[str],
    extensions: list[str],
    before_snapshot: dict[str, tuple[int, int]],
) -> int:
    count = 0
    for asset in discover_figures(figure_dirs, extensions=extensions):
        key = str(asset.path.resolve())
        if before_snapshot.get(key) != (asset.path.stat().st_mtime_ns, asset.size_bytes):
            count += 1
    return count


def create_graph_creation_job(
    *,
    commands: list[str],
    selected_graph_commands: list[str],
    selected_run_paths: list[str],
    all_run_count: int,
    env_overrides: dict[str, str],
    figure_dirs: list[str],
    figure_extensions: list[str],
    job_dir: str | Path = GRAPH_CREATION_JOB_DIR,
) -> tuple[str, Path]:
    resolved_job_dir = resolve_project_path(job_dir)
    job_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    job_path = resolved_job_dir / f"{job_id}.json"
    payload = {
        "job_id": job_id,
        "status": "queued",
        "pid": None,
        "created_at": _now_iso(),
        "started_at": None,
        "updated_at": _now_iso(),
        "finished_at": None,
        "commands": commands,
        "selected_graph_commands": selected_graph_commands,
        "total_commands": len(commands),
        "completed_commands": 0,
        "current_command": "",
        "selected_run_count": len(selected_run_paths),
        "all_run_count": all_run_count,
        "env_overrides": env_overrides,
        "selected_run_paths": selected_run_paths,
        "figure_dirs": figure_dirs,
        "figure_extensions": figure_extensions,
        "generated_or_updated_figures": 0,
        "error": "",
        "results": [],
    }
    _atomic_write_json(job_path, payload)
    return job_id, job_path


def load_graph_creation_job_status(job_path: str | Path) -> dict[str, Any]:
    return _load_json(Path(job_path))


def load_graph_creation_job_statuses(
    job_dir: str | Path = GRAPH_CREATION_JOB_DIR,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    resolved_job_dir = resolve_project_path(job_dir)
    if not resolved_job_dir.exists():
        return []
    statuses: list[dict[str, Any]] = []
    for path in resolved_job_dir.glob("*.json"):
        try:
            status = load_graph_creation_job_status(path)
        except (OSError, json.JSONDecodeError, PermissionError):
            continue
        status["_path"] = str(path)
        statuses.append(status)
    statuses.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return statuses[:limit] if limit is not None else statuses


def run_graph_creation_job_file(job_path: str | Path) -> int:
    path = Path(job_path)
    payload = load_graph_creation_job_status(path)
    commands = [str(command) for command in payload.get("commands", [])]
    selected_run_paths = [str(item) for item in payload.get("selected_run_paths", [])]
    all_run_count = int(payload.get("all_run_count") or 0)
    figure_dirs = [str(item) for item in payload.get("figure_dirs", [])]
    figure_extensions = [str(item) for item in payload.get("figure_extensions", [])]
    env_overrides = {str(key): str(value) for key, value in dict(payload.get("env_overrides") or {}).items()}
    uses_subset = len(selected_run_paths) != all_run_count
    work_dir = path.with_suffix(".work")
    log_path = path.with_suffix(".log")
    results: list[dict[str, str]] = []
    failed_count = 0

    payload.update(
        {
            "status": "running",
            "pid": os.getpid(),
            "started_at": payload.get("started_at") or _now_iso(),
            "updated_at": _now_iso(),
            "completed_commands": 0,
            "current_command": "",
            "error": "",
            "results": [],
        }
    )
    _atomic_write_json(path, payload)
    before_snapshot = _figure_snapshot(figure_dirs, figure_extensions)

    try:
        command_env = dict(env_overrides)
        if uses_subset:
            payload["current_command"] = "対象runを準備中(Preparing selected runs)"
            payload["updated_at"] = _now_iso()
            _atomic_write_json(path, payload)
            if work_dir.exists():
                shutil.rmtree(work_dir)
            temp_runs_dir = work_dir / "runs"
            temp_aggregated_dir = work_dir / "aggregated"
            _copy_selected_runs_to_temp(selected_run_paths, temp_runs_dir)
            temp_aggregated_dir.mkdir(parents=True, exist_ok=True)
            command_env.update(
                {
                    "RESEARCH_PROGRAM_RUNS_DIR": str(temp_runs_dir),
                    "RESEARCH_PROGRAM_AGGREGATED_DIR": str(temp_aggregated_dir),
                    "RESEARCH_PROGRAM_FORCE_RECALCULATE": "1",
                }
            )

        for index, command_name in enumerate(commands, start=1):
            payload.update(
                {
                    "current_command": command_name,
                    "completed_commands": index - 1,
                    "updated_at": _now_iso(),
                }
            )
            _atomic_write_json(path, payload)
            ok, output = _run_research_program_command(command_name, command_env)
            status_text = "完了(Done)" if ok else "失敗(Failed)"
            if not ok:
                failed_count += 1
            message = output.splitlines()[0] if output else ""
            result = {
                "command": command_name,
                "status": status_text,
                "message": message,
            }
            results.append(result)
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"$ research-program {command_name}\n{output}\n\n")
            payload.update(
                {
                    "completed_commands": index,
                    "current_command": command_name,
                    "updated_at": _now_iso(),
                    "results": results,
                }
            )
            _atomic_write_json(path, payload)

        changed_count = _changed_figure_count(figure_dirs, figure_extensions, before_snapshot)
        payload.update(
            {
                "status": "completed_with_errors" if failed_count else "completed",
                "current_command": "",
                "updated_at": _now_iso(),
                "finished_at": _now_iso(),
                "generated_or_updated_figures": changed_count,
                "results": results,
            }
        )
        _atomic_write_json(path, payload)
        return 1 if failed_count else 0
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
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m research_program.plotting.jobs <job-json>", file=sys.stderr)
        return 2
    return run_graph_creation_job_file(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
