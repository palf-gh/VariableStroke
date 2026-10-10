# encoding: utf-8
"""Width profile editor: a resizable window with the profile curve, its points'
values and the font's profile library (see width_profile)."""
import copy
import math
import uuid
import objc
from AppKit import (NSView, NSObject, NSColor, NSBezierPath, NSEvent, NSImage, NSFont,
                    NSGraphicsContext,
                    NSAttributedString, NSFontAttributeName, NSForegroundColorAttributeName,
                    NSEventModifierFlagOption, NSEventModifierFlagShift)
from GlyphsApp import Glyphs
from vanilla import FloatingWindow, Group, PopUpButton, Button, EditText, TextBox
import width_profile
from glyphs_bridge import (profiles, set_profiles, preview_profile, forget_profiles,
                           profile_users, profile_id, forget_interpolations)


def _loc(english, japanese):
    return Glyphs.localize({'en': english, 'jp': japanese, 'ja': japanese})


class _SteppingEditText(EditText):
    # Glyphs' own numeric field: arrow keys step by 1, with Shift by 10.
    try:
        nsTextFieldClass = objc.lookUpClass('GSSteppingTextField')
    except objc.nosuchclass_error:
        pass


def profile_icon(profile, size=(44, 14)):
    """A stroke silhouette of `profile`: its thickness along the length."""
    width, height = size
    line = width_profile.polyline(profile, 32)
    top = max(100.0, width_profile.y_range(profile)[1])
    half = (height - 2) / 2.0
    middle = height / 2.0
    upper = [(1 + x / 100.0 * (width - 2), middle + y / top * half) for x, y in line]
    lower = [(px, 2 * middle - py) for px, py in reversed(upper)]

    def draw(rect):
        body = NSBezierPath.bezierPath()
        body.moveToPoint_(upper[0])
        for point in upper[1:] + lower:
            body.lineToPoint_(point)
        body.closePath()
        NSColor.blackColor().set()
        body.fill()
        return True
    image = NSImage.imageWithSize_flipped_drawingHandler_(size, False, draw)
    image.setTemplate_(True)
    return image


_ICONS = {}


def cached_icon(profile):
    key = tuple(tuple(point) for point in profile['points'])
    icon = _ICONS.get(key)
    if icon is None:
        if len(_ICONS) > 512:
            _ICONS.clear()
        icon = _ICONS[key] = profile_icon(profile)
    return icon


def sorted_profiles(library):
    """[(id, profile)] by name."""
    return sorted(library.items(), key=lambda item: (item[1]['name'].lower(), item[0]))


def fill_popup(popup, entries, selected, none_title=None):
    """Fill a vanilla PopUpButton with [(id, profile)] and preview icons; `selected`
    is an id, None (the none entry) or '' (no entry selected: mixed values)."""
    button = popup.getNSPopUpButton()
    button.removeAllItems()
    ids = []
    if none_title is not None:
        button.addItemWithTitle_(none_title)
        ids.append(None)
    for pid, profile in entries:
        title = profile['name'] or '?'
        while button.itemWithTitle_(title) is not None:
            title += ' '  # NSPopUpButton merges equal titles
        button.addItemWithTitle_(title)
        button.lastItem().setImage_(cached_icon(profile))
        ids.append(pid)
    if selected in ids:
        button.selectItemAtIndex_(ids.index(selected))
    else:
        button.selectItemAtIndex_(-1)
    return ids


# Canvas ---------------------------------------------------------------------

MARGIN_LEFT, MARGIN_RIGHT, MARGIN_BOTTOM, MARGIN_TOP = 42.0, 14.0, 24.0, 12.0
HIT = 7.0


