"""Glyphs 3 adapters shared by the editing tool and the export filter."""
import collections
import contextlib
import math
import threading
import time
import uuid
from GlyphsApp import GSPath, GSNode, LINE, CURVE, OFFCURVE
from variable_stroke_core import outline_curves, ellipse_nib_edges, sub, DEFAULT_CAP_CURVE

PATH_KEY = 'com.codex.VariableStroke.enabled'
# Legacy absolute node width; converted to STROKE_WIDTH_KEY x SCALE_KEY on edit.
WIDTH_KEY = 'com.codex.VariableStroke.width'
STROKE_WIDTH_KEY = 'com.codex.VariableStroke.strokeWidth'  # per path, font units
SCALE_KEY = 'com.codex.VariableStroke.scale'  # per node, percent of the path width
HEIGHT_SCALE_KEY = 'com.codex.VariableStroke.heightScale'  # per node, percent of path height
STROKE_HEIGHT_KEY = 'com.codex.VariableStroke.strokeHeight'  # per path, font units
OFFSET_KEY = 'com.codex.VariableStroke.offset'  # per node; values beyond +/-100 move the stroke off the centerline
VIRTUAL_KEY = 'com.codex.VariableStroke.virtualNodes'  # per path, ordered virtual sections
VIRTUAL_ANCHOR_KEY = 'com.codex.VariableStroke.virtualAnchor'  # stable on-curve identity
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
_STROKE_ATTRIBUTES = (PATH_KEY, CAP_START_KEY, CAP_END_KEY, CAP_START_ANGLE_KEY,
                      CAP_END_ANGLE_KEY, CAP_START_CURVE_KEY, CAP_END_CURVE_KEY,
                      CAP_START_CURVE_ON_KEY, CAP_END_CURVE_ON_KEY)

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


def virtual_nodes(path, segment_count=None, items=None):
    """Normalized sections, following their original segment across node edits."""
    try:
        raw = path.attributes.get(VIRTUAL_KEY) or []
    except (AttributeError, TypeError):
        return []
    if not raw:
        return []
    count = len(segments_for_path(path)) if segment_count is None else segment_count
    on_curve_items = _on_curve(path_nodes(path) if items is None else items,
                               bool(path.closed))
    on_curve = [item[0] for item in on_curve_items]
    pairs = [(on_curve[i], on_curve[(i+1) % len(on_curve)]) for i in range(count)] \
        if len(on_curve) >= count + (0 if path.closed else 1) and on_curve else []
    anchor_ids = [item[3].get(VIRTUAL_ANCHOR_KEY) for item in on_curve_items]
    saved = list(raw)
    changed = False
    result = []
    for raw_index, item in enumerate(raw):
        try:
            data = dict(item)
            segment, t = int(data['segment']), float(data['t'])
            if not (0.005 <= t <= 0.995 and math.isfinite(t)):
                continue
            start_id, end_id = data.get('anchorStart'), data.get('anchorEnd')
            if start_id and end_id:
                matches = [i for i in range(len(pairs))
                           if anchor_ids[i] == start_id and
                           anchor_ids[(i+1) % len(on_curve)] == end_id]
                if matches:
                    segment = matches[0]
                elif (start_id in anchor_ids or end_id in anchor_ids or
                      data.get('anchorCount') != count):
                    # Its original segment was removed or split. Keep the saved
                    # entry so undo can restore the virtual node with the path.
                    continue
            if not 0 <= segment < count:
                continue
            mode = data.get('mode', 'continuous')
            side = data.get('side', 'both')
            direction = data.get('direction', 'normal')
            if mode not in ('continuous', 'step') or side not in ('left', 'right', 'both') \
                    or direction not in ('normal', 'horizontal', 'vertical', 'angle'):
                continue
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
            if pairs:
                start, end = pairs[segment]
                for index in (segment, (segment+1) % len(on_curve)):
                    if not anchor_ids[index]:
                        anchor_ids[index] = uuid.uuid4().hex
                        on_curve[index].userData[VIRTUAL_ANCHOR_KEY] = anchor_ids[index]
                start_id = anchor_ids[segment]
                end_id = anchor_ids[(segment+1) % len(on_curve)]
                if (data.get('segment') != segment or data.get('anchorStart') != start_id
                        or data.get('anchorEnd') != end_id or
                        data.get('anchorCount') != count):
                    data.update(segment=segment, anchorStart=start_id,
                                anchorEnd=end_id, anchorCount=count)
                    saved[raw_index] = data
                    changed = True
            result.append({'id': str(data.get('id', '')), 'segment': segment, 't': t,
                           'mode': mode, 'side': side, 'direction': direction,
                           'angle': angle, 'linked': bool(data.get('linked', True)),
                           'before': before, 'after': after,
                           'anchorStart': start_id, 'anchorEnd': end_id,
                           'anchorCount': count})
        except (TypeError, ValueError, KeyError):
            continue
    if changed:
        path.attributes[VIRTUAL_KEY] = saved
    return sorted(result, key=lambda spec: (spec['segment'], spec['t'], spec['id']))


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


