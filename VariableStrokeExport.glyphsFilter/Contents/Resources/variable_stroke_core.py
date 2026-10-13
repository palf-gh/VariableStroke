"""Pure geometry for Variable Stroke. Coordinates are Glyphs font units."""
from __future__ import division
import math
import width_profile

CAPS = ('flat', 'round', 'ellipse', 'square', 'horizontal', 'vertical', 'angle', 'curve')
CUTS = ('horizontal', 'vertical', 'angle')
EPS = 1e-9
DEFAULT_CAP_CURVE = ((1.0/3.0, 0.35), (-1.0/3.0, 0.35))


def add(a, b): return (a[0] + b[0], a[1] + b[1])
def sub(a, b): return (a[0] - b[0], a[1] - b[1])
def mul(a, k): return (a[0] * k, a[1] * k)
def length(a): return math.hypot(a[0], a[1])
def unit(a):
    d = length(a)
    return (a[0] / d, a[1] / d) if d > EPS else (1.0, 0.0)
def normal(a): return (-a[1], a[0])
def _dot(a, b): return a[0]*b[0] + a[1]*b[1]


def move_outline_vertices(contours, edges, offsets, handle_offsets=None):
    """Move individual sharp outline vertices and their adjacent cubic handles.

    `offsets` maps the edge index to (left delta, right delta). The number and
    kinds of segments stay fixed for interpolation between masters.
    """
    handle_offsets = handle_offsets or {}
    targets = []
    for index, pair in edges.items():
        vertex_pair = offsets.get(index, ((0, 0), (0, 0)))
        handles = handle_offsets.get(index, ((0, 0),) * 4)
        for side, (point, delta) in enumerate(zip(pair, vertex_pair)):
            incoming, outgoing = handles[side*2:side*2+2]
            if any(value for vector in (delta, incoming, outgoing) for value in vector):
                targets.append((point, delta, incoming, outgoing))
    if not targets:
        return contours
    moved = []
    for contour in contours:
        result = []
        for kind, points in contour:
            points = list(points)
            for endpoint, handle in ((0, 1), (-1, -2)):
                original = points[endpoint]
                for point, delta, incoming, outgoing in targets:
                    if length(sub(original, point)) < 1e-5:
                        points[endpoint] = add(original, delta)
                        if kind == 'cubic':
                            points[handle] = add(points[handle], add(delta,
                                incoming if endpoint == -1 else outgoing))
                        break
            result.append((kind, tuple(points)))
        moved.append(result)
    for index, pair in list(edges.items()):
        deltas = offsets.get(index, ((0, 0), (0, 0)))
        edges[index] = tuple(add(point, delta) for point, delta in zip(pair, deltas))
    return moved


def outline_direction_at_vertex(contours, point, prefer_incoming=True):
    """Prefer a touching line; otherwise use the nearest Bézier control handle."""
    lines, curves = [], []
    for contour in contours:
        for kind, points in contour:
            start, end = points[0], points[-1]
            if length(sub(end, point)) < 1e-5:
                direction = sub(end, start) if kind == 'line' else sub(end, points[-2])
                if length(direction) < EPS and kind == 'cubic':
                    direction = sub(end, points[-3])
                (lines if kind == 'line' else curves).append((True, direction))
            if length(sub(start, point)) < 1e-5:
                direction = sub(end, start) if kind == 'line' else sub(points[1], start)
                if length(direction) < EPS and kind == 'cubic':
                    direction = sub(points[2], start)
                (lines if kind == 'line' else curves).append((False, direction))
    for candidates in (lines, curves):
        for incoming in (prefer_incoming, not prefer_incoming):
            for candidate_incoming, direction in candidates:
                if candidate_incoming == incoming and length(direction) > EPS:
                    return unit(direction)
    return None


# The two hottest functions of live editing, written out (same arithmetic as
# add/mul, so results are unchanged).
def cubic(p0, p1, p2, p3, t):
    s = 1 - t
    a, b, c, d = s*s*s, 3*s*s*t, 3*s*t*t, t*t*t
    return ((p0[0]*a + p1[0]*b) + (p2[0]*c + p3[0]*d),
            (p0[1]*a + p1[1]*b) + (p2[1]*c + p3[1]*d))


def cubic_derivative(p0, p1, p2, p3, t):
    s = 1 - t
    a, b, c = 3*s*s, 6*s*t, 3*t*t
    return (((p1[0]-p0[0])*a + (p2[0]-p1[0])*b) + (p3[0]-p2[0])*c,
            ((p1[1]-p0[1])*a + (p2[1]-p1[1])*b) + (p3[1]-p2[1])*c)


def sample_segments(segments, closed=False):
    """Each segment: (kind, points, width_at_start, width_at_end)."""
    samples = []
    for kind, pts, w0, w1 in segments:
        if w0 <= 0 or w1 <= 0:
            raise ValueError('Width must be positive')
        if kind == 'line':
            steps = max(1, int(math.ceil(length(sub(pts[1], pts[0])) / 10.0)))
        elif kind == 'cubic':
            chord = length(sub(pts[-1], pts[0]))
            hull = sum(length(sub(pts[i+1], pts[i])) for i in range(3))
            steps = max(8, min(128, int(math.ceil(max(hull / 8.0, (hull-chord) / 2.0)))))
        else:
            raise ValueError('Unsupported segment: ' + str(kind))
        for i in range(steps + 1):
            if samples and i == 0:
                continue
            t = i / float(steps)
            p = add(mul(pts[0], 1-t), mul(pts[1], t)) if kind == 'line' else cubic(*pts, t)
            tangent = sub(pts[1], pts[0]) if kind == 'line' else cubic_derivative(*pts, t)
            samples.append((p, tangent, w0*(1-t)+w1*t))
    if closed and len(samples) > 1 and length(sub(samples[0][0], samples[-1][0])) < EPS:
        samples.pop()
    return samples


def _side_points(samples, closed):
    left, right, tangents = [], [], []
    count = len(samples)
    for i, (p, derivative, width) in enumerate(samples):
        if closed:
            before = samples[(i-1) % count][0]
            after = samples[(i+1) % count][0]
        else:
            before = samples[max(0, i-1)][0]
            after = samples[min(count-1, i+1)][0]
        tangent = unit(add(unit(derivative), unit(sub(after, before))))
        n = normal(tangent)
        left.append(add(p, mul(n, width/2.0)))
        right.append(sub(p, mul(n, width/2.0)))
        tangents.append(tangent)
    return left, right, tangents


def _cap(center, width, tangent, style, at_end):
    n = normal(tangent)
    if style == 'horizontal':
        direction = (1.0 if n[0] >= 0 else -1.0, 0.0)
        return (add(center, mul(direction, width/2)), sub(center, mul(direction, width/2)), [])
    if style == 'vertical':
        direction = (0.0, 1.0 if n[1] >= 0 else -1.0)
        return (add(center, mul(direction, width/2)), sub(center, mul(direction, width/2)), [])
    if style == 'square':
        extension = mul(tangent, width/2 if at_end else -width/2)
        c = add(center, extension)
        return (add(c, mul(n, width/2)), sub(c, mul(n, width/2)), [])
    if style in ('round', 'ellipse'):
        steps = max(8, min(64, int(math.ceil(width/4.0))))
        if at_end:
            arc = [add(center, mul(add(mul(n, math.cos(math.pi*i/steps)),
                                       mul(tangent, math.sin(math.pi*i/steps))), width/2))
                   for i in range(steps+1)]
        else:
            arc = [add(center, mul(add(mul(n, -math.cos(math.pi*i/steps)),
                                       mul(tangent, -math.sin(math.pi*i/steps))), width/2))
                   for i in range(steps+1)]
        return (arc[0], arc[-1], arc[1:-1])
    if style == 'flat':
        return (add(center, mul(n, width/2)), sub(center, mul(n, width/2)), [])
    if style == 'curve':
        left, right = add(center, mul(n, width/2)), sub(center, mul(n, width/2))
        points, _ = cap_curve_geometry(left, right, tangent, at_end)
        return (points[0], points[-1], [cubic(*points, i/16.0) for i in range(1, 16)])
    raise ValueError('Unknown cap: ' + str(style))


def outline(segments, closed=False, cap_start='flat', cap_end='flat'):
    """Return one closed polygon for open paths, two opposite-wound rings for closed paths."""
    samples = sample_segments(segments, closed)
    if len(samples) < 2:
        return []
    left, right, tangents = _side_points(samples, closed)
    if closed:
        outer, inner = left, list(reversed(right))
        return [outer, inner]
    start = _cap(samples[0][0], samples[0][2], tangents[0], cap_start, False)
    end = _cap(samples[-1][0], samples[-1][2], tangents[-1], cap_end, True)
    left[0], right[0] = start[0], start[1]
    left[-1], right[-1] = end[0], end[1]
    # End cap runs left-to-right; start cap runs right-to-left.
    return [left + end[2] + list(reversed(right)) + start[2]]

