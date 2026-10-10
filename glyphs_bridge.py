"""Glyphs 3 adapters shared by the editing tool and the export filter."""
import collections
import contextlib
import json
import math
import threading
import time
import uuid
from GlyphsApp import GSPath, GSNode, LINE, CURVE, OFFCURVE
from variable_stroke_core import (outline_curves, ellipse_nib_edges, sub, add, mul,
                                  move_outline_vertices,
                                  outline_direction_at_vertex, DEFAULT_CAP_CURVE)
import width_profile

PATH_KEY = 'com.codex.VariableStroke.enabled'
# Legacy absolute node width; converted to STROKE_WIDTH_KEY x SCALE_KEY on edit.
WIDTH_KEY = 'com.codex.VariableStroke.width'
STROKE_WIDTH_KEY = 'com.codex.VariableStroke.strokeWidth'  # per path, font units
SCALE_KEY = 'com.codex.VariableStroke.scale'  # per node, percent of the path width
HEIGHT_SCALE_KEY = 'com.codex.VariableStroke.heightScale'  # per node, percent of path height
STROKE_HEIGHT_KEY = 'com.codex.VariableStroke.strokeHeight'  # per path, font units
OFFSET_KEY = 'com.codex.VariableStroke.offset'  # per node; values beyond +/-100 move the stroke off the centerline
CORNER_OFFSET_LEFT_KEY = 'com.codex.VariableStroke.cornerOffsetLeft'
CORNER_OFFSET_RIGHT_KEY = 'com.codex.VariableStroke.cornerOffsetRight'
CORNER_HANDLE_KEYS = (
    'com.codex.VariableStroke.cornerHandleLeftIn',
    'com.codex.VariableStroke.cornerHandleLeftOut',
    'com.codex.VariableStroke.cornerHandleRightIn',
    'com.codex.VariableStroke.cornerHandleRightOut')
VIRTUAL_KEY = 'com.codex.VariableStroke.virtualNodes'  # per path, ordered virtual sections
VIRTUAL_ANCHOR_KEY = 'com.codex.VariableStroke.virtualAnchor'  # stable on-curve identity
VIRTUAL_STORE_KEY = 'com.codex.VariableStroke.virtualSections'  # per layer (see virtual_nodes)
VIRTUAL_OWNER_KEY = 'com.codex.VariableStroke.virtualOwner'  # per path id
# Virtual node modes. 'whole' is a step whose before/after percentages scale
# every width up to the path's start/end; 'section' scales the stretch up to
# its endSegment/endT.
VIRTUAL_MODES = ('continuous', 'step', 'whole', 'section')
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
CAP_START_CURVE_KEY = 'com.codex.VariableStroke.capStartCurve'
CAP_END_CURVE_KEY = 'com.codex.VariableStroke.capEndCurve'
CAP_START_CURVE_ON_KEY = 'com.codex.VariableStroke.capStartCurveOn'
CAP_END_CURVE_ON_KEY = 'com.codex.VariableStroke.capEndCurveOn'
# Width profiles (see width_profile): the font's library {id: profile} and, per
# path, the id of the profile it uses. Interpolated layers cannot reach their
# font, so they carry the profile itself.
FONT_PROFILES_KEY = 'com.codex.VariableStroke.profiles'
PROFILE_KEY = 'com.codex.VariableStroke.profile'
PROFILE_DATA_KEY = 'com.codex.VariableStroke.profileData'
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
BLEND_KEY = 'com.codex.VariableStroke.blend'  # instance path: its blended outline in _BLENDS
ORIGINAL_FILL_KEY = 'com.codex.VariableStroke.originalFill'
# Keys written by an earlier build that stored outlines in the layer itself.
LEGACY_KEYS = ('com.codex.VariableStroke.id', 'com.codex.VariableStroke.signature')
LEGACY_HAIRLINE_KEY = 'com.codex.VariableStroke.hairline'


# Path attributes that define the stroke (copied onto interpolated layers).
_STROKE_ATTRIBUTES = (PATH_KEY, CAP_START_KEY, CAP_END_KEY, CAP_START_ANGLE_KEY,
                      CAP_END_ANGLE_KEY, CAP_START_CURVE_KEY, CAP_END_CURVE_KEY,
                      CAP_START_CURVE_ON_KEY, CAP_END_CURVE_ON_KEY, PROFILE_KEY)

# Outline geometry is recomputed constantly (Glyphs' preview, handle drawing,
# hit tests) for unchanged input, so results are kept by their exact input.
# Layers are also prepared off the main thread, hence the lock.
_CACHE = collections.OrderedDict()
_CACHE_SIZE = 512
_CACHE_LOCK = threading.Lock()


class Profile(object):
    """Opt-in timings of the work done while editing, summed per section (the
    tool turns it on, see PROFILE_KEY in its plugin). Thread safe: Glyphs also
    prepares layers off the main thread."""

    def __init__(self):
        self.enabled = False
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self._stats = {}
            self._start = time.perf_counter()

    def add(self, name, seconds):
        with self._lock:
            count, total = self._stats.get(name, (0, 0.0))
            self._stats[name] = (count + 1, total + seconds)

    @contextlib.contextmanager
    def section(self, name):
        if not self.enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - start)

    def report(self):
        with self._lock:
            stats, elapsed = self._stats, time.perf_counter() - self._start
        self.reset()
        lines = ['Variable Stroke profile: %.0f ms' % (elapsed * 1000)]
        for name, (count, total) in sorted(stats.items(), key=lambda item: -item[1][1]):
            lines.append('  %-34s %6d x %9.1f ms  (%.2f ms each)'
                         % (name, count, total * 1000, total * 1000 / count))
        return '\n'.join(lines)


PROFILE = Profile()


def _cached(key, compute):
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    with PROFILE.section('outline geometry (cache miss)'):
        value = compute()
    with _CACHE_LOCK:
        _CACHE[key] = value
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return value


def node_data(node):
    """A node's userData as a plain dict, read from Glyphs in one call.

    Every stroke value lives in node userData. Reading it key by key through the
    GlyphsApp proxy costs a bridge round trip per key, and paths are read again
    for every redraw while editing, so hot paths read each node once."""
    try:
        raw = node.pyobjc_instanceMethods.userData()
    except AttributeError:
        return node.userData  # a plain mapping already (detached data, tests)
    return dict(raw) if raw is not None else {}


def corner_offset_of(data, side):
    value = data.get(CORNER_OFFSET_LEFT_KEY if side == 'left' else CORNER_OFFSET_RIGHT_KEY, (0, 0))
    try:
        x, y = float(value[0]), float(value[1])
        return (x, y) if math.isfinite(x) and math.isfinite(y) else (0.0, 0.0)
    except (TypeError, ValueError, IndexError, KeyError):
        return (0.0, 0.0)


def corner_handle_of(data, side, incoming):
    key = CORNER_HANDLE_KEYS[(0 if side == 'left' else 2) + (0 if incoming else 1)]
    value = data.get(key, (0, 0))
    try:
        x, y = float(value[0]), float(value[1])
        return (x, y) if math.isfinite(x) and math.isfinite(y) else (0.0, 0.0)
    except (TypeError, ValueError, IndexError, KeyError):
        return (0.0, 0.0)


def path_nodes(path):
    """[(node, type, (x, y), data)] for every node of a path, read once; `data`
    (see node_data) only for on-curve nodes, None for handles."""
    result = []
    for node in path.nodes:
        kind = node.type
        result.append((node, kind, xy(node), node_data(node) if kind != OFFCURVE else None))
    return result


def _on_curve(items, closed):
    """On-curve entries of path_nodes in segment order (segment i starts at i)."""
    first = next((i for i, item in enumerate(items) if item[1] != OFFCURVE), None)
    if first is None:
        return []
    ordered = items[first:] + (items[:first] if closed else [])
    return [item for item in ordered if item[1] != OFFCURVE]


def has_live_corners(items):
    return any(data is not None and corner_on_of(data) for _, _, _, data in items)


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


# Node values come in pairs: `name(node)` for single reads and `name_of(data)`
# working on node_data, for code that reads whole paths.

def scale_of(data):
    return _number(data.get(SCALE_KEY, data.get(HEIGHT_SCALE_KEY)), 100.0)


def height_scale_of(data):
    return _number(data.get(HEIGHT_SCALE_KEY, data.get(SCALE_KEY)), 100.0)


def scale(node):
    """Width percent; an unset width shares the height percent."""
    return scale_of(node_data(node))


def height_scale(node):
    """Height percent; an unset height shares the width percent."""
    return height_scale_of(node_data(node))


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


def offset_of(data):
    try:
        value = float(data.get(OFFSET_KEY, 0.0))
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def offset(node):
    """Where the centerline sits in the stroke at this node: -100 right .. 100 left."""
    return offset_of(node_data(node))


def rotation_of(data, default=0.0):
    try:
        return float(data.get(ROTATION_KEY, default)) % 180.0
    except (TypeError, ValueError):
        return default


def rotation(node, default=0.0):
    """Page angle of this node's ellipse axes, or its master's default."""
    return rotation_of(node_data(node), default)


def corner_on_of(data):
    return bool(data.get(CORNER_ON_KEY, CORNER_KEY in data))


def corner_on(node):
    return corner_on_of(node_data(node))


def _corner_value(data, key, default):
    try:
        value = data.get(key)
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def corner_radius(node):
    """Outer live-corner radius at a node, or None when its corners stay sharp."""
    data = node_data(node)
    if not corner_on_of(data):
        return None
    return max(0.0, _corner_value(data, CORNER_KEY, DEFAULT_CORNER_RADIUS))


def corner_spec_of(data):
    if not corner_on_of(data):
        return None
    outer = max(0.0, _corner_value(data, CORNER_KEY, DEFAULT_CORNER_RADIUS))
    tension = _corner_value(data, CORNER_TENSION_KEY, 100.0)
    ratio = _corner_value(data, CORNER_RATIO_KEY, 100.0)
    return {'outer': outer, 'inner': max(0.0, _corner_value(data, CORNER_INNER_KEY, outer)),
            'tension': tension,
            'inner_tension': _corner_value(data, CORNER_INNER_TENSION_KEY, tension),
            'ratio': ratio,
            'inner_ratio': _corner_value(data, CORNER_INNER_RATIO_KEY, ratio)}


def corner_spec(node):
    """The node's live corner for the outline code, or None when it is off."""
    return corner_spec_of(node_data(node))


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
            _corner_value(node_data(node), first, default))


def corner_side_key(node, kind, which):
    """Write the shared value while linked, or one side while independent."""
    first, second = _CORNER_PAIRS[kind]
    return second if which == 'inner' and not corner_linked(node, kind) else first


def _legacy_width(data):
    return WIDTH_KEY in data and SCALE_KEY not in data and HEIGHT_SCALE_KEY not in data


def _width_of(data, base):
    if _legacy_width(data):
        return max(1.0, _number(data.get(WIDTH_KEY), DEFAULT_WIDTH))
    return max(1.0, base * scale_of(data) / 100.0)


def width(node, path=None, base=None):
    """Effective stroke width at an on-curve node."""
    if base is None:
        base = stroke_width(path) if path is not None else DEFAULT_WIDTH
    return _width_of(node_data(node), base)


def nib_of(data, base_width, base_height, default_angle):
    w = _width_of(data, base_width)
    h = w * base_height / base_width if _legacy_width(data) else \
        base_height * height_scale_of(data) / 100.0
    return (w, max(1.0, h), offset_of(data) / 100.0, rotation_of(data, default_angle))


def node_nib(node, path, base_width, base_height, default_angle=None):
    """(width, height, offset fraction, rotation) at an on-curve node."""
    if default_angle is None:
        default_angle = layer_defaults(getattr(path, 'parent', None)).nib_angle
    return nib_of(node_data(node), base_width, base_height, default_angle)


def migrate_path(path):
    """Turn legacy absolute node widths into path width + node percentages."""
    legacy = [(node, data) for node, _, _, data in path_nodes(path)
              if data is not None and _legacy_width(data)]
    if not legacy:
        return False
    widths = [_number(data.get(WIDTH_KEY), DEFAULT_WIDTH) for _, data in legacy]
    legacy = [node for node, _ in legacy]
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
    position = node.position
    return (float(position.x), float(position.y))


def segments_for_path(path, defaults=None, items=None):
    """Centerline segments with a (width, height, offset) nib at each end.
    `items` is the path's path_nodes, when the caller has read them already."""
    items = path_nodes(path) if items is None else items
    first = next((i for i, item in enumerate(items) if item[1] != OFFCURVE), None)
    if first is None:
        return []
    defaults = _resolve(defaults, path)
    base_width = stroke_width(path, defaults)
    base_height = stroke_height(path, defaults)
    # (type, point, nib): every on-curve nib once, though it ends one segment
    # and starts the next.
    entries = [(kind, point, None if data is None else
                nib_of(data, base_width, base_height, defaults.nib_angle))
               for _, kind, point, data in items]
    if path.closed:
        entries = entries[first:] + entries[:first]
        run = entries[1:] + [entries[0]]
    else:
        entries = entries[first:]
        run = entries[1:]
    start = entries[0]
    controls = []
    result = []
    for entry in run:
        if entry[0] == OFFCURVE:
            controls.append(entry[1])
            continue
        if len(controls) == 0:
            result.append(('line', (start[1], entry[1]), start[2], entry[2]))
        elif len(controls) == 2 and entry[0] == CURVE:
            result.append(('cubic', (start[1], controls[0], controls[1], entry[1]),
                           start[2], entry[2]))
        else:
            raise ValueError('Only line and cubic path segments are supported')
        start = entry
        controls = []
    if controls:
        raise ValueError('Path ends with loose off-curve handles')
    return result


