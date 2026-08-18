"""命令行入口。

用法：
  python -m zixun.cli init                       # 初始化数据库
  python -m zixun.cli run --dry-run              # 干跑（只解析不入库）
  python -m zixun.cli run                        # 抓取
  python -m zixun.cli run --source iron_daily    # 仅抓指定栏目
  python -m zixun.cli backfill --pages 5         # 历史回填（翻 5 页）
"""
from __future__ import annotations

import argparse
import logging
import sys

from .pipeline import run
from .settings import DB_PATH
from .storage import init_db


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_stats(stats: dict[str, dict]) -> None:
    if not stats:
        return
    print("\n=== 抓取统计 ===")
    print(
        f"{'栏目':<28} {'列出':>6} {'筛掉':>6} {'保留':>6} "
        f"{'新增':>6} {'跳过':>6} {'失败':>6}"
    )
    tot = {
        "listed": 0, "filtered": 0, "kept": 0,
        "new": 0, "skipped": 0, "failed": 0,
    }
    for sid, st in stats.items():
        print(
            f"{sid:<28} {st['listed']:>6} {st['filtered']:>6} {st['kept']:>6} "
            f"{st['new']:>6} {st['skipped']:>6} {st['failed']:>6}"
        )
        for k in tot:
            tot[k] += st[k]
    print("-" * 70)
    print(
        f"{'合计':<28} {tot['listed']:>6} {tot['filtered']:>6} {tot['kept']:>6} "
        f"{tot['new']:>6} {tot['skipped']:>6} {tot['failed']:>6}"
    )


def cmd_init(args) -> int:
    init_db()
    print(f"数据库已初始化: {DB_PATH}")
    return 0


def cmd_run(args) -> int:
    stats = run(
        dry_run=args.dry_run,
        source_id=args.source,
    )
    _print_stats(stats)
    return 0


def cmd_backfill(args) -> int:
    stats = run(
        max_pages_override=args.pages,
    )
    _print_stats(stats)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zixun", description="黑色系现货资讯抓取")
    p.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="初始化数据库")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("run", help="抓取最新文章")
    sp.add_argument("--dry-run", action="store_true", help="只解析不入库（验证用）")
    sp.add_argument("--source", help="仅抓指定栏目 id")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("backfill", help="历史回填（翻多页）")
    sp.add_argument("--pages", type=int, default=5, help="每个栏目翻几页")
    sp.set_defaults(func=cmd_backfill)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
