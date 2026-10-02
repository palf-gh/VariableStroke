"""Pure geometry for Variable Stroke. Coordinates are Glyphs font units."""
from __future__ import division
import math

CAPS = ('flat', 'round', 'ellipse', 'square', 'horizontal', 'vertical', 'angle')
CUTS = ('horizontal', 'vertical', 'angle')
EPS = 1e-9


def add(a, b): return (a[0] + b[0], a[1] + b[1])
def sub(a, b): return (a[0] - b[0], a[1] - b[1])
def mul(a, k): return (a[0] * k, a[1] * k)
def length(a): return math.hypot(a[0], a[1])
def unit(a):
    d = length(a)
    return (a[0] / d, a[1] / d) if d > EPS else (1.0, 0.0)
def normal(a): return (-a[1], a[0])


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


def _cap_segments(center, width, tangent, style, at_end, left, right, nib=None, slant=0.0):
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
    width, height, _, angle = _nib(nib)
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
    (height = width). Offset is -1..1: 0 keeps the centerline in the middle,
    1 puts the whole stroke on the left of the path direction, -1 on the right.
    Rotation turns the width and height axes of the ellipse on the page.
    """
    if isinstance(value, (tuple, list)):
        w = float(value[0])
        h = float(value[1]) if len(value) > 1 and value[1] is not None else w
        o = float(value[2]) if len(value) > 2 and value[2] is not None else 0.0
        rotation = float(value[3]) if len(value) > 3 and value[3] is not None else 0.0
    else:
        w = h = float(value)
        o = 0.0
        rotation = 0.0
    if w <= 0 or h <= 0:
        raise ValueError('Width must be positive')
    return (w, h, max(-1.0, min(1.0, o)), rotation % 180.0)


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
    w, h, o, angle = _nib(nib)
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
    w, h, o, rotation = _nib(nib)
    n = normal(unit(tangent))
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
#   * every cap is a single line, except round caps which add two quarter arcs.
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

    def __init__(self, kind, pts, e0, e1, sign, slant=0.0, shared=None):
        if kind not in ('line', 'cubic'):
            raise ValueError('Unsupported segment: ' + str(kind))
        self.kind, self.pts, self.sign, self.slant = kind, pts, sign, slant
        # {t: (left, right)}: both sides of a segment sample the same centerline
        # points, so the pair shares one table (see _side_pair).
        self._edges = {} if shared is None else shared
        self.n0, self.n1 = _nib(e0), _nib(e1)
        self.w0, self.w1 = self.n0[0], self.n1[0]
        self.ta, self.tb = 0.0, 1.0
        self.start, self.end = self.at(0.0), self.at(1.0)
        self.override_piece = None
        self.join_extension_ratio = 0.0
        self.smooth_start = self.smooth_end = False

    def nib(self, t):
        values = [x*(1-t) + y*t for x, y in zip(self.n0[:3], self.n1[:3])]
        # The two axes describe the same ellipse after a 180-degree turn.
        # Interpolating normalized end angles directly (e.g. 179 -> 1)
        # would rotate through 90 degrees and make the outline swell midway.
        turn = (self.n1[3] - self.n0[3] + 90.0) % 180.0 - 90.0
        return tuple(values + [self.n0[3] + turn*t])

    def extent(self, t):
        w, h, _, _ = self.nib(t)
        return max(w, h)

    def at(self, t):
        edges = self._edges.get(t)
        if edges is None:
            pts = self.pts
            p = add(mul(pts[0], 1-t), mul(pts[1], t)) if self.kind == 'line' else cubic(*pts, t)
            edges = self._edges[t] = nib_edges(p, self.tangent(t), self.nib(t))
        return edges[0] if self.sign > 0 else edges[1]

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
        best = None
        for _ in range(4):
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
        if abs(ta) < EPS and (self.smooth_start or abs(self.n0[3]) > EPS):
            handle = min(max(length(sub(p1, p0)), 0.05*chord), 0.75*chord)
            p1 = add(p0, mul(center0, handle))
        if abs(tb-1) < EPS and (self.smooth_end or abs(self.n1[3]) > EPS):
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


def _side_pair(kind, pts, e0, e1, slant=0.0):
    """Left and right side of one centerline segment, sharing their samples."""
    shared = {}
    return (_Side(kind, pts, e0, e1, 1, slant, shared),
            _Side(kind, pts, e0, e1, -1, slant, shared))


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
    if a.kind == 'cubic' or b.kind == 'cubic':
        crossing = _side_crossing(a, b, ta, tb, reach)
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
    if distance > reach:
        point = add(middle, mul(sub(point, middle), reach / distance))
    a_controls = (a.override_piece or a.piece())[1] if a.kind == 'cubic' else None
    b_controls = (b.override_piece or b.piece())[1] if b.kind == 'cubic' else None
    a_trim = ((a_tb-a.ta)/(a.tb-a.ta) if a_controls is not None and a.tb-a.ta > EPS
              else 1.0)
    b_trim = ((b_ta-b.ta)/(b.tb-b.ta) if b_controls is not None and b.tb-b.ta > EPS
              else 0.0)
    a_on_curve = (a_controls is not None and a_trim < 1-1e-4 and
                  length(sub(a.at(a_tb), point)) < max(0.5, 0.01*width))
    b_on_curve = (b_controls is not None and b_trim > 1e-4 and
                  length(sub(b.at(b_ta), point)) < max(0.5, 0.01*width))
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
        point = (min(center[0]+x_limit, max(center[0]-x_limit, point[0])),
                 min(center[1]+y_limit, max(center[1]-y_limit, point[1])))
    a.tb, b.ta = a_tb, b_ta
    a.end = b.start = point
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


def _side_contour(sides, widths, closed, joined=False):
    count = len(sides)
    if not joined:
        for k in range(count if closed else count-1):
            _join(sides[k], sides[(k+1) % count], widths[k])
    return [side.override_piece or side.piece() for side in sides]


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
    """Keep a requested cut when an exact cubic continuation turns away from it."""
    p0, p1, p2, p3 = controls
    if at_end:
        chord = sub(target, p0)
        direction = unit(sub(p3, p2))
        if direction[0]*chord[0] + direction[1]*chord[1] < 0.5*length(chord):
            direction = unit(chord)
        handle = min(max(length(sub(p3, p2)), 0.08*length(chord)),
                     0.4*length(chord))
        return (p0, p1, sub(target, mul(direction, handle)), target)
    chord = sub(p3, target)
    direction = unit(sub(p1, p0))
    if direction[0]*chord[0] + direction[1]*chord[1] < 0.5*length(chord):
        direction = unit(chord)
    handle = min(max(length(sub(p1, p0)), 0.08*length(chord)),
                 0.4*length(chord))
    return (target, add(target, mul(direction, handle)), p2, p3)


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


def _bend_join_endpoint(controls, at_end, target):
    """Extend one cubic while retaining the width-derived offset interval."""
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
            w, h, _, _ = nib
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
            side.override_piece = ('line', (side.start, side.end))
        return True

    controls = (side.override_piece or side.piece())[1]
    edge_point = cubic(*controls, edge)
    fallback_direction = mul(tangent, outward)

    def keep_cut():
        if style == 'square':
            w, h, _, _ = nib
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
        side.override_piece = ('cubic', _bend_curve_endpoint(controls, at_end, target))
        if at_end:
            side.end = target
        else:
            side.start = target
        return True

    derivative = mul(cubic_derivative(*controls, edge), outward)
    if length(derivative) < EPS:
        return keep_cut()
    initial = unit(derivative)
    max_travel = min(0.75*side.extent(side.tb if at_end else side.ta),
                     0.5*length(sub(side.pts[-1], side.pts[0])))
    if max_travel < EPS:
        return keep_cut()
    if style == 'square':
        w, h, _, _ = nib
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
                    unit(direction)[1]*initial[1] < 0.85 or travelled > max_travel:
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
    """A cut nearly parallel to the stroke would need a spike longer than MITER_LIMIT
    half-widths; such caps fall back to flat (same node count)."""
    if style in CUTS:
        m = normal(_cut_direction(style, slant, angle))
        n = normal(tangent)
        along = abs(tangent[0]*m[0] + tangent[1]*m[1])
        across = abs(n[0]*m[0] + n[1]*m[1])
        if along < 1e-6 or across / along > MITER_LIMIT:
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
    just gets a zero-size arc. Trims are limited to half of each neighbouring
    piece so arcs never overlap. Rounded vertices are reported (see outline_curves)
    when `report` is a list.
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
        first = min(first, lengths[i-1] / 2.0)
        second = min(second, lengths[i] / 2.0)
        trim_end[i-1], trim_start[i] = first, second
        arcs[i] = (turn, first, second, tension_value, vertex, t_in, t_out)
    trimmed = []
    for i, piece in enumerate(contour):
        t0 = _t_at_length(samples[i], trim_start[i]) if trim_start[i] > 0 else 0.0
        t1 = _t_at_length(samples[i], lengths[i] - trim_end[i]) if trim_end[i] > 0 else 1.0
        trimmed.append(_sub_piece(piece, t0, max(t0, t1)) if (t0 > 0 or t1 < 1) else piece)
    result = []
    for i, piece in enumerate(trimmed):
        if i in arcs:
            turn, first, second, tension, vertex, t_in, t_out = arcs[i]
            before = trimmed[i-1]
            p1, p2 = before[1][-1], piece[1][0]
            d1, d2 = _piece_tangent(before, 1.0), _piece_tangent(piece, 0.0)
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
                   corner_of, cap_end, cap_start):
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

    start = at(start_node, 'outer', cap=True) if cap_start not in ('round', 'ellipse') else None
    end = at(end_node, 'outer', cap=True) if cap_end not in ('round', 'ellipse') else None
    vertices = [start] + [interior(k, False) for k in range(1, n)]
    vertices += [end] + [None] * (cap_pieces_end - 1)
    vertices += [dict(end, flip=True, which='inner') if end else None]
    vertices += [interior(n-j, True) for j in range(1, n)]
    vertices += [dict(start, flip=True, which='inner') if start else None] + [None] * (cap_pieces_start - 1)
    return vertices


def outline_curves(segments, closed=False, cap_start='flat', cap_end='flat',
                   tolerance=FIT_TOLERANCE, italic_angle=0.0, start_angle=0.0, end_angle=0.0,
                   corner_radii=None, report=None, edges=None):
    """Return closed contours of ('line'|'cubic', control points) segments.

    Each segment is (kind, points, start, end) where start/end is a width or a
    (width, height, offset) nib; see _nib. `italic_angle` (degrees) tilts
    vertical cuts. `start_angle` / `end_angle` (degrees
    on the page) are the cut directions of 'angle' caps. `corner_radii[i]` is the
    live corner of on-curve node i (segment i starts at node i): None (sharp), a
    radius or a dict (see _corner_spec). `report`, if a list, collects a dict per
    rounded corner: node, which ('outer'|'inner'), corner point, arc midpoint, the
    arc's points p1 c1 c2 p2, the turn angle, both trims and orientation flags.
    `edges`, if a dict, receives node_edges' result from the same joins, so the
    editor's width handles cost nothing extra.
    """
    for style in (cap_start, cap_end):
        if style not in CAPS:
            raise ValueError('Unknown cap: ' + str(style))
    slant = math.tan(math.radians(italic_angle or 0.0))
    lefts, rights, widths, indices = [], [], [], []
    for index, (kind, pts, e0, e1) in enumerate(segments):
        if all(length(sub(p, pts[0])) < EPS for p in pts):
            continue  # zero-length segment
        left, right = _side_pair(kind, pts, e0, e1, slant)
        lefts.append(left)
        rights.append(right)
        widths.append(lefts[-1].extent(1.0))
        indices.append(index)
    if not lefts:
        return []

    def corner_of(node):
        if not corner_radii:
            return None
        node = node % len(corner_radii) if closed else node
        return _corner_spec(corner_radii[node]) if 0 <= node < len(corner_radii) else None

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
        for sides in (lefts, rights):
            for k in range(n):
                _join(sides[k], sides[(k+1) % n], widths[k])
        if edges is not None:
            edges.update(_edge_map(lefts, rights, indices, True))
        for side in lefts + rights:
            _fillet_side(side)
            _optical_outer_curve(side)
        outer = _side_contour(lefts, widths, True, joined=True)
        inner = _reverse(_side_contour(rights, widths, True, joined=True))
        return [_round_contour(outer, [closed_vertex(k, False) for k in range(n)], report),
                _round_contour(inner, [closed_vertex((n-j) % n, True) for j in range(n)],
                               report)]
    first, last = lefts[0], lefts[-1]
    start_center, start_tangent = first.pts[0], first.tangent(0.0)
    end_center, end_tangent = last.pts[-1], last.tangent(1.0)
    cap_start = _usable_cap(cap_start, start_tangent, slant, start_angle)
    cap_end = _usable_cap(cap_end, end_tangent, slant, end_angle)
    for sides in (lefts, rights):
        for k in range(len(sides)-1):
            _join(sides[k], sides[k+1], widths[k])
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
    left = _side_contour(lefts, widths, False, joined=True)
    right = _side_contour(rights, widths, False, joined=True)
    end_cap = _cap_segments(end_center, last.w1, end_tangent, cap_end, True,
                            lefts[-1].end, rights[-1].end, last.n1, slant)
    start_cap = _cap_segments(start_center, first.w0, start_tangent, cap_start, False,
                              lefts[0].start, rights[0].start, first.n0, slant)
    contour = left + end_cap + _reverse(right) + start_cap
    if corner_radii:
        contour = _round_contour(contour, _open_vertices(
            lefts, indices, len(end_cap), len(start_cap), corner_of,
            cap_end, cap_start), report)
    return [contour]
