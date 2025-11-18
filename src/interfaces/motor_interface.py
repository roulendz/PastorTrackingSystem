"""
motor_interface.py - Serial communication with Arduino motor controller

This module handles all communication with the Arduino stepper controller.

Follows:
- SRP: Only handles motor communication, nothing else
- Thread-safe state storage
- Async feedback reception
"""

import serial
import threading
import time
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
            dMotorTimestampSeconds=0.0
        )
        self._obStateLock = threading.Lock()
        
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
            
            # Wait for Arduino reset and READY message
            time.sleep(2.0)
            self.obSerial.reset_input_buffer()
            
            # Start feedback reception thread
            self._start_feedback_thread()
            
            self._bIsConnected = True
            logger.info(f"Connected to motor on {self.sSerialPortName}")
            return True
            
        except serial.SerialException as e:
            logger.error(f"Failed to connect to motor: {e}")
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
        sCommand = f"M,{flTargetAngleDegrees:.2f}\n"
        return self._send_command(sCommand)
    
    def send_emergency_stop_command(self) -> bool:
        """Stop motor immediately."""
        return self._send_command("E\n")
    
    def send_home_command(self) -> bool:
        """Move motor to 0 degrees."""
        return self._send_command("H\n")
    
    def send_enable_driver_command(self, bEnableDriver: bool) -> bool:
        """
        Enable or disable motor driver.
        
        Args:
            bEnableDriver: True to enable, False to disable
        """
        iEnableValue = 1 if bEnableDriver else 0
        return self._send_command(f"X,{iEnableValue}\n")
    
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
                ulTimestampMicros = int(vParts[4])
                iSequence = int(vParts[5])
                
                # Convert microseconds to seconds
                dTimestampSeconds = ulTimestampMicros / 1_000_000.0
                
                # Update state (thread-safe)
                with self._obStateLock:
                    self._obLatestMotorState.flMotorAngleDegrees = flCurrentAngle
                    self._obLatestMotorState.flMotorTargetAngleDegrees = flTargetAngle
                    self._obLatestMotorState.flMotorSpeedStepsPerSecond = flSpeed
                    self._obLatestMotorState.bMotorIsMoving = bIsMoving
                    self._obLatestMotorState.dMotorTimestampSeconds = dTimestampSeconds
                    self._obLatestMotorState.iMotorSequenceNumber = iSequence
                
                # Call callback if registered
                if self._fnFeedbackCallback:
                    self._fnFeedbackCallback(self.get_latest_motor_state())
                
                logger.debug(f"Motor state updated: {flCurrentAngle:.2f}° (seq {iSequence})")
                
        except Exception as e:
            logger.error(f"Failed to parse feedback: {sFeedbackLine} - {e}")
    
    def __del__(self):
        """Cleanup on deletion."""
        self.disconnect_from_motor_controller()
