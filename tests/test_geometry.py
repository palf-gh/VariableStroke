import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from variable_stroke_core import outline, outline_curves, node_edges


class GeometryTests(unittest.TestCase):
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

    def test_custom_angle_cut(self):
        seg = [('line', ((0, 0), (0, 300)), 60, 60)]
        contour = outline_curves(seg, False, 'flat', 'angle', end_angle=30)[0]
        # The end cap is a single line through (0, 300) at 30 degrees.
        cap = [points for kind, points in contour if kind == 'line'
               and abs(points[0][1] - 300) < 40 and abs(points[1][1] - 300) < 40][0]
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

    def test_bad_width(self):
        with self.assertRaises(ValueError):
            outline([('line', ((0, 0), (10, 0)), 0, 10)])


if __name__ == '__main__':
    unittest.main()
