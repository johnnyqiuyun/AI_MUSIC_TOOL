"""两级 AI 分离流水线。

第一级: Demucs htdemucs_6s → 人声/鼓/贝斯/吉他/钢琴/其他
第二级: BS-Roformer (MVSep-Mega 单stem模型) 对「其他」轨提取 小号、弦乐
残余:   其他 = 第一级其他 − 小号 − 弦乐（保证各轨之和≈原曲）

任务状态存内存字典, 由 FastAPI 轮询接口读取。
"""
import json
import math
import os
import re
import shutil
import sys
import threading
import traceback

import numpy as np
import soundfile as sf

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MSST_DIR = os.path.join(BASE, "models", "msst")
CKPT_DIR = os.path.join(BASE, "models", "ckpt")
JOBS_DIR = os.path.join(BASE, "jobs")

# 第一级 Demucs 轨道中文名
DEMUCS_LABELS = {
    "vocals": "人声", "drums": "鼓", "bass": "贝斯",
    "guitar": "吉他", "piano": "钢琴", "other": "其他",
}
# 第二级: (轨道id, 中文名, ckpt文件名前缀)
STAGE2_MODELS = [
    ("trumpet", "小号", "bs_mega_53stem_trumpet_mvsep"),
    ("strings", "弦乐", "bs_mega_53stem_bowed_strings_mvsep"),
]
# 最终轨道顺序（界面显示顺序）
TRACK_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "trumpet", "strings", "other"]
TRACK_LABELS = {**DEMUCS_LABELS, "trumpet": "小号", "strings": "弦乐"}
EXPERIMENTAL = {"trumpet", "strings"}  # 社区模型, 标注实验性

ACTIVE_THRESHOLD_DB = -50.0  # 整轨RMS低于此值视为「基本无内容」
PEAK_BUCKETS = 4000

JOBS = {}  # job_id -> {stage, percent, done, error}
QUEUE_FILE = os.path.join(JOBS_DIR, "_queue.json")

# GPU 一次只跑一首: 单工作线程 + 排队列表
_queue_cv = threading.Condition()
_pending = []  # [(job_id, src_path), ...] 等待中
_current = None  # 正在跑的 job_id
_worker_started = False


def job_id_for(src_path: str, owner: str | None = None) -> str:
    stem = os.path.splitext(os.path.basename(src_path))[0]
    slug = re.sub(r"[^\w一-鿿\-]+", "_", stem)
    return f"{owner}__{slug}" if owner else slug


def job_dir(job_id: str) -> str:
    return os.path.join(JOBS_DIR, job_id)


def job_finished(job_id: str) -> bool:
    return os.path.exists(os.path.join(job_dir(job_id), "meta.json"))


def _set(job_id, stage=None, percent=None, done=None, error=None):
    st = JOBS.setdefault(job_id, {"stage": "", "percent": 0.0, "done": False, "error": None})
    if stage is not None:
        st["stage"] = stage
    if percent is not None:
        st["percent"] = round(float(percent), 1)
    if done is not None:
        st["done"] = done
    if error is not None:
        st["error"] = error


def _save_queue():
    """排队列表落盘, 服务重启后可恢复。调用方需持有 _queue_cv。"""
    try:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump([{"job_id": j, "src": s} for j, s in _pending], f, ensure_ascii=False)
    except OSError:
        pass


def _ensure_worker():
    global _worker_started
    if not _worker_started:
        _worker_started = True
        threading.Thread(target=_worker, daemon=True).start()


def _worker():
    global _current
    while True:
        with _queue_cv:
            while not _pending:
                _queue_cv.wait()
            jid, src = _pending.pop(0)
            _current = jid
            _save_queue()
        try:
            _separate(jid, src)
            _set(jid, stage="完成", percent=100, done=True)
        except Exception:
            _set(jid, error=traceback.format_exc(), stage="出错", done=True)
        with _queue_cv:
            _current = None


