# payment_bot.py
import os
import re
import time
import threading
import datetime
import pyrebase
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, ConversationHandler, CallbackQueryHandler

# === Firebase Configuration ===
firebase_config = {
    "apiKey": "FAKE-KEY",
    "authDomain": "payvari.firebaseapp.com",
    "databaseURL": "https://payvari-default-rtdb.firebaseio.com/",
    "storageBucket": "payvari.appspot.com"
}

firebase = pyrebase.initialize_app(firebase_config)
db = firebase.database()

# === Telegram Token ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or "7651343412:AAHmHZWDhgDMGLcqtGKBi-r8M7pVvzJ_baY"

# === States ===
ASK_AMOUNT, ASK_NAME, VERIFYING = range(3)

# === Welcome + Collect Amount ===
def start(update: Update, context: CallbackContext):
    update.message.reply_text("\U0001F44B Welcome to the Payment Verification Bot!\n\nHow much would you like to pay (in ₹)?")
    return ASK_AMOUNT

# === Collect Name ===
def ask_name(update: Update, context: CallbackContext):
    try:
        context.user_data["amount"] = float(update.message.text.strip())
        update.message.reply_text("Please enter your name (as on UPI app):")
        return ASK_NAME
    except ValueError:
        update.message.reply_text("Please enter a valid amount (in numbers):")
        return ASK_AMOUNT

# === Display QR and Start Verifying ===
def show_qr_and_verify(update: Update, context: CallbackContext):
    context.user_data["name"] = update.message.text.strip()
    amount = context.user_data["amount"]
    name = context.user_data["name"]

    qr_path = "qrphoto.jpg"  # Should exist in Railway project folder
    upi_id = "9351044618@mbk"

    update.message.reply_photo(open(qr_path, 'rb'), caption=f"\u2B06\uFE0F *Scan to Pay*\n\nSend *₹{amount}* to UPI ID: `{upi_id}`\nPayment will verify instantly!", parse_mode='Markdown')

    # Start 5-min monitor thread
    threading.Thread(target=monitor_payment_and_reply, args=(update, context, name, amount), daemon=True).start()

    return VERIFYING

# === Monitor Firebase for 5 min ===
def monitor_payment_and_reply(update, context, name, amount):
    user_id = update.message.chat_id
    matched = False
    for _ in range(30):  # Check every 10s for 5 mins
        time.sleep(10)
        payments = db.child("verified_payments").get().val()
        if payments:
            for record in payments.values():
                if (record["name"].lower() == name.lower()
                    and abs(record["amount"] - amount) < 0.01):
                    context.bot.send_message(chat_id=user_id, text=f"\u2705 *Payment of ₹{amount} received successfully!*", parse_mode='Markdown')
                    return

    # If not matched after 5 min, show retry button
    button = [[InlineKeyboardButton("Verify Again", callback_data=f"verify|{name}|{amount}")]]
    reply_markup = InlineKeyboardMarkup(button)
    context.bot.send_message(chat_id=user_id, text="⏱️ Payment not verified within 5 minutes.", reply_markup=reply_markup)

# === Handle Retry Button ===
def verify_again(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    _, name, amount = query.data.split("|")
    amount = float(amount)
    payments = db.child("verified_payments").get().val()
    if payments:
        for record in payments.values():
            if (record["name"].lower() == name.lower()
                and abs(record["amount"] - amount) < 0.01):
                query.edit_message_text(f"\u2705 *Payment of ₹{amount} received successfully!*", parse_mode='Markdown')
                return
    query.edit_message_text("❌ Still no payment found. Please try again later or contact support.")

# === Cancel Command ===
def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# === Main ===
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, ask_name)],
            ASK_NAME: [MessageHandler(Filters.text & ~Filters.command, show_qr_and_verify)],
            VERIFYING: []  # No user input handled during this
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    dp.add_handler(conv_handler)
    dp.add_handler(CallbackQueryHandler(verify_again, pattern=r"^verify\|"))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
