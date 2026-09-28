"""Glyphs 3 adapters shared by the editing tool and the export filter."""
import hashlib
from GlyphsApp import GSPath, GSNode, LINE, CURVE, OFFCURVE
from variable_stroke_core import outline

PATH_KEY = 'com.codex.VariableStroke.enabled'
WIDTH_KEY = 'com.codex.VariableStroke.width'
CAP_START_KEY = 'com.codex.VariableStroke.capStart'
CAP_END_KEY = 'com.codex.VariableStroke.capEnd'
EXPORT_FILTER = 'VariableStrokeExport'
DEFAULT_WIDTH = 40.0
GENERATED_KEY = 'com.codex.VariableStroke.generated'
GENERATED_SIGNATURE_KEY = 'com.codex.VariableStroke.signature'
GENERATED_COUNT_KEY = 'com.codex.VariableStroke.generatedCount'


def enabled(path):
    return bool(path.attributes.get(PATH_KEY))


def generated(path):
    return bool(path.attributes.get(GENERATED_KEY))


def width(node):
    try:
        return max(1.0, float(node.userData.get(WIDTH_KEY, DEFAULT_WIDTH)))
    except (ValueError, TypeError):
        return DEFAULT_WIDTH


def xy(node):
    return (float(node.position.x), float(node.position.y))


def segments_for_path(path):
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
    for node in run:
        if node.type == OFFCURVE:
            controls.append(node)
            continue
        if len(controls) == 0:
            result.append(('line', (xy(start), xy(node)), width(start), width(node)))
        elif len(controls) == 2 and node.type == CURVE:
            result.append(('cubic', (xy(start), xy(controls[0]), xy(controls[1]), xy(node)), width(start), width(node)))
        else:
            raise ValueError('Only line and cubic path segments are supported')
        start = node
        controls = []
    if controls:
        raise ValueError('Path ends with loose off-curve handles')
    return result


def polygons_for_path(path):
    return outline(segments_for_path(path), bool(path.closed),
                   path.attributes.get(CAP_START_KEY, 'flat'),
                   path.attributes.get(CAP_END_KEY, 'flat'))


def generated_paths(path):
    result = []
    for polygon in polygons_for_path(path):
        if len(polygon) < 3:
            continue
        new_path = GSPath()
        new_path.closed = True
        for point in polygon:
            new_path.nodes.append(GSNode(point, LINE))
        result.append(new_path)
    return result


def _signature(layer):
    data = []
    for path in layer.paths:
        if not enabled(path):
            continue
        data.append((bool(path.closed), path.attributes.get(CAP_START_KEY, 'flat'),
                     path.attributes.get(CAP_END_KEY, 'flat'),
                     tuple((node.type, xy(node), width(node) if node.type != OFFCURVE else None)
                           for node in path.nodes)))
    return hashlib.sha1(repr(data).encode('utf-8')).hexdigest()


def layer_needs_sync(layer):
    sources = [path for path in layer.paths if enabled(path)]
    cached = [path for path in layer.paths if generated(path)]
    if not sources:
        return bool(cached)
    signature = _signature(layer)
    if not cached:
        return any(segments_for_path(path) for path in sources)
    return (any(path.attributes.get(GENERATED_SIGNATURE_KEY) != signature for path in cached)
            or any(int(path.attributes.get(GENERATED_COUNT_KEY, -1)) != len(cached)
                   for path in cached))


def sync_layer(layer):
    """Materialize outlines as locked, filled Glyphs paths for native rendering."""
    if not layer_needs_sync(layer):
        return False
    sources = [path for path in layer.paths if enabled(path)]
    cached = [path for path in layer.paths if generated(path)]
    replacements = []
    for path in sources:
        replacements.extend(generated_paths(path))
    signature = _signature(layer)
    for path in replacements:
        path.attributes[GENERATED_KEY] = True
        path.attributes[GENERATED_SIGNATURE_KEY] = signature
        path.attributes[GENERATED_COUNT_KEY] = len(replacements)
        path.attributes['fill'] = True
        path.locked = True
    for path in cached:
        layer.shapes.remove(path)
    for path in replacements:
        layer.shapes.append(path)
    return True


def convert_layer(layer):
    """Replace only marked skeletons. Other outlines and components survive."""
    originals = [shape for shape in list(layer.shapes) if isinstance(shape, GSPath) and enabled(shape)]
    if not originals:
        return 0
    sync_layer(layer)
    cached = [shape for shape in list(layer.shapes) if isinstance(shape, GSPath) and generated(shape)]
    for path in originals:
        layer.shapes.remove(path)
    for path in cached:
        path.locked = False
        for key in (GENERATED_KEY, GENERATED_SIGNATURE_KEY, GENERATED_COUNT_KEY):
            path.attributes[key] = None
    return len(originals)
