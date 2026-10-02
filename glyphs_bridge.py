"""Glyphs 3 adapters shared by the editing tool and the export filter."""
import collections
import threading
from GlyphsApp import GSPath, GSNode, LINE, CURVE, OFFCURVE
from variable_stroke_core import outline_curves, node_edges, ellipse_nib_edges, sub

PATH_KEY = 'com.codex.VariableStroke.enabled'
# Legacy absolute node width; converted to STROKE_WIDTH_KEY x SCALE_KEY on edit.
WIDTH_KEY = 'com.codex.VariableStroke.width'
STROKE_WIDTH_KEY = 'com.codex.VariableStroke.strokeWidth'  # per path, font units
SCALE_KEY = 'com.codex.VariableStroke.scale'  # per node, percent of the path width
HEIGHT_SCALE_KEY = 'com.codex.VariableStroke.heightScale'  # per node, percent of path height
STROKE_HEIGHT_KEY = 'com.codex.VariableStroke.strokeHeight'  # per path, font units
OFFSET_KEY = 'com.codex.VariableStroke.offset'  # per node, -100 (right) .. 100 (left)
ROTATION_KEY = 'com.codex.VariableStroke.rotation'  # per node, nib axes angle in page degrees
# Live corners, per node. The radius key alone (older files) also means ON.
CORNER_ON_KEY = 'com.codex.VariableStroke.corner'  # bool
CORNER_KEY = 'com.codex.VariableStroke.cornerRadius'  # outer radius, font units
CORNER_INNER_KEY = 'com.codex.VariableStroke.cornerInner'  # inner radius; absent = outer
CORNER_TENSION_KEY = 'com.codex.VariableStroke.cornerTension'  # %, 100 = circular arc
CORNER_RATIO_KEY = 'com.codex.VariableStroke.cornerRatio'  # %, 100 = symmetric
CORNER_INNER_TENSION_KEY = 'com.codex.VariableStroke.cornerInnerTension'
CORNER_INNER_RATIO_KEY = 'com.codex.VariableStroke.cornerInnerRatio'
DEFAULT_CORNER_RADIUS = 20.0
# Written by interpolate_layer on interpolated (instance) layers, whose glyph copy
# does not carry the ON/OFF state or the master's italic angle.
LAYER_STATE_KEY = 'com.codex.VariableStroke.layerGlyphEnabled'
LAYER_ITALIC_KEY = 'com.codex.VariableStroke.layerItalicAngle'
LAYER_NIB_ANGLE_KEY = 'com.codex.VariableStroke.layerNibAngle'
ZERO_CORNER = {'outer': 0.0, 'inner': 0.0, 'tension': 100.0,
               'inner_tension': 100.0, 'ratio': 100.0, 'inner_ratio': 100.0}
_CORNER_PARTS = ('outer', 'inner', 'tension', 'inner_tension', 'ratio', 'inner_ratio')
_CORNER_PAIRS = {'radius': (CORNER_KEY, CORNER_INNER_KEY),
                 'tension': (CORNER_TENSION_KEY, CORNER_INNER_TENSION_KEY),
                 'ratio': (CORNER_RATIO_KEY, CORNER_INNER_RATIO_KEY)}

