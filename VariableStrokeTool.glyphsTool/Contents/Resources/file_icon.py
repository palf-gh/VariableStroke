"""Stroke outlines in Finder's icon of a font file.

Finder's icon of a .glyphs file comes from Glyphs' Quick Look thumbnail
extension, which, like the start window's previews, reads the file itself
without plugins: a stroke shows as its bare centerline (see start_thumbnails).
That extension runs in a process of its own, out of reach of the plugin. So
the start window's stand-in (the font's "Ag", strokes expanded) is handed to
that same extension, and what it draws is set as the file's custom icon,
which Finder shows in place of the thumbnail.
The icon lives in the file's extended attributes (com.apple.ResourceFork and
com.apple.FinderInfo); the font's data is never touched. An icon set here is
marked, so it alone is taken off again when the font no longer has strokes
in its "Ag"; a custom icon someone else gave the file is left alone.
Packages (.glyphspackage) are skipped: their icon would be a file inside them.
"""
import ctypes
import ctypes.util
import json
import os
import traceback

MARK = 'com.codex.VariableStroke.icon'  # extended attribute: the icon is ours
_RESOURCE_FORK = 'com.apple.ResourceFork'  # where Finder keeps a custom icon
_FINDER_INFO = 'com.apple.FinderInfo'
_HAS_CUSTOM_ICON = 0x0400  # in the Finder flags, bytes 8-9 of the Finder info
ICON_SIZE = 512  # points; drawn at twice that
# Icons asked for and not yet set, by real path: a newer request for the same
# file makes an older one's late answer moot.
_pending = {}

_libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
_libc.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                           ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
_libc.getxattr.restype = ctypes.c_ssize_t
_libc.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                           ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
_libc.setxattr.restype = ctypes.c_int
_libc.removexattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
_libc.removexattr.restype = ctypes.c_int


def get_xattr(path, name):
    """The bytes of extended attribute `name` of `path`, or None."""
    path, name = os.fsencode(path), name.encode('utf-8')
    size = _libc.getxattr(path, name, None, 0, 0, 0)
    if size < 0:
        return None
    buffer = ctypes.create_string_buffer(size)
    size = _libc.getxattr(path, name, buffer, size, 0, 0)
    return buffer.raw[:size] if size >= 0 else None


def set_xattr(path, name, value):
    if _libc.setxattr(os.fsencode(path), name.encode('utf-8'), value, len(value), 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), path)


def remove_xattr(path, name):
    _libc.removexattr(os.fsencode(path), name.encode('utf-8'), 0)


def stamp(path):
    """What changes when the file is saved again (the extended attributes
    written here change neither)."""
    try:
        return [os.path.getmtime(path), os.path.getsize(path)]
    except OSError:
        return None


def _mark(path):
    """What was noted when the icon was set here, or None when the file has
    no icon of ours."""
    value = get_xattr(path, MARK)
    if value is None:
        return None
    try:
        return json.loads(value.decode('utf-8'))
    except ValueError:
        return {}


def has_custom_icon(path):
    info = get_xattr(path, _FINDER_INFO)
    return bool(info and len(info) >= 10 and (info[8] << 8 | info[9]) & _HAS_CUSTOM_ICON)


def has_foreign_icon(path):
    """Whether the file has a custom icon that was not set here."""
    return has_custom_icon(path) and _mark(path) is None


def _eligible(path):
    return bool(path) and os.path.isfile(path) and path.endswith('.glyphs')


def update(path, stand_in, stale_only=False, render=None, apply=None):
    """Bring the icon of the font file at `path` in line with `stand_in`, the
    start window's stand-in for it (None when it has none). With `stale_only`,
    an icon of ours that was set after the file's last save stands.
    `render(stand_in, done)` makes the image and calls `done(image)` (None
    when it could not), maybe later, on the main thread; `apply(image,
    path)` sets the icon (None takes it off) and says whether it did. Returns
    whether an icon was asked for."""
    if not _eligible(path):
        return False
    path = os.path.realpath(path)
    render = render or render_thumbnail
    apply = apply or set_icon
    mark = _mark(path)
    if stand_in is None:
        _pending.pop(path, None)
        if mark is not None:
            apply(None, path)
            remove_xattr(path, MARK)
        return False
    if mark is None and has_foreign_icon(path):
        return False
    if stale_only and mark is not None and mark.get('stamp') == stamp(path):
        return False
    request = object()
    _pending[path] = request

    def done(image):
        if _pending.get(path) is not request:
            return  # asked for again since, or no longer wanted
        del _pending[path]
        if image is None or not _eligible(path):
            return
        if _mark(path) is None and has_foreign_icon(path):
            return
        if apply(image, path):
            set_xattr(path, MARK, json.dumps({'stamp': stamp(path)}).encode('utf-8'))

    render(stand_in, done)
    return True


def _on_main_thread(work):
    from Foundation import NSOperationQueue, NSThread

    def run():
        try:
            work()
        except Exception:
            print(traceback.format_exc())
    if NSThread.isMainThread():
        run()
    else:
        NSOperationQueue.mainQueue().addOperationWithBlock_(run)


def render_thumbnail(stand_in, done):
    """Have Glyphs' own Quick Look thumbnail extension draw `stand_in` as an
    icon, as Finder would draw the font's file, and pass the NSImage (or None)
    to `done`, on the main thread."""
    from Foundation import NSURL
    from QuickLookThumbnailing import (QLThumbnailGenerator, QLThumbnailGenerationRequest,
                                       QLThumbnailGenerationRequestRepresentationTypeThumbnail)
    request = QLThumbnailGenerationRequest.alloc() \
        .initWithFileAtURL_size_scale_representationTypes_(
            NSURL.fileURLWithPath_(stand_in), (ICON_SIZE, ICON_SIZE), 2.0,
            QLThumbnailGenerationRequestRepresentationTypeThumbnail)
    request.setIconMode_(True)

    def finished(representation, error):
        image = None
        try:
            if representation is not None:
                image = representation.NSImage()
                if image is not None:  # 2x pixels on a 1x canvas
                    image.setSize_((ICON_SIZE, ICON_SIZE))
        except Exception:
            print(traceback.format_exc())
        _on_main_thread(lambda: done(image))
    QLThumbnailGenerator.sharedGenerator() \
        .generateBestRepresentationForRequest_completionHandler_(request, finished)


def set_icon(image, path):
    """Set `image` as the custom icon of the file at `path`, or with None,
    take it off (Finder clears its flag but keeps the resource fork, which
    held nothing else). The file's modification time is put back: Finder
    moves it on, and Glyphs would then take the file for changed by another
    program (and the start window's stand-in for out of date)."""
    from AppKit import NSWorkspace
    before = os.stat(path)
    try:
        done = bool(NSWorkspace.sharedWorkspace().setIcon_forFile_options_(image, path, 0))
        if image is None:
            remove_xattr(path, _RESOURCE_FORK)
    finally:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    return done
