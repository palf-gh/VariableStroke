# encoding: utf-8
"""Variable Stroke editing tool for Glyphs 3."""
import traceback
import objc
from AppKit import (NSBezierPath, NSColor, NSEvent, NSImage, NSMenu, NSMenuItem, NSObject,
                    NSThread,
                    NSEventModifierFlagOption,
                    NSRoundLineCapStyle, NSRoundLineJoinStyle)
from GlyphsApp import (Glyphs, GSCallbackHandler, GSCustomParameter, OFFCURVE, DOCUMENTOPENED,
                       UPDATEINTERFACE, DRAWBACKGROUND, CONTEXTMENUCALLBACK, WINDOW_MENU)
from GlyphsApp.plugins import SelectTool
from vanilla import (Window, FloatingWindow, Group, SegmentedButton, TextBox, EditText,
                     ImageButton, Button, List)
from glyphs_bridge import (CAP_START_KEY, CAP_END_KEY, CAP_START_ANGLE_KEY, CAP_END_ANGLE_KEY,
                           WIDTH_KEY,
                           cut_angle, STROKE_WIDTH_KEY, SCALE_KEY, EXPORT_FILTER,
                           enabled, stroke_width, scale, migrate_path, interpolate_widths,
                           segments_for_path, convert_layer, expand_layer, generated,
                           cleanup_legacy_layer, glyph_enabled, GLYPH_KEY, set_glyph_enabled,
                           normalize_layer, MASTER_WIDTH_KEY, master_default_width,
                           has_width_override, reset_width_overrides, STROKE_HEIGHT_KEY,
                           OFFSET_KEY, MASTER_HEIGHT_KEY, master_default_height, master_defaults,
                           layer_defaults, stroke_height, has_height_override, offset,
                           edges_for_path)
from variable_stroke_core import unit, sub, add, length, outline_curves

CAP_NAMES = [('flat', 'Flat', 'フラット'), ('round', 'Round', '丸'),
             ('square', 'Square', '四角'), ('horizontal', 'Horizontal cut', '水平カット'),
             ('vertical', 'Vertical cut', '垂直カット'),
             ('angle', 'Cut at a custom angle', '角度カット')]
CAP_VALUES = [item[0] for item in CAP_NAMES]
# Callback names from GlyphsCore/GSCallbackHandler.h: the inspector strip and the
# outline preparation hooks.
INSPECTOR_CALLBACK = 'GSInspectorViewControllersCallback'
PREPARE_LAYER_CALLBACK = 'GSPrepareLayerCallback'
PANEL_SIZE = (442, 52)


def _loc(english, japanese):
    return Glyphs.localize({'en': english, 'jp': japanese, 'ja': japanese})


def _ensure_export_filter(font):
    # PreInterpolationFilter expands each master before interpolation (static and
    # variable exports); Filter stays as a fallback and finds nothing left to do.
    if font is None:
        return
    for instance in font.instances:
        for name in ('PreInterpolationFilter', 'Filter'):
            if any(parameter.name == name and str(parameter.value).split(';')[0] == EXPORT_FILTER
                   for parameter in instance.customParameters):
                continue
            instance.customParameters.append(GSCustomParameter(name, EXPORT_FILTER + ';'))


def _invalidate(layer, paths=None):
    """Tell Glyphs the layer changed so it prepares its preview outline again."""
    for path in (paths if paths is not None else list(layer.paths)):
        try:
            layer.elementDidChange_(path)
        except Exception:
            pass


def _cap_icon(style):
    """Template icon drawn with the real outline code: a diagonal stroke ending in `style`."""
    def draw(rect):
        start, end = (-4.0, -3.0), (11.0, 8.0)
        body = NSBezierPath.bezierPath()
        for contour in outline_curves([('line', (start, end), 7.0, 7.0)], False, 'flat', style,
                                      end_angle=160.0):
            body.moveToPoint_(contour[0][1][0])
            for kind, points in contour:
                if kind == 'cubic':
                    body.curveToPoint_controlPoint1_controlPoint2_(points[3], points[1], points[2])
                else:
                    body.lineToPoint_(points[1])
            body.closePath()
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.45).set()
        body.fill()
        NSColor.blackColor().set()
        center = NSBezierPath.bezierPath()
        center.moveToPoint_(start)
        center.lineToPoint_(end)
        center.setLineWidth_(1.0)
        center.stroke()
        NSBezierPath.bezierPathWithOvalInRect_(((end[0]-1.6, end[1]-1.6), (3.2, 3.2))).fill()
        return True
    image = NSImage.imageWithSize_flipped_drawingHandler_((18, 14), False, draw)
    image.setTemplate_(True)
    return image


class _Only(object):
    """A layer-like view exposing only some of a layer's paths."""

    def __init__(self, layer, paths):
        self.paths = paths


def _master_for(layer):
    """The layer's master, also for detached preview copies (found by master id)."""
    try:
        master = layer.master
        if master is not None:
            return master
    except Exception:
        pass
    if not NSThread.isMainThread():
        return None  # the font list is AppKit state: main thread only
    master_id = getattr(layer, 'associatedMasterId', None)
    for font in Glyphs.fonts:
        for master in font.masters:
            if master.id == master_id:
                return master
    return None


def _glyph_state(layer):
    """ON/OFF of the layer's glyph, or None when the layer is detached (export copy)."""
    try:
        glyph = layer.parent
        if glyph is not None and glyph.userData is not None:
            return glyph_enabled(glyph)
    except Exception:
        pass
    return None


