import re
import json
import time
import datetime
import threading
import uuid
import pyrebase
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler, ConversationHandler
from config import (
    PAYMENT_REQUEST_TIMEOUT_SECONDS, PAYMENT_AUTO_VERIFY_WINDOW_SECONDS, PAYMENT_RETRY_LIMIT,
    PAYMENT_RETRY_WINDOW_SECONDS, AMOUNT_TOLERANCE, QR_TIME_LEFT_MESSAGE, HELP_MESSAGE,
    COUNTDOWN_UPDATE_INTERVAL_SECONDS, ADMIN_CHAT_ID, ADMIN_BOT_ONLINE_MESSAGE,
    ADMIN_BOT_OFFLINE_MESSAGE, ADMIN_ERROR_MESSAGE, ADMIN_STATUS_MESSAGE, 
    ADMIN_LIVE_UPTIME_MESSAGE, LIVE_UPTIME_UPDATE_INTERVAL
)
import difflib

# === CONFIG ===
TELEGRAM_TOKEN = "8139748151:AAEOVSiq9tDt8DANm1Gji0nFt19FHqugAfQ"
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
user_request_time = {}  # Track when QR was sent to user
user_rate_limit = {}  # user_id: [timestamps]

# === ADMIN & LOGGING GLOBALS ===
BOT_START_TIME = None
LIVE_UPTIME_MESSAGE_ID = None  # Store the message ID of the live uptime message

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
    """Check if names match (flexible matching - first name should match, allow minor typos)"""
    user_name = user_name.lower().strip()
    firebase_name = firebase_name.lower().strip()
    
    # Split names into words
    user_words = user_name.split()
    firebase_words = firebase_name.split()
    
    # If either name is empty, return False
    if not user_words or not firebase_words:
        return False
    
    # Use difflib to allow for minor typos in first name
    first_user = user_words[0]
    first_firebase = firebase_words[0]
    similarity = difflib.SequenceMatcher(None, first_user, first_firebase).ratio()
    if similarity > 0.8:
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
    """Delete all payment_requests and verified_payments older than 1 hour."""
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            # Clean up payment_requests
            payment_reqs = db.child("payment_requests").get().val()
            if payment_reqs:
                for user_id, data in payment_reqs.items():
                    if "timestamp" not in data:
                        continue
                    try:
                        payment_time = datetime.datetime.fromisoformat(data["timestamp"].replace('Z', '+00:00'))
                        if payment_time.tzinfo is None:
                            payment_time = payment_time.replace(tzinfo=datetime.timezone.utc)
                        if (now - payment_time).total_seconds() > 3600:
                            db.child("payment_requests").child(str(user_id)).remove()
                    except Exception:
                        db.child("payment_requests").child(str(user_id)).remove()
            # Clean up verified_payments
            verified = db.child("verified_payments").get().val()
            if verified:
                for key, data in verified.items():
                    # Use 'timestamp' if available, otherwise skip
                    if "timestamp" not in data:
                        continue
                    try:
                        payment_time = datetime.datetime.fromisoformat(data["timestamp"].replace('Z', '+00:00'))
                        if payment_time.tzinfo is None:
                            payment_time = payment_time.replace(tzinfo=datetime.timezone.utc)
                        if (now - payment_time).total_seconds() > 3600:
                            db.child("verified_payments").child(key).remove()
                    except Exception:
                        db.child("verified_payments").child(key).remove()
        except Exception as e:
            print(f"[auto_cleanup_unclaimed_payments error] {e}")
        time.sleep(600)

# === INVOICE GENERATOR ===
def generate_invoice(user):
    invoice_id = uuid.uuid4().hex[:8].upper()
    date_time = datetime.datetime.now()
    formatted_date = date_time.strftime("%d %B %Y")
    formatted_time = date_time.strftime("%I:%M %p")
    
    return (
        f"🎉 *PAYMENT SUCCESSFUL*\n\n"
        f"📋 *PAYMENT INVOICE*\n"
        f"👤 *Name:* `{user['name']}`\n"
        f"💰 *Amount:* `₹{user['amount']:.2f}`\n"
        f"📅 *Date:* {formatted_date}\n"
        f"🕐 *Time:* {formatted_time}\n"
        f"🆔 *ID:* `{invoice_id}`\n\n"
        f"✅ *Payment Was Verified By @paypilotsbot*\n"
        f"💡 **Keep this invoice for records**"
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
        "🚀 *Welcome to PayPilots!*\n"
        "**This is a Private Telegram bot that securely confirms payments in realtime, insuring fast transactions for the clients of @curiositymind**\n\n"
       "*💡Lets Start! ✅*\n"
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
        "💰 *Enter the **amount** to pay (₹)*:\n"
        "💡 **Ex: 1 or 250.50**",
        parse_mode="Markdown"
    )
    store_message_id(user_id, msg)
    return ASK_AMOUNT

