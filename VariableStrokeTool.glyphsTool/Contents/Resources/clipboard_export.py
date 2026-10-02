"""Vector clipboard data (SVG and PDF) for outlines copied out of Glyphs.

Contours are [(closed, segments)] with segments ('line', (p0, p1)) or
('cubic', (p0, p1, p2, p3)) in font units, y up. One unit becomes one point.
"""


def bounds(contours):
    points = [point for _, segments in contours for _, points in segments for point in points]
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _num(value):
    text = ('%.3f' % value).rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


def svg_path_data(segments, closed):
    """SVG path data with y flipped (SVG runs down the page)."""
    def point(p):
        return '%s %s' % (_num(p[0]), _num(-p[1]))
    parts = ['M' + point(segments[0][1][0])]
    for kind, points in segments:
        if kind == 'cubic':
            parts.append('C' + ' '.join(point(p) for p in points[1:]))
        else:
            parts.append('L' + point(points[1]))
    if closed:
        parts.append('Z')
    return ''.join(parts)


def svg_document(contours):
    box = bounds(contours)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    filled = ''.join(svg_path_data(segments, True) for closed, segments in contours if closed)
    stroked = ''.join(svg_path_data(segments, False) for closed, segments in contours
                      if not closed)
    paths = []
    if filled:
        paths.append('<path d="%s"/>' % filled)
    if stroked:  # open plain paths copied with the strokes: hairlines, as Glyphs shows them
        paths.append('<path d="%s" fill="none" stroke="#000"/>' % stroked)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" '
            'viewBox="%s %s %s %s">%s</svg>\n'
            % (_num(width), _num(height), _num(x0), _num(-y1), _num(width), _num(height),
               ''.join(paths)))


def pdf_document(contours):
    """One-page PDF (NSData) of the contours, or None when there is nothing to draw."""
    import Quartz
    from Foundation import NSMutableData
    box = bounds(contours)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    data = NSMutableData.data()
    media = Quartz.CGRectMake(0, 0, max(x1 - x0, 1.0), max(y1 - y0, 1.0))
    context = Quartz.CGPDFContextCreate(Quartz.CGDataConsumerCreateWithCFData(data), media, None)
    Quartz.CGPDFContextBeginPage(context, None)
    Quartz.CGContextTranslateCTM(context, -x0, -y0)
    for closed in (True, False):
        drawn = False
        for is_closed, segments in contours:
            if is_closed != closed:
                continue
            drawn = True
            Quartz.CGContextMoveToPoint(context, *segments[0][1][0])
            for kind, points in segments:
                if kind == 'cubic':
                    Quartz.CGContextAddCurveToPoint(context, *(points[1] + points[2] + points[3]))
                else:
                    Quartz.CGContextAddLineToPoint(context, *points[1])
            if closed:
                Quartz.CGContextClosePath(context)
        if drawn and closed:
            Quartz.CGContextFillPath(context)
        elif drawn:
            Quartz.CGContextSetLineWidth(context, 1.0)
            Quartz.CGContextStrokePath(context)
    Quartz.CGPDFContextEndPage(context)
    Quartz.CGPDFContextClose(context)
    return data
