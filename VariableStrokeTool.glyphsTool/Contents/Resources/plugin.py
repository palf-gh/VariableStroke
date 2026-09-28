# encoding: utf-8
"""Variable Stroke editing tool for Glyphs 3."""
import objc
from AppKit import NSBezierPath, NSColor, NSPoint, NSEvenOddWindingRule
from GlyphsApp import Glyphs, GSCustomParameter, OFFCURVE, DOCUMENTOPENED, UPDATEINTERFACE, Message
from GlyphsApp.plugins import SelectTool
from vanilla import Window, Group, CheckBox, TextBox, EditText, PopUpButton, Button
from glyphs_bridge import (PATH_KEY, WIDTH_KEY, CAP_START_KEY, CAP_END_KEY,
                           EXPORT_FILTER, DEFAULT_WIDTH, enabled, width, polygons_for_path,
                           segments_for_path, convert_layer)
from variable_stroke_core import normal, unit, sub, add, mul, length

CAP_NAMES = [('flat', 'フラット'), ('round', '丸'), ('square', '四角'),
             ('horizontal', '水平カット'), ('vertical', '垂直カット')]
ORIGINAL_FILL_KEY = 'com.codex.VariableStroke.originalFill'


def _loc(english, japanese):
    return Glyphs.localize({'en': english, 'jp': japanese, 'ja': japanese})


GSInspectorView = objc.lookUpClass('GSInspectorView')


class InspectorGroup(Group):
    nsViewClass = GSInspectorView


