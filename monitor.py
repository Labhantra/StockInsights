import os
import time
import json
import requests
import xml.etree.ElementTree as ET
from google import genai

# --- SYSTEM SETTINGS & SECRETS ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = "-1004369470593"  # Your target channel ID
RSS_URL = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Initialize Production-Grade Gemini Client
ai = genai.Client(api_key=GEMINI_API_KEY)

# Track memory inside local loop instance
sent_announcements = set()

def clean_html(text):
    """Escapes special HTML characters so they don't break Telegram's HTML parser."""
    if not text:
        return ""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def load_live_configs():
    """Reads the configuration settings edited from the mobile interface."""
    try:
        with open("watchlist.json", "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[⚠️] Configuration load failed, falling back to default rules: {e}")
        return {
            "tracking_mode": "All Stocks (Default)",
            "selected_watchlist": [],
            "high_value_keywords": ["FINANCIAL RESULTS", "DIVIDEND"],
            "routine_keywords": ["SHAREHOLDING PATTERN"]
        }

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=5)
        if res.status_code != 200:
            print(f"[❌] Telegram Server Rejected Message ({res.status_code}): {res.text}")
        else:
            return True
    except Exception as e:
        print(f"[-] Telegram dispatch network error: {e}")
    return False

def analyze_with_ai(headline, details):
    prompt = f"""
    Analyze the following Indian stock market filing:
    Headline: {headline}
    Details: {details}

    Format your output EXACTLY as: Category | Sentiment | Bulleted Summary
    Rules:
    1. Category: One from ["Business Update", "Routine", "Financial Results", "Credit Rating"]
    2. Sentiment: One from ["🟢 Positive", "Neutral", "🔴 Negative"]
    3. Summary: 2-3 short bullets starting with '-'. Clear facts only. No intro text.
    """
    try:
        # Utilizing the production standard gemini-2.0-flash model
        response = ai.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        text_out = response.text.strip()
        parts = text_out.split('|')
        if len(parts) >= 3:
            return parts[0].strip(), parts[1].strip(), parts[2].strip()
        return "Business Update", "Neutral", f"- {text_out}"
    except Exception as e:
        return "Routine", "Neutral", f"- Summary extraction issue: {e}"

def check_feed_cycle():
    configs = load_live_configs()
    tracking_mode = configs.get("tracking_mode", "All Stocks (Default)")
    watchlist = [s.upper() for s in configs.get("selected_watchlist", [])]
    
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(RSS_URL, headers=headers, timeout=8)
        if response.status_code != 200:
            return
        
        root = ET.fromstring(response.content)
        items = root.findall('.//item')
        
        # If this is the script's very first launch, sync state to prevent spamming old items
        if not sent_announcements:
            for item in items:
                title = item.find('title').text if item.find('title') is not None else ""
                pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
                sent_announcements.add(f"{title}_{pub_date}")
            print(f"[📥] Initial baseline sync complete. Watching for upcoming announcements...")
            return

        # Process from oldest to newest
        for item in reversed(items):
            title = item.find('title').text if item.find('title') is not None else "UNKNOWN"
            link = item.find('link').text if item.find('link') is not None else ""
            desc = item.find('description').text if item.find('description') is not None else ""
            pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
            
            unique_key = f"{title}_{pub_date}"
            if unique_key in sent_announcements:
                continue
                
            # Filter checks
            matched = False
            if tracking_mode == "All Stocks (Default)":
                matched = True
            else:
                for symbol in watchlist:
                    if symbol in title.upper() or symbol in link.upper():
                        matched = True
                        break
            
            if not matched:
                sent_announcements.add(unique_key)
                continue
                
            # Generate Alert
            category, sentiment, ai_summary = analyze_with_ai(title, desc)
            
            # Clean variables for HTML safety
            safe_title = clean_html(title)
            safe_category = clean_html(category)
            safe_sentiment = clean_html(sentiment)
            safe_summary = clean_html(ai_summary)
            safe_pub_date = clean_html(pub_date)

            message = (
                f"⚡ <b>Company:</b> {safe_title}\n\n"
                f"📄 <b>Category:</b> {safe_category} | {safe_sentiment}\n\n"
                f"📝 <b>AI Summary:</b>\n{safe_summary}\n\n"
                f"⏰ <b>Time:</b> {safe_pub_date}\n"
            )
            if link:
                message += f"📁 <b>Filing File:</b> <a href='{link}'>View Original Document</a>"
                
            if send_telegram_message(message):
                print(f"[📢] Dispatched instant flash alert for: {title}")
            
            sent_announcements.add(unique_key)
            
    except Exception as e:
        print(f"[⚠️] Error during check block: {e}")

if __name__ == "__main__":
    RUN_DURATION = 21000  
    CHECK_INTERVAL = 10     
    
    print("[🔥] Starting High-Speed Relay Engine execution environment...")
    start_time = time.time()
    
    while time.time() - start_time < RUN_DURATION:
        check_feed_cycle()
        time.sleep(CHECK_INTERVAL)
        
    print("[⏰] Execution window closing. Pinging GitHub API to execute handover runner...")
    
    pat_token = os.environ.get("GH_PAT")
    repo = os.environ.get("GITHUB_REPOSITORY")
    
    if pat_token and repo:
        url = f"https://api.github.com/repos/{repo}/actions/workflows/run.yml/dispatches"
        headers = {
            "Authorization": f"token {pat_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        try:
            res = requests.post(url, json={"ref": "main"}, headers=headers, timeout=10)
            if res.status_code == 204:
                print("[🚀] Handover chain succeeded. Next runner initialization initialized.")
            else:
                print(f"[❌] Handoff execution failed: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"[❌] Relay connection terminal failure: {e}")
            
    print("[🏁] Former worker loop teardown sequence complete.")
