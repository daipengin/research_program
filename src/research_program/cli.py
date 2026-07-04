from __future__ import annotations

from research_program.graph_workflow import get_storage_overview, list_graph_jobs


def main() -> int:
    overview = get_storage_overview()
    print("research-program graph-first workspace")
    print(f"graph_runs_root: {overview['root']}")
    print(f"jobs: {overview['job_count']}")
    print(f"sqlite files: {overview['sqlite_count']}")
    print()
    for job in list_graph_jobs():
        key = ", ".join(f"{k}={v}" for k, v in job.graph_key.items())
        print(
            f"{job.graph_id} | {job.graph_type} | {key} | "
            f"{job.status} | {job.completed_runs}/{job.total_runs}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

