#!/usr/bin/env python3
"""Tessellate Adafruit STEP files and render isometric thumbnail images."""

from __future__ import annotations

import io
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path

import cascadio
import moderngl
import numpy as np
import trimesh
from PIL import Image

# Catalog card thumb background (light theme)
BG = np.array([0xEC, 0xE4, 0xD8], dtype=np.float32) / 255.0
WIDTH, HEIGHT = 600, 450
RENDER_SCALE = 2  # render at 2x then downsize
SAMPLES = 4


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb_u8(c: np.ndarray) -> np.ndarray:
    # c is 0-1 float RGB already in sRGB if we write sRGB in the shader
    return np.clip(np.round(c * 255.0), 0, 255).astype(np.uint8)


def pick_main_step(steps: list[dict], dirname: str) -> dict:
    if len(steps) == 1:
        return steps[0]

    def score(st: dict) -> tuple:
        name = st["name"].lower()
        s = 0
        # prefer names close to the directory
        dir_norm = " ".join(dirname.lower().replace("-", " ").split())
        stem = name.rsplit(".", 1)[0]
        stem_norm = " ".join(stem.replace("-", " ").split())
        if dir_norm in stem_norm or stem_norm in dir_norm:
            s += 20
        # later revisions
        for i, rev in enumerate(["rev a", "rev b", "rev c", "rev d", "rev e", "v2", "v3"]):
            if rev in name:
                s += 5 + i
        # penalize partial / secondary bits
        for bad in ("keyplate", "key-plate", "bottom-plate", "bottom plate",
                    "plate", "pads", "drawing", "switch"):
            if bad in name:
                s -= 12
        # assembly can be a useful complete view, slight bonus
        if "assembly" in name:
            s += 6
        # larger file often has more of the product (weak signal)
        s += min(st.get("size", 0) / 5_000_000, 3)
        return (s, st.get("size", 0), name)

    return max(steps, key=score)


def load_step_scene(step_bytes: bytes, linear_tol: float = 0.12) -> trimesh.Scene:
    glb = cascadio.load(
        step_bytes,
        file_type="step",
        include_materials=True,
        merge_primitives=True,
        use_parallel=True,
        tol_linear=float(linear_tol),
        tol_angular=0.3,
        tol_relative=False,
    )
    if not glb:
        raise RuntimeError("cascadio returned empty GLB")
    scene = trimesh.load(io.BytesIO(glb), file_type="glb", force="scene")
    if not isinstance(scene, trimesh.Scene):
        scene = trimesh.Scene(scene)
    if not scene.geometry:
        raise RuntimeError("no geometry in converted scene")
    return scene


