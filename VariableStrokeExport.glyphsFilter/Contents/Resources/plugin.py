# encoding: utf-8
import objc
from GlyphsApp.plugins import FilterWithoutDialog
from glyphs_bridge import convert_layer


class VariableStrokeExport(FilterWithoutDialog):
    @objc.python_method
    def settings(self):
        self.menuName = 'Variable Stroke: Convert to Outlines'

    @objc.python_method
    def filter(self, layer, inEditView, customParameters):
        convert_layer(layer)

    @objc.python_method
    def __file__(self):
        return __file__
