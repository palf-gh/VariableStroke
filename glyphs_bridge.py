"""Glyphs 3 adapters shared by the editing tool and the export filter."""
from GlyphsApp import GSPath, GSNode, LINE, CURVE, OFFCURVE
from variable_stroke_core import outline_curves, node_edges

PATH_KEY = 'com.codex.VariableStroke.enabled'
# Legacy absolute node width; converted to STROKE_WIDTH_KEY x SCALE_KEY on edit.
WIDTH_KEY = 'com.codex.VariableStroke.width'
STROKE_WIDTH_KEY = 'com.codex.VariableStroke.strokeWidth'  # per path, font units
SCALE_KEY = 'com.codex.VariableStroke.scale'  # per node, percent of the path width
STROKE_HEIGHT_KEY = 'com.codex.VariableStroke.strokeHeight'  # per path, font units
OFFSET_KEY = 'com.codex.VariableStroke.offset'  # per node, -100 (right) .. 100 (left)
MASTER_WIDTH_KEY = 'com.codex.VariableStroke.defaultWidth'  # per master, font units
MASTER_HEIGHT_KEY = 'com.codex.VariableStroke.defaultHeight'  # per master; unset = width
CAP_START_KEY = 'com.codex.VariableStroke.capStart'
CAP_END_KEY = 'com.codex.VariableStroke.capEnd'
CAP_START_ANGLE_KEY = 'com.codex.VariableStroke.capStartAngle'  # degrees, 'angle' caps
CAP_END_ANGLE_KEY = 'com.codex.VariableStroke.capEndAngle'
DEFAULT_CUT_ANGLE = 45.0
EXPORT_FILTER = 'VariableStrokeExport'
DEFAULT_WIDTH = 40.0
GLYPH_KEY = 'com.codex.VariableStroke.glyphEnabled'
GENERATED_KEY = 'com.codex.VariableStroke.generated'
ORIGINAL_FILL_KEY = 'com.codex.VariableStroke.originalFill'
# Keys written by an earlier build that stored outlines in the layer itself.
LEGACY_KEYS = ('com.codex.VariableStroke.id', 'com.codex.VariableStroke.signature')
LEGACY_HAIRLINE_KEY = 'com.codex.VariableStroke.hairline'


def enabled(path):
    return bool(path.attributes.get(PATH_KEY))


def generated(path):
    return bool(path.attributes.get(GENERATED_KEY))


def _number(value, default):
    try:
        value = float(value)
    except (ValueError, TypeError):
        return default
    return value if value > 0 else default


class StrokeDefaults(object):
    """What a master contributes to its strokes: default width/height and italic angle."""

    def __init__(self, width=DEFAULT_WIDTH, height=None, italic_angle=0.0):
        self.width = width
        self.height = height  # None: same as the width
        self.italic_angle = italic_angle


def master_default_width(master):
    """Default stroke width of a master (Variable Stroke settings window)."""
    try:
        return _number(master.userData.get(MASTER_WIDTH_KEY), DEFAULT_WIDTH)
    except AttributeError:
        return DEFAULT_WIDTH


def master_default_height(master):
    """Default stroke height of a master, or None to follow the width."""
    try:
        return _number(master.userData.get(MASTER_HEIGHT_KEY), None)
    except AttributeError:
        return None


def master_defaults(master):
    if master is None:
        return StrokeDefaults()
    try:
        angle = float(master.italicAngle or 0.0)
    except (AttributeError, TypeError, ValueError):
        angle = 0.0
    return StrokeDefaults(master_default_width(master), master_default_height(master), angle)


def layer_master(layer):
    master = None
    try:
        master = layer.master
    except Exception:
        pass
    if master is None:
        try:
            master = layer.parent.parent.masters[layer.associatedMasterId]
        except Exception:
            master = None
    return master


def layer_defaults(layer):
    return master_defaults(layer_master(layer)) if layer is not None else StrokeDefaults()


def layer_default_width(layer):
    """Default stroke width for a layer, from its (associated) master."""
    return layer_defaults(layer).width


def _resolve(defaults, path):
    if defaults is None:
        return layer_defaults(getattr(path, 'parent', None))
    if isinstance(defaults, StrokeDefaults):
        return defaults
    return StrokeDefaults(width=_number(defaults, DEFAULT_WIDTH))  # a plain width


