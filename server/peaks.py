"""波形峰值预计算：把长音频压成固定桶数的 min/max 序列，供前端 canvas 绘制。"""
import numpy as np


def compute_peaks(data: np.ndarray, buckets: int = 4000) -> list:
    """data: (n,) 或 (n, ch) 浮点波形 → [[min, max], ...] 长度恒为 buckets。"""
    if data.ndim == 2:
        mono = data.mean(axis=1)
    else:
        mono = data
    n = mono.shape[0]
    out = []
    if n == 0:
        return [[0.0, 0.0]] * buckets
    edges = np.linspace(0, n, buckets + 1).astype(np.int64)
    for i in range(buckets):
        a, b = edges[i], edges[i + 1]
        if a >= b:
            out.append([0.0, 0.0])
        else:
            seg = mono[a:b]
            out.append([round(float(seg.min()), 4), round(float(seg.max()), 4)])
    return out


def activity_db(data: np.ndarray) -> float:
    """整轨 RMS 电平（dBFS），用于判断该轨是否基本无内容。"""
    if data.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
    return 20.0 * np.log10(rms + 1e-12)
