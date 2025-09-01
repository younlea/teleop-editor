# backend/app/routers/play.py
from fastapi import APIRouter, HTTPException, status, Response
from pydantic import BaseModel, Field
from app.robot.robot import ROBOT
import logging
from app.state import State

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/play", tags=["play"])


class PlayStartReq(BaseModel):
    t0_ms: float = 0.0


class SeekReq(BaseModel):
    marker_ms: float


class TimescalePoint(BaseModel):
    t_ms: float = Field(ge=0)
    scale: float = Field(gt=0)


class TimescaleSetReq(BaseModel):
    enabled: bool = True
    points: list[TimescalePoint] = Field(
        default_factory=lambda: [TimescalePoint(t_ms=0.0, scale=1.0)]
    )


class TimescaleGetRes(BaseModel):
    enabled: bool
    points: list[TimescalePoint]


@router.post("/start", status_code=status.HTTP_204_NO_CONTENT)
def play_start(req: PlayStartReq):
    ok, reason = ROBOT.can_play()
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)
    if not ROBOT.start_play(t0_ms=float(req.t0_ms)):
        raise HTTPException(status_code=500, detail="Failed to start play")
    return Response(status_code=204)


@router.post("/stop", status_code=status.HTTP_204_NO_CONTENT)
def play_stop():
    ROBOT.stop_play()
    return Response(status_code=204)


@router.post("/seek", status_code=status.HTTP_204_NO_CONTENT)
def play_seek(req: SeekReq):
    t_timeline = float(req.marker_ms)
    m_master = State.master_from_timeline_ms(t_timeline)
    if not ROBOT.seek(m_master):
        raise HTTPException(status_code=400, detail="Seek failed")
    return Response(status_code=204)


@router.get("/state")
def play_state():
    st = ROBOT.play_state()
    t_ms = State.timeline_from_master_ms(st["marker_ms"])
    st_ui = {**st, "marker_ms": int(round(t_ms)), "domain": "timeline"}
    return st_ui
