"""
motor_interface.py - Serial communication with Arduino motor controller

This module handles all communication with the Arduino stepper controller.

Follows:
- SRP: Only handles motor communication, nothing else
- Thread-safe state storage
- Async feedback reception
"""

import serial
from serial.tools import list_ports
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Callable
import logging
import numpy as np

from utilities.clock import Clock, RealClock

logger = logging.getLogger(__name__)


@dataclass
class MotorState:
    """Current state of the motor."""
    flMotorAngleDegrees: float
    flMotorTargetAngleDegrees: float
    flMotorSpeedStepsPerSecond: float
    bMotorIsMoving: bool
    dMotorTimestampSeconds: float
    iMotorSequenceNumber: int = 0
    iAccelerationState: int = 0  # 0=stopped, 1=accel, 2=constant, 3=decel


# Gear ratio constant: 200 steps * 180:1 gear * 8 microstep = 288,000 steps/rev
_FL_DEGREES_PER_STEP = 360.0 / 288000.0


def _hermite_interpolate_angle(
    dT0: float, flP0: float, flV0DegreesPerSecond: float,
    dT1: float, flP1: float, flV1DegreesPerSecond: float,
    dAtTimestamp: float
) -> float:
    """
    Hermite cubic interpolation between two motor state points.

    Uses position and velocity at each endpoint to produce a C1-continuous
    curve (smooth position AND velocity). This handles direction reversals
    naturally -- the velocity passes through zero smoothly.

    Args:
        dT0: Timestamp of first point (seconds)
        flP0: Position at first point (degrees)
        flV0DegreesPerSecond: Velocity at first point (degrees/s, signed)
        dT1: Timestamp of second point (seconds)
        flP1: Position at second point (degrees)
        flV1DegreesPerSecond: Velocity at second point (degrees/s, signed)
        dAtTimestamp: Timestamp to interpolate at

    Returns:
        Interpolated angle in degrees
    """
    dInterval = dT1 - dT0
    if dInterval <= 0.0:
        return flP1

    # Normalize t to [0, 1]
    flT = (dAtTimestamp - dT0) / dInterval

    # Scale velocities to the interval
    flM0 = flV0DegreesPerSecond * dInterval
    flM1 = flV1DegreesPerSecond * dInterval

    # Hermite basis functions
    flT2 = flT * flT
    flT3 = flT2 * flT

    flH00 = 2.0 * flT3 - 3.0 * flT2 + 1.0
    flH10 = flT3 - 2.0 * flT2 + flT
    flH01 = -2.0 * flT3 + 3.0 * flT2
    flH11 = flT3 - flT2

    return flH00 * flP0 + flH10 * flM0 + flH01 * flP1 + flH11 * flM1


def _get_signed_velocity_degrees_per_second(obState: MotorState) -> float:
    """
    Get signed velocity in degrees/second from a MotorState.

    Speed is always positive in MotorState. Direction is inferred from
    the difference between target and current angle.
    """
    flSpeedDegreesPerSecond = float(obState.flMotorSpeedStepsPerSecond) * _FL_DEGREES_PER_STEP
    if obState.iAccelerationState == 0:
        return 0.0
    # Direction: toward target
    flDirection = 1.0 if obState.flMotorTargetAngleDegrees > obState.flMotorAngleDegrees else -1.0
    if abs(obState.flMotorTargetAngleDegrees - obState.flMotorAngleDegrees) < 0.001:
        flDirection = 0.0
    return flSpeedDegreesPerSecond * flDirection