def expanded_virtual_segments(segments, specs, corners=None, closed=False):
    """Split source segments without changing the Glyphs path; return pieces,
    step breaks, expanded corner list, and original-node index for each vertex."""
    from variable_stroke_core import _nib, _derivative, unit
    by_segment = collections.defaultdict(list)
    for spec in specs:
        by_segment[spec['segment']].append(spec)
    result = []
    breaks = {'left': set(), 'right': set()}
    smooths, expanded_corners, node_map = set(), [], []
    for index, (kind, pts, e0, e1) in enumerate(segments):
        current_pts, start_t, current_nib = pts, 0.0, e0
        source_specs = sorted(by_segment[index], key=lambda item: item['t'])
        for spec in source_specs:
            t = spec['t']
            if t <= start_t + 0.001:
                continue
            first, current_pts = _split_segment(kind, current_pts,
                                                 (t-start_t)/(1-start_t))
            base0, base1 = _nib(e0), _nib(e1)
            values = [base0[j]*(1-t)+base1[j]*t for j in range(3)]
            turn = (base1[3]-base0[3]+90.0) % 180.0-90.0
            base = tuple(values + [base0[3]+turn*t])
            axis = _virtual_section_angle(spec, unit(_derivative(kind, pts, t)))
            if axis is None:
                axis = 0.0  # defensive fallback for an invalid saved direction
            before = _virtual_nib(base, spec['before'], axis)
            after = _virtual_nib(base, spec['after'] if spec['mode'] == 'step'
                                 else spec['before'], axis)
            result.append((kind, first, current_nib, before))
            expanded_corners.append(corners[index] if corners and start_t == 0 else None)
            node_map.append(index if start_t == 0 else None)
            if spec['mode'] == 'step':
                for side in ('left', 'right'):
                    if spec['side'] in (side, 'both'):
                        breaks[side].add(len(result)-1)
            else:
                smooths.add(len(result)-1)
            current_nib, start_t = after, t
        result.append((kind, current_pts, current_nib, e1))
        expanded_corners.append(corners[index] if corners and start_t == 0 else None)
        node_map.append(index if start_t == 0 else None)
    if not closed:
        expanded_corners.append(corners[-1] if corners else None)
        node_map.append(len(segments))
    return result, breaks, smooths, expanded_corners, node_map


def virtual_widgets(path, defaults=None):
    """Canvas positions for virtual sections, using the same nibs as expansion."""
    from variable_stroke_core import _nib, _derivative, unit, nib_edges, normal
    segments = segments_for_path(path, defaults)
    widgets = []
    for spec in virtual_nodes(path, len(segments)):
        kind, pts, e0, e1 = segments[spec['segment']]
        t = spec['t']
        center = virtual_point((kind, pts), t)
        tangent = unit(_derivative(kind, pts, t))
        n0, n1 = _nib(e0), _nib(e1)
        values = [n0[j]*(1-t)+n1[j]*t for j in range(3)]
        turn = (n1[3]-n0[3]+90.0) % 180.0-90.0
        base = tuple(values + [n0[3]+turn*t])
        angle = _virtual_section_angle(spec, tangent)
        if angle is None:
            angle = 0.0
        before = _virtual_nib(base, spec['before'], angle)
        after = _virtual_nib(base, spec['after'] if spec['mode'] == 'step'
                             else spec['before'], angle)
        baseline = nib_edges(center, tangent, base)
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
        widgets.append({'spec': spec, 'center': center, 'tangent': tangent,
                        'axis': axis, 'before': before_edges,
                        'after': after_edges,
                        'base': base})
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
    for kind, points, _, _ in segments:
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
    for spec in source_specs:
        if matching_segments:
            segment, t = spec['segment'], spec['t']
        else:
            distance = source_totals[spec['segment']] + \
                _length_at(source_tables[spec['segment']], spec['t'])
            progress = distance/source_totals[-1] if source_totals[-1] > 1e-9 else \
                (spec['segment']+spec['t'])/len(source_segments)
            segment, t = _map_virtual_progress(progress, target_tables, target_totals)
        copied = {key: value for key, value in spec.items()
                  if not key.startswith('anchor')}
        copied.update(id=uuid.uuid4().hex, segment=segment, t=round(t, 6),
                      before=dict(spec['before']), after=dict(spec['after']))
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
        for key in corner_keys:
            if key in source_data:
                data[key] = source_data[key]
    if any(corner_on_of(item[3]) for item in source_nodes):
        note_corner(target)
    if virtuals:
        virtual_nodes(target, len(target_segments), target_items)
    return True


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
        index = next(i for i, other in enumerate(paths) if other == path)
        layers = list(glyph.layers)
    except Exception:
        return []
    result = []
    for other_layer in layers:
        if other_layer == layer:
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


