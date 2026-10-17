import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       'VariableStrokeTool.glyphsTool/Contents/Resources'))
file_icon = importlib.import_module('file_icon')


class Icons:
    """Stands in for Quick Look and Finder: images are made at once, or held
    back until `release`; set icons are recorded and kept as the resource
    fork Finder would write."""

    def __init__(self, hold=False):
        self.hold, self.held, self.applied = hold, [], []

    def render(self, stand_in, done):
        if self.hold:
            self.held.append((stand_in, done))
        else:
            done('image of ' + stand_in)

    def release(self):
        for stand_in, done in self.held:
            done('image of ' + stand_in)
        self.held = []

    def apply(self, image, path):
        self.applied.append(image)
        set_custom_icon(path, image is not None)
        return True


def set_custom_icon(path, on, icon=b'icon'):
    """What Finder does to the file's Finder info and resource fork."""
    flags = bytearray(32)
    flags[8] = 0x04 if on else 0
    file_icon.set_xattr(path, 'com.apple.FinderInfo', bytes(flags))
    if on:
        file_icon.set_xattr(path, 'com.apple.ResourceFork', icon)


class FileIconTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.font = os.path.realpath(os.path.join(self.folder.name, 'font.glyphs'))
        Path(self.font).write_text('font')
        self.stand_in = os.path.join(self.folder.name, 'stand-in.glyphs')
        Path(self.stand_in).write_text('stand-in')

    def update(self, icons, stand_in='default', **options):
        stand_in = self.stand_in if stand_in == 'default' else stand_in
        return file_icon.update(self.font, stand_in, render=icons.render,
                                apply=icons.apply, **options)

    def test_icon_is_set_and_marked_with_the_file_it_was_made_for(self):
        icons = Icons()
        self.assertTrue(self.update(icons))
        self.assertEqual(icons.applied, ['image of ' + self.stand_in])
        mark = json.loads(file_icon.get_xattr(self.font, file_icon.MARK))
        self.assertEqual(mark['stamp'], file_icon.stamp(self.font))
        self.assertEqual(Path(self.font).read_text(), 'font')

    def test_only_a_stale_icon_is_made_again_on_open(self):
        icons = Icons()
        self.update(icons)
        self.assertFalse(self.update(icons, stale_only=True))
        Path(self.font).write_text('font, saved elsewhere')
        os.utime(self.font, (1, 1))
        self.assertTrue(self.update(icons, stale_only=True))
        self.assertEqual(len(icons.applied), 2)

    def test_without_a_stand_in_only_our_icon_comes_off(self):
        icons = Icons()
        self.update(icons)
        self.assertFalse(self.update(icons, stand_in=None))
        self.assertEqual(icons.applied[-1], None)
        self.assertIsNone(file_icon.get_xattr(self.font, file_icon.MARK))
        # A font that never had our icon: nothing to take off.
        count = len(icons.applied)
        self.update(icons, stand_in=None)
        self.assertEqual(len(icons.applied), count)

    def test_someone_elses_icon_is_left_alone(self):
        set_custom_icon(self.font, True, b'their icon')
        icons = Icons()
        self.assertFalse(self.update(icons))
        self.update(icons, stand_in=None)
        self.assertEqual(icons.applied, [])
        self.assertEqual(file_icon.get_xattr(self.font, 'com.apple.ResourceFork'), b'their icon')

    def test_a_resource_fork_without_the_custom_icon_flag_is_no_icon(self):
        file_icon.set_xattr(self.font, 'com.apple.ResourceFork', b'left over')
        icons = Icons()
        self.assertTrue(self.update(icons))
        self.assertEqual(icons.applied, ['image of ' + self.stand_in])

    def test_a_late_image_gives_way_to_a_newer_request_or_to_no_strokes(self):
        icons = Icons(hold=True)
        self.update(icons)
        self.update(icons)
        icons.release()
        self.assertEqual(len(icons.applied), 1)
        self.update(icons)
        self.update(icons, stand_in=None)  # takes the first icon off
        icons.release()
        self.assertEqual(icons.applied, ['image of ' + self.stand_in, None])
        self.assertIsNone(file_icon.get_xattr(self.font, file_icon.MARK))

    def test_packages_and_unsaved_fonts_get_no_icon(self):
        package = os.path.join(self.folder.name, 'font.glyphspackage')
        os.mkdir(package)
        icons = Icons()
        self.assertFalse(file_icon.update(package, self.stand_in, render=icons.render,
                                          apply=icons.apply))
        self.assertFalse(file_icon.update(None, self.stand_in, render=icons.render,
                                          apply=icons.apply))
        self.assertEqual(icons.applied, [])


if __name__ == '__main__':
    unittest.main()
