from typing import Annotated, Optional
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from app.robot.master_arm import MASTER

router = APIRouter(prefix="/master", tags=["master"])

Vec14 = Annotated[list[float], Field(min_length=14, max_length=14)]


class MoveReq(BaseModel):
    q: Vec14
    max_duration: float = Field(8.0, ge=0.5, le=120.0)
    block: bool = True
    # 필요 시 제한값 오버라이드 (없으면 None)
    max_vel: Optional[Vec14] = None
    max_acc: Optional[Vec14] = None
    max_jerk: Optional[Vec14] = None


@router.get("/state")
def state():
    return MASTER.state()  # 200 JSON


@router.post("/connect", status_code=status.HTTP_204_NO_CONTENT)
def connect():
    if not MASTER.connect():
        raise HTTPException(status_code=500, detail="Master connect failed")
    return Response(status_code=204)


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def disconnect():
    MASTER.disconnect()
    return Response(status_code=204)


@router.post("/move_to")
def move_to(req: MoveReq):
    if not MASTER.connected:
        raise HTTPException(status_code=400, detail="MasterArm not connected")
    try:
        ok_or_started = MASTER.move_to_joints(
            req.q,
            max_duration=req.max_duration,
            block=req.block,
            max_vel=req.max_vel,
            max_acc=req.max_acc,
            max_jerk=req.max_jerk,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if req.block:
        return {"ok": bool(ok_or_started), "blocking": True}
    return Response(
        content='{"ok": true, "blocking": false}',
        media_type="application/json",
        status_code=status.HTTP_202_ACCEPTED,
    )


class StopReq(BaseModel):
    ok: bool = False


@router.post("/move_stop")
def move_stop(req: StopReq):
    if not MASTER.stop_move(ok=req.ok):
        raise HTTPException(status_code=400, detail="No active motion to stop")
    return {"ok": True}
