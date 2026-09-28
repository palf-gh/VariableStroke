import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from variable_stroke_core import outline, outline_curves


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

    def test_bad_width(self):
        with self.assertRaises(ValueError):
            outline([('line', ((0, 0), (10, 0)), 0, 10)])


if __name__ == '__main__':
    unittest.main()