def _hermite_interpolate_from_history(
    vHistory: list, dAtTimestampSeconds: float
) -> float:
    """
    Shared Hermite interpolation logic used by both MotorInterface and
    SimulatedMotorInterface.

    Per locked decisions:
    - Interpolation always runs (no rest-detection bypass)
    - Single smooth curve through direction reversals (C1 continuity)

    Args:
        vHistory: List of MotorState entries (must have >= 1 entry)
        dAtTimestampSeconds: Timestamp to interpolate at

    Returns:
        Interpolated angle in degrees
    """
    if len(vHistory) == 1:
        # Single point: extrapolate using velocity
        obSingle = vHistory[0]
        flVel = _get_signed_velocity_degrees_per_second(obSingle)
        dDt = dAtTimestampSeconds - obSingle.dMotorTimestampSeconds
        return float(obSingle.flMotorAngleDegrees) + flVel * dDt

    dAt = float(dAtTimestampSeconds)

    # Before history: extrapolate from first point
    if dAt <= float(vHistory[0].dMotorTimestampSeconds):
        obFirst = vHistory[0]
        flVel = _get_signed_velocity_degrees_per_second(obFirst)
        dDt = dAt - obFirst.dMotorTimestampSeconds
        return float(obFirst.flMotorAngleDegrees) + flVel * dDt

    # After history: extrapolate from last point
    if dAt >= float(vHistory[-1].dMotorTimestampSeconds):
        obLast = vHistory[-1]
        flVel = _get_signed_velocity_degrees_per_second(obLast)
        dDt = dAt - obLast.dMotorTimestampSeconds
        return float(obLast.flMotorAngleDegrees) + flVel * dDt

    # Between history points: Hermite interpolation
    obPrev = vHistory[0]
    for obNext in vHistory[1:]:
        if dAt <= float(obNext.dMotorTimestampSeconds):
            flV0 = _get_signed_velocity_degrees_per_second(obPrev)
            flV1 = _get_signed_velocity_degrees_per_second(obNext)
            return _hermite_interpolate_angle(
                obPrev.dMotorTimestampSeconds, obPrev.flMotorAngleDegrees, flV0,
                obNext.dMotorTimestampSeconds, obNext.flMotorAngleDegrees, flV1,
                dAt
            )
        obPrev = obNext

    return float(vHistory[-1].flMotorAngleDegrees)


