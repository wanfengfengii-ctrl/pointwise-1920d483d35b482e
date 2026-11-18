from dataclasses import dataclass

from portal.columns import BadRecording
from portal.pages import NamedPageRow, decode_named_pages
from portal.recording import (
    load_recording_object,
    read_batch_id,
    read_source_scale,
)


@dataclass(frozen=True, slots=True)
class RetestRecording:
    batch_id: str
    event_id: str
    source_scale: tuple[int, int]
    rows: tuple[NamedPageRow, ...]
    raw: bytes


def read_event_id(recording: dict) -> str:
    event_id = recording.get('event_id')
    if not isinstance(event_id, str) or not event_id.strip():
        raise BadRecording('retest event_id must be a nonempty string')
    try:
        event_id.encode('utf-8')
    except UnicodeEncodeError as error:
        raise BadRecording('retest event_id must be valid UTF-8 text') from error
    return event_id


def decode_retest_recording(raw: bytes) -> RetestRecording:
    recording = load_recording_object(raw)
    return RetestRecording(
        read_batch_id(recording),
        read_event_id(recording),
        read_source_scale(recording),
        decode_named_pages(recording.get('pages')),
        raw,
    )
