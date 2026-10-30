"""Start a scan when the ScanSnap's hardware Scan button is pressed.

The fujitsu SANE backend exposes the button as a read-only sensor option
named "scan". The watcher keeps the scanner open while idle and polls that
sensor; on a press it closes the device (scanimage needs it free), runs the
scan, then reopens and goes back to polling.

Run on its own to test:   python3 button.py
Show every sensor change:  python3 button.py --debug
"""
import logging
import sys
import threading
import time
from contextlib import contextmanager

import sane

import scanner

log = logging.getLogger("button")

BUTTON_PRESET = "bw_double"   # what a button press scans
POLL_INTERVAL = 0.1           # seconds between sensor reads
RETRY_DELAY = 3               # seconds between reopen attempts when offline
BUTTON_SENSOR = "scan"        # fujitsu driver's name for the Scan button


class ButtonWatcher:
    def __init__(self, on_press, interval: float = POLL_INTERVAL, debug: bool = False):
        self._on_press = on_press
        self._interval = interval
        self._lock = threading.Lock()     # whoever holds this owns the scanner
        self._stop = threading.Event()
        self._dev = None
        self._devname = None
        self._prev = False
        self._offline_msg = None
        self._debug = debug
        self._last_sensors = None
        self._checked = False

    # -- device handling ------------------------------------------------
    def _find(self) -> str:
        for name, vendor, model, _ in sane.get_devices():
            if name.startswith("fujitsu"):
                return name
        raise RuntimeError("ScanSnap not found (powered off or lid closed?)")

    def _open(self):
        self._devname = self._devname or self._find()
        try:
            self._dev = sane.open(self._devname)
        except Exception:
            self._devname = None          # re-enumerate on the next attempt
            raise
        if not self._checked:
            self._check_sensor()
            self._checked = True
        # A button already held at open time must not count as a press.
        self._prev = self._sensor(BUTTON_SENSOR)
        if self._offline_msg:
            log.info("Scanner back online")
        self._offline_msg = None

    def _sensor_names(self) -> list[str]:
        # Hardware sensors: options the scanner detects itself and software
        # can't set (SANE_CAP_SOFT_DETECT without SANE_CAP_SOFT_SELECT).
        return [n for n, o in self._dev.opt.items()
                if o.is_active() and o.cap & 4 and not o.cap & 1]

    def _check_sensor(self):
        names = self._sensor_names()
        log.info("Opened %s; sensors: %s", self._devname, ", ".join(names) or "none")
        if BUTTON_SENSOR not in names:
            log.error("The driver does not report a '%s' sensor, so the button "
                      "can't be detected. Run with --debug and press the button "
                      "to see whether another sensor changes.", BUTTON_SENSOR)

    def _dump_sensors(self):
        vals = {}
        for n in self._sensor_names():
            try:
                vals[n] = self._dev.dev.get_option(self._dev.opt[n].index)
            except Exception as e:
                vals[n] = f"error: {e}"
        if vals != self._last_sensors:
            log.info("sensors: %s", "  ".join(f"{k}={v}" for k, v in vals.items()))
            self._last_sensors = vals

    def _close(self):
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None

    def _sensor(self, name: str, default: bool = False) -> bool:
        # Read via the option index: dev.scan is python-sane's scan() method,
        # so the "scan" sensor can't be read as an attribute.
        opt = self._dev.opt.get(name)
        if opt is None or not opt.is_active():
            return default
        return bool(self._dev.dev.get_option(opt.index))

    @contextmanager
    def released(self):
        """Borrow the scanner from the watcher, e.g. for a touch-screen scan.

        Blocks while a button-triggered scan is running, so two scans can
        never collide. Polling resumes when the block exits.
        """
        with self._lock:
            self._close()
            yield

    # -- polling loop ---------------------------------------------------
    def _tick(self) -> float:
        """One poll. Returns how long to wait before the next one."""
        with self._lock:
            try:
                if self._dev is None:
                    self._open()
                    return self._interval
                if self._debug:
                    self._dump_sensors()
                pressed = self._sensor(BUTTON_SENSOR)
                fire = pressed and not self._prev
                self._prev = pressed
            except Exception as e:
                self._close()
                msg = f"{type(e).__name__}: {e}"
                if msg != self._offline_msg:      # log each new reason once
                    log.warning("Scanner unavailable (%s); retrying every %ds",
                                msg, RETRY_DELAY)
                    self._offline_msg = msg
                return RETRY_DELAY

            if fire:
                log.info("Scan button pressed")
                self._close()
                try:
                    self._on_press()
                except scanner.ScanError as e:
                    log.warning("Scan failed: %s", e)
                except Exception:
                    log.exception("Button-triggered scan failed")
                # Device reopens on the next tick.
        return self._interval

    def run(self):
        sane.init()
        try:
            while not self._stop.is_set():
                self._stop.wait(self._tick())
        finally:
            with self._lock:
                self._close()
            sane.exit()

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="button-watcher", daemon=True)
        t.start()
        return t

    def stop(self):
        self._stop.set()


def scan_bw_double():
    job, pages = scanner.scan(BUTTON_PRESET)
    pdf = scanner.build_pdf(job, pages, BUTTON_PRESET)
    log.info("Scanned %d page(s) -> %s", len(pages), pdf)
    # TODO: hand `pdf` to the touch UI preview (accept / rescan) once it exists.
    return pdf


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    log.info("Waiting for the Scan button (Ctrl+C to quit)")
    watcher = ButtonWatcher(scan_bw_double, debug="--debug" in sys.argv[1:])
    try:
        watcher.run()
    except KeyboardInterrupt:
        pass
