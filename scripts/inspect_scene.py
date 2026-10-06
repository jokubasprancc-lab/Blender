"""Dump everything about a .blend that the edit pass needs to know.

Usage:
    blender -b scene.blend --python scripts/inspect_scene.py
    python scripts/inspect_scene.py scene.blend        # with the `bpy` PyPI module

Prints frame range, cameras and their keyframes, every animated property
(with first/last key and interpolation), Follow Path / Curve modifiers that
tie the rings to a curve, lights, emissive materials and timeline markers.
"""
import sys

import bpy

if bpy.app.background and len(sys.argv) > 1 and sys.argv[-1].endswith(".blend") and not bpy.data.filepath:
    bpy.ops.wm.open_mainfile(filepath=sys.argv[-1])

V = bpy.app.version


def fcurves_of(idblock):
    ad = getattr(idblock, "animation_data", None)
    if ad is None or ad.action is None:
        return []
    if V >= (4, 4, 0):
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        return list(cb.fcurves) if cb else []
    return list(ad.action.fcurves)


def dump_fcurves(label, idblock):
    fcs = fcurves_of(idblock)
    for fc in fcs:
        kps = fc.keyframe_points
        if not len(kps):
            continue
        frames = [round(k.co.x, 1) for k in kps]
        vals = [round(k.co.y, 3) for k in kps]
        interp = sorted({k.interpolation for k in kps})
        mods = [m.type for m in fc.modifiers]
        print(f"    {label}.{fc.data_path}[{fc.array_index}] keys={len(kps)} "
              f"frames={frames if len(frames) <= 12 else frames[:6] + ['...'] + frames[-6:]} "
              f"values={vals if len(vals) <= 12 else vals[:6] + ['...'] + vals[-6:]} "
              f"interp={interp} extrap={fc.extrapolation} mods={mods}")


sc = bpy.context.scene
print("=" * 70)
print(f"Blender {bpy.app.version_string}  file={bpy.data.filepath}")
print(f"Scene '{sc.name}' frames {sc.frame_start}-{sc.frame_end} @ {sc.render.fps}/{sc.render.fps_base} fps")
print(f"Render {sc.render.engine} {sc.render.resolution_x}x{sc.render.resolution_y} @ {sc.render.resolution_percentage}%")
print(f"Active camera: {sc.camera.name if sc.camera else None}")
print(f"Markers: {[(m.name, m.frame, m.camera.name if m.camera else None) for m in sc.timeline_markers]}")
print(f"View transform: {sc.view_settings.view_transform} / look {sc.view_settings.look} / exposure {sc.view_settings.exposure}")

print("\n--- Objects ---")
for ob in sorted(bpy.data.objects, key=lambda o: o.name):
    dims = tuple(round(d, 3) for d in ob.dimensions)
    print(f"{ob.type:8} {ob.name!r} parent={ob.parent.name if ob.parent else None} "
          f"loc={tuple(round(v, 3) for v in ob.matrix_world.translation)} scale={tuple(round(v, 3) for v in ob.scale)} dims={dims} "
          f"hide_render={ob.hide_render} colls={[c.name for c in ob.users_collection]}")
    for con in ob.constraints:
        extra = ""
        if con.type == "FOLLOW_PATH":
            extra = f" target={con.target.name if con.target else None} offset={con.offset} use_curve_follow={con.use_curve_follow}"
        elif hasattr(con, "target"):
            extra = f" target={con.target.name if con.target else None}"
        print(f"    constraint {con.type} {con.name!r}{extra}")
    for md in ob.modifiers:
        extra = ""
        if md.type == "CURVE":
            extra = f" object={md.object.name if md.object else None}"
        elif md.type == "NODES" and md.node_group:
            extra = f" group={md.node_group.name!r}"
        print(f"    modifier {md.type} {md.name!r}{extra}")
    dump_fcurves("ob", ob)
    if ob.data is not None:
        dump_fcurves("data", ob.data)
    if ob.type == "CURVE":
        cu = ob.data
        print(f"    curve: splines={len(cu.splines)} bevel_depth={cu.bevel_depth} extrude={cu.extrude} "
              f"path_duration={cu.path_duration} use_path={cu.use_path} eval_time={cu.eval_time}")
        for sp in cu.splines[:3]:
            pts = sp.bezier_points if sp.type == "BEZIER" else sp.points
            radii = [round(p.radius, 3) for p in pts][:8]
            print(f"      spline {sp.type} cyclic={sp.use_cyclic_u} points={len(pts)} radii={radii}")
    if ob.type == "LIGHT":
        li = ob.data
        print(f"    light {li.type} energy={li.energy} color={tuple(round(c, 3) for c in li.color)}")
    if ob.type == "CAMERA":
        ca = ob.data
        print(f"    camera lens={ca.lens} dof={ca.dof.use_dof} focus_obj={ca.dof.focus_object.name if ca.dof.focus_object else None} "
              f"fstop={ca.dof.aperture_fstop}")

print("\n--- Emissive / animated materials ---")
for mat in bpy.data.materials:
    if not mat.node_tree:
        continue
    for n in mat.node_tree.nodes:
        for s in n.inputs:
            if s.name in ("Emission Strength", "Strength") and hasattr(s, "default_value"):
                print(f"  {mat.name!r} node {n.name!r} {s.name}={round(s.default_value, 3)} users={mat.users}")
    dump_fcurves(f"mat[{mat.name}]", mat.node_tree)

print("\n--- World ---")
if sc.world and sc.world.node_tree:
    dump_fcurves("world", sc.world.node_tree)

print("\n--- Geometry node groups ---")
for ng in bpy.data.node_groups:
    if ng.bl_idname == "GeometryNodeTree":
        sims = [n.name for n in ng.nodes if n.bl_idname == "GeometryNodeSimulationOutput"]
        print(f"  {ng.name!r} nodes={len(ng.nodes)} sim_zones={sims}")
        dump_fcurves(f"gn[{ng.name}]", ng)
print("=" * 70)
