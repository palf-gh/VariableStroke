import sys
import unittest
from pathlib import Path as FilePath

sys.path.insert(0, str(FilePath(__file__).resolve().parents[1]))
sys.path.insert(0, str(FilePath(__file__).resolve().parent))

from test_bridge import Node, Path, bridge  # installs the fake GlyphsApp
import width_profile
from variable_stroke_core import cubic, length, sub


def profile_data(points, name='test'):
    return {'name': name, 'points': points}


# 100 % at both ends, 50 % in the middle.
DIP = profile_data([[0, 100, 0, 100, 20, 100],
                    [50, 50, 35, 50, 65, 50],
                    [100, 100, 80, 100, 100, 100]])


def stroke(nodes, profile=None, closed=False):
    path = Path(nodes, closed)
    for node in path.nodes:
        node.userData.clear()
    if profile is not None:
        path.attributes[bridge.PROFILE_KEY] = 'p'
        path.attributes[bridge.PROFILE_DATA_KEY] = profile
    return path


def line(x1=1000):
    return [Node(0, 0), Node(x1, 0)]


def thickness_at(contours, point):
    """Distance across the stroke at `point`: nearest outline point on each side."""
    samples = []
    for contour in contours:
        for kind, points in contour:
            if kind == 'cubic':
                samples += [cubic(*points, i/200.0) for i in range(201)]
            else:
                samples += [(points[0][0] + (points[1][0]-points[0][0])*i/200.0,
                             points[0][1] + (points[1][1]-points[0][1])*i/200.0)
                            for i in range(201)]
    above = min(abs(p[1]-point[1]) for p in samples if p[1] > point[1] and abs(p[0]-point[0]) < 2)
    below = min(abs(p[1]-point[1]) for p in samples if p[1] < point[1] and abs(p[0]-point[0]) < 2)
    return above + below


class ProfileTests(unittest.TestCase):
    def test_evaluate_ends_and_middle(self):
        profile = width_profile.normalize(DIP)
        self.assertAlmostEqual(width_profile.evaluate(profile, 0), 100)
        self.assertAlmostEqual(width_profile.evaluate(profile, 100), 100)
        self.assertAlmostEqual(width_profile.evaluate(profile, 50), 50, places=3)
        self.assertLess(width_profile.evaluate(profile, 30), 100)

    def test_normalize_pins_ends_and_keeps_handles_in_their_span(self):
        profile = width_profile.normalize(profile_data(
            [[5, 80, -10, 80, 90, 80], [40, 120, -5, 120, 200, 120], [90, 60, 0, 60, 90, 60]]))
        points = profile['points']
        self.assertEqual((points[0][0], points[-1][0]), (0.0, 100.0))
        self.assertGreaterEqual(points[1][2], points[0][0])
        self.assertLessEqual(points[1][4], points[2][0])
        self.assertLessEqual(points[0][4], points[1][0])

    def test_flat_profile_adds_no_sections(self):
        flat = width_profile.normalize(profile_data(width_profile.default_points()))
        self.assertEqual(width_profile.divisions(flat), 0)
        self.assertGreater(width_profile.divisions(width_profile.normalize(DIP)), 0)

    def test_insert_point_keeps_the_curve(self):
        profile = width_profile.normalize(DIP)
        inserted, index = width_profile.insert_point(profile, 25)
        self.assertEqual(index, 1)
        for x in (10, 25, 40, 70):
            self.assertAlmostEqual(width_profile.evaluate(inserted, x),
                                   width_profile.evaluate(profile, x), places=1)

    def test_flat_profile_draws_the_same_outline(self):
        plain = bridge.curves_for_path(stroke(line()), 40)
        flat = bridge.curves_for_path(
            stroke(line(), profile_data(width_profile.default_points())), 40)
        self.assertEqual(plain, flat)

    def test_profile_scales_the_stroke_along_its_length(self):
        contours = bridge.curves_for_path(stroke(line(), DIP), 40)
        profile = width_profile.normalize(DIP)
        for x in range(10, 1000, 30):  # between the sections too
            expected = 40 * width_profile.evaluate(profile, x/10.0) / 100
            self.assertAlmostEqual(thickness_at(contours, (x, 0)), expected, delta=0.3)

    def test_masters_with_other_segment_lengths_stay_compatible(self):
        first = stroke([Node(0, 0), Node(100, 0), Node(1000, 0)], DIP)
        second = stroke([Node(0, 0), Node(900, 0), Node(1000, 0)], DIP)
        self.assertEqual(bridge.outline_signature(first, 40),
                         bridge.outline_signature(second, 40))

    def test_virtual_node_percent_is_relative_to_the_profiled_width(self):
        path = stroke(line(), DIP)
        path.attributes[bridge.VIRTUAL_KEY] = [{
            'id': 'v', 'segment': 0, 't': 0.5, 'mode': 'continuous',
            'before': {'left': 100, 'right': 100}}]
        contours = bridge.curves_for_path(path, 40)
        self.assertAlmostEqual(thickness_at(contours, (500, 0)), 20, delta=1.0)  # a node there
        widget = bridge.virtual_widgets(path, 40)[0]
        across = length(sub(widget['before'][0], widget['before'][1]))
        self.assertAlmostEqual(across, 20, delta=0.5)

    def test_section_percent_scales_the_profiled_width(self):
        plain = bridge.curves_for_path(stroke(line(), DIP), 40)
        path = stroke(line(), DIP)
        path.attributes[bridge.VIRTUAL_KEY] = [{
            'id': 'v', 'segment': 0, 't': 0.4, 'mode': 'section', 'endSegment': 0,
            'endT': 0.6, 'before': {'left': 50, 'right': 50}}]
        contours = bridge.curves_for_path(path, 40)
        self.assertAlmostEqual(thickness_at(contours, (500, 0)), 10, delta=1.0)
        for x in (200, 300, 700):
            self.assertAlmostEqual(thickness_at(contours, (x, 0)),
                                   thickness_at(plain, (x, 0)), delta=0.5)
        start, end = bridge.virtual_widgets(path, 40)
        for widget, x in ((start, 400), (end, 600)):
            across = length(sub(widget['before'][0], widget['before'][1]))
            self.assertAlmostEqual(across, thickness_at(plain, (x, 0))/2, delta=0.5)

    def test_paths_out_of_reach_of_their_font_still_find_the_profile(self):
        # Instances (Variable Font Preview) belong to glyph copies whose parent
        # is no font, or to fonts that do not carry the profiles.
        class Font:
            glyphs, masters = [], []

            def __init__(self):
                self.userData = {}
        font = Font()
        bridge.set_profiles(font, {'dip': DIP})
        try:
            path = stroke(line())
            path.attributes[bridge.PROFILE_KEY] = 'dip'
            plain = bridge.curves_for_path(stroke(line()), 40)
            self.assertNotEqual(bridge.curves_for_path(path, 40), plain)
            bridge.set_profiles(font, {})  # deleted: drawn without a profile
            self.assertEqual(bridge.curves_for_path(path, 40), plain)
        finally:
            bridge.forget_profiles()


if __name__ == '__main__':
    unittest.main()
