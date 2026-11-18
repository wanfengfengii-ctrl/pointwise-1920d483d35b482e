from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class NamedImportIdentity:
    registration_id: str
    original_digest: str


@dataclass(frozen=True, slots=True)
class RetestImportIdentity:
    registration_id: str
    event_id: str
    original_digest: str


def identify_named_import(registration_id: str, raw: bytes) -> NamedImportIdentity:
    return NamedImportIdentity(registration_id, sha256(raw).hexdigest())


def identify_retest_import(
    registration_id: str, event_id: str, raw: bytes
) -> RetestImportIdentity:
    return RetestImportIdentity(registration_id, event_id, sha256(raw).hexdigest())
