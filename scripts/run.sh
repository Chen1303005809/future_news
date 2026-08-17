#!/usr/bin/env bash
# zixun 定时抓取入口（供 cron 调用）
#
# 用法：
#   ./scripts/run.sh                # 抓取最新（默认）
#   ./scripts/run.sh backfill 5     # 历史回填，每栏目翻 5 页
#
# cron 示例（每天 09:13 / 13:17 / 18:23 抓取）：
#   13 9 * * *  /Users/eurus/Code/zixun/scripts/run.sh
#   17 13 * * * /Users/eurus/Code/zixun/scripts/run.sh
#   23 18 * * * /Users/eurus/Code/zixun/scripts/run.sh
set -euo pipefail

# 切到项目根（脚本所在目录的上一级）
cd "$(dirname "$0")/.."

# 确保能找到 python（cron 环境 PATH 很精简，显式加入 miniconda）。
# 若项目虚拟环境存在，默认优先使用它；也可用 PYTHON 或 ZIXUN_PYTHON 覆盖。
export PATH="/opt/miniconda3/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

if [[ -n "${PYTHON:-}" ]]; then
  PYBIN="$PYTHON"
elif [[ -n "${ZIXUN_PYTHON:-}" ]]; then
  PYBIN="$ZIXUN_PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
  PYBIN=".venv/bin/python"
else
  PYBIN="python3"
fi
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# 命令：默认 run；传 "backfill N" 则历史回填
if [[ "${1:-run}" == "backfill" ]]; then
  PAGES="${2:-5}"
  exec "$PYBIN" -m zixun.cli backfill --pages "$PAGES" >> "$LOG_DIR/cron.log" 2>&1
else
  exec "$PYBIN" -m zixun.cli run >> "$LOG_DIR/cron.log" 2>&1
fi
