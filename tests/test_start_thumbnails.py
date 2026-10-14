import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       'VariableStrokeTool.glyphsTool/Contents/Resources'))
start_thumbnails = importlib.import_module('start_thumbnails')


class Glyphs(dict):
    def __getitem__(self, name):
        return self.get(name)

    def __iter__(self):
        return iter(self.values())


class Font:
    """Just what write_preview touches."""

    def __init__(self, names, saved):
        self.names, self.saved = names, saved
        self.masters = [types.SimpleNamespace(id='m01')]
        self.glyphs = Glyphs((name, types.SimpleNamespace(
            name=name, layers={'m01': types.SimpleNamespace(components=[])}))
            for name in names)

    def copy(self):
        return Font(self.names, self.saved)

    def removeGlyphs_(self, glyphs):
        for glyph in glyphs:
            dict.__delitem__(self.glyphs, glyph.name)

    def save(self, path):
        self.saved.append(sorted(dict.keys(self.glyphs)))
        Path(path).write_text('stand-in')


class StartThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cache = start_thumbnails.CACHE_DIR
        start_thumbnails.CACHE_DIR = os.path.join(self.folder.name, 'cache')
        self.addCleanup(setattr, start_thumbnails, 'CACHE_DIR', self.cache)
        self.source = os.path.join(self.folder.name, 'font.glyphs')
        Path(self.source).write_text('font')

    def test_stand_in_holds_only_the_preview_glyphs_and_follows_the_file(self):
        saved = []
        font = Font(['A', 'B', 'g', 'o'], saved)
        expanded = []
        self.assertTrue(start_thumbnails.write_preview(
            font, self.source, lambda layer, glyph, master: expanded.append(glyph.name) or True))
        self.assertEqual(saved, [['A', 'g']])
        self.assertEqual(sorted(expanded), ['A', 'g'])
        stand_in = start_thumbnails.cached_preview(self.source)
        self.assertTrue(stand_in and os.path.exists(stand_in))
        # Saved again (elsewhere, by a Glyphs without the plugin): out of date.
        Path(self.source).write_text('font, edited')
        os.utime(self.source, (1, 1))
        self.assertIsNone(start_thumbnails.cached_preview(self.source))

    def test_open_font_keeps_its_stand_in_while_it_is_being_saved(self):
        start_thumbnails.write_preview(Font(['A', 'g'], []), self.source, lambda *args: True)
        # Glyphs has written the file but not yet said so (the stand-in is
        # written again then): the open font's stand-in still stands.
        Path(self.source).write_text('font, saved again')
        os.utime(self.source, (1, 1))
        self.assertIsNone(start_thumbnails.preview_for(self.source))
        start_thumbnails.note_open(self.source)
        self.addCleanup(start_thumbnails.note_closed, self.source)
        self.assertIsNotNone(start_thumbnails.preview_for(self.source))
        start_thumbnails.note_closed(self.source)
        self.assertIsNone(start_thumbnails.preview_for(self.source))

    def test_no_strokes_no_stand_in(self):
        font = Font(['A', 'g'], [])
        start_thumbnails.write_preview(font, self.source, lambda *args: True)
        self.assertIsNotNone(start_thumbnails.cached_preview(self.source))
        self.assertFalse(start_thumbnails.write_preview(font, self.source, lambda *args: False))
        self.assertIsNone(start_thumbnails.cached_preview(self.source))

    def test_unknown_files_have_no_stand_in(self):
        self.assertIsNone(start_thumbnails.cached_preview(None))
        self.assertIsNone(start_thumbnails.cached_preview(self.source))


if __name__ == '__main__':
    unittest.main()
