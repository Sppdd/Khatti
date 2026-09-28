import asyncio

import pytest

from khatti.models import DocumentInput, ExtractedValue, FieldStatus, Line, Outcome, ReaderStatus, SourceSpan, Transcript
from khatti.pipeline import Pipeline
from khatti.readers import StaticReader
from khatti.structuring import LabelStructurer, locate, verify

from .conftest import TODAY, labelled


def reader(name, images, pages, overrides=None, samples=1, status=ReaderStatus.OK):
    """A StaticReader returning labelled lines for each slot's image; overrides patch labels per slot."""
    by_image = {}
    for slot, img in images.items():
        caption, pairs = pages[slot]
        pairs = {**pairs, **(overrides or {}).get(slot, {})}
        pairs = {k: v for k, v in pairs.items() if v is not None}
        by_image[img] = (caption, [labelled(pairs)] * samples)
    return StaticReader(name, by_image, status=status)


def session(images, *slots):
    return [DocumentInput(slot=s, image=images[s], mime_type="image/png") for s in slots]


ALL = ("national_id_front", "national_id_back", "commercial_registration", "tax_card")


def run(pipeline, docs):
    return asyncio.run(pipeline.run(docs, today=TODAY))


def two_readers(images, pages, b_overrides=None, a_overrides=None):
    return [reader("nvidia-omni", images, pages, a_overrides), reader("arabic-vlm", images, pages, b_overrides, samples=3)]


# ---------------------------------------------------------------- copy-only guarantee

def test_locate_tolerates_digit_script_and_whitespace_only():
    text = "الرقم الوطني:  ١٩٩٠ ١٢٣٤"
    start, end = locate("1990 1234", text)
    assert text[start:end] == "١٩٩٠ ١٢٣٤"
    assert locate("1990 1235", text) is None


def test_structurer_cannot_invent_values():
    t = Transcript(reader="r", lines=[Line(line_id="L1", text="الاسم الكامل: علي حسين")])
    # model "corrects" a letter -> rejected
    assert verify(ExtractedValue(value="علي حسن", spans=[]), t).value is None
    assert verify(ExtractedValue(value="علي حسن", spans=[]), t).reason == "not_verbatim"
    # stored value is cut from the line, not the model's string
    v = verify(ExtractedValue(value="علي  حسين", spans=[SourceSpan(line_id="L1", start=0, end=0)]), t)
    assert v.value == "علي حسين" and v.source_line_ids == ["L1"]


def test_illegible_marker_is_never_exposed():
    t = Transcript(reader="r", lines=[Line(line_id="L1", text="الرقم الوطني: 1990?2345678")])
    v = verify(ExtractedValue(value="1990?2345678"), t)
    assert v.value is None and v.reason == "illegible" and v.raw_partial == "1990?2345678"


# ---------------------------------------------------------------- end-to-end

def test_clean_session_auto_passes(images, pages):
    result = run(Pipeline(two_readers(images, pages), LabelStructurer()), session(images, *ALL))
    assert result.decision.outcome is Outcome.AUTO_PASS, result.decision.reasons
    front = result.documents[0]
    assert front.fields["id_number"].value == "١٩٩٠١٢٣٤٥٦٧٨"  # stored as read
    assert front.fields["id_number"].status is FieldStatus.OK
    assert {c.rule: c.status for c in result.cross_checks}["name_id_vs_license"] == "match"
    assert result.review_summary == []


def test_glare_on_digits_is_blank_and_flagged(images, pages):
    glare = {"national_id_front": {"الرقم الوطني": "١٩٩٠١٢?٤٥٦٧٨"}}
    result = run(Pipeline(two_readers(images, pages, b_overrides=glare, a_overrides=glare), LabelStructurer()),
                 session(images, *ALL))
    f = result.documents[0].fields["id_number"]
    assert f.value is None and f.status is FieldStatus.UNREADABLE and f.raw_partial
    assert result.decision.outcome is Outcome.HUMAN_REVIEW
    assert "UNREADABLE_FIELD" in result.decision.reasons
    req = next(r for r in result.retake_requests if r.field == "id_number")
    assert "الرقم الوطني" in req.message_ar and req.slot == "national_id_front"


