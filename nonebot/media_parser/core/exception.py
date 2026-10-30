"""解析器异常。

限制类异常会把「实际值和上限」带进消息，用户看到的是
「视频时长 12 分 30 秒，超过上限 10 分钟」而不是笼统的「超过配置限制」。
"""


def human_size(num_bytes: float) -> str:
    """字节转可读大小。"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def human_duration(seconds: float) -> str:
    """秒转可读时长：90 -> 1 分 30 秒。"""
    total = int(round(max(seconds, 0)))
    if total < 60:
        return f"{total} 秒"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {secs} 秒" if secs else f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"


class ParseException(Exception):
    """异常基类"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TipException(ParseException):
    """提示异常。

    `reason` 是给管理面板日志用的短标签，`detail` 是给人看的完整说明。
    """

    reason = "提示"
    detail = ""

    def __init__(self, message: str | None = None, *, detail: str | None = None):
        super().__init__(message or self.detail or self.reason)
        self.detail = detail or self.detail or self.message


class DownloadException(ParseException):
    """下载异常"""

    reason = "下载失败"

    def __init__(self, message: str | None = None, *, detail: str | None = None):
        super().__init__(message or "媒体下载失败")
        self.detail = detail or self.message


class DownloadLimitException(DownloadException):
    """下载超过限制异常"""

    reason = "超出限制"


class SizeLimitException(DownloadLimitException):
    """下载大小超过限制异常"""

    def __init__(self, actual_bytes: int | None = None, limit_mb: float | None = None):
        if actual_bytes is not None and limit_mb is not None:
            message = f"媒体大小 {human_size(actual_bytes)}，超过上限 {limit_mb:g} MB，已取消下载"
        elif limit_mb is not None:
            message = f"媒体大小超过上限 {limit_mb:g} MB，已取消下载"
        else:
            message = "媒体大小超过配置限制，取消下载"
        super().__init__(message, detail=message)


class DurationLimitException(DownloadLimitException):
    """下载时长超过限制异常"""

    def __init__(self, actual_seconds: float | None = None, limit_minutes: float | None = None):
        if actual_seconds is not None and limit_minutes is not None:
            message = (
                f"视频时长 {human_duration(actual_seconds)}，"
                f"超过上限 {limit_minutes:g} 分钟，已取消下载"
            )
        elif limit_minutes is not None:
            message = f"视频时长超过上限 {limit_minutes:g} 分钟，已取消下载"
        else:
            message = "媒体时长超过配置限制，取消下载"
        super().__init__(message, detail=message)


class ZeroSizeException(DownloadException):
    """下载大小为 0 异常"""

    reason = "空文件"

    def __init__(self):
        super().__init__("媒体大小为 0, 取消下载")


class RedirectException(DownloadException):
    """下载重定向异常"""

    reason = "重定向异常"

    def __init__(self):
        super().__init__("媒体链接重定向时出现异常")