MASTER_WIDTH_KEY = 'com.codex.VariableStroke.defaultWidth'  # per master, font units
MASTER_HEIGHT_KEY = 'com.codex.VariableStroke.defaultHeight'  # per master; unset = width
MASTER_ANGLE_KEY = 'com.codex.VariableStroke.defaultAngle'  # ellipse axes on page
CAP_START_KEY = 'com.codex.VariableStroke.capStart'
CAP_END_KEY = 'com.codex.VariableStroke.capEnd'
CAP_START_ANGLE_KEY = 'com.codex.VariableStroke.capStartAngle'  # degrees, 'angle' caps
CAP_END_ANGLE_KEY = 'com.codex.VariableStroke.capEndAngle'
DEFAULT_CUT_ANGLE = 45.0
EXPORT_FILTER = 'VariableStrokeExport'
DEFAULT_WIDTH = 40.0
GLYPH_KEY = 'com.codex.VariableStroke.glyphEnabled'
# Set on a glyph once any of its nodes has a live corner, so glyphs without
# corners skip looking at their other masters (see corner_specs).
GLYPH_CORNERS_KEY = 'com.codex.VariableStroke.glyphHasCorners'
GENERATED_KEY = 'com.codex.VariableStroke.generated'  # legacy in-layer outlines
# Marks outlines this plugin produced, so they are never taken for centerlines
# again (a layer prepared twice, an interpolated or exported copy).
OUTLINE_KEY = 'com.codex.VariableStroke.outline'
ORIGINAL_FILL_KEY = 'com.codex.VariableStroke.originalFill'
# Keys written by an earlier build that stored outlines in the layer itself.
LEGACY_KEYS = ('com.codex.VariableStroke.id', 'com.codex.VariableStroke.signature')
LEGACY_HAIRLINE_KEY = 'com.codex.VariableStroke.hairline'


# Path attributes that define the stroke (copied onto interpolated layers).
_STROKE_ATTRIBUTES = (PATH_KEY, CAP_START_KEY, CAP_END_KEY, CAP_START_ANGLE_KEY, CAP_END_ANGLE_KEY)

# Outline geometry is recomputed constantly (Glyphs' preview, handle drawing,
# hit tests) for unchanged input, so results are kept by their exact input.
# Layers are also prepared off the main thread, hence the lock.
_CACHE = collections.OrderedDict()
_CACHE_SIZE = 512
_CACHE_LOCK = threading.Lock()


def _cached(key, compute):
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    value = compute()
    with _CACHE_LOCK:
        _CACHE[key] = value
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return value


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


def _blend_angles(values):
    """Interpolate ellipse axes by the shortest turn (angles repeat at 180°)."""
    reference = values[0][0]
    return (reference + sum(factor * ((angle - reference + 90.0) % 180.0 - 90.0)
                            for angle, factor in values)) % 180.0


class StrokeDefaults(object):
    """What a master contributes to its strokes."""

    def __init__(self, width=DEFAULT_WIDTH, height=None, italic_angle=0.0, nib_angle=0.0):
        self.width = width
        self.height = height  # None: same as the width
        self.italic_angle = italic_angle
        self.nib_angle = nib_angle


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


def master_default_angle(master):
    """Page angle of a master's elliptical pen; zero preserves old fonts."""
    try:
        return float(master.userData.get(MASTER_ANGLE_KEY, 0.0)) % 180.0
    except (AttributeError, TypeError, ValueError):
        return 0.0


def master_defaults(master):
    if master is None:
        return StrokeDefaults()
    try:
        angle = float(master.italicAngle or 0.0)
    except (AttributeError, TypeError, ValueError):
        angle = 0.0
    return StrokeDefaults(master_default_width(master), master_default_height(master), angle,
                          master_default_angle(master))


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
    defaults = master_defaults(layer_master(layer)) if layer is not None else StrokeDefaults()
    if layer is not None:
        try:
            value = layer.userData.get(LAYER_NIB_ANGLE_KEY)
            if value is not None:
                defaults.nib_angle = float(value) % 180.0
        except (AttributeError, TypeError, ValueError):
            pass
    return defaults


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
    """The path's independent height: its own value, else the master's default."""
    if has_height_override(path):
        return _number(path.attributes.get(STROKE_HEIGHT_KEY), DEFAULT_WIDTH)
    defaults = _resolve(defaults, path)
    return defaults.height if defaults.height is not None else DEFAULT_WIDTH


def scale(node):
    """Width percent; an unset width shares the height percent."""
    return _number(node.userData.get(SCALE_KEY,
                   node.userData.get(HEIGHT_SCALE_KEY)), 100.0)


def height_scale(node):
    """Height percent; an unset height shares the width percent."""
    return _number(node.userData.get(HEIGHT_SCALE_KEY,
                   node.userData.get(SCALE_KEY)), 100.0)