def ask_amount(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    # Rate limiting: max 5 requests in 10 minutes
    now = time.time()
    timestamps = user_rate_limit.get(user_id, [])
    # Remove timestamps older than PAYMENT_RETRY_WINDOW_SECONDS
    timestamps = [t for t in timestamps if now - t < PAYMENT_RETRY_WINDOW_SECONDS]
    if len(timestamps) >= PAYMENT_RETRY_LIMIT:
        msg = update.message.reply_text(
            f"🚫 *Rate Limit Exceeded!*\n\nYou can only create {PAYMENT_RETRY_LIMIT} payment requests every {PAYMENT_RETRY_WINDOW_SECONDS//60} minutes. Please wait and try again.",
            parse_mode="Markdown"
        )
        store_message_id(user_id, msg)
        return ConversationHandler.END
    timestamps.append(now)
    user_rate_limit[user_id] = timestamps

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

    # Show time left in QR message
    minutes = PAYMENT_AUTO_VERIFY_WINDOW_SECONDS // 60
    seconds = PAYMENT_AUTO_VERIFY_WINDOW_SECONDS % 60
    time_left_msg = QR_TIME_LEFT_MESSAGE.format(minutes=minutes, seconds=seconds)

    msg = context.bot.send_photo(
        chat_id=user_id,
        photo=open(QR_IMAGE_PATH, 'rb'),
        caption=(
            f"📱 **SCAN QR TO PAY**\n\n"
            f"👤 **Name:** {user_inputs[user_id]['name']}\n"
            f"💰 **Amount:** ₹{amount:.2f}\n"
            f"🏦 **UPI:** `{UPI_ID}`\n\n"
            f"🔍 *Monitoring your payment...*\n"
            f"⏳ {time_left_msg}"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Payment", callback_data="cancel_payment")]
        ])
    )
    store_message_id(user_id, msg)
    
    # Mark that QR code has been sent to this user
    user_qr_sent[user_id] = True
    user_request_time[user_id] = time.time()  # Track request time (now)

    # Store payment request in Firebase
    db.child("payment_requests").child(str(user_id)).set({
        "name": user_inputs[user_id]["name"],
        "amount": amount,
        "timestamp": str(datetime.datetime.now())
    })

    # Start live countdown updater
    context.job_queue.run_repeating(
        update_qr_countdown,
        interval=COUNTDOWN_UPDATE_INTERVAL_SECONDS,
        first=COUNTDOWN_UPDATE_INTERVAL_SECONDS,
        context={
            'user_id': user_id,
            'message_id': msg.message_id,
            'start_time': user_request_time[user_id]
        },
        name=f"qr_countdown_{user_id}"
    )

    context.job_queue.run_repeating(realtime_verify, interval=5, first=5, context=user_id, name=str(user_id))
    context.job_queue.run_once(stop_verification, 300, context=user_id, name=str(user_id) + "_timeout")
    return ConversationHandler.END

