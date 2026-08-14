"""mixdown / peaks 单元测试。运行: python -m pytest tests -q （或 python tests/test_mixdown_peaks.py）"""
import sys
import os
import tempfile

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from mixdown import resolve_gains, mix_stems, write_wav24  # noqa: E402
from peaks import compute_peaks, activity_db  # noqa: E402


def make_stem(path, data, sr=44100):
    sf.write(path, data, sr, subtype="PCM_24")


def test_resolve_gains_plain():
    state = {
        "a": {"gain": 1.0, "mute": False, "solo": False},
        "b": {"gain": 0.5, "mute": False, "solo": False},
    }
    g = resolve_gains(state)
    assert g == {"a": 1.0, "b": 0.5}


def test_resolve_gains_mute():
    state = {
        "a": {"gain": 1.0, "mute": True, "solo": False},
        "b": {"gain": 0.8, "mute": False, "solo": False},
    }
    g = resolve_gains(state)
    assert g["a"] == 0.0 and g["b"] == 0.8


def test_resolve_gains_solo_overrides_others():
    state = {
        "a": {"gain": 1.0, "mute": False, "solo": True},
        "b": {"gain": 0.8, "mute": False, "solo": False},
        "c": {"gain": 0.7, "mute": False, "solo": True},
    }
    g = resolve_gains(state)
    assert g["a"] == 1.0 and g["c"] == 0.7 and g["b"] == 0.0


def test_resolve_gains_solo_plus_mute_on_same_track():
    # solo了但同时mute：mute优先（和真实调音台一致）
    state = {
        "a": {"gain": 1.0, "mute": True, "solo": True},
        "b": {"gain": 0.8, "mute": False, "solo": False},
    }
    g = resolve_gains(state)
    assert g["a"] == 0.0 and g["b"] == 0.0  # a被solo选中但mute了; b因存在solo被压掉


def test_mix_stems_sums_with_gain(tmp_dir=None):
    with tempfile.TemporaryDirectory() as d:
        sr = 44100
        n = sr // 10
        a = np.full((n, 2), 0.25, dtype=np.float64)
        b = np.full((n, 2), 0.5, dtype=np.float64)
        make_stem(os.path.join(d, "a.wav"), a, sr)
        make_stem(os.path.join(d, "b.wav"), b, sr)
        mixed, out_sr, clipped = mix_stems(
            {"a": os.path.join(d, "a.wav"), "b": os.path.join(d, "b.wav")},
            {"a": 1.0, "b": 0.5},
        )
        assert out_sr == sr
        assert mixed.shape == (n, 2)
        # 0.25*1.0 + 0.5*0.5 = 0.5
        assert np.allclose(mixed, 0.5, atol=1e-4)
        assert clipped is False


def test_mix_stems_peak_protection():
    with tempfile.TemporaryDirectory() as d:
        sr = 44100
        n = sr // 10
        a = np.full((n, 2), 0.9, dtype=np.float64)
        make_stem(os.path.join(d, "a.wav"), a, sr)
        make_stem(os.path.join(d, "b.wav"), a, sr)
        mixed, _, clipped = mix_stems(
            {"a": os.path.join(d, "a.wav"), "b": os.path.join(d, "b.wav")},
            {"a": 1.0, "b": 1.0},
        )
        assert clipped is True
        assert np.max(np.abs(mixed)) <= 0.9991  # 缩到峰值以内


def test_write_wav24_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        sr = 44100
        data = (np.random.rand(1000, 2) * 1.6 - 0.8).astype(np.float64)
        p = os.path.join(d, "out.wav")
        write_wav24(p, data, sr)
        back, back_sr = sf.read(p)
        assert back_sr == sr
        assert np.allclose(back, data, atol=2e-7)  # 24bit量化误差内


def test_compute_peaks_shape_and_range():
    sr = 44100
    t = np.linspace(0, 1, sr, endpoint=False)
    sig = np.stack([np.sin(2 * np.pi * 440 * t) * 0.5] * 2, axis=1)
    pk = compute_peaks(sig, buckets=200)
    assert len(pk) == 200
    mins = [p[0] for p in pk]
    maxs = [p[1] for p in pk]
    assert all(-0.51 <= m <= 0 for m in mins)
    assert all(0 <= m <= 0.51 for m in maxs)


def test_compute_peaks_short_signal():
    sig = np.zeros((10, 2))
    pk = compute_peaks(sig, buckets=200)
    assert len(pk) == 200  # 不足桶数也要填满（补零）


def test_activity_db():
    sr = 44100
    silent = np.zeros((sr, 2))
    assert activity_db(silent) < -80
    loud = np.full((sr, 2), 0.5)
    assert activity_db(loud) > -10


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
