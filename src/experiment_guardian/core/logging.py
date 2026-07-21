"""结构化日志配置。

日志只记录事件和标识符。调用方不得把 Token、数据库 URL、完整配置文件或原始环境变量
放入日志字段；敏感数据清洗会在真实 API/MCP 鉴权中间件接入时继续强化。
"""

import logging

import structlog


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
