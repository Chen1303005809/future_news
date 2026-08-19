# 资讯事件—期货收盘价时间对齐

实现入口是 [`zixun/time_alignment.py`](../zixun/time_alignment.py)。抓取、Kronos
产物读取和校准检索都通过这个模块比较时间；调用方不直接比较 SQLite 的时间字符串。

## 统一字段

| 字段 | 语义 |
| --- | --- |
| `observation_start` / `observation_end` | 文章明确描述的事实观察区间。当前页面没有结构化值时保留为空。 |
| `event_time` | 事实实际发生时间；没有明确证据时保留为空。 |
| `publish_time` | Mysteel 页面发布时间。旧库字符串在边界处解释为 `Asia/Shanghai`。 |
| `available_at` | 系统第一次能使用文章的时点。默认是 `publish_time`，并强制 `available_at >= publish_time`。 |
| `forecast_origin` | Kronos 最后一个已完成观测 bar 的 close 可用时点。 |
| `target_close_at` | 每个预测收益率对应的真实交易收盘时点。它来自目标 K 线的末根 close，而不是自然日。 |
| `price_echo` | 文章主要复述已经发生的价格/盘面变化时为 true。 |
| `conclusion_delay` | `publish_time - observation_end`，无法得到观察结束时为空。 |

所有 datetime 在内存中都是带 `Asia/Shanghai` 的 aware `datetime`，序列化为带
`+08:00` 的 ISO 字符串。只有兼容旧 SQLite 查询时才格式化为本地秒级字符串。

## K 线约定

供应商记录含 `TeD`（自然日）、`TiD`（交易日归属）和 `T`。本项目实测黑色系小时
数据的 `T` 为 bar 起始标签：日盘标签为 `09:00/10:00/11:00/13:00/14:00`，夜盘
通常为 `21:00/22:00`；`C` 在小时 bar 完成时可用。因此：

- `14:00` bar 的 `target_close_at` 是 `15:00`；
- `22:00` bar 的 close 是 `23:00`；
- 夜盘 bar 通过 `TiD` 归入下一个交易日；
- 周末、节假日和夜盘间隔不由自然日加一推断，而由 K 线中实际存在的交易日/端点给出；
- 缺少 `14:00` 日盘收盘 bar 的尾部交易日会被视为未完成，不生成虚假的 15:00 端点。

## 资讯过滤与端点映射

1. SQL 的 `publish_time` 窗口只是粗筛。
2. `available_at > forecast_origin` 的文章在校准前排除；预测起点之后发布的文章即使
   作用于未来端点，也不能进入这次校准。
3. 其余文章只映射到 `target_close_at >= available_at` 的端点。收盘后发布的文章从
   下一个尚未完成端点开始。
4. 统计期、周报、库存、发运、到港和调研的结束日期不会替代首次发布时间。
5. 同一 `event_key` 只保留首次披露；只有显式 `information_increment=true` 的新增事实、
   影响规模或状态变化才保留后续报道。
6. 日报/行情复盘会标记 `price_echo=true`。提示词要求对纯价格复述 abstain 或明显
   降权，避免把预测起点前已经发生的价格变化再次计入。
7. 事件影响窗口通过 `config/calibration.yaml` 的
   `time_alignment.impact_window_hours` 配置。默认为 null，即在当前三个真实端点内
   不凭感觉截断；历史事件研究后可按 `event_type` 配置小时窗口。

缺失发布时间的文章不会进入校准；缺失事件时间/观察结束时间不会由模型补写，只会
保留为空并使用可核实的 `publish_time`。

