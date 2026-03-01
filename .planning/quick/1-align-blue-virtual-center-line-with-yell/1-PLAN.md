---
phase: quick
plan: 1
type: execute
wave: 1
depends_on: []
files_modified:
  - config/default_config.json
  - src/main.py
autonomous: true
requirements: []
must_haves:
  truths:
    - "When motor is at 0 degrees, the home line (cyan) aligns with camera center line (yellow)"
    - "A config parameter allows tuning the offset between motor home and camera optical center"
    - "Existing overlay behavior unchanged when offset is 0 (default)"
  artifacts:
    - path: "config/default_config.json"
      provides: "flCameraMotorOffsetDegrees config key"
      contains: "flCameraMotorOffsetDegrees"
    - path: "src/main.py"
      provides: "Offset-corrected home line calculation"
  key_links:
    - from: "config/default_config.json"
      to: "src/main.py"
      via: "obConfig.flCameraMotorOffsetDegrees read in draw_visualization_overlay"
      pattern: "flCameraMotorOffsetDegrees"
---

<objective>
Fix the misalignment between the cyan home line (motor virtual center with V-marker) and the yellow camera center line in the overlay. The two lines should coincide when the motor is at its home position (0 degrees).

Purpose: The motor's physical 0-degree position does not exactly match the camera's optical center, causing the virtual center (cyan line + V-marker) to appear offset from the actual camera center (yellow line). A configurable offset corrects this misalignment.

Output: Updated overlay drawing that accounts for camera-motor alignment offset via a new config parameter `flCameraMotorOffsetDegrees`.
</objective>

<execution_context>
@C:/Users/lives/.claude/get-shit-done/workflows/execute-plan.md
@C:/Users/lives/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@src/main.py (draw_visualization_overlay function, lines 252-368)
@config/default_config.json
@config/user_config.json

<interfaces>
From src/main.py line 272-278 (current home line calculation):
```python
flMotorAngle = obSample.flMotorAngleDegrees if obSample is not None else (obStats['motor_angle'] if obStats and 'motor_angle' in obStats else 0.0)
if flAnglePerPixel != 0 and iWidth > 0:
    iHomeLineX = int(iCenterX - (flMotorAngle / flAnglePerPixel))
    iHomeLineX = max(0, min(iWidth - 1, iHomeLineX))
else:
    iHomeLineX = iCenterX
cv2.line(obFrame, (iHomeLineX, 0), (iHomeLineX, iHeight), (255, 255, 0), 1)
```

The formula `iHomeLineX = iCenterX - (flMotorAngle / flAnglePerPixel)` maps "where is 0 degrees in this frame?" When motor angle is 0, iHomeLineX equals iCenterX. The offset between physical motor home and camera optical center means flMotorAngle should be adjusted by a fixed offset before this calculation.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add camera-motor offset config and apply to home line calculation</name>
  <files>config/default_config.json, src/main.py</files>
  <action>
1. In `config/default_config.json`, add a new key `"flCameraMotorOffsetDegrees": 0.0` in the camera/FOV section (after `flInitialAnglePerPixelDegrees`). This represents the angular offset between the motor's 0-degree position and the camera's true optical center. Positive values shift the home line to the right; negative to the left.

2. In `src/main.py` function `draw_visualization_overlay()`, modify the home line calculation at line 272-274. Read the offset from config and subtract it from the motor angle before computing the home line pixel position:

Change:
```python
flMotorAngle = obSample.flMotorAngleDegrees if obSample is not None else (obStats['motor_angle'] if obStats and 'motor_angle' in obStats else 0.0)
if flAnglePerPixel != 0 and iWidth > 0:
    iHomeLineX = int(iCenterX - (flMotorAngle / flAnglePerPixel))
```

To:
```python
flMotorAngle = obSample.flMotorAngleDegrees if obSample is not None else (obStats['motor_angle'] if obStats and 'motor_angle' in obStats else 0.0)
flCameraMotorOffsetDegrees = float(getattr(obConfig, 'flCameraMotorOffsetDegrees', 0.0))
if flAnglePerPixel != 0 and iWidth > 0:
    iHomeLineX = int(iCenterX - ((flMotorAngle - flCameraMotorOffsetDegrees) / flAnglePerPixel))
```

The logic: when motor is at angle `flCameraMotorOffsetDegrees`, the camera is pointing exactly at the physical center. By subtracting the offset from motor angle, we make `iHomeLineX == iCenterX` when the motor's actual position corresponds to the camera's optical center.

This also means the deadzone overlay and V-marker (which both use iHomeLineX) will automatically be corrected since they derive from iHomeLineX downstream.

3. Do NOT add this parameter to user_config.json -- the user will tune it via the live settings panel or manually edit their config. The default of 0.0 preserves current behavior for anyone not experiencing the offset.
  </action>
  <verify>
    <automated>cd D:\System\Documents\PastorTrackingSystem && python -c "import json; c=json.load(open('config/default_config.json')); assert 'flCameraMotorOffsetDegrees' in c; assert c['flCameraMotorOffsetDegrees'] == 0.0; print('Config OK')" && python -c "import ast; ast.parse(open('src/main.py').read()); print('Syntax OK')"</automated>
  </verify>
  <done>
    - `flCameraMotorOffsetDegrees` exists in default_config.json with value 0.0
    - draw_visualization_overlay() applies the offset when computing iHomeLineX
    - With offset=0.0, behavior is identical to before (backward compatible)
    - With a non-zero offset, the cyan home line shifts to compensate for motor-camera misalignment
  </done>
</task>

</tasks>

<verification>
- Config loads without errors: `python -c "import json; json.load(open('config/default_config.json'))"`
- Source parses without syntax errors: `python -c "import ast; ast.parse(open('src/main.py').read())"`
- Default offset of 0.0 means no behavioral change for existing users
- The offset value can be positive or negative to shift the home line in either direction
</verification>

<success_criteria>
- The cyan home line position accounts for `flCameraMotorOffsetDegrees` from config
- When the user sets the offset to match their physical setup, the cyan and yellow lines align at motor home
- Zero offset (default) preserves existing behavior exactly
</success_criteria>

<output>
After completion, create `.planning/quick/1-align-blue-virtual-center-line-with-yell/1-SUMMARY.md`
</output>