class VariableStrokeTool(SelectTool):
    @objc.python_method
    def settings(self):
        self.name = _loc('Variable Stroke', '可変ストローク')
        self.toolbarPosition = 105
        self._icon = 'toolbarIconTemplate.pdf'
        self._drag = None
        self._instance_counts = {}
        self._updating_ui = False
        self._last_ui_state = None
        self.generalContextMenus = [
            {'name': _loc('Variable Stroke: Toggle', '可変ストローク：オン／オフ'), 'action': self.toggleStroke_},
            {'name': _loc('Variable Stroke: Convert to Outlines', '可変ストローク：アウトライン化'),
             'action': self.convertSelectedLayer_},
        ]
        self._build_inspector()

    @objc.python_method
    def _build_inspector(self):
        width, height = 290, 182
        self.infoBoxWindow = Window((width, height))
        group = self.infoBoxWindow.group = InspectorGroup((0, 0, width, height))
        group.targetLabel = TextBox((12, 7, 270, 18), _loc('Select a path', 'パスを選択してください'))
        group.enableStroke = CheckBox((10, 28, 270, 22), _loc('Stroke ON', 'ストロークを有効にする'),
                                callback=self.toggleFromInspector_)
        group.widthLabel = TextBox((12, 59, 70, 20), _loc('Width', '線幅'))
        group.widthField = EditText((88, 55, 185, 24), callback=self.widthFromInspector_,
                                    continuous=False)
        group.startLabel = TextBox((12, 89, 70, 20), _loc('Start cap', '始点の線端'))
        group.startCap = PopUpButton((88, 85, 185, 25),
                                  [_loc(english, japanese) for english, japanese in
                                   [('Flat', 'フラット'), ('Round', '丸'), ('Square', '四角'),
                                    ('Horizontal cut', '水平カット'), ('Vertical cut', '垂直カット')]],
                                  callback=self.startCapFromInspector_)
        group.endLabel = TextBox((12, 119, 70, 20), _loc('End cap', '終点の線端'))
        group.endCap = PopUpButton((88, 115, 185, 25),
                                [_loc(english, japanese) for english, japanese in
                                 [('Flat', 'フラット'), ('Round', '丸'), ('Square', '四角'),
                                  ('Horizontal cut', '水平カット'), ('Vertical cut', '垂直カット')]],
                                callback=self.endCapFromInspector_)
        group.convert = Button((88, 147, 185, 24), _loc('Convert to Outlines', 'アウトライン化'),
                               callback=self.convertSelectedLayer_)
        self.infoBoxView = group.getNSView()
        self.inspectorDialogView = True

    def view(self):
        self._refresh_ui()
        return self.infoBoxView

    @objc.python_method
    def start(self):
        Glyphs.addCallback(self._sync_export, DOCUMENTOPENED)
        Glyphs.addCallback(self._sync_export, UPDATEINTERFACE)
        self._sync_export()

    @objc.python_method
    def activate(self):
        self._sync_export()
        self._refresh_ui()

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
        self._refresh_ui()

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

    @objc.python_method
    def _target_paths(self, layer):
        paths = self._selected_paths(layer)
        if paths:
            return paths
        return list(layer.paths) if len(layer.paths) == 1 else []

    @objc.python_method
    def _target_nodes(self, layer, paths):
        selected = list(layer.selection)
        nodes = [node for path in paths for node in path.nodes
                 if node.type != OFFCURVE and node in selected]
        return nodes if nodes else [node for path in paths for node in path.nodes
                                   if node.type != OFFCURVE]

    @objc.python_method
    def _refresh_ui(self):
        if self._updating_ui or not hasattr(self, 'infoBoxWindow'):
            return
        layer = self._layer()
        paths = self._target_paths(layer) if layer is not None else []
        active = bool(paths) and all(enabled(path) for path in paths)
        editable = bool(paths) and active
        nodes = self._target_nodes(layer, paths) if editable else []
        widths = [width(node) for node in nodes]
        layer_paths = list(layer.paths) if layer is not None else []
        can_convert = any(enabled(path) for path in layer_paths)
        ui_state = (
            (layer.parent.name, layer.layerId) if layer else None,
            tuple((i, j) for i, path in enumerate(layer_paths)
                  for j, node in enumerate(path.nodes)
                  if node in layer.selection) if layer else (),
            tuple((layer_paths.index(path), enabled(path), bool(path.closed),
                   path.attributes.get(CAP_START_KEY, 'flat'),
                   path.attributes.get(CAP_END_KEY, 'flat')) for path in paths),
            tuple(widths), can_convert,
        )
        if ui_state == self._last_ui_state:
            return
        self._last_ui_state = ui_state
        group = self.infoBoxWindow.group
        self._updating_ui = True
        try:
            if not paths:
                group.targetLabel.set(_loc('Select a path', 'パスを選択してください'))
            elif len(paths) == 1 and layer is not None and len(layer_paths) == 1:
                group.targetLabel.set(_loc('One path in this layer', 'このレイヤーのパスを編集中'))
            else:
                group.targetLabel.set(_loc('%d selected paths' % len(paths),
                                           '選択中のパス：%d本' % len(paths)))
            group.enableStroke.enable(bool(paths))
            group.enableStroke.set(active)
            group.widthField.enable(editable)
            group.startCap.enable(editable and all(not path.closed for path in paths))
            group.endCap.enable(editable and all(not path.closed for path in paths))
            group.convert.enable(can_convert)
            group.widthField.set(('%g' % widths[0]) if widths and
                                 all(abs(value-widths[0]) < 0.001 for value in widths) else '')
            for side, control in (('start', group.startCap), ('end', group.endCap)):
                key = CAP_START_KEY if side == 'start' else CAP_END_KEY
                styles = [path.attributes.get(key, 'flat') for path in paths]
                if styles and len(set(styles)) == 1:
                    cap_values = [item[0] for item in CAP_NAMES]
                    control.set(cap_values.index(styles[0]) if styles[0] in cap_values else 0)
                else:
                    control.set(0)
        finally:
            self._updating_ui = False

    def toggleStroke_(self, sender):
        layer = self._layer()
        if layer is None:
            return
        paths = self._target_paths(layer)
        self._set_enabled(paths, not all(enabled(path) for path in paths))

    def toggleFromInspector_(self, sender):
        if self._updating_ui:
            return
        layer = self._layer()
        if layer is not None:
            self._set_enabled(self._target_paths(layer), bool(sender.get()))

    @objc.python_method
    def _set_enabled(self, paths, state):
        if not paths:
            return
        if state:
            try:
                for path in paths:
                    segments_for_path(path)
            except ValueError:
                Message(_loc('Only line and cubic paths are supported.',
                             '直線と3次ベジェのパスのみ対応しています。'),
                        title=self.name)
                self._refresh_ui()
                return
        layer = self._layer()
        if layer is None:
            return
        layer.beginChanges()
        try:
            for path in paths:
                if state:
                    if ORIGINAL_FILL_KEY not in path.attributes:
                        path.attributes[ORIGINAL_FILL_KEY] = bool(path.attributes.get('fill', True))
                    path.attributes[PATH_KEY] = True
                    path.attributes[CAP_START_KEY] = path.attributes.get(CAP_START_KEY, 'flat')
                    path.attributes[CAP_END_KEY] = path.attributes.get(CAP_END_KEY, 'flat')
                    path.attributes['fill'] = False
                    for node in path.nodes:
                        if node.type != OFFCURVE and WIDTH_KEY not in node.userData:
                            node.userData[WIDTH_KEY] = DEFAULT_WIDTH
                else:
                    path.attributes[PATH_KEY] = False
                    path.attributes['fill'] = bool(path.attributes.get(ORIGINAL_FILL_KEY, True))
        finally:
            layer.endChanges()
        self._instance_counts.clear()
        self._sync_export()
        self._refresh_ui()
        self._redraw()

    @objc.python_method
    def _set_cap(self, side, style):
        layer = self._layer()
        if layer is None:
            return
        key = CAP_START_KEY if side == 'start' else CAP_END_KEY
        layer.beginChanges()
        try:
            for path in self._target_paths(layer):
                if enabled(path) and not path.closed:
                    path.attributes[key] = style
        finally:
            layer.endChanges()
        self._refresh_ui()
        self._redraw()

    def startCapFromInspector_(self, sender):
        if not self._updating_ui:
            self._set_cap('start', CAP_NAMES[sender.get()][0])

    def endCapFromInspector_(self, sender):
        if not self._updating_ui:
            self._set_cap('end', CAP_NAMES[sender.get()][0])

    def widthFromInspector_(self, sender):
        if self._updating_ui:
            return
        try:
            value = float(sender.get())
        except (TypeError, ValueError):
            return
        if not 0 < value < 10000:
            return
        layer = self._layer()
        if layer is None:
            return
        paths = [path for path in self._target_paths(layer) if enabled(path)]
        nodes = self._target_nodes(layer, paths)
        if not nodes:
            return
        layer.beginChanges()
        try:
            for node in nodes:
                node.userData[WIDTH_KEY] = value
        finally:
            layer.endChanges()
        self._redraw()

    def convertSelectedLayer_(self, sender):
        layer = self._layer()
        if layer is None:
            return
        layer.beginChanges()
        try:
            convert_layer(layer)
        finally:
            layer.endChanges()
        self._refresh_ui()
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
            self._refresh_ui()
            self._redraw()
            return
        objc.super(VariableStrokeTool, self).mouseUp_(event)
        self._refresh_ui()
        self._redraw()

    @objc.python_method
    def __file__(self):
        return __file__
