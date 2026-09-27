"""Merge reader outputs; disagreement between readers is the confidence signal."""

from __future__ import annotations

from .arabic import similarity
from .models import DocumentReading, FieldConsensus, ReaderResult, ReaderStatus

# Two readings count as agreeing above this similarity (absorbs minor OCR noise).
AGREE_THRESHOLD = 0.9


def merge_field(name: str, candidates: dict[str, str | None]) -> FieldConsensus:
    values = [v for v in candidates.values() if v]
    if not values:
        return FieldConsensus(name=name, value=None, agreement=0.0, candidates=candidates)

    # Pick the value that the most readers agree with (medoid voting).
    best, best_votes = values[0], 0
    for v in values:
        votes = sum(similarity(v, other) >= AGREE_THRESHOLD for other in values)
        if votes > best_votes:
            best, best_votes = v, votes

    # Denominator counts every reader that ran, so a missing read lowers agreement.
    agreement = best_votes / len(candidates)
    return FieldConsensus(name=name, value=best, agreement=agreement, candidates=candidates)


def build_reading(doc_type: str, field_names: list[str], results: list[ReaderResult]) -> DocumentReading:
    readers = {r.reader: r.status for r in results}
    ok = [r for r in results if r.status is ReaderStatus.OK]
    if not ok:
        return DocumentReading(doc_type=doc_type, fields={}, readers=readers)

    # Only schema fields: readers that invent extra keys do not get them into the record.
    # Failed readers still count as a missing vote: one surviving reader is not consensus.
    fields = {n: merge_field(n, {r.reader: r.fields.get(n) for r in results}) for n in field_names}
    return DocumentReading(doc_type=doc_type, fields=fields, readers=readers)
