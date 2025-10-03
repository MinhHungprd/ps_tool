@echo off
setlocal

REM Đi tới thư mục chứa file .bat
cd /d "%~dp0"

REM Kích hoạt virtual env
call "venv\Scripts\activate.bat"

REM Chạy ứng dụng
python run.py
