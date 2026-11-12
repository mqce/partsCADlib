#!/usr/bin/env python3
"""Build a self-contained Japanese catalog of Adafruit and SparkFun CAD parts."""

from __future__ import annotations

import base64
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

OUT_PATH = Path("/workspace/adafruit_cad_parts/index.html")
THUMBS_DIR = Path("/workspace/adafruit_cad_parts/thumbs")
ZIP_PATH = Path("/workspace/adafruit_cad_parts/catalog.zip")
ADA_TREE = Path("/tmp/tree.json")
SF_TREE = Path("/tmp/sf-tree.json")
SF_NAMES = Path("/tmp/sf_product_names.json")
SF_EAGLE = Path("/tmp/sf_eagleup.json")

ADA = {
    "maker": "adafruit",
    "label": "Adafruit",
    "repo": "adafruit/Adafruit_CAD_Parts",
    "branch": "main",
    "commit": "9f2a89f3d5c3491886d58cb7fe83385417563cf4",
    "commit_utc": "2026-11-05T15:08:48Z",
    "product_url": "https://www.adafruit.com/product/{id}",
}
SF = {
    "maker": "sparkfun",
    "label": "SparkFun",
    "repo": "sparkfun/3D_Models",
    "branch": "master",
    "commit": "53d298c3b8202cf8fce9ab61885855720c84c012",
    "commit_utc": "2023-10-26T22:06:11Z",
    "product_url": "https://www.sparkfun.com/products/{id}",
}