_OUTSIDE = object()  # beyond the ends of an open path (see _find_section)


def _find_section(data, anchor_ids, count, closed):
    """(segment, reversed) of a saved section among the path's anchors, or None.

    A reversed path finds the section with its two anchors swapped. Joining two
    strokes keeps one node where their ends met, so a section next to that end
    may have lost the anchor there; the anchor on its other side, saved with
    it, then tells which neighbour of the remaining anchor it now runs to."""
    total = len(anchor_ids)

    def at(index):
        if closed:
            return anchor_ids[index % total]
        return anchor_ids[index] if 0 <= index < total else _OUTSIDE
    start_id, end_id = data.get('anchorStart'), data.get('anchorEnd')
    for index in range(count):
        if (at(index), at(index+1)) == (start_id, end_id):
            return index, False
        if (at(index), at(index+1)) == (end_id, start_id):
            return index, True
    for found, lost, sign, open_key, outer_key in (
            (start_id, end_id, 1, 'endOpen', 'anchorBefore'),
            (end_id, start_id, -1, 'startOpen', 'anchorAfter')):
        if not data.get(open_key) or lost in anchor_ids or found not in anchor_ids:
            continue
        index = anchor_ids.index(found)
        outer = data.get(outer_key) or _OUTSIDE
        for step in (1, -1):
            if at(index-step) != outer or at(index+step) is _OUTSIDE:
                continue
            segment = (index if step == 1 else index-1) % total
            if 0 <= segment < count:
                return segment, step != sign
    return None


def _flip_sides(values):
    return {'left': values['right'], 'right': values['left']}


def _layer_of(path):
    try:
        return path.parent
    except AttributeError:
        return None


_BOOKKEEPING = [0]  # writes made under _without_undo (see virtual_nodes)


@contextlib.contextmanager
def _without_undo(layer):
    """Bookkeeping written while drawing (anchor ids, the layer's copies of the
    sections) must not become undo steps: undoing one would only have it
    written again by the next redraw, and undo would never get past it."""
    try:
        manager = layer.parent.undoManager()
    except Exception:
        manager = None
    if manager is not None:
        manager.disableUndoRegistration()
    _BOOKKEEPING[0] += 1
    try:
        yield
    finally:
        if manager is not None:
            manager.enableUndoRegistration()


def _kept_store(layer):
    """The layer's own object holding the kept sections (see _kept_sections)."""
    try:
        return layer.userData.get(VIRTUAL_STORE_KEY) if layer is not None else None
    except (AttributeError, TypeError):
        return None


def _kept_sections(layer, store=None):
    """{path id: JSON of its sections} kept on a layer (see virtual_nodes)."""
    if store is None:
        store = _kept_store(layer)
    try:
        return {str(key): str(value) for key, value in dict(store or {}).items()}
    except (AttributeError, TypeError, ValueError):
        return None


_PARSED_SECTIONS = collections.OrderedDict()  # kept JSON -> [(entry, start, end)]


def _parsed_sections(text):
    """Entries of one path's kept JSON, parsed once: every redraw of every path
    looks through the copies of all the layer's other paths."""
    parsed = _PARSED_SECTIONS.get(text)
    if parsed is None:
        try:
            parsed = [(dict(entry), entry.get('anchorStart'), entry.get('anchorEnd'))
                      for entry in json.loads(text)]
        except (TypeError, ValueError, AttributeError):
            parsed = []
        with _CACHE_LOCK:
            _PARSED_SECTIONS[text] = parsed
            while len(_PARSED_SECTIONS) > _CACHE_SIZE:
                _PARSED_SECTIONS.popitem(last=False)
    return parsed


def _joined_sections(path, layer, kept, owner, anchor_ids):
    """Sections kept for strokes joined into this path: those of a path id no
    path on the layer has any more, on a segment with one of this path's anchors."""
    anchors = set(anchor_id for anchor_id in anchor_ids if anchor_id)
    found = {}
    for key, text in kept.items():
        if key == owner:
            continue
        entries = [dict(entry) for entry, start, end in _parsed_sections(text)
                   if start in anchors or end in anchors]
        if entries:
            found[key] = entries
    if found:
        try:
            alive = set(str(other.attributes.get(VIRTUAL_OWNER_KEY))
                        for other in layer.paths if other is not path)
        except (AttributeError, TypeError):
            return {}
        found = {key: entries for key, entries in found.items() if key not in alive}
    return found


# Results of virtual_nodes by what they were read from: every redraw asks
# for each stroke's sections, also on the copies Glyphs prepares for every
# preview, and turning the stored values into Python (the path's attributes,
# the layer's kept copies) and normalizing them costs more than the outline
# it then finds cached. Their description, built by Foundation in one call,
# tells whether they changed.
_VIRTUAL_RESULTS = collections.OrderedDict()


def virtual_nodes(path, segment_count=None, items=None):
    """Normalized sections, following their original segment across node edits.

    The path's attributes hold the sections. When Glyphs joins two strokes only
    one path keeps its attributes, so the layer also keeps each path's sections
    by path id; a path takes over those of a path id gone from the layer whose
    anchor nodes it now has. (Not on the nodes: Glyphs writes node userData into
    a fixed-size buffer.)"""
    try:
        raw = path.attributes.get(VIRTUAL_KEY) or []
        owner = path.attributes.get(VIRTUAL_OWNER_KEY)
    except (AttributeError, TypeError):
        return []
    owner = str(owner) if owner else None
    if not raw and items is None:
        return []
    items = path_nodes(path) if items is None else items
    closed = bool(path.closed)
    on_curve_items = _on_curve(items, closed)
    anchor_ids = [item[3].get(VIRTUAL_ANCHOR_KEY) for item in on_curve_items]
    layer = _layer_of(path)
    if not raw and not any(anchor_ids):
        return []
    store = _kept_store(layer)
    key = (_described(raw), _described(store), owner, tuple(anchor_ids), segment_count,
           closed)
    cached = _VIRTUAL_RESULTS.get(key)
    if cached is not None:
        if PROFILE.enabled:
            PROFILE.add('    virtual nodes: reused', 0.0)
        return [dict(spec) for spec in cached]
    writes = _BOOKKEEPING[0]
    result = _virtual_nodes(path, raw, owner, items, closed, on_curve_items, anchor_ids,
                            layer, _kept_sections(layer, store), segment_count)
    if _BOOKKEEPING[0] != writes:
        # It bound anchors or kept a copy: the same values on another path
        # (a duplicate) need that too, so only a quiet read is reused.
        return result
    with _CACHE_LOCK:
        _VIRTUAL_RESULTS[key] = [dict(spec) for spec in result]
        while len(_VIRTUAL_RESULTS) > _CACHE_SIZE:
            _VIRTUAL_RESULTS.popitem(last=False)
    return result


def _described(value):
    """Text that changes with a stored value's contents (see _VIRTUAL_RESULTS)."""
    if value is None:
        return None
    try:
        return str(value.description())
    except AttributeError:  # a Python value (tests, values set in this session)
        return repr(value)


def _virtual_nodes(path, raw, owner, items, closed, on_curve_items, anchor_ids, layer, kept,
                   segment_count):
    saved = list(raw)
    changed = False
    adopted = set()
    joined = _joined_sections(path, layer, kept, owner, anchor_ids) if kept else {}
    if joined:
        known = set()
        for item in raw:
            try:
                known.add(str(dict(item).get('id', '')))
            except (TypeError, ValueError):
                continue
        for entries in joined.values():
            for entry in entries:
                if str(entry.get('id', '')) not in known:
                    known.add(str(entry.get('id', '')))
                    adopted.add(len(saved))
                    saved.append(entry)
                    changed = True
    if not saved:
        if kept and owner in kept:  # every section was deleted
            _keep_sections(path, layer, kept, owner, [], joined)
        return []
    if not owner:
        owner = uuid.uuid4().hex
        with _without_undo(layer):
            path.attributes[VIRTUAL_OWNER_KEY] = owner
    count = len(segments_for_path(path)) if segment_count is None else segment_count
    on_curve = [item[0] for item in on_curve_items]
    pairs = [(on_curve[i], on_curve[(i+1) % len(on_curve)]) for i in range(count)] \
        if len(on_curve) >= count + (0 if closed else 1) and on_curve else []
    result = []
    for raw_index, item in enumerate(saved):
        try:
            data = dict(item)
            segment, t = int(data['segment']), float(data['t'])
            if not (0.005 <= t <= 0.995 and math.isfinite(t)):
                continue
            reverse = False
            start_id, end_id = data.get('anchorStart'), data.get('anchorEnd')
            if start_id and end_id:
                match = _find_section(data, anchor_ids, count, closed) if pairs else None
                if match:
                    segment, reverse = match
                elif (raw_index in adopted or start_id in anchor_ids or
                      end_id in anchor_ids or data.get('anchorCount') != count):
                    # Its original segment was removed or split. Keep the saved
                    # entry so undo can restore the virtual node with the path.
                    continue
            elif raw_index in adopted:
                continue
            if not 0 <= segment < count:
                continue
            mode = data.get('mode', 'continuous')
            side = data.get('side', 'both')
            direction = data.get('direction', 'normal')
            if mode not in VIRTUAL_MODES or side not in ('left', 'right', 'both') \
                    or direction not in ('normal', 'horizontal', 'vertical', 'angle'):
                continue
            end_segment = end_t = None
            if mode == 'section':
                end_t = float(data['endT'])
                if not (0.005 <= end_t <= 0.995 and math.isfinite(end_t)):
                    continue
                span = int(data.get('endSegment', data['segment'])) - int(data['segment'])
                found = _find_section({'anchorStart': data.get('endAnchorStart'),
                                       'anchorEnd': data.get('endAnchorEnd')},
                                      anchor_ids, count, closed) \
                    if pairs and data.get('endAnchorStart') and data.get('endAnchorEnd') \
                    else None
                if found:
                    end_segment = found[0]
                    if found[1] != reverse:  # one end reversed, the other not
                        continue
                elif pairs and data.get('endAnchorEnd') and (
                        data.get('endAnchorStart') in anchor_ids or
                        data['endAnchorEnd'] in anchor_ids or
                        data.get('anchorCount') != count):
                    continue  # its end's segment was removed (kept for undo)
                else:
                    end_segment = segment - span if reverse else segment + span
                    end_segment = end_segment % count if closed else \
                        max(0, min(count-1, end_segment))
            before = dict(data.get('before') or {})
            after = dict(data.get('after') or before)
            for values in (before, after):
                for key in ('left', 'right'):
                    value = float(values.get(key, 100.0))
                    if not (0 < value < 10000 and math.isfinite(value)):
                        raise ValueError('Invalid virtual width')
                    values[key] = value
            angle = float(data.get('angle', 0.0))
            if not math.isfinite(angle):
                continue
            if reverse:  # the path now runs the other way along this segment
                t = 1.0-t
                side = {'left': 'right', 'right': 'left'}.get(side, side)
                before, after = (_flip_sides(after), _flip_sides(before)) \
                    if mode in ('step', 'whole') \
                    else (_flip_sides(before), _flip_sides(after))
                if mode == 'section':  # its end comes first now
                    segment, t, end_segment, end_t = end_segment, 1.0-end_t, segment, t
            if mode == 'section':
                if not closed and (end_segment, end_t) < (segment, t):
                    segment, t, end_segment, end_t = end_segment, end_t, segment, t
                if end_segment == segment and abs(end_t-t) < 0.002:
                    continue
                after = dict(before)
            spec = {'id': str(data.get('id', '')), 'segment': segment, 't': t,
                    'mode': mode, 'side': side, 'direction': direction,
                    'angle': angle, 'linked': bool(data.get('linked', True)),
                    'before': before, 'after': after,
                    'anchorStart': start_id, 'anchorEnd': end_id, 'anchorCount': count}
            if mode == 'section':
                spec.update(endSegment=end_segment, endT=end_t,
                            endAnchorStart=data.get('endAnchorStart'),
                            endAnchorEnd=data.get('endAnchorEnd'))
            if pairs:
                last = len(on_curve)-1
                neighbours = (segment-1, segment, segment+1, segment+2)
                if mode == 'section':
                    neighbours += (end_segment, end_segment+1)
                if not closed:
                    neighbours = tuple(i for i in neighbours if 0 <= i <= last)
                for index in neighbours:
                    index %= len(on_curve)
                    if not anchor_ids[index]:
                        anchor_ids[index] = uuid.uuid4().hex
                        with _without_undo(layer):
                            on_curve[index].userData[VIRTUAL_ANCHOR_KEY] = anchor_ids[index]
                first, second = segment, (segment+1) % len(on_curve)
                spec.update(anchorStart=anchor_ids[first], anchorEnd=anchor_ids[second],
                            startOpen=not closed and first == 0,
                            endOpen=not closed and second == last,
                            anchorBefore=anchor_ids[(first-1) % len(on_curve)]
                            if closed or first > 0 else None,
                            anchorAfter=anchor_ids[(second+1) % len(on_curve)]
                            if closed or second < last else None)
                if mode == 'section':
                    spec.update(endAnchorStart=anchor_ids[end_segment],
                                endAnchorEnd=anchor_ids[(end_segment+1) % len(on_curve)])
                if reverse or raw_index in adopted or any(
                        data.get(key) != spec.get(key) for key in (
                            'segment', 'anchorStart', 'anchorEnd', 'anchorCount',
                            'startOpen', 'endOpen', 'anchorBefore', 'anchorAfter',
                            'endSegment', 'endT', 'endAnchorStart', 'endAnchorEnd')):
                    saved[raw_index] = dict(spec)
                    changed = True
            result.append(spec)
        except (TypeError, ValueError, KeyError):
            continue
    if changed:
        with _without_undo(layer):
            path.attributes[VIRTUAL_KEY] = saved
    if pairs and layer is not None:
        _keep_sections(path, layer, kept, owner, result, joined)
    return sorted(result, key=lambda spec: (spec['segment'], spec['t'], spec['id']))


