"""Glyphs 3 adapters shared by the editing tool and the export filter."""
from GlyphsApp import GSPath, GSNode, LINE, CURVE, OFFCURVE
from variable_stroke_core import outline, outline_curves

PATH_KEY = 'com.codex.VariableStroke.enabled'
WIDTH_KEY = 'com.codex.VariableStroke.width'
CAP_START_KEY = 'com.codex.VariableStroke.capStart'
CAP_END_KEY = 'com.codex.VariableStroke.capEnd'
EXPORT_FILTER = 'VariableStrokeExport'
DEFAULT_WIDTH = 40.0
GLYPH_KEY = 'com.codex.VariableStroke.glyphEnabled'
GENERATED_KEY = 'com.codex.VariableStroke.generated'


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


def curves_for_path(path):
    return outline_curves(segments_for_path(path), bool(path.closed),
                          path.attributes.get(CAP_START_KEY, 'flat'),
                          path.attributes.get(CAP_END_KEY, 'flat'))


def generated_paths(path):
    result = []
    for contour in curves_for_path(path):
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


def convert_layer(layer):
    """Replace enabled skeletons with Bézier outlines only in the target layer."""
    if getattr(layer, 'parent', None) is not None and not glyph_enabled(layer.parent):
        return 0
    originals = [shape for shape in list(layer.shapes)
                 if isinstance(shape, GSPath) and enabled(shape)]
    if not originals:
        return 0
    replacements = [generated for path in originals for generated in generated_paths(path)]
    for path in originals:
        layer.shapes.remove(path)
    for path in replacements:
        layer.shapes.append(path)
    return len(originals)
