"""Tiny dependency-free SVG charts for eval reports."""

from __future__ import annotations

INK, MUTED, GRID, ACCENT = "#1f2937", "#6b7280", "#e5e7eb", "#2563eb"


def _frame(title: str, xlabel: str, ylabel: str, w: int = 420, h: int = 360, m: int = 50) -> tuple[list[str], callable]:
    pw, ph = w - m - 20, h - m - 40

    def xy(x: float, y: float) -> tuple[float, float]:
        return m + x * pw, 30 + (1 - y) * ph

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="sans-serif" font-size="11">',
             f'<rect width="{w}" height="{h}" fill="#fff"/>',
             f'<text x="{w / 2}" y="18" text-anchor="middle" font-size="13" fill="{INK}">{title}</text>']
    for t in (0, 0.25, 0.5, 0.75, 1):
        x0, y0 = xy(t, 0)
        x1, y1 = xy(0, t)
        parts += [f'<line x1="{x0}" y1="30" x2="{x0}" y2="{30 + ph}" stroke="{GRID}"/>',
                  f'<line x1="{m}" y1="{y1}" x2="{m + pw}" y2="{y1}" stroke="{GRID}"/>',
                  f'<text x="{x0}" y="{30 + ph + 14}" text-anchor="middle" fill="{MUTED}">{t:g}</text>',
                  f'<text x="{m - 6}" y="{y1 + 4}" text-anchor="end" fill="{MUTED}">{t:g}</text>']
    parts += [f'<text x="{m + pw / 2}" y="{h - 6}" text-anchor="middle" fill="{INK}">{xlabel}</text>',
              f'<text x="14" y="{30 + ph / 2}" text-anchor="middle" fill="{INK}" transform="rotate(-90 14 {30 + ph / 2})">{ylabel}</text>']
    return parts, xy


def reliability_svg(bins: list[tuple[float, float, int]], title: str) -> str:
    parts, xy = _frame(title, "predicted confidence", "observed accuracy")
    (ax, ay), (bx, by) = xy(0, 0), xy(1, 1)
    parts.append(f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="{MUTED}" stroke-dasharray="4 4"/>')
    pts = [xy(p, a) for p, a, _ in bins]
    if pts:
        parts.append(f'<polyline fill="none" stroke="{ACCENT}" stroke-width="2" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}"/>')
    for (x, y), (_, _, n) in zip(pts, bins):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{ACCENT}"><title>n={n}</title></circle>')
    return "".join(parts) + "</svg>"


def selective_risk_svg(curve: list[tuple[float, float, float]], title: str) -> str:
    parts, xy = _frame(title, "coverage (share auto-accepted)", "error among accepted")
    ymax = max([e for _, _, e in curve] + [0.05])
    pts = [xy(c, e / ymax) for _, c, e in sorted(curve, key=lambda t: t[1])]
    if pts:
        parts.append(f'<polyline fill="none" stroke="{ACCENT}" stroke-width="2" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}"/>')
    parts.append(f'<text x="56" y="44" fill="{MUTED}">y axis max = {ymax:.3f}</text>')
    return "".join(parts) + "</svg>"
