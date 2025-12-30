import argparse
import atexit
import io
import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Deque, List, Optional

import cbor2
import serial
from flask import Flask, jsonify, render_template, request
from serial.tools import list_ports

DEFAULT_BAUD = 115200
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000

app = Flask(__name__)


@dataclass
class Measurement:
    received_at: float
    received_iso: str
    ts_us: Optional[int]
    start_sensor: Optional[int]
    end_sensor: Optional[int]
    dt_us: Optional[int]
    mph: Optional[float]
    err: Optional[float]
    err_low: Optional[float]
    err_high: Optional[float]
    ok: Optional[bool]

    def to_dict(self) -> dict:
        return asdict(self)


class MeasurementStore:
    def __init__(self, maxlen: int = 10) -> None:
        self._measurements: Deque[Measurement] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, measurement: Measurement) -> None:
        with self._lock:
            self._measurements.append(measurement)

    def recent(self) -> List[dict]:
        with self._lock:
            return [m.to_dict() for m in reversed(self._measurements)]


class DeviceInfoStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._version: Optional[str] = None
        self._version_iso: Optional[str] = None

    def update_version(self, version: str) -> None:
        with self._lock:
            self._version = version
            self._version_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": self._version,
                "version_received_iso": self._version_iso,
            }


class ConfigStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._distance_mm: Optional[int] = None
        self._look_mm: Optional[int] = None
        self._timeout_s: Optional[int] = None
        self._received_iso: Optional[str] = None

    def update(
        self,
        distance_mm: Optional[int],
        look_mm: Optional[int],
        timeout_s: Optional[int],
    ) -> None:
        if distance_mm is None and look_mm is None and timeout_s is None:
            return
        with self._lock:
            if distance_mm is not None:
                self._distance_mm = distance_mm
            if look_mm is not None:
                self._look_mm = look_mm
            if timeout_s is not None:
                self._timeout_s = timeout_s
            self._received_iso = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime()
            )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "distance_mm": self._distance_mm,
                "look_mm": self._look_mm,
                "timeout_s": self._timeout_s,
                "config_received_iso": self._received_iso,
            }


class SerialReader(threading.Thread):
    def __init__(self, port: str, baud: int, store: MeasurementStore) -> None:
        super().__init__(daemon=True)
        self._port = port
        self._baud = baud
        self._store = store
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
        self._serial = None

    def stop(self) -> None:
        self._stop_event.set()
        if self._serial is not None:
            try:
                self._serial.close()
            except serial.SerialException:
                pass

    def run(self) -> None:
        try:
            self._serial = serial.Serial(self._port, self._baud, timeout=1)
        except serial.SerialException as exc:
            logging.error("Failed to open serial port %s: %s", self._port, exc)
            return

        logging.info("Reading serial port %s at %s baud", self._port, self._baud)
        self.send_command({"t": "getv"})
        buffer = bytearray()

        while not self._stop_event.is_set():
            try:
                chunk = self._serial.read(self._serial.in_waiting or 1)
            except serial.SerialException as exc:
                logging.warning("Serial read error: %s", exc)
                time.sleep(0.5)
                continue

            if not chunk:
                continue

            buffer.extend(chunk)

            while buffer:
                stream = io.BytesIO(buffer)
                decoder = cbor2.CBORDecoder(stream)
                try:
                    message = decoder.decode()
                except cbor2.CBORDecodeEOF:
                    break
                except cbor2.CBORDecodeError as exc:
                    logging.warning("CBOR decode error: %s", exc)
                    buffer.clear()
                    break

                consumed = stream.tell()
                if consumed <= 0:
                    break
                buffer = buffer[consumed:]
                self._handle_message(message)

        logging.info("Serial reader stopped")

    def send_command(self, payload: dict) -> bool:
        if self._serial is None or not self._serial.is_open:
            return False
        try:
            encoded = cbor2.dumps(payload)
        except (TypeError, ValueError) as exc:
            logging.warning("Failed to encode command: %s", exc)
            return False
        try:
            with self._write_lock:
                self._serial.write(encoded)
                self._serial.flush()
        except serial.SerialException as exc:
            logging.warning("Failed to write command: %s", exc)
            return False
        return True

    def _handle_message(self, message: object) -> None:
        if not isinstance(message, dict):
            return
        packet_type = message.get("t")
        if packet_type == "version":
            version = message.get("v")
            if isinstance(version, str):
                device_info.update_version(version)
            return
        if packet_type == "cfg":
            distance_mm = _to_int(message.get("d"))
            timeout_s = _to_int(message.get("to"))
            look_mm = _to_int(message.get("r"))
            if look_mm is None:
                look_mm = _to_int(message.get("h"))
            config_store.update(distance_mm, look_mm, timeout_s)
            return
        if packet_type != "transit":
            return

        mph = _to_float(message.get("mph"))
        err = _to_float(message.get("err"))
        err_low, err_high = _calc_err_band(mph, err)

        measurement = Measurement(
            received_at=time.time(),
            received_iso=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            ts_us=_to_int(message.get("ts")),
            start_sensor=_to_int(message.get("f")),
            end_sensor=_to_int(message.get("to")),
            dt_us=_to_int(message.get("dt")),
            mph=mph,
            err=err,
            err_low=err_low,
            err_high=err_high,
            ok=message.get("ok"),
        )

        self._store.add(measurement)


