# zixun —— 黑色系现货资讯抓取与浏览系统

定期抓取我的钢铁网（mysteel）**螺纹钢 / 铁矿石 / 焦煤焦炭** 三个品种的现货资讯
（日报/早报、库存产量等基本面数据、供需快讯、趋势研判），入库后通过 Streamlit
面板浏览，用于后续与 K 线走势数据结合做趋势分析。

- **聚焦现货基本面**，已排除期货价格类栏目（预测端已有期货数据源）。
- 抓取网站自带的 **AI 摘要** 作为核心内容（高度浓缩的市场总结）。
- 文章带**品种标签**和**精确发布时间**，便于和 K 线按时间对齐。
- 栏目配置驱动（`config/sources.yaml`），加品种/栏目只改配置不动代码。

---

## 依赖安装

需要 Python 3.10+。

```bash
pip install -r requirements.txt
```

> 若 pypi 官方源连接不稳定，可使用国内镜像：
> `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

---

## 快速开始

```bash
# 1. 初始化数据库
python -m zixun.cli init

# 2. 干跑验证（只解析不入库，看各栏目抓到几篇、标题样本）
python -m zixun.cli run --dry-run --priority core

# 3. 实际抓取（默认所有栏目）
python -m zixun.cli run

# 4. 历史回填（每栏目翻 5 页，补抓近期历史）
python -m zixun.cli backfill --pages 5

# 5. 启动前端面板
streamlit run zixun/dashboard.py
```

### 在面板里直接抓取 / 配置定时任务

启动面板后（`streamlit run zixun/dashboard.py`），页面顶部「**⚙️ 抓取与定时**」区提供：

- **🚀 抓取全部 / ⚡ 仅抓 core**：一键触发后台抓取，子进程独立运行，不阻塞面板浏览；状态与日志每 5 秒自动刷新。
- **抓取日志**：展开可看尾部 20 行（含每个栏目解析篇数、新增文章标题）。
- **定时任务（crontab）管理**：
  - 一键应用预设：**每天 3 次（9:13 / 13:17 / 18:23）** 或 **每天 1 次（08:37）**
  - 自定义时间（分/时/说明）添加
  - 已有条目可**启用 / 禁用 / 删除**
  - 只管理带 `# zixun-cron` 标记的条目，**不影响系统其他定时任务**

---

## 命令一览

| 命令 | 说明 |
|------|------|
| `python -m zixun.cli init` | 初始化 SQLite 数据库 |
| `python -m zixun.cli run` | 抓取最新文章并入库 |
| `python -m zixun.cli run --dry-run` | 干跑：只解析、不入库（验证用） |
| `python -m zixun.cli run --source <id>` | 仅抓指定栏目（栏目 id 见 `sources.yaml`） |
| `python -m zixun.cli run --priority core` | 仅抓 core 优先级栏目 |
| `python -m zixun.cli backfill --pages N` | 历史回填（每栏目翻 N 页） |
| `streamlit run zixun/dashboard.py` | 启动前端面板（默认 http://localhost:8501） |

`-v` 开启调试日志，例如 `python -m zixun.cli -v run`。

---

## 定时抓取（cron）

用 `scripts/run.sh` 作为入口（已处理项目目录与 Python 路径）：

```cron
# 每天抓 3 次（早报多上午发、日报多傍晚发；错峰避开整点）
13 9 * * *  /Users/eurus/Code/zixun/scripts/run.sh
17 13 * * * /Users/eurus/Code/zixun/scripts/run.sh
23 18 * * * /Users/eurus/Code/zixun/scripts/run.sh
```

查看 `crontab -e` 添加。日志写入 `logs/cron.log`。

首次部署建议先跑一次回填建立历史语料：
```cron
# 一次性历史回填（执行后可删除该行）
37 8 * * * /Users/eurus/Code/zixun/scripts/run.sh backfill 5
```

---

## 抓取筛选规则

为避免抓入**无法反映整体趋势**的地区性资讯（如"山东/湖南/唐山建筑钢材早报"），
系统在标题层做了条件筛选。规则存 `config/filters.yaml`，面板可在线编辑。

筛选顺序：
1. 命中**排除词**（会议/评选/广告…）→ 丢弃
2. **地区过滤**：标题含地区词（省份/城市/港口/区域）且**不含全局词**（全国/整体/宏观…）→ 丢弃
   - 保留"全国建筑钢材早报"，丢弃"山东建筑钢材早报"
3. **白名单**（仅 analysis 类栏目）：标题须命中相关词（库存/产量/调价…）才保留

预览某栏目会被过滤掉哪些（不入库）：
```bash
python -m zixun.cli run --dry-run --source rebar_daily
# 日志会打印 "[dry-run] 丢弃 [rebar_daily|地区资讯] <标题>"
```

面板顶部「🎯 抓取筛选规则」可：
- 开关筛选 / 开关地区过滤
- 编辑四组词库（地区词 / 全局词 / 排除词 / 白名单，分 tab）
- 保存后对后续抓取立即生效