def test_readers_disagree_and_neither_valid_gives_null(images, pages):
    a = {"national_id_front": {"الرقم الوطني": "1990123"}}  # too short
    b = {"national_id_front": {"الرقم الوطني": "88887777"}}  # too short, different
    result = run(Pipeline(two_readers(images, pages, b_overrides=b, a_overrides=a), LabelStructurer()),
                 session(images, "national_id_front"))
    f = result.documents[0].fields["id_number"]
    assert f.value is None and f.status is FieldStatus.LOW_CONFIDENCE
    assert set(v for v in f.candidates.values()) == {"1990123", "88887777"}


def test_missing_grandfather_name_routes_with_partial_match(images, pages):
    lic = {"commercial_registration": {"اسم المالك": "مثال أحمد محمد"}}
    result = run(Pipeline(two_readers(images, pages, lic, lic), LabelStructurer()), session(images, *ALL))
    assert {c.rule: c.status for c in result.cross_checks}["name_id_vs_license"] == "partial_match"
    assert result.decision.outcome is Outcome.HUMAN_REVIEW
    assert "NAME_PARTIAL_MATCH" in result.decision.reasons


def test_different_person_is_mismatch(images, pages):
    tax = {"tax_card": {"اسم المكلف": "حسن علي عباس"}}
    result = run(Pipeline(two_readers(images, pages, tax, tax), LabelStructurer()), session(images, *ALL))
    assert {c.rule: c.status for c in result.cross_checks}["name_id_vs_tax"] == "mismatch"
    assert result.documents[3].fields["holder_name_ar"].status is FieldStatus.MISMATCH


def test_expired_license_routes(images, pages):
    exp = {"commercial_registration": {"تاريخ النفاذ": "2025/01/10"}}
    result = run(Pipeline(two_readers(images, pages, exp, exp), LabelStructurer()), session(images, *ALL))
    assert result.documents[2].fields["expiry_date"].status is FieldStatus.EXPIRED
    assert "EXPIRED" in result.decision.reasons
    assert {c.rule: c.status for c in result.cross_checks}["docs_not_expired"] == "fail"


def test_hijri_expiry_is_converted_and_flagged(images, pages):
    hij = {"tax_card": {"تاريخ النفاذ": "1450/05/01 هـ"}}
    result = run(Pipeline(two_readers(images, pages, hij, hij), LabelStructurer()), session(images, "tax_card"))
    f = result.documents[0].fields["expiry_date"]
    assert f.calendar == "hijri" and f.calendar_converted


def test_unavailable_nvidia_reader_degrades_to_review(images, pages):
    readers = [reader("nvidia-omni", images, pages, status=ReaderStatus.UNAVAILABLE), reader("arabic-vlm", images, pages)]
    result = run(Pipeline(readers, LabelStructurer()), session(images, *ALL))
    assert result.documents[0].readers["nvidia-omni"] is ReaderStatus.UNAVAILABLE
    assert any(c.code == "reader_unavailable" for c in result.checks)
    # One surviving reader is not consensus.
    assert result.decision.outcome is Outcome.HUMAN_REVIEW


def test_wrong_document_in_slot(images, pages):
    # The tax card image is uploaded into the national_id_front slot.
    imgs = {**images, "national_id_front": images["tax_card"]}
    pgs = {**pages, "national_id_front": pages["tax_card"]}
    result = run(Pipeline(two_readers(imgs, pgs), LabelStructurer()), session(imgs, "national_id_front"))
    assert any(c.code == "wrong_document_in_slot" for c in result.checks)
    assert result.documents[0].doc_type == "tax_card"
    assert "WRONG_DOCUMENT_IN_SLOT" in result.decision.reasons


def test_primary_reader_breaks_ties(images, pages):
    a = {"national_id_front": {"اسم الأم": "زينب عليوي"}}
    readers = two_readers(images, pages, a_overrides=a)
    p = Pipeline(readers, LabelStructurer(), primary_readers={"names": "arabic-vlm"})
    f = run(p, session(images, "national_id_front")).documents[0].fields["mother_name_ar"]
    assert f.value == "زينب علي"
    assert f.status is FieldStatus.LOW_CONFIDENCE


