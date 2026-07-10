import base64
import functools
import json
import logging
from typing import Callable, Any

from flask import jsonify, make_response, request, g, session
from pydantic import BaseModel
import functools
import logging
import traceback
import ast
from flask import Response
from aisec_agent.model.typing import Ret



def catch_it(*validator: Callable[..., bool]) -> Callable[..., Any]:
    def _catch_it(func):
        @functools.wraps(func)
        def _do(*args, **kwargs):
            try:
                if validator and not all(map(lambda v: v(), validator)):
                    return jsonify(err_no=403, msg='validate error')
                ret = func(*args, **kwargs)
                if isinstance(ret, BaseModel):
                    resp = make_response(ret.json())
                    resp.content_type = 'application/json'
                    return resp
                else:
                    return ret
            except Exception as e:
                logging.warning("%s request error: %s", func.__name__, e)
                return jsonify(err_no=500, msg=f'exception error {e}')

        return _do

    return _catch_it


def login_check(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        token = request.headers.get('Authorization', None)
        if not token:
            return jsonify(err_no=401, msg='请先登录')

        return func(*args, **kwargs)

    return wrapper




def handle_stream_or_normal(
    logic_func,
    stream_transformer=None,
    final_stream_transformer=None,
    normal_transformer=None
):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            # ✅ 正确拿 Flask 的 g.form，而不是 self.g
            form = getattr(g, 'form', None) or kwargs.get('form')
            if form is None:
                logging.error("handle_stream_or_normal: g.form 未注入（装饰器顺序或 form_validate 失败）")
                return Ret(err_no=400, msg="invalid request: form is missing").dict()

            # 兼容 pydantic BaseModel / 普通 dict
            try:
                form_dict = form.dict() if hasattr(form, "dict") else dict(form)
            except Exception:
                # 即使转换失败，也兜底个空字典，避免再次抛错
                logging.exception("handle_stream_or_normal: 转换 form 为 dict 失败")
                form_dict = {}

            stream = getattr(form, 'stream', False)

            try:
                if stream:
                    def stream_response():
                        accumulated_content = ""
                        try:
                            for chunk in logic_func(**form_dict):
                                try:
                                    if chunk:
                                        if stream_transformer:
                                            out = stream_transformer(chunk, accumulated_content)
                                        else:
                                            accumulated_content += str(chunk)
                                            out = accumulated_content
                                        yield f"data: {Ret(data=out).json()}\n\n"
                                except Exception:
                                    logging.exception("处理流数据块出错")
                                    continue

                            if final_stream_transformer:
                                final_stream_transformer(accumulated_content, form)

                            yield f"data: {Ret(data=True).json()}\n\n"

                        except Exception:
                            error_details = traceback.format_exc()
                            logging.error(f"流响应生成错误: {error_details}")
                            yield f"data: {Ret(err_no=500, msg=str(error_details)).json()}\n\n"
                            yield f"data: {Ret(data=True).json()}\n\n"

                    resp = Response(stream_response(), mimetype="text/event-stream")
                    resp.headers.update({
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                        "Content-Type": "text/event-stream; charset=utf-8"
                    })
                    return resp

                else:
                    try:
                        content = None
                        for item in logic_func(**form_dict):
                            content = item

                        # 容错解析
                        try:
                            content = ast.literal_eval(content)
                        except Exception:
                            pass
                        if isinstance(content, bytes):
                            content = content.decode('utf-8', errors='replace')

                        if normal_transformer:
                            return normal_transformer(content, form)
                        else:
                            return Ret(data={"answer": content, "reference": []}).dict()

                    except Exception:
                        error_details = traceback.format_exc()
                        logging.error(f"非流式调用错误: {error_details}")
                        return Ret(err_no=500, msg=str(error_details)).dict()

            except Exception as e:
                error_details = traceback.format_exc()
                logging.error(f"API调用错误: {error_details}")
                return Ret(err_no=500, msg=str(e)).dict()

        return wrapper
    return decorator
