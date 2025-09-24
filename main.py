import os
import asyncio
import logging
from decimal import Decimal, ROUND_DOWN
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import gate_api
from gate_api.exceptions import ApiException, GateApiException
import math
from typing import Dict, List, Tuple

# 加載環境變數
load_dotenv()

# 配置日誌
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Gate.io API 配置
configuration = gate_api.Configuration(
    key=os.getenv("GATE_API_KEY"),
    secret=os.getenv("GATE_API_SECRET")
)

# 會話狀態
SELECTING_SYMBOL, SELECTING_LEVERAGE, SELECTING_MARGIN, SELECTING_ENTRY_PRICE, SELECTING_ROLL_COUNT, SELECTING_ORDER_TYPE, CONFIRMATION = range(7)

# 用戶數據存儲
user_data = {}

class RolloverBot:
    def __init__(self):
        self.api_client = gate_api.ApiClient(configuration)
        self.futures_api = gate_api.FuturesApi(self.api_client)
        
    def calculate_contract_size(self, symbol: str, price: float, margin: float, leverage: int) -> int:
        """計算合約數量（張數）"""
        try:
            # 獲取合約信息
            contracts = self.futures_api.list_futures_contracts(symbol)
            contract = contracts[0]
            
            # 計算合約價值
            contract_value = margin * leverage
            # 計算合約數量（張數）
            contract_size = int(contract_value / price)
            
            return max(1, contract_size)  # 至少1張合約
        except Exception as e:
            logger.error(f"計算合約數量錯誤: {e}")
            return 0
    
    async def get_current_price(self, symbol: str) -> float:
        """獲取當前價格"""
        try:
            tickers = self.futures_api.list_futures_tickers(symbol)
            return float(tickers[0].last)
        except Exception as e:
            logger.error(f"獲取價格錯誤: {e}")
            return 0.0
    
    async def place_market_order(self, symbol: str, contract_size: int, leverage: int) -> bool:
        """下市價單"""
        try:
            # 設置槓桿
            leverage_str = f"{leverage}"
            self.futures_api.update_position_leverage(symbol, leverage_str)
            
            # 下單
            order = gate_api.FuturesOrder(
                contract=symbol,
                size=contract_size,
                price="0",  # 市價單
                tif="ioc"
            )
            result = self.futures_api.create_futures_order(order)
            return True
        except Exception as e:
            logger.error(f"下單錯誤: {e}")
            return False
    
    async def place_limit_order(self, symbol: str, contract_size: int, price: float, leverage: int) -> bool:
        """下限價單"""
        try:
            # 設置槓桿
            leverage_str = f"{leverage}"
            self.futures_api.update_position_leverage(symbol, leverage_str)
            
            # 下單
            order = gate_api.FuturesOrder(
                contract=symbol,
                size=contract_size,
                price=str(price),
                tif="gtc"
            )
            result = self.futures_api.create_futures_order(order)
            return True
        except Exception as e:
            logger.error(f"下單錯誤: {e}")
            return False
    
    def calculate_rollover_orders(self, entry_price: float, margin: float, 
                                leverage: int, roll_count: int, symbol: str) -> List[Dict]:
        """計算滾倉訂單"""
        orders = []
        current_price = entry_price
        
        # 計算初始合約數量
        initial_contract_size = self.calculate_contract_size(symbol, entry_price, margin, leverage)
        
        for i in range(roll_count):
            # 計算下一次滾倉價格（上漲2%）
            rollover_price = current_price * Decimal('1.02')
            rollover_price = float(rollover_price.quantize(Decimal('0.01'), rounding=ROUND_DOWN))
            
            # 使用相同的合約數量
            contract_size = initial_contract_size
            
            orders.append({
                'rollover_number': i + 1,
                'price': rollover_price,
                'contract_size': contract_size,
                'margin_required': contract_size * rollover_price / leverage
            })
            
            current_price = rollover_price
        
        return orders

