import os
import asyncio
import logging
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional
import hashlib
import hmac
import time
import json
from datetime import datetime

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class GateIOAPI:
    """Gate.io API 接口类"""
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.gateio.ws"
        self.session = None
        
    async def init_session(self):
        """初始化 HTTP 会话"""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def close_session(self):
        """关闭 HTTP 会话"""
        if self.session:
            await self.session.close()
    
    def _generate_signature(self, method: str, uri: str, query: str, body: str, timestamp: str) -> str:
        """生成 API 签名"""
        message = f"{method}\n{uri}\n{query}\n{hashlib.sha512(body.encode()).hexdigest()}\n{timestamp}"
        signature = hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha512
        ).hexdigest()
        return signature
    
    async def _request(self, method: str, endpoint: str, params: Dict = None, data: Dict = None) -> Dict:
        """发送 API 请求"""
        await self.init_session()
        
        uri = f"/api/v4{endpoint}"
        url = f"{self.base_url}{uri}"
        
        query = ""
        if params:
            query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        
        body = ""
        if data:
            body = json.dumps(data, separators=(',', ':'))
        
        timestamp = str(int(time.time()))
        signature = self._generate_signature(method, uri, query, body, timestamp)
        
        headers = {
            "KEY": self.api_key,
            "Timestamp": timestamp,
            "SIGN": signature,
            "Content-Type": "application/json"
        }
        
        try:
            if method.upper() == "GET":
                async with self.session.get(url, params=params, headers=headers) as response:
                    result = await response.json()
            else:
                async with self.session.request(method, url, params=params, json=data, headers=headers) as response:
                    result = await response.json()
            
            if response.status != 200:
                logger.error(f"API 请求失败: {response.status}, {result}")
                raise Exception(f"API 请求失败: {result.get('message', 'Unknown error')}")
            
            return result
            
        except Exception as e:
            logger.error(f"API 请求异常: {str(e)}")
            raise
    
    async def get_futures_account(self) -> Dict:
        """获取期货账户信息"""
        return await self._request("GET", "/futures/usdt/accounts")
    
    async def get_ticker(self, contract: str) -> Dict:
        """获取合约行情"""
        params = {"contract": contract}
        tickers = await self._request("GET", "/futures/usdt/tickers", params=params)
        return tickers[0] if tickers else {}
    
    async def place_order(self, contract: str, size: int, price: str = None, 
                         order_type: str = "limit", time_in_force: str = "gtc") -> Dict:
        """下单"""
        data = {
            "contract": contract,
            "size": size,
            "time_in_force": time_in_force,
            "auto_size": "close_long"  # 全仓模式
        }
        
        if price and order_type == "limit":
            data["price"] = price
        
        if order_type == "market":
            data["time_in_force"] = "ioc"
            
        return await self._request("POST", "/futures/usdt/orders", data=data)
    
    async def get_positions(self, contract: str = None) -> List[Dict]:
        """获取持仓信息"""
        params = {}
        if contract:
            params["contract"] = contract
        return await self._request("GET", "/futures/usdt/positions", params=params)

