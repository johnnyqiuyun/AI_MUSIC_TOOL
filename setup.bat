@echo off
rem Stem Studio 环境一键搭建（克隆仓库后运行一次）
rem 依赖: git、curl（Win10+ 自带）、uv（https://docs.astral.sh/uv/）
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo 未找到 uv，请先安装:
  echo   powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  pause
  exit /b 1
)

echo [1/4] 创建 Python 3.12 虚拟环境...
uv venv .venv --python 3.12
if errorlevel 1 (pause & exit /b 1)

echo [2/4] 安装 PyTorch CUDA 12.6 版（约2.4GB，无NVIDIA显卡可改装CPU版）...
uv pip install --python .venv torch torchaudio --index-url https://download.pytorch.org/whl/cu126
if errorlevel 1 (pause & exit /b 1)

echo [3/4] 安装其余依赖...
uv pip install --python .venv -r requirements.txt
if errorlevel 1 (pause & exit /b 1)

echo [4/4] 下载 MSST 推理框架与小号/弦乐/铜管模型权重（约300MB）...
if not exist models mkdir models
if not exist models\msst git clone --depth 1 https://github.com/ZFTurbo/Music-Source-Separation-Training.git models\msst
if not exist models\ckpt mkdir models\ckpt
set BASE=https://huggingface.co/noblebarkrr/BS-Roformer-MVSep-Mega-53-stems/resolve/main/v1
for %%s in (trumpet bowed_strings brass) do (
  if not exist models\ckpt\bs_mega_53stem_%%s_mvsep.ckpt curl -L "%BASE%/bs_mega_53stem_%%s_mvsep.ckpt" -o models\ckpt\bs_mega_53stem_%%s_mvsep.ckpt
  if not exist models\ckpt\bs_mega_53stem_%%s_mvsep_config.yaml curl -L "%BASE%/bs_mega_53stem_%%s_mvsep_config.yaml" -o models\ckpt\bs_mega_53stem_%%s_mvsep_config.yaml
)

echo.
echo 完成！Demucs 权重会在首次分离时自动下载。双击 start.bat 启动。
pause
