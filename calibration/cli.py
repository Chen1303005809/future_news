"""资讯校准 CLI 入口。

用法：
    python -m calibration \
        --input outputs/three_day_i2609_20260814/forecast_result.json \
        [--output calibration.json] [--lookback-days 3] [--max-articles 15] ...

退出码语义（供上游脚本判断）：
    0  成功（含"无资讯跳过校准"：此时 calibration.json 已写出，view=range，透传原值）
    2  未知合约前缀，无法确定品种（不调 LLM）
    3  目标品种 DB 无资讯且未启用黑色系兜底
    4  LLM 调用最终失败（已透传原值，meta.llm_error 有详情）
    5  LLM 返回非法 JSON（重试后仍失败，已透传原值，meta.parse_error）
    6  forecast_result.json 缺失关键字段（ForecastLoadError）

LLM key 从环境变量 OPENAI_API_KEY 读取，不进 CLI 参数。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .article_retrieval import retrieve_articles
from .calibration_engine import CalibrationParseError, CalibrationResult, apply_calibration
from .config import CalibrationConfig
from .forecast_loader import ForecastLoadError, ForecastSnapshot, load_forecast
from .instrument_mapping import instrument_to_variety
from .llm_client import LLMConfigError, call_llm
from .output_writer import build_output, write_calibration
from .prompt_builder import build_messages

EXIT_OK = 0
EXIT_UNKNOWN_VARIETY = 2
EXIT_NO_ARTICLES_NO_FALLBACK = 3
EXIT_LLM_FAILED = 4
EXIT_PARSE_FAILED = 5
EXIT_FORECAST_LOAD_FAILED = 6

logger = logging.getLogger("calibration")


def _render_calibrated_plot(input_path: Path, calibration_path: Path) -> None:
    """在校准成功后生成带橙色校准线的预测图；绘图失败不影响校准产物。"""
    try:
        from .forecast_plot import render_calibrated_forecast_plot

        # 覆盖预测步骤刚生成的同一张图；下次预测会先重新生成原始图，再校准叠加。
        plot_path = input_path.parent / "forecast_plot.png"
        staged_path = plot_path.with_name(
            f".{plot_path.stem}.calibrating{plot_path.suffix}"
        )
        try:
            render_calibrated_forecast_plot(input_path, calibration_path, staged_path)
            staged_path.replace(plot_path)
        finally:
            staged_path.unlink(missing_ok=True)
        logger.info("已写出校准叠加图 %s", plot_path)
    except Exception as exc:  # noqa: BLE001 - 图表是附加产物，不能遮蔽校准结果
        logger.warning("校准叠加图生成失败：%s", exc)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _passthrough_result(
    snapshot: ForecastSnapshot,
    *,
    view: str,
    confidence: float,
    commentary: str,
    llm_meta: dict,
    variety_fallback: bool = False,
    variety_queried: tuple[str, ...] = (),
    used_articles: list | None = None,
) -> CalibrationResult:
    """构造"未校准/透传原值"的结果：三日 calibrated == original，shift=0。"""
    from .calibration_engine import DayCalibration

    days = tuple(
        DayCalibration(
            day=d.day,
            original_probability=d.up_probability,
            calibrated_probability=d.up_probability,
            original_return=d.predicted_return,
            calibrated_return=d.predicted_return,
            applied_prob_shift=0.0,
            applied_return_shift=0.0,
            agreement="agree",
            direction_flipped=False,
            target_close_at=d.target_close_at,
        )
        for d in snapshot.days
    )
    return CalibrationResult(
        view=view,
        confidence=confidence,
        commentary=commentary,
        days=days,
        variety_fallback=variety_fallback,
        variety_queried=variety_queried,
        used_articles=list(used_articles or []),
        llm_meta=llm_meta,
    )


def _run_calibration(
    input_path: Path,
    output_path: Path,
    config: CalibrationConfig,
) -> int:
    # 1. 加载预测快照
    snapshot = load_forecast(input_path)

    # 2. 品种映射
    variety, prefix = instrument_to_variety(snapshot.instrument)
    if variety is None:
        logger.warning("无法识别合约前缀 '%s'（品种），跳过校准", prefix)
        return EXIT_UNKNOWN_VARIETY

    # 3. 检索资讯（含无数据时的黑色系兜底）
    digests, meta = retrieve_articles(
        variety=variety,
        origin_timestamp=snapshot.origin_timestamp,
        lookback_days=config.lookback_days,
        max_articles=config.max_articles,
        fallback_to_black_sector=config.fallback_to_black_sector,
        endpoints=snapshot.endpoints,
        alignment_policy=config.alignment_policy,
    )
    logger.info(
        "品种=%s 窗口=%s ~ %s 命中=%d 条，过滤后 %d 条%s",
        variety, meta.date_from, meta.date_to, meta.total_found, len(digests),
        "（黑色系兜底）" if meta.variety_fallback else "",
    )

    # 3a. 目标品种无数据且未兜底 → 退出
    if (
        not digests
        and meta.variety_queried
        and not meta.variety_fallback
        and meta.abstain_reason not in {"missing_publish_time", "no_eligible_articles"}
    ):
        logger.warning(
            "品种 %s 在窗口内无资讯（兜底已关闭），跳过校准", variety
        )
        return EXIT_NO_ARTICLES_NO_FALLBACK

    # 4. 无资讯 → 跳过校准，透传原值
    if not digests:
        logger.warning("时间窗口内无相关资讯，跳过校准（透传模型原值）")
        skip_reason = meta.abstain_reason or "no_articles"
        result = _passthrough_result(
            snapshot,
            view="range",
            confidence=0.0,
            commentary=f"无可用资讯（{skip_reason}），跳过校准",
            llm_meta={"skipped": skip_reason},
        )
        output = build_output(
            snapshot, result, config,
            skipped_reason=skip_reason,
        )
        write_calibration(output, output_path)
        logger.info("已写出 %s（未校准）", output_path)
        return EXIT_OK

    # 5. 构造 prompt 并调用 LLM
    messages = build_messages(snapshot, digests, config)
    try:
        resp = call_llm(
            messages,
            base_url=config.base_url,
            model=config.model,
            temperature=config.temperature,
            max_retries=config.max_retries,
            timeout=config.timeout_seconds,
        )
    except LLMConfigError as e:
        logger.error("LLM 配置错误：%s", e)
        result = _passthrough_result(
            snapshot,
            view="range", confidence=0.0,
            commentary="LLM 调用失败，透传模型原值",
            llm_meta={"error": str(e)},
            variety_fallback=meta.variety_fallback,
            variety_queried=meta.variety_queried,
            used_articles=digests,
        )
        output = build_output(
            snapshot, result, config,
            llm_model=config.model,
            llm_error=str(e),
        )
        write_calibration(output, output_path)
        return EXIT_LLM_FAILED
    except Exception as e:  # noqa: BLE001 LLM 网络/超时最终失败
        logger.error("LLM 调用最终失败：%s", e)
        result = _passthrough_result(
            snapshot,
            view="range", confidence=0.0,
            commentary="LLM 调用失败，透传模型原值",
            llm_meta={"error": str(e)},
            variety_fallback=meta.variety_fallback,
            variety_queried=meta.variety_queried,
            used_articles=digests,
        )
        output = build_output(
            snapshot, result, config,
            llm_model=config.model,
            llm_error=str(e),
        )
        write_calibration(output, output_path)
        return EXIT_LLM_FAILED

    llm_meta = {
        "model": resp.model,
        "tokens": {"prompt": resp.prompt_tokens, "completion": resp.completion_tokens},
        "attempts": resp.attempt,
        "raw_parsed": resp.parsed is not None,
    }

    # 6. 解析失败 → 重试一次（附提醒），仍失败透传
    if resp.parsed is None:
        logger.warning("LLM 返回非法 JSON，重试一次")
        try:
            messages2 = build_messages(snapshot, digests, config)
            messages2[1]["content"] += (
                "\n\n注意：你上一次输出无法解析为 JSON，请只返回合法 JSON，不要包含任何其他文字。"
            )
            resp = call_llm(
                messages2,
                base_url=config.base_url,
                model=config.model,
                temperature=config.temperature,
                max_retries=config.max_retries,
                timeout=config.timeout_seconds,
            )
            llm_meta["attempts"] = resp.attempt
            llm_meta["raw_parsed"] = resp.parsed is not None
        except Exception as e:  # noqa: BLE001
            logger.error("LLM 重试失败：%s", e)
            result = _passthrough_result(
                snapshot,
                view="range", confidence=0.0,
                commentary="LLM 输出无法解析，透传模型原值",
                llm_meta={"error": str(e)},
                variety_fallback=meta.variety_fallback,
                variety_queried=meta.variety_queried,
                used_articles=digests,
            )
            output = build_output(
                snapshot, result, config,
                llm_model=config.model,
                llm_error=str(e),
            )
            write_calibration(output, output_path)
            return EXIT_LLM_FAILED

    if resp.parsed is None:
        logger.error("LLM 两次输出均非法 JSON，透传原值")
        result = _passthrough_result(
            snapshot,
            view="range", confidence=0.0,
            commentary="LLM 输出无法解析，透传模型原值",
            llm_meta={"parse_error": "invalid_json_twice"},
            variety_fallback=meta.variety_fallback,
            variety_queried=meta.variety_queried,
            used_articles=digests,
        )
        output = build_output(
            snapshot, result, config,
            llm_model=config.model,
            skipped_reason="invalid_json_twice",
        )
        write_calibration(output, output_path)
        return EXIT_PARSE_FAILED

    # 7. 结构校验 + 温和校准
    try:
        result = apply_calibration(
            snapshot,
            resp.parsed,
            config,
            variety_fallback=meta.variety_fallback,
            variety_queried=meta.variety_queried,
            used_articles=digests,
            llm_meta=llm_meta,
        )
    except CalibrationParseError as e:
        logger.error("LLM 输出结构非法：%s，透传原值", e)
        result = _passthrough_result(
            snapshot,
            view="range", confidence=0.0,
            commentary="LLM 输出结构非法，透传模型原值",
            llm_meta={"parse_error": str(e)},
            variety_fallback=meta.variety_fallback,
            variety_queried=meta.variety_queried,
            used_articles=digests,
        )
        output = build_output(
            snapshot, result, config,
            llm_model=config.model,
            llm_tokens=llm_meta.get("tokens"),
            llm_attempts=llm_meta.get("attempts"),
            skipped_reason="invalid_schema",
        )
        write_calibration(output, output_path)
        return EXIT_PARSE_FAILED

    # 8. 写出
    output = build_output(
        snapshot, result, config,
        llm_model=config.model,
        llm_tokens=llm_meta.get("tokens"),
        llm_attempts=llm_meta.get("attempts"),
    )
    write_calibration(output, output_path)
    _render_calibrated_plot(input_path, output_path)
    logger.info("已写出校准结果 %s", output_path)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="calibration",
        description="资讯校准 K线预测：读 forecast_result.json，筛相关资讯喂 LLM，输出 calibration.json",
    )
    p.add_argument("--input", required=True, type=Path, help="Kronos 生成的 forecast_result.json 路径")
    p.add_argument("--output", type=Path, default=None, help="calibration.json 输出路径（默认与 input 同目录）")
    p.add_argument("--lookback-days", type=int, default=None, help="资讯回溯天数（默认取配置）")
    p.add_argument("--max-articles", type=int, default=None, help="喂给 LLM 的资讯条数上限")
    p.add_argument("--model", default=None, help="覆盖 LLM 模型")
    p.add_argument("--base-url", default=None, help="覆盖 OpenAI 兼容端点")
    p.add_argument("--temperature", type=float, default=None, help="覆盖 LLM 温度")
    p.add_argument("--max-retries", type=int, default=None, help="覆盖 LLM 重试次数")
    p.add_argument("--no-variety-fallback", action="store_true", help="关闭目标品种无数据时的黑色系兜底")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="可选的 legacy YAML 基础配置；默认从 .env 读取",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    config = CalibrationConfig.from_env(args.config)
    if args.lookback_days is not None:
        config = config.replace(lookback_days=args.lookback_days)
    if args.max_articles is not None:
        config = config.replace(max_articles=args.max_articles)
    if args.model is not None:
        config = config.replace(model=args.model)
    if args.base_url is not None:
        config = config.replace(base_url=args.base_url)
    if args.temperature is not None:
        config = config.replace(temperature=args.temperature)
    if args.max_retries is not None:
        config = config.replace(max_retries=args.max_retries)
    if args.no_variety_fallback:
        config = config.replace(fallback_to_black_sector=False)

    output_path = args.output
    if output_path is None:
        output_path = args.input.parent / "calibration.json"

    try:
        return _run_calibration(args.input, output_path, config)
    except ForecastLoadError as e:
        logger.error("读取预测失败：%s", e)
        return EXIT_FORECAST_LOAD_FAILED
    except Exception as e:  # noqa: BLE001 兜底
        logger.error("未预期的错误：%s", e, exc_info=args.verbose)
        return EXIT_FORECAST_LOAD_FAILED


if __name__ == "__main__":
    sys.exit(main())
