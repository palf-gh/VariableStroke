"""Pure geometry for Variable Stroke. Coordinates are Glyphs font units."""
from __future__ import division
import math

CAPS = ('flat', 'round', 'square', 'horizontal', 'vertical')
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
    return fit(0, len(points)-1)


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


def _cap_segments(center, width, tangent, style, at_end, left, right):
    if style == 'round':
        return _round_cap(center, width/2, tangent, not at_end)
    return [('line', (left, right))] if at_end else [('line', (right, left))]


def outline_curves(segments, closed=False, cap_start='flat', cap_end='flat', tolerance=0.35):
    """Return closed contours of ('line'|'cubic', control points) segments."""
    samples = sample_segments(segments, closed)
    if len(samples) < 2:
        return []
    left, right, tangents = _side_points(samples, closed)
    if closed:
        contours = []
        for side in (left, list(reversed(right))):
            side = side + [side[0]]
            curves = _fit_side(side, tolerance)
            if curves:
                contours.append(curves)
        return contours
    start = _cap(samples[0][0], samples[0][2], tangents[0], cap_start, False)
    end = _cap(samples[-1][0], samples[-1][2], tangents[-1], cap_end, True)
    left[0], right[0] = start[0], start[1]
    left[-1], right[-1] = end[0], end[1]
    contour = _fit_side(left, tolerance)
    contour += _cap_segments(samples[-1][0], samples[-1][2], tangents[-1], cap_end, True,
                             end[0], end[1])
    contour += _fit_side(list(reversed(right)), tolerance)
    contour += _cap_segments(samples[0][0], samples[0][2], tangents[0], cap_start, False,
                             start[0], start[1])
    return [contour] if contour else []
