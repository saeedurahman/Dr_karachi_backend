"""Constants and messaging templates for WhatsApp consent keywords and system responses."""
from __future__ import annotations

# Inbound keyword definitions (case-insensitive, exact match after stripping)
KEYWORD_STOP = {"STOP", "UNSUBSCRIBE"}
KEYWORD_STOP_ALL = {"STOP ALL", "STOPALL"}
KEYWORD_START = {"START", "UNSTOP"}

# Automated consent reply messages (sent inside 24-hour service window)
# English
STOP_CONFIRMATION_EN = (
    "You have been unsubscribed from promotional updates. You will continue to receive "
    "important appointment and order notifications. Reply START to re-subscribe."
)
STOP_ALL_CONFIRMATION_EN = (
    "You have unsubscribed from all WhatsApp messages from Karachi Lab & Clinic. "
    "Reply START at any time to resume notifications."
)
START_CONFIRMATION_EN = (
    "Welcome back! You are now subscribed to updates from Karachi Lab & Clinic. "
    "Reply STOP to opt out of promotional messages."
)

# Urdu (Roman / Urdu script)
STOP_CONFIRMATION_UR = (
    "Aap ko promotional messages se unsubscribe kar diya gaya hai. "
    "Appointment aur order ki notifications milti rahengi. Dubara shuru karne ke liye START likhein."
)
STOP_ALL_CONFIRMATION_UR = (
    "Aap ko tamam WhatsApp messages se unsubscribe kar diya gaya hai. "
    "Dubara shuru karne ke liye kisi bhi waqt START likhein."
)
START_CONFIRMATION_UR = (
    "Khush amdeed! Aap Karachi Lab & Clinic ki notifications ke liye subscribe ho gaye hain. "
    "Promotional messages band karne ke liye STOP likhein."
)

# ── Phase 6 Inbound Auto-Reply Menu & Handoff Constants ────────────

# Greeting triggers (case-insensitive, trimmed)
GREETING_TRIGGERS = {
    "hi",
    "hello",
    "salam",
    "assalamualaikum",
    "assalam o alaikum",
    "assalam-o-alaikum",
    "menu",
    "help",
    "السلام علیکم",
    "سلام",
    "مینو",
    "مدد",
}

# Keywords that reset a human_agent conversation back to bot mode
BOT_MODE_RESET_KEYWORDS = {"menu", "bot", "مینو", "بوٹ"}

# Row IDs for interactive list menu
ROW_BRANCHES = "menu_branches"
ROW_TIMINGS = "menu_timings"
ROW_BOOK_APPOINTMENT = "menu_book_appointment"
ROW_SPEAK_STAFF = "menu_speak_staff"

# Interactive List Menu Strings
MENU_HEADER_EN = "Karachi Lab & Clinic"
MENU_HEADER_UR = "کراچی لیب اور کلینک"

MENU_BODY_EN = (
    "Welcome to Karachi Lab, Pharmacy & Clinics! How can we assist you today? "
    "Please choose an option from the menu below:"
)
MENU_BODY_UR = (
    "کراچی لیب، فارمیسی اور کلینکس میں خوش آمدید! ہم آپ کی کیا مدد کر سکتے ہیں؟ "
    "برائے مہربانی نیچے دیے گئے مینو سے ایک آپشن منتخب کریں:"
)

MENU_BUTTON_TEXT_EN = "Main Menu"
MENU_BUTTON_TEXT_UR = "اہم اختیارات"

MENU_SECTION_TITLE_EN = "Services & Help"
MENU_SECTION_TITLE_UR = "خدمات اور معلومات"

MENU_FOOTER_EN = "Reply MENU anytime to reopen this list"
MENU_FOOTER_UR = "دوبارہ مینو دیکھنے کے لیے MENU لکھیں"

# List Menu Row Definitions
MENU_ROW_BRANCHES_TITLE_EN = "Branches & contacts"
MENU_ROW_BRANCHES_DESC_EN = "Find clinic & lab addresses and numbers"
MENU_ROW_BRANCHES_TITLE_UR = "برانچز اور رابطہ"
MENU_ROW_BRANCHES_DESC_UR = "کلینک اور لیب کے پتے اور فون نمبر"

MENU_ROW_TIMINGS_TITLE_EN = "Timings"
MENU_ROW_TIMINGS_DESC_EN = "Working hours & schedule"
MENU_ROW_TIMINGS_TITLE_UR = "اوقات کار"
MENU_ROW_TIMINGS_DESC_UR = "کلینک اور لیب کے کھلنے کے اوقات"