def _normalize_quietly(layer):
    """Sync path flags with the glyph's state without adding an undo step."""
    state = _glyph_state(layer)
    if state is None:
        return False
    try:
        manager = layer.parent.undoManager()
    except Exception:
        manager = None
    if manager is not None:
        manager.disableUndoRegistration()
    try:
        changed = normalize_layer(layer, state)
    finally:
        if manager is not None:
            manager.enableUndoRegistration()
    if changed:
        _invalidate(layer)
    return changed


_PATH_KEYS = (STROKE_WIDTH_KEY, STROKE_HEIGHT_KEY, CAP_START_KEY, CAP_END_KEY,
              CAP_START_ANGLE_KEY, CAP_END_ANGLE_KEY)
_NODE_KEYS = (SCALE_KEY, OFFSET_KEY, WIDTH_KEY)
_MISSING = object()


def _parse_field(name, text):
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    if name == 'offset':
        return max(-100.0, min(100.0, value))
    if name in ('startAngle', 'endAngle'):
        return value % 360.0
    return value if 0 < value < 10000 else None


def _apply_field(name, value, paths, nodes):
    for path in paths:
        migrate_path(path)
    if name == 'width':
        for path in paths:
            path.attributes[STROKE_WIDTH_KEY] = value
    elif name == 'height':
        for path in paths:
            path.attributes[STROKE_HEIGHT_KEY] = value
    elif name == 'scale':
        for node in nodes:
            node.userData[SCALE_KEY] = value
    elif name == 'offset':
        for node in nodes:
            node.userData[OFFSET_KEY] = value
    else:
        key, angle_key = ((CAP_START_KEY, CAP_START_ANGLE_KEY) if name == 'startAngle'
                          else (CAP_END_KEY, CAP_END_ANGLE_KEY))
        for path in paths:
            path.attributes[angle_key] = value
            path.attributes[key] = 'angle'  # typing an angle selects the angle cut


def _field_is(name, value, paths, nodes):
    """True when the targets already hold `value` (a repeated commit does nothing)."""
    if name in ('width', 'height'):
        key = STROKE_WIDTH_KEY if name == 'width' else STROKE_HEIGHT_KEY
        return all(path.attributes.get(key) == value for path in paths)
    if name in ('scale', 'offset'):
        key = SCALE_KEY if name == 'scale' else OFFSET_KEY
        return all(node.userData.get(key) == value for node in nodes)
    key, angle_key = ((CAP_START_KEY, CAP_START_ANGLE_KEY) if name == 'startAngle'
                      else (CAP_END_KEY, CAP_END_ANGLE_KEY))
    return all(path.attributes.get(key) == 'angle' and path.attributes.get(angle_key) == value
               for path in paths)


def _snapshot(paths):
    """The stroke settings of `paths` and their nodes, to undo a live preview."""
    saved = []
    for path in paths:
        saved.append((path.attributes, {k: path.attributes.get(k, _MISSING) for k in _PATH_KEYS}))
        for node in path.nodes:
            saved.append((node.userData, {k: node.userData.get(k, _MISSING) for k in _NODE_KEYS}))
    return saved


def _restore(snapshot):
    for store, values in snapshot:
        for key, value in values.items():
            if value is _MISSING:
                if store.get(key) is not None:
                    del store[key]
            else:
                store[key] = value


GSInspectorView = objc.lookUpClass('GSInspectorView')
class InspectorGroup(Group):
    nsViewClass = GSInspectorView


class SteppingEditText(EditText):
    # Glyphs' own numeric field: arrow keys step by 1, with Shift by 10.
    try:
        nsTextFieldClass = objc.lookUpClass('GSSteppingTextField')
    except objc.nosuchclass_error:
        pass


class VariableStrokeFieldDelegate(NSObject):
    """Routes one numeric field to the tool: typing previews live, Return / Tab /
    leaving the field commits (one undo step), arrow-key steps commit at once."""

    def initWithTool_name_(self, tool, name):
        self = objc.super(VariableStrokeFieldDelegate, self).init()
        if self is None:
            return None
        self._tool, self._name = tool, name
        return self

    def controlTextDidChange_(self, notification):
        self._tool._field_edit(self._name, notification.object().stringValue(), False)

    def controlTextDidEndEditing_(self, notification):
        self._tool._field_edit(self._name, notification.object().stringValue(), True)

    def stepped_(self, sender):
        self._tool._field_edit(self._name, sender.stringValue(), True)


class VariableStrokeLayerProcessor(NSObject):
    """GSPrepareLayerCallback: Glyphs hands over the copy it builds for preview,
    inactive glyphs and metrics. Centerlines become outlines there only, so the
    editable layer keeps nothing but the centerline and its nodes."""

    def callOrder(self):
        return 0

    @objc.signature(b'Z@:@@o^@')
    def processLayer_extraHandles_error_(self, layer, extraHandles, error):
        try:
            # Glyphs also prepares layers for other plugins, sometimes off the main
            # thread (e.g. a reporter building paths in a worker thread). Leave
            # every layer without strokes untouched and return at once.
            state = _glyph_state(layer)
            if state is False:
                return True, None
            paths = list(layer.paths)
            if not paths or (state is None and not any(enabled(path) for path in paths)):
                return True, None
            expand_layer(layer, state, master_defaults(_master_for(layer)))
        except Exception:
            print(traceback.format_exc())
        return True, None

    @objc.signature(b'Z@:@@@o^@')
    def interpolateLayer_glyph_interpolation_error_(self, layer, glyph, interpolation, error):
        # Instances in the preview: blend widths from the masters as well.
        try:
            interpolate_widths(layer, glyph, interpolation)
        except Exception:
            print(traceback.format_exc())
        return True, None


