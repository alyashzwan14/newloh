#!/usr/bin/env python3
import asyncio
import logging
import math
import os

try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal

from metaapi_cloud_sdk import MetaApi
from prettytable import PrettyTable
from telegram import ParseMode, Update
from telegram.ext import CommandHandler, Filters, MessageHandler, Updater, ConversationHandler, CallbackContext

# MetaAPI Credentials
API_KEY = os.environ.get("API_KEY")
ACCOUNT_ID = os.environ.get("ACCOUNT_ID")

# Telegram Credentials
TOKEN = os.environ.get("TOKEN")
TELEGRAM_USER = os.environ.get("TELEGRAM_USER")

# Heroku Credentials
APP_URL = os.environ.get("APP_URL")

# Port number for Telegram bot web hook
PORT = int(os.environ.get('PORT', '8443'))

# Allowed MT4 Account Number
ALLOWED_MT4_ACCOUNT_NUMBER = os.environ.get("4835673")

# Enables logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# possible states for conversation handler
CALCULATE, TRADE, DECISION = range(3)

# allowed FX symbols
SYMBOLS = ['AUDCAD', 'AUDCHF', 'AUDJPY', 'AUDNZD', 'AUDUSD', 'CADCHF', 'CADJPY', 'CHFJPY', 'EURAUD', 'EURCAD', 'EURCHF', 'EURGBP', 'EURJPY', 'EURNZD', 'EURUSD', 'GBPAUD', 'GBPCAD', 'GBPCHF', 'GBPJPY', 'GBPNZD', 'GBPUSD', 'NOW', 'NZDCAD', 'NZDCHF', 'NZDJPY', 'NZDUSD', 'USDCAD', 'USDCHF', 'USDJPY', 'XAGUSD', 'XAUUSD']

# RISK FACTOR
RISK_FACTOR = float(os.environ.get("RISK_FACTOR"))

# Helper Functions
def ParseSignal(signal: str) -> dict:
    """Starts process of parsing signal and entering trade on MetaTrader account.

    Arguments:
        signal: trading signal

    Returns:
        a dictionary that contains trade signal information
    """

    signal = signal.splitlines()
    signal = [line.rstrip() for line in signal]

    trade = {}

    if 'Buy Limit'.lower() in signal[0].lower():
        trade['OrderType'] = 'Buy Limit'
    elif 'Sell Limit'.lower() in signal[0].lower():
        trade['OrderType'] = 'Sell Limit'
    elif 'Buy Stop'.lower() in signal[0].lower():
        trade['OrderType'] = 'Buy Stop'
    elif 'Sell Stop'.lower() in signal[0].lower():
        trade['OrderType'] = 'Sell Stop'
    elif 'Buy'.lower() in signal[0].lower():
        trade['OrderType'] = 'Buy'
    elif 'Sell'.lower() in signal[0].lower():
        trade['OrderType'] = 'Sell'
    else:
        return {}

    trade['Symbol'] = (signal[0].split())[-1].upper()

    if trade['Symbol'] not in SYMBOLS:
        return {}

    if trade['OrderType'] == 'Buy' or trade['OrderType'] == 'Sell':
        trade['Entry'] = (signal[1].split())[-1]
    else:
        trade['Entry'] = float((signal[1].split())[-1])

    trade['StopLoss'] = float((signal[2].split())[-1])
    trade['TP'] = [float((signal[3].split())[-1])]

    if len(signal) > 4:
        trade['TP'].append(float(signal[4].split()[-1]))

    trade['RiskFactor'] = RISK_FACTOR

    return trade