def scene_to_arrays(scene: trimesh.Scene) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return vertices (N,3), normals (N,3), colors (N,3) in 0-1 sRGB, one vertex per corner (flat)."""
    verts_l = []
    norms_l = []
    cols_l = []
    nodes = list(scene.graph.nodes_geometry)
    if not nodes:
        # some scenes only have geometry dict
        items = [(np.eye(4), name) for name in scene.geometry]
    else:
        items = [scene.graph.get(n) for n in nodes]

    for transform, geom_name in items:
        geom = scene.geometry[geom_name]
        if geom.faces is None or len(geom.faces) == 0:
            continue
        mesh = geom.copy()
        if transform is not None:
            mesh.apply_transform(transform)
        v = np.asarray(mesh.vertices, dtype=np.float64)
        f = np.asarray(mesh.faces, dtype=np.int64)
        # face vertices
        fv = v[f]  # F,3,3
        # face normals
        e1 = fv[:, 1] - fv[:, 0]
        e2 = fv[:, 2] - fv[:, 0]
        fn = np.cross(e1, e2)
        nlen = np.linalg.norm(fn, axis=1, keepdims=True)
        nlen = np.maximum(nlen, 1e-12)
        fn = fn / nlen
        fn3 = np.repeat(fn[:, None, :], 3, axis=1)  # F,3,3

        color = np.array([0.62, 0.62, 0.62], dtype=np.float32)
        vis = getattr(mesh, "visual", None)
        mat = getattr(vis, "material", None) if vis is not None else None
        if mat is not None and getattr(mat, "baseColorFactor", None) is not None:
            bc = np.asarray(mat.baseColorFactor, dtype=np.float64).reshape(-1)
            # trimesh exposes glTF baseColorFactor (linear) as uint8 0-255
            if bc.max() > 1.0 + 1e-6:
                bc = bc / 255.0
            lin = np.clip(bc[:3], 0, 1)
            # convert linear -> sRGB for the shader (which converts back to linear)
            cut = lin <= 0.0031308
            srgb = np.empty_like(lin)
            srgb[cut] = 12.92 * lin[cut]
            srgb[~cut] = 1.055 * np.power(lin[~cut], 1.0 / 2.4) - 0.055
            color = srgb.astype(np.float32)
        cols = np.broadcast_to(color, (fv.shape[0] * 3, 3)).copy()

        verts_l.append(fv.reshape(-1, 3))
        norms_l.append(fn3.reshape(-1, 3))
        cols_l.append(cols)

    if not verts_l:
        raise RuntimeError("no faces after assembling scene")
    return (
        np.concatenate(verts_l).astype(np.float32),
        np.concatenate(norms_l).astype(np.float32),
        np.concatenate(cols_l).astype(np.float32),
    )


def orient_object(vertices: np.ndarray, normals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Put the thinnest AABB axis up (Z) and the longest along X."""
    mins = vertices.min(axis=0)
    maxs = vertices.max(axis=0)
    extents = maxs - mins
    # Avoid degenerate permutation when nearly a cube
    order = np.argsort(extents)  # small, mid, large
    # source axes -> (X=large, Y=mid, Z=small)
    perm = np.array([order[2], order[1], order[0]], dtype=int)
    v = vertices[:, perm]
    n = normals[:, perm]
    # If the permutation is odd, flip one axis to keep a right-handed frame (optional)
    # Determine a "top": more projected area toward +Z should face up.
    # Use area-weighted face normals (every 3 verts share a normal)
    fn = n[0::3]
    # approximate area from... we don't have it here; equal weight is ok
    z_sign = np.sign(fn[:, 2].mean()) or 1.0
    if z_sign < 0:
        v = v * np.array([1, 1, -1], dtype=np.float32)
        n = n * np.array([1, 1, -1], dtype=np.float32)
    # center at origin
    center = (v.min(axis=0) + v.max(axis=0)) * 0.5
    v = v - center
    return v, n


def look_at(eye: np.ndarray, target: np.ndarray, up: np.ndarray) -> np.ndarray:
    f = target - eye
    f = f / (np.linalg.norm(f) + 1e-12)
    s = np.cross(f, up)
    s = s / (np.linalg.norm(s) + 1e-12)
    u = np.cross(s, f)
    m = np.eye(4, dtype=np.float32)
    m[0, :3] = s
    m[1, :3] = u
    m[2, :3] = -f
    t = np.eye(4, dtype=np.float32)
    t[:3, 3] = -eye
    return m @ t


def ortho(l, r, b, t, n, f) -> np.ndarray:
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0] = 2 / (r - l)
    m[1, 1] = 2 / (t - b)
    m[2, 2] = -2 / (f - n)
    m[0, 3] = -(r + l) / (r - l)
    m[1, 3] = -(t + b) / (t - b)
    m[2, 3] = -(f + n) / (f - n)
    m[3, 3] = 1
    return m


VERT = """
#version 330
uniform mat4 u_mvp;
uniform mat3 u_nmat;
in vec3 in_pos;
in vec3 in_n;
in vec3 in_col;
out vec3 v_n;
out vec3 v_col;
out vec3 v_pos;
void main() {
    v_pos = in_pos;
    v_n = normalize(u_nmat * in_n);
    v_col = in_col;
    gl_Position = u_mvp * vec4(in_pos, 1.0);
}
"""

