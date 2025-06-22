import re
import json
import time
import datetime
import threading
import uuid
import pyrebase
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler, ConversationHandler

# === CONFIG ===
TELEGRAM_TOKEN = "7645994825:AAFd7MSE8RKI4a8USEaCdnQvkkxuYIMil2U"
FIREBASE_CONFIG = {
    "apiKey": "fake",
    "authDomain": "payvari.firebaseapp.com",
    "databaseURL": "https://payvari-default-rtdb.firebaseio.com/",
    "storageBucket": "payvari.appspot.com"
}
QR_IMAGE_PATH = "qrphoto.jpg"
UPI_ID = "9351044618@mbk"

firebase = pyrebase.initialize_app(FIREBASE_CONFIG)
db = firebase.database()

ASK_NAME, ASK_AMOUNT = range(2)
user_inputs = {}
user_messages = {}
user_verified = {}
user_last_attempt = {}
user_qr_sent = {}  # Track users who received QR code

# === MESSAGE CLEANUP ===
def cleanup_all_messages(user_id, context):
    if user_id in user_messages:
        for msg_id in user_messages[user_id]:
            try:
                context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                time.sleep(0.1)
            except Exception as e:
                pass
        user_messages[user_id] = []

def store_message_id(user_id, message):
    if user_id not in user_messages:
        user_messages[user_id] = []
    if hasattr(message, 'message_id'):
        user_messages[user_id].append(message.message_id)

# === PAYMENT PARSER ===
def parse_payment_sms(sms):
    match = re.search(r"received\s+Rs\.?\s*([\d.]+).*?from\s+(.+?)\.", sms, re.IGNORECASE)
    if match:
        amount = float(match.group(1))
        name = match.group(2).strip()
        date = datetime.datetime.now().strftime("%Y-%m-%d")
        return {"name": name, "amount": amount, "date": date}
    return None

# === NAME MATCHING HELPER ===
def names_match(user_name, firebase_name):
    """Check if names match (flexible matching - first name should match)"""
    user_name = user_name.lower().strip()
    firebase_name = firebase_name.lower().strip()
    
    # Split names into words
    user_words = user_name.split()
    firebase_words = firebase_name.split()
    
    # If either name is empty, return False
    if not user_words or not firebase_words:
        return False
    
    # Check if first name matches
    if user_words[0] == firebase_words[0]:
        return True
    
    # Also check exact match for backward compatibility
    if user_name == firebase_name:
        return True
    
    return False

# === MONITOR SMS ===
def monitor_sms():
    while True:
        try:
            sms_data = db.child("raw_sms").get().val()
            if sms_data:
                for key, value in sms_data.items():
                    msg = value.get("message", "")
                    parsed = parse_payment_sms(msg)
                    if parsed:
                        # Add timestamp when payment is first added
                        parsed["timestamp"] = str(datetime.datetime.now())
                        db.child("verified_payments").push(parsed)
                    db.child("raw_sms").child(key).remove()
        except Exception as e:
            print(f"[monitor_sms error] {e}")
        time.sleep(3)

# === AUTO CLEANUP UNCLAIMED PAYMENTS ===
def auto_cleanup_unclaimed_payments():
    """Automatically verify and delete unclaimed payments after 12 hours"""
    while True:
        try:
            verified = db.child("verified_payments").get().val()
            if verified:
                current_time = datetime.datetime.now()
                for key, data in verified.items():
                    # Check if payment has timestamp, if not add one
                    if "timestamp" not in data:
                        data["timestamp"] = str(current_time)
                        db.child("verified_payments").child(key).update({"timestamp": str(current_time)})
                        continue
                    
                    # Parse the timestamp
                    try:
                        payment_time = datetime.datetime.fromisoformat(data["timestamp"].replace('Z', '+00:00'))
                        # If timestamp is naive, assume local time
                        if payment_time.tzinfo is None:
                            payment_time = payment_time.replace(tzinfo=datetime.timezone.utc)
                        
                        # Calculate time difference
                        time_diff = current_time.replace(tzinfo=datetime.timezone.utc) - payment_time
                        
                        # If more than 12 hours have passed, auto-verify and delete
                        if time_diff.total_seconds() > 12 * 3600:  # 12 hours in seconds
                            print(f"[AUTO CLEANUP] Auto-verifying unclaimed payment: {data['name']} - ₹{data['amount']}")
                            
                            # Add to auto-verified payments for record keeping
                            db.child("auto_verified_payments").push({
                                "name": data["name"],
                                "amount": data["amount"],
                                "original_timestamp": data["timestamp"],
                                "auto_verified_at": str(current_time),
                                "status": "auto_verified_after_12h"
                            })
                            
                            # Delete from verified_payments
                            db.child("verified_payments").child(key).remove()
                            
                    except Exception as e:
                        print(f"[AUTO CLEANUP] Error processing payment {key}: {e}")
                        # If there's an error parsing timestamp, delete the payment anyway
                        db.child("verified_payments").child(key).remove()
                        
        except Exception as e:
            print(f"[auto_cleanup_unclaimed_payments error] {e}")
        
        # Check every hour
        time.sleep(3600)

