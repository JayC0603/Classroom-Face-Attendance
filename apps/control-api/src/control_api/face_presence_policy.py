"""Bounded, session-scoped face presence and consecutive no-match policy.

Only distinct analyzed frames count. Detection/search failures, missing faces,
ambiguous results and matching identities break the no-match streak.
"""

from dataclasses import dataclass, field
from hashlib import sha256
from math import isfinite
from threading import Lock
from uuid import uuid4


@dataclass(frozen=True)
class FaceEventCandidate:
    kind: str
    presence_id: str
    track_id: int | None
    frame_id: str
    box: tuple[float, float, float, float]
    consecutive_unknown: int


@dataclass
class _Presence:
    id: str
    track_id: int | None
    box: tuple[float, float, float, float]
    seen: float
    unknown: int = 0


@dataclass
class _Scope:
    seen: float
    frame_id: str = ""
    revision: str = ""
    tracks: list[_Presence] = field(default_factory=list)
    candidates: list[FaceEventCandidate] = field(default_factory=list)


def _box(raw: object) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, w, h = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    if not all(isfinite(v) for v in (x, y, w, h)):
        return None
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.0002 or y + h > 1.0002:
        return None
    return x, y, w, h


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    area = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    )
    return area / max(1e-8, a[2] * a[3] + b[2] * b[3] - area)


class FacePresencePolicy:
    def __init__(self) -> None:
        self._scopes: dict[str, _Scope] = {}
        self._lock = Lock()

    def evaluate(
        self,
        *,
        scope: str,
        frame_id: str,
        now: float,
        faces: list[dict[str, object]],
        persons: list[dict[str, object]],
        search_ready: bool,
        reference_revision: str,
        threshold: int,
    ) -> list[FaceEventCandidate]:
        with self._lock:
            self._scopes = {k: v for k, v in self._scopes.items() if now - v.seen < 3}
            if scope not in self._scopes and len(self._scopes) >= 16:
                self._scopes.pop(min(self._scopes, key=lambda k: self._scopes[k].seen))
            state = self._scopes.setdefault(scope, _Scope(now))
            if state.frame_id and now < state.seen:
                return []
            if state.revision != reference_revision or not search_ready:
                for previous_track in state.tracks:
                    previous_track.unknown = 0
                state.candidates = [c for c in state.candidates if c.kind == "FACE_CAPTURED"]
            if state.frame_id == frame_id:
                return list(state.candidates)
            state.frame_id, state.seen, state.revision = frame_id, now, reference_revision
            state.tracks = [t for t in state.tracks if now - t.seen < 1.5]
            used: set[str] = set()
            candidates: list[FaceEventCandidate] = []
            for face in faces[:8]:
                box = _box(face.get("box"))
                if box is None:
                    continue
                cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
                containing = []
                for person in persons:
                    pb = _box(person.get("box"))
                    tid = person.get("track_id")
                    if (
                        pb
                        and isinstance(tid, int)
                        and pb[0] <= cx <= pb[0] + pb[2]
                        and pb[1] <= cy <= pb[1] + pb[3]
                    ):
                        containing.append((pb[2] * pb[3], tid))
                track_id = min(containing)[1] if containing else None
                matches = [
                    t
                    for t in state.tracks
                    if t.id not in used
                    and (
                        t.track_id == track_id
                        if track_id is not None
                        else t.track_id is None and _iou(t.box, box) >= 0.3
                    )
                ]
                track = max(matches, key=lambda t: _iou(t.box, box), default=None)
                if track is None:
                    track = _Presence(uuid4().hex, track_id, box, now)
                    state.tracks.append(track)
                track.box, track.seen = box, now
                track.unknown = (
                    min(threshold, track.unknown + 1)
                    if search_ready and face.get("state") == "UNKNOWN"
                    else 0
                )
                used.add(track.id)
                # The durable store deduplicates each event kind per presence.
                # Re-offering candidates permits retry after a database failure.
                candidates.append(
                    FaceEventCandidate("FACE_CAPTURED", track.id, track_id, frame_id, box, 0)
                )
                if track.unknown >= threshold:
                    candidates.append(
                        FaceEventCandidate(
                            "UNKNOWN_ALERT", track.id, track_id, frame_id, box, track.unknown
                        )
                    )
            for track in state.tracks:
                if track.id not in used:
                    track.unknown = 0
            state.tracks = sorted(state.tracks, key=lambda t: t.seen, reverse=True)[:32]
            state.candidates = candidates
            return list(candidates)


def event_dedupe_key(scope: str, candidate: FaceEventCandidate) -> str:
    return sha256(f"{scope}/{candidate.presence_id}/{candidate.kind}".encode()).hexdigest()
