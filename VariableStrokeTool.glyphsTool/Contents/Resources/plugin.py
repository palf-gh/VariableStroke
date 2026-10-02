# encoding: utf-8
"""Variable Stroke editing tool for Glyphs 3."""
import functools
import math
import time
import traceback
import objc
from AppKit import (NSBezierPath, NSColor, NSEvent, NSImage, NSMenu, NSMenuItem, NSObject,
                    NSThread, NSPasteboard, NSPasteboardTypePDF, NSData,
                    NSEventModifierFlagOption, NSEventModifierFlagShift,
                    NSEventModifierFlagCommand,
                    NSRoundLineCapStyle, NSRoundLineJoinStyle)
from GlyphsApp import (Glyphs, GSCallbackHandler, GSCustomParameter, GSComponent, OFFCURVE,
                       DOCUMENTOPENED, UPDATEINTERFACE, DRAWBACKGROUND, CONTEXTMENUCALLBACK, WINDOW_MENU)
from GlyphsApp.plugins import SelectTool
from vanilla import (Window, FloatingWindow, Group, SegmentedButton, TextBox, EditText,
                     ImageButton, Button, List, HorizontalLine)
from glyphs_bridge import (CAP_START_KEY, CAP_END_KEY, CAP_START_ANGLE_KEY, CAP_END_ANGLE_KEY,
                           WIDTH_KEY, HEIGHT_SCALE_KEY, CORNER_KEY, CORNER_ON_KEY, CORNER_INNER_KEY,
                           CORNER_TENSION_KEY, CORNER_RATIO_KEY, DEFAULT_CORNER_RADIUS,
                           CORNER_INNER_TENSION_KEY, CORNER_INNER_RATIO_KEY,
                           corner_on, corner_spec, corner_widgets, note_corner,
                           corner_linked, set_corner_linked, corner_side_key,
                           cut_angle, STROKE_WIDTH_KEY, SCALE_KEY, EXPORT_FILTER,
                           enabled, stroke_width, scale, height_scale, migrate_path, interpolate_layer,
                           convert_glyph, layer_state, LAYER_ITALIC_KEY,
                           expand_layer, generated,
                           cleanup_legacy_layer, glyph_enabled, GLYPH_KEY, set_glyph_enabled,
                           normalize_layer, MASTER_WIDTH_KEY, master_default_width,
                           has_width_override, reset_width_overrides, STROKE_HEIGHT_KEY,
                           OFFSET_KEY, ROTATION_KEY, MASTER_HEIGHT_KEY, MASTER_ANGLE_KEY,
                           LAYER_NIB_ANGLE_KEY, master_default_height, master_default_angle,
                           master_defaults,
                           layer_defaults, stroke_height, has_height_override, offset, rotation,
                           edges_for_path, selected_nib_nodes, ellipse_cap_nodes,
                           set_node_nib_size, path_nodes, PROFILE, LAYER_STATE_KEY, has_live_corners, nib_of,
                           scale_of, height_scale_of, offset_of, rotation_of,
                           corner_on_of, corner_spec_of, copied_contours)
from clipboard_export import svg_document, pdf_document
from variable_stroke_core import (unit, sub, add, length, outline_curves,
                                  nib_edges, ellipse_nib_edges)

CAP_NAMES = [('flat', 'Flat', 'フラット'), ('round', 'Round', '丸'),
             ('ellipse', 'Ellipse', '楕円'),
             ('square', 'Square', '四角'), ('horizontal', 'Horizontal cut', '水平カット'),
             ('vertical', 'Vertical cut', '垂直カット'),
             ('angle', 'Cut at a custom angle', '角度カット')]
CAP_VALUES = [item[0] for item in CAP_NAMES]
# Callback names from GlyphsCore/GSCallbackHandler.h: the inspector strip and the
# outline preparation hooks.
INSPECTOR_CALLBACK = 'GSInspectorViewControllersCallback'
# Glyphs.defaults[PROFILE_KEY] = True (Macro panel) prints where the time of each
# drag on the canvas went, after the mouse is released.
PROFILE_KEY = 'com.codex.VariableStroke.profile'
# Diagnostics while profiling: a comma-separated list of 'nohandles' (no handles
# while nodes move) and 'noinspector' (the tool shows no panel at all).
EXPERIMENT_KEY = 'com.codex.VariableStroke.experiment'
# The panel is the tool's own inspector (the SDK's view() hook). Set this to
# 'callback' to deliver it through INSPECTOR_CALLBACK instead, as earlier builds
# did; Glyphs rebuilt that strip during node drags, stalling 100-250 ms each time.
INSPECTOR_MODE_KEY = 'com.codex.VariableStroke.inspectorMode'


def _inspector_via_callback():
    try:
        return str(Glyphs.defaults[INSPECTOR_MODE_KEY] or '') == 'callback'
    except Exception:
        return False
PREPARE_LAYER_CALLBACK = 'GSPrepareLayerCallback'
PANEL_SIZE = (545, 56)
TAB_TOP = 32  # the tab row, below the stroke row
TABS = (('node', 'Node', 'ノード'), ('caps', 'Caps', '線端'), ('corner', 'Corners', '角丸'))
TAB_NAMES = [item[0] for item in TABS]
TAB_DEFAULTS_KEY = 'com.codex.VariableStroke.inspectorTab'


def _timed(name):
    """Count a Python-only method's time in PROFILE (not for Objective-C methods)."""
    def decorate(function):
        @functools.wraps(function)
        def timed(*args, **kwargs):
            with PROFILE.section(name):
                return function(*args, **kwargs)
        return timed
    return decorate


def _loc(english, japanese):
    return Glyphs.localize({'en': english, 'jp': japanese, 'ja': japanese})


# Fonts known to use strokes, by id(font). Filled lazily by one scan per font and
# directly whenever a glyph is switched ON (tool panel or context menu).
_STROKE_FONTS = {}


def _ensure_export_filter(font):
    # PreInterpolationFilter expands each master before interpolation (static and
    # variable exports); Filter stays as a fallback and finds nothing left to do.
    if font is None:
        return
    _STROKE_FONTS[id(font)] = True
    for instance in font.instances:
        for name in ('PreInterpolationFilter', 'Filter'):
            if any(parameter.name == name and str(parameter.value).split(';')[0] == EXPORT_FILTER
                   for parameter in instance.customParameters):
                continue
            instance.customParameters.append(GSCustomParameter(name, EXPORT_FILTER + ';'))


# Copy and cut in the edit view belong to GlyphsPathPlugin, the base of the select
# and drawing tools. Glyphs keeps its own pasteboard type (pasting back into Glyphs
# brings the centerlines and their stroke data); the representations other apps
# read are replaced by the outline when the selection holds a live stroke.
_PATH_TOOL_CLASS = 'GlyphsPathPlugin'
_SVG_TYPE = 'public.svg-image'
_DRAWING_TYPES = {NSPasteboardTypePDF, 'Apple PDF pasteboard type', _SVG_TYPE,
                  'com.adobe.svg', 'com.adobe.illustrator.svg', 'com.adobe.illustrator.svgm',
                  'com.adobe.illustrator.aicb', 'com.adobe.encapsulated-postscript',
                  'public.tiff', 'NeXT TIFF v4.0 pasteboard type', 'public.png',
                  'com.apple.pict', 'Apple PICT pasteboard type'}
_TEXT_TYPE = 'public.utf8-plain-text'
_hooked_copy = []


def _bezier_contours(bezier):
    """[(closed, segments)] of an NSBezierPath (a selected component)."""
    contours, segments, start, current = [], [], None, None
    for index in range(bezier.elementCount()):
        kind, points = bezier.elementAtIndex_associatedPoints_(index)
        points = [(float(point.x), float(point.y)) for point in points]
        if kind == 0:  # move
            if segments:
                contours.append((False, segments))
            segments, start, current = [], points[0], points[0]
        elif kind == 1:  # line
            segments.append(('line', (current, points[0])))
            current = points[0]
        elif kind == 2:  # curve
            segments.append(('cubic', (current, points[0], points[1], points[2])))
            current = points[2]
        elif kind == 3:  # close
            if current != start:
                segments.append(('line', (current, start)))
            if segments:
                contours.append((True, segments))
            segments, current = [], start
    if segments:
        contours.append((False, segments))
    return contours


def _copied_outline(tool):
    """Outline contours of what the tool is about to copy, or None to leave the copy alone."""
    try:
        layer = tool.editViewController().graphicView().activeLayer()
    except Exception:
        font = Glyphs.font
        layer = font.selectedLayers[0] if font and font.selectedLayers else None
    if layer is None:
        return None
    selection = list(layer.selection or [])
    contours = copied_contours(layer, set(selection))
    if contours is None:
        return None
    for item in selection:
        if isinstance(item, GSComponent):
            try:
                contours.extend(_bezier_contours(item.bezierPath))
            except Exception:
                pass
    return contours


