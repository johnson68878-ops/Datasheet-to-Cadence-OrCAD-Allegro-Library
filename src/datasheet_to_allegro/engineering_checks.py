"""Evidence-based checks: absence is unresolved, never a passing result."""
import math


def rotated_bounds(width, height, angle, *, circle=False):
    if circle:
        return width, height
    radians = math.radians(angle)
    c, s = abs(math.cos(radians)), abs(math.sin(radians))
    return c * width + s * height, s * width + c * height


def audit_ep_polygons(copper_points, window_points, *, coverage, min_gap, rotation=0, origin=(0, 0)):
    """Use actual polygon area/distance, never bounding-box area; normalize pose."""
    from shapely.geometry import Polygon
    from shapely.affinity import rotate, translate
    if not copper_points or not window_points:
        return {"status": "unresolved", "errors": ["missing native polygons"]}
    if not 0 < coverage <= 1 or min_gap < 0 or not all(math.isfinite(v) for v in (coverage, min_gap, rotation, *origin)):
        raise ValueError("Invalid EP review constraints")
    def local(points):
        polygon = Polygon(points)
        if not polygon.is_valid or polygon.area <= 0:
            raise ValueError("Invalid or zero-area polygon")
        return rotate(translate(polygon, -origin[0], -origin[1]), -rotation, origin=(0, 0))
    copper = local(copper_points)
    windows = [local(points) for points in window_points]
    errors = []
    for i, window in enumerate(windows):
        if not copper.buffer(1e-6).covers(window):
            errors.append(f"window {i} outside EP copper")
        for j, other in enumerate(windows[:i]):
            if window.intersection(other).area > 1e-8:
                errors.append(f"windows {j}/{i} overlap")
            if window.distance(other) + 1e-6 < min_gap:
                errors.append(f"windows {j}/{i} gap below {min_gap}")
    from shapely.ops import unary_union
    area = unary_union(windows).area
    ratio = area / copper.area
    if abs(ratio - coverage) > 0.005:
        errors.append("paste coverage mismatch")
    return {"status": "fail" if errors else "pass", "errors": errors,
            "copper_area_mm2": copper.area, "paste_area_mm2": area, "coverage": ratio,
            "minimum_gap_mm": min((a.distance(b) for i, a in enumerate(windows) for b in windows[:i]), default=None),
            "window_count": len(windows)}


def audit_net_semantics(pins, nets):
    """Check explicit pin types and connectivity; never infer missing nets/types."""
    errors, unresolved = [], []
    if not pins:
        unresolved.append("missing physical pin inventory")
    if not nets:
        unresolved.append("missing native connectivity inventory")
    identities = [(p["refdes"], p["number"]) for p in pins]
    if len(set(identities)) != len(identities):
        errors.append("duplicate physical pin identity")
    index = dict(zip(identities, pins))
    seen = {}
    for net in nets:
        members = []
        for raw in net["pins"]:
            key = tuple(raw)
            if key not in index:
                errors.append(f"unknown pin {key}")
                continue
            if key in seen and seen[key] != net["name"]:
                errors.append(f"pin {key} assigned to multiple nets")
            seen[key] = net["name"]
            members.append(index[key])
        drivers = [p for p in members if p.get("type") == "Output"]
        if len(drivers) > 1:
            errors.append(f"{net['name']}: multiple push-pull outputs")
        if any(p.get("type") == "Input" for p in members) and not any(p.get("type") in {"Output", "Bidirectional", "3State", "OpenCollector", "Power"} for p in members):
            unresolved.append(f"{net['name']}: input has no evidenced driver")
    for key, pin in index.items():
        evidence_type = pin.get("datasheet_type")
        if evidence_type and evidence_type != pin.get("type"):
            errors.append(f"{key}: native type differs from datasheet type")
        if not pin.get("type_evidence"):
            unresolved.append(f"{key}: missing datasheet electrical-type evidence")
        if key not in seen and not pin.get("explicit_nc"):
            unresolved.append(f"{key}: no net or explicit NC evidence")
    return {"status": "fail" if errors else "unresolved" if unresolved else "pass", "errors": errors, "unresolved": unresolved}


def assign_ep_windows(coppers, windows, *, tolerance=1e-6):
    """Assign each whole native window to exactly one EP, never by bbox/centroid.

    coppers is keyed by physical pad identity. A straddling or unowned window
    blocks acceptance instead of silently disappearing from area coverage.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    shapes, assigned, errors = {}, {}, []
    for key, loops in coppers.items():
        if not loops:
            errors.append(f"{key}: missing copper polygons")
            continue
        parts = [Polygon(loop) for loop in loops]
        if any(not p.is_valid or p.area <= 0 for p in parts):
            errors.append(f"{key}: invalid copper polygon")
            continue
        shapes[key] = unary_union(parts)
        assigned[key] = []
    for i, points in enumerate(windows):
        window = Polygon(points)
        if not window.is_valid or window.area <= 0:
            errors.append(f"window {i}: invalid polygon")
            continue
        owners = [key for key, shape in shapes.items() if shape.buffer(tolerance).covers(window)]
        if len(owners) != 1:
            errors.append(f"window {i}: expected one EP owner, found {len(owners)}")
        else:
            assigned[owners[0]].append(points)
    return {"status": "unresolved" if errors or not shapes else "pass", "errors": errors, "windows": assigned}
