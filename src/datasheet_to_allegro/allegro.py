from __future__ import annotations

import json
import hashlib
import math
import re
from pathlib import Path

from .geometry import FootprintGeometry, build_geometry
from .models import FootprintSpec
from .naming import cadence_ascii_text


REQUIRED_PROFILE_KEYS = {"allegro_version", "solder_mask_expansion_mm", "courtyard_clearance_mm"}


def _write_ascii_skill(output: Path, lines: list[str]) -> None:
    """Write old-Cadence-compatible SKILL after sanitizing every external string."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(cadence_ascii_text(line) for line in lines) + "\n", encoding="ascii")


def _symbol_name(spec: FootprintSpec) -> str:
    raw = f"{spec.package_type}_{spec.pin_count}_{spec.part_number}"
    return re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").lower()[:58]


def symbol_name(spec: FootprintSpec) -> str:
    """Return the stable Allegro package-symbol name used by the generator."""
    return _symbol_name(spec)


def _skill_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _micrometre_token(value_mm: float) -> str:
    return str(int(round(value_mm * 1000)))


def _padstack_name(pad, geometry: FootprintGeometry, *, exposed: bool = False) -> str:
    """Name a padstack by physical apertures so Layout can identify it.

    Names remain deterministic and below the conservative 31-character limit.
    Identical geometry intentionally receives the same name independent of
    manufacturer, part number, source filename or source hash.
    """
    width = _micrometre_token(pad.width_mm)
    length = _micrometre_token(pad.length_mm)
    mask_width = _micrometre_token(pad.width_mm + 2 * geometry.solder_mask_expansion_mm)
    mask_length = _micrometre_token(pad.length_mm + 2 * geometry.solder_mask_expansion_mm)
    paste_width = _micrometre_token(geometry.paste_width_mm or pad.width_mm * geometry.paste_scale)
    paste_length = _micrometre_token(geometry.paste_length_mm or pad.length_mm * geometry.paste_scale)
    prefix = "ep" if exposed else ("c" if pad.shape == "CIRCLE" else "r")
    if pad.shape == "CIRCLE" and pad.width_mm == pad.length_mm:
        name = f"{prefix}{width}_s{mask_width}_p{paste_width}"
    else:
        name = f"{prefix}{width}x{length}_s{mask_width}x{mask_length}_p{paste_width}x{paste_length}"
    if pad.drill_mm:
        name += f"_d{_micrometre_token(pad.drill_mm)}"
    # The readable micrometre prefix is lossy (rounding and truncation). Bind
    # the name to aperture geometry, never document identity or pin position.
    signature = {
        "shape": pad.shape, "width": pad.width_mm, "length": pad.length_mm,
        "drill": pad.drill_mm, "mask_expansion": geometry.solder_mask_expansion_mm,
        "paste_scale": geometry.paste_scale,
        "paste_width": geometry.paste_width_mm,
        "paste_length": geometry.paste_length_mm,
        "paste_shape": geometry.paste_shape,
        "paste_corner_radius": geometry.paste_corner_radius_mm,
        "exposed": exposed,
    }
    digest = hashlib.sha256(
        json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()[:10]
    readable = ""
    for component in name.split("_"):
        candidate = f"{readable}_{component}" if readable else component
        if len(candidate) > 20:
            break  # Never truncate a number into a misleading smaller value.
        readable = candidate
    return f"{readable or prefix}_{digest}"


def _measurement_nominal(item) -> float | None:
    if item is None:
        return None
    if item.nominal_mm is not None:
        return item.nominal_mm
    if item.value_mm is not None:
        return item.value_mm
    if item.min_mm is not None and item.max_mm is not None:
        return (item.min_mm + item.max_mm) / 2
    return item.min_mm or item.max_mm


def _measurement_max(item) -> float | None:
    if item is None:
        return None
    return next((value for value in (
        item.max_mm, item.nominal_mm, item.value_mm, item.min_mm
    ) if value is not None), None)


def _write_review_display_skill(directory: Path) -> Path:
    target = directory / "review_display_colors.il"
    target.write_text(
        "; Community review display - Allegro 17.4\n"
        "procedure(ds2aApplyReviewDisplay()\n"
        "  let((layer)\n"
        "    axlColorSet(25 '(35 190 255))\n"
        "    axlColorSet(26 '(255 190 45))\n"
        "    axlColorSet(27 '(125 145 165))\n"
        "    axlColorSet(28 '(55 135 255))\n"
        "    axlColorSet(29 '(235 240 245))\n"
        "    axlColorSet(30 '(255 255 255))\n"
        "    layer = axlLayerGet(\"PIN/TOP\")\n"
        "    when(layer layer->color = 28 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"PACKAGE GEOMETRY/ASSEMBLY_TOP\")\n"
        "    when(layer layer->color = 25 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"PACKAGE GEOMETRY/BODY_CENTER\")\n"
        "    when(layer layer->color = 25 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"PACKAGE GEOMETRY/SILKSCREEN_TOP\")\n"
        "    when(layer layer->color = 26 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"REF DES/SILKSCREEN_TOP\")\n"
        "    when(layer layer->color = 29 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"COMPONENT VALUE/SILKSCREEN_TOP\")\n"
        "    when(layer layer->color = 29 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"REF DES/ASSEMBLY_TOP\")\n"
        "    when(layer layer->color = 29 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"COMPONENT VALUE/ASSEMBLY_TOP\")\n"
        "    when(layer layer->color = 29 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"PACKAGE GEOMETRY/PIN_NUMBER\")\n"
        "    when(layer layer->color = 29 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"PACKAGE GEOMETRY/DIMENSION\")\n"
        "    when(layer layer->color = 30 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"SOLDERMASK_TOP\")\n"
        "    when(layer layer->color = 25 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"PASTEMASK_TOP\")\n"
        "    when(layer layer->color = 26 layer->pattern = 0 axlLayerSet(layer))\n"
        "    layer = axlLayerGet(\"PACKAGE GEOMETRY/PLACE_BOUND_TOP\")\n"
        "    when(layer layer->color = 27 layer->pattern = 0 axlLayerSet(layer))\n"
        "    axlVisibleLayer(\"PACKAGE GEOMETRY/PLACE_BOUND_TOP\" nil)\n"
        "    axlVisibleUpdate(t)\n"
        "  )\n"
        ")\n"
        "ds2aApplyReviewDisplay()\n",
        encoding="utf-8",
    )
    return target


def _generate_native_array_skill(
    spec: FootprintSpec,
    geometry: FootprintGeometry,
    output: Path,
    *,
    approved_by: str | None,
) -> Path:
    """Generate non-drilled lands directly in Package Symbol Editor.

    Also used by reviewed perimeter/discrete sources, avoiding the legacy
    layout-host symbol-skeleton/write-package path.
    """
    symbol = _symbol_name(spec)
    procedure = f"ds2aGenerate_{symbol}"
    peripheral = min(geometry.pads, key=lambda pad: min(pad.width_mm, pad.length_mm))
    pad_name = _padstack_name(peripheral, geometry)
    pad_w = peripheral.width_mm
    pad_l = peripheral.length_mm
    mask_w = pad_w + geometry.solder_mask_expansion_mm * 2
    mask_l = pad_l + geometry.solder_mask_expansion_mm * 2
    paste_w = pad_w * geometry.paste_scale
    paste_l = pad_l * geometry.paste_scale
    if geometry.paste_width_mm and geometry.paste_length_mm:
        paste_w, paste_l = geometry.paste_width_mm, geometry.paste_length_mm
    shape = "CIRCLE" if peripheral.shape == "CIRCLE" else "RECTANGLE"
    paste_shape = "ROUNDED_RECTANGLE" if geometry.paste_shape == "rounded_rectangle" else ("SQUARE" if geometry.paste_shape == "square" and abs(paste_w - paste_l) < 1e-6 else shape)
    paste_options = ""
    if paste_shape == "ROUNDED_RECTANGLE":
        paste_options = f' ?radius {_skill_number(geometry.paste_corner_radius_mm or 0.0)} ?corners "UR-UL-LR-LL"'
    pitch_x = _measurement_nominal(spec.pitch_x) or _measurement_nominal(spec.pitch) or pad_w
    pitch_y = _measurement_nominal(spec.pitch_y) or _measurement_nominal(spec.pitch) or pad_l
    pitch = min(pitch_x, pitch_y)
    text_height = min(pad_w * 0.42, pitch * 0.24)
    text_width = text_height * 0.48
    text_stroke = max(0.006, min(0.012, text_height * 0.12))
    # Allegro centers native pin text horizontally; its Y coordinate is a
    # baseline, so half-height compensation places the glyph inside the land.
    text_y_offset = -text_height / 2
    marker = geometry.pin1_marker
    copper_x = max(abs(pad.x_mm) + (pad.length_mm if int(pad.rotation_deg) % 180 == 90 else pad.width_mm) / 2 for pad in geometry.pads)
    copper_y = max(abs(pad.y_mm) + (pad.width_mm if int(pad.rotation_deg) % 180 == 90 else pad.length_mm) / 2 for pad in geometry.pads)
    x_centers = [pad.x_mm for pad in geometry.pads if pad.role == "peripheral"]
    y_centers = [pad.y_mm for pad in geometry.pads if pad.role == "peripheral"]
    grid_left, grid_right = min(x_centers), max(x_centers)
    grid_bottom, grid_top = min(y_centers), max(y_centers)
    manufacturer_body = geometry.assembly_outline_kind == "manufacturer_body"
    body_x = geometry.body_width_mm / 2 if manufacturer_body else copper_x
    body_y = geometry.body_length_mm / 2 if manufacturer_body else copper_y
    silk_x = body_x + 0.10
    silk_y = body_y + 0.10
    if spec.package_type.upper() not in {"BGA", "WLCSP", "LGA"}:
        silk_x = max(silk_x, copper_x + 0.10)
        silk_y = max(silk_y, copper_y + 0.10)
    axis_offset = max(0.18, min(0.28, pitch * 0.55))
    axis_height = max(0.12, min(0.18, pitch * 0.40))
    axis_width = axis_height * 0.55
    height_max = _measurement_max(spec.package_height)
    approval = "t" if approved_by else "nil"
    lines = [
        "; Community AI Layout - Allegro 17.4 native array package generator",
        f"; source={cadence_ascii_text(spec.source_file)}",
        f"; part={cadence_ascii_text(spec.part_number)} family={spec.package_type} pins={spec.pin_count}",
        "; Must run in a new Package Symbol Editor drawing (-symbol package).",
        f"ds2aEngineerApproved = {approval}",
        "",
        f"procedure({procedure}()",
        "  prog((padList padName padStackDbid padStacks pinTextOrient pinText pinObj textParam refTextOrient axisTextOrient placeBound placeBoundObj)",
        "    unless(ds2aEngineerApproved",
        '      error("EE/Layout approval required before native package generation\\n")',
        "    )",
        '    axlDBChangeDesignUnits("MILLIMETERS" 4)',
        f'    padName = "{pad_name}"',
        "    padList = nil",
        f"    padList = cons(make_axlPadStackPad(?layer \"TOP\" ?type 'REGULAR ?figure '{shape} ?figureSize {_skill_number(pad_w)}:{_skill_number(pad_l)}) padList)",
        f"    padList = cons(make_axlPadStackPad(?layer \"SOLDERMASK_TOP\" ?type 'REGULAR ?figure '{shape} ?figureSize {_skill_number(mask_w)}:{_skill_number(mask_l)}) padList)",
        f"    padList = cons(make_axlPadStackPad(?layer \"PASTEMASK_TOP\" ?type 'REGULAR ?figure '{paste_shape} ?figureSize {_skill_number(paste_w)}:{_skill_number(paste_l)}{paste_options}) padList)",
        "    padStackDbid = axlDBCreatePadStack(padName nil padList t)",
        '    unless(padStackDbid error("Array padstack could not be created\\n"))',
        "    axlPadstackToDisk(padName)",
        '    textParam = axlGetParam("paramTextBlock:14")',
        '    unless(textParam error("Text block 14 is unavailable for native ball numbers\\n"))',
        f"    textParam->width = {_skill_number(text_width)}",
        f"    textParam->height = {_skill_number(text_height)}",
        "    textParam->lineSpace = 0.01",
        "    textParam->charSpace = 0.003",
        f"    textParam->photoWidth = {_skill_number(text_stroke)}",
        '    unless(axlSetParam(textParam) error("Native ball-number text parameters could not be applied\\n"))',
        '    pinTextOrient = make_axlTextOrientation(?textBlock 14 ?rotation 0.0 ?mirrored nil ?justify "center")',
        '    textParam = axlGetParam("paramTextBlock:15")',
        '    unless(textParam error("Text block 15 is unavailable for REF DES\\n"))',
        "    textParam->width = 0.18",
        "    textParam->height = 0.22",
        "    textParam->lineSpace = 0.08",
        "    textParam->charSpace = 0.02",
        "    textParam->photoWidth = 0.03",
        '    unless(axlSetParam(textParam) error("REF DES text parameters could not be applied\\n"))',
        '    refTextOrient = make_axlTextOrientation(?textBlock 15 ?rotation 0.0 ?mirrored nil ?justify "center")',
        '    textParam = axlGetParam("paramTextBlock:16")',
        '    unless(textParam error("Text block 16 is unavailable for array axis labels\\n"))',
        f'    textParam->width = {_skill_number(axis_width)}',
        f'    textParam->height = {_skill_number(axis_height)}',
        '    textParam->lineSpace = 0.025',
        '    textParam->charSpace = 0.006',
        '    textParam->photoWidth = 0.02',
        '    unless(axlSetParam(textParam) error("Array axis-label text parameters could not be applied\\n"))',
        '    axisTextOrient = make_axlTextOrientation(?textBlock 16 ?rotation 0.0 ?mirrored nil ?justify "center")',
        f"    unless(axlDBChangeDesignExtents(list(list({_skill_number(-max(silk_x + 2.0, 7.0))} {_skill_number(-silk_y - 2.0)}) list({_skill_number(max(silk_x + 2.0, 7.0))} {_skill_number(silk_y + 2.0)}))) error(\"Package drawing extents could not be expanded\\n\"))",
    ]
    # One physical padstack per geometry, not one per package. The initial
    # stack above remains the uniform-array path; additional sizes use exactly
    # the same database operations and independently scaled mask apertures.
    stack_key = lambda pad: (pad.width_mm, pad.length_mm, pad.shape)
    stack_names = {stack_key(peripheral): pad_name}
    lines.append('    padStacks = list(padStackDbid)')
    for pad in geometry.pads:
        key = stack_key(pad)
        if key in stack_names:
            continue
        name = _padstack_name(pad, geometry)
        if name in stack_names.values():
            name = f"{name[:28]}_{len(stack_names)}"
        stack_names[key] = name
        width, length, figure = key
        pw, pl = width * geometry.paste_scale, length * geometry.paste_scale
        if geometry.paste_width_mm and geometry.paste_length_mm:
            pw, pl = geometry.paste_width_mm, geometry.paste_length_mm
        pf = "ROUNDED_RECTANGLE" if geometry.paste_shape == "rounded_rectangle" else ("SQUARE" if geometry.paste_shape == "square" and abs(pw-pl) < 1e-6 else figure)
        lines += [
            f'    padName = "{name}"',
            '    padList = nil',
            f"    padList = cons(make_axlPadStackPad(?layer \"TOP\" ?type 'REGULAR ?figure '{figure} ?figureSize {_skill_number(width)}:{_skill_number(length)}) padList)",
            f"    padList = cons(make_axlPadStackPad(?layer \"SOLDERMASK_TOP\" ?type 'REGULAR ?figure '{figure} ?figureSize {_skill_number(width+2*geometry.solder_mask_expansion_mm)}:{_skill_number(length+2*geometry.solder_mask_expansion_mm)}) padList)",
            f"    padList = cons(make_axlPadStackPad(?layer \"PASTEMASK_TOP\" ?type 'REGULAR ?figure '{pf} ?figureSize {_skill_number(pw)}:{_skill_number(pl)}{paste_options if pf == 'ROUNDED_RECTANGLE' else ''}) padList)",
            '    padStackDbid = axlDBCreatePadStack(padName nil padList t)',
            '    unless(padStackDbid error("Padstack could not be created\\n"))',
            '    axlPadstackToDisk(padName)',
            '    padStacks = append(padStacks list(padStackDbid))',
        ]
    for pad in geometry.pads:
        if pad.role != "peripheral":
            continue
        stack_ref = 'padStackDbid' if len(stack_names) == 1 else f'nth({list(stack_names).index(stack_key(pad))} padStacks)'
        lines += [
            f'    pinText = make_axlPinText(?number "{pad.number}" ?offset 0.0:{_skill_number(text_y_offset)} ?text pinTextOrient)',
            f"    pinObj = axlDBCreatePin({stack_ref} {_skill_number(pad.x_mm)}:{_skill_number(pad.y_mm)} pinText {pad.rotation_deg:.1f})",
            f'    unless(pinObj error("Native pin {pad.number} could not be created\\n"))',
        ]
    # Only a manufacturer numeric D/E may create an Assembly outline. A ball-
    # land envelope is useful for calculations but is not a component body and
    # must never be drawn as a closed outline that resembles the PDF D/E box.
    if geometry.assembly_outline_kind == "manufacturer_body":
        lines.append(f"    axlDBCreateLine(list({_skill_number(-body_x)}:{_skill_number(-body_y)} {_skill_number(body_x)}:{_skill_number(-body_y)} {_skill_number(body_x)}:{_skill_number(body_y)} {_skill_number(-body_x)}:{_skill_number(body_y)} {_skill_number(-body_x)}:{_skill_number(-body_y)}) 0.02 \"PACKAGE GEOMETRY/ASSEMBLY_TOP\" nil nil)")
    else:
        lines.append("    ; D/E=UNRESOLVED - NO PACKAGE BODY OUTLINE DRAWN; array labels and Pin 1 marker remain for review.")
        lines.append("    ; D/E unresolved: no package-body outline is drawn.")
    lines += [
        f"    axlDBCreateLine(list({_skill_number(-silk_x)}:{_skill_number(-silk_y)} {_skill_number(silk_x)}:{_skill_number(-silk_y)} {_skill_number(silk_x)}:{_skill_number(silk_y)} {_skill_number(-silk_x)}:{_skill_number(silk_y)} {_skill_number(-silk_x)}:{_skill_number(-silk_y)}) 0.02 \"PACKAGE GEOMETRY/SILKSCREEN_TOP\" nil nil)",
        f"    axlDBCreateCircle(list({_skill_number(marker.x_mm)}:{_skill_number(marker.y_mm)} {_skill_number(marker.radius_mm)}) {_skill_number(marker.line_width_mm)} \"PACKAGE GEOMETRY/SILKSCREEN_TOP\" nil)",
        f"    axlDBCreateCircle(list({_skill_number(marker.x_mm)}:{_skill_number(marker.y_mm)} {_skill_number(marker.radius_mm)}) {_skill_number(marker.line_width_mm)} \"PACKAGE GEOMETRY/ASSEMBLY_TOP\" nil)",
        '    axlDBCreateLine(list(-0.5:0.0 0.5:0.0) 0.02 "PACKAGE GEOMETRY/BODY_CENTER" nil nil)',
        '    axlDBCreateLine(list(0.0:-0.5 0.0:0.5) 0.02 "PACKAGE GEOMETRY/BODY_CENTER" nil nil)',
        f"    placeBound = axlPathStart(list({_skill_number(-body_x)}:{_skill_number(-body_y)} {_skill_number(body_x)}:{_skill_number(-body_y)} {_skill_number(body_x)}:{_skill_number(body_y)} {_skill_number(-body_x)}:{_skill_number(body_y)} {_skill_number(-body_x)}:{_skill_number(-body_y)}))",
        '    placeBoundObj = car(axlDBCreateShape(placeBound t "PACKAGE GEOMETRY/PLACE_BOUND_TOP" nil nil))',
        '    unless(placeBoundObj error("Place bound could not be created\\n"))',
    ]
    if height_max is not None:
        lines.append(f'    axlDBAddProp(list(placeBoundObj) list("PACKAGE_HEIGHT_MAX" "{_skill_number(height_max)} MM"))')
    for column in range(1, (spec.column_count or 0) + 1):
        x = (column - 1 - ((spec.column_count or 1) - 1) / 2) * pitch_x
        for layer_name in ("PACKAGE GEOMETRY/SILKSCREEN_TOP", "PACKAGE GEOMETRY/ASSEMBLY_TOP"):
            lines.append(f'    axlDBCreateText("{column}" {_skill_number(x)}:{_skill_number(body_y + axis_offset)} axisTextOrient "{layer_name}" nil)')
    for index, row in enumerate(spec.row_names):
        y = ((len(spec.row_names) - 1) / 2 - index) * pitch_y
        for layer_name in ("PACKAGE GEOMETRY/SILKSCREEN_TOP", "PACKAGE GEOMETRY/ASSEMBLY_TOP"):
            lines.append(f'    axlDBCreateText("{row}" {_skill_number(-body_x - axis_offset)}:{_skill_number(y)} axisTextOrient "{layer_name}" nil)')
    lines += [
        f'    axlDBCreateText("REF*" 0.0:{_skill_number(-silk_y - 0.55)} refTextOrient "REF DES/SILKSCREEN_TOP" nil)',
        f'    axlDBCreateText("VALUE*" 0.0:{_skill_number(-silk_y - 0.85)} refTextOrient "COMPONENT VALUE/SILKSCREEN_TOP" nil)',
        '    axlDBCreateText("REF*" 0.0:-0.18 refTextOrient "REF DES/ASSEMBLY_TOP" nil)',
        '    axlDBCreateText("VALUE*" 0.0:0.18 refTextOrient "COMPONENT VALUE/ASSEMBLY_TOP" nil)',
    ]
    lines += [
        '    let((layer)',
        '      layer = axlLayerGet("PIN/TOP")',
        '      when(layer layer->color = 28 layer->pattern = 0 axlLayerSet(layer))',
        '      layer = axlLayerGet("PACKAGE GEOMETRY/PIN_NUMBER")',
        '      when(layer layer->color = 30 layer->pattern = 0 axlLayerSet(layer))',
        '      layer = axlLayerGet("PACKAGE GEOMETRY/ASSEMBLY_TOP")',
        '      when(layer layer->color = 25 layer->pattern = 0 axlLayerSet(layer))',
        '      layer = axlLayerGet("PACKAGE GEOMETRY/BODY_CENTER")',
        '      when(layer layer->color = 25 layer->pattern = 0 axlLayerSet(layer))',
        '      layer = axlLayerGet("PACKAGE GEOMETRY/SILKSCREEN_TOP")',
        '      when(layer layer->color = 26 layer->pattern = 0 axlLayerSet(layer))',
        '      layer = axlLayerGet("REF DES/SILKSCREEN_TOP")',
        '      when(layer layer->color = 29 layer->pattern = 0 axlLayerSet(layer))',
        '      layer = axlLayerGet("COMPONENT VALUE/SILKSCREEN_TOP")',
        '      when(layer layer->color = 29 layer->pattern = 0 axlLayerSet(layer))',
        '      layer = axlLayerGet("REF DES/ASSEMBLY_TOP")',
        '      when(layer layer->color = 29 layer->pattern = 0 axlLayerSet(layer))',
        '      layer = axlLayerGet("COMPONENT VALUE/ASSEMBLY_TOP")',
        '      when(layer layer->color = 29 layer->pattern = 0 axlLayerSet(layer))',
        '      layer = axlLayerGet("SOLDERMASK_TOP")',
        '      when(layer layer->color = 25 layer->pattern = 0 axlLayerSet(layer))',
        '      layer = axlLayerGet("PASTEMASK_TOP")',
        '      when(layer layer->color = 26 layer->pattern = 0 axlLayerSet(layer))',
        '    )',
    ]
    lines += [
        "    axlSaveDesign(?mode \"nocheck\" ?noConfirm t)",
        f'    unless(axlCompileSymbol(?symbol "{symbol}" ?type "package") error("Package symbol compilation failed\\n"))',
        f'    printf("Generated native array package: {symbol}\\n")',
        "    t",
        "  )",
        ")",
        "",
    ]
    if approved_by:
        lines.append(f"{procedure}()")
    _write_ascii_skill(output, lines)
    output.with_suffix(".launch.json").write_text(
        json.dumps({"mode": "symbol_package", "dra_name": f"{symbol}.dra"}, indent=2),
        encoding="utf-8",
    )
    _write_review_display_skill(output.parent)
    return output


def generate_geometry_skill(
    spec: FootprintSpec,
    geometry: FootprintGeometry,
    output: Path,
    *,
    approved_by: str | None = None,
    gate_mode: str = "release",
) -> Path:
    """Write an Allegro 17.2/17.4 SKILL package generator using Cadence dbcreate APIs."""
    from .generation_gate import generation_blockers, review_generation_blockers
    if gate_mode not in {"release", "review"}:
        raise ValueError(f"Unknown generation gate mode: {gate_mode}")
    blockers = generation_blockers(spec) if gate_mode == "release" else review_generation_blockers(spec)
    if blockers:
        raise ValueError("Generation blocked: " + "; ".join(item.code for item in blockers))
    from .geometry import build_geometry
    if geometry != build_geometry(spec):
        raise ValueError("Geometry differs from source specification")
    from .pin_reconciliation import require_reconciled_geometry
    require_reconciled_geometry(spec, geometry)
    if gate_mode == 'review':
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix('.review.json').write_text(json.dumps({
            'mode': 'engineering_draft', 'publish_allowed': False,
            'native_verified': False,
            'note': 'Generation plan only; CAD execution and engineering approval are separate.',
            'source_spec_sha256': hashlib.sha256(spec.model_dump_json().encode('utf-8')).hexdigest(),
            'omitted_fields': ['package_height'] if spec.package_height is None else [],
            'release_blockers': [item.model_dump() for item in generation_blockers(spec)],
            'manufacturing_rules': {name: getattr(spec, name).model_dump(mode='json') for name in
                ('recommended_land_pattern','solder_mask_expansion','paste_mask_rule','courtyard')},
            'engineering_warning': 'UNAPPROVED ENGINEERING DRAFT: verify copper, mask, paste and courtyard before use. Not for production.',
        }, ensure_ascii=False, indent=2), encoding='utf-8')
    peripheral_keys = {(p.width_mm, p.length_mm, p.shape, p.drill_mm) for p in geometry.pads if p.role == "peripheral"}
    peripheral_smd = all(p.role == "peripheral" and p.drill_mm is None for p in geometry.pads)
    # Backend capability depends on actual geometry, not package family or
    # whether the land dimensions were supplied as a per-pad list.
    if peripheral_smd:
        return _generate_native_array_skill(spec, geometry, output, approved_by=approved_by)
    if len(peripheral_keys) != 1:
        raise ValueError("Mixed drilled or EP padstacks require a verified backend; no first-pad substitution")
    launch_metadata = output.with_suffix(".launch.json")
    symbol = _symbol_name(spec)
    procedure = f"ds2aGenerate_{symbol}"
    peripheral = next(pad for pad in geometry.pads if pad.role == "peripheral")
    exposed = next((pad for pad in geometry.pads if pad.role == "exposed_pad"), None)
    pad_name = _padstack_name(peripheral, geometry)
    ep_pad_name = _padstack_name(exposed, geometry, exposed=True) if exposed else "unused_ep"
    pad_w = peripheral.width_mm
    pad_l = peripheral.length_mm
    mask_w = pad_w + geometry.solder_mask_expansion_mm * 2
    mask_l = pad_l + geometry.solder_mask_expansion_mm * 2
    paste_w = pad_w * geometry.paste_scale
    paste_l = pad_l * geometry.paste_scale
    terminal_w = _measurement_nominal(spec.terminal_width_min) or pad_w
    terminal_l = _measurement_nominal(spec.terminal_length_min) or pad_l
    shape = "CIRCLE" if peripheral.shape == "CIRCLE" else "RECTANGLE"
    pin_label_height = max(0.055, min(min(pad_w, pad_l) * 0.48, 0.11))
    pin_label_width = pin_label_height * 0.36
    pin_label_stroke = max(0.0025, min(0.004, pin_label_height * 0.035))
    pin_label_y_offset = -pin_label_height / 2
    def half_extents(pad):
        if pad.role == "exposed_pad" or pad.shape == "CIRCLE":
            return pad.width_mm / 2, pad.length_mm / 2
        return (pad.length_mm / 2, pad.width_mm / 2) if int(pad.rotation_deg) % 180 == 90 else (pad.width_mm / 2, pad.length_mm / 2)
    marker = geometry.pin1_marker
    marker_outer_x = abs(marker.x_mm) + marker.radius_mm + marker.line_width_mm / 2 if marker else 0.0
    marker_outer_y = abs(marker.y_mm) + marker.radius_mm + marker.line_width_mm / 2 if marker else 0.0
    ext_x = max(max(abs(pad.x_mm) + half_extents(pad)[0] for pad in geometry.pads) + geometry.courtyard_clearance_mm, marker_outer_x)
    ext_y = max(max(abs(pad.y_mm) + half_extents(pad)[1] for pad in geometry.pads) + geometry.courtyard_clearance_mm, marker_outer_y)
    # A complete review silkscreen is derived from the body and actual copper
    # envelope.  It stays inside the courtyard while maintaining clearance
    # from every generated land, instead of relying on package-name constants.
    copper_x = max(abs(pad.x_mm) + half_extents(pad)[0] for pad in geometry.pads)
    copper_y = max(abs(pad.y_mm) + half_extents(pad)[1] for pad in geometry.pads)
    silk_x = min(ext_x - 0.04, max(geometry.body_width_mm / 2 + 0.12, copper_x + 0.08))
    silk_y = min(ext_y - 0.04, max(geometry.body_length_mm / 2 + 0.12, copper_y + 0.08))
    review_ext_x = ext_x + 1.8
    review_ext_y = ext_y + 2.0
    body_x = geometry.body_width_mm / 2
    body_y = geometry.body_length_mm / 2
    height_max = _measurement_max(spec.package_height)
    out_dir = output.parent.resolve().as_posix()
    approval = "t" if approved_by else "nil"
    lines = [
        "; Community AI Layout - Allegro 17.4 staging generator",
        f"; source={cadence_ascii_text(spec.source_file)}",
        f"; part={cadence_ascii_text(spec.part_number)} family={spec.package_type} pins={spec.pin_count}",
        f"; approved_by={approved_by or 'NOT_APPROVED'}",
        "; IMPORTANT: run in a metric MM package-symbol design and staging directory only.",
        f"ds2aEngineerApproved = {approval}",
        "",
        f"procedure({procedure}()",
        "  prog((padList epPadList drillData pinData symdef padName epPadName symbolName outline placeBound placeBoundObj pasteWindow nativePinParam pinLabelBlock pinLabelParam pinLabelOrient refTextBlock refTextParam refTextOrient pinObj)",
        "    unless(ds2aEngineerApproved",
        '      error("EE/Layout approval required: set ds2aEngineerApproved=t after reviewing footprint_spec.json and preview.svg\\n")',
        "    )",
        '    axlDBChangeDesignUnits("MILLIMETERS" 4)',
        f'    padName = "{pad_name}"',
        f'    epPadName = "{ep_pad_name}"',
        f'    symbolName = "{symbol}"',
        "    padList = nil",
    ]
    if geometry.pads[0].drill_mm:
        drill = _skill_number(geometry.pads[0].drill_mm or 0.0)
        lines += [
            f"    drillData = make_axlPadStackDrill(?holeType 'CIRCLE_DRILL ?drillDiameter {drill} ?plating 'PLATED)",
            f"    padList = cons(make_axlPadStackPad(?layer \"TOP\" ?type 'REGULAR ?figure '{shape} ?figureSize {_skill_number(pad_w)}:{_skill_number(pad_l)}) padList)",
            f"    padList = cons(make_axlPadStackPad(?layer \"DEFAULT_INTERNAL\" ?type 'REGULAR ?figure 'CIRCLE ?figureSize {_skill_number(pad_w)}:{_skill_number(pad_w)}) padList)",
            f"    padList = cons(make_axlPadStackPad(?layer \"BOTTOM\" ?type 'REGULAR ?figure '{shape} ?figureSize {_skill_number(pad_w)}:{_skill_number(pad_l)}) padList)",
        ]
    else:
        lines += [
            "    drillData = nil",
            f"    padList = cons(make_axlPadStackPad(?layer \"TOP\" ?type 'REGULAR ?figure '{shape} ?figureSize {_skill_number(pad_w)}:{_skill_number(pad_l)}) padList)",
            f"    padList = cons(make_axlPadStackPad(?layer \"SOLDERMASK_TOP\" ?type 'REGULAR ?figure '{shape} ?figureSize {_skill_number(mask_w)}:{_skill_number(mask_l)}) padList)",
            f"    padList = cons(make_axlPadStackPad(?layer \"PASTEMASK_TOP\" ?type 'REGULAR ?figure '{shape} ?figureSize {_skill_number(paste_w)}:{_skill_number(paste_l)}) padList)",
        ]
    lines += [
        "    axlDBCreatePadStack(padName drillData padList t)",
        "    axlPadstackToDisk(padName)",
    ]
    if exposed is not None:
        ep_mask_w = exposed.width_mm + geometry.solder_mask_expansion_mm * 2
        ep_mask_l = exposed.length_mm + geometry.solder_mask_expansion_mm * 2
        ep_figure = "SQUARE" if abs(exposed.width_mm - exposed.length_mm) <= 1e-9 else "RECTANGLE"
        ep_mask_figure = "SQUARE" if abs(ep_mask_w - ep_mask_l) <= 1e-9 else "RECTANGLE"
        lines += [
            "    epPadList = nil",
            f"    epPadList = cons(make_axlPadStackPad(?layer \"TOP\" ?type 'REGULAR ?figure '{ep_figure} ?figureSize {_skill_number(exposed.width_mm)}:{_skill_number(exposed.length_mm)}) epPadList)",
            f"    epPadList = cons(make_axlPadStackPad(?layer \"SOLDERMASK_TOP\" ?type 'REGULAR ?figure '{ep_mask_figure} ?figureSize {_skill_number(ep_mask_w)}:{_skill_number(ep_mask_l)}) epPadList)",
            "    axlDBCreatePadStack(epPadName nil epPadList t)",
            "    axlPadstackToDisk(epPadName)",
        ]
    lines.append("    pinData = nil")
    for pad in geometry.pads:
        rotation = pad.rotation_deg
        stack = "epPadName" if pad.role == "exposed_pad" else "padName"
        lines.append(
            f'    pinData = cons(make_axlPinData(?rotation {rotation:.1f} ?origin {_skill_number(pad.x_mm)}:{_skill_number(pad.y_mm)} ?padstack {stack} ?number "{pad.number}") pinData)'
        )
    lines += [
        # Skeleton pin numbers are database-native but their automatic offsets
        # depend on the editor template. Hide only that rendered copy and draw
        # one deterministic, centred review label per native pin number.
        "    nativePinParam = axlGetParam(\"paramTextBlock:1\")",
        "    when(nativePinParam",
        "      nativePinParam->width = 0.001",
        "      nativePinParam->height = 0.001",
        "      nativePinParam->lineSpace = 0.001",
        "      nativePinParam->charSpace = 0.001",
        "      nativePinParam->photoWidth = 0.001",
        "      axlSetParam(nativePinParam)",
        "    )",
        "    pinLabelBlock = \"14\"",
        "    pinLabelParam = axlGetParam(\"paramTextBlock:14\")",
        "    unless(pinLabelParam error(\"Text block 14 is unavailable for pin labels\\n\"))",
        f"    pinLabelParam->width = {_skill_number(pin_label_width)}",
        f"    pinLabelParam->height = {_skill_number(pin_label_height)}",
        "    pinLabelParam->lineSpace = 0.005",
        "    pinLabelParam->charSpace = 0.002",
        f"    pinLabelParam->photoWidth = {_skill_number(pin_label_stroke)}",
        "    unless(axlSetParam(pinLabelParam) error(\"Pin label text parameters could not be applied\\n\"))",
        "    pinLabelOrient = make_axlTextOrientation(?textBlock pinLabelBlock ?rotation 0.0 ?mirrored nil ?justify \"center\")",
        "    refTextBlock = \"15\"",
        "    refTextParam = axlGetParam(\"paramTextBlock:15\")",
        "    unless(refTextParam error(\"Text block 15 is unavailable for REF/VALUE text\\n\"))",
        "    refTextParam->width = 0.18",
        "    refTextParam->height = 0.22",
        "    refTextParam->lineSpace = 0.08",
        "    refTextParam->charSpace = 0.02",
        "    refTextParam->photoWidth = 0.03",
        "    unless(axlSetParam(refTextParam) error(\"REF/VALUE text parameters could not be applied\\n\"))",
        "    refTextOrient = make_axlTextOrientation(?textBlock refTextBlock ?rotation 0.0 ?mirrored nil ?justify \"center\")",
        "    unless(axlLayerGet(\"PACKAGE GEOMETRY/PIN_NUMBER\") error(\"PACKAGE GEOMETRY/PIN_NUMBER is unavailable\\n\"))",
    ]
    lines += [
        f"    symdef = _axlDBCreateSymDefSkeleton(list(symbolName \"PACKAGE\") list({_skill_number(-review_ext_x)}:{_skill_number(-review_ext_y)} {_skill_number(review_ext_x)}:{_skill_number(review_ext_y)}) pinData)",
        "    unless(symdef error(\"Package symbol definition could not be created\\n\"))",
        f"    axlDBCreateLine(list({_skill_number(-geometry.body_width_mm/2)}:{_skill_number(-geometry.body_length_mm/2)} {_skill_number(geometry.body_width_mm/2)}:{_skill_number(-geometry.body_length_mm/2)} {_skill_number(geometry.body_width_mm/2)}:{_skill_number(geometry.body_length_mm/2)} {_skill_number(-geometry.body_width_mm/2)}:{_skill_number(geometry.body_length_mm/2)} {_skill_number(-geometry.body_width_mm/2)}:{_skill_number(-geometry.body_length_mm/2)}) 0.03 \"PACKAGE GEOMETRY/ASSEMBLY_TOP\" nil symdef)",
    ]
    for pad in geometry.pads:
        label_y = pad.y_mm + pin_label_y_offset
        lines.append(
            f'    axlDBCreateText("{pad.number}" {_skill_number(pad.x_mm)}:{_skill_number(label_y)} pinLabelOrient "PACKAGE GEOMETRY/PIN_NUMBER" symdef)'
        )
    for pad in geometry.pads:
        if pad.role != "peripheral" or pad.shape == "CIRCLE":
            continue
        if int(pad.rotation_deg) % 180 == 90:
            cx = (1 if pad.x_mm >= 0 else -1) * (geometry.body_width_mm / 2 - terminal_l / 2)
            cy, w, h = pad.y_mm, terminal_l, terminal_w
        else:
            cx = pad.x_mm
            cy = (1 if pad.y_mm >= 0 else -1) * (geometry.body_length_mm / 2 - terminal_l / 2)
            w, h = terminal_w, terminal_l
        x1, x2, y1, y2 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
        lines.append(
            f"    axlDBCreateLine(list({_skill_number(x1)}:{_skill_number(y1)} {_skill_number(x2)}:{_skill_number(y1)} {_skill_number(x2)}:{_skill_number(y2)} {_skill_number(x1)}:{_skill_number(y2)} {_skill_number(x1)}:{_skill_number(y1)}) 0.015 \"PACKAGE GEOMETRY/ASSEMBLY_TOP\" nil symdef)"
        )
    if exposed is not None:
        lines.append(
            f"    axlDBCreateLine(list({_skill_number(-exposed.width_mm/2)}:{_skill_number(-exposed.length_mm/2)} {_skill_number(exposed.width_mm/2)}:{_skill_number(-exposed.length_mm/2)} {_skill_number(exposed.width_mm/2)}:{_skill_number(exposed.length_mm/2)} {_skill_number(-exposed.width_mm/2)}:{_skill_number(exposed.length_mm/2)} {_skill_number(-exposed.width_mm/2)}:{_skill_number(-exposed.length_mm/2)}) 0.025 \"PACKAGE GEOMETRY/ASSEMBLY_TOP\" nil symdef)"
        )
        rows = max(1, geometry.exposed_pad_paste_rows)
        columns = max(1, geometry.exposed_pad_paste_columns)
        gap = max(0.0, geometry.exposed_pad_paste_gap_mm)
        usable_w = max(exposed.width_mm * 0.2, exposed.width_mm - gap * (columns - 1))
        usable_l = max(exposed.length_mm * 0.2, exposed.length_mm - gap * (rows - 1))
        available_ratio = usable_w * usable_l / (exposed.width_mm * exposed.length_mm)
        aperture_scale = min(1.0, math.sqrt(geometry.exposed_pad_paste_area_coverage / available_ratio))
        window_w = usable_w / columns * aperture_scale
        window_l = usable_l / rows * aperture_scale
        for row in range(rows):
            for column in range(columns):
                cx = (column - (columns - 1) / 2) * (window_w + gap)
                cy = (row - (rows - 1) / 2) * (window_l + gap)
                x1, x2 = cx - window_w / 2, cx + window_w / 2
                y1, y2 = cy - window_l / 2, cy + window_l / 2
                lines += [
                    f"    pasteWindow = axlPathStart(list({_skill_number(x1)}:{_skill_number(y1)} {_skill_number(x2)}:{_skill_number(y1)} {_skill_number(x2)}:{_skill_number(y2)} {_skill_number(x1)}:{_skill_number(y2)} {_skill_number(x1)}:{_skill_number(y1)}))",
                    "    axlDBCreateShape(pasteWindow t \"PACKAGE GEOMETRY/PASTEMASK_TOP\" nil symdef)",
                ]
    lines += [
        f"    axlDBCreateCircle(list({_skill_number(marker.x_mm)}:{_skill_number(marker.y_mm)} {_skill_number(marker.radius_mm)}) {_skill_number(marker.line_width_mm)} \"PACKAGE GEOMETRY/SILKSCREEN_TOP\" symdef)",
        f'    axlDBCreateLine(list({_skill_number(-min(body_x / 2, 0.5))}:0.0 {_skill_number(min(body_x / 2, 0.5))}:0.0) 0.02 "PACKAGE GEOMETRY/BODY_CENTER" nil symdef)',
        f'    axlDBCreateLine(list(0.0:{_skill_number(-min(body_y / 2, 0.5))} 0.0:{_skill_number(min(body_y / 2, 0.5))}) 0.02 "PACKAGE GEOMETRY/BODY_CENTER" nil symdef)',
        f"    axlDBCreateCircle(list({_skill_number(marker.x_mm)}:{_skill_number(marker.y_mm)} {_skill_number(marker.radius_mm)}) {_skill_number(marker.line_width_mm)} \"PACKAGE GEOMETRY/ASSEMBLY_TOP\" symdef)",
        f"    axlDBCreateLine(list({_skill_number(-silk_x)}:{_skill_number(-silk_y)} {_skill_number(silk_x)}:{_skill_number(-silk_y)} {_skill_number(silk_x)}:{_skill_number(silk_y)} {_skill_number(-silk_x)}:{_skill_number(silk_y)} {_skill_number(-silk_x)}:{_skill_number(-silk_y)}) 0.05 \"PACKAGE GEOMETRY/SILKSCREEN_TOP\" nil symdef)",
        f"    placeBound = axlPathStart(list({_skill_number(-body_x)}:{_skill_number(-body_y)} {_skill_number(body_x)}:{_skill_number(-body_y)} {_skill_number(body_x)}:{_skill_number(body_y)} {_skill_number(-body_x)}:{_skill_number(body_y)} {_skill_number(-body_x)}:{_skill_number(-body_y)}))",
        "    placeBoundObj = car(axlDBCreateShape(placeBound t \"PACKAGE GEOMETRY/PLACE_BOUND_TOP\" nil symdef))",
        "    unless(placeBoundObj error(\"Place bound could not be created\\n\"))",
    ]
    if height_max is not None:
        lines.append(f'    axlDBAddProp(list(placeBoundObj) list("PACKAGE_HEIGHT_MAX" "{_skill_number(height_max)} MM"))')
    lines += [
        f"    axlDBCreateText(\"REF*\" 0.0:{_skill_number(-silk_y - 0.55)} refTextOrient \"REF DES/SILKSCREEN_TOP\" symdef)",
        f"    axlDBCreateText(\"VALUE*\" 0.0:{_skill_number(-silk_y - 0.85)} refTextOrient \"COMPONENT VALUE/SILKSCREEN_TOP\" symdef)",
        "    axlDBCreateText(\"REF*\" 0.0:-0.18 refTextOrient \"REF DES/ASSEMBLY_TOP\" symdef)",
        "    axlDBCreateText(\"VALUE*\" 0.0:0.18 refTextOrient \"COMPONENT VALUE/ASSEMBLY_TOP\" symdef)",
        f'    axlWritePackageFile(symdef "{out_dir}")',
        f'    printf("Generated staging package: {symbol} at {out_dir}\\n")',
        "    symdef",
        "  )",
        ")",
        "",
        f'; After approval: ds2aEngineerApproved=t  then run {procedure}()',
    ]
    if approved_by:
        lines.append(f"{procedure}()")
    _write_ascii_skill(output, lines)
    launch_metadata.write_text(
        # Peripheral generators use _axlDBCreateSymDefSkeleton, an API that is
        # valid in a layout drawing.  Run them in the isolated host BRD and let
        # axlWritePackageFile create the native DRA/PSM.  Array generators use
        # Package Symbol Editor directly and keep mode=symbol_package.
        json.dumps({"mode": "layout_host", "dra_name": f"{symbol}.dra"}, indent=2),
        encoding="utf-8",
    )
    _write_review_display_skill(output.parent)
    return output


def generate_skill(spec_path: Path, profile_path: Path, approved_by: str, output: Path) -> None:
    spec = FootprintSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    missing = sorted(key for key in REQUIRED_PROFILE_KEYS if profile.get(key) in (None, ""))
    if missing:
        raise ValueError(f"Company profile is incomplete: {', '.join(missing)}")
    target_version = str(profile["allegro_version"])
    from .cadence_compatibility import target_version_error
    if target_version_error(target_version):
        raise ValueError("Company profile must declare an exact Allegro version")
    blockers = [item for item in spec.unresolved_items if item.severity == "blocking"]
    if blockers:
        raise ValueError("Spec still has blocking unresolved items: " + ", ".join(item.code for item in blockers))
    from .models import RuleStatus
    for field, key, profile_key in (
        ("solder_mask_expansion", "expansion_mm", "solder_mask_expansion_mm"),
        ("courtyard", "clearance_mm", "courtyard_clearance_mm"),
    ):
        value = float(profile[profile_key])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid company profile value: {profile_key}")
        prior = getattr(spec, field)
        if prior.status == "manufacturer_recommended":
            declared = prior.value.get(key) if isinstance(prior.value, dict) else None
            if declared != value:
                raise ValueError(f"Company profile conflicts with manufacturer evidence: {field}")
        else:
            spec = spec.model_copy(update={field: RuleStatus(
                status="company_rule", value={key: value}, source_tier="company_rule",
                note=f"Explicit generation profile: {profile_path.name}")})
    geometry = build_geometry(spec)
    generate_geometry_skill(spec, geometry, output, approved_by=approved_by)
    metadata_path = output.with_suffix(".launch.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["target_version"] = target_version
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