FRAG = """
#version 330
in vec3 v_n;
in vec3 v_col;
in vec3 v_pos;
out vec4 f_color;
uniform vec3 u_eye;
uniform vec3 u_l1;
uniform vec3 u_l2;

vec3 to_lin(vec3 c) {
    bvec3 cutoff = lessThanEqual(c, vec3(0.04045));
    vec3 low = c / 12.92;
    vec3 high = pow((c + 0.055) / 1.055, vec3(2.4));
    return mix(high, low, cutoff);
}
vec3 to_srgb(vec3 c) {
    bvec3 cutoff = lessThanEqual(c, vec3(0.0031308));
    vec3 low = c * 12.92;
    vec3 high = 1.055 * pow(c, vec3(1.0/2.4)) - 0.055;
    return mix(high, low, cutoff);
}

void main() {
    vec3 n = normalize(v_n);
    if (!gl_FrontFacing) n = -n;
    vec3 col = to_lin(clamp(v_col, 0.0, 1.0));
    vec3 V = normalize(u_eye - v_pos);
    vec3 L1 = normalize(u_l1);
    vec3 L2 = normalize(u_l2);
    float amb = 0.22;
    float hemi = 0.14 * (0.5 * n.z + 0.5);
    float d1 = max(dot(n, L1), 0.0);
    float d2 = max(dot(n, L2), 0.0) * 0.40;
    // wrap a little so dark faces aren't ink-black
    float wrap = 0.10 * max(dot(n, L1) + 0.4, 0.0);
    vec3 H1 = normalize(L1 + V);
    float spec = pow(max(dot(n, H1), 0.0), 40.0) * 0.22;
    vec3 lit = col * (amb + hemi + d1 + d2 + wrap) + vec3(spec);
    f_color = vec4(to_srgb(clamp(lit, 0.0, 1.0)), 1.0);
}
"""


class Renderer:
    def __init__(self, width: int = WIDTH, height: int = HEIGHT, scale: int = RENDER_SCALE):
        self.width = width
        self.height = height
        self.rw = width * scale
        self.rh = height * scale
        self.scale = scale
        self.ctx = moderngl.create_context(standalone=True, require=330)
        self.prog = self.ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        color = self.ctx.texture((self.rw, self.rh), 4, samples=SAMPLES)
        depth = self.ctx.depth_renderbuffer((self.rw, self.rh), samples=SAMPLES)
        self.fbo = self.ctx.framebuffer(color_attachments=[color], depth_attachment=depth)
        self.resolve_tex = self.ctx.texture((self.rw, self.rh), 4)
        self.resolve = self.ctx.framebuffer(color_attachments=[self.resolve_tex])
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.ctx.disable(moderngl.CULL_FACE)

    def render(self, vertices: np.ndarray, normals: np.ndarray, colors: np.ndarray) -> Image.Image:
        v, n = orient_object(vertices, normals)
        # Isometric-ish from above-front-right
        direction = np.array([1.05, -1.2, 0.95], dtype=np.float32)
        direction /= np.linalg.norm(direction)
        # Temporary view at unit distance to measure extents
        eye0 = direction * 100.0
        view = look_at(eye0, np.zeros(3, dtype=np.float32), np.array([0, 0, 1], dtype=np.float32))
        vh = np.c_[v, np.ones((len(v), 1), dtype=np.float32)]
        vv = (view @ vh.T).T[:, :3]
        mins = vv.min(axis=0)
        maxs = vv.max(axis=0)
        cx, cy = (mins[0] + maxs[0]) * 0.5, (mins[1] + maxs[1]) * 0.5
        span_x = maxs[0] - mins[0]
        span_y = maxs[1] - mins[1]
        pad = 1.14  # ~7% margin each side after aspect fit
        aspect = self.rw / self.rh
        half_w = (span_x * pad) / 2
        half_h = (span_y * pad) / 2
        if half_w / half_h > aspect:
            half_h = half_w / aspect
        else:
            half_w = half_h * aspect
        near, far = -maxs[2] - 2 * max(span_x, span_y) - 10, -mins[2] + 2 * max(span_x, span_y) + 10
        # If depth range inverted oddly, keep a wide range
        if far <= near:
            near, far = -1e6, 1e6
        proj = ortho(cx - half_w, cx + half_w, cy - half_h, cy + half_h, near, far)
        mvp = proj @ view

        # normal matrix: upper 3x3 of view (no scale)
        nmat = view[:3, :3].astype(np.float32)

        vbo_p = self.ctx.buffer(np.ascontiguousarray(v, dtype="f4").tobytes())
        vbo_n = self.ctx.buffer(np.ascontiguousarray(n, dtype="f4").tobytes())
        vbo_c = self.ctx.buffer(np.ascontiguousarray(colors, dtype="f4").tobytes())
        vao = self.ctx.vertex_array(
            self.prog,
            [
                (vbo_p, "3f", "in_pos"),
                (vbo_n, "3f", "in_n"),
                (vbo_c, "3f", "in_col"),
            ],
        )
        self.prog["u_mvp"].write(mvp.T.tobytes())
        self.prog["u_nmat"].write(nmat.T.tobytes())
        self.prog["u_eye"].value = tuple(eye0.tolist())
        # lights in world space: key from camera-left-above, fill from opposite
        self.prog["u_l1"].value = tuple((direction + np.array([0.2, 0.15, 0.55], dtype=np.float32)).tolist())
        self.prog["u_l2"].value = tuple((-direction[0] * 0.4, 0.8, 0.35))

        self.fbo.use()
        self.ctx.viewport = (0, 0, self.rw, self.rh)
        self.ctx.clear(*BG.tolist(), 1.0)
        vao.render(moderngl.TRIANGLES)
        self.ctx.copy_framebuffer(self.resolve, self.fbo)
        raw = self.resolve.read(components=3, alignment=1)
        vao.release()
        vbo_p.release()
        vbo_n.release()
        vbo_c.release()
        img = Image.frombytes("RGB", (self.rw, self.rh), raw)
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
        if self.scale != 1:
            img = img.resize((self.width, self.height), Image.Resampling.LANCZOS)
        return img


