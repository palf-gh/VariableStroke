import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fake = types.ModuleType('GlyphsApp')
fake.LINE = 'line'
fake.CURVE = 'curve'
fake.OFFCURVE = 'offcurve'


class Node:
    def __init__(self, x, y=None, kind='line', width=20):
        if isinstance(x, tuple):
            x, y, kind = x[0], x[1], y
        self.position = types.SimpleNamespace(x=x, y=y)
        self.type = kind
        self.userData = {'com.codex.VariableStroke.width': width}


class Path:
    def __init__(self, nodes=None, closed=False):
        self.nodes = nodes or []
        self.closed = closed
        self.attributes = {'com.codex.VariableStroke.enabled': True} if nodes else {}
        self.locked = False


class Layer:
    def __init__(self, paths):
        self.shapes = list(paths)

    @property
    def paths(self):
        return [shape for shape in self.shapes if isinstance(shape, Path)]


fake.GSPath = Path
fake.GSNode = Node
sys.modules['GlyphsApp'] = fake
bridge = importlib.import_module('glyphs_bridge')


class BridgeTests(unittest.TestCase):
    def test_open_cubic(self):
        path = Path([Node(0, 0), Node(0, 50, 'offcurve'),
                     Node(100, 50, 'offcurve'), Node(100, 0, 'curve', 40)])
        segs = bridge.segments_for_path(path)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0][0], 'cubic')
        self.assertEqual((segs[0][2], segs[0][3]), (20.0, 40.0))

    def test_closed_line_segments(self):
        path = Path([Node(0, 0), Node(100, 0), Node(100, 100), Node(0, 100)], True)
        self.assertEqual(len(bridge.segments_for_path(path)), 4)
        self.assertEqual(len(bridge.polygons_for_path(path)), 2)

    def test_glyph_switch_works_without_selected_path(self):
        first = Path([Node(0, 0), Node(100, 0)])
        second = Path([Node(0, 0), Node(0, 100)])
        first.attributes.clear()
        second.attributes.clear()
        glyph = types.SimpleNamespace(userData={}, layers=[Layer([first]), Layer([second])])
        bridge.set_glyph_enabled(glyph, True)
        self.assertTrue(bridge.glyph_enabled(glyph))
        self.assertTrue(bridge.enabled(first))
        self.assertTrue(bridge.enabled(second))
        bridge.set_glyph_enabled(glyph, False)
        self.assertFalse(bridge.glyph_enabled(glyph))
        empty = types.SimpleNamespace(userData={}, layers=[Layer([])])
        bridge.set_glyph_enabled(empty, True)
        self.assertTrue(bridge.glyph_enabled(empty))

    def test_conversion_creates_bezier_without_cached_paths(self):
        source = Path([Node(0, 0), Node(0, 50, 'offcurve'),
                       Node(100, 50, 'offcurve'), Node(100, 0, 'curve', 40)])
        layer = Layer([source])
        self.assertEqual(len(layer.paths), 1)
        self.assertEqual(bridge.convert_layer(layer), 1)
        self.assertEqual(len(layer.paths), 1)
        self.assertIn('offcurve', [node.type for node in layer.paths[0].nodes])
        self.assertFalse(bridge.enabled(layer.paths[0]))


if __name__ == '__main__':
    unittest.main()
