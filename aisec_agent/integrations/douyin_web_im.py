"""Receive-only adapter for the Douyin Web IM protocol.

This module mirrors the receive side of the public cv-cat/DouYin_Spider
reference implementation. It is intentionally isolated from the production
private-message worker: this is a Web login/session protocol, not the official
Douyin Open Platform API.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional, Set
from urllib.parse import urlencode

try:
    import websocket
except ImportError:  # pragma: no cover - exercised only in minimal installs
    websocket = None

try:
    from google.protobuf.message import DecodeError
except ImportError:  # pragma: no cover - protobuf is a project dependency
    DecodeError = ValueError


logger = logging.getLogger(__name__)


DEFAULT_WS_URL = "wss://frontier-im.douyin.com/ws/v2"
DEFAULT_AID = "6383"
DEFAULT_FPID = "9"
DEFAULT_APP_KEY = "e1bd35ec9db7b8d846de66ed140b1ad9"
DEFAULT_ACCESS_KEY_SUFFIX = "f8a69f1719916z"
DEFAULT_ORIGIN = "https://www.douyin.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class DouyinWebIMEvent:
    """Normalized event emitted from a Web IM push frame."""

    event_type: str
    direction: str
    account_id: str
    sender_id: str = ""
    conversation_id: str = ""
    conversation_short_id: int = 0
    server_message_id: int = 0
    index_in_conversation: int = 0
    message_type: int = 0
    content: Any = None
    raw_content: str = ""
    payload_type: str = ""
    dedupe_key: str = ""
    received_at: float = field(default_factory=time.time)
    raw_frame: bytes = b""


class DouyinWebIMProtocolError(RuntimeError):
    """Raised when a Web IM frame cannot be decoded."""


def build_web_im_access_key(
    device_id: str,
    *,
    fpid: str = DEFAULT_FPID,
    app_key: str = DEFAULT_APP_KEY,
    suffix: str = DEFAULT_ACCESS_KEY_SUFFIX,
) -> str:
    """Build the access key used by the Web IM receive connection."""

    raw = f"{fpid}{app_key}{device_id}{suffix}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def build_web_im_url(
    device_id: str,
    session_id: str,
    *,
    ws_url: str = DEFAULT_WS_URL,
    aid: str = DEFAULT_AID,
    device_platform: str = "douyin_pc",
    fpid: str = DEFAULT_FPID,
    app_key: str = DEFAULT_APP_KEY,
) -> str:
    """Build the receive URL without exposing the Cookie itself."""

    params = {
        "aid": aid,
        "device_platform": device_platform,
        "fpid": fpid,
        "device_id": str(device_id),
        "token": str(session_id),
        "access_key": build_web_im_access_key(device_id, fpid=fpid, app_key=app_key),
    }
    return f"{ws_url}?{urlencode(params)}"


def _load_proto_types() -> Any:
    """Load generated protobuf classes lazily so import stays lightweight."""

    try:
        from aisec_agent.integrations.douyin_web_im_proto import Live_pb2, Response_pb2
    except ImportError as exc:  # pragma: no cover - setup error, not parser logic
        raise DouyinWebIMProtocolError(
            "Douyin Web IM protobuf classes are not installed; "
            "generate them from the reference .proto files first"
        ) from exc
    return Live_pb2.PushFrame, Response_pb2.Response


def _parse_content(raw_content: str) -> Any:
    try:
        return json.loads(raw_content)
    except (TypeError, ValueError):
        return raw_content


def _message_direction(sender_id: str, account_id: str, own_user_id: str) -> str:
    if own_user_id and str(sender_id) == str(own_user_id):
        return "outbound"
    return "inbound"


def parse_web_im_frame(
    frame_bytes: bytes,
    *,
    account_id: str,
    own_user_id: str = "",
) -> Optional[DouyinWebIMEvent]:
    """Parse one PushFrame into a normalized event.

    Non-protobuf JSON frames and protobuf notifications unrelated to a new
    message are ignored. The parser does not assume that every WebSocket
    frame is a ``new_message_notify`` event.
    """

    push_frame_cls, response_cls = _load_proto_types()
    frame = push_frame_cls()
    try:
        frame.ParseFromString(frame_bytes)
    except DecodeError as exc:
        raise DouyinWebIMProtocolError("invalid PushFrame protobuf") from exc

    payload_type = str(getattr(frame, "payloadType", "") or "")
    if payload_type == "text/json":
        return None
    if payload_type != "pb" or not getattr(frame, "payload", b""):
        return None

    response = response_cls()
    try:
        response.ParseFromString(frame.payload)
    except DecodeError as exc:
        raise DouyinWebIMProtocolError("invalid nested Web IM protobuf") from exc

    if not response.HasField("body") or not response.body.HasField("new_message_notify"):
        return None

    notification = response.body.new_message_notify
    if not notification.HasField("message"):
        return None
    message = notification.message
    sender_id = str(message.sender)
    server_message_id = int(message.server_message_id)
    conversation_id = str(message.conversation_id)
    index = int(message.index_in_conversation)
    dedupe_key = (
        f"server:{server_message_id}"
        if server_message_id
        else f"conversation:{conversation_id}:index:{index}:sender:{sender_id}"
    )
    return DouyinWebIMEvent(
        event_type="message",
        direction=_message_direction(sender_id, account_id, own_user_id),
        account_id=str(account_id),
        sender_id=sender_id,
        conversation_id=conversation_id,
        conversation_short_id=int(message.conversation_short_id),
        server_message_id=server_message_id,
        index_in_conversation=index,
        message_type=int(message.message_type),
        content=_parse_content(message.content),
        raw_content=str(message.content),
        payload_type=payload_type,
        dedupe_key=dedupe_key,
        raw_frame=bytes(frame_bytes),
    )


class DouyinWebIMReceiver:
    """A reconnecting, receive-only Web IM client.

    ``on_event`` should be fast. Persist or enqueue the event in the callback;
    do not run model generation on the WebSocket thread.
    """

    def __init__(
        self,
        *,
        account_id: str,
        session_id: str,
        device_id: str,
        on_event: Callable[[DouyinWebIMEvent], None],
        cookie_header: str = "",
        own_user_id: str = "",
        ws_url: str = DEFAULT_WS_URL,
        origin: str = DEFAULT_ORIGIN,
        user_agent: str = DEFAULT_USER_AGENT,
        reconnect_delay_seconds: float = 3.0,
        max_reconnect_delay_seconds: float = 60.0,
        dedupe_cache_size: int = 10_000,
        ws_app_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        if not str(account_id).strip():
            raise ValueError("account_id is required")
        if not str(session_id).strip():
            raise ValueError("session_id is required")
        if not str(device_id).strip():
            raise ValueError("device_id is required")
        if not callable(on_event):
            raise TypeError("on_event must be callable")
        self.account_id = str(account_id)
        self.session_id = str(session_id)
        self.device_id = str(device_id)
        self.cookie_header = str(cookie_header or f"sessionid={self.session_id}")
        self.own_user_id = str(own_user_id or "")
        self.on_event = on_event
        self.ws_url = ws_url
        self.origin = origin
        self.user_agent = user_agent
        self.reconnect_delay_seconds = max(0.1, float(reconnect_delay_seconds))
        self.max_reconnect_delay_seconds = max(
            self.reconnect_delay_seconds,
            float(max_reconnect_delay_seconds),
        )
        self.dedupe_cache_size = max(1, int(dedupe_cache_size))
        self.ws_app_factory = ws_app_factory
        self._seen_keys: Set[str] = set()
        self._seen_order: list[str] = []
        self._stop_event = threading.Event()
        self._ws: Any = None

    def _remember_event(self, event: DouyinWebIMEvent) -> bool:
        key = event.dedupe_key
        if not key or key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        self._seen_order.append(key)
        while len(self._seen_order) > self.dedupe_cache_size:
            expired = self._seen_order.pop(0)
            self._seen_keys.discard(expired)
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                logger.debug("failed to close Douyin Web IM socket", exc_info=True)

    def _on_message(self, _ws: Any, raw_message: Any) -> None:
        if isinstance(raw_message, str):
            return
        try:
            event = parse_web_im_frame(
                bytes(raw_message),
                account_id=self.account_id,
                own_user_id=self.own_user_id,
            )
        except Exception:
            logger.exception("failed to parse Douyin Web IM frame")
            return
        if event is not None and self._remember_event(event):
            self.on_event(event)

    def _create_ws(self, **callbacks: Any) -> Any:
        factory = self.ws_app_factory
        if factory is None:
            if websocket is None:
                raise RuntimeError("websocket-client is required for Douyin Web IM receiving")
            factory = websocket.WebSocketApp
        return factory(
            url=build_web_im_url(self.device_id, self.session_id, ws_url=self.ws_url),
            header={
                "Pragma": "no-cache",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "User-Agent": self.user_agent,
                "Cache-Control": "no-cache",
                "Sec-WebSocket-Protocol": "binary, base64, pbbp2",
                "Sec-WebSocket-Extensions": "permessage-deflate; client_max_window_bits",
            },
            cookie=self.cookie_header,
            **callbacks,
        )

    def run_forever(self) -> None:
        """Run until ``stop`` is called, reconnecting with backoff."""

        delay = self.reconnect_delay_seconds
        while not self._stop_event.is_set():
            closed_normally = False
            self._ws = self._create_ws(
                on_open=lambda ws: logger.info("Douyin Web IM connected: %s", self.account_id),
                on_message=self._on_message,
                on_error=lambda _ws, error: logger.warning("Douyin Web IM error: %s", error),
                on_close=lambda _ws, code, msg: logger.info(
                    "Douyin Web IM closed: account=%s code=%s message=%s",
                    self.account_id,
                    code,
                    msg,
                ),
            )
            try:
                self._ws.run_forever(origin=self.origin)
                closed_normally = self._stop_event.is_set()
            finally:
                self._ws = None
            if closed_normally:
                return
            self._stop_event.wait(delay)
            delay = min(delay * 2, self.max_reconnect_delay_seconds)


__all__ = [
    "DouyinWebIMEvent",
    "DouyinWebIMProtocolError",
    "DouyinWebIMReceiver",
    "build_web_im_access_key",
    "build_web_im_url",
    "parse_web_im_frame",
]