# Verified by fetching first bytes (Adafruit extensionless files).
EXTENSIONLESS = {
    "1602 CAP1188/1602 Adafruit CAP1188": "step",
    "2925 8-Way Switch/2925-8-way-switch": "step",
    "413 Large Solenoid/413-large-solenoid": "image",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
STEP_EXTS = {".step", ".stp"}
SKIP_FILES = {".ds_store", "thumbs.db"}
SKIP_META_EXTS = {".md", ".json", ".rpt", ".txt", ".html"}

# Display label for a file extension (uppercase, IGES covers .igs, STEP covers .stp)
EXT_LABEL = {
    ".step": "STEP", ".stp": "STEP",
    ".iges": "IGES", ".igs": "IGES",
    ".stl": "STL", ".3mf": "3MF",
    ".f3d": "F3D", ".f3z": "F3Z",
    ".sldprt": "SLDPRT", ".sldasm": "SLDASM",
    ".blend": "BLEND", ".skp": "SKP",
    ".obj": "OBJ", ".dae": "DAE", ".mtl": "MTL",
    ".zip": "ZIP", ".pdf": "PDF",
    ".dxf": "DXF", ".svg": "SVG",
    ".brd": "BRD", ".sch": "SCH",
    ".prt": "PRT", ".x_t": "X_T", ".brep": "BREP",
    ".scad": "SCAD", ".fcstd": "FCSTD", ".wrl": "WRL",
    ".fbx": "FBX", ".smt": "SMT",
    ".jpg": "JPG", ".jpeg": "JPG", ".png": "PNG",
    ".gif": "GIF", ".webp": "WEBP", ".bmp": "BMP",
}
EXT_PRIORITY = [
    "STEP", "IGES", "STL", "3MF", "F3D", "F3Z", "SLDPRT", "SLDASM",
    "BLEND", "SKP", "OBJ", "DAE", "ZIP", "DXF", "SVG", "PDF",
    "BRD", "SCH", "SCAD", "FCSTD", "WRL", "PRT", "X_T", "BREP",
    "FBX", "SMT", "PNG", "JPG", "GIF", "WEBP", "BMP", "MTL",
]

JST = timezone(timedelta(hours=9))
MAX_HTML_BYTES = 10 * 1024 * 1024

ACRONYMS = {
    "dc": "DC", "ac": "AC", "usb": "USB", "led": "LED", "rgb": "RGB",
    "smd": "SMD", "spdt": "SPDT", "spst": "SPST", "dpdt": "DPDT",
    "avr": "AVR", "i2c": "I2C", "i2s": "I2S", "spi": "SPI",
    "lcd": "LCD", "oled": "OLED", "tft": "TFT", "gps": "GPS",
    "imu": "IMU", "ble": "BLE", "wifi": "WiFi", "poe": "PoE",
    "can": "CAN", "sma": "SMA", "jst": "JST", "pth": "PTH",
    "rj11": "RJ11", "rj45": "RJ45", "db9": "DB9", "db25": "DB25",
    "xbee": "XBee", "lora": "LoRa", "qwiic": "Qwiic",
    "esp32": "ESP32", "esp8266": "ESP8266", "nrf": "nRF",
    "lipo": "LiPo", "liion": "Li-Ion", "icsp": "ICSP", "uart": "UART",
    "gpio": "GPIO", "pwm": "PWM", "hat": "HAT", "qt": "QT",
    "ic": "IC", "pcb": "PCB", "rf": "RF", "ir": "IR", "uv": "UV",
    "ssop": "SSOP", "soic": "SOIC", "qfn": "QFN", "dip": "DIP",
    "xh": "XH", "ph": "PH", "mm": "mm", "cm": "cm",
}


def raw_url(repo: str, branch: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{quote(path, safe='/')}"


def github_tree_url(repo: str, branch: str, path: str) -> str:
    return f"https://github.com/{repo}/tree/{branch}/{quote(path, safe='/')}"


def suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." not in name or name.startswith("."):
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def classify_image_or_step(path: str) -> str | None:
    ext = suffix(path)
    if ext in IMAGE_EXTS:
        return "image"
    if ext in STEP_EXTS:
        return "step"
    if path in EXTENSIONLESS:
        return EXTENSIONLESS[path]
    return None


def file_label(path: str) -> str | None:
    """Return a compact type label, or None if the file should not be listed."""
    name = path.rsplit("/", 1)[-1]
    if name.lower() in SKIP_FILES:
        return None
    ext = suffix(path)
    if path in EXTENSIONLESS:
        return "STEP" if EXTENSIONLESS[path] == "step" else "JPG"
    if not ext or ext in SKIP_META_EXTS:
        return None
    return EXT_LABEL.get(ext, ext[1:].upper())


def download_name(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if path in EXTENSIONLESS:
        return name + (".step" if EXTENSIONLESS[path] == "step" else ".jpg")
    return name


def slug_dir(key: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key).strip("_")


def pretty_token(tok: str) -> str:
    raw = tok.strip()
    if not raw:
        return raw
    low = raw.lower()
    if low in ACRONYMS:
        return ACRONYMS[low]
    m = re.fullmatch(r"(\d+)(v|mhz|khz|mah|mm|cm|awg|k|w|a|hz)", low)
    if m:
        unit = {"v": "V", "mhz": "MHz", "khz": "kHz", "mah": "mAh", "mm": "mm",
                "cm": "cm", "awg": "AWG", "k": "k", "w": "W", "a": "A", "hz": "Hz"}[m.group(2)]
        return m.group(1) + unit
    if raw.isupper() or raw.isdigit():
        return raw
    # rs485, nrf24l01, etc.
    if low.startswith("rs") and re.match(r"rs-?\d+", low):
        return "RS-" + re.sub(r"^rs-?", "", low)
    if low.startswith("nrf"):
        return "nRF" + raw[3:]
    return raw[0].upper() + raw[1:] if raw else raw


def pretty_stem(stem: str) -> str:
    s = stem.replace("_20", " ")
    s = s.replace("%20", " ")
    s = re.sub(r"[_/]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # keep hyphens inside tokens like RS-485, SSOP20
    parts = re.split(r"[\s]+", s)
    out = []
    for p in parts:
        if "-" in p and not re.fullmatch(r"\d+-\d+", p):
            out.append("-".join(pretty_token(x) if x else x for x in p.split("-")))
        else:
            out.append(pretty_token(p))
    name = " ".join(x for x in out if x)
    name = re.sub(r"\bV(\d+)\b", lambda m: "v" + m.group(1), name)
    return name


def image_score(path: str, dirname: str, size: int, prefer_display: bool = False) -> tuple:
    fname = path.rsplit("/", 1)[-1].lower()
    stem = fname.rsplit(".", 1)[0] if "." in fname else fname
    ext = suffix(path)
    score = 0
    if prefer_display:
        if "/display/" in path.replace("\\", "/"):
            score += 25
        if "/geometry_edit_blender/" in path or "/renderings/" in path:
            score -= 8
    if ext == ".gif":
        score -= 25
    elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
        score += 12
    elif ext == "":
        score += 5
    if "rear" in fname or "bottom" in fname:
        score -= 18
    if "front" in fname or "top" in fname:
        score += 16
    if "drawing" in fname or "dimension" in fname or "texture" in fname:
        score -= 12
    dir_norm = re.sub(r"[^a-z0-9]+", " ", dirname.lower()).strip()
    stem_norm = re.sub(r"[^a-z0-9]+", " ", stem).strip()
    if dir_norm and (dir_norm in stem_norm or stem_norm in dir_norm):
        score += 8
    return (score, size, fname)


def parse_adafruit_dir(dirname: str) -> tuple[list[int], str]:
    m = re.match(r"^((?:\d+\s+)+)(.*)$", dirname)
    if not m:
        return [], dirname
    ids = [int(x) for x in m.group(1).split()]
    name = m.group(2).strip() or dirname
    return ids, name


def iter_download_files(rel_paths: list[dict], repo: str, branch: str) -> list[dict]:
    files = []
    for entry in rel_paths:
        path = entry["path"]
        label = file_label(path)
        if not label:
            continue
        # Skip duplicate preview images living next to Blender sources when a display/ copy exists
        files.append({
            "name": download_name(path),
            "label": label,
            "url": raw_url(repo, branch, path),
            "size": int(entry.get("size") or 0),
        })
    # Drop blender-folder images if the same filename exists under display/
    display_imgs = {f["name"].lower() for f in files if f["label"] in {"PNG", "JPG", "GIF", "WEBP"}}
    # Actually we don't have path in the dict now... keep it simple: include all listed files.
    # Sort by type priority then name
    pri = {lab: i for i, lab in enumerate(EXT_PRIORITY)}
    files.sort(key=lambda f: (pri.get(f["label"], 80), f["name"].lower()))
    return files


def number_labels(files: list[dict]) -> None:
    counts: dict[str, int] = defaultdict(int)
    for f in files:
        counts[f["label"]] += 1
    seen: dict[str, int] = defaultdict(int)
    for f in files:
        seen[f["label"]] += 1
        if counts[f["label"]] > 1:
            f["button"] = f"{f['label']} {seen[f['label']]}"
        else:
            f["button"] = f["label"]


def build_adafruit_parts(tree: list[dict]) -> list[dict]:
    by_dir: dict[str, list[dict]] = defaultdict(list)
    dirs: list[str] = []
    for entry in tree:
        path = entry["path"]
        if entry["type"] == "tree" and "/" not in path:
            dirs.append(path)
        elif entry["type"] == "blob" and "/" in path:
            by_dir[path.split("/", 1)[0]].append(entry)

    parts = []
    for dirname in dirs:
        ids, name = parse_adafruit_dir(dirname)
        images, steps = [], []
        for entry in by_dir.get(dirname, []):
            kind = classify_image_or_step(entry["path"])
            if not kind:
                continue
            item = {
                "name": entry["path"].rsplit("/", 1)[-1],
                "url": raw_url(ADA["repo"], ADA["branch"], entry["path"]),
                "size": int(entry.get("size") or 0),
            }
            if kind == "image":
                images.append((image_score(entry["path"], dirname, item["size"]), item))
            else:
                steps.append(item)
        images.sort(key=lambda x: (-x[0][0], -x[0][1], x[0][2]))
        images = [it for _, it in images]
        steps.sort(key=lambda s: s["name"].lower())
        files = iter_download_files(by_dir.get(dirname, []), ADA["repo"], ADA["branch"])
        number_labels(files)
        rec = {
            "maker": "adafruit",
            "dir": dirname,
            "ids": ids,
            "name": name,
            "images": images,
            "steps": steps,
            "files": files,
            "github": github_tree_url(ADA["repo"], ADA["branch"], dirname),
            "productUrl": (ADA["product_url"].format(id=ids[0]) if ids else None),
        }
        parts.append(rec)
    return parts


def sparkfun_name_from_files(pid: str, blobs: list[dict]) -> str | None:
    stems = []
    for entry in blobs:
        fn = entry["path"].rsplit("/", 1)[-1]
        if fn.lower() in ("eagleup.json", "readme.md"):
            continue
        if "." not in fn:
            continue
        stem = fn.rsplit(".", 1)[0]
        low = stem.lower()
        if low in {pid, "model", "boardpost"}:
            continue
        low = re.sub(r"_+(top|bottom)_board$", "", low)
        stem = re.sub(r"_+(top|bottom)_board$", "", stem, flags=re.I)
        if low.endswith("_assy"):
            stem = stem[:-5]
        if not stem or stem.lower() in {pid, "model"}:
            continue
        # skip 'Serial_20...' duplicate if a cleaner one exists; still usable
        stems.append(stem)
    # unique, prefer longer / more word-like
    seen = set()
    uniq = []
    for s in stems:
        k = re.sub(r"[^a-z0-9]+", "", s.lower())
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)

    def score(s: str) -> tuple:
        letters = sum(ch.isalpha() for ch in s)
        return (letters, len(s), -s.lower().count("wig"))

    if not uniq:
        return None
    best = max(uniq, key=score)
    if sum(ch.isalpha() for ch in best) < 3:
        return None
    return pretty_stem(best)


def sparkfun_name_from_eagle(repo_url: str | None) -> str | None:
    if not repo_url:
        return None
    m = re.search(r"github\.com/sparkfun/([^/]+)", repo_url, re.I)
    if not m:
        # sometimes raw.githubusercontent.com/sparkfun/Name/
        m = re.search(r"github(?:usercontent)?\.com/sparkfun/([^/]+)", repo_url, re.I)
    if not m:
        return None
    return pretty_stem(m.group(1))


def build_sparkfun_parts(tree: list[dict], names: dict, eagle: dict) -> list[dict]:
    products: dict[str, list[dict]] = defaultdict(list)
    for entry in tree:
        path = entry["path"]
        if entry["type"] == "blob" and path.startswith("products/") and path.count("/") >= 2:
            products[path.split("/")[1]].append(entry)

    parts = []
    for pid, blobs in products.items():
        official = names.get(pid) or {}
        name = official.get("name")
        if name:
            name = pretty_stem(name.replace(" ", "_")) if False else " ".join(pretty_token(t) for t in name.split())
        if not name:
            name = sparkfun_name_from_files(pid, blobs)
        if not name:
            name = sparkfun_name_from_eagle((eagle.get(pid) or {}).get("repository"))
        if not name:
            name = f"SparkFun {pid}"
        name = name.replace("Sparkfun", "SparkFun").replace("sparkfun", "SparkFun")

        images, steps = [], []
        for entry in blobs:
            kind = classify_image_or_step(entry["path"])
            if not kind:
                continue
            # ignore blender copies of board renders when scoring, handled in score
            item = {
                "name": entry["path"].rsplit("/", 1)[-1],
                "url": raw_url(SF["repo"], SF["branch"], entry["path"]),
                "size": int(entry.get("size") or 0),
            }
            if kind == "image":
                images.append((image_score(entry["path"], pid, item["size"], prefer_display=True), item))
            else:
                steps.append(item)
        images.sort(key=lambda x: (-x[0][0], -x[0][1], x[0][2]))
        images = [it for _, it in images]
        steps.sort(key=lambda s: s["name"].lower())
        files = iter_download_files(blobs, SF["repo"], SF["branch"])
        number_labels(files)
        ids = [int(pid)] if pid.isdigit() else []
        product_url = official.get("url") if official.get("status") in (200, 301, 302) and official.get("url") else None
        rec = {
            "maker": "sparkfun",
            "dir": f"products/{pid}",
            "ids": ids,
            "name": name,
            "images": images,
            "steps": steps,
            "files": files,
            "github": github_tree_url(SF["repo"], SF["branch"], f"products/{pid}"),
            "productUrl": product_url,
        }
        parts.append(rec)
    return parts


def attach_generated_thumbs(parts: list[dict]) -> int:
    attached = 0
    for part in parts:
        if part.get("images"):
            continue
        # Adafruit key is dir; SparkFun key is sparkfun/{id}
        if part["maker"] == "sparkfun":
            pid = part["dir"].rsplit("/", 1)[-1]
            key = f"sparkfun/{pid}"
        else:
            key = part["dir"]
        path = THUMBS_DIR / f"{slug_dir(key)}.webp"
        if not path.is_file() or path.stat().st_size == 0:
            continue
        raw = path.read_bytes()
        part["images"] = [{
            "name": "STEPから生成",
            "url": "data:image/webp;base64," + base64.b64encode(raw).decode("ascii"),
            "size": len(raw),
            "generated": True,
            "file": path.name,
        }]
        attached += 1
    return attached


def use_external_thumbs(parts: list[dict]) -> None:
    for part in parts:
        for im in part.get("images") or []:
            if im.get("generated") and im["url"].startswith("data:"):
                im["url"] = "thumbs/" + im["file"]


def html_page(parts: list[dict], generated_jst: str, embed: bool) -> str:
    ada_dt = datetime.fromisoformat(ADA["commit_utc"].replace("Z", "+00:00")).astimezone(JST).strftime("%Y-%m-%d %H:%M")
    sf_dt = datetime.fromisoformat(SF["commit_utc"].replace("Z", "+00:00")).astimezone(JST).strftime("%Y-%m-%d %H:%M")
    data = {
        "pageSize": 40,
        "sources": {
            "adafruit": {"repo": ADA["repo"], "branch": ADA["branch"], "commit": ADA["commit"], "date": ada_dt},
            "sparkfun": {"repo": SF["repo"], "branch": SF["branch"], "commit": SF["commit"], "date": sf_dt},
        },
        "parts": parts,
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")

    n = len(parts)
    n_ada = sum(1 for p in parts if p["maker"] == "adafruit")
    n_sf = sum(1 for p in parts if p["maker"] == "sparkfun")
    n_gen = sum(1 for p in parts if p["images"] and p["images"][0].get("generated"))
    n_repo_img = sum(1 for p in parts if p["images"] and not p["images"][0].get("generated"))
    n_step = sum(1 for p in parts if p["steps"])
    thumb_note = (
        "生成したサムネイルはページ内に埋め込んでいます。"
        if embed else
        "生成したサムネイルは thumbs/ フォルダを同じ場所に置いてご覧ください。"
    )

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>電子部品 CADモデルカタログ（Adafruit / SparkFun）</title>
<meta name="description" content="Adafruit と SparkFun の公式CADリポジトリから、画像・商品名・各種ファイルのダウンロードを一覧する非公式カタログです。">
<style>
:root {{
  --bg: #f3efe8;
  --bg-accent: #efe6d8;
  --card: #fffcf7;
  --card-hover: #ffffff;
  --text: #1c1916;
  --muted: #5f574e;
  --faint: #8a8176;
  --line: #e4dbd0;
  --accent: #c81e1e;
  --accent-soft: #f8d4d0;
  --accent-ink: #8e1010;
  --ok: #1f6b4a;
  --ok-soft: #d8f0e4;
  --warn-bg: #f4efe4;
  --shadow: 0 1px 2px rgba(40, 24, 10, .05), 0 10px 24px rgba(40, 24, 10, .06);
  --header: rgba(243, 239, 232, .88);
  --thumb-bg: #ece4d8;
  --focus: 0 0 0 3px rgba(200, 30, 30, .35);
  color-scheme: light;
}}
html[data-theme="dark"] {{
  --bg: #161311;
  --bg-accent: #1e1a16;
  --card: #221e1a;
  --card-hover: #2a2520;
  --text: #f4efe8;
  --muted: #b7aea3;
  --faint: #8e857a;
  --line: #3a342e;
  --accent: #ff6b5c;
  --accent-soft: #4a221d;
  --accent-ink: #ffd2cc;
  --ok: #6ee7b0;
  --ok-soft: #173528;
  --warn-bg: #2a2418;
  --shadow: 0 1px 2px rgba(0,0,0,.35), 0 12px 28px rgba(0,0,0,.28);
  --header: rgba(22, 19, 17, .86);
  --thumb-bg: #2a251f;
  color-scheme: dark;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
  font-family: "Noto Sans CJK JP", "Noto Sans JP", "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic", YuGothic, system-ui, sans-serif;
  background: radial-gradient(1200px 500px at 10% -10%, var(--bg-accent), transparent 60%), var(--bg);
  color: var(--text);
  line-height: 1.5;
  min-height: 100vh;
}}
a {{ color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }}
a:hover {{ text-decoration: underline; }}
button, input {{ font: inherit; }}
.wrap {{ width: min(1280px, calc(100% - 32px)); margin: 0 auto; }}
header.top {{
  position: sticky; top: 0; z-index: 20;
  backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
  background: var(--header); border-bottom: 1px solid var(--line);
}}
.top-inner {{
  display: flex; flex-wrap: wrap; gap: 14px 20px;
  align-items: flex-end; justify-content: space-between;
  padding: 14px 0 12px;
}}
.brand h1 {{ font-size: 1.18rem; letter-spacing: .01em; margin: 0 0 4px; font-weight: 700; }}
.brand p {{ margin: 0; color: var(--muted); font-size: .82rem; }}
.brand .src a {{ color: inherit; }}
.controls {{
  display: flex; flex-wrap: wrap; gap: 8px;
  align-items: center; justify-content: flex-end; flex: 1 1 380px;
}}
.search {{ position: relative; flex: 1 1 200px; min-width: 180px; max-width: 360px; }}
.search input {{
  width: 100%; border: 1px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 999px; padding: 8px 14px 8px 36px; outline: none;
}}
.search input:focus {{ box-shadow: var(--focus); border-color: var(--accent); }}
.search svg {{
  position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
  width: 16px; height: 16px; color: var(--faint); pointer-events: none;
}}
.seg {{
  display: inline-flex; border: 1px solid var(--line); border-radius: 999px;
  overflow: hidden; background: var(--card);
}}
.seg button {{
  border: 0; background: transparent; color: var(--muted);
  padding: 7px 12px; cursor: pointer; white-space: nowrap;
}}
.seg button[aria-pressed="true"] {{
  background: var(--accent-soft); color: var(--accent-ink); font-weight: 700;
}}
.toggle {{
  display: flex; align-items: center; gap: 8px;
  background: var(--card); border: 1px solid var(--line); border-radius: 999px;
  padding: 7px 12px; cursor: pointer; user-select: none; white-space: nowrap;
}}
.toggle input {{ accent-color: var(--accent); width: 15px; height: 15px; }}
.icon-btn {{
  border: 1px solid var(--line); background: var(--card); color: var(--text);
  border-radius: 999px; width: 38px; height: 38px; display: grid; place-items: center; cursor: pointer;
}}
.icon-btn:focus-visible, .toggle:focus-within, .dl:focus-visible, .seg button:focus-visible, .pages button:focus-visible {{
  box-shadow: var(--focus); outline: none;
}}
.toolbar {{
  display: flex; flex-wrap: wrap; gap: 10px 16px;
  align-items: center; justify-content: space-between;
  padding: 12px 0 8px; color: var(--muted); font-size: .88rem;
}}
.toolbar strong {{ color: var(--text); font-weight: 700; }}
.pages {{ display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }}
.pages button {{
  min-width: 32px; height: 32px; padding: 0 8px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--card); color: var(--text); cursor: pointer;
}}
.pages button[aria-current="page"] {{
  background: var(--accent-soft); color: var(--accent-ink); border-color: transparent; font-weight: 700;
}}
.pages button:disabled {{ opacity: .4; cursor: default; }}
.pages .ellipsis {{ padding: 0 4px; color: var(--faint); }}
.grid {{
  display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 16px; padding: 6px 0 16px;
}}
.card {{
  background: var(--card); border: 1px solid var(--line); border-radius: 16px;
  box-shadow: var(--shadow); display: flex; flex-direction: column; overflow: hidden; min-width: 0;
}}
.card:hover {{ background: var(--card-hover); }}
.thumb {{
  position: relative; height: 200px;
  background: var(--thumb-bg); overflow: hidden;
}}
.thumb-link {{
  display: grid; place-items: center; width: 100%; height: 100%;
  color: inherit; text-decoration: none;
}}
.thumb-link:hover {{ filter: brightness(.97); }}
.thumb-link:focus-visible {{ box-shadow: var(--focus); outline: none; }}
.thumb img {{
  max-width: 100%; max-height: 100%; width: auto; height: auto;
  object-fit: contain; display: block;
}}
.placeholder {{
  color: var(--faint); font-size: .85rem; display: grid; place-items: center;
  gap: 6px; text-align: center; padding: 16px;
}}
.placeholder svg {{ width: 36px; height: 36px; opacity: .7; }}
.img-pager {{
  position: absolute; right: 8px; bottom: 8px; display: flex; gap: 4px; align-items: center; z-index: 2;
}}
.img-pager button {{
  width: 28px; height: 28px; border: 0; border-radius: 999px;
  background: rgba(20,16,12,.62); color: #fff; cursor: pointer;
  display: grid; place-items: center; padding: 0;
}}
html[data-theme="dark"] .img-pager button {{ background: rgba(0,0,0,.55); }}
.img-count {{
  position: absolute; left: 8px; bottom: 8px; font-size: .72rem;
  background: rgba(20,16,12,.62); color: #fff; border-radius: 999px;
  padding: 2px 8px; z-index: 2; pointer-events: none;
}}
.gen-badge {{
  position: absolute; left: 8px; top: 8px; z-index: 2;
  font-size: .68rem; font-weight: 600; letter-spacing: .02em;
  background: rgba(20,16,12,.58); color: #fff; border-radius: 999px;
  padding: 2px 8px; pointer-events: none;
}}
.body {{ padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 6px; flex: 1; }}
.maker {{
  font-size: .68rem; color: var(--faint); letter-spacing: .06em;
  font-weight: 700; text-transform: uppercase;
}}
.name {{ font-weight: 700; font-size: .98rem; line-height: 1.35; margin: 0; }}
.name a {{ color: inherit; text-decoration: none; }}
.name a:hover {{ text-decoration: underline; text-underline-offset: 2px; }}
.name a:focus-visible {{ box-shadow: var(--focus); outline: none; border-radius: 4px; }}
.files {{ display: flex; flex-wrap: wrap; gap: 5px; margin-top: auto; padding-top: 4px; }}
.dl, .none {{
  display: inline-flex; align-items: center; gap: 5px;
  border-radius: 7px; padding: 3px 8px; font-size: .72rem;
  border: 0; cursor: pointer; text-align: left;
}}
.dl {{ background: var(--ok-soft); color: var(--ok); font-weight: 700; letter-spacing: .02em; }}
.dl:hover {{ filter: brightness(1.04); }}
.dl .meta {{ font-weight: 500; opacity: .8; font-size: .65rem; letter-spacing: 0; white-space: nowrap; }}
.none {{
  color: var(--faint); cursor: default; background: var(--warn-bg);
  width: 100%; justify-content: center;
}}
.empty {{ text-align: center; color: var(--muted); padding: 64px 16px; grid-column: 1 / -1; }}
.bottom-nav {{ padding: 8px 0 28px; }}
footer {{ border-top: 1px solid var(--line); color: var(--muted); font-size: .8rem; padding: 20px 0 48px; }}
footer p {{ margin: 0 0 8px; }}
@media (max-width: 600px) {{
  .wrap {{ width: calc(100% - 20px); }}
  .thumb {{ height: 180px; }}
  .brand h1 {{ font-size: 1.05rem; }}
}}
</style>
</head>
<body>
  <header class="top">
    <div class="wrap top-inner">
      <div class="brand">
        <h1>電子部品 CADモデルカタログ</h1>
        <p class="src">Adafruit / SparkFun 公式リポジトリの非公式一覧</p>
      </div>
      <div class="controls">
        <div class="seg" role="group" aria-label="メーカー">
          <button type="button" data-maker="all" aria-pressed="true">すべて</button>
          <button type="button" data-maker="adafruit" aria-pressed="false">Adafruit</button>
          <button type="button" data-maker="sparkfun" aria-pressed="false">SparkFun</button>
        </div>
        <label class="search">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <circle cx="11" cy="11" r="7"></circle>
            <path d="M20 20l-3.5-3.5"></path>
          </svg>
          <input type="search" id="q" placeholder="商品名または製品IDで検索" autocomplete="off" spellcheck="false" aria-label="商品名または製品IDで検索">
        </label>
        <label class="toggle">
          <input type="checkbox" id="stepOnly">
          <span>STEPがある部品のみ</span>
        </label>
        <button type="button" class="icon-btn" id="themeBtn" aria-label="配色を切り替える" title="配色を切り替える">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path d="M21 14.5A8.5 8.5 0 1 1 9.5 3 7 7 0 0 0 21 14.5z"></path>
          </svg>
        </button>
      </div>
    </div>
  </header>

  <main class="wrap">
    <div class="toolbar">
      <div id="stats"></div>
      <nav class="pages" id="pagesTop" aria-label="ページ送り"></nav>
    </div>
    <section class="grid" id="grid" aria-live="polite"></section>
    <div class="toolbar bottom-nav">
      <div id="statsBottom"></div>
      <nav class="pages" id="pagesBottom" aria-label="ページ送り"></nav>
    </div>
  </main>

  <footer class="wrap">
    <p>このページは Adafruit と SparkFun の公式CADリポジトリをまとめた非公式カタログです。リポジトリ付属の画像とCADファイルは GitHub から直接配信されます（Git LFS は使われていません）。{thumb_note}</p>
    <p>Adafruit: <a href="https://github.com/{ADA['repo']}" target="_blank" rel="noopener">{ADA['repo']}</a>（{ADA['branch']} / {ada_dt} Asia/Tokyo / <code>{ADA['commit'][:10]}</code>）。SparkFun: <a href="https://github.com/{SF['repo']}" target="_blank" rel="noopener">{SF['repo']}</a>（{SF['branch']} / {sf_dt} Asia/Tokyo / <code>{SF['commit'][:10]}</code>）。ページ生成: {generated_jst}（Asia/Tokyo）。全{n}件（Adafruit {n_ada} / SparkFun {n_sf}）。リポジトリの画像 {n_repo_img}件、STEPから生成したサムネイル {n_gen}件、STEPあり {n_step}件。</p>
    <p>販売終了などで SparkFun 製品ページが見つからない部品は、フォルダ内のファイル名などから商品名を補っています。</p>
  </footer>

  <script type="application/json" id="catalog-data">{payload}</script>
  <script>
  (function () {{
    const data = JSON.parse(document.getElementById("catalog-data").textContent);
    const PAGE = data.pageSize || 40;
    const grid = document.getElementById("grid");
    const stats = document.getElementById("stats");
    const statsBottom = document.getElementById("statsBottom");
    const pagesTop = document.getElementById("pagesTop");
    const pagesBottom = document.getElementById("pagesBottom");
    const q = document.getElementById("q");
    const stepOnly = document.getElementById("stepOnly");
    const themeBtn = document.getElementById("themeBtn");
    const seg = document.querySelector(".seg");

    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const savedTheme = localStorage.getItem("cad-catalog-theme");
    setTheme(savedTheme || (prefersDark ? "dark" : "light"));
    themeBtn.addEventListener("click", function () {{
      setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark", true);
    }});
    function setTheme(theme, persist) {{
      document.documentElement.setAttribute("data-theme", theme);
      if (persist) localStorage.setItem("cad-catalog-theme", theme);
    }}

    const params = new URLSearchParams(location.search);
    let maker = params.get("maker") || "all";
    if (maker !== "adafruit" && maker !== "sparkfun") maker = "all";
    let page = Math.max(1, parseInt(params.get("page") || "1", 10) || 1);

    function fmtSize(n) {{
      if (!n && n !== 0) return "";
      if (n < 1024) return n + " B";
      if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10 * 1024 ? 1 : 0) + " KB";
      return (n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0) + " MB";
    }}
    function norm(s) {{ return (s || "").toLowerCase().replace(/[‐-―]/g, "-"); }}
    function escapeHtml(s) {{
      return String(s).replace(/[&<>"']/g, function (c) {{
        return ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }})[c];
      }});
    }}
    function makerLabel(m) {{ return m === "sparkfun" ? "SparkFun" : "Adafruit"; }}

    function matches(part, query) {{
      if (!query) return true;
      const hay = norm([
        part.dir, part.name, makerLabel(part.maker),
        (part.ids || []).join(" "),
        (part.files || []).map(function (f) {{ return f.name + " " + f.label; }}).join(" ")
      ].join(" "));
      return query.split(/\\s+/).filter(Boolean).every(function (w) {{ return hay.indexOf(w) !== -1; }});
    }}

    function filtered() {{
      const query = norm(q.value);
      const only = stepOnly.checked;
      return data.parts.filter(function (p) {{
        if (maker !== "all" && p.maker !== maker) return false;
        if (only && !(p.steps && p.steps.length)) return false;
        return matches(p, query);
      }});
    }}

    function writeUrl() {{
      const u = new URL(location.href);
      if (maker === "all") u.searchParams.delete("maker"); else u.searchParams.set("maker", maker);
      if (page <= 1) u.searchParams.delete("page"); else u.searchParams.set("page", String(page));
      history.replaceState(null, "", u);
    }}

    function setMaker(next) {{
      maker = next;
      page = 1;
      Array.prototype.forEach.call(seg.querySelectorAll("button"), function (b) {{
        b.setAttribute("aria-pressed", b.getAttribute("data-maker") === maker ? "true" : "false");
      }});
      render();
    }}

    const phSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"></rect><circle cx="8.5" cy="10" r="1.5"></circle><path d="M21 16l-5.5-5.5L7 19"></path></svg>';

    function pageItems(total) {{
      const last = Math.max(1, Math.ceil(total / PAGE));
      const items = [];
      const add = function (p) {{ if (items.indexOf(p) === -1) items.push(p); }};
      add(1);
      if (last <= 9) {{
        for (let i = 2; i <= last; i++) add(i);
      }} else {{
        const start = Math.max(2, page - 2);
        const end = Math.min(last - 1, page + 2);
        if (start > 2) items.push("…");
        for (let i = start; i <= end; i++) add(i);
        if (end < last - 1) items.push("…");
        add(last);
      }}
      return {{ last: last, items: items }};
    }}

    function renderPager(el, total) {{
      el.innerHTML = "";
      if (total === 0) return;
      const info = pageItems(total);
      const prev = document.createElement("button");
      prev.type = "button";
      prev.textContent = "前へ";
      prev.disabled = page <= 1;
      prev.addEventListener("click", function () {{ goto(page - 1); }});
      el.appendChild(prev);
      info.items.forEach(function (it) {{
        if (it === "…") {{
          const s = document.createElement("span");
          s.className = "ellipsis";
          s.textContent = "…";
          el.appendChild(s);
          return;
        }}
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = String(it);
        if (it === page) b.setAttribute("aria-current", "page");
        b.addEventListener("click", function () {{ goto(it); }});
        el.appendChild(b);
      }});
      const next = document.createElement("button");
      next.type = "button";
      next.textContent = "次へ";
      next.disabled = page >= info.last;
      next.addEventListener("click", function () {{ goto(page + 1); }});
      el.appendChild(next);
    }}

    function goto(p) {{
      page = p;
      render();
      window.scrollTo({{ top: 0, behavior: "smooth" }});
    }}

    function statsText(shown, start, end) {{
      if (!shown.length) return "該当する部品がありません";
      return "表示 <strong>" + start.toLocaleString("ja-JP") + "–" + end.toLocaleString("ja-JP") + "</strong> / " +
        shown.length.toLocaleString("ja-JP") + " 件";
    }}

    function render() {{
      const shown = filtered();
      const last = Math.max(1, Math.ceil(shown.length / PAGE) || 1);
      if (page > last) page = 1;
      const startIdx = (page - 1) * PAGE;
      const slice = shown.slice(startIdx, startIdx + PAGE);
      const start = shown.length ? startIdx + 1 : 0;
      const end = startIdx + slice.length;
      stats.innerHTML = statsText(shown, start, end);
      statsBottom.innerHTML = statsText(shown, start, end);
      renderPager(pagesTop, shown.length);
      renderPager(pagesBottom, shown.length);
      writeUrl();
      Array.prototype.forEach.call(seg.querySelectorAll("button"), function (b) {{
        b.setAttribute("aria-pressed", b.getAttribute("data-maker") === maker ? "true" : "false");
      }});

      if (!slice.length) {{
        grid.innerHTML = '<div class="empty">該当する部品がありません。検索条件を変えてみてください。</div>';
        return;
      }}
      const frag = document.createDocumentFragment();
      slice.forEach(function (part) {{ frag.appendChild(renderCard(part)); }});
      grid.innerHTML = "";
      grid.appendChild(frag);
    }}

    function renderCard(part) {{
      const card = document.createElement("article");
      card.className = "card";

      const thumb = document.createElement("div");
      thumb.className = "thumb";
      const thumbLink = document.createElement("a");
      thumbLink.className = "thumb-link";
      thumbLink.href = part.github;
      thumbLink.target = "_blank";
      thumbLink.rel = "noopener";
      thumbLink.title = "GitHubフォルダを開く";
      thumbLink.setAttribute("aria-label", part.name + " のGitHubフォルダを開く");
      const images = part.images || [];
      if (!images.length) {{
        thumbLink.innerHTML = '<div class="placeholder">' + phSvg + "<div>画像なし</div></div>";
      }} else {{
        const img = document.createElement("img");
        img.loading = "lazy";
        img.decoding = "async";
        img.alt = part.name;
        let idx = 0;
        function show(i) {{
          idx = (i + images.length) % images.length;
          img.src = images[idx].url;
          img.alt = part.name + (images[idx].generated ? "（STEPから生成）" : "（" + images[idx].name + "）");
        }}
        img.addEventListener("error", function () {{
          if (img.dataset.failed) return;
          img.dataset.failed = "1";
          thumbLink.innerHTML = '<div class="placeholder">' + phSvg + "<div>画像を読み込めません</div></div>";
        }});
        show(0);
        thumbLink.appendChild(img);
        if (images[0] && images[0].generated) {{
          const badge = document.createElement("div");
          badge.className = "gen-badge";
          badge.textContent = "STEPから生成";
          thumb.appendChild(badge);
        }}
        if (images.length > 1) {{
          const count = document.createElement("div");
          count.className = "img-count";
          count.textContent = images.length + "枚";
          thumb.appendChild(count);
          const pager = document.createElement("div");
          pager.className = "img-pager";
          const prev = document.createElement("button");
          prev.type = "button";
          prev.setAttribute("aria-label", "前の画像");
          prev.textContent = "‹";
          const next = document.createElement("button");
          next.type = "button";
          next.setAttribute("aria-label", "次の画像");
          next.textContent = "›";
          prev.addEventListener("click", function (e) {{ e.preventDefault(); e.stopPropagation(); show(idx - 1); }});
          next.addEventListener("click", function (e) {{ e.preventDefault(); e.stopPropagation(); show(idx + 1); }});
          pager.appendChild(prev);
          pager.appendChild(next);
          thumb.appendChild(pager);
        }}
      }}
      thumb.appendChild(thumbLink);
      card.appendChild(thumb);

      const body = document.createElement("div");
      body.className = "body";
      const mk = document.createElement("div");
      mk.className = "maker";
      mk.textContent = makerLabel(part.maker);
      body.appendChild(mk);

      const h = document.createElement("h2");
      h.className = "name";
      const nameLink = document.createElement("a");
      nameLink.href = part.github;
      nameLink.target = "_blank";
      nameLink.rel = "noopener";
      nameLink.textContent = part.name;
      const idPart = (part.ids && part.ids.length)
        ? makerLabel(part.maker) + " #" + part.ids.join(", #") + " — "
        : makerLabel(part.maker) + " — ";
      nameLink.title = idPart + part.name;
      h.appendChild(nameLink);
      body.appendChild(h);

      const files = document.createElement("div");
      files.className = "files";
      const list = part.files || [];
      if (!list.length) {{
        const none = document.createElement("div");
        none.className = "none";
        none.textContent = "ファイルなし";
        files.appendChild(none);
      }} else {{
        list.forEach(function (st) {{
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "dl";
          const label = st.button || st.label;
          btn.title = st.name + "（" + fmtSize(st.size) + "）";
          btn.setAttribute("aria-label", st.name + " をダウンロード");
          btn.innerHTML = '<span class="label"></span><span class="meta"></span>';
          btn.querySelector(".label").textContent = label;
          btn.querySelector(".meta").textContent = fmtSize(st.size);
          btn.addEventListener("click", function () {{ downloadFile(st, btn); }});
          files.appendChild(btn);
        }});
      }}
      body.appendChild(files);
      card.appendChild(body);
      return card;
    }}

    async function downloadFile(st, btn) {{
      const lab = btn.querySelector(".label");
      const meta = btn.querySelector(".meta");
      const origLabel = lab.textContent;
      const origMeta = meta.textContent;
      btn.disabled = true;
      lab.textContent = "…";
      try {{
        const res = await fetch(st.url);
        if (!res.ok) throw new Error("HTTP " + res.status);
        const total = Number(res.headers.get("content-length")) || st.size || 0;
        let blob;
        if (res.body && total > 2 * 1024 * 1024) {{
          const reader = res.body.getReader();
          const chunks = [];
          let rec = 0;
          while (true) {{
            const {{ done, value }} = await reader.read();
            if (done) break;
            chunks.push(value);
            rec += value.length;
            if (total) meta.textContent = Math.min(99, Math.round(rec / total * 100)) + "%";
          }}
          blob = new Blob(chunks, {{ type: "application/octet-stream" }});
        }} else {{
          blob = await res.blob();
        }}
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = st.name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () {{ URL.revokeObjectURL(url); }}, 2000);
      }} catch (err) {{
        window.open(st.url, "_blank", "noopener");
      }} finally {{
        btn.disabled = false;
        lab.textContent = origLabel;
        meta.textContent = origMeta;
      }}
    }}

    function schedule() {{
      page = 1;
      render();
    }}
    q.addEventListener("input", function () {{
      clearTimeout(q._t);
      q._t = setTimeout(schedule, 60);
    }});
    q.addEventListener("search", schedule);
    stepOnly.addEventListener("change", schedule);
    seg.addEventListener("click", function (e) {{
      const b = e.target.closest("button[data-maker]");
      if (b) setMaker(b.getAttribute("data-maker"));
    }});
    render();
  }})();
  </script>