# === INVOICE GENERATOR ===
def generate_invoice(user):
    invoice_id = uuid.uuid4().hex[:8].upper()
    date_time = datetime.datetime.now()
    formatted_date = date_time.strftime("%d %B %Y")
    formatted_time = date_time.strftime("%I:%M %p")
    
    return (
        f"🎉 **PAYMENT SUCCESSFUL**\n\n"
        f"**📋 INVOICE**\n"
        f"👤 **Name:** `{user['name']}`\n"
        f"💰 **Amount:** `₹{user['amount']:.2f}`\n"
        f"📅 **Date:** {formatted_date}\n"
        f"🕐 **Time:** {formatted_time}\n"
        f"🆔 **ID:** `{invoice_id}`\n\n"
        f"✅ *Payment Verified Successfully*\n"
        f"💡 *Keep this invoice for your records*"
    )

# === USER FLOW ===
def start(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    now = time.time()
    if now - user_last_attempt.get(user_id, 0) < 5:
        update.message.reply_text("⏳ Please wait before starting again.")
        return ConversationHandler.END
    
    user_last_attempt[user_id] = now
    user_messages[user_id] = []
    user_verified[user_id] = False
    
    store_message_id(user_id, update.message)
    
    msg = update.message.reply_text(
        "🚀 **Welcome to PayVery!**\n\n"
        "💫 *Quick 2-Step Payment Checkout *\n"
       
        "👤 Please enter your **full name**:",
        parse_mode="Markdown"
    )
    store_message_id(user_id, msg)
    return ASK_NAME

def ask_name(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    
    store_message_id(user_id, update.message)
    cleanup_all_messages(user_id, context)
    
    user_inputs[user_id] = {"name": update.message.text.strip()}
    
    msg = update.message.reply_text(
        f"👋 **Hello {user_inputs[user_id]['name']}!**\n\n"
        "💰 Enter the **amount** to pay (₹):\n"
        "💡 *Example: 1 or 250.50*",
        parse_mode="Markdown"
    )
    store_message_id(user_id, msg)
    return ASK_AMOUNT

def ask_amount(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    
    store_message_id(user_id, update.message)
    cleanup_all_messages(user_id, context)
    
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError("Amount must be positive")
        user_inputs[user_id]["amount"] = amount
    except:
        msg = update.message.reply_text(
            "❌ **Invalid Amount!**\n\n"
            "Enter a valid number:\n"
            "✅ *Example: 1 or 250.50*",
            parse_mode="Markdown"
        )
        store_message_id(user_id, msg)
        return ASK_AMOUNT

    msg = context.bot.send_photo(
        chat_id=user_id,
        photo=open(QR_IMAGE_PATH, 'rb'),
        caption=(
            f"📱 **SCAN QR TO PAY**\n\n"
            f"👤 **Name:** {user_inputs[user_id]['name']}\n"
            f"💰 **Amount:** ₹{amount:.2f}\n"
            f"🏦 **UPI:** `{UPI_ID}`\n\n"
            f"🔍 *Monitoring your payment...*\n"
           
        ),
        parse_mode="Markdown"
    )
    store_message_id(user_id, msg)
    
    # Mark that QR code has been sent to this user
    user_qr_sent[user_id] = True

    context.job_queue.run_repeating(realtime_verify, interval=5, first=5, context=user_id, name=str(user_id))
    context.job_queue.run_once(stop_verification, 300, context=user_id, name=str(user_id) + "_timeout")
    return ConversationHandler.END

def realtime_verify(context: CallbackContext):
    user_id = context.job.context
    if user_verified.get(user_id):
        context.job.schedule_removal()
        return
    
    user = user_inputs.get(user_id, {})
    name = user.get("name", "")
    amount = user.get("amount", 0)
    verified = db.child("verified_payments").get().val()
    
    if verified:
        for key, data in verified.items():
            if names_match(name, data["name"]) and abs(data["amount"] - amount) < 0.01:
                user_verified[user_id] = True
                
                cleanup_all_messages(user_id, context)
                
                success_msg = context.bot.send_message(
                    chat_id=user_id, 
                    text="🎉 **Payment Recieved!**\n\n⚡ *Processing...*",
                    parse_mode="Markdown"
                )
                
                time.sleep(2)
                
                try:
                    context.bot.delete_message(chat_id=user_id, message_id=success_msg.message_id)
                except:
                    pass
                
                context.bot.send_message(chat_id=user_id, text=generate_invoice(user), parse_mode="Markdown")
                
                db.child("verified_payments").child(key).remove()
                
                # Cancel both the verification job and timeout job
                context.job.schedule_removal()
                
                # Cancel the timeout job by removing it from job queue
                try:
                    context.job_queue.get_jobs_by_name(str(user_id) + "_timeout")[0].schedule_removal()
                except:
                    pass
                
                context.job_queue.run_once(send_restart_button, 30, context=user_id)
                
                # Clean up user data after successful verification
                if user_id in user_inputs:
                    del user_inputs[user_id]
                if user_id in user_verified:
                    del user_verified[user_id]
                if user_id in user_qr_sent:
                    del user_qr_sent[user_id]
                return

def stop_verification(context: CallbackContext):
    user_id = context.job.context
    
    # Additional safety check: if user data was cleaned up, don't send timeout
    if user_id not in user_inputs:
        return
        
    # Only send timeout message if user received QR code and payment wasn't verified
    if not user_verified.get(user_id) and user_qr_sent.get(user_id):
        cleanup_all_messages(user_id, context)
        
        db.child("failed_attempts").push({
            "name": user_inputs[user_id].get("name", ""),
            "amount": user_inputs[user_id].get("amount", 0),
            "user_id": user_id,
            "timestamp": str(datetime.datetime.now())
        })
        keyboard = [[InlineKeyboardButton("🔄 Try Again", callback_data="verify_again")]]
        context.bot.send_message(
            chat_id=user_id,
            text="⏰ **Payment Session Timeout Reached**\n\n"
                 "❌ * Type /start To Pay Again*\n\n"
                 "🔄 *Click to check again*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

def send_restart_button(context: CallbackContext):
    user_id = context.job.context
    keyboard = [[InlineKeyboardButton("🔁 Verify ", callback_data="pay_again")]]
    context.bot.send_message(
        chat_id=user_id,
        text="💡 **Want to Verify another payment?**\n\n🚀 *Click to start*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id
    query.answer()

    if query.data == "verify_again":
        cleanup_all_messages(user_id, context)
        
        checking_msg = context.bot.send_message(
            chat_id=user_id,
            text="🔍 **Re-checking payment...**\n\n⏳ *Please wait...*",
            parse_mode="Markdown"
        )
        
        # Reset verification status for re-checking
        user_verified[user_id] = False
        
        time.sleep(2)
        try:
            context.bot.delete_message(chat_id=user_id, message_id=checking_msg.message_id)
        except:
            pass
        
        # Re-check payment immediately
        user = user_inputs.get(user_id, {})
        name = user.get("name", "")
        amount = user.get("amount", 0)
        verified = db.child("verified_payments").get().val()
        
        payment_found = False
        if verified:
            for key, data in verified.items():
                if names_match(name, data["name"]) and abs(data["amount"] - amount) < 0.01:
                    user_verified[user_id] = True
                    payment_found = True
                    
                    context.bot.send_message(
                        chat_id=user_id, 
                        text="🎉 **Payment Recieved!**\n\n⚡ *Processing...*",
                        parse_mode="Markdown"
                    )
                    
                    time.sleep(2)
                    
                    context.bot.send_message(chat_id=user_id, text=generate_invoice(user), parse_mode="Markdown")
                    
                    db.child("verified_payments").child(key).remove()
                    context.job_queue.run_once(send_restart_button, 30, context=user_id)
                    break
        
        if not payment_found:
            context.bot.send_message(
                chat_id=user_id,
                text="❌ **Payment Not Found Again**\n\n"
                     "💡 *Make sure you have completed the payment*\n"
                     "🔄 *Click /start To Pay again*",
                parse_mode="Markdown"
            )

    elif query.data == "pay_again":
        cleanup_all_messages(user_id, context)
        
        # Clean up user data for fresh start
        if user_id in user_inputs:
            del user_inputs[user_id]
        if user_id in user_verified:
            del user_verified[user_id]
        if user_id in user_qr_sent:
            del user_qr_sent[user_id]
        
        context.bot.send_message(
            chat_id=user_id, 
            text="🔄 **Starting New Verification**\n\n💫 *Type /start to begin*",
            parse_mode="Markdown"
        )

def cleanup_messages(user_id, context):
    cleanup_all_messages(user_id, context)

# === MAIN ===
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    threading.Thread(target=monitor_sms, daemon=True).start()
    threading.Thread(target=auto_cleanup_unclaimed_payments, daemon=True).start()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("rstart", start)],
        states={
            ASK_NAME: [MessageHandler(Filters.text & ~Filters.command, ask_name)],
            ASK_AMOUNT: [MessageHandler(Filters.text & ~Filters.command, ask_amount)],
        },
        fallbacks=[]
    )

    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(button_handler))

    updater.start_polling()
    print("🤖 PayVery Bot is running...")
    updater.idle()

if __name__ == "__main__":
    main()