class MotorInterface:
    """
    Interface for communicating with Arduino motor controller.
    
    Responsibilities:
    - Send commands to Arduino via serial
    - Receive and parse feedback messages
    - Maintain latest motor state (thread-safe)
    - Handle connection/reconnection
    """
    
    def __init__(self, sSerialPortName: str, iBaudRate: int = 115200, obClock: Optional[Clock] = None):
        """
        Initialize motor interface.

        Args:
            sSerialPortName: Serial port (e.g., '/dev/ttyUSB0' or 'COM3')
            iBaudRate: Serial baud rate (default 115200)
            obClock: Injected clock for timestamps (defaults to RealClock)
        """
        self.sSerialPortName = sSerialPortName
        self.iBaudRate = iBaudRate
        self.obSerial: Optional[serial.Serial] = None
        self._obClock = obClock or RealClock()

        # Thread-safe state storage
        self._obLatestMotorState = MotorState(
            flMotorAngleDegrees=0.0,
            flMotorTargetAngleDegrees=0.0,
            flMotorSpeedStepsPerSecond=0.0,
            bMotorIsMoving=False,
            dMotorTimestampSeconds=self._obClock.get_time_seconds()
        )
        self._obStateLock = threading.Lock()
        self._flLastCommandedTargetAngleDegrees: Optional[float] = None
        self._dLastCommandTimestampSeconds: Optional[float] = None
        self._obMotorStateHistory = deque(maxlen=50)  # 1 second at 50Hz feedback rate
        self._iArduinoLastTimestampMicros: Optional[int] = None
        self._iArduinoTimestampRolloverCount: int = 0
        self._dArduinoToPerfCounterOffsetSeconds: Optional[float] = None
        self._vClockSyncSamples = deque(maxlen=50)  # 1 second at 50Hz Arduino feedback rate
        self._flClockSyncSlope = 1.0
        self._flClockSyncOffset = 0.0
        
        # Feedback reception thread
        self._bFeedbackThreadRunning = False
        self._obFeedbackThread: Optional[threading.Thread] = None
        
        # Connection state
        self._bIsConnected = False
        
        # Callback for feedback events (optional)
        self._fnFeedbackCallback: Optional[Callable[[MotorState], None]] = None
    
    def connect_to_motor_controller(self) -> bool:
        """
        Open serial connection to Arduino.
        
        Returns:
            True if connection successful
        """
        try:
            self.obSerial = serial.Serial(
                port=self.sSerialPortName,
                baudrate=self.iBaudRate,
                timeout=1.0
            )
            time.sleep(2.0)
            self.obSerial.reset_input_buffer()
            self._start_feedback_thread()
            self._bIsConnected = True
            logger.info(f"Connected to motor on {self.sSerialPortName}")
            return True
        except serial.SerialException:
            vPorts = [p.device for p in list_ports.comports()]
            for sPort in vPorts:
                try:
                    self.obSerial = serial.Serial(
                        port=sPort,
                        baudrate=self.iBaudRate,
                        timeout=1.0
                    )
                    time.sleep(2.0)
                    self.obSerial.reset_input_buffer()
                    self._start_feedback_thread()
                    self._bIsConnected = True
                    self.sSerialPortName = sPort
                    logger.info(f"Connected to motor on {sPort}")
                    return True
                except serial.SerialException:
                    continue
            logger.error("Failed to connect to motor: no available serial ports")
            if vPorts:
                logger.error(f"Available ports: {', '.join(vPorts)}")
            return False
    
    def disconnect_from_motor_controller(self):
        """Close serial connection."""
        self._stop_feedback_thread()
        
        if self.obSerial and self.obSerial.is_open:
            self.obSerial.close()
            self._bIsConnected = False
            logger.info("Disconnected from motor")
    
    def is_connected_to_motor_controller(self) -> bool:
        """Check if connected to motor."""
        return self._bIsConnected and self.obSerial and self.obSerial.is_open
    
    def send_move_to_angle_command(self, flTargetAngleDegrees: float) -> bool:
        """
        Command motor to move to absolute angle.
        
        Args:
            flTargetAngleDegrees: Target angle in degrees
            
        Returns:
            True if command sent successfully
        """
        with self._obStateLock:
            self._flLastCommandedTargetAngleDegrees = float(flTargetAngleDegrees)
            self._dLastCommandTimestampSeconds = self._obClock.get_time_seconds()
        sCommand = f"M,{flTargetAngleDegrees:.2f}\n"
        return self._send_command(sCommand)
    
    def send_emergency_stop_command(self) -> bool:
        """Stop motor immediately."""
        return self._send_command("E\n")
    
    def send_home_command(self) -> bool:
        """Move motor to 0 degrees."""
        with self._obStateLock:
            self._flLastCommandedTargetAngleDegrees = 0.0
            self._dLastCommandTimestampSeconds = self._obClock.get_time_seconds()
        return self._send_command("H\n")
    
    def send_enable_driver_command(self, bEnableDriver: bool) -> bool:
        """
        Enable or disable motor driver.
        
        Args:
            bEnableDriver: True to enable, False to disable
        """
        iEnableValue = 1 if bEnableDriver else 0
        return self._send_command(f"X,{iEnableValue}\n")

    def send_reset_position_command(self) -> bool:
        """Set current mechanical position as 0° without moving."""
        return self._send_command("R\n")
    
    def send_speed_and_acceleration_settings(
        self,
        flMaxSpeedStepsPerSecond: float,
        flMaxAccelerationStepsPerSecondSquared: float
    ) -> bool:
        """
        Update motor speed and acceleration parameters.
        
        Args:
            flMaxSpeedStepsPerSecond: Maximum speed
            flMaxAccelerationStepsPerSecondSquared: Maximum acceleration
        """
        # PID values kept at defaults
        sCommand = (f"S,{flMaxSpeedStepsPerSecond:.1f},"
                   f"{flMaxAccelerationStepsPerSecondSquared:.1f},"
                   f"1.0,0.0,0.1\n")
        return self._send_command(sCommand)
    
    def get_latest_motor_state(self) -> MotorState:
        """
        Get current motor state (thread-safe).
        
        Returns:
            Latest motor state snapshot
        """
        with self._obStateLock:
            # Return a copy to avoid race conditions
            return MotorState(
                flMotorAngleDegrees=self._obLatestMotorState.flMotorAngleDegrees,
                flMotorTargetAngleDegrees=self._obLatestMotorState.flMotorTargetAngleDegrees,
                flMotorSpeedStepsPerSecond=self._obLatestMotorState.flMotorSpeedStepsPerSecond,
                bMotorIsMoving=self._obLatestMotorState.bMotorIsMoving,
                dMotorTimestampSeconds=self._obLatestMotorState.dMotorTimestampSeconds,
                iMotorSequenceNumber=self._obLatestMotorState.iMotorSequenceNumber,
                iAccelerationState=self._obLatestMotorState.iAccelerationState
            )

    def get_estimated_motor_angle_degrees(self, dAtTimestampSeconds: float) -> float:
        """
        Estimate motor angle at a given timestamp using Hermite cubic interpolation.

        Uses position and velocity from the motor state history to produce a
        C1-continuous curve. Handles direction reversals smoothly. Per locked
        decisions: interpolation always runs (no rest-detection bypass).

        Args:
            dAtTimestampSeconds: Timestamp to estimate angle at

        Returns:
            Estimated motor angle in degrees
        """
        with self._obStateLock:
            vHistory = list(self._obMotorStateHistory)
            if not vHistory:
                return float(self._obLatestMotorState.flMotorAngleDegrees)

        return _hermite_interpolate_from_history(vHistory, dAtTimestampSeconds)

    def set_feedback_callback(self, fnCallback: Callable[[MotorState], None]):
        """
        Set callback function to be called on each feedback message.

        Args:
            fnCallback: Function taking MotorState as argument
        """
        self._fnFeedbackCallback = fnCallback
    
    # Private methods
    
    def _send_command(self, sCommand: str) -> bool:
        """Send command to Arduino."""
        if not self.is_connected_to_motor_controller():
            logger.error("Cannot send command - not connected")
            return False
        
        try:
            self.obSerial.write(sCommand.encode('utf-8'))
            logger.debug(f"Sent command: {sCommand.strip()}")
            return True
        except Exception as e:
            logger.error(f"Failed to send command: {e}")
            return False
    
    def _start_feedback_thread(self):
        """Start thread for receiving feedback from Arduino."""
        self._bFeedbackThreadRunning = True
        self._obFeedbackThread = threading.Thread(
            target=self._feedback_reception_loop,
            daemon=True
        )
        self._obFeedbackThread.start()
        logger.debug("Feedback thread started")
    
    def _stop_feedback_thread(self):
        """Stop feedback reception thread."""
        self._bFeedbackThreadRunning = False
        if self._obFeedbackThread:
            self._obFeedbackThread.join(timeout=2.0)
            logger.debug("Feedback thread stopped")
    
    def _feedback_reception_loop(self):
        """
        Thread function that continuously reads feedback from Arduino.
        
        Parses feedback messages in format:
        FB:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState
        """
        while self._bFeedbackThreadRunning:
            try:
                if self.obSerial and self.obSerial.in_waiting > 0:
                    sLine = self.obSerial.readline().decode('utf-8').strip()
                    
                    if sLine.startswith('FB:'):
                        self._parse_feedback_message(sLine)
                    elif sLine.startswith('ERROR:'):
                        logger.error(f"Arduino error: {sLine}")
                    
                else:
                    time.sleep(0.001)  # Small sleep to avoid busy-waiting
                    
            except Exception as e:
                logger.error(f"Error in feedback loop: {e}")
                time.sleep(0.1)
    
    def _parse_feedback_message(self, sFeedbackLine: str):
        """
        Parse feedback message from Arduino.
        
        Format: FB:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState
        """
        try:
            # Remove "FB:" prefix and split
            sData = sFeedbackLine[3:]
            vParts = sData.split(',')
            
            if len(vParts) >= 7:
                flCurrentAngle = float(vParts[0])
                flTargetAngle = float(vParts[1])
                flSpeed = float(vParts[2])
                bIsMoving = bool(int(vParts[3]))
                iSequence = int(vParts[5])
                iAccelState = int(vParts[6])
                dNow = self._obClock.get_time_seconds()
                dTimestampSeconds = dNow
                try:
                    iTimestampMicros = int(vParts[4])
                    if self._iArduinoLastTimestampMicros is not None and iTimestampMicros < int(self._iArduinoLastTimestampMicros):
                        self._iArduinoTimestampRolloverCount += 1
                    self._iArduinoLastTimestampMicros = iTimestampMicros
                    iUnwrappedMicros = int(iTimestampMicros) + int(self._iArduinoTimestampRolloverCount) * 4294967296
                    dArduinoSeconds = float(iUnwrappedMicros) / 1_000_000.0
                    dMeasuredOffset = float(dNow) - float(dArduinoSeconds)

                    # Linear regression over sliding window (SYNC-04)
                    self._vClockSyncSamples.append((dArduinoSeconds, dNow))
                    if len(self._vClockSyncSamples) >= 2:
                        vArduinoTimes = np.array([s[0] for s in self._vClockSyncSamples])
                        vPcTimes = np.array([s[1] for s in self._vClockSyncSamples])
                        # Linear fit: pc_time = slope * arduino_time + offset
                        # slope ~1.0 (clocks run at same rate), offset = clock difference
                        vCoeffs = np.polyfit(vArduinoTimes, vPcTimes, 1)
                        self._flClockSyncSlope = float(vCoeffs[0])
                        self._flClockSyncOffset = float(vCoeffs[1])
                        dTimestampSeconds = self._flClockSyncSlope * dArduinoSeconds + self._flClockSyncOffset
                    else:
                        # Not enough samples yet, use direct offset
                        if self._dArduinoToPerfCounterOffsetSeconds is None:
                            self._dArduinoToPerfCounterOffsetSeconds = dMeasuredOffset
                        dTimestampSeconds = dArduinoSeconds + float(self._dArduinoToPerfCounterOffsetSeconds)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse Arduino timestamp: {e}")
                    dTimestampSeconds = dNow

                # Update state (thread-safe)
                with self._obStateLock:
                    self._obLatestMotorState.flMotorAngleDegrees = flCurrentAngle
                    self._obLatestMotorState.flMotorTargetAngleDegrees = flTargetAngle
                    self._obLatestMotorState.flMotorSpeedStepsPerSecond = flSpeed
                    self._obLatestMotorState.bMotorIsMoving = bIsMoving
                    self._obLatestMotorState.dMotorTimestampSeconds = dTimestampSeconds
                    self._obLatestMotorState.iMotorSequenceNumber = iSequence
                    self._obLatestMotorState.iAccelerationState = iAccelState
                    self._obMotorStateHistory.append(MotorState(
                        flMotorAngleDegrees=self._obLatestMotorState.flMotorAngleDegrees,
                        flMotorTargetAngleDegrees=self._obLatestMotorState.flMotorTargetAngleDegrees,
                        flMotorSpeedStepsPerSecond=self._obLatestMotorState.flMotorSpeedStepsPerSecond,
                        bMotorIsMoving=self._obLatestMotorState.bMotorIsMoving,
                        dMotorTimestampSeconds=self._obLatestMotorState.dMotorTimestampSeconds,
                        iMotorSequenceNumber=self._obLatestMotorState.iMotorSequenceNumber,
                        iAccelerationState=iAccelState
                    ))
                
                # Call callback if registered
                if self._fnFeedbackCallback:
                    self._fnFeedbackCallback(self.get_latest_motor_state())
                
                logger.debug(f"Motor state updated: {flCurrentAngle:.2f}° (seq {iSequence})")
                
        except Exception as e:
            logger.error(f"Failed to parse feedback: {sFeedbackLine} - {e}")
    
    def __del__(self):
        """Cleanup on deletion."""
        self.disconnect_from_motor_controller()