def _keep_sections(path, layer, kept, owner, specs, joined):
    """Save a path's sections on its layer, dropping those of joined paths."""
    kept = dict((_kept_sections(layer) if kept is None else kept) or {})
    updated = dict(kept)
    for key in joined:
        updated.pop(key, None)
    text = json.dumps(sorted(specs, key=lambda spec: spec['id']),
                      sort_keys=True) if specs else None
    if text is not None and kept.get(owner) not in (None, text):
        # A pasted or duplicated stroke carries the id of the one it was copied
        # from; sharing it, the two would overwrite each other's copy forever.
        try:
            shared = any(other is not path and
                         str(other.attributes.get(VIRTUAL_OWNER_KEY)) == owner
                         for other in layer.paths)
        except (AttributeError, TypeError):
            shared = False
        if shared:
            owner = uuid.uuid4().hex
            with _without_undo(layer):
                path.attributes[VIRTUAL_OWNER_KEY] = owner
    if text is not None:
        updated[owner] = text
    else:
        updated.pop(owner, None)
    if updated == kept:
        return
    try:
        with _without_undo(layer):
            if updated:
                layer.userData[VIRTUAL_STORE_KEY] = updated
            else:
                _pop(layer.userData, VIRTUAL_STORE_KEY)
    except (AttributeError, TypeError):
        pass


def virtual_point(segment, t):
    kind, pts = segment[:2]
    if kind == 'line':
        return (pts[0][0]*(1-t)+pts[1][0]*t, pts[0][1]*(1-t)+pts[1][1]*t)
    from variable_stroke_core import cubic
    return cubic(*pts, t)


def _split_segment(kind, pts, t):
    if kind == 'line':
        middle = virtual_point((kind, pts), t)
        return (pts[0], middle), (middle, pts[1])
    from variable_stroke_core import add, mul
    a, b, c = (add(mul(pts[i], 1-t), mul(pts[i+1], t)) for i in range(3))
    d, e = add(mul(a, 1-t), mul(b, t)), add(mul(b, 1-t), mul(c, t))
    middle = add(mul(d, 1-t), mul(e, t))
    return (pts[0], a, d, middle), (middle, e, c, pts[3])


def _virtual_section_angle(spec, tangent):
    direction = spec['direction']
    if direction == 'normal':
        return 0.0
    target = 0.0 if direction == 'horizontal' else 90.0 if direction == 'vertical' \
        else spec['angle']
    normal_angle = math.degrees(math.atan2(tangent[0], -tangent[1]))
    delta = (target-normal_angle+90.0) % 180.0-90.0
    return delta if abs(math.cos(math.radians(delta))) >= 0.25 else None


def _virtual_nib(base, percentages, section_angle):
    w, h, o, angle = base[:4]
    left = (1+o)*percentages['left']/100.0
    right = (1-o)*percentages['right']/100.0
    total = left+right
    if total <= 0.0001:
        raise ValueError('Virtual section reverses the outline')
    return (w*total/2.0, h*total/2.0, (left-right)/total, angle, section_angle)


_FULL = {'left': 100.0, 'right': 100.0}


def _side_factors(spec, values):
    """(left, right) factors of a spec's percentages, 1 on a side it leaves alone."""
    return tuple(values[side]/100.0 if spec['side'] in (side, 'both') else 1.0
                 for side in ('left', 'right'))


def _virtual_scale(specs, closed):
    """Factor (left, right) at a position (segment, t) from the nodes that scale
    everything after or before them, or a section of the stroke."""
    reach = []
    for spec in specs:
        start = (spec['segment'], spec['t'])
        if spec['mode'] == 'whole' and not closed:
            reach.append((lambda at, start=start: at > start,
                          _side_factors(spec, spec['after'])))
            reach.append((lambda at, start=start: at < start,
                          _side_factors(spec, spec['before'])))
        elif spec['mode'] == 'section':
            end = (spec['endSegment'], spec['endT'])
            if start < end:
                inside = (lambda at, start=start, end=end: start < at < end)
            else:  # around the start node of a closed path
                inside = (lambda at, start=start, end=end: at > start or at < end)
            reach.append((inside, _side_factors(spec, spec['before'])))

    def scale(segment, t):
        left = right = 1.0
        for inside, (l, r) in reach:
            if inside((segment, t)):
                left, right = left*l, right*r
        return left, right
    return scale


def _virtual_events(segments, specs, closed=False):
    """The cuts virtual nodes make in a stroke, in path order, with the nibs on
    both sides of each. A section makes two cuts (roles 'start' and 'end');
    the others one, with their mode as role ('step' for a whole step on a closed
    path, which has no ends to scale up to)."""
    from variable_stroke_core import _nib, _derivative, unit
    scale = _virtual_scale(specs, closed)
    events = []
    for spec in specs:
        role = spec['mode']
        if closed and role == 'whole':
            role = 'step'
        if role == 'section':
            events.append((spec['segment'], spec['t'], spec, 'start'))
            events.append((spec['endSegment'], spec['endT'], spec, 'end'))
        else:
            events.append((spec['segment'], spec['t'], spec, role))
    result = []
    for segment, t, spec, role in sorted(events, key=lambda item: item[:2]):
        kind, pts, e0, e1 = segments[segment][:4]
        base0, base1 = _nib(e0), _nib(e1)
        values = [base0[j]*(1-t)+base1[j]*t for j in range(3)]
        turn = (base1[3]-base0[3]+90.0) % 180.0-90.0
        base = tuple(values + [base0[3]+turn*t])
        tangent = unit(_derivative(kind, pts, t))
        axis = _virtual_section_angle(spec, tangent)
        if axis is None:
            axis = 0.0  # defensive fallback for an invalid saved direction
        local = spec.get('endScale' if role == 'end' else 'scale', 1.0)
        before_scale, after_scale = scale(segment, t-1e-9), scale(segment, t+1e-9)

        def nib(values, factors):
            return _virtual_nib(base, {side: values[side]*factor*local for side, factor
                                       in zip(('left', 'right'), factors)}, axis)
        before = nib(_FULL if role in ('whole', 'start', 'end') else spec['before'],
                     before_scale)
        after = nib(spec['after'] if role == 'step' else
                    spec['before'] if role == 'continuous' else _FULL, after_scale)
        result.append({'segment': segment, 't': t, 'spec': spec, 'role': role,
                       'base': nib(_FULL, before_scale), 'axis': axis,
                       'tangent': tangent, 'before': before, 'after': after})
    return result, scale


def _scaled_nib(nib, factors):
    if factors == (1.0, 1.0):
        return nib
    from variable_stroke_core import _nib
    base = _nib(nib)
    return _virtual_nib(base, {'left': 100.0*factors[0], 'right': 100.0*factors[1]},
                        base[4])


def expanded_virtual_segments(segments, specs, corners=None, closed=False):
    """Split source segments without changing the Glyphs path; return pieces,
    step breaks, expanded corner list, and original-node index for each vertex."""
    events, scale = _virtual_events(segments, specs, closed)
    by_segment = collections.defaultdict(list)
    for event in events:
        by_segment[event['segment']].append(event)
    result = []
    breaks = {'left': set(), 'right': set()}
    smooths, expanded_corners, node_map = set(), [], []
    for index, segment in enumerate(segments):
        kind, pts, e0, e1 = segment[:4]
        modulation = segment[4] if len(segment) > 4 else None
        if modulation is not None:
            table = _stroke_length_tables([segment])[0][0]

            def progress_at(t, table=table, modulation=modulation):
                share = _length_at(table, t)/table[-1] if table[-1] > 1e-9 else t
                return modulation[1] + (modulation[2]-modulation[1])*share

        def piece(points, nib0, nib1, t0, t1):
            if modulation is None:
                return (kind, points, nib0, nib1)
            return (kind, points, nib0, nib1,
                    (modulation[0], progress_at(t0), progress_at(t1)))
        current_pts, start_t = pts, 0.0
        current_nib = _scaled_nib(e0, scale(index, 0.0))
        for event in by_segment[index]:
            t = event['t']
            if t <= start_t + 0.001:
                continue
            first, current_pts = _split_segment(kind, current_pts,
                                                 (t-start_t)/(1-start_t))
            result.append(piece(first, current_nib, event['before'], start_t, t))
            expanded_corners.append(corners[index] if corners and start_t == 0 else None)
            node_map.append(index if start_t == 0 else None)
            if event['role'] != 'continuous':
                for side in ('left', 'right'):
                    if event['spec']['side'] in (side, 'both'):
                        breaks[side].add(len(result)-1)
            else:
                smooths.add(len(result)-1)
            current_nib, start_t = event['after'], t
        result.append(piece(current_pts, current_nib, _scaled_nib(e1, scale(index, 1.0)),
                            start_t, 1.0))
        expanded_corners.append(corners[index] if corners and start_t == 0 else None)
        node_map.append(index if start_t == 0 else None)
    if not closed:
        expanded_corners.append(corners[-1] if corners else None)
        node_map.append(len(segments))
    return result, breaks, smooths, expanded_corners, node_map


# Width profiles ------------------------------------------------------------
# A font's profiles are read from its userData once and kept here by font, as
# outlines are computed for every redraw. set_profiles() writes and refreshes.
_PROFILE_LIBRARIES = {}
# Every profile seen, by id (uuids, unique across fonts): instances and other
# interpolated layers belong to glyph copies that cannot reach their font.
_PROFILES_BY_ID = {}
_PROFILE_SEARCHED = set()  # ids looked for in every open font without success


def _font_key(font):
    try:
        import objc
        return objc.pyobjc_id(font)
    except Exception:
        return id(font)


def font_of(item):
    """The font a path, layer or glyph belongs to, or None."""
    for _ in range(4):
        if item is None:
            return None
        if hasattr(item, 'glyphs') and hasattr(item, 'masters'):
            return item
        item = getattr(item, 'parent', None)
    return None


def _plain(value):
    """Plist data from Glyphs (NSDictionary, NSArray, NSNumber) as plain Python."""
    if isinstance(value, str):
        return str(value)
    if hasattr(value, 'keys'):
        return {str(key): _plain(value[key]) for key in value.keys()}
    if isinstance(value, (list, tuple)) or hasattr(value, 'count') and \
            hasattr(value, 'objectAtIndex_'):
        return [_plain(item) for item in value]
    return value


def profiles(font):
    """{id: profile} of the font's width profiles (see width_profile)."""
    if font is None:
        return {}
    key = _font_key(font)
    library = _PROFILE_LIBRARIES.get(key)
    if library is None:
        library = {}
        try:
            raw = font.userData.get(FONT_PROFILES_KEY)
        except Exception:
            raw = None
        for profile_id, data in (_plain(raw) or {}).items():
            profile = width_profile.normalize(data)
            if profile is not None:
                library[str(profile_id)] = profile
        _PROFILE_LIBRARIES[key] = library
        _PROFILES_BY_ID.update(library)
    return library


def _saved_profile(profile):
    data = {'name': profile['name'], 'points': [list(point) for point in profile['points']]}
    if profile.get('divisions') is not None:
        data['divisions'] = profile['divisions']
    return data


def set_profiles(font, library):
    """Save the font's width profiles {id: profile}."""
    library = {str(key): width_profile.normalize(value) for key, value in library.items()}
    for removed in set(profiles(font)) - set(library):
        _PROFILES_BY_ID.pop(removed, None)
    font.userData[FONT_PROFILES_KEY] = {key: _saved_profile(value)
                                        for key, value in library.items()}
    _PROFILE_LIBRARIES[_font_key(font)] = library
    _PROFILES_BY_ID.update(library)
    _PROFILE_SEARCHED.clear()


def preview_profile(font, pid, profile):
    """Draw strokes with `profile` as `pid` until set_profiles or forget_profiles,
    without saving it (an editor drag in progress)."""
    library = dict(profiles(font))
    library[str(pid)] = width_profile.normalize(profile)
    _PROFILE_LIBRARIES[_font_key(font)] = library
    _PROFILES_BY_ID[str(pid)] = library[str(pid)]


def forget_profiles(font=None):
    if font is None:
        _PROFILE_LIBRARIES.clear()
        _PROFILES_BY_ID.clear()
    else:
        _PROFILE_LIBRARIES.pop(_font_key(font), None)
    _PROFILE_SEARCHED.clear()


def _profile_anywhere(pid):
    """A profile by id when the path's own font is out of reach."""
    profile = _PROFILES_BY_ID.get(pid)
    if profile is not None or pid in _PROFILE_SEARCHED:
        return profile
    try:
        from GlyphsApp import Glyphs
        fonts = list(Glyphs.fonts)
    except Exception:
        fonts = []
    for font in fonts:
        profiles(font)
    profile = _PROFILES_BY_ID.get(pid)
    if profile is None:
        _PROFILE_SEARCHED.add(pid)
    return profile


