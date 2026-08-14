# 分轨工作台 Stem Studio (AI_MUSIC_TOOL)

把整首歌的 WAV 用 AI 拆成 8 条分轨，在浏览器里查看波形、静音/独奏/调音量、实时试听，并导出混合后的新 WAV。全程本地运行（NVIDIA GPU 加速，无 GPU 自动退回 CPU）。

## 安装（新机器）

仓库只含核心代码，大文件（PyTorch、推理框架、模型权重）由脚本自动下载：

1. 安装 [uv](https://docs.astral.sh/uv/)：`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`（还需要 git）
2. `git clone https://github.com/johnnyqiuyun/AI_MUSIC_TOOL.git`
3. 双击 `setup.bat`（装依赖约 2.4GB + 模型权重约 300MB；Demucs 权重首次分离时自动下载）

## 使用

1. 把歌曲 WAV 放进仓库的**上级目录**（或设置环境变量 `STEM_STUDIO_SRC` 指向任意歌曲目录）
2. 双击 `start.bat`（自动打开浏览器 http://127.0.0.1:8765）
3. 点「开始分离」，等几分钟（GPU 加速），完成后自动进入混音台
4. 调好 M（静音）/ S（独奏）/ 推子后点「导出混音 WAV」

## 分轨

| 轨道 | 来源 |
|------|------|
| 人声 / 鼓 / 贝斯 / 吉他 / 钢琴 | 第一级：Demucs `htdemucs_6s`（官方模型） |
| 小号 / 弦乐 | 第二级：BS-Roformer MVSep-Mega 单 stem 模型（社区模型，标注「实验性」） |
| 其他 | 第一级「其他」减去小号与弦乐的残余，保证所有轨之和 ≈ 原曲 |

所有分轨输出 44.1kHz / 24bit WAV，存放在 `jobs/<歌名>/`，导出的混音在 `jobs/<歌名>/exports/`。

## 结构

```
server/   FastAPI 后端: app.py(接口) pipeline.py(两级分离) mixdown.py(混音导出) peaks.py(波形)
static/   前端: index.html app.js style.css
models/   msst(推理框架) ckpt(小号/弦乐/铜管模型权重)
tests/    单元测试: .venv\Scripts\python.exe tests\test_mixdown_peaks.py
jobs/     分离输出
```

## 备注

- 换用铜管整体模型（小号+圆号等）：把 `server/pipeline.py` 里 `STAGE2_MODELS` 的
  `bs_mega_53stem_trumpet_mvsep` 改成 `bs_mega_53stem_brass_mvsep`（权重已下载好）。
- 无 NVIDIA GPU 时自动退回 CPU（慢很多但可用）。
- 端口冲突时改 `start.bat` 里的 `--port`。
