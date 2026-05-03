# Firmware unit tests

Run host-side (no Uno needed):

```
pio test -e native
```

Run on Uno:

```
pio test -e uno
```

Place test files as `test_<name>/test_<name>.cpp`. Use Unity test framework
(bundled with PlatformIO).
