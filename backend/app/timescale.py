# app/timescale.py
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Tuple, Optional

_EPS = 1e-9

@dataclass
class TsKey:
    t: float   # timeline ms
    s: float   # scale (>0)

class TimeScaler:
    """
    Piecewise-linear scale s(t) over timeline t (ms).
    We need two maps:
      M(t) = ∫_0^t (1/s(u)) du   (timeline -> master)
      F(m) = M^{-1}(m)          (master -> timeline)   <-- used at runtime/export
    """
    def __init__(self, keys: Optional[List[Tuple[float, float]]] = None):
        if not keys:
            keys = [(0.0, 1.0)]
        # sort + sanitize
        ks = sorted((float(t), max(1e-6, float(s))) for t, s in keys)
        # dedup equal t: keep last
        compact: List[TsKey] = []
        for t, s in ks:
            if compact and abs(compact[-1].t - t) < 1e-9:
                compact[-1] = TsKey(t, s)
            else:
                compact.append(TsKey(t, s))
        self.keys = compact
        self._build()

    def _build(self):
        ks = self.keys
        self.seg = []  # segments with precomputed coeffs
        m_cum = 0.0
        for i in range(len(ks) - 1):
            t0, s0 = ks[i].t, ks[i].s
            t1, s1 = ks[i + 1].t, ks[i + 1].s
            if t1 <= t0:
                t1 = t0 + 1e-6
            a = (s1 - s0) / (t1 - t0)    # s(t) = a*t + b
            b = s0 - a * t0

            if abs(a) < _EPS:  # constant segment
                dm = (t1 - t0) / max(_EPS, s0)
            else:
                dm = (math.log(abs(a * t1 + b)) - math.log(abs(a * t0 + b))) / a

            self.seg.append({"t0": t0, "t1": t1, "s0": s0, "s1": s1,
                             "a": a, "b": b, "m0": m_cum})
            m_cum += dm

        # tail (t >= last_key): constant scale = last scale
        self.tail = {"t0": ks[-1].t, "s": ks[-1].s, "m0": m_cum}
        self.master_at_last_key = m_cum

    # timeline -> master
    def master_from_timeline(self, t: float) -> float:
        if t <= 0.0:
            return 0.0
        # within defined segments
        for d in self.seg:
            t0, t1 = d["t0"], d["t1"]
            a, b, m0, s0 = d["a"], d["b"], d["m0"], d["s0"]
            if t >= t1:
                # add full segment; continue
                if abs(a) < _EPS:
                    dm = (t1 - t0) / max(_EPS, s0)
                else:
                    dm = (math.log(abs(a * t1 + b)) - math.log(abs(a * t0 + b))) / a
                # accumulate by moving forward; handled by m0 on next loop
                continue
            # inside this segment: integrate up to t
            if abs(a) < _EPS:
                return m0 + (t - t0) / max(_EPS, s0)
            else:
                return m0 + (math.log(abs(a * t + b)) - math.log(abs(a * t0 + b))) / a
        # tail
        t0, s, m0 = self.tail["t0"], self.tail["s"], self.tail["m0"]
        if t <= t0:
            return m0  # exactly at last key
        return m0 + (t - t0) / max(_EPS, s)

    # master -> timeline (inverse of the above)
    def timeline_from_master(self, m: float) -> float:
        if m <= 0.0:
            return 0.0
        # search segment
        for d in self.seg:
            t0, t1 = d["t0"], d["t1"]
            a, b, m0, s0 = d["a"], d["b"], d["m0"], d["s0"]
            # master span of this segment:
            if abs(a) < _EPS:
                dm_full = (t1 - t0) / max(_EPS, s0)
            else:
                dm_full = (math.log(abs(a * t1 + b)) - math.log(abs(a * t0 + b))) / a
            m1 = m0 + dm_full
            if m <= m1 + 1e-9:
                dmq = m - m0
                if abs(a) < _EPS:
                    return t0 + dmq * s0
                v0 = a * t0 + b
                return (v0 * math.exp(a * dmq) - b) / a
        # tail
        t0, s, m0 = self.tail["t0"], self.tail["s"], self.tail["m0"]
        return t0 + (m - m0) * max(_EPS, s)

    def master_duration_until(self, timeline_end_ms: float) -> float:
        """M(timeline_end)"""
        return self.master_from_timeline(timeline_end_ms)
