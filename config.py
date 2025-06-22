# Timeouts and limits
PAYMENT_REQUEST_TIMEOUT_SECONDS = 3600  # 1 hour (for request/session expiry)
PAYMENT_AUTO_VERIFY_WINDOW_SECONDS = 300  # 5 minutes (auto-verification window)
PAYMENT_RETRY_LIMIT = 5  # Max requests per window
PAYMENT_RETRY_WINDOW_SECONDS = 600  # 10 minutes (rate limit window)
AMOUNT_TOLERANCE = 0.05  # INR (amount matching tolerance)
COUNTDOWN_UPDATE_INTERVAL_SECONDS = 1  # How often to update the countdown in the QR message (seconds)

# Messages
QR_TIME_LEFT_MESSAGE = "Time left: {minutes}:{seconds:02d}"
HELP_MESSAGE = (
    "ℹ️ *PayVery Help & FAQ*\n\n"
    "1. *How do I pay?*\n"
    "  - Type /start and follow the instructions.\n\n"
    "2. *How long is my payment session valid?*\n"
    "  - Each session is valid for 5 minutes. After that, you must start again.\n\n"
    "3. *Can I retry if payment is not detected?*\n"
    "  - Yes, use the Retry button within 1 hour.\n\n"
    "4. *How do I cancel a payment?*\n"
    "  - Use the Cancel Payment button in the QR message.\n\n"
    "5. *How many requests can I make?*\n"
    "  - You can make up to 5 payment requests every 10 minutes.\n\n"
    "6. *What if my name is slightly different?*\n"
    "  - The bot matches your first name and allows for minor typos.\n\n"
    "7. *Need more help?*\n"
    "  - Contact support or type /start to begin again."
)

# Admin and Logging
ADMIN_CHAT_ID = "1981828128"
ADMIN_BOT_ONLINE_MESSAGE = "✅ *PayPilotsBot is LIVE!*\nStart Time: {start_time}"
ADMIN_BOT_OFFLINE_MESSAGE = "❌ *PayPilotsBot is DOWN!*\nTotal Uptime: {uptime}\nLast seen: {last_seen}"
ADMIN_ERROR_MESSAGE = "🚨 *An error occurred!*\n\n*User:* {user}\n*Error:* ```{error}```"
ADMIN_STATUS_MESSAGE = "⚙️ *Bot Status*\n\n*Uptime:* {uptime}\n*Running since:* {start_time}"
ADMIN_LIVE_UPTIME_MESSAGE = "🟢 *PayPilotsBot is LIVE*\n\n⏰ *Uptime:* {uptime}\n🕐 *Current Time:* {current_time}"
LIVE_UPTIME_UPDATE_INTERVAL = 300  # Send live update every 5 minutes (300 seconds) 
