import calendar
from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta


def get_last_day(current: datetime, dt=1):
    last = current - timedelta(days=1)
    return last.replace(hour=0, minute=0, second=0), last.replace(hour=23, minute=59, second=59)

def get_last_month_range(current: datetime):
    first_day_of_this_month = current.replace(day=1)
    last_day_of_last_month = first_day_of_this_month - timedelta(days=1)
    first_day_of_last_month = last_day_of_last_month.replace(day=1)
    return first_day_of_last_month.replace(hour=0, minute=0, second=0), last_day_of_last_month.replace(hour=23,
                                                                                                       minute=59,
                                                                                                       second=59)


def get_last_week_range(current: datetime):
    start_of_this_week = current - timedelta(days=current.weekday())
    end_of_last_week = start_of_this_week - timedelta(days=1)
    start_of_last_week = end_of_last_week - timedelta(days=6)
    return start_of_last_week.replace(hour=0, minute=0, second=0), end_of_last_week.replace(hour=23, minute=59,
                                                                                            second=59)

def convert_datetime_to_str(dt: datetime) -> str:
    """转化日期时间为字符串"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def convert_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M:%S").time()


def get_before_3_months() -> tuple[datetime, datetime]:
    end_time = datetime.now()
    start_time = end_time - timedelta(days=90)
    return start_time, end_time


def start_end_day() -> tuple[str, str]:
    """时间维度-天"""
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
    return convert_datetime_to_str(start), convert_datetime_to_str(end)


def start_end_week() -> tuple[str, str]:
    """时间维度-周"""
    end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
    start = (end - relativedelta(weeks=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return convert_datetime_to_str(start), convert_datetime_to_str(end)


def start_end_month() -> tuple[str, str]:
    """时间维度-月"""
    end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
    start = (end - relativedelta(months=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return convert_datetime_to_str(start), convert_datetime_to_str(end)


def get_month_range(date: datetime):
    # 获取当前月份的第一天
    first_day = date.replace(day=1)

    # 获取当前月份的最后一天
    last_day = date.replace(day=calendar.monthrange(date.year, date.month)[1])

    return int(first_day.strftime("%Y%m%d")), int(last_day.strftime("%Y%m%d"))


def get_week_of_year(date: datetime) -> int:
    # 使用 isocalendar 方法获取年、周数和星期几
    _, week_of_year, _ = date.isocalendar()
    return week_of_year


def get_week_range(date: datetime):
    # 获取当前日期的星期几，1 是星期一，7 是星期日
    current_weekday = date.isoweekday()

    # 计算这周的第一天（星期一）
    first_day = date - timedelta(days=current_weekday - 1)

    # 计算这周的最后一天（星期日）
    last_day = date + timedelta(days=(7 - current_weekday))

    return int(first_day.strftime("%Y%m%d")), int(last_day.strftime("%Y%m%d"))


def seconds_to_target_time(days: int, hour: int, minute: int = 0, second: int = 0):
    """
    计算从当前时间到第 n 天 n 点 n 分 n 秒的秒数。

    :param days: 从今天起第 n 天（0 表示今天，1 表示明天，以此类推）
    :param hour: 指定的小时（0-23）
    :param minute: 指定的分钟（0-59）
    :param second: 指定的秒数（0-59）
    :return: 到目标时间的秒数
    """
    now = datetime.now()
    # 目标时间为 n_days 天后的 n_hour 点 n_minute 分 n_second 秒
    target_time = (now + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=second, microsecond=0
    )
    return int((target_time - now).total_seconds())