class FakeUltra:
    name = "fake-ultra"

    def __init__(self, bullets):
        self._bullets = bullets
        self.payloads = []

    async def bullets(self, payload):
        self.payloads.append(payload)
        return self._bullets


def test_ultra_summary_uses_locked_placeholders(images, pages):
    lic = {"commercial_registration": {"اسم المالك": "مثال أحمد محمد"}}
    ultra = FakeUltra([
        "Owner name {{field:commercial_registration.owner_name_ar}} omits the grandfather name; confirm same person.",
        "The ID number is 199012345678.",  # writes a value itself -> dropped
        "اسم مختلف",  # Arabic text -> dropped
        "Check {{field:nope.nothing}}",  # unknown placeholder -> dropped
    ])
    result = run(Pipeline(two_readers(images, pages, lic, lic), LabelStructurer(), reviewer=ultra), session(images, *ALL))
    assert result.review_summary == ["Owner name مثال أحمد محمد omits the grandfather name; confirm same person."]
    # Ultra never sees field values
    assert "مثال" not in str(ultra.payloads[0]["documents"])


def test_blurry_photo_requests_retake(images, pages):
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(images["tax_card"], np.uint8), cv2.IMREAD_COLOR)
    blurred = cv2.imencode(".png", cv2.GaussianBlur(img, (41, 41), 0))[1].tobytes()
    imgs = {**images, "tax_card": blurred}
    result = run(Pipeline(two_readers(imgs, pages), LabelStructurer()), session(imgs, "tax_card"))
    assert "blur" in result.documents[0].quality.issues
    assert "RETAKE_REQUESTED" in result.decision.reasons


def test_unknown_slot_rejected(images):
    with pytest.raises(KeyError):
        run(Pipeline([StaticReader("r", {})], LabelStructurer()), [DocumentInput(slot="passport", image=b"x")])


class BoxReader:
    """Returns one transcript whose ID-number line is illegible and located at `box`."""

    samples = 1

    def __init__(self, name, box):
        self.name, self.box = name, box

    async def transcribe(self, images):
        return [Transcript(reader=self.name, caption="national card front", lines=[
            Line(line_id="L1", text="الاسم الكامل: مثال أحمد جاسم محمد", bbox=[0.4, 0.2, 0.9, 0.28]),
            Line(line_id="L2", text="الرقم الوطني: ١٩٩٠?٢٣٤٥٦٧٨", bbox=self.box),
        ])]


def _retake_for(image_png, box):
    readers = [BoxReader("a", box), BoxReader("b", box)]
    result = run(Pipeline(readers, LabelStructurer()), [DocumentInput(slot="national_id_front", image=image_png)])
    return next(r for r in result.retake_requests if r.field == "id_number")


def test_retake_names_glare_over_the_field():
    import cv2
    import numpy as np

    from .conftest import card_image

    img = cv2.imdecode(np.frombuffer(card_image(1), np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    box = [0.45, 0.55, 0.85, 0.65]
    cv2.rectangle(img, (int(0.45 * w), int(0.55 * h)), (int(0.85 * w), int(0.65 * h)), (255, 255, 255), -1)
    req = _retake_for(cv2.imencode(".png", img)[1].tobytes(), box)
    assert req.reason == "glare"
    assert "انعكاس ضوء فوق الرقم الوطني" in req.message_ar and "Glare over the ID number" in req.message_en


def test_retake_names_a_thumb_and_a_cut_off_field():
    import cv2
    import numpy as np

    from .conftest import card_image

    img = cv2.imdecode(np.frombuffer(card_image(1), np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    cv2.ellipse(img, (int(0.65 * w), int(0.6 * h)), (int(0.2 * w), int(0.06 * h)), 0, 0, 360, (120, 150, 205), -1)
    assert _retake_for(cv2.imencode(".png", img)[1].tobytes(), [0.45, 0.55, 0.85, 0.65]).reason == "thumb"
    assert _retake_for(card_image(1), [0.5, 0.9, 1.0, 1.0]).reason == "cut_off"