def start_job(src_path: str, force: bool = False, owner: str | None = None):
    """把分离任务加入队列。返回 (job_id, started)。"""
    jid = job_id_for(src_path, owner)
    with _queue_cv:
        if job_finished(jid) and not force:
            return jid, False
        if jid == _current or any(j == jid for j, _ in _pending):
            return jid, False  # 已在跑或已排队
        JOBS[jid] = {"stage": "排队中", "percent": 0.0, "done": False, "error": None}
        _pending.append((jid, src_path))
        _save_queue()
        _queue_cv.notify()
    _ensure_worker()
    return jid, True


def queue_position(job_id: str) -> int:
    """0 = 正在跑或不在队列; N = 排在第 N 位。"""
    with _queue_cv:
        for i, (j, _) in enumerate(_pending):
            if j == job_id:
                return i + 1
    return 0


def restore_state():
    """服务启动时调用: 清理上次中断的半成品目录, 恢复未完成的排队任务。"""
    for d in os.listdir(JOBS_DIR):
        p = os.path.join(JOBS_DIR, d)
        if os.path.isdir(p) and not os.path.exists(os.path.join(p, "meta.json")):
            shutil.rmtree(p, ignore_errors=True)
    entries = []
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, ValueError):
            entries = []
    with _queue_cv:
        for e in entries:
            if os.path.isfile(e.get("src", "")) and not job_finished(e["job_id"]):
                JOBS[e["job_id"]] = {"stage": "排队中", "percent": 0.0, "done": False, "error": None}
                _pending.append((e["job_id"], e["src"]))
        _save_queue()
        if _pending:
            _queue_cv.notify()
    _ensure_worker()


# ---------------------------------------------------------------- 第一级 Demucs

def _demucs_separate(wav_ct, sr, device, progress_cb):
    """wav_ct: torch float32 (ch, t)。返回 ({source: np(ch,t)}, 44100)。"""
    import torch
    import torchaudio
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    model = get_model("htdemucs_6s")
    model.to(device)
    model.eval()
    if sr != model.samplerate:
        wav_ct = torchaudio.functional.resample(wav_ct, sr, model.samplerate)
        sr = model.samplerate

    ref_mean, ref_std = wav_ct.mean(), wav_ct.std() + 1e-8
    norm = (wav_ct - ref_mean) / ref_std

    _t = torch
    C, T = norm.shape
    chunk = int(30.0 * sr)
    ov = int(3.0 * sr)
    step = chunk - ov
    n_src = len(model.sources)
    n_chunks = max(1, math.ceil(max(T - ov, 1) / step))
    idx = 0
    pos = 0
    out = _t.zeros(n_src, C, T)
    wsum = _t.zeros(T)
    while pos < T:
        end = min(pos + chunk, T)
        seg = norm[:, pos:end]
        with _t.no_grad():
            res = apply_model(model, seg[None], device=device, split=True,
                              overlap=0.25, progress=False)[0].cpu()
        w = _t.ones(end - pos)
        if pos > 0:
            w[:ov] = _t.linspace(0.0, 1.0, ov)
        if end < T:
            w[-ov:] = _t.linspace(1.0, 0.0, ov)
        out[:, :, pos:end] += res * w
        wsum[pos:end] += w
        idx += 1
        progress_cb(min(idx / n_chunks, 1.0))
        if end >= T:
            break
        pos += step
    out /= wsum.clamp_min(1e-8)
    out = out * ref_std + ref_mean  # 还原归一化
    sources = list(model.sources)
    del model
    if device == "cuda":
        _t.cuda.empty_cache()
    return {s: out[i].numpy() for i, s in enumerate(sources)}, sr


# ---------------------------------------------------------------- 第二级 BS-Roformer

