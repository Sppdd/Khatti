"""Render fictional Iraqi-style documents to PNG with headless Chromium (correct Arabic shaping).

Safety (plan H): no real emblems, seals or security features; fictional authority names; a
visible "نموذج / SPECIMEN — FICTIONAL" watermark on every document.
"""

from __future__ import annotations

import base64
import html
import random
from dataclasses import dataclass
from pathlib import Path

from khatti.registry import DocTypeSpec, Shape

FONTS = Path(__file__).parent / "fonts"
WATERMARK = "نموذج / SPECIMEN — FICTIONAL"

HEADERS = {
    "national_id_front": ["وزارة الشؤون المدنية النموذجية", "کارتی نیشتمانی - نموونە"],
    "national_id_back": ["وزارة الشؤون المدنية النموذجية", "کارتی نیشتمانی - نموونە"],
    "commercial_registration": ["دائرة تسجيل الشركات النموذجية"],
    "tax_card": ["الهيئة العامة للضرائب النموذجية"],
}
PRINTED_FONTS = ["Naskh", "Kufi"]


@dataclass
class Rendered:
    png: bytes
    width: int
    height: int
    field_boxes: dict[str, list[float]]  # field -> [x0, y0, x1, y1] relative
    lines: list[str]  # every printed line, in reading order
    font: str


def _font_face(name: str, file: str, weight: int = 400) -> str:
    data = base64.b64encode((FONTS / file).read_bytes()).decode()
    return f"@font-face{{font-family:'{name}';src:url(data:font/ttf;base64,{data}) format('truetype');font-weight:{weight};}}"


_FACES = None


def font_faces() -> str:
    global _FACES
    if _FACES is None:
        _FACES = "".join([
            _font_face("Naskh", "NotoNaskhArabic-Regular.ttf"), _font_face("Naskh", "NotoNaskhArabic-Bold.ttf", 700),
            _font_face("Kufi", "NotoKufiArabic-Regular.ttf"), _font_face("Kufi", "NotoKufiArabic-Bold.ttf", 700),
            _font_face("Ruqaa", "ArefRuqaa-Regular.ttf"), _font_face("Latin", "NotoSans-Regular.ttf"),
        ])
    return _FACES


def document_html(spec: DocTypeSpec, values: dict[str, str | None], handwritten: list[str], font: str, rng: random.Random) -> tuple[str, list[str]]:
    card = spec.shape is Shape.ID1_CARD
    w, h = (856, 540) if card else (794, 1123)
    hue = rng.choice([200, 150, 30, 260, 0])
    lines = [spec.label_ar, *HEADERS.get(spec.key, [])]
    rows = []
    for f in spec.fields:
        v = values.get(f.name)
        if v is None:
            continue
        hand = f.name in handwritten
        cls = "v hand" if hand else "v"
        rows.append(
            f'<div class="row"><span class="l">{html.escape(f.label_ar)}</span>: '
            f'<span class="{cls}" data-field="{f.name}" style="transform:rotate({rng.uniform(-1.5, 1.5) if hand else 0:.2f}deg)">{html.escape(v)}</span></div>'
        )
        lines.append(f"{f.label_ar}: {v}")
    head = "".join(f'<div class="sub">{html.escape(x)}</div>' for x in HEADERS.get(spec.key, []))
    photo = '<div class="photo"></div>' if spec.key == "national_id_front" else ""
    if not card:  # certificate: stamp area and signature line
        photo = ('<div class="stamp">مكان الختم</div>'
                 '<div class="sign">توقيع المسجل: ____________________</div>')
    size = 22 if card else 24
    doc = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><style>
{font_faces()}
body {{ margin:0; background:#fff; }}
#doc {{ position:relative; width:{w}px; height:{h}px; overflow:hidden; box-sizing:border-box;
       padding:{28 if card else 60}px; font-family:'{font}','Naskh',serif; font-size:{size}px; color:#1d2330;
       background:linear-gradient(135deg,hsl({hue} 45% 94%),hsl({hue} 35% 86%));
       border:{2 if card else 0}px solid hsl({hue} 30% 55%); border-radius:{18 if card else 0}px; }}
.title {{ font-family:'Kufi'; font-weight:700; font-size:{size + 6}px; color:hsl({hue} 50% 28%); }}
.sub {{ font-size:{size - 6}px; color:hsl({hue} 25% 35%); }}
.body {{ margin-top:{14 if card else 40}px; line-height:1.75; }}
.row {{ white-space:nowrap; }}
.l {{ color:hsl({hue} 30% 30%); }}
.v {{ display:inline-block; font-weight:700; }}
.hand {{ font-family:'Ruqaa'; font-weight:400; color:#1b3a8a; font-size:{size + 4}px; }}
.photo {{ position:absolute; left:32px; top:120px; width:180px; height:230px; border-radius:8px;
         background:radial-gradient(circle at 50% 35%,#b9bfc9 22%,transparent 23%),
                    radial-gradient(ellipse at 50% 100%,#b9bfc9 45%,transparent 46%),#dde1e8; }}
.stamp {{ position:absolute; left:80px; bottom:170px; width:170px; height:170px; border:2px dashed hsl({hue} 30% 55%);
          border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:16px; color:hsl({hue} 25% 45%); }}
.sign {{ position:absolute; right:60px; bottom:120px; font-size:18px; color:hsl({hue} 25% 35%); }}
.wm {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center; pointer-events:none;
      font-family:'Kufi','Latin'; font-size:{46 if card else 50}px; font-weight:700; color:rgba(180,30,30,.16);
      transform:rotate(-18deg); white-space:nowrap; }}
</style></head><body><div id="doc">
<div class="title">{html.escape(spec.label_ar)}</div>{head}{photo}
<div class="body">{''.join(rows)}</div>
<div class="wm">{html.escape(WATERMARK)}</div>
</div></body></html>"""
    lines.append(WATERMARK)
    return doc, lines


class Renderer:
    def __init__(self, scale: float = 1.5):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._page = self._browser.new_page(device_scale_factor=scale)

    def render(self, spec: DocTypeSpec, values: dict[str, str | None], handwritten: list[str], rng: random.Random) -> Rendered:
        font = rng.choice(PRINTED_FONTS)
        doc, lines = document_html(spec, values, handwritten, font, rng)
        self._page.set_content(doc, wait_until="load")
        self._page.evaluate("document.fonts.ready")
        el = self._page.query_selector("#doc")
        box = el.bounding_box()
        png = el.screenshot(type="png")
        boxes = {}
        for span in self._page.query_selector_all("[data-field]"):
            b = span.bounding_box()
            if b:
                boxes[span.get_attribute("data-field")] = [
                    round((b["x"] - box["x"]) / box["width"], 4), round((b["y"] - box["y"]) / box["height"], 4),
                    round((b["x"] + b["width"] - box["x"]) / box["width"], 4), round((b["y"] + b["height"] - box["y"]) / box["height"], 4),
                ]
        return Rendered(png, int(box["width"]), int(box["height"]), boxes, lines, font)

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()