def has_width_override(path):
    return STROKE_WIDTH_KEY in path.attributes


def has_height_override(path):
    return STROKE_HEIGHT_KEY in path.attributes


def stroke_width(path, defaults=None):
    """The path's base width in font units: its own value, else the master default."""
    if has_width_override(path):
        return _number(path.attributes.get(STROKE_WIDTH_KEY), DEFAULT_WIDTH)
    return _resolve(defaults, path).width


def stroke_height(path, defaults=None):
    """The path's base height: its own value, else the master's, else its width."""
    if has_height_override(path):
        return _number(path.attributes.get(STROKE_HEIGHT_KEY), DEFAULT_WIDTH)
    defaults = _resolve(defaults, path)
    if defaults.height is not None and not has_width_override(path):
        return defaults.height
    if defaults.height is not None and has_width_override(path):
        # A path-specific width keeps the master's width:height proportion.
        return stroke_width(path, defaults) * defaults.height / defaults.width
    return stroke_width(path, defaults)


def scale(node):
    """The node's width as a percentage of its path's base width."""
    return _number(node.userData.get(SCALE_KEY), 100.0)


def offset(node):
    """Where the centerline sits in the stroke at this node: -100 right .. 100 left."""
    try:
        value = float(node.userData.get(OFFSET_KEY, 0.0))
    except (TypeError, ValueError):
        return 0.0
    return max(-100.0, min(100.0, value))


def width(node, path=None, base=None):
    """Effective stroke width at an on-curve node."""
    if SCALE_KEY not in node.userData and WIDTH_KEY in node.userData:
        return max(1.0, _number(node.userData.get(WIDTH_KEY), DEFAULT_WIDTH))
    if base is None:
        base = stroke_width(path) if path is not None else DEFAULT_WIDTH
    return max(1.0, base * scale(node) / 100.0)


def node_nib(node, path, base_width, base_height):
    """(width, height, offset fraction) of the stroke at an on-curve node."""
    w = width(node, path, base_width)
    return (w, max(1.0, w * base_height / base_width), offset(node) / 100.0)


def migrate_path(path):
    """Turn legacy absolute node widths into path width + node percentages."""
    legacy = [node for node in path.nodes if node.type != OFFCURVE
              and WIDTH_KEY in node.userData and SCALE_KEY not in node.userData]
    if not legacy:
        return False
    widths = [_number(node.userData.get(WIDTH_KEY), DEFAULT_WIDTH) for node in legacy]
    if STROKE_WIDTH_KEY not in path.attributes:
        path.attributes[STROKE_WIDTH_KEY] = max(widths)
    base = stroke_width(path)
    for node, value in zip(legacy, widths):
        node.userData[SCALE_KEY] = round(value / base * 100.0, 2)
    for node in path.nodes:
        if WIDTH_KEY in node.userData:
            del node.userData[WIDTH_KEY]
    return True


def xy(node):
    return (float(node.position.x), float(node.position.y))


def segments_for_path(path, defaults=None):
    """Centerline segments with a (width, height, offset) nib at each end."""
    nodes = list(path.nodes)
    if not nodes:
        return []
    first = next((i for i, n in enumerate(nodes) if n.type != OFFCURVE), None)
    if first is None:
        return []
    if path.closed:
        nodes = nodes[first:] + nodes[:first]
        run = nodes[1:] + [nodes[0]]
    else:
        nodes = nodes[first:]
        run = nodes[1:]
    start = nodes[0]
    controls = []
    result = []

    defaults = _resolve(defaults, path)
    base_width = stroke_width(path, defaults)
    base_height = stroke_height(path, defaults)

    def width_at(node):
        return node_nib(node, path, base_width, base_height)

    for node in run:
        if node.type == OFFCURVE:
            controls.append(node)
            continue
        if len(controls) == 0:
            result.append(('line', (xy(start), xy(node)), width_at(start), width_at(node)))
        elif len(controls) == 2 and node.type == CURVE:
            result.append(('cubic', (xy(start), xy(controls[0]), xy(controls[1]), xy(node)),
                           width_at(start), width_at(node)))
        else:
            raise ValueError('Only line and cubic path segments are supported')
        start = node
        controls = []
    if controls:
        raise ValueError('Path ends with loose off-curve handles')
    return result