def step_file_to_image(path: Path | None = None, data: bytes | None = None, renderer: Renderer | None = None,
                       linear_tol: float | None = None) -> Image.Image:
    if data is None:
        assert path is not None
        data = path.read_bytes()
    # Adaptive linear tolerance: start from a default, refine if mesh is huge/tiny after a cheap first convert.
    if linear_tol is None:
        # Estimate from file size: bigger STEP -> slightly looser
        sz = len(data)
        if sz > 8_000_000:
            linear_tol = 0.28
        elif sz > 2_000_000:
            linear_tol = 0.16
        else:
            linear_tol = 0.10
    scene = load_step_scene(data, linear_tol=linear_tol)
    vertices, normals, colors = scene_to_arrays(scene)
    # If extremely dense, could skip; 2M tris is still ok for llvmpipe at 1200x900
    own = False
    if renderer is None:
        renderer = Renderer()
        own = True
    try:
        return renderer.render(vertices, normals, colors)
    finally:
        if own:
            renderer.ctx.release()


def save_webp(img: Image.Image, dest: Path, quality: int = 82, target_max_kb: int = 60) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    q = quality
    while q >= 62:
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=q, method=4)
        size = buf.tell()
        if size <= target_max_kb * 1024 or q <= 62:
            dest.write_bytes(buf.getvalue())
            return size
        q -= 6
    return dest.stat().st_size


def slug_dir(dirname: str) -> str:
    # stable filename from product directory
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in dirname).strip("_")


def main_samples() -> None:
    outdir = Path("/workspace/adafruit_cad_parts/thumbs/samples")
    outdir.mkdir(parents=True, exist_ok=True)
    renderer = Renderer()
    for p in sorted(Path("/tmp/step_samples").glob("*.step")):
        t0 = time.time()
        print("rendering", p.name, flush=True)
        img = step_file_to_image(path=p, renderer=renderer)
        dest = outdir / (p.stem + ".webp")
        png = outdir / (p.stem + ".png")
        size = save_webp(img, dest)
        img.save(png)  # lossless preview for visual QA
        print(f"  -> {dest} {size} bytes, also {png} in {time.time()-t0:.2f}s", flush=True)



