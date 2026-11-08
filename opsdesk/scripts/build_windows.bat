@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\.."

echo [1/3] 安装依赖...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

echo [2/3] 打包 Windows 可执行程序...
python -m PyInstaller --noconfirm --clean --windowed --name OpsDesk --collect-all psutil app.py
if errorlevel 1 goto :fail

echo [3/3] 完成
echo 输出目录：%CD%\dist\OpsDesk
echo 将整个 OpsDesk 文件夹压缩后上传到仓库 ops/releases/files/ 即可分发。
pause
exit /b 0

:fail
echo 构建失败，请检查 Python 与网络环境。
pause
exit /b 1