def realtime_verify(context: CallbackContext):
    user_id = context.job.context
    req_time = user_request_time.get(user_id)
    # Check Firebase for payment request timestamp
    payment_req = db.child("payment_requests").child(str(user_id)).get().val()
    if payment_req and "timestamp" in payment_req:
        try:
            payment_time = datetime.datetime.fromisoformat(payment_req["timestamp"].replace('Z', '+00:00'))
            if payment_time.tzinfo is None:
                payment_time = payment_time.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            # Only allow auto-verification for 5 minutes
            if (now - payment_time).total_seconds() > 300:
                context.job.schedule_removal()
                return
        except Exception as e:
            context.job.schedule_removal()
            return
    else:
        context.job.schedule_removal()
        return
    if req_time is None or (time.time() - req_time) > 3600:
        context.job.schedule_removal()
        return
    if user_verified.get(user_id):
        context.job.schedule_removal()
        return
    
    user = user_inputs.get(user_id, {})
    name = user.get("name", "")
    amount = user.get("amount", 0)
    verified = db.child("verified_payments").get().val()
    
    if verified:
        for key, data in verified.items():
            if names_match(name, data["name"]) and abs(data["amount"] - amount) <= AMOUNT_TOLERANCE:
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
                if user_id in user_request_time:
                    del user_request_time[user_id]
                # Delete payment request from Firebase
                db.child("payment_requests").child(str(user_id)).remove()
                return

