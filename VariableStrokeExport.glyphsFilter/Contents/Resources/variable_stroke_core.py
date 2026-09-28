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