def profile_id(path):
    try:
        value = path.attributes.get(PROFILE_KEY)
    except (AttributeError, TypeError):
        return None
    return str(value) if value else None


def path_profile(path):
    """The width profile a stroke uses, or None. A profile id the font does not
    know (deleted) means none; the id stays so undo can bring it back."""
    pid = profile_id(path)
    if pid is None:
        return None
    try:
        data = path.attributes.get(PROFILE_DATA_KEY)
    except (AttributeError, TypeError):
        data = None
    if data:
        return width_profile.normalize(_plain(data))
    # Instances belong to glyph copies or interpolated fonts that may not reach
    # (or carry) the font's profiles: look the id up among all of them.
    font = font_of(path)
    profile = profiles(font).get(pid) if font is not None else None
    return profile if profile is not None else _profile_anywhere(pid)


def profile_name(path):
    profile = path_profile(path)
    return None if profile is None else (profile['name'] or '?')


def profile_users(font, pid):
    """Layers of the font with a stroke using profile `pid`."""
    result = []
    for glyph in list(font.glyphs):
        if not glyph_enabled(glyph):
            continue
        for layer in list(glyph.layers):
            if any(profile_id(path) == pid for path in list(layer.paths)):
                result.append(layer)
    return result


def profile_node_factors(path, items=None):
    """{index in path_nodes: thickness factor} of a stroke's on-curve nodes from
    its width profile (1.0 everywhere without one)."""
    items = path_nodes(path) if items is None else items
    on_curve = [index for index, item in enumerate(items) if item[1] != OFFCURVE]
    profile = path_profile(path)
    if profile is None:
        return {index: 1.0 for index in on_curve}
    segments = segments_for_path(path, items=items)
    if not segments:
        return {index: 1.0 for index in on_curve}
    _, totals = _stroke_length_tables(segments)
    closed = bool(path.closed)
    first = on_curve[0]
    ordered = on_curve[on_curve.index(first):]  # segments_for_path starts here
    result = {}
    for position, index in enumerate(ordered):
        progress = totals[position]/totals[-1] if totals[-1] > 1e-9 else \
            position/float(max(1, len(segments)))
        result[index] = width_profile.evaluate(profile, progress*100.0) / 100.0
    if closed and ordered:
        result[ordered[0]] = width_profile.evaluate(profile, 0.0) / 100.0
    return result


def apply_profile(segments, specs, profile, closed=False):
    """Bake a width profile into centerline segments and virtual specs.

    The profile is a factor over the stroke's length. Each node's nib is scaled
    by its value there, and every segment gets the same number of smooth
    sections (width_profile.divisions), placed evenly along its length, that
    carry the profile between nodes. The count depends on the profile alone, so
    masters keep compatible outlines whatever their segment lengths. Virtual
    nodes' percentages are relative to the straight blend of their segment's end
    nibs, so theirs and the new sections' are corrected for that blend.
    """
    if profile is None or not segments:
        return segments, specs
    tables, totals = _stroke_length_tables(segments)
    total = totals[-1]
    count = len(segments)

    def progress(index, distance):
        if total < 1e-9:
            return (index + (distance/tables[index][-1] if tables[index][-1] > 1e-9 else 0.0)) \
                / float(count)
        return (totals[index] + distance) / total

    def factor(value):
        return width_profile.evaluate(profile, value*100.0) / 100.0

    ends = [(factor(progress(index, 0.0)), factor(progress(index, tables[index][-1])))
            for index in range(count)]

    def scaled(nib, value):
        nib = tuple(nib) if isinstance(nib, (tuple, list)) else (nib,)
        width = nib[0] * value
        height = (nib[1] if len(nib) > 1 and nib[1] is not None else nib[0]) * value
        return (width, height) + nib[2:]

    flat = width_profile.is_flat(profile)
    key = tuple(tuple(point) for point in profile['points'])
    result = []
    for index, (kind, pts, e0, e1) in enumerate(segments):
        if kind == 'line' and not flat:
            # The profile bends a straight stroke's edges: fit them as curves.
            kind, pts = 'cubic', (pts[0], add(mul(pts[0], 2/3.0), mul(pts[1], 1/3.0)),
                                  add(mul(pts[0], 1/3.0), mul(pts[1], 2/3.0)), pts[1])
        segment = (kind, pts, scaled(e0, ends[index][0]), scaled(e1, ends[index][1]))
        if not flat:
            segment += ((key, progress(index, 0.0), progress(index, tables[index][-1])),)
        result.append(segment)

    def ratio(index, t):
        f0, f1 = ends[index]
        base = f0*(1-t) + f1*t
        return factor(progress(index, _length_at(tables[index], t))) / max(base, 1e-9)

    corrected = []
    for spec in specs:
        # A node that scales the stroke beyond itself keeps its percentages.
        spec = dict(spec, scale=spec.get('scale', 1.0)*ratio(spec['segment'], spec['t']))
        if spec['mode'] == 'section':
            spec['endScale'] = spec.get('endScale', 1.0)*ratio(spec['endSegment'],
                                                               spec['endT'])
        corrected.append(spec)
    sections = width_profile.divisions(profile)
    taken = collections.defaultdict(list)
    for spec in specs:
        taken[spec['segment']].append(spec['t'])
        if spec['mode'] == 'section':
            taken[spec['endSegment']].append(spec['endT'])
    for index in range(count):
        length = tables[index][-1]
        for j in range(1, sections+1):
            t = _t_at_length(tables[index], length*j/float(sections+1)) if length > 1e-9 \
                else j/float(sections+1)
            t = max(0.006, min(0.994, t))
            for other in taken[index]:  # a virtual node there would swallow it
                if abs(other - t) < 0.003:
                    t = other + (0.003 if t >= other else -0.003)
            percent = 100.0 * ratio(index, t)
            corrected.append({
                'id': '~profile-%d-%d' % (index, j), 'segment': index, 't': t,
                'mode': 'continuous', 'side': 'both', 'direction': 'normal',
                'angle': 0.0, 'linked': True,
                'before': {'left': percent, 'right': percent},
                'after': {'left': percent, 'right': percent}})
    corrected.sort(key=lambda spec: (spec['segment'], spec['t'], spec['id']))
    return result, corrected


def virtual_widgets(path, defaults=None, items=None):
    """Canvas positions for virtual sections, using the same nibs as expansion.

    A section has two widgets, at its start and end; their 'before' and 'after'
    edges are both those of the stretch it scales."""
    from variable_stroke_core import nib_edges, normal
    try:
        if not path.attributes.get(VIRTUAL_KEY):
            return []  # (sections of a joined stroke are taken over by the outline)
    except (AttributeError, TypeError):
        return []
    items = path_nodes(path) if items is None else items
    segments = segments_for_path(path, defaults, items)
    specs = virtual_nodes(path, len(segments), items)
    if not specs:
        return []
    # A width profile scales the stroke around each virtual node: its 100 % is
    # the profiled width there (see apply_profile).
    segments, profiled = apply_profile(segments, specs, path_profile(path), bool(path.closed))
    originals = {spec['id']: spec for spec in specs}
    events, _ = _virtual_events(segments, profiled, bool(path.closed))
    widgets = []
    for event in events:
        spec = originals.get(event['spec']['id'])
        if spec is None:  # a profile's own section
            continue
        kind, pts = segments[event['segment']][:2]
        center = virtual_point((kind, pts), event['t'])
        tangent, angle = event['tangent'], event['axis']
        before, after = event['before'], event['after']
        if event['role'] == 'start':
            before = after
        elif event['role'] == 'end':
            after = before
        baseline = nib_edges(center, tangent, event['base'])
        before_edges = nib_edges(center, tangent, before)
        after_edges = nib_edges(center, tangent, after)
        if spec['side'] == 'left':
            before_edges = (before_edges[0], baseline[1])
            after_edges = (after_edges[0], baseline[1])
        elif spec['side'] == 'right':
            before_edges = (baseline[0], before_edges[1])
            after_edges = (baseline[0], after_edges[1])
        axis = normal(tangent)
        radians = math.radians(angle)
        axis = (axis[0]*math.cos(radians)-axis[1]*math.sin(radians),
                axis[0]*math.sin(radians)+axis[1]*math.cos(radians))
        widgets.append({'spec': spec, 'role': event['role'], 'center': center,
                        'tangent': tangent, 'axis': axis, 'before': before_edges,
                        'after': after_edges, 'base': event['base']})
    return widgets


def _on_curve_nodes(path):
    """On-curve nodes in segment order (segment i starts at node i)."""
    return [item[0] for item in _on_curve(path_nodes(path), bool(path.closed))]


def selected_nib_nodes(path, selected, items=None):
    """On-curve nodes whose own point or attached Bézier handle is selected."""
    items = path_nodes(path) if items is None else items
    nodes = [item[0] for item in items]
    types = [item[1] for item in items]
    count = len(nodes)
    owners = set()
    for index, node in enumerate(nodes):
        if node not in selected:
            continue
        if types[index] != OFFCURVE:
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
                if types[candidate] != OFFCURVE:
                    owners.add(candidate)
                    break
            else:
                continue
            break
    return [node for index, node in enumerate(nodes) if index in owners]


def ellipse_cap_nodes(path, items=None):
    """Open-path end nodes whose cap is the oriented nib ellipse."""
    if path.closed:
        return []
    start = path.attributes.get(CAP_START_KEY) == 'ellipse'
    end = path.attributes.get(CAP_END_KEY) == 'ellipse'
    if not (start or end):
        return []
    on_curve = [item[0] for item in _on_curve(path_nodes(path) if items is None else items,
                                              False)]
    if not on_curve:
        return []
    result = []
    if start:
        result.append(on_curve[0])
    if end and on_curve[-1] not in result:
        result.append(on_curve[-1])
    return result


def edges_for_path(path, defaults=None, items=None):
    """[(node, (left, right))] for each on-curve node: where the outline really
    passes on both sides of it (miter/crossing points at corners). They come
    from the same (cached) computation as the outline Glyphs shows."""
    defaults = _resolve(defaults, path)
    items = path_nodes(path) if items is None else items
    closed = bool(path.closed)
    on_curve = [item[0] for item in _on_curve(items, closed)]
    if not on_curve:
        return []
    segments, cached, node_map = _outline(path, defaults, items)
    edges = cached[2]
    if node_map is not None:
        original_edges = {}
        for expanded_index, original_index in enumerate(node_map):
            if original_index is not None and expanded_index in edges:
                original_edges[original_index] = edges[expanded_index]
        edges = original_edges
    if segments and not closed:
        first_cap = path.attributes.get(CAP_START_KEY, 'flat')
        last_cap = path.attributes.get(CAP_END_KEY, 'flat')
        if first_cap == 'ellipse' or last_cap == 'ellipse':
            edges = dict(edges)  # keep the cached result intact
            if first_cap == 'ellipse':
                _, points, nib, _ = segments[0]
                edges[0] = ellipse_nib_edges(points[0], sub(points[1], points[0]), nib)
            if last_cap == 'ellipse':
                _, points, _, nib = segments[-1]
                edges[len(on_curve)-1] = ellipse_nib_edges(
                    points[-1], sub(points[-1], points[-2]), nib)
    return [(on_curve[i], pair) for i, pair in sorted(edges.items()) if i < len(on_curve)]


def corner_slide_direction(path, node, side, defaults=None, items=None):
    """Direction of the straight edge or Bézier tangent entering this corner."""
    defaults = _resolve(defaults, path)
    items = path_nodes(path) if items is None else items
    pair = next((pair for owner, pair in edges_for_path(path, defaults, items)
                 if owner == node), None)
    if pair is None:
        return None
    _, cached, _ = _outline(path, defaults, items)
    return outline_direction_at_vertex(cached[0], pair[0 if side == 'left' else 1],
                                       prefer_incoming=(side == 'left'))


def corner_bezier_controls(path, defaults=None, items=None):
    """Editable cubic controls adjacent to each generated outline corner."""
    defaults = _resolve(defaults, path)
    items = path_nodes(path) if items is None else items
    _, cached, _ = _outline(path, defaults, items)
    contours = cached[0]
    result = []
    for node, pair in edges_for_path(path, defaults, items):
        for side, corner in zip(('left', 'right'), pair):
            for contour in contours:
                for kind, points in contour:
                    if kind != 'cubic':
                        continue
                    for incoming, endpoint, handle in ((False, 0, 1), (True, -1, -2)):
                        if abs(points[endpoint][0]-corner[0]) < 1e-5 and \
                                abs(points[endpoint][1]-corner[1]) < 1e-5:
                            result.append({'node': node, 'side': side, 'incoming': incoming,
                                           'corner': corner, 'control': points[handle]})
    return result


def cut_angle(path, key):
    try:
        return float(path.attributes.get(key, DEFAULT_CUT_ANGLE))
    except (TypeError, ValueError):
        return DEFAULT_CUT_ANGLE


def cap_curve_of(path, at_end):
    """Two editable cap controls in the cap's width-relative frame."""
    key = CAP_END_CURVE_KEY if at_end else CAP_START_CURVE_KEY
    raw = path.attributes.get(key)
    try:
        values = tuple(tuple(float(value) for value in point) for point in raw)
        if len(values) == 2 and all(len(point) == 2 and
                                    all(math.isfinite(value) for value in point)
                                    for point in values):
            return values
    except (TypeError, ValueError):
        pass
    return DEFAULT_CAP_CURVE