</body>
</html>
"""


def sort_parts(parts: list[dict]) -> list[dict]:
    def key(p):
        pid = p["ids"][0] if p["ids"] else 10**9
        return (pid, 0 if p["maker"] == "adafruit" else 1, p["name"].lower())
    parts.sort(key=key)
    return parts


def write_zip() -> None:
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(OUT_PATH, "index.html")
        if THUMBS_DIR.is_dir():
            for p in sorted(THUMBS_DIR.glob("*.webp")):
                z.write(p, f"thumbs/{p.name}")


def main() -> None:
    ada_tree = json.loads(ADA_TREE.read_text())["tree"]
    sf_tree = json.loads(SF_TREE.read_text())["tree"]
    names = {r["id"]: r for r in json.loads(SF_NAMES.read_text())} if SF_NAMES.exists() else {}
    eagle = json.loads(SF_EAGLE.read_text()) if SF_EAGLE.exists() else {}

    parts = build_adafruit_parts(ada_tree) + build_sparkfun_parts(sf_tree, names, eagle)
    sort_parts(parts)
    n_attached = attach_generated_thumbs(parts)
    print(f"attached generated thumbs: {n_attached}")

    generated_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    html = html_page(parts, generated_jst, embed=True)
    size = len(html.encode("utf-8"))
    embed = size <= MAX_HTML_BYTES
    if not embed:
        print(f"HTML with data URIs is {size} bytes, switching to external thumbs/")
        use_external_thumbs(parts)
        html = html_page(parts, generated_jst, embed=False)
        size = len(html.encode("utf-8"))
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"wrote {OUT_PATH} ({size} bytes) embed={embed}")

    if not embed:
        write_zip()
        print(f"wrote {ZIP_PATH} ({ZIP_PATH.stat().st_size} bytes)")

    def report(maker=None):
        subset = [p for p in parts if maker is None or p["maker"] == maker]
        n = len(subset)
        repo_img = sum(1 for p in subset if p["images"] and not p["images"][0].get("generated"))
        gen = sum(1 for p in subset if p["images"] and p["images"][0].get("generated"))
        none = sum(1 for p in subset if not p["images"])
        step = sum(1 for p in subset if p["steps"])
        nofile = sum(1 for p in subset if not p["files"])
        print(f"  {maker or 'ALL'}: parts={n} repo_img={repo_img} generated={gen} no_image={none} with_step={step} no_files={nofile}")

    print("COUNTS:")
    report()
    report("adafruit")
    report("sparkfun")
    print("NO IMAGE remaining:")
    for p in parts:
        if not p["images"]:
            print(f"  [{p['maker']}] {p['dir']} | {p['name']} | files={len(p['files'])} steps={len(p['steps'])}")


if __name__ == "__main__":
    main()
