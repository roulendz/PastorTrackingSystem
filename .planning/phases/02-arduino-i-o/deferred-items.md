# Phase 02 Deferred Items

## arduino_transport.py coverage 88% (< 90% target)

- **Discovered during:** Plan 02-03 verification (post-Task 3 coverage gate)
- **Lines uncovered:** 101, 108-109, 121-125, 128 (all in `PySerialTransport`)
- **Reason:** `PySerialTransport` requires a real Arduino on a USB port; the
  existing 02-02 test file explicitly deferred this to Phase 8 / QA-04
  ("PySerialTransport is intentionally NOT exercised here — it requires a
  real device and is deferred to Phase 8 / QA-04 stage smoke" — see
  `tests/test_arduino_transport.py:5-6`).
- **Pre-existing:** Plan 02-02 summary did not claim 90% on this module;
  this gap was carried over, not introduced by Plan 02-03.
- **Resolution:** Phase 8 stage smoke test on real hardware closes this gap.
  Plan 02-03 is otherwise green: `arduino_protocol.py` 100% line + branch,
  `arduino_motor.py` 95% line (above the 90% bar).
