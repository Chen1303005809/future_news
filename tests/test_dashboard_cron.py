from __future__ import annotations

import contextlib
import importlib
import io
import sys
import unittest
from unittest.mock import patch

import streamlit

import zixun.cron as cron


def _run_fragment_inline(function=None, *args, **kwargs):
    """让管理区在测试中同步执行，覆盖实际的渲染代码路径。"""
    del args, kwargs

    if function is not None:
        return function

    def decorate(function):
        return function

    return decorate


class DashboardCronTests(unittest.TestCase):
    def test_dashboard_renders_an_existing_cron_entry(self) -> None:
        entry = {
            "schedule": "7 9 * * *",
            "label": "回归测试",
            "enabled": True,
        }
        sys.modules.pop("zixun.dashboard", None)

        try:
            with (
                patch.object(streamlit, "fragment", _run_fragment_inline),
                patch.object(cron, "list_entries", return_value=[entry]),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                importlib.import_module("zixun.dashboard")
        finally:
            sys.modules.pop("zixun.dashboard", None)


if __name__ == "__main__":
    unittest.main()
