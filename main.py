# payment_bot.py
import os
import re
import time
import threading
import datetime
import pyrebase
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, ParseMode
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
TELEGRAM_TOKEN = "7651343412:AAHmHZWDhgDMGLcqtGKBi-r8M7pVvzJ_baY"

# === States ===
ASK_AMOUNT, ASK_NAME, VERIFYING = range(3)

# === Welcome + Collect Amount ===
def start(update: Update, context: CallbackContext):
    context.bot.send_message(chat_id=update.effective_chat.id, text="👋 *Welcome to PayVery!*\n\n💰 How much would you like to pay?", parse_mode=ParseMode.MARKDOWN)
    delete_previous_messages(context)
    return ASK_AMOUNT

# === Collect Name ===
def ask_name(update: Update, context: CallbackContext):
    try:
        context.user_data["amount"] = float(update.message.text.strip())
        delete_previous_messages(context)
        msg = context.bot.send_message(chat_id=update.effective_chat.id, text="✍️ Please enter your *UPI name* (as shown in your app):", parse_mode=ParseMode.MARKDOWN)
        context.user_data["last_msg"] = msg.message_id
        return ASK_NAME
    except ValueError:
        update.message.reply_text("⚠️ Please enter a valid amount in numbers only.")
        return ASK_AMOUNT

# === Display QR and Start Verifying ===
def show_qr_and_verify(update: Update, context: CallbackContext):
    name = update.message.text.strip()
    amount = context.user_data.get("amount")
    context.user_data["name"] = name

    delete_previous_messages(context)

    qr_path = "qrphoto.jpg"
    upi_id = "9351044618@mbk"

    msg = update.message.reply_photo(
        open(qr_path, 'rb'),
        caption=f"📲 *Scan to Pay*\n\n💸 Send *₹{amount}* to: `{upi_id}`\n\n_Payment will verify instantly!_",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data["last_msg"] = msg.message_id

    checking_msg = context.bot.send_message(chat_id=update.effective_chat.id, text="🔍 *Checking for your payment...*", parse_mode=ParseMode.MARKDOWN)
    context.user_data["checking_msg"] = checking_msg.message_id

    threading.Thread(target=monitor_payment_and_reply, args=(update, context, name, amount), daemon=True).start()

    return VERIFYING

# === Monitor Firebase for 5 min ===
def monitor_payment_and_reply(update, context, name, amount):
    user_id = update.message.chat_id
    found = False

    for _ in range(30):
        time.sleep(10)
        payments = db.child("verified_payments").get().val()
        if payments:
            for key, record in payments.items():
                record_name = record.get("name", "").lower().split()[0]
                if (record_name == name.lower().split()[0] and
                    abs(record.get("amount", 0) - amount) < 0.01):

                    now = datetime.datetime.now()
                    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

                    # Delete payment from database to prevent re-verification
                    db.child("verified_payments").child(key).remove()

                    try:
                        context.bot.delete_message(chat_id=user_id, message_id=context.user_data.get("checking_msg"))
                    except:
                        pass

                    context.bot.send_message(
                        chat_id=user_id,
                        text=f"✅ *Payment Verified Successfully!*\n\n📄 *Invoice Details:*\n*Name:* `{record.get('name')}`\n*Amount:* ₹{record.get('amount')}\n🕒 *Verified At:* {timestamp}\n\n✅ _Thank you for your payment via PayVery!_",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    found = True
                    return

    if not found:
        try:
            context.bot.delete_message(chat_id=user_id, message_id=context.user_data.get("checking_msg"))
        except:
            pass
        button = [[InlineKeyboardButton("🔁 Verify Again", callback_data=f"verify|{name}|{amount}")]]
        reply_markup = InlineKeyboardMarkup(button)
        context.bot.send_message(chat_id=user_id, text="⏳ *Payment not found within 5 minutes.*", parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

# === Handle Retry Button ===
def verify_again(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    _, name, amount = query.data.split("|")
    amount = float(amount)
    payments = db.child("verified_payments").get().val()

    if payments:
        for key, record in payments.items():
            record_name = record.get("name", "").lower().split()[0]
            if (record_name == name.lower().split()[0] and
                abs(record.get("amount", 0) - amount) < 0.01):

                now = datetime.datetime.now()
                timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
                db.child("verified_payments").child(key).remove()

                query.edit_message_text(
                    f"✅ *Payment Verified Successfully!*\n\n📄 *Invoice Details:*\n*Name:* `{record.get('name')}`\n*Amount:* ₹{record.get('amount')}\n🕒 *Verified At:* {timestamp}\n\n✅ _Thank you for your payment via PayVery!_",
                    parse_mode=ParseMode.MARKDOWN
                )
                return

    query.edit_message_text("❌ *Still no matching payment found.* Please ensure the name and amount are correct.", parse_mode=ParseMode.MARKDOWN)

# === Cancel Command ===
def cancel(update: Update, context: CallbackContext):
    context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Cancelled.")
    return ConversationHandler.END

# === Utility to delete old messages ===
def delete_previous_messages(context):
    try:
        user_id = context._chat_id_and_data[0]
        if "last_msg" in context.user_data:
            context.bot.delete_message(chat_id=user_id, message_id=context.user_data["last_msg"])
    except:
        pass

# === Main ===
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, ask_name)],
            ASK_NAME: [MessageHandler(Filters.text & ~Filters.command, show_qr_and_verify)],
            VERIFYING: []
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    dp.add_handler(conv_handler)
    dp.add_handler(CallbackQueryHandler(verify_again, pattern=r"^verify\|"))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
