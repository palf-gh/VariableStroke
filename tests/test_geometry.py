import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from variable_stroke_core import (outline, outline_curves, node_edges, nib_edges,
                                  move_outline_vertices, outline_direction_at_vertex,
                                  cap_curve_geometry, cap_curve_coordinates,
                                  _Side, _join, sub, unit, length, cubic)


class GeometryTests(unittest.TestCase):
    def test_corner_slide_uses_line_before_curve_and_curve_handle_otherwise(self):
        point = (10, 10)
        curve = ('cubic', ((0, 0), (2, 8), (8, 6), point))
        line = ('line', (point, (20, 10)))
        direction = outline_direction_at_vertex([[curve]], point)
        self.assertAlmostEqual(direction[0], 1 / math.sqrt(5))
        self.assertAlmostEqual(direction[1], 2 / math.sqrt(5))
        direction = outline_direction_at_vertex([[curve, line]], point)
        self.assertEqual(direction, (1.0, 0.0))

    def test_moving_bezier_corner_preserves_its_handle_direction(self):
        curve = ('cubic', ((0, 0), (2, 8), (8, 6), (10, 10)))
        moved = move_outline_vertices([[curve]], {0: ((10, 10), (50, 50))},
                                      {0: ((-3, 4), (0, 0))})[0][0][1]
        self.assertEqual(moved[-1], (7, 14))
        self.assertEqual(moved[-2], (5, 10))
        self.assertEqual(sub(moved[-1], moved[-2]),
                         sub(curve[1][-1], curve[1][-2]))

    def test_corner_offset_moves_one_outline_vertex_without_changing_structure(self):
        segments = [('line', ((0, 0), (100, 0)), 20, 20),
                    ('line', ((100, 0), (100, 100)), 20, 20)]
        edges = {}
        original = outline_curves(segments, edges=edges)
        target = edges[1][0]
        moved = move_outline_vertices(original, edges, {1: ((-25, -10), (0, 0))})
        self.assertEqual(edges[1][0], (target[0]-25, target[1]-10))
        self.assertEqual([piece[0] for piece in moved[0]],
                         [piece[0] for piece in original[0]])
        for before, after in zip(moved[0], moved[0][1:] + moved[0][:1]):
            self.assertEqual(before[1][-1], after[1][0])
        self.assertIn(edges[1][0], [piece[1][0] for piece in moved[0]])

    def test_editable_cubic_caps_keep_endpoints_and_allow_concave_shape(self):
        segments = [('line', ((0, 0), (0, 100)), 20, 20)]
        settings = ((0.6, -0.5), (-0.4, -0.3))
        contour = outline_curves(segments, cap_start='curve', cap_end='curve',
                                 cap_end_curve=settings)[0]
        self.assertEqual([piece[0] for piece in contour],
                         ['line', 'cubic', 'line', 'cubic'])
        self.assertEqual(contour[1][1][0], (-10.0, 100.0))
        self.assertEqual(contour[1][1][-1], (10.0, 100.0))
        self.assertLess(contour[1][1][1][1], 100.0)
        self.assertLess(contour[1][1][2][1], 100.0)
        for before, after in zip(contour, contour[1:] + contour[:1]):
            self.assertEqual(before[1][-1], after[1][0])

    def test_cap_control_coordinates_follow_width_and_tangent(self):
        values = ((0.7, -0.2), (-0.6, 0.4))
        for left, right, tangent, at_end in (
                ((-10, 100), (10, 100), (0, 1), True),
                ((-20, 100), (20, 100), (0, 1), True),
                ((0, 10), (0, -10), (1, 0), False)):
            points, _ = cap_curve_geometry(left, right, tangent, at_end, values)
            for value, point in zip(values, points[1:3]):
                measured = cap_curve_coordinates(left, right, tangent, at_end, point)
                self.assertAlmostEqual(measured[0], value[0])
                self.assertAlmostEqual(measured[1], value[1])

    def test_curve_mode_preserves_each_cap_cut_and_ignores_round_and_ellipse(self):
        segments = [('line', ((0, 0), (100, 100)), 20, 20)]
        settings = ((0.6, -0.5), (-0.4, -0.3))
        for style in ('flat', 'square', 'horizontal', 'vertical', 'angle'):
            with self.subTest(style=style):
                straight = outline_curves(segments, cap_end=style)[0]
                curved = outline_curves(segments, cap_end=style,
                                        cap_end_curve=settings)[0]
                self.assertEqual(curved[1][0], 'cubic')
                self.assertEqual((curved[1][1][0], curved[1][1][-1]),
                                 (straight[1][1][0], straight[1][1][-1]))
        for style in ('round', 'ellipse'):
            self.assertEqual(outline_curves(segments, cap_end=style),
                             outline_curves(segments, cap_end=style,
                                            cap_end_curve=settings))

    def test_nib_angle_rotates_width_and_height_axes(self):
        left, right = nib_edges((0, 0), (1, 0), (165, 140, 0, 30))
        expected = math.hypot(165*math.sin(math.radians(30)),
                              140*math.cos(math.radians(30)))
        self.assertAlmostEqual(length(sub(left, right)), expected)
        self.assertAlmostEqual(left[1] - right[1], expected)
        self.assertAlmostEqual(left[0], -right[0])
        self.assertAlmostEqual(left[0], 0)
        plain = nib_edges((0, 0), (1, 0), (165, 140, 0, 0))
        self.assertEqual(plain, ((0.0, 70.0), (0.0, -70.0)))
        turned = nib_edges((0, 0), (1, 0), (165, 140, 0, 90))
        self.assertAlmostEqual(turned[0][1] - turned[1][1], 165)
        vertical = nib_edges((0, 0), (0, 1), (165, 140, 0, 90))
        self.assertAlmostEqual(vertical[0][0] - vertical[1][0], -140)

    def test_node_rotation_changes_only_its_own_width_section(self):
        nib0, nib1 = (165, 140, 0, 0), (165, 140, 0, 30)
        contour = outline_curves([('line', ((0, 0), (100, 0)), nib0, nib1)])[0]
        self.assertEqual(contour[0][1][0], (0.0, 70.0))
        self.assertEqual(contour[0][1][1][0], 100)
        self.assertGreater(contour[0][1][1][1], 70)
        self.assertLess(contour[2][1][0][1], -70)

    def test_nib_angle_crossing_180_does_not_turn_through_90(self):
        side = _Side('line', ((0, 0), (100, 0)),
                     (165, 40, 0, 179), (165, 40, 0, 1), 1)
        self.assertLess(side.at(0.5)[1], 25)
        self.assertAlmostEqual(side.at(0.5)[1], 20, delta=0.1)

    def test_line_after_smooth_join_takes_next_corner(self):
        # J: a curve flowing smoothly into a short stem, then a corner into
        # the top bar. The stem's sides must end at that corner's join.
        segments = [('cubic', ((0, 0), (354, 0), (447, 225), (447, 534)), 90, 90),
                    ('line', ((447, 534), (447, 670)), 90, 90),
                    ('line', ((447, 670), (98, 670)), 90, 90)]
        contour = outline_curves(segments)[0]
        for (_, before), (_, after) in zip(contour, contour[1:] + contour[:1]):
            self.assertLess(length(sub(before[-1], after[0])), 1e-6)

    def test_smooth_centerline_join_preserves_outline_tangent(self):
        nib0, nib1, nib2 = ((165, 140, 0, 0),
                              (165, 140, 0, 0),
                              (165, 70, 0, 0))
        segments = [
            ('cubic', ((0, 0), (70, 0), (130, 100), (200, 100)), nib0, nib1),
            ('cubic', ((200, 100), (270, 100), (330, 0), (400, 0)), nib1, nib2),
        ]
        contour = outline_curves(segments)[0]
        for incoming, outgoing in ((contour[0][1], contour[1][1]),
                                    (contour[3][1], contour[4][1])):
            before = unit(sub(incoming[-1], incoming[-2]))
            after = unit(sub(outgoing[1], outgoing[0]))
            self.assertGreater(before[0]*after[0] + before[1]*after[1], 0.995)

    def test_nearly_smooth_svg_join_stays_smooth_at_heavy_width(self):
        # SVG: M0,78.928c100-95,285-115,349,0c53,95,216,103,305,41
        nib = (165, 140, 0, 0)
        segments = [
            ('cubic', ((0, 78.928), (100, -16.072), (285, -36.072),
                       (349, 78.928)), nib, nib),
            ('cubic', ((349, 78.928), (402, 173.928), (565, 181.928),
                       (654, 119.928)), nib, nib),
        ]
        contour = outline_curves(segments)[0]
        for incoming, outgoing in ((contour[0][1], contour[1][1]),
                                    (contour[3][1], contour[4][1])):
            before = unit(sub(incoming[-1], incoming[-2]))
            after = unit(sub(outgoing[1], outgoing[0]))
            self.assertGreater(before[0]*after[0] + before[1]*after[1], 0.995)

    def test_nearby_svg_paths_keep_nearby_outlines(self):
        nib = (165, 140, 0, 0)
        outlines = []
        for y, first_control, second_control in ((33.374, 48, -41),
                                                  (33.224, 49, -42)):
            segments = [
                ('cubic', ((0, y), (100, y-95), (227, y+first_control),
                           (349, y)), nib, nib),
                ('cubic', ((349, y), (453, y+second_control), (565, y+103),
                           (654, y+41)), nib, nib),
            ]
            outlines.append(outline_curves(segments)[0])
        for index in (0, 1, 3, 4):
            first, second = outlines[0][index][1], outlines[1][index][1]
            for p, q in zip(first, second):
                self.assertLess(length(sub(p, q)), 3.0)

    def test_nearby_svg_paths_across_gentle_curve_threshold(self):
        nib = (165, 140, 0, 0)
        outlines = []
        for y, x, first_control, second_control in ((29.7, 244, 75, -74),
                                                     (29.823, 243, 74, -73)):
            segments = [
                ('cubic', ((0, y), (100, y-95), (x, y+first_control),
                           (349, y)), nib, nib),
                ('cubic', ((349, y), (453, y+second_control), (565, y+103),
                           (654, y+41)), nib, nib),
            ]
            outlines.append(outline_curves(segments)[0])
        for index in (0, 1, 3, 4):
            for p, q in zip(outlines[0][index][1], outlines[1][index][1]):
                self.assertLess(length(sub(p, q)), 3.0)

    def test_rotated_smooth_node_keeps_source_handle_angle(self):
        plain, tilted = (165, 140, 0, 0), (165, 140, 0, 30)
        segments = [
            ('cubic', ((0, 0), (70, 0), (130, 100), (200, 100)), plain, tilted),
            ('cubic', ((200, 100), (270, 100), (330, 0), (400, 0)), tilted, plain),
        ]
        contour = outline_curves(segments)[0]
        for incoming, outgoing in ((contour[0][1], contour[1][1]),
                                    (contour[3][1], contour[4][1])):
            self.assertEqual(incoming[-1], outgoing[0])
            before = sub(incoming[-1], incoming[-2])
            after = sub(outgoing[1], outgoing[0])
            self.assertGreater(length(before), 1)
            self.assertGreater(length(after), 1)
            self.assertAlmostEqual(before[1], 0, places=5)
            self.assertAlmostEqual(after[1], 0, places=5)

    def test_cut_extension_does_not_change_the_curve_handles(self):
        segment = [('cubic', ((0, 0), (50, 50), (150, 100), (200, 150)),
                    (120, 80, 0), (120, 80, 0))]
        flat = outline_curves(segment)[0]
        cut = outline_curves(segment, cap_end='horizontal')[0]
        # The right side extends its existing cubic polynomial, so the original
        # curve interval is identical even though the endpoint moves.
        from variable_stroke_core import cubic, length
        original = tuple(reversed(flat[2][1]))
        extended = tuple(reversed(cut[2][1]))
        factor = length(sub(extended[1], extended[0])) / \
                 length(sub(original[1], original[0]))
        self.assertGreater(factor, 1)
        for i in range(11):
            u = i/(10*factor)
            self.assertLess(length(sub(cubic(*extended, u), cubic(*original, u*factor))),
                            1e-6)
        self.assertEqual(len(cut), len(flat))
        self.assertTrue(all(abs(point[1]-150) < 1e-6 for point in cut[1][1]))

    def test_cut_corner_widgets_stay_on_the_cut(self):
        segment = [('cubic', ((0, 0), (50, 50), (150, 100), (200, 150)),
                    (120, 80, 0), (120, 80, 0))]
        report = []
        outline_curves(segment, cap_end='horizontal',
                       corner_radii=[None, {'outer': 10}], report=report)
        self.assertEqual(len(report), 2)
        self.assertTrue(all(widget['node'] == 1 and widget['cap'] and
                            abs(widget['corner'][1]-150) < 1e-6 for widget in report))

    def test_gentle_centerline_keeps_a_forward_outline_at_large_width(self):
        from variable_stroke_core import cubic
        side = _Side('cubic', ((0, 0), (100, 10), (200, 90), (300, 100)),
                     800, 800, -1)
        controls = side.piece()[1]
        chord = unit(sub(controls[-1], controls[0]))
        points = [cubic(*controls, i/32.0) for i in range(33)]
        progress = [p[0]*chord[0] + p[1]*chord[1] for p in points]
        self.assertTrue(all(b > a for a, b in zip(progress, progress[1:])))
        last_handle = unit(sub(controls[-1], controls[-2]))
        self.assertGreater(last_handle[0]*chord[0] + last_handle[1]*chord[1], 0.5)

    def test_gentle_bend_remains_visible_in_wide_outline(self):
        from variable_stroke_core import cubic, normal
        source = ((0, 0), (70, 20), (230, 120), (300, 100))
        side = _Side('cubic', source, 200, 200, -1)
        edge = side.piece()[1]
        bend_normal = normal(unit(sub(source[-1], source[0])))

        def bend(points):
            middle = cubic(*points, 0.5)
            chord_middle = tuple((a+b)*0.5 for a, b in zip(points[0], points[-1]))
            return sum((middle[i]-chord_middle[i])*bend_normal[i] for i in range(2))

        self.assertGreater(bend(edge)/bend(source), 0.65)
        direction = unit(sub(edge[-1], edge[0]))
        positions = [cubic(*edge, i/32.0) for i in range(33)]
        progress = [sum(p[j]*direction[j] for j in range(2)) for p in positions]
        self.assertTrue(all(b > a for a, b in zip(progress, progress[1:])))

    def test_curved_cut_keeps_variable_outline_structure(self):
        points = ((0, 0), (50, 50), (150, 100), (200, 150))
        for style in ('horizontal', 'vertical', 'angle', 'square'):
            counts = set()
            for width in (30, 120, 300, 600):
                segment = [('cubic', points, width, width)]
                contour = outline_curves(segment, cap_end=style, end_angle=10)[0]
                counts.add(len(contour))
                if style == 'horizontal':
                    self.assertTrue(all(abs(p[1]-150) < 1e-4
                                        for p in contour[1][1]))
                    start_cut = outline_curves(segment, cap_start='horizontal')[0][-1][1]
                    self.assertTrue(all(abs(p[1]) < 1e-4 for p in start_cut))
            self.assertEqual(counts, {4})

    def test_curve_line_corner_follows_curve_or_bends_smoothly(self):
        points = ((0, 0), (100, 0), (200, 50), (300, 50))
        for sign in (-1, 1):
            curve = _Side('cubic', points, 200, 200, sign)
            following = _Side('line', ((300, 50), (300, 200)), 200, 200, sign)
            _join(curve, following, 200)
            self.assertEqual(curve.end, following.start)
            self.assertIsNotNone(curve.override_piece)
            controls = curve.override_piece[1]
            from variable_stroke_core import cubic
            samples = [cubic(*controls, i/32.0) for i in range(33)]
            progress = [p[0] for p in samples]
            self.assertTrue(all(b >= a for a, b in zip(progress, progress[1:])))

    def test_long_curve_line_miter_keeps_a_visible_turn(self):
        from variable_stroke_core import cubic, _bend_curve_endpoint
        nib = (240, 326, 0)
        line = _Side('line', ((282, 0), (282, 415)), nib, nib, 1)
        curve = _Side('cubic', ((282, 415), (362, 291), (575, 232), (646, 212)),
                      nib, nib, 1)
        original = curve.piece()[1]
        _join(line, curve, 240)
        controls = curve.override_piece[1]
        plain = _bend_curve_endpoint(original, False, curve.start)
        self.assertEqual(line.end, curve.start)
        def nearest(piece, point):
            return min(math.dist(cubic(*piece, i/100.0), point)
                       for i in range(101))
        self.assertLess(sum(nearest(controls, curve.at(t)) for t in (0.25, 0.5)),
                        sum(nearest(plain, curve.at(t)) for t in (0.25, 0.5)))
        # The first tangent still reads as an extension of the blue curve,
        # rather than turning almost vertical at the miter.
        self.assertGreater(unit(sub(controls[1], controls[0]))[0], 0.25)
        points = [cubic(*controls, i/32.0) for i in range(33)]
        vectors = [sub(b, a) for a, b in zip(points, points[1:])]
        self.assertTrue(all(a[0]*b[1]-a[1]*b[0] > 0
                            for a, b in zip(vectors, vectors[1:])))

    def test_two_curved_mountain_sides_keep_their_offset_shape(self):
        from variable_stroke_core import cubic, _bend_curve_endpoint
        nib = (430, 340, 0)
        left_points = ((289, 709), (459, 612), (755, 311), (834, 227))
        right_points = ((834, 227), (913, 311), (1209, 612), (1379, 709))
        left = _Side('cubic', left_points, nib, nib, -1)
        right = _Side('cubic', right_points, nib, nib, -1)
        original = left.piece()[1]
        _join(left, right, 430)
        actual = left.override_piece[1]
        plain = _bend_curve_endpoint(original, True, left.end)
        self.assertEqual(left.end, right.start)
        def nearest(controls, target):
            return min(math.dist(cubic(*controls, i/100.0), target)
                       for i in range(101))
        self.assertLess(nearest(actual, left.at(0.5)),
                        nearest(plain, left.at(0.5)))
        samples = [cubic(*actual, i/32.0) for i in range(33)]
        directions = [sub(b, a) for a, b in zip(samples, samples[1:])]
        self.assertTrue(all(v[0] > 0 and v[1] < 0 for v in directions))

    def test_svg_curve_corner_tracks_width_derived_offset(self):
        from variable_stroke_core import cubic
        points = ((0, 457), (31, 419), (114, 385), (142, 377))
        nib = (95, 140, 0)
        for sign in (-1, 1):
            line = _Side('line', ((0, 0), (0, 457)), nib, nib, sign)
            curve = _Side('cubic', points, nib, nib, sign)
            _join(line, curve, 95)
            self.assertAlmostEqual(line.end[0], sign*-47.5, places=5)
            self.assertEqual(line.end, curve.start)
            controls = curve.override_piece[1]
            outline = [cubic(*controls, i/100.0) for i in range(101)]
            for t in (0.5, 0.75):
                self.assertLess(min(math.dist(curve.at(t), point)
                                    for point in outline), 10)

    def test_svg_open_path_keeps_inside_gentler_than_outside(self):
        from variable_stroke_core import _normalized_bend
        nib = (95, 140, 0)
        segments = [
            ('line', ((142, 512), (142, 0)), nib, nib),
            ('line', ((142, 0), (0, 0)), nib, nib),
            ('line', ((0, 0), (0, 457)), nib, nib),
            ('cubic', ((0, 457), (31, 419), (114, 385), (142, 377)), nib, nib),
        ]
        contour = outline_curves(segments, cap_end='vertical')[0]
        outside = abs(_normalized_bend(contour[3][1]))
        inside = abs(_normalized_bend(contour[5][1]))
        self.assertGreater(outside, 1.4*inside)
        source = abs(_normalized_bend(segments[-1][1]))
        self.assertGreater(outside, 1.2*source)
        self.assertLess(inside, source)

    def test_svg_mountain_inner_peak_trims_at_offset_crossing(self):
        left = ((0, 315), (93, 251), (306, 55), (357, 0))
        right = ((357, 0), (408, 55), (621, 251), (714, 315))
        nib = (165, 140, 0)
        a = _Side('cubic', left, nib, nib, 1)
        b = _Side('cubic', right, nib, nib, 1)
        _join(a, b, 165)
        self.assertEqual(a.end, b.start)
        self.assertAlmostEqual(a.end[0], 357)
        self.assertLess(math.dist(a.at(a.tb), a.end), 0.5)

    def test_svg_mountain_horizontal_cuts_follow_curves(self):
        from variable_stroke_core import _normalized_bend
        left = ((0, 315), (93, 251), (306, 55), (357, 0))
        right = ((357, 0), (408, 55), (621, 251), (714, 315))
        nib = (165, 140, 0)
        contour = outline_curves([('cubic', left, nib, nib),
                                  ('cubic', right, nib, nib)],
                                 cap_start='horizontal', cap_end='horizontal')[0]
        for cap in (contour[2], contour[-1]):
            self.assertEqual(cap[0], 'line')
            self.assertTrue(all(abs(p[1]-315) < 1e-6 for p in cap[1]))
        self.assertGreater(abs(_normalized_bend(contour[4][1])),
                           abs(_normalized_bend(contour[0][1])))
        self.assertGreater(abs(_normalized_bend(contour[4][1])),
                           1.1*abs(_normalized_bend(left)))

    def test_width_and_height_follow_stroke_direction_independently(self):
        for angle in (0, 15):
            horizontal = nib_edges((0, 0), (1, 0), (200, 30, 0), angle)
            vertical = nib_edges((0, 0), (0, 1), (200, 30, 0), angle)
            diagonal = nib_edges((0, 0), (1, 1), (200, 30, 0), angle)
            self.assertAlmostEqual(horizontal[0][1] - horizontal[1][1], 30)
            self.assertAlmostEqual(vertical[1][0] - vertical[0][0], 200)
            self.assertLess(30, math.dist(*diagonal))
            self.assertLess(math.dist(*diagonal), 200)

    def test_thick_curve_follows_offset_tangents(self):
        side = _Side('cubic', ((0, 0), (0, 100), (90, 200), (110, 300)),
                     220, 360, 1)
        p0, c1, c2, p3 = side.piece()[1]
        for actual, fitted in ((side.edge_tangent(0), unit(sub(c1, p0))),
                               (side.edge_tangent(1), unit(sub(p3, c2)))):
            self.assertGreater(actual[0]*fitted[0] + actual[1]*fitted[1], 0.999)

    def test_moved_curve_ends_do_not_create_spurious_s_bend(self):
        from variable_stroke_core import cubic
        side = _Side('cubic', ((0, 0), (95, 11), (261, 103), (320, 160)),
                     (134, 106, 0), (99, 113, 0), -1)
        side.start = (side.start[0]-9, side.start[1]+100)
        side.end = (side.end[0]+70, side.end[1]+95)
        controls = side.piece()[1]
        points = [cubic(*controls, i/20.0) for i in range(21)]
        vectors = [sub(b, a) for a, b in zip(points, points[1:])]
        turns = [a[0]*b[1]-a[1]*b[0] for a, b in zip(vectors, vectors[1:])]
        self.assertTrue(all(turn > 0 for turn in turns))

    def test_variable_width_vertical_line(self):
        poly = outline([('line', ((0, 0), (0, 100)), 20, 40)])[0]
        self.assertIn((-10.0, 0.0), poly)
        self.assertIn((10.0, 0.0), poly)
        self.assertIn((-20.0, 100.0), poly)
        self.assertIn((20.0, 100.0), poly)

    def test_absolute_cuts_through_endpoints(self):
        seg = [('line', ((0, 0), (100, 80)), 20, 20)]
        horizontal = outline(seg, cap_start='horizontal', cap_end='horizontal')[0]
        self.assertEqual(horizontal[0][1], 0)
        self.assertEqual(horizontal[-1][1], 0)
        self.assertEqual(sum(1 for _, y in horizontal if y == 80), 2)
        vertical = outline(seg, cap_start='vertical', cap_end='vertical')[0]
        self.assertEqual(vertical[0][0], 0)
        self.assertEqual(vertical[-1][0], 0)

    def test_cut_line_end_beside_a_smooth_curve_cuts_both_sides(self):
        # A straight end joined smoothly to a curve: the smooth join stores the
        # line's piece, and the cut must still move both of its corners.
        nib = (90, 20, 0, 0)
        segments = [('line', ((222, 32), (222, 167)), nib, nib),
                    ('cubic', ((222, 167), (222, 319), (281, 421), (453, 421)), nib, nib),
                    ('line', ((453, 421), (633, 421)), nib, nib)]
        for style, angle in (('horizontal', 0.0), ('angle', 18.0)):
            contour = outline_curves(segments, False, style, style, start_angle=angle,
                                     end_angle=angle)[0]
            m = (-math.sin(math.radians(angle)), math.cos(math.radians(angle)))
            for node in ((222, 32), (633, 421)):
                on_cut = [point for point in (segment[1][0] for segment in contour)
                          if abs((point[0]-node[0])*m[0] + (point[1]-node[1])*m[1]) < 1e-6]
                if style == 'horizontal' and node == (633, 421):
                    continue  # parallel to the stroke: falls back to flat
                with self.subTest(style=style, node=node):
                    self.assertEqual(len(on_cut), 2)

    def test_flat_nib_bend_edges_turn_one_way(self):
        # A wide, flat nib from a stem into a thin diagonal: the true offsets bend
        # back (inner notch, outer flattening). Both edges turn as one fillet.
        nib = (90, 20, 0, 0)
        segments = [('line', ((0, 227), (0, 144)), nib, nib),
                    ('cubic', ((0, 144), (0, 67), (78, 89), (113, 102)), nib, nib),
                    ('line', ((113, 102), (162, 121)), nib, nib)]
        contour = outline_curves(segments)[0]
        for start in ((45.0, 144.0), (-45.0, 144.0)):
            edge = next(points for kind, points in contour
                        if kind == 'cubic' and start in (points[0], points[3]))
            samples = [cubic(*edge, i/32.0) for i in range(33)]
            turns = [math.atan2(a[0]*b[1]-a[1]*b[0], a[0]*b[0]+a[1]*b[1])
                     for a, b in ((sub(q, p), sub(r, q)) for p, q, r
                                  in zip(samples, samples[1:], samples[2:]))]
            with self.subTest(start=start):
                self.assertTrue(all(t >= -1e-9 for t in turns) or
                                all(t <= 1e-9 for t in turns))
                if start[0] > 0:  # inner edge stays off the stem's outside
                    self.assertGreaterEqual(min(x for x, _ in samples), 45.0 - 1e-6)
                else:  # outer edge no further out than the stem's half width
                    self.assertGreaterEqual(min(x for x, _ in samples), -45.0 - 1e-6)

    def test_round_nib_bends_keep_their_fit(self):
        segments = [('line', ((0, 227), (0, 144)), 60, 60),
                    ('cubic', ((0, 144), (0, 67), (78, 89), (113, 102)), 60, 60),
                    ('line', ((113, 102), (162, 121)), 60, 60)]
        import variable_stroke_core as core
        saved = core.FILLET_REVERSE
        try:
            core.FILLET_REVERSE = float('inf')
            plain = outline_curves(segments)
        finally:
            core.FILLET_REVERSE = saved
        self.assertEqual(outline_curves(segments), plain)

    def test_square_and_round_extend(self):
        seg = [('line', ((0, 0), (100, 0)), 20, 20)]
        square = outline(seg, cap_start='square', cap_end='square')[0]
        self.assertAlmostEqual(min(x for x, _ in square), -10)
        self.assertAlmostEqual(max(x for x, _ in square), 110)
        round_poly = outline(seg, cap_start='round', cap_end='round')[0]
        self.assertLess(min(x for x, _ in round_poly), 0)
        self.assertGreater(max(x for x, _ in round_poly), 100)

    def test_cubic_and_closed_path(self):
        cubic = [('cubic', ((0, 0), (0, 50), (100, 50), (100, 0)), 20, 30)]
        self.assertGreater(len(outline(cubic)[0]), 20)
        square = [
            ('line', ((0, 0), (100, 0)), 20, 20),
            ('line', ((100, 0), (100, 100)), 20, 20),
            ('line', ((100, 100), (0, 100)), 20, 20),
            ('line', ((0, 100), (0, 0)), 20, 20),
        ]
        self.assertEqual(len(outline(square, closed=True)), 2)

    def test_bezier_fit_and_caps(self):
        seg = [('cubic', ((0, 0), (0, 80), (100, 80), (100, 0)), 20, 40)]
        contour = outline_curves(seg, cap_start='round', cap_end='horizontal')[0]
        self.assertTrue(any(kind == 'cubic' for kind, _ in contour))
        self.assertTrue(any(kind == 'line' for kind, _ in contour))
        horizontal = [points for kind, points in contour if kind == 'line'
                      and abs(points[0][1]) < 0.001 and abs(points[-1][1]) < 0.001]
        self.assertTrue(horizontal)

    def test_ellipse_cap_uses_along_path_nib_dimension(self):
        horizontal = [('line', ((0, 0), (100, 0)), (165, 140, 0, 0),
                       (165, 140, 0, 0))]
        round_end = outline_curves(horizontal, cap_end='round')[0]
        ellipse_end = outline_curves(horizontal, cap_end='ellipse')[0]
        self.assertEqual([kind for kind, _ in round_end],
                         [kind for kind, _ in ellipse_end])
        self.assertAlmostEqual(max(points[-1][0] for _, points in round_end), 170)
        self.assertAlmostEqual(max(points[-1][0] for _, points in ellipse_end), 182.5)
        vertical = [('line', ((0, 0), (0, 100)), (165, 140, 0, 0),
                     (165, 140, 0, 0))]
        vertical_end = outline_curves(vertical, cap_end='ellipse')[0]
        self.assertAlmostEqual(max(points[-1][1] for _, points in vertical_end), 170)

    def test_rotated_ellipse_cap_follows_the_previewed_nib(self):
        from variable_stroke_core import cubic, ellipse_nib_edges
        nib = (165, 70, 0.2, 30)
        contour = outline_curves([('line', ((0, 0), (100, 0)), nib, nib)],
                                 cap_end='ellipse')[0]
        left, right = ellipse_nib_edges((100, 0), (1, 0), nib)
        self.assertEqual(contour[0][1][-1], left)
        self.assertEqual(contour[1][1][0], left)
        self.assertEqual(contour[2][1][-1], right)
        self.assertEqual(contour[3][1][0], right)
        center = tuple((a+b)/2 for a, b in zip(left, right))
        angle = math.radians(nib[3])
        u = (math.cos(angle), math.sin(angle))
        v = (-u[1], u[0])
        for piece in contour[1:3]:
            for index in range(33):
                delta = sub(cubic(*piece[1], index/32), center)
                x = sum(a*b for a, b in zip(delta, u)) / (nib[0]/2)
                y = sum(a*b for a, b in zip(delta, v)) / (nib[1]/2)
                self.assertAlmostEqual(x*x + y*y, 1, delta=0.001)

    def test_corner_handles_sit_on_the_outline_corner(self):
        segments = [('line', ((0, 0), (300, 0)), (40, 40, 0), (40, 40, 0)),
                    ('line', ((300, 0), (300, 300)), (100, 100, 0), (100, 100, 0))]
        edges = node_edges(segments)
        left, right = edges[1]
        # Turning left: the left side is the inner corner, the right side the miter.
        self.assertAlmostEqual(left[0], 250.0)
        self.assertAlmostEqual(left[1], 20.0)
        self.assertAlmostEqual(right[0], 350.0)
        self.assertAlmostEqual(right[1], -20.0)
        self.assertEqual(sorted(edges), [0, 1, 2])

    def test_corner_midpoint_moves_linearly_with_tension(self):
        segments = [('line', ((0, 0), (200, 0)), 40, 40),
                    ('line', ((200, 0), (200, 200)), 40, 40)]
        reports = []
        for tension in (100, 150):
            report = []
            outline_curves(segments, corner_radii=[None,
                {'outer': 40, 'inner': 40, 'tension': tension, 'ratio': 100}],
                report=report)
            reports.append(report)
        for first, second in zip(*reports):
            predicted = tuple(first['middle'][axis] +
                              first['middle_slope'][axis] * 50 for axis in (0, 1))
            self.assertAlmostEqual(predicted[0], second['middle'][0])
            self.assertAlmostEqual(predicted[1], second['middle'][1])

    def test_custom_angle_cut(self):
        seg = [('line', ((0, 0), (0, 300)), 60, 60)]
        contour = outline_curves(seg, False, 'flat', 'angle', end_angle=30)[0]
        # The cap remains one line through (0, 300).
        cap = contour[1][1]
        (x0, y0), (x1, y1) = cap
        self.assertAlmostEqual(math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180, 30, places=3)
        self.assertAlmostEqual((y0 - 300) * math.cos(math.radians(30)) -
                               x0 * math.sin(math.radians(30)), 0, places=3)

    def test_corner_moves_continuously(self):
        previous = None
        for step in range(0, 1700):
            angle = math.radians(step * 0.1 + 0.05)
            end = (400 + 300*math.cos(angle), 300*math.sin(angle))
            segments = [('line', ((0, 0), (400, 0)), 60, 60), ('line', ((400, 0), end), 60, 60)]
            left, right = node_edges(segments)[1]
            if previous:
                jump = max(math.hypot(left[0]-previous[0][0], left[1]-previous[0][1]),
                           math.hypot(right[0]-previous[1][0], right[1]-previous[1][1]))
                self.assertLess(jump, 2.0, 'jump at %.1f degrees' % (step * 0.1))
            previous = (left, right)

    def test_corner_handles_round_trip(self):
        # Moving an arc end to a new trim must reproduce that trim when the radius
        # and ratio derived from it (as the tool does) are fed back in.
        segments = [('line', ((0, 0), (0, 300)), 100, 100), ('line', ((0, 300), (300, 300)), 100, 100)]
        for which in ('outer', 'inner'):
            report = []
            outline_curves(segments, corner_radii=[None, {'outer': 30, 'inner': 30}, None],
                           report=report)
            widget = next(w for w in report if w['which'] == which)
            first, second = 45.0, widget['second']
            before, after = (second, first) if widget['flip'] else (first, second)
            radius = math.sqrt(before * after) / math.tan(widget['turn'] / 2.0)
            ratio = 100.0 * before / after
            again = []
            outline_curves(segments, corner_radii=[None, {'outer': radius, 'inner': radius,
                                                         'ratio': ratio}, None], report=again)
            widget = next(w for w in again if w['which'] == which)
            self.assertAlmostEqual(widget['first'], 45.0, places=3)
            self.assertAlmostEqual(widget['second'], second, places=3)

    def test_bad_width(self):
        with self.assertRaises(ValueError):
            outline([('line', ((0, 0), (10, 0)), 0, 10)])


if __name__ == '__main__':
    unittest.main()
