"""
control_algorithm.py - Control algorithms for person centering

This module implements control algorithms (P, PI, PID) for centering
the detected person in the frame.

Follows:
- SRP: Only calculates corrections, doesn't send commands
- Clean separation of different control strategies
"""

from abc import ABC, abstractmethod
import time
import logging

logger = logging.getLogger(__name__)


class ControlAlgorithm(ABC):
    """Abstract base class for control algorithms."""
    
    @abstractmethod
    def calculate_correction_from_error(self, flErrorDegrees: float) -> float:
        """
        Calculate control correction from error.
        
        Args:
            flErrorDegrees: Angular error (positive = person right of center)
            
        Returns:
            Correction angle in degrees
        """
        pass
    
    @abstractmethod
    def reset_controller(self):
        """Reset controller state (for integral/derivative terms)."""
        pass


class ProportionalController(ControlAlgorithm):
    """
    Simple P controller.
    
    Output = Kp * error
    
    Fast and stable for most applications.
    """
    
    def __init__(self, flProportionalGain: float = 1.0):
        """
        Initialize P controller.
        
        Args:
            flProportionalGain: Proportional gain (Kp)
        """
        self.flProportionalGain = flProportionalGain
        logger.info(f"P Controller initialized: Kp={flProportionalGain}")
    
    def calculate_correction_from_error(self, flErrorDegrees: float) -> float:
        """Calculate proportional correction."""
        return self.flProportionalGain * flErrorDegrees
    
    def reset_controller(self):
        """Nothing to reset for P controller."""
        pass
    
    def set_proportional_gain(self, flProportionalGain: float):
        """Update Kp value."""
        self.flProportionalGain = flProportionalGain
        logger.info(f"Kp updated to {flProportionalGain}")


class PIDController(ControlAlgorithm):
    """
    Full PID controller with anti-windup.
    
    Output = Kp * error + Ki * integral + Kd * derivative
    
    More sophisticated but requires tuning.
    """
    
    def __init__(
        self,
        flProportionalGain: float = 1.0,
        flIntegralGain: float = 0.0,
        flDerivativeGain: float = 0.1,
        flMaximumIntegralValue: float = 10.0
    ):
        """
        Initialize PID controller.
        
        Args:
            flProportionalGain: Proportional gain (Kp)
            flIntegralGain: Integral gain (Ki)
            flDerivativeGain: Derivative gain (Kd)
            flMaximumIntegralValue: Anti-windup limit for integral term
        """
        self.flProportionalGain = flProportionalGain
        self.flIntegralGain = flIntegralGain
        self.flDerivativeGain = flDerivativeGain
        self.flMaximumIntegralValue = flMaximumIntegralValue
        
        # State variables
        self.flIntegralAccumulator = 0.0
        self.flPreviousError = 0.0
        self.dPreviousTime = time.time()
        
        logger.info(
            f"PID Controller initialized: "
            f"Kp={flProportionalGain}, Ki={flIntegralGain}, Kd={flDerivativeGain}"
        )
    
    def calculate_correction_from_error(self, flErrorDegrees: float) -> float:
        """
        Calculate PID correction.
        
        Args:
            flErrorDegrees: Current error
            
        Returns:
            Control correction
        """
        dCurrentTime = time.time()
        flDeltaTime = dCurrentTime - self.dPreviousTime
        
        # Avoid division by zero
        if flDeltaTime <= 0.0:
            flDeltaTime = 0.001
        
        # Proportional term
        flProportionalTerm = self.flProportionalGain * flErrorDegrees
        
        # Integral term with anti-windup
        self.flIntegralAccumulator += flErrorDegrees * flDeltaTime
        # Clamp integral to prevent windup
        self.flIntegralAccumulator = max(
            -self.flMaximumIntegralValue,
            min(self.flMaximumIntegralValue, self.flIntegralAccumulator)
        )
        flIntegralTerm = self.flIntegralGain * self.flIntegralAccumulator
        
        # Derivative term
        flErrorDerivative = (flErrorDegrees - self.flPreviousError) / flDeltaTime
        flDerivativeTerm = self.flDerivativeGain * flErrorDerivative
        
        # Total output
        flTotalCorrection = flProportionalTerm + flIntegralTerm + flDerivativeTerm
        
        # Update state
        self.flPreviousError = flErrorDegrees
        self.dPreviousTime = dCurrentTime
        
        logger.debug(
            f"PID: P={flProportionalTerm:.2f}, "
            f"I={flIntegralTerm:.2f}, "
            f"D={flDerivativeTerm:.2f}"
        )
        
        return flTotalCorrection
    
    def reset_controller(self):
        """Reset integral and derivative state."""
        self.flIntegralAccumulator = 0.0
        self.flPreviousError = 0.0
        self.dPreviousTime = time.time()
        logger.debug("PID controller reset")
    
    def set_gains(
        self,
        flProportionalGain: float,
        flIntegralGain: float,
        flDerivativeGain: float
    ):
        """Update PID gains."""
        self.flProportionalGain = flProportionalGain
        self.flIntegralGain = flIntegralGain
        self.flDerivativeGain = flDerivativeGain
        logger.info(
            f"PID gains updated: "
            f"Kp={flProportionalGain}, Ki={flIntegralGain}, Kd={flDerivativeGain}"
        )


