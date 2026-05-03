"""Critically-damped 2nd-order follower. Pure math. No PID. No EMA.

State: ``FollowerState(position, velocity)``.
Update: ``CriticallyDampedFollower.step(state, target, dt) -> FollowerState``.

Closed-form (Daniel Holden "Spring-It-On"). Unconditionally stable for any
``dt`` and provably zero overshoot when critically damped. The Pade
approximation in the PROMPT pseudocode is semi-implicit Euler — stable only
for ``dt < 2/omega``; Holden's exact form has no such constraint.

Sources:
- Daniel Holden — theorangeduck.com/page/spring-roll-call
- Game Programming Gems 4 — "Critically Damped Ease-In/Ease-Out Smoothing"
  (basis of Unity's ``Vector3.SmoothDamp``)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

# Named constants (CLAUDE.md rule 6).
LN2: float = math.log(2.0)
DAMPING_NUMERATOR: float = 4.0 * LN2  # 4 * ln(2) — halflife coupling factor
HALF: float = 0.5


@dataclass(frozen=True, slots=True)
class FollowerState:
    """Position + velocity of the damped follower at one instant."""

    position: float
    velocity: float


@dataclass(frozen=True, slots=True)
class CriticallyDampedFollower:
    """Closed-form critically-damped 2nd-order follower (Holden exact form).

    ``time_constant_sec`` is the tau exposed in
    ``Config.framing_time_constant_sec`` and ``Config.pan_time_constant_sec``.
    Internally converted to halflife via ``halflife = tau * ln(2)`` (error
    halves every halflife seconds).
    """

    time_constant_sec: float

    def __post_init__(self) -> None:
        if self.time_constant_sec <= 0.0:
            raise ValueError(
                f"time_constant_sec must be > 0, got {self.time_constant_sec}"
            )

    def initial_state(
        self, position: float, velocity: float = 0.0
    ) -> FollowerState:
        """Construct the initial state at rest (default) or with a seed velocity."""
        return FollowerState(position=position, velocity=velocity)

    def step(
        self,
        state: FollowerState,
        target: float,
        dt: float,
    ) -> FollowerState:
        """Advance the follower one timestep. Pure: returns a new state."""
        if dt <= 0.0:
            raise ValueError(f"dt must be > 0, got {dt}")
        halflife = self.time_constant_sec * LN2
        damping = DAMPING_NUMERATOR / halflife
        y = damping * HALF
        j0 = state.position - target
        j1 = state.velocity + j0 * y
        decay = math.exp(-y * dt)
        new_position = target + decay * (j0 + j1 * dt)
        new_velocity = decay * (state.velocity - j1 * y * dt)
        return replace(state, position=new_position, velocity=new_velocity)