def cap_curve_enabled(path, at_end):
    style = path.attributes.get(CAP_END_KEY if at_end else CAP_START_KEY, 'flat')
    if style == 'curve':  # paths made by the earlier standalone curve style
        return True
    return style not in ('round', 'ellipse') and bool(path.attributes.get(
        CAP_END_CURVE_ON_KEY if at_end else CAP_START_CURVE_ON_KEY, False))


def cap_curve_widget(path, at_end, defaults=None, items=None):
    """Visible cap controls at the actual outline endpoints."""
    from variable_stroke_core import _derivative, unit
    if path.closed or not cap_curve_enabled(path, at_end):
        return None
    items = path_nodes(path) if items is None else items
    segments = segments_for_path(path, defaults, items)
    nodes = _on_curve(items, False)
    if not segments or not nodes:
        return None
    kind, points, _, _ = segments[-1 if at_end else 0]
    tangent = unit(_derivative(kind, points, 1.0 if at_end else 0.0))
    node = nodes[-1 if at_end else 0][0]
    _, cached, _ = _outline(path, _resolve(defaults, path), items)
    controls = cached[4].get('end' if at_end else 'start')
    if controls is None:
        return None
    left, right = (controls[0], controls[-1]) if at_end else \
        (controls[-1], controls[0])
    return {'node': node, 'left': left, 'right': right,
            'tangent': tangent, 'points': controls, 'at_end': at_end}


def _stroke_length_tables(segments):
    """Cumulative centerline lengths, with samples for Bézier distance mapping."""
    from variable_stroke_core import cubic, length
    tables, totals = [], [0.0]
    for segment in segments:
        kind, points = segment[:2]
        samples = [points[0]] + [
            (points[0][0]*(1-i/32.0)+points[1][0]*i/32.0,
             points[0][1]*(1-i/32.0)+points[1][1]*i/32.0)
            if kind == 'line' else cubic(*points, i/32.0)
            for i in range(1, 33)]
        distances = [0.0]
        for before, after in zip(samples, samples[1:]):
            distances.append(distances[-1] + length(sub(after, before)))
        tables.append(distances)
        totals.append(totals[-1] + distances[-1])
    return tables, totals


def _length_at(table, t):
    position = max(0.0, min(32.0, t*32.0))
    index = min(31, int(position))
    fraction = position-index
    return table[index]*(1-fraction)+table[index+1]*fraction


def _t_at_length(table, distance):
    for index in range(32):
        if distance <= table[index+1] or index == 31:
            span = table[index+1]-table[index]
            return (index + (distance-table[index])/span)/32.0 if span > 1e-9 \
                else index/32.0
    return 1.0


def _node_progresses(totals, closed):
    count = len(totals)-1
    if totals[-1] < 1e-9:
        return [i/float(max(1, count)) for i in range(count if closed else count+1)]
    return [distance/totals[-1] for distance in totals[:count if closed else count+1]]


def _numeric_at_progress(samples, positions, progress, closed):
    if len(samples) == 1:
        return samples[0]
    if closed:
        positions = positions + [1.0]
        samples = samples + [samples[0]]
        progress %= 1.0
    for index in range(len(positions)-1):
        if progress <= positions[index+1] or index == len(positions)-2:
            span = positions[index+1]-positions[index]
            fraction = max(0.0, min(1.0, (progress-positions[index])/span)) \
                if span > 1e-9 else 0.0
            before, after = samples[index], samples[index+1]
            angle = _blend_angles(((before[3], 1-fraction), (after[3], fraction)))
            return tuple(before[i]*(1-fraction)+after[i]*fraction for i in range(3)) + (angle,)
    return samples[-1]


def _nearest_progress_index(positions, progress, closed):
    return min(range(len(positions)), key=lambda index: min(
        abs(positions[index]-progress), 1-abs(positions[index]-progress))
        if closed else abs(positions[index]-progress))


def _map_virtual_progress(progress, tables, totals):
    if totals[-1] < 1e-9:
        index = min(len(tables)-1, int(progress*len(tables)))
        return index, max(0.005, min(0.995, progress*len(tables)-index))
    distance = progress*totals[-1]
    index = next((i for i in range(len(tables)) if distance <= totals[i+1]),
                 len(tables)-1)
    t = _t_at_length(tables[index], distance-totals[index])
    return index, max(0.005, min(0.995, t))


def copy_stroke_settings(source, target):
    """Copy a stroke's appearance onto another centerline without changing its nodes."""
    import uuid
    source_items, target_items = path_nodes(source), path_nodes(target)
    source_segments = segments_for_path(source, items=source_items)
    target_segments = segments_for_path(target, items=target_items)
    source_nodes = _on_curve(source_items, bool(source.closed))
    target_nodes = _on_curve(target_items, bool(target.closed))
    if not source_segments or not target_segments or not source_nodes or not target_nodes:
        return False
    source_width, source_height = stroke_width(source), stroke_height(source)
    source_angle = layer_defaults(getattr(source, 'parent', None)).nib_angle
    source_tables, source_totals = _stroke_length_tables(source_segments)
    target_tables, target_totals = _stroke_length_tables(target_segments)
    source_positions = _node_progresses(source_totals, bool(source.closed))
    target_positions = _node_progresses(target_totals, bool(target.closed))
    matching_nodes = (len(source_nodes) == len(target_nodes) and
                      bool(source.closed) == bool(target.closed))
    matching_segments = (bool(source.closed) == bool(target.closed) and
                         [(kind, len(points)) for kind, points, _, _ in source_segments] ==
                         [(kind, len(points)) for kind, points, _, _ in target_segments])
    samples = [nib_of(item[3], source_width, source_height, source_angle)
               for item in source_nodes]
    source_specs = virtual_nodes(source, len(source_segments), source_items)
    virtuals = []

    def mapped(segment, t):
        if matching_segments:
            return segment, t
        distance = source_totals[segment] + _length_at(source_tables[segment], t)
        progress = distance/source_totals[-1] if source_totals[-1] > 1e-9 else \
            (segment+t)/len(source_segments)
        return _map_virtual_progress(progress, target_tables, target_totals)
    for spec in source_specs:
        segment, t = mapped(spec['segment'], spec['t'])
        copied = {key: value for key, value in spec.items()
                  if not key.startswith(('anchor', 'endAnchor'))}
        copied.update(id=uuid.uuid4().hex, segment=segment, t=round(t, 6),
                      before=dict(spec['before']), after=dict(spec['after']))
        if spec['mode'] == 'section':
            end_segment, end_t = mapped(spec['endSegment'], spec['endT'])
            copied.update(endSegment=end_segment, endT=round(end_t, 6))
        virtuals.append(copied)
    attributes = target.attributes
    attributes[STROKE_WIDTH_KEY] = source_width
    attributes[STROKE_HEIGHT_KEY] = source_height
    for key in (CAP_START_KEY, CAP_END_KEY, CAP_START_ANGLE_KEY, CAP_END_ANGLE_KEY,
                CAP_START_CURVE_ON_KEY, CAP_END_CURVE_ON_KEY):
        if key in source.attributes:
            attributes[key] = source.attributes[key]
        else:
            _pop(attributes, key)
    for at_end, key in ((False, CAP_START_CURVE_KEY), (True, CAP_END_CURVE_KEY)):
        if key in source.attributes:
            attributes[key] = [list(point) for point in cap_curve_of(source, at_end)]
        else:
            _pop(attributes, key)
    attributes[VIRTUAL_KEY] = virtuals
    if profile_id(source):
        attributes[PROFILE_KEY] = profile_id(source)
    else:
        _pop(attributes, PROFILE_KEY)
    corner_keys = (CORNER_ON_KEY, CORNER_KEY, CORNER_INNER_KEY,
                   CORNER_TENSION_KEY, CORNER_INNER_TENSION_KEY,
                   CORNER_RATIO_KEY, CORNER_INNER_RATIO_KEY)
    for index, (position, item) in enumerate(zip(target_positions, target_nodes)):
        node = item[0]
        width, height, offset_value, rotation_value = samples[index] if matching_nodes else \
            _numeric_at_progress(samples, source_positions, position, bool(source.closed))
        data = node.userData
        _pop(data, WIDTH_KEY)
        data[SCALE_KEY] = round(width/source_width*100.0, 4)
        data[HEIGHT_SCALE_KEY] = round(height/source_height*100.0, 4)
        data[OFFSET_KEY] = round(offset_value*100.0, 4)
        data[ROTATION_KEY] = round(rotation_value, 4)
        for key in corner_keys:
            _pop(data, key)
        source_index = index if matching_nodes else _nearest_progress_index(
            source_positions, position, bool(source.closed))
        source_data = source_nodes[source_index][3]
        for side, key in (('left', CORNER_OFFSET_LEFT_KEY), ('right', CORNER_OFFSET_RIGHT_KEY)):
            if key in source_data:
                data[key] = list(corner_offset_of(source_data, side))
            else:
                _pop(data, key)
        for side in ('left', 'right'):
            for incoming in (True, False):
                key = CORNER_HANDLE_KEYS[(0 if side == 'left' else 2) + (0 if incoming else 1)]
                if key in source_data:
                    data[key] = list(corner_handle_of(source_data, side, incoming))
                else:
                    _pop(data, key)
        for key in corner_keys:
            if key in source_data:
                data[key] = source_data[key]
    if any(corner_on_of(item[3]) for item in source_nodes):
        note_corner(target)
    if virtuals:
        virtual_nodes(target, len(target_segments), target_items)
    return True


def _index_of(items, item):
    """Position of `item` in `items`, by identity first: == on Glyphs objects
    sends isEqual:, which compares whole paths and layers."""
    for i, other in enumerate(items):
        if other is item:
            return i
    for i, other in enumerate(items):
        if other == item:
            return i
    raise StopIteration


def _sibling_paths(path, count):
    """On-curve path_nodes of the same path in the glyph's other layers (masters,
    brace layers) with `count` on-curve nodes: needed so every layer rounds the
    same corners."""
    layer = getattr(path, 'parent', None)
    glyph = getattr(layer, 'parent', None) if layer is not None else None
    if glyph is None:
        return []
    try:
        paths = list(layer.paths)
        index = _index_of(paths, path)
        layers = list(glyph.layers)
    except Exception:
        return []
    layer_id = getattr(layer, 'layerId', None)
    result = []
    for other_layer in layers:
        if other_layer is layer or (layer_id is not None and
                                    getattr(other_layer, 'layerId', None) == layer_id):
            continue
        other_paths = list(other_layer.paths)
        if index < len(other_paths):
            other = other_paths[index]
            on_curve = _on_curve(path_nodes(other), bool(other.closed))
            if len(on_curve) == count:
                result.append(on_curve)
    return result


def _glyph_of(item):
    """The glyph a path or node belongs to, or None."""
    while item is not None and not hasattr(item, 'layers'):
        item = getattr(item, 'parent', None)
    return item


def _glyph_has_corners(path):
    # A path's glyph is its layer's parent: walking up with hasattr() (see
    # _glyph_of) asks the Objective-C runtime for a missing method each step.
    layer = getattr(path, 'parent', None)
    glyph = getattr(layer, 'parent', None) if layer is not None else None
    if glyph is None:
        return False
    try:
        data = glyph.pyobjc_instanceMethods.userData()
        return bool(data is not None and data.objectForKey_(GLYPH_CORNERS_KEY))
    except AttributeError:
        pass  # plain objects (tests)
    except Exception:
        return False
    try:
        return bool(glyph.userData.get(GLYPH_CORNERS_KEY))
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


# On-curve indices with a live corner in another layer, by (glyph, last
# change, layer, path index, count): reading every master's nodes again for
# each stroke of each redraw was most of preparing a master with live corners.
_ROUNDED_ELSEWHERE = collections.OrderedDict()
_ROUNDED_ELSEWHERE_SIZE = 4096


def _rounded_elsewhere(path, count):
    key = None
    with PROFILE.section('    corners: parents'):
        layer = getattr(path, 'parent', None)
        glyph = getattr(layer, 'parent', None) if layer is not None else None
    try:
        with PROFILE.section('    corners: lastChange'):
            change = glyph.lastChange
        if change is not None:
            with PROFILE.section('    corners: path index'):
                index = _index_of(list(layer.paths), path)
            with PROFILE.section('    corners: key'):
                key = (str(glyph.name), str(change), str(layer.layerId), index, count)
    except Exception:
        key = None  # plain objects (tests), detached copies: read every time
    if key is not None:
        with _CACHE_LOCK:
            if key in _ROUNDED_ELSEWHERE:
                _ROUNDED_ELSEWHERE.move_to_end(key)
                return _ROUNDED_ELSEWHERE[key]
    with PROFILE.section('corners in other masters'):
        result = frozenset(k for sibling in _sibling_paths(path, count)
                           for k, (_, _, _, data) in enumerate(sibling)
                           if corner_on_of(data))
    if key is not None:
        _remember(_ROUNDED_ELSEWHERE, _ROUNDED_ELSEWHERE_SIZE, key, result)
    return result


def corner_specs(path, items=None):
    """Live corner per on-curve node. A corner that is on in any other master of
    the glyph gets a zero-size arc here, so masters keep compatible outlines while
    each master switches its corners on or off on its own."""
    items = path_nodes(path) if items is None else items
    with PROFILE.section('    corners: own nodes'):
        specs = [corner_spec_of(data) for _, _, _, data in _on_curve(items, bool(path.closed))]
    if all(spec is not None for spec in specs):
        return specs
    with PROFILE.section('    corners: glyph flag'):
        has_corners = _glyph_has_corners(path)
    if not has_corners:
        return specs
    rounded_elsewhere = _rounded_elsewhere(path, len(specs))
    return [spec if spec is not None or k not in rounded_elsewhere else dict(ZERO_CORNER)
            for k, spec in enumerate(specs)]