class RollingStrategy:
    """滚仓策略类"""
    
    def __init__(self, api: GateIOAPI):
        self.api = api
        self.active_strategies: Dict[int, Dict] = {}  # user_id -> strategy_data
    
    def calculate_position_size(self, cost_usdt: float, leverage: int, price: float) -> int:
        """计算张数（做多为正数）"""
        # Gate.io 期货合约，1张 = 1 USD 名义价值
        # 张数 = 成本 * 杠杆 / 价格
        size = int(cost_usdt * leverage / price)
        return size
    
    def calculate_rolling_prices(self, entry_price: float, rolling_count: int, interval_percent: float = 2.0) -> List[float]:
        """计算滚仓价格"""
        prices = []
        current_price = entry_price
        
        for i in range(rolling_count):
            current_price = current_price * (1 + interval_percent / 100)
            prices.append(round(current_price, 6))
        
        return prices
    
    async def start_rolling_strategy(self, user_id: int, params: Dict) -> Dict:
        """开始滚仓策略"""
        try:
            contract = params["symbol"]
            leverage = params["leverage"]
            cost_usdt = params["cost_usdt"]
            rolling_count = params["rolling_count"]
            order_type = params["order_type"]  # "market" or "limit"
            entry_price = params.get("entry_price")  # 限价单需要
            
            # 获取当前价格
            ticker = await self.api.get_ticker(contract)
            current_price = float(ticker["last"])
            
            # 初始进场
            if order_type == "market":
                # 市价单进场
                size = self.calculate_position_size(cost_usdt, leverage, current_price)
                order_result = await self.api.place_order(contract, size, order_type="market")
                actual_entry_price = current_price  # 市价单使用当前价格估算
                logger.info(f"市价单进场成功: {order_result}")
            else:
                # 限价单进场
                if not entry_price:
                    raise ValueError("限价单必须指定进场价格")
                size = self.calculate_position_size(cost_usdt, leverage, entry_price)
                order_result = await self.api.place_order(contract, size, str(entry_price))
                actual_entry_price = entry_price
                logger.info(f"限价单进场成功: {order_result}")
            
            # 计算滚仓价格和张数
            rolling_prices = self.calculate_rolling_prices(actual_entry_price, rolling_count)
            
            # 计算第一次补仓张数（盈利2%时的张数）
            first_rolling_price = rolling_prices[0]
            rolling_size = self.calculate_position_size(cost_usdt, leverage, first_rolling_price)
            
            # 预挂滚仓订单
            rolling_orders = []
            for i, price in enumerate(rolling_prices):
                try:
                    order = await self.api.place_order(contract, rolling_size, str(price))
                    rolling_orders.append({
                        "order_id": order.get("id"),
                        "price": price,
                        "size": rolling_size,
                        "status": "pending"
                    })
                    logger.info(f"滚仓订单 {i+1} 挂单成功，价格: {price}, 张数: {rolling_size}")
                    await asyncio.sleep(0.1)  # 避免频率限制
                except Exception as e:
                    logger.error(f"滚仓订单 {i+1} 挂单失败: {str(e)}")
                    rolling_orders.append({
                        "order_id": None,
                        "price": price,
                        "size": rolling_size,
                        "status": "failed",
                        "error": str(e)
                    })
            
            # 保存策略数据
            strategy_data = {
                "user_id": user_id,
                "contract": contract,
                "leverage": leverage,
                "cost_usdt": cost_usdt,
                "entry_price": actual_entry_price,
                "rolling_count": rolling_count,
                "rolling_size": rolling_size,
                "rolling_orders": rolling_orders,
                "initial_order": order_result,
                "created_at": datetime.now().isoformat(),
                "status": "active"
            }
            
            self.active_strategies[user_id] = strategy_data
            
            return {
                "success": True,
                "message": "滚仓策略启动成功",
                "data": strategy_data
            }
            
        except Exception as e:
            logger.error(f"启动滚仓策略失败: {str(e)}")
            return {
                "success": False,
                "message": f"启动失败: {str(e)}"
            }
    
    async def get_strategy_status(self, user_id: int) -> Dict:
        """获取策略状态"""
        if user_id not in self.active_strategies:
            return {"success": False, "message": "没有活跃的策略"}
        
        strategy = self.active_strategies[user_id]
        
        try:
            # 获取持仓信息
            positions = await self.api.get_positions(strategy["contract"])
            current_position = next((p for p in positions if p["contract"] == strategy["contract"]), None)
            
            # 获取当前价格
            ticker = await self.api.get_ticker(strategy["contract"])
            current_price = float(ticker["last"])
            
            return {
                "success": True,
                "strategy": strategy,
                "current_position": current_position,
                "current_price": current_price
            }
        except Exception as e:
            logger.error(f"获取策略状态失败: {str(e)}")
            return {"success": False, "message": f"获取状态失败: {str(e)}"}