def set_node_nib_size(node, axis, size, base_width, base_height):
    """Edit one nib axis without changing the other axis's effective size."""
    if axis == 'width':
        if HEIGHT_SCALE_KEY not in node.userData:
            node.userData[HEIGHT_SCALE_KEY] = height_scale(node)
        node.userData[SCALE_KEY] = max(1.0, round(size / base_width * 100.0, 1))
    elif axis == 'height':
        if SCALE_KEY not in node.userData:
            node.userData[SCALE_KEY] = scale(node)
        node.userData[HEIGHT_SCALE_KEY] = max(1.0, round(size / base_height * 100.0, 1))
    else:
        raise ValueError('Unknown nib axis: ' + str(axis))


def offset(node):
    """Where the centerline sits in the stroke at this node: -100 right .. 100 left."""
    try:
        value = float(node.userData.get(OFFSET_KEY, 0.0))
    except (TypeError, ValueError):
        return 0.0
    return max(-100.0, min(100.0, value))


def rotation(node, default=0.0):
    """Page angle of this node's ellipse axes, or its master's default."""
    try:
        return float(node.userData.get(ROTATION_KEY, default)) % 180.0
    except (TypeError, ValueError):
        return default


def corner_on(node):
    data = node.userData
    return bool(data.get(CORNER_ON_KEY, CORNER_KEY in data))


def _corner_value(node, key, default):
    try:
        value = node.userData.get(key)
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def corner_radius(node):
    """Outer live-corner radius at a node, or None when its corners stay sharp."""
    if not corner_on(node):
        return None
    return max(0.0, _corner_value(node, CORNER_KEY, DEFAULT_CORNER_RADIUS))


def corner_spec(node):
    """The node's live corner for the outline code, or None when it is off."""
    outer = corner_radius(node)
    if outer is None:
        return None
    tension = _corner_value(node, CORNER_TENSION_KEY, 100.0)
    ratio = _corner_value(node, CORNER_RATIO_KEY, 100.0)
    return {'outer': outer, 'inner': max(0.0, _corner_value(node, CORNER_INNER_KEY, outer)),
            'tension': tension,
            'inner_tension': _corner_value(node, CORNER_INNER_TENSION_KEY, tension),
            'ratio': ratio,
            'inner_ratio': _corner_value(node, CORNER_INNER_RATIO_KEY, ratio)}


def corner_linked(node, kind):
    """An absent second value follows the first, including in legacy files."""
    return _CORNER_PAIRS[kind][1] not in node.userData


def set_corner_linked(node, kind, linked):
    first, second = _CORNER_PAIRS[kind]
    if linked:
        if second in node.userData:
            del node.userData[second]
    elif second not in node.userData:
        spec = corner_spec(node)
        default = {'radius': DEFAULT_CORNER_RADIUS,
                   'tension': 100.0, 'ratio': 100.0}[kind]
        node.userData[second] = (spec or {}).get(
            {'radius': 'inner', 'tension': 'inner_tension', 'ratio': 'inner_ratio'}[kind],
            _corner_value(node, first, default))


def corner_side_key(node, kind, which):
    """Write the shared value while linked, or one side while independent."""
    first, second = _CORNER_PAIRS[kind]
    return second if which == 'inner' and not corner_linked(node, kind) else first


def width(node, path=None, base=None):
    """Effective stroke width at an on-curve node."""
    if SCALE_KEY not in node.userData and HEIGHT_SCALE_KEY not in node.userData \
            and WIDTH_KEY in node.userData:
        return max(1.0, _number(node.userData.get(WIDTH_KEY), DEFAULT_WIDTH))
    if base is None:
        base = stroke_width(path) if path is not None else DEFAULT_WIDTH
    return max(1.0, base * scale(node) / 100.0)


