"""Report whether any ring passes into the keyboard, frame by frame.

Usage:
    python scripts/check_clearance.py scene_edit.blend config.json [step]

Uses the same test as apply_edits.py: every evaluated ring vertex against the
keyboard's bounding box (children included), in world units.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
from apply_edits import min_gap  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
blend = next((a for a in argv if a.endswith(".blend")), None)
with open(next(a for a in argv if a.endswith(".json"))) as fh:
    cfg = json.load(fh)
step = int(next((a for a in argv if a.isdigit()), 5))
if blend:
    bpy.ops.wm.open_mainfile(filepath=blend)

sc = bpy.context.scene
kb = bpy.data.objects[cfg["keyboard"]]
rings = [bpy.data.objects[n] for n in cfg["rings"]]
clips = []
gap = min_gap(rings, kb, range(sc.frame_start, sc.frame_end + 1, step), clips)
print(f"closest gap {gap:.4f} scene units")
if clips:
    frames = sorted({f for f, _, _ in clips})
    print(f"CLIPPING on frames {frames[:20]}{' ...' if len(frames) > 20 else ''}")
else:
    print("no clipping")