def _put_outline_on_pasteboard(contours):
    svg = svg_document(contours)
    pdf = pdf_document(contours)
    if svg is None or pdf is None:
        return
    svg_data = NSData.dataWithBytes_length_(svg.encode('utf-8'), len(svg.encode('utf-8')))
    board = NSPasteboard.generalPasteboard()
    kept = []
    for kind in list(board.types() or []):
        if kind in _DRAWING_TYPES:
            continue
        data = board.dataForType_(kind)
        if data is None:
            continue
        if kind == _TEXT_TYPE:
            text = (board.stringForType_(kind) or '').lstrip()
            if text.startswith(('<svg', '<?xml', '%!')):  # a drawing as text: the outline
                data = svg_data
        kept.append((kind, data))
    board.clearContents()
    board.declareTypes_owner_([kind for kind, _ in kept] + [NSPasteboardTypePDF, _SVG_TYPE],
                              None)
    for kind, data in kept:
        board.setData_forType_(data, kind)
    board.setData_forType_(pdf, NSPasteboardTypePDF)
    board.setData_forType_(svg_data, _SVG_TYPE)


def _hook_copy():
    """Wrap -copy: and -cut: of every path tool, once per launch."""
    if _hooked_copy:
        return
    cls = objc.lookUpClass(_PATH_TOOL_CLASS)
    for name in (b'copy:', b'cut:'):
        original = cls.instanceMethodForSelector_(name)

        def wrapper(self, sender, original=original):
            contours = None
            try:  # before the original runs: cut removes the selection
                contours = _copied_outline(self)
            except Exception:
                print(traceback.format_exc())
            original(self, sender)
            if contours:
                try:
                    _put_outline_on_pasteboard(contours)
                except Exception:
                    print(traceback.format_exc())

        objc.classAddMethod(cls, name, objc.selector(wrapper, selector=name,
                                                     signature=original.signature))
    _hooked_copy.append(True)


def _invalidate(layer, paths=None):
    """Tell Glyphs the layer changed so it prepares its preview outline again."""
    for path in (paths if paths is not None else list(layer.paths)):
        try:
            layer.elementDidChange_(path)
        except Exception:
            pass


def _cap_icon(style):
    """Template icon drawn with the real outline code: a diagonal stroke ending in `style`.

    AppKit runs the drawing handler on every redraw of the inspector (which Glyphs
    does while nodes move), so the outline is computed once, here, not there."""
    start, end = (-4.0, -3.0), (11.0, 8.0)
    nib = (7.0, 13.0, 0.0, 0.0) if style == 'ellipse' else 7.0
    contours = outline_curves([('line', (start, end), nib, nib)], False, 'flat', style,
                              end_angle=160.0)

    def draw(rect):
        with PROFILE.section('draw cap icon'):
            body = NSBezierPath.bezierPath()
            for contour in contours:
                body.moveToPoint_(contour[0][1][0])
                for kind, points in contour:
                    if kind == 'cubic':
                        body.curveToPoint_controlPoint1_controlPoint2_(points[3], points[1],
                                                                       points[2])
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


_LINK_ICONS = {}


def _link_icon(linked):
    """A small chain control, with a slash when its two values are independent."""
    if linked in _LINK_ICONS:
        return _LINK_ICONS[linked]
    symbol = 'link' if linked else 'link.slash'
    if hasattr(NSImage, 'imageWithSystemSymbolName_accessibilityDescription_'):
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, symbol)
        if image is not None:
            _LINK_ICONS[linked] = image
            return image

    def draw(rect):
        NSColor.labelColor().set()
        chain = NSBezierPath.bezierPath()
        chain.setLineWidth_(1.4)
        chain.appendBezierPathWithRoundedRect_xRadius_yRadius_(((2, 5), (9, 6)), 3, 3)
        chain.appendBezierPathWithRoundedRect_xRadius_yRadius_(((7, 5), (9, 6)), 3, 3)
        chain.stroke()
        if not linked:
            NSColor.windowBackgroundColor().set()
            gap = NSBezierPath.bezierPath()
            gap.moveToPoint_((12, 3))
            gap.lineToPoint_((6, 13))
            gap.setLineWidth_(3)
            gap.stroke()
            NSColor.labelColor().set()
            gap.setLineWidth_(1.3)
            gap.stroke()
        return True

    image = NSImage.imageWithSize_flipped_drawingHandler_((18, 16), False, draw)
    image.setTemplate_(True)
    _LINK_ICONS[linked] = image
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
    return layer_state(layer)


def _layer_shape(layer):
    """What decides whether normalize_layer has anything to do: the glyph state
    and each path's identity, node count and stroke flag."""
    try:
        state = glyph_enabled(layer.parent)
        return (objc.pyobjc_id(layer), state,
                tuple((objc.pyobjc_id(path), len(path.nodes), enabled(path))
                      for path in layer.paths))
    except Exception:
        return None


def _normalize_quietly(layer):
    """Sync path flags with the (real, edited) glyph's state without an undo step."""
    try:
        state = glyph_enabled(layer.parent)
    except Exception:
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
_NODE_KEYS = (SCALE_KEY, HEIGHT_SCALE_KEY, OFFSET_KEY, ROTATION_KEY, WIDTH_KEY, CORNER_ON_KEY, CORNER_KEY, CORNER_INNER_KEY,
              CORNER_TENSION_KEY, CORNER_INNER_TENSION_KEY,
              CORNER_RATIO_KEY, CORNER_INNER_RATIO_KEY)
_MISSING = object()
# Node fields of the live corner and the userData key each one writes.
CORNER_FIELDS = {'radius': CORNER_KEY, 'innerRadius': CORNER_INNER_KEY,
                 'tension': CORNER_TENSION_KEY, 'innerTension': CORNER_INNER_TENSION_KEY,
                 'ratio': CORNER_RATIO_KEY, 'innerRatio': CORNER_INNER_RATIO_KEY}
NODE_FIELDS = ('scale', 'heightScale', 'offset', 'rotation') + tuple(CORNER_FIELDS)


def _same(values):
    """True when every value is set and (nearly) equal, so one number can show."""
    return bool(values) and all(v is not None and abs(v-values[0]) < 0.001 for v in values)


def _corner_color(which):
    """Outer corner handles are orange, inner ones green."""
    return (NSColor.colorWithCalibratedRed_green_blue_alpha_(0.95, 0.5, 0.0, 1.0)
            if which == 'outer' else
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.62, 0.38, 1.0))


