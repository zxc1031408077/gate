import os
from dotenv import load_dotenv

load_dotenv()

# Gate.io API 配置
GATEIO_API_KEY = os.getenv('GATEIO_API_KEY')
GATEIO_API_SECRET = os.getenv('GATEIO_API_SECRET')

# Telegram Bot 配置
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# 交易配置
DEFAULT_LEVERAGE = 10  # 預設槓桿倍數
SETTLE_CURRENCY = 'usdt'  # 結算貨幣

# 風險控制
MAX_POSITION_SIZE = 1000  # 最大持倉金額 (USDT)
MAX_ROLLOVER_TIMES = 10   # 最大滾倉次數

class Config:
    def __init__(self):
        self.validate_config()
    
    def validate_config(self):
        """驗證配置是否完整"""
        required_vars = {
            'GATEIO_API_KEY': GATEIO_API_KEY,
            'GATEIO_API_SECRET': GATEIO_API_SECRET,
            'TELEGRAM_BOT_TOKEN': TELEGRAM_BOT_TOKEN,
            'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID
        }
        
        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            raise ValueError(f"缺少必要的環境變數: {', '.join(missing_vars)}")