class VariableStrokeProfileCanvas(NSView):
    """Draws and edits the current profile of its editor."""

    def initWithFrame_(self, frame):
        self = objc.super(VariableStrokeProfileCanvas, self).initWithFrame_(frame)
        if self is None:
            return None
        self.editor = None
        self.drag = None
        self.y0, self.y1 = 0.0, 150.0
        return self

    def isFlipped(self):
        return False

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    # mapping
    @objc.python_method
    def _plot(self):
        (x, y), (w, h) = self.bounds()
        return (x + MARGIN_LEFT, y + MARGIN_BOTTOM,
                max(10.0, w - MARGIN_LEFT - MARGIN_RIGHT), max(10.0, h - MARGIN_BOTTOM - MARGIN_TOP))

    @objc.python_method
    def to_view(self, point):
        left, bottom, width, height = self._plot()
        return (left + point[0] / 100.0 * width,
                bottom + (point[1] - self.y0) / (self.y1 - self.y0) * height)

    @objc.python_method
    def to_data(self, point):
        left, bottom, width, height = self._plot()
        return ((point[0] - left) / width * 100.0,
                self.y0 + (point[1] - bottom) / height * (self.y1 - self.y0))

    @objc.python_method
    def fit(self):
        profile = self.editor.profile() if self.editor is not None else None
        top = width_profile.y_range(profile)[1] if profile is not None else 100.0
        self.y0, self.y1 = 0.0, max(120.0, top * 1.15)
        self.setNeedsDisplay_(True)

    @objc.python_method
    def zoom(self, factor, anchor_view_y):
        anchor = self.to_data((0.0, anchor_view_y))[1]
        y0 = anchor - (anchor - self.y0) * factor
        y1 = anchor + (self.y1 - anchor) * factor
        if 5.0 <= y1 - y0 <= 5000.0:
            self._show(y0, y1)

    @objc.python_method
    def _show(self, y0, y1):
        """Show y0..y1, moved up so nothing below 0 % (no thickness) shows."""
        if y0 < 0.0:
            y0, y1 = 0.0, y1 - y0
        self.y0, self.y1 = y0, y1
        self.setNeedsDisplay_(True)

    # drawing
    @objc.python_method
    def _text(self, text, point, align=0.0, color=None):
        attributes = {NSFontAttributeName: NSFont.systemFontOfSize_(10.0),
                      NSForegroundColorAttributeName: color or NSColor.secondaryLabelColor()}
        string = NSAttributedString.alloc().initWithString_attributes_(text, attributes)
        size = string.size()
        string.drawAtPoint_((point[0] - size.width * align, point[1] - size.height / 2.0))

    def drawRect_(self, rect):
        NSColor.textBackgroundColor().set()
        NSBezierPath.fillRect_(self.bounds())
        left, bottom, width, height = self._plot()
        span = self.y1 - self.y0
        step = next((value for value in (1, 2, 5, 10, 25, 50, 100, 200, 500, 1000)
                     if value / span * height >= 28), 1000)
        value = math.ceil(self.y0 / step) * step
        grid = NSBezierPath.bezierPath()
        while value <= self.y1:
            y = self.to_view((0, value))[1]
            grid.moveToPoint_((left, y))
            grid.lineToPoint_((left + width, y))
            self._text('%g%%' % value, (left - 4, y), 1.0)
            value += step
        for x in (25, 50, 75):
            px = self.to_view((x, 0))[0]
            grid.moveToPoint_((px, bottom))
            grid.lineToPoint_((px, bottom + height))
        for x in (0, 25, 50, 75, 100):
            self._text('%d%%' % x, (self.to_view((x, 0))[0], bottom - 12), 0.5)
        NSColor.separatorColor().set()
        grid.setLineWidth_(1.0)
        grid.stroke()
        frame = NSBezierPath.bezierPathWithRect_(((left, bottom), (width, height)))
        NSColor.labelColor().colorWithAlphaComponent_(0.6).set()
        frame.stroke()
        if self.y0 <= 100.0 <= self.y1:
            y = self.to_view((0, 100))[1]
            hundred = NSBezierPath.bezierPath()
            hundred.moveToPoint_((left, y))
            hundred.lineToPoint_((left + width, y))
            hundred.setLineWidth_(1.0)
            NSColor.labelColor().set()
            hundred.stroke()
        profile = self.editor.profile() if self.editor is not None else None
        if profile is None:
            self._text(_loc('No profile. Press “New”.', 'プロファイルがありません。「新規」を押してください。'),
                       (left + width / 2.0, bottom + height / 2.0), 0.5)
            return
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath.clipRect_(((left, bottom), (width, height)))
        curve = NSBezierPath.bezierPath()
        pieces = width_profile.pieces(profile)
        curve.moveToPoint_(self.to_view(pieces[0][0]))
        for p0, p1, p2, p3 in pieces:
            curve.curveToPoint_controlPoint1_controlPoint2_(
                self.to_view(p3), self.to_view(p1), self.to_view(p2))
        curve.setLineWidth_(2.0)
        NSColor.systemBlueColor().set()
        curve.stroke()
        NSGraphicsContext.restoreGraphicsState()
        selected = self.editor.selected
        points = profile['points']
        lines = NSBezierPath.bezierPath()
        for index, point in enumerate(points):
            anchor = self.to_view(point[:2])
            for handle in self._handles(points, index):
                lines.moveToPoint_(anchor)
                lines.lineToPoint_(self.to_view(handle[1]))
        NSColor.secondaryLabelColor().set()
        lines.setLineWidth_(1.0)
        lines.stroke()
        for index, point in enumerate(points):
            for _, handle in self._handles(points, index):
                x, y = self.to_view(handle)
                diamond = NSBezierPath.bezierPath()
                diamond.moveToPoint_((x, y + 4))
                diamond.lineToPoint_((x + 4, y))
                diamond.lineToPoint_((x, y - 4))
                diamond.lineToPoint_((x - 4, y))
                diamond.closePath()
                NSColor.textBackgroundColor().set()
                diamond.fill()
                NSColor.systemBlueColor().set()
                diamond.stroke()
        for index, point in enumerate(points):
            x, y = self.to_view(point[:2])
            dot = NSBezierPath.bezierPathWithOvalInRect_(((x - 4.5, y - 4.5), (9, 9)))
            (NSColor.systemBlueColor() if index == selected else NSColor.textBackgroundColor()).set()
            dot.fill()
            NSColor.systemBlueColor().set()
            dot.setLineWidth_(1.5)
            dot.stroke()
        if selected is not None and selected < len(points):
            point = points[selected]
            x, y = self.to_view(point[:2])
            self._text('%g%%, %g%%' % (round(point[0], 1), round(point[1], 1)),
                       (x + 8, y + 12), 0.0, NSColor.labelColor())

    @objc.python_method
    def _handles(self, points, index):
        """[(kind, (x, y))] of a point's handles that stick out of it."""
        point = points[index]
        result = []
        if index > 0 and (point[2], point[3]) != (point[0], point[1]):
            result.append(('in', (point[2], point[3])))
        if index < len(points) - 1 and (point[4], point[5]) != (point[0], point[1]):
            result.append(('out', (point[4], point[5])))
        return result

    # editing
    @objc.python_method
    def _hit(self, location):
        profile = self.editor.profile()
        if profile is None:
            return None
        points = profile['points']
        order = list(range(len(points)))
        if self.editor.selected is not None:
            order.remove(self.editor.selected)
            order.insert(0, self.editor.selected)
        candidates = []
        for index in order:  # handles of the selected point first, then points
            for kind, handle in self._handles(points, index):
                candidates.append((kind, index, handle))
        candidates = [item for item in candidates if item[1] == order[0]] + \
            [('point', index, points[index][:2]) for index in order] + \
            [item for item in candidates if item[1] != order[0]]
        for kind, index, position in candidates:
            x, y = self.to_view(position)
            if math.hypot(x - location[0], y - location[1]) <= HIT:
                return kind, index
        return None

    def mouseDown_(self, event):
        if self.editor is None:
            return
        self.window().makeFirstResponder_(self)
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        location = (location.x, location.y)
        hit = self._hit(location)
        if hit is None:
            if event.clickCount() == 2 and self.editor.profile() is not None:
                x = self.to_data(location)[0]
                if 0.0 < x < 100.0:
                    self.editor.insert_point(x)
                return
            self.editor.select(None)
            return
        kind, index = hit
        self.editor.select(index)
        self.drag = {'kind': kind, 'index': index, 'start': self.to_data(location),
                     'points': copy.deepcopy(self.editor.profile()['points']), 'moved': False}

    def mouseDragged_(self, event):
        drag = self.drag
        if drag is None:
            return
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        current = self.to_data((location.x, location.y))
        dx, dy = current[0] - drag['start'][0], current[1] - drag['start'][1]
        flags = NSEvent.modifierFlags()
        if drag['kind'] == 'point' and flags & NSEventModifierFlagShift:
            if abs(dx) > abs(dy) * (self.y1 - self.y0) / 100.0:
                dy = 0.0
            else:
                dx = 0.0
        points = moved_points(drag['points'], drag['kind'], drag['index'], dx, dy,
                              bool(flags & NSEventModifierFlagOption))
        drag['moved'] = True
        self.editor.preview_points(points)

    def mouseUp_(self, event):
        drag, self.drag = self.drag, None
        if drag is not None and drag['moved']:
            self.editor.commit()

    def scrollWheel_(self, event):
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        delta = event.scrollingDeltaY() if event.hasPreciseScrollingDeltas() \
            else event.deltaY() * 8.0
        if NSEvent.modifierFlags() & NSEventModifierFlagShift:
            shift = delta / max(1.0, self._plot()[3]) * (self.y1 - self.y0)
            self._show(self.y0 + shift, self.y1 + shift)
            return
        self.zoom(math.exp(-delta * 0.01), location.y)

    def magnifyWithEvent_(self, event):
        location = self.convertPoint_fromView_(event.locationInWindow(), None)
        self.zoom(1.0 / max(0.2, 1.0 + event.magnification()), location.y)

    def keyDown_(self, event):
        characters = str(event.charactersIgnoringModifiers() or '')
        if characters in ('\x7f', '') and self.editor is not None:
            self.editor.delete_point()
            return
        arrows = {'': (0, 1), '': (0, -1), '': (-1, 0), '': (1, 0)}
        if characters in arrows and self.editor is not None:
            step = 10.0 if NSEvent.modifierFlags() & NSEventModifierFlagShift else 1.0
            dx, dy = arrows[characters]
            self.editor.nudge(dx * step, dy * step)
            return
        objc.super(VariableStrokeProfileCanvas, self).keyDown_(event)

    def undo_(self, sender):
        if self.editor is not None:
            self.editor.undo()

    def redo_(self, sender):
        if self.editor is not None:
            self.editor.redo()

    def validateUserInterfaceItem_(self, item):
        action = item.action()
        if action == 'undo:':
            return self.editor is not None and bool(self.editor.history)
        if action == 'redo:':
            return self.editor is not None and bool(self.editor.future)
        return False