def _parse_field(name, text):
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    if name == 'offset':
        return max(-100.0, min(100.0, value))
    if name == 'rotation':
        return value % 180.0
    if name in ('startAngle', 'endAngle'):
        return value % 360.0
    if name in ('radius', 'innerRadius'):
        return value if 0 <= value < 10000 else None
    if name in ('tension', 'innerTension'):
        return max(0.0, min(300.0, value))
    if name in ('ratio', 'innerRatio'):
        return value if 1 <= value < 10000 else None
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
    elif name in ('scale', 'heightScale'):
        key = SCALE_KEY if name == 'scale' else HEIGHT_SCALE_KEY
        for node in nodes:
            if value is None:
                if key in node.userData:
                    del node.userData[key]
            else:
                node.userData[key] = value
    elif name == 'offset':
        for node in nodes:
            node.userData[OFFSET_KEY] = value
    elif name == 'rotation':
        for node in nodes:
            node.userData[ROTATION_KEY] = value
    elif name in CORNER_FIELDS:
        for node in nodes:
            node.userData[CORNER_FIELDS[name]] = value
            node.userData[CORNER_ON_KEY] = True  # typing a corner value switches it on
        if nodes and paths:
            note_corner(paths[0])
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
    if name in CORNER_FIELDS:
        return all(corner_on(node) and node.userData.get(CORNER_FIELDS[name]) == value
                   for node in nodes)
    if name in ('scale', 'heightScale', 'offset', 'rotation'):
        key = {'scale': SCALE_KEY, 'heightScale': HEIGHT_SCALE_KEY, 'offset': OFFSET_KEY,
               'rotation': ROTATION_KEY}[name]
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
        start = time.perf_counter() if PROFILE.enabled else None
        name = 'prepare layer: no strokes'
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
            defaults = master_defaults(_master_for(layer))
            try:
                italic = layer.userData.get(LAYER_ITALIC_KEY)
            except Exception:
                italic = None
            if italic is not None:  # interpolated layer: its own blended angle
                defaults.italic_angle = float(italic)
            nib_angle = layer.userData.get(LAYER_NIB_ANGLE_KEY)
            if nib_angle is not None:
                defaults.nib_angle = float(nib_angle)
            if start is not None:
                name = 'prepare layer: %s, %s thread' % (
                    'instance' if layer.userData.get(LAYER_STATE_KEY) is not None else 'master',
                    'main' if NSThread.isMainThread() else 'background')
            expand_layer(layer, state, defaults)
        except Exception:
            print(traceback.format_exc())
        finally:
            if start is not None:
                PROFILE.add(name, time.perf_counter() - start)
        return True, None

    @objc.signature(b'Z@:@@@o^@')
    def interpolateLayer_glyph_interpolation_error_(self, layer, glyph, interpolation, error):
        # Instances in the preview, interpolation previews, virtual masters: carry
        # the ON state, stroke settings and blended values onto the new layer.
        try:
            with PROFILE.section('interpolate layer'):
                interpolate_layer(layer, glyph, interpolation)
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
            glyph.beginUndo()
            try:
                convert_glyph(glyph, keep_marks=False)
                for layer in glyph.layers:
                    _invalidate(layer)
            finally:
                glyph.endUndo()
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
        try:
            if 'noinspector' in str(Glyphs.defaults[EXPERIMENT_KEY] or ''):
                return []
        except Exception:
            pass
        with PROFILE.section('inspector callback'):
            if not self._tool._moving_nodes:  # moving nodes changes no shown value
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
    """Per-font settings window: default stroke dimensions and nib angle per master.

    Paths without their own width follow their master's default, so changing
    a value here restyles every such stroke of that master at once.
    """

    def __init__(self):
        self.font = None
        self.w = FloatingWindow((440, 260), _loc('Variable Stroke Settings', '可変ストローク設定'),
                                minSize=(380, 190))
        self.w.note = TextBox((12, 10, -12, 42), _loc(
            'Paths without their own width use the default of their master. '
            'Empty height = 40 units. Nib angle rotates width and height on the page.',
            'パスごとの線幅を指定していないストロークは、マスターの既定値に従います。'
            '高さが空欄なら40ユニット。角度で幅と高さの軸を回転します。'),
            sizeStyle='small')
        self.w.masters = List((12, 56, -12, -44), [], columnDescriptions=[
            {'title': _loc('Master', 'マスター'), 'key': 'name', 'editable': False},
            {'title': _loc('Width', '既定幅'), 'key': 'width', 'editable': True, 'width': 70},
            {'title': _loc('Height', '既定高さ'), 'key': 'height', 'editable': True, 'width': 70},
            {'title': _loc('Angle', '既定角度'), 'key': 'angle', 'editable': True, 'width': 70}],
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
                             'angle': '%g' % master_default_angle(master),
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
            try:
                angle = float(item['angle']) % 180.0
            except (TypeError, ValueError):
                angle = master_default_angle(master)
            if abs(angle - master_default_angle(master)) > 1e-9:
                master.userData[MASTER_ANGLE_KEY] = angle
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
        self._angle_visual_node = None
        self._angle_visual_angle = 0.0
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
        self._refresh_pending = False
        self._moving_nodes = False  # a plain node drag is in progress
        self._frame_profile, self._frame_last, self._frame_times = False, None, []
        self._normalized = None  # what _normalize_quietly last saw (see _on_update)
        try:
            self._tab = str(Glyphs.defaults[TAB_DEFAULTS_KEY] or 'node')
        except Exception:
            self._tab = 'node'
        if self._tab not in TAB_NAMES:
            self._tab = 'node'
        self._build_inspector()
        self._inspector_provider = VariableStrokeInspectorProvider.alloc().initWithTool_(self)

    @objc.python_method
    def _build_inspector(self):
        # The stroke row (ON/OFF, width, height) stays; below it one tab at a time:
        # node values, caps or corners.
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
        group.tabs = SegmentedButton((width_px - 241, 4, 210, 20),
                                     [{'title': _loc(english, japanese)}
                                      for _, english, japanese in TABS],
                                     callback=self.tabFromInspector_, sizeStyle='small')
        group.settings = ImageButton((width_px - 23, 5, 17, 17), imageNamed='NSActionTemplate',
                                     bordered=False, callback=self.showSettingsFromInspector_)
        group.divider = HorizontalLine((6, 28, -6, 1))

        node = group.nodeTab = Group((0, TAB_TOP, -0, 24))
        node.scaleLabel = TextBox((6, 4, 24, 14), _loc('W', '幅'), sizeStyle='small')
        node.scaleField = SteppingEditText((31, 0, 43, 19), sizeStyle='small')
        node.scaleUnit = TextBox((76, 4, 12, 14), '%', sizeStyle='small')
        node.heightScaleLabel = TextBox((100, 4, 24, 14), _loc('H', '高'), sizeStyle='small')
        node.heightScaleField = SteppingEditText((125, 0, 43, 19), sizeStyle='small')
        node.heightScaleUnit = TextBox((170, 4, 12, 14), '%', sizeStyle='small')
        node.offsetLabel = TextBox((196, 4, 30, 14), _loc('Pos', '位置'), sizeStyle='small')
        node.offsetField = SteppingEditText((227, 0, 43, 19), sizeStyle='small')
        node.offsetUnit = TextBox((272, 4, 12, 14), '%', sizeStyle='small')
        node.rotationLabel = TextBox((296, 4, 33, 14), _loc('Ang', '角度'), sizeStyle='small')
        node.rotationField = SteppingEditText((331, 0, 43, 19), sizeStyle='small')
        node.rotationUnit = TextBox((376, 4, 7, 14), '°', sizeStyle='small')

        caps = [{'imageObject': _cap_icon(value), 'width': 21} for value in CAP_VALUES]
        cap = group.capsTab = Group((0, TAB_TOP, -0, 24))
        cap.startLabel = TextBox((6, 4, 30, 14), _loc('Start', '始点'), sizeStyle='small')
        cap.startCap = SegmentedButton((36, 0, 147, 20), caps,
                                       callback=self.startCapFromInspector_, sizeStyle='small')
        cap.startAngle = SteppingEditText((187, 0, 36, 19), sizeStyle='small')
        cap.startAngleUnit = TextBox((225, 4, 10, 14), '°', sizeStyle='small')
        cap.endLabel = TextBox((254, 4, 30, 14), _loc('End', '終点'), sizeStyle='small')
        cap.endCap = SegmentedButton((284, 0, 147, 20), caps,
                                     callback=self.endCapFromInspector_, sizeStyle='small')
        cap.endAngle = SteppingEditText((435, 0, 36, 19), sizeStyle='small')
        cap.endAngleUnit = TextBox((473, 4, 10, 14), '°', sizeStyle='small')

        corner = group.cornerTab = Group((0, TAB_TOP, -0, 24))
        corner.toggle = SegmentedButton((6, 0, 64, 20), [{'title': 'ON'}, {'title': 'OFF'}],
                                        callback=self.cornerToggleFromInspector_,
                                        sizeStyle='small')
        # Every pair below is (outer, inner), or (left, right) at path ends.
        corner.sides = TextBox((76, 4, 44, 14), '', sizeStyle='small')
        corner.radiusLabel = TextBox((122, 4, 36, 14), _loc('Radius', '半径'), sizeStyle='small')
        corner.outerField = SteppingEditText((158, 0, 36, 19), sizeStyle='small')
        corner.innerField = SteppingEditText((196, 0, 36, 19), sizeStyle='small')
        corner.radiusLink = ImageButton((233, 0, 18, 19), imageObject=_link_icon(True),
                                        bordered=False, callback=self.radiusLinkFromInspector_)
        corner.tensionLabel = TextBox((259, 4, 46, 14), _loc('Strength', '強さ'),
                                      sizeStyle='small')
        corner.tensionField = SteppingEditText((305, 0, 36, 19), sizeStyle='small')
        corner.innerTensionField = SteppingEditText((343, 0, 36, 19), sizeStyle='small')
        corner.tensionLink = ImageButton((380, 0, 18, 19), imageObject=_link_icon(True),
                                         bordered=False, callback=self.tensionLinkFromInspector_)
        corner.ratioLabel = TextBox((406, 4, 38, 14), _loc('Ratio', '縦横比'), sizeStyle='small')
        corner.ratioField = SteppingEditText((444, 0, 36, 19), sizeStyle='small')
        corner.innerRatioField = SteppingEditText((482, 0, 36, 19), sizeStyle='small')
        corner.ratioLink = ImageButton((519, 0, 18, 19), imageObject=_link_icon(True),
                                       bordered=False, callback=self.ratioLinkFromInspector_)

        group.widthReset.getNSButton().setToolTip_(
            _loc('Follow the master default width and height', 'マスターの既定の幅・高さに戻す'))
        group.settings.getNSButton().setToolTip_(
            _loc('Variable Stroke Settings…', '可変ストローク設定…'))
        group.heightField.getNSTextField().setToolTip_(
            _loc('Stroke height of the path (thickness of horizontal strokes). '
                 'Grey: follows the master default.',
                 'パスの高さ（横線の太さ）。'
                 'グレー表示はマスターの既定値に従っています。'))
        group.widthField.getNSTextField().setToolTip_(
            _loc('Stroke width of the path (thickness of vertical strokes). '
                 'Grey: follows the master default.',
                 'パスの幅（縦線の太さ）。グレー表示はマスターの既定値に従っています。'))
        node.scaleField.getNSTextField().setToolTip_(
            _loc('Selected node width %. Empty shares the height %; both empty = 100%.',
                 '選択ノードの幅％。空欄なら高さ％と共通。両方空欄なら100％。'))
        node.heightScaleField.getNSTextField().setToolTip_(
            _loc('Selected node height %. Empty shares the width %.',
                 '選択ノードの高さ％。空欄なら幅％と共通。'))
        node.offsetField.getNSTextField().setToolTip_(
            _loc('Where the centerline sits in the stroke at the selected nodes: 0 centre, '
                 '100 stroke on the left, -100 on the right (of the path direction). '
                 'Option-drag a handle to move one side only.',
                 '選択ノードでの中心線の位置：0 で中央、100 で線幅がパスの進行方向の左側、'
                 '−100 で右側。ハンドルを Option ドラッグすると片側だけ動かせます。'))
        node.rotationField.getNSTextField().setToolTip_(
            _loc('Page angle of the width and height axes. Grey follows the master default. '
                 'Drag the purple ellipse handle on the canvas.',
                 '幅と高さの軸のページ上の角度。グレー表示はマスターの既定値。'
                 'キャンバスの紫ハンドルでも回転できます。'))
        for field in (cap.startAngle, cap.endAngle):
            field.getNSTextField().setToolTip_(_loc(
                'Cut angle on the page (0 horizontal, 90 vertical); typing one selects the angle cut',
                'カットの角度（ページ上、0 で水平、90 で垂直）。入力すると角度カットになります'))
        for control in (cap.startCap, cap.endCap):
            segmented = control.getNSSegmentedButton()
            for index, (_, english, japanese) in enumerate(CAP_NAMES):
                segmented.setToolTip_forSegment_(_loc(english, japanese), index)
        for field, english, japanese in (
                (corner.outerField, 'Radius of the outer corner at the selected nodes (also the '
                 'cap corners at ends). Drag the orange handle on the canvas.',
                 '選択ノードの外側の角丸の半径（端点では線端の角）。キャンバスのオレンジのハンドルでも調整できます'),
                (corner.innerField, 'Radius of the inner corner (defaults to the outer radius). '
                 'Drag the green handle on the canvas.',
                 '内側の角丸の半径（未指定なら外側と同じ）。キャンバスの緑のハンドルでも調整できます'),
                (corner.tensionField, 'Curve strength: 100 = circular arc, lower = tighter, '
                 'higher = squarer', 'カーブの強さ：100 で円弧、小さいほど尖り、大きいほど角張ります'),
                (corner.innerTensionField, 'Inner or right curve strength when unlinked',
                 'リンク解除時の内側または右側の強さ'),
                (corner.ratioField, 'Aspect ratio: 100 = symmetric; above 100 the rounding runs '
                 'further along the side before the node (path direction)',
                 '縦横比：100 で対称。大きいほどパスの進行方向の手前側に長く、小さいほど先側に長く丸めます'),
                (corner.innerRatioField, 'Inner or right aspect ratio when unlinked',
                 'リンク解除時の内側または右側の縦横比')):
            field.getNSTextField().setToolTip_(_loc(english, japanese))
        group.enableStroke.set(1)
        # Numeric fields: live preview while typing, Tab / Shift-Tab between them.
        self._field_delegates = []
        self._shown = {}  # text each field showed after the last refresh
        # Hidden tabs drop out of the Tab key loop by themselves.
        fields = [('width', group.widthField), ('height', group.heightField),
                  ('scale', node.scaleField), ('heightScale', node.heightScaleField),
                  ('offset', node.offsetField), ('rotation', node.rotationField),
                  ('startAngle', cap.startAngle), ('endAngle', cap.endAngle),
                  ('radius', corner.outerField), ('innerRadius', corner.innerField),
                  ('tension', corner.tensionField), ('innerTension', corner.innerTensionField),
                  ('ratio', corner.ratioField), ('innerRatio', corner.innerRatioField)]
        self._fields = dict(fields)
        for name, field in fields:
            delegate = VariableStrokeFieldDelegate.alloc().initWithTool_name_(self, name)
            self._field_delegates.append(delegate)
            text_field = field.getNSTextField()
            text_field.setDelegate_(delegate)
            text_field.setTarget_(delegate)
            text_field.setAction_('stepped:')
        for (_, field), (_, following) in zip(fields, fields[1:] + fields[:1]):
            field.getNSTextField().setNextKeyView_(following.getNSTextField())
        self._show_tab(self._tab)
        view = group.getNSView()
        # Glyphs lays the strip out with Auto Layout. A frame-only view collapses to
        # zero size there, which is why the panel never appeared; pin its size.
        view.removeFromSuperview()
        view.setTranslatesAutoresizingMaskIntoConstraints_(False)
        view.widthAnchor().constraintEqualToConstant_(width_px).setActive_(True)
        view.heightAnchor().constraintEqualToConstant_(height_px).setActive_(True)
        self.infoBoxView = view
        self.inspectorDialogView = None if _inspector_via_callback() else view

    @objc.python_method
    def start(self):
        Glyphs.addCallback(self._on_document_opened, DOCUMENTOPENED)
        Glyphs.addCallback(self._on_update, UPDATEINTERFACE)
        Glyphs.addCallback(self._draw_centerlines, DRAWBACKGROUND)
        GSCallbackHandler.addCallback_forOperation_(self._menu_callback, CONTEXTMENUCALLBACK)
        if _inspector_via_callback():
            GSCallbackHandler.addCallback_forOperation_(self._inspector_provider,
                                                        INSPECTOR_CALLBACK)
        GSCallbackHandler.addCallback_forOperation_(self._layer_processor, PREPARE_LAYER_CALLBACK)
        try:
            _hook_copy()
        except Exception:
            print(traceback.format_exc())
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
        self._sync_export()  # also fills the panel (_refresh_ui)
        self._inspector_provider.performSelector_withObject_afterDelay_('reloadInspector:', None, 0.0)

    @objc.python_method
    def deactivate(self):
        self._is_active = False
        self._moving_nodes = False
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
    def _show_tab(self, name):
        group = self.infoBoxWindow.group
        for tab, view in (('node', group.nodeTab), ('caps', group.capsTab),
                          ('corner', group.cornerTab)):
            view.show(tab == name)
        group.tabs.set(TAB_NAMES.index(name))

    @objc.python_method
    def _select_tab(self, name):
        """Show tab `name`, also when a canvas handle of that kind is grabbed."""
        if name == self._tab:
            return
        self._tab = name
        try:
            Glyphs.defaults[TAB_DEFAULTS_KEY] = name
        except Exception:
            pass
        self._show_tab(name)
        self._last_ui_state = None
        self._refresh_ui()

    def tabFromInspector_(self, sender):
        if 0 <= sender.get() < len(TAB_NAMES):
            self._select_tab(TAB_NAMES[sender.get()])

    @objc.python_method
    def _frame_tick(self):
        """With profiling on, time edit view draws while the mouse button is down,
        whichever tool is active (this callback draws for every tool), and print
        the result once it is released: a baseline to compare tools."""
        if not self._frame_profile:
            return
        now = time.perf_counter()
        if NSEvent.pressedMouseButtons() & 1:
            if self._frame_last is not None and now - self._frame_last > 0.0005:
                self._frame_times.append(now - self._frame_last)
            self._frame_last = now
            return
        self._frame_last = None
        times, self._frame_times = self._frame_times, []
        if len(times) >= 3:
            try:
                tool = Glyphs.font.tool
            except Exception:
                tool = '?'
            times.sort()
            print('Frame timing (%s): %d draws, average %.1f ms, median %.1f ms, slowest %.1f ms'
                  % (tool, len(times), sum(times) / len(times) * 1000,
                     times[len(times) // 2] * 1000, times[-1] * 1000))

    @objc.python_method
    @_timed('update callback')
    def _on_update(self, notification=None):
        try:
            self._frame_profile = bool(Glyphs.defaults[PROFILE_KEY])
        except Exception:
            self._frame_profile = False
        # Runs after every edit with any tool: new paths in an ON glyph become
        # strokes, pasted strokes in an OFF glyph stop being strokes.
        # This tool already invalidates its edited path during a drag. A full
        # layer scan and inspector refresh on every mouse event stalls drawing.
        if getattr(self, '_drag', None) is not None or self._moving_nodes:
            return  # mouseUp_ catches up once
        if not self._normalizing:
            self._normalizing = True
            try:
                layer = Glyphs.font.currentTab.activeLayer() if Glyphs.font and \
                    Glyphs.font.currentTab else None
                if layer is not None:
                    # Dragging nodes sends this for every mouse event, but only new,
                    # pasted, re-flagged or newly drawn-into paths need a look.
                    shape = _layer_shape(layer)
                    if shape is None or shape != self._normalized:
                        _normalize_quietly(layer)
                        self._normalized = _layer_shape(layer)
                if self._settings is not None and self._settings.is_open():
                    self._settings.reload()
            except Exception:
                pass
            finally:
                self._normalizing = False
        # Several updates can arrive for one event; refresh the panel once after it.
        if not self._refresh_pending and self._is_current():
            self._refresh_pending = True
            self.performSelector_withObject_afterDelay_('deferredRefresh:', None, 0.0)

    def deferredRefresh_(self, sender):
        self._refresh_pending = False
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
        # Geometry only: drawn on every redraw, so no widths are computed here.
        nodes = list(source.nodes)
        first = next((i for i, node in enumerate(nodes) if node.type != OFFCURVE), None)
        line = NSBezierPath.bezierPath()
        if first is None:
            return line
        if source.closed:  # walk all the way round, back to the first on-curve node
            ordered = nodes[first:] + nodes[:first] + [nodes[first]]
        else:
            ordered = nodes[first:]
        line.moveToPoint_(ordered[0].position)
        controls = []
        for node in ordered[1:]:
            if node.type == OFFCURVE:
                controls.append(node.position)
            elif len(controls) == 2:
                line.curveToPoint_controlPoint1_controlPoint2_(node.position, controls[0],
                                                               controls[1])
                controls = []
            else:
                line.lineToPoint_(node.position)
                controls = []
        if source.closed:
            line.closePath()
        return line

    @objc.python_method
    @_timed('draw centerlines')
    def _draw_centerlines(self, layer, options=None):
        self._frame_tick()
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
                # Glyphs keeps the path's own Bézier path ready; copy it so the
                # styling below never touches its cache.
                line = source.bezierPath.copy()
            except Exception:
                line = None
            if line is None:
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
    def _font_has_strokes(self, font):
        # Scanning a large (e.g. CJK) font is slow, so it happens once per font; turning
        # a glyph ON marks the font directly (see _set_enabled).
        key = id(font)
        if key not in _STROKE_FONTS:
            _STROKE_FONTS[key] = any(glyph.userData.get(GLYPH_KEY) for glyph in font.glyphs)
        return _STROKE_FONTS[key]

    @objc.python_method
    def _sync_export(self, notification=None):
        # The export filters are custom parameters of each instance; add them when a
        # font uses strokes and its instances changed.
        for font in list(Glyphs.fonts):
            count = len(font.instances)
            if self._instance_counts.get(id(font)) == count or not self._font_has_strokes(font):
                continue
            self._instance_counts[id(font)] = count
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
    def _selected_paths(self, layer, selected=None):
        selected = set(layer.selection) if selected is None else selected
        if not selected:
            return []
        return [path for path in layer.paths if not generated(path) and
                any(node in selected for node in path.nodes)]

    @objc.python_method
    def _target_paths(self, layer, selected=None):
        paths = self._selected_paths(layer, selected)
        if paths:
            return paths
        editable_paths = [path for path in layer.paths if not generated(path)]
        if len(editable_paths) == 1:
            return editable_paths
        return [path for path in editable_paths if enabled(path)]

    @objc.python_method
    def _target_nodes(self, layer, paths, selected=None):
        selected = set(layer.selection) if selected is None else selected
        nodes = [node for path in paths for node in path.nodes
                 if node.type != OFFCURVE and node in selected]
        return nodes if nodes else [node for path in paths for node in path.nodes
                                   if node.type != OFFCURVE]

    @objc.python_method
    def _target_entries(self, paths, selected):
        """[(node, data, endpoint)] for the selected on-curve nodes of `paths` (all
        of them if none is selected), with node_data read once; `endpoint` marks
        the ends of open paths."""
        entries = []
        for path in paths:
            on_curve = [(node, data) for node, _, _, data in path_nodes(path)
                        if data is not None]
            ends = (on_curve[0][0], on_curve[-1][0]) if on_curve and not path.closed else ()
            entries.extend((node, data, any(node is end for end in ends))
                           for node, data in on_curve)
        chosen = [entry for entry in entries if entry[0] in selected]
        return chosen or entries

    @objc.python_method
    @_timed('refresh panel')
    def _refresh_ui(self, notification=None):
        if self._updating_ui or self._live is not None or not hasattr(self, 'infoBoxWindow') \
                or not self._is_current():
            return
        layer = self._layer()
        # One set for all membership tests: layer.selection builds a new list per call.
        selected = set(layer.selection) if layer is not None else set()
        paths = self._target_paths(layer, selected) if layer is not None else []
        active = bool(layer) and glyph_enabled(layer.parent)
        editable = bool(paths) and active
        tab = self._tab
        # Only the stroke row and the visible tab are read and compared.
        if not editable:
            paths = []
        stroke = (layer is not None, active, editable,
                  tuple(stroke_width(path) for path in paths),
                  tuple(stroke_height(path) for path in paths),
                  tuple(has_width_override(path) for path in paths),
                  tuple(has_height_override(path) for path in paths))
        if tab == 'caps':
            open_paths = [path for path in paths if not path.closed]
            details = tuple((path.attributes.get(CAP_START_KEY, 'flat'),
                             path.attributes.get(CAP_END_KEY, 'flat'),
                             cut_angle(path, CAP_START_ANGLE_KEY),
                             cut_angle(path, CAP_END_ANGLE_KEY)) for path in open_paths)
        else:
            entries = self._target_entries(paths, selected) if paths else []
            if tab == 'node':
                default_angle = layer_defaults(layer).nib_angle if layer is not None else 0.0
                details = tuple((
                    # Glyphs may deserialize userData numbers as NSString; the
                    # accessors return floats for comparison and %g formatting.
                    scale_of(data) if SCALE_KEY in data or HEIGHT_SCALE_KEY not in data
                    else None,
                    height_scale_of(data) if HEIGHT_SCALE_KEY in data else None,
                    offset_of(data), rotation_of(data, default_angle), ROTATION_KEY in data)
                    for _, data, _ in entries)
            else:
                details = (tuple((corner_on_of(data),
                                  tuple(sorted((corner_spec_of(data) or {}).items())),
                                  CORNER_INNER_KEY not in data,
                                  CORNER_INNER_TENSION_KEY not in data,
                                  CORNER_INNER_RATIO_KEY not in data)
                                 for _, data, _ in entries),
                           bool(entries) and all(end for _, _, end in entries))
        ui_state = (tab, stroke, details)
        if ui_state == self._last_ui_state:
            return
        self._last_ui_state = ui_state
        group = self.infoBoxWindow.group
        self._updating_ui = True
        try:
            self._show_stroke_row(group, layer, active, editable, stroke)
            if tab == 'node':
                self._show_node_tab(group.nodeTab, editable, details)
            elif tab == 'caps':
                self._show_caps_tab(group.capsTab, bool(details), details)
            else:
                self._show_corner_tab(group.cornerTab, editable, *details)
            self._shown = {name: str(field.getNSTextField().stringValue())
                           for name, field in self._fields.items()}
        finally:
            self._updating_ui = False

    @objc.python_method
    def _show_stroke_row(self, group, layer, active, editable, stroke):
        _, _, _, bases, heights, overridden, height_overridden = stroke
        group.enableStroke.enable(layer is not None)
        group.enableStroke.set(0 if active else 1)
        for field in (group.widthField, group.heightField):
            field.enable(editable)
        group.widthField.set(('%g' % bases[0]) if _same(bases) else '')
        group.heightField.set(('%g' % round(heights[0], 2)) if _same(heights) else '')
        group.widthReset.enable(any(overridden) or any(height_overridden))
        for field, flags in ((group.widthField, overridden),
                             (group.heightField, height_overridden)):
            field.getNSTextField().setTextColor_(
                NSColor.labelColor() if any(flags) else NSColor.secondaryLabelColor())

    @objc.python_method
    def _show_node_tab(self, tab, editable, details):
        scales, height_scales, offsets, rotations, rotation_overridden = \
            [list(column) for column in zip(*details)] or ([], [], [], [], [])
        for field in (tab.scaleField, tab.heightScaleField, tab.offsetField, tab.rotationField):
            field.enable(editable)
        tab.scaleField.set(('%g' % scales[0]) if _same(scales) else '')
        tab.heightScaleField.set(('%g' % height_scales[0]) if _same(height_scales) else '')
        tab.offsetField.set(('%g' % offsets[0]) if _same(offsets) else '')
        tab.rotationField.set(('%g' % rotations[0]) if _same(rotations) else '')
        tab.rotationField.getNSTextField().setTextColor_(
            NSColor.labelColor() if any(rotation_overridden) else NSColor.secondaryLabelColor())

    @objc.python_method
    def _show_caps_tab(self, tab, open_paths, details):
        for index, (control, field) in enumerate(((tab.startCap, tab.startAngle),
                                                  (tab.endCap, tab.endAngle))):
            styles = {item[index] for item in details}
            angles = [item[2 + index] for item in details]
            control.enable(open_paths)
            if len(styles) == 1 and next(iter(styles)) in CAP_VALUES:
                control.set(CAP_VALUES.index(next(iter(styles))))
            else:
                control.getNSSegmentedButton().setSelectedSegment_(-1)  # mixed or none
            field.enable(open_paths)
            field.set(('%g' % angles[0]) if _same(angles) else '')
            field.getNSTextField().setTextColor_(
                NSColor.labelColor() if 'angle' in styles else NSColor.secondaryLabelColor())

    @objc.python_method
    def _show_corner_tab(self, tab, editable, details, cap_mode):
        states = [item[0] for item in details]
        values = [dict(item[1]) for item in details]
        has_nodes = bool(details)
        tab.toggle.enable(editable and has_nodes)
        if states and all(states):
            tab.toggle.set(0)
        elif states and not any(states):
            tab.toggle.set(1)
        else:
            tab.toggle.getNSSegmentedButton().setSelectedSegment_(-1)
        tab.sides.set(_loc('L / R', '左 / 右') if cap_mode else _loc('Out / In', '外 / 内'))
        color = NSColor.labelColor() if any(states) else NSColor.secondaryLabelColor()
        for field, key in ((tab.outerField, 'outer'), (tab.innerField, 'inner'),
                           (tab.tensionField, 'tension'),
                           (tab.innerTensionField, 'inner_tension'),
                           (tab.ratioField, 'ratio'), (tab.innerRatioField, 'inner_ratio')):
            shown = [v[key] for v in values if v]
            if not shown:  # all off: show what switching on would give
                shown = [{'outer': DEFAULT_CORNER_RADIUS, 'inner': DEFAULT_CORNER_RADIUS,
                          'tension': 100.0, 'inner_tension': 100.0,
                          'ratio': 100.0, 'inner_ratio': 100.0}[key]] if has_nodes else []
            field.enable(editable)
            field.set(('%g' % round(shown[0], 2)) if _same(shown) else '')
            field.getNSTextField().setTextColor_(color)
        for index, button, field in ((0, tab.radiusLink, tab.innerField),
                                     (1, tab.tensionLink, tab.innerTensionField),
                                     (2, tab.ratioLink, tab.innerRatioField)):
            linked = has_nodes and all(item[2 + index] for item in details)
            button.enable(editable and has_nodes)
            button.getNSButton().setImage_(_link_icon(linked))
            button.getNSButton().setToolTip_(
                _loc('Linked; click to separate values', '連動中。クリックで独立') if linked else
                _loc('Independent; click to link values', '独立中。クリックで連動'))
            field.enable(editable and not linked)

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
        if name in NODE_FIELDS:
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
        clear_scale = name in ('scale', 'heightScale') and not str(text).strip()
        if not paths or (name in NODE_FIELDS and not nodes):
            return
        if commit and str(text) == self._shown.get(name):
            # Only focused (clicked in, tabbed through, Return) or typed back to the
            # shown value: nothing changes, e.g. an angle field must not switch the
            # cap to an angle cut just because the cursor passed through it.
            if live is not None:
                self._quietly(layer, lambda: _restore(live['snapshot']))
                self._live = None
                _invalidate(layer, paths)
                self._redraw()
            return
        if not commit:
            if live is None:
                live = self._live = {'name': name, 'layer': layer, 'paths': paths,
                                     'snapshot': _snapshot(paths)}

            def preview():
                _restore(live['snapshot'])  # half-typed text shows the original
                if value is not None or clear_scale:
                    _apply_field(name, value, paths, nodes)
            self._quietly(layer, preview)
            _invalidate(layer, paths)
            self._redraw()
            return
        if live is not None:
            self._quietly(layer, lambda: _restore(live['snapshot']))
            self._live = None
        if (value is not None or clear_scale) and not _field_is(name, value, paths, nodes):
            layer.beginChanges()
            try:
                _apply_field(name, value, paths, nodes)
            finally:
                layer.endChanges()
        _invalidate(layer, paths)
        self._last_ui_state = None
        self._refresh_ui()
        self._redraw()

    def cornerToggleFromInspector_(self, sender):
        """Switch the live corner of the selected nodes (all nodes if none) on/off.
        Switching off keeps the values, so switching back restores the shape."""
        if self._updating_ui:
            return
        state = sender.get() == 0
        layer = self._layer()
        paths = self._edit_paths(layer)
        nodes = self._target_nodes(layer, paths) if paths else []
        if not nodes:
            return
        layer.beginChanges()
        try:
            for node in nodes:
                node.userData[CORNER_ON_KEY] = state
                if state and node.userData.get(CORNER_KEY) is None:
                    node.userData[CORNER_KEY] = DEFAULT_CORNER_RADIUS
            if state:
                note_corner(paths[0])
        finally:
            layer.endChanges()
        _invalidate(layer, paths)
        self._last_ui_state = None
        self._refresh_ui()
        self._redraw()

    @objc.python_method
    def _toggle_corner_link(self, kind):
        if self._updating_ui:
            return
        layer = self._layer()
        paths = self._edit_paths(layer)
        nodes = self._target_nodes(layer, paths) if paths else []
        if not nodes:
            return
        linked = not all(corner_linked(node, kind) for node in nodes)
        layer.beginChanges()
        try:
            for node in nodes:
                set_corner_linked(node, kind, linked)
        finally:
            layer.endChanges()
        _invalidate(layer, paths)
        self._last_ui_state = None
        self._refresh_ui()
        self._redraw()

    def radiusLinkFromInspector_(self, sender):
        self._toggle_corner_link('radius')

    def tensionLinkFromInspector_(self, sender):
        self._toggle_corner_link('tension')

    def ratioLinkFromInspector_(self, sender):
        self._toggle_corner_link('ratio')

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
    def _strokes(self, layer):
        """(defaults, [(path, path_nodes)]) of the layer's editable strokes: each
        node is read once for all handles of a redraw or a click."""
        if layer is None or not glyph_enabled(getattr(layer, 'parent', None)):
            return None, []
        return layer_defaults(layer), [(path, path_nodes(path)) for path in layer.paths
                                       if enabled(path) and not generated(path)]

    @objc.python_method
    def _node_tangent(self, items, closed, index):
        """Direction of the centerline through a node, from its own handles/neighbours."""
        count = len(items)
        here = items[index][2]

        def neighbour(step):
            i = index
            for _ in range(count-1):
                i += step
                if not closed and not 0 <= i < count:
                    return None
                other = items[i % count][2]
                if length(sub(other, here)) > 1e-6:
                    return other
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
    def _handles(self, strokes, defaults):
        """Width handles where the outline really passes each centerline node:
        across the stroke on smooth nodes, on the miter / inner corner at corners."""
        for path, items in strokes:
            ellipse_ends = set(ellipse_cap_nodes(path, items))
            on_curve = {node: point for node, kind, point, _ in items if kind != OFFCURVE}
            if ellipse_ends and len(ellipse_ends) == len(on_curve):
                continue
            try:
                edges = edges_for_path(path, defaults, items)
            except ValueError:
                continue
            for node, pair in edges:
                if node in ellipse_ends:
                    continue
                yield path, node, on_curve[node], pair

    @objc.python_method
    def _corner_handles(self, strokes, defaults):
        """[(path, widget)] for every rounded outline corner of nodes whose live
        corner is on (widget: see corner_widgets)."""
        result = []
        for path, items in strokes:
            if has_live_corners(items):
                result.extend((path, widget) for widget in corner_widgets(path, defaults, items))
        return result

    @objc.python_method
    def _corner_parts(self, strokes, defaults, selected):
        """Draggable corner handles: (kind, path, widget, position). The radius handle
        (arc midpoint) is always shown; the ratio handles (arc ends) and tension
        handle (on the corner diagonal) only for selected nodes."""
        parts = []
        for path, widget in self._corner_handles(strokes, defaults):
            parts.append(('radius', path, widget, widget['middle']))
            if widget['node'] not in selected:
                continue
            parts.append(('ratio1', path, widget, widget['p1']))
            parts.append(('ratio2', path, widget, widget['p2']))
            chord_middle = ((widget['p1'][0] + widget['p2'][0]) / 2,
                            (widget['p1'][1] + widget['p2'][1]) / 2)
            diagonal = unit(sub(widget['corner'], chord_middle))
            reach = 24 / self._scale()
            position = add(widget['middle'], (diagonal[0] * reach,
                                               diagonal[1] * reach))
            parts.append(('tension', path, dict(widget, tension_origin=position), position))
        return parts

    @objc.python_method
    def _selected_nibs(self, strokes, defaults, selected):
        """Selected nibs; only ellipse caps receive the full preview."""
        if not selected:
            return
        for path, items in strokes:
            owners = set(selected_nib_nodes(path, selected, items))
            if not owners:
                continue
            ellipse_ends = set(ellipse_cap_nodes(path, items))
            base_w = stroke_width(path, defaults)
            base_h = stroke_height(path, defaults)
            for index, (node, _, point, data) in enumerate(items):
                if node not in owners:
                    continue
                nib = nib_of(data, base_w, base_h, defaults.nib_angle)
                tangent = self._node_tangent(items, path.closed, index)
                ellipse_end = node in ellipse_ends
                left, right = (ellipse_nib_edges if ellipse_end else nib_edges)(
                    point, tangent, nib)
                middle = ((left[0] + right[0]) / 2.0,
                          (left[1] + right[1]) / 2.0)
                yield path, node, middle, nib, ellipse_end

    @objc.python_method
    def _angle_handles(self, previews):
        """Angle knobs for every selected stroke node."""
        reach = 18.0 / self._scale()
        for path, node, middle, nib, ellipse_end in previews:
            degrees = nib[3]
            if node == getattr(self, '_angle_visual_node', None) and abs(
                    (self._angle_visual_angle - degrees + 90.0) % 180.0 - 90.0) < 0.11:
                degrees = self._angle_visual_angle
            angle = math.radians(degrees)
            direction = (math.cos(angle), math.sin(angle))
            tip = add(middle, (direction[0] * nib[0]/2.0,
                               direction[1] * nib[0]/2.0))
            knob = add(tip, (direction[0] * reach, direction[1] * reach))
            yield path, node, middle, tip, knob, ellipse_end

    @objc.python_method
    def _nib_size_handles(self, previews):
        """Width and height knobs on the selected node's nib ellipse."""
        for path, node, middle, nib, _ in previews:
            angle = math.radians(nib[3])
            u = (math.cos(angle), math.sin(angle))
            v = (-u[1], u[0])
            for axis, direction, diameter in (('width', u, nib[0]),
                                               ('height', v, nib[1])):
                tip = add(middle, (direction[0]*diameter/2.0,
                                   direction[1]*diameter/2.0))
                yield axis, path, node, middle, direction, tip

    @objc.python_method
    @_timed('draw handles (foreground)')
    def foreground(self, layer):
        if layer is None:
            return
        if PROFILE.enabled and getattr(self, '_drag_handled', None) is not None:
            PROFILE.add('gap: event handled -> handles drawn', time.perf_counter() -
                        self._drag_handled)
            self._drag_handled = None
        try:
            self._draw_handles(layer)
        finally:
            if PROFILE.enabled:
                self._handles_drawn = time.perf_counter()

    @objc.python_method
    def _draw_handles(self, layer):
        scale = self._scale()
        radius = 4.0 / scale
        small = 3.0 / scale
        if self._moving_nodes and 'nohandles' in getattr(self, '_experiment', ()):
            return
        defaults, strokes = self._strokes(layer)
        if not strokes:
            return
        selected = set(layer.selection)
        handles = list(self._handles(strokes, defaults))
        selected_nibs = list(self._selected_nibs(strokes, defaults, selected))
        previews = [item for item in selected_nibs if item[4]]
        for kind, _, widget, position in self._corner_parts(strokes, defaults, selected):
            color = _corner_color(widget['which'])
            line = NSBezierPath.bezierPath()
            if kind == 'radius':
                line.moveToPoint_(widget['corner'])
                line.lineToPoint_(position)
            elif kind == 'tension':
                line.moveToPoint_(widget['middle'])
                line.lineToPoint_(position)
            line.setLineWidth_(1.0 / scale)
            color.colorWithAlphaComponent_(0.6).set()
            line.stroke()
            color.set()
            x, y = position
            if kind == 'radius':
                ring = NSBezierPath.bezierPathWithOvalInRect_(((x-radius, y-radius), (radius*2, radius*2)))
                ring.setLineWidth_(1.5 / scale)
                ring.stroke()
                NSBezierPath.bezierPathWithOvalInRect_(
                    ((x-radius/2.5, y-radius/2.5), (radius/1.25, radius/1.25))).fill()
            elif kind.startswith('ratio'):  # diamond
                diamond = NSBezierPath.bezierPath()
                diamond.moveToPoint_((x, y-small*1.3))
                diamond.lineToPoint_((x+small*1.3, y))
                diamond.lineToPoint_((x, y+small*1.3))
                diamond.lineToPoint_((x-small*1.3, y))
                diamond.closePath()
                diamond.fill()
            else:  # square
                NSBezierPath.bezierPathWithRect_(((x-small, y-small), (small*2, small*2))).fill()
        color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.02, 0.36, 0.81, 1.0)
        for _, _, center, (left, right) in handles:
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
        angle_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.25, 0.85, 1.0)
        for _, _, middle, nib, _ in previews:
            angle = math.radians(nib[3])
            u = (math.cos(angle), math.sin(angle))
            v = (-u[1], u[0])
            rx, ry = nib[0]/2.0, nib[1]/2.0
            k = 0.5522847498307936
            ellipse = NSBezierPath.bezierPath()
            ellipse.moveToPoint_(add(middle, (u[0]*rx, u[1]*rx)))
            for i in range(4):
                a = math.pi*i/2.0
                b = a + math.pi/2.0
                p0 = add(middle, (u[0]*rx*math.cos(a)+v[0]*ry*math.sin(a),
                                  u[1]*rx*math.cos(a)+v[1]*ry*math.sin(a)))
                p3 = add(middle, (u[0]*rx*math.cos(b)+v[0]*ry*math.sin(b),
                                  u[1]*rx*math.cos(b)+v[1]*ry*math.sin(b)))
                d0 = (-u[0]*rx*math.sin(a)+v[0]*ry*math.cos(a),
                      -u[1]*rx*math.sin(a)+v[1]*ry*math.cos(a))
                d3 = (-u[0]*rx*math.sin(b)+v[0]*ry*math.cos(b),
                      -u[1]*rx*math.sin(b)+v[1]*ry*math.cos(b))
                ellipse.curveToPoint_controlPoint1_controlPoint2_(
                    p3, add(p0, (d0[0]*k, d0[1]*k)),
                    sub(p3, (d3[0]*k, d3[1]*k)))
            ellipse.closePath()
            angle_color.colorWithAlphaComponent_(0.08).set()
            ellipse.fill()
            angle_color.colorWithAlphaComponent_(0.55).set()
            ellipse.setLineWidth_(1.0 / scale)
            ellipse.stroke()
        for axis, _, _, middle, _, tip in self._nib_size_handles(previews):
            color = (NSColor.colorWithCalibratedRed_green_blue_alpha_(0.05, 0.48, 0.82, 1.0)
                     if axis == 'width' else
                     NSColor.colorWithCalibratedRed_green_blue_alpha_(0.1, 0.58, 0.34, 1.0))
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(middle)
            line.lineToPoint_(tip)
            line.setLineWidth_(1.0 / scale)
            color.colorWithAlphaComponent_(0.5).set()
            line.stroke()
            color.set()
            x, y = tip
            if axis == 'width':
                NSBezierPath.bezierPathWithRect_(
                    ((x-small, y-small), (small*2, small*2))).fill()
            else:
                diamond = NSBezierPath.bezierPath()
                diamond.moveToPoint_((x, y-small*1.4))
                diamond.lineToPoint_((x+small*1.4, y))
                diamond.lineToPoint_((x, y+small*1.4))
                diamond.lineToPoint_((x-small*1.4, y))
                diamond.closePath()
                diamond.fill()
        for _, _, middle, tip, knob, ellipse_end in self._angle_handles(selected_nibs):
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(tip if ellipse_end else middle)
            line.lineToPoint_(knob)
            line.setLineWidth_(1.0 / scale)
            angle_color.colorWithAlphaComponent_(0.65).set()
            line.stroke()
            angle_color.set()
            x, y = knob
            NSBezierPath.bezierPathWithOvalInRect_(
                ((x-small, y-small), (small*2, small*2))).fill()

    @objc.python_method
    def _select_node(self, layer, node):
        """Grabbing a handle selects its node (Shift adds it), so the panel shows it."""
        if not NSEvent.modifierFlags() & NSEventModifierFlagShift:
            try:
                layer.clearSelection()
            except Exception:
                layer.selection = []
        node.selected = True

    def mouseDown_(self, event):
        self._moving_nodes = False  # in case a drag ended outside this tool
        try:
            PROFILE.enabled = bool(Glyphs.defaults[PROFILE_KEY])
        except Exception:
            PROFILE.enabled = False
        self._experiment = ()
        if PROFILE.enabled:
            PROFILE.reset()
            self._last_drag_event = None
            self._drag_handled = self._handles_drawn = None
            try:
                self._experiment = tuple(part.strip() for part in
                                         str(Glyphs.defaults[EXPERIMENT_KEY] or '').split(','))
            except Exception:
                pass
        layer = self._layer()
        if layer is not None:
            loc = self.editViewController().graphicView().getActiveLocation_(event)
            point = (loc.x, loc.y)
            threshold = 8.0 / self._scale()
            defaults, strokes = self._strokes(layer)
            selected = set(layer.selection)
            handles = list(self._handles(strokes, defaults))
            selected_nibs = list(self._selected_nibs(strokes, defaults, selected))
            previews = [item for item in selected_nibs if item[4]]
            # Small handles first: they can sit right next to the radius handle.
            parts = sorted(self._corner_parts(strokes, defaults, selected),
                           key=lambda part: part[0] == 'radius')
            for kind, path, widget, position in parts:
                if length(sub(point, position)) > threshold:
                    continue
                node = widget['node']
                self._select_node(layer, node)
                layer.beginChanges()
                spec = corner_spec(node) or {}
                self._drag = {'kind': 'corner-' + kind, 'layer': layer, 'path': path,
                              'node': node, 'widget': widget,
                              'radius': spec.get(widget['which'], 0.0),
                              'tension': spec.get('inner_tension' if widget['which'] == 'inner'
                                                  else 'tension', 100.0)}
                self._select_tab('corner')  # show the values being dragged
                self._last_ui_state = None
                self._refresh_ui()
                return
            for path, node, origin, _, knob, _ in self._angle_handles(selected_nibs):
                if length(sub(point, knob)) > threshold:
                    continue
                self._select_node(layer, node)
                layer.beginChanges()
                self._angle_visual_node = node
                self._angle_visual_angle = math.degrees(math.atan2(
                    knob[1]-origin[1], knob[0]-origin[0]))
                self._drag = {'kind': 'rotation', 'layer': layer, 'path': path,
                              'node': node, 'center': origin}
                self._select_tab('node')
                self._last_ui_state = None
                self._refresh_ui()
                return
            for axis, path, node, origin, direction, knob in self._nib_size_handles(previews):
                if length(sub(point, knob)) > threshold:
                    continue
                self._select_node(layer, node)
                layer.beginChanges()
                migrate_path(path)
                self._drag = {'kind': 'nib-size', 'axis': axis, 'layer': layer,
                              'path': path, 'node': node, 'center': origin,
                              'direction': direction,
                              'base_width': stroke_width(path, defaults),
                              'base_height': stroke_height(path, defaults)}
                self._select_tab('node')
                self._last_ui_state = None
                self._refresh_ui()
                return
            for path, node, center, (left, right) in handles:
                for sign, handle, other in ((1, left, right), (-1, right, left)):
                    if length(sub(point, handle)) > threshold:
                        continue
                    self._select_node(layer, node)
                    layer.beginChanges()
                    migrate_path(path)
                    near, far = length(sub(handle, center)), length(sub(other, center))
                    direction = unit(sub(handle, center)) if near > 1e-6 \
                        else unit(sub(center, other))
                    scale_key = SCALE_KEY if abs(direction[0]) >= abs(direction[1]) \
                        else HEIGHT_SCALE_KEY
                    self._drag = {'layer': layer, 'path': path, 'node': node, 'sign': sign,
                                  'center': center, 'direction': direction,
                                  'near': near, 'far': far,
                                  'offset': offset(node) / 100.0,
                                  'scale': scale(node) if scale_key == SCALE_KEY else height_scale(node),
                                  'scale_key': scale_key,
                                  'rotation': rotation(node, defaults.nib_angle)}
                    self._select_tab('node')
                    self._last_ui_state = None
                    self._refresh_ui()
                    return
        objc.super(VariableStrokeTool, self).mouseDown_(event)

    @objc.python_method
    def _drag_corner(self, drag, mouse):
        widget, node = drag['widget'], drag['node']
        kind = drag['kind']
        corner, turn = widget['corner'], widget['turn']
        radius_key = corner_side_key(node, 'radius', widget['which'])

        def along(origin, direction):
            delta = sub(mouse, origin)
            return delta[0]*direction[0] + delta[1]*direction[1]

        if kind == 'corner-radius':
            # The arc scales with the radius, so the handle distance does too.
            reach = length(sub(widget['middle'], corner))
            if reach > 1e-3:
                direction = unit(sub(widget['middle'], corner))
            else:  # zero radius: outer arcs grow towards the node, inner ones away
                towards = unit(sub((node.position.x, node.position.y), corner))
                direction = towards if widget['which'] == 'outer' else (-towards[0], -towards[1])
            distance = max(0.0, along(corner, direction))
            if reach > 1e-3 and drag['radius'] > 1e-3:
                node.userData[radius_key] = round(drag['radius'] * distance / reach, 1)
            else:
                node.userData[radius_key] = round(distance * 2.0, 1)
        elif kind in ('corner-ratio1', 'corner-ratio2'):
            # Slide one end of the arc along its edge; the other end stays. The two
            # trims give the radius (their geometric mean) and the aspect ratio.
            if turn < 1e-3:
                return
            first, second = widget['first'], widget['second']
            if kind == 'corner-ratio1':
                first = max(0.5, along(corner, (-widget['in'][0], -widget['in'][1])))
            else:
                second = max(0.5, along(corner, widget['out']))
            before, after = (second, first) if widget['flip'] else (first, second)
            reach = math.sqrt(before * after)
            node.userData[radius_key] = round(reach / math.tan(turn / 2.0), 1)
            ratio_key = corner_side_key(node, 'ratio', widget['which'])
            node.userData[ratio_key] = round(max(1.0, 100.0 * before / after), 1)
        else:
            origin = widget['tension_origin']
            slope = widget['middle_slope']
            travel_squared = slope[0]*slope[0] + slope[1]*slope[1]
            if travel_squared < 1e-8:
                return
            movement = sub(mouse, origin)
            tension = max(0.0, min(300.0, drag['tension'] +
                                   (movement[0]*slope[0] + movement[1]*slope[1]) /
                                   travel_squared))
            tension_key = corner_side_key(node, 'tension', widget['which'])
            node.userData[tension_key] = round(tension, 1)
        node.userData[CORNER_ON_KEY] = True

    def mouseDragged_(self, event):
        if PROFILE.enabled:
            now = time.perf_counter()
            if getattr(self, '_last_drag_event', None) is not None:
                PROFILE.add('time between drag events', now - self._last_drag_event)
                if self._handles_drawn is not None:
                    PROFILE.add('gap: handles drawn -> next event', now - self._handles_drawn)
                else:
                    PROFILE.add('gap: event without handle drawing', now - self._last_drag_event)
            self._last_drag_event = now
            self._handles_drawn = None
        if self._drag is None:
            # Moving nodes changes nothing the panel shows; skip its updates.
            self._moving_nodes = True
            with PROFILE.section('Glyphs moves the nodes (super)'):
                objc.super(VariableStrokeTool, self).mouseDragged_(event)
            # No full view redraw here: Glyphs redraws what the move changes, and
            # repainting the whole edit view cost ~17 ms per event.
            if PROFILE.enabled:
                self._drag_handled = time.perf_counter()
            return
        drag = self._drag
        loc = self.editViewController().graphicView().getActiveLocation_(event)
        if drag.get('kind', '').startswith('corner-'):
            self._drag_corner(drag, (loc.x, loc.y))
            _invalidate(drag['layer'], [drag['path']])
            self._redraw()
            return
        if drag.get('kind') == 'rotation':
            delta = sub((loc.x, loc.y), drag['center'])
            if length(delta) > 1e-6:
                angle = math.degrees(math.atan2(delta[1], delta[0]))
                self._angle_visual_node = drag['node']
                self._angle_visual_angle = angle
                drag['node'].userData[ROTATION_KEY] = round(angle % 180.0, 1)
                _invalidate(drag['layer'], [drag['path']])
                self._redraw()
            return
        if drag.get('kind') == 'nib-size':
            delta = sub((loc.x, loc.y), drag['center'])
            direction = drag['direction']
            size = max(1.0, 2.0*(delta[0]*direction[0] + delta[1]*direction[1]))
            set_node_nib_size(drag['node'], drag['axis'], size,
                              drag['base_width'], drag['base_height'])
            _invalidate(drag['layer'], [drag['path']])
            self._redraw()
            return
        delta = sub((loc.x, loc.y), drag['center'])
        if NSEvent.modifierFlags() & NSEventModifierFlagCommand:
            direction = drag['direction']
            if length(delta) > 1e-6:
                change = math.degrees(math.atan2(
                    direction[0]*delta[1]-direction[1]*delta[0],
                    direction[0]*delta[0]+direction[1]*delta[1]))
                angle = drag['rotation'] + change
                self._angle_visual_node = drag['node']
                self._angle_visual_angle = angle
                drag['node'].userData[ROTATION_KEY] = round(angle % 180.0, 1)
                _invalidate(drag['layer'], [drag['path']])
                self._redraw()
            return
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
            node.userData[drag['scale_key']] = max(1.0, round(total / 2.0, 1))
            node.userData[OFFSET_KEY] = round(sign * (near_new - far_share) / total * 100.0, 1)
        else:
            node.userData[drag['scale_key']] = max(1.0, round(pct * reach / drag['near'], 1))
        _invalidate(drag['layer'], [drag['path']])
        self._redraw()

    def mouseUp_(self, event):
        try:
            self._mouse_up(event)
        finally:
            if PROFILE.enabled:
                print(PROFILE.report())
                PROFILE.enabled = False

    @objc.python_method
    def _mouse_up(self, event):
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
        if self._moving_nodes:
            self._moving_nodes = False
            self._on_update()
        self._refresh_ui()
        self._redraw()

    @objc.python_method
    def __file__(self):
        return __file__
