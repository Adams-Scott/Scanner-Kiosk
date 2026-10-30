"""Start a scan when the ScanSnap's hardware Scan button is pressed.

The fujitsu SANE backend exposes the button as a read-only sensor option
named "scan". The watcher keeps the scanner open while idle and polls that
sensor; on a press it closes the device (scanimage needs it free), runs the
scan, then reopens and goes back to polling.

Run on its own to test:   python3 button.py
Show every sensor change:  python3 button.py --debug
"""
import faulthandler
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
        self._polls, self._poll_time, self._beat = 0, 0.0, time.monotonic()
        self._checked = False
        self._last_tick = time.monotonic()   # for the --debug stall watchdog
        self._scanning = False

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
        # Not read here: the S1500 reports a press as a brief blip, and a read
        # at open time would swallow a press made while the scanner was
        # being opened.
        self._prev = False
        self._polls, self._poll_time, self._beat = 0, 0.0, time.monotonic()
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

    def _dump_sensors(self, pressed: bool, t0: float):
        # Called right after the "scan" read, which fetched fresh hardware
        # status; the other sensors come from that same fetch, so this adds
        # no extra scanner traffic that could swallow a press.
        vals = {}
        for n in self._sensor_names():
            if n == BUTTON_SENSOR:
                vals[n] = int(pressed)
                continue
            try:
                vals[n] = self._dev.dev.get_option(self._dev.opt[n].index)
            except Exception as e:
                vals[n] = f"error: {e}"
        if vals != self._last_sensors:
            log.info("sensors: %s", "  ".join(f"{k}={v}" for k, v in vals.items()))
            self._last_sensors = vals
        # Heartbeat: proves polling is still running even when nothing changes.
        self._polls += 1
        self._poll_time += time.monotonic() - t0
        if time.monotonic() - self._beat >= 5:
            log.info("still polling: %d reads in the last 5 s, %.0f ms each, scan=%s",
                     self._polls, 1000 * self._poll_time / self._polls,
                     vals.get(BUTTON_SENSOR))
            self._polls, self._poll_time, self._beat = 0, 0.0, time.monotonic()

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
                t0 = time.monotonic()
                pressed = self._sensor(BUTTON_SENSOR)   # exactly one read per tick
                if self._debug:
                    self._dump_sensors(pressed, t0)
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
                self._scanning = True
                try:
                    self._on_press()
                except scanner.ScanError as e:
                    log.warning("Scan failed: %s", e)
                except Exception:
                    log.exception("Button-triggered scan failed")
                finally:
                    self._scanning = False
                # Device reopens on the next tick.
        return self._interval

    def _watchdog(self):
        """--debug only: if a poll hangs, print where every thread is stuck."""
        reported = False
        while not self._stop.wait(1):
            stuck = time.monotonic() - self._last_tick
            # Skip while opening (device discovery alone can take ~7 s).
            if stuck > 3 and not self._scanning and self._dev is not None:
                if not reported:
                    log.error("Polling has been stuck for %.0f s; stack traces follow", stuck)
                    faulthandler.dump_traceback(all_threads=True)
                    reported = True
            else:
                reported = False

    def run(self):
        sane.init()
        if self._debug:
            threading.Thread(target=self._watchdog, daemon=True).start()
        try:
            while not self._stop.is_set():
                delay = self._tick()
                self._last_tick = time.monotonic()
                self._stop.wait(delay)
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