class NullMotorInterface:
    def __init__(self, obClock: Optional[Clock] = None):
        self._obClock = obClock or RealClock()
        self._obLatestMotorState = MotorState(
            flMotorAngleDegrees=0.0,
            flMotorTargetAngleDegrees=0.0,
            flMotorSpeedStepsPerSecond=0.0,
            bMotorIsMoving=False,
            dMotorTimestampSeconds=self._obClock.get_time_seconds(),
            iAccelerationState=0
        )
        self._fnFeedbackCallback = None

    def connect_to_motor_controller(self) -> bool:
        return True

    def disconnect_from_motor_controller(self):
        pass

    def is_connected_to_motor_controller(self) -> bool:
        return True

    def send_move_to_angle_command(self, flTargetAngleDegrees: float) -> bool:
        self._obLatestMotorState.flMotorTargetAngleDegrees = flTargetAngleDegrees
        self._obLatestMotorState.flMotorAngleDegrees = flTargetAngleDegrees
        return True

    def send_emergency_stop_command(self) -> bool:
        return True

    def send_home_command(self) -> bool:
        self._obLatestMotorState.flMotorTargetAngleDegrees = 0.0
        self._obLatestMotorState.flMotorAngleDegrees = 0.0
        return True

    def send_enable_driver_command(self, bEnableDriver: bool) -> bool:
        return True

    def send_speed_and_acceleration_settings(self, a, b) -> bool:
        return True

    def send_reset_position_command(self) -> bool:
        self._obLatestMotorState.flMotorAngleDegrees = 0.0
        self._obLatestMotorState.flMotorTargetAngleDegrees = 0.0
        return True

    def get_latest_motor_state(self) -> MotorState:
        return MotorState(
            flMotorAngleDegrees=self._obLatestMotorState.flMotorAngleDegrees,
            flMotorTargetAngleDegrees=self._obLatestMotorState.flMotorTargetAngleDegrees,
            flMotorSpeedStepsPerSecond=self._obLatestMotorState.flMotorSpeedStepsPerSecond,
            bMotorIsMoving=False,
            dMotorTimestampSeconds=self._obClock.get_time_seconds(),
            iMotorSequenceNumber=self._obLatestMotorState.iMotorSequenceNumber,
            iAccelerationState=0
        )

    def get_estimated_motor_angle_degrees(self, dAtTimestampSeconds: float) -> float:
        return float(self._obLatestMotorState.flMotorAngleDegrees)

    def set_feedback_callback(self, fnCallback):
        self._fnFeedbackCallback = fnCallback


