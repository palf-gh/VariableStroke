# encoding: utf-8
import objc
from GlyphsApp import Glyphs
from GlyphsApp.plugins import FilterWithoutDialog
from glyphs_bridge import convert_layer, convert_glyph
from outline_union import union_layer_outlines


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
            if not inEditView:
                union_layer_outlines(layer)

    @objc.signature(b'v@:@@')
    def processGlyph_withArguments_(self, glyph, arguments):
        # 'PreInterpolationFilter': expand every master before interpolation. The
        # outline structure is fixed, so the expanded masters stay compatible.
        convert_glyph(glyph)
        # Union a single master's generated outlines before Glyphs' Core
        # Remove Overlap step. For multiple masters, keep contour topology
        # compatible until the interpolated layer reaches filter().
        layers = list(glyph.layers)
        if len(layers) == 1:
            union_layer_outlines(layers[0])

    @objc.python_method
    def __file__(self):
        return __file__