def edges_for_path(path, defaults=None):
    """[(node, (left, right))] for each on-curve node: where the outline really
    passes on both sides of it (miter/crossing points at corners)."""
    defaults = _resolve(defaults, path)
    nodes = list(path.nodes)
    first = next((i for i, n in enumerate(nodes) if n.type != OFFCURVE), None)
    if first is None:
        return []
    ordered = nodes[first:] + (nodes[:first] if path.closed else [])
    on_curve = [node for node in ordered if node.type != OFFCURVE]
    edges = node_edges(segments_for_path(path, defaults), bool(path.closed),
                       defaults.italic_angle)
    return [(on_curve[i], pair) for i, pair in sorted(edges.items()) if i < len(on_curve)]


def cut_angle(path, key):
    try:
        return float(path.attributes.get(key, DEFAULT_CUT_ANGLE))
    except (TypeError, ValueError):
        return DEFAULT_CUT_ANGLE


def curves_for_path(path, defaults=None):
    defaults = _resolve(defaults, path)
    return outline_curves(segments_for_path(path, defaults), bool(path.closed),
                          path.attributes.get(CAP_START_KEY, 'flat'),
                          path.attributes.get(CAP_END_KEY, 'flat'),
                          italic_angle=defaults.italic_angle,
                          start_angle=cut_angle(path, CAP_START_ANGLE_KEY),
                          end_angle=cut_angle(path, CAP_END_ANGLE_KEY))


def generated_paths(path, defaults=None):
    result = []
    for contour in curves_for_path(path, defaults):
        if not contour:
            continue
        new_path = GSPath()
        new_path.closed = True
        start = contour[0][1][0]
        new_path.nodes.append(GSNode(start, LINE))
        for kind, points in contour:
            if kind == 'cubic':
                new_path.nodes.append(GSNode(points[1], OFFCURVE))
                new_path.nodes.append(GSNode(points[2], OFFCURVE))
                new_path.nodes.append(GSNode(points[3], CURVE))
            else:
                new_path.nodes.append(GSNode(points[1], LINE))
        # Glyphs closes from its last node back to the first. No extra zero edge.
        if len(new_path.nodes) > 1 and xy(new_path.nodes[-1]) == start:
            if contour[-1][0] == 'cubic':
                new_path.nodes[0].type = CURVE
            new_path.nodes.pop()
        result.append(new_path)
    return result


def glyph_enabled(glyph):
    return bool(glyph and glyph.userData.get(GLYPH_KEY))


def active(path):
    try:
        glyph = path.parent.parent
    except AttributeError:
        glyph = None
    return enabled(path) and (glyph is None or glyph_enabled(glyph))


def set_glyph_enabled(glyph, state):
    """Toggle one glyph and prepare its centerlines across all layers."""
    glyph.userData[GLYPH_KEY] = bool(state)
    for layer in glyph.layers:
        cleanup_legacy_layer(layer)
        normalize_layer(layer, state)


def _is_centerline(path):
    if generated(path) or not path.nodes:
        return False
    try:
        segments_for_path(path)
    except ValueError:
        return False
    return True


def normalize_layer(layer, state):
    """Make every path's stroke flag follow the glyph's ON/OFF state.

    Paths drawn after switching ON get the stroke defaults; paths pasted into an
    OFF glyph lose the flag (their widths and caps are kept for later).
    Returns True when anything changed.
    """
    changed = False
    for path in layer.paths:
        if not state:
            if enabled(path):
                # Keep widths and caps so switching back ON restores the stroke.
                path.attributes[PATH_KEY] = False
                if ORIGINAL_FILL_KEY in path.attributes:
                    path.attributes['fill'] = path.attributes[ORIGINAL_FILL_KEY]
                changed = True
            continue
        if not _is_centerline(path):
            continue
        if not enabled(path):
            if ORIGINAL_FILL_KEY not in path.attributes:
                path.attributes[ORIGINAL_FILL_KEY] = bool(path.attributes.get('fill', True))
            path.attributes[PATH_KEY] = True
            path.attributes['fill'] = False
            changed = True
        for key in (CAP_START_KEY, CAP_END_KEY):
            if key not in path.attributes:
                path.attributes[key] = 'flat'
                changed = True
        changed = migrate_path(path) or changed
    return changed


def _pop(mapping, key):
    try:
        del mapping[key]
    except (KeyError, TypeError):
        pass