class TelegramBot:
    """Telegram 机器人类"""
    
    def __init__(self, token: str, gate_api: GateIOAPI):
        self.token = token
        self.gate_api = gate_api
        self.strategy = RollingStrategy(gate_api)
        self.user_states: Dict[int, Dict] = {}  # 用户状态管理
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """开始命令"""
        user_id = update.effective_user.id
        
        welcome_text = """
🤖 Gate.io 自动滚仓机器人

功能说明：
• 永续合约全仓模式
• 只做多方向
• 支持市价/限价进场
• 自动滚仓（固定2%间隔）

使用 /roll 开始设置滚仓策略
使用 /status 查看当前策略状态
使用 /help 获取帮助信息
        """
        
        await update.message.reply_text(welcome_text)
    
    async def roll_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """滚仓设置命令"""
        user_id = update.effective_user.id
        
        # 检查是否已有活跃策略
        if user_id in self.strategy.active_strategies:
            await update.message.reply_text("❌ 你已有活跃的滚仓策略，请先停止当前策略")
            return
        
        # 初始化用户状态
        self.user_states[user_id] = {"step": "symbol"}
        
        await update.message.reply_text("📝 请输入合约代码（例如：BTC_USDT）：")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理用户消息"""
        user_id = update.effective_user.id
        text = update.message.text
        
        if user_id not in self.user_states:
            await update.message.reply_text("请先使用 /roll 命令开始设置")
            return
        
        state = self.user_states[user_id]
        step = state.get("step")
        
        try:
            if step == "symbol":
                # 验证合约格式
                if "_USDT" not in text.upper():
                    await update.message.reply_text("❌ 请输入正确的合约格式（例如：BTC_USDT）")
                    return
                
                state["symbol"] = text.upper()
                state["step"] = "leverage"
                await update.message.reply_text("📝 请输入杠杆倍数（1-100）：")
                
            elif step == "leverage":
                leverage = int(text)
                if not 1 <= leverage <= 100:
                    await update.message.reply_text("❌ 杠杆倍数必须在 1-100 之间")
                    return
                
                state["leverage"] = leverage
                state["step"] = "cost"
                await update.message.reply_text("📝 请输入成本（USDT）：")
                
            elif step == "cost":
                cost = float(text)
                if cost <= 0:
                    await update.message.reply_text("❌ 成本必须大于 0")
                    return
                
                state["cost_usdt"] = cost
                state["step"] = "rolling_count"
                await update.message.reply_text("📝 请输入滚仓次数：")
                
            elif step == "rolling_count":
                count = int(text)
                if count <= 0:
                    await update.message.reply_text("❌ 滚仓次数必须大于 0")
                    return
                
                state["rolling_count"] = count
                state["step"] = "order_type"
                
                # 选择订单类型
                keyboard = [
                    [InlineKeyboardButton("市价单", callback_data="market")],
                    [InlineKeyboardButton("限价单", callback_data="limit")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text("📝 请选择进场方式：", reply_markup=reply_markup)
                
            elif step == "entry_price":
                price = float(text)
                if price <= 0:
                    await update.message.reply_text("❌ 价格必须大于 0")
                    return
                
                state["entry_price"] = price
                
                # 显示确认信息
                await self.show_confirmation(update, user_id)
                
        except ValueError:
            await update.message.reply_text("❌ 请输入有效的数值")
        except Exception as e:
            logger.error(f"处理消息失败: {str(e)}")
            await update.message.reply_text(f"❌ 处理失败: {str(e)}")
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理回调"""
        query = update.callback_query
        user_id = query.from_user.id
        data = query.data
        
        await query.answer()
        
        if user_id not in self.user_states:
            await query.edit_message_text("会话已过期，请重新开始")
            return
        
        state = self.user_states[user_id]
        
        if data in ["market", "limit"]:
            state["order_type"] = data
            
            if data == "market":
                # 市价单直接确认
                await self.show_confirmation(query, user_id)
            else:
                # 限价单需要输入价格
                state["step"] = "entry_price"
                await query.edit_message_text("📝 请输入进场价格：")
                
        elif data == "confirm":
            # 执行滚仓策略
            await query.edit_message_text("⏳ 正在启动滚仓策略...")
            
            result = await self.strategy.start_rolling_strategy(user_id, state)
            
            if result["success"]:
                success_msg = f"""
✅ 滚仓策略启动成功！

合约：{state['symbol']}
杠杆：{state['leverage']}x
成本：{state['cost_usdt']} USDT
滚仓次数：{state['rolling_count']}
进场方式：{'市价单' if state['order_type'] == 'market' else '限价单'}
"""
                await query.edit_message_text(success_msg)
            else:
                await query.edit_message_text(f"❌ {result['message']}")
            
            # 清除用户状态
            del self.user_states[user_id]
            
        elif data == "cancel":
            await query.edit_message_text("❌ 已取消设置")
            del self.user_states[user_id]
    
    async def show_confirmation(self, update_or_query, user_id: int):
        """显示确认信息"""
        state = self.user_states[user_id]
        
        confirm_text = f"""
📋 请确认滚仓设置：

合约：{state['symbol']}
杠杆：{state['leverage']}x
成本：{state['cost_usdt']} USDT
滚仓次数：{state['rolling_count']}
进场方式：{'市价单' if state['order_type'] == 'market' else '限价单'}
"""
        
        if state["order_type"] == "limit":
            confirm_text += f"进场价格：{state['entry_price']}\n"
        
        confirm_text += "\n⚠️ 请确认信息无误后点击确认"
        
        keyboard = [
            [InlineKeyboardButton("✅ 确认", callback_data="confirm")],
            [InlineKeyboardButton("❌ 取消", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if hasattr(update_or_query, 'edit_message_text'):
            await update_or_query.edit_message_text(confirm_text, reply_markup=reply_markup)
        else:
            await update_or_query.message.reply_text(confirm_text, reply_markup=reply_markup)
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """查看策略状态"""
        user_id = update.effective_user.id
        
        result = await self.strategy.get_strategy_status(user_id)
        
        if not result["success"]:
            await update.message.reply_text(f"❌ {result['message']}")
            return
        
        strategy = result["strategy"]
        current_price = result["current_price"]
        position = result.get("current_position")
        
        status_text = f"""
📊 策略状态

合约：{strategy['contract']}
杠杆：{strategy['leverage']}x
成本：{strategy['cost_usdt']} USDT
进场价：{strategy['entry_price']}
当前价：{current_price}
滚仓张数：{strategy['rolling_size']}

滚仓订单状态：
"""
        
        for i, order in enumerate(strategy['rolling_orders']):
            status_icon = "✅" if order['status'] == 'pending' else "❌"
            status_text += f"{status_icon} 第{i+1}次：{order['price']} ({order['status']})\n"
        
        if position:
            pnl_pct = ((current_price - strategy['entry_price']) / strategy['entry_price']) * 100
            status_text += f"\n当前持仓：{position.get('size', 0)} 张"
            status_text += f"\n浮动盈亏：{pnl_pct:.2f}%"
        
        await update.message.reply_text(status_text)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """帮助命令"""
        help_text = """
📖 使用帮助

/start - 开始使用机器人
/roll - 设置滚仓策略
/status - 查看策略状态
/help - 显示此帮助信息

滚仓策略说明：
• 固定 2% 间隔滚仓
• 自动计算合适的张数
• 盈利后自动追加保证金
• 全仓模式，只做多

注意事项：
• 请确保账户有足够余额
• 滚仓有风险，请谨慎操作
• 建议先小额测试
        """
        await update.message.reply_text(help_text)

async def main():
    """主函数"""
    # 从环境变量获取配置
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    GATE_API_KEY = os.getenv("GATE_API_KEY")
    GATE_API_SECRET = os.getenv("GATE_API_SECRET")
    
    if not all([TELEGRAM_TOKEN, GATE_API_KEY, GATE_API_SECRET]):
        logger.error("缺少必要的环境变量")
        return
    
    # 初始化 Gate.io API
    gate_api = GateIOAPI(GATE_API_KEY, GATE_API_SECRET)
    
    # 初始化 Telegram Bot
    telegram_bot = TelegramBot(TELEGRAM_TOKEN, gate_api)
    
    # 创建应用
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", telegram_bot.start_command))
    application.add_handler(CommandHandler("roll", telegram_bot.roll_command))
    application.add_handler(CommandHandler("status", telegram_bot.status_command))
    application.add_handler(CommandHandler("help", telegram_bot.help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, telegram_bot.handle_message))
    application.add_handler(CallbackQueryHandler(telegram_bot.handle_callback))
    
    try:
        # 启动机器人
        logger.info("启动 Gate.io 滚仓机器人...")
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        
        # 保持运行
        await application.updater.idle()
        
    except Exception as e:
        logger.error(f"机器人运行错误: {str(e)}")
    finally:
        await gate_api.close_session()
        await application.stop()

if __name__ == "__main__":
    asyncio.run(main())
