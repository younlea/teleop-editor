# backend/app/robot/master_arm.py
import logging
import os, time
from typing import Optional, Dict, Any, Callable, List
import rby1_sdk as rby
import numpy as np
import threading
from .common import Settings
from ruckig import Ruckig, InputParameter, OutputParameter, Result


logger = logging.getLogger(__name__)


class MasterArmManager:
    """
    RBY Master Arm wrapper.
    The actual control callback is provided by teleop.
    """

    DOF = 14

    def __init__(self) -> None:
        self.device = rby.upc.MasterArmDeviceName
        self.master: Optional[rby.upc.MasterArm] = None
        self.connected: bool = False
        self.running: bool = False
        self.keep_last_command: bool = False
        self.last_command: Optional[rby.upc.MasterArm.ControlInput] = None
        self._stop_evt = threading.Event()
        self._done_evt = threading.Event()
        self._lock = threading.Lock()

        self.dt = Settings.master_arm_loop_period  # 제어 주기

        # Ruckig 파라미터
        self.q_max_vel = np.deg2rad(np.full(self.DOF, 90.0))
        self.q_max_acc = np.deg2rad(np.full(self.DOF, 300.0))
        self.q_max_jerk = np.deg2rad(np.full(self.DOF, 2000.0))

        # 포지션 운용 모드 & 토크 리밋(teleop 예시와 동일)
        self.ma_torque_limit = np.array(
            [3.5, 3.5, 3.5, 1.5, 1.5, 1.5, 1.5] * 2, dtype=float
        )

        # 모션 제어
        self._reached = True
        self._last_qd = [0.0] * self.DOF
        self._target_q = [0.0] * self.DOF
        self._move_start = 0.0

    def connect(self) -> bool:
        logger.info(f"Connecting to Master Arm on {self.device}...")

        try:
            rby.upc.initialize_device(self.device)
        except Exception as e:
            logger.error(f"Error initializing device {self.device}: {e}")
            return False

        try:
            model_path = f"{os.path.dirname(os.path.realpath(__file__))}/master_arm.urdf"
            self.master = rby.upc.MasterArm(self.device)
            self.master.set_model_path(model_path)
            self.master.set_control_period(self.dt)
            active = self.master.initialize(verbose=True)
            ok = len(active) == rby.upc.MasterArm.DeviceCount
            self.connected = bool(ok)
        except Exception as e:
            logger.error(f"Error connecting to Master Arm: {e}")
            return False
        
        return self.connected

    def disconnect(self):
        if not self.connected:
            logger.debug("Master Arm is not connected. No need to disconnect.")
            return
        
        logger.info("Disconnecting Master Arm...")

        try:
            self.stop_control()
        except Exception as e:
            logger.error(f"Error stopping Master Arm control: {e}")
            self.running = False

        self.master = None
        self.connected = False

    def start_control(
        self,
        cb: Callable[[rby.upc.MasterArm.State], rby.upc.MasterArm.ControlInput],
        keep_last_command: bool = False,
    ):
        if not self.connected:
            raise RuntimeError(
                "Failed to start Master Arm control: Master is not connected"
            )

        if self.running:
            msg = "Failed to start Master Arm control: Control is already running."
            logger.error(msg)
            raise RuntimeError(msg)

        logger.info(f"Starting Master Arm control({keep_last_command=})...")

        # --- 제어 준비 ---
        self._stop_evt.clear()
        self._done_evt.clear()
        self.keep_last_command = keep_last_command

        def ctrl_cb(state: rby.upc.MasterArm.State) -> rby.upc.MasterArm.ControlInput:
            i = rby.upc.MasterArm.ControlInput()
            if self._stop_evt.is_set():
                self._done_evt.set()
                if self.keep_last_command:
                    if self.last_command is None:
                        i.target_operating_mode.fill(
                            rby.DynamixelBus.CurrentBasedPositionControlMode
                        )
                        i.target_position = state.q_joint
                        i.target_torque = self.ma_torque_limit
                    else:
                        i = self.last_command
                else:
                    i.target_operating_mode.fill(rby.DynamixelBus.CurrentControlMode)
                    i.target_position = state.q_joint
                    i.target_torque.fill(0.0)
            else:
                i = cb(state)

            self.last_command = i

            return i

        self.master.start_control(ctrl_cb)
        self.running = True

    def stop_control(self):
        logger.info("Stopping Master Arm control...")

        if not self.running:
            logger.debug(
                "Master Arm control is not running. No need to stop."
            )
            return

        try:
            self._stop_evt.set()
            if not self._done_evt.wait(timeout=5.0):
                logger.error("Master Arm control did not stop in time.")
            time.sleep(self.dt * 2)
            self.master.stop_control()
        except Exception as e:
            logger.error(f"Error while stopping Master Arm control: {e}")
        finally:
            self.running = False

    def move_to_joints(
        self,
        q_target: List[float],
        minimum_duration: float = 5.0,
        max_vel: Optional[List[float]] = None,
        max_acc: Optional[List[float]] = None,
        max_jerk: Optional[List[float]] = None,
    ) -> bool:

        if not self.connected:
            raise RuntimeError("Master not connected")
        if len(q_target) != self.DOF:
            raise ValueError(f"q_target must have length {self.DOF}")
        if self.running:
            raise RuntimeError(
                "Already running. Stop first or integrate with your teleop multiplexer."
            )

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

        with self._lock:
            self._last_qd = list(q_target)
            self._reached = False
            self._target_q = list(q_target)
            self._move_start = time.time()

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
        inp.minimum_duration = minimum_duration

        initialized = False

        def cb(state: rby.upc.MasterArm.State) -> rby.upc.MasterArm.ControlInput:
            nonlocal initialized

            cin = rby.upc.MasterArm.ControlInput()
            cin.target_operating_mode.fill(
                rby.DynamixelBus.CurrentBasedPositionControlMode
            )
            cin.target_torque = self.ma_torque_limit

            with self._lock:
                if self._reached:
                    cin.target_position = self._last_qd
                    return cin

            if not initialized:
                q0 = np.asarray(state.q_joint, dtype=float)
                dq0 = np.asarray(state.qvel_joint, dtype=float)
                inp.current_position = q0.tolist()
                inp.current_velocity = dq0.tolist()
                inp.current_acceleration = [0.0] * self.DOF

            res = otg.update(inp, out)
            q_d = np.array(out.new_position, dtype=float)
            with self._lock:
                self._last_qd = q_d.tolist()
                cin.target_position = self._last_qd

            out.pass_to_input(inp)
            initialized = True

            if res == Result.Finished:
                with self._lock:
                    self._reached = True

            return cin

        self.start_control(cb, keep_last_command=True)

        return True

    def state(self) -> Dict[str, Any]:
        with self._lock:
            moving = self.running and not self._reached
            elapsed = (time.time() - self._move_start) if self._move_start > 0 else 0.0
            return {
                "device": self.device,
                "connected": self.connected,
                "running": self.running,
                "moving": moving,
                "move": {
                    "active": moving,
                    "reached": self._reached,
                    "target_q": self._target_q,
                    "last_qd": self._last_qd,
                    "elapsed": elapsed,
                },
            }


MASTER = MasterArmManager()
