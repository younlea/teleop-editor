# backend/app/routers/play.py
from fastapi import APIRouter, HTTPException, status, Response
from pydantic import BaseModel, Field
from app.robot.robot import ROBOT

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
    points: list[TimescalePoint] = [TimescalePoint(t_ms=0, scale=1.0)]


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
    if not ROBOT.seek(req.marker_ms):
        raise HTTPException(status_code=400, detail="Seek failed")
    return Response(status_code=204)


@router.get("/state")
def play_state():
    return ROBOT.play_state()  # 200 JSON


@router.post("/timescale", status_code=status.HTTP_204_NO_CONTENT)
def play_timescale_set(req: TimescaleSetReq):
    ROBOT.set_timescale_enabled(req.enabled)
    ROBOT.set_timescale_points([(p.t_ms, p.scale) for p in req.points])
    return Response(status_code=204)


@router.get("/timescale", response_model=TimescaleGetRes)
def play_timescale_get():
    return TimescaleGetRes(
        enabled=ROBOT._warp_enabled,
        points=[TimescalePoint(t_ms=t, scale=s) for (t, s) in ROBOT._warp_points],
    )
