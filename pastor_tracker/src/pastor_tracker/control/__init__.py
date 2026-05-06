"""Phase 5 control stages: pan controller + command dispatcher (D-01 consume() shape).

Public surface:
    * ``PanController`` -- FOV conversion + stage-2 damper + clamp + deadband (CTRL-01..03)
    * ``CommandDispatcher`` -- synchronous Delta-and-interval emission gate (CTRL-04)
    * ``ControlError`` -- typed root for control-stage errors
"""
from pastor_tracker.control.command_dispatcher import CommandDispatcher
from pastor_tracker.control.pan_controller import ControlError, PanController

__all__ = ["CommandDispatcher", "ControlError", "PanController"]
