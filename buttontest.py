"""Find which way of polling sees the ScanSnap's Scan button.

Each round lasts 15 seconds. Press the Scan button 3 times during each round
(paper loaded), then paste the whole output.

    python3 buttontest.py          # rounds A-D
    python3 buttontest.py E F      # only the named rounds
"""
import logging
import subprocess
import sys
import threading
import time

import sane

ROUND = 15


def scan_opt(dev):
    return dev.opt["scan"].index


def round_open(devname, interval):
    """Keep the scanner open and read the sensor every `interval` s."""
    dev = sane.open(devname)
    idx, hits, reads = scan_opt(dev), 0, 0
    end = time.monotonic() + ROUND
    try:
        while time.monotonic() < end:
            reads += 1
            if dev.dev.get_option(idx):
                hits += 1
                print(f"    scan=1 at {time.strftime('%H:%M:%S')}", flush=True)
            time.sleep(interval)
    finally:
        dev.close()
    return hits, reads


def round_reopen(devname, interval):
    """Open, read, close for every check (what scanimage does)."""
    hits, reads = 0, 0
    end = time.monotonic() + ROUND
    while time.monotonic() < end:
        dev = sane.open(devname)
        try:
            reads += 1
            # Read every sensor in order, like scanimage -A does.
            vals = {n: dev.dev.get_option(o.index) for n, o in dev.opt.items()
                    if o.is_active() and o.cap & 4 and not o.cap & 1}
        finally:
            dev.close()
        if vals.get("scan"):
            hits += 1
            print(f"    scan=1 at {time.strftime('%H:%M:%S')}", flush=True)
        time.sleep(interval)
    return hits, reads


def round_scanimage(devname, interval):
    """Control: the scanimage loop that already caught a press once."""
    hits, reads = 0, 0
    end = time.monotonic() + ROUND
    while time.monotonic() < end:
        out = subprocess.run(["scanimage", "-d", devname, "-A"],
                             capture_output=True, text=True).stdout
        reads += 1
        if "--scan[=(yes|no)] [yes]" in out:
            hits += 1
            print(f"    scan=yes at {time.strftime('%H:%M:%S')}", flush=True)
        time.sleep(interval)
    return hits, reads


def round_watcher(devname, debug):
    """The real ButtonWatcher from button.py, with the scan replaced by a print."""
    import button
    hits = []
    w = button.ButtonWatcher(lambda: (hits.append(1), print(
        f"    FIRED at {time.strftime('%H:%M:%S')}", flush=True)), debug=debug)
    w._devname = devname
    reads = [0]
    real = w._sensor
    def counting(name, default=False):
        reads[0] += 1
        return real(name, default)
    w._sensor = counting
    sane.exit()                 # ButtonWatcher.run() does its own init/exit
    t = threading.Thread(target=w.run, daemon=True)
    t.start()
    time.sleep(ROUND)
    w.stop()
    t.join()
    sane.init()
    return len(hits), reads[0]


def main():
    logging.basicConfig(level=logging.INFO, format="    %(name)s: %(message)s")
    sane.init()
    devname = next(d[0] for d in sane.get_devices() if d[0].startswith("fujitsu"))
    print(f"Using {devname}\n")
    rounds = [
        ("A: stay open, check every 0.1 s", round_open, 0.1),
        ("B: stay open, check every 0.5 s", round_open, 0.5),
        ("C: reopen for every check", round_reopen, 0.2),
        ("D: scanimage -A loop (control)", round_scanimage, 0.2),
        ("E: button.py watcher", round_watcher, False),
        ("F: button.py watcher --debug", round_watcher, True),
    ]
    picked = {a.upper() for a in sys.argv[1:]}
    rounds = [r for r in rounds if r[0][0] in picked] if picked else rounds[:4]
    results = []
    for label, fn, interval in rounds:
        print(f"Round {label}")
        for i in (3, 2, 1):
            print(f"  starting in {i}...", flush=True)
            time.sleep(1)
        print(f"  GO - press the Scan button 3 times ({ROUND} s)", flush=True)
        t0 = time.monotonic()
        hits, reads = fn(devname, interval)
        ms = 1000 * (time.monotonic() - t0) / max(reads, 1)
        print(f"  done: {hits} press(es) seen in {reads} checks (~{ms:.0f} ms per check)\n")
        results.append((label, hits, reads))
    sane.exit()
    print("Summary")
    for label, hits, reads in results:
        print(f"  {label}: {hits} seen")


if __name__ == "__main__":
    main()
