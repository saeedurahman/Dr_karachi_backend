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
