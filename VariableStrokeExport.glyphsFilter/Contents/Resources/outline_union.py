"""Union generated stroke contours before Glyphs' export overlap pass."""
import os
import sys

from glyphs_bridge import OUTLINE_KEY, generated_paths, xy


def _pathops():
    try:
        import pathops
        return pathops
    except ImportError:
        vendor = os.path.join(os.path.dirname(__file__), 'vendor')
        if not os.path.isdir(vendor):
            vendor = os.path.join(os.path.dirname(__file__),
                'VariableStrokeExport.glyphsFilter', 'Contents', 'Resources', 'vendor')
        if vendor not in sys.path:
            sys.path.insert(0, vendor)
        import pathops
        return pathops


class _ContourPen(object):
    """Receive a PathOps union as the same line/cubic contours as the bridge."""
    def __init__(self):
        self.contours = []
        self.current = None
        self.start = None
        self.point = None

    def moveTo(self, point):
        self.current = []
        self.start = self.point = tuple(point)

    def lineTo(self, point):
        point = tuple(point)
        if point != self.point:
            self.current.append(('line', (self.point, point)))
        self.point = point

    def curveTo(self, first, second, point):
        point = tuple(point)
        self.current.append(('cubic', (self.point, tuple(first), tuple(second), point)))
        self.point = point

    def closePath(self):
        if self.current is None:
            return
        if self.point != self.start:
            self.lineTo(self.start)
        if self.current:
            self.contours.append(self.current)
        self.current = None

    def endPath(self):
        self.closePath()


def _skia_path(path, pathops):
    from GlyphsApp import OFFCURVE
    nodes = list(path.nodes)
    first = next((i for i, node in enumerate(nodes) if node.type != OFFCURVE), None)
    if first is None:
        raise ValueError('Outline has no on-curve node')
    nodes = nodes[first:] + nodes[:first+1]
    result = pathops.Path()
    result.moveTo(*xy(nodes[0]))
    controls = []
    for node in nodes[1:]:
        if node.type == OFFCURVE:
            controls.append(xy(node))
        elif len(controls) == 2:
            result.cubicTo(*controls[0], *controls[1], *xy(node))
            controls = []
        elif not controls:
            result.lineTo(*xy(node))
        else:
            raise ValueError('Invalid outline control points')
    if controls:
        raise ValueError('Outline ends with loose control points')
    result.close()
    return result


def union_layer_outlines(layer):
    """Replace this plugin's overlapping outlines with a PathOps union.

    The export filter calls this on throwaway export layers only. On any
    failure it leaves the original paths in place so the user can still export
    with Glyphs' Remove Overlap setting disabled.
    """
    from GlyphsApp import GSPath
    paths = [shape for shape in layer.shapes
             if isinstance(shape, GSPath) and shape.attributes.get(OUTLINE_KEY)]
    if len(paths) < 2:
        return 0
    try:
        pathops = _pathops()
        pen = _ContourPen()
        pathops.union([_skia_path(path, pathops) for path in paths], pen)
        replacements = generated_paths(paths[0], contours=pen.contours)
        if not replacements:
            raise ValueError('Union removed every outline')
    except Exception as error:
        print('VariableStrokeExport: outline union failed: %s' % error)
        return 0
    for path in paths:
        layer.shapes.remove(path)
    for path in replacements:
        layer.shapes.append(path)
    return len(paths) - len(replacements)
