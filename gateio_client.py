import gate_api
from gate_api import ApiClient, Configuration, FuturesOrder, FuturesApi
import hashlib
import hmac
import time
import json
from datetime import datetime
from config import GATEIO_API_KEY, GATEIO_API_SECRET, SETTLE_CURRENCY

class GateIOClient:
    def __init__(self):
        self.config = Configuration(
            key=GATEIO_API_KEY,
            secret=GATEIO_API_SECRET,
            host="https://api.gateio.ws/api/v4"
        )
        self.api_client = ApiClient(self.config)
        self.futures_api = FuturesApi(self.api_client)
    
    def get_ticker_price(self, symbol):
        """獲取當前價格"""
        try:
            tickers = self.futures_api.list_futures_tickers(settle=SETTLE_CURRENCY, contract=symbol)
            if tickers:
                return float(tickers[0].last)
            return None
        except Exception as e:
            raise Exception(f"獲取價格失敗: {str(e)}")
    
    def get_account_balance(self):
        """獲取帳戶餘額"""
        try:
            # 調試：先打印返回的對象類型
            account_data = self.futures_api.list_futures_accounts(settle=SETTLE_CURRENCY)
            print(f"Account data type: {type(account_data)}")
            print(f"Account data: {account_data}")
            
            # 根據實際返回的數據結構進行處理
            if hasattr(account_data, 'total'):
                # 如果是單一對象且有total屬性
                return float(account_data.total)
            elif isinstance(account_data, list) and len(account_data) > 0:
                # 如果是列表
                if hasattr(account_data[0], 'total'):
                    return float(account_data[0].total)
                else:
                    # 嘗試其他可能的屬性名
                    for attr in ['available', 'balance', 'total_balance']:
                        if hasattr(account_data[0], attr):
                            return float(getattr(account_data[0], attr))
            else:
                # 嘗試直接訪問可能存在的屬性
                for attr in ['total', 'available', 'balance']:
                    if hasattr(account_data, attr):
                        return float(getattr(account_data, attr))
            
            # 如果以上都不行，嘗試轉換為字典
            if hasattr(account_data, '__dict__'):
                account_dict = account_data.__dict__
                for key in ['total', 'available', 'balance', 'total_balance']:
                    if key in account_dict:
                        return float(account_dict[key])
            
            return 0.0
            
        except Exception as e:
            print(f"Error details: {str(e)}")
            raise Exception(f"獲取餘額失敗: {str(e)}")
    
    def set_leverage(self, symbol, leverage):
        """設定槓桿"""
        try:
            leverage_str = f"{leverage}"
            result = self.futures_api.update_position_leverage(
                settle=SETTLE_CURRENCY,
                contract=symbol,
                leverage=leverage_str
            )
            return True
        except Exception as e:
            raise Exception(f"設定槓桿失敗: {str(e)}")
    
    def calculate_position_size(self, symbol, margin, leverage, price):
        """計算可開倉數量"""
        try:
            # 獲取合約資訊
            contract = self.futures_api.get_futures_contract(settle=SETTLE_CURRENCY, contract=symbol)
            
            # 計算合約價值
            if hasattr(contract, 'quanto_multiplier') and contract.quanto_multiplier:
                contract_size = float(contract.quanto_multiplier)
            elif hasattr(contract, 'size') and contract.size:
                contract_size = float(contract.size)
            else:
                contract_size = 1.0
            
            # 計算可開倉張數
            total_value = margin * leverage
            position_size = total_value / price / contract_size
            
            return int(position_size)  # 返回整數張數
        except Exception as e:
            raise Exception(f"計算倉位大小失敗: {str(e)}")
    
    def place_market_order(self, symbol, size, side='long'):
        """下市價單"""
        try:
            order = FuturesOrder(
                contract=symbol,
                size=size,
                price='0',  # 市價單價格設為0
                side='buy' if side == 'long' else 'sell',
                time_in_force='ioc'
            )
            result = self.futures_api.create_futures_order(settle=SETTLE_CURRENCY, futures_order=order)
            return result
        except Exception as e:
            raise Exception(f"下單失敗: {str(e)}")
    
    def place_limit_order(self, symbol, size, price, side='long'):
        """下限價單"""
        try:
            order = FuturesOrder(
                contract=symbol,
                size=size,
                price=str(price),
                side='buy' if side == 'long' else 'sell',
                time_in_force='gtc'  # 一直有效直到取消
            )
            result = self.futures_api.create_futures_order(settle=SETTLE_CURRENCY, futures_order=order)
            return result
        except Exception as e:
            raise Exception(f"下限價單失敗: {str(e)}")
    
    def place_conditional_order(self, symbol, size, trigger_price, side='long'):
        """下條件單"""
        try:
            # 使用止盈止損單來實現條件單功能
            order = FuturesOrder(
                contract=symbol,
                size=size,
                price='0',  # 市價單
                side='buy' if side == 'long' else 'sell',
                time_in_force='ioc',
                stop_trigger=str(trigger_price)
            )
            result = self.futures_api.create_futures_order(settle=SETTLE_CURRENCY, futures_order=order)
            return result
        except Exception as e:
            raise Exception(f"下條件單失敗: {str(e)}")
    
    def get_open_orders(self, symbol):
        """獲取未成交訂單"""
        try:
            orders = self.futures_api.list_futures_orders(
                settle=SETTLE_CURRENCY, 
                contract=symbol,
                status='open'
            )
            return orders
        except Exception as e:
            raise Exception(f"獲取訂單失敗: {str(e)}")
    
    def cancel_all_orders(self, symbol):
        """取消所有訂單"""
        try:
            result = self.futures_api.cancel_futures_orders(
                settle=SETTLE_CURRENCY,
                contract=symbol
            )
            return True
        except Exception as e:
            raise Exception(f"取消訂單失敗: {str(e)}")