# Outline contours are stored as true cubic Beziers. The sampled offset is
# recursively fitted, so the node count depends on curvature, not sample count.
def _fit_side(points, tolerance=0.35):
    if len(points) < 2:
        return []
    def fit(lo, hi):
        p0, p3 = points[lo], points[hi]
        chord = sub(p3, p0)
        distance = length(chord)
        if distance < EPS:
            return []
        span = points[lo:hi+1]
        if max(abs(chord[0]*(p[1]-p0[1])-chord[1]*(p[0]-p0[0]))/distance for p in span) < tolerance:
            return [('line', (p0, p3))]
        t0 = unit(sub(points[lo+1], p0))
        t1 = unit(sub(p3, points[hi-1]))
        # Chord length parametrization and least-squares tangent lengths.
        distances = [0.0]
        for j in range(lo+1, hi+1):
            distances.append(distances[-1]+length(sub(points[j], points[j-1])))
        total = distances[-1]
        if total < EPS:
            return []
        c00 = c01 = c11 = x0 = x1 = 0.0
        us = [d/total for d in distances]
        for p, u in zip(span, us):
            s = 1-u
            a0, a1 = mul(t0, 3*s*s*u), mul(t1, -3*s*u*u)
            base = add(mul(p0, s*s*s+3*s*s*u), mul(p3, 3*s*u*u+u*u*u))
            residual = sub(p, base)
            c00 += a0[0]*a0[0]+a0[1]*a0[1]
            c01 += a0[0]*a1[0]+a0[1]*a1[1]
            c11 += a1[0]*a1[0]+a1[1]*a1[1]
            x0 += a0[0]*residual[0]+a0[1]*residual[1]
            x1 += a1[0]*residual[0]+a1[1]*residual[1]
        determinant = c00*c11-c01*c01
        if abs(determinant) > EPS:
            alpha = (x0*c11-x1*c01)/determinant
            beta = (c00*x1-c01*x0)/determinant
        else:
            alpha = beta = distance/3
        if alpha <= 0 or beta <= 0 or alpha > total*4 or beta > total*4:
            alpha = beta = distance/3
        controls = (p0, add(p0, mul(t0, alpha)), sub(p3, mul(t1, beta)), p3)
        errors = [length(sub(cubic(*controls, u), p)) for p, u in zip(span, us)]
        largest = max(errors)
        if largest <= tolerance or hi-lo <= 1:
            return [('cubic', controls)]
        split = lo+errors.index(largest)
        split = max(lo+1, min(hi-1, split))
        return fit(lo, split)+fit(split, hi)
    last = len(points)-1
    if last > 1 and length(sub(points[last], points[0])) < EPS:
        # A closed side starts and ends on the same point; fit it in two halves.
        return fit(0, last//2)+fit(last//2, last)
    return fit(0, last)


def _round_cap(center, radius, tangent, start, section=None, axial_radius=None):
    """Two quarter-ellipse cubics; equal radii retain the circular round cap."""
    outward = mul(tangent, -1 if start else 1)
    n = unit(section) if section is not None else normal(tangent)
    left = add(center, mul(n, radius))
    right = sub(center, mul(n, radius))
    axial = radius if axial_radius is None else axial_radius
    tip = add(center, mul(outward, axial))
    k_side = 0.5522847498307936*radius
    k_axial = 0.5522847498307936*axial
    if start:
        # right -> outward tip -> left
        return [('cubic', (right, add(right, mul(outward, k_axial)),
                            sub(tip, mul(n, k_side)), tip)),
                ('cubic', (tip, add(tip, mul(n, k_side)),
                            add(left, mul(outward, k_axial)), left))]
    return [('cubic', (left, add(left, mul(outward, k_axial)),
                        add(tip, mul(n, k_side)), tip)),
            ('cubic', (tip, sub(tip, mul(n, k_side)),
                        add(right, mul(outward, k_axial)), right))]


def cap_curve_geometry(left, right, tangent, at_end, controls=None):
    """Cubic cap and its local frame; controls use half-width units."""
    first, last = (left, right) if at_end else (right, left)
    middle = mul(add(first, last), 0.5)
    lateral = mul(sub(first, last), 0.5)
    outward = mul(unit(tangent), length(lateral) * (1 if at_end else -1))
    values = controls if controls is not None else DEFAULT_CAP_CURVE
    points = tuple(add(middle, add(mul(lateral, float(s)), mul(outward, float(d))))
                   for s, d in values)
    return (first, points[0], points[1], last), (middle, lateral, outward)


def cap_curve_coordinates(left, right, tangent, at_end, point):
    """Return a dragged control point in the same local frame."""
    _, (middle, lateral, outward) = cap_curve_geometry(
        left, right, tangent, at_end)
    determinant = lateral[0]*outward[1] - lateral[1]*outward[0]
    if abs(determinant) < EPS:
        return None
    delta = sub(point, middle)
    return ((delta[0]*outward[1] - delta[1]*outward[0])/determinant,
            (lateral[0]*delta[1] - lateral[1]*delta[0])/determinant)


def _cap_segments(center, width, tangent, style, at_end, left, right, nib=None,
                  slant=0.0, curve=None):
    if style == 'curve' or (curve is not None and style not in ('round', 'ellipse')):
        return [('cubic', cap_curve_geometry(left, right, tangent, at_end, curve)[0])]
    if style in ('round', 'ellipse'):
        if nib is None:
            return _round_cap(center, width/2, tangent, not at_end)
        # Use the actual rotated section endpoints, including its offset.
        middle = mul(add(left, right), 0.5)
        if style == 'ellipse':
            return _ellipse_cap(middle, nib, tangent, not at_end, left, right)
        section = mul(sub(left, right), 0.5)
        return _round_cap(middle, length(section), tangent, not at_end, section)
    return [('line', (left, right))] if at_end else [('line', (right, left))]


def _ellipse_cap(center, nib, tangent, start, left, right):
    """The outward half of the nib ellipse, in the nib's page angle."""
    width, height, _, angle, _ = _nib(nib)
    radians = math.radians(angle)
    u = (math.cos(radians), math.sin(radians))
    v = (-u[1], u[0])
    rx, ry = width/2.0, height/2.0
    first, last = (right, left) if start else (left, right)
    delta = sub(first, center)
    theta = math.atan2((delta[0]*v[0] + delta[1]*v[1])/ry,
                       (delta[0]*u[0] + delta[1]*u[1])/rx)
    outward = mul(unit(tangent), -1 if start else 1)

    def point(a):
        return add(center, add(mul(u, rx*math.cos(a)), mul(v, ry*math.sin(a))))

    def derivative(a):
        return add(mul(u, -rx*math.sin(a)), mul(v, ry*math.cos(a)))

    direction = 1 if sum(x*y for x, y in zip(sub(point(theta+math.pi/2), center), outward)) > 0 else -1
    step = direction * math.pi/2
    k = 0.5522847498307936 * direction
    pieces = []
    for index in range(2):
        a = theta + index*step
        b = a + step
        p0 = first if index == 0 else point(a)
        p3 = last if index == 1 else point(b)
        pieces.append(('cubic', (p0, add(p0, mul(derivative(a), k)),
                                 sub(p3, mul(derivative(b), k)), p3)))
    return pieces


FIT_TOLERANCE = 1.0
MITER_LIMIT = 4.0
# A line meeting a curve keeps its true miter (both edges straight on to their
# crossing) up to this many half-widths; only near hairpins is it pulled in.
MITER_FAR_LIMIT = 10.0
# Inside a smooth node whose fold is cut by a corner (see _trim_folds), the
# radius the outline rounds that corner off by: this many times the
# geometric mean of the corner's distance from where the unfolded edges
# would meet and the stroke's half-width there. A node's own rounding
# (`fold_rounds`) scales it.
FOLD_ROUNDING = 1.0
# How many half-widths a cap cut may reach along the stroke: a horizontal cut
# stays horizontal down to a stroke about 5 degrees from horizontal.
CUT_LIMIT = 12.0
# Relative change of nib width or height along a segment at which its
# thickness is fully held between its end thicknesses (see _Side._thickness_range).
THICKNESS_LIMIT_CHANGE = 0.25
# Bend fillets (see _fillet_edge): handle length as a share of the distance to
# the tangent crossing when the centerline gives none, how much an edge may turn
# back (degrees) before it is replaced, and the sharpest centerline turn
# (degrees) that gets one.
FILLET_HANDLE = 0.6
FILLET_REVERSE = 3.0
FILLET_MAX_TURN = 135.0
NEAR_STRAIGHT = math.sin(math.radians(10))  # below this kink a corner fades to smooth


def _nib(value):
    """Normalize a node's stroke to (width, height, offset, rotation).

    `value` is a width, or (width, height[, offset]). Height None means round
    (height = width). Offset 0 keeps the centerline in the middle; +/-1 puts
    one edge on it. Larger magnitudes move the entire stroke beyond it.
    Rotation turns the width and height axes of the ellipse on the page. An
    optional fifth value rotates the section axis away from the local normal.
    """
    if isinstance(value, (tuple, list)):
        w = float(value[0])
        h = float(value[1]) if len(value) > 1 and value[1] is not None else w
        o = float(value[2]) if len(value) > 2 and value[2] is not None else 0.0
        rotation = float(value[3]) if len(value) > 3 and value[3] is not None else 0.0
        section_angle = float(value[4]) if len(value) > 4 and value[4] is not None else 0.0
    else:
        w = h = float(value)
        o = 0.0
        rotation = 0.0
        section_angle = 0.0
    if w <= 0 or h <= 0:
        raise ValueError('Width must be positive')
    return (w, h, o, rotation % 180.0, section_angle)


def ellipse_support(direction, width, height, angle):
    """Projected half-width of a rotated ellipse along `direction`."""
    radians = math.radians(angle)
    u = (math.cos(radians), math.sin(radians))
    v = (-u[1], u[0])
    direction = unit(direction)
    a = direction[0]*u[0] + direction[1]*u[1]
    b = direction[0]*v[0] + direction[1]*v[1]
    rx, ry = width/2.0, height/2.0
    radius = math.hypot(rx*a, ry*b)
    return mul(direction, radius)


def ellipse_contact(direction, width, height, angle):
    """Actual contact point of the rotated nib ellipse in `direction`."""
    radians = math.radians(angle)
    u = (math.cos(radians), math.sin(radians))
    v = (-u[1], u[0])
    direction = unit(direction)
    a = direction[0]*u[0] + direction[1]*u[1]
    b = direction[0]*v[0] + direction[1]*v[1]
    rx, ry = width/2.0, height/2.0
    radius = math.hypot(rx*a, ry*b)
    return add(mul(u, rx*rx*a/radius), mul(v, ry*ry*b/radius))


def ellipse_nib_edges(point, tangent, nib):
    """The nib ellipse's contact points on both sides of a path end."""
    w, h, o, angle, _ = _nib(nib)
    support = ellipse_contact(normal(unit(tangent)), w, h, angle)
    return add(point, mul(support, 1+o)), sub(point, mul(support, 1-o))


def _place_ellipse_cap(left_side, right_side, point, tangent, nib, at_end):
    """Move only the terminal outline handles onto the nib ellipse."""
    left, right = ellipse_nib_edges(point, tangent, nib)
    direction = unit(tangent)
    for side, target in ((left_side, left), (right_side, right)):
        piece = side.override_piece or side.piece()
        if side.kind == 'cubic':
            p0, p1, p2, p3 = piece[1]
            if at_end:
                handle = length(sub(p3, p2))
                side.end = target
                side.override_piece = ('cubic', (p0, p1,
                                                  sub(target, mul(direction, handle)), target))
            else:
                handle = length(sub(p1, p0))
                side.start = target
                side.override_piece = ('cubic', (target,
                                                  add(target, mul(direction, handle)), p2, p3))
        elif at_end:
            side.end = target
            side.override_piece = ('line', (side.start, target))
        else:
            side.start = target
            side.override_piece = ('line', (target, side.end))


def _half_thickness(n, w, h, slant):
    """Half the stroke thickness across direction n (unit normal of the stroke).

    Vertical strokes get the width, horizontal ones the height and diagonals a
    smooth blend. The axes must stay independent even when an italic angle is
    set; that angle controls cuts, not the thickness of a vertical stroke.
    """
    return math.hypot(w*n[0], h*n[1]) / 2.0


def nib_edges(point, tangent, nib, italic_angle=0.0):
    """Left and right outline points of the stroke at a centerline point."""
    w, h, o, rotation, section_angle = _nib(nib)
    n = normal(unit(tangent))
    if section_angle:
        radians = math.radians(section_angle)
        n = (n[0]*math.cos(radians)-n[1]*math.sin(radians),
             n[0]*math.sin(radians)+n[1]*math.cos(radians))
    support = ellipse_support(n, w, h, rotation)
    return add(point, mul(support, 1+o)), sub(point, mul(support, 1-o))


def node_edges(segments, closed=False, italic_angle=0.0):
    """Outline points on both sides of each centerline node, exactly as the outline
    uses them: corners at their miter / crossing point, open ends before the caps.

    Returns {on-curve index: (left, right)}; segment i starts at on-curve i.
    """
    slant = math.tan(math.radians(italic_angle or 0.0))
    lefts, rights, widths, indices = [], [], [], []
    for index, (kind, pts, e0, e1) in enumerate(segments):
        if all(length(sub(p, pts[0])) < EPS for p in pts):
            continue
        left, right = _side_pair(kind, pts, e0, e1, slant)
        lefts.append(left)
        rights.append(right)
        widths.append(lefts[-1].extent(1.0))
        indices.append(index)
    if not lefts:
        return {}
    _ease_sizes(lefts, closed)
    _ease_sizes(rights, closed)
    _trim_folds(lefts, closed)
    _trim_folds(rights, closed)
    count = len(lefts)
    for sides in (lefts, rights):
        for k in range(count if closed else count-1):
            _join(sides[k], sides[(k+1) % count], widths[k])
    return _edge_map(lefts, rights, indices, closed)


def _edge_map(lefts, rights, indices, closed):
    """{on-curve index: (left, right)} of joined sides (see node_edges)."""
    result = {index: (lefts[k].start, rights[k].start) for k, index in enumerate(indices)}
    if not closed:
        result[indices[-1] + 1] = (lefts[-1].end, rights[-1].end)
    return result


def _cut_direction(style, slant, angle=0.0):
    if style == 'horizontal':
        return (1.0, 0.0)
    if style == 'angle':  # custom cut: degrees on the page, 0 horizontal, 90 vertical
        radians = math.radians(angle or 0.0)
        return (math.cos(radians), math.sin(radians))
    return unit((slant, 1.0))  # vertical cuts follow the italic angle

# Outline structure is fixed so masters stay interpolation compatible:
#   * every centerline segment gives exactly one piece per side (line -> line,
#     cubic -> one cubic), so each side has one node per centerline node;
#   * corners collapse to a single node per side (miter point or trim point);
#   * each editable curve cap is one cubic; round caps add two quarter arcs.
# Only the coordinates change with the design, never the node count.


def _derivative(kind, pts, t):
    if kind == 'line':
        return sub(pts[1], pts[0])
    d = cubic_derivative(*pts, t)
    if length(d) < EPS:  # retracted handle: look slightly inward
        d = cubic_derivative(*pts, min(1.0, max(0.0, t + (1e-3 if t < 0.5 else -1e-3))))
    if length(d) < EPS:
        d = sub(pts[-1], pts[0])
    return d


def _point_segment_distance(p, a, b):
    abx, aby = b[0]-a[0], b[1]-a[1]
    denominator = abx*abx + aby*aby
    t = 0.0 if denominator < EPS else max(0.0, min(1.0, ((p[0]-a[0])*abx + (p[1]-a[1])*aby) / denominator))
    return math.hypot(p[0] - (a[0] + abx*t), p[1] - (a[1] + aby*t))


class _Side(object):
    """One side (sign +1 left, -1 right) of one centerline segment, trimmable by t."""

    def __init__(self, kind, pts, e0, e1, sign, slant=0.0, shared=None, modulation=None):
        if kind not in ('line', 'cubic'):
            raise ValueError('Unsupported segment: ' + str(kind))
        self.kind, self.pts, self.sign, self.slant = kind, pts, sign, slant
        self._modulation = _Modulation(kind, pts, modulation) if modulation else None
        # {t: (left, right)}: both sides of a segment sample the same centerline
        # points, so the pair shares one table (see _side_pair).
        self._edges = {} if shared is None else shared
        self.n0, self.n1 = _nib(e0), _nib(e1)
        self.w0, self.w1 = self.n0[0], self.n1[0]
        self.ta, self.tb = 0.0, 1.0
        self.size_slopes = None  # see _ease_sizes
        # The edge with its folds rounded off (see _round_chain_folds) and
        # its directions at the ends (None: its own), or None.
        self.target = None
        self.target_dirs = (None, None)
        self.start, self.end = self.at(0.0), self.at(1.0)
        self.override_piece = None
        self.join_extension_ratio = 0.0
        self.smooth_start = self.smooth_end = False

    def nib(self, t):
        values = [x*(1-t) + y*t for x, y in zip(self.n0[:3], self.n1[:3])]
        if self.size_slopes is not None:
            # Width and height ease through the nodes (see _ease_sizes).
            h00, h10 = 2*t**3 - 3*t*t + 1, t**3 - 2*t*t + t
            h01, h11 = -2*t**3 + 3*t*t, t**3 - t*t
            for axis, (m0, m1) in enumerate(self.size_slopes):
                values[axis] = max(EPS, h00*self.n0[axis] + h10*m0 +
                                   h01*self.n1[axis] + h11*m1)
        # The two axes describe the same ellipse after a 180-degree turn.
        # Interpolating normalized end angles directly (e.g. 179 -> 1)
        # would rotate through 90 degrees and make the outline swell midway.
        if self._modulation is not None:
            factor = self._modulation.factor(t)
            values[0] *= factor
            values[1] *= factor
        turn = (self.n1[3] - self.n0[3] + 90.0) % 180.0 - 90.0
        return tuple(values + [self.n0[3] + turn*t,
                               self.n0[4]*(1-t) + self.n1[4]*t])

    def extent(self, t):
        w, h, _, _, _ = self.nib(t)
        return max(w, h)

    def at(self, t):
        edges = self._edges.get(t)
        if edges is None:
            pts = self.pts
            p = add(mul(pts[0], 1-t), mul(pts[1], t)) if self.kind == 'line' else cubic(*pts, t)
            edges = self._edges[t] = self._limited(p, nib_edges(p, self.tangent(t),
                                                                self.nib(t)))
        return edges[0] if self.sign > 0 else edges[1]

    def _thickness_range(self):
        """(low, high, weight): the end thicknesses, and how far a segment whose
        nib size changes is held between them.

        A nib growing along a turning centerline can make the stroke thicker
        midway than at either end: the width axis grows while the stroke still
        runs across it, then the stroke turns onto the thinner height axis. A
        growing stroke should not swell and shrink again before its end. With
        an unchanged nib the turn alone shapes the thickness (round strokes
        thicken where they run vertically), so the limit fades in with the
        change of nib size.
        """
        limits = self._edges.get(None)
        if limits is None:
            ends = []
            for t in (0.0, 1.0):
                pts = self.pts
                p = add(mul(pts[0], 1-t), mul(pts[1], t)) if self.kind == 'line' \
                    else cubic(*pts, t)
                left, right = nib_edges(p, self.tangent(t), self.nib(t))
                ends.append(length(sub(left, right)))
            change = max(abs(a-b) / max(a, b, EPS)
                         for a, b in zip(self.n0[:2], self.n1[:2]))
            weight = min(1.0, change / THICKNESS_LIMIT_CHANGE)
            weight = weight*weight*(3.0 - 2.0*weight)
            limits = self._edges[None] = (min(ends), max(ends), weight)
        return limits

    def _limited(self, p, edges):
        if self.kind == 'line' or self._modulation is not None:
            return edges  # a width profile shapes the thickness on purpose
        low, high, weight = self._thickness_range()
        thickness = length(sub(edges[0], edges[1]))
        if weight <= 0 or thickness < EPS or low <= thickness <= high:
            return edges
        target = min(max(thickness, low), high)
        scale = 1.0 + weight*(target/thickness - 1.0)
        return tuple(add(p, mul(sub(edge, p), scale)) for edge in edges)

    def tangent(self, t):
        # The centerline's own direction: both sides and both neighbours of a smooth
        # node share it, so smooth centerline nodes stay smooth in the outline.
        return unit(_derivative(self.kind, self.pts, t))

    def edge_tangent(self, t):
        """Direction of the offset edge, including changing width and curvature."""
        step = max((self.tb - self.ta) * 1e-4, 1e-6)
        lo, hi = max(self.ta, t-step), min(self.tb, t+step)
        if hi <= lo:
            return self.tangent(t)
        direction = sub(self.at(hi), self.at(lo))
        return unit(direction) if length(direction) > EPS else self.tangent(t)

    def polyline(self, count=48):
        return [self.start] + [self.at(self.ta + (self.tb-self.ta)*i/float(count))
                               for i in range(1, count)] + [self.end]

    def piece(self):
        if self.kind == 'line':
            return ('line', (self.start, self.end))
        ta, tb = self.ta, self.tb
        p0, p3 = self.start, self.end
        chord = length(sub(p3, p0))
        d0, d1 = self.edge_tangent(ta), self.edge_tangent(tb)
        count = 16
        step = (tb-ta) / count
        # A corner join or angled cut can move an endpoint far from the true
        # offset curve. Its old tangent then points away from the first interior
        # sample and can force a loop or S-bend into the fitted cubic.
        if length(sub(p0, self.at(ta))) > 0.05:
            d0 = unit(sub(self.at(ta+step), p0))
        if length(sub(p3, self.at(tb))) > 0.05:
            d1 = unit(sub(p3, self.at(tb-step)))
        # Next to a fold (see _trim_folds) the edge all but stops and turns:
        # its direction at the end says little about where it goes. Then
        # head for a point a little further on.
        ahead, behind = unit(sub(self.at(ta+step), p0)), unit(sub(p3, self.at(tb-step)))
        if _dot(d0, ahead) < 0.9:
            d0 = ahead
        if _dot(d1, behind) < 0.9:
            d1 = behind
        # When the stroke is wider than the local radius of curvature, the
        # mathematical offset can turn backwards even though the centerline is
        # gentle. Keep the outline visually flowing forward in that case.
        source_chord = length(sub(self.pts[-1], self.pts[0]))
        source_hull = sum(length(sub(self.pts[i+1], self.pts[i])) for i in range(3))
        center0, center1 = self.tangent(ta), self.tangent(tb)
        gentle = (source_chord > EPS and length(sub(p0, self.at(ta))) < 0.05 and
                  length(sub(p3, self.at(tb))) < 0.05 and
                  source_hull < 1.4*source_chord and
                  center0[0]*center1[0] + center0[1]*center1[1] > 0.7)
        chord_direction = unit(sub(p3, p0))
        if gentle:
            for index, (edge, center) in enumerate(((d0, center0), (d1, center1))):
                if edge[0]*chord_direction[0] + edge[1]*chord_direction[1] < 0.5 or \
                        edge[0]*center[0] + edge[1]*center[1] < 0.2:
                    direction = unit(add(mul(center, 0.7), mul(chord_direction, 0.3)))
                    if index == 0:
                        d0 = direction
                    else:
                        d1 = direction
        us = [i/float(count) for i in range(1, count)]
        samples = [self.at(ta + (tb-ta)*u) for u in us]
        if self.target is not None:
            # A fold rounded off (see _round_chain_folds): fit to that edge.
            # Leaving out what a join has cut off since.
            kept = [(p, t) for p, t in self.target[1:-1]
                    if t is None or ta - 1e-9 <= t <= tb + 1e-9]
            path = [p0] + [p for p, _ in kept] + [p3]
            if chord < 1e-6 or len(path) < 3:
                return ('cubic', (p0, p0, p3, p3))
            first, last = self.target_dirs
            d0 = first if first is not None else unit(sub(path[1], p0))
            d1 = last if last is not None else unit(sub(p3, path[-2]))
            gentle = False
            run = [0.0]
            for a, b in zip(path, path[1:]):
                run.append(run[-1] + length(sub(b, a)))
            samples = path[1:-1]
            # Along the edge by its own parameter (as above), along a round
            # by its share of the way.
            by_way = [r / run[-1] for r in run[1:-1]]
            us = [min(max((t - ta) / (tb - ta) if t is not None and tb > ta else w, 1e-3),
                      1 - 1e-3) for (_, t), w in zip(kept, by_way)]
            for i in range(1, len(us)):
                us[i] = max(us[i], us[i-1])
        best = None
        for _ in range(4 if self.target is None else 10):
            controls = _handles(p0, p3, d0, d1, samples, us, chord)
            if gentle:
                alpha = min(max(length(sub(controls[1], p0)), 0.05*chord), 0.75*chord)
                beta = min(max(length(sub(p3, controls[2])), 0.05*chord), 0.75*chord)
                progress = (alpha*(d0[0]*chord_direction[0] + d0[1]*chord_direction[1]) +
                            beta*(d1[0]*chord_direction[0] + d1[1]*chord_direction[1]))
                if progress > 0.9*chord:
                    scale = 0.9*chord/progress
                    alpha, beta = alpha*scale, beta*scale
                controls = (p0, add(p0, mul(d0, alpha)),
                            sub(p3, mul(d1, beta)), p3)
            curve = [cubic(*controls, i/64.0) for i in range(65)]
            nearest = _nearest_along(samples, curve)
            error = max(min(_point_segment_distance(p, curve[j], curve[j+1])
                            for j in (i-1, i) if 0 <= j < 64) for p, i in zip(samples, nearest))
            if best is None or error < best[0]:
                best = (error, controls)
            if error <= FIT_TOLERANCE / 4.0:
                break  # already far below what anyone can see
            us = [min(max(i/64.0, 1e-3), 1-1e-3) for i in nearest]
        controls = best[1]
        # A true normal offset can cancel a gentle centerline's bend entirely.
        # For display outlines, retain some of that bend rather than producing
        # an almost straight edge beside a visibly curved centerline.
        hull_ratio = source_hull / source_chord if source_chord > EPS else float('inf')
        optical_weight = min(1.0, max(0.0, (1.55 - hull_ratio) / 0.30))
        optical_weight = optical_weight*optical_weight*(3.0 - 2.0*optical_weight)
        if (optical_weight > 0 and abs(ta) < EPS and abs(tb-1) < EPS and
                source_chord > EPS and center0[0]*center1[0] + center0[1]*center1[1] > 0.7):
            bend_normal = normal(unit(sub(self.pts[-1], self.pts[0])))
            source_mid = cubic(*self.pts, 0.5)
            source_bend = sum((source_mid[i] - (self.pts[0][i]+self.pts[-1][i])*0.5)
                              * bend_normal[i] for i in range(2))
            if abs(source_bend) > 2.0:
                mid = cubic(*controls, 0.5)
                fitted_bend = sum((mid[i] - (p0[i]+p3[i])*0.5)*bend_normal[i]
                                  for i in range(2))
                if source_bend*fitted_bend < 0.7*source_bend*source_bend:
                    offset0, offset1 = sub(p0, self.pts[0]), sub(p3, self.pts[-1])
                    shape = (p0,
                             add(self.pts[1], add(mul(offset0, 2/3), mul(offset1, 1/3))),
                             add(self.pts[2], add(mul(offset0, 1/3), mul(offset1, 2/3))),
                             p3)
                    shape_mid = cubic(*shape, 0.5)
                    shape_bend = sum((shape_mid[i] - (p0[i]+p3[i])*0.5)*bend_normal[i]
                                     for i in range(2))
                    difference = shape_bend-fitted_bend
                    if abs(difference) > EPS:
                        weight = min(1.0, max(0.0,
                            (0.7*source_bend-fitted_bend)/difference))
                        weight *= optical_weight
                        q1 = add(mul(controls[1], 1-weight), mul(shape[1], weight))
                        q2 = add(mul(controls[2], 1-weight), mul(shape[2], weight))
                        # Keep the control polygon moving along the edge chord.
                        u1 = sum((q1[i]-p0[i])*chord_direction[i] for i in range(2))
                        u2 = sum((q2[i]-p0[i])*chord_direction[i] for i in range(2))
                        v1 = min(chord, max(0.0, u1))
                        v2 = min(chord, max(v1, u2))
                        q1 = add(q1, mul(chord_direction, v1-u1))
                        q2 = add(q2, mul(chord_direction, v2-u2))
                        controls = (p0, q1, q2, p3)
        # Section tilt positions the two outline nodes, but must not tilt the
        # Bézier handles at the source node. Adjacent segments may have quite
        # different tilt gradients; using their offset derivatives here makes
        # an otherwise smooth centerline acquire a visible kink.
        p0, p1, p2, p3 = controls
        # A width profile turns the edge on purpose; _join matches its handles.
        modulated = self._modulation is not None
        if abs(ta) < EPS and ((self.smooth_start and not modulated) or abs(self.n0[3]) > EPS):
            handle = min(max(length(sub(p1, p0)), 0.05*chord), 0.75*chord)
            p1 = add(p0, mul(center0, handle))
        if abs(tb-1) < EPS and ((self.smooth_end and not modulated) or abs(self.n1[3]) > EPS):
            handle = min(max(length(sub(p3, p2)), 0.05*chord), 0.75*chord)
            p2 = sub(p3, mul(center1, handle))
        return ('cubic', (p0, p1, p2, p3))


def _turns(points):
    """Signed turning angle at each interior point of a polyline."""
    result = []
    for a, b, c in zip(points, points[1:], points[2:]):
        u, v = sub(b, a), sub(c, b)
        if length(u) > EPS and length(v) > EPS:
            result.append(math.atan2(u[0]*v[1] - u[1]*v[0], u[0]*v[0] + u[1]*v[1]))
    return result


def _fillet_side(side):
    """Apply _fillet_edge to a joined side whose ends still sit on its true
    offset (smooth joins and free ends). Corner joins and cuts have moved the
    ends, and their own extension keeps the edge in shape there."""
    if side.kind != 'cubic' or side.join_extension_ratio > 0.05:
        return
    controls = (side.override_piece or side.piece())[1]
    if length(sub(controls[0], side.at(side.ta))) > 1.0 or \
            length(sub(controls[-1], side.at(side.tb))) > 1.0:
        return
    samples = [side.at(side.ta + (side.tb-side.ta)*i/16.0) for i in range(1, 16)]
    filleted = _fillet_edge(side, controls, samples, side.tangent(side.ta),
                            side.tangent(side.tb))
    if filleted is not controls:
        side.override_piece = ('cubic', filleted)


def _crossing(p0, d0, p3, d1):
    """Distances (s0, s1) with p0 + d0*s0 == p3 - d1*s1, or None if parallel."""
    cross = d0[0]*d1[1] - d0[1]*d1[0]
    if abs(cross) < 1e-9:
        return None
    q = sub(p3, p0)
    return ((q[0]*d1[1] - q[1]*d1[0]) / cross, (d0[0]*q[1] - d0[1]*q[0]) / cross)


def _handle_shares(points):
    """How far each handle of a cubic reaches towards its tangent crossing (0..1)."""
    p0, p1, p2, p3 = points
    a, b = length(sub(p1, p0)), length(sub(p3, p2))
    if a < EPS or b < EPS:
        return None
    crossing = _crossing(p0, unit(sub(p1, p0)), p3, unit(sub(p3, p2)))
    if crossing is None or crossing[0] <= EPS or crossing[1] <= EPS:
        return None
    return (min(max(a / crossing[0], 0.15), 1.0), min(max(b / crossing[1], 0.15), 1.0))


def _fillet_edge(side, controls, samples, d0, d1):
    """Turn an edge that bends back on itself into one fillet between its tangents.

    Where the thickness changes faster than the stroke turns (a wide, flat nib
    going from a stem into a thin diagonal), the true offset (`samples`) or its
    fitted cubic bends the wrong way: the inner edge dips back past the stem
    before it turns, the outer edge flattens and swings out again. When the
    centerline turns one way only and the edge turns back by more than
    FILLET_REVERSE degrees, the edge becomes a fillet from the start tangent d0
    into the end tangent d1. Each handle reaches the same share of the way to
    the tangent crossing as the centerline's own handle does, so a tighter
    centerline bend gives a tighter outline bend. Outside the bend the fillet is
    drawn in until it keeps no further from the centerline than the thicker
    end, so the bend does not swell beyond the stroke width, and it is used
    only where it reaches further out than the fit.
    """
    p0, p1, p2, p3 = controls
    source = side.pts if side.ta < EPS and side.tb > 1-EPS else \
        _cubic_interval(side.pts, side.ta, side.tb)
    bends = _turns(list(source))
    if any(x > 1e-6 for x in bends) and any(x < -1e-6 for x in bends):
        return controls  # an S-shaped centerline
    turn = math.atan2(d0[0]*d1[1] - d0[1]*d1[0], d0[0]*d1[0] + d0[1]*d1[1])
    if not math.radians(5.0) < abs(turn) <= math.radians(FILLET_MAX_TURN):
        return controls
    fitted = [cubic(*controls, i/32.0) for i in range(33)]
    reverse = max(sum(abs(x) for x in _turns(points) if x*turn < 0)
                  for points in ([p0] + list(samples) + [p3], fitted))
    if math.degrees(reverse) < FILLET_REVERSE:
        return controls
    chord = length(sub(p3, p0))
    crossing = _crossing(p0, d0, p3, d1)
    if crossing is None:
        return controls
    s0, s1 = crossing
    if not (EPS < s0 <= 2*chord and EPS < s1 <= 2*chord):
        return controls
    k0, k1 = _handle_shares(source) or (FILLET_HANDLE, FILLET_HANDLE)

    def fillet(scale):
        return (p0, add(p0, mul(d0, scale*k0*s0)), sub(p3, mul(d1, scale*k1*s1)), p3)
    scale = 1.0
    if turn*side.sign < 0:  # outside of the bend
        center = [cubic(*source, i/48.0) for i in range(49)]
        limit = max(length(sub(p0, center[0])), length(sub(p3, center[-1])))

        def fits(value):  # the fillet's middle, kept within the limit
            return all(min(length(sub(cubic(*fillet(value), t), c)) for c in center) <= limit
                       for t in (0.25, 0.5, 0.75))
        if not fits(scale):
            lo, hi = 0.0, scale
            for _ in range(20):
                mid = (lo + hi) / 2.0
                lo, hi = (mid, hi) if fits(mid) else (lo, mid)
            scale = lo
        middle = mul(add(p0, p3), 0.5)
        outward = unit(sub(add(p0, mul(d0, s0)), middle))  # towards the tangent crossing

        def depth(points):
            v = sub(cubic(*points, 0.5), middle)
            return v[0]*outward[0] + v[1]*outward[1]
        if min(k0*s0, k1*s1)*scale < 0.2*chord or depth(fillet(scale)) <= depth(controls):
            return controls  # the fit already bends at least as far out
    return fillet(scale)


def _trim_folds(sides, closed, fold_rounds=None):
    """Where a side's edge runs backwards (inside a bend tighter than the
    stroke is wide the offset folds over itself), leave the fold out.
    Fitted together with its fold a side's cubic misses the true edge by
    many units.

    Next to a corner the side starts or ends where its edge runs forward
    again, and the join cuts the two sides where they really cross. Along
    sides joined at smooth nodes see _round_chain_folds."""
    total = len(sides)

    def smooth(a, b):
        return _dot(a.tangent(1.0), b.tangent(0.0)) > 0.995
    for k, side in enumerate(sides):
        if side.kind != 'cubic':
            continue
        previous = sides[k-1] if (closed or k > 0) and total > 1 else None
        following = sides[(k+1) % total] if (closed or k < total-1) and total > 1 else None

        def forward(t):
            step = 1e-4
            lo, hi = max(t - step, 0.0), min(t + step, 1.0)
            edge = sub(side.at(hi), side.at(lo))
            return _dot(edge, side.tangent(t))
        count = 64
        start = 0.0
        if (previous is None or not smooth(previous, side)) and \
                (forward(0.0) < 0 or forward(1.0 / count) < 0):
            i = 0
            while i < count // 2 and forward(i / float(count)) < 0:
                i += 1
            if forward(i / float(count)) > 0:
                lo, hi = max(i - 1, 0) / float(count), i / float(count)
                for _ in range(30):
                    middle = (lo + hi) / 2.0
                    if forward(middle) < 0:
                        lo = middle
                    else:
                        hi = middle
                start = hi
        end = 1.0
        if (following is None or not smooth(side, following)) and \
                (forward(1.0) < 0 or forward(1.0 - 1.0 / count) < 0):
            i = count
            while i > count // 2 and forward(i / float(count)) < 0:
                i -= 1
            if forward(i / float(count)) > 0:
                lo, hi = i / float(count), min(i + 1, count) / float(count)
                for _ in range(30):
                    middle = (lo + hi) / 2.0
                    if forward(middle) < 0:
                        hi = middle
                    else:
                        lo = middle
                end = lo
        if start > 0.0 and start < end:
            side.ta, side.start = start, side.at(start)
        if end < 1.0 and start < end:
            side.tb, side.end = end, side.at(end)
    _round_chain_folds(sides, closed, fold_rounds, smooth)


FOLD_SAMPLES = 48  # edge samples per side when looking for folds along a chain


def _round_chain_folds(sides, closed, fold_rounds, smooth):
    """Along sides joined at smooth nodes, cut each fold out of the edge
    (where it runs backwards) as offsetting a thin outline does: the edge
    turns a corner where its stretch before the fold meets its stretch
    after it. That corner is rounded off (the stroke a little thicker
    there), and a node within the round goes where the round heads that
    node's way, so it stays a smooth node upright to the centerline's
    handles. Everything here moves continuously with the design: the round
    grows from nothing as a fold begins.

    The sides it reshapes get `target`, the edge their cubic is fitted to,
    and `target_dirs`, its directions at their ends (see _Side.piece)."""
    total = len(sides)
    if total < 2:
        return
    # Chains: runs of cubic sides, each joined to the next at a smooth node.
    links = [sides[k].kind == 'cubic' and sides[(k+1) % total].kind == 'cubic' and
             (closed or k < total - 1) and smooth(sides[k], sides[(k+1) % total])
             for k in range(total)]
    chains, k = [], 0
    if closed and all(links):
        chains.append(list(range(total)))
    else:
        begin = 0
        if closed:
            # Start after a break so no chain wraps over the seam unseen.
            begin = next(i for i in range(total) if not links[i]) + 1
        visited = 0
        while visited < total:
            k = (begin + visited) % total
            chain = [k]
            visited += 1
            while links[chain[-1]] and visited < total and (closed or chain[-1] < total - 1):
                chain.append((chain[-1] + 1) % total)
                visited += 1
            if len(chain) > 1 and all(sides[i].kind == 'cubic' for i in chain):
                chains.append(chain)
    for chain in chains:
        _round_folds_along(sides, chain, fold_rounds)


def _round_folds_along(sides, chain, fold_rounds):
    count = FOLD_SAMPLES
    points, owners, nodes = [], [], []
    for m, k in enumerate(chain):
        side = sides[k]
        if m:
            nodes.append(len(points) - 1)
        for i in range(0 if m == 0 else 1, count + 1):
            t = side.ta + (side.tb - side.ta) * i / float(count)
            points.append(side.at(t))
            owners.append((m, t))
    last = len(points) - 1
    # How far each step runs on along the centerline: back where negative.
    onward = []
    for q in range(last):
        m, t = owners[q]
        _, t1 = owners[q+1]
        side = sides[chain[m]]
        middle = (t + t1) / 2.0 if owners[q+1][0] == m else t
        onward.append(_dot(sub(points[q+1], points[q]), side.tangent(middle)))
    runs, q = [], 0
    while q < last:
        if onward[q] < 0:
            start = q
            while q < last and onward[q] < 0:
                q += 1
            runs.append((start, q))  # steps start..q-1 run back
        else:
            q += 1

    def half_at(q):
        m, t = owners[q]
        side = sides[chain[m]]
        return length(sub(points[q], cubic(*side.pts, t)))

    def along(position, distance, step):
        """Walk `distance` from chain position `position` (a float index)
        towards the start (step -1) or end (step +1); the point and its
        position."""
        point = _chain_point(points, position)
        q = math.floor(position) if step > 0 else math.ceil(position)
        q = q + 1 if step > 0 else q - 1
        if step < 0 and abs(position - round(position)) < 1e-9:
            q = int(round(position)) - 1
        if step > 0 and abs(position - round(position)) < 1e-9:
            q = int(round(position)) + 1
        left = distance
        while 0 <= q <= last:
            gap = length(sub(points[q], point))
            if gap >= left and gap > EPS:
                f = left / gap
                spot = add(point, mul(sub(points[q], point), f))
                previous = q - step
                return spot, _chain_position(points, previous, q, spot)
            left -= gap
            point = points[q]
            q += step
        end = 0 if step < 0 else last
        return points[end], float(end)
    cuts = []
    for begin, finish in runs:
        cusp_in, cusp_out = begin, finish  # point indices around the fold
        back = -sum(onward[q] for q in range(begin, finish))
        half = max(half_at(cusp_in), EPS)
        if back < 1e-9 or cusp_in == 0 or cusp_out == last:
            continue
        corner = _fold_corner(points, cusp_in, cusp_out, half)
        if corner is None:
            continue
        point, before_at, after_at = corner
        # The round: a radius growing from nothing with the fold (how far
        # its edge runs back) and with the width, set back from the corner
        # by the turn, so sharp corners round as much as square ones.
        mid = (cusp_in + cusp_out) / 2.0
        node = min(nodes, key=lambda n: abs(n - mid)) if nodes else None
        rounding = 1.0
        if fold_rounds and node is not None:
            rounding = fold_rounds.get(chain[nodes.index(node) + 1], 1.0)
        depth = max(length(sub(point, points[cusp_in])), length(sub(point, points[cusp_out])))
        onset = min(1.0, back / max(0.05 * half, EPS))
        radius = rounding * FOLD_ROUNDING * math.sqrt(depth * half) * onset
        back_point, _ = along(before_at, max(radius, 1e-6), -1)
        on_point, _ = along(after_at, max(radius, 1e-6), 1)
        coming, going = sub(point, back_point), sub(on_point, point)
        if length(coming) > EPS and length(going) > EPS:
            turn = math.acos(max(-1.0, min(1.0, _dot(unit(coming), unit(going)))))
        else:
            turn = math.pi / 2.0
        setback = radius * math.tan(min(turn, math.radians(170)) / 2.0)
        room_back = _chain_length(points, 0.0, before_at) + length(sub(point, _chain_point(points, before_at)))
        room_on = _chain_length(points, after_at, float(last)) + length(sub(point, _chain_point(points, after_at)))
        setback = min(setback, 0.75 * room_back, 0.75 * room_on)
        first, first_at = _walk_from(points, point, before_at, setback, -1, along)
        final, final_at = _walk_from(points, point, after_at, setback, 1, along)
        cuts.append((back, (first, point, final, first_at, final_at)))
    # The deeper folds first; one within another's round is part of it.
    chosen = []
    for _, cut in sorted(cuts, key=lambda item: -item[0]):
        if all(cut[4] <= other[3] or cut[3] >= other[4] for other in chosen):
            chosen.append(cut)
    cuts = sorted(chosen, key=lambda cut: cut[3])
    if not cuts:
        return
    # The edge with each fold cut out and rounded; nodes within a round go
    # where it heads their way.
    placed = {}
    for first, corner, final, first_at, final_at in cuts:
        in_turn, out_turn = sub(corner, first), sub(final, corner)
        previous_split = 0.0
        for index, n in enumerate(nodes):
            if not first_at < n < final_at:
                continue
            way = sides[chain[index + 1]].tangent(sides[chain[index + 1]].ta)
            cross_in = in_turn[0]*way[1] - in_turn[1]*way[0]
            cross_out = out_turn[0]*way[1] - out_turn[1]*way[0]
            split = cross_in / (cross_in - cross_out) if abs(cross_in - cross_out) > EPS else 0.0
            split = min(max(split, 0.0), 1.0)
            # A node only just inside the round moves onto it gradually
            # from the round's end it is at, so it never jumps.
            inside_from = _chain_length(points, first_at, n)
            inside_to = _chain_length(points, n, final_at)
            ease = min(1.0, min(inside_from, inside_to) / max(0.25 * length(sub(final, first)), EPS))
            edge_split = 0.0 if inside_from < inside_to else 1.0
            split = edge_split + (split - edge_split) * ease
            split = min(max(split, previous_split), 1.0)
            previous_split = split
            placed[index] = (split, (first, corner, final))
    # Each side's stretch of the cleaned edge, from its start node to its end.
    ends = [0.0] + [float(n) for n in nodes] + [float(last)]
    for m, k in enumerate(chain):
        side = sides[k]
        lo, hi = ends[m], ends[m+1]
        touched = any(first_at < hi and final_at > lo for _, _, _, first_at, final_at in cuts)
        if not touched:
            continue
        # (point, the side's parameter there, or None on a round)
        def edge_points(lo_at, hi_at):
            return [(points[q], owners[q][1] if owners[q][0] == m else
                     (side.ta if q <= ends[m] else side.tb))
                    for q in range(int(math.floor(lo_at)) + 1, int(math.ceil(hi_at)))
                    if lo_at < q < hi_at]
        path = []
        start_point = _quadratic(*placed[m-1][1], placed[m-1][0]) if m and (m-1) in placed \
            else points[int(lo)]
        end_point = _quadratic(*placed[m][1], placed[m][0]) if m in placed \
            else points[int(hi)]
        path.append((start_point, None))
        position = lo
        for first, corner, final, first_at, final_at in cuts:
            if final_at <= lo or first_at >= hi:
                continue
            # Edge up to the round, then the round, within this side.
            s0 = placed[m-1][0] if m and (m-1) in placed and placed[m-1][1][1] == corner else 0.0
            s1 = placed[m][0] if m in placed and placed[m][1][1] == corner else 1.0
            if first_at > position:
                path += edge_points(position, first_at)
                path.append((first, None))
            path += [(_quadratic(first, corner, final, s0 + (s1 - s0) * i / 16.0), None)
                     for i in range(17)]
            position = final_at if s1 >= 1.0 else hi
        if position < hi:
            path.append((_chain_point(points, position), None))
            path += edge_points(position, hi)
        path.append((end_point, None))
        cleaned = [path[0]]
        for p, t in path[1:]:
            if length(sub(p, cleaned[-1][0])) > 1e-6:
                cleaned.append((p, t))
        if len(cleaned) < 2:
            cleaned = [(start_point, None), (end_point, None)]
        side.start, side.end = start_point, end_point
        side.target = cleaned
        # Smooth nodes keep the centerline's way; the chain's own ends the edge's.
        side.target_dirs = (side.tangent(side.ta) if m else side.edge_tangent(side.ta),
                            side.tangent(side.tb) if m < len(chain) - 1
                            else side.edge_tangent(side.tb))


def _chain_point(points, position):
    q = min(max(int(math.floor(position)), 0), len(points) - 1)
    f = position - q
    if f <= 1e-12 or q == len(points) - 1:
        return points[q]
    return add(points[q], mul(sub(points[q+1], points[q]), f))


def _chain_points(points, lo, hi):
    """The sample points strictly between chain positions `lo` and `hi`."""
    return [points[q] for q in range(int(math.floor(lo)) + 1, int(math.ceil(hi)))
            if lo < q < hi]


def _chain_length(points, lo, hi):
    if hi <= lo:
        return 0.0
    path = [_chain_point(points, lo)] + _chain_points(points, lo, hi) + [_chain_point(points, hi)]
    return sum(length(sub(b, a)) for a, b in zip(path, path[1:]))


def _chain_position(points, a, b, point):
    """The position of `point` on the step between sample indices a and b."""
    lo, hi = min(a, b), max(a, b)
    span = sub(points[hi], points[lo])
    size = _dot(span, span)
    f = 0.0 if size < EPS else min(max(_dot(sub(point, points[lo]), span) / size, 0.0), 1.0)
    return lo + f


def _walk_from(points, corner, position, distance, step, along):
    """From the corner (off the edge at `position` or on it) `distance` back
    (step -1) or on (step +1) along the edge."""
    reach = length(sub(_chain_point(points, position), corner))
    if distance <= reach:
        spot = _chain_point(points, position)
        f = distance / reach if reach > EPS else 0.0
        return add(corner, mul(sub(spot, corner), f)), position
    return along(position, distance - reach, step)


def _fold_corner(points, cusp_in, cusp_out, half):
    """Where the edge before the fold (points up to `cusp_in`) and the edge
    after it (from `cusp_out`) meet: where they cross, else where one,
    carried straight on from its end, meets the other, else where both
    carried on meet. (point, position before, position after): positions on
    the chain where the corner's edges leave it (the corner itself when on
    them)."""
    last = len(points) - 1
    window = 2 * FOLD_SAMPLES
    for i in range(cusp_in - 1, max(-1, cusp_in - 1 - window), -1):
        for j in range(cusp_out, min(last, cusp_out + window)):
            hit = _segment_intersection(points[i], points[i+1], points[j], points[j+1])
            if hit is not None:
                return hit[0], i + hit[1], j + hit[2]

    def heading(index, step):
        # The way the edge runs from a cusp, over a little stretch (right
        # past a fold it still curls round).
        target = 0.25 * half
        q, travelled = index, 0.0
        while 0 < q < last and travelled < target:
            travelled += length(sub(points[q + step], points[q]))
            q += step
        direction = sub(points[q], points[index])
        return unit(direction) if length(direction) > EPS else None
    into = heading(cusp_in, -1)   # from the cusp back along the edge before
    out_of = heading(cusp_out, 1)
    reach = 3.0 * half
    if out_of is not None:
        origin = points[cusp_out]
        tip = add(origin, mul(out_of, -reach))
        for i in range(cusp_in - 1, max(-1, cusp_in - 1 - window), -1):
            hit = _segment_intersection(origin, tip, points[i], points[i+1])
            if hit is not None:
                return hit[0], i + hit[2], float(cusp_out)
    if into is not None:
        origin = points[cusp_in]
        tip = add(origin, mul(into, -reach))
        for j in range(cusp_out, min(last, cusp_out + window)):
            hit = _segment_intersection(origin, tip, points[j], points[j+1])
            if hit is not None:
                return hit[0], float(cusp_in), j + hit[2]
    if into is not None and out_of is not None:
        hit = _segment_intersection(points[cusp_in], add(points[cusp_in], mul(into, -reach)),
                                    points[cusp_out], add(points[cusp_out], mul(out_of, -reach)))
        if hit is not None:
            return hit[0], float(cusp_in), float(cusp_out)
    return (mul(add(points[cusp_in], points[cusp_out]), 0.5), float(cusp_in), float(cusp_out))


def _quadratic(p0, p1, p2, t):
    return add(add(mul(p0, (1-t)*(1-t)), mul(p1, 2*t*(1-t))), mul(p2, t*t))


def _ease_sizes(sides, closed):
    """Let width and height change smoothly through the nodes. Changing them
    in a straight line along each segment makes the stroke's thickness turn
    sharply at a node it is thickest or thinnest at, and the edge there comes
    to a point (inside ぬ's loop, where the node is narrower than its
    neighbours). Each segment instead follows a cubic through its end sizes
    whose slope at a node is 0 where the size turns there and between its
    neighbours' slopes otherwise (no overshoot), so the sizes at the nodes
    stay what they are. A size that steps at a node (virtual steps), and the
    ends of an open path, keep the segment's own straight slope."""
    count = len(sides)

    def through(before, after):
        if before * after <= 0:
            return 0.0
        return 2.0 * before * after / (before + after)
    for k, side in enumerate(sides):
        if side.kind != 'cubic':
            continue
        previous = sides[k-1] if (closed or k > 0) and count > 1 else None
        following = sides[(k+1) % count] if (closed or k < count-1) and count > 1 else None
        slopes = []
        for axis in (0, 1):
            change = side.n1[axis] - side.n0[axis]
            start = end = change
            if previous is not None and abs(previous.n1[axis] - side.n0[axis]) < 1e-6:
                start = through(previous.n1[axis] - previous.n0[axis], change)
            if following is not None and abs(following.n0[axis] - side.n1[axis]) < 1e-6:
                end = through(change, following.n1[axis] - following.n0[axis])
            slopes.append((start, end))
        if any(abs(a - b) > 1e-9 for (a, b), axis in zip(slopes, (0, 1))
               for b in (side.n1[axis] - side.n0[axis],)):
            side.size_slopes = tuple(slopes)
            side._edges.clear()
            side.start, side.end = side.at(side.ta), side.at(side.tb)


def _side_pair(kind, pts, e0, e1, slant=0.0, modulation=None):
    """Left and right side of one centerline segment, sharing their samples."""
    shared = {}
    return (_Side(kind, pts, e0, e1, 1, slant, shared, modulation),
            _Side(kind, pts, e0, e1, -1, slant, shared, modulation))


class _Modulation(object):
    """A width profile over one segment: (profile points, start, end), where start
    and end are the segment's share of the stroke's length (0-1). The segment's end
    nibs already carry the profile there; between them the straight blend of the
    nibs is scaled to follow the profile along the segment's length."""

    def __init__(self, kind, pts, modulation):
        points, self.p0, self.p1 = modulation
        self.profile = {'points': points}
        self.f0 = width_profile.evaluate(self.profile, self.p0*100.0)
        self.f1 = width_profile.evaluate(self.profile, self.p1*100.0)
        samples = [add(mul(pts[0], 1-i/32.0), mul(pts[1], i/32.0)) if kind == 'line'
                   else cubic(*pts, i/32.0) for i in range(33)]
        self.lengths = [0.0]
        for a, b in zip(samples, samples[1:]):
            self.lengths.append(self.lengths[-1] + length(sub(b, a)))

    def factor(self, t):
        position = min(32.0, max(0.0, t*32.0))
        index = min(31, int(position))
        distance = self.lengths[index] + (self.lengths[index+1]-self.lengths[index]) * \
            (position-index)
        share = distance/self.lengths[-1] if self.lengths[-1] > EPS else t
        value = width_profile.evaluate(self.profile,
                                       (self.p0 + (self.p1-self.p0)*share)*100.0)
        return value / max(self.f0*(1-t) + self.f1*t, EPS)


def _nearest_along(samples, curve, window=24):
    """Index of the closest curve point for each sample. Samples run along the
    curve in order, so each search starts just before the previous match."""
    result, start, last = [], 0, len(curve)
    for x, y in samples:
        best_i, best_d = start, float('inf')
        for i in range(start, min(last, start + window)):
            dx, dy = curve[i][0] - x, curve[i][1] - y
            d = dx*dx + dy*dy
            if d < best_d:
                best_i, best_d = i, d
        result.append(best_i)
        start = max(0, best_i - 4)
    return result


def _handles(p0, p3, d0, d1, samples, us, chord):
    """Least-squares handle lengths along fixed end tangents, kept non-negative."""
    c00 = c01 = c11 = x0 = x1 = 0.0
    for p, u in zip(samples, us):
        v = 1-u
        a0, a1 = mul(d0, 3*v*v*u), mul(d1, -3*v*u*u)
        residual = sub(p, add(mul(p0, v*v*v+3*v*v*u), mul(p3, 3*v*u*u+u*u*u)))
        c00 += a0[0]*a0[0]+a0[1]*a0[1]
        c01 += a0[0]*a1[0]+a0[1]*a1[1]
        c11 += a1[0]*a1[0]+a1[1]*a1[1]
        x0 += a0[0]*residual[0]+a0[1]*residual[1]
        x1 += a1[0]*residual[0]+a1[1]*residual[1]
    determinant = c00*c11 - c01*c01
    alpha = beta = chord/3.0
    if abs(determinant) > EPS:
        alpha = (x0*c11 - x1*c01) / determinant
        beta = (c00*x1 - c01*x0) / determinant
    limit = max(chord, EPS) * 3.0
    alpha = min(max(alpha, 0.0), limit)
    beta = min(max(beta, 0.0), limit)
    return (p0, add(p0, mul(d0, alpha)), sub(p3, mul(d1, beta)), p3)


def _segment_intersection(a, b, c, d):
    r, s = sub(b, a), sub(d, c)
    denominator = r[0]*s[1] - r[1]*s[0]
    if abs(denominator) < EPS:
        return None
    q = sub(c, a)
    t = (q[0]*s[1] - q[1]*s[0]) / denominator
    u = (q[0]*r[1] - q[1]*r[0]) / denominator
    if -EPS <= t <= 1+EPS and -EPS <= u <= 1+EPS:
        return add(a, mul(r, t)), t, u
    return None


def _curve_line_join(a, b, reach):
    """Join a cubic and a line at their actual intersection, continuing the cubic.

    The cubic's existing interval stays exactly the same polynomial. Only its
    parameter interval changes, so the corner cannot pull a handle inward.
    """
    curve, line = (a, b) if a.kind == 'cubic' else (b, a)
    at_end = curve is a
    edge = 1.0 if at_end else 0.0
    controls = (curve.override_piece or curve.piece())[1]
    origin, direction = line.start, unit(sub(line.end, line.start))
    if length(sub(line.end, line.start)) < EPS:
        return False

    def signed(t):
        delta = sub(cubic(*controls, t), origin)
        return delta[0]*direction[1] - delta[1]*direction[0]

    original = cubic(*controls, edge)
    line_end = line.start if at_end else line.end
    initial = unit(cubic_derivative(*controls, edge))
    choices = []
    for step, count in ((-1.0 if at_end else 1.0, 32),
                        (1.0 if at_end else -1.0, 16)):
        previous_t, previous = edge, signed(edge)
        for i in range(1, count+1):
            t = edge + step*i/32.0
            current = signed(t)
            if previous == 0 or (previous > 0) != (current > 0):
                lo, hi = previous_t, t
                for _ in range(24):
                    mid = (lo+hi)/2.0
                    if (signed(mid) > 0) == (previous > 0):
                        lo = mid
                    else:
                        hi = mid
                hit_t = (lo+hi)/2.0
                hit = cubic(*controls, hit_t)
                curve_distance = length(sub(hit, original))
                line_distance = length(sub(hit, line_end))
                if curve_distance <= reach and line_distance <= reach:
                    tangent = unit(cubic_derivative(*controls, hit_t))
                    if 0.0 <= hit_t <= 1.0 or \
                            tangent[0]*initial[0] + tangent[1]*initial[1] > 0.85:
                        choices.append((curve_distance+line_distance, hit_t, hit))
                break  # nearest crossing in this direction
            previous_t, previous = t, current
    if not choices:
        return False
    _, hit_t, hit = min(choices)
    interval = (0.0, hit_t) if at_end else (hit_t, 1.0)
    curve.override_piece = ('cubic', _cubic_interval(controls, *interval))
    if at_end:
        curve.end = line.start = hit
    else:
        curve.start = line.end = hit
    return True


def _join(a, b, width):
    """Make side `a` end and side `b` start on one shared point at a centerline node.

    The point moves continuously with the design, so dragging a corner never
    makes the stroke jump:
      * smooth nodes already meet;
      * near the corner, the sides (extended along their end tangents) meet at
        their crossing: the trimmed overlap inside, the miter outside;
      * further away that crossing blends smoothly into the plain tangent-line
        crossing, and the result is pulled in so it never lies further than
        MITER_LIMIT half-widths from the node.
    The corner is a single node per side either way.
    """
    incoming, outgoing = a.tangent(a.tb), b.tangent(b.ta)
    aligned = incoming[0]*outgoing[0] + incoming[1]*outgoing[1] > 0.995
    gap = length(sub(a.end, b.start))
    if aligned and gap < max(0.05, width*0.01):
        a_piece = a.override_piece or a.piece()
        b_piece = b.override_piece or b.piece()
        point = mul(add(a.end, b.start), 0.5)
        direction = unit(add(incoming, outgoing))
        if a._modulation is not None or b._modulation is not None:
            # Width profiles turn the edges: share the edges' own direction.
            edge = add(a.edge_tangent(a.tb), b.edge_tangent(b.ta))
            if length(edge) > EPS:
                direction = unit(edge)
        a.end = b.start = point
        # A line side's piece is just start..end. Freezing it here would keep
        # its far end even after the next corner join moves that end.
        if a.kind == 'cubic':
            p0, p1, p2, p3 = a_piece[1]
            handle = length(sub(p3, p2))
            a.override_piece = ('cubic', (p0, p1, sub(point, mul(direction, handle)), point))
        if b.kind == 'cubic':
            p0, p1, p2, p3 = b_piece[1]
            handle = length(sub(p1, p0))
            b.override_piece = ('cubic', (point, add(point, mul(direction, handle)), p2, p3))
        a.smooth_end = b.smooth_start = True
        return
    if gap < 0.05:
        b.start = a.end
        return
    reach = MITER_LIMIT * width / 2.0
    ta, tb = a.tangent(a.tb), b.tangent(b.ta)
    bend = abs(ta[0]*tb[1] - ta[1]*tb[0])
    if a.kind != b.kind and bend > NEAR_STRAIGHT and _curve_line_join(a, b, reach):
        return
    middle = mul(add(a.end, b.start), 0.5)
    denominator = ta[0]*tb[1] - ta[1]*tb[0]
    if abs(denominator) < 1e-9:
        tangent_point = middle
    else:
        q = sub(b.start, a.end)
        tangent_point = add(a.end, mul(ta, (q[0]*tb[1] - q[1]*tb[0]) / denominator))
    point, a_tb, b_ta = tangent_point, a.tb, b.ta
    # Inside a sharp corner the sides cross far from the node: they are cut
    # where they really cross, however far that is (see `inner` below).
    inner = None
    if a.kind == 'line' and b.kind == 'line':
        back, on = -_dot(sub(point, a.end), ta), _dot(sub(point, b.start), tb)
        a_size, b_size = length(sub(a.end, a.start)), length(sub(b.end, b.start))
        if back > 0 and on > 0 and a_size > EPS and b_size > EPS:
            # A crossing past a side's far end swallows that side: it stops there.
            if on >= b_size:
                inner = (b.end, a.tb, b.tb)
            elif back >= a_size:
                inner = (a.start, a.ta, b.ta)
            else:
                inner = (point, a.tb, b.ta)
    if a.kind == 'cubic' or b.kind == 'cubic':
        crossing = _side_crossing(a, b, ta, tb, reach)
        if crossing is None or crossing[0] > reach:
            span = sum(length(sub(q, p)) for side in (a, b)
                       for p, q in zip(side.polyline(8), side.polyline(8)[1:]))
            far = _side_crossing(a, b, ta, tb, span)
            if far is not None:
                far = _refined_crossing(a, b, far)
            if far is not None and far[2] < a.tb - 1e-6 and far[3] > b.ta + 1e-6:
                inner = (far[1], far[2], far[3])
        if crossing is not None:
            cost, hit, hit_a_tb, hit_b_ta = crossing
            # 0 within `reach` along the sides, 1 at twice that: blend, don't switch.
            w = min(max((cost - reach) / reach, 0.0), 1.0)
            point = add(mul(hit, 1-w), mul(tangent_point, w))
            a_tb = hit_a_tb*(1-w) + a.tb*w
            b_ta = hit_b_ta*(1-w) + b.ta*w
    # Nearly straight through the node the sides are almost parallel and any
    # crossing is ill-defined, so fade towards the plain middle instead.
    fade = min(abs(denominator) / NEAR_STRAIGHT, 1.0)
    point = add(middle, mul(sub(point, middle), fade))
    a_tb = a.tb + (a_tb - a.tb) * fade
    b_ta = b.ta + (b_ta - b.ta) * fade
    distance = length(sub(point, middle))
    limit = MITER_FAR_LIMIT * width / 2.0 if a.kind != b.kind else reach
    if distance > limit:
        point = add(middle, mul(sub(point, middle), limit / distance))
    if inner is not None:
        point, a_tb, b_ta = inner
    # Sides nothing has reshaped yet can simply be fitted again over what is
    # left of them once cut (see below).
    a_fresh, b_fresh = a.override_piece is None, b.override_piece is None
    a_controls = (a.override_piece or a.piece())[1] if a.kind == 'cubic' else None
    b_controls = (b.override_piece or b.piece())[1] if b.kind == 'cubic' else None
    a_trim = ((a_tb-a.ta)/(a.tb-a.ta) if a_controls is not None and a.tb-a.ta > EPS
              else 1.0)
    b_trim = ((b_ta-b.ta)/(b.tb-b.ta) if b_controls is not None and b.tb-b.ta > EPS
              else 0.0)
    # The true crossing inside a corner is on both sides: cut them there.
    exact = inner is not None and length(sub(point, inner[0])) < max(0.5, 0.01*width)
    a_on_curve = (a_controls is not None and a_trim < 1-1e-4 and
                  (exact or length(sub(a.at(a_tb), point)) < max(0.5, 0.01*width)))
    b_on_curve = (b_controls is not None and b_trim > 1e-4 and
                  (exact or length(sub(b.at(b_ta), point)) < max(0.5, 0.01*width)))
    if a.kind != b.kind and not \
            (a_on_curve or b_on_curve):
        # Only a miter built from tangent extensions needs a visual bound.
        # A true intersection on the offset curve must stay where it is.
        center = mul(add(a.pts[-1], b.pts[0]), 0.5)
        nib_width = max(a.n1[0]*(1+abs(a.n1[2])),
                        b.n0[0]*(1+abs(b.n0[2]))) / 2.0
        nib_height = max(a.n1[1]*(1+abs(a.n1[2])),
                         b.n0[1]*(1+abs(b.n0[2]))) / 2.0
        x_limit = max(1.35*nib_width, abs(a.end[0]-center[0]),
                      abs(b.start[0]-center[0]))
        y_limit = max(1.35*nib_height, abs(a.end[1]-center[1]),
                      abs(b.start[1]-center[1]))
        bounded = (min(center[0]+x_limit, max(center[0]-x_limit, point[0])),
                   min(center[1]+y_limit, max(center[1]-y_limit, point[1])))
        # A sharp corner's miter is far outside that bound, and pulling it in
        # takes an edge off its own direction (the stroke leaves its
        # centerline): past half a nib of pulling, the true miter takes over.
        pulled = length(sub(point, bounded)) / max(max(nib_width, nib_height), EPS)
        weight = min(max(pulled - 1.0, 0.0), 1.0)
        weight = weight*weight*(3-2*weight)
        point = add(mul(bounded, 1-weight), mul(point, weight))
    a.tb, b.ta = a_tb, b_ta
    a.end = b.start = point
    if exact:
        # The side's own curve runs a little off its centerline parameter on a
        # thick stroke: cut it where it passes nearest the crossing.
        if a_controls is not None:
            a_trim = _nearest_parameter(a_controls, point, a_trim)
        if b_controls is not None:
            b_trim = _nearest_parameter(b_controls, point, b_trim)
    if exact:
        # Cut at the true crossing: a side's cubic fitted over the whole
        # segment (with an edge that may have run back near the node) is cut
        # less well than one fitted over what is left; refit those.
        if a_fresh:
            a_controls = None
        if b_fresh:
            b_controls = None
    if a_controls is not None:
        if a_on_curve:
            part = _cubic_interval(a_controls, 0.0, a_trim)
            delta = sub(point, part[-1])
            controls = (part[0], part[1], add(part[2], delta), point)
        else:
            controls = _bend_join_endpoint(a_controls, True, point)
            direction = unit(sub(a_controls[-1], a_controls[-2]))
            delta = sub(point, a_controls[-1])
            if delta[0]*direction[0] + delta[1]*direction[1] > 0:
                a.join_extension_ratio = max(a.join_extension_ratio,
                    length(delta)/max(length(sub(a_controls[-1], a_controls[0])), EPS))
        a.override_piece = ('cubic', controls)
    if b_controls is not None:
        if b_on_curve:
            part = _cubic_interval(b_controls, b_trim, 1.0)
            delta = sub(point, part[0])
            controls = (point, add(part[1], delta), part[2], part[3])
        else:
            controls = _bend_join_endpoint(b_controls, False, point)
            direction = unit(sub(b_controls[1], b_controls[0]))
            delta = sub(point, b_controls[0])
            if delta[0]*direction[0] + delta[1]*direction[1] < 0:
                b.join_extension_ratio = max(b.join_extension_ratio,
                    length(delta)/max(length(sub(b_controls[-1], b_controls[0])), EPS))
        b.override_piece = ('cubic', controls)


def _side_crossing(a, b, ta, tb, reach):
    """Where side a (extended past its end) crosses side b (extended before its
    start), measured along the sides from the corner: (cost, point, a.tb, b.ta).
    Only crossings within 2*reach along the sides count."""
    # Curves are already smooth here; 24 chords locate the overlap to well below
    # an outline unit in ordinary glyph geometry while halving the expensive
    # offset evaluations made for every neighbouring pair during live editing.
    count = 24
    extension = reach * 2
    pa = a.polyline(count) + [add(a.end, mul(ta, extension))]
    pb = [sub(b.start, mul(tb, extension))] + b.polyline(count)
    lengths_a = [length(sub(pa[i+1], pa[i])) for i in range(len(pa)-1)]
    lengths_b = [length(sub(pb[j+1], pb[j])) for j in range(len(pb)-1)]
    tail_a = [0.0] * (len(lengths_a) + 1)  # length from the end of piece i to a's end
    for i in range(count-1, -1, -1):
        tail_a[i] = tail_a[i+1] + (lengths_a[i+1] if i+1 < count else 0.0)
    head_b = [0.0] * (len(lengths_b) + 1)  # length from b's start to the start of piece j
    for j in range(2, len(lengths_b)+1):
        head_b[j] = head_b[j-1] + lengths_b[j-1]
    limit = 2 * reach
    boxes_b = [(min(pb[j][0], pb[j+1][0]), max(pb[j][0], pb[j+1][0]),
                min(pb[j][1], pb[j+1][1]), max(pb[j][1], pb[j+1][1])) for j in range(len(pb)-1)]
    best = None
    for i in range(len(pa)-1):
        x0, x1 = min(pa[i][0], pa[i+1][0]), max(pa[i][0], pa[i+1][0])
        y0, y1 = min(pa[i][1], pa[i+1][1]), max(pa[i][1], pa[i+1][1])
        for j, (bx0, bx1, by0, by1) in enumerate(boxes_b):
            if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
                continue
            hit = _segment_intersection(pa[i], pa[i+1], pb[j], pb[j+1])
            if hit is None:
                continue
            point, t, u = hit
            cost_a = t * lengths_a[i] if i == count else tail_a[i] + (1-t) * lengths_a[i]
            cost_b = (1-u) * lengths_b[j] if j == 0 else head_b[j] + u * lengths_b[j]
            cost = cost_a + cost_b
            if cost > limit or (best is not None and cost >= best[0]):
                continue
            a_tb = a.ta + (a.tb-a.ta) * (i+t) / count if i < count else a.tb
            b_ta = b.ta + (b.tb-b.ta) * (j-1+u) / count if j >= 1 else b.ta
            best = (cost, point, a_tb, b_ta)
    return best


def _nearest_parameter(controls, point, guess):
    """The parameter of the cubic `controls` nearest to `point`, from `guess`."""
    best = min((i / 64.0 for i in range(65)),
               key=lambda t: length(sub(cubic(*controls, t), point)))
    if abs(best - guess) > 0.25:
        best = guess if length(sub(cubic(*controls, guess), point)) < \
            length(sub(cubic(*controls, best), point)) else best
    t = best
    for _ in range(12):
        d = cubic_derivative(*controls, t)
        q = sub(cubic(*controls, t), point)
        dd = d[0]*d[0] + d[1]*d[1]
        if dd < EPS:
            break
        t = min(max(t - (q[0]*d[0] + q[1]*d[1]) / dd, 0.0), 1.0)
    return t


def _refined_crossing(a, b, crossing):
    """A crossing of sides a and b found on their polylines (see
    _side_crossing), moved onto the sides themselves by Newton steps on their
    parameters. Thick strokes' sides bend away from their chords by units."""
    cost, point, s, t = crossing
    for _ in range(8):
        pa, pb = a.at(s), b.at(t)
        f = sub(pa, pb)
        if length(f) < 1e-6:
            break
        step = 1e-4
        da = mul(sub(a.at(min(s + step, 1.0)), a.at(max(s - step, 0.0))), 0.5 / step)
        db = mul(sub(b.at(min(t + step, 1.0)), b.at(max(t - step, 0.0))), 0.5 / step)
        determinant = -da[0]*db[1] + da[1]*db[0]
        if abs(determinant) < EPS:
            break
        s -= (-f[0]*db[1] + f[1]*db[0]) / determinant
        t -= (da[0]*f[1] - da[1]*f[0]) / determinant
        s, t = min(max(s, a.ta), a.tb), min(max(t, b.ta), b.tb)
    return (cost, mul(add(a.at(s), b.at(t)), 0.5), s, t)


def _side_contour(sides, widths, closed, joined=False, breaks=(), zero=()):
    """The pieces of one side. After side k, a break (k in `breaks`) adds the
    line to the next side's start; `zero` adds a line of no length where the
    sides were joined (a bevel in another master, see outline_curves)."""
    count = len(sides)
    breaks = set(breaks)
    if not joined:
        for k in range(count if closed else count-1):
            if k not in breaks:
                _join(sides[k], sides[(k+1) % count], widths[k])
    result = []
    for k, side in enumerate(sides):
        result.append(side.override_piece or side.piece())
        if k in breaks and (closed or k < count-1):
            result.append(('line', (side.end, sides[(k+1) % count].start)))
        elif k in zero and (closed or k < count-1):
            result.append(('line', (side.end, side.end)))
    return result


def _bevel_breaks(lefts, rights, node_of, bevels, closed):
    """Joins to cut on the outer side: ({side: {k: angle, ('clip', limit) or
    None}}, {side: set of k}) for 'bevel' (or ('bevel', page angle of the
    cut)), ('clip', limit) (a miter cut off `limit` half-widths out, see
    _clipped_miter) and for 'zero' (a cut in another master), k being the
    join of side k and side k+1; `node_of(sides, k)` names its node in
    `bevels`."""
    cut = {'left': {}, 'right': {}}
    zero = {'left': set(), 'right': set()}
    if not bevels:
        return cut, zero
    for name, sides in (('left', lefts), ('right', rights)):
        n = len(sides)
        for k in range(n if closed else n-1):
            node = node_of(sides, k)
            if node is None:
                continue
            if closed:
                node %= len(bevels)
            if not 0 <= node < len(bevels) or not bevels[node]:
                continue
            # The left side is outside of a right turn, the right side of a left one.
            turns_left = _turns_left(sides[k], sides[(k+1) % n])
            if turns_left != (name == 'right'):
                continue
            spec = bevels[node]
            if spec == 'zero':
                zero[name].add(k)
            elif isinstance(spec, (tuple, list)) and spec and spec[0] == 'clip':
                cut[name][k] = ('clip', float(spec[1]))
            else:
                angle = spec[1] if isinstance(spec, (tuple, list)) and len(spec) > 1 else None
                cut[name][k] = None if angle is None else float(angle)
    return cut, zero


def _apply_bevel_angles(sides, widths, cuts):
    for k, angle in cuts.items():
        if isinstance(angle, tuple):
            _clipped_miter(sides[k], sides[(k+1) % len(sides)], angle[1], widths[k])
        elif angle is not None:
            _angled_bevel(sides[k], sides[(k+1) % len(sides)], angle, widths[k])


def _clipped_miter(a, b, limit, width):
    """A miter cut off `limit` half-widths out from the node (as in Affinity
    Designer): both edges go straight on along their end tangents, to their
    crossing where that is nearer, else to the cut across the corner's middle
    direction there, which the cut line (of no size at a full point) joins.
    0 is a bevel; the edges are never taken back."""
    node = mul(add(a.pts[-1], b.pts[0]), 0.5)
    ta, tb = a.tangent(a.tb), b.tangent(b.ta)
    out = sub(ta, tb)  # the incoming side runs on, the outgoing one back
    if length(out) < EPS:
        return
    out = unit(out)
    distance = max(float(limit), 0.0) * width / 2.0
    ends = []
    for origin, tangent in ((a.end, ta), (b.start, mul(tb, -1.0))):
        along = _dot(tangent, out)
        if along < 0.05:
            ends.append(origin)  # running away from the cut: keep the bevel's end
            continue
        reach = (distance - _dot(sub(origin, node), out)) / along
        ends.append(add(origin, mul(tangent, max(reach, 0.0))))
    # Where the edges cross before the cut, they meet there: a full point.
    denominator = ta[0]*tb[1] - ta[1]*tb[0]
    if abs(denominator) > 1e-9:
        q = sub(b.start, a.end)
        s = (q[0]*tb[1] - q[1]*tb[0]) / denominator
        u = (q[0]*ta[1] - q[1]*ta[0]) / denominator
        miter = add(a.end, mul(ta, s))
        if s >= 0 and u <= 0 and _dot(sub(miter, node), out) <= distance:
            ends = [miter, miter]
    for side, at_end, point in ((a, True, ends[0]), (b, False, ends[1])):
        if length(sub(point, side.end if at_end else side.start)) < 1e-9:
            continue
        if side.kind == 'cubic':
            # Straight on along the end tangent, the curve itself kept: bending
            # the curve to reach the point would swell the stroke.
            controls = (side.override_piece or side.piece())[1]
            straight = _straight_extension(controls, at_end, point)
            side.override_piece = ('cubic', straight if straight is not None else
                                   _bend_join_endpoint(controls, at_end, point))
        if at_end:
            side.end = point
        else:
            side.start = point


def _angled_bevel(a, b, angle, width):
    """Turn the bevel between side a's end and side b's start to the page
    `angle` (degrees), about the middle of the automatic cut: each edge goes
    straight on along its end tangent to the cut line (or back from it).
    Cuts nearly along an edge, or reaching too far, keep the automatic cut."""
    radians = math.radians(angle)
    direction = (math.cos(radians), math.sin(radians))
    middle = mul(add(a.end, b.start), 0.5)
    reach = MITER_FAR_LIMIT * width / 2.0

    def hit(origin, tangent):
        denominator = direction[0]*tangent[1] - direction[1]*tangent[0]
        if abs(denominator) < 0.1:
            return None
        q = sub(origin, middle)
        along = (q[0]*direction[1] - q[1]*direction[0]) / denominator
        return None if abs(along) > reach else add(origin, mul(tangent, along))
    end = hit(a.end, a.tangent(a.tb))
    start = hit(b.start, b.tangent(b.ta))
    if end is None or start is None:
        return
    for side, at_end, point in ((a, True, end), (b, False, start)):
        if side.kind == 'cubic':
            controls = (side.override_piece or side.piece())[1]
            side.override_piece = ('cubic', _bend_join_endpoint(controls, at_end, point))
        if at_end:
            side.end = point
        else:
            side.start = point


def _smooth_virtual_join(a, b):
    """Use matched cubic handles at a continuous width control on a line."""
    if a.kind != 'line' or b.kind != 'line':
        return
    p0, middle, p3 = a.start, a.end, b.end
    first, second = sub(middle, p0), sub(p3, middle)
    first_length, second_length = length(first), length(second)
    if first_length < EPS or second_length < EPS:
        return
    d0, d1 = unit(first), unit(second)
    shared = add(d0, d1)
    if length(shared) < 0.2:
        return
    shared = unit(shared)
    handle = min(first_length, second_length) / 3.0
    # In a run of controls (a width profile) each line is joined at both ends:
    # keep the handle the other join already gave it, in whichever order.
    start = a.override_piece[1][1] if a.override_piece and \
        a.override_piece[0] == 'cubic' else add(p0, mul(d0, first_length/3.0))
    end = b.override_piece[1][2] if b.override_piece and \
        b.override_piece[0] == 'cubic' else sub(p3, mul(d1, second_length/3.0))
    a.override_piece = ('cubic', (p0, start, sub(middle, mul(shared, handle)), middle))
    b.override_piece = ('cubic', (middle, add(middle, mul(shared, handle)), end, p3))


def _normalized_bend(points):
    chord = sub(points[-1], points[0])
    distance = length(chord)
    if distance < EPS:
        return 0.0
    middle = cubic(*points, 0.5)
    midpoint = mul(add(points[0], points[-1]), 0.5)
    offset = sub(middle, midpoint)
    direction = normal(unit(chord))
    return (offset[0]*direction[0] + offset[1]*direction[1]) / distance


def _optical_outer_curve(side):
    """Give an extended outer edge the visual bend of its source stroke."""
    if side.kind != 'cubic' or side.join_extension_ratio <= 0.05:
        return
    source = side.pts
    start, end = side.tangent(0.0), side.tangent(1.0)
    turn = start[0]*end[1] - start[1]*end[0]
    if turn*side.sign <= 0 or start[0]*end[0] + start[1]*end[1] < 0.7:
        return
    source_chord = length(sub(source[-1], source[0]))
    hull = sum(length(sub(source[i+1], source[i])) for i in range(3))
    if source_chord < EPS or hull > 1.4*source_chord:
        return
    source_bend = _normalized_bend(source)
    controls = (side.override_piece or side.piece())[1]
    actual_bend = _normalized_bend(controls)
    if source_bend*actual_bend <= 0 or abs(actual_bend) >= 1.25*abs(source_bend):
        return
    progress = min(1.0, (side.join_extension_ratio-0.05)/0.2)
    progress = progress*progress*(3-2*progress)
    scale = 1 + progress*(min(3.0, 1.25*abs(source_bend/actual_bend))-1)
    p0, p1, p2, p3 = controls
    chord = sub(p3, p0)
    direction = unit(chord)
    for _ in range(5):
        q1 = add(p0, mul(sub(p1, p0), scale))
        q2 = sub(p3, mul(sub(p3, p2), scale))
        u1 = sub(q1, p0)[0]*direction[0] + sub(q1, p0)[1]*direction[1]
        u2 = sub(q2, p0)[0]*direction[0] + sub(q2, p0)[1]*direction[1]
        if 0 <= u1 <= u2 <= length(chord):
            side.override_piece = ('cubic', (p0, q1, q2, p3))
            return
        scale = 1 + (scale-1)*0.5


def _reverse(pieces):
    return [(kind, tuple(reversed(points))) for kind, points in reversed(pieces)]


def _cubic_interval(controls, t0, t1):
    """Exactly reparameterize a cubic, including a short interval beyond 0..1."""
    p0, p3 = cubic(*controls, t0), cubic(*controls, t1)
    span = (t1-t0)/3.0
    return (p0, add(p0, mul(cubic_derivative(*controls, t0), span)),
            sub(p3, mul(cubic_derivative(*controls, t1), span)), p3)


def _bend_curve_endpoint(controls, at_end, target):
    """Keep a requested cut when an exact cubic continuation turns away from it.

    While the end handle still points along the chord, it keeps its length (up
    to 3/4 of the chord): shortening a long handle on a gently curving edge
    pulls the whole edge inwards. A handle turned onto the chord is kept short.
    """
    p0, p1, p2, p3 = controls
    if at_end:
        chord = sub(target, p0)
        direction = unit(sub(p3, p2))
        limit = 0.75
        if direction[0]*chord[0] + direction[1]*chord[1] < 0.5*length(chord):
            direction, limit = unit(chord), 0.4
        handle = min(max(length(sub(p3, p2)), 0.08*length(chord)),
                     limit*length(chord))
        return (p0, p1, sub(target, mul(direction, handle)), target)
    chord = sub(p3, target)
    direction = unit(sub(p1, p0))
    limit = 0.75
    if direction[0]*chord[0] + direction[1]*chord[1] < 0.5*length(chord):
        direction, limit = unit(chord), 0.4
    handle = min(max(length(sub(p1, p0)), 0.08*length(chord)),
                 limit*length(chord))
    return (target, add(target, mul(direction, handle)), p2, p3)


def _refit_extension(controls, bent, at_end):
    """Refit a bent cut end through the curve it replaces, keeping its end tangents.

    _bend_curve_endpoint moves the end and its handle together. On a long edge
    that drags the middle of the edge sideways, widening or thinning the stroke;
    least-squares handle lengths along the same tangents keep it on the old edge.
    """
    p0, p3 = bent[0], bent[-1]
    a, b = length(sub(bent[1], p0)), length(sub(p3, bent[2]))
    chord = length(sub(p3, p0))
    old_length = sum(length(sub(cubic(*controls, (i+1)/16.0), cubic(*controls, i/16.0)))
                     for i in range(16))
    extension = length(sub(p3 if at_end else p0, controls[-1] if at_end else controls[0]))
    if min(a, b, chord, old_length) < EPS:
        return bent
    d0, d1 = unit(sub(bent[1], p0)), unit(sub(p3, bent[2]))
    occupied = old_length/(old_length+extension)
    samples = [cubic(*controls, i/16.0) for i in range(1, 16)]
    us = [i/16.0*occupied if at_end else 1-occupied+i/16.0*occupied for i in range(1, 16)]

    def error(points):
        curve = [cubic(*points, i/64.0) for i in range(65)]
        return max(min(_point_segment_distance(p, curve[j], curve[j+1])
                       for j in (i-1, i) if 0 <= j < 64)
                   for p, i in zip(samples, _nearest_along(samples, curve)))
    best = (error(bent), bent)
    for _ in range(3):
        fitted = _handles(p0, p3, d0, d1, samples, us, chord)
        if length(sub(fitted[1], p0)) < EPS or length(sub(p3, fitted[2])) < EPS:
            break
        best = min(best, (error(fitted), fitted))
        curve = [cubic(*fitted, i/64.0) for i in range(65)]
        us = [min(max(i/64.0, 1e-3), 1-1e-3) for i in _nearest_along(samples, curve)]
    return best[1]


def _fit_join_extension(controls, at_end, target):
    """Fit one longer cubic through the existing offset curve's interior."""
    old_length = sum(length(sub(cubic(*controls, (i+1)/12.0),
                                cubic(*controls, i/12.0))) for i in range(12))
    extension = length(sub(target, controls[-1] if at_end else controls[0]))
    if old_length < EPS or extension < EPS:
        return None
    occupied = old_length/(old_length+extension)
    p0, p3 = (controls[0], target) if at_end else (target, controls[-1])
    c00 = c01 = c11 = 0.0
    x0 = [0.0, 0.0]
    x1 = [0.0, 0.0]
    for i in range(13):
        s = i/12.0
        t = s*occupied if at_end else 1-occupied+s*occupied
        v = 1-t
        a, b = 3*v*v*t, 3*v*t*t
        sample = cubic(*controls, s)
        baseline = add(mul(p0, v*v*v), mul(p3, t*t*t))
        residual = sub(sample, baseline)
        c00 += a*a
        c01 += a*b
        c11 += b*b
        for j in range(2):
            x0[j] += a*residual[j]
            x1[j] += b*residual[j]
    determinant = c00*c11-c01*c01
    if abs(determinant) < EPS:
        return None
    p1 = tuple((x0[j]*c11-x1[j]*c01)/determinant for j in range(2))
    p2 = tuple((x1[j]*c00-x0[j]*c01)/determinant for j in range(2))
    return (p0, p1, p2, p3)


def _straight_extension(controls, at_end, target):
    """One cubic for an edge extended straight along its end tangent to `target`
    and then the edge itself (a miter): both ends keep their directions, and the
    handle lengths fit the straight piece and the curve together. None when
    `target` is not on that tangent line."""
    p0, p1, p2, p3 = controls
    end = p3 if at_end else p0
    tangent = unit(sub(p3, p2) if at_end else sub(p1, p0))
    offset = sub(target, end)
    along = offset[0]*tangent[0] + offset[1]*tangent[1]
    across = abs(offset[0]*tangent[1] - offset[1]*tangent[0])
    if (along <= 0 if at_end else along >= 0) or across > max(0.5, 0.02*abs(along)):
        return None
    curve = [cubic(*controls, i/24.0) for i in range(25)]
    straight = [add(end, mul(offset, i/8.0)) for i in range(1, 9)]
    points = curve + straight if at_end else list(reversed(straight)) + curve
    distances = [0.0]
    for before, after in zip(points, points[1:]):
        distances.append(distances[-1] + length(sub(after, before)))
    total = distances[-1]
    if total < EPS:
        return None
    start, finish = (p0, target) if at_end else (target, p3)
    d0 = unit(sub(p1, p0)) if at_end else tangent
    d1 = tangent if at_end else unit(sub(p3, p2))
    samples, us = points[1:-1], [d/total for d in distances[1:-1]]
    fitted = _handles(start, finish, d0, d1, samples, us, total)
    for _ in range(2):
        lines = [cubic(*fitted, i/64.0) for i in range(65)]
        us = [min(max(i/64.0, 1e-3), 1-1e-3) for i in _nearest_along(samples, lines)]
        fitted = _handles(start, finish, d0, d1, samples, us, total)
    return fitted


def _bend_join_endpoint(controls, at_end, target):
    """Extend one cubic while retaining the width-derived offset interval.

    A long extension (a sharp miter) is the edge drawn straight on to the
    miter: the bent and refitted curve would bulge there, taking the stroke off
    its centerline. Short ones keep their bend; in between the two blend."""
    shaped = _shaped_join_endpoint(controls, at_end, target)
    source_chord = length(sub(controls[-1], controls[0]))
    if source_chord < EPS:
        return shaped
    ratio = length(sub(target, controls[-1] if at_end else controls[0])) / source_chord
    weight = min(max((ratio-0.2)/0.25, 0.0), 1.0)
    weight = weight*weight*(3-2*weight)
    if weight <= 0.0:
        return shaped
    straight = _straight_extension(controls, at_end, target)
    if straight is None:
        return shaped
    return tuple(add(mul(a, 1-weight), mul(b, weight)) for a, b in zip(shaped, straight))


def _shaped_join_endpoint(controls, at_end, target):
    bent = _bend_curve_endpoint(controls, at_end, target)
    original = controls[-1] if at_end else controls[0]
    source_chord = length(sub(controls[-1], controls[0]))
    if source_chord < EPS:
        return bent
    ratio = length(sub(target, original)) / source_chord
    tangent = unit(sub(controls[-1], controls[-2]) if at_end else
                   sub(controls[1], controls[0]))
    displacement = sub(target, original)
    along = displacement[0]*tangent[0] + displacement[1]*tangent[1]
    if ratio <= 0.05 or (along <= 0 if at_end else along >= 0):
        return bent
    fitted = _fit_join_extension(controls, at_end, target)
    if fitted is None:
        return bent
    activation = min(1.0, (ratio-0.05)/0.13)
    activation = activation*activation*(3-2*activation)
    weight = min(1.0, 0.3/ratio)*activation
    p0, p3 = bent[0], bent[-1]
    chord = sub(p3, p0)
    chord_length = length(chord)
    if chord_length < EPS:
        return bent
    direction = unit(chord)
    for _ in range(4):
        far_weight = min(1.0, 1.75*weight)
        first_weight, second_weight = ((far_weight, weight) if at_end else
                                       (weight, far_weight))
        p1 = add(mul(bent[1], 1-first_weight), mul(fitted[1], first_weight))
        p2 = add(mul(bent[2], 1-second_weight), mul(fitted[2], second_weight))
        # The moved end keeps the edge's own direction: a join pulled back from
        # a long miter is off the tangent, and a fitted handle turned towards
        # the curve there makes the edge wave (an S) on its way back.
        if at_end:
            reach = (p3[0]-p2[0])*tangent[0] + (p3[1]-p2[1])*tangent[1]
            p2 = sub(p3, mul(tangent, max(reach, 0.05*chord_length)))
        else:
            reach = (p1[0]-p0[0])*tangent[0] + (p1[1]-p0[1])*tangent[1]
            p1 = add(p0, mul(tangent, max(reach, 0.05*chord_length)))
        u1 = sub(p1, p0)[0]*direction[0] + sub(p1, p0)[1]*direction[1]
        u2 = sub(p2, p0)[0]*direction[0] + sub(p2, p0)[1]*direction[1]
        if 0 <= u1 <= u2 <= chord_length:
            return (p0, p1, p2, p3)
        weight *= 0.5
    return bent


def _cap_on_curve(side, at_end, center, tangent, style, nib, slant, angle=0.0):
    """Trim or extend the fitted outline cubic itself, with no connector node."""
    edge = 1.0 if at_end else 0.0
    outward = 1.0 if at_end else -1.0
    if side.kind == 'line':
        p = side.end if at_end else side.start
        if style == 'square':
            w, h = nib[:2]
            point = add(p, mul(tangent, outward *
                               _half_thickness(normal(tangent), w, h, slant)))
        else:
            m = normal(_cut_direction(style, slant, angle))
            direction = mul(tangent, outward)
            along = direction[0]*m[0] + direction[1]*m[1]
            if abs(along) < 1e-6:
                return False
            distance = ((center[0]-p[0])*m[0] + (center[1]-p[1])*m[1]) / along
            point = add(p, mul(direction, distance))
        if at_end:
            side.end = point
        else:
            side.start = point
        if side.override_piece is not None:  # set by a smooth join; keep it in step
            if side.override_piece[0] == 'cubic':  # keep the join's matched handle
                _, q1, q2, _ = side.override_piece[1]
                side.override_piece = ('cubic', (side.start, q1, q2, side.end))
            else:
                side.override_piece = ('line', (side.start, side.end))
        return True

    controls = (side.override_piece or side.piece())[1]
    edge_point = cubic(*controls, edge)
    fallback_direction = mul(tangent, outward)

    def keep_cut():
        if style == 'square':
            w, h = nib[:2]
            target = add(edge_point, mul(fallback_direction,
                             _half_thickness(normal(tangent), w, h, slant)))
        else:
            m = normal(_cut_direction(style, slant, angle))
            along = fallback_direction[0]*m[0] + fallback_direction[1]*m[1]
            if abs(along) < EPS:
                return False
            delta = sub(center, edge_point)
            target = add(edge_point, mul(fallback_direction,
                         (delta[0]*m[0] + delta[1]*m[1]) / along))
        side.override_piece = ('cubic', _refit_extension(
            controls, _bend_curve_endpoint(controls, at_end, target), at_end))
        if at_end:
            side.end = target
        else:
            side.start = target
        return True

    derivative = mul(cubic_derivative(*controls, edge), outward)
    if length(derivative) < EPS:
        return keep_cut()
    initial = unit(derivative)
    # A cut may reach as far along the edge as _usable_cap allows: CUT_LIMIT
    # half-widths. Beyond the cubic's own continuation keep_cut bends the end
    # handle, which distorts a long, gently curving edge.
    max_travel = min(0.5*CUT_LIMIT*side.extent(side.tb if at_end else side.ta),
                     0.5*length(sub(side.pts[-1], side.pts[0])))
    if max_travel < EPS:
        return keep_cut()
    if style == 'square':
        w, h = nib[:2]
        target = _half_thickness(normal(tangent), w, h, slant)
        max_travel = max(max_travel, target*1.05)
        search = outward
        m = None
    else:
        m = normal(_cut_direction(style, slant, angle))
        offset = sub(edge_point, center)
        signed = offset[0]*m[0] + offset[1]*m[1]
        if abs(signed) < 1e-7:
            return True
        speed = derivative[0]*m[0] + derivative[1]*m[1]
        if abs(speed) < EPS:
            return keep_cut()
        search = outward if -signed/speed >= 0 else -outward
        target = None

    previous_t, previous_point = edge, edge_point
    previous_signed = signed if m is not None else None
    travelled = 0.0
    steps = 64 if m is None or search != outward else 32
    for i in range(1, steps+1):
        t = edge + search*i/64.0
        point = cubic(*controls, t)
        segment_length = length(sub(point, previous_point))
        travelled += segment_length
        if search == outward:
            direction = mul(cubic_derivative(*controls, t), outward)
            if length(direction) < EPS or unit(direction)[0]*initial[0] + \
                    unit(direction)[1]*initial[1] < 0.85 or \
                    travelled - segment_length > max_travel:
                return keep_cut()
        if m is None:
            crossed = travelled >= target
        else:
            delta = sub(point, center)
            current_signed = delta[0]*m[0] + delta[1]*m[1]
            crossed = previous_signed * current_signed <= 0
        if crossed:
            lo, hi = previous_t, t
            for _ in range(25):
                mid = (lo+hi)/2.0
                if m is None:
                    # Arc length over this small interval is sufficiently linear.
                    fraction = (target-(travelled-segment_length)) / segment_length
                    hit = previous_t + (t-previous_t)*fraction
                    break
                delta = sub(cubic(*controls, mid), center)
                value = delta[0]*m[0] + delta[1]*m[1]
                if (value > 0) == (previous_signed > 0):
                    lo = mid
                else:
                    hi = mid
            else:
                hit = (lo+hi)/2.0
            # One step is long on a long curve: the crossing may lie inside the
            # travel limit although the end of its step does not.
            if search == outward and travelled - segment_length + length(
                    sub(cubic(*controls, hit), previous_point)) > max_travel:
                return keep_cut()
            interval = (0.0, hit) if at_end else (hit, 1.0)
            piece = ('cubic', _cubic_interval(controls, *interval))
            side.override_piece = piece
            if at_end:
                side.end = piece[1][-1]
            else:
                side.start = piece[1][0]
            return True
        previous_t, previous_point = t, point
        if m is not None:
            previous_signed = current_signed
    return keep_cut()


def _usable_cap(style, tangent, slant=0.0, angle=0.0):
    """A cut nearly parallel to the stroke would need a spike longer than CUT_LIMIT
    half-widths; such caps fall back to flat (same node count)."""
    if style in CUTS:
        m = normal(_cut_direction(style, slant, angle))
        n = normal(tangent)
        along = abs(tangent[0]*m[0] + tangent[1]*m[1])
        across = abs(n[0]*m[0] + n[1]*m[1])
        if along < 1e-6 or across / along > CUT_LIMIT:
            return 'flat'
    return style


# ---------------------------------------------------------------------------
# Live corners: round outline vertices with a fixed radius, independent of the width.


def _piece_point(piece, t):
    kind, pts = piece
    return add(mul(pts[0], 1-t), mul(pts[1], t)) if kind == 'line' else cubic(*pts, t)


def _piece_tangent(piece, t):
    kind, pts = piece
    if kind == 'line':
        return unit(sub(pts[1], pts[0]))
    return unit(_derivative('cubic', pts, t))


def _piece_samples(piece, count=32):
    """[(t, cumulative length)] along a piece."""
    result, total, previous = [(0.0, 0.0)], 0.0, _piece_point(piece, 0.0)
    for i in range(1, count+1):
        t = i / float(count)
        point = _piece_point(piece, t)
        total += length(sub(point, previous))
        result.append((t, total))
        previous = point
    return result


def _t_at_length(samples, distance):
    for (t0, l0), (t1, l1) in zip(samples, samples[1:]):
        if l1 >= distance:
            return t0 + (t1-t0) * ((distance-l0) / (l1-l0) if l1 > l0 else 0.0)
    return 1.0


def _split_cubic(pts, t):
    p0, p1, p2, p3 = pts
    a, b, c = add(mul(p0, 1-t), mul(p1, t)), add(mul(p1, 1-t), mul(p2, t)), add(mul(p2, 1-t), mul(p3, t))
    d, e = add(mul(a, 1-t), mul(b, t)), add(mul(b, 1-t), mul(c, t))
    f = add(mul(d, 1-t), mul(e, t))
    return (p0, a, d, f), (f, e, c, p3)


def _sub_piece(piece, t0, t1):
    kind, pts = piece
    if kind == 'line':
        return ('line', (_piece_point(piece, t0), _piece_point(piece, t1)))
    part = pts
    if t1 < 1.0:
        part = _split_cubic(part, t1)[0]
    if t0 > 0.0:
        part = _split_cubic(part, t0 / t1 if t1 > EPS else 0.0)[1]
    return ('cubic', part)


def _corner_spec(value):
    """Normalize a node's live corner: None (sharp), a radius, or a dict with
    'outer', 'inner' (radii), 'tension' (%, 100 = circular) and 'ratio' (%,
    100 = symmetric; above 100 the side before the node, in path direction,
    is rounded over a longer stretch)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        value = {'outer': value}
    outer = max(float(value.get('outer') or 0.0), 0.0)
    inner = value.get('inner')
    tension = max(float(value.get('tension') if value.get('tension') is not None
                        else 100.0), 0.0) / 100.0
    ratio = max(float(value.get('ratio') if value.get('ratio') is not None
                      else 100.0), 1.0) / 100.0
    inner_tension = value.get('inner_tension')
    inner_ratio = value.get('inner_ratio')
    return {'outer': outer, 'inner': outer if inner is None else max(float(inner), 0.0),
            'tension': tension,
            'inner_tension': tension if inner_tension is None else
                             max(float(inner_tension), 0.0) / 100.0,
            'ratio': ratio,
            'inner_ratio': ratio if inner_ratio is None else
                           max(float(inner_ratio), 1.0) / 100.0}


def _round_contour(contour, vertices, report=None):
    """Round contour vertices. vertices[i] describes the vertex before piece i:
    None (sharp) or dict(corner=spec, which='outer'|'inner', flip=bool, cap=bool,
    node=index). `flip` marks contour runs against the path direction, so the
    aspect ratio stays tied to the centerline's before/after sides.

    Each rounded vertex becomes one cubic whatever the values, so the node
    structure only depends on which nodes are rounded; a straight-through vertex
    just gets a zero-size arc. An arc may take up to the whole of each
    neighbouring piece; where the arcs at both ends of a piece would overlap,
    they share it in proportion to what each asks for and meet on it, where they
    follow its direction (the piece is left with no length, keeping the
    structure). Rounded vertices are reported (see outline_curves) when `report`
    is a list.
    """
    count = len(contour)
    if count < 2 or not any(v is not None for v in vertices):
        return contour
    samples = [_piece_samples(piece) for piece in contour]
    lengths = [s[-1][1] for s in samples]
    trim_start, trim_end, arcs = [0.0]*count, [0.0]*count, {}
    for i, vertex in enumerate(vertices):
        if vertex is None:
            continue
        corner = vertex['corner']
        radius = corner['outer'] if vertex['which'] == 'outer' else corner['inner']
        before, after = contour[i-1], contour[i]
        t_in, t_out = _piece_tangent(before, 1.0), _piece_tangent(after, 0.0)
        cross = t_in[0]*t_out[1] - t_in[1]*t_out[0]
        dot = t_in[0]*t_out[0] + t_in[1]*t_out[1]
        turn = math.atan2(abs(cross), dot)  # 0 straight .. pi hairpin
        reach = radius * math.tan(min(turn, math.pi - 1e-3) / 2.0)
        inner_side = vertex['which'] == 'inner'
        ratio_value = corner['inner_ratio'] if inner_side else corner['ratio']
        tension_value = corner['inner_tension'] if inner_side else corner['tension']
        ratio = math.sqrt(ratio_value)
        # Path-direction sides: before the node gets reach*ratio, after reach/ratio.
        first, second = reach * ratio, reach / ratio
        if vertex.get('flip'):
            first, second = second, first
        trim_end[i-1], trim_start[i] = first, second
        arcs[i] = (turn, tension_value, vertex, t_in, t_out)
    for i in range(count):
        # Each piece gives its ends at most its whole length, shared by what they ask.
        wanted = trim_start[i] + trim_end[i]
        if wanted > lengths[i]:
            share = lengths[i] / wanted if wanted > EPS else 0.0
            trim_start[i] *= share
            trim_end[i] *= share
    trimmed, starts, ends = [], [], []
    for i, piece in enumerate(contour):
        t0 = _t_at_length(samples[i], trim_start[i]) if trim_start[i] > 0 else 0.0
        t1 = _t_at_length(samples[i], lengths[i] - trim_end[i]) if trim_end[i] > 0 else 1.0
        t1 = max(t0, t1)
        starts.append(t0)
        ends.append(t1)
        trimmed.append(_sub_piece(piece, t0, t1) if (t0 > 0 or t1 < 1) else piece)
    result = []
    for i, piece in enumerate(trimmed):
        if i in arcs:
            turn, tension, vertex, t_in, t_out = arcs[i]
            first, second = trim_end[i-1], trim_start[i]
            before = trimmed[i-1]
            p1, p2 = before[1][-1], piece[1][0]
            # Directions from the untrimmed pieces: a piece both arcs used up
            # has no length left to take one from.
            d1 = _piece_tangent(contour[i-1], ends[i-1])
            d2 = _piece_tangent(contour[i], starts[i])
            # Circular-arc handle proportion (4/3 tan(turn/4) / tan(turn/2)),
            # scaled by each side's trim so uneven sides give an elliptic arc.
            k = (4.0/3.0 * math.tan(turn/4.0) / math.tan(turn/2.0)) if turn > 1e-6 else 2.0/3.0
            arc = ('cubic', (p1, add(p1, mul(d1, first * k * tension)),
                             sub(p2, mul(d2, second * k * tension)), p2))
            result.append(arc)
            if report is not None:
                p1, c1, c2, p2 = arc[1]
                report.append({'node': vertex['node'], 'which': vertex['which'],
                               'corner': contour[i][1][0], 'middle': cubic(*arc[1], 0.5),
                               'p1': p1, 'c1': c1, 'c2': c2, 'p2': p2, 'turn': turn,
                               'first': first, 'second': second, 'arc_factor': k,
                               'middle_slope': mul(sub(mul(d1, first), mul(d2, second)),
                                                   3.0*k/800.0),
                               'flip': bool(vertex.get('flip')), 'cap': bool(vertex.get('cap')),
                               'in': t_in, 'out': t_out})
        result.append(piece)
    return result


def _turns_left(before, after):
    """True when the centerline turns left (counter-clockwise) at the node joining
    side `before` to side `after`."""
    t_in, t_out = before.tangent(1.0), after.tangent(0.0)
    return t_in[0]*t_out[1] - t_in[1]*t_out[0] > 0


def _open_vertices(lefts, indices, cap_pieces_end, cap_pieces_start,
                   corner_of, cap_end, cap_start, curve_start=False, curve_end=False):
    """Vertex descriptions for an open contour: left sides, end cap, reversed right
    sides, start cap. Round caps are already smooth and keep their joins."""
    n = len(indices)
    start_node, end_node = indices[0], indices[-1] + 1

    def at(node, which, flip=False, cap=False):
        corner = corner_of(node)
        return None if corner is None else {'corner': corner, 'which': which, 'flip': flip,
                                            'cap': cap, 'node': node}

    def interior(k, flip):
        # Left of the path is the inside of a left turn.
        left_turn = _turns_left(lefts[k-1], lefts[k])
        which = ('inner' if left_turn else 'outer') if not flip else \
            ('outer' if left_turn else 'inner')
        return at(indices[k], which, flip)

    start = at(start_node, 'outer', cap=True) if not curve_start and \
        cap_start not in ('round', 'ellipse', 'curve') else None
    end = at(end_node, 'outer', cap=True) if not curve_end and \
        cap_end not in ('round', 'ellipse', 'curve') else None
    vertices = [start] + [interior(k, False) for k in range(1, n)]
    vertices += [end] + [None] * (cap_pieces_end - 1)
    vertices += [dict(end, flip=True, which='inner') if end else None]
    vertices += [interior(n-j, True) for j in range(1, n)]
    vertices += [dict(start, flip=True, which='inner') if start else None] + [None] * (cap_pieces_start - 1)
    return vertices


def _independent_outline_curves(left_segments, right_segments, closed, cap_start,
                                cap_end, italic_angle, start_angle, end_angle,
                                corners, left_map, right_map, left_breaks,
                                right_breaks, left_smooths, right_smooths,
                                report, edges, cap_start_curve, cap_end_curve, cap_out,
                                bevels=None):
    """Build each side from only the virtual nodes that affect that side."""
    slant = math.tan(math.radians(italic_angle or 0.0))

    def make_sides(segments, sign):
        sides, widths = [], []
        for segment in segments:
            kind, pts, e0, e1 = segment[:4]
            if all(length(sub(p, pts[0])) < EPS for p in pts):
                continue
            side = _Side(kind, pts, e0, e1, sign, slant, None,
                         segment[4] if len(segment) > 4 else None)
            sides.append(side)
            widths.append(side.extent(1.0))
        return sides, widths

    lefts, left_widths = make_sides(left_segments, 1)
    rights, right_widths = make_sides(right_segments, -1)
    if not lefts or not rights:
        return []
    _ease_sizes(lefts, closed)
    _ease_sizes(rights, closed)
    _trim_folds(lefts, closed)
    _trim_folds(rights, closed)
    left_breaks, right_breaks = set(left_breaks), set(right_breaks)
    maps = {id(lefts): left_map, id(rights): right_map}
    cut, zero = _bevel_breaks(lefts, rights,
                              lambda sides, k: maps[id(sides)][(k+1) % len(sides)],
                              bevels, closed)
    left_breaks |= set(cut['left'])
    right_breaks |= set(cut['right'])
    left_zero, right_zero = zero['left'] - left_breaks, zero['right'] - right_breaks

    def corner_at(node):
        if node is None or not corners:
            return None
        if closed:
            node %= len(corners)
        if not 0 <= node < len(corners):
            return None
        return _corner_spec(corners[node])

    def vertex(node, which, flip=False, cap=False):
        spec = corner_at(node)
        return None if spec is None else {'corner': spec, 'which': which,
                                          'flip': flip, 'cap': cap, 'node': node}

    def join_sides(sides, widths, side_breaks, smooths):
        n = len(sides)
        for k in range(n if closed else n-1):
            if k not in side_breaks:
                _join(sides[k], sides[(k+1) % n], widths[k])
        for k in smooths:
            _smooth_virtual_join(sides[k], sides[(k+1) % n])

    join_sides(lefts, left_widths, left_breaks, left_smooths)
    join_sides(rights, right_widths, right_breaks, right_smooths)
    _apply_bevel_angles(lefts, left_widths, cut['left'])
    _apply_bevel_angles(rights, right_widths, cut['right'])

    if edges is not None:
        left_edges = {node: lefts[k].start for k, node in enumerate(left_map[:len(lefts)])
                      if node is not None}
        right_edges = {node: rights[k].start for k, node in enumerate(right_map[:len(rights)])
                       if node is not None}
        if not closed:
            left_edges[left_map[-1]] = lefts[-1].end
            right_edges[right_map[-1]] = rights[-1].end
        for node in left_edges.keys() & right_edges.keys():
            edges[node] = (left_edges[node], right_edges[node])

    if closed:
        for side in lefts + rights:
            _fillet_side(side)
            _optical_outer_curve(side)
        outer = _side_contour(lefts, left_widths, True, joined=True, breaks=left_breaks,
                              zero=left_zero)
        inner = _reverse(_side_contour(rights, right_widths, True, joined=True,
                                       breaks=right_breaks, zero=right_zero))
        outer_vertices = []
        for k in range(len(lefts)):
            inside = _turns_left(lefts[k-1], lefts[k])
            outer_vertices.append(vertex(left_map[k], 'inner' if inside else 'outer'))
        inner_vertices = []
        n = len(rights)
        for j in range(n):
            k = (n-j) % n
            inside = _turns_left(rights[k-1], rights[k])
            inner_vertices.append(vertex(right_map[k], 'outer' if inside else 'inner', True))
        for index in sorted((k+1 for k in left_breaks | left_zero), reverse=True):
            outer_vertices.insert(index, None)
        for index in sorted((len(rights)-1-k for k in right_breaks | right_zero),
                            reverse=True):
            inner_vertices.insert(index, None)
        return [_round_contour(outer, outer_vertices, report),
                _round_contour(inner, inner_vertices, report)]

    start_center, start_tangent = lefts[0].pts[0], lefts[0].tangent(0.0)
    end_center, end_tangent = lefts[-1].pts[-1], lefts[-1].tangent(1.0)
    cap_start = _usable_cap(cap_start, start_tangent, slant, start_angle)
    cap_end = _usable_cap(cap_end, end_tangent, slant, end_angle)

    def apply_cap_pair(at_end, center, tangent, style, angle):
        if style not in CUTS and style != 'square':
            return style
        pair = (lefts[-1], rights[-1]) if at_end else (lefts[0], rights[0])
        saved = [(side.start, side.end, side.override_piece) for side in pair]
        for side in pair:
            nib = side.n1 if at_end else side.n0
            if not _cap_on_curve(side, at_end, center, tangent, style, nib, slant, angle):
                for old_side, (start, end, piece) in zip(pair, saved):
                    old_side.start, old_side.end, old_side.override_piece = start, end, piece
                return 'flat'
        return style

    cap_start = apply_cap_pair(False, start_center, start_tangent, cap_start, start_angle)
    cap_end = apply_cap_pair(True, end_center, end_tangent, cap_end, end_angle)
    if cap_start == 'ellipse':
        _place_ellipse_cap(lefts[0], rights[0], start_center, start_tangent,
                           lefts[0].n0, False)
    if cap_end == 'ellipse':
        _place_ellipse_cap(lefts[-1], rights[-1], end_center, end_tangent,
                           lefts[-1].n1, True)
    for side in lefts + rights:
        _fillet_side(side)
        _optical_outer_curve(side)
    left = _side_contour(lefts, left_widths, False, joined=True, breaks=left_breaks,
                         zero=left_zero)
    right = _side_contour(rights, right_widths, False, joined=True, breaks=right_breaks,
                          zero=right_zero)
    end_cap = _cap_segments(end_center, lefts[-1].w1, end_tangent, cap_end, True,
                            lefts[-1].end, rights[-1].end, lefts[-1].n1, slant,
                            cap_end_curve)
    start_cap = _cap_segments(start_center, lefts[0].w0, start_tangent, cap_start, False,
                              lefts[0].start, rights[0].start, lefts[0].n0, slant,
                              cap_start_curve)
    if cap_out is not None:
        if cap_end == 'curve' or (cap_end_curve is not None and
                                  cap_end not in ('round', 'ellipse')):
            cap_out['end'] = end_cap[0][1]
        if cap_start == 'curve' or (cap_start_curve is not None and
                                    cap_start not in ('round', 'ellipse')):
            cap_out['start'] = start_cap[0][1]
    contour = left + end_cap + _reverse(right) + start_cap
    if not corners:
        return [contour]

    start = vertex(left_map[0], 'outer', cap=True) \
        if cap_start_curve is None and cap_start not in ('round', 'ellipse', 'curve') else None
    end = vertex(left_map[-1], 'outer', cap=True) \
        if cap_end_curve is None and cap_end not in ('round', 'ellipse', 'curve') else None
    left_vertices = [start]
    for k in range(1, len(lefts)):
        inside = _turns_left(lefts[k-1], lefts[k])
        left_vertices.append(vertex(left_map[k], 'inner' if inside else 'outer'))
    for index in sorted((k+1 for k in left_breaks | left_zero), reverse=True):
        left_vertices.insert(index, None)

    n = len(rights)
    right_vertices = [dict(end, flip=True, which='inner') if end else None]
    for j in range(1, n):
        k = n-j
        inside = _turns_left(rights[k-1], rights[k])
        right_vertices.append(vertex(right_map[k], 'outer' if inside else 'inner', True))
    for index in sorted((n-1-k for k in right_breaks | right_zero), reverse=True):
        right_vertices.insert(index, None)
    vertices = (left_vertices + [end] + [None]*(len(end_cap)-1) + right_vertices +
                [dict(start, flip=True, which='inner') if start else None] +
                [None]*(len(start_cap)-1))
    return [_round_contour(contour, vertices, report)]


def outline_curves(segments, closed=False, cap_start='flat', cap_end='flat',
                   tolerance=FIT_TOLERANCE, italic_angle=0.0, start_angle=0.0, end_angle=0.0,
                   corner_radii=None, report=None, edges=None, breaks=(), smooth_virtuals=(),
                   right_segments=None, right_corner_radii=None, left_map=None, right_map=None,
                   right_smooth_virtuals=(), cap_start_curve=None, cap_end_curve=None,
                   cap_out=None, bevels=None, right_bevels=None, fold_rounds=None):
    """Return closed contours of ('line'|'cubic', control points) segments.

    Each segment is (kind, points, start, end[, modulation]) where start/end is a
    width or a (width, height, offset) nib; see _nib, and the optional modulation
    shapes the width between them by a width profile; see _Modulation. `italic_angle` (degrees) tilts
    vertical cuts. `start_angle` / `end_angle` (degrees
    on the page) are the cut directions of 'angle' caps. `corner_radii[i]` is the
    live corner of on-curve node i (segment i starts at node i): None (sharp), a
    radius or a dict (see _corner_spec). `report`, if a list, collects a dict per
    rounded corner: node, which ('outer'|'inner'), corner point, arc midpoint, the
    arc's points p1 c1 c2 p2, the turn angle, both trims and orientation flags.
    `edges`, if a dict, receives node_edges' result from the same joins, so the
    editor's width handles cost nothing extra. `bevels[i]` is 'bevel' to cut the
    outer side of the corner at node i with a line between the two edges'
    ends (('bevel', angle) turns that line to a page angle about its middle), 'zero' for a line of no length at the joined corner instead (a
    bevel in another master: the masters keep one structure), or None. With
    `right_segments`, `right_bevels` index the original nodes, as
    `right_corner_radii` do. `fold_rounds` ({node index: factor}) scales
    FOLD_ROUNDING at those nodes (see _trim_folds).
    """
    for style in (cap_start, cap_end):
        if style not in CAPS:
            raise ValueError('Unknown cap: ' + str(style))
    if right_segments is not None:
        return _independent_outline_curves(
            segments, right_segments, closed, cap_start, cap_end, italic_angle,
            start_angle, end_angle, right_corner_radii if right_corner_radii is not None
            else corner_radii, left_map, right_map,
            breaks.get('left', ()) if isinstance(breaks, dict) else breaks,
            breaks.get('right', ()) if isinstance(breaks, dict) else breaks,
            smooth_virtuals, right_smooth_virtuals, report, edges,
            cap_start_curve, cap_end_curve, cap_out,
            right_bevels if right_bevels is not None else bevels)
    slant = math.tan(math.radians(italic_angle or 0.0))
    if isinstance(breaks, dict):
        left_breaks = set(breaks.get('left', ()))
        right_breaks = set(breaks.get('right', ()))
    else:
        left_breaks = right_breaks = set(breaks)
    smooth_virtuals = set(smooth_virtuals)
    lefts, rights, widths, indices = [], [], [], []
    for index, segment in enumerate(segments):
        kind, pts, e0, e1 = segment[:4]
        if all(length(sub(p, pts[0])) < EPS for p in pts):
            continue  # zero-length segment
        left, right = _side_pair(kind, pts, e0, e1, slant,
                                 segment[4] if len(segment) > 4 else None)
        lefts.append(left)
        rights.append(right)
        widths.append(lefts[-1].extent(1.0))
        indices.append(index)
    if not lefts:
        return []
    _ease_sizes(lefts, closed)
    _ease_sizes(rights, closed)
    _trim_folds(lefts, closed, fold_rounds)
    _trim_folds(rights, closed, fold_rounds)
    cut, zero = _bevel_breaks(lefts, rights,
                              lambda sides, k: indices[(k+1) % len(sides)], bevels, closed)

    def corner_of(node):
        if not corner_radii:
            return None
        node = node % len(corner_radii) if closed else node
        return _corner_spec(corner_radii[node]) if 0 <= node < len(corner_radii) else None
    # A cut corner folds its inside in: each inner edge runs to its own end
    # and a line joins them (the outline overlaps itself there and fills
    # as the two strokes together). Cut where the inner edges cross, a
    # hairpin whose outgoing stroke is thinner loses the strip of the
    # incoming one that sticks out past it, and edges almost back to back
    # may not cross where the join looks at all. Every master cuts the same
    # corners (see _bevel_breaks), so all have this line; a rounded inner
    # corner keeps its join.
    fold_left = {k for k in set(cut['right']) | zero['right']
                 if corner_of(indices[(k+1) % len(indices)]) is None}
    fold_right = {k for k in set(cut['left']) | zero['left']
                  if corner_of(indices[(k+1) % len(indices)]) is None}
    left_breaks = left_breaks | set(cut['left']) | fold_left
    right_breaks = right_breaks | set(cut['right']) | fold_right
    left_zero, right_zero = zero['left'] - left_breaks, zero['right'] - right_breaks

    if closed:
        n = len(indices)
        turns = [_turns_left(lefts[k-1], lefts[k]) for k in range(n)]  # at node indices[k]

        def closed_vertex(k, flip):
            corner = corner_of(indices[k])
            if corner is None:
                return None
            inner_side = turns[k] != flip  # left side is inside of a left turn
            return {'corner': corner, 'which': 'inner' if inner_side else 'outer',
                    'flip': flip, 'node': indices[k]}
        for sides, side_breaks in ((lefts, left_breaks), (rights, right_breaks)):
            for k in range(n):
                if k not in side_breaks:
                    _join(sides[k], sides[(k+1) % n], widths[k])
            for k in smooth_virtuals:
                _smooth_virtual_join(sides[k], sides[(k+1) % n])
        _apply_bevel_angles(lefts, widths, cut['left'])
        _apply_bevel_angles(rights, widths, cut['right'])
        if edges is not None:
            edges.update(_edge_map(lefts, rights, indices, True))
        for side in lefts + rights:
            _fillet_side(side)
            _optical_outer_curve(side)
        outer = _side_contour(lefts, widths, True, joined=True, breaks=left_breaks,
                              zero=left_zero)
        inner = _reverse(_side_contour(rights, widths, True, joined=True,
                                       breaks=right_breaks, zero=right_zero))
        outer_vertices = [closed_vertex(k, False) for k in range(n)]
        inner_vertices = [closed_vertex((n-j) % n, True) for j in range(n)]
        for index in sorted((k+1 for k in left_breaks | left_zero), reverse=True):
            outer_vertices.insert(index, None)
        for index in sorted((n-1-k for k in right_breaks | right_zero), reverse=True):
            inner_vertices.insert(index, None)
        return [_round_contour(outer, outer_vertices, report),
                _round_contour(inner, inner_vertices, report)]
    first, last = lefts[0], lefts[-1]
    start_center, start_tangent = first.pts[0], first.tangent(0.0)
    end_center, end_tangent = last.pts[-1], last.tangent(1.0)
    cap_start = _usable_cap(cap_start, start_tangent, slant, start_angle)
    cap_end = _usable_cap(cap_end, end_tangent, slant, end_angle)
    for sides, side_breaks in ((lefts, left_breaks), (rights, right_breaks)):
        for k in range(len(sides)-1):
            if k not in side_breaks:
                _join(sides[k], sides[k+1], widths[k])
        for k in smooth_virtuals:
            _smooth_virtual_join(sides[k], sides[k+1])
    _apply_bevel_angles(lefts, widths, cut['left'])
    _apply_bevel_angles(rights, widths, cut['right'])
    if edges is not None:
        edges.update(_edge_map(lefts, rights, indices, False))

    def apply_cap_pair(at_end, center, tangent, style, nib, angle):
        if style not in CUTS and style != 'square':
            return style
        pair = (lefts[-1], rights[-1]) if at_end else (lefts[0], rights[0])
        saved = [(side.start, side.end, side.override_piece) for side in pair]
        for side in pair:
            if not _cap_on_curve(side, at_end, center, tangent, style, nib, slant, angle):
                for old_side, (start, end, piece) in zip(pair, saved):
                    old_side.start, old_side.end, old_side.override_piece = start, end, piece
                return 'flat'
        return style

    cap_start = apply_cap_pair(False, start_center, start_tangent, cap_start, first.n0,
                               start_angle)
    cap_end = apply_cap_pair(True, end_center, end_tangent, cap_end, last.n1,
                             end_angle)
    if cap_start == 'ellipse':
        _place_ellipse_cap(lefts[0], rights[0], start_center, start_tangent,
                           first.n0, False)
    if cap_end == 'ellipse':
        _place_ellipse_cap(lefts[-1], rights[-1], end_center, end_tangent,
                           last.n1, True)
    for side in lefts + rights:
        _fillet_side(side)
        _optical_outer_curve(side)
    left = _side_contour(lefts, widths, False, joined=True, breaks=left_breaks,
                         zero=left_zero)
    right = _side_contour(rights, widths, False, joined=True, breaks=right_breaks,
                          zero=right_zero)
    end_cap = _cap_segments(end_center, last.w1, end_tangent, cap_end, True,
                            lefts[-1].end, rights[-1].end, last.n1, slant,
                            cap_end_curve)
    start_cap = _cap_segments(start_center, first.w0, start_tangent, cap_start, False,
                              lefts[0].start, rights[0].start, first.n0, slant,
                              cap_start_curve)
    if cap_out is not None:
        if cap_end == 'curve' or (cap_end_curve is not None and
                                  cap_end not in ('round', 'ellipse')):
            cap_out['end'] = end_cap[0][1]
        if cap_start == 'curve' or (cap_start_curve is not None and
                                    cap_start not in ('round', 'ellipse')):
            cap_out['start'] = start_cap[0][1]
    contour = left + end_cap + _reverse(right) + start_cap
    if corner_radii:
        vertices = _open_vertices(
            lefts, indices, len(end_cap), len(start_cap), corner_of,
            cap_end, cap_start, cap_start_curve is not None,
            cap_end_curve is not None)
        n, e = len(lefts), len(end_cap)
        insertion_points = [k+1 for k in left_breaks | left_zero] + \
                           [n+e+n-1-k for k in right_breaks | right_zero]
        for index in sorted(insertion_points, reverse=True):
            vertices.insert(index, None)
        contour = _round_contour(contour, vertices, report)
    return [contour]


# Crossing fillets: a stroke whose outline crosses itself (the loop of す)
# leaves sharp corners where one part's edge runs into another's, e.g. the
# loop's hole against the straight stroke it crosses. Overlaying a patch would
# show its own edges when interpolated, so the outline itself is reconnected.
CROSSING_SAMPLES = 24
CROSSING_END = 1e-4  # crossings this close to a piece's end are at a node: skipped
CROSSING_MIN_SINE = 0.02  # near-tangent edges do not make a corner
# A sliver between nearly parallel edges is not a counter: its rounding fades
# out between these corner angles (degrees) and keeps a corner of no size.
CROSSING_SLIVER = (20.0, 45.0)


def _flat_piece(piece, count=CROSSING_SAMPLES):
    kind, pts = piece
    if kind == 'line':
        return [(0.0, pts[0]), (1.0, pts[1])]
    return [(i / float(count), cubic(*pts, i / float(count))) for i in range(count+1)]


def _bounds(points):
    xs, ys = [p[0] for _, p in points], [p[1] for _, p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _refine_crossing(a, b, s, t):
    """Newton steps on a(s) = b(t) from a polyline estimate."""
    for _ in range(12):
        pa, pb = _piece_point(a, s), _piece_point(b, t)
        f = sub(pa, pb)
        if length(f) < 1e-9:
            break
        da, db = _derivative(a[0], a[1], s), _derivative(b[0], b[1], t)
        det = -da[0]*db[1] + da[1]*db[0]
        if abs(det) < EPS:
            break
        ds = (-f[0]*db[1] + f[1]*db[0]) / det
        dt = (da[0]*f[1] - da[1]*f[0]) / det
        s, t = min(max(s - ds, 0.0), 1.0), min(max(t - dt, 0.0), 1.0)
    return s, t


def _piece_crossings(a, b, flat_a, flat_b):
    """[(s, t)] where piece a at s crosses piece b at t."""
    hits = []
    for (s0, p0), (s1, p1) in zip(flat_a, flat_a[1:]):
        for (t0, q0), (t1, q1) in zip(flat_b, flat_b[1:]):
            hit = _segment_intersection(p0, p1, q0, q1)
            if hit is None:
                continue
            s, t = _refine_crossing(a, b, s0 + (s1-s0)*hit[1], t0 + (t1-t0)*hit[2])
            if length(sub(_piece_point(a, s), _piece_point(b, t))) > 0.01:
                continue
            if all(abs(s-s2) > 1e-4 or abs(t-t2) > 1e-4 for s2, t2 in hits):
                hits.append((s, t))
    return hits


def _winding(polygons, point):
    total = 0
    x, y = point
    for polygon in polygons:
        for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]):
            if y0 <= y < y1 and (x1-x0)*(y-y0) - (x-x0)*(y1-y0) > 0:
                total += 1
            elif y1 <= y < y0 and (x1-x0)*(y-y0) - (x-x0)*(y1-y0) < 0:
                total -= 1
    return total


def contour_crossings(contours, holes_only=True):
    """Where a stroke's outline crosses itself with a hole on one side: [(point,
    (contour, piece, t), (contour, piece, t), fillet)] in contour order.
    `holes_only` leaves out corners open to the outside (a fork's notch).
    Leaving strand 1 for strand 2 there (and 2 for 1) reconnects the outline
    keeping its direction; `fillet` names the corner facing the hole, 12 (the
    end of strand 1 into strand 2) or 21. The other corner is inside the
    overlap of both parts, so it stays sharp and out of sight."""
    flats = [[_flat_piece(piece) for piece in contour] for contour in contours]
    polygons = [[p for flat in contour for _, p in flat[:-1]] for contour in flats]
    boxes = [[_bounds(flat) for flat in contour] for contour in flats]
    places = [(c, i) for c, contour in enumerate(contours) for i in range(len(contour))
              if length(sub(_piece_point(contour[i], 1.0), _piece_point(contour[i], 0.0)))
              > 1e-6 or contour[i][0] == 'cubic']
    result = []
    for k, (c1, i1) in enumerate(places):
        a, box_a = contours[c1][i1], boxes[c1][i1]
        for c2, i2 in places[k+1:]:
            n = len(contours[c1])
            if c1 == c2 and (i2 - i1 == 1 or (i1 == 0 and i2 == n-1)):
                continue  # neighbours meet at their shared node
            b, box_b = contours[c2][i2], boxes[c2][i2]
            if box_a[0] > box_b[2] or box_b[0] > box_a[2] or \
                    box_a[1] > box_b[3] or box_b[1] > box_a[3]:
                continue
            for s, t in _piece_crossings(a, b, flats[c1][i1], flats[c2][i2]):
                if min(s, 1-s, t, 1-t) < CROSSING_END:
                    continue
                d1, d2 = _piece_tangent(a, s), _piece_tangent(b, t)
                sine = d1[0]*d2[1] - d1[1]*d2[0]
                if abs(sine) < CROSSING_MIN_SINE:
                    continue
                point = _piece_point(a, s)
                bisector = unit(sub(d2, d1))  # between strand 1 arriving and 2 leaving
                # Both corners have the same angle: step in far enough to clear the edges.
                half = math.sqrt(max(0.0, (1.0 + d1[0]*d2[0] + d1[1]*d2[1]) / 2.0))
                reach = min(0.75 / max(half, 0.05), 10.0)
                w12 = _winding(polygons, add(point, mul(bisector, reach)))
                w21 = _winding(polygons, sub(point, mul(bisector, reach)))
                if w12 == 0 and abs(w21) == 2:
                    fillet = 12
                elif w21 == 0 and abs(w12) == 2:
                    fillet = 21
                else:
                    continue
                result.append((point, (c1, i1, s), (c2, i2, t), fillet))
    result.sort(key=lambda item: (item[1], item[2]))
    if holes_only and result:
        holes = set()
        for _, members, _, kind in _crossing_cycles(contours, result):
            if kind == 'hole':
                holes.update(members)
        result = [crossing for x, crossing in enumerate(result) if x in holes]
    return result


def _area(points):
    return 0.5 * sum(x0*y1 - x1*y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))


def _cycle_kind(points, polygons, ink):
    """'hole' (against the ink), 'outer' (with the ink, outside beyond it) or
    'overlap' (with the ink, ink beyond it: where two parts overlap)."""
    if _area(points) * ink < 0:
        return 'hole'
    best, middle, direction = -1.0, None, None
    for p0, p1 in zip(points, points[1:] + points[:1]):
        size = length(sub(p1, p0))
        if size > best:
            best, middle, direction = size, mul(add(p0, p1), 0.5), unit(sub(p1, p0))
    if middle is None:
        return 'outer'
    # The ink is left of a counter-clockwise contour: step to the other side.
    outward = (direction[1], -direction[0]) if ink > 0 else (-direction[1], direction[0])
    return 'outer' if _winding(polygons, add(middle, mul(outward, 0.5))) == 0 else 'overlap'


def _junction_open(crossing, strand):
    """True when leaving `strand` (0, 1) at the crossing turns its open corner
    (the one facing a hole or the outside), False for the corner in the overlap."""
    return crossing[3] == (12 if strand == 0 else 21)


def _crossing_cycles(contours, crossings, chosen=None):
    """Reconnect the outline at the crossings given (or those `chosen`):
    [(outline points of a resulting contour, [crossings whose open corner it
    turns], [crossings whose overlap corner it turns], kind (see _cycle_kind))]."""
    events = _crossing_events(crossings, chosen)
    runs, starts, first_run = _crossing_runs(contours, events, crossings, None)
    polygons = [[p for piece in contour for _, p in _flat_piece(piece)[:-1]]
                for contour in contours]
    ink = sum(_area(polygon) for polygon in polygons)
    cycles = []
    for order in _cycle_orders(runs, starts, first_run, events):
        points, open_members, overlap_members = [], [], []
        for r in order:
            pieces, (x, strand) = runs[r]
            for piece in pieces:
                points.extend(p for _, p in _flat_piece(piece)[:-1])
            (open_members if _junction_open(crossings[x], strand) else
             overlap_members).append(x)
        cycles.append((points, sorted(open_members), sorted(overlap_members),
                       _cycle_kind(points, polygons, ink)))
    return cycles


def _crossing_events(crossings, chosen=None):
    events = {}  # contour: [(piece, t, crossing, strand)]
    for x, (_, first, second, _) in enumerate(crossings):
        if chosen is not None and x not in chosen:
            continue
        for strand, (c, i, t) in enumerate((first, second)):
            events.setdefault(c, []).append((i, t, x, strand))
    return events


def _cycle_orders(runs, starts, first_run, events):
    """The runs of each reconnected contour, in order."""
    orders, seen = [], set()
    for c in sorted(events):
        for r in range(first_run[c], len(runs)):
            if r in seen:
                continue
            order = []
            while r not in seen:
                seen.add(r)
                order.append(r)
                x, strand = runs[r][1]
                r = starts[(x, 1 - strand)]
            orders.append(order)
    return orders


def crossing_loops(contours, crossings=None):
    """The holes a stroke makes by crossing itself, each with the part of the
    outline that would be joined to round it: [(outline points of the hole,
    [crossings at its corners], [crossings of its loop])]. A loop's crossings
    are those around the overlap behind the hole's corners (for す, the four
    corners where the loop runs across the straight stroke); `crossings` are
    contour_crossings(contours, holes_only=False)."""
    if crossings is None:
        crossings = contour_crossings(contours, holes_only=False)
    if not crossings:
        return []
    cycles = _crossing_cycles(contours, crossings)
    behind = {}  # crossing: the overlap's crossings behind its corner
    for _, _, overlap_members, kind in cycles:
        if kind == 'overlap':
            for x in overlap_members:
                behind[x] = overlap_members
    loops = []
    for points, open_members, _, kind in cycles:
        if kind == 'hole' and open_members:
            part = sorted({y for x in open_members for y in behind.get(x, [x])} |
                          set(open_members))
            loops.append((points, open_members, part))
    return loops


def joined_outline(contours, crossings, part, fillets=None, ranges=None, report=None,
                   slots=None, others=()):
    """Join the outline where it crosses itself at the crossings in `part`
    (see crossing_loops), as one would draw the loop by hand: one outer
    contour and the hole as a contour of its own, without the overlap.

    At a corner left sharp the edges are not cut at their crossing: each goes
    on to the end of its piece, inside the other part's ink, and a line joins
    them there, so the corner is wherever the edges cross in any blend of the
    masters. `fillets` rounds the hole's corners instead: those are cut at the
    crossing and joined by one cubic (of no size at length 0). Per crossing,
    {'before': length along the edge arriving at the corner, 'after': along
    the edge leaving it, 'tension': % (100 about circular)} or a radius (a
    circular rounding as wide on both edges). `ranges` lay out pieces a
    crossing lands on in some master only (see _crossing_runs). Masters joined
    at the same crossings interpolate. `report`, a dict, gets per rounded
    crossing {'corner', 'start', 'end' (the ends of the rounding), 'in', 'out'
    (the edges' directions there), 'middle', 'before', 'after', 'tension'}.

    A rounding uses up edge pieces up to where it ends, and masters rounding
    by other lengths end in other pieces: blended point by point, one
    master's used-up piece would meet another's kept one. `slots`, a dict,
    gets per rounded crossing the pieces (index in the run arriving, in the
    run leaving) its ends lie in; `others` are the other masters' (each such
    a dict). From the node starting the furthest piece any master reaches
    to the rounding's end, every master has one cubic fitted to its edge
    (and pieces of no size to the corner), so the blends stay on the edge.
    """
    fillets = fillets or {}
    part = set(part)
    if not part:
        return contours
    events = _crossing_events(crossings, part)
    polygons = [[p for piece in contour for _, p in _flat_piece(piece)[:-1]]
                for contour in contours]
    ends = _overshoots(contours, events, crossings, fillets, polygons)
    built = _crossing_runs(contours, events, crossings, ranges, ends)
    if built is None:  # the ranges do not fit this master: lay it out on its own
        built = _crossing_runs(contours, events, crossings, None, ends)
    runs, starts, first_run = built
    lengths = [sum(_piece_samples(piece)[-1][1] for piece in pieces) for pieces, _ in runs]
    trim_start, trim_end, rounded, tensions = [0.0]*len(runs), [0.0]*len(runs), {}, {}
    for r, (pieces, (x, strand)) in enumerate(runs):
        if x not in fillets or not _junction_open(crossings[x], strand):
            continue
        after = starts[(x, 1 - strand)]
        t_in = _piece_tangent(pieces[-1], 1.0)
        t_out = _piece_tangent(runs[after][0][0], 0.0)
        turn = math.atan2(abs(t_in[0]*t_out[1] - t_in[1]*t_out[0]),
                          t_in[0]*t_out[0] + t_in[1]*t_out[1])
        # A sliver between nearly parallel edges is not a counter: no rounding.
        fade = (math.degrees(math.pi - turn) - CROSSING_SLIVER[0]) / \
            (CROSSING_SLIVER[1] - CROSSING_SLIVER[0])
        fade = min(max(fade, 0.0), 1.0)
        fade = fade*fade*(3-2*fade)
        spec = fillets[x]
        if isinstance(spec, dict):
            first = max(float(spec.get('before') or 0.0), 0.0)
            second = max(float(spec.get('after') or 0.0), 0.0)
            tensions[after] = max(float(spec.get('tension', 100.0) or 0.0), 0.0) / 100.0
        else:  # a radius: as wide on both edges as a circle in the corner
            first = second = max(float(spec or 0.0), 0.0) * \
                math.tan(min(turn, math.radians(FILLET_MAX_TURN)) / 2.0)
            tensions[after] = 1.0
        trim_end[r], trim_start[after] = first * fade, second * fade
        rounded[after] = r
    for r in range(len(runs)):
        # A run gives its ends at most its whole length, shared by what they ask.
        wanted = trim_start[r] + trim_end[r]
        if wanted > lengths[r]:
            share = lengths[r] / wanted if wanted > EPS else 0.0
            trim_start[r] *= share
            trim_end[r] *= share
        # A rounding ending just short of (or past) a node of its edge ends on
        # it: no sliver of an edge left between the node and the rounding.
        nodes, position = [], 0.0
        for piece in runs[r][0][:-1]:
            position += _piece_samples(piece)[-1][1]
            nodes.append(position)
        if trim_start[r] > 0:
            trim_start[r] = _onto_node(trim_start[r], nodes)
        if trim_end[r] > 0:
            trim_end[r] = lengths[r] - _onto_node(lengths[r] - trim_end[r], nodes)
    cut = [_cut_run(pieces, trim_start[r], trim_end[r]) for r, (pieces, _) in enumerate(runs)]
    own = {}
    for after, r in rounded.items():
        x = runs[r][1][0]
        own[x] = (_slot_at(runs[r][0], lengths[r] - trim_end[r]),
                  _slot_at(runs[after][0], trim_start[after]))
    if slots is not None:
        slots.update(own)
    regions = {}
    for after, r in rounded.items():
        x = runs[r][1][0]
        reached = [own[x]] + [other[x] for other in others if x in other]
        # Arriving: the furthest piece from the corner is the lowest; leaving, the highest.
        low = min(item[0] for item in reached)
        high = max(item[1] for item in reached)
        arriving, leaving = runs[r][0], runs[after][0]
        p1, d1, p2, d2 = cut[r][1], cut[r][2], cut[after][3], cut[after][4]
        h1, h2 = _rounding_handles(p1, d1, p2, d2, trim_end[r], trim_start[after],
                                   tensions[after])
        arc = ('cubic', (p1, add(p1, mul(d1, h1)), sub(p2, mul(d2, h2)), p2))
        start = sum(_piece_samples(piece)[-1][1] for piece in arriving[:low])
        end = sum(_piece_samples(piece)[-1][1] for piece in leaving[:high + 1])
        regions[after] = (arc, _extreme_region(
            arriving, start, lengths[r] - trim_end[r], arc, leaving, trim_start[after],
            end, crossings[x][0]))
        # The edges from the node before the rounding and to the node after it
        # are in the region's cubics now: their pieces have no size there.
        m = _piece_point(arriving[low], 0.0)
        n = _piece_point(leaving[high], 1.0)
        # (Each keeps its kind: which pieces a master's rounding reaches does
        # not change the structure.)
        cut[r] = (list(cut[r][0][:low]) + [(kind, (m,) * len(points)) for kind, points
                                            in arriving[low:]],) + tuple(cut[r][1:])
        cut[after] = ([(kind, (n,) * len(points)) for kind, points in leaving[:high + 1]] +
                      list(cut[after][0][high + 1:]),) + tuple(cut[after][1:])
    joined = {}
    for order in _cycle_orders(runs, starts, first_run, events):
        if all(not _junction_open(crossings[runs[r][1][0]], runs[r][1][1]) for r in order):
            continue  # the overlap of both parts: inside the ink, left out
        contour = []
        for k, r in enumerate(order):
            x, strand = runs[order[k-1]][1]
            if r in rounded:
                p1, d1, p2, d2 = cut[order[k-1]][1], cut[order[k-1]][2], cut[r][3], cut[r][4]
                arc, region = regions[r]
                contour.extend(region)
                if report is not None:
                    report[x] = {'corner': crossings[x][0], 'start': p1, 'end': p2,
                                 'in': d1, 'out': d2, 'middle': cubic(*arc[1], 0.5),
                                 'before': trim_end[order[k-1]], 'after': trim_start[r],
                                 'tension': tensions[r] * 100.0}
            elif x not in fillets:
                contour.append(('line', (cut[order[k-1]][1], cut[r][3])))
            contour.extend(cut[r][0])
        joined.setdefault(min(c for c in events), []).append(contour)
    result = []
    for c, contour in enumerate(contours):
        if c not in events:
            result.append(contour)
        elif c == min(events):
            result.extend(joined.get(c, []))
    return result


def _extreme_region(arriving, start, keep_to, arc, leaving, keep_from, end, corner):
    """Three cubics for the hole's outline from the node before a rounding
    (`start` along the run `arriving`) through the rounding `arc` to the node
    after it (`end` along `leaving`), meeting at the outline's extremes
    towards the corner there: where it is furthest that way horizontally
    (running upright) and vertically (running level).

    Every master then has nodes at the same places of the hole (its
    extremes, the nodes the edges had), whatever lengths it rounds by, and
    blends of masters stay smooth. An extreme at the region's end leaves the
    piece there of no size; each cubic is fitted to the outline it replaces."""
    samples = []  # (distance along the region, point, direction)
    position = 0.0

    def take(pieces, lo, hi):
        nonlocal position
        at = 0.0
        for piece in pieces:
            size = _piece_samples(piece)[-1][1]
            if size > 1e-9 and at + size >= lo - 1e-9 and at <= hi + 1e-9:
                table = _piece_samples(piece)
                a = _t_at_length(table, max(lo - at, 0.0))
                b = _t_at_length(table, min(hi - at, size))
                for i in range(25):
                    t = a + (b - a) * i / 24.0
                    point = _piece_point(piece, t)
                    if samples:
                        position += length(sub(point, samples[-1][1]))
                    samples.append((position, point, _piece_tangent(piece, t)))
            at += size
    take(arriving, start, keep_to)
    take([arc], 0.0, _piece_samples(arc)[-1][1])
    take(leaving, keep_from, end)
    if len(samples) < 2:
        point = samples[0][1] if samples else arc[1][0]
        return [('cubic', (point,) * 4)] * 3
    first, last = samples[0], samples[-1]
    toward = sub(corner, mul(add(arc[1][0], arc[1][3]), 0.5))
    marks = []
    for axis in (0, 1):
        sign = 1.0 if toward[axis] >= 0 else -1.0
        best = max(sample[1][axis] * sign for sample in samples)
        # The middle of a stretch at the extreme (an upright straight edge).
        tied = [k for k, sample in enumerate(samples) if sample[1][axis] * sign >= best - 1e-6]
        marks.append((tied[len(tied) // 2], axis))
    marks.sort()
    ends = [(0, None)] + marks + [(len(samples) - 1, None)]
    cubics = []
    for (k0, axis0), (k1, axis1) in zip(ends, ends[1:]):
        p0, p3 = samples[k0][1], samples[k1][1]
        if k1 <= k0 or length(sub(p3, p0)) < 1e-6:
            cubics.append(('cubic', (p0,) * 4))
            continue
        d0 = _upright_direction(samples[k0][2], axis0)
        d1 = _upright_direction(samples[k1][2], axis1)
        inner = samples[k0 + 1:k1]
        span = samples[k1][0] - samples[k0][0]
        us = [(sample[0] - samples[k0][0]) / span for sample in inner]
        cubics.append(('cubic', _handles(p0, p3, d0, d1, [sample[1] for sample in inner],
                                         us, length(sub(p3, p0)))))
    return cubics


def _upright_direction(direction, axis):
    """`direction` turned upright (axis 0: an x extreme) or level (axis 1),
    keeping its sense; unchanged for no axis."""
    if axis is None:
        return direction
    if axis == 0:
        return (0.0, 1.0 if direction[1] >= 0 else -1.0)
    return (1.0 if direction[0] >= 0 else -1.0, 0.0)


ROUNDING_NODE_CATCH = 2.0  # units: a rounding's end this close to a node goes onto it


def _onto_node(distance, nodes):
    """`distance` along a run moved onto the nearest of `nodes` (distances
    of the nodes between its pieces) within ROUNDING_NODE_CATCH."""
    near = [node for node in nodes if abs(node - distance) <= ROUNDING_NODE_CATCH and
            node > EPS]
    return min(near, key=lambda node: abs(node - distance)) if near else distance


def _slot_at(pieces, distance):
    """The index of the piece `distance` along a run lies in, as _cut_run
    finds it."""
    position = 0.0
    for i, piece in enumerate(pieces):
        size = _piece_samples(piece)[-1][1]
        if distance <= position + size:
            return i
        position += size
    return len(pieces) - 1


def _rounding_handles(p1, d1, p2, d2, first, second, tension):
    """Handle lengths of the cubic rounding a corner from p1 (going along d1)
    to p2 (arriving along d2): an arc fitting both ends' own directions, the
    handles reaching `tension` (1 about circular) of the way an arc would to
    where the two directions cross. Curved edges turn on before the corner,
    so the ends' directions, not the corner's, give the arc."""
    turn = math.acos(max(-1.0, min(1.0, d1[0]*d2[0] + d1[1]*d2[1])))
    denominator = d1[0]*d2[1] - d1[1]*d2[0]
    if turn > 1e-6 and abs(denominator) > 1e-9:
        q = sub(p2, p1)
        a = (q[0]*d2[1] - q[1]*d2[0]) / denominator
        b = (q[0]*d1[1] - q[1]*d1[0]) / denominator
        if a > 0 and b > 0:
            f = 4.0/3.0 * math.tan(turn/4.0) / math.tan(turn/2.0)
            return a * f * tension, b * f * tension
    # The directions do not meet ahead of both ends: by the lengths cut instead.
    f = 4.0/3.0 * math.tan(turn/4.0) / math.tan(turn/2.0) if turn > 1e-6 else 2.0/3.0
    return first * f * tension, second * f * tension


def _overshoots(contours, events, crossings, fillets, polygons):
    """(contour, piece, t, point) where each strand of a sharp corner ends:
    the edge arriving at the corner goes on past the crossing, the edge
    leaving it starts before, both inside the overlap of the two parts: to
    the end of the piece, or half way to the next crossing on it. A join
    line that would leave the ink is pulled back towards the crossing.
    {(crossing, strand): (t, point)}; fillet corners stay at the crossing."""
    ends = {}
    for c, marks in events.items():
        marks = sorted(marks)
        for k, (i, t, x, strand) in enumerate(marks):
            point = crossings[x][0]
            if x in fillets:
                ends[(x, strand)] = (t, point)
                continue
            forward = _junction_open(crossings[x], strand)  # arriving here: go on
            others = [mark[1] for mark in marks if mark[0] == i and
                      (mark[1] > t if forward else mark[1] < t)]
            limit = (min(others) if others else 1.0) if forward else \
                (max(others) if others else 0.0)
            target = limit if not others else (t + limit) / 2.0
            ends[(x, strand)] = (target, _piece_point(contours[c][i], target), t, c, i)
    result = {}
    for (x, strand), value in ends.items():
        if len(value) == 2:
            result[(x, strand)] = value
            continue
        if (x, strand) in result:
            continue
        other = 1 - strand
        mine, theirs = value, ends[(x, other)]
        # The arriving strand's end and the leaving one's start are joined.
        for _ in range(6):
            p, q = mine[1], theirs[1]
            if all(_winding(polygons, add(mul(p, 1-u), mul(q, u))) != 0
                   for u in (0.0, 0.25, 0.5, 0.75, 1.0)):
                break
            mine = _pulled(contours, mine)
            theirs = _pulled(contours, theirs)
        result[(x, strand)] = (mine[0], mine[1])
        result[(x, other)] = (theirs[0], theirs[1])
    return result


def _pulled(contours, end):
    target, _, t, c, i = end
    target = (target + t) / 2.0
    return (target, _piece_point(contours[c][i], target), t, c, i)


def _as_cubic(piece):
    kind, pts = piece
    if kind == 'cubic':
        return piece
    a, b = pts
    return ('cubic', (a, add(a, mul(sub(b, a), 1/3.0)), add(a, mul(sub(b, a), 2/3.0)), b))


def _crossing_runs(contours, events, crossings, ranges, ends=None):
    """(runs, starts, first run per contour): runs are (pieces, (crossing,
    strand) at the end) from one crossing to the next along a contour, and
    starts[(crossing, strand)] the run leaving there. `ends[(crossing,
    strand)]` = (t, point) moves where the run arriving there ends or the one
    leaving starts (see _overshoots); otherwise both are at the crossing.

    `ranges[x]` gives, per strand of crossing x, the (first, last) piece it
    lands on in any master: a run then has a slot for every piece its two
    crossings may land on, and the slots a crossing has not reached in this
    master are pieces of no size at the run's end, inside the overlap of both
    parts or at a rounded corner, out of sight. None when this master's
    crossings do not fit the ranges."""
    runs, starts, first_run = [], {}, {}
    for c in sorted(events):
        contour, marks = contours[c], sorted(events[c])
        n = len(contour)
        first_run[c] = len(runs)
        for k, (i0, t0, x0, s0) in enumerate(marks):
            i1, t1, x1, s1 = marks[(k+1) % len(marks)]
            once = len(marks) == 1 or (i1 == i0 and t1 <= t0)  # all the way round
            p0, p1 = crossings[x0][0], crossings[x1][0]
            if ends is not None:
                t0, p0 = ends.get((x0, s0), (t0, p0))
                t1, p1 = ends.get((x1, s1), (t1, p1))
            if i1 == i0 and not once:
                pieces = [_sub_piece(contour[i0], t0, t1)]
            else:
                pieces = [_sub_piece(contour[i0], t0, 1.0)]
                pieces += [contour[(i0+1+m) % n] for m in range((i1-i0-1) % n)]
                pieces.append(_sub_piece(contour[i1], 0.0, t1))
            pieces[0] = _with_end(pieces[0], False, p0)
            pieces[-1] = _with_end(pieces[-1], True, p1)
            if ranges is not None:
                lo0, hi1 = ranges[x0][s0][0], ranges[x1][s1][1]
                front, back = (i0 - lo0) % n, (hi1 - i1) % n
                slots = (hi1 - lo0) % n + 1 + (n if once else 0)
                if front + len(pieces) + back != slots or front > n // 2 or back > n // 2:
                    return None
                if ranges[x0][s0] != (i0, i0) or ranges[x1][s1] != (i1, i1):
                    # Slots that hold a piece in one master and none in another
                    # must be one kind in all of them.
                    pieces = [_as_cubic(piece) for piece in pieces]
                pieces = _padded(pieces, front, False)
                pieces = _padded(pieces, back, True)
            starts[(x0, s0)] = len(runs)
            runs.append((pieces, (x1, s1)))
    return runs, starts, first_run


def _padded(pieces, extra, at_end):
    """`pieces` with `extra` pieces of no size at their start or end."""
    if not extra:
        return pieces
    point = pieces[-1][1][-1] if at_end else pieces[0][1][0]
    filler = [('cubic', (point,) * 4)] * extra
    return pieces + filler if at_end else filler + pieces


def _cut_run(pieces, trim_start, trim_end):
    """Pieces of a run without `trim_start` / `trim_end` of its length at its
    ends, and (pieces, end point, end direction, start point, start direction).
    A piece the trim uses up stays as one of no length at the trim's point,
    so the structure does not depend on how far the trims reach."""
    samples = [_piece_samples(piece) for piece in pieces]
    lengths = [sample[-1][1] for sample in samples]
    total = sum(lengths)
    keep_from, keep_to = trim_start, max(trim_start, total - trim_end)

    def locate(distance):
        position = 0.0
        for i, size in enumerate(lengths):
            if distance <= position + size or i == len(lengths) - 1:
                local = min(max(distance - position, 0.0), size)
                t = _t_at_length(samples[i], local) if size > EPS else 0.0
                return i, t
            position += size
    i0, t0 = locate(keep_from)
    i1, t1 = locate(keep_to)
    if (i1, t1) < (i0, t0):
        i1, t1 = i0, t0
    start = _piece_point(pieces[i0], t0)
    end = _piece_point(pieces[i1], t1)
    result = []
    for i, piece in enumerate(pieces):
        kind, pts = piece
        if i < i0 or i > i1:
            point = start if i < i0 else end
            result.append((kind, (point,) * len(pts)))
        elif i0 == i1 == i or (i == i0 and t0 > 0.0) or (i == i1 and t1 < 1.0):
            a, b = (t0 if i == i0 else 0.0), (t1 if i == i1 else 1.0)
            result.append(_sub_piece(piece, a, b) if b > a else (kind, ((start if i == i0
                          else end),) * len(pts)))
        else:
            result.append(piece)
    return (result, end, _piece_tangent(pieces[i1], t1), start,
            _piece_tangent(pieces[i0], t0))


def _with_end(piece, at_end, point):
    kind, pts = piece
    pts = list(pts)
    pts[-1 if at_end else 0] = point
    return (kind, tuple(pts))
