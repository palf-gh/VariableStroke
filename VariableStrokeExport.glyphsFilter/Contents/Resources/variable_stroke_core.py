"""Pure geometry for Variable Stroke. Coordinates are Glyphs font units."""
from __future__ import division
import math

CAPS = ('flat', 'round', 'square', 'horizontal', 'vertical', 'angle')
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


def cubic(p0, p1, p2, p3, t):
    s = 1 - t
    return add(add(mul(p0, s*s*s), mul(p1, 3*s*s*t)),
               add(mul(p2, 3*s*t*t), mul(p3, t*t*t)))


def cubic_derivative(p0, p1, p2, p3, t):
    s = 1 - t
    return add(add(mul(sub(p1, p0), 3*s*s), mul(sub(p2, p1), 6*s*t)),
               mul(sub(p3, p2), 3*t*t))


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
    if style == 'round':
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


def _round_cap(center, radius, tangent, start):
    """Two exact-quarter-circle cubic approximations, in contour order."""
    outward = mul(tangent, -1 if start else 1)
    n = normal(tangent)
    left = add(center, mul(n, radius))
    right = sub(center, mul(n, radius))
    tip = add(center, mul(outward, radius))
    k = 0.5522847498307936*radius
    if start:
        # right -> outward tip -> left
        return [('cubic', (right, add(right, mul(outward, k)), sub(tip, mul(n, k)), tip)),
                ('cubic', (tip, add(tip, mul(n, k)), add(left, mul(outward, k)), left))]
    return [('cubic', (left, add(left, mul(outward, k)), add(tip, mul(n, k)), tip)),
            ('cubic', (tip, sub(tip, mul(n, k)), add(right, mul(outward, k)), right))]


def _cap_segments(center, width, tangent, style, at_end, left, right, nib=None, slant=0.0):
    if style == 'round':
        if nib is None:
            return _round_cap(center, width/2, tangent, not at_end)
        # A plain half circle spanning the stroke, perpendicular to its direction.
        w, h, o = nib
        n = normal(tangent)
        half = _half_thickness(n, w, h, slant)
        return _round_cap(add(center, mul(n, half*o)), half, tangent, not at_end)
    return [('line', (left, right))] if at_end else [('line', (right, left))]


FIT_TOLERANCE = 1.0
MITER_LIMIT = 4.0
NEAR_STRAIGHT = math.sin(math.radians(10))  # below this kink a corner fades to smooth


def _nib(value):
    """Normalize a node's stroke to (width, height, offset).

    `value` is a width, or (width, height[, offset]). Height None means round
    (height = width). Offset is -1..1: 0 keeps the centerline in the middle,
    1 puts the whole stroke on the left of the path direction, -1 on the right.
    """
    if isinstance(value, (tuple, list)):
        w = float(value[0])
        h = float(value[1]) if len(value) > 1 and value[1] is not None else w
        o = float(value[2]) if len(value) > 2 and value[2] is not None else 0.0
    else:
        w = h = float(value)
        o = 0.0
    if w <= 0 or h <= 0:
        raise ValueError('Width must be positive')
    return (w, h, max(-1.0, min(1.0, o)))


def _nib_matrix_apply(q, a, b, slant):
    # Unit circle -> nib ellipse: semi-axis a horizontal, b along the italic
    # direction (0, 1) sheared by `slant` = tan(italic angle).
    return (a*q[0] + b*slant*q[1], b*q[1])


def _nib_direction(n, a, b, slant):
    """Unit-circle parameter of the nib point that lies furthest in direction n."""
    v = (a*n[0], b*(slant*n[0] + n[1]))
    return unit(v) if length(v) > EPS else (0.0, 0.0)


def _support(n, a, b, slant):
    return _nib_matrix_apply(_nib_direction(n, a, b, slant), a, b, slant)


def _half_thickness(n, w, h, slant):
    """Half the stroke thickness across direction n (unit normal of the stroke).

    Vertical strokes get the width, horizontal ones the height and diagonals a
    smooth blend (the reach of a w x h ellipse whose height axis follows the
    italic angle). The edges themselves are always placed along n, so ends
    and caps stay perpendicular to the stroke.
    """
    edge = _support(n, w/2.0, h/2.0, slant)
    return max(edge[0]*n[0] + edge[1]*n[1], 0.0)