def stop_verification(context: CallbackContext):
    user_id = context.job.context
    
    # Additional safety check: if user data was cleaned up, don't send timeout
    if user_id not in user_inputs:
        return
    
    # If request is older than 1 hour, do not send timeout or auto-verify, just clean up
    req_time = user_request_time.get(user_id)
    if req_time is None or (time.time() - req_time) > 3600:
        if user_id in user_inputs:
            del user_inputs[user_id]
        if user_id in user_verified:
            del user_verified[user_id]
        if user_id in user_qr_sent:
            del user_qr_sent[user_id]
        if user_id in user_request_time:
            del user_request_time[user_id]
        # Delete payment request from Firebase
        db.child("payment_requests").child(str(user_id)).remove()
        # Remove realtime_verify job for this user
        jobs = context.job_queue.get_jobs_by_name(str(user_id))
        for job in jobs:
            job.schedule_removal()
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
        keyboard = [[InlineKeyboardButton("🔄 Retry ", callback_data="verify_again")]]
        context.bot.send_message(
            chat_id=user_id,
            text="⏰ **Payment Session Timeout Reached**\n\n"
                 "❌ * Type /start To Pay Again*\n\n"
                 "🔄 *Click Retry to check again*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        # Remove realtime_verify job for this user
        jobs = context.job_queue.get_jobs_by_name(str(user_id))
        for job in jobs:
            job.schedule_removal()
        return

def send_restart_button(context: CallbackContext):
    user_id = context.job.context
    keyboard = [[InlineKeyboardButton("🔁 Verify New Payment", callback_data="pay_again")]]
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
        
        # Check if the request is older than 1 hour (from Firebase)
        payment_req = db.child("payment_requests").child(str(user_id)).get().val()
        if not payment_req or "timestamp" not in payment_req:
            context.bot.send_message(
                chat_id=user_id,
                text="⏰ **This payment session has expired.**\n\nPlease start a new payment request with /start.",
                parse_mode="Markdown"
            )
            # Clean up user data for this user
            if user_id in user_inputs:
                del user_inputs[user_id]
            if user_id in user_verified:
                del user_verified[user_id]
            if user_id in user_qr_sent:
                del user_qr_sent[user_id]
            if user_id in user_request_time:
                del user_request_time[user_id]
            db.child("payment_requests").child(str(user_id)).remove()
            return
        try:
            payment_time = datetime.datetime.fromisoformat(payment_req["timestamp"].replace('Z', '+00:00'))
            if payment_time.tzinfo is None:
                payment_time = payment_time.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            if (now - payment_time).total_seconds() > 3600:
                context.bot.send_message(
                    chat_id=user_id,
                    text="⏰ **This payment session has expired.**\n\nPlease start a new payment request with /start.",
                    parse_mode="Markdown"
                )
                if user_id in user_inputs:
                    del user_inputs[user_id]
                if user_id in user_verified:
                    del user_verified[user_id]
                if user_id in user_qr_sent:
                    del user_qr_sent[user_id]
                if user_id in user_request_time:
                    del user_request_time[user_id]
                db.child("payment_requests").child(str(user_id)).remove()
                return
        except Exception as e:
            context.bot.send_message(
                chat_id=user_id,
                text="⏰ **This payment session has expired.**\n\nPlease start a new payment request with /start.",
                parse_mode="Markdown"
            )
            if user_id in user_inputs:
                del user_inputs[user_id]
            if user_id in user_verified:
                del user_verified[user_id]
            if user_id in user_qr_sent:
                del user_qr_sent[user_id]
            if user_id in user_request_time:
                del user_request_time[user_id]
            db.child("payment_requests").child(str(user_id)).remove()
            return
        
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
                if names_match(name, data["name"]) and abs(data["amount"] - amount) <= AMOUNT_TOLERANCE:
                    user_verified[user_id] = True
                    payment_found = True
                    
                    context.bot.send_message(
                        chat_id=user_id, 
                        text="🎉 *Payment Recieved!*\n\n⚡ *Processing...*",
                        parse_mode="Markdown"
                    )
                    
                    time.sleep(2)
                    
                    context.bot.send_message(chat_id=user_id, text=generate_invoice(user), parse_mode="Markdown")
                    
                    db.child("verified_payments").child(key).remove()
                    context.job_queue.run_once(send_restart_button, 30, context=user_id)
                    
                    # Clean up user data after successful verification
                    if user_id in user_inputs:
                        del user_inputs[user_id]
                    if user_id in user_verified:
                        del user_verified[user_id]
                    if user_id in user_qr_sent:
                        del user_qr_sent[user_id]
                    if user_id in user_request_time:
                        del user_request_time[user_id]
                    # Delete payment request from Firebase
                    db.child("payment_requests").child(str(user_id)).remove()
                    break
        
        if not payment_found:
            context.bot.send_message(
                chat_id=user_id,
                text="❌ **Payment Not Found Again**\n\n"
                     "💡 *Make sure you have completed the payment*\n"
                     "🔄 * Or Click /start To Pay again*",
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
        if user_id in user_request_time:
            del user_request_time[user_id]
        
        # Delete old payment request from Firebase
        db.child("payment_requests").child(str(user_id)).remove()
        
        context.bot.send_message(
            chat_id=user_id, 
            text="🔄 *Starting New Verification*\n\n💫 *Type /start to begin*",
            parse_mode="Markdown"
        )

    elif query.data == "cancel_payment":
        # Clean up user data
        if user_id in user_inputs:
            del user_inputs[user_id]
        if user_id in user_verified:
            del user_verified[user_id]
        if user_id in user_qr_sent:
            del user_qr_sent[user_id]
        if user_id in user_request_time:
            del user_request_time[user_id]
        # Delete payment request from Firebase
        db.child("payment_requests").child(str(user_id)).remove()
        # Remove all jobs for this user
        jobs = context.job_queue.get_jobs_by_name(str(user_id))
        for job in jobs:
            job.schedule_removal()
        jobs = context.job_queue.get_jobs_by_name(str(user_id) + "_timeout")
        for job in jobs:
            job.schedule_removal()
        context.bot.send_message(
            chat_id=user_id,
            text="❌ *Payment Cancelled*\n\nTo *pay again*, type /start.",
            parse_mode="Markdown"
        )
        return

def cleanup_messages(user_id, context):
    cleanup_all_messages(user_id, context)

def help_command(update: Update, context: CallbackContext):
    update.message.reply_text(HELP_MESSAGE, parse_mode="Markdown")

# === ADMIN & LOGGING ===
def send_admin_message(context: CallbackContext, message: str):
    """Helper function to send a message to the admin."""
    try:
        context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=message,
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Failed to send message to admin: {e}")

def error_handler(update: object, context: CallbackContext) -> None:
    """Log the error and send a message to the admin."""
    print(f"Exception while handling an update: {context.error}")

    user_info = "N/A (Scheduled job or internal error)"
    if update and hasattr(update, 'effective_user') and update.effective_user:
        user_info = f"ID: {update.effective_user.id}, Name: {update.effective_user.full_name}"

    error_message = ADMIN_ERROR_MESSAGE.format(
        user=user_info,
        error=context.error
    )
    send_admin_message(context, error_message)

def get_uptime():
    """Calculates the bot's uptime in a human-readable format."""
    if BOT_START_TIME is None:
        return "N/A"
    delta = datetime.datetime.now() - BOT_START_TIME
    hours, remainder = divmod(delta.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"

def status_command(update: Update, context: CallbackContext):
    """Shows the bot's status and uptime, admin-only."""
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        update.message.reply_text("⛔ Sorry, this is an admin-only command.")
        return

    uptime = get_uptime()
    start_time_str = BOT_START_TIME.strftime("%Y-%m-%d %H:%M:%S") if BOT_START_TIME else "N/A"
    
    status_message = ADMIN_STATUS_MESSAGE.format(
        uptime=uptime,
        start_time=start_time_str
    )
    update.message.reply_text(status_message, parse_mode="Markdown")

def send_live_uptime_update(context: CallbackContext):
    """Send live uptime update to admin by editing a single message."""
    global LIVE_UPTIME_MESSAGE_ID
    try:
        uptime = get_uptime()
        current_time = datetime.datetime.now().strftime("%I:%M:%S %p")
        
        live_message = ADMIN_LIVE_UPTIME_MESSAGE.format(
            uptime=uptime,
            current_time=current_time
        )
        
        # If we don't have a message ID yet, send the first message
        if LIVE_UPTIME_MESSAGE_ID is None:
            msg = context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=live_message,
                parse_mode="Markdown"
            )
            LIVE_UPTIME_MESSAGE_ID = msg.message_id
        else:
            # Edit the existing message
            try:
                context.bot.edit_message_text(
                    chat_id=ADMIN_CHAT_ID,
                    message_id=LIVE_UPTIME_MESSAGE_ID,
                    text=live_message,
                    parse_mode="Markdown"
                )
            except Exception:
                # If editing fails (message too old), send a new one
                msg = context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=live_message,
                    parse_mode="Markdown"
                )
                LIVE_UPTIME_MESSAGE_ID = msg.message_id
    except Exception as e:
        print(f"Failed to send live uptime update: {e}")

def update_qr_countdown(context: CallbackContext):
    data = context.job.context
    user_id = data['user_id']
    message_id = data['message_id']
    start_time = data['start_time']
    chat_id = user_id
    
    # If payment is verified, cancelled, or timed out, stop updating
    if user_verified.get(user_id) or user_id not in user_qr_sent:
        context.job.schedule_removal()
        return
    elapsed = int(time.time() - start_time)
    remaining = PAYMENT_AUTO_VERIFY_WINDOW_SECONDS - elapsed
    if remaining <= 0:
        context.job.schedule_removal()
        return
    minutes = remaining // 60
    seconds = remaining % 60
    time_left_msg = QR_TIME_LEFT_MESSAGE.format(minutes=minutes, seconds=seconds)
    try:
        context.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=(
                f"📱 *SCAN QR TO PAY*\n\n"
                f"👤 *Name:* {user_inputs[user_id]['name']}\n"
                f"💰 *Amount:* ₹{user_inputs[user_id]['amount']:.2f}\n"
                f"🏦 *TO UPI:* `{UPI_ID}`\n\n"
                f"🔍 *Monitoring your payment...*\n"
                f"⏳ {time_left_msg}"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel Payment", callback_data="cancel_payment")]
            ])
        )
    except Exception:
        pass

# === MAIN ===
def main():
    global BOT_START_TIME
    BOT_START_TIME = datetime.datetime.now()

    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    
    # Send startup message to admin
    try:
        start_time_str = BOT_START_TIME.strftime("%Y-%m-%d %H:%M:%S")
        updater.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=ADMIN_BOT_ONLINE_MESSAGE.format(start_time=start_time_str),
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Could not send startup message to admin: {e}")

    dp = updater.dispatcher

    # Add error handler
    dp.add_error_handler(error_handler)

    # Start live uptime monitoring
    context.job_queue.run_repeating(
        send_live_uptime_update,
        interval=1,  # Update every second
        first=1,     # Start immediately
        context=updater.bot
    )

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
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("status", status_command))

    updater.start_polling()
    print("🤖 PayVery Bot is running...")

    try:
        updater.idle()
    finally:
        # Send shutdown message to admin
        uptime = get_uptime()
        last_seen = datetime.datetime.now().strftime("%I:%M:%S %p")
        try:
            updater.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=ADMIN_BOT_OFFLINE_MESSAGE.format(uptime=uptime, last_seen=last_seen),
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Could not send shutdown message to admin: {e}")
        print("🤖 PayVery Bot is shutting down.")

if __name__ == "__main__":
    main()