def _msst_extract(ckpt_base: str, wav_ct_np, device, progress_cb):
    """对 (ch, t) numpy 波形跑单stem BS-Roformer。返回 np (ch, t)。"""
    import torch
    if MSST_DIR not in sys.path:
        sys.path.insert(0, MSST_DIR)
    from utils.settings import get_model_from_config
    import utils.model_utils as mu

    config_path = os.path.join(CKPT_DIR, ckpt_base + "_config.yaml")
    ckpt_path = os.path.join(CKPT_DIR, ckpt_base + ".ckpt")
    model, config = get_model_from_config("bs_roformer", config_path)
    try:
        state = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    except Exception:
        state = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    elif isinstance(state, dict) and "state" in state:
        state = state["state"]
    model.load_state_dict(state)
    model = model.to(device).eval()

    # 用 shim 替换 tqdm 拿到进度
    class _Shim:
        def __init__(self, total=None, desc=None, leave=False, **kw):
            self.total = total or 1
            self.n = 0
        def update(self, k):
            self.n += k
            progress_cb(min(self.n / self.total, 1.0))
        def close(self):
            progress_cb(1.0)
    old_tqdm = mu.tqdm
    mu.tqdm = _Shim
    try:
        res = mu.demix(config, model, wav_ct_np, torch.device(device),
                       model_type="bs_roformer", pbar=True)
    finally:
        mu.tqdm = old_tqdm
    target = config.training.target_instrument
    out = res[target] if isinstance(res, dict) else res
    del model
    if device == "cuda":
        torch.cuda.empty_cache()
    return np.asarray(out, dtype=np.float32)


# ---------------------------------------------------------------- 主流程

def _separate(job_id: str, src_path: str):
    import torch
    from peaks import compute_peaks, activity_db
    from mixdown import write_wav24

    device = "cuda" if torch.cuda.is_available() else "cpu"
    out_dir = job_dir(job_id)
    os.makedirs(out_dir, exist_ok=True)

    _set(job_id, stage="读取音频", percent=1)
    data, sr = sf.read(src_path, dtype="float32", always_2d=True)  # (t, ch)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    wav_ct = torch.from_numpy(data.T.copy())  # (ch, t)
    del data

    _set(job_id, stage="第一级分离 (Demucs 6轨)", percent=2)
    stems, out_sr = _demucs_separate(
        wav_ct, sr, device,
        lambda p: _set(job_id, percent=2 + p * 58),
    )
    del wav_ct

    other = stems["other"]
    extracted = {}
    base_pct = 60
    per_model = 15
    for i, (tid, label, ckpt_base) in enumerate(STAGE2_MODELS):
        _set(job_id, stage=f"第二级分离 ({label})", percent=base_pct + i * per_model)
        lo = base_pct + i * per_model
        extracted[tid] = _msst_extract(
            ckpt_base, other, device,
            lambda p, lo=lo: _set(job_id, percent=lo + p * per_model),
        )

    _set(job_id, stage="计算残余轨与波形", percent=90)
    residual = other.copy()
    for tid, arr in extracted.items():
        n = min(residual.shape[1], arr.shape[1])
        residual[:, :n] -= arr[:, :n]
    stems["other"] = residual
    stems.update(extracted)

    # 写文件 + 峰值 + 元数据
    all_peaks = {}
    tracks_meta = []
    n_tracks = len(TRACK_ORDER)
    for i, tid in enumerate(TRACK_ORDER):
        arr = stems[tid]  # (ch, t)
        wav_tc = arr.T  # (t, ch)
        write_wav24(os.path.join(out_dir, f"{tid}.wav"), wav_tc, out_sr)
        all_peaks[tid] = compute_peaks(wav_tc, PEAK_BUCKETS)
        db = activity_db(wav_tc)
        tracks_meta.append({
            "id": tid,
            "label": TRACK_LABELS[tid],
            "file": f"{tid}.wav",
            "rms_db": round(db, 1),
            "active": bool(db > ACTIVE_THRESHOLD_DB),
            "experimental": tid in EXPERIMENTAL,
        })
        _set(job_id, percent=90 + (i + 1) / n_tracks * 9)

    duration = stems[TRACK_ORDER[0]].shape[1] / out_sr
    meta = {
        "name": job_id,
        "source_file": os.path.basename(src_path),
        "duration": round(duration, 2),
        "samplerate": out_sr,
        "tracks": tracks_meta,
    }
    with open(os.path.join(out_dir, "peaks.json"), "w", encoding="utf-8") as f:
        json.dump(all_peaks, f)
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
