



class AgentException(Exception):
    """
    认证模块异常基类
    """

    def __init__(self, code: int = 500, msg: str = ""):
        self.message = msg
        self.code = code

    msg = property(lambda self: self.message)

    def _prefix(self):

        tb = self.__traceback__
        cn = self.__class__.__name__

        if tb:
            return "{}:{}:{}".format(tb.tb_frame.f_code.co_filename, cn, tb.tb_lineno)
        return cn

    def __str__(self):
        return "{} {}".format(self._prefix(), self.message)

    @classmethod
    def new(cls, code: int = 500, msg: str = "") -> "AgentException":
        t = cls()
        t.code = code
        t.message = msg
        return t


class BoxCtrlException(AgentException):
    ...


class AgentNetworkError(AgentException):
    def __init__(self, code: int = 50201, msg: str = "网络异常"):
        super().__init__(code, msg)


class AgentRespError(AgentException):
    def __init__(self, code: int = 50400, msg: str = "请求异常"):
        super().__init__(code, msg)


class MessageException(AgentException):
    """
    异常消息
    """

    def __init__(self, message: str):
        super().__init__()
        self.code = 418
        self.message = message

    def __str__(self):
        return self.message