class _CanvasGroup(Group):
    nsViewClass = VariableStrokeProfileCanvas


def moved_points(points, kind, index, dx, dy, independent=False):
    """Points after moving point `index` or one of its handles by (dx, dy). Its
    handles move with a point; a handle turns the opposite one with it (same
    length) unless `independent`. x stays within the neighbours."""
    points = copy.deepcopy(points)
    last = len(points) - 1
    point = points[index]
    low = points[index - 1][0] if index > 0 else 0.0
    high = points[index + 1][0] if index < last else 100.0
    if kind == 'point':
        if index in (0, last):
            dx = 0.0
        else:
            x = min(high - 0.1, max(low + 0.1, point[0] + dx))
            dx = x - point[0]
        y = min(width_profile.MAX_Y, max(width_profile.MIN_Y, point[1] + dy))
        dy = y - point[1]
        for i in (0, 2, 4):
            point[i] += dx
            point[i + 1] += dy
        return points
    if kind == 'in':
        point[2] = min(point[0], max(low, point[2] + dx))
        point[3] += dy
        moved, other, other_index = (point[2], point[3]), (point[4], point[5]), 4
    else:
        point[4] = max(point[0], min(high, point[4] + dx))
        point[5] += dy
        moved, other, other_index = (point[4], point[5]), (point[2], point[3]), 2
    has_other = index < last if kind == 'in' else index > 0
    if not independent and has_other:
        reach = math.hypot(other[0] - point[0], other[1] - point[1])
        vx, vy = point[0] - moved[0], point[1] - moved[1]
        size = math.hypot(vx, vy)
        if size > 1e-9 and reach > 1e-9:
            x = point[0] + vx / size * reach
            y = point[1] + vy / size * reach
            if other_index == 4:
                x = max(point[0], min(high, x))
            else:
                x = min(point[0], max(low, x))
            points[index][other_index], points[index][other_index + 1] = x, y
    return points