def download(url: str, dest: Path, timeout: int = 90) -> None:
    import urllib.request
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "AdafruitCADThumb/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(dest)


def run_batch(list_path: Path, outdir: Path, cache: Path, timeout_s: int = 180) -> None:
    import signal

    class TimeoutError_(Exception):
        pass

    def _alarm(signum, frame):
        raise TimeoutError_(f"timeout after {timeout_s}s")

    signal.signal(signal.SIGALRM, _alarm)
    jobs = json.loads(list_path.read_text())
    outdir.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    log_path = outdir / "render.log"
    manifest_path = outdir / "manifest.json"
    results = []
    if manifest_path.exists():
        try:
            results = json.loads(manifest_path.read_text())
        except Exception:
            results = []
    done = {r["dir"] for r in results if r.get("ok")}
    renderer = Renderer()
    n = len(jobs)
    print(f"batch start: {n} parts, {len(done)} already done", flush=True)
    for i, part in enumerate(jobs, 1):
        dirname = part["dir"]
        slug = slug_dir(dirname)
        dest = outdir / f"{slug}.webp"
        rec = {"dir": dirname, "slug": slug}
        if dirname in done and dest.exists():
            print(f"[{i}/{n}] skip exists {dirname}", flush=True)
            continue
        try:
            st = pick_main_step(part["steps"], dirname)
            rec["step"] = st["name"]
            rec["step_size"] = st["size"]
            rec["url"] = st["url"]
            cache_name = slug_dir(st["name"]) or "file.step"
            cache_file = cache / f"{slug}__{cache_name}"
            t0 = time.time()
            if not cache_file.exists() or cache_file.stat().st_size == 0:
                print(f"[{i}/{n}] download {dirname} / {st['name']}", flush=True)
                download(st["url"], cache_file)
            print(f"[{i}/{n}] render {dirname} ({cache_file.stat().st_size} bytes)", flush=True)
            signal.alarm(timeout_s)
            try:
                img = step_file_to_image(path=cache_file, renderer=renderer)
            finally:
                signal.alarm(0)
            size = save_webp(img, dest)
            rec.update(ok=True, bytes=size, seconds=round(time.time() - t0, 2),
                       width=img.width, height=img.height)
            print(f"    ok {size} bytes in {rec['seconds']}s", flush=True)
        except TimeoutError_ as e:
            rec.update(ok=False, error=str(e), seconds=timeout_s)
            print(f"    FAIL timeout {dirname}", flush=True)
        except Exception as e:
            rec.update(ok=False, error=f"{type(e).__name__}: {e}", seconds=round(time.time() - t0, 2) if "t0" in locals() else None)
            rec["trace"] = traceback.format_exc()[-1500:]
            print(f"    FAIL {dirname}: {rec['error']}", flush=True)
        # replace previous record for this dir
        results = [r for r in results if r.get("dir") != dirname]
        results.append(rec)
        manifest_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        with log_path.open("a") as log:
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
    ok = sum(1 for r in results if r.get("ok"))
    fail = [r for r in results if not r.get("ok")]
    print(f"batch done: ok={ok} fail={len(fail)} total_records={len(results)}", flush=True)
    for r in fail:
        print("  FAIL", r["dir"], r.get("error"), flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "samples":
        main_samples()
    elif cmd == "batch":
        list_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/need_thumbs.json")
        outdir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/workspace/adafruit_cad_parts/thumbs")
        cache = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("/tmp/step_cache")
        timeout_s = int(sys.argv[5]) if len(sys.argv) > 5 else 180
        run_batch(list_path, outdir, cache, timeout_s=timeout_s)
    else:
        print("use: render_thumbs.py samples | batch [list.json outdir cache timeout]")
