from datetime import date

import cv2
import numpy as np
import pytest

TODAY = date(2026, 9, 28)


def card_image(seed: int = 0, w: int = 1000, aspect: float = 85.60 / 53.98, bg: int = 40, margin: int = 120) -> bytes:
    """A sharp synthetic 'card' (light rectangle with text-like strokes) on a dark background."""
    rng = np.random.default_rng(seed)
    h = int(w / aspect)
    canvas = np.full((h + 2 * margin, w + 2 * margin, 3), bg, np.uint8)
    card = np.full((h, w, 3), 225, np.uint8)
    for row in range(40, h - 30, 55):
        x = w - 40
        while x > 80:
            word = int(rng.integers(40, 140))
            cv2.rectangle(card, (x - word, row), (x, row + 18), (30, 30, 30), -1)
            x -= word + int(rng.integers(15, 35))
    canvas[margin : margin + h, margin : margin + w] = card
    ok, buf = cv2.imencode(".png", canvas)
    return buf.tobytes()


def a4_image(seed: int = 0) -> bytes:
    return card_image(seed, w=900, aspect=297 / 210)


def labelled(pairs: dict[str, str]) -> list[str]:
    return [f"{k}: {v}" for k, v in pairs.items()]


ID_FRONT = {
    "الاسم الكامل": "مثال أحمد جاسم محمد",
    "اللقب": "الموصلي",
    "اسم الأم": "زينب علي",
    "الجنس": "ذكر",
    "الرقم الوطني": "١٩٩٠١٢٣٤٥٦٧٨",
}
ID_BACK = {
    "تاريخ الولادة": "1990/05/14",
    "محل الولادة": "نينوى",
    "تاريخ الإصدار": "2021/04/12",
    "تاريخ النفاذ": "2031/04/12",
}
LICENSE = {
    "اسم المالك": "مثال أحمد جاسم محمد",
    "الاسم التجاري": "أسواق النور",
    "رقم التسجيل": "07/123456",
    "المحافظة": "نينوى",
    "تاريخ الإصدار": "2024/01/10",
    "تاريخ النفاذ": "2027/01/10",
}
TAX = {
    "اسم المكلف": "مثال أحمد جاسم محمد",
    "الاسم التجاري": "أسواق النور",
    "الرقم الضريبي": "123456789",
    "تاريخ الإصدار": "2024/02/01",
    "تاريخ النفاذ": "2027/02/01",
}


@pytest.fixture
def images():
    return {
        "national_id_front": card_image(1),
        "national_id_back": card_image(2),
        "commercial_registration": a4_image(3),
        "tax_card": card_image(4),
    }


@pytest.fixture
def pages():
    return {
        "national_id_front": ("front of an Iraqi national card", ID_FRONT),
        "national_id_back": ("back of an Iraqi national card", ID_BACK),
        "commercial_registration": ("commercial registration certificate", LICENSE),
        "tax_card": ("tax card", TAX),
    }