def corner_specs(path, items=None):
    """Live corner per on-curve node. A corner that is on in any other master of
    the glyph gets a zero-size arc here, so masters keep compatible outlines while
    each master switches its corners on or off on its own."""
    items = path_nodes(path) if items is None else items
    specs = [corner_spec_of(data) for _, _, _, data in _on_curve(items, bool(path.closed))]
    if all(spec is not None for spec in specs) or not _glyph_has_corners(path):
        return specs
    rounded_elsewhere = set()
    for sibling in _sibling_paths(path, len(specs)):
        for k, (_, _, _, data) in enumerate(sibling):
            if corner_on_of(data):
                rounded_elsewhere.add(k)
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
        source_segments = segments_for_path(path, defaults, items)
        closed = bool(path.closed)
        attributes = path.attributes
        caps = (attributes.get(CAP_START_KEY, 'flat'), attributes.get(CAP_END_KEY, 'flat'))
        angles = (cut_angle(path, CAP_START_ANGLE_KEY), cut_angle(path, CAP_END_ANGLE_KEY))
        cap_curves = (cap_curve_of(path, False) if cap_curve_enabled(path, False) else None,
                      cap_curve_of(path, True) if cap_curve_enabled(path, True) else None)
        specs = virtual_nodes(path, len(source_segments), items)
        source_corners = corner_specs(path, items)
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
               tuple(None if c is None else tuple(sorted(c.items())) for c in corners))

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


def _expansion_plan(layer, glyph_on=None, defaults=None):
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
        for at_end, curve_key in ((False, CAP_START_CURVE_KEY),
                                  (True, CAP_END_CURVE_KEY)):
            if cap_curve_enabled(path, at_end):
                controls = [cap_curve_of(other, at_end) for _, other, _ in others]
                path.attributes[curve_key] = [
                    [sum(factor * controls[j][point][axis]
                         for j, (_, _, factor) in enumerate(others))
                     for axis in (0, 1)] for point in (0, 1)]
        virtual_sources = [(virtual_nodes(other), factor) for _, other, factor in others]
        if virtual_sources and all(
                [(spec['id'], spec['segment'], spec['mode'], spec['side']) for spec in specs] ==
                [(spec['id'], spec['segment'], spec['mode'], spec['side'])
                 for spec in virtual_sources[0][0]] for specs, _ in virtual_sources):
            blended = []
            for position, template_spec in enumerate(virtual_sources[0][0]):
                spec = dict(template_spec)
                spec['t'] = sum(factor * specs[position]['t']
                                for specs, factor in virtual_sources)
                spec['angle'] = sum(factor * specs[position]['angle']
                                    for specs, factor in virtual_sources)
                for end in ('before', 'after'):
                    spec[end] = {side: sum(factor * specs[position][end][side]
                                           for specs, factor in virtual_sources)
                                 for side in ('left', 'right')}
                blended.append(spec)
            path.attributes[VIRTUAL_KEY] = blended
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


def _mismatch_reasons(entries):
    """Settings that differ between the (path, items) of one path in several layers
    and change the outline structure."""
    reasons = []
    for at_end, end in ((False, 'start'), (True, 'end')):
        if len({cap_curve_enabled(path, at_end) for path, _ in entries}) > 1:
            reasons.append(('cap curve', end))
        key = CAP_END_KEY if at_end else CAP_START_KEY
        if len({path.attributes.get(key, 'flat') in ('round', 'ellipse')
                for path, _ in entries}) > 1:
            reasons.append(('cap shape', end))
    corners = {tuple(data is not None and corner_on_of(data)
                     for _, _, _, data in _on_curve(items, bool(path.closed)))
               for path, items in entries}
    if len(corners) > 1:
        reasons.append(('corner', None))
    return reasons or [('outline', None)]


def master_incompatibilities(glyph):
    """{path index in layer.paths: {'reasons': [(kind, end)], 'layers': [names]}}
    for strokes whose centerlines are compatible but whose outlines get a different
    structure in some master, e.g. a cap curve switched on in one master only.
    `layers` names the masters that differ from the most common outline."""
    if glyph is None or not glyph_enabled(glyph):
        return {}
    layers = _interpolated_layers(glyph)
    if len(layers) < 2:
        return {}
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
        result[index] = {
            'reasons': _mismatch_reasons([(row[0], row[1]) for row in column]),
            'layers': [getattr(layer, 'name', None) for layer, signature
                       in zip(layers, signatures) if signature != common]}
    return result


def describe_incompatibility(entry, japanese=False):
    """One line for the edit view or the export log."""
    names = {'cap curve': ('cap curve', 'キャップカーブ'),
             'cap shape': ('round cap', '丸キャップ'),
             'corner': ('live corner', 'ライブコーナー'),
             'outline': ('outline structure', '輪郭構成')}
    ends = {'start': ('start', '始点'), 'end': ('end', '終点'), None: ('', '')}
    pick = 1 if japanese else 0
    parts = []
    for kind, end in entry['reasons']:
        label = names[kind][pick]
        if ends[end][pick]:
            label = ('%s（%s）' if japanese else '%s (%s)') % (label, ends[end][pick])
        parts.append(label)
    layers = ', '.join(name for name in entry['layers'] if name)
    if japanese:
        return 'マスター非互換：' + '・'.join(parts) + (' ― ' + layers if layers else '')
    return 'Masters incompatible: ' + ', '.join(parts) + (' - ' + layers if layers else '')


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
