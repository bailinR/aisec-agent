"""Run the receive-only Douyin Web IM experiment."""

from __future__ import annotations

import argparse
import json
import logging
import os
from http.cookies import SimpleCookie

from aisec_agent.integrations.douyin_web_im import DouyinWebIMEvent, DouyinWebIMReceiver


def _session_id_from_cookie(cookie_header: str) -> str:
    parsed = SimpleCookie()
    parsed.load(cookie_header)
    return str(parsed.get("sessionid").value if parsed.get("sessionid") else "").strip()


def _event_for_log(event: DouyinWebIMEvent) -> dict:
    return {
        "event_type": event.event_type,
        "direction": event.direction,
        "account_id": event.account_id,
        "sender_id": event.sender_id,
        "conversation_id": event.conversation_id,
        "conversation_short_id": event.conversation_short_id,
        "server_message_id": event.server_message_id,
        "index_in_conversation": event.index_in_conversation,
        "message_type": event.message_type,
        "content": event.content,
        "dedupe_key": event.dedupe_key,
        "received_at": event.received_at,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Receive-only Douyin Web IM listener.")
    parser.add_argument("--account-id", default=os.getenv("DOUYIN_WEB_IM_ACCOUNT_ID", "douyin_web_01"))
    parser.add_argument("--session-id", default=os.getenv("DOUYIN_WEB_IM_SESSION_ID", ""))
    parser.add_argument("--device-id", default=os.getenv("DOUYIN_WEB_IM_DEVICE_ID", ""))
    parser.add_argument("--own-user-id", default=os.getenv("DOUYIN_WEB_IM_OWN_USER_ID", ""))
    parser.add_argument("--cookie-env", default="DOUYIN_WEB_IM_COOKIE")
    args = parser.parse_args(argv)

    cookie_header = str(os.getenv(args.cookie_env, "") or "").strip()
    session_id = str(args.session_id or _session_id_from_cookie(cookie_header)).strip()
    if not cookie_header:
        raise SystemExit(f"environment variable {args.cookie_env} is required")
    if not session_id:
        raise SystemExit("sessionid is required in --session-id or the Cookie header")
    if not args.device_id:
        raise SystemExit("--device-id or DOUYIN_WEB_IM_DEVICE_ID is required")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    def on_event(event: DouyinWebIMEvent) -> None:
        logging.getLogger(__name__).info("douyin_web_im_event=%s", json.dumps(_event_for_log(event), ensure_ascii=False))

    receiver = DouyinWebIMReceiver(
        account_id=args.account_id,
        session_id=session_id,
        device_id=args.device_id,
        cookie_header=cookie_header,
        own_user_id=args.own_user_id,
        on_event=on_event,
    )
    try:
        receiver.run_forever()
    except KeyboardInterrupt:
        receiver.stop()


if __name__ == "__main__":
    main()

