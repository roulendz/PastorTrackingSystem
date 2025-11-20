class FieldOfViewEstimator:
    def __init__(self, flAnglePerPixelDegrees: float = 0.05):
        self.flCurrentAnglePerPixelEstimate = float(flAnglePerPixelDegrees)

    def reset_field_of_view_estimator_with_initial_angle_per_pixel(self, flAnglePerPixelDegrees: float):
        self.flCurrentAnglePerPixelEstimate = float(flAnglePerPixelDegrees)

    def get_estimated_angle_per_pixel_ratio(self) -> float:
        return self.flCurrentAnglePerPixelEstimate

    def get_estimated_field_of_view_degrees(self, iImageWidthPixels: int) -> float:
        return self.flCurrentAnglePerPixelEstimate * float(iImageWidthPixels)
