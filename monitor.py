import os
import sys
import time
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from google import genai

# ==========================================
# CONFIGURATION SETTINGS
# ==========================================
# Your verified Telegram Channel ID
CHAT_ID = "-1004369470593"

# Pull access keys securely from GitHub Environments
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GH_PAT = os.getenv("GH_PAT")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")

# Local cache configuration to keep memory tiny
MEMORY_EXPIRY_HOURS = 6
processed_cache = {}

def clean_expired_cache():
    """Cleans up internal tracking logs to keep memory consumption near zero."""
    now = datetime.now()
    expired_keys = [k for k, timestamp in processed_cache.items() if now - timestamp > timedelta(hours=MEMORY_EXPIRY_HOURS)]
    for k in expired_keys:
        del processed_cache[k]

def fetch_watchlist_from_github():
    """Dynamically reads the stock tracking rules managed on your Streamlit Dashboard."""
    if not GH_PAT or not GITHUB_REPOSITORY:
        print("[Watchlist] GitHub secrets missing. Defaulting to empty tracking configs.")
        return {"mode": "all", "stocks": [], "keywords": []}
    
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/watchlist.json"
    headers = {
        "Authorization": f"token {GH_PAT}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            file_data = res.json()
            from base64 import b64decode
            content = b64decode(file_data["content"]).decode("utf-8")
            return json.loads(content)
    except Exception as e:
        print(f"[Watchlist] Fetch failed ({e}). Defaulting to All Stocks.")
    
    return {"mode": "all", "stocks": [], "keywords": []}

def analyze_with_gemini(headline, company):
    """Summarizes stock filings using the stable gemini-2.0-flash production model."""
    if not GEMINI_API_KEY:
        return None
        
    try:
        # Initialize Google's latest modern SDK Client
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = (
            f"Analyze this Indian Stock Exchange corporate announcement headline:\n"
            f"Company: {company}\n"
            f"Headline: {headline}\n\n"
            f"Provide a summary in exactly 2 bullet points:\n"
            f"1. What is happening (Core facts only).\n"
            f"2. Business Impact (Positive/Negative/Neutral and why in brief).\n"
            f"Be concise, plain text, no markdown styling, no bold characters (**), no stars."
        )
        
        # Call generation endpoint using correct new SDK syntax
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
        )
        
        if response and response.text:
            return response.text.strip()
    except Exception as e:
        print(f"[AI Error] Generation handshake failed: {e}")
    return None

def send_telegram_alert(company, headline, link, timestamp, ai_summary=None):
    """Formats and transmits structured alerts directly to your Telegram channel channel."""
    if not TELEGRAM_TOKEN:
        print("[Telegram] Token missing. Skipping relay notification.")
        return

    # Clean text to guarantee reliable rendering
    clean_headline = headline.replace("**", "").replace("*", "")
    
    message = f"📢 *NEW NSE FILING DETECTED*\n\n"
    message += f"🏢 *Company:* {company}\n"
    message += f"📋 *Event:* {clean_headline}\n"
    message += f"⏰ *Time:* {timestamp}\n\n"
    
    if ai_summary:
        clean_summary = ai_summary.replace("**", "").replace("*", "")
        message += f"🤖 *AI Summary & Business Impact:*\n{clean_summary}\n\n"
    else:
        message += f"⚠️ _AI analysis failed or returned empty. Showing raw detail._\n\n"
        
    message += f"🔗 [View Official Document]({link})"
    
    url = f"https://api.telegram.com/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print(f"[Relay Success] Dispatched notification for {company}")
        else:
            print(f"[Relay Failed] Telegram endpoint rejected payload: {res.text}")
    except Exception as e:
        print(f"[Relay Error] Network connectivity failure: {e}")

def trigger_workflow_handover():
    """Launches a clean background worker clone right before the GitHub runner timeouts."""
    if not GH_PAT or not GITHUB_REPOSITORY:
        print("[Handover] Security credentials missing. Cannot loop pipeline automations.")
        return
        
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/workflows/monitor.yml/dispatches"
    headers = {
        "Authorization": f"token {GH_PAT}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {"ref": "main"}
    
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code in [201, 204]:
            print("[Handover] Success! Spawned successor node worker loop.")
            sys.exit(0)
        else:
            print(f"[Handover] Server rejected execution request: {res.text}")
    except Exception as e:
        print(f"[Handover] Critical pipeline failure: {e}")

def parse_nse_feed(is_baseline=False):
    """Checks the exchange RSS stream every 10 seconds for corporate updates."""
    url = "https://nsearchives.nseindia.com/static/rss/corporate-announcements.xml"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/xml"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return
            
        root = ET.fromstring(res.content)
        watchlist = fetch_watchlist_from_github()
        
        mode = watchlist.get("mode", "all")
        tracked_stocks = [s.strip().upper() for s in watchlist.get("stocks", []) if s.strip()]
        tracked_keywords = [k.strip().lower() for k in watchlist.get("keywords", []) if k.strip()]
        
        # Walk through corporate items in chronological order
        for item in reversed(root.findall(".//item")):
            title = item.find("title").text or ""
            link = item.find("link").text or ""
            pub_date = item.find("pubDate").text or ""
            
            if " - " not in title:
                continue
                
            company_part, headline_part = title.split(" - ", 1)
            company = company_part.strip()
            headline = headline_part.strip()
            
            # Create unique fingerprint tracking signature
            unique_key = f"{company}_{pub_date}"
            
            if unique_key in processed_cache:
                continue
                
            # If this is the script's very first launch, bookmark the item and do not send an alert
            if is_baseline:
                processed_cache[unique_key] = datetime.now()
                continue
                
            # Filter checks based on dashboard preferences
            is_matched = False
            if mode == "all":
                is_matched = True
            else:
                # Custom filters
                symbol_match = company.upper() in tracked_stocks
                keyword_match = any(kw in headline.lower() for kw in tracked_keywords)
                
                if mode == "stocks" and symbol_match:
                    is_matched = True
                elif mode == "keywords" and keyword_match:
                    is_matched = True
                elif mode == "both" and (symbol_match or keyword_match):
                    is_matched = True
            
            # Save to internal memory log
            processed_cache[unique_key] = datetime.now()
            
            if is_matched:
                print(f"[Match Found] Processing {company}: {headline}")
                ai_summary = analyze_with_gemini(headline, company)
                send_telegram_alert(company, headline, link, pub_date, ai_summary)
                time.sleep(1) # Graceful pacing spacing
                
    except Exception as e:
        print(f"[Parser Error] Failed to scan feed stream: {e}")

def main():
    print("==================================================")
    print(f"STOCKS MONITOR BOOTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("==================================================")
    
    # Run historical safety net sync to prevent old spam
    print("[Sync] Building historical baseline cache memory...")
    parse_nse_feed(is_baseline=True)
    print(f"[Sync] Baseline complete. Indexed {len(processed_cache)} backlog items.")
    
    start_time = time.time()
    # 5 Hours 50 Minutes running window layout
    max_duration = 21000 
    
    loop_count = 0
    while time.time() - start_time < max_duration:
        loop_count += 1
        if loop_count % 30 == 0:
            clean_expired_cache()
            
        parse_nse_feed(is_baseline=False)
        time.sleep(10) # High-speed 10-second polling cycle
        
    print("[Cycle Complete] Approaching engine runtime limits. Handing over baton...")
    trigger_workflow_handover()

if __name__ == "__main__":
    main()