def corner_widgets(path, defaults=None, items=None):
    """One dict per rounded outline corner (see outline_curves' report), with
    'node' set to the GSNode it belongs to."""
    items = path_nodes(path) if items is None else items
    report = []
    try:
        curves_for_path(path, defaults, report, items=items)
    except ValueError:
        return []
    on_curve = [item[0] for item in _on_curve(items, bool(path.closed))]
    if not on_curve:
        return []
    _, _, node_map = _outline(path, _resolve(defaults, path), items)
    for widget in report:
        original = (node_map[widget['node'] % len(node_map)]
                    if node_map is not None else widget['node'])
        if original is not None:
            widget['node'] = on_curve[original % len(on_curve)]
    report = [widget for widget in report if not isinstance(widget['node'], int)]
    return report


def _outline(path, defaults, items):
    """(segments, cached geometry, original-node map) of a path."""
    with PROFILE.section('outline inputs (segments, corners)'):
        with PROFILE.section('  inputs: segments'):
            source_segments = segments_for_path(path, defaults, items)
        with PROFILE.section('  inputs: path attributes'):
            closed = bool(path.closed)
            attributes = path.attributes
            caps = (attributes.get(CAP_START_KEY, 'flat'), attributes.get(CAP_END_KEY, 'flat'))
            angles = (cut_angle(path, CAP_START_ANGLE_KEY), cut_angle(path, CAP_END_ANGLE_KEY))
            cap_curves = (cap_curve_of(path, False) if cap_curve_enabled(path, False) else None,
                          cap_curve_of(path, True) if cap_curve_enabled(path, True) else None)
        with PROFILE.section('  inputs: virtual nodes'):
            specs = virtual_nodes(path, len(source_segments), items)
        with PROFILE.section('  inputs: width profile'):
            source_segments, specs = apply_profile(source_segments, specs,
                                                   path_profile(path), bool(path.closed))
        with PROFILE.section('  inputs: corners'):
            source_corners = corner_specs(path, items)
        source_offsets = [(corner_offset_of(data, 'left'), corner_offset_of(data, 'right'))
                          for _, _, _, data in _on_curve(items, closed)]
        source_handles = [tuple(corner_handle_of(data, side, incoming)
                                for side in ('left', 'right') for incoming in (True, False))
                          for _, _, _, data in _on_curve(items, closed)]
        independent = any(spec['side'] != 'both' for spec in specs)
        if independent:
            left_specs = [spec for spec in specs if spec['side'] in ('left', 'both')]
            right_specs = [spec for spec in specs if spec['side'] in ('right', 'both')]
            segments, left_breaks, left_smooths, corners, left_map = \
                expanded_virtual_segments(source_segments, left_specs, source_corners, closed)
            right_segments, right_breaks, right_smooths, _, right_map = \
                expanded_virtual_segments(source_segments, right_specs, source_corners, closed)
            breaks = {'left': left_breaks['left'], 'right': right_breaks['right']}
            smooths = left_smooths
            node_map = None
            right_segments = tuple(right_segments)
        else:
            segments, breaks, smooths, corners, node_map = expanded_virtual_segments(
                source_segments, specs, source_corners, closed)
            right_segments = right_smooths = right_map = left_map = None
        segments = tuple(segments)
        key = ('curves', segments, right_segments, closed, caps, angles, cap_curves,
               defaults.italic_angle, tuple(sorted(breaks['left'])),
               tuple(sorted(breaks['right'])), tuple(sorted(smooths)),
               tuple(sorted(right_smooths)) if independent else (),
               tuple(left_map) if independent else (),
               tuple(right_map) if independent else (),
               tuple(None if c is None else tuple(sorted(c.items())) for c in corners),
               tuple(source_offsets), tuple(source_handles))

    def compute():
        collected, edges, cap_points = [], {}, {}
        contours = outline_curves(list(segments), closed, caps[0], caps[1],
                                  italic_angle=defaults.italic_angle,
                                  start_angle=angles[0], end_angle=angles[1],
                                  corner_radii=corners, report=collected, edges=edges,
                                  breaks=breaks, smooth_virtuals=smooths,
                                  right_segments=right_segments,
                                  right_corner_radii=source_corners if independent else None,
                                  left_map=left_map, right_map=right_map,
                                  right_smooth_virtuals=right_smooths if independent else (),
                                  cap_start_curve=cap_curves[0],
                                  cap_end_curve=cap_curves[1], cap_out=cap_points)
        if any(delta != (0.0, 0.0) for pair in source_offsets for delta in pair) or \
                any(delta != (0.0, 0.0) for handles in source_handles for delta in handles):
            if independent:
                # Independent sides report their edges by original node index.
                offsets = dict(enumerate(source_offsets))
                handles = dict(enumerate(source_handles))
            else:
                offsets = {expanded_index: source_offsets[original_index]
                           for expanded_index, original_index in enumerate(node_map)
                           if original_index is not None and original_index < len(source_offsets)}
                handles = {expanded_index: source_handles[original_index]
                           for expanded_index, original_index in enumerate(node_map)
                           if original_index is not None and original_index < len(source_handles)}
            contours = move_outline_vertices(contours, edges, offsets, handles)
        # The last slot keeps the outline as GSPaths once built (see _outline_paths).
        return [contours, collected, edges, None, cap_points]

    return segments, _cached(key, compute), node_map


def curves_for_path(path, defaults=None, report=None, items=None):
    defaults = _resolve(defaults, path)
    items = path_nodes(path) if items is None else items
    _, cached, _ = _outline(path, defaults, items)
    contours, collected = cached[:2]
    if report is not None:
        report.extend(dict(widget) for widget in collected)  # callers annotate them
    return contours


def _outline_paths(path, defaults, items):
    """GSPaths of a centerline's outline. Glyphs prepares the whole layer again
    for every mouse event while a node moves, so the outline of each unchanged
    stroke is built once and handed out as copies (one call instead of one
    GSNode per point)."""
    _, cached, _ = _outline(path, _resolve(defaults, path), items)
    templates = cached[3]
    if templates is None:
        with PROFILE.section('build outline GSPaths'):
            templates = cached[3] = generated_paths(path, contours=cached[0])
    if not all(hasattr(template, 'copy') for template in templates):
        return generated_paths(path, contours=cached[0])  # plain Python paths (tests)
    with PROFILE.section('copy outline GSPaths'):
        copies = [template.copy() for template in templates]
        for copied in copies:
            if not is_outline(copied):
                copied.attributes[OUTLINE_KEY] = True
    return copies


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


def _valid_structure(path, types=None):
    """Only lines and cubics (two off-curves before a curve node): what
    segments_for_path accepts, checked without computing any widths."""
    if types is None:
        types = [node.type for node in path.nodes]
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
            if any(has_live_corners(path_nodes(path)) for path in paths):
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


def _expansion_plan(layer, glyph_on=None, defaults=None, blends=False):
    if defaults is None:
        defaults = layer_defaults(layer)
    if glyph_on is False:
        return []
    plan = []
    for path in [shape for shape in list(layer.shapes) if isinstance(shape, GSPath)]:
        if generated(path) or is_outline(path) or not (glyph_on or enabled(path)):
            continue
        with PROFILE.section('read nodes (prepare)'):
            items = path_nodes(path)  # read once for the check and the outline
        if not items or not _valid_structure(path, [item[1] for item in items]):
            continue
        if blends:
            with PROFILE.section('look up blended outline'):
                contours = _blended_outline(path, items, defaults)
            if contours is not None:
                with PROFILE.section('build outline GSPaths'):
                    plan.append((path, generated_paths(path, contours=contours)))
                continue
        try:
            plan.append((path, _outline_paths(path, defaults, items)))
        except ValueError:
            continue
    return plan


def _apply_plan(layer, plan):
    for path, replacements in plan:
        layer.shapes.remove(path)
        for replacement in replacements:
            layer.shapes.append(replacement)
    return len(plan)


def expand_layer(layer, glyph_on=None, defaults=None, blends=False):
    """Replace every centerline in `layer` by its Bézier outline.

    Glyphs calls this on the throwaway copy it prepares for preview, inactive
    glyphs and metrics (GSPrepareLayerCallback), and the export filter calls it
    on the export copy. The editable layer keeps only the centerlines.

    `glyph_on` is the glyph's ON/OFF state when known; it wins over the per-path
    flag, so new paths count at once and pasted paths in an OFF glyph do not.
    `defaults` (StrokeDefaults) carries the master's default width/height and
    italic angle; pass it when the layer is a detached copy that cannot find its
    master.

    `blends`: the layer is an interpolated instance (not a master, brace or
    bracket layer); strokes whose master outlines were blended when Glyphs
    interpolated it (see interpolate_layer) take that blend.
    """
    return _apply_plan(layer, _expansion_plan(layer, glyph_on, defaults, blends))


class _RecordedNode(object):
    __slots__ = ('type', 'userData')

    def __init__(self, kind, data):
        self.type, self.userData = kind, data


class _RecordedPath(object):
    def __init__(self, path):
        self.attributes = dict(path.attributes or {})
        self.nodes = [_RecordedNode(kind, dict(data) if data is not None else {})
                      for _, kind, _, data in path_nodes(path)]


class _RecordedLayer(object):
    """Stands in for an interpolated layer while interpolate_layer works, so the
    result can be written in one pass and reused (see _INTERPOLATIONS)."""
    def __init__(self, layer):
        self.userData = {}
        self.paths = [_RecordedPath(path) for path in layer.paths]
        self._original = [(dict(path.attributes),
                           [dict(node.userData) for node in path.nodes])
                          for path in self.paths]

    def shape(self):
        return tuple(len(path.nodes) for path in self.paths)

    def changes(self):
        paths = []
        for path, (attributes, nodes) in zip(self.paths, self._original):
            set_attributes = {key: value for key, value in path.attributes.items()
                              if attributes.get(key, _MISSING) != value}
            node_changes = []
            for position, (node, before) in enumerate(zip(path.nodes, nodes)):
                updates = {key: value for key, value in node.userData.items()
                           if before.get(key, _MISSING) != value}
                removed = [key for key in before if key not in node.userData]
                if updates or removed:
                    node_changes.append((position, updates, removed, dict(node.userData)))
            paths.append((set_attributes, node_changes))
        return dict(self.userData), paths


_MISSING = object()
# Interpolations of unchanged glyphs at the same position come back on every
# redraw of instance previews (Variable Font Preview, the preview area); the
# writes they need are kept by the caller's key (glyph, last change, location).
_INTERPOLATIONS = collections.OrderedDict()
_INTERPOLATIONS_SIZE = 512


def _apply_interpolation(layer, recorded):
    layer_data, paths = recorded
    for key, value in layer_data.items():
        layer.userData[key] = value
    for path, (attributes, node_changes) in zip(list(layer.paths), paths):
        for key, value in attributes.items():
            path.attributes[key] = value
        if node_changes:
            nodes = list(path.nodes)
            for position, updates, removed, final in node_changes:
                if _replace_user_data(nodes[position], final):
                    continue
                data = nodes[position].userData
                for key, value in updates.items():
                    data[key] = value
                for key in removed:
                    if key in data:
                        del data[key]


_SET_USER_DATA = []  # [NSMutableDictionary class] once GSNode accepts setUserData:


def _replace_user_data(node, data):
    """Write a node's whole userData in one call: an interpolated node gets some
    ten values, and Glyphs takes each key as a separate bridge call otherwise.
    False for plain objects (tests) and wherever the call is not available."""
    if _SET_USER_DATA == [None]:
        return False
    try:
        methods = node.pyobjc_instanceMethods
    except AttributeError:
        return False
    try:
        if not _SET_USER_DATA:
            from Foundation import NSMutableDictionary
            if not methods.respondsToSelector_('setUserData:'):
                _SET_USER_DATA.append(None)
                return False
            _SET_USER_DATA.append(NSMutableDictionary)
        methods.setUserData_(_SET_USER_DATA[0].dictionaryWithDictionary_(data))
        return True
    except Exception:
        if not _SET_USER_DATA:
            _SET_USER_DATA.append(None)
        return False


# Outlines of instances. The masters' outlines have a fixed structure, so an
# instance is drawn as their point-wise blend: what the exported fonts contain
# (PreInterpolationFilter expands the masters before interpolation), and no
# outline geometry per slider step of Variable Font Preview. _SOURCE_OUTLINES
# holds each master path's outline and values by (glyph, last change, layer,
# path index). interpolate_layer gives each blended path a token (BLEND_KEY)
# naming its entry in _BLENDS, together with the positions and values the blend
# was made for, so a layer that differs from them computes its own outline.
_SOURCE_OUTLINES = collections.OrderedDict()
_SOURCE_OUTLINES_SIZE = 4096
_BLENDS = collections.OrderedDict()
_BLENDS_SIZE = 4096
# Interpolated values a blend is checked against (all plain numbers).
_BLEND_CHECKED = (SCALE_KEY, HEIGHT_SCALE_KEY, OFFSET_KEY, ROTATION_KEY, CORNER_KEY,
                  CORNER_INNER_KEY)
_BLEND_TOLERANCE = 0.51  # Glyphs may round interpolated coordinates


