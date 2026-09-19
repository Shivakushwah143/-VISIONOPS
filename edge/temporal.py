"""Model-independent temporal analysis: tracks in, behaviour out.

Detection is not tracking, and tracking is not behaviour. This module owns the
behaviour step, so the verified PPE rule and the new zone rules share one
interface instead of accumulating special cases inside the pipeline.

    Detector -> Tracker -> TrackHistory -> TemporalAnalyzer -> SafetyEvent

Analyzers are deterministic and replayable: they consume tracked-object history
with monotonic timestamps and emit a canonical event dict, or ``None``. No
learned drowning/fall classifier is claimed anywhere; the spatial rules are
explicit geometry plus dwell time, and are labelled as heuristics.
"""
from collections import deque
from dataclasses import dataclass, field

from .rules import SafetyRule

# Event types the central API accepts (see backend/app/schemas.py SafetyInput).
EVENT_TYPES = ('no_helmet_violation', 'restricted_zone_dwell', 'loitering', 'person_down_suspected')

DEFAULT_TEMPORAL_SETTINGS = {
    'analyzers': ['ppe_sustained'],
    'zones': [],
    'zone_min_presence_frames': 5,
    'zone_max_gap_seconds': 2.0,
    'zone_cooldown_seconds': 60.0,
    'loitering_min_dwell_seconds': 10.0,
    'person_down_min_seconds': 6.0,
    'person_down_window_seconds': 6.0,
    'person_down_min_samples': 5,
    'person_down_max_displacement_ratio': 0.25,
}


@dataclass
class TrackObservation:
    """One tracked object in one frame, in normalized frame coordinates.

    `subject` is what the tracker is following (a person track); `label` is the
    optional PPE/headgear state associated with it. Presence/zone/motion rules
    reason about the subject; the PPE rule reasons about the label. Keeping both
    avoids the trap of a helmet label silently disabling an unrelated rule.
    """

    session: str
    track_id: str
    timestamp: float
    observed_at: object
    bbox: list
    label: str = 'unknown'
    confidence: float = 0.0
    frame_sequence: int = 0
    subject: str = 'person'

    @property
    def center(self):
        x, y, X, Y = self.bbox
        return ((x + X) / 2.0, (y + Y) / 2.0)

    @property
    def diagonal(self):
        x, y, X, Y = self.bbox
        return max(((X - x) ** 2 + (Y - y) ** 2) ** 0.5, 1e-6)


def normalise_bbox(bbox):
    """Clamp a normalized xyxy box; raise when it is degenerate after clamping."""
    x, y, X, Y = (min(max(float(v), 0.0), 1.0) for v in bbox)
    x, X = min(x, X), max(x, X)
    y, Y = min(y, Y), max(y, Y)
    if X <= x or Y <= y:
        raise ValueError('degenerate_bbox')
    return [x, y, X, Y]


def canonical_event(event_type, observation, *, supporting_frames, confidence, span_seconds, zone_id=None, reason_code=None):
    """Build the event payload shape the central SafetyInput schema validates."""
    return {'event_type': event_type, 'track_id': str(observation.track_id),
            'bbox': normalise_bbox(observation.bbox), 'confidence': float(confidence),
            'supporting_frames': int(supporting_frames), 'span_seconds': float(span_seconds),
            'zone_id': zone_id, 'reason_code': reason_code or event_type}


class TemporalAnalyzer:
    """Interface every analyzer implements. Additive by design."""

    name = 'abstract'
    requires_track_history = True

    def observe(self, observation):
        raise NotImplementedError

    def retain(self, session, track_ids):
        """Drop state for tracks the tracker no longer reports."""

    def reset(self, session):
        """Drop all state for a stream session."""


