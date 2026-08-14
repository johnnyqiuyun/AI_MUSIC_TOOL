"""Stem Studio 后端服务。

启动: .venv\\Scripts\\python.exe -m uvicorn app:app --app-dir server --host 0.0.0.0 --port 8765
"""
import json
import os
import re
import time

import soundfile as sf
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import pipeline
from mixdown import mix_stems, resolve_gains, write_wav24

BASE = pipeline.BASE
SRC_DIR = os.environ.get("STEM_STUDIO_SRC") or os.path.dirname(BASE)  # 共享曲库目录，默认仓库上级
UPLOADS_DIR = os.path.join(BASE, "uploads")
STATIC_DIR = os.path.join(BASE, "static")
JOBS_DIR = pipeline.JOBS_DIR

ALLOWED_EXT = {".wav", ".flac", ".mp3"}
MAX_UPLOAD_MB = 500

os.makedirs(JOBS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
app = FastAPI(title="Stem Studio")
pipeline.restore_state()  # 恢复上次未完成的排队任务


class SeparateReq(BaseModel):
    file: str
    scope: str = "shared"  # shared=共享曲库 mine=我的上传
    force: bool = False


class TrackState(BaseModel):
    gain: float = 1.0
    mute: bool = False
    solo: bool = False


class ExportReq(BaseModel):
    tracks: dict[str, TrackState]


def _owner(client_id: str | None) -> str | None:
    """浏览器令牌 → 目录安全的归属标识。"""
    if not client_id:
        return None
    o = re.sub(r"[^a-zA-Z0-9]", "", client_id)[:12].lower()
    return o or None


def _safe_name(filename: str) -> str:
    name = os.path.basename(filename or "")
    name = re.sub(r"[^\w一-鿿\-. ()]+", "_", name).strip()
    return name or "untitled.wav"


def _resolve_src(file: str, scope: str, owner: str | None) -> tuple[str, str | None]:
    """返回 (源文件路径, 任务归属owner)。共享曲库的任务不带owner前缀, 所有人共享。"""
    fname = _safe_name(file)
    if scope == "mine":
        if not owner:
            raise HTTPException(400, "缺少客户端标识")
        return os.path.join(UPLOADS_DIR, owner, fname), owner
    return os.path.join(SRC_DIR, fname), None


def _file_entry(path: str, scope: str, owner: str | None):
    try:
        info = sf.info(path)
    except Exception:
        return None
    jid = pipeline.job_id_for(path, owner)
    return {
        "name": os.path.basename(path),
        "scope": scope,
        "size": os.path.getsize(path),
        "duration": round(info.frames / info.samplerate, 1),
        "samplerate": info.samplerate,
        "job_id": jid,
        "separated": pipeline.job_finished(jid),
    }


@app.get("/api/files")
def list_files(x_client_id: str | None = Header(default=None)):
    """我的上传 + 共享曲库（服务器本地目录，所有人可见）。"""
    owner = _owner(x_client_id)
    out = []
    if owner:
        mydir = os.path.join(UPLOADS_DIR, owner)
        if os.path.isdir(mydir):
            for fn in sorted(os.listdir(mydir)):
                if os.path.splitext(fn)[1].lower() in ALLOWED_EXT:
                    e = _file_entry(os.path.join(mydir, fn), "mine", owner)
                    if e:
                        out.append(e)
    for fn in sorted(os.listdir(SRC_DIR)):
        p = os.path.join(SRC_DIR, fn)
        if os.path.isfile(p) and os.path.splitext(fn)[1].lower() in ALLOWED_EXT:
            e = _file_entry(p, "shared", None)
            if e:
                out.append(e)
    return out


@app.post("/api/upload")
def upload(file: UploadFile = File(...), x_client_id: str | None = Header(default=None)):
    owner = _owner(x_client_id)
    if not owner:
        raise HTTPException(400, "缺少客户端标识，请刷新页面重试")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "仅支持 WAV / FLAC / MP3")
    mydir = os.path.join(UPLOADS_DIR, owner)
    os.makedirs(mydir, exist_ok=True)
    name = _safe_name(file.filename)
    stem, ext2 = os.path.splitext(name)
    dest = os.path.join(mydir, name)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(mydir, f"{stem}({n}){ext2}")

    limit = MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, f"文件超过 {MAX_UPLOAD_MB}MB 上限")
                out.write(chunk)
        sf.info(dest)  # 校验能否解析
    except HTTPException:
        if os.path.exists(dest):
            os.remove(dest)
        raise
    except Exception:
        if os.path.exists(dest):
            os.remove(dest)
        raise HTTPException(400, "无法解析的音频文件")
    return _file_entry(dest, "mine", owner)


@app.post("/api/separate")
def separate(req: SeparateReq, x_client_id: str | None = Header(default=None)):
    path, job_owner = _resolve_src(req.file, req.scope, _owner(x_client_id))
    if not os.path.isfile(path):
        raise HTTPException(404, "文件不存在")
    jid, started = pipeline.start_job(path, force=req.force, owner=job_owner)
    return {"job_id": jid, "started": started, "finished": pipeline.job_finished(jid)}


@app.get("/api/job/{job_id}/status")
def job_status(job_id: str):
    st = pipeline.JOBS.get(job_id)
    if st is not None:
        return {**st, "queue_pos": pipeline.queue_position(job_id)}
    if pipeline.job_finished(job_id):
        return {"stage": "完成", "percent": 100, "done": True, "error": None, "queue_pos": 0}
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
