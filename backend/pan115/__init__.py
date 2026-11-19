"""115 网盘 SDK 封装包。

提供登录态与容量缓存、风控自适应限速、目录读写以及与 Python 3.12 的 Path.is_file 兼容补丁。
"""
import pathlib

# 兼容 Python 3.12 及以下版本中 Path.is_file 不支持 follow_symlinks 参数的问题
# 部分第三方依赖（如 p115client 0.0.9.7.3+）内部在 __init__.py 误用了 Python 3.13 新增的 follow_symlinks 参数，
# 此处通过全局透明代理吃掉不受支持的关键字参数，确保在 Python 3.12 下永不崩溃。
_orig_is_file = pathlib.Path.is_file


def _compat_is_file(self, *args, **kwargs):
    try:
        return _orig_is_file(self, *args, **kwargs)
    except TypeError:
        kwargs.pop("follow_symlinks", None)
        return _orig_is_file(self, *args, **kwargs)


pathlib.Path.is_file = _compat_is_file