# 初始化機器人
bot = RolloverBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """開始對話"""
    user_id = update.message.from_user.id
    user_data[user_id] = {}
    
    await update.message.reply_text(
        "🤖 歡迎使用自動滾倉機器人！\n\n"
        "請輸入交易對（例如: BTC_USDT）:"
    )
    return SELECTING_SYMBOL

async def symbol_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收交易對"""
    user_id = update.message.from_user.id
    symbol = update.message.text.upper().replace('/', '_')
    
    user_data[user_id]['symbol'] = symbol
    
    await update.message.reply_text(
        f"交易對: {symbol}\n"
        "請輸入槓桿倍數（例如: 10）:"
    )
    return SELECTING_LEVERAGE

async def leverage_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收槓桿"""
    user_id = update.message.from_user.id
    try:
        leverage = int(update.message.text)
        if leverage <= 0 or leverage > 100:
            await update.message.reply_text("槓桿必須在1-100之間，請重新輸入:")
            return SELECTING_LEVERAGE
        
        user_data[user_id]['leverage'] = leverage
        
        await update.message.reply_text(
            f"槓桿: {leverage}x\n"
            "請輸入保證金（USDT）:"
        )
        return SELECTING_MARGIN
    except ValueError:
        await update.message.reply_text("請輸入有效的數字:")
        return SELECTING_LEVERAGE

async def margin_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收保證金"""
    user_id = update.message.from_user.id
    try:
        margin = float(update.message.text)
        if margin <= 0:
            await update.message.reply_text("保證金必須大於0，請重新輸入:")
            return SELECTING_MARGIN
        
        user_data[user_id]['margin'] = margin
        
        await update.message.reply_text(
            f"保證金: {margin} USDT\n"
            "請輸入初始進場價格:"
        )
        return SELECTING_ENTRY_PRICE
    except ValueError:
        await update.message.reply_text("請輸入有效的數字:")
        return SELECTING_MARGIN

async def entry_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收進場價格"""
    user_id = update.message.from_user.id
    try:
        entry_price = float(update.message.text)
        if entry_price <= 0:
            await update.message.reply_text("價格必須大於0，請重新輸入:")
            return SELECTING_ENTRY_PRICE
        
        user_data[user_id]['entry_price'] = entry_price
        
        await update.message.reply_text(
            f"進場價格: {entry_price}\n"
            "請輸入滾倉次數:"
        )
        return SELECTING_ROLL_COUNT
    except ValueError:
        await update.message.reply_text("請輸入有效的數字:")
        return SELECTING_ENTRY_PRICE

async def roll_count_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收滾倉次數"""
    user_id = update.message.from_user.id
    try:
        roll_count = int(update.message.text)
        if roll_count <= 0 or roll_count > 20:
            await update.message.reply_text("滾倉次數必須在1-20之間，請重新輸入:")
            return SELECTING_ROLL_COUNT
        
        user_data[user_id]['roll_count'] = roll_count
        
        keyboard = [['市價單', '限價單']]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        
        await update.message.reply_text(
            f"滾倉次數: {roll_count}\n"
            "請選擇下單方式:",
            reply_markup=reply_markup
        )
        return SELECTING_ORDER_TYPE
    except ValueError:
        await update.message.reply_text("請輸入有效的整數:")
        return SELECTING_ROLL_COUNT

async def order_type_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收下單方式"""
    user_id = update.message.from_user.id
    order_type = update.message.text
    
    user_data[user_id]['order_type'] = order_type
    
    # 計算滾倉訂單
    symbol = user_data[user_id]['symbol']
    entry_price = user_data[user_id]['entry_price']
    margin = user_data[user_id]['margin']
    leverage = user_data[user_id]['leverage']
    roll_count = user_data[user_id]['roll_count']
    
    orders = bot.calculate_rollover_orders(entry_price, margin, leverage, roll_count, symbol)
    user_data[user_id]['orders'] = orders
    
    # 顯示訂單摘要
    summary = f"📊 訂單摘要:\n\n"
    summary += f"交易對: {symbol}\n"
    summary += f"槓桿: {leverage}x\n"
    summary += f"保證金: {margin} USDT\n"
    summary += f"進場價格: {entry_price}\n"
    summary += f"下單方式: {order_type}\n"
    summary += f"滾倉次數: {roll_count}\n\n"
    summary += "📈 滾倉訂單:\n"
    
    for order in orders:
        summary += f"第{order['rollover_number']}次: 價格${order['price']:.2f}, 合約{order['contract_size']}張\n"
    
    summary += "\n確認執行？(是/否)"
    
    await update.message.reply_text(summary)
    return CONFIRMATION

