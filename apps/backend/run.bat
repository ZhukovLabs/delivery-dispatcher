@echo off
rem Запуск «Диспетчера доставки». При первом запуске установите зависимости:
rem   pip install -r requirements.txt
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден. Установите Python 3.10+ с python.org и повторите.
  pause
  exit /b 1
)
python -c "import flask, ortools, waitress" 2>nul
if errorlevel 1 (
  echo Устанавливаю зависимости...
  python -m pip install -r requirements.txt
)
echo Диспетчер доставки: http://127.0.0.1:5050  (закройте это окно для остановки)
python app.py
pause
