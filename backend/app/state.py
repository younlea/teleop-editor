# app/state.py
from __future__ import annotations

import json
import logging
import threading
from typing import Optional, List
from copy import deepcopy
import math

import numpy as np
from scipy.spatial.transform import Rotation as R

from app.motion.evaluator import TrajectoryEvaluator, Limits
from app.motion.types import DOF, Project as RTProject
from app.motion.adapter import to_runtime, from_runtime  # ← adapters 로 수정
from app.models import Project as PydProject
from app.robot.robot import ROBOT
from app.timescale import TimeScaler  # ← TimeScaler 사용

logger = logging.getLogger(__name__)

DEFAULT_V_MAX = [10.0] * DOF
DEFAULT_A_MAX = [50.0] * DOF
DEFAULT_J_MAX = [1000.0] * DOF


class RuntimeState:
    """
    앱의 런타임 상태를 관리하는 싱글톤.

    시간 도메인 주의:
      - 외부(WS/플레이헤드)는 'master time (m_ms)'를 사용.
      - 내부 트랙/클립은 'timeline time (t_ms)' 기준.
      - TimeScaler F(m)=M^{-1}(m) 로 master→timeline 변환 후 평가.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._quest_state: Optional[dict] = None
        self.robot_connected: bool = False
        self.quest_udp_running: bool = False
        self.quest_udp_bind: Optional[tuple[str, int]] = None
        self.quest_seq: int = 0

        # Quest 헤드 마운트
        self._quest_head_position: Optional[np.ndarray] = None
        self._quest_head_quat: Optional[np.ndarray] = None

        # Project / Evaluator
        lim = Limits(v_max=DEFAULT_V_MAX, a_max=DEFAULT_A_MAX, j_max=DEFAULT_J_MAX)
        self._evaluator = TrajectoryEvaluator(limits=lim)
        self._rt_project: Optional[RTProject] = None

        # Time scaling
        self._timescaler: Optional[TimeScaler] = None  # master↔timeline 매핑기

        # 로봇 재생용 evaluator 주입 (timeline domain)
        # ROBOT.set_play_evaluator(self._eval_range_timeline, self._eval_at_timeline)
        ROBOT.set_play_evaluator(self.eval_range, self.eval_at)

    # --------------- Quest 상태 ---------------

    @property
    def quest_state_json(self) -> str:
        with self._lock:
            return (
                json.dumps(self._quest_state, separators=(",", ":"))
                if self._quest_state
                else "{}"
            )

    @property
    def quest_head_position(self) -> Optional[np.ndarray]:
        with self._lock:
            return (
                None
                if self._quest_head_position is None
                else self._quest_head_position.copy()
            )

    @property
    def quest_head_quat(self) -> Optional[np.ndarray]:
        with self._lock:
            return (
                None if self._quest_head_quat is None else self._quest_head_quat.copy()
            )

    @property
    def quest_state(self) -> Optional[dict]:
        with self._lock:
            return deepcopy(self._quest_state)

    @quest_state.setter
    def quest_state(self, value: dict):
        with self._lock:
            self._quest_state = value
            head = value.get("head") or {}
            self._quest_head_position = np.asarray(
                head.get("position", [0, 0, 0]), dtype=np.float64
            )
            self._quest_head_quat = np.asarray(
                head.get("rotation", [0, 0, 0, 1]), dtype=np.float64
            )

    # --------------- Project / Evaluator / TimeScaler ---------------

    def _build_timescaler(self, rt: RTProject) -> TimeScaler:
        """
        RTProject.timescale → TimeScaler
        points: List[(t_ms, scale)] 로 변환, 빈 경우 identity(1.0) 사용.
        """
        ts = getattr(rt, "timescale", None)
        if not ts or not getattr(ts, "enabled", True):
            return TimeScaler(keys=[(0.0, 1.0)])

        pts = getattr(ts, "points", None) or []
        if not pts:
            return TimeScaler(keys=[(0.0, 1.0)])

        # 런타임 타입은 TimeScalePoint(t_ms, scale)
        keys = []
        for p in pts:
            # dataclass라 속성 접근 보장
            t = float(getattr(p, "t_ms"))
            s = float(getattr(p, "scale"))
            keys.append((t, s))
        return TimeScaler(keys=keys)

    def set_project(self, project: PydProject):
        """
        motion WS에서 호출됨.
        - PydProject → RTProject 변환
        - TimeScaler 구성
        - Evaluator에 프로젝트 설정
        - (스냅샷 저장은 상위 WS 레이어 로직에 따라 별도 수행)
        """
        rt = to_runtime(project)
        with self._lock:
            self._rt_project = rt
            self._timescaler = self._build_timescaler(rt)
            self._evaluator.set_project(rt)

    def get_project(self) -> Optional[RTProject]:
        with self._lock:
            return deepcopy(self._rt_project) if self._rt_project is not None else None

    # ---------- 내부: timeline 도메인 평가 (로봇 재생용) ----------

    def _eval_at_timeline(self, t_ms: int) -> List[float]:
        """로봇에게 넘기는 콜백: timeline 시간 그대로 평가."""
        # evaluator는 timeline 기준
        return self._evaluator.eval_at(t_ms)

    def _eval_range_timeline(
        self, t0_ms: int, t1_ms: int, step_ms: float
    ) -> List[List[float]]:
        """로봇에게 넘기는 콜백: timeline 구간 그대로 평가."""
        return self._evaluator.eval_range(t0_ms, t1_ms, step_ms)

    # ---------- 외부 API: master 도메인 평가 ----------

    def _master_to_timeline(self, m_ms: float) -> float:
        ts = self._timescaler
        if ts is None:
            return float(m_ms)
        return float(ts.timeline_from_master(float(m_ms)))

    def eval_at(self, m_ms: int) -> List[float]:
        """
        Master 시간에서의 포즈 평가 (UI/WS에서 사용하는 기본 API).
        TimeScale이 켜져 있으면 m_ms→t_ms 변환 후 evaluator 호출.
        """
        with self._lock:
            if not self._rt_project:
                return [0.0] * DOF
            t_ms = int(round(self._master_to_timeline(m_ms)))
            logger.info(f"{m_ms = } -----> {t_ms = }")
            return self._evaluator.eval_at(t_ms)

    def eval_range(self, m0_ms: int, m1_ms: int, step_ms: float) -> List[List[float]]:
        """
        Master 구간 샘플링 → 각 샘플을 timeline으로 사상하여 평가.
        (비선형 스케일에도 정확한 샘플링 보장)
        """
        with self._lock:
            if not self._rt_project:
                return [[0.0] * DOF]

            if m1_ms <= m0_ms:
                m0_ms, m1_ms = m1_ms, m0_ms
            if step_ms <= 0:
                step_ms = self.default_step_ms()

            n = max(1, int(math.floor((m1_ms - m0_ms) / step_ms)) + 1)
            out: List[List[float]] = []
            for i in range(n):
                m = m0_ms + i * step_ms
                t = int(round(self._master_to_timeline(m)))
                out.append(self._evaluator.eval_at(t))
            return out

    # ---------- 길이/스텝 ----------

    def _timeline_duration_ms(self) -> int:
        """
        타임라인 기준 프로젝트 길이(ms).
        (클립/소스만으로 계산; TimeScale 미적용)
        """
        p = self._rt_project
        if p is None:
            return 0
        max_end = 0.0
        for c in p.clips:
            s = p.sources.get(c.sourceId)
            if not s:
                continue
            frames = max(1, c.outFrame - c.inFrame)
            dur = frames * float(s.dt) * 1000.0
            end = max(0.0, float(c.t0)) + dur
            if end > max_end:
                max_end = end
        return int(round(max_end))

    def project_duration_ms(self) -> int:
        """
        Master 기준 프로젝트 재생 시간(ms).
        TimeScale이 켜져 있으면 M(t_end)을 반환.
        """
        with self._lock:
            t_end = self._timeline_duration_ms()
            if t_end <= 0:
                return 0
            if self._timescaler is None:
                return t_end
            return int(round(self._timescaler.master_duration_until(t_end)))

    def default_step_ms(self) -> float:
        """
        기본 샘플 간격(ms). 소스 샘플링 주파수에 기반.
        (master/timeline 차이는 무시하고 보수적으로 최소 dt 사용)
        """
        with self._lock:
            p = self._rt_project
            if p is None or not p.sources:
                return 33.0  # 30Hz fallback
            ms = [float(s.dt) * 1000.0 for s in p.sources.values()]
            # 너무 작지 않게 하한 1ms
            return float(max(1.0, min(ms)))

    def master_from_timeline_ms(self, t_ms: float) -> float:
        with self._lock:
            if self._timescaler is None:
                return float(t_ms)
            return float(self._timescaler.master_from_timeline(float(t_ms)))

    def timeline_from_master_ms(self, m_ms: float) -> float:
        with self._lock:
            if self._timescaler is None:
                return float(m_ms)
            return float(self._timescaler.timeline_from_master(float(m_ms)))

    # ---------- Internal ----------

    @staticmethod
    def _pose_to_se3(position, rotation_quat):
        T = np.eye(4)
        T[:3, :3] = R.from_quat(rotation_quat).as_matrix()
        T[:3, 3] = position
        return T


State = RuntimeState()
