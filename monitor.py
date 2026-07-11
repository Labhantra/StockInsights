import os
import time
import requests
import xml.etree.ElementTree as ET
from google import genai

# --- SECURE CREDENTIAL ARRAYS ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = "-5017305086"  # Updated with your verified group Chat ID
RAW_KEYS = os.environ.get("GEMINI_API_KEY", "")

# Automatically split the comma-separated string into a list of keys
GEMINI_KEYS = [k.strip() for k in RAW_KEYS.split(",") if k.strip()]
CURRENT_KEY_INDEX = 0
RSS_URL = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
CACHE_FILE = "processed_announcements.txt"

# --- LOCAL FILTERS ---
ROUTINE_KEYWORDS = [
    "SHAREHOLDING PATTERN", "LOSS OF SHARE CERTIFICATE", "DUPLICATE SHARE CERTIFICATE",
    "CLOSURE OF TRADING WINDOW", "COMPLIANCE CERTIFICATE", "ISIN", "NCLT UPDATE",
    "REGULATION 39", "REGULATION 7", "REGULATION 30", "REPLY TO CLARIFICATION"
]
HIGH_VALUE_KEYWORDS = [
    "FINANCIAL RESULTS", "EARNINGS", "DIVIDEND", "BONUS", "SPLIT", "ACQUISITION",
    "MERGER", "ORDER WON", "AWARD OF CONTRACT", "CAPACITY EXPANSION", "BOARD MEETING"
]

def get_ai_client():
    """Initializes the Gemini client using the currently active key index."""
    global CURRENT_KEY_INDEX
    if not GEMINI_KEYS:
        print("[❌] Critical: No Gemini API keys found in your GitHub secrets!")
        return None
    
    active_key = GEMINI_KEYS[CURRENT_KEY_INDEX]
    masked_key = f"{active_key[:6]}...{active_key[-4:]}" if len(active_key) > 10 else "Invalid Key"
    print(f"[🔑] Using API Key Index {CURRENT_KEY_INDEX} ({masked_key})")
    
    return genai.Client(api_key=active_key)

def rotate_key():
    """Rotates to the next available API key in the list."""
    global CURRENT_KEY_INDEX
    if len(GEMINI_KEYS) <= 1:
        print("[⚠️] Only one key available. Pausing to clear temporary rate limit...")
        time.sleep(15)
        return
    
    CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(GEMINI_KEYS)
    print(f"[🔄] Quota limit encountered. Rotating to Key Index: {CURRENT_KEY_INDEX}")
    time.sleep(2)

def send_telegram_message(text):
    """Sends a formatted message to your Telegram channel/group."""
    if not TELEGRAM_TOKEN:
        print("[❌] Telegram token missing.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[❌] Failed to transmit Telegram notification: {e}")

def load_cached_announcements():
    """Loads previously processed announcement links to prevent duplicate alerts."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def cache_announcement(link):
    """Appends a newly processed link to the local text storage file."""
    with open(CACHE_FILE, "a") as f:
        f.write(f"{link}\n")

def fetch_nse_feed():
    """Fetches and parses the real-time NSE RSS XML feed data."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        response = requests.get(RSS_URL, headers=headers, timeout=15)
        if response.status_code == 200:
            return ET.fromstring(response.content)
    except Exception as e:
        print(f"[❌] Error querying the National Stock Exchange feed endpoint: {e}")
    return None

def main():
    print("[🚀] Launching Fail-Safe StockInsights Engine Pool...")
    
    root = fetch_nse_feed()
    if root is None:
        print("[❌] Empty or invalid response from exchange server. Terminating run.")
        return
        
    cached_links = load_cached_announcements()
    items = root.findall(".//item")
    print(f"[📊] Discovered {len(items)} total live items on the exchange terminal feed.")
    
    # FREE TIER SAFETY NET: If the history cache file is empty (first run),
    # bookmark all current items as read so we don't blow your free daily limits.
    if not cached_links:
        print("[ℹ️] History log cache is clean. Pre-caching existing feed items to protect free tier credits...")
        for item in items:
            link = item.find("link").text if item.find("link") is not None else ""
            if link:
                cache_announcement(link)
        print("[✅] Complete current live feed bookmarked successfully! System will track new alerts on next cycle.")
        return

    consecutive_failures = 0
    max_allowed_failures = len(GEMINI_KEYS) * 2
    for item in reversed(items):
        link = item.find("link").text if item.find("link") is not None else ""
        if not link or link in cached_links:
            continue
            
        title = item.find("title").text if item.find("title") is not None else ""
        desc = item.find("description").text if item.find("description") is not None else ""
        upper_text = f"{title} {desc}".upper()
        
        is_high_value = any(k in upper_text for k in HIGH_VALUE_KEYWORDS)
        is_routine = any(k in upper_text for k in ROUTINE_KEYWORDS)
        
        if not (is_high_value or is_routine):
            continue
            
        print(f"[🎯] Matching Announcement Found: {title}")
        
        prompt = (
            f"Analyze this Indian Stock Market Corporate Announcement framework:\n\n"
            f"Heading: {title}\nDetails: {desc}\n\n"
            f"Provide a brief 3-bullet point summary focusing exclusively on commercial impact, "
            f"numerical figures, or structural transformations."
        )
        
        ai_success = False
        while not ai_success:
            if consecutive_failures >= max_allowed_failures:
                print("[🚨] All keys in the secret pool are exhausted. Sleeping for 15 seconds...")
                time.sleep(15)
                consecutive_failures = 0
                
            client = get_ai_client()
            if not client:
                break
                
            try:
                response = client.models.generate_content(
                    model='gemini-2.0-flash-lite', 
                    contents=prompt
                )
                summary = response.text
                
                header_icon = "🔥 HIGH PRIORITY ALERT" if is_high_value else "📋 ROUTINE ANNOUNCEMENT"
                tele_payload = f"*{header_icon}*\n\n*Company:* {title}\n\n*AI Summary:*\n{summary}\n\n🔗 [View Official Document]({link})"
                
                send_telegram_message(tele_payload)
                cache_announcement(link)
                
                ai_success = True
                consecutive_failures = 0
                
                # Mandatory 4-second delay to prevent hitting API ceiling
                print("[💤] Request complete. Cooling down for 4 seconds...")
                time.sleep(4)
                
            except Exception as e:
                print(f"[⚠️] API Error encountered: {e}")
                consecutive_failures += 1
                rotate_key()

if __name__ == "__main__":
    main()
