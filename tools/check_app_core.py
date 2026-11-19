"""Check the app's core against the forecaster, and replay a flight through it. Needs a Mac with Swift.

  uv run python tools/check_app_core.py out/packs/2027-03-14_BRU-AGP.planeview [path id]

1. Builds ios/PlaneViewCore (Swift) and unpacks the pack with it (the app's own ZIP reader).
2. Works out every sighting along one of the pack's paths twice - with planeview.visibility (Python) and with the
   app's Spotter (Swift), on the pack's features and terrain - and compares them.
3. Replays the path as a flight: with GPS throughout, with a 25-minute GPS gap, on a routing 45 km off any flown,
   and with no GPS at all (25 minutes late, with the position pinned by hand 40 minutes in).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from planeview import geo, visibility as V  # noqa: E402


def main():
    pack = Path(sys.argv[1])
    pid = sys.argv[2] if len(sys.argv) > 2 else "V1"
    core = ROOT / "ios" / "PlaneViewCore"
    subprocess.run(["swift", "build", "-c", "release"], cwd=core, check=True, capture_output=True)
    pv = core / ".build" / "release" / "pvcore"
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "pack"
        subprocess.run([pv, "unpack", pack, d], check=True)
        sw = json.loads(subprocess.run([pv, "schedule", d, pid], check=True, capture_output=True, text=True).stdout)
        man = json.loads((d / "pack.json").read_text())
        F = json.loads((d / "features.json").read_text())["features"]
        feats = []
        for r in F:
            pts = np.array(r["p"]).reshape(-1, 2) if "p" in r else np.array([[r["la"], r["lo"]]])
            feats.append(dict(id=r["i"], name=r["n"], kind=r["k"], sub=r.get("s"), lat=r["la"], lon=r["lo"], z=r.get("z", 0.0),
                              size_km=r["sz"], pop=r.get("pop", 0), elev=r.get("el"), pts=pts, broad=bool(r.get("br"))))
        idx = V.FeatureIndex(feats)
        tr = np.array(next(p for p in man["paths"] if p["id"] == pid)["track"])
        trk = V.track_arrays(tr[:, 0] * 60, tr[:, 1], tr[:, 2], tr[:, 3])
        sun = geo.sun_az_el(man["flight"]["takeoff_unix"] + trk["t"], trk["lat"], trk["lon"])[1]
        res = V.evaluate(idx, trk, sun, terrain=V.Terrain(allow_fetch=False))
        py = {(feats[fi]["id"], sd): r for (fi, sd), r in res.items()}
        swd = {(r["id"], r["side"]): r for r in sw}
        both = set(py) & set(swd)
        dq = np.array([abs(py[k]["q"] - swd[k]["q"]) for k in both])
        strong = [k for k in py if py[k]["q"] >= 0.3]
        found = sum(k in swd for k in strong)
        print(f"{man['id']} {pid}: {len(F)} features")
        print(f"  sightings: forecaster {len(py)}, app {len(swd)}, in both {len(both)}")
        print(f"  quality differs by a median {np.median(dq):.4f}, at most {dq.max():.3f}")
        print(f"  strong sightings (q >= 0.3): {found} of {len(strong)} found by the app")
        ok = found >= 0.99 * len(strong) and len(both) >= 0.98 * len(py)
        print()
        for title, opts in (("GPS throughout", []), ("GPS lost from 20 to 45 min", ["--gap", "1200-2700"]),
                            ("45 km right of the route in the cruise", ["--shift", "45"]),
                            ("no GPS, 25 min late, pinned 40 min in", ["--nogps", "--late", "1500", "--pin", "2400"])):
            out = subprocess.run([pv, "replay", d, pid, "--every", "600", *opts], check=True, capture_output=True, text=True).stdout
            tail = [ln for ln in out.splitlines() if ln.startswith(("routes", "position"))]
            print(f"  replay, {title}: " + "; ".join(tail))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