async def confirmation_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收確認"""
    user_id = update.message.from_user.id
    response = update.message.text.lower()
    
    if response == '是':
        await execute_rollover_strategy(update, user_id)
        return ConversationHandler.END
    else:
        await update.message.reply_text("已取消操作。輸入 /start 重新開始。")
        return ConversationHandler.END

async def execute_rollover_strategy(update: Update, user_id: int):
    """執行滾倉策略"""
    try:
        data = user_data[user_id]
        symbol = data['symbol']
        leverage = data['leverage']
        margin = data['margin']
        entry_price = data['entry_price']
        order_type = data['order_type']
        orders = data['orders']
        
        await update.message.reply_text("🚀 開始執行滾倉策略...")
        
        # 下初始訂單
        initial_contract_size = bot.calculate_contract_size(symbol, entry_price, margin, leverage)
        
        if order_type == '市價單':
            success = await bot.place_market_order(symbol, initial_contract_size, leverage)
            order_type_str = "市價單"
        else:
            success = await bot.place_limit_order(symbol, initial_contract_size, entry_price, leverage)
            order_type_str = "限價單"
        
        if success:
            await update.message.reply_text(
                f"✅ 初始訂單下單成功！\n"
                f"方式: {order_type_str}\n"
                f"價格: {entry_price}\n"
                f"合約數量: {initial_contract_size}張"
            )
        else:
            await update.message.reply_text("❌ 初始訂單下單失敗！")
            return
        
        # 下滾倉訂單
        await update.message.reply_text("📝 設置滾倉訂單...")
        
        successful_orders = 0
        for order in orders:
            success = await bot.place_limit_order(
                symbol, 
                order['contract_size'], 
                order['price'], 
                leverage
            )
            
            if success:
                successful_orders += 1
                await update.message.reply_text(
                    f"✅ 滾倉訂單 #{order['rollover_number']} 設置成功\n"
                    f"價格: ${order['price']:.2f}\n"
                    f"合約: {order['contract_size']}張"
                )
            else:
                await update.message.reply_text(f"❌ 滾倉訂單 #{order['rollover_number']} 設置失敗")
        
        await update.message.reply_text(
            f"🎯 策略執行完成！\n"
            f"成功設置 {successful_orders}/{len(orders)} 個滾倉訂單"
        )
        
    except Exception as e:
        logger.error(f"執行策略錯誤: {e}")
        await update.message.reply_text(f"❌ 執行過程中發生錯誤: {str(e)}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """取消操作"""
    await update.message.reply_text("操作已取消。輸入 /start 重新開始。")
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """錯誤處理"""
    logger.error(f"更新 {update} 導致錯誤 {context.error}")
    await update.message.reply_text("發生錯誤，請稍後再試。")

def main():
    """主函數"""
    # 創建應用程序
    application = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    
    # 創建會話處理器
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, symbol_received)],
            SELECTING_LEVERAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, leverage_received)],
            SELECTING_MARGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, margin_received)],
            SELECTING_ENTRY_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, entry_price_received)],
            SELECTING_ROLL_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, roll_count_received)],
            SELECTING_ORDER_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_type_received)],
            CONFIRMATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmation_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)
    
    # 啟動機器人
    print("🤖 自動滾倉機器人已啟動...")
    application.run_polling()

if __name__ == '__main__':
    main()