def node_nib(node, path, base_width, base_height, default_angle=None):
    """(width, height, offset fraction, rotation) at an on-curve node."""
    w = width(node, path, base_width)
    legacy = WIDTH_KEY in node.userData and SCALE_KEY not in node.userData \
        and HEIGHT_SCALE_KEY not in node.userData
    h = w * base_height / base_width if legacy else base_height * height_scale(node) / 100.0
    if default_angle is None:
        default_angle = layer_defaults(getattr(path, 'parent', None)).nib_angle
    return (w, max(1.0, h),
            offset(node) / 100.0, rotation(node, default_angle))


def migrate_path(path):
    """Turn legacy absolute node widths into path width + node percentages."""
    legacy = [node for node in path.nodes if node.type != OFFCURVE
              and WIDTH_KEY in node.userData and SCALE_KEY not in node.userData
              and HEIGHT_SCALE_KEY not in node.userData]
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
        return node_nib(node, path, base_width, base_height, defaults.nib_angle)

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


def _on_curve_nodes(path):
    """On-curve nodes in segment order (segment i starts at node i)."""
    nodes = list(path.nodes)
    first = next((i for i, n in enumerate(nodes) if n.type != OFFCURVE), None)
    if first is None:
        return []
    ordered = nodes[first:] + (nodes[:first] if path.closed else [])
    return [node for node in ordered if node.type != OFFCURVE]


def selected_nib_nodes(path, selected):
    """On-curve nodes whose own point or attached Bézier handle is selected."""
    nodes = list(path.nodes)
    count = len(nodes)
    owners = set()
    for index, node in enumerate(nodes):
        if node not in selected:
            continue
        if node.type != OFFCURVE:
            owners.add(index)
            continue
        for distance in range(1, count):
            before = index - distance
            after = index + distance
            candidates = (before, after)
            for candidate in candidates:
                if not path.closed and not 0 <= candidate < count:
                    continue
                candidate %= count
                if nodes[candidate].type != OFFCURVE:
                    owners.add(candidate)
                    break
            else:
                continue
            break
    return [node for index, node in enumerate(nodes) if index in owners]


def ellipse_cap_nodes(path):
    """Open-path end nodes whose cap is the oriented nib ellipse."""
    if path.closed:
        return []
    on_curve = _on_curve_nodes(path)
    if not on_curve:
        return []
    result = []
    if path.attributes.get(CAP_START_KEY) == 'ellipse':
        result.append(on_curve[0])
    if path.attributes.get(CAP_END_KEY) == 'ellipse' and on_curve[-1] not in result:
        result.append(on_curve[-1])
    return result


def edges_for_path(path, defaults=None):
    """[(node, (left, right))] for each on-curve node: where the outline really
    passes on both sides of it (miter/crossing points at corners)."""
    defaults = _resolve(defaults, path)
    on_curve = _on_curve_nodes(path)
    if not on_curve:
        return []
    segments = tuple(segments_for_path(path, defaults))
    closed = bool(path.closed)
    edges = _cached(('edges', segments, closed, defaults.italic_angle),
                    lambda: node_edges(list(segments), closed, defaults.italic_angle))
    if segments and not closed:
        first_cap = path.attributes.get(CAP_START_KEY, 'flat')
        last_cap = path.attributes.get(CAP_END_KEY, 'flat')
        if first_cap == 'ellipse' or last_cap == 'ellipse':
            edges = dict(edges)  # the cached node edges belong to every cap style
            if first_cap == 'ellipse':
                _, points, nib, _ = segments[0]
                edges[0] = ellipse_nib_edges(points[0], sub(points[1], points[0]), nib)
            if last_cap == 'ellipse':
                _, points, _, nib = segments[-1]
                edges[len(on_curve)-1] = ellipse_nib_edges(
                    points[-1], sub(points[-1], points[-2]), nib)
    return [(on_curve[i], pair) for i, pair in sorted(edges.items()) if i < len(on_curve)]


def cut_angle(path, key):
    try:
        return float(path.attributes.get(key, DEFAULT_CUT_ANGLE))
    except (TypeError, ValueError):
        return DEFAULT_CUT_ANGLE


