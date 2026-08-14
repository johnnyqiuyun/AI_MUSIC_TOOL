"""混音导出：按每轨 增益/静音/独奏 状态求和，防削波，写 24bit WAV。"""
import numpy as np
import soundfile as sf

PEAK_CEILING = 0.999  # 防削波峰值上限


def resolve_gains(state: dict) -> dict:
    """把 {track: {gain, mute, solo}} 解析成最终线性增益 {track: float}。

    规则与真实调音台一致：
    - 存在任一 solo 轨时，非 solo 轨全部压为 0
    - mute 永远优先（即使该轨同时被 solo）
    """
    any_solo = any(t.get("solo", False) for t in state.values())
    gains = {}
    for name, t in state.items():
        g = float(t.get("gain", 1.0))
        if t.get("mute", False):
            g = 0.0
        elif any_solo and not t.get("solo", False):
            g = 0.0
        gains[name] = g
    return gains


def mix_stems(paths: dict, gains: dict):
    """按增益混合多个 WAV 分轨。

    Args:
        paths: {track: wav文件路径}
        gains: {track: 线性增益}
    Returns:
        (mixed float64 (n, ch), samplerate, clipped: 是否触发了峰值保护)
    """
    mixed = None
    sr = None
    for name, path in paths.items():
        g = float(gains.get(name, 1.0))
        if g == 0.0:
            continue
        data, s = sf.read(path, dtype="float64", always_2d=True)
        if sr is None:
            sr = s
        elif s != sr:
            raise ValueError(f"采样率不一致: {name} 是 {s}, 期望 {sr}")
        if mixed is None:
            mixed = data * g
        elif data.shape[0] == mixed.shape[0]:
            mixed += data * g
        else:  # 长度略有差异时按最长对齐
            n = max(mixed.shape[0], data.shape[0])
            if mixed.shape[0] < n:
                mixed = np.pad(mixed, ((0, n - mixed.shape[0]), (0, 0)))
            mixed[: data.shape[0]] += data * g

    if mixed is None:  # 全部静音：输出等长静音
        first = next(iter(paths.values()))
        info = sf.info(first)
        return np.zeros((info.frames, info.channels)), info.samplerate, False

    clipped = False
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > PEAK_CEILING:
        mixed *= PEAK_CEILING / peak
        clipped = True
    return mixed, sr, clipped


def write_wav24(path: str, data: np.ndarray, sr: int) -> None:
    data = np.clip(data, -1.0, 1.0 - 2 ** -23)
    sf.write(path, data, sr, subtype="PCM_24")
