"""Width profiles: a stroke's thickness along its length as one Bézier curve.

A profile is a dict {'name': str, 'points': [[x, y, in_x, in_y, out_x, out_y],
...], 'divisions': int or None}. x runs over the stroke's length (0-100 %),
y is a thickness factor in % (100 keeps the stroke as its nodes make it).
Each point carries its incoming and outgoing Bézier handle in absolute
coordinates. The first point sits at x 0 and the last at x 100; handles stay
within the x span of their curve piece, so every x has one thickness.

Plain Python, no Glyphs: the editing tool, the export filter and the tests use
it alike.
"""
import math

MIN_Y = 1.0  # a thinner section would turn the outline inside out
MAX_Y = 1000.0
MAX_DIVISIONS = 16
AUTO_DIVISIONS = 8  # at most, per centerline segment
_SAMPLES = 48  # per curve piece, for evaluate
_TABLES = {}
_TABLES_SIZE = 256


def default_points():
    """A flat profile: 100 % along the whole stroke."""
    return [[0.0, 100.0, 0.0, 100.0, 100.0/3.0, 100.0],
            [100.0, 100.0, 200.0/3.0, 100.0, 100.0, 100.0]]


def _finite(value, fallback):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    return value if math.isfinite(value) else fallback


def normalize(data):
    """A valid profile dict from saved data (any mapping), or None."""
    try:
        raw_points = list(data.get('points') or [])
    except (AttributeError, TypeError):
        return None
    points = []
    for raw in raw_points:
        try:
            values = [float(value) for value in list(raw)[:6]]
            values = [round(value, 4) if math.isfinite(value) else value for value in values]
        except (TypeError, ValueError):
            continue
        if len(values) < 2 or not all(math.isfinite(value) for value in values):
            continue
        x, y = values[0], values[1]
        if len(values) < 6:
            values = [x, y, x, y, x, y]
        points.append(values)
    points.sort(key=lambda point: point[0])
    if len(points) < 2:
        points = default_points()
    points[0][0], points[-1][0] = 0.0, 100.0
    result = []
    for point in points:
        x = min(100.0, max(0.0, point[0]))
        if result and x <= result[-1][0]:
            continue  # two points at one x
        result.append([x, min(MAX_Y, max(MIN_Y, point[1]))] + point[2:6])
    if len(result) < 2 or result[-1][0] < 100.0:
        result = [result[0], [100.0, result[-1][1]] + result[-1][2:6]] \
            if result and result[0][0] < 100.0 else default_points()
    for index, point in enumerate(result):
        low = result[index-1][0] if index > 0 else point[0]
        high = result[index+1][0] if index+1 < len(result) else point[0]
        point[2] = min(point[0], max(low, point[2]))
        point[4] = max(point[0], min(high, point[4]))
        if index == 0:
            point[2], point[3] = point[0], point[1]
        if index == len(result)-1:
            point[4], point[5] = point[0], point[1]
    divisions = None
    try:
        raw_divisions = data.get('divisions')
        if raw_divisions is not None:
            divisions = int(min(MAX_DIVISIONS, max(0, float(raw_divisions))))
    except (TypeError, ValueError, AttributeError):
        divisions = None
    try:
        name = str(data.get('name') or '')
    except (AttributeError, TypeError):
        name = ''
    return {'name': name, 'points': result, 'divisions': divisions}


def _key(profile):
    return tuple(tuple(point) for point in profile['points'])


def pieces(profile):
    """[(p0, p1, p2, p3)] Bézier pieces between consecutive points."""
    points = profile['points']
    return [((a[0], a[1]), (a[4], a[5]), (b[2], b[3]), (b[0], b[1]))
            for a, b in zip(points, points[1:])]


def _cubic(p0, p1, p2, p3, t):
    s = 1.0 - t
    return tuple(s*s*s*p0[i] + 3*s*s*t*p1[i] + 3*s*t*t*p2[i] + t*t*t*p3[i]
                 for i in range(2))


def _table(profile):
    """Dense (x, y) samples with x never decreasing."""
    key = _key(profile)
    table = _TABLES.get(key)
    if table is None:
        table = []
        for piece in pieces(profile):
            for i in range(_SAMPLES + 1):
                if table and i == 0:
                    continue
                x, y = _cubic(*piece, i/float(_SAMPLES))
                if table and x < table[-1][0]:
                    x = table[-1][0]  # handles crossing over: keep x monotone
                table.append((x, y))
        if len(_TABLES) >= _TABLES_SIZE:
            _TABLES.clear()
        _TABLES[key] = table
    return table


def evaluate(profile, x):
    """Thickness factor in % at x (0-100 % of the stroke's length)."""
    table = _table(profile)
    x = min(100.0, max(0.0, x))
    lo, hi = 0, len(table)-1
    while hi - lo > 1:
        middle = (lo + hi) // 2
        if table[middle][0] <= x:
            lo = middle
        else:
            hi = middle
    (x0, y0), (x1, y1) = table[lo], table[hi]
    y = y0 if x1 - x0 < 1e-12 else y0 + (y1-y0)*(x-x0)/(x1-x0)
    return min(MAX_Y, max(MIN_Y, y))


def is_flat(profile, tolerance=0.05):
    return all(abs(value - 100.0) <= tolerance
               for point in profile['points'] for value in (point[1], point[3], point[5]))


def divisions(profile):
    """Sections each centerline segment is split into, beyond its own two ends.
    It depends on the profile alone, so every master gets the same outline
    structure."""
    if profile['divisions'] is not None:
        return profile['divisions']
    if is_flat(profile):
        return 0
    return min(AUTO_DIVISIONS, len(profile['points']))  # curve pieces + 1


def polyline(profile, count=64):
    """(x, y) points along the curve, for previews."""
    table = _table(profile)
    step = max(1, (len(table)-1) // count)
    result = table[::step]
    if result[-1] != table[-1]:
        result.append(table[-1])
    return result


def y_range(profile):
    """(lowest, highest) y of the curve and its handles."""
    values = [value for point in profile['points'] for value in (point[1], point[3], point[5])]
    return min(values), max(values)


def insert_point(profile, x):
    """A copy of `profile` with a smooth point on the curve at x, splitting its
    piece so the curve keeps its shape."""
    points = [list(point) for point in profile['points']]
    for index, piece in enumerate(pieces(profile)):
        if piece[0][0] < x < piece[3][0]:
            lo, hi = 0.0, 1.0
            for _ in range(40):
                middle = (lo + hi) / 2.0
                if _cubic(*piece, middle)[0] < x:
                    lo = middle
                else:
                    hi = middle
            t = (lo + hi) / 2.0
            p0, p1, p2, p3 = piece
            mix = lambda a, b: (a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t)
            a, b, c = mix(p0, p1), mix(p1, p2), mix(p2, p3)
            d, e = mix(a, b), mix(b, c)
            m = mix(d, e)
            points[index][4:6] = list(a)
            points[index+1][2:4] = list(c)
            points.insert(index+1, [m[0], m[1], d[0], d[1], e[0], e[1]])
            result = dict(profile)
            result['points'] = points
            return normalize(result), index+1
    return profile, None