def _sibling_paths(path):
    """The same path in the glyph's other layers (masters, brace layers), when
    compatible: needed so every layer rounds the same corners."""
    layer = getattr(path, 'parent', None)
    glyph = getattr(layer, 'parent', None) if layer is not None else None
    if glyph is None:
        return []
    try:
        paths = list(layer.paths)
        index = next(i for i, other in enumerate(paths) if other == path)
        layers = list(glyph.layers)
    except Exception:
        return []
    count = len(_on_curve_nodes(path))
    result = []
    for other_layer in layers:
        if other_layer == layer:
            continue
        other_paths = list(other_layer.paths)
        if index < len(other_paths) and len(_on_curve_nodes(other_paths[index])) == count:
            result.append(other_paths[index])
    return result


def _glyph_of(item):
    """The glyph a path or node belongs to, or None."""
    while item is not None and not hasattr(item, 'layers'):
        item = getattr(item, 'parent', None)
    return item


def _glyph_has_corners(path):
    glyph = _glyph_of(path)
    try:
        return bool(glyph is not None and glyph.userData.get(GLYPH_CORNERS_KEY))
    except Exception:
        return False


def note_corner(node):
    """Remember that this node's glyph uses live corners (see corner_specs)."""
    glyph = _glyph_of(node)
    try:
        if glyph is not None and not glyph.userData.get(GLYPH_CORNERS_KEY):
            glyph.userData[GLYPH_CORNERS_KEY] = True
    except Exception:
        pass


def corner_specs(path):
    """Live corner per on-curve node. A corner that is on in any other master of
    the glyph gets a zero-size arc here, so masters keep compatible outlines while
    each master switches its corners on or off on its own."""
    nodes = _on_curve_nodes(path)
    specs = [corner_spec(node) for node in nodes]
    if all(spec is not None for spec in specs) or not _glyph_has_corners(path):
        return specs
    rounded_elsewhere = set()
    for sibling in _sibling_paths(path):
        for k, node in enumerate(_on_curve_nodes(sibling)):
            if corner_on(node):
                rounded_elsewhere.add(k)
    return [spec if spec is not None or k not in rounded_elsewhere else dict(ZERO_CORNER)
            for k, spec in enumerate(specs)]


def corner_widgets(path, defaults=None):
    """One dict per rounded outline corner (see outline_curves' report), with
    'node' set to the GSNode it belongs to."""
    report = []
    try:
        curves_for_path(path, defaults, report)
    except ValueError:
        return []
    on_curve = _on_curve_nodes(path)
    if not on_curve:
        return []
    for widget in report:
        widget['node'] = on_curve[widget['node'] % len(on_curve)]
    return report


def curves_for_path(path, defaults=None, report=None):
    defaults = _resolve(defaults, path)
    segments = tuple(segments_for_path(path, defaults))
    closed = bool(path.closed)
    caps = (path.attributes.get(CAP_START_KEY, 'flat'), path.attributes.get(CAP_END_KEY, 'flat'))
    angles = (cut_angle(path, CAP_START_ANGLE_KEY), cut_angle(path, CAP_END_ANGLE_KEY))
    corners = corner_specs(path)
    key = ('curves', segments, closed, caps, angles, defaults.italic_angle,
           tuple(None if c is None else tuple(sorted(c.items())) for c in corners))

    def compute():
        collected = []
        contours = outline_curves(list(segments), closed, caps[0], caps[1],
                                  italic_angle=defaults.italic_angle,
                                  start_angle=angles[0], end_angle=angles[1],
                                  corner_radii=corners, report=collected)
        return contours, collected

    contours, collected = _cached(key, compute)
    if report is not None:
        report.extend(dict(widget) for widget in collected)  # callers annotate them
    return contours


def generated_paths(path, defaults=None, contours=None):
    result = []
    for contour in (curves_for_path(path, defaults) if contours is None else contours):
        if not contour:
            continue
        new_path = GSPath()
        new_path.attributes[OUTLINE_KEY] = True
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


