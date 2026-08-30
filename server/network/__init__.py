"""WebSocket transport and authenticated game-session boundaries."""

from .protocol import ProtocolMessageValidator, ProtocolValidationError
from .server import WebSocketGameServer
from .session import (
    ConnectionContext,
    GameRegistry,
    SessionManager,
    SessionResult,
    TickDriver,
    monotonic_seconds,
)

__all__ = [
    "ConnectionContext",
    "GameRegistry",
    "ProtocolMessageValidator",
    "ProtocolValidationError",
    "SessionManager",
    "SessionResult",
    "TickDriver",
    "WebSocketGameServer",
    "monotonic_seconds",
]
