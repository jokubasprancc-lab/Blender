"""Render a fast preview of a frame range and encode it to MP4.

Usage:
    python scripts/render_preview.py scene_edit.blend out_dir [--start 1] [--end 360]
        [--step 1] [--pct 40] [--samples 12] [--engine CYCLES]

Cycles on CPU with adaptive sampling + denoising, low resolution. Frames go
to out_dir/frames/####.png and are encoded to out_dir/preview.mp4 with the
ffmpeg from the imageio-ffmpeg package (or `ffmpeg` on PATH).
"""
import argparse
import os
import shutil
import subprocess
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
ap = argparse.ArgumentParser()
ap.add_argument("blend")
ap.add_argument("out")
ap.add_argument("--start", type=int)
ap.add_argument("--end", type=int)
ap.add_argument("--step", type=int, default=1)
ap.add_argument("--pct", type=int, default=40)
ap.add_argument("--samples", type=int, default=12)
ap.add_argument("--engine", default="CYCLES")
a = ap.parse_args(argv)

bpy.ops.wm.open_mainfile(filepath=a.blend)
sc = bpy.context.scene
start = a.start or sc.frame_start
end = a.end or sc.frame_end
sc.render.engine = a.engine
if a.engine == "CYCLES":
    sc.cycles.device = "CPU"
    sc.cycles.samples = a.samples
    sc.cycles.use_adaptive_sampling = True
    sc.cycles.adaptive_threshold = 0.05
    sc.cycles.use_denoising = True
    sc.cycles.denoiser = "OPENIMAGEDENOISE"
    sc.render.use_persistent_data = True
sc.render.resolution_percentage = a.pct
sc.render.image_settings.file_format = "PNG"
frames_dir = os.path.join(os.path.abspath(a.out), "frames")
os.makedirs(frames_dir, exist_ok=True)

for f in range(start, end + 1, a.step):
    path = os.path.join(frames_dir, f"{f:04d}.png")
    if os.path.exists(path):
        continue
    sc.frame_set(f)
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    print(f"[render] {f}/{end}", flush=True)

ff = shutil.which("ffmpeg")
if ff is None:
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ff = None
if ff:
    fps = sc.render.fps / sc.render.fps_base / a.step
    mp4 = os.path.join(os.path.abspath(a.out), "preview.mp4")
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(fps),
                    "-pattern_type", "glob", "-i", os.path.join(frames_dir, "*.png"),
                    "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "20", mp4], check=True)
    print(f"[encode] {mp4}")
else:
    print("[encode] no ffmpeg found; frames left in", frames_dir)