class VelocityController(ControlAlgorithm):
    """
    Velocity-based controller.
    
    Instead of commanding position, commands velocity based on error.
    Smoother for continuous tracking.
    """
    
    def __init__(
        self,
        flVelocityGain: float = 5.0,
        flMaximumVelocityDegreesPerSecond: float = 30.0,
        flVelocitySmoothingAlpha: float = 0.3
    ):
        """
        Initialize velocity controller.
        
        Args:
            flVelocityGain: Gain for velocity calculation
            flMaximumVelocityDegreesPerSecond: Maximum output velocity
        """
        self.flVelocityGain = flVelocityGain
        self.flMaximumVelocityDegreesPerSecond = flMaximumVelocityDegreesPerSecond
        self.flVelocitySmoothingAlpha = flVelocitySmoothingAlpha
        self.dPreviousTime = time.time()
        self._flPreviousVelocity = 0.0
        
        logger.info(
            f"Velocity Controller initialized: "
            f"Kv={flVelocityGain}, max_vel={flMaximumVelocityDegreesPerSecond}"
        )
    
    def calculate_correction_from_error(self, flErrorDegrees: float) -> float:
        """
        Calculate velocity-based correction.
        
        Returns position change based on desired velocity.
        """
        dCurrentTime = time.time()
        flDeltaTime = dCurrentTime - self.dPreviousTime
        self.dPreviousTime = dCurrentTime
        
        if flDeltaTime <= 0.0:
            flDeltaTime = 0.001
        
        # Calculate desired velocity
        flDesiredVelocity = self.flVelocityGain * flErrorDegrees
        
        # Clamp to maximum
        flDesiredVelocity = max(
            -self.flMaximumVelocityDegreesPerSecond,
            min(self.flMaximumVelocityDegreesPerSecond, flDesiredVelocity)
        )
        
        # Ease-in/out smoothing on velocity
        flSmoothedVelocity = self._flPreviousVelocity + self.flVelocitySmoothingAlpha * (flDesiredVelocity - self._flPreviousVelocity)
        self._flPreviousVelocity = flSmoothedVelocity
        # Convert velocity to position change
        flPositionChange = flSmoothedVelocity * flDeltaTime
        
        return flPositionChange
    
    def reset_controller(self):
        """Reset time state."""
        self.dPreviousTime = time.time()
    def set_parameters(self, flVelocityGain: float, flMaximumVelocityDegreesPerSecond: float, flVelocitySmoothingAlpha: float):
        self.flVelocityGain = float(flVelocityGain)
        self.flMaximumVelocityDegreesPerSecond = float(flMaximumVelocityDegreesPerSecond)
        self.flVelocitySmoothingAlpha = float(flVelocitySmoothingAlpha)
