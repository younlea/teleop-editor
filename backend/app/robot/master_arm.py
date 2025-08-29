# backend/app/robot/master_arm.py
import os, time
from typing import Optional, Dict, Any, Callable, List
import rby1_sdk as rby
import numpy as np
import threading
from .common import Settings
from ruckig import Ruckig, InputParameter, OutputParameter, Result


class MasterArmManager:
    """
    RBY Master Arm wrapper (connect + start/stop control).
    The actual control callback is provided by teleop.
    """

    DOF = 14

    def __init__(self) -> None:
        self.device = rby.upc.MasterArmDeviceName
        self.master: Optional[rby.upc.MasterArm] = None
        self.connected = False
        self.running = False
        self.zero_torque = False

        self.dt = Settings.master_arm_loop_period  # 제어 주기

        # Ruckig 파라미터
        self.q_max_vel = np.deg2rad(np.full(self.DOF, 90.0))
        self.q_max_acc = np.deg2rad(np.full(self.DOF, 300.0))
        self.q_max_jerk = np.deg2rad(np.full(self.DOF, 2000.0))

        # 포지션 운용 모드 & 토크 리밋(teleop 예시와 동일)
        self.dxl_mode_pos = rby.DynamixelBus.CurrentBasedPositionControlMode
        self.ma_torque_limit = np.array(
            [3.5, 3.5, 3.5, 1.5, 1.5, 1.5, 1.5] * 2, dtype=float
        )

        # 모션 제어
        self._stop_evt = threading.Event()
        self._done_evt = threading.Event()
        self._ok = False
        self._last_qd = [0.0] * self.DOF

        self._target_q = [0.0] * self.DOF
        self._move_start = 0.0
        self._max_duration = 0.0

    def connect(self) -> bool:
        print(self.device)
        rby.upc.initialize_device(self.device)
        model_path = f"{os.path.dirname(os.path.realpath(__file__))}/master_arm.urdf"
        self.master = rby.upc.MasterArm(self.device)
        self.master.set_model_path(model_path)
        self.master.set_control_period(Settings.master_arm_loop_period)
        active = self.master.initialize(verbose=True)
        ok = len(active) == rby.upc.MasterArm.DeviceCount
        self.connected = bool(ok)
        return self.connected

    def disconnect(self):
        self.stop_control()
        self.connected = False

    def start_control(
        self, cb: Callable[[rby.upc.MasterArm.State], rby.upc.MasterArm.ControlInput]
    ):
        if not self.connected:
            raise RuntimeError("Master not connected")
        if self.running:
            return
        self.zero_torque = False
        self.master.start_control(cb)
        self.running = True

    def stop_control(self):
        if not self.running:
            return
        try:
            self.zero_torque = True
            time.sleep(Settings.master_arm_loop_period * 2)

            self.master.stop_control()
        finally:
            self.running = False

    def move_to_joints(
        self,
        q_target: List[float],
        max_duration: float = 8.0,
        block: bool = True,
        max_vel: Optional[List[float]] = None,
        max_acc: Optional[List[float]] = None,
        max_jerk: Optional[List[float]] = None,
    ) -> bool:
        """Ruckig 오픈루프 플래닝 → 매 주기 target_position만 전송."""
        if not self.connected:
            raise RuntimeError("Master not connected")
        if len(q_target) != self.DOF:
            raise ValueError(f"q_target must have length {self.DOF}")
        if self.running:
            raise RuntimeError(
                "Already running. Stop first or integrate with your teleop multiplexer."
            )

        # 이벤트 초기화
        self._stop_evt.clear()
        self._done_evt.clear()
        self._ok = False
        self._last_qd = list(q_target)

        q_target = np.asarray(q_target, dtype=float)
        vmax = (
            np.asarray(max_vel, dtype=float) if max_vel is not None else self.q_max_vel
        )
        amax = (
            np.asarray(max_acc, dtype=float) if max_acc is not None else self.q_max_acc
        )
        jmax = (
            np.asarray(max_jerk, dtype=float)
            if max_jerk is not None
            else self.q_max_jerk
        )

        self._target_q = list(q_target)
        self._move_start = time.time()
        self._max_duration = float(max_duration)

        # Ruckig 준비
        otg = Ruckig(self.DOF, self.dt)
        inp = InputParameter(self.DOF)
        out = OutputParameter(self.DOF)
        inp.target_position = q_target.tolist()
        inp.target_velocity = [0.0] * self.DOF
        inp.target_acceleration = [0.0] * self.DOF
        inp.max_velocity = vmax.tolist()
        inp.max_acceleration = amax.tolist()
        inp.max_jerk = jmax.tolist()

        start_time = time.time()
        initialized = False

        def cb(state: rby.upc.MasterArm.State) -> rby.upc.MasterArm.ControlInput:
            nonlocal initialized

            cin = rby.upc.MasterArm.ControlInput()
            cin.target_operating_mode[0:14].fill(self.dxl_mode_pos)
            cin.target_torque[0:14] = self.ma_torque_limit

            # 외부 종료 또는 타임아웃
            if self._stop_evt.is_set() or (time.time() - start_time > max_duration):
                cin.target_position[0:14] = self._last_qd
                self._done_evt.set()
                return cin

            # 첫 주기만 실측으로 초기화
            if not initialized:
                q0 = np.asarray(state.q_joint, dtype=float)
                dq0 = np.asarray(state.qvel_joint, dtype=float)
                inp.current_position = q0.tolist()
                inp.current_velocity = dq0.tolist()
                inp.current_acceleration = [0.0] * self.DOF

            # 오픈루프 업데이트
            res = otg.update(inp, out)
            q_d = np.array(out.new_position, dtype=float)
            self._last_qd = q_d.tolist()

            # 포지션 명령
            cin.target_position[0:14] = self._last_qd

            # 다음 주기 입력으로 전달(오픈루프 재생의 핵심)
            out.pass_to_input(inp)
            initialized = True

            if res == Result.Finished:
                self._ok = True
                self._done_evt.set()

            return cin

        # 제어 시작
        self.start_control(cb)

        def waiter():
            self._done_evt.wait()
            time.sleep(self.dt * 2)  # 살짝 홀드
            self.stop_control()

        if block:
            waiter()
            return self._ok
        else:
            threading.Thread(target=waiter, daemon=True).start()
            return True

    def stop_move(self, ok: bool = False) -> bool:
        """외부에서 현재 이동을 종료. ok=True면 '성공'으로 표시."""
        if not self.running or self._done_evt.is_set():
            return False
        self._ok = bool(ok)
        self._stop_evt.set()
        return True

    def state(self) -> Dict[str, Any]:
        moving = self.running and not self._done_evt.is_set()
        elapsed = (time.time() - self._move_start) if self._move_start > 0 else 0.0
        return {
            "device": self.device,
            "connected": self.connected,
            "running": self.running,
            "moving": moving,
            "move": {
                "active": moving,
                "ok": (self._ok if self._done_evt.is_set() else None),
                "target_q": self._target_q,
                "last_qd": self._last_qd,
                "elapsed": elapsed,
                "max_duration": self._max_duration,
            },
        }


MASTER = MasterArmManager()
