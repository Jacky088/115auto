"""pytest 全局测试夹具与兼容补丁。"""
import pathlib

# 兼容 Python 3.12 及以下版本中 Path.is_file 不支持 follow_symlinks 参数的问题
_orig_is_file = pathlib.Path.is_file


def _compat_is_file(self, *args, **kwargs):
    try:
        return _orig_is_file(self, *args, **kwargs)
    except TypeError:
        kwargs.pop("follow_symlinks", None)
        return _orig_is_file(self, *args, **kwargs)


pathlib.Path.is_file = _compat_is_file
