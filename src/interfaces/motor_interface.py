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


class MotorInterface:
    """
    Interface for communicating with Arduino motor controller.
    
    Responsibilities:
    - Send commands to Arduino via serial
    - Receive and parse feedback messages
    - Maintain latest motor state (thread-safe)
    - Handle connection/reconnection
    """
    
    def __init__(self, sSerialPortName: str, iBaudRate: int = 115200):
        """
        Initialize motor interface.
        
        Args:
            sSerialPortName: Serial port (e.g., '/dev/ttyUSB0' or 'COM3')
            iBaudRate: Serial baud rate (default 115200)
        """
        self.sSerialPortName = sSerialPortName
        self.iBaudRate = iBaudRate
        self.obSerial: Optional[serial.Serial] = None
        
        # Thread-safe state storage
        self._obLatestMotorState = MotorState(
            flMotorAngleDegrees=0.0,
            flMotorTargetAngleDegrees=0.0,
            flMotorSpeedStepsPerSecond=0.0,
            bMotorIsMoving=False,
            dMotorTimestampSeconds=time.perf_counter()
        )
        self._obStateLock = threading.Lock()
        self._flLastCommandedTargetAngleDegrees: Optional[float] = None
        self._dLastCommandTimestampSeconds: Optional[float] = None
        self._obMotorStateHistory = deque(maxlen=32)
        self._iArduinoLastTimestampMicros: Optional[int] = None
        self._iArduinoTimestampRolloverCount: int = 0
        self._dArduinoToPerfCounterOffsetSeconds: Optional[float] = None
        
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
            self._dLastCommandTimestampSeconds = time.perf_counter()
        sCommand = f"M,{flTargetAngleDegrees:.2f}\n"
        return self._send_command(sCommand)
    
    def send_emergency_stop_command(self) -> bool:
        """Stop motor immediately."""
        return self._send_command("E\n")
    
    def send_home_command(self) -> bool:
        """Move motor to 0 degrees."""
        with self._obStateLock:
            self._flLastCommandedTargetAngleDegrees = 0.0
            self._dLastCommandTimestampSeconds = time.perf_counter()
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
                iMotorSequenceNumber=self._obLatestMotorState.iMotorSequenceNumber
            )

    def get_estimated_motor_angle_degrees(self, dAtTimestampSeconds: float) -> float:
        with self._obStateLock:
            vHistory = list(self._obMotorStateHistory)
            if not vHistory:
                return float(self._obLatestMotorState.flMotorAngleDegrees)

        if len(vHistory) == 1:
            return float(vHistory[0].flMotorAngleDegrees)

        dAt = float(dAtTimestampSeconds)
        if dAt <= float(vHistory[0].dMotorTimestampSeconds):
            return float(vHistory[0].flMotorAngleDegrees)
        if dAt >= float(vHistory[-1].dMotorTimestampSeconds):
            return float(vHistory[-1].flMotorAngleDegrees)

        obPrev = vHistory[0]
        for obNext in vHistory[1:]:
            dNext = float(obNext.dMotorTimestampSeconds)
            if dAt <= dNext:
                dPrev = float(obPrev.dMotorTimestampSeconds)
                if dNext <= dPrev:
                    return float(obNext.flMotorAngleDegrees)
                dFrac = (dAt - dPrev) / (dNext - dPrev)
                flA0 = float(obPrev.flMotorAngleDegrees)
                flA1 = float(obNext.flMotorAngleDegrees)
                return flA0 + (flA1 - flA0) * float(dFrac)
            obPrev = obNext

        return float(vHistory[-1].flMotorAngleDegrees)
    
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
                dNow = time.perf_counter()
                dTimestampSeconds = dNow
                try:
                    iTimestampMicros = int(vParts[4])
                    if self._iArduinoLastTimestampMicros is not None and iTimestampMicros < int(self._iArduinoLastTimestampMicros):
                        self._iArduinoTimestampRolloverCount += 1
                    self._iArduinoLastTimestampMicros = iTimestampMicros
                    iUnwrappedMicros = int(iTimestampMicros) + int(self._iArduinoTimestampRolloverCount) * 4294967296
                    dArduinoSeconds = float(iUnwrappedMicros) / 1_000_000.0
                    dMeasuredOffset = float(dNow) - float(dArduinoSeconds)
                    if self._dArduinoToPerfCounterOffsetSeconds is None:
                        self._dArduinoToPerfCounterOffsetSeconds = dMeasuredOffset
                    else:
                        self._dArduinoToPerfCounterOffsetSeconds = (0.98 * float(self._dArduinoToPerfCounterOffsetSeconds)) + (0.02 * dMeasuredOffset)
                    dTimestampSeconds = float(dArduinoSeconds) + float(self._dArduinoToPerfCounterOffsetSeconds)
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
                    self._obMotorStateHistory.append(MotorState(
                        flMotorAngleDegrees=self._obLatestMotorState.flMotorAngleDegrees,
                        flMotorTargetAngleDegrees=self._obLatestMotorState.flMotorTargetAngleDegrees,
                        flMotorSpeedStepsPerSecond=self._obLatestMotorState.flMotorSpeedStepsPerSecond,
                        bMotorIsMoving=self._obLatestMotorState.bMotorIsMoving,
                        dMotorTimestampSeconds=self._obLatestMotorState.dMotorTimestampSeconds,
                        iMotorSequenceNumber=self._obLatestMotorState.iMotorSequenceNumber
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
    def __init__(self):
        self._obLatestMotorState = MotorState(
            flMotorAngleDegrees=0.0,
            flMotorTargetAngleDegrees=0.0,
            flMotorSpeedStepsPerSecond=0.0,
            bMotorIsMoving=False,
            dMotorTimestampSeconds=time.perf_counter()
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
            dMotorTimestampSeconds=time.perf_counter(),
            iMotorSequenceNumber=self._obLatestMotorState.iMotorSequenceNumber
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

    def __init__(self):
        self._flCurrentAngleDegrees = 0.0
        self._flTargetAngleDegrees = 0.0
        self._flCurrentVelocityDegreesPerSecond = 0.0
        self._flMaxSpeedDegreesPerSecond = 30.0
        self._flAccelerationDegreesPerSecondSquared = 60.0
        self._dLastUpdateTimestampSeconds = time.perf_counter()
        self._obStateLock = threading.Lock()
        self._obMotorStateHistory = deque(maxlen=100)
        self._iSequenceNumber = 0
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
                iMotorSequenceNumber=self._iSequenceNumber
            )

    def get_estimated_motor_angle_degrees(self, dAtTimestampSeconds: float) -> float:
        with self._obStateLock:
            vHistory = list(self._obMotorStateHistory)
            if not vHistory:
                return float(self._flCurrentAngleDegrees)

        if len(vHistory) == 1:
            return float(vHistory[0].flMotorAngleDegrees)

        dAt = float(dAtTimestampSeconds)
        if dAt <= float(vHistory[0].dMotorTimestampSeconds):
            return float(vHistory[0].flMotorAngleDegrees)
        if dAt >= float(vHistory[-1].dMotorTimestampSeconds):
            return float(vHistory[-1].flMotorAngleDegrees)

        obPrev = vHistory[0]
        for obNext in vHistory[1:]:
            dNext = float(obNext.dMotorTimestampSeconds)
            if dAt <= dNext:
                dPrev = float(obPrev.dMotorTimestampSeconds)
                if dNext <= dPrev:
                    return float(obNext.flMotorAngleDegrees)
                dFrac = (dAt - dPrev) / (dNext - dPrev)
                flA0 = float(obPrev.flMotorAngleDegrees)
                flA1 = float(obNext.flMotorAngleDegrees)
                return flA0 + (flA1 - flA0) * float(dFrac)
            obPrev = obNext

        return float(vHistory[-1].flMotorAngleDegrees)

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

            # Update timestamp and sequence
            self._dLastUpdateTimestampSeconds = time.perf_counter()
            self._iSequenceNumber += 1

            # Record state in history
            self._obMotorStateHistory.append(MotorState(
                flMotorAngleDegrees=self._flCurrentAngleDegrees,
                flMotorTargetAngleDegrees=self._flTargetAngleDegrees,
                flMotorSpeedStepsPerSecond=abs(self._flCurrentVelocityDegreesPerSecond) / self._FL_DEGREES_PER_STEP,
                bMotorIsMoving=abs(self._flCurrentVelocityDegreesPerSecond) > 0.01 or abs(self._flTargetAngleDegrees - self._flCurrentAngleDegrees) > 0.001,
                dMotorTimestampSeconds=self._dLastUpdateTimestampSeconds,
                iMotorSequenceNumber=self._iSequenceNumber
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
        """Thread function: advance simulation at ~1000 Hz using perf_counter for delta."""
        dLastTime = time.perf_counter()
        while self._bBackgroundThreadRunning:
            dNow = time.perf_counter()
            dDelta = dNow - dLastTime
            dLastTime = dNow
            if dDelta > 0.0:
                self.advance_simulation(dDelta)
            time.sleep(0.001)