class VariableStrokeContextMenu(NSObject):
    def contextMenuCallback_forSelectedLayers_event_(self, menu, layers, event):
        glyphs = []
        for layer in layers or []:
            glyph = getattr(layer, 'parent', None)
            if glyph is not None and glyph not in glyphs:
                glyphs.append(glyph)
        if not glyphs:
            font = Glyphs.font
            glyphs = list(font.selection) if font else []
            if not glyphs and font:
                glyphs = [layer.parent for layer in font.selectedLayers]
            if not glyphs and font and font.currentTab:
                active_layer = font.currentTab.graphicView().activeLayer()
                if active_layer is not None:
                    glyphs = [active_layer.parent]
        submenu = NSMenu.alloc().initWithTitle_(_loc('Variable Stroke', '可変ストローク'))
        submenu.setAutoenablesItems_(False)
        for title, action in ((_loc('Turn ON', 'オン'), 'turnOn_'),
                              (_loc('Turn OFF', 'オフ'), 'turnOff_'),
                              (_loc('Convert to Outlines', 'アウトライン化'), 'convert_')):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, getattr(self, action), '')
            item.setTarget_(self)
            item.setRepresentedObject_(glyphs)
            item.setEnabled_(bool(glyphs))
            if action == 'turnOn_':
                item.setState_(1 if glyphs and all(glyph_enabled(g) for g in glyphs) else 0)
            elif action == 'turnOff_':
                item.setState_(1 if glyphs and all(not glyph_enabled(g) for g in glyphs) else 0)
            submenu.addItem_(item)
        parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(_loc('Variable Stroke', '可変ストローク'), None, '')
        parent.setSubmenu_(submenu)
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(parent)

    def turnOn_(self, sender):
        self._set_glyphs(sender.representedObject(), True)

    def turnOff_(self, sender):
        self._set_glyphs(sender.representedObject(), False)

    def convert_(self, sender):
        for glyph in sender.representedObject():
            for layer in glyph.layers:
                layer.beginChanges()
                try:
                    convert_layer(layer)
                finally:
                    layer.endChanges()
        Glyphs.redraw()

    @objc.python_method
    def _set_glyphs(self, glyphs, state):
        for glyph in glyphs:
            set_glyph_enabled(glyph, state)
            for layer in glyph.layers:
                _invalidate(layer)
        if state and glyphs:
            _ensure_export_filter(glyphs[0].parent)
        Glyphs.redraw()

    def callOrder(self):
        return 1000000


class VariableStrokeInspectorController(NSObject):
    """Supplies `-view` for the inspector strip."""

    def initWithTool_(self, tool):
        self = objc.super(VariableStrokeInspectorController, self).init()
        if self is None:
            return None
        self._tool = tool
        return self

    def view(self):
        return self._tool.infoBoxView


class VariableStrokeInspectorProvider(NSObject):
    """Answers Glyphs' inspector callback while the Variable Stroke tool is active."""

    def initWithTool_(self, tool):
        self = objc.super(VariableStrokeInspectorProvider, self).init()
        if self is None:
            return None
        self._tool = tool
        self._controller = VariableStrokeInspectorController.alloc().initWithTool_(tool)
        return self

    def inspectorViewControllersForLayer_(self, layer):
        if not self._tool._is_current():
            return []
        self._tool._last_ui_state = None
        self._tool._refresh_ui()
        return [self._controller]

    def showSettings_(self, sender):
        self._tool._show_settings()

    def reloadInspector_(self, sender):
        # Glyphs may build the strip before willActivate; ask it to rebuild.
        font = Glyphs.font
        targets = []
        try:
            targets.append(font.currentTab)
            targets.append(font.parent.windowController())
        except Exception:
            pass
        for target in targets:
            try:
                if target is not None and target.respondsToSelector_('updateInspector'):
                    target.updateInspector()
            except Exception:
                pass

    def callOrder(self):
        return 1000000


