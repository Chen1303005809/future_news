"""资讯校准 K线预测 —— 独立后处理包。

读 Kronos 生成的 ``forecast_result.json``，按其元数据（品种 + 时间）从资讯库
（复用 ``zixun.queries``）筛出相关资讯，喂给 LLM 做"研判 + 温和数值校准"，
输出 ``calibration.json``。与 Kronos 解耦，纯后处理。

用法：
    python -m calibration --input outputs/three_day_i2609_20260814/forecast_result.json
"""
