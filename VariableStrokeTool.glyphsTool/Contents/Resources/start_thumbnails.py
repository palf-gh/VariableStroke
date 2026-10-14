"""Stroke outlines in the previews of Glyphs' start window.

Glyphs draws the preview of a recent file (its "Ag") with a helper program
that reads the file itself, without plugins: a stroke shows as its bare
centerline, closed ones filled, open ones not at all. So whenever Glyphs
opens or saves a font with strokes, a small stand-in file is written to the
cache: a copy of the font with only "A" and "g", their strokes expanded to
outlines.
While that is up to date with the font's file, Glyphs is handed the stand-in
instead when it asks for the preview, and draws it as it draws any font.
The font's own file is never touched.
"""
import hashlib
import json
import os
import traceback

CACHE_DIR = os.path.expanduser('~/Library/Caches/com.codex.VariableStroke/StartWindow')
PREVIEW_GLYPHS = ('A', 'g')  # what the start window shows
# NSImage(QuickLook) class methods in GlyphsCore that make the previews; the
# start window may come through either.
_SELECTORS = (b'thumbnailOfGlyphsFileAtURL:ofSize:asIcon:',
              b'thumbnailOfFileAtURL:ofSize:asIcon:fallback:')
_hooked = []


def _key(path):
    return hashlib.sha1(os.path.realpath(path).encode('utf-8')).hexdigest()


def _stamp(path):
    """What changes when the font's file is saved again: modification times
    (of a package, its font info and preview glyphs) and the size of a file."""
    try:
        if os.path.isdir(path):
            parts = []
            for name in ['fontinfo.plist'] + ['glyphs/%s.glyph' % g for g in PREVIEW_GLYPHS]:
                full = os.path.join(path, name)
                parts.append(os.path.getmtime(full) if os.path.exists(full) else None)
            return ['package'] + parts
        return [os.path.getmtime(path), os.path.getsize(path)]
    except OSError:
        return None


def cached_preview(path):
    """The stand-in file for the font at `path`, or None when there is none or
    the font's file has changed since it was written."""
    if not path:
        return None
    key = _key(path)
    stand_in = os.path.join(CACHE_DIR, key + '.glyphs')
    try:
        with open(os.path.join(CACHE_DIR, key + '.json')) as handle:
            meta = json.load(handle)
    except (OSError, ValueError):
        return None
    if meta.get('stamp') != _stamp(path) or not os.path.exists(stand_in):
        return None
    return stand_in


def forget_preview(path):
    key = _key(path)
    for ext in ('.glyphs', '.json'):
        try:
            os.remove(os.path.join(CACHE_DIR, key + ext))
        except OSError:
            pass


def write_preview(font, path, expand):
    """Write the stand-in for `font`, saved at `path`: a copy of the font
    (its masters, metrics and settings place the preview) with only the
    preview glyphs, their strokes turned into outlines by `expand(layer,
    glyph, master)`, which says whether a layer had any. Without strokes in
    the preview glyphs, any old stand-in goes: Glyphs' own preview is right
    then."""
    if not path:
        return False
    if not any(font.glyphs[name] is not None for name in PREVIEW_GLYPHS):
        forget_preview(path)
        return False
    stand_in = font.copy()
    stand_in.removeGlyphs_([glyph for glyph in stand_in.glyphs
                            if glyph.name not in PREVIEW_GLYPHS])
    any_stroke = False
    for glyph in stand_in.glyphs:
        source = font.glyphs[glyph.name]
        for master in stand_in.masters:
            layer = glyph.layers[master.id]
            if layer is None:
                continue
            try:
                if layer.components:
                    layer.decomposeComponents()
            except Exception:
                pass
            if expand(layer, source, master):
                any_stroke = True
    if not any_stroke:
        forget_preview(path)
        return False
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _key(path)
    target = os.path.join(CACHE_DIR, key + '.glyphs')
    temporary = os.path.join(CACHE_DIR, key + '.tmp.glyphs')
    stand_in.save(temporary)
    os.replace(temporary, target)
    with open(os.path.join(CACHE_DIR, key + '.json'), 'w') as handle:
        json.dump({'path': os.path.realpath(path), 'stamp': _stamp(path)}, handle)
    return True


def _url_path(url):
    try:
        return url.path() if url is not None and url.isFileURL() else None
    except Exception:
        return None


def hook():
    """Hand Glyphs the stand-in wherever it makes a preview of a font file that
    has one (see cached_preview)."""
    if _hooked:
        return
    import objc
    from AppKit import NSURL
    cls = objc.lookUpClass('NSImage')
    for name in _SELECTORS:
        if not cls.respondsToSelector_(name):
            continue
        original = cls.methodForSelector_(name)

        def wrapper(self, url, *rest, original=original):
            try:
                stand_in = cached_preview(_url_path(url))
                if stand_in is not None:
                    url = NSURL.fileURLWithPath_(stand_in)
            except Exception:
                print(traceback.format_exc())
            return original(self, url, *rest)

        objc.classAddMethod(cls, name, objc.selector(wrapper, selector=name,
                                                     signature=original.signature,
                                                     isClassMethod=True))
    _hooked.append(True)
