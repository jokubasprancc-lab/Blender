"""Edit pass for the keyboard ad (pass 2 notes).

Notes being implemented:
  * Cut everything at frame 360.
  * Camera: faster, continuous, no slow-down / settle at the end of the roll.
    Retimed so the collapse lands around frame 350. The jump into the
    "car chase" section (~f260) is stretched so the keyboard is revealed
    more slowly / mysteriously.
  * Rings: stop scaling the ring objects. Instead shrink the radius of the
    curve the rings follow, so they close in and hug the keyboard without
    clipping into it.
  * Lightning ramps up in intensity towards the collapse.
  * Collapse -> hard cut, a few flashes, backlight intensifies.

Usage:
    blender -b scene.blend --python scripts/apply_edits.py -- config.json
    python scripts/apply_edits.py scene.blend config.json      # bpy module

Every object name lives in the config (see scripts/edit_config.example.json),
so nothing here is tied to one file. Run inspect_scene.py first to get names.
The edited file is saved next to the original as <name>_edit.blend; the
original is never overwritten.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

V = bpy.app.version

DEFAULTS = {
    "frame_end": 360,
    "collapse_frame": 350,
    "camera": None,                      # camera object name (default: scene camera)
    # Piecewise-linear retime of the camera's keys: [[old_frame, new_frame], ...].
    # Use extra anchors to stretch a window (e.g. the f260 transition) while
    # compressing the rest. Empty list = no retime.
    "camera_retime": [],
    "camera_continuous": True,           # AUTO handles + linear extrapolation (no settle)
    "camera_keep_ease_in": True,         # keep the very first key eased
    "rings": [],                         # ring object names
    "ring_path": None,                   # curve object the rings follow
    "keyboard": None,                    # keyboard root/body object (for clearance)
    "ring_clearance": 0.02,              # extra gap (scene units) between ring and keyboard
    "ring_shrink_start": None,           # frame the path starts shrinking (default: first ring scale key)
    "ring_shrink_end": None,             # default: collapse_frame
    "ring_shrink_scale": None,           # force a target path scale instead of computing it
    "lightning_materials": [],           # materials whose emission ramps up
    "lightning_lights": [],              # light objects that ramp up with the lightning
    "lightning_ramp_start": 280,
    "lightning_peak": 3.0,               # multiplier at collapse_frame
    "backlights": [],                    # light objects for flashes + intensify after the cut
    "backlight_materials": [],           # emissive materials (e.g. key legends) for the same
    "flash_pattern": [[0, 4.0], [1, 0.4], [3, 5.0], [4, 0.6], [6, 3.0]],  # [offset from cut, multiplier], held
    "backlight_final": 2.5,              # multiplier ramped to by frame_end
    "cut_camera": True,                  # add a static hero camera at the cut
    "cut_camera_dir": [1.0, -1.4, 0.8],  # direction from keyboard centre to the cut camera
    "cut_camera_lens": 50.0,
}


# --------------------------------------------------------------------- utils
def fcurves_of(idblock):
    ad = getattr(idblock, "animation_data", None)
    if ad is None or ad.action is None:
        return []
    if V >= (4, 4, 0):
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        return list(cb.fcurves) if cb else []
    return list(ad.action.fcurves)


def remove_fcurve(idblock, fc):
    ad = idblock.animation_data
    if V >= (4, 4, 0):
        from bpy_extras import anim_utils
        anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot).fcurves.remove(fc)
    else:
        ad.action.fcurves.remove(fc)


def piecewise(anchors):
    pts = sorted((float(a), float(b)) for a, b in anchors)

    def f(x):
        if x <= pts[0][0]:
            (a0, b0), (a1, b1) = pts[0], pts[1]
        elif x >= pts[-1][0]:
            (a0, b0), (a1, b1) = pts[-2], pts[-1]
        else:
            for (a0, b0), (a1, b1) in zip(pts, pts[1:]):
                if a0 <= x <= a1:
                    break
        return b0 + (x - a0) * (b1 - b0) / (a1 - a0)
    return f


def obj(name, required=True):
    if name is None:
        return None
    ob = bpy.data.objects.get(name)
    if ob is None and required:
        raise KeyError(f"object {name!r} not found; objects: {sorted(o.name for o in bpy.data.objects)}")
    return ob


def emission_sockets(mat):
    out = []
    if mat and mat.node_tree:
        for n in mat.node_tree.nodes:
            for s in n.inputs:
                if s.name in ("Emission Strength", "Strength") and n.type in ("BSDF_PRINCIPLED", "EMISSION"):
                    out.append(s)
    return out


def value_at(idblock, data_path, default, frame, index=0):
    for fc in fcurves_of(idblock):
        if fc.data_path == data_path and fc.array_index == index:
            return fc.evaluate(frame)
    return default


def key_multiplier(idblock, data_path, base_value, setter, keys, interp="BEZIER"):
    """Insert keys of base*mult at the given frames. setter(value) assigns the property."""
    for frame, mult in keys:
        setter(base_value * mult)
        idblock.keyframe_insert(data_path=data_path, frame=frame)
    for fc in fcurves_of(idblock):
        if fc.data_path == data_path:
            for k in fc.keyframe_points:
                if any(abs(k.co.x - f) < 0.01 for f, _ in keys):
                    k.interpolation = interp


def world_bbox(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    pts = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    for ch in ob.children_recursive:
        if ch.type in ("MESH", "CURVE", "FONT"):
            ce = ch.evaluated_get(dg)
            pts += [ce.matrix_world @ Vector(c) for c in ce.bound_box]
    return pts


def min_gap(rings, kb, frames, report=None):
    """Smallest signed gap between any ring vertex and the keyboard's local
    bounding box (children included) over `frames`; negative = clipping.
    Appends (frame, ring_name, gap) to `report` for every clipping frame."""
    sc = bpy.context.scene
    worst = float("inf")
    for f in frames:
        sc.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        kev = kb.evaluated_get(dg)
        loc, rot, _ = kev.matrix_world.decompose()
        inv = Matrix.LocRotScale(loc, rot, None).inverted()   # keyboard frame, world units
        pts = [inv @ (kev.matrix_world @ Vector(c)) for c in kev.bound_box]
        for ch in kb.children_recursive:
            if ch.type == "MESH":
                ce = ch.evaluated_get(dg)
                pts += [inv @ (ce.matrix_world @ Vector(c)) for c in ce.bound_box]
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        for r in rings:
            rev = r.evaluated_get(dg)
            me = rev.to_mesh()
            m = inv @ rev.matrix_world
            ring_worst = float("inf")
            for v in me.vertices:
                p = m @ v.co
                d = Vector([max(lo[i] - p[i], p[i] - hi[i]) for i in range(3)])
                outside = Vector([max(x, 0.0) for x in d]).length
                ring_worst = min(ring_worst, outside if outside > 0 else max(d))
            rev.to_mesh_clear()
            worst = min(worst, ring_worst)
            if report is not None and ring_worst < 0:
                report.append((f, r.name, ring_worst))
    return worst


# ------------------------------------------------------------------ actions
def retime_camera(cfg, cam):
    if not cfg["camera_retime"]:
        return
    f = piecewise(cfg["camera_retime"])
    for idb in (cam, cam.data):
        for fc in fcurves_of(idb):
            for k in fc.keyframe_points:
                dl = k.handle_left.x - k.co.x
                dr = k.handle_right.x - k.co.x
                k.co.x = f(k.co.x)
                k.handle_left.x = k.co.x + dl
                k.handle_right.x = k.co.x + dr
            fc.update()
    print(f"[camera] retimed with anchors {cfg['camera_retime']}")


def make_continuous(cfg, cam):
    if not cfg["camera_continuous"]:
        return
    for idb in (cam, cam.data):
        for fc in fcurves_of(idb):
            kps = fc.keyframe_points
            if len(kps) < 2:
                continue
            for i, k in enumerate(kps):
                if i == 0 and cfg["camera_keep_ease_in"]:
                    continue
                k.interpolation = "BEZIER"
                k.handle_left_type = k.handle_right_type = "AUTO"   # AUTO (not clamped): no flat stops
            fc.extrapolation = "LINEAR"                              # keeps moving past the last key
            fc.update()
    print("[camera] AUTO handles + linear extrapolation (no end settle)")


def rings_hug_keyboard(cfg):
    rings = [obj(n) for n in cfg["rings"]]
    path = obj(cfg["ring_path"], required=False)
    if not rings or path is None:
        print("[rings] skipped (no rings / ring_path in config)")
        return
    sc = bpy.context.scene
    first_scale_key = None
    for r in rings:
        for fc in list(fcurves_of(r)):
            if fc.data_path == "scale":
                fk = fc.keyframe_points[0].co.x
                first_scale_key = fk if first_scale_key is None else min(first_scale_key, fk)
                r.scale[fc.array_index] = fc.keyframe_points[0].co.y
                remove_fcurve(r, fc)
    print(f"[rings] removed ring scale animation (started f{first_scale_key})")

    start = cfg["ring_shrink_start"] or first_scale_key or (cfg["collapse_frame"] - 60)
    end = cfg["ring_shrink_end"] or cfg["collapse_frame"]

    base = tuple(path.scale)
    target = cfg["ring_shrink_scale"]
    if target is None:
        kb = obj(cfg["keyboard"], required=False)
        if kb is None:
            raise KeyError("set 'keyboard' (or 'ring_shrink_scale') so the hug radius can be computed")
        # Smallest path scale whose rings never enter the keyboard (plus clearance)
        # on any frame from the end of the shrink to the end of the shot.
        frames = list(range(int(end), sc.frame_end + 1, 2)) or [int(end)]
        lo, hi = 0.02, 1.0

        def ok(s):
            path.scale = (base[0] * s, base[1] * s, base[2])
            return min_gap(rings, kb, frames) >= cfg["ring_clearance"]
        if not ok(hi):
            print("[rings] WARNING: rings already clip at full path size; leaving path scale at 1.0")
            lo = hi
        for _ in range(14):
            mid = 0.5 * (lo + hi)
            hi, lo = (mid, lo) if ok(mid) else (hi, mid)
        target = hi
        path.scale = base
        print(f"[rings] tightest non-clipping path scale {target:.3f} (clearance {cfg['ring_clearance']})")

    path.scale = base
    path.keyframe_insert("scale", frame=start)
    path.scale = (base[0] * target, base[1] * target, base[2])
    path.keyframe_insert("scale", frame=end)
    for fc in fcurves_of(path):
        if fc.data_path == "scale":
            for k in fc.keyframe_points:
                k.interpolation = "BEZIER"
                k.easing = "EASE_IN"           # accelerates into the collapse
                k.handle_left_type = k.handle_right_type = "AUTO_CLAMPED"
            fc.update()
    print(f"[rings] path '{path.name}' scale {base[0]:.3f} -> {base[0] * target:.3f} over f{start}-{end}")


def lightning_ramp(cfg):
    s, e, peak = cfg["lightning_ramp_start"], cfg["collapse_frame"], cfg["lightning_peak"]
    for name in cfg["lightning_materials"]:
        mat = bpy.data.materials[name]
        for sock in emission_sockets(mat):
            path = sock.path_from_id("default_value")
            base = value_at(mat.node_tree, path, sock.default_value, s)
            key_multiplier(mat.node_tree, path, base,
                           lambda v, so=sock: setattr(so, "default_value", v),
                           [(s, 1.0), (e, peak)])
            print(f"[lightning] {name}.{sock.node.name} {base:.2f} -> {base * peak:.2f} over f{s}-{e}")
    for name in cfg["lightning_lights"]:
        li = obj(name).data
        base = value_at(li, "energy", li.energy, s)
        key_multiplier(li, "energy", base, lambda v, l=li: setattr(l, "energy", v), [(s, 1.0), (e, peak)])
        print(f"[lightning] light {name} {base:.1f} -> {base * peak:.1f} W")


def cut_and_flashes(cfg, cam):
    sc = bpy.context.scene
    cut = cfg["collapse_frame"]
    keys = [(cut + off, m) for off, m in cfg["flash_pattern"]]
    keys.append((cfg["frame_end"], cfg["backlight_final"]))
    pre = [(cut - 1, 1.0)]
    for name in cfg["backlights"]:
        li = obj(name).data
        base = value_at(li, "energy", li.energy, cut - 1)
        key_multiplier(li, "energy", base, lambda v, l=li: setattr(l, "energy", v), pre + keys, interp="CONSTANT")
        print(f"[cut] backlight {name}: flashes {cfg['flash_pattern']} then x{cfg['backlight_final']}")
    for name in cfg["backlight_materials"]:
        mat = bpy.data.materials[name]
        for sock in emission_sockets(mat):
            path = sock.path_from_id("default_value")
            base = value_at(mat.node_tree, path, sock.default_value, cut - 1)
            key_multiplier(mat.node_tree, path, base, lambda v, so=sock: setattr(so, "default_value", v),
                           pre + keys, interp="CONSTANT")
        print(f"[cut] backlight material {name}")
    # last flash -> final level should ramp, not snap
    for idb in [obj(n).data for n in cfg["backlights"]] + [bpy.data.materials[n].node_tree for n in cfg["backlight_materials"]]:
        for fc in fcurves_of(idb):
            kps = sorted(fc.keyframe_points, key=lambda k: k.co.x)
            if len(kps) >= 2:
                kps[-2].interpolation = "BEZIER"

    if cfg["cut_camera"]:
        kb = obj(cfg["keyboard"], required=False)
        if kb is None:
            print("[cut] no keyboard in config; skipping cut camera")
            return
        sc.frame_set(cut)
        pts = world_bbox(kb)
        centre = sum(pts, Vector()) / len(pts)
        size = max((p - centre).length for p in pts)
        d = Vector(cfg["cut_camera_dir"]).normalized()
        cam_data = cam.data.copy()
        cam_data.animation_data_clear()
        cam_data.lens = cfg["cut_camera_lens"]
        cut_cam = bpy.data.objects.new("CAM_Cut", cam_data)
        sc.collection.objects.link(cut_cam)
        sensor = cam_data.sensor_width if cam_data.sensor_fit != "VERTICAL" else cam_data.sensor_height
        fov = 2 * math.atan(sensor / (2 * cam_data.lens))
        aspect = min(sc.render.resolution_x, sc.render.resolution_y) / max(sc.render.resolution_x, sc.render.resolution_y)
        half_narrow = math.atan(math.tan(fov * 0.5) * aspect)   # narrow side of the frame
        dist = size / math.sin(half_narrow) * 1.1
        cut_cam.location = centre + d * dist
        cut_cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        if cam_data.dof.use_dof:
            cam_data.dof.focus_object = kb
        for m in [m for m in sc.timeline_markers if m.camera]:
            if m.frame >= cut:
                sc.timeline_markers.remove(m)
        if not any(m.camera for m in sc.timeline_markers):
            m0 = sc.timeline_markers.new("CAM_main", frame=sc.frame_start)
            m0.camera = cam
        mc = sc.timeline_markers.new("CUT", frame=cut)
        mc.camera = cut_cam
        print(f"[cut] hard cut to CAM_Cut at f{cut} (dist {dist:.2f})")


# --------------------------------------------------------------------- main
def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    blend = next((a for a in argv if a.endswith(".blend")), None)
    cfg_path = next((a for a in argv if a.endswith(".json")), None)
    if blend and os.path.abspath(blend) != os.path.abspath(bpy.data.filepath or ""):
        bpy.ops.wm.open_mainfile(filepath=blend)
    cfg = dict(DEFAULTS)
    if cfg_path:
        with open(cfg_path) as fh:
            cfg.update(json.load(fh))

    sc = bpy.context.scene
    sc.frame_end = cfg["frame_end"]
    print(f"[scene] frame_end -> {sc.frame_end}")
    cam = obj(cfg["camera"]) if cfg["camera"] else sc.camera

    retime_camera(cfg, cam)
    make_continuous(cfg, cam)
    rings_hug_keyboard(cfg)
    lightning_ramp(cfg)
    cut_and_flashes(cfg, cam)

    src = bpy.data.filepath
    out = cfg.get("output") or (os.path.splitext(src)[0] + "_edit.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True)
    print(f"[save] {out}")


if __name__ == "__main__":
    main()