class VariableStrokeProfileFieldDelegate(NSObject):
    def initWithEditor_name_(self, editor, name):
        self = objc.super(VariableStrokeProfileFieldDelegate, self).init()
        if self is None:
            return None
        self._editor, self._name = editor, name
        return self

    def controlTextDidChange_(self, notification):
        self._editor.field_edit(self._name, notification.object().stringValue(), False)

    def controlTextDidEndEditing_(self, notification):
        self._editor.field_edit(self._name, notification.object().stringValue(), True)

    def stepped_(self, sender):
        self._editor.field_edit(self._name, sender.stringValue(), True)


# Window ---------------------------------------------------------------------

class VariableStrokeProfileEditor(object):
    """Edits the current font's width profiles. Changes apply to every stroke
    using the profile, in all glyphs and masters; the window keeps its own undo
    history (Glyphs' undo does not cover font data)."""

    def __init__(self, tool):
        self.tool = tool
        self.font = None
        self.library = {}
        self.current = None
        self.selected = None
        self.history, self.future = [], []
        self._before = None
        self._ids = []
        self._loading = False
        self.w = FloatingWindow((640, 400), _loc('Width Profiles', '線幅プロファイル'),
                                minSize=(460, 280))
        self.w.profile = PopUpButton((12, 12, 200, 22), [], callback=self.choose)
        self.w.nameLabel = TextBox((222, 16, 40, 17), _loc('Name', '名前'), sizeStyle='small')
        self.w.name = EditText((262, 13, 140, 21), sizeStyle='small')
        self.w.new = Button((-232, 12, 70, 22), _loc('New', '新規'), callback=self.new,
                            sizeStyle='small')
        self.w.duplicate = Button((-158, 12, 70, 22), _loc('Duplicate', '複製'),
                                  callback=self.duplicate, sizeStyle='small')
        self.w.delete = Button((-84, 12, 72, 22), _loc('Delete', '削除'), callback=self.delete,
                               sizeStyle='small')
        self.w.canvas = _CanvasGroup((12, 44, -12, -46))
        self.w.xLabel = TextBox((12, -33, 16, 17), 'X', sizeStyle='small')
        self.w.x = _SteppingEditText((28, -36, 52, 21), sizeStyle='small')
        self.w.xUnit = TextBox((82, -33, 14, 17), '%', sizeStyle='small')
        self.w.yLabel = TextBox((102, -33, 16, 17), 'Y', sizeStyle='small')
        self.w.y = _SteppingEditText((118, -36, 52, 21), sizeStyle='small')
        self.w.yUnit = TextBox((172, -33, 14, 17), '%', sizeStyle='small')
        self.w.divisionsLabel = TextBox((196, -33, 70, 17), _loc('Sections', '分割数'),
                                        sizeStyle='small')
        self.w.divisions = _SteppingEditText((256, -36, 46, 21), sizeStyle='small')
        self.w.fit = Button((-262, -36, 90, 22), _loc('Fit', '全体表示'), callback=self.fit,
                            sizeStyle='small')
        self.w.apply = Button((-166, -36, 154, 22), _loc('Apply to selection', '選択ストロークに適用'),
                              callback=self.apply, sizeStyle='small')
        self.canvas = self.w.canvas.getNSView()
        self.canvas.editor = self
        self.w.divisions.getNSTextField().setPlaceholderString_(_loc('Auto', '自動'))
        self.w.divisions.getNSTextField().setToolTip_(_loc(
            'Sections each centerline segment is split into to follow the profile. '
            'Empty: automatic. More sections follow the curve closer but add outline nodes.',
            '中心線の区間ごとに、プロファイルに沿わせるための分割数。空欄で自動。'
            '多いほど曲線に忠実になりますが、輪郭のノードが増えます。'))
        self.canvas.setToolTip_(_loc(
            'Drag points and handles. Double-click to add a point, Delete removes it. '
            'Scroll or pinch zooms vertically, Shift-scroll moves. Option: move one handle only.',
            '点とハンドルをドラッグ。ダブルクリックで点を追加、Delete で削除。'
            'スクロールまたはピンチで縦方向にズーム、Shift＋スクロールで上下に移動。'
            'Option で片側のハンドルだけ動かします。'))
        self._delegates = []
        for name, field in (('x', self.w.x), ('y', self.w.y), ('divisions', self.w.divisions),
                            ('name', self.w.name)):
            delegate = VariableStrokeProfileFieldDelegate.alloc().initWithEditor_name_(self, name)
            self._delegates.append(delegate)
            text_field = field.getNSTextField()
            text_field.setDelegate_(delegate)
            if name != 'name':
                text_field.setTarget_(delegate)
                text_field.setAction_('stepped:')
        self.w.bind('became key', self._became_key)
        self.w.bind('should close', self._should_close)

    # window
    def open(self, pid=None):
        self.reload(force=True)
        if pid is not None and pid in self.library:
            self.current, self.selected = pid, None
        self.refresh(fit=True)
        self.w.open()
        self.w.makeKey()

    def is_open(self):
        try:
            return self.w.getNSWindow().isVisible()
        except Exception:
            return False

    def _became_key(self, sender):
        self.reload()

    def _should_close(self, sender):
        if self._before is not None:
            self.commit()
        return True

    def reload(self, force=False):
        font = Glyphs.font
        if font is self.font and not force:
            return
        if font is not self.font:
            self.history, self.future = [], []
        self.font = font
        self.library = copy.deepcopy(profiles(font)) if font is not None else {}
        if self.current not in self.library:
            entries = sorted_profiles(self.library)
            self.current = entries[0][0] if entries else None
            self.selected = None
        self.refresh(fit=True)

    def refresh(self, fit=False):
        self._loading = True
        try:
            self._ids = fill_popup(self.w.profile, sorted_profiles(self.library), self.current)
            profile = self.profile()
            for control in (self.w.profile, self.w.name, self.w.duplicate, self.w.delete,
                            self.w.divisions, self.w.apply, self.w.fit):
                control.enable(profile is not None)
            self.w.new.enable(self.font is not None)
            self.w.name.set(profile['name'] if profile is not None else '')
            divisions = profile['divisions'] if profile is not None else None
            self.w.divisions.set('' if divisions is None else str(divisions))
            point = self._point()
            for field, index in ((self.w.x, 0), (self.w.y, 1)):
                field.enable(point is not None and (index == 1 or
                                                    0 < self.selected < len(profile['points']) - 1))
                field.set('' if point is None else '%g' % round(point[index], 2))
        finally:
            self._loading = False
        if fit:
            self.canvas.fit()
        self.canvas.setNeedsDisplay_(True)

    # state
    def profile(self):
        return self.library.get(self.current) if self.current is not None else None

    def _point(self):
        profile = self.profile()
        if profile is None or self.selected is None or self.selected >= len(profile['points']):
            return None
        return profile['points'][self.selected]

    def select(self, index):
        self.selected = index
        self.refresh()

    def _begin(self):
        if self._before is None:
            self._before = (copy.deepcopy(profiles(self.font)), self.current)

    def preview_points(self, points, update_fields=True):
        profile = dict(self.profile())
        profile['points'] = points
        self._preview(profile, update_fields)

    def _preview(self, profile, update_fields=True):
        """Show `profile` as the current one on the canvas and the active glyph."""
        self._begin()
        profile = width_profile.normalize(profile)
        self.library[self.current] = profile
        preview_profile(self.font, self.current, profile)
        layer = self.tool._layer()
        if layer is not None:
            self._invalidate(layer, self.current)
            self.tool._redraw()
        if update_fields:
            self.refresh()
        else:  # keep what is being typed
            self.canvas.setNeedsDisplay_(True)

    def commit(self):
        """Save the library and redraw every stroke that uses the profile."""
        if self.font is None:
            return
        before, self._before = self._before, None
        if before is not None:
            self.history.append(before)
            del self.history[:-100]
            self.future = []
        changed = {pid for pid in set(self.library) | set(profiles(self.font))
                   if self.library.get(pid) != profiles(self.font).get(pid)}
        set_profiles(self.font, self.library)
        self._after_change(changed or {self.current})

    def _after_change(self, changed):
        forget_interpolations()
        for pid in changed:
            for layer in profile_users(self.font, pid):
                self._invalidate(layer, pid)
        Glyphs.redraw()
        self.tool._last_ui_state = None
        self.tool._refresh_ui()
        self.refresh()

    @staticmethod
    def _invalidate(layer, pid):
        for path in list(layer.paths):
            if profile_id(path) == pid:
                try:
                    layer.elementDidChange_(path)
                except Exception:
                    pass

    def undo(self):
        self._restore(self.history, self.future)

    def redo(self):
        self._restore(self.future, self.history)

    def _restore(self, source, target):
        if not source or self.font is None:
            return
        target.append((copy.deepcopy(profiles(self.font)), self.current))
        library, current = source.pop()
        changed = {pid for pid in set(library) | set(profiles(self.font))
                   if library.get(pid) != profiles(self.font).get(pid)}
        self.library = copy.deepcopy(library)
        self.current = current if current in self.library else \
            (sorted_profiles(self.library) or [(None, None)])[0][0]
        self.selected = None
        set_profiles(self.font, self.library)
        self._after_change(changed)

    # actions
    def choose(self, sender):
        if self._loading:
            return
        index = sender.get()
        if 0 <= index < len(self._ids):
            self.current, self.selected = self._ids[index], None
            self.refresh(fit=True)

    def new(self, sender):
        self._add({'name': self._unique_name(_loc('Profile', 'プロファイル')),
                   'points': width_profile.default_points(), 'divisions': None})

    def duplicate(self, sender):
        profile = self.profile()
        if profile is not None:
            copied = copy.deepcopy(profile)
            copied['name'] = self._unique_name(profile['name'] or _loc('Profile', 'プロファイル'))
            self._add(copied)

    def _unique_name(self, base):
        names = {profile['name'] for profile in self.library.values()}
        if base not in names:
            return base
        number = 2
        while '%s %d' % (base, number) in names:
            number += 1
        return '%s %d' % (base, number)

    def _add(self, profile):
        if self.font is None:
            return
        self._begin()
        pid = uuid.uuid4().hex
        self.library[pid] = width_profile.normalize(profile)
        self.current, self.selected = pid, None
        self.commit()
        self.refresh(fit=True)

    def delete(self, sender):
        if self.profile() is None:
            return
        self._begin()
        del self.library[self.current]
        entries = sorted_profiles(self.library)
        self.current, self.selected = (entries[0][0] if entries else None), None
        self.commit()
        self.refresh(fit=True)

    def fit(self, sender):
        self.canvas.fit()

    def apply(self, sender):
        if self.current is not None:
            self.tool._set_profile(self.current)

    def insert_point(self, x):
        profile, index = width_profile.insert_point(self.profile(), x)
        if index is None:
            return
        self._begin()
        self.selected = index
        self._preview(profile)
        self.commit()

    def delete_point(self):
        profile = self.profile()
        if profile is None or self.selected is None or \
                not 0 < self.selected < len(profile['points']) - 1:
            return
        points = copy.deepcopy(profile['points'])
        del points[self.selected]
        self.selected = None
        self.preview_points(points)
        self.commit()

    def nudge(self, dx, dy):
        profile = self.profile()
        if profile is None or self.selected is None:
            return
        self.preview_points(moved_points(profile['points'], 'point', self.selected, dx, dy))
        self.commit()

    def field_edit(self, name, text, commit):
        if self._loading or self.profile() is None:
            return
        text = str(text).strip()
        profile = self.profile()
        if name == 'name':
            if commit and text and text != profile['name']:
                self._begin()
                renamed = dict(profile)
                renamed['name'] = text
                self.library[self.current] = width_profile.normalize(renamed)
                self.commit()
            return
        if name == 'divisions':
            if not commit:
                return
            try:
                value = None if not text else int(min(width_profile.MAX_DIVISIONS,
                                                      max(0, float(text))))
            except ValueError:
                self.refresh()
                return
            if value != profile['divisions']:
                self._begin()
                changed = dict(profile)
                changed['divisions'] = value
                self.library[self.current] = width_profile.normalize(changed)
                self.commit()
            return
        point = self._point()
        try:
            value = float(text)
        except ValueError:
            value = None
        if point is not None and value is not None and math.isfinite(value):
            axis = 0 if name == 'x' else 1
            delta = value - point[axis]
            if abs(delta) > 1e-9:
                points = moved_points(profile['points'], 'point', self.selected,
                                      delta if axis == 0 else 0.0, delta if axis == 1 else 0.0)
                self.preview_points(points, update_fields=False)
        if commit:
            if self._before is not None:
                self.commit()
            else:
                self.refresh()
