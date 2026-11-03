"""XRandR observation and affine touchscreen mapping; no mode/rate setters."""
import hashlib
import math
import re

IDENTITY = (1., 0., 0., 0., 1., 0., 0., 0., 1.)
ROTATION = {"normal": IDENTITY, "right": (0., 1., 0., -1., 0., 1., 0., 0., 1.),
            "left": (0., -1., 1., 1., 0., 0., 0., 0., 1.),
            "inverted": (-1., 0., 1., 0., -1., 1., 0., 0., 1.)}


def multiply(a, b):
    return tuple(sum(a[row*3+k]*b[k*3+col] for k in range(3)) for row in range(3) for col in range(3))


def affine(matrix):
    if len(matrix) != 9 or not all(math.isfinite(v) for v in matrix):
        raise ValueError("Invalid display transformation")
    if any(abs(matrix[i]-IDENTITY[i]) > 1e-6 for i in (6, 7, 8)):
        raise ValueError("Projective display transformations are unsupported")
    if abs(matrix[0]*matrix[4]-matrix[1]*matrix[3]) < 1e-9:
        raise ValueError("Singular display transformation")
    return tuple(matrix)


def inverse(matrix):
    a, b, c, d, e, f, _, _, _ = affine(matrix)
    det = a*e-b*d
    return (e/det, -b/det, (b*f-e*c)/det, -d/det, a/det, (d*c-a*f)/det, 0., 0., 1.)


def parse_verbose(text):
    displays = []
    sections = re.split(r"(?m)^(?=\S+ (?:connected|disconnected)\b)", text)
    for section in sections:
        lines = section.splitlines()
        if not lines or not re.match(r"^\S+ connected\b", lines[0]):
            continue
        header = lines[0]
        name = header.split()[0]
        geometry = re.search(r"\b(\d+)x(\d+)([+-]\d+)([+-]\d+)", header)
        tail = header[geometry.end():].split("(normal", 1)[0] if geometry else ""
        rotation = next((r for r in ROTATION if re.search(r"\b"+r+r"\b", tail)), "normal")
        transform = IDENTITY
        match = re.search(r"Transform:\s*([^\n]+)\n\s*([^\n]+)\n\s*([^\n]+)", section)
        if match:
            try:
                transform = tuple(float(v) for line in match.groups() for v in line.split())
            except ValueError:
                transform = ()
        panel = re.search(r"panel orientation:\s*([^\n]+)", section)
        current = re.search(r"(?m)^\s+([^\n]+\*current[^\n]*)\n[^\n]*\n[^\n]*clock\s+([\d.]+)Hz", section)
        if not current:
            current = re.search(r"(?m)^\s+(\d+x\d+)\s+([\d.]+)\*", section)
        native = re.search(r"(\d+)x(\d+)", current[1]) if current else None
        edid = re.search(r"EDID:\s*\n((?:\s+[0-9a-fA-F]{32}\n?)+)", section)
        identity = hashlib.sha256(re.sub(r"\s", "", edid[1]).encode()).hexdigest() if edid else "connector:"+name
        panning = re.search(r"Panning:\s*(\d+)x(\d+)", section, re.I)
        displays.append({"name": name, "identity": identity, "active": geometry is not None,
                         "internal": name.lower().startswith(("dsi", "edp", "lvds", "lcd")),
                         "rotation": rotation, "geometry": tuple(map(int, geometry.groups())) if geometry else None,
                         "transform": transform, "reflection": ("x axis" in tail, "y axis" in tail),
                         "panel_orientation": panel[1].strip() if panel else "Unknown",
                         "refresh_hz": float(current[2]) if current else None,
                         "mode_size": tuple(map(int, native.groups())) if native else None,
                         "panning": bool(panning and (int(panning[1]) or int(panning[2])))})
    return displays


def touch_mapping(display, desktop, calibration=IDENTITY):
    width, height, x, y = display["geometry"]
    if display.get('mode_size'):
        width, height = display['mode_size']
        if display['rotation'] in ('left','right'):
            width, height = height, width
    dw, dh = desktop
    if min(width, height, dw, dh) <= 0 or display.get("panning"):
        raise ValueError("Unsupported display geometry or panning; existing mapping preserved")
    transform = affine(display.get("transform", IDENTITY))
    rotation = ROTATION[display["rotation"]]
    rx, ry = display.get("reflection", (False, False))
    reflection = (-1. if rx else 1., 0., 1. if rx else 0.,
                  0., -1. if ry else 1., 1. if ry else 0., 0., 0., 1.)
    pixels = (width, 0., 0., 0., height, 0., 0., 0., 1.)
    viewport = (1/dw, 0., x/dw, 0., 1/dh, y/dh, 0., 0., 1.)
    return multiply(multiply(multiply(multiply(multiply(viewport, transform), pixels), reflection), rotation), affine(calibration))


def rotation_command(display, rotation):
    if rotation not in ROTATION or not display.get("internal") or not display.get("active"):
        raise ValueError("Rotation requires an active internal panel")
    affine(display.get("transform", IDENTITY))
    rate = display.get("refresh_hz")
    if not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
        raise ValueError("Cannot verify the current refresh rate; display unchanged")
    if display.get("panning"):
        raise ValueError("Display panning is unsupported; no display settings changed")
    return ["xrandr", "--output", display["name"], "--rotate", rotation]
