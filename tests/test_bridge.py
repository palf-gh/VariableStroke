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
        self.userData = {}
        for path in self.shapes:
            path.parent = self

    @property
    def paths(self):
        return [shape for shape in self.shapes if isinstance(shape, Path)]


fake.GSPath = Path
fake.GSNode = Node
sys.modules['GlyphsApp'] = fake
bridge = importlib.import_module('glyphs_bridge')


class BridgeTests(unittest.TestCase):
    def test_export_union_follows_glyphs_overlap_checkbox(self):
        from export_settings import remove_overlap_enabled
        class Defaults:
            def __init__(self, value):
                self.value = value

            def objectForKey_(self, key):
                self.key = key
                return self.value

        for value, expected in ((None, True), (True, True), (False, False)):
            defaults = Defaults(value)
            self.assertEqual(remove_overlap_enabled(defaults), expected)
            self.assertEqual(defaults.key, 'OTFExportRemoveOverlap')

    def test_export_unions_overlapping_stroke_outlines(self):
        from outline_union import _pathops, union_layer_outlines
        try:
            _pathops()
        except ImportError:
            self.skipTest('Skia PathOps binary is unavailable on this platform')
        def rectangle(x0, y0, x1, y1):
            points = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
            return [('line', (points[i], points[(i+1) % 4])) for i in range(4)]
        source = Path()
        outlines = bridge.generated_paths(source, contours=[rectangle(0, 0, 100, 100)])
        outlines += bridge.generated_paths(source, contours=[rectangle(50, 50, 150, 150)])
        layer = Layer(outlines)
        self.assertEqual(union_layer_outlines(layer), 1)
        self.assertEqual(len(layer.paths), 1)
        self.assertTrue(layer.paths[0].attributes.get(bridge.OUTLINE_KEY))

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
        glyph = types.SimpleNamespace(layers={'a': master(40, (100, 50)), 'b': master(100, (100, 100))},
                                      userData={bridge.GLYPH_KEY: True})
        target = master(40, (100, 50))
        self.assertTrue(bridge.interpolate_widths(target, glyph, {'a': 0.5, 'b': 0.5}))
        self.assertTrue(target.userData[bridge.LAYER_STATE_KEY])
        segs = bridge.segments_for_path(target.paths[0])
        self.assertAlmostEqual(segs[0][2][0], 70.0)  # (40 + 100) / 2
        self.assertAlmostEqual(segs[0][3][0], 60.0)  # (20 + 100) / 2

    def test_interpolated_layer_blends_section_rotation(self):
        def master(angle):
            path = Path([Node(0, 0), Node(100, 0)])
            path.nodes[1].userData = {bridge.ROTATION_KEY: angle}
            return Layer([path])
        glyph = types.SimpleNamespace(layers={'a': master(-30), 'b': master(30)},
                                      userData={bridge.GLYPH_KEY: True})
        target = master(0)
        self.assertTrue(bridge.interpolate_layer(target, glyph, {'a': 0.25, 'b': 0.75}))
        self.assertAlmostEqual(bridge.segments_for_path(target.paths[0])[0][3][3], 15)

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
        self.assertEqual(segs[0][2], (80.0, 20.0, 0.0, 0.0))
        self.assertEqual(segs[0][3], (40.0, 10.0, -1.0, 0.0))
        self.assertEqual(bridge.layer_defaults(layer).italic_angle, 10.0)
        path.attributes[bridge.STROKE_WIDTH_KEY] = 160
        self.assertEqual(bridge.stroke_height(path), 20.0)
        self.assertEqual(bridge.segments_for_path(path)[0][2][:2], (160.0, 20.0))

    def test_width_does_not_change_unspecified_height(self):
        path = Path([Node(0, 0), Node(100, 0)])
        for node in path.nodes:
            node.userData = {}
        path.attributes[bridge.STROKE_WIDTH_KEY] = 80
        self.assertEqual(bridge.stroke_height(path), 40.0)
        path.attributes[bridge.STROKE_WIDTH_KEY] = 160
        self.assertEqual(bridge.stroke_height(path), 40.0)

    def test_corner_radius_rounds_only_marked_nodes(self):
        path = Path([Node(0, 0), Node(0, 300), Node(300, 300)])
        for node in path.nodes:
            node.userData = {}
        sharp = [len(c) for c in bridge.curves_for_path(path, 60)]
        path.nodes[1].userData[bridge.CORNER_KEY] = 20
        rounded = bridge.curves_for_path(path, 60)
        self.assertEqual([len(c) for c in rounded], [sharp[0] + 2])  # outer + inner arc
        path.nodes[1].userData[bridge.CORNER_KEY] = 0  # still compatible
        self.assertEqual([len(c) for c in bridge.curves_for_path(path, 60)], [sharp[0] + 2])
        path.nodes[1].userData[bridge.CORNER_ON_KEY] = False  # switched off, values kept
        self.assertEqual([len(c) for c in bridge.curves_for_path(path, 60)], sharp)
        path.nodes[1].userData.update({bridge.CORNER_ON_KEY: True, bridge.CORNER_KEY: 30,
                                       bridge.CORNER_INNER_KEY: 5})
        widgets = bridge.corner_widgets(path, 60)
        self.assertEqual(sorted(w['which'] for w in widgets), ['inner', 'outer'])
        self.assertTrue(all(w['node'] is path.nodes[1] for w in widgets))
        outer = next(w for w in widgets if w['which'] == 'outer')
        # 90 degree turn, circular: the arc starts one radius before the corner.
        self.assertAlmostEqual(outer['first'], 30.0, places=3)
        self.assertAlmostEqual(outer['second'], 30.0, places=3)

    def test_corner_on_in_one_master_keeps_masters_compatible(self):
        def master():
            path = Path([Node(0, 0), Node(0, 300), Node(300, 300)])
            for node in path.nodes:
                node.userData = {}
            return Layer([path])
        bold, light = master(), master()
        glyph = types.SimpleNamespace(layers=[bold, light], userData={bridge.GLYPH_KEY: True})
        bold.parent = light.parent = glyph
        bold.paths[0].nodes[1].userData[bridge.CORNER_ON_KEY] = True
        bold.paths[0].nodes[1].userData[bridge.CORNER_KEY] = 30
        # Not yet known to use corners: other masters are not consulted...
        self.assertIsNone(bridge.corner_specs(light.paths[0])[1])
        # ...until an edit of the layer (normalize) notices the corner.
        bridge.normalize_layer(bold, True)
        self.assertTrue(glyph.userData[bridge.GLYPH_CORNERS_KEY])
        counts = [[len(c) for c in bridge.curves_for_path(layer.paths[0], 60)]
                  for layer in (bold, light)]
        self.assertEqual(counts[0], counts[1])
        self.assertEqual(bridge.corner_specs(light.paths[0])[1], bridge.ZERO_CORNER)
        self.assertIsNone(bridge.corner_specs(light.paths[0])[0])

    def test_interpolated_layer_gets_strokes_and_blended_corner(self):
        def master(radius):
            path = Path([Node(0, 0), Node(0, 300), Node(300, 300)])
            path.attributes[bridge.CAP_END_KEY] = 'round'
            for node in path.nodes:
                node.userData = {}
            if radius is not None:
                path.nodes[1].userData.update({bridge.CORNER_ON_KEY: True,
                                               bridge.CORNER_KEY: radius})
            return Layer([path])
        glyph = types.SimpleNamespace(layers={'a': master(40), 'b': master(None)},
                                      userData={bridge.GLYPH_KEY: True})
        target = master(None)
        target.paths[0].attributes.clear()  # Glyphs' interpolated copy lacks our attributes
        bridge.interpolate_layer(target, glyph, {'a': 0.5, 'b': 0.5})
        self.assertTrue(bridge.enabled(target.paths[0]))
        self.assertEqual(target.paths[0].attributes[bridge.CAP_END_KEY], 'round')
        self.assertEqual(bridge.corner_spec(target.paths[0].nodes[1])['outer'], 20.0)
        off = types.SimpleNamespace(layers=glyph.layers, userData={})
        other = master(None)
        bridge.interpolate_layer(other, off, {'a': 1.0})
        self.assertFalse(other.userData[bridge.LAYER_STATE_KEY])

    def test_generated_outlines_are_never_expanded_again(self):
        stroke = Path([Node(0, 0), Node(0, 300), Node(300, 300)])
        layer = Layer([stroke])
        bridge.expand_layer(layer, glyph_on=True)
        once = [len(path.nodes) for path in layer.paths]
        self.assertTrue(all(bridge.is_outline(path) for path in layer.paths))
        self.assertEqual(bridge.expand_layer(layer, glyph_on=True), 0)  # prepared twice
        self.assertEqual([len(path.nodes) for path in layer.paths], once)
        self.assertFalse(bridge.normalize_layer(layer, True))  # not flagged as strokes

    def test_user_conversion_leaves_plain_paths(self):
        layer = Layer([Path([Node(0, 0), Node(100, 0)])])
        bridge.convert_layer(layer, keep_marks=False)
        self.assertFalse(any(bridge.is_outline(path) for path in layer.paths))
        self.assertFalse(bridge.layer_state(layer))

    def test_glyph_conversion_turns_variable_stroke_off(self):
        layers = [Layer([Path([Node(0, 0), Node(100, 0)])]) for _ in range(2)]
        glyph = types.SimpleNamespace(layers=layers, userData={bridge.GLYPH_KEY: True})
        for layer in layers:
            layer.parent = glyph
        self.assertEqual(bridge.convert_glyph(glyph, keep_marks=False), 2)
        self.assertFalse(bridge.glyph_enabled(glyph))
        self.assertTrue(all(bridge.layer_state(layer) is False for layer in layers))
        self.assertTrue(all(not bridge.enabled(path) and not bridge.is_outline(path)
                            for layer in layers for path in layer.paths))
        self.assertEqual(sum(bridge.expand_layer(layer) for layer in layers), 0)

    def test_export_conversion_keeps_editable_glyph_state(self):
        layer = Layer([Path([Node(0, 0), Node(100, 0)])])
        glyph = types.SimpleNamespace(layers=[layer], userData={bridge.GLYPH_KEY: True})
        layer.parent = glyph
        self.assertEqual(bridge.convert_glyph(glyph), 1)
        self.assertTrue(bridge.glyph_enabled(glyph))
        self.assertTrue(bridge.is_outline(layer.paths[0]))

    def test_quick_structure_check_matches_segments(self):
        import itertools
        kinds = ['line', 'curve', 'offcurve']
        for count in range(1, 6):
            for combo in itertools.product(kinds, repeat=count):
                for closed in (False, True):
                    path = Path([Node(i*10, i*5, kind) for i, kind in enumerate(combo)], closed)
                    try:
                        bridge.segments_for_path(path, 40)
                        expected = any(kind != 'offcurve' for kind in combo)
                    except ValueError:
                        expected = False
                    with self.subTest(combo=combo, closed=closed):
                        self.assertEqual(bridge._valid_structure(path), expected)


if __name__ == '__main__':
    unittest.main()
