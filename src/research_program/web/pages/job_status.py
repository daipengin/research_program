from __future__ import annotations

from typing import Any

import streamlit as st

from research_program.graph_workflow.execution import (
    run_convergence_cycle_vs_k_job,
    run_interval_per_vs_k_job,
    run_phase_gap_error_vs_k_job,
)
from research_program.graph_workflow.storage import (
    delete_graph_job,
    list_graph_jobs,
    request_cancel_graph_job,
)
from research_program.web.constants import RUNNING_STATUSES
from research_program.web.utils import format_graph_key


def render_job_status_page() -> None:
    st.header("ジョブ確認")
    if st.button("Refresh status"):
        st.rerun()

    jobs = list_graph_jobs()
    if not jobs:
        st.info("No jobs yet.")
        return

    for job in jobs:
        with st.container(border=True):
            cols = st.columns([2, 1, 1, 1])
            cols[0].markdown(f"**{job.graph_id}**")
            cols[0].caption(format_graph_key(job.graph_key))
            cols[1].metric("status", job.status)
            cols[2].metric("runs", f"{job.completed_runs}/{job.total_runs}")
            cols[3].metric("aggregate", job.aggregate_count)
            st.caption(f"updated: {job.updated_at}")
            st.code(str(job.path), language="text")

            action_cols = st.columns([1, 1, 1, 1])
            if action_cols[0].button("Run", key=f"run_{job.graph_id}", disabled=job.status != "queued"):
                with st.spinner("Running job..."):
                    result = run_graph_job(job)
                if result.get("output") is None:
                    st.warning("Job was cancelled.")
                else:
                    st.success("Job completed.")
                    st.code(str(result["output"]), language="text")
                st.rerun()

            resume_disabled = not can_resume_job(job)
            if action_cols[1].button("Resume", key=f"resume_{job.graph_id}", disabled=resume_disabled):
                with st.spinner("Resuming missing runs, rebuilding aggregate, and rendering PDF..."):
                    result = run_graph_job(job)
                if result.get("output") is None:
                    st.warning("Job was cancelled.")
                else:
                    st.success("Job resumed and completed.")
                    st.code(str(result["output"]), language="text")
                st.rerun()

            cancel_disabled = job.status in {"completed", "failed", "cancelled"}
            if action_cols[2].button("Cancel", key=f"cancel_{job.graph_id}", disabled=cancel_disabled):
                request_cancel_graph_job(job.path)
                st.warning("Cancel requested. Running code checks this between run completions.")
                st.rerun()

            delete_disabled = job.status in RUNNING_STATUSES
            if action_cols[3].button("Delete history/data", key=f"delete_history_{job.graph_id}", disabled=delete_disabled):
                deleted_path = delete_graph_job(job.path)
                st.success(f"Deleted: {deleted_path}")
                st.rerun()


def can_resume_job(job: Any) -> bool:
    if job.status in RUNNING_STATUSES or job.status in {"queued", "completed"}:
        return False
    if job.status in {"cancelled", "failed"}:
        return True
    return job.completed_runs < job.total_runs


def run_graph_job(job: Any) -> dict[str, Any]:
    if job.graph_type == "convergence_cycle_vs_k":
        return run_convergence_cycle_vs_k_job(job.path)
    if job.graph_type == "phase_gap_error_vs_k":
        return run_phase_gap_error_vs_k_job(job.path)
    return run_interval_per_vs_k_job(job.path)