class DeterministicPpeAnalyzer(TemporalAnalyzer):
    """The verified sustained bare-head rule, unchanged, as one analyzer."""

    name = 'ppe_sustained'

    def __init__(self, rule=None):
        self.rule = rule or SafetyRule()

    def observe(self, observation):
        # Identical call shape and semantics to the previously verified pipeline:
        # any label other than `no_helmet` (including `unknown`) resets the window.
        event = self.rule.observe(observation.session, observation.track_id, observation.timestamp,
                                  observation.label, observation.confidence, observation.bbox)
        if not event:
            return None
        return canonical_event('no_helmet_violation', observation,
                               supporting_frames=event['supporting_frames'],
                               confidence=event['confidence'],
                               span_seconds=event['span_seconds'])

    def retain(self, session, track_ids):
        self.rule.retain(session, track_ids)

    def reset(self, session):
        for key in [k for k in self.rule.windows if k[0] == session]:
            self.rule.windows.pop(key, None)
        for key in [k for k in self.rule.cooldowns if k[0] == session]:
            self.rule.cooldowns.pop(key, None)


class ZoneRule:
    """One restricted-area or loitering definition in normalized coordinates."""

    def __init__(self, zone_id, bbox, kind='restricted', min_dwell_seconds=3.0,
                 min_presence_frames=5, cooldown_seconds=60.0, max_gap_seconds=2.0, label='person'):
        self.zone_id = str(zone_id)
        self.bbox = normalise_bbox(bbox)
        self.kind = kind
        if kind not in ('restricted', 'loitering'):
            raise ValueError('invalid_zone_kind')
        self.min_dwell_seconds = float(min_dwell_seconds)
        self.min_presence_frames = max(5, int(min_presence_frames))
        self.cooldown_seconds = float(cooldown_seconds)
        self.max_gap_seconds = float(max_gap_seconds)
        self.label = label

    @property
    def event_type(self):
        return 'restricted_zone_dwell' if self.kind == 'restricted' else 'loitering'

    def contains(self, center):
        x, y = center
        left, top, right, bottom = self.bbox
        return left <= x <= right and top <= y <= bottom

    @classmethod
    def from_dict(cls, raw, defaults):
        return cls(raw['zone_id'], raw['bbox'], raw.get('kind', 'restricted'),
                   raw.get('min_dwell_seconds', 3.0),
                   raw.get('min_presence_frames', defaults['zone_min_presence_frames']),
                   raw.get('cooldown_seconds', defaults['zone_cooldown_seconds']),
                   raw.get('max_gap_seconds', defaults['zone_max_gap_seconds']),
                   raw.get('label', 'person'))


class ZoneDwellAnalyzer(TemporalAnalyzer):
    """Deterministic dwell rule: a track enters a zone and stays long enough.

    Emitting requires an unbroken presence inside the zone (`max_gap_seconds`
    tolerates single-frame occlusion) plus a minimum presence count and a
    minimum dwell span. It is geometry, not a learned behaviour model.
    """

    name = 'zone_dwell'

    def __init__(self, zones, defaults=None):
        defaults = {**DEFAULT_TEMPORAL_SETTINGS, **(defaults or {})}
        self.zones = [z if isinstance(z, ZoneRule) else ZoneRule.from_dict(z, defaults) for z in zones]
        self.entered = {}
        self.cooldowns = {}

    def observe(self, observation):
        if observation.subject != 'person':
            return None
        for zone in self.zones:
            event = self._emit(zone, observation)
            if event:
                return event
        return None

    def _emit(self, zone, observation):
        key = (observation.session, str(observation.track_id), zone.zone_id)
        if not zone.contains(observation.center):
            self.entered.pop(key, None)
            return None
        state = self.entered.get(key)
        if state is None or observation.timestamp - state['last'] > zone.max_gap_seconds:
            state = {'start': observation.timestamp, 'last': observation.timestamp, 'frames': 0, 'confidence': observation.confidence}
            self.entered[key] = state
        state['last'] = observation.timestamp
        state['frames'] += 1
        state['confidence'] = min(state['confidence'], observation.confidence)
        dwell = observation.timestamp - state['start']
        if state['frames'] < zone.min_presence_frames or dwell < zone.min_dwell_seconds:
            return None
        if observation.timestamp - self.cooldowns.get(key, -1e9) < zone.cooldown_seconds:
            return None
        self.cooldowns[key] = observation.timestamp
        event = canonical_event(zone.event_type, observation, supporting_frames=state['frames'],
                               confidence=state['confidence'], span_seconds=dwell,
                               zone_id=zone.zone_id, reason_code=zone.event_type)
        state['start'] = observation.timestamp
        state['frames'] = 0
        return event

    def retain(self, session, track_ids):
        allowed = {str(t) for t in track_ids}
        self.entered = {k: v for k, v in self.entered.items() if k[0] != session or k[1] in allowed}
        self.cooldowns = {k: v for k, v in self.cooldowns.items() if k[0] != session or k[1] in allowed}

    def reset(self, session):
        self.entered = {k: v for k, v in self.entered.items() if k[0] != session}
        self.cooldowns = {k: v for k, v in self.cooldowns.items() if k[0] != session}


