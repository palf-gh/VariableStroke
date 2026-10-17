import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       'VariableStrokeTool.glyphsTool/Contents/Resources'))
file_preview = importlib.import_module('file_preview')
file_icon = importlib.import_module('file_icon')


class Pictures:
    """Stands in for Quick Look: images are made at once, or held back until
    `release`."""

    def __init__(self, hold=False):
        self.hold, self.held, self.asked = hold, [], []

    def render(self, stand_in, done):
        self.asked.append(stand_in)
        if self.hold:
            self.held.append((stand_in, done))
        else:
            done('image of ' + stand_in)

    def release(self):
        for stand_in, done in self.held:
            done('image of ' + stand_in)
        self.held = []


class FilePreviewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(file_preview._pending.clear)
        self.font = os.path.realpath(os.path.join(self.folder.name, 'font.glyphs'))
        Path(self.font).write_text('font')
        self.stand_in = os.path.join(self.folder.name, 'stand-in.glyphs')

    def update(self, pictures, stand_in='default', **options):
        stand_in = self.stand_in if stand_in == 'default' else stand_in
        return file_preview.update(self.font, stand_in, render=pictures.render,
                                   encode=lambda image: image.encode('utf-8'), **options)

    def kept(self):
        return file_icon.get_xattr(self.font, file_preview.PREVIEW)

    def test_picture_is_kept_with_the_file_it_was_made_for(self):
        self.assertTrue(self.update(Pictures()))
        self.assertEqual(self.kept(), ('image of ' + self.stand_in).encode('utf-8'))
        self.assertTrue(file_preview.is_current(self.font))
        self.assertEqual(Path(self.font).read_text(), 'font')
        # Saved again (elsewhere): the picture no longer stands for the file.
        Path(self.font).write_text('font, saved again')
        os.utime(self.font, (1, 1))
        self.assertFalse(file_preview.is_current(self.font))

    def test_only_a_stale_picture_is_made_again_on_open(self):
        pictures = Pictures()
        self.update(pictures)
        self.assertFalse(self.update(pictures, stale_only=True))
        os.utime(self.font, (1, 1))
        self.assertTrue(self.update(pictures, stale_only=True))
        self.assertEqual(len(pictures.asked), 2)

    def test_without_a_stand_in_the_picture_comes_off(self):
        self.update(Pictures())
        self.assertFalse(self.update(Pictures(), stand_in=None))
        self.assertIsNone(self.kept())
        self.assertIsNone(file_icon.get_xattr(self.font, file_preview.STAMP))

    def test_a_late_picture_gives_way_to_a_newer_request_or_to_no_strokes(self):
        pictures = Pictures(hold=True)
        self.update(pictures)
        self.update(pictures, stand_in=None)
        pictures.release()
        self.assertIsNone(self.kept())

    def test_a_stamp_of_another_version_is_stale(self):
        file_icon.set_xattr(self.font, file_preview.STAMP, json.dumps(
            {'version': file_preview.VERSION - 1, 'stamp': file_icon.stamp(self.font)}).encode())
        self.assertFalse(file_preview.is_current(self.font))

    def test_packages_and_unsaved_fonts_get_no_picture(self):
        package = os.path.join(self.folder.name, 'font.glyphspackage')
        os.mkdir(package)
        pictures = Pictures()
        self.assertFalse(file_preview.update(package, self.stand_in, render=pictures.render))
        self.assertFalse(file_preview.update(None, self.stand_in, render=pictures.render))
        self.assertEqual(pictures.asked, [])


if __name__ == '__main__':
    unittest.main()