class VariableStrokeSettings(object):
    """Per-font settings window: the default stroke width and height of each master.

    Paths without their own width follow their master's default, so changing
    a value here restyles every such stroke of that master at once.
    """

    def __init__(self):
        self.font = None
        self.w = FloatingWindow((400, 260), _loc('Variable Stroke Settings', '可変ストローク設定'),
                                minSize=(320, 190))
        self.w.note = TextBox((12, 10, -12, 42), _loc(
            'Paths without their own width use the default of their master. '
            'Empty height = same as width. Height follows the italic angle.',
            'パスごとの線幅を指定していないストロークは、マスターの既定値に従います。'
            '高さが空欄なら幅と同じ。高さはイタリック角度の方向に測ります。'),
            sizeStyle='small')
        self.w.masters = List((12, 56, -12, -44), [], columnDescriptions=[
            {'title': _loc('Master', 'マスター'), 'key': 'name', 'editable': False},
            {'title': _loc('Width', '既定幅'), 'key': 'width', 'editable': True, 'width': 70},
            {'title': _loc('Height', '既定高さ'), 'key': 'height', 'editable': True, 'width': 70}],
            editCallback=self.edited, allowsMultipleSelection=True)
        self.w.reset = Button((12, -34, -12, 22),
                              _loc('Reset path widths of selected masters to default',
                                   '選択マスターのパス個別線幅を既定に戻す'),
                              callback=self.resetOverrides, sizeStyle='small')
        self._loading = False

    def open(self):
        self.reload(force=True)
        self.w.open()
        self.w.makeKey()

    def is_open(self):
        try:
            return self.w.getNSWindow().isVisible()
        except Exception:
            return False

    def reload(self, force=False):
        font = Glyphs.font
        if font is self.font and not force:
            return
        self.font = font
        self._loading = True
        try:
            rows = []
            for master in (font.masters if font else []):
                height = master_default_height(master)
                rows.append({'name': master.name, 'width': '%g' % master_default_width(master),
                             'height': '%g' % height if height is not None else '',
                             'id': master.id})
            self.w.masters.set(rows)
        finally:
            self._loading = False

    def edited(self, sender):
        if self._loading or self.font is None:
            return
        for item in sender.get():
            master = self.font.masters[item['id']]
            if master is None:
                continue
            changed = False
            try:
                value = float(item['width'])
            except (TypeError, ValueError):
                value = None
            if value is not None and 0 < value < 10000 and \
                    abs(master_default_width(master) - value) > 1e-9:
                master.userData[MASTER_WIDTH_KEY] = value
                changed = True
            text = str(item['height'] or '').strip()
            try:
                height = float(text) if text else None
            except ValueError:
                height = master_default_height(master)
            if height is not None and not 0 < height < 10000:
                height = master_default_height(master)
            if height != master_default_height(master):
                if height is None:
                    del master.userData[MASTER_HEIGHT_KEY]
                else:
                    master.userData[MASTER_HEIGHT_KEY] = height
                changed = True
            if changed:
                self._invalidate_master(master.id)
        Glyphs.redraw()

    def resetOverrides(self, sender):
        if self.font is None:
            return
        items = self.w.masters.get()
        master_ids = {items[i]['id'] for i in self.w.masters.getSelection()}
        for glyph in self.font.glyphs:
            if not glyph_enabled(glyph):
                continue
            layers = [layer for layer in glyph.layers if layer.associatedMasterId in master_ids]
            if layers and reset_width_overrides(layers):
                for layer in layers:
                    _invalidate(layer)
        Glyphs.redraw()

    def _invalidate_master(self, master_id):
        for glyph in self.font.glyphs:
            if glyph_enabled(glyph):
                for layer in glyph.layers:
                    if layer.associatedMasterId == master_id:
                        _invalidate(layer)


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
        self.generalContextMenus = []
        self._is_active = False
        self._normalizing = False
        self._live = None  # snapshot while a field is being typed into
        self._menu_callback = VariableStrokeContextMenu.new()
        self._layer_processor = VariableStrokeLayerProcessor.new()
        self._settings = None
        self._build_inspector()
        self._inspector_provider = VariableStrokeInspectorProvider.alloc().initWithTool_(self)

    @objc.python_method
    def _build_inspector(self):
        # Compact strip beside Glyphs' own info box:
        #   [ON|OFF] Width [ 40] Height [ 40] ↺  Node [100]%  Position [0]%  ⚙
        #   Start [5 cap icons]  End [5 cap icons]
        width_px, height_px = PANEL_SIZE
        self.infoBoxWindow = Window((width_px, height_px))
        group = self.infoBoxWindow.group = InspectorGroup((0, 0, width_px, height_px))
        group.enableStroke = SegmentedButton((6, 4, 70, 20), [{'title': 'ON'}, {'title': 'OFF'}],
                                             callback=self.toggleFromInspector_, sizeStyle='small')
        group.widthLabel = TextBox((82, 7, 18, 14), _loc('W', '幅'), sizeStyle='small')
        group.widthField = SteppingEditText((100, 4, 42, 19), sizeStyle='small')
        group.heightLabel = TextBox((147, 7, 27, 14), _loc('H', '高さ'), sizeStyle='small')
        group.heightField = SteppingEditText((174, 4, 42, 19), sizeStyle='small')
        group.widthReset = ImageButton((218, 5, 17, 17), imageNamed='NSRefreshTemplate',
                                       bordered=False, callback=self.resetWidthFromInspector_)
        group.scaleLabel = TextBox((243, 7, 35, 14), _loc('Node', 'ノード'), sizeStyle='small')
        group.scaleField = SteppingEditText((278, 4, 40, 19), sizeStyle='small')
        group.scaleUnit = TextBox((320, 7, 12, 14), '%', sizeStyle='small')
        group.offsetLabel = TextBox((336, 7, 27, 14), _loc('Pos', '位置'), sizeStyle='small')
        group.offsetField = SteppingEditText((363, 4, 40, 19), sizeStyle='small')
        group.offsetUnit = TextBox((405, 7, 12, 14), '%', sizeStyle='small')
        group.settings = ImageButton((420, 5, 17, 17), imageNamed='NSActionTemplate',
                                     bordered=False, callback=self.showSettingsFromInspector_)
        group.widthReset.getNSButton().setToolTip_(
            _loc('Follow the master default width and height', 'マスターの既定の幅・高さに戻す'))
        group.settings.getNSButton().setToolTip_(
            _loc('Variable Stroke Settings…', '可変ストローク設定…'))
        group.heightField.getNSTextField().setToolTip_(
            _loc('Stroke height of the path (thickness of horizontal strokes; along the italic '
                 'angle when there is one). Grey: follows the master default.',
                 'パスの高さ（横線の太さ。イタリック角度がある場合はその方向）。'
                 'グレー表示はマスターの既定値に従っています。'))
        group.offsetField.getNSTextField().setToolTip_(
            _loc('Where the centerline sits in the stroke at the selected nodes: 0 centre, '
                 '100 stroke on the left, -100 on the right (of the path direction). '
                 'Option-drag a handle to move one side only.',
                 '選択ノードでの中心線の位置：0 で中央、100 で線幅がパスの進行方向の左側、'
                 '−100 で右側。ハンドルを Option ドラッグすると片側だけ動かせます。'))
        caps = [{'imageObject': _cap_icon(value), 'width': 21} for value in CAP_VALUES]
        group.startLabel = TextBox((6, 33, 30, 14), _loc('Start', '始点'), sizeStyle='small')
        group.startCap = SegmentedButton((36, 29, 128, 20), caps,
                                         callback=self.startCapFromInspector_, sizeStyle='small')
        group.startAngle = SteppingEditText((168, 29, 36, 19), sizeStyle='small')
        group.startAngleUnit = TextBox((206, 32, 10, 14), '°', sizeStyle='small')
        group.endLabel = TextBox((222, 33, 30, 14), _loc('End', '終点'), sizeStyle='small')
        group.endCap = SegmentedButton((252, 29, 128, 20), caps,
                                       callback=self.endCapFromInspector_, sizeStyle='small')
        group.endAngle = SteppingEditText((384, 29, 36, 19), sizeStyle='small')
        group.endAngleUnit = TextBox((422, 32, 10, 14), '°', sizeStyle='small')
        for field in (group.startAngle, group.endAngle):
            field.getNSTextField().setToolTip_(_loc(
                'Cut angle on the page (0 horizontal, 90 vertical); typing one selects the angle cut',
                'カットの角度（ページ上、0 で水平、90 で垂直）。入力すると角度カットになります'))
        for control in (group.startCap, group.endCap):
            segmented = control.getNSSegmentedButton()
            for index, (_, english, japanese) in enumerate(CAP_NAMES):
                segmented.setToolTip_forSegment_(_loc(english, japanese), index)
        group.widthField.getNSTextField().setToolTip_(
            _loc('Stroke width of the path (thickness of vertical strokes). '
                 'Grey: follows the master default.',
                 'パスの幅（縦線の太さ）。グレー表示はマスターの既定値に従っています。'))
        group.scaleField.getNSTextField().setToolTip_(
            _loc('Width of the selected nodes, in % of the stroke width',
                 '選択ノードの太さ（線幅に対する％）'))
        group.enableStroke.set(1)
        # Numeric fields: live preview while typing, Tab / Shift-Tab between them.
        self._field_delegates = []
        fields = [('width', group.widthField), ('height', group.heightField),
                  ('scale', group.scaleField), ('offset', group.offsetField),
                  ('startAngle', group.startAngle), ('endAngle', group.endAngle)]
        for name, field in fields:
            delegate = VariableStrokeFieldDelegate.alloc().initWithTool_name_(self, name)
            self._field_delegates.append(delegate)
            text_field = field.getNSTextField()
            text_field.setDelegate_(delegate)
            text_field.setTarget_(delegate)
            text_field.setAction_('stepped:')
        for (_, field), (_, following) in zip(fields, fields[1:] + fields[:1]):
            field.getNSTextField().setNextKeyView_(following.getNSTextField())
        view = group.getNSView()
        # Glyphs lays the strip out with Auto Layout. A frame-only view collapses to
        # zero size there, which is why the panel never appeared; pin its size.
        view.removeFromSuperview()
        view.setTranslatesAutoresizingMaskIntoConstraints_(False)
        view.widthAnchor().constraintEqualToConstant_(width_px).setActive_(True)
        view.heightAnchor().constraintEqualToConstant_(height_px).setActive_(True)
        self.infoBoxView = view
        # The panel is delivered through INSPECTOR_CALLBACK, not the SDK's view() hook.
        self.inspectorDialogView = None

    @objc.python_method
    def start(self):
        Glyphs.addCallback(self._on_document_opened, DOCUMENTOPENED)
        Glyphs.addCallback(self._on_update, UPDATEINTERFACE)
        Glyphs.addCallback(self._draw_centerlines, DRAWBACKGROUND)
        GSCallbackHandler.addCallback_forOperation_(self._menu_callback, CONTEXTMENUCALLBACK)
        GSCallbackHandler.addCallback_forOperation_(self._inspector_provider, INSPECTOR_CALLBACK)
        GSCallbackHandler.addCallback_forOperation_(self._layer_processor, PREPARE_LAYER_CALLBACK)
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            _loc('Variable Stroke Settings…', '可変ストローク設定…'), 'showSettings:', '')
        item.setTarget_(self._inspector_provider)
        Glyphs.menu[WINDOW_MENU].append(item)
        for font in Glyphs.fonts:
            self._cleanup_font(font)
        self._sync_export()

    @objc.python_method
    def activate(self):
        self._is_active = True
        self._last_ui_state = None
        self._sync_export()
        self._inspector_provider.performSelector_withObject_afterDelay_('reloadInspector:', None, 0.0)

    @objc.python_method
    def deactivate(self):
        self._is_active = False
        self._inspector_provider.performSelector_withObject_afterDelay_('reloadInspector:', None, 0.0)

    @objc.python_method
    def _is_current(self):
        if self._is_active:
            return True
        try:
            return Glyphs.font.tool == self.className()
        except Exception:
            return False

    @objc.python_method
    def _cleanup_font(self, font):
        # An earlier build stored outline paths in the layers; drop them once.
        for glyph in font.glyphs:
            if GLYPH_KEY not in glyph.userData:
                continue
            for layer in glyph.layers:
                changed = cleanup_legacy_layer(layer)
                for path in layer.paths:
                    if enabled(path):
                        changed = migrate_path(path) or changed
                if changed:
                    _invalidate(layer)
            if not glyph_enabled(glyph) and any(enabled(path) for layer in glyph.layers
                                                for path in layer.paths):
                set_glyph_enabled(glyph, False)

    @objc.python_method
    def _show_settings(self):
        if self._settings is None:
            self._settings = VariableStrokeSettings()
        self._settings.open()

    def showSettingsFromInspector_(self, sender):
        self._show_settings()

    @objc.python_method
    def _on_update(self, notification=None):
        # Runs after every edit with any tool: new paths in an ON glyph become
        # strokes, pasted strokes in an OFF glyph stop being strokes.
        if not self._normalizing:
            self._normalizing = True
            try:
                layer = Glyphs.font.currentTab.activeLayer() if Glyphs.font and \
                    Glyphs.font.currentTab else None
                if layer is not None:
                    _normalize_quietly(layer)
                if self._settings is not None and self._settings.is_open():
                    self._settings.reload()
            except Exception:
                pass
            finally:
                self._normalizing = False
        self._refresh_ui()

    @objc.python_method
    def _on_document_opened(self, notification=None):
        try:
            self._cleanup_font(notification.object().font)
        except Exception:
            pass
        self._sync_export()

    @objc.python_method
    def _centerline(self, source):
        line = NSBezierPath.bezierPath()
        segments = segments_for_path(source)
        if not segments:
            return line
        line.moveToPoint_(segments[0][1][0])
        for kind, points, _, _ in segments:
            if kind == 'cubic':
                line.curveToPoint_controlPoint1_controlPoint2_(points[3], points[1], points[2])
            else:
                line.lineToPoint_(points[1])
        if source.closed:
            line.closePath()
        return line

    @objc.python_method
    def _draw_centerlines(self, layer, options=None):
        # Glyphs draws the prepared outline itself; only the light blue
        # centerline is ours.
        if layer is None or not glyph_enabled(getattr(layer, 'parent', None)):
            return
        try:
            scale = max(0.05, float(options['Scale']))
        except Exception:
            scale = self._scale()
        for source in layer.paths:
            if not enabled(source) or generated(source):
                continue
            try:
                line = self._centerline(source)
            except ValueError:
                continue
            line.setLineWidth_(3.0 / scale)
            line.setLineCapStyle_(NSRoundLineCapStyle)
            line.setLineJoinStyle_(NSRoundLineJoinStyle)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.45, 0.62, 0.86, 0.55).set()
            line.stroke()

    @objc.python_method
    def _sync_export(self, notification=None):
        # The Filter custom parameter is evaluated on the export copy of each instance.
        for font in list(Glyphs.fonts):
            has_strokes = any(glyph_enabled(glyph) and enabled(path) for glyph in font.glyphs
                              for layer in glyph.layers for path in layer.paths)
            state = (len(font.instances), has_strokes)
            if self._instance_counts.get(id(font)) == state:
                continue
            self._instance_counts[id(font)] = state
            if has_strokes:
                _ensure_export_filter(font)
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
        return [path for path in layer.paths if not generated(path) and
                any(node in selected for node in path.nodes)]

    @objc.python_method
    def _target_paths(self, layer):
        paths = self._selected_paths(layer)
        if paths:
            return paths
        editable_paths = [path for path in layer.paths if not generated(path)]
        if len(editable_paths) == 1:
            return editable_paths
        return [path for path in editable_paths if enabled(path)]

    @objc.python_method
    def _target_nodes(self, layer, paths):
        selected = list(layer.selection)
        nodes = [node for path in paths for node in path.nodes
                 if node.type != OFFCURVE and node in selected]
        return nodes if nodes else [node for path in paths for node in path.nodes
                                   if node.type != OFFCURVE]

    @objc.python_method
    def _refresh_ui(self, notification=None):
        if self._updating_ui or self._live is not None or not hasattr(self, 'infoBoxWindow'):
            return
        layer = self._layer()
        paths = self._target_paths(layer) if layer is not None else []
        active = bool(layer) and glyph_enabled(layer.parent)
        editable = bool(paths) and active
        nodes = self._target_nodes(layer, paths) if editable else []
        bases = [stroke_width(path) for path in paths] if editable else []
        heights = [stroke_height(path) for path in paths] if editable else []
        overridden = [has_width_override(path) for path in paths] if editable else []
        height_overridden = [has_height_override(path) for path in paths] if editable else []
        scales = [scale(node) if SCALE_KEY in node.userData else None for node in nodes]
        offsets = [offset(node) for node in nodes]
        layer_paths = list(layer.paths) if layer is not None else []
        ui_state = (
            (layer.parent.name, layer.layerId) if layer else None,
            tuple((i, j) for i, path in enumerate(layer_paths)
                  for j, node in enumerate(path.nodes)
                  if node in layer.selection) if layer else (),
            tuple((layer_paths.index(path), enabled(path), bool(path.closed),
                   path.attributes.get(CAP_START_KEY, 'flat'),
                   path.attributes.get(CAP_END_KEY, 'flat'),
                   path.attributes.get(CAP_START_ANGLE_KEY), path.attributes.get(CAP_END_ANGLE_KEY))
                  for path in paths),
            tuple(bases), tuple(heights), tuple(overridden), tuple(height_overridden),
            tuple(scales), tuple(offsets),
            glyph_enabled(layer.parent) if layer else False,
        )
        if ui_state == self._last_ui_state:
            return
        self._last_ui_state = ui_state
        group = self.infoBoxWindow.group
        self._updating_ui = True

        def same(values):
            return values and all(v is not None and abs(v-values[0]) < 0.001 for v in values)
        try:
            open_paths = editable and any(not path.closed for path in paths)
            group.enableStroke.enable(layer is not None)
            group.enableStroke.set(0 if active else 1)
            for field in (group.widthField, group.heightField, group.scaleField,
                          group.offsetField):
                field.enable(editable)
            group.startCap.enable(open_paths)
            group.endCap.enable(open_paths)
            group.widthField.set(('%g' % bases[0]) if same(bases) else '')
            group.heightField.set(('%g' % round(heights[0], 2)) if same(heights) else '')
            group.widthReset.enable(any(overridden) or any(height_overridden))
            for field, flags in ((group.widthField, overridden),
                                 (group.heightField, height_overridden)):
                field.getNSTextField().setTextColor_(
                    NSColor.labelColor() if any(flags) else NSColor.secondaryLabelColor())
            group.offsetField.set(('%g' % offsets[0]) if same(offsets) else '')
            scales = [value if value is not None else 100.0 for value in scales]
            group.scaleField.set(('%g' % scales[0]) if same(scales) else '')
            for key, angle_key, field in ((CAP_START_KEY, CAP_START_ANGLE_KEY, group.startAngle),
                                          (CAP_END_KEY, CAP_END_ANGLE_KEY, group.endAngle)):
                open_ones = [path for path in paths if not path.closed]
                angles = [cut_angle(path, angle_key) for path in open_ones]
                field.enable(open_paths)
                field.set(('%g' % angles[0]) if same(angles) else '')
                field.getNSTextField().setTextColor_(
                    NSColor.labelColor() if any(path.attributes.get(key) == 'angle'
                                                for path in open_ones)
                    else NSColor.secondaryLabelColor())
            for key, control in ((CAP_START_KEY, group.startCap), (CAP_END_KEY, group.endCap)):
                styles = {path.attributes.get(key, 'flat') for path in paths if not path.closed}
                segmented = control.getNSSegmentedButton()
                if len(styles) == 1 and next(iter(styles)) in CAP_VALUES:
                    control.set(CAP_VALUES.index(next(iter(styles))))
                else:
                    segmented.setSelectedSegment_(-1)  # mixed or none
        finally:
            self._updating_ui = False

    def toggleStroke_(self, sender):
        layer = self._layer()
        if layer is None:
            return
        paths = self._target_paths(layer)
        self._set_enabled(paths, not glyph_enabled(layer.parent))

    def toggleFromInspector_(self, sender):
        if self._updating_ui:
            return
        layer = self._layer()
        if layer is not None:
            self._set_enabled(self._target_paths(layer), sender.get() == 0)

    @objc.python_method
    def _set_enabled(self, paths, state):
        layer = self._layer()
        if layer is None:
            return
        # The switch applies to the whole glyph. Path selection only controls
        # which widths and caps are edited after the glyph is enabled.
        layer.beginChanges()
        try:
            set_glyph_enabled(layer.parent, state)
        finally:
            layer.endChanges()
        for glyph_layer in layer.parent.layers:
            _invalidate(glyph_layer)
        if state:
            _ensure_export_filter(layer.parent.parent)
        self._instance_counts.clear()
        self._refresh_ui()
        Glyphs.redraw()

    @objc.python_method
    def _set_cap(self, side, style):
        layer = self._layer()
        if layer is None:
            return
        key = CAP_START_KEY if side == 'start' else CAP_END_KEY
        layer.beginChanges()
        changed = []
        try:
            for path in self._target_paths(layer):
                if enabled(path) and glyph_enabled(layer.parent) and not path.closed:
                    path.attributes[key] = style
                    changed.append(path)
        finally:
            layer.endChanges()
        _invalidate(layer, changed)
        self._refresh_ui()
        self._redraw()

    def startCapFromInspector_(self, sender):
        if not self._updating_ui and 0 <= sender.get() < len(CAP_VALUES):
            self._set_cap('start', CAP_VALUES[sender.get()])

    def endCapFromInspector_(self, sender):
        if not self._updating_ui and 0 <= sender.get() < len(CAP_VALUES):
            self._set_cap('end', CAP_VALUES[sender.get()])

    @objc.python_method
    def _edit_paths(self, layer):
        if layer is None or not glyph_enabled(layer.parent):
            return []
        return [path for path in self._target_paths(layer) if enabled(path)]

    @objc.python_method
    def _field_targets(self, name, layer):
        """(paths, nodes) a field edits: widths/heights and cut angles act on the
        target paths, node % and position on the selected nodes (all if none)."""
        paths = self._edit_paths(layer)
        if name in ('startAngle', 'endAngle'):
            return [path for path in paths if not path.closed], []
        if name in ('scale', 'offset'):
            return paths, (self._target_nodes(layer, paths) if paths else [])
        return paths, []

    @objc.python_method
    def _apply_field(self, name, value, paths, nodes):
        _apply_field(name, value, paths, nodes)

    @objc.python_method
    def _quietly(self, layer, action):
        """Run `action` without recording undo (live preview while typing)."""
        try:
            manager = layer.parent.undoManager()
        except Exception:
            manager = None
        if manager is not None:
            manager.disableUndoRegistration()
        try:
            action()
        finally:
            if manager is not None:
                manager.enableUndoRegistration()

    @objc.python_method
    def _field_edit(self, name, text, commit):
        """Typing previews the value live without undo; committing restores the
        pre-edit state and applies the final value once, as one undo step."""
        if self._updating_ui:
            return
        layer = self._layer()
        live = self._live
        if live is not None and (live['name'] != name or live['layer'] != layer):
            self._quietly(live['layer'], lambda: _restore(live['snapshot']))
            _invalidate(live['layer'], live['paths'])
            self._live = live = None
        paths, nodes = self._field_targets(name, layer)
        value = _parse_field(name, text)
        if not paths or (name in ('scale', 'offset') and not nodes):
            return
        if not commit:
            if live is None:
                live = self._live = {'name': name, 'layer': layer, 'paths': paths,
                                     'snapshot': _snapshot(paths)}

            def preview():
                _restore(live['snapshot'])  # half-typed text shows the original
                if value is not None:
                    _apply_field(name, value, paths, nodes)
            self._quietly(layer, preview)
            _invalidate(layer, paths)
            self._redraw()
            return
        if live is not None:
            self._quietly(layer, lambda: _restore(live['snapshot']))
            self._live = None
        if value is not None and not _field_is(name, value, paths, nodes):
            layer.beginChanges()
            try:
                _apply_field(name, value, paths, nodes)
            finally:
                layer.endChanges()
        _invalidate(layer, paths)
        self._last_ui_state = None
        self._refresh_ui()
        self._redraw()

    def resetWidthFromInspector_(self, sender):
        """Drop the path's own width/height so it follows the master default again."""
        layer = self._layer()
        paths = self._edit_paths(layer)
        if not paths:
            return
        layer.beginChanges()
        try:
            reset_width_overrides([_Only(layer, paths)])
        finally:
            layer.endChanges()
        _invalidate(layer, paths)
        self._last_ui_state = None
        self._refresh_ui()
        self._redraw()

    @objc.python_method
    def _node_tangent(self, path, index):
        """Direction of the centerline through a node, from its own handles/neighbours."""
        nodes = list(path.nodes)
        count = len(nodes)
        here = (nodes[index].position.x, nodes[index].position.y)

        def neighbour(step):
            i = index
            for _ in range(count-1):
                i += step
                if not path.closed and not 0 <= i < count:
                    return None
                other = nodes[i % count].position
                if length(sub((other.x, other.y), here)) > 1e-6:
                    return (other.x, other.y)
            return None

        before, after = neighbour(-1), neighbour(1)
        directions = [unit(sub(here, before)) if before else None,
                      unit(sub(after, here)) if after else None]
        directions = [d for d in directions if d]
        if not directions:
            return (1.0, 0.0)
        return unit(add(directions[0], directions[-1])) if len(directions) == 2 and \
            length(add(directions[0], directions[-1])) > 1e-6 else directions[-1]

    @objc.python_method
    def _handles(self, layer):
        """Width handles where the outline really passes each centerline node:
        across the stroke on smooth nodes, on the miter / inner corner at corners."""
        if not glyph_enabled(getattr(layer, 'parent', None)):
            return
        defaults = layer_defaults(layer)
        for path in layer.paths:
            if not enabled(path) or generated(path):
                continue
            try:
                edges = edges_for_path(path, defaults)
            except ValueError:
                continue
            for node, pair in edges:
                yield path, node, (node.position.x, node.position.y), pair

    @objc.python_method
    def foreground(self, layer):
        if layer is None:
            return
        scale = self._scale()
        radius = 4.0 / scale
        color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.02, 0.36, 0.81, 1.0)
        for _, _, center, (left, right) in self._handles(layer):
            color.colorWithAlphaComponent_(0.5).set()
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(left)
            line.lineToPoint_(center)
            line.lineToPoint_(right)
            line.setLineWidth_(1.0 / scale)
            line.stroke()
            color.set()
            for handle in (left, right):
                NSBezierPath.bezierPathWithOvalInRect_(((handle[0]-radius, handle[1]-radius),
                                                        (radius*2, radius*2))).fill()

    def mouseDown_(self, event):
        layer = self._layer()
        if layer is not None:
            loc = self.editViewController().graphicView().getActiveLocation_(event)
            point = (loc.x, loc.y)
            threshold = 8.0 / self._scale()
            for path, node, center, (left, right) in self._handles(layer):
                for sign, handle, other in ((1, left, right), (-1, right, left)):
                    if length(sub(point, handle)) > threshold:
                        continue
                    layer.beginChanges()
                    migrate_path(path)
                    near, far = length(sub(handle, center)), length(sub(other, center))
                    direction = unit(sub(handle, center)) if near > 1e-6 \
                        else unit(sub(center, other))
                    self._drag = {'layer': layer, 'path': path, 'node': node, 'sign': sign,
                                  'center': center, 'direction': direction,
                                  'near': near, 'far': far,
                                  'offset': offset(node) / 100.0, 'scale': scale(node)}
                    return
        objc.super(VariableStrokeTool, self).mouseDown_(event)

    def mouseDragged_(self, event):
        if self._drag is None:
            objc.super(VariableStrokeTool, self).mouseDragged_(event)
            self._redraw()
            return
        drag = self._drag
        loc = self.editViewController().graphicView().getActiveLocation_(event)
        delta = sub((loc.x, loc.y), drag['center'])
        # The handle slides on the line from the node through its outline point
        # (across the stroke, or towards the corner at corners).
        reach = max(0.0, delta[0]*drag['direction'][0] + delta[1]*drag['direction'][1])
        node, sign, o, pct = drag['node'], drag['sign'], drag['offset'], drag['scale']
        # Each side's reach is proportional to (1 ± offset) x scale.
        near_share, far_share = (1 + sign*o) * pct, (1 - sign*o) * pct
        if NSEvent.modifierFlags() & NSEventModifierFlagOption or drag['near'] < 1e-6:
            # Option: keep the opposite edge where it is and move this one only.
            if drag['near'] < 1e-6:
                near_new = reach / max(drag['far'], 1e-6) * far_share
            else:
                near_new = near_share * reach / drag['near']
            total = near_new + far_share
            if total < 1e-6:
                return
            node.userData[SCALE_KEY] = max(1.0, round(total / 2.0, 1))
            node.userData[OFFSET_KEY] = round(sign * (near_new - far_share) / total * 100.0, 1)
        else:
            node.userData[SCALE_KEY] = max(1.0, round(pct * reach / drag['near'], 1))
        _invalidate(drag['layer'], [drag['path']])
        self._redraw()

    def mouseUp_(self, event):
        if self._drag is not None:
            layer, path = self._drag['layer'], self._drag['path']
            self._drag = None
            layer.endChanges()
            _invalidate(layer, [path])
            self._last_ui_state = None
            self._refresh_ui()
            self._redraw()
            return
        objc.super(VariableStrokeTool, self).mouseUp_(event)
        self._refresh_ui()
        self._redraw()

    @objc.python_method
    def __file__(self):
        return __file__
