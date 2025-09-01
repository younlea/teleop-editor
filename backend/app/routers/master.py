# app/routers/master.py
from typing import Annotated, Optional
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from app.robot.master_arm import MASTER
import asyncio

router = APIRouter(prefix="/master", tags=["master"])

Vec14 = Annotated[list[float], Field(min_length=14, max_length=14)]

class MoveReq(BaseModel):
    q: Vec14
    minimum_duration: float = Field(5.0, ge=0.5, le=120.0)
    max_vel: Optional[Vec14] = None
    max_acc: Optional[Vec14] = None
    max_jerk: Optional[Vec14] = None


# ---------- reached 자동 stop 워처 ----------
_auto_stop_task: asyncio.Task | None = None

def _cancel_auto_stop():
    global _auto_stop_task
    if _auto_stop_task and not _auto_stop_task.done():
        _auto_stop_task.cancel()
    _auto_stop_task = None

async def _watch_reached_and_stop(
    poll_hz: float = 20.0,
    hold_s: float = 0.15,     # reached true 유지 최소시간 (바운스 방지)
    hard_timeout_s: float = 180.0,  # 안전 타임아웃
):
    """MASTER.state()['reached'] 가 일정 시간 true면 stop_control()을 호출."""
    period = 1.0 / max(1e-3, poll_hz)
    reached_since: float | None = None
    start = asyncio.get_event_loop().time()

    try:
        while True:
            now = asyncio.get_event_loop().time()
            if hard_timeout_s and (now - start) > hard_timeout_s:
                # 타임아웃: 그냥 종료 (stop은 호출하지 않음)
                return

            st = MASTER.state()
            reached = bool(st.get("move", {}).get("reached", False))

            if reached:
                if reached_since is None:
                    reached_since = now
                # reached가 hold_s 동안 유지되면 stop
                if (now - reached_since) >= hold_s:
                    try:
                        MASTER.stop_control()
                    finally:
                        return
            else:
                reached_since = None

            await asyncio.sleep(period)
    except asyncio.CancelledError:
        # 새 move가 오거나 외부에서 stop을 호출해 취소됨
        return


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
    _cancel_auto_stop()
    MASTER.disconnect()
    return Response(status_code=204)


@router.post("/move_to")
async def move_to(req: MoveReq):
    if not MASTER.connected:
        raise HTTPException(status_code=400, detail="MasterArm not connected")
    try:
        MASTER.move_to_joints(
            req.q,
            minimum_duration=req.minimum_duration,
            max_vel=req.max_vel,
            max_acc=req.max_acc,
            max_jerk=req.max_jerk,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 새 이동 시작 → 이전 워처 취소 후 새 워처 시작
    _cancel_auto_stop()
    loop = asyncio.get_event_loop()
    globals()["_auto_stop_task"] = loop.create_task(_watch_reached_and_stop())

    return Response(
        content='{"ok": true}',
        media_type="application/json",
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.post("/move_stop")
def move_stop():
    _cancel_auto_stop()
    if not MASTER.stop_control():
        raise HTTPException(status_code=400, detail="No active motion to stop")
    return {"ok": True}