def cleanup_legacy_layer(layer):
    """Remove outline paths and hairline strokes left in the file by an earlier build."""
    changed = False
    for path in list(layer.paths):
        if generated(path):
            layer.shapes.remove(path)
            changed = True
        elif path.attributes.get(LEGACY_HAIRLINE_KEY):
            _pop(path.attributes, 'strokeWidth')
            _pop(path.attributes, LEGACY_HAIRLINE_KEY)
            changed = True
        for key in LEGACY_KEYS:
            if key in path.attributes:
                _pop(path.attributes, key)
                changed = True
    return changed


def expand_layer(layer, glyph_on=None, defaults=None):
    """Replace every centerline in `layer` by its Bézier outline.

    Glyphs calls this on the throwaway copy it prepares for preview, inactive
    glyphs and metrics (GSPrepareLayerCallback), and the export filter calls it
    on the export copy. The editable layer keeps only the centerlines.

    `glyph_on` is the glyph's ON/OFF state when known; it wins over the per-path
    flag, so new paths count at once and pasted paths in an OFF glyph do not.
    `defaults` (StrokeDefaults) carries the master's default width/height and
    italic angle; pass it when the layer is a detached copy that cannot find its
    master.
    """
    if defaults is None:
        defaults = layer_defaults(layer)
    if glyph_on is False:
        return 0
    count = 0
    for path in [shape for shape in list(layer.shapes) if isinstance(shape, GSPath)]:
        if not (_is_centerline(path) and (glyph_on or enabled(path))):
            continue
        try:
            replacements = generated_paths(path, defaults)
        except ValueError:
            continue
        layer.shapes.remove(path)
        for replacement in replacements:
            layer.shapes.append(replacement)
        count += 1
    return count


def interpolate_widths(layer, glyph, interpolation):
    """Give an interpolated layer the blended stroke widths of its source layers.

    Glyphs interpolates node positions but copies path attributes and node
    userData from one source, so width, height, node percentages and offsets are
    blended here from `interpolation` ({layerId: factor}). Outlines themselves
    have a fixed node structure, so the layer stays compatible either way.
    """
    sources = []
    for layer_id, factor in dict(interpolation or {}).items():
        try:
            source = glyph.layers[layer_id]
        except (KeyError, IndexError, TypeError):
            source = None
        if source is not None:
            sources.append((list(source.paths), float(factor)))
    if not sources:
        return False
    changed = False
    for index, path in enumerate(layer.paths):
        if not enabled(path):
            continue
        base_w = base_h = 0.0
        widths, offsets = {}, {}
        for paths, factor in sources:
            if index >= len(paths) or len(paths[index].nodes) != len(path.nodes):
                break
            other = paths[index]
            other_w, other_h = stroke_width(other), stroke_height(other)
            base_w += factor * other_w
            base_h += factor * other_h
            for position, node in enumerate(other.nodes):
                if node.type != OFFCURVE:
                    widths[position] = widths.get(position, 0.0) + factor * width(node, other, other_w)
                    offsets[position] = offsets.get(position, 0.0) + factor * offset(node)
        else:
            if base_w <= 0 or base_h <= 0:
                continue
            path.attributes[STROKE_WIDTH_KEY] = base_w
            path.attributes[STROKE_HEIGHT_KEY] = base_h
            for position, value in widths.items():
                node = path.nodes[position]
                node.userData[SCALE_KEY] = value / base_w * 100.0
                node.userData[OFFSET_KEY] = offsets[position]
                if WIDTH_KEY in node.userData:
                    del node.userData[WIDTH_KEY]
            changed = True
    return changed


def reset_width_overrides(layers):
    """Let the paths in `layers` follow their master's default width again."""
    count = 0
    for layer in layers:
        for path in layer.paths:
            if enabled(path) and (has_width_override(path) or has_height_override(path)):
                migrate_path(path)
                for key in (STROKE_WIDTH_KEY, STROKE_HEIGHT_KEY):
                    if key in path.attributes:
                        del path.attributes[key]
                count += 1
    return count


def convert_layer(layer):
    """Permanently replace enabled centerlines with Bézier outlines in the target layer."""
    if getattr(layer, 'parent', None) is not None and not glyph_enabled(layer.parent):
        return 0
    cleanup_legacy_layer(layer)
    return expand_layer(layer, glyph_on=True if getattr(layer, 'parent', None) is not None else None)
