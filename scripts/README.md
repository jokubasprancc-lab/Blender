# Keyboard ad: edit + preview scripts

Headless tools for the pass-2 notes (cut at f360, collapse ~f350, continuous
camera, rings hug the keyboard via the path radius, lightning ramp, hard cut +
backlight flashes). They run with Blender 4.2+ / 5.x, either as
`blender -b file.blend --python script.py -- args` or with the `bpy` module.

1. `inspect_scene.py file.blend` prints object names, keyframes, constraints and lights.
2. Copy `edit_config.example.json`, fill in the real object and material names and the camera retime anchors.
3. `apply_edits.py file.blend config.json` writes `file_edit.blend` and never touches the original.
4. `check_clearance.py file_edit.blend config.json 1` confirms no ring enters the keyboard on any frame.
5. `render_preview.py file_edit.blend out/ --pct 40 --samples 12` renders the frames to `out/preview.mp4`.

The ring path scale is solved by bisection against the real evaluated ring
geometry, so the rings stop exactly `ring_clearance` away from the keyboard.
