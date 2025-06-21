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

# === Helper Functions ===
def delete_message_safe(context, chat_id, message_id):
    """Safely delete a message"""
    try:
        context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass  # Message might already be deleted

def normalize_name(name):
    """Normalize name for comparison"""
    return re.sub(r'[^a-zA-Z\s]', '', name.lower().strip())

def check_name_match(user_name, firebase_name):
    """Check if first name matches and at least 70% similarity"""
    user_parts = normalize_name(user_name).split()
    firebase_parts = normalize_name(firebase_name).split()
    
    if not user_parts or not firebase_parts:
        return False
    
    # First name must match exactly
    if user_parts[0] != firebase_parts[0]:
        return False
    
    return True

def format_currency(amount):
    """Format currency in Indian style"""
    return f"₹{amount:,.2f}"

def generate_invoice(name, amount, payment_time):
    """Generate a formatted invoice"""
    invoice_id = f"PV{int(time.time())}"
    
    invoice = f"""
🧾 **PAYMENT INVOICE** 🧾
{'═' * 30}

📋 **Invoice ID:** `{invoice_id}`
👤 **Name:** *{name}*
💰 **Amount:** **{format_currency(amount)}**
📅 **Date:** {payment_time.strftime('%d %B %Y')}
🕐 **Time:** {payment_time.strftime('%I:%M %p')}
✅ **Status:** **VERIFIED SUCCESSFULLY**

{'═' * 30}
🔐 *Verified by PayVeri System*
🤖 *Automated Payment Verification*

Thank you for your payment! 💚
"""
    return invoice

# === Welcome + Collect Amount ===
def start(update: Update, context: CallbackContext):
    # Store message ID for deletion
    context.user_data["last_message_id"] = update.message.message_id
    
    message = update.message.reply_text(
        "🎉 **Welcome to PayVeri!**\n\n"
        "💸 *Instant Payment Verification System*\n\n"
        "💰 **How much would you like to pay?**\n"
        "_Please enter the amount in ₹_",
        parse_mode='Markdown'
    )
    context.user_data["bot_message_id"] = message.message_id
    return ASK_AMOUNT

# === Collect Name ===
def ask_name(update: Update, context: CallbackContext):
    # Delete previous messages
    if "last_message_id" in context.user_data:
        delete_message_safe(context, update.message.chat_id, context.user_data["last_message_id"])
    if "bot_message_id" in context.user_data:
        delete_message_safe(context, update.message.chat_id, context.user_data["bot_message_id"])
    
    try:
        amount = float(update.message.text.strip())
        context.user_data["amount"] = amount
        context.user_data["last_message_id"] = update.message.message_id
        
        message = update.message.reply_text(
            f"💰 **Amount:** {format_currency(amount)}\n\n"
            "👤 **Please enter your full name**\n"
            "_Enter your name exactly as registered in your UPI app_",
            parse_mode='Markdown'
        )
        context.user_data["bot_message_id"] = message.message_id
        return ASK_NAME
    except ValueError:
        context.user_data["last_message_id"] = update.message.message_id
        message = update.message.reply_text(
            "❌ **Invalid amount!**\n\n"
            "💰 **Please enter a valid amount in numbers**\n"
            "_Example: 100 or 250.50_",
            parse_mode='Markdown'
        )
        context.user_data["bot_message_id"] = message.message_id
        return ASK_AMOUNT

# === Display QR and Start Verifying ===
def show_qr_and_verify(update: Update, context: CallbackContext):
    # Delete previous messages
    if "last_message_id" in context.user_data:
        delete_message_safe(context, update.message.chat_id, context.user_data["last_message_id"])
    if "bot_message_id" in context.user_data:
        delete_message_safe(context, update.message.chat_id, context.user_data["bot_message_id"])
    
    context.user_data["name"] = update.message.text.strip()
    context.user_data["last_message_id"] = update.message.message_id
    
    amount = context.user_data["amount"]
    name = context.user_data["name"]

    qr_path = "qrphoto.jpg"  # Should exist in Railway project folder
    upi_id = "9351044618@mbk"

    qr_message = update.message.reply_photo(
        open(qr_path, 'rb'), 
        caption=f"📱 **SCAN QR CODE TO PAY** 📱\n"
                f"{'═' * 25}\n\n"
                f"👤 **Payee:** *{name}*\n"
                f"💰 **Amount:** **{format_currency(amount)}**\n"
                f"🆔 **UPI ID:** `{upi_id}`\n\n"
                f"⚡ *Payment verification is automatic!*\n"
                f"🔄 *Please wait after payment...*",
        parse_mode='Markdown'
    )
    
    context.user_data["qr_message_id"] = qr_message.message_id
    context.user_data["payment_start_time"] = datetime.datetime.now()

    # Start monitoring thread
    threading.Thread(
        target=monitor_payment_and_reply, 
        args=(update, context, name, amount), 
        daemon=True
    ).start()

    return VERIFYING

