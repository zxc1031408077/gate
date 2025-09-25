#!/usr/bin/env python3
import os
import signal
import sys
import logging
from telegram_bot import TradingBot
from config import Config

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)

logger = logging.getLogger(__name__)

def signal_handler(signum, frame):
    """處理信號，優雅退出"""
    logger.info("收到關閉信號，正在退出...")
    sys.exit(0)

def main():
    """主函數"""
    try:
        # 設定信號處理
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # 驗證配置
        logger.info("開始驗證配置...")
        config = Config()
        logger.info("✅ 配置驗證成功")
        
        # 啟動 Telegram 機器人
        logger.info("🤖 啟動 Gate.io 自動滾倉交易機器人...")
        bot = TradingBot()
        bot.run()
        
    except ValueError as e:
        logger.error(f"❌ 配置錯誤: {str(e)}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ 啟動失敗: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