| 文件 | 作用 |
|------|------|
| `config/filters.yaml` | 筛选词库（地区/全局/排除/白名单） |
| `zixun/filters.py` | `evaluate()` 筛选判定 + 加载/保存 |

---

## 项目结构

```
zixun/
├── config/sources.yaml     # 栏目配置（品种/URL/类型/优先级/黑名单）
├── zixun/
│   ├── fetcher.py          # HTTP 抓取（限速/重试/防反爬）
│   ├── parser.py           # 列表页+详情页通用解析
│   ├── classifier.py       # 品种分类
│   ├── filters.py          # 抓取筛选（地区/排除/白名单）
│   ├── storage.py          # SQLite 存储 + Markdown 导出
│   ├── pipeline.py         # 抓取主流程
│   ├── queries.py          # 面板数据查询
│   ├── runner.py           # 面板触发的后台抓取执行器
│   ├── cron.py             # 面板调用的 crontab 管理（带标记）
│   ├── cli.py              # 命令行入口
│   └── dashboard.py        # Streamlit 面板
├── scripts/run.sh          # cron 入口
├── data/zixun.db           # SQLite（运行时生成）
└── articles/               # Markdown 导出（按品种/日期）
```

---

## 数据说明

### SQLite `articles` 表

| 字段 | 说明 |
|------|------|
| `url`, `url_hash` | 文章原文 URL 及其 md5（去重键） |
| `title` | 标题 |
| `variety` | 品种标签，逗号分隔：`rebar` / `ironore` / `cokingcoal` / `coke` |
| `report_type` | `daily` / `weekly` / `monthly` / `data` / `analysis` |
| `source_channel` | 频道：`jiancai` / `tks` / `coal` / `list1` |
| `source_id` | 栏目 id（对应 `sources.yaml`） |
| `publish_time` | 精确发布时间（与 K 线对齐用） |
| `ai_summary` | 网站自带 AI 摘要（核心内容） |
| `body_text` | 正文纯文本（过短时用摘要兜底） |
| `priority` | `core` / `optional` |

索引：`(variety, publish_time)`、`(publish_time)`、`(report_type, publish_time)`。

### 与 K 线对齐

`publish_time` 是真实 `DATETIME`，可直接按日聚合与 K 线 join。
`queries.count_by_day()` 已提供"按日 × 主品种"的篇数序列，可作为 K 线副图数据源。

---

## 资讯校准 K线预测（`calibration/`）

把 Kronos 生成的后三日预测与资讯打通：读 `forecast_result.json`，按其元数据
（品种 + 时间）从资讯库筛出相关资讯，喂 LLM 做**研判 + 温和数值校准**，
输出 `calibration.json`。纯后处理，不改 Kronos 代码。

```bash
# 1. 依赖：安装 openai SDK（已加入 requirements.txt）
pip install -r requirements.txt

# 2. 配置 LLM（config/calibration.yaml，OpenAI 协议，可换通义/DeepSeek 等）
export OPENAI_API_KEY=sk-xxx

# 3. 校准一次预测
python -m calibration \
  --input outputs/three_day_i2609_20260814/forecast_result.json \
  [--lookback-days 3] [--max-articles 15] [--model deepseek-chat]
```

`calibration.json` 含：LLM 研判（`view`/`confidence`/`commentary`）、三日的
原始 vs 校准值（`original`/`calibrated`/`applied_shift`）、来源资讯列表
（`sources`，可追溯）。校准边界温和：单日概率偏移 ≤±0.10、收益率偏移 ≤±1.5%，
越界请求会被裁剪（`applied_shift` 记录实际生效值）。

品种映射：合约前缀 `i→ironore`、`rb→rebar`、`j→coke`、`jm→cokingcoal`。
目标品种在窗口内无资讯时默认退到黑色系通用资讯（可用 `--no-variety-fallback` 关闭）。

退出码：`0` 成功（含无资讯跳过校准）、`2` 未知合约、`3` 品种无数据且未兜底、
`4` LLM 调用失败、`5` LLM 非法 JSON（均透传模型原值）、`6` 预测文件缺字段。

---

## 栏目配置（`config/sources.yaml`）

加新品种/栏目只需追加一条：

```yaml
sources:
  - id: rebar_steel_mill_price        # 唯一 id
    variety: [rebar]                  # 品种标签
    channel: list1                    # 频道
    report_type: data                 # daily/weekly/monthly/data/analysis
    priority: optional                # core/optional
    list_url: "https://list1.mysteel.com/article/p-XXX-------------{page}.html"
    max_pages: 1                      # 翻几页
```

- `{page}` 为页码占位符。
- `title_blacklist` 全局生效，命中词的标题直接丢弃。
- `report_type: analysis` 类栏目额外启用相关词白名单，避免抓到无关软文。
- 已排除期货价格类栏目（黑色期货早报、连铁持仓等）。

---

## 反爬与礼貌策略

- 单线程顺序抓取，每请求间隔 1–2 秒随机抖动。
- 真实浏览器 UA，失败指数退避重试（最多 3 次）。
- 如遇风控，可调大 `Fetcher` 的 `delay_range`。
