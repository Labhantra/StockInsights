import os
import time
import requests
import xml.etree.ElementTree as ET
import pandas as pd
from google import genai

# --- SECURE CREDENTIAL ARRAYS ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = "-1003970817235"
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
    """Dynamically returns a client using the currently active operational key."""
    global CURRENT_KEY_INDEX
    if not GEMINI_KEYS:
        raise ValueError("CRITICAL: No Gemini API keys found in environment configuration!")
    return genai.Client(api_key=GEMINI_KEYS[CURRENT_KEY_INDEX])

def rotate_key():
    """Rotates to the next free API project key when a quota limit is encountered."""
    global CURRENT_KEY_INDEX
    if len(GEMINI_KEYS) > 1:
        CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(GEMINI_KEYS)
        print(f"[🔄] Quota limit triggered. Rotated to API Key Index: {CURRENT_KEY_INDEX}")
    else:
        print("[⚠️] Warning: Quota hit but no secondary backup keys are available to rotate.")

def load_processed_keys():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_processed_key(key):
    with open(CACHE_FILE, "a") as f:
        f.write(f"{key}\n")

def get_all_nse_tickers():
    csv_url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(csv_url, headers=headers, timeout=12)
        if response.status_code == 200:
            lines = response.content.decode('utf-8').splitlines()
            df = pd.read_csv(requests.compat.StringIO('\n'.join(lines)))
            df.columns = df.columns.str.strip()
            return set(t.upper() for t in df['SYMBOL'].dropna().astype(str).str.strip().tolist())
    except Exception:
        pass
    return {"SUZLON", "POWERGRID", "HCLTECH", "RELIANCE", "SBIN"}

def analyze_announcement_with_ai(headline, details):
    prompt = f"Headline: {headline}\nDetails: {details}\nProvide output as: Category | Sentiment | Bulleted Summary"
    
    # Try up to 3 times to rotate through keys if a quota exhaustion error occurs
    for _ in range(min(3, len(GEMINI_KEYS))):
        try:
            ai = get_ai_client()
            response = ai.models.generate_content(model='gemini-2.0-flash-lite', contents=prompt)
            return response.text.strip()
        except Exception as e:
            # Check for standard Google quota/rate limit error strings (e.g., ResourceExhausted, 429)
            if "429" in str(e) or "quota" in str(e).lower() or "exhausted" in str(e).lower():
                rotate_key()
                continue
            return f"Business Update | ⚪ Neutral | - Summary bypass note: {e}"
    return "Business Update | ⚪ Neutral | - All active keys exhausted for this cycle frame."

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception:
        pass

if __name__ == "__main__":
    print(f"[🚀] Launching Unlimited Fail-Safe Engine Pool with {len(GEMINI_KEYS)} keys.")
    processed_history = load_processed_keys()
    tickers = get_all_nse_tickers()
    
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(RSS_URL, headers=headers, timeout=8)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            
            for item in reversed(root.findall('.//item')):
                title = item.find('title').text or "UNKNOWN"
                pub_date = item.find('pubDate').text or "N/A"
                link_url = item.find('link').text or ""
                desc = item.find('description').text or ""
                
                unique_key = f"{title}_{pub_date}"
                if unique_key in processed_history:
                    continue
                    
                matched = False
                title_up, link_up = title.upper(), link_url.upper()
                for sym in tickers:
                    if sym in title_up or sym in link_up:
                        matched = True
                        break
                        
                if matched:
                    is_routine = any(kw in title_up for kw in ROUTINE_KEYWORDS)
                    is_high_value = any(kw in title_up for kw in HIGH_VALUE_KEYWORDS)
                    
                    if is_routine and not is_high_value:
                        msg = (
                            f"⚡ *Company:* {title}\n\n"
                            f"📄 *Category:* Routine Compliance | ⚪ Neutral\n\n"
                            f"📝 *Summary:* Standard administrative exchange filing.\n\n"
                            f"⏰ *Time:* {pub_date}\n"
                        )
                        if link_url:
                            msg += f"📁 *More:* [View Original Document]({link_url})"
                        send_telegram(msg)
                    else:
                        analysis = analyze_announcement_with_ai(title, desc)
                        parts = analysis.split('|')
                        if len(parts) >= 3:
                            cat, sent, summ = parts[0].strip(), parts[1].strip(), parts[2].strip()
                            msg = (
                                f"⚡ *Company:* {title}\n\n"
                                f"📄 *Category:* {cat} | {sent}\n\n"
                                f"📝 *AI Summary:*\n{summ}\n\n"
                                f"⏰ *Time:* {pub_date}\n"
                            )
                        else:
                            msg = f"⚡ *Company:* {title}\n\n📝 *Analysis:*\n{analysis}\n\n⏰ *Time:* {pub_date}\n"
                        
                        if link_url:
                            msg += f"📁 *More:* [View Original Document]({link_url})"
                        send_telegram(msg)
                    
                save_processed_key(unique_key)
    except Exception as main_err:
        print(f"[-] Loop processing note: {main_err}")
