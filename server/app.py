"""Stem Studio 后端服务。

启动: .venv\\Scripts\\python.exe -m uvicorn app:app --app-dir server --port 8765
"""
import json
import os
import time

import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import pipeline
from mixdown import mix_stems, resolve_gains, write_wav24

BASE = pipeline.BASE
SRC_DIR = os.environ.get("STEM_STUDIO_SRC") or os.path.dirname(BASE)  # 歌曲源目录，默认仓库上级
STATIC_DIR = os.path.join(BASE, "static")
JOBS_DIR = pipeline.JOBS_DIR

os.makedirs(JOBS_DIR, exist_ok=True)
app = FastAPI(title="Stem Studio")


class SeparateReq(BaseModel):
    file: str
    force: bool = False


class TrackState(BaseModel):
    gain: float = 1.0
    mute: bool = False
    solo: bool = False


class ExportReq(BaseModel):
    tracks: dict[str, TrackState]


@app.get("/api/files")
def list_files():
    """列出源目录下的 WAV 文件及其分离状态。"""
    out = []
    for fn in sorted(os.listdir(SRC_DIR)):
        if not fn.lower().endswith(".wav"):
            continue
        p = os.path.join(SRC_DIR, fn)
        if not os.path.isfile(p):
            continue
        try:
            info = sf.info(p)
        except Exception:
            continue
        jid = pipeline.job_id_for(fn)
        out.append({
            "name": fn,
            "size": os.path.getsize(p),
            "duration": round(info.frames / info.samplerate, 1),
            "samplerate": info.samplerate,
            "job_id": jid,
            "separated": pipeline.job_finished(jid),
        })
    return out


@app.post("/api/separate")
def separate(req: SeparateReq):
    path = os.path.join(SRC_DIR, req.file)
    if not os.path.isfile(path):
        raise HTTPException(404, "文件不存在")
    jid, started = pipeline.start_job(path, force=req.force)
    return {"job_id": jid, "started": started, "finished": pipeline.job_finished(jid)}


@app.get("/api/job/{job_id}/status")
def job_status(job_id: str):
    st = pipeline.JOBS.get(job_id)
    if st is not None:
        return st
    if pipeline.job_finished(job_id):
        return {"stage": "完成", "percent": 100, "done": True, "error": None}
    raise HTTPException(404, "任务不存在")


@app.post("/api/job/{job_id}/export")
def export(job_id: str, req: ExportReq):
    jdir = pipeline.job_dir(job_id)
    meta_path = os.path.join(jdir, "meta.json")
    if not os.path.exists(meta_path):
        raise HTTPException(404, "该任务尚未完成分离")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    paths = {t["id"]: os.path.join(jdir, t["file"]) for t in meta["tracks"]}
    state = {k: v.model_dump() for k, v in req.tracks.items() if k in paths}
    gains = resolve_gains(state) if state else {}
    # 请求里没提到的轨视为不参与混音
    full_gains = {k: 0.0 for k in paths}
    full_gains.update(gains)
    mixed, sr, clipped = mix_stems(paths, full_gains)
    ts = time.strftime("%Y%m%d_%H%M%S")
    name = f"{job_id}_mix_{ts}.wav"
    exp_dir = os.path.join(jdir, "exports")
    os.makedirs(exp_dir, exist_ok=True)
    write_wav24(os.path.join(exp_dir, name), mixed, sr)
    return {"url": f"/jobs/{job_id}/exports/{name}", "clipped": clipped, "filename": name}


app.mount("/jobs", StaticFiles(directory=JOBS_DIR), name="jobs")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
