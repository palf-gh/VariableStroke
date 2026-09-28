# encoding: utf-8
"""Variable Stroke editing tool for Glyphs 3."""
import math
import objc
from AppKit import NSBezierPath, NSColor, NSPoint, NSEvenOddWindingRule
from GlyphsApp import Glyphs, GSPath, GSCustomParameter, OFFCURVE, DOCUMENTOPENED, UPDATEINTERFACE
from GlyphsApp.plugins import SelectTool
from glyphs_bridge import (PATH_KEY, WIDTH_KEY, CAP_START_KEY, CAP_END_KEY,
                           EXPORT_FILTER, DEFAULT_WIDTH, enabled, width, polygons_for_path,
                           segments_for_path, convert_layer)
from variable_stroke_core import normal, unit, sub, add, mul, length

CAP_NAMES = [('flat', 'Flat'), ('round', 'Round'), ('square', 'Square'),
             ('horizontal', 'Horizontal cut'), ('vertical', 'Vertical cut')]


class VariableStrokeTool(SelectTool):
    @objc.python_method
    def settings(self):
        self.name = 'Variable Stroke'
        self.toolbarPosition = 105
        self._drag = None
        self._instance_counts = {}
        self.generalContextMenus = [
            {'name': 'Variable Stroke: Enable selected paths', 'action': self.enableSelectedPaths_},
            {'name': 'Variable Stroke: Convert selected layer to outlines', 'action': self.convertSelectedLayer_},
        ]
        self._build_cap_menus()

    @objc.python_method
    def _build_cap_menus(self):
        for side in ('start', 'end'):
            for style, label in CAP_NAMES:
                method = getattr(self, 'cap%s%s_' % (side.title(), style.title()))
                self.generalContextMenus.append({
                    'name': 'Variable Stroke: %s cap → %s' % (side.title(), label),
                    'action': method,
                })

    @objc.python_method
    def start(self):
        Glyphs.addCallback(self._sync_export, DOCUMENTOPENED)
        Glyphs.addCallback(self._sync_export, UPDATEINTERFACE)
        self._sync_export()

    @objc.python_method
    def activate(self):
        self._sync_export()

    @objc.python_method
    def _sync_export(self, notification=None):
        # The Filter custom parameter is evaluated on the export copy of each instance.
        for font in list(Glyphs.fonts):
            marker = id(font)
            count = len(font.instances)
            if self._instance_counts.get(marker) == count:
                continue
            self._instance_counts[marker] = count
            has_strokes = any(enabled(path) for glyph in font.glyphs
                              for layer in glyph.layers for path in layer.paths)
            if not has_strokes:
                continue
            for instance in font.instances:
                if any(p.name == 'Filter' and str(p.value).split(';')[0] == EXPORT_FILTER
                       for p in instance.customParameters):
                    continue
                instance.customParameters.append(GSCustomParameter('Filter', EXPORT_FILTER + ';'))

    @objc.python_method
    def _layer(self):
        try:
            return self.editViewController().graphicView().activeLayer()
        except Exception:
            return None

    @objc.python_method
    def _redraw(self):
        try:
            self.editViewController().graphicView().setNeedsDisplay_(True)
        except Exception:
            pass

    @objc.python_method
    def _scale(self):
        try:
            return max(0.05, float(self.editViewController().graphicView().scale()))
        except Exception:
            return 1.0

    @objc.python_method
    def _selected_paths(self, layer):
        selected = list(layer.selection)
        return [path for path in layer.paths if any(node in selected for node in path.nodes)]

    def enableSelectedPaths_(self, sender):
        layer = self._layer()
        if layer is None:
            return
        paths = self._selected_paths(layer)
        if not paths:
            return
        for path in paths:
            segments_for_path(path)
        layer.beginChanges()
        try:
            for path in paths:
                path.userData[PATH_KEY] = True
                path.userData[CAP_START_KEY] = path.userData.get(CAP_START_KEY, 'flat')
                path.userData[CAP_END_KEY] = path.userData.get(CAP_END_KEY, 'flat')
                path.attributes['fill'] = False
                for node in path.nodes:
                    if node.type != OFFCURVE and WIDTH_KEY not in node.userData:
                        node.userData[WIDTH_KEY] = DEFAULT_WIDTH
        finally:
            layer.endChanges()
        self._instance_counts.clear()
        self._sync_export()
        self._redraw()

    @objc.python_method
    def _set_cap(self, side, style):
        layer = self._layer()
        if layer is None:
            return
        key = CAP_START_KEY if side == 'start' else CAP_END_KEY
        layer.beginChanges()
        try:
            for path in self._selected_paths(layer):
                if enabled(path) and not path.closed:
                    path.userData[key] = style
        finally:
            layer.endChanges()
        self._redraw()

    def capStartFlat_(self, sender): self._set_cap('start', 'flat')
    def capStartRound_(self, sender): self._set_cap('start', 'round')
    def capStartSquare_(self, sender): self._set_cap('start', 'square')
    def capStartHorizontal_(self, sender): self._set_cap('start', 'horizontal')
    def capStartVertical_(self, sender): self._set_cap('start', 'vertical')
    def capEndFlat_(self, sender): self._set_cap('end', 'flat')
    def capEndRound_(self, sender): self._set_cap('end', 'round')
    def capEndSquare_(self, sender): self._set_cap('end', 'square')
    def capEndHorizontal_(self, sender): self._set_cap('end', 'horizontal')
    def capEndVertical_(self, sender): self._set_cap('end', 'vertical')

    def convertSelectedLayer_(self, sender):
        layer = self._layer()
        if layer is None:
            return
        layer.beginChanges()
        try:
            convert_layer(layer)
        finally:
            layer.endChanges()
        self._redraw()

    @objc.python_method
    def _handles(self, layer):
        for path in layer.paths:
            if not enabled(path):
                continue
            nodes = [node for node in path.nodes if node.type != OFFCURVE]
            for i, node in enumerate(nodes):
                p = (node.position.x, node.position.y)
                if len(nodes) == 1:
                    tangent = (1.0, 0.0)
                else:
                    previous = nodes[i-1] if (i > 0 or path.closed) else node
                    following = nodes[(i+1) % len(nodes)] if (i < len(nodes)-1 or path.closed) else node
                    tangent = unit((following.position.x-previous.position.x,
                                    following.position.y-previous.position.y))
                n = normal(tangent)
                yield path, node, p, n, add(p, mul(n, width(node)/2.0))

    @objc.python_method
    def foreground(self, layer):
        if layer is None:
            return
        for path in layer.paths:
            if not enabled(path):
                continue
            try:
                polygons = polygons_for_path(path)
            except ValueError as error:
                print('Variable Stroke:', error)
                continue
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.12, 0.46, 0.94, 0.28).set()
            shape = NSBezierPath.bezierPath()
            shape.setWindingRule_(NSEvenOddWindingRule)
            for polygon in polygons:
                if len(polygon) < 3:
                    continue
                shape.moveToPoint_(NSPoint(*polygon[0]))
                for point in polygon[1:]:
                    shape.lineToPoint_(NSPoint(*point))
                shape.closePath()
            shape.fill()
        radius = 4.0 / self._scale()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.02, 0.36, 0.81, 1.0).set()
        for _, _, _, _, handle in self._handles(layer):
            dot = NSBezierPath.bezierPathWithOvalInRect_(((handle[0]-radius, handle[1]-radius),
                                                         (radius*2, radius*2)))
            dot.fill()

    def mouseDown_(self, event):
        layer = self._layer()
        if layer is not None:
            loc = self.editViewController().graphicView().getActiveLocation_(event)
            point = (loc.x, loc.y)
            threshold = 8.0 / self._scale()
            for path, node, center, n, handle in self._handles(layer):
                if length(sub(point, handle)) <= threshold:
                    layer.beginChanges()
                    self._drag = (layer, node, center, n)
                    return
        objc.super(VariableStrokeTool, self).mouseDown_(event)

    def mouseDragged_(self, event):
        if self._drag is None:
            objc.super(VariableStrokeTool, self).mouseDragged_(event)
            self._redraw()
            return
        layer, node, _, n = self._drag
        loc = self.editViewController().graphicView().getActiveLocation_(event)
        center = (node.position.x, node.position.y)
        delta = sub((loc.x, loc.y), center)
        node.userData[WIDTH_KEY] = max(1.0, 2.0 * abs(delta[0]*n[0] + delta[1]*n[1]))
        self._redraw()

    def mouseUp_(self, event):
        if self._drag is not None:
            layer = self._drag[0]
            self._drag = None
            layer.endChanges()
            self._redraw()
            return
        objc.super(VariableStrokeTool, self).mouseUp_(event)
        self._redraw()

    @objc.python_method
    def __file__(self):
        return __file__