def nib_edges(point, tangent, nib, italic_angle=0.0):
    """Left and right outline points of the stroke at a centerline point."""
    w, h, o = _nib(nib)
    n = normal(unit(tangent))
    half = _half_thickness(n, w, h, math.tan(math.radians(italic_angle or 0.0)))
    return add(point, mul(n, half*(1+o))), sub(point, mul(n, half*(1-o)))


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
        lefts.append(_Side(kind, pts, e0, e1, 1, slant))
        rights.append(_Side(kind, pts, e0, e1, -1, slant))
        widths.append(lefts[-1].extent(1.0))
        indices.append(index)
    if not lefts:
        return {}
    count = len(lefts)
    for sides in (lefts, rights):
        for k in range(count if closed else count-1):
            _join(sides[k], sides[(k+1) % count], widths[k])
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
    ab = sub(b, a)
    denominator = ab[0]*ab[0] + ab[1]*ab[1]
    t = 0.0 if denominator < EPS else max(0.0, min(1.0, ((p[0]-a[0])*ab[0] + (p[1]-a[1])*ab[1]) / denominator))
    return length(sub(p, add(a, mul(ab, t))))


class _Side(object):
    """One side (sign +1 left, -1 right) of one centerline segment, trimmable by t."""

    def __init__(self, kind, pts, e0, e1, sign, slant=0.0):
        if kind not in ('line', 'cubic'):
            raise ValueError('Unsupported segment: ' + str(kind))
        self.kind, self.pts, self.sign, self.slant = kind, pts, sign, slant
        self.n0, self.n1 = _nib(e0), _nib(e1)
        self.w0, self.w1 = self.n0[0], self.n1[0]
        self.ta, self.tb = 0.0, 1.0
        self.start, self.end = self.at(0.0), self.at(1.0)

    def nib(self, t):
        return tuple(x*(1-t) + y*t for x, y in zip(self.n0, self.n1))

    def extent(self, t):
        w, h, _ = self.nib(t)
        return max(w, h)

    def at(self, t):
        pts = self.pts
        p = add(mul(pts[0], 1-t), mul(pts[1], t)) if self.kind == 'line' else cubic(*pts, t)
        w, h, o = self.nib(t)
        n = normal(self.tangent(t))
        half = _half_thickness(n, w, h, self.slant)
        # Edges sit on the normal; o slides the stroke sideways across the centerline.
        return add(p, mul(n, half*(1+o))) if self.sign > 0 else sub(p, mul(n, half*(1-o)))

    def tangent(self, t):
        # The centerline's own direction: both sides and both neighbours of a smooth
        # node share it, so smooth centerline nodes stay smooth in the outline.
        return unit(_derivative(self.kind, self.pts, t))

    def polyline(self, count=48):
        return [self.start] + [self.at(self.ta + (self.tb-self.ta)*i/float(count))
                               for i in range(1, count)] + [self.end]

    def piece(self):
        if self.kind == 'line':
            return ('line', (self.start, self.end))
        p0, p3 = self.start, self.end
        chord = length(sub(p3, p0))
        d0, d1 = self.tangent(self.ta), self.tangent(self.tb)
        count = 16
        us = [i/float(count) for i in range(1, count)]
        samples = [self.at(self.ta + (self.tb-self.ta)*u) for u in us]
        best = None
        for _ in range(4):
            controls = _handles(p0, p3, d0, d1, samples, us, chord)
            curve = [cubic(*controls, i/64.0) for i in range(65)]
            nearest = [min(range(65), key=lambda i: length(sub(p, curve[i]))) for p in samples]
            error = max(min(_point_segment_distance(p, curve[j], curve[j+1])
                            for j in (i-1, i) if 0 <= j < 64) for p, i in zip(samples, nearest))
            if best is None or error < best[0]:
                best = (error, controls)
            us = [min(max(i/64.0, 1e-3), 1-1e-3) for i in nearest]
        return ('cubic', best[1])


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
    if length(sub(a.end, b.start)) < 0.05:
        b.start = a.end
        return
    reach = MITER_LIMIT * width / 2.0
    ta, tb = a.tangent(a.tb), b.tangent(b.ta)
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
    a.tb, b.ta = a_tb, b_ta
    a.end = b.start = point


def _side_crossing(a, b, ta, tb, reach):
    """Where side a (extended past its end) crosses side b (extended before its
    start), measured along the sides from the corner: (cost, point, a.tb, b.ta).
    Only crossings within 2*reach along the sides count."""
    count = 48
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


def _side_contour(sides, widths, closed):
    count = len(sides)
    for k in range(count if closed else count-1):
        _join(sides[k], sides[(k+1) % count], widths[k])
    return [side.piece() for side in sides]


