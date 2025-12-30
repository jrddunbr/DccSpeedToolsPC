# Protocol

v1.0.0

This project emits a stream of CBOR maps over Serial. Each message is a single
CBOR map and messages are concatenated in the byte stream (no framing
characters). All keys are text strings.

Purpose

The stream is intended for host-side consumers to:
- Track per-sensor blocked/clear events and transit state changes.
- Capture transit results (scale MPH with error bounds).
- Receive periodic API version beacons.
- Log hardware/driver warnings and errors.

Common Fields

- `t` (string): Packet type identifier.
- `ts` (uint): Timestamp in microseconds since boot (present on event packets
  that need precise timing).

Units

- `ts`, `dt`, `min`: microseconds.
- `el`, `to`: seconds (integer).
- `d`, `r`: millimeters (integer).
- `mph`, `err`: scale miles per hour (float).

Packet Types

- `state`
    - `s` (int): Sensor index (0 or 1).
    - `st` (string): `blk` or `clr`.
    - `ts` (uint): Timestamp for the state change.
- `start`
    - `s` (int): Sensor index where the transit started.
    - `ts` (uint): Timestamp of the first sensor hit.
- `finish`
    - `s` (int): Sensor index of the second hit.
    - `dt` (uint): Transit time since `start`.
    - `ts` (uint): Timestamp of the second hit.
- `reject`
    - `dt` (uint): Measured delta time.
    - `min` (uint): Minimum allowed delta time.
- `wait`
    - `f` (int): Sensor index where the transit started.
    - `el` (uint): Seconds elapsed since the first hit.
    - `to` (uint): Timeout threshold in seconds.
- `timeout`
    - `el` (uint): Seconds spent waiting for the second sensor.
- `transit`
    - `f` (int): Start sensor index.
    - `to` (int): End sensor index.
    - `dt` (uint): Transit time.
    - `mph` (float): Scale MPH.
    - `err` (float): Typical error estimate in scale MPH.
    - `ok` (bool): True if `mph` is within the configured min/max range.
- `cfg`
    - `to` (uint): Current max wait for the second sensor.
    - `d` (uint): Current spacing between sensors.
    - `r` (uint): Current max accepted range for object detection.
- `version`
    - `v` (string): API version string.
    - Emitted every 5 minutes while running, and in response to `getv`.
- `sensor`
    - `s` (int): Sensor index.
    - `drv` (array[int]): Driver version `[major, minor, build, rev]` when
      available.
    - `prod` (array[int]): Device info `[type, rev_major, rev_minor]` when
      available.
    - `uid` (uint): Device UID when available.
- `log`
    - `l` (string): `i`, `w`, or `e`.
    - `m` (string): Short message identifier.
    - `ts` (uint): Timestamp of the log event.
    - `s` (int, optional): Sensor index when relevant.
    - `c` (int, optional): Error/status code when relevant.
    - `p` (int, optional): Mux port index when relevant.

Host -> Device Commands

Commands are CBOR maps with a `t` field. All fields are optional unless noted.

- `getv` (fetch API version)
    - `t` = `getv`
    - Behavior: immediately emits a `version` packet.
- `getc` (fetch current config)
    - `t` = `getc`
    - Behavior: immediately emits a `cfg` packet.
- `cfg` (set config)
    - `t` = `cfg`
    - `to` (uint, seconds): Override max wait for the second sensor.
    - `d` (uint, millimeters): Override spacing between sensors.
    - `r` (uint, millimeters): Override max accepted range for object detection.

Device -> Host Config Response
See `cfg` under Packet Types.