MENU_ROW_BOOK_APP_TITLE_EN = "Book appointment"
MENU_ROW_BOOK_APP_DESC_EN = "Schedule a doctor or test visit"
MENU_ROW_BOOK_APP_TITLE_UR = "اپوائنٹمنٹ بک کریں"
MENU_ROW_BOOK_APP_DESC_UR = "ڈاکٹر یا ٹیسٹ کی بکنگ کروائیں"

MENU_ROW_SPEAK_STAFF_TITLE_EN = "Speak with staff"
MENU_ROW_SPEAK_STAFF_DESC_EN = "Connect with our reception desk"
MENU_ROW_SPEAK_STAFF_TITLE_UR = "عملے سے بات کریں"
MENU_ROW_SPEAK_STAFF_DESC_UR = "ہمارے نمائندے سے رابطہ کریں"

# Row Replies
BRANCHES_HEADER_EN = "📍 *Our Branches & Contacts:*\n\n"
BRANCHES_HEADER_UR = "📍 *ہماری برانچز اور رابطہ:*\n\n"
BRANCHES_FOOTER_EN = "\n📞 Reply *MENU* to see options again."
BRANCHES_FOOTER_UR = "\n📞 دوبارہ اختیارات دیکھنے کے لیے *MENU* لکھیں۔"

DEFAULT_BRANCHES_INFO_EN = (
    "• Clifton Clinic: Block 5, Clifton, Karachi | Phone: 021-35800000\n"
    "• Gulshan Branch: Block 13, Gulshan-e-Iqbal, Karachi | Phone: 021-34567890"
)
DEFAULT_BRANCHES_INFO_UR = (
    "• کلفٹن کلینک: بلاک 5، کلفٹن، کراچی | فون: 021-35800000\n"
    "• گلشن برانچ: بلاک 13، گلشن اقبال، کراچی | فون: 021-34567890"
)

TIMINGS_HEADER_EN = "⏰ *Clinic & Lab Working Hours:*\n\n"
TIMINGS_HEADER_UR = "⏰ *کلینک اور لیب کے اوقات کار:*\n\n"

DEFAULT_CLINIC_TIMINGS_EN = (
    "• Monday – Saturday: 09:00 AM – 09:00 PM\n"
    "• Sunday: 10:00 AM – 06:00 PM\n"
    "• Emergency & Home Sample Collection: 24/7 Available"
)
DEFAULT_CLINIC_TIMINGS_UR = (
    "• پیر تا ہفتہ: صبح 09:00 تا رات 09:00\n"
    "• اتوار: صبح 10:00 تا شام 06:00\n"
    "• ایمرجنسی اور ہوم سیمپل کلیکشن: 24 گھنٹے دستیاب"
)

BOOK_APPOINTMENT_TEXT_EN = (
    "📅 *Book an Appointment*\n\n"
    "You can easily schedule a doctor consultation or lab test through our online portal:\n"
    "{appointment_url}\n\n"
    "Reply *MENU* to view other options."
)
BOOK_APPOINTMENT_TEXT_UR = (
    "📅 *اپوائنٹمنٹ بک کریں*\n\n"
    "آپ ہمارے آن لائن پورٹل کے ذریعے ڈاکٹر یا لیب ٹیسٹ کی بکنگ کروا سکتے ہیں:\n"
    "{appointment_url}\n\n"
    "دوبارہ مینو دیکھنے کے لیے *MENU* لکھیں۔"
)

SPEAK_WITH_STAFF_TEXT_EN = (
    "👩‍⚕️ *Connected with Support Team*\n\n"
    "Our clinic front desk has been notified. A representative will contact you via message "
    "or call shortly during working hours (09:00 AM – 09:00 PM).\n\n"
    "Reply *MENU* or *BOT* at any time to return to the automated assistant."
)
SPEAK_WITH_STAFF_TEXT_UR = (
    "👩‍⚕️ *ہمارے عملے کو مطلع کر دیا گیا ہے*\n\n"
    "ہمارا نمائندہ دفتری اوقات (صبح 09:00 تا رات 09:00) میں جلد آپ سے رابطہ کرے گا۔\n\n"
    "کسی بھی وقت خودکار اسسٹنٹ پر واپس آنے کے لیے *MENU* یا *BOT* لکھیں۔"
)

FALLBACK_TEXT_EN = "Reply MENU to see options."
FALLBACK_TEXT_UR = "اختیارات دیکھنے کے لیے MENU لکھ کر بھیجیں۔"