def _reverse(pieces):
    return [(kind, tuple(reversed(points))) for kind, points in reversed(pieces)]


def _trim(side, at_end, center, m):
    """Cut `side` back to where it crosses the line through `center` with normal m."""
    count = 64
    ts = [side.ta + (side.tb-side.ta)*i/float(count) for i in range(count+1)]
    if at_end:
        ts.reverse()

    def distance(point):
        return (point[0]-center[0])*m[0] + (point[1]-center[1])*m[1]

    previous_t, previous = ts[0], distance(side.end if at_end else side.start)
    for t in ts[1:]:
        current = distance(side.at(t))
        if previous == 0 or (previous > 0) != (current > 0):
            f = previous / (previous-current) if previous != current else 0.0
            hit_t = previous_t + (t-previous_t)*f
            point = side.at(hit_t)
            point = sub(point, mul(m, distance(point)))  # exactly on the cut line
            if at_end:
                side.tb, side.end = hit_t, point
            else:
                side.ta, side.start = hit_t, point
            return True
        previous_t, previous = t, current
    return False


def _apply_cap(side, at_end, center, tangent, style, nib, slant, angle=0.0):
    """Move the side's end onto the cap: extend along the end tangent or trim back.
    The side keeps its single piece, so every cap has the same node count."""
    p = side.end if at_end else side.start
    outward = tangent if at_end else mul(tangent, -1)
    target = None
    if style == 'square':
        # Extend by half the stroke thickness: square corners, like a square cap.
        w, h, _ = nib
        target = add(p, mul(outward, _half_thickness(normal(tangent), w, h, slant)))
    elif style in CUTS:
        m = normal(_cut_direction(style, slant, angle))
        along = outward[0]*m[0] + outward[1]*m[1]
        if abs(along) > 1e-6:  # otherwise the stroke runs along the cut
            s = ((center[0]-p[0])*m[0] + (center[1]-p[1])*m[1]) / along
            if s >= 0:
                target = add(p, mul(outward, s))
            elif not _trim(side, at_end, center, m):
                target = add(p, mul(outward, s))
    if target is not None:
        if at_end:
            side.end = target
        else:
            side.start = target


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


def outline_curves(segments, closed=False, cap_start='flat', cap_end='flat',
                   tolerance=FIT_TOLERANCE, italic_angle=0.0, start_angle=0.0, end_angle=0.0):
    """Return closed contours of ('line'|'cubic', control points) segments.

    Each segment is (kind, points, start, end) where start/end is a width or a
    (width, height, offset) nib; see _nib. `italic_angle` (degrees) tilts the
    nib's height axis and vertical cuts. `start_angle` / `end_angle` (degrees
    on the page) are the cut directions of 'angle' caps.
    """
    for style in (cap_start, cap_end):
        if style not in CAPS:
            raise ValueError('Unknown cap: ' + str(style))
    slant = math.tan(math.radians(italic_angle or 0.0))
    lefts, rights, widths = [], [], []
    for kind, pts, e0, e1 in segments:
        if all(length(sub(p, pts[0])) < EPS for p in pts):
            continue  # zero-length segment
        lefts.append(_Side(kind, pts, e0, e1, 1, slant))
        rights.append(_Side(kind, pts, e0, e1, -1, slant))
        widths.append(lefts[-1].extent(1.0))
    if not lefts:
        return []
    if closed:
        return [_side_contour(lefts, widths, True),
                _reverse(_side_contour(rights, widths, True))]
    first, last = lefts[0], lefts[-1]
    start_center, start_tangent = first.pts[0], first.tangent(0.0)
    end_center, end_tangent = last.pts[-1], last.tangent(1.0)
    cap_start = _usable_cap(cap_start, start_tangent, slant, start_angle)
    cap_end = _usable_cap(cap_end, end_tangent, slant, end_angle)
    for sides in (lefts, rights):
        _apply_cap(sides[0], False, start_center, start_tangent, cap_start, first.n0, slant,
                   start_angle)
        _apply_cap(sides[-1], True, end_center, end_tangent, cap_end, last.n1, slant, end_angle)
    left = _side_contour(lefts, widths, False)
    right = _side_contour(rights, widths, False)
    contour = left
    contour += _cap_segments(end_center, last.w1, end_tangent, cap_end, True,
                             lefts[-1].end, rights[-1].end, last.n1, slant)
    contour += _reverse(right)
    contour += _cap_segments(start_center, first.w0, start_tangent, cap_start, False,
                             lefts[0].start, rights[0].start, first.n0, slant)
    return [contour]