class LowMotionAnalyzer(TemporalAnalyzer):
    """Bounded LOW-MOTION heuristic for a possible person-down.

    A track that persists for at least ``min_seconds`` while its centroid moves
    less than a fraction of its own bounding-box diagonal is reported as
    ``person_down_suspected``. This is an explicit motion heuristic over tracked
    history; it is not a trained fall/drowning classifier and must be reviewed
    as an evidence-sparse signal.
    """

    name = 'low_motion'

    def __init__(self, defaults=None):
        defaults = {**DEFAULT_TEMPORAL_SETTINGS, **(defaults or {})}
        self.min_seconds = float(defaults['person_down_min_seconds'])
        self.window_seconds = float(defaults['person_down_window_seconds'])
        self.min_samples = int(defaults['person_down_min_samples'])
        self.max_displacement_ratio = float(defaults['person_down_max_displacement_ratio'])
        self.history = {}

    def observe(self, observation):
        if observation.subject != 'person':
            return None
        key = (observation.session, str(observation.track_id))
        history = self.history.setdefault(key, deque(maxlen=256))
        history.append((observation.timestamp, observation.center, observation.diagonal))
        while history and observation.timestamp - history[0][0] > self.window_seconds:
            history.popleft()
        if len(history) < self.min_samples:
            return None
        span = observation.timestamp - history[0][0]
        if span < self.min_seconds:
            return None
        xs = [c[0] for _, c, _ in history]
        ys = [c[1] for _, c, _ in history]
        displacement = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5
        mean_diagonal = sum(d for _, _, d in history) / len(history)
        if displacement > self.max_displacement_ratio * mean_diagonal:
            return None
        samples = len(history)
        history.clear()
        return canonical_event('person_down_suspected', observation, supporting_frames=samples,
                               confidence=observation.confidence, span_seconds=span,
                               reason_code='low_motion_heuristic')

    def retain(self, session, track_ids):
        allowed = {str(t) for t in track_ids}
        self.history = {k: v for k, v in self.history.items() if k[0] != session or k[1] in allowed}

    def reset(self, session):
        self.history = {k: v for k, v in self.history.items() if k[0] != session}


ANALYZERS = {'ppe_sustained': lambda settings: DeterministicPpeAnalyzer(),
             'zone_dwell': lambda settings: ZoneDwellAnalyzer(settings.get('zones', []), settings),
             'low_motion': lambda settings: LowMotionAnalyzer(settings)}


def build_analyzers(settings=None):
    """Instantiate the analyzers named by configuration, in a stable order."""
    settings = {**DEFAULT_TEMPORAL_SETTINGS, **(settings or {})}
    names = settings.get('analyzers') or ['ppe_sustained']
    analyzers = []
    for name in names:
        if name not in ANALYZERS:
            raise ValueError('unknown_temporal_analyzer')
        if name == 'zone_dwell' and not settings.get('zones'):
            continue
        analyzers.append(ANALYZERS[name](settings))
    return analyzers


class TemporalPipeline:
    """Fan one track observation out to every configured analyzer."""

    def __init__(self, analyzers=None, settings=None):
        self.analyzers = analyzers if analyzers is not None else build_analyzers(settings)

    def observe(self, observation):
        return [event for event in (analyzer.observe(observation) for analyzer in self.analyzers) if event]

    def retain(self, session, track_ids):
        for analyzer in self.analyzers:
            analyzer.retain(session, track_ids)

    def reset(self, session):
        for analyzer in self.analyzers:
            analyzer.reset(session)

    @property
    def names(self):
        return [analyzer.name for analyzer in self.analyzers]
