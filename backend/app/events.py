"""Additive realtime event bus over WebSocket.

REST stays the CRUD contract; this module only *broadcasts* state that the REST
handlers already committed. Nothing is published before commit, so a rolled-back
transaction can never produce a phantom alert:

    SafetyEvent / device health / campaign state -> after_commit -> bus -> browser

Event types: `safety_event.created`, `device.health_changed`,
`campaign.status_changed`, plus `stream.hello` on connect.

IMPLEMENTED - NOT RUNTIME VERIFIED: the development host has no FastAPI/
PostgreSQL installation, so the socket handshake, fan-out and browser rendering
were not exercised. The REST endpoints remain the source of truth either way.
"""
import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import event

from .db import Session, User, UserSession, now  # noqa: F401 (re-exported helpers)
from .security import ORIGIN, digest

EVENT_TYPES = ('safety_event.created', 'device.health_changed', 'campaign.status_changed')


class EventBus:
    """Bounded fan-out to connected browsers, safe to call from worker threads."""

    def __init__(self, queue_size=64):
        self.queue_size = queue_size
        self.subscribers = set()
        self.loop = None
        self.sequence = 0
        self.published = 0

    def bind_loop(self, loop):
        self.loop = loop

    async def subscribe(self):
        queue = asyncio.Queue(self.queue_size)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.subscribers.discard(queue)

    def publish(self, event_type, payload):
        """Never raises: telemetry must not break a committed request."""
        if event_type not in EVENT_TYPES:
            raise ValueError('unknown_event_type')
        if self.loop is None or not self.subscribers:
            return 0
        self.sequence += 1
        message = {'event': event_type, 'sequence': self.sequence, 'data': payload}
        delivered = 0
        for queue in list(self.subscribers):
            try:
                self.loop.call_soon_threadsafe(queue.put_nowait, message)
                delivered += 1
            except RuntimeError:
                self.unsubscribe(queue)
        self.published += 1
        return delivered

    def publish_on_commit(self, session, event_type, payload):
        """Publish only if the surrounding transaction actually commits."""
        @event.listens_for(session, 'after_commit', once=True)
        def _fire(_session):  # pragma: no cover - exercised only with a live database
            self.publish(event_type, payload)


bus = EventBus()


def websocket_authenticated(websocket):
    """Session-cookie + Origin check for the handshake; no credential in the URL."""
    if websocket.headers.get('origin') != ORIGIN:
        return None
    token = websocket.cookies.get('visionops_session', '')
    if not token:
        return None
    with Session() as db:
        session = db.query(UserSession).filter(UserSession.token_hash == digest(token),
                                              UserSession.expires_at > now()).first()
        user = db.get(User, session.user_id) if session else None
        if not user or user.status != 'active':
            return None
        return {'user_id': user.user_id, 'role': user.role}


def register(app, path='/api/v1/ws/events'):
    @app.on_event('startup')
    async def _bind():
        bus.bind_loop(asyncio.get_running_loop())

    @app.websocket(path)
    async def ws_events(websocket: WebSocket):
        identity = websocket_authenticated(websocket)
        if not identity:
            # Reject before accept so the browser sees a clean 1008 policy close.
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = await bus.subscribe()
        await websocket.send_text(json.dumps({'event': 'stream.hello', 'sequence': 0,
                                              'data': {'role': identity['role'], 'event_types': list(EVENT_TYPES)}}))
        try:
            while True:
                message = await queue.get()
                await websocket.send_text(json.dumps(message))
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(queue)

    return bus