def is_outline(path):
    return bool(path.attributes.get(OUTLINE_KEY))


def _valid_structure(path):
    """Only lines and cubics (two off-curves before a curve node): what
    segments_for_path accepts, checked without computing any widths."""
    nodes = list(path.nodes)
    types = [node.type for node in nodes]
    if not any(kind != OFFCURVE for kind in types):
        return False
    if path.closed:
        first = next(i for i, kind in enumerate(types) if kind != OFFCURVE)
        types = types[first+1:] + types[:first+1]
    else:
        if types[0] == OFFCURVE:
            types = types[next(i for i, kind in enumerate(types) if kind != OFFCURVE):]
        types = types[1:]
    controls = 0
    for kind in types:
        if kind == OFFCURVE:
            controls += 1
            continue
        if controls not in (0, 2) or (controls == 2 and kind != CURVE):
            return False
        controls = 0
    return controls == 0


def _is_centerline(path):
    if generated(path) or is_outline(path) or not path.nodes:
        return False
    return _valid_structure(path)


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
    if state:
        # Corners can arrive by paste; make sure the glyph is known to use them.
        paths = list(layer.paths)
        if paths and not _glyph_has_corners(paths[0]):
            if any(node.type != OFFCURVE and corner_on(node)
                   for path in paths for node in path.nodes):
                note_corner(paths[0])
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


def _expansion_plan(layer, glyph_on=None, defaults=None):
    if defaults is None:
        defaults = layer_defaults(layer)
    if glyph_on is False:
        return []
    plan = []
    for path in [shape for shape in list(layer.shapes) if isinstance(shape, GSPath)]:
        if not (_is_centerline(path) and (glyph_on or enabled(path))):
            continue
        try:
            plan.append((path, generated_paths(path, defaults)))
        except ValueError:
            continue
    return plan


def _apply_plan(layer, plan):
    for path, replacements in plan:
        layer.shapes.remove(path)
        for replacement in replacements:
            layer.shapes.append(replacement)
    return len(plan)


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
    return _apply_plan(layer, _expansion_plan(layer, glyph_on, defaults))


