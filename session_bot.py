import asyncio
import logging
import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "ضع_توكن_البوت_هنا"

# ─── Flask Server لـ UptimeRobot ─────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "✅ بوت استخراج الجلسة يعمل!", 200

@flask_app.route("/ping")
def ping():
    return "pong", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
# ─────────────────────────────────────────────────────────────────────────────

ASK_API_ID, ASK_API_HASH, ASK_PHONE, ASK_CODE, ASK_2FA = range(5)

telethon_clients = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 مرحباً!\n\n"
        "هذا البوت يستخرج لك الـ Session String الخاص بحسابك.\n\n"
        "📌 احصل على بيانات API من: my.telegram.org\n\n"
        "أرسل الـ API ID:"
    )
    return ASK_API_ID


async def ask_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ API ID يجب أن يكون رقماً فقط. أعد الإرسال:")
        return ASK_API_ID

    context.user_data["api_id"] = int(text)
    await update.message.reply_text("✅ تم.\n\nأرسل الآن الـ API Hash:")
    return ASK_API_HASH


async def ask_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["api_hash"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ تم.\n\n"
        "أرسل الآن رقم هاتفك مع رمز الدولة:\n"
        "مثال: `+9647801234567`",
        parse_mode="Markdown"
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.text.strip()
    api_id = context.user_data["api_id"]
    api_hash = context.user_data["api_hash"]

    await update.message.reply_text("⏳ جاري الإتصال وإرسال رمز التحقق...")

    try:
        client = TelegramClient(
            StringSession(),
            api_id,
            api_hash,
            device_model="Desktop",
            system_version="Windows 10",
            app_version="4.8.1",
            lang_code="ar",
        )
        await client.connect()
        sent = await client.send_code_request(phone)
        context.user_data["phone"] = phone
        context.user_data["phone_code_hash"] = sent.phone_code_hash
        telethon_clients[user_id] = client

        await update.message.reply_text(
            "✅ تم إرسال رمز التحقق إلى تيليغرام.\n\n"
            "أرسل الرمز مع وضع شارطة ( - ) بين كل رقم:\n"
            "مثال: `2-3-5-5-7`",
            parse_mode="Markdown"
        )
        return ASK_CODE

    except Exception as e:
        await update.message.reply_text(
            f"❌ حدث خطأ: {str(e)}\n\n"
            "تأكد من صحة البيانات وأرسل /start للمحاولة مجدداً."
        )
        return ConversationHandler.END


async def ask_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    code = update.message.text.strip().replace("-", "").replace(" ", "")
    client = telethon_clients.get(user_id)

    if not client:
        await update.message.reply_text("❌ انتهت الجلسة. أرسل /start للبدء من جديد.")
        return ConversationHandler.END

    try:
        await client.sign_in(
            phone=context.user_data["phone"],
            code=code,
            phone_code_hash=context.user_data["phone_code_hash"],
        )
        return await finish_session(update, context, client, user_id)

    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ الرمز غير صحيح. أرسل الرمز مجدداً:")
        return ASK_CODE

    except SessionPasswordNeededError:
        await update.message.reply_text(
            "🔒 حسابك محمي بكلمة مرور ثنائية (2FA).\n\n"
            "أرسل كلمة المرور:"
        )
        return ASK_2FA

    except Exception as e:
        await update.message.reply_text(
            f"❌ حدث خطأ: {str(e)}\n\n"
            "أرسل /start للمحاولة مجدداً."
        )
        telethon_clients.pop(user_id, None)
        return ConversationHandler.END


async def ask_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    password = update.message.text.strip()
    client = telethon_clients.get(user_id)

    if not client:
        await update.message.reply_text("❌ انتهت الجلسة. أرسل /start للبدء من جديد.")
        return ConversationHandler.END

    try:
        await client.sign_in(password=password)
        return await finish_session(update, context, client, user_id)

    except Exception as e:
        await update.message.reply_text(
            f"❌ كلمة المرور غير صحيحة: {str(e)}\n\n"
            "أرسل كلمة المرور مجدداً:"
        )
        return ASK_2FA


async def finish_session(update, context, client, user_id):
    try:
        me = await client.get_me()
        session_string = client.session.save()
        await client.disconnect()
        telethon_clients.pop(user_id, None)

        name = me.first_name or ""
        if me.last_name:
            name += f" {me.last_name}"
        username_text = f"@{me.username}" if me.username else "لا يوجد يوزرنيم"

        await update.message.reply_text(
            f"✅ تم استخراج الجلسة بنجاح!\n\n"
            f"👤 الحساب: {name} ({username_text})\n\n"
            f"🔑 *Session String الخاص بك:*\n\n"
            f"`{session_string}`\n\n"
            f"📋 *انسخ الجلسة أعلاه وأضفها في البوت الرئيسي عبر زر:*\n"
            f"🔑 إضافة جلسة (Session)",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ حدث خطأ أثناء استخراج الجلسة: {str(e)}\n\n"
            "أرسل /start للمحاولة مجدداً."
        )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client = telethon_clients.pop(user_id, None)
    if client:
        try:
            await client.disconnect()
        except:
            pass
    context.user_data.clear()
    await update.message.reply_text("❌ تم الإلغاء. أرسل /start للبدء من جديد.")
    return ConversationHandler.END


def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🌐 Flask server started for UptimeRobot")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_api_id)],
            ASK_API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_api_hash)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            ASK_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_code)],
            ASK_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_2fa)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )

    app.add_handler(conv)

    print("✅ بوت استخراج الجلسة يعمل الآن...")
    app.run_polling()


if __name__ == "__main__":
    main()
