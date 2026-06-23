@echo off
cd /d "%~dp0"
uv run streamlit run src/research_program/web/app.py
pause