def GetTradeInformation(update: Update, trade: dict, balance: float) -> None:
    if trade['Symbol'] == 'XAUUSD':
        multiplier = 0.1
    elif trade['Symbol'] == 'XAGUSD':
        multiplier = 0.001
    elif str(trade['Entry']).index('.') >= 2:
        multiplier = 0.01
    else:
        multiplier = 0.0001

    stopLossPips = abs(round((trade['StopLoss'] - trade['Entry']) / multiplier))
    trade['PositionSize'] = math.floor(((balance * trade['RiskFactor']) / stopLossPips) / 10 * 100) / 100

    takeProfitPips = []
    for takeProfit in trade['TP']:
        takeProfitPips.append(abs(round((takeProfit - trade['Entry']) / multiplier)))

    table = CreateTable(trade, balance, stopLossPips, takeProfitPips)
    update.effective_message.reply_text(f'<pre>{table}</pre>', parse_mode=ParseMode.HTML)

    return

def CreateTable(trade: dict, balance: float, stopLossPips: int, takeProfitPips: int) -> PrettyTable:
    table = PrettyTable()
    table.title = "Trade Information"
    table.field_names = ["Key", "Value"]
    table.align["Key"] = "l"  
    table.align["Value"] = "l" 

    table.add_row([trade["OrderType"], trade["Symbol"]])
    table.add_row(['Entry\n', trade['Entry']])
    table.add_row(['Stop Loss', '{} pips'.format(stopLossPips)])

    for count, takeProfit in enumerate(takeProfitPips):
        table.add_row([f'TP {count + 1}', f'{takeProfit} pips'])

    table.add_row(['\nRisk Factor', '\n{:,.0f} %'.format(trade['RiskFactor'] * 100)])
    table.add_row(['Position Size', trade['PositionSize']])
    table.add_row(['\nCurrent Balance', '\n$ {:,.2f}'.format(balance)])
    table.add_row(['Potential Loss', '$ {:,.2f}'.format(round((trade['PositionSize'] * 10) * stopLossPips, 2))])

    totalProfit = 0
    for count, takeProfit in enumerate(takeProfitPips):
        profit = round((trade['PositionSize'] * 10 * (1 / len(takeProfitPips))) * takeProfit, 2)
        table.add_row([f'TP {count + 1} Profit', '$ {:,.2f}'.format(profit)])
        totalProfit += profit

    table.add_row(['\nTotal Profit', '\n$ {:,.2f}'.format(totalProfit)])

    return table