def _remember(table, size, key, value):
    with _CACHE_LOCK:
        table[key] = value
        table.move_to_end(key)
        while len(table) > size:
            table.popitem(last=False)


def _number_or_none(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _blend_check(nodes):
    """[(kind, (x, y), data)] -> what a layer must still have to use a blend:
    positions and the on-curve values interpolation wrote."""
    return [(point, None if kind == OFFCURVE else
             tuple(_number_or_none(data.get(key)) for key in _BLEND_CHECKED))
            for kind, point, data in nodes]


def _blend_mismatch(expected, actual):
    """None when a layer still has what its blend was made for, else the first
    difference ('node count', 'position', 'node type' or a userData key)."""
    if len(expected) != len(actual):
        return 'node count'
    for (point, values), (other_point, other_values) in zip(expected, actual):
        if abs(point[0] - other_point[0]) > _BLEND_TOLERANCE or \
                abs(point[1] - other_point[1]) > _BLEND_TOLERANCE:
            return 'position'
        if values == other_values:
            continue
        if values is None or other_values is None:
            return 'node type'
        for key, a, b in zip(_BLEND_CHECKED, values, other_values):
            # Glyphs keeps userData numbers at a lower precision than Python.
            if (a is None) != (b is None) or (a is not None and
                                              abs(a - b) > 1e-4 * max(1.0, abs(a), abs(b))):
                return key.rsplit('.', 1)[-1]
    return None


def _blended_outline(path, items, defaults=None):
    """The blended master outline made for this centerline, or None."""
    try:
        token = path.attributes.get(BLEND_KEY)
    except Exception:
        token = None
    if not token:
        if PROFILE.enabled:
            PROFILE.add('blend skipped: no token', 0.0)
        return None
    token = str(token)
    with _CACHE_LOCK:
        entry = _BLENDS.get(token)
        if entry is not None:
            _BLENDS.move_to_end(token)
    if entry is None:
        if PROFILE.enabled:
            PROFILE.add('blend skipped: token unknown', 0.0)
        return None
    expected, contours = entry
    mismatch = _blend_mismatch(expected, _blend_check(
        [(kind, point, data) for _, kind, point, data in items]))
    if mismatch is not None:
        if PROFILE.enabled:
            PROFILE.add('blend skipped: %s differs' % mismatch, 0.0)
        return None
    return contours


def _source_outline(path, defaults, key=None):
    """Outline contours of a master's path as its export expands it, or None."""
    if key is not None:
        with _CACHE_LOCK:
            if key in _SOURCE_OUTLINES:
                _SOURCE_OUTLINES.move_to_end(key)
                return _SOURCE_OUTLINES[key]
    contours = None
    if not generated(path) and not is_outline(path):
        items = path_nodes(path)
        if items and _valid_structure(path, [item[1] for item in items]):
            try:
                contours = curves_for_path(path, defaults, items=items)
            except ValueError:
                contours = None
    if key is not None:
        _remember(_SOURCE_OUTLINES, _SOURCE_OUTLINES_SIZE, key, contours)
    return contours


def _source_values(path, defaults, key=None):
    """A master path's stroke values as interpolation blends them, read from
    Glyphs once per edit of the glyph (by `key`) instead of once per instance:
    {'width', 'height', 'nodes': {position: (w, h, offset, rotation, corner
    spec or None, left offset, right offset, 4 corner handles)}}."""
    if key is not None:
        key = ('values',) + tuple(key)
        with _CACHE_LOCK:
            if key in _SOURCE_OUTLINES:
                _SOURCE_OUTLINES.move_to_end(key)
                return _SOURCE_OUTLINES[key]
    base_w = stroke_width(path, defaults)
    base_h = stroke_height(path, defaults)
    nodes = {}
    for position, (_, kind, _, data) in enumerate(path_nodes(path)):
        if kind == OFFCURVE:
            continue
        nib = nib_of(data, base_w, base_h, defaults.nib_angle)
        nodes[position] = (
            _width_of(data, base_w), nib[1], offset_of(data),
            rotation_of(data, defaults.nib_angle), corner_spec_of(data),
            corner_offset_of(data, 'left'), corner_offset_of(data, 'right'),
            corner_handle_of(data, 'left', True), corner_handle_of(data, 'left', False),
            corner_handle_of(data, 'right', True), corner_handle_of(data, 'right', False))
    values = {'width': base_w, 'height': base_h, 'nodes': nodes}
    if key is not None:
        _remember(_SOURCE_OUTLINES, _SOURCE_OUTLINES_SIZE, key, values)
    return values


def _blend_contours(weighted):
    """Point-wise blend of [(contours, factor)], or None when the contours do
    not share one structure (contour count, segment kinds)."""
    first = weighted[0][0]
    for contours, _ in weighted[1:]:
        if len(contours) != len(first) or any(
                [kind for kind, _ in a] != [kind for kind, _ in b]
                for a, b in zip(contours, first)):
            return None
    result = []
    for index, contour in enumerate(first):
        blended = []
        for position, (kind, points) in enumerate(contour):
            sources = [(contours[index][position][1], factor) for contours, factor in weighted]
            blended.append((kind, tuple(
                (sum(factor * pts[k][0] for pts, factor in sources),
                 sum(factor * pts[k][1] for pts, factor in sources))
                for k in range(len(points)))))
        result.append(blended)
    return result


def forget_interpolations():
    """Master settings changed (default width, angle): recompute everything."""
    with _CACHE_LOCK:
        _INTERPOLATIONS.clear()
        _SOURCE_OUTLINES.clear()
        _BLENDS.clear()


def interpolate_layer(layer, glyph, interpolation, cache_key=None, glyph_key=None):
    """Carry the strokes over to `layer` (see _interpolate_layer) and keep the
    blended master outlines for its strokes (see _BLENDS). With `cache_key`, a
    result computed for the same key and path structure is written again
    without reading the sources. `glyph_key` names the glyph's current state
    (identity, last change) so the masters' outlines are read once per edit."""
    if cache_key is not None:
        with _CACHE_LOCK:
            cached = _INTERPOLATIONS.get(cache_key)
        if cached is not None:
            shape, result, recorded, blends = cached
            if shape == tuple(len(path.nodes) for path in layer.paths):
                _apply_interpolation(layer, recorded)
                for key, contours in blends:
                    _remember(_BLENDS, _BLENDS_SIZE, key, contours)
                return result
    with PROFILE.section('interpolate layer: read layer'):
        recorder = _RecordedLayer(layer)
    recorder.blends = {}
    with PROFILE.section('interpolate layer: values'):
        result = _interpolate_layer(recorder, glyph, interpolation, glyph_key)
    with PROFILE.section('interpolate layer: write layer'):
        recorded = recorder.changes()
        _apply_interpolation(layer, recorded)
    blends = []
    if recorder.blends:
        paths = list(layer.paths)
        for index, (token, contours) in recorder.blends.items():
            source = recorder.paths[index]
            with PROFILE.section('interpolate layer: blend check'):
                check = _blend_check([(node.type, xy(real), node.userData)
                                      for node, real in zip(source.nodes, paths[index].nodes)])
            blends.append((token, (check, contours)))
            _remember(_BLENDS, _BLENDS_SIZE, token, (check, contours))
    if cache_key is not None:
        with _CACHE_LOCK:
            _INTERPOLATIONS[cache_key] = (recorder.shape(), result, recorded, blends)
            while len(_INTERPOLATIONS) > _INTERPOLATIONS_SIZE:
                _INTERPOLATIONS.popitem(last=False)
    return result


def _source_record(glyph, layer_id, glyph_key=None):
    """What interpolation reads from one source layer, read from Glyphs once per
    edit of the glyph (by `glyph_key`): its defaults and, per path, the node
    count, stroke flag, stroke attributes, cap curves and virtual nodes."""
    key = None if glyph_key is None else ('source', glyph_key, str(layer_id))
    if key is not None:
        with _CACHE_LOCK:
            if key in _SOURCE_OUTLINES:
                _SOURCE_OUTLINES.move_to_end(key)
                return _SOURCE_OUTLINES[key]
    try:
        source = glyph.layers[layer_id]
    except (KeyError, IndexError, TypeError):
        source = None
    record = None
    if source is not None:
        paths = []
        for path in list(source.paths):
            attributes = path.attributes
            paths.append({
                'path': path, 'count': len(path.nodes), 'enabled': enabled(path),
                'attributes': {name: attributes[name] for name in _STROKE_ATTRIBUTES
                               if attributes.get(name) is not None},
                'cap_curves': (cap_curve_of(path, False), cap_curve_of(path, True)),
                'virtual': virtual_nodes(path), 'profile': path_profile(path)})
        record = {'id': str(layer_id), 'defaults': layer_defaults(source), 'paths': paths}
    if key is not None:
        _remember(_SOURCE_OUTLINES, _SOURCE_OUTLINES_SIZE, key, record)
    return record


def _interpolate_layer(layer, glyph, interpolation, glyph_key=None):
    """Carry the strokes over to a layer Glyphs has just interpolated (instances in
    the preview, interpolation previews by other plugins, virtual masters).

    Glyphs interpolates node positions only, and the interpolated glyph does not
    know the ON/OFF state, so this writes the state and the blended italic angle
    onto the layer, the stroke attributes onto its paths, and blends width,
    height, node %, position and live corners (a corner off in a source counts as
    radius 0) from `interpolation` ({layerId: factor}). When `layer` has a
    `blends` dict, the blended master outline of each stroke goes there by
    path index.
    """
    sources = []
    for layer_id, factor in dict(interpolation or {}).items():
        record = _source_record(glyph, layer_id, glyph_key)
        if record is not None:
            sources.append((record, float(factor)))
    if not sources:
        return False
    state = glyph_enabled(glyph)
    layer.userData[LAYER_STATE_KEY] = state
    layer.userData[LAYER_ITALIC_KEY] = sum(factor * record['defaults'].italic_angle
                                           for record, factor in sources)
    layer.userData[LAYER_NIB_ANGLE_KEY] = _blend_angles([
        (record['defaults'].nib_angle, factor) for record, factor in sources])
    if not state:
        return True
    for index, path in enumerate(layer.paths):
        # (source record, its path record, factor) for this path index
        others = [(record, record['paths'][index], factor) for record, factor in sources
                  if index < len(record['paths']) and
                  record['paths'][index]['count'] == len(path.nodes)]
        if len(others) != len(sources):
            continue
        template = next((other for _, other, _ in others if other['enabled']), None)
        if template is None:
            continue
        if path.attributes.get(BLEND_KEY):
            path.attributes[BLEND_KEY] = ''  # copied from a source: not this blend
        for key, value in template['attributes'].items():
            path.attributes[key] = value
        if template['profile'] is not None:  # the instance cannot reach the font
            path.attributes[PROFILE_DATA_KEY] = _saved_profile(template['profile'])
        else:
            _pop(path.attributes, PROFILE_KEY)
            _pop(path.attributes, PROFILE_DATA_KEY)
        for at_end, curve_key in ((False, CAP_START_CURVE_KEY),
                                  (True, CAP_END_CURVE_KEY)):
            if cap_curve_enabled(path, at_end):
                controls = [other['cap_curves'][at_end] for _, other, _ in others]
                path.attributes[curve_key] = [
                    [sum(factor * controls[j][point][axis]
                         for j, (_, _, factor) in enumerate(others))
                     for axis in (0, 1)] for point in (0, 1)]
        virtual_sources = [(other['virtual'], factor) for _, other, factor in others]
        if virtual_sources and all(
                [(spec['id'], spec['segment'], spec['mode'], spec['side'],
                  spec.get('endSegment')) for spec in specs] ==
                [(spec['id'], spec['segment'], spec['mode'], spec['side'],
                  spec.get('endSegment')) for spec in virtual_sources[0][0]]
                for specs, _ in virtual_sources):
            blended = []
            for position, template_spec in enumerate(virtual_sources[0][0]):
                spec = dict(template_spec)
                spec['t'] = sum(factor * specs[position]['t']
                                for specs, factor in virtual_sources)
                spec['angle'] = sum(factor * specs[position]['angle']
                                    for specs, factor in virtual_sources)
                if spec['mode'] == 'section':
                    spec['endT'] = sum(factor * specs[position]['endT']
                                       for specs, factor in virtual_sources)
                for end in ('before', 'after'):
                    spec[end] = {side: sum(factor * specs[position][end][side]
                                           for specs, factor in virtual_sources)
                                 for side in ('left', 'right')}
                blended.append(spec)
            path.attributes[VIRTUAL_KEY] = blended
        path.attributes['fill'] = False
        values = [(_source_values(other['path'], record['defaults'],
                                  None if glyph_key is None else
                                  (glyph_key, record['id'], index)), factor)
                  for record, other, factor in others]
        base_w = sum(factor * entry['width'] for entry, factor in values)
        base_h = sum(factor * entry['height'] for entry, factor in values)
        if base_w <= 0 or base_h <= 0:
            continue
        path.attributes[STROKE_WIDTH_KEY] = base_w
        path.attributes[STROKE_HEIGHT_KEY] = base_h
        for position, node in enumerate(path.nodes):
            if node.type == OFFCURVE:
                continue
            nodes = [(entry['nodes'][position], factor) for entry, factor in values]
            # (w, h, offset, rotation, corner spec or None, 2 offsets, 4 handles)
            node.userData[SCALE_KEY] = sum(f * v[0] for v, f in nodes) / base_w * 100.0
            node.userData[HEIGHT_SCALE_KEY] = sum(f * v[1] for v, f in nodes) / base_h * 100.0
            node.userData[OFFSET_KEY] = sum(f * v[2] for v, f in nodes)
            for slot, key in ((5, CORNER_OFFSET_LEFT_KEY), (6, CORNER_OFFSET_RIGHT_KEY),
                              (7, CORNER_HANDLE_KEYS[0]), (8, CORNER_HANDLE_KEYS[1]),
                              (9, CORNER_HANDLE_KEYS[2]), (10, CORNER_HANDLE_KEYS[3])):
                value = [sum(f * v[slot][0] for v, f in nodes),
                         sum(f * v[slot][1] for v, f in nodes)]
                # Absent means (0, 0): skip a write per node and key where no
                # master moves its outline corners (most strokes).
                if value != [0.0, 0.0] or key in node.userData:
                    node.userData[key] = value
            node.userData[ROTATION_KEY] = _blend_angles([(v[3], f) for v, f in nodes])
            any_corner = any(v[4] is not None for v, _ in nodes)
            node.userData[CORNER_ON_KEY] = any_corner
            if any_corner:
                corner = {key: sum(f * (v[4] or ZERO_CORNER)[key] for v, f in nodes)
                          for key in _CORNER_PARTS}
                node.userData[CORNER_KEY] = corner['outer']
                node.userData[CORNER_INNER_KEY] = corner['inner']
                node.userData[CORNER_TENSION_KEY] = corner['tension']
                node.userData[CORNER_INNER_TENSION_KEY] = corner['inner_tension']
                node.userData[CORNER_RATIO_KEY] = corner['ratio']
                node.userData[CORNER_INNER_RATIO_KEY] = corner['inner_ratio']
            if WIDTH_KEY in node.userData:
                del node.userData[WIDTH_KEY]
        blends = getattr(layer, 'blends', None)
        if blends is not None:
            with PROFILE.section('interpolate layer: blend outlines'):
                weighted = []
                for record, other, factor in others:
                    key = None if glyph_key is None else (glyph_key, record['id'], index)
                    contours = _source_outline(other['path'], record['defaults'], key)
                    if contours is None:
                        break
                    weighted.append((contours, factor))
                else:
                    contours = _blend_contours(weighted)
                    if contours is not None:
                        token = uuid.uuid4().hex
                        path.attributes[BLEND_KEY] = token
                        blends[index] = (token, contours)
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


def outline_signature(path, defaults=None, items=None):
    """Segment kinds of a centerline's outline, one string per contour ('l' line,
    'c' cubic): what has to match between masters for interpolation."""
    items = path_nodes(path) if items is None else items
    _, cached, _ = _outline(path, _resolve(defaults, path), items)
    return tuple(''.join('c' if kind == 'cubic' else 'l' for kind, _ in contour)
                 for contour in cached[0] if contour)


def compare_string_suffix(layer):
    """Text appended to Glyphs' compare string of a layer (its compatibility
    check): the outline structure of every stroke, so Glyphs itself reports masters
    whose outlines differ although their centerlines match. Empty for layers
    without strokes, which keeps every other glyph exactly as Glyphs sees it."""
    if not layer_state(layer):
        return ''
    defaults = None
    parts = []
    for path in list(layer.paths):
        if generated(path) or is_outline(path) or not enabled(path):
            parts.append('-')
            continue
        items = path_nodes(path)
        if not items or not _valid_structure(path, [item[1] for item in items]):
            parts.append('?')
            continue
        if defaults is None:
            defaults = layer_defaults(layer)
        try:
            parts.append(','.join(outline_signature(path, defaults, items)))
        except ValueError:
            parts.append('?')
    if all(part in ('-', '?') for part in parts):
        return ''
    return '|vs:' + ';'.join(parts)



def _interpolated_layers(glyph):
    """Master and brace/bracket layers: the layers Glyphs interpolates."""
    result = []
    for layer in list(glyph.layers):
        try:
            if not (layer.isMasterLayer or layer.isSpecialLayer):
                continue
        except AttributeError:
            pass  # plain layer objects (tests)
        result.append(layer)
    return result


def _groups(values, names):
    """[(value, [layer names])] in order of first appearance."""
    groups = {}
    for value, name in zip(values, names):
        groups.setdefault(value, []).append(name)
    return list(groups.items())


def _virtual_layout(path, items):
    """Per centerline segment, the (mode, side) of its virtual nodes in order."""
    count = max(0, len(_on_curve(items, bool(path.closed))) - (0 if path.closed else 1))
    layout = [[] for _ in range(count)]
    for spec in virtual_nodes(path, count, items):
        if 0 <= spec['segment'] < count:
            layout[spec['segment']].append((spec['t'], spec['mode'], spec['side']))
        if spec['mode'] == 'section' and 0 <= spec['endSegment'] < count:
            layout[spec['endSegment']].append((spec['endT'], 'section end', spec['side']))
    return [tuple(entry[1:] for entry in sorted(specs)) for specs in layout]


def _mismatch_reasons(entries, names):
    """Settings that differ between the (path, items) of one path in several layers
    and change the outline structure: [(kind, where, [(value, [layer names])])].
    `where` is 'start'/'end' for caps, node numbers for corners and the segment
    number for virtual nodes."""
    reasons = []
    for at_end, end in ((False, 'start'), (True, 'end')):
        key = CAP_END_KEY if at_end else CAP_START_KEY
        styles = [path.attributes.get(key, 'flat') for path, _ in entries]
        if len({style in ('round', 'ellipse') for style in styles}) > 1:
            reasons.append(('cap shape', end, _groups(styles, names)))
            continue  # a round cap has no cap curve
        curves = [cap_curve_enabled(path, at_end) for path, _ in entries]
        if len(set(curves)) > 1:
            reasons.append(('cap curve', end, _groups(curves, names)))
    profiled = [width_profile.divisions(profile) if profile is not None else None
                for profile in (path_profile(path) for path, _ in entries)]
    if len(set(profiled)) > 1:
        values = [profile_name(path) for path, _ in entries]
        reasons.append(('profile', None, _groups(values, names)))
    layouts = [_virtual_layout(path, items) for path, items in entries]
    if len({len(layout) for layout in layouts}) == 1:
        for segment in range(len(layouts[0])):
            values = [layout[segment] for layout in layouts]
            if len(set(values)) > 1:
                reasons.append(('virtual', segment + 1, _groups(values, names)))
    if not reasons:
        # A live corner in one master only usually gets a zero-size twin in the
        # others, so it is named only when nothing else explains the difference.
        corners = [[data is not None and bool(corner_on_of(data))
                    for _, _, _, data in _on_curve(items, bool(path.closed))]
                   for path, items in entries]
        patterns = {}
        if len({len(row) for row in corners}) == 1:
            for index, pattern in enumerate(zip(*corners)):
                if len(set(pattern)) > 1:
                    patterns.setdefault(pattern, []).append(index + 1)
        for pattern, nodes in patterns.items():
            reasons.append(('corner', tuple(nodes), _groups(pattern, names)))
    return reasons


def master_incompatibilities(glyph):
    """{path index in layer.paths: {'reasons': [(kind, where, [(value, [names])])],
    'layers': [names]}} for strokes whose centerlines are compatible but whose
    outlines get a different structure in some master, e.g. a cap curve switched
    on in one master only. `layers` names the masters that differ from the most
    common outline."""
    if glyph is None or not glyph_enabled(glyph):
        return {}
    layers = _interpolated_layers(glyph)
    if len(layers) < 2:
        return {}
    names = [getattr(layer, 'name', None) or '?' for layer in layers]
    table = []
    for layer in layers:
        defaults = layer_defaults(layer)
        rows = []
        for path in list(layer.paths):
            items = path_nodes(path)
            kinds = tuple(item[1] for item in items)
            signature = None
            if not generated(path) and not is_outline(path) and enabled(path) and \
                    items and _valid_structure(path, list(kinds)):
                try:
                    signature = outline_signature(path, defaults, items)
                except ValueError:
                    signature = None
            rows.append((path, items, (bool(path.closed), kinds), signature))
        table.append(rows)
    if len({len(rows) for rows in table}) != 1:
        return {}  # Glyphs reports differing path counts itself
    result = {}
    for index in range(len(table[0])):
        column = [rows[index] for rows in table]
        if len({row[2] for row in column}) != 1 or any(row[3] is None for row in column):
            continue  # different centerlines: Glyphs' own compatibility check shows them
        signatures = [row[3] for row in column]
        if len(set(signatures)) == 1:
            continue
        common = max(set(signatures), key=signatures.count)
        reasons = _mismatch_reasons([(row[0], row[1]) for row in column], names) or \
            [('outline', None, _groups(signatures, names))]
        result[index] = {
            'reasons': reasons,
            'layers': [name for name, signature in zip(names, signatures)
                       if signature != common]}
    return result


_CAP_LABELS = {'flat': ('flat', 'フラット'), 'round': ('round', '丸'),
               'ellipse': ('ellipse', '楕円'), 'square': ('square', '四角'),
               'horizontal': ('horizontal cut', '水平カット'),
               'vertical': ('vertical cut', '垂直カット'),
               'angle': ('angle cut', '角度カット'), 'curve': ('curve', 'カーブ')}


def _describe_value(kind, value, pick):
    if kind in ('cap curve', 'corner'):
        return 'ON' if value else 'OFF'
    if kind == 'cap shape':
        return _CAP_LABELS.get(value, (value, value))[pick]
    if kind == 'profile':
        return value or ('none', 'なし')[pick]
    if kind == 'virtual':
        if not value:
            return ('none', 'なし')[pick]
        modes = {'continuous': ('smooth', '連続'), 'step': ('step', '段差'),
                 'whole': ('whole step', '全体段差'),
                 'section': ('section', '区間'), 'section end': ('section end', '区間終点')}
        sides = {'left': ('left', '左'), 'right': ('right', '右'), 'both': ('both', '両方')}
        return ', '.join(('%s/%s', '%s・%s')[pick] % (modes[mode][pick], sides[side][pick])
                         for mode, side in value)
    # outline: segments per contour
    counts = '+'.join(str(len(contour)) for contour in value)
    return ('%s segments' % counts, '%s区間' % counts)[pick]


def describe_incompatibility(entry, japanese=False):
    """Lines for the edit view or the export log: a heading, then one line per
    setting with the value each master has."""
    pick = 1 if japanese else 0
    names = {'cap curve': ('Cap curve', 'キャップカーブ'),
             'cap shape': ('Cap shape', '線端の形'),
             'corner': ('Live corner', 'ライブコーナー'),
             'virtual': ('Virtual nodes', '仮想ノード'),
             'profile': ('Width profile', '線幅プロファイル'),
             'outline': ('Outline structure', '輪郭構成')}
    ends = {'start': ('start', '始点'), 'end': ('end', '終点')}
    lines = [('Masters incompatible', 'マスター非互換')[pick] +
             (' (%s)' % ', '.join(entry['layers']) if entry['layers'] else '')]
    for kind, where, groups in entry['reasons']:
        label = names[kind][pick]
        if kind in ('cap curve', 'cap shape'):
            label += (' (%s)', '（%s）')[pick] % ends[where][pick]
        elif kind == 'corner':
            label += (' (node %s)', '（ノード %s）')[pick] % ', '.join(map(str, where))
        elif kind == 'virtual':
            label += (' (segment %d)', '（区間 %d）')[pick] % where
        values = ('; ', '／')[pick].join(
            '%s: %s' % (_describe_value(kind, value, pick), ', '.join(layers))
            for value, layers in groups)
        lines.append(('%s – %s', '%s：%s')[pick] % (label, values))
    return '\n'.join(lines)


def node_contour(path):
    """A path's own nodes as ('line'|'cubic', points) segments, starting on a
    node that is on the curve. Other off-curve runs (quadratics) become lines."""
    nodes = [(xy(node), node.type) for node in path.nodes]
    first = next((i for i, (_, kind) in enumerate(nodes) if kind != OFFCURVE), None)
    if first is None:
        return []
    sequence = nodes[first:] + nodes[:first] + [nodes[first]] if path.closed else nodes[first:]
    segments, start, controls = [], sequence[0][0], []
    for point, kind in sequence[1:]:
        if kind == OFFCURVE:
            controls.append(point)
            continue
        if len(controls) == 2:
            segments.append(('cubic', (start, controls[0], controls[1], point)))
        else:
            segments.append(('line', (start, point)))
        start, controls = point, []
    return segments


def copied_contours(layer, selected):
    """What a copy of `selected` (the layer's selection) should look like outside
    Glyphs: each live stroke the selection touches becomes its outline, and fully
    selected plain paths stay as they are. Returns [(closed, segments)], or None
    when the selection holds no live stroke, so the copy needs no change."""
    state = layer_state(layer)
    if state is False or not selected:
        return None
    defaults = layer_defaults(layer)
    result, found = [], False
    for path in layer.paths:
        picked = [node in selected for node in path.nodes]
        if not any(picked):
            continue
        if not generated(path) and not is_outline(path) and (state or enabled(path)):
            items = path_nodes(path)
            if items and _valid_structure(path, [item[1] for item in items]):
                try:
                    contours = curves_for_path(path, defaults, items=items)
                except ValueError:
                    contours = None
                if contours is not None:
                    found = True
                    result.extend((True, contour) for contour in contours if contour)
                    continue
        if all(picked):
            segments = node_contour(path)
            if segments:
                result.append((bool(path.closed), segments))
    return result if found else None
