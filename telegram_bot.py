from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import logging
from gateio_client import GateIOClient, TradingStrategy
from config import Config, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 設定日誌
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class TradingBot:
    def __init__(self):
        self.config = Config()
        self.gateio_client = GateIOClient()
        self.strategy = TradingStrategy(self.gateio_client)
        self.user_sessions = {}
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """開始命令"""
        user_id = update.effective_user.id
        
        # 檢查是否為授權用戶
        if str(user_id) != TELEGRAM_CHAT_ID:
            await update.message.reply_text("❌ 未授權使用此機器人")
            return
        
        welcome_text = """
🤖 Gate.io 自動滾倉交易機器人

支援功能：
✅ 永續合約多單交易
✅ 全倉模式
✅ 市價單/掛單進場
✅ 自動滾倉條件單

可用命令：
/start - 啟動機器人
/balance - 查看餘額
/new_trade - 新建交易
/cancel_orders - 取消所有訂單
/status - 查看交易狀態
        """
        
        keyboard = [['/new_trade', '/balance'], ['/status', '/cancel_orders']]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def check_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """檢查餘額"""
        try:
            balance = self.gateio_client.get_account_balance()
            await update.message.reply_text(f"💰 帳戶餘額: {balance:.2f} USDT")
        except Exception as e:
            await update.message.reply_text(f"❌ 獲取餘額失敗: {str(e)}")
    
    async def new_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """新建交易對話"""
        user_id = update.effective_user.id
        
        if str(user_id) != TELEGRAM_CHAT_ID:
            await update.message.reply_text("❌ 未授權使用此機器人")
            return
        
        self.user_sessions[user_id] = {'step': 'symbol'}
        
        await update.message.reply_text(
            "📊 請輸入交易對 (例如: BTCUSDT):"
        )
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理用戶消息"""
        user_id = update.effective_user.id
        message_text = update.message.text
        
        if str(user_id) != TELEGRAM_CHAT_ID:
            return
        
        if user_id not in self.user_sessions:
            return
        
        session = self.user_sessions[user_id]
        
        try:
            if session['step'] == 'symbol':
                session['symbol'] = message_text.upper()
                session['step'] = 'entry_type'
                await update.message.reply_text(
                    "請選擇進場方式:\n"
                    "1. market - 市價單\n"
                    "2. limit - 掛單\n"
                    "請輸入 1 或 2:"
                )
            
            elif session['step'] == 'entry_type':
                if message_text == '1':
                    session['entry_type'] = 'market'
                    session['step'] = 'leverage'
                elif message_text == '2':
                    session['entry_type'] = 'limit'
                    session['step'] = 'entry_price'
                else:
                    await update.message.reply_text("請輸入 1 或 2:")
                    return
                
                if session['entry_type'] == 'market':
                    await update.message.reply_text("請輸入槓桿倍數 (例如: 10):")
                else:
                    await update.message.reply_text("請輸入掛單價格 (例如: 50000):")
            
            elif session['step'] == 'entry_price':
                try:
                    session['entry_price'] = float(message_text)
                    session['step'] = 'leverage'
                    await update.message.reply_text("請輸入槓桿倍數 (例如: 10):")
                except ValueError:
                    await update.message.reply_text("請輸入有效的價格數字:")
            
            elif session['step'] == 'leverage':
                try:
                    session['leverage'] = int(message_text)
                    session['step'] = 'margin'
                    await update.message.reply_text("請輸入保證金金額 (USDT, 例如: 100):")
                except ValueError:
                    await update.message.reply_text("請輸入有效的整數:")
            
            elif session['step'] == 'margin':
                try:
                    session['margin'] = float(message_text)
                    session['step'] = 'rollover_times'
                    await update.message.reply_text("請輸入滾倉次數 (例如: 5):")
                except ValueError:
                    await update.message.reply_text("請輸入有效的金額數字:")
            
            elif session['step'] == 'rollover_times':
                try:
                    session['rollover_times'] = int(message_text)
                    session['step'] = 'percentage_increase'
                    await update.message.reply_text("請輸入每次滾倉漲幅百分比 (例如: 2):")
                except ValueError:
                    await update.message.reply_text("請輸入有效的整數:")
            
            elif session['step'] == 'percentage_increase':
                try:
                    session['percentage_increase'] = float(message_text)
                    
                    # 執行交易策略
                    await update.message.reply_text("⏳ 正在執行交易策略...")
                    
                    success, result = self.strategy.execute_strategy(
                        symbol=session['symbol'],
                        entry_type=session['entry_type'],
                        leverage=session['leverage'],
                        margin=session['margin'],
                        rollover_times=session['rollover_times'],
                        percentage_increase=session['percentage_increase'],
                        entry_price=session.get('entry_price')
                    )
                    
                    if success:
                        # 格式化成功消息
                        message = f"""
✅ 交易策略執行成功！

📈 交易對: {session['symbol']}
💰 保證金: {session['margin']} USDT
⚡ 槓桿: {session['leverage']}x
🎯 進場方式: {session['entry_type']}
🔄 滾倉次數: {session['rollover_times']}次
📊 每次漲幅: {session['percentage_increase']}%

📋 訂單詳情:
- 進場訂單 ID: {result['entry_order']}
- 倉位大小: {result['position_size']}張

🔔 滾倉條件單已建立:
"""
                        for i, order in enumerate(result['rollover_orders']):
                            message += f"{i+1}. 觸發價: {order['trigger_price']} | 張數: {order['size']}\n"
                        
                        await update.message.reply_text(message)
                    else:
                        await update.message.reply_text(f"❌ 交易失敗: {result}")
                    
                    # 清除會話
                    del self.user_sessions[user_id]
                    
                except ValueError:
                    await update.message.reply_text("請輸入有效的百分比數字:")
        
        except Exception as e:
            await update.message.reply_text(f"❌ 發生錯誤: {str(e)}")
            if user_id in self.user_sessions:
                del self.user_sessions[user_id]
    
    async def cancel_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """取消所有訂單"""
        try:
            # 這裡需要用戶指定交易對，簡化處理
            await update.message.reply_text("請輸入要取消訂單的交易對 (例如: BTCUSDT):")
            context.user_data['waiting_for_symbol'] = True
        except Exception as e:
            await update.message.reply_text(f"❌ 取消訂單失敗: {str(e)}")
    
    async def get_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """獲取交易狀態"""
        try:
            # 這裡可以實現獲取當前持倉和訂單狀態
            await update.message.reply_text("📊 狀態功能開發中...")
        except Exception as e:
            await update.message.reply_text(f"❌ 獲取狀態失敗: {str(e)}")
    
    def run(self):
        """啟動機器人"""
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # 添加處理器
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("balance", self.check_balance))
        application.add_handler(CommandHandler("new_trade", self.new_trade))
        application.add_handler(CommandHandler("cancel_orders", self.cancel_orders))
        application.add_handler(CommandHandler("status", self.get_status))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        # 啟動機器人
        application.run_polling()