async def ConnectMetaTrader(update: Update, trade: dict, enterTrade: bool):
    api = MetaApi(API_KEY)
    
    try:
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        initial_state = account.state
        deployed_states = ['DEPLOYING', 'DEPLOYED']

        if initial_state not in deployed_states:
            logger.info('Deploying account')
            await account.deploy()

        logger.info('Waiting for API server to connect to broker ...')
        await account.wait_connected()

        connection = account.get_rpc_connection()
        await connection.connect()

        logger.info('Waiting for SDK to synchronize to terminal state ...')
        await connection.wait_synchronized()

        account_information = await connection.get_account_information()

        if account_information['login'] != int(ALLOWED_MT4_ACCOUNT_NUMBER):
            update.effective_message.reply_text("Connected to an unauthorized MT4 account. Operation aborted.")
            logger.error('Connected to an unauthorized MT4 account.')
            return

        update.effective_message.reply_text("Successfully connected to MetaTrader!\nCalculating trade risk ...")

        if trade['Entry'] == 'NOW':
            price = await connection.get_symbol_price(symbol=trade['Symbol'])

            if trade['OrderType'] == 'Buy':
                trade['Entry'] = float(price['bid'])
            elif trade['OrderType'] == 'Sell':
                trade['Entry'] = float(price['ask'])

        GetTradeInformation(update, trade, account_information['balance'])
            
        if enterTrade:
            update.effective_message.reply_text("Entering trade on MetaTrader Account ...")
            try:
                if trade['OrderType'] == 'Buy':
                    for takeProfit in trade['TP']:
                        result = await connection.create_market_buy_order(trade['Symbol'], trade['PositionSize'] / len(trade['TP']), trade['StopLoss'], takeProfit)
                elif trade['OrderType'] == 'Buy Limit':
                    for takeProfit in trade['TP']:
                        result = await connection.create_limit_buy_order(trade['Symbol'], trade['PositionSize'] / len(trade['TP']), trade['Entry'], trade['StopLoss'], takeProfit)
                elif trade['OrderType'] == 'Buy Stop':
                    for takeProfit in trade['TP']:
                        result = await connection.create_stop_buy_order(trade['Symbol'], trade['PositionSize'] / len(trade['TP']), trade['Entry'], trade['StopLoss'], takeProfit)
                elif trade['OrderType'] == 'Sell':
                    for takeProfit in trade['TP']:
                        result = await connection.create_market_sell_order(trade['Symbol'], trade['PositionSize'] / len(trade['TP']), trade['StopLoss'], takeProfit)
                elif trade['OrderType'] == 'Sell Limit':
                    for takeProfit in trade['TP']:
                        result = await connection.create_limit_sell_order(trade['Symbol'], trade['PositionSize'] / len(trade['TP']), trade['Entry'], trade['StopLoss'], takeProfit)
                elif trade['OrderType'] == 'Sell Stop':
                    for takeProfit in trade['TP']:
                        result = await connection.create_stop_sell_order(trade['Symbol'], trade['PositionSize'] / len(trade['TP']), trade['Entry'], trade['StopLoss'], takeProfit)
                
                update.effective_message.reply_text("Trade entered successfully, Good Luck!")
                logger.info('\nTrade entered successfully, Good Luck!')
                logger.info('Result Code: {}\n'.format(result['stringCode']))
            
            except Exception as error:
                logger.info(f"\nTrade failed with error: {error}\n")
                update.effective_message.reply_text(f"There was an issue \n\nError Message:\n{error}")
    
    except Exception as error:
        logger.error(f'Error: {error}')
        update.effective_message.reply_text(f"There was an issue with the connection \n\nError Message:\n{error}")
    
    return

def PlaceTrade(update: Update, context: CallbackContext) -> int:
    if context.user_data['trade'] is None:
        try: 
            trade = ParseSignal(update.effective_message.text)
            if not trade:
                raise Exception('Invalid Trade')
            context.user_data['trade'] = trade
            update.effective_message.reply_text("Trade Successfully Parsed! \nConnecting to MetaTrader ... \n(May take a while)")
        except Exception as error:
            logger.error(f'Error: {error}')
            errorMessage = f"There was an error parsing this trade \n\nError: {error}\n\nPlease re-enter trade with this format:\n\nBUY/SELL SYMBOL\nEntry \nSL \nTP \n\nOr use the /cancel to command to cancel this action."
            update.effective_message.reply_text(errorMessage)
            return TRADE
    
    asyncio.run(ConnectMetaTrader(update, context.user_data['trade'], True))
    context.user_data['trade'] = None
    return ConversationHandler.END

def CalculateTrade(update: Update, context: CallbackContext) -> int:
    if context.user_data['trade'] is None:
        try: 
            trade = ParseSignal(update.effective_message.text)
            if not trade:
                raise Exception('Invalid Trade')
            context.user_data['trade'] = trade
            update.effective_message.reply_text("Trade Successfully Parsed!\nConnecting to MetaTrader ... (May take a while)")
        except Exception as error:
            logger.error(f'Error: {error}')
            errorMessage = f"There was an error parsing this trade 😕\n\nError: {error}\n\nPlease re-enter trade with this format:\n\nBUY/SELL SYMBOL\nEntry \nSL \nTP \n\nOr use the /cancel to command to cancel this action."
            update.effective_message.reply_text(errorMessage)
            return CALCULATE
    
    asyncio.run(ConnectMetaTrader(update, context.user_data['trade'], False))
    update.effective_message.reply_text("Would you like to enter this trade?\nTo enter, select: /yes\nTo decline, select: /no")
    return DECISION

