"""Windows counterpart of probe.py: list the mouse's HID collections, listen to its input reports, then read the
settings reports.

Nothing is written to the mouse except the app's read request (feature report 0xA0). Needs ``pip install hidapi``.
Usage: py tools/probe_win.py [seconds]
"""
import sys
import time

import hid

VID = 0x1D57


def collections():
    for d in hid.enumerate(VID):
        print(f"  {d['product_id']:04x} mi{d['interface_number']} page {d['usage_page']:#06x} usage {d['usage']:#04x}"
              f"  {d['path'].decode()}")
    return hid.enumerate(VID)


def opened(path):
    h = hid.device()
    h.open_path(path)
    return h


def listen(h, seconds, name="input"):
    end = time.time() + seconds
    while (left := end - time.time()) > 0:
        r = h.read(64, max(1, int(left * 1000)))
        if r:
            print(f"  {time.time() % 1000:8.2f} {name} {bytes(r).hex(' ')}")


def read_report(feat, inp, rid, size, flag):
    print(f"read {rid:#04x}")
    feat.send_feature_report(bytes([0xA0, rid, size, 0, flag, 0, 0, 0]))
    listen(inp, 0.5)
    for n in (8, 64):
        try:
            print(f"  a0 answer ({n}) {bytes(feat.get_feature_report(0xA0, n)).hex(' ')}")
        except (OSError, ValueError) as e:
            print(f"  a0 answer ({n}) error {e}")
    for n in (size, 64):
        try:
            print(f"  report {rid:#04x} ({n}) {bytes(feat.get_feature_report(rid, n)).hex(' ')}")
        except (OSError, ValueError) as e:
            print(f"  report {rid:#04x} ({n}) error {e}")


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8
    print("collections:")
    devs = collections()
    pick = {(d["product_id"], d["usage_page"]): d["path"] for d in devs if d["interface_number"] == 2}
    pid = next(p for p in (0xFA60, 0xFA55) if (p, 0x0B) in pick)
    feat, inp = opened(pick[pid, 0x0B]), opened(pick[pid, 0x0A])
    cons = opened(pick[pid, 0x0C]) if (pid, 0x0C) in pick else None
    print(f"listening {secs:g}s on 0x0A (and 0x0C) - press the DPI button and a media-mapped button...")
    end = time.time() + secs
    while time.time() < end:
        listen(inp, 0.05, "0x0A")
        if cons:
            listen(cons, 0.05, "0x0C")
    for rid, size, flag in ((0x0B, 8, 0), (0x06, 9, 1), (0x05, 15, 1), (0x04, 56, 1)):
        read_report(feat, inp, rid, size, flag)
    for h in (feat, inp, cons):
        if h:
            h.close()


if __name__ == "__main__":
    main()
