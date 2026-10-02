import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clipboard_export

SQUARE = (True, [('line', ((0, 0), (100, 0))),
                 ('cubic', ((100, 0), (100, 50), (50, 100), (0, 100))),
                 ('line', ((0, 100), (0, 0)))])
OPEN = (False, [('line', ((200, -20), (300, 50)))])


class ClipboardTests(unittest.TestCase):
    def test_svg_flips_y_and_fits_the_view_box(self):
        svg = clipboard_export.svg_document([SQUARE, OPEN])
        self.assertIn('width="300" height="120" viewBox="0 -100 300 120"', svg)
        self.assertIn('<path d="M0 0L100 0C100 -50 50 -100 0 -100L0 0Z"/>', svg)
        self.assertIn('<path d="M200 20L300 -50" fill="none" stroke="#000"/>', svg)

    def test_nothing_to_draw(self):
        self.assertIsNone(clipboard_export.svg_document([]))

    def test_pdf_page_is_the_outline_bounds(self):
        try:
            from Quartz import PDFDocument
        except ImportError:
            self.skipTest('PyObjC Quartz is not installed')
        document = PDFDocument.alloc().initWithData_(clipboard_export.pdf_document([SQUARE]))
        self.assertEqual(document.pageCount(), 1)
        size = document.pageAtIndex_(0).boundsForBox_(0).size
        self.assertEqual((size.width, size.height), (100, 100))


if __name__ == '__main__':
    unittest.main()