measurement_store = MeasurementStore()
device_info = DeviceInfoStore()
config_store = ConfigStore()
serial_reader: Optional[SerialReader] = None


@app.route("/")
def index() -> str:
    info = device_info.snapshot()
    return render_template(
        "index.html",
        serial_port=app.config.get("SERIAL_PORT"),
        baud=app.config.get("SERIAL_BAUD"),
        version=info.get("version"),
    )


@app.route("/settings")
def settings() -> str:
    info = device_info.snapshot()
    return render_template(
        "settings.html",
        serial_port=app.config.get("SERIAL_PORT"),
        baud=app.config.get("SERIAL_BAUD"),
        version=info.get("version"),
    )


@app.route("/api/measurements")
def api_measurements() -> object:
    measurements = measurement_store.recent()
    info = device_info.snapshot()
    return jsonify(
        {
            "count": len(measurements),
            "measurements": measurements,
            "version": info.get("version"),
            "version_received_iso": info.get("version_received_iso"),
        }
    )


@app.route("/api/config", methods=["GET", "POST"])
def api_config() -> object:
    if request.method == "GET":
        refresh = request.args.get("refresh") == "1"
        sent = False
        if refresh and serial_reader is not None:
            sent = serial_reader.send_command({"t": "getc"})
        snapshot = config_store.snapshot()
        snapshot["refresh_sent"] = sent
        return jsonify(snapshot)

    payload = request.get_json(silent=True) or {}
    distance_mm = _parse_positive_int(payload.get("distance_mm"))
    look_mm = _parse_positive_int(payload.get("look_mm"))
    timeout_s = _parse_positive_int(payload.get("timeout_s"))

    if distance_mm is None and look_mm is None and timeout_s is None:
        return jsonify({"ok": False, "error": "No values provided."}), 400

    if serial_reader is None:
        return jsonify({"ok": False, "error": "No serial connection."}), 503

    command = {"t": "cfg"}
    if distance_mm is not None:
        command["d"] = distance_mm
    if look_mm is not None:
        command["r"] = look_mm
    if timeout_s is not None:
        command["to"] = timeout_s

    if not serial_reader.send_command(command):
        return jsonify({"ok": False, "error": "Failed to send command."}), 503

    return jsonify({"ok": True, "sent": command})


def _calc_err_band(mph: Optional[float], err: Optional[float]) -> tuple:
    if mph is None or err is None:
        return None, None
    return mph - err, mph + err


def _to_float(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _to_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _parse_positive_int(value: object) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def detect_serial_port(explicit_port: Optional[str]) -> Optional[str]:
    if explicit_port:
        return explicit_port

    ports = list(list_ports.comports())
    if not ports:
        return None

    if len(ports) == 1:
        return ports[0].device

    def score(port) -> int:
        desc = " ".join(
            part
            for part in [port.device, port.description, port.manufacturer]
            if part
        ).lower()
        score_value = 0
        if "ttyusb" in desc or "ttyacm" in desc:
            score_value += 3
        if "usb" in desc:
            score_value += 2
        if "serial" in desc:
            score_value += 1
        return score_value

    ports.sort(key=score, reverse=True)
    return ports[0].device


def start_reader(port: Optional[str], baud: int) -> Optional[SerialReader]:
    if not port:
        logging.warning("No serial port detected. Running without a reader.")
        return None

    reader = SerialReader(port, baud, measurement_store)
    reader.start()
    return reader


def stop_reader() -> None:
    if serial_reader is not None:
        serial_reader.stop()
        serial_reader.join(timeout=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Expose DCC Speed Tools transit data over HTTP."
    )
    parser.add_argument("--port", help="Serial port path (auto-detect by default)")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--http-port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def main() -> None:
    global serial_reader

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    args = parse_args()
    port = detect_serial_port(args.port)

    app.config["SERIAL_PORT"] = port or "Not detected"
    app.config["SERIAL_BAUD"] = args.baud

    serial_reader = start_reader(port, args.baud)
    atexit.register(stop_reader)

    app.run(host=args.host, port=args.http_port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
