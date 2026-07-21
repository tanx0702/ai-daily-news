"""
日志配置模块

统一配置项目日志系统:
- 按天自动切割日志文件
- 错误单独记录到 error.log
- 生产环境关闭控制台输出
- 日志保留 30 天(普通日志) / 90 天(错误日志)
"""

import logging
import logging.handlers
import os
import sys


def setup_logging():
    """
    配置全局日志系统。

    环境变量:
        LOG_DIR: 日志目录,默认 logs/
        ENV: 环境标识,production 时关闭控制台输出
    """
    log_dir = os.getenv("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)

    # 日志格式
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 根 logger 配置
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 避免重复添加 handler(多次调用 setup_logging 时)
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # 1. 主日志文件(按天切割,保留 30 天)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "ai-news.log"),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)

    # 2. 错误日志单独记录(按天切割,保留 90 天)
    error_handler = logging.handlers.TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "error.log"),
        when="midnight",
        interval=1,
        backupCount=90,
        encoding="utf-8",
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    root_logger.addHandler(error_handler)

    # 3. 控制台输出(非生产环境)
    env = os.getenv("ENV", "").lower()
    if env != "production":
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        root_logger.addHandler(console_handler)

    # 首条日志标记
    root_logger.info("=" * 60)
    root_logger.info("Logging system initialized: log_dir=%s, env=%s", log_dir, env or "development")
