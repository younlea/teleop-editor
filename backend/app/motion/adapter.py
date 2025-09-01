# app/motion/adapters.py
from __future__ import annotations
from typing import Dict
from app.models import (
    Project as PydProject,
    Clip as PydClip,
    Source as PydSource,
    Blend as PydBlend,
    TimeScale as PydTimeScale,
    TimeScalePoint as PydTimeScalePoint,
)
from .types import (
    Project as RTProject,
    Clip as RTClip,
    Source as RTSource,
    Blend as RTBlend,
    TimeScale as RTTimeScale,
    TimeScalePoint as RTTimeScalePoint,
)


def to_runtime(p: PydProject) -> RTProject:
    """Pydantic Project -> Runtime Project (dataclass)"""
    sources_rt: Dict[str, RTSource] = {}
    for sid, s in p.sources.items():
        sources_rt[sid] = RTSource(
            id=s.id,
            dt=float(s.dt),
            frames=[list(q) for q in s.frames],
            name=s.name,
        )

    clips_rt = []
    for c in p.clips:
        b: PydBlend = c.blend or PydBlend()
        clips_rt.append(
            RTClip(
                id=c.id,
                sourceId=c.sourceId,
                t0=int(c.t0),
                inFrame=int(c.inFrame),
                outFrame=int(c.outFrame),
                name=c.name,
                blend=RTBlend(
                    mode=b.mode,
                    inMs=int(b.inMs),
                    outMs=int(b.outMs),
                    curve=b.curve,
                    weight=float(b.weight),
                    priority=int(b.priority),
                ),
            )
        )

    ts_points = []
    for tsp in p.timescale.points:
        ts_points.append(RTTimeScalePoint(t_ms=tsp.t_ms, scale=tsp.scale))

    ts = RTTimeScale(enabled=p.timescale.enabled, points=ts_points)

    return RTProject(
        lengthMs=int(p.lengthMs), sources=sources_rt, clips=clips_rt, timescale=ts
    )


def from_runtime(p: RTProject) -> PydProject:
    """Runtime Project -> Pydantic Project (API로 내보낼 때 필요하면 사용)"""
    sources_pd: Dict[str, PydSource] = {}
    for sid, s in p.sources.items():
        sources_pd[sid] = PydSource(
            id=s.id,
            dt=float(s.dt),
            frames=[list(q) for q in s.frames],
            name=s.name,
        )

    clips_pd = []
    for c in p.clips:
        b = c.blend
        clips_pd.append(
            PydClip(
                id=c.id,
                sourceId=c.sourceId,
                t0=int(c.t0),
                inFrame=int(c.inFrame),
                outFrame=int(c.outFrame),
                name=c.name,
                blend=PydBlend(
                    mode=b.mode,
                    inMs=int(b.inMs),
                    outMs=int(b.outMs),
                    curve=b.curve,
                    weight=float(b.weight),
                    priority=int(b.priority),
                ),
            )
        )

    ts_points_pd = []
    for tsp in p.timescale.points:
        ts_points_pd.append(PydTimeScalePoint(t_ms=tsp.t_ms, scale=tsp.scale))
    ts_pd = PydTimeScale(enabled=p.timescale.enabled, points=ts_points_pd)

    return PydProject(
        lengthMs=int(p.lengthMs), sources=sources_pd, clips=clips_pd, timescale=ts_pd
    )
