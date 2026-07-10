from typing import Iterable, Mapping, Any, Tuple, Callable

from flask import Flask, Blueprint
from werkzeug.wrappers import Response

__author__ = 'Memory_Leak<yuz@cnns.net>'
__all__ = ('make_app',)


def make_app(name: str = __name__, blue_prints: Iterable[Blueprint] = None, plugins: Iterable = None,
             config: Mapping[str, Any] = None, mount: Iterable[Tuple[str, callable]] = None,
             after_requests: Iterable[Callable[[Response], Response]] = None) -> Flask:
    """
    创建Flask对象
    :param name:
    :param blue_prints:
    :param plugins:
    :param config:
    :param mount:
    :param after_requests:
    :return:
    """
    app = Flask(name)
    if config:
        app.config.update(**config)
    if blue_prints:
        for bl in blue_prints:
            app.register_blueprint(bl)
    if mount:
        for r, f in mount:
            app.route(r)(f)
    if after_requests:
        for m in after_requests:
            app.after_request(m)
    return app


