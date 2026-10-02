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
    def test_selected_bezier_handles_preview_their_owning_nodes(self):
        start = Node(0, 0)
        outgoing = Node(30, 20, 'offcurve')
        incoming = Node(70, 20, 'offcurve')
        end = Node(100, 0, 'curve')
        path = Path([start, outgoing, incoming, end])
        self.assertEqual(bridge.selected_nib_nodes(path, {outgoing}), [start])
        self.assertEqual(bridge.selected_nib_nodes(path, {incoming}), [end])
        self.assertEqual(bridge.selected_nib_nodes(path, {outgoing, incoming}),
                         [start, end])

    def test_closed_path_handle_wraps_to_its_owning_node(self):
        incoming = Node(0, 20, 'offcurve')
        end = Node(0, 0, 'curve')
        outgoing = Node(20, 0, 'offcurve')
        next_handle = Node(40, 0, 'offcurve')
        next_node = Node(60, 0, 'curve')
        closing_outgoing = Node(80, 20, 'offcurve')
        path = Path([incoming, end, outgoing, next_handle, next_node,
                     closing_outgoing], True)
        self.assertEqual(bridge.selected_nib_nodes(path, {incoming}), [end])
        self.assertEqual(bridge.selected_nib_nodes(path, {outgoing}), [end])

    def test_ellipse_cap_width_handles_follow_rotated_nib_contact(self):
        from variable_stroke_core import ellipse_nib_edges
        start, end = Node(0, 0), Node(100, 0)
        path = Path([start, end])
        path.attributes[bridge.STROKE_WIDTH_KEY] = 165
        path.attributes[bridge.STROKE_HEIGHT_KEY] = 70
        path.attributes[bridge.CAP_END_KEY] = 'ellipse'
        for node in path.nodes:
            node.userData.clear()
            node.userData[bridge.ROTATION_KEY] = 30
        actual = dict(bridge.edges_for_path(path))[end]
        expected = ellipse_nib_edges((100, 0), (100, 0), (165, 70, 0, 30))
        for point, target in zip(actual, expected):
            self.assertAlmostEqual(point[0], target[0])
            self.assertAlmostEqual(point[1], target[1])

    def test_outline_reads_each_node_once_through_the_glyphs_proxy(self):
        reads = []

        class Methods:
            def __init__(self, node):
                self.node = node

            def userData(self):
                reads.append(self.node)
                return dict(self.node.userData)

        class CountedNode(Node):
            @property
            def pyobjc_instanceMethods(self):
                return Methods(self)

        nodes = [CountedNode(0, 0), CountedNode(30, 60, 'offcurve'),
                 CountedNode(70, 60, 'offcurve'), CountedNode(100, 0, 'curve'),
                 CountedNode(200, 0)]
        for node in nodes:
            node.userData = {bridge.SCALE_KEY: 120.0, bridge.CORNER_ON_KEY: True}
        path = Path(nodes)
        Layer([path])
        bridge.curves_for_path(path, 40.0)
        self.assertEqual(sorted(map(id, reads)),
                         sorted(id(node) for node in nodes if node.type != 'offcurve'))

    def test_preview_builds_each_unchanged_outline_once(self):
        built = []

        class CopyablePath(Path):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                built.append(self)

            def copy(self):
                twin = Path(list(self.nodes), self.closed)
                twin.attributes = dict(self.attributes)
                return twin

        original = bridge.GSPath
        bridge.GSPath = CopyablePath
        try:
            def layer():  # coordinates no other test uses, so nothing is cached yet
                nodes = [Node(17, 0), Node(17, 311), Node(219, 311)]
                return Layer([CopyablePath(nodes)])
            first, second = layer(), layer()
            del built[:]
            bridge.expand_layer(first, True, 40.0)
            count = len(built)
            bridge.expand_layer(second, True, 40.0)
        finally:
            bridge.GSPath = original
        self.assertGreater(count, 0)
        self.assertEqual(len(built), count)  # the second layer got copies
        self.assertTrue(all(bridge.is_outline(path) for path in second.paths))
        self.assertEqual([len(path.nodes) for path in first.paths],
                         [len(path.nodes) for path in second.paths])

    def test_width_handles_come_from_the_outline_joins(self):
        from variable_stroke_core import node_edges
        nodes = [Node(0, 0), Node(0, 300), Node(300, 300), Node(300, 0)]
        for node in nodes:
            node.userData.clear()
        nodes[1].userData[bridge.CORNER_ON_KEY] = True
        path = Path(nodes, closed=True)
        Layer([path])
        expected = node_edges(bridge.segments_for_path(path, 40.0), True)
        actual = bridge.edges_for_path(path, 40.0)
        self.assertEqual([node for node, _ in actual], nodes)
        self.assertEqual([pair for _, pair in actual], [expected[i] for i in range(4)])

    def test_ellipse_cap_preview_targets_only_configured_open_ends(self):
        start, middle, end = Node(0, 0), Node(50, 50), Node(100, 0)
        path = Path([start, middle, end])
        self.assertEqual(bridge.ellipse_cap_nodes(path), [])
        path.attributes[bridge.CAP_END_KEY] = 'ellipse'
        self.assertEqual(bridge.ellipse_cap_nodes(path), [end])
        path.attributes[bridge.CAP_START_KEY] = 'ellipse'
        self.assertEqual(bridge.ellipse_cap_nodes(path), [start, end])
        path.closed = True
        self.assertEqual(bridge.ellipse_cap_nodes(path), [])

    def test_nib_axis_handles_keep_the_other_axis_size(self):
        node = Node(0, 0)
        node.userData.clear()
        bridge.set_node_nib_size(node, 'width', 150, 100, 80)
        self.assertEqual(bridge.scale(node), 150)
        self.assertEqual(bridge.height_scale(node), 100)
        bridge.set_node_nib_size(node, 'height', 120, 100, 80)
        self.assertEqual(bridge.scale(node), 150)
        self.assertEqual(bridge.height_scale(node), 150)

        other = Node(0, 0)
        other.userData.clear()
        other.userData[bridge.SCALE_KEY] = 125
        bridge.set_node_nib_size(other, 'height', 120, 100, 80)
        self.assertEqual(bridge.scale(other), 125)
        self.assertEqual(bridge.height_scale(other), 150)

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

    def test_node_width_and_height_percentages_share_only_when_unset(self):
        path = Path([Node(0, 0), Node(100, 0)])
        for node in path.nodes:
            node.userData = {}
        path.attributes[bridge.STROKE_WIDTH_KEY] = 80
        path.attributes[bridge.STROKE_HEIGHT_KEY] = 120
        node = path.nodes[0]
        self.assertEqual(bridge.node_nib(node, path, 80, 120)[:2], (80, 120))
        node.userData[bridge.SCALE_KEY] = 50
        self.assertEqual(bridge.node_nib(node, path, 80, 120)[:2], (40, 60))
        node.userData[bridge.HEIGHT_SCALE_KEY] = 150
        self.assertEqual(bridge.node_nib(node, path, 80, 120)[:2], (40, 180))
        del node.userData[bridge.SCALE_KEY]
        self.assertEqual(bridge.node_nib(node, path, 80, 120)[:2], (120, 180))

    def test_node_percentages_accept_saved_string_values(self):
        node = Node(0, 0)
        node.userData = {bridge.SCALE_KEY: '75', bridge.HEIGHT_SCALE_KEY: '125'}
        self.assertEqual(bridge.scale(node), 75.0)
        self.assertEqual(bridge.height_scale(node), 125.0)

    def test_interpolation_blends_node_width_and_height_separately(self):
        def master(width_percent, height_percent):
            path = Path([Node(0, 0), Node(100, 0)])
            path.nodes[0].userData = {bridge.SCALE_KEY: width_percent,
                                      bridge.HEIGHT_SCALE_KEY: height_percent}
            path.nodes[1].userData = {}
            path.attributes[bridge.STROKE_WIDTH_KEY] = 80
            path.attributes[bridge.STROKE_HEIGHT_KEY] = 120
            return Layer([path])
        glyph = types.SimpleNamespace(
            layers={'a': master(50, 100), 'b': master(100, 50)},
            userData={bridge.GLYPH_KEY: True})
        target = master(50, 100)
        bridge.interpolate_layer(target, glyph, {'a': 0.5, 'b': 0.5})
        node = target.paths[0].nodes[0]
        self.assertEqual(node.userData[bridge.SCALE_KEY], 75)
        self.assertEqual(node.userData[bridge.HEIGHT_SCALE_KEY], 75)
        self.assertEqual(bridge.node_nib(node, target.paths[0], 80, 120)[:2], (60, 90))

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

    def test_master_angle_applies_without_node_override(self):
        master = types.SimpleNamespace(userData={bridge.MASTER_ANGLE_KEY: '45'},
                                       italicAngle=0)
        defaults = bridge.master_defaults(master)
        path = Path([Node(0, 0), Node(100, 0)])
        for node in path.nodes:
            node.userData = {}
        self.assertEqual(bridge.segments_for_path(path, defaults)[0][2][3], 45)
        path.nodes[0].userData[bridge.ROTATION_KEY] = 90
        self.assertEqual(bridge.segments_for_path(path, defaults)[0][2][3], 90)

    def test_ellipse_angles_interpolate_across_180_boundary(self):
        self.assertAlmostEqual(bridge._blend_angles([(170, 0.5), (10, 0.5)]), 0)

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

    def test_corner_sides_default_linked_and_can_be_independent(self):
        node = Node(0, 0)
        node.userData = {bridge.CORNER_ON_KEY: True, bridge.CORNER_KEY: 30,
                         bridge.CORNER_TENSION_KEY: 125,
                         bridge.CORNER_RATIO_KEY: 140}
        for kind in ('radius', 'tension', 'ratio'):
            self.assertTrue(bridge.corner_linked(node, kind))
            self.assertEqual(bridge.corner_side_key(node, kind, 'inner'),
                             bridge.corner_side_key(node, kind, 'outer'))
            bridge.set_corner_linked(node, kind, False)
            self.assertFalse(bridge.corner_linked(node, kind))
            self.assertNotEqual(bridge.corner_side_key(node, kind, 'inner'),
                                bridge.corner_side_key(node, kind, 'outer'))
        spec = bridge.corner_spec(node)
        self.assertEqual((spec['inner'], spec['inner_tension'], spec['inner_ratio']),
                         (30, 125, 140))
        node.userData[bridge.CORNER_INNER_KEY] = 12
        node.userData[bridge.CORNER_INNER_TENSION_KEY] = 75
        node.userData[bridge.CORNER_INNER_RATIO_KEY] = 80
        spec = bridge.corner_spec(node)
        self.assertEqual((spec['inner'], spec['inner_tension'], spec['inner_ratio']),
                         (12, 75, 80))
        for kind in ('radius', 'tension', 'ratio'):
            bridge.set_corner_linked(node, kind, True)
            self.assertTrue(bridge.corner_linked(node, kind))
        spec = bridge.corner_spec(node)
        self.assertEqual((spec['inner'], spec['inner_tension'], spec['inner_ratio']),
                         (30, 125, 140))

    def test_cap_corner_widgets_use_left_and_right_values(self):
        path = Path([Node(0, 0), Node(150, 0)])
        for node in path.nodes:
            node.userData = {}
        end = path.nodes[-1]
        end.userData.update({bridge.CORNER_ON_KEY: True, bridge.CORNER_KEY: 30,
                             bridge.CORNER_INNER_KEY: 12,
                             bridge.CORNER_TENSION_KEY: 120,
                             bridge.CORNER_INNER_TENSION_KEY: 65,
                             bridge.CORNER_RATIO_KEY: 130,
                             bridge.CORNER_INNER_RATIO_KEY: 75})
        widgets = [w for w in bridge.corner_widgets(path, 60) if w['node'] is end]
        self.assertEqual({w['which'] for w in widgets}, {'outer', 'inner'})
        left = next(w for w in widgets if w['which'] == 'outer')
        right = next(w for w in widgets if w['which'] == 'inner')
        self.assertNotEqual(left['first'], right['first'])

    def test_interpolation_keeps_independent_corner_strength_and_ratio(self):
        def master(inner_tension, inner_ratio):
            path = Path([Node(0, 0), Node(0, 300), Node(300, 300)])
            for node in path.nodes:
                node.userData = {}
            path.nodes[1].userData.update({bridge.CORNER_ON_KEY: True,
                                           bridge.CORNER_KEY: 20,
                                           bridge.CORNER_TENSION_KEY: 100,
                                           bridge.CORNER_INNER_TENSION_KEY: inner_tension,
                                           bridge.CORNER_RATIO_KEY: 100,
                                           bridge.CORNER_INNER_RATIO_KEY: inner_ratio})
            return Layer([path])
        glyph = types.SimpleNamespace(layers={'a': master(50, 80),
                                               'b': master(150, 140)},
                                      userData={bridge.GLYPH_KEY: True})
        target = master(100, 100)
        bridge.interpolate_layer(target, glyph, {'a': 0.5, 'b': 0.5})
        spec = bridge.corner_spec(target.paths[0].nodes[1])
        self.assertEqual(spec['inner_tension'], 100)
        self.assertEqual(spec['inner_ratio'], 110)
        self.assertEqual(spec['tension'], 100)
        self.assertEqual(spec['ratio'], 100)

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