class TradingStrategy:
    def __init__(self, gateio_client):
        self.client = gateio_client
    
    def calculate_rollover_prices(self, entry_price, rollover_times, percentage_increase):
        """計算滾倉觸發價格"""
        prices = []
        current_price = entry_price
        
        for i in range(rollover_times):
            current_price = current_price * (1 + percentage_increase / 100)
            prices.append(round(current_price, 2))
        
        return prices
    
    def execute_strategy(self, symbol, entry_type, leverage, margin, rollover_times, percentage_increase, entry_price=None):
        """執行交易策略"""
        try:
            # 獲取當前價格
            current_price = self.client.get_ticker_price(symbol)
            if not current_price:
                return False, "無法獲取當前價格"
            
            # 設定槓桿
            self.client.set_leverage(symbol, leverage)
            
            # 計算開倉數量
            if entry_type == 'market':
                entry_price = current_price
            
            position_size = self.client.calculate_position_size(symbol, margin, leverage, entry_price)
            
            if position_size <= 0:
                return False, "計算的倉位大小為0"
            
            # 下進場單
            if entry_type == 'market':
                order_result = self.client.place_market_order(symbol, position_size, 'long')
            else:
                if not entry_price:
                    return False, "掛單需要指定進場價格"
                order_result = self.client.place_limit_order(symbol, position_size, entry_price, 'long')
            
            # 計算滾倉價格
            rollover_prices = self.calculate_rollover_prices(
                entry_price if entry_type == 'limit' else current_price,
                rollover_times,
                percentage_increase
            )
            
            # 下滾倉條件單
            rollover_orders = []
            for i, trigger_price in enumerate(rollover_prices):
                cond_order = self.client.place_conditional_order(
                    symbol, position_size, trigger_price, 'long'
                )
                rollover_orders.append({
                    'order_id': cond_order.id,
                    'trigger_price': trigger_price,
                    'size': position_size
                })
            
            return True, {
                'entry_order': order_result.id,
                'entry_price': entry_price if entry_type == 'limit' else current_price,
                'position_size': position_size,
                'rollover_orders': rollover_orders
            }
            
        except Exception as e:
            return False, str(e)
