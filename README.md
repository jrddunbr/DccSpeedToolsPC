# DCC Speed Tools PC

Reads CBOR transit messages from the DCC Speed Tools serial stream and exposes
recent measurements over a Flask web UI and JSON API.

## Requirements

- Python 3.9+
- Serial device emitting the protocol described in
  `~/CLionProjects/DccSpeedTools/protocol.md`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python app.py
```

Optional flags:

```bash
python app.py --port /dev/ttyUSB0 --baud 115200 --host 0.0.0.0 --http-port 5000
```

## API

- `GET /api/measurements` returns the 10 most recent transit measurements.
- `POST /api/config` accepts JSON `{distance_mm, look_mm, timeout_s}` and sends
  a `cfg` command over serial.
- `GET /api/config` returns the most recent `cfg` packet; add `?refresh=1` to
  send a `getc` request before reading.

## Settings UI

Open `http://127.0.0.1:5000/settings` to apply sensor spacing (mm) and look
distance (mm) overrides.
