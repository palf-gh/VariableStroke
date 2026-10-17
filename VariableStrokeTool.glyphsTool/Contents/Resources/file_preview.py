"""Stroke outlines in Quick Look's preview of a font file.

Quick Look (the space bar in Finder) shows a .glyphs file through an
extension that reads the file without plugins, so strokes would show as
bare centerlines. When Glyphs opens or saves a font, the start window's
stand-in (the font's "Ag", strokes expanded; see start_thumbnails) is drawn
large by Glyphs' own Quick Look thumbnail extension, and that PNG is kept in
the file's extended attributes, together with the modification time and size
of the file it was made for. The VariableStroke Quick Look extension
(QuickLook/ in this repository) shows that PNG while it matches the file, and
Glyphs' own thumbnail of the file otherwise, which is right for a font
without strokes in its "Ag". The font's data is never touched.
Packages (.glyphspackage) are skipped, as for the icon (see file_icon).
"""
import json
import os

import file_icon

PREVIEW = 'com.codex.VariableStroke.preview'  # extended attribute: the PNG
STAMP = 'com.codex.VariableStroke.previewStamp'  # what the PNG was made for
VERSION = 2  # 1 held a PDF of the display strings
SIZE = 800  # points; drawn at twice that
# Previews asked for and not yet kept, by real path: a newer request for the
# same file makes an older one's late answer moot.
_pending = {}


def made_for(path):
    """The stamp of the file the kept PNG was made for, or None."""
    value = file_icon.get_xattr(path, STAMP)
    if value is None:
        return None
    try:
        made = json.loads(value.decode('utf-8'))
    except ValueError:
        return None
    return made.get('stamp') if made.get('version') == VERSION else None


def is_current(path):
    made = made_for(path)
    return made is not None and made == file_icon.stamp(path)


def forget(path):
    file_icon.remove_xattr(path, PREVIEW)
    file_icon.remove_xattr(path, STAMP)


def update(path, stand_in, stale_only=False, render=None, encode=None):
    """Keep a large picture of `stand_in`, the start window's stand-in for the
    font file at `path`, with that file; without a stand-in (no strokes in its
    "Ag"), take any picture off. With `stale_only`, a picture made for the
    file as it is stands. `render(stand_in, done)` makes the image and calls
    `done(image)` (None when it could not), maybe later, on the main thread;
    `encode(image)` gives its PNG bytes. Returns whether a picture was asked
    for."""
    if not file_icon._eligible(path):
        return False
    path = os.path.realpath(path)
    if stand_in is None:
        _pending.pop(path, None)
        forget(path)
        return False
    if stale_only and is_current(path):
        return False
    render = render or (lambda stand_in, done: file_icon.render_thumbnail(
        stand_in, done, SIZE, icon_mode=False))
    encode = encode or png
    made = file_icon.stamp(path)
    request = object()
    _pending[path] = request

    def done(image):
        if _pending.get(path) is not request:
            return  # asked for again since, or no longer wanted
        del _pending[path]
        data = encode(image) if image is not None else None
        if not data or not file_icon._eligible(path):
            return
        file_icon.set_xattr(path, PREVIEW, data)
        file_icon.set_xattr(path, STAMP, json.dumps(
            {'version': VERSION, 'stamp': made}).encode('utf-8'))

    render(stand_in, done)
    return True


def png(image):
    from AppKit import NSBitmapImageRep, NSPNGFileType
    rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    data = rep.representationUsingType_properties_(NSPNGFileType, {}) if rep is not None else None
    return bytes(data) if data is not None else None