# === Monitor Firebase for 5 min ===
def monitor_payment_and_reply(update, context, name, amount):
    user_id = update.message.chat_id
    
    # Send checking message
    checking_message = context.bot.send_message(
        chat_id=user_id, 
        text="🔍 **Checking for payment...**\n\n"
             "⏳ *Please wait while we verify your transaction*\n"
             "🔄 *This may take a few moments*",
        parse_mode='Markdown'
    )
    
    payment_start_time = context.user_data.get("payment_start_time", datetime.datetime.now())
    
    for attempt in range(30):  # Check every 10s for 5 mins
        time.sleep(10)
        
        try:
            payments = db.child("verified_payments").get().val()
            if payments:
                for payment_key, record in payments.items():
                    # Check if payment matches
                    if (check_name_match(name, record.get("name", "")) and 
                        abs(record.get("amount", 0) - amount) < 0.01):
                        
                        # Check if payment is within time window (last 10 minutes)
                        payment_timestamp = record.get("timestamp")
                        if payment_timestamp:
                            payment_time = datetime.datetime.fromtimestamp(payment_timestamp)
                            time_diff = abs((payment_time - payment_start_time).total_seconds())
                            
                            # Payment should be recent (within 10 minutes)
                            if time_diff <= 600:
                                # Mark payment as used to prevent reuse
                                db.child("verified_payments").child(payment_key).update({
                                    "used": True,
                                    "used_at": int(time.time()),
                                    "telegram_user": user_id
                                })
                                
                                # Delete checking message
                                delete_message_safe(context, user_id, checking_message.message_id)
                                
                                # Delete QR message
                                if "qr_message_id" in context.user_data:
                                    delete_message_safe(context, user_id, context.user_data["qr_message_id"])
                                
                                # Send success message with invoice
                                success_message = (
                                    "🎉 **PAYMENT VERIFIED!** 🎉\n\n"
                                    "✅ *Your payment has been successfully processed*\n"
                                    "💚 *Transaction completed instantly*\n\n"
                                    "📄 **Invoice will be sent below...**"
                                )
                                
                                context.bot.send_message(
                                    chat_id=user_id, 
                                    text=success_message,
                                    parse_mode='Markdown'
                                )
                                
                                # Send invoice
                                invoice_text = generate_invoice(name, amount, payment_time)
                                context.bot.send_message(
                                    chat_id=user_id, 
                                    text=invoice_text,
                                    parse_mode='Markdown'
                                )
                                
                                return
                        
        except Exception as e:
            print(f"Error checking payment: {e}")
            continue
    
    # If not found after 5 minutes
    delete_message_safe(context, user_id, checking_message.message_id)
    
    button = [[InlineKeyboardButton("🔄 Verify Again", callback_data=f"verify|{name}|{amount}")]]
    reply_markup = InlineKeyboardMarkup(button)
    
    context.bot.send_message(
        chat_id=user_id, 
        text="⏱️ **Payment verification timeout**\n\n"
             "❌ *Payment not found within 5 minutes*\n"
             "🔄 *Click below to check again*\n\n"
             "💡 *Make sure you entered the correct name and amount*",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# === Handle Retry Button ===
def verify_again(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    
    _, name, amount = query.data.split("|")
    amount = float(amount)
    
    # Update message to show checking
    query.edit_message_text(
        "🔍 **Checking for payment again...**\n\n"
        "⏳ *Please wait while we verify your transaction*",
        parse_mode='Markdown'
    )
    
    try:
        payments = db.child("verified_payments").get().val()
        if payments:
            for payment_key, record in payments.items():
                # Skip already used payments
                if record.get("used", False):
                    continue
                    
                if (check_name_match(name, record.get("name", "")) and 
                    abs(record.get("amount", 0) - amount) < 0.01):
                    
                    # Mark as used
                    db.child("verified_payments").child(payment_key).update({
                        "used": True,
                        "used_at": int(time.time()),
                        "telegram_user": query.message.chat_id
                    })
                    
                    payment_time = datetime.datetime.fromtimestamp(record.get("timestamp", time.time()))
                    
                    # Send success message
                    success_text = (
                        "🎉 **PAYMENT VERIFIED!** 🎉\n\n"
                        "✅ *Your payment has been successfully processed*\n"
                        "💚 *Transaction completed!*"
                    )
                    
                    query.edit_message_text(success_text, parse_mode='Markdown')
                    
                    # Send invoice
                    invoice_text = generate_invoice(name, amount, payment_time)
                    context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=invoice_text,
                        parse_mode='Markdown'
                    )
                    return
                    
    except Exception as e:
        print(f"Error in verify_again: {e}")
    
    # Still not found
    query.edit_message_text(
        "❌ **Payment still not found**\n\n"
        "🔍 *Please check:*\n"
        "• Payment amount matches exactly\n"
        "• Name matches your UPI registration\n"
        "• Payment was made recently\n\n"
        "💬 *Contact support if payment was made*",
        parse_mode='Markdown'
    )

# === Cancel Command ===
def cancel(update: Update, context: CallbackContext):
    # Clean up messages
    if "last_message_id" in context.user_data:
        delete_message_safe(context, update.message.chat_id, context.user_data["last_message_id"])
    if "bot_message_id" in context.user_data:
        delete_message_safe(context, update.message.chat_id, context.user_data["bot_message_id"])
    if "qr_message_id" in context.user_data:
        delete_message_safe(context, update.message.chat_id, context.user_data["qr_message_id"])
    
    update.message.reply_text(
        "❌ **Payment process cancelled**\n\n"
        "👋 *Thank you for using PayVeri!*\n"
        "🔄 *Type /start to begin again*",
        parse_mode='Markdown'
    )
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

    print("🤖 PayVeri Bot started successfully!")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
