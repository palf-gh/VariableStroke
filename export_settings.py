"""Read Glyphs' own OTF export overlap setting."""


def remove_overlap_enabled(defaults=None):
    if defaults is None:
        from Foundation import NSUserDefaults
        defaults = NSUserDefaults.standardUserDefaults()
    value = defaults.objectForKey_('OTFExportRemoveOverlap')
    # Glyphs uses an enabled checkbox as the default before the user changes it.
    return True if value is None else bool(value)