def unknown_command(update: Update, context: CallbackContext) -> None:
    if not (update.effective_message.chat.username == TELEGRAM_USER):
        update.effective_message.reply_text("Sorry, You are not authorized to use this bot!")
        return
    update.effective_message.reply_text("Unknown command. Use /trade to place a trade or /calculate to find information for a trade. You can also use the /help command to view instructions for this bot.")
    return

def welcome(update: Update, context: CallbackContext) -> None:
    welcome_message = "Hi, Welcome to the ProjexFX - Signal Trading MM Telegram Bot! \n\nYou can use this bot to enter trades directly from Telegram and get a detailed look at your risk to reward ratio with profit, loss, and calculated lot size. You are able to change specific settings such as allowed symbols, risk factor, and more from your personalized Python script and environment variables.\n\nUse the /help command to view instructions and example trades."
    update.effective_message.reply_text(welcome_message)
    return

def help(update: Update, context: CallbackContext) -> None:
    help_message = "This bot is used to automatically enter trades onto your MetaTrader account directly from Telegram. To begin, ensure that you are authorized to use this bot by adjusting your Python script or environment variables.\n\nThis bot supports all trade order types (Market Execution, Limit, and Stop)\n\nAfter an extended period away from the bot, please be sure to re-enter the start command to restart the connection to your MetaTrader account."
    commands = "List of commands:\n/start : displays welcome message\n/help : displays list of commands and example trades\n/trade : takes in user inputted trade for parsing and placement\n/calculate : calculates trade information for a user inputted trade"
    trade_example = "Example Trades:\n\n"
    market_execution_example = "Market Execution:\nBUY GBPUSD\nEntry NOW\nSL 1.14336\nTP 1.28930\nTP 1.29845\n\n"
    limit_example = "Limit Execution:\nBUY LIMIT GBPUSD\nEntry 1.14480\nSL 1.14336\nTP 1.28930\n\n"
    note = "You are able to enter up to two take profits. If two are entered, both trades will use half of the position size, and one will use TP1 while the other uses TP2.\n\nNote: Use 'NOW' as the entry to enter a market execution trade."
    update.effective_message.reply_text(help_message)
    update.effective_message.reply_text(commands)
    update.effective_message.reply_text(trade_example + market_execution_example + limit_example + note)
    return

def cancel(update: Update, context: CallbackContext) -> int:
    update.effective_message.reply_text("Command has been canceled.")
    context.user_data['trade'] = None
    return ConversationHandler.END

def error(update: Update, context: CallbackContext) -> None:
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    return

def Trade_Command(update: Update, context: CallbackContext) -> int:
    if not (update.effective_message.chat.username == TELEGRAM_USER):
        update.effective_message.reply_text("You are not authorized to use this bot!")
        return ConversationHandler.END
    context.user_data['trade'] = None
    update.effective_message.reply_text("Please enter the trade that you would like to place.")
    return TRADE

def Calculation_Command(update: Update, context: CallbackContext) -> int:
    if not (update.effective_message.chat.username == TELEGRAM_USER):
        update.effective_message.reply_text("Sorry, You are not authorized to use this bot!")
        return ConversationHandler.END
    context.user_data['trade'] = None
    update.effective_message.reply_text("Please enter the trade that you would like to calculate.")
    return CALCULATE

def main() -> None:
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", welcome))
    dp.add_handler(CommandHandler("help", help))

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("trade", Trade_Command), CommandHandler("calculate", Calculation_Command)],
        states={
            TRADE: [MessageHandler(Filters.text & ~Filters.command, PlaceTrade)],
            CALCULATE: [MessageHandler(Filters.text & ~Filters.command, CalculateTrade)],
            DECISION: [CommandHandler("yes", PlaceTrade), CommandHandler("no", cancel)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    dp.add_handler(conv_handler)
    dp.add_handler(MessageHandler(Filters.text, unknown_command))
    dp.add_error_handler(error)
    
    updater.start_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=APP_URL + TOKEN)
    updater.idle()
    return

if __name__ == '__main__':
    main()
