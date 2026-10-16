# encoding: utf-8
import os
import threading
import time
import objc
from GlyphsApp import Glyphs
from GlyphsApp.plugins import FilterWithoutDialog
from glyphs_bridge import (convert_layer, convert_glyph, convert_glyph_cached,
                           master_incompatibilities, describe_incompatibility,
                           drop_empty_segments, CONVERSION_STATS)
from outline_union import union_layer_outlines
from export_settings import remove_overlap_enabled


def _is_master_layer(layer):
    """True for a layer of a font with several masters (variable export), False
    for the single-master font of a static instance."""
    try:
        return len(layer.parent.parent.masters) > 1
    except Exception:
        return False


def _report_incompatibilities(glyph):
    """Settings that change the outline structure in one master only are named
    first, before Glyphs stops with its own compatibility error."""
    try:
        for index, entry in sorted(master_incompatibilities(glyph).items()):
            print('Variable Stroke: %s path %d: %s' % (
                glyph.name, index + 1,
                describe_incompatibility(entry).replace('\n', '\n    ')))
    except Exception:
        pass


def _convert_masters(glyph):
    """Expand all layers of a glyph together (corners stay compatible)."""
    _report_incompatibilities(glyph)
    convert_glyph(glyph)


_LOG = os.path.expanduser('~/Library/Logs/VariableStrokeExport.log')
_logged = [None]
_log_lock = threading.Lock()


def _log_conversions():
    """The PreInterpolationFilter's work so far (see convert_glyph_cached), when
    it changed: one line per style that reaches its Filter step."""
    with _log_lock:
        stats = (CONVERSION_STATS['glyphs'], CONVERSION_STATS['reused'],
                 CONVERSION_STATS['seconds'])
        if stats == _logged[0] or not stats[0]:
            return
        _logged[0] = stats
        try:
            with open(_LOG, 'a') as log:
                log.write('%s pre-interpolation: %d glyphs, %d reused, %.1f s in all\n'
                          % ((time.strftime('%m-%d %H:%M:%S'),) + stats))
        except Exception:
            pass


class VariableStrokeExport(FilterWithoutDialog):
    @objc.python_method
    def settings(self):
        self.menuName = Glyphs.localize({
            'en': 'Variable Stroke: Convert to Outlines',
            'jp': '可変ストローク：アウトライン化',
            'ja': '可変ストローク：アウトライン化',
        })

    @objc.python_method
    def filter(self, layer, inEditView, customParameters):
        # From the Filter menu the result stays as plain editable paths; on export
        # the outline marks keep later steps from expanding it again.
        if inEditView and getattr(layer, 'parent', None) is not None:
            convert_glyph(layer.parent, keep_marks=False)
        else:
            convert_layer(layer, keep_marks=not inEditView)
            # A variable export hands over the master layers themselves; a union
            # there would change each master's contours on its own, and their
            # segments of no length keep the masters compatible.
            if not inEditView and not _is_master_layer(layer):
                drop_empty_segments(layer)
                if remove_overlap_enabled():
                    union_layer_outlines(layer)

    @objc.signature(b'v@:@@')
    def processFont_withArguments_(self, font, arguments):
        # 'Filter': the base class hands only the first master's layer of each
        # glyph to filter(). A static instance has just that one master, but a
        # variable export passes the font with all its masters: expand every
        # layer of every glyph there, or only the first master gets outlines.
        _log_conversions()
        if len(font.masters) <= 1:
            FilterWithoutDialog.processFont_withArguments_(self, font, arguments)
            return
        for glyph in list(font.glyphs):
            _convert_masters(glyph)

    @objc.signature(b'v@:@@')
    def processGlyph_withArguments_(self, glyph, arguments):
        # 'PreInterpolationFilter': expand every master before interpolation. The
        # outline structure is fixed, so the expanded masters stay compatible.
        # Every style's copy of a glyph is alike: the first style's outlines
        # are reused (see convert_glyph_cached).
        convert_glyph_cached(glyph, _report_incompatibilities)
        # Union a single master's generated outlines before Glyphs' Core
        # Remove Overlap step. For multiple masters, keep contour topology
        # compatible until the interpolated layer reaches filter().
        layers = list(glyph.layers)
        if len(layers) == 1 and remove_overlap_enabled():
            union_layer_outlines(layers[0])

    @objc.python_method
    def __file__(self):
        return __file__
