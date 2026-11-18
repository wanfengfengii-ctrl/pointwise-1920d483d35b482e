import json
from dataclasses import dataclass

from portal.columns import BadRecording
from portal.pages import NamedPageRow, decode_named_pages


@dataclass(frozen=True, slots=True)
class NamedRecording:
    batch_id: str
    source_scale: tuple[int, int]
    rows: tuple[NamedPageRow, ...]
    raw: bytes


def _object_without_duplicate_fields(pairs: list[tuple[str, object]]) -> dict:
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise BadRecording(f'duplicate recording field: {key!r}')
        fields[key] = value
    return fields


def _reject_non_json_number(token: str) -> None:
    raise BadRecording(f'invalid JSON number: {token}')


def load_recording_object(raw: bytes) -> dict:
    if not isinstance(raw, bytes):
        raise BadRecording('original recording must be bytes')
    try:
        recording = json.loads(
            raw.decode('utf-8'),
            object_pairs_hook=_object_without_duplicate_fields,
            parse_constant=_reject_non_json_number,
        )
    except BadRecording:
        raise
    except (ValueError, RecursionError) as error:
        raise BadRecording('original recording must be valid UTF-8 JSON') from error
    if not isinstance(recording, dict):
        raise BadRecording('recording must be an object')
    return recording


def read_batch_id(recording: dict) -> str:
    batch_id = recording.get('batch_id')
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise BadRecording('recording batch_id must be a nonempty string')
    return batch_id


def read_source_scale(recording: dict) -> tuple[int, int]:
    source_scale = recording.get('source_scale')
    if (
        not isinstance(source_scale, list)
        or len(source_scale) != 2
        or any(type(part) is not int for part in source_scale)
    ):
        raise BadRecording('source_scale must contain two integers')
    return source_scale[0], source_scale[1]


def decode_named_recording(raw: bytes) -> NamedRecording:
    recording = load_recording_object(raw)
    return NamedRecording(
        read_batch_id(recording),
        read_source_scale(recording),
        decode_named_pages(recording.get('pages')),
        raw,
    )
