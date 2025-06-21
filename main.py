import re
import json
import datetime
import pyrebase
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler, CallbackContext

# === Firebase Configuration ===
firebase_config = {
    "apiKey": "FAKE-KEY",
    "authDomain": "payvari.firebaseapp.com",
    "databaseURL": "https://payvari-default-rtdb.firebaseio.com/",
    "storageBucket": "payvari.appspot.com"
}

firebase = pyrebase.initialize_app(firebase_config)
db = firebase.database()

# === Telegram Bot Token ===
TELEGRAM_TOKEN = "7651343412:AAHmHZWDhgDMGLcqtGKBi-r8M7pVvzJ_baY"

# === States for Conversation ===
ASK_NAME, ASK_AMOUNT, ASK_DATE = range(3)

# === SMS Parsing Function ===
def parse_sms(message):
    match = re.search(r"received\s+Rs\.\s*([\d.]+).*from\s+(.+?)\.", message, re.IGNORECASE)
    if match:
        amount = float(match.group(1))
        name = match.group(2).strip()
        date = datetime.datetime.now().strftime('%Y-%m-%d')
        return {"name": name, "amount": amount, "date": date}
    return None

# === Monitor Firebase for New SMS ===
def monitor_sms():
    all_sms = db.child("sms_messages").get().val()
    if all_sms:
        for key, value in all_sms.items():
            parsed = parse_sms(value.get("message", ""))
            if parsed:
                db.child("verified_payments").push(parsed)
                db.child("sms_messages").child(key).remove()  # Clean up processed SMS

# === Bot Handlers ===
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Welcome to the Payment Verification Bot.\nPlease enter the payer's name:")
    return ASK_NAME

def ask_name(update: Update, context: CallbackContext):
    context.user_data["name"] = update.message.text.strip()
    update.message.reply_text("Enter the amount paid:")
    return ASK_AMOUNT

def ask_amount(update: Update, context: CallbackContext):
    try:
        context.user_data["amount"] = float(update.message.text.strip())
    except ValueError:
        update.message.reply_text("Invalid amount. Please enter a number:")
        return ASK_AMOUNT
    update.message.reply_text("Enter the date of payment (YYYY-MM-DD):")
    return ASK_DATE

def ask_date(update: Update, context: CallbackContext):
    context.user_data["date"] = update.message.text.strip()
    name = context.user_data["name"]
    amount = context.user_data["amount"]
    date = context.user_data["date"]

    payments = db.child("verified_payments").get().val()
    if payments:
        for record in payments.values():
            if (record["name"].lower() == name.lower()
                and abs(record["amount"] - amount) < 0.01
                and record["date"] == date):
                update.message.reply_text("✅ Payment Verified!")
                return ConversationHandler.END
    update.message.reply_text("❌ Payment not found.")
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# === Main Function ===
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            ASK_NAME: [MessageHandler(Filters.text & ~Filters.command, ask_name)],
            ASK_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, ask_amount)],
            ASK_DATE: [MessageHandler(Filters.text & ~Filters.command, ask_date)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    dispatcher.add_handler(conv_handler)

    # Poll Firebase every 10 seconds for new SMS messages
    import threading
    def run_monitor():
        import time
        while True:
            monitor_sms()
            time.sleep(10)

    threading.Thread(target=run_monitor, daemon=True).start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