class SimulatedMotorInterface:
    """
    Simulated motor with trapezoidal velocity profile physics.

    Replaces NullMotorInterface for testing scenarios where realistic
    motor behavior is needed (motor takes real time to reach target,
    respects max velocity, decelerates to stop at target position).

    For interactive use (main.py --video), call start_background_simulation()
    which runs advance_simulation() at ~1000 Hz in a daemon thread.
    For unit tests, call advance_simulation(dt) directly with explicit time steps.
    """

    # Gear ratio constant: 200 steps * 180:1 gear * 8 microstep = 288,000 steps/rev
    _FL_DEGREES_PER_STEP = 360.0 / 288000.0

    def __init__(self, obClock: Optional[Clock] = None):
        self._obClock = obClock or RealClock()
        self._flCurrentAngleDegrees = 0.0
        self._flTargetAngleDegrees = 0.0
        self._flCurrentVelocityDegreesPerSecond = 0.0
        self._flMaxSpeedDegreesPerSecond = 30.0
        self._flAccelerationDegreesPerSecondSquared = 60.0
        self._dLastUpdateTimestampSeconds = self._obClock.get_time_seconds()
        self._obStateLock = threading.Lock()
        self._obMotorStateHistory = deque(maxlen=100)
        self._iSequenceNumber = 0
        self._iCurrentAccelerationState = 0  # 0=stopped, 1=accel, 2=constant, 3=decel
        self._bBackgroundThreadRunning = False
        self._obBackgroundThread: Optional[threading.Thread] = None
        self._fnFeedbackCallback: Optional[Callable[[MotorState], None]] = None
        self._bIsConnected = False

    def connect_to_motor_controller(self) -> bool:
        self._bIsConnected = True
        logger.info("SimulatedMotorInterface connected (simulated)")
        return True

    def disconnect_from_motor_controller(self):
        self.stop_background_simulation()
        self._bIsConnected = False
        logger.info("SimulatedMotorInterface disconnected")

    def is_connected_to_motor_controller(self) -> bool:
        return self._bIsConnected

    def send_move_to_angle_command(self, flTargetAngleDegrees: float) -> bool:
        with self._obStateLock:
            self._flTargetAngleDegrees = float(flTargetAngleDegrees)
        return True

    def send_emergency_stop_command(self) -> bool:
        with self._obStateLock:
            self._flTargetAngleDegrees = self._flCurrentAngleDegrees
            self._flCurrentVelocityDegreesPerSecond = 0.0
        return True

    def send_home_command(self) -> bool:
        with self._obStateLock:
            self._flTargetAngleDegrees = 0.0
        return True

    def send_enable_driver_command(self, bEnableDriver: bool) -> bool:
        return True

    def send_speed_and_acceleration_settings(
        self,
        flMaxSpeedStepsPerSecond: float,
        flMaxAccelerationStepsPerSecondSquared: float
    ) -> bool:
        with self._obStateLock:
            self._flMaxSpeedDegreesPerSecond = float(flMaxSpeedStepsPerSecond) * self._FL_DEGREES_PER_STEP
            self._flAccelerationDegreesPerSecondSquared = float(flMaxAccelerationStepsPerSecondSquared) * self._FL_DEGREES_PER_STEP
        return True

    def send_reset_position_command(self) -> bool:
        with self._obStateLock:
            self._flCurrentAngleDegrees = 0.0
            self._flTargetAngleDegrees = 0.0
            self._flCurrentVelocityDegreesPerSecond = 0.0
        return True

    def get_latest_motor_state(self) -> MotorState:
        with self._obStateLock:
            flVelocity = self._flCurrentVelocityDegreesPerSecond
            flDistance = abs(self._flTargetAngleDegrees - self._flCurrentAngleDegrees)
            bIsMoving = abs(flVelocity) > 0.01 or flDistance > 0.001
            flSpeedStepsPerSecond = abs(flVelocity) / self._FL_DEGREES_PER_STEP
            return MotorState(
                flMotorAngleDegrees=self._flCurrentAngleDegrees,
                flMotorTargetAngleDegrees=self._flTargetAngleDegrees,
                flMotorSpeedStepsPerSecond=flSpeedStepsPerSecond,
                bMotorIsMoving=bIsMoving,
                dMotorTimestampSeconds=self._dLastUpdateTimestampSeconds,
                iMotorSequenceNumber=self._iSequenceNumber,
                iAccelerationState=self._iCurrentAccelerationState
            )

    def get_estimated_motor_angle_degrees(self, dAtTimestampSeconds: float) -> float:
        """
        Estimate motor angle at a given timestamp using Hermite cubic interpolation.

        Uses the same shared Hermite interpolation as MotorInterface for
        consistency. Per locked decisions: interpolation always runs, single
        smooth curve through direction reversals.

        Args:
            dAtTimestampSeconds: Timestamp to estimate angle at

        Returns:
            Estimated motor angle in degrees
        """
        with self._obStateLock:
            vHistory = list(self._obMotorStateHistory)
            if not vHistory:
                return float(self._flCurrentAngleDegrees)

        return _hermite_interpolate_from_history(vHistory, dAtTimestampSeconds)

    def set_feedback_callback(self, fnCallback: Callable[[MotorState], None]):
        self._fnFeedbackCallback = fnCallback

    def advance_simulation(self, dDeltaTimeSeconds: float):
        """
        Advance the motor simulation by one time step using trapezoidal velocity profile.

        Args:
            dDeltaTimeSeconds: Time step in seconds
        """
        with self._obStateLock:
            flDistance = self._flTargetAngleDegrees - self._flCurrentAngleDegrees
            flAbsDistance = abs(flDistance)
            flDirection = 1.0 if flDistance > 0.0 else -1.0

            # Check if we are close enough to snap to target
            if flAbsDistance < 0.001 and abs(self._flCurrentVelocityDegreesPerSecond) < 0.01:
                self._flCurrentAngleDegrees = self._flTargetAngleDegrees
                self._flCurrentVelocityDegreesPerSecond = 0.0
            else:
                flAbsVelocity = abs(self._flCurrentVelocityDegreesPerSecond)
                flAccel = self._flAccelerationDegreesPerSecondSquared

                # Stopping distance: v^2 / (2*a)
                flStoppingDistance = (flAbsVelocity * flAbsVelocity) / (2.0 * flAccel) if flAccel > 0.0 else 0.0

                if flStoppingDistance >= flAbsDistance:
                    # Decelerate
                    flDecel = flAccel * dDeltaTimeSeconds
                    if flAbsVelocity <= flDecel:
                        self._flCurrentVelocityDegreesPerSecond = 0.0
                    else:
                        # Decelerate in the direction opposite to current velocity
                        flVelDirection = 1.0 if self._flCurrentVelocityDegreesPerSecond > 0.0 else -1.0
                        self._flCurrentVelocityDegreesPerSecond -= flVelDirection * flDecel
                elif flAbsVelocity < self._flMaxSpeedDegreesPerSecond:
                    # Accelerate toward target
                    self._flCurrentVelocityDegreesPerSecond += flDirection * flAccel * dDeltaTimeSeconds
                    # Clamp to max speed
                    if abs(self._flCurrentVelocityDegreesPerSecond) > self._flMaxSpeedDegreesPerSecond:
                        flVelDir = 1.0 if self._flCurrentVelocityDegreesPerSecond > 0.0 else -1.0
                        self._flCurrentVelocityDegreesPerSecond = flVelDir * self._flMaxSpeedDegreesPerSecond
                # else: cruise at max speed (velocity stays the same)

                # Update position
                self._flCurrentAngleDegrees += self._flCurrentVelocityDegreesPerSecond * dDeltaTimeSeconds

            # Determine acceleration state from physics
            flAbsDistance = abs(self._flTargetAngleDegrees - self._flCurrentAngleDegrees)
            flAbsVelocity = abs(self._flCurrentVelocityDegreesPerSecond)
            flAccel = self._flAccelerationDegreesPerSecondSquared
            flStoppingDistance = (flAbsVelocity * flAbsVelocity) / (2.0 * flAccel) if flAccel > 0.0 else 0.0

            if flAbsDistance < 0.001 and flAbsVelocity < 0.01:
                iAccelState = 0  # stopped
            elif flStoppingDistance >= flAbsDistance:
                iAccelState = 3  # decelerating
            elif flAbsVelocity < self._flMaxSpeedDegreesPerSecond:
                iAccelState = 1  # accelerating
            else:
                iAccelState = 2  # constant velocity

            self._iCurrentAccelerationState = iAccelState

            # Update timestamp and sequence
            self._dLastUpdateTimestampSeconds = self._obClock.get_time_seconds()
            self._iSequenceNumber += 1

            # Record state in history
            self._obMotorStateHistory.append(MotorState(
                flMotorAngleDegrees=self._flCurrentAngleDegrees,
                flMotorTargetAngleDegrees=self._flTargetAngleDegrees,
                flMotorSpeedStepsPerSecond=flAbsVelocity / self._FL_DEGREES_PER_STEP,
                bMotorIsMoving=flAbsVelocity > 0.01 or flAbsDistance > 0.001,
                dMotorTimestampSeconds=self._dLastUpdateTimestampSeconds,
                iMotorSequenceNumber=self._iSequenceNumber,
                iAccelerationState=self._iCurrentAccelerationState
            ))

        # Call feedback callback outside the lock
        if self._fnFeedbackCallback:
            self._fnFeedbackCallback(self.get_latest_motor_state())

    def start_background_simulation(self):
        """Start a daemon thread that calls advance_simulation() at ~1000 Hz."""
        if self._bBackgroundThreadRunning:
            return
        self._bBackgroundThreadRunning = True
        self._obBackgroundThread = threading.Thread(
            target=self._background_simulation_loop,
            daemon=True
        )
        self._obBackgroundThread.start()
        logger.info("SimulatedMotorInterface background simulation started")

    def stop_background_simulation(self):
        """Stop the background simulation thread."""
        self._bBackgroundThreadRunning = False
        if self._obBackgroundThread is not None:
            self._obBackgroundThread.join(timeout=2.0)
            self._obBackgroundThread = None
        logger.debug("SimulatedMotorInterface background simulation stopped")

    def _background_simulation_loop(self):
        """Thread function: advance simulation at ~1000 Hz using perf_counter for delta.

        NOTE: This loop intentionally uses raw time.perf_counter() instead of the
        injected clock. The background loop needs real wall-clock time for its
        sleep interval calculation. The injected clock is used for timestamps
        recorded inside advance_simulation() instead.
        """
        dLastTime = time.perf_counter()
        while self._bBackgroundThreadRunning:
            dNow = time.perf_counter()
            dDelta = dNow - dLastTime
            dLastTime = dNow
            if dDelta > 0.0:
                self.advance_simulation(dDelta)
            time.sleep(0.001)
