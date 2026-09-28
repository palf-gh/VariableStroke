"""Glyphs 3 adapters shared by the editing tool and the export filter."""
from GlyphsApp import GSPath, GSNode, LINE, CURVE, OFFCURVE
from variable_stroke_core import outline

PATH_KEY = 'com.codex.VariableStroke.enabled'
WIDTH_KEY = 'com.codex.VariableStroke.width'
CAP_START_KEY = 'com.codex.VariableStroke.capStart'
CAP_END_KEY = 'com.codex.VariableStroke.capEnd'
EXPORT_FILTER = 'VariableStrokeExport'
DEFAULT_WIDTH = 40.0


def enabled(path):
    return bool(path.attributes.get(PATH_KEY))


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


def convert_layer(layer):
    """Replace only marked skeletons. Other outlines and components survive."""
    originals = [shape for shape in list(layer.shapes) if isinstance(shape, GSPath) and enabled(shape)]
    if not originals:
        return 0
    replacement = []
    for path in originals:
        replacement.extend(generated_paths(path))
    for path in originals:
        layer.shapes.remove(path)
    for path in replacement:
        layer.shapes.append(path)
    return len(originals)
