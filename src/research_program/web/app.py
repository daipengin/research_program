from __future__ import annotations

import streamlit as st

from research_program.web.pages.coupling_check import render_coupling_function_page
from research_program.web.pages.job_add import render_job_add_page
from research_program.web.pages.job_status import render_job_status_page
from research_program.web.pages.management import render_management_page
from research_program.web.pages.results import render_results_page


st.set_page_config(page_title="Research Program", page_icon="RP", layout="wide")


def main() -> None:
    pages = [
        st.Page(
            render_job_add_page,
            title="ジョブ追加 / Add Job",
            url_path="job_add",
            default=True,
        ),
        st.Page(
            render_job_status_page,
            title="ジョブ確認 / Jobs",
            url_path="job_status",
        ),
        st.Page(
            render_results_page,
            title="結果・グラフ確認 / Results",
            url_path="results",
        ),
        st.Page(
            render_coupling_function_page,
            title="結合関数確認 / Coupling Check",
            url_path="coupling_check",
        ),
        st.Page(
            render_management_page,
            title="その他管理 / Management",
            url_path="management",
        ),
    ]
    selected_page = st.navigation(pages, position="sidebar")
    selected_page.run()


if __name__ == "__main__":
    main()
