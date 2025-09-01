# app/routers/motion.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, status
from fastapi.responses import StreamingResponse, Response
from typing import Set, Annotated, Optional
import logging
import asyncio
from app.state import State
from app.models import SetProjectMsg, SeekMsg, PrefetchMsg
from app.motion.types import DOF
from pydantic import BaseModel, Field
from app.robot.master_arm import MASTER

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def send_json(self, ws: WebSocket, data):
        await ws.send_json(data)

    async def broadcast_json(self, data):
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


Mgr = ConnectionManager()


@router.websocket("/ws/motion")
async def motion_ws(ws: WebSocket):
    await Mgr.connect(ws)
    try:
        while True:
            raw = await ws.receive_json()
            t = raw.get("type")
            if t == "set_project":
                msg = SetProjectMsg(**raw)
                State.set_project(msg.project)

                # 1) 보낸 사람에게 ACK
                await Mgr.send_json(ws, {"type": "ack", "ok": True})

                # 2) (선택) 다른 모두에게 프로젝트 변경 알림
                await Mgr.broadcast_json({"type": "project_updated"})
                # 필요하면 version/summary도 같이 보냄

            elif t == "seek":
                msg = SeekMsg(**raw)
                t_timeline = float(msg.t_ms)
                logger.debug(f"{t_timeline = }")
                m_master = State.master_from_timeline_ms(t_timeline)
                q = State.eval_at(m_master)
                await Mgr.broadcast_json(
                    {"type": "pose", "t_ms": msg.t_ms, "q": q}
                )  # TODO: broadcast로 보내도 되는걸까

            elif t == "prefetch":
                msg = PrefetchMsg(**raw)
                t0 = int(msg.center_ms - msg.window_ms // 2)
                t1 = int(msg.center_ms + msg.window_ms // 2)
                poses = State.eval_range(t0, t1, msg.step_ms)
                await Mgr.send_json(
                    ws,
                    {
                        "type": "prefetch_result",
                        "t0_ms": t0,
                        "step_ms": msg.step_ms,
                        "count": len(poses),
                        "poses": poses,
                    },
                )
    except WebSocketDisconnect:
        pass
    finally:
        Mgr.disconnect(ws)


class ExportCsvRequest(BaseModel):
    t0_ms: int = 0
    t1_ms: int | None = None
    step_ms: float | None = None
    include_header: bool = True


@router.post("/motion/export_csv")
async def export_csv(req: ExportCsvRequest):
    # 프로젝트 유무 확인
    rt = State.get_project()
    if rt is None:
        raise HTTPException(status_code=400, detail="No project set")

    # 구간/간격 결정
    t0 = max(0, int(req.t0_ms))
    t1 = int(req.t1_ms) if req.t1_ms is not None else State.project_duration_ms()
    if t1 < t0:
        raise HTTPException(status_code=400, detail="Invalid time range")
    step_ms = float(req.step_ms) if req.step_ms is not None else State.default_step_ms()
    step_ms = max(1.0, step_ms)

    # 샘플 (편집/블렌드/브릿지 모두 포함한 최종 trajectory)
    samples = State.eval_range(t0, t1, step_ms)

    def _iter_csv():
        if req.include_header:
            head = ["time"] + [f"q{i}" for i in range(DOF)]
            yield ",".join(head) + "\n"

        t = float(t0)
        for row in samples:
            vals = [f"{t/1000.}"] + [f"{v:.9f}" for v in row]
            yield ",".join(vals) + "\n"
            t += step_ms

    return StreamingResponse(
        _iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="trajectory.csv"'},
    )


_auto_stop_task: asyncio.Task | None = None


def _cancel_auto_stop():
    global _auto_stop_task
    if _auto_stop_task and not _auto_stop_task.done():
        _auto_stop_task.cancel()
    _auto_stop_task = None


async def _watch_reached_and_stop(
    poll_hz: float = 20.0, hold_s: float = 0.15, hard_timeout_s: float = 180.0
):
    period = 1.0 / max(1e-3, poll_hz)
    reached_since: float | None = None
    start = asyncio.get_event_loop().time()
    try:
        while True:
            now = asyncio.get_event_loop().time()
            if hard_timeout_s and (now - start) > hard_timeout_s:
                return
            st = MASTER.state()
            reached = bool(st.get("move", {}).get("reached", False))
            if reached:
                if reached_since is None:
                    reached_since = now
                if (now - reached_since) >= hold_s:
                    try:
                        MASTER.stop_control()
                    finally:
                        return
            else:
                reached_since = None
            await asyncio.sleep(period)
    except asyncio.CancelledError:
        return


Vec14 = Annotated[list[float], Field(min_length=14, max_length=14)]


class MoveToAtTimelineReq(BaseModel):
    t_ms: float  # 타임라인(ms)
    minimum_duration: float = Field(5.0, ge=0.5, le=120.0)
    max_vel: Optional[Vec14] = None
    max_acc: Optional[Vec14] = None
    max_jerk: Optional[Vec14] = None


@router.post("/motion/move_to_at_timeline")
async def move_to_at_timeline(req: MoveToAtTimelineReq):
    if State.get_project() is None:
        raise HTTPException(status_code=400, detail="No project set")
    if not MASTER.connected:
        raise HTTPException(status_code=400, detail="MasterArm not connected")

    m_master = State.master_from_timeline_ms(float(req.t_ms))
    print(m_master)
    q = State.eval_at(m_master)
    if not isinstance(q, (list, tuple)) or len(q) != DOF:
        raise HTTPException(
            status_code=500, detail=f"Invalid DOF from State.eval_at: got {len(q)}"
        )

    try:
        MASTER.move_to_joints(
            list(q[2 + 6:2 + 6 + 14]),
            minimum_duration=req.minimum_duration,
            max_vel=req.max_vel,
            max_acc=req.max_acc,
            max_jerk=req.max_jerk,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 새 이동 → 기존 워처 취소 후 재시작
    _cancel_auto_stop()
    loop = asyncio.get_event_loop()
    globals()["_auto_stop_task"] = loop.create_task(_watch_reached_and_stop())

    return Response(
        content='{"ok": true}',
        media_type="application/json",
        status_code=status.HTTP_202_ACCEPTED,
    )
