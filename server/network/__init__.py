"""WebSocket transport and authenticated game-session boundaries."""

from .protocol import ProtocolMessageValidator, ProtocolValidationError
from .server import WebSocketGameServer
from .session import (
    ConnectionContext,
    GameRegistry,
    SessionManager,
    TickDriver,
    monotonic_seconds,
)

__all__ = [
    "ConnectionContext",
    "GameRegistry",
    "ProtocolMessageValidator",
    "ProtocolValidationError",
    "SessionManager",
    "TickDriver",
    "WebSocketGameServer",
    "monotonic_seconds",
]
