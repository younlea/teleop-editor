# app/models.py
from __future__ import annotations
from typing import Dict, List, Optional, Literal, Any
from pydantic import BaseModel, Field, model_validator

from app.motion.types import DOF

_MIN_TS = 0.05
_MAX_TS = 4.0


class TimeScalePoint(BaseModel):
    t_ms: float = Field(ge=0, description="키프레임 시간(ms, timeline 기준)")
    scale: float = Field(gt=0, description="배속(>0)")


class TimeScale(BaseModel):
    enabled: bool = True
    points: List[TimeScalePoint] = Field(
        default_factory=lambda: [TimeScalePoint(t_ms=0.0, scale=1.0)],
        description="오름차순 정렬된 (t_ms, scale) 키 목록",
    )

    @model_validator(mode="after")
    def _normalize(self):
        pts = self.points or [TimeScalePoint(t_ms=0.0, scale=1.0)]
        # 1) 정규화/클램프
        pts = [
            TimeScalePoint(
                t_ms=max(0.0, float(p.t_ms)),
                scale=float(min(_MAX_TS, max(_MIN_TS, p.scale))),
            )
            for p in pts
        ]
        # 2) 시간 오름차순 정렬
        pts.sort(key=lambda p: p.t_ms)
        # 3) 같은 시각 키프레임 병합(마지막 값 우선)
        merged: List[TimeScalePoint] = []
        for p in pts:
            if merged and abs(merged[-1].t_ms - p.t_ms) < 1e-9:
                merged[-1] = p
            else:
                merged.append(p)
        self.points = merged or [TimeScalePoint(t_ms=0.0, scale=1.0)]
        return self


# ---------- Core API Schemas ----------
class Source(BaseModel):
    id: str
    dt: float = Field(..., gt=0, description="Seconds per frame (uniform sampling)")
    frames: List[List[float]] = Field(
        ..., description=f"List of frames; each pose must have length {DOF}"
    )
    name: Optional[str] = None

    @model_validator(mode="after")
    def _check_frames(self):
        if not self.frames:
            raise ValueError("Source.frames must not be empty")
        for i, q in enumerate(self.frames):
            if len(q) != DOF:
                raise ValueError(
                    f"Source.frames[{i}] must have length {DOF}, got {len(q)}"
                )
        return self


BlendMode = Literal["override", "crossfade", "additive"]
BlendCurve = Literal["linear", "smoothstep", "easeInOut"]


class Blend(BaseModel):
    mode: BlendMode = Field("override", description="override | crossfade | additive")
    inMs: int = Field(0, ge=0, description="Fade-in duration (ms)")
    outMs: int = Field(0, ge=0, description="Fade-out duration (ms)")
    curve: BlendCurve = Field("linear", description="Weight curve")
    weight: float = Field(1.0, ge=0.0, description="Blend weight")
    priority: int = Field(0, description="Priority (higher wins for override)")


class Clip(BaseModel):
    id: str
    sourceId: str
    t0: int = Field(..., ge=0, description="Start time (ms)")
    inFrame: int = Field(..., ge=0, description="Inclusive frame index")
    outFrame: int = Field(..., ge=1, description="Exclusive frame index")
    name: Optional[str] = None
    blend: Optional[Blend] = Field(default_factory=Blend)

    @model_validator(mode="after")
    def _check_range(self):
        if self.outFrame <= self.inFrame:
            raise ValueError("Clip.outFrame must be > inFrame")
        return self


def _default_timescale() -> TimeScale:
    return TimeScale()


class Project(BaseModel):
    lengthMs: int = Field(0, ge=0, description="Project length (ms)")
    sources: Dict[str, Source] = Field(
        default_factory=dict, description="Sources by ID"
    )
    clips: List[Clip] = Field(default_factory=list, description="Clip list")
    timescale: TimeScale = Field(
        default_factory=_default_timescale,
        description="Piecewise-linear timescale (timeline → master time)",
    )


# ---------- WS / API Payloads ----------
class SetProjectMsg(BaseModel):
    type: Literal["set_project"] = "set_project"
    project: Project


class ApplyOpsMsg(BaseModel):
    type: Literal["apply_ops"] = "apply_ops"
    ops: List[Dict[str, Any]]


class SeekMsg(BaseModel):
    type: Literal["seek"] = "seek"
    t_ms: int


class PrefetchMsg(BaseModel):
    type: Literal["prefetch"] = "prefetch"
    center_ms: int
    window_ms: int = 4000
    step_ms: float = 16.67


# ---------- Robot / Quest ----------
class RobotConnectReq(BaseModel):
    address: str = Field("localhost:50051", description="Robot gRPC address")


class QuestConnectReq(BaseModel):
    local_ip: str
    local_port: int = Field(5005, description="UDP listen port")
    quest_ip: str
    quest_port: int = Field(6000, description="Quest UDP port")


class SimpleOkResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