def interpolate_layer(layer, glyph, interpolation):
    """Carry the strokes over to a layer Glyphs has just interpolated (instances in
    the preview, interpolation previews by other plugins, virtual masters).

    Glyphs interpolates node positions only, and the interpolated glyph does not
    know the ON/OFF state, so this writes the state and the blended italic angle
    onto the layer, the stroke attributes onto its paths, and blends width,
    height, node %, position and live corners (a corner off in a source counts as
    radius 0) from `interpolation` ({layerId: factor}).
    """
    sources = []
    for layer_id, factor in dict(interpolation or {}).items():
        try:
            source = glyph.layers[layer_id]
        except (KeyError, IndexError, TypeError):
            source = None
        if source is not None:
            sources.append((source, list(source.paths), float(factor)))
    if not sources:
        return False
    state = glyph_enabled(glyph)
    layer.userData[LAYER_STATE_KEY] = state
    layer.userData[LAYER_ITALIC_KEY] = sum(factor * layer_defaults(source).italic_angle
                                           for source, _, factor in sources)
    layer.userData[LAYER_NIB_ANGLE_KEY] = _blend_angles([
        (layer_defaults(source).nib_angle, factor) for source, _, factor in sources])
    if not state:
        return True
    for index, path in enumerate(layer.paths):
        others = [(source, paths[index], factor) for source, paths, factor in sources
                  if index < len(paths) and len(paths[index].nodes) == len(path.nodes)]
        if len(others) != len(sources):
            continue
        template = next((other for _, other, _ in others if enabled(other)), None)
        if template is None:
            continue
        for key in _STROKE_ATTRIBUTES:
            if template.attributes.get(key) is not None:
                path.attributes[key] = template.attributes[key]
        path.attributes['fill'] = False
        base_w = sum(factor * stroke_width(other) for _, other, factor in others)
        base_h = sum(factor * stroke_height(other) for _, other, factor in others)
        if base_w <= 0 or base_h <= 0:
            continue
        path.attributes[STROKE_WIDTH_KEY] = base_w
        path.attributes[STROKE_HEIGHT_KEY] = base_h
        for position, node in enumerate(path.nodes):
            if node.type == OFFCURVE:
                continue
            w = h = o = 0.0
            angles = []
            corner, any_corner = dict.fromkeys(_CORNER_PARTS, 0.0), False
            for source, other, factor in others:
                source_node = other.nodes[position]
                w += factor * width(source_node, other, stroke_width(other))
                h += factor * node_nib(source_node, other, stroke_width(other),
                                       stroke_height(other))[1]
                o += factor * offset(source_node)
                angles.append((rotation(source_node,
                                        layer_defaults(source).nib_angle), factor))
                spec = corner_spec(source_node)
                any_corner = any_corner or spec is not None
                for key in _CORNER_PARTS:
                    corner[key] += factor * (spec or ZERO_CORNER)[key]
            node.userData[SCALE_KEY] = w / base_w * 100.0
            node.userData[HEIGHT_SCALE_KEY] = h / base_h * 100.0
            node.userData[OFFSET_KEY] = o
            node.userData[ROTATION_KEY] = _blend_angles(angles)
            node.userData[CORNER_ON_KEY] = any_corner
            if any_corner:
                node.userData[CORNER_KEY] = corner['outer']
                node.userData[CORNER_INNER_KEY] = corner['inner']
                node.userData[CORNER_TENSION_KEY] = corner['tension']
                node.userData[CORNER_INNER_TENSION_KEY] = corner['inner_tension']
                node.userData[CORNER_RATIO_KEY] = corner['ratio']
                node.userData[CORNER_INNER_RATIO_KEY] = corner['inner_ratio']
            if WIDTH_KEY in node.userData:
                del node.userData[WIDTH_KEY]
    return True


interpolate_widths = interpolate_layer  # earlier name


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


def layer_state(layer):
    """ON/OFF for a layer's strokes, or None when unknown (then the per-path flags
    decide). Interpolated layers carry it themselves (see interpolate_layer); a
    glyph copy without our key, e.g. from an interpolated font, is unknown."""
    try:
        marked = layer.userData.get(LAYER_STATE_KEY)
        if marked is not None:
            return bool(marked)
    except Exception:
        pass
    try:
        glyph = layer.parent
        if glyph is not None and glyph.userData is not None and \
                glyph.userData.get(GLYPH_KEY) is not None:
            return glyph_enabled(glyph)
    except Exception:
        pass
    return None


def _unmark(layer):
    for path in layer.paths:
        if is_outline(path):
            del path.attributes[OUTLINE_KEY]


def convert_layer(layer, keep_marks=True):
    """Replace enabled centerlines with Bézier outlines in the target layer.

    Export copies keep the outline marks so no later step expands them again;
    a conversion the user asks for (keep_marks=False) leaves plain paths.
    """
    state = layer_state(layer)
    if state is False:
        return 0
    cleanup_legacy_layer(layer)
    count = expand_layer(layer, glyph_on=state)
    if not keep_marks:
        _unmark(layer)
        layer.userData[LAYER_STATE_KEY] = False
    return count


def convert_glyph(glyph, keep_marks=True):
    """Convert every layer of a glyph. All outlines are computed before any layer
    changes, because each layer looks at the others to keep corners compatible."""
    if not glyph_enabled(glyph):
        return 0
    layers = list(glyph.layers)
    for layer in layers:
        cleanup_legacy_layer(layer)
    plans = [(layer, _expansion_plan(layer, True)) for layer in layers]
    count = sum(_apply_plan(layer, plan) for layer, plan in plans)
    if not keep_marks:
        for layer in layers:
            _unmark(layer)
        set_glyph_enabled(glyph, False)
        for layer in layers:
            layer.userData[LAYER_STATE_KEY] = False
    return count
