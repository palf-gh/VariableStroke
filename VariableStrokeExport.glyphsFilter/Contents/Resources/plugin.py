# encoding: utf-8
import objc
from GlyphsApp import Glyphs
from GlyphsApp.plugins import FilterWithoutDialog
from glyphs_bridge import convert_layer


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
        convert_layer(layer)

    @objc.signature(b'v@:@@')
    def processGlyph_withArguments_(self, glyph, arguments):
        # 'PreInterpolationFilter': expand every master before interpolation. The
        # outline structure is fixed, so the expanded masters stay compatible.
        for layer in glyph.layers:
            convert_layer(layer)

    @objc.python_method
    def __file__(self):
        return __file__
