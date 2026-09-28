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
        self.assertEqual((segs[0][2][0], segs[0][3][0]), (20.0, 40.0))

    def test_closed_line_segments(self):
        path = Path([Node(0, 0), Node(100, 0), Node(100, 100), Node(0, 100)], True)
        self.assertEqual(len(bridge.segments_for_path(path)), 4)
        self.assertEqual(len(bridge.curves_for_path(path, 20)), 2)

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

    def test_expand_replaces_centerlines_only(self):
        stroke = Path([Node(0, 0), Node(100, 0)])
        plain = Path([Node(0, 0), Node(10, 0), Node(10, 10)], True)
        plain.attributes.clear()
        layer = Layer([stroke, plain])
        self.assertEqual(bridge.expand_layer(layer), 1)
        self.assertNotIn(stroke, layer.paths)
        self.assertIn(plain, layer.paths)
        self.assertEqual(len(layer.paths), 2)

    def test_closed_centerline_expands_to_two_contours(self):
        ring = Path([Node(0, 0), Node(100, 0), Node(100, 100), Node(0, 100)], True)
        layer = Layer([ring])
        bridge.expand_layer(layer)
        self.assertEqual(len(layer.paths), 2)

    def test_off_disables_paths_and_removes_legacy_outlines(self):
        source = Path([Node(0, 0), Node(100, 0)])
        stale = Path([Node(0, 0), Node(1, 0)])
        stale.attributes = {bridge.GENERATED_KEY: 'abc'}
        glyph = types.SimpleNamespace(userData={}, layers=[Layer([source, stale])])
        bridge.set_glyph_enabled(glyph, True)
        self.assertEqual(glyph.layers[0].paths, [source])
        bridge.set_glyph_enabled(glyph, False)
        self.assertFalse(bridge.enabled(source))
        self.assertEqual(bridge.expand_layer(glyph.layers[0]), 0)
    def test_node_width_is_percent_of_path_width(self):
        path = Path([Node(0, 0), Node(100, 0)])
        for node in path.nodes:
            node.userData = {}
        path.attributes[bridge.STROKE_WIDTH_KEY] = 60
        path.nodes[1].userData[bridge.SCALE_KEY] = 50
        segs = bridge.segments_for_path(path)
        self.assertEqual((segs[0][2][0], segs[0][3][0]), (60.0, 30.0))
        path.attributes[bridge.STROKE_WIDTH_KEY] = 100
        segs = bridge.segments_for_path(path)
        self.assertEqual((segs[0][2][0], segs[0][3][0]), (100.0, 50.0))

    def test_legacy_absolute_widths_migrate(self):
        path = Path([Node(0, 0, width=20), Node(100, 0, width=40)])
        before = bridge.segments_for_path(path)
        self.assertTrue(bridge.migrate_path(path))
        self.assertEqual(path.attributes[bridge.STROKE_WIDTH_KEY], 40)  # widest legacy node
        self.assertEqual(path.nodes[0].userData[bridge.SCALE_KEY], 50.0)
        self.assertNotIn(bridge.WIDTH_KEY, path.nodes[0].userData)
        self.assertEqual(bridge.segments_for_path(path), before)
        self.assertFalse(bridge.migrate_path(path))

    def test_interpolated_layer_blends_widths(self):
        def master(base, scales):
            path = Path([Node(0, 0), Node(100, 0)])
            for node, value in zip(path.nodes, scales):
                node.userData = {bridge.SCALE_KEY: value}
            path.attributes[bridge.STROKE_WIDTH_KEY] = base
            return Layer([path])
        glyph = types.SimpleNamespace(layers={'a': master(40, (100, 50)), 'b': master(100, (100, 100))})
        target = master(40, (100, 50))
        self.assertTrue(bridge.interpolate_widths(target, glyph, {'a': 0.5, 'b': 0.5}))
        segs = bridge.segments_for_path(target.paths[0])
        self.assertAlmostEqual(segs[0][2][0], 70.0)  # (40 + 100) / 2
        self.assertAlmostEqual(segs[0][3][0], 60.0)  # (20 + 100) / 2

    def test_outline_node_count_is_stable_across_designs(self):
        from variable_stroke_core import outline_curves
        def count(width, bend):
            segments = [('cubic', ((0, 0), (0, bend), (300, bend), (300, 400)), width, width),
                        ('line', ((300, 400), (600, 400)), width, width * 0.5)]
            return [len(contour) for contour in outline_curves(segments, False, 'round', 'horizontal')]
        self.assertEqual(len({tuple(count(w, b)) for w in (10, 80, 200) for b in (50, 300, 700)}), 1)

    def test_glyph_state_wins_over_path_flag(self):
        drawn_later = Path([Node(0, 0), Node(100, 0)])
        drawn_later.attributes.clear()
        layer = Layer([drawn_later])
        self.assertEqual(bridge.expand_layer(layer, glyph_on=True), 1)
        pasted = Path([Node(0, 0), Node(100, 0)])  # carries the ON flag
        layer = Layer([pasted])
        self.assertEqual(bridge.expand_layer(layer, glyph_on=False), 0)
        self.assertEqual(layer.paths, [pasted])

    def test_normalize_follows_glyph_state(self):
        new = Path([Node(0, 0), Node(100, 0)])
        new.attributes.clear()
        layer = Layer([new])
        self.assertTrue(bridge.normalize_layer(layer, True))
        self.assertTrue(bridge.enabled(new))
        self.assertFalse(bridge.normalize_layer(layer, True))
        self.assertTrue(bridge.normalize_layer(layer, False))
        self.assertFalse(bridge.enabled(new))
        self.assertFalse(bridge.normalize_layer(layer, False))

    def test_paths_follow_master_default_until_overridden(self):
        master = types.SimpleNamespace(userData={bridge.MASTER_WIDTH_KEY: 80})
        path = Path([Node(0, 0), Node(100, 0)])
        for node in path.nodes:
            node.userData = {}
        layer = Layer([path])
        layer.master = master
        path.parent = layer
        self.assertEqual(bridge.stroke_width(path), 80)
        self.assertEqual(bridge.segments_for_path(path)[0][2][0], 80)
        self.assertEqual(bridge.segments_for_path(path, 30)[0][2][0], 30)  # detached copy
        path.attributes[bridge.STROKE_WIDTH_KEY] = 50
        self.assertEqual(bridge.stroke_width(path), 50)
        self.assertEqual(bridge.reset_width_overrides([layer]), 1)
        self.assertEqual(bridge.stroke_width(path), 80)
        master.userData[bridge.MASTER_WIDTH_KEY] = 120
        self.assertEqual(bridge.stroke_width(path), 120)
        self.assertFalse(bridge.normalize_layer(layer, True) and bridge.has_width_override(path))

    def test_height_and_offset_per_node(self):
        master = types.SimpleNamespace(userData={bridge.MASTER_WIDTH_KEY: 80,
                                                 bridge.MASTER_HEIGHT_KEY: 20}, italicAngle=10)
        path = Path([Node(0, 0), Node(100, 0)])
        for node in path.nodes:
            node.userData = {}
        path.nodes[1].userData = {bridge.SCALE_KEY: 50, bridge.OFFSET_KEY: -100}
        layer = Layer([path])
        layer.master = master
        path.parent = layer
        segs = bridge.segments_for_path(path)
        self.assertEqual(segs[0][2], (80.0, 20.0, 0.0))
        self.assertEqual(segs[0][3], (40.0, 10.0, -1.0))
        self.assertEqual(bridge.layer_defaults(layer).italic_angle, 10.0)
        path.attributes[bridge.STROKE_WIDTH_KEY] = 160  # keeps the master's 4:1
        self.assertEqual(bridge.stroke_height(path), 40.0)


if __name__ == '__main__':
    unittest.main()
