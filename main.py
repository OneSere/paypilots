import re
import json
import datetime
import threading
import pyrebase
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler, CallbackContext

# === Firebase Configuration ===
firebase_config = {
    "apiKey": "FAKE-KEY",  # Optional
    "authDomain": "payvari.firebaseapp.com",
    "databaseURL": "https://payvari-default-rtdb.firebaseio.com/",
    "storageBucket": "payvari.appspot.com"
}

firebase = pyrebase.initialize_app(firebase_config)
db = firebase.database()

# === Hardcoded Telegram Token ===
TELEGRAM_TOKEN = "7651343412:AAHmHZWDhgDMGLcqtGKBi-r8M7pVvzJ_baY"

# === Conversation States ===
ASK_NAME, ASK_AMOUNT, ASK_DATE = range(3)

# === Extract payment details from SMS ===
def parse_sms(message):
    match = re.search(r"received\s+Rs\.?\s*([\d.]+).*?from\s+(.+?)\.", message, re.IGNORECASE)
    if match:
        amount = float(match.group(1))
        name = match.group(2).strip()
        date = datetime.datetime.now().strftime('%Y-%m-%d')
        return {"name": name, "amount": amount, "date": date}
    return None

# === Monitor and move SMS to verified_payments ===
def monitor_sms():
    while True:
        try:
            all_sms = db.child("sms_messages").get().val()
            if all_sms:
                for key, value in all_sms.items():
                    msg = value.get("message", "")
                    parsed = parse_sms(msg)
                    if parsed:
                        db.child("verified_payments").push(parsed)
                    db.child("sms_messages").child(key).remove()
        except Exception as e:
            print(f"[monitor_sms] Error: {e}")
        import time
        time.sleep(10)

# === Telegram Bot Handlers ===
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Welcome to Payment Verification Bot.\nEnter payer name:")
    return ASK_NAME

def ask_name(update: Update, context: CallbackContext):
    context.user_data["name"] = update.message.text.strip()
    update.message.reply_text("Enter amount paid:")
    return ASK_AMOUNT

def ask_amount(update: Update, context: CallbackContext):
    try:
        context.user_data["amount"] = float(update.message.text.strip())
        update.message.reply_text("Enter payment date (YYYY-MM-DD):")
        return ASK_DATE
    except ValueError:
        update.message.reply_text("Invalid amount. Try again:")
        return ASK_AMOUNT

def ask_date(update: Update, context: CallbackContext):
    context.user_data["date"] = update.message.text.strip()
    name = context.user_data["name"]
    amount = context.user_data["amount"]
    date = context.user_data["date"]

    try:
        payments = db.child("verified_payments").get().val()
        if payments:
            for p in payments.values():
                if (p["name"].lower() == name.lower()
                        and abs(p["amount"] - amount) < 0.01
                        and p["date"] == date):
                    update.message.reply_text("✅ Payment Verified!")
                    return ConversationHandler.END
    except:
        pass

    update.message.reply_text("❌ Payment not found.")
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("Verification canceled.")
    return ConversationHandler.END

# === Main Execution ===
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(Filters.text & ~Filters.command, ask_name)],
            ASK_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, ask_amount)],
            ASK_DATE: [MessageHandler(Filters.text & ~Filters.command, ask_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    dp.add_handler(conv_handler)

    # Start Firebase monitoring in background thread
    threading.Thread(target=monitor_sms, daemon=True).start()

    updater.start_polling()
    print("🤖 Bot is running...")
    updater.idle()

if __name__ == "__main__":
    main()
