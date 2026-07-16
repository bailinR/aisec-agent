from __future__ import annotations

import functools
from typing import Any, Callable, Iterable

from flask import g, request
from pydantic import ValidationError

from aisec_agent.model.typing import Ret

__version__ = "0.0.1.1"


def _mode_set(mode: Any) -> set[str]:
    if mode is None:
        return {"form"}
    if isinstance(mode, str):
        return {mode.strip().lower()}
    if isinstance(mode, Iterable):
        return {str(item).strip().lower() for item in mode}
    return {"form"}


def _request_payload(modes: set[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if "query" in modes:
        payload.update(request.args.to_dict(flat=True))
    if "json" in modes or "form" in modes:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            payload.update(body)
        payload.update(request.form.to_dict(flat=True))
    if "file" in modes:
        payload.update(request.files.to_dict(flat=True))
    if not payload:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            payload.update(body)
        payload.update(request.form.to_dict(flat=True))
        payload.update(request.args.to_dict(flat=True))
        payload.update(request.files.to_dict(flat=True))
    return payload


def form_validate(form_cls: Callable[..., Any], mode: Any = "form") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    modes = _mode_set(mode)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            try:
                payload = _request_payload(modes)
                form = form_cls(**payload)
                g.form = form
                return func(*args, **kwargs)
            except ValidationError as exc:
                return Ret(code=400, msg=str(exc)).dict()
            except Exception as exc:
                return Ret(code=500, msg=str(exc)).dict()

        return wrapper

    return decorator
