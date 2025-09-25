#!/usr/bin/env python3
import os
import signal
import sys
from telegram_bot import TradingBot
from config import Config

def signal_handler(signum, frame):
    """處理信號，優雅退出"""
    print("收到關閉信號，正在退出...")
    sys.exit(0)

def main():
    """主函數"""
    try:
        # 設定信號處理
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # 驗證配置
        config = Config()
        print("✅ 配置驗證成功")
        
        # 啟動 Telegram 機器人
        print("🤖 啟動 Gate.io 自動滾倉交易機器人...")
        bot = TradingBot()
        bot.run()
        
    except Exception as e:
        print(f"❌ 啟動失敗: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
