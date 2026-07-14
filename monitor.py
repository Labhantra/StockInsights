import os
import sys
import io
import time
import json
import base64
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from openai import OpenAI

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

# ==========================================
# SYSTEM SETTINGS & SECRETS
# ==========================================
CHAT_ID = "-1004369470593"
RSS_URL = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
GROQ_MODEL = "llama-3.1-8b-instant"  # 14,400 requests/day free tier
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GH_PAT = os.getenv("GH_PAT")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")

# Fetch all 3 keys from your updated environment variables
API_KEYS = [
    os.getenv("GROQ_API_KEY_1"),
    os.getenv("GROQ_API_KEY_2"),
    os.getenv("GROQ_API_KEY_3")
]
API_KEYS = [k for k in API_KEYS if k]

if not API_KEYS:
    print("[❌ CRITICAL] No Groq API keys found in environment variables!")
    sys.exit(1)

if not TELEGRAM_TOKEN:
    print("[❌ CRITICAL] TELEGRAM_TOKEN is missing!")
    sys.exit(1)

current_key_index = 0
MEMORY_EXPIRY_HOURS = 6
processed_cache = {}

# ==========================================
# DETERMINISTIC PRIORITY-TIER CLASSIFICATION
# Rule-based instead of AI-based, since NSE "Subject" text follows a
# fairly fixed vocabulary — far more reliable than asking a small model to judge.
# ==========================================
CATEGORY_RULES = [
    ("🔴 Critical", [
        "order win", "receiving of orders", "bagging", "contract award", "government contract",
        "export order", "order cancellation", "order termination", "termination of order",
        "financial results", "quarterly financial", "annual financial",
        "commercial production", "capacity expansion", "regulatory approval", "usfda",
        "acquisition", "merger", "amalgamation", "demerger", "corporate restructuring",
        "buyback", "promoter shareholding", "bulk deal", "block deal",
        "pledge creation", "pledge release", "pledge of shares",
        "factory shutdown", "fire", "accident", "insolvency", "nclt",
        "delisting", "relisting", "management guidance",
        "credit rating", "auditor resignation", "resignation of", "cessation",
        "cybersecurity incident"
    ]),
    ("🟠 Important", [
        "business update", "capex", "capital expenditure", "strategic partnership",
        "joint venture", "product launch", "patent", "intellectual property",
        "investment", "subsidiary formation", "subsidiary disposal", "incorporation of subsidiary",
        "fund raising", "qip", "rights issue", "fpo", "preferential issue",
        "debt raising", "debt reduction", "dividend", "bonus issue", "stock split",
        "litigation", "tax notice", "gst notice", "force majeure", "board meeting outcome"
    ]),
    ("🟡 Strategic", [
        "investor presentation", "analyst", "investor meet", "conference call", "con. call",
        "press release", "clarification", "general update", "esg", "sustainability",
        "memorandum of understanding", " mou ", "board meeting notice",
        "agm", "egm", "postal ballot", "voting results", "scrutinizer", "scrutiniser"
    ]),
    ("⚪ Routine", [
        "trading window", "record date", "book closure", "compliance filing",
        "compliance certificate", "newspaper publication", "share certificate",
        "duplicate share certificate", "authorized signatory", "authorised signatory",
        "regulation 6(1)", "regulation 7", "disclosure under regulation",
        "sebi (depositories", "shareholding pattern", "loss of share certificate"
    ])
]

def classify_priority(subject_text):
    """Matches the NSE filing 'Subject' text against the priority-tier keyword rules.
    Falls back to Strategic (not Routine) when unmatched, so unusual filings still
    get surfaced for a human glance rather than silently buried as low-priority."""
    if not subject_text:
        return "🟡 Strategic"
    text = subject_text.lower()
    for tier, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in text:
                return tier
    return "🟡 Strategic"

def get_next_client():
    """Rotates through the available keys sequentially to balance free-tier limits."""
    global current_key_index
    key = API_KEYS[current_key_index]
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    print(f"[🔄 Key Rotation] Using API Key Slot {current_key_index + 1} for this request.")
    return OpenAI(api_key=key, base_url=GROQ_BASE_URL)

def clean_expired_cache():
    now = datetime.now()
    expired_keys = [k for k, timestamp in processed_cache.items() if now - timestamp > timedelta(hours=MEMORY_EXPIRY_HOURS)]
    for k in expired_keys:
        del processed_cache[k]

def load_live_configs():
    # Prefer fetching the live version straight from GitHub, since the local checkout
    # is a frozen snapshot from when this ~5h50m job started. Without this, changes
    # made in the Streamlit dashboard mid-run wouldn't apply until the next handover.
    if GH_PAT and GITHUB_REPOSITORY:
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/watchlist.json"
            headers = {"Authorization": f"token {GH_PAT}", "Accept": "application/vnd.github.v3+json"}
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                content = base64.b64decode(res.json()["content"]).decode("utf-8")
                return json.loads(content)
            else:
                print(f"[⚠️] Remote config fetch returned {res.status_code}, falling back to local file.")
        except Exception as e:
            print(f"[⚠️] Remote config fetch failed, falling back to local file: {e}")

    try:
        with open("watchlist.json", "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[⚠️] Configuration load failed, falling back to defaults: {e}")
        return {
            "tracking_mode": "All Stocks (Default)",
            "selected_watchlist": [],
            "high_value_keywords": ["FINANCIAL RESULTS", "DIVIDEND"],
            "routine_keywords": ["SHAREHOLDING PATTERN"]
        }

def split_headline(desc):
    """Splits the RSS description into a subject line and a detail line.
    NSE feed format is: "<detail text> |SUBJECT: <category/subject text>"
    """
    if not desc:
        return "N/A", ""
    if "|SUBJECT:" in desc:
        detail, subject = desc.split("|SUBJECT:", 1)
        return subject.strip(), detail.strip()
    return desc.strip(), ""

def extract_pdf_text(link, max_chars=6000, max_bytes=8_000_000, max_pages=6):
    """Downloads and extracts text from the filing PDF so the AI summary reflects
    the actual document content instead of just the short RSS description."""
    if not link or PdfReader is None:
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(link, headers=headers, timeout=15, stream=True)
        if res.status_code != 200:
            return ""

        content = b""
        for chunk in res.iter_content(chunk_size=65536):
            content += chunk
            if len(content) > max_bytes:
                break

        reader = PdfReader(io.BytesIO(content))
        text_parts = []
        for page in reader.pages[:max_pages]:
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            if page_text:
                text_parts.append(page_text)
            if sum(len(t) for t in text_parts) > max_chars:
                break

        full_text = "\n".join(text_parts).strip()
        return full_text[:max_chars]
    except Exception as e:
        print(f"[⚠️] PDF text extraction failed: {e}")
        return ""

def analyze_with_ai(headline, details):
    """Returns (sentiment, summary_text). Category/priority is handled separately
    via classify_priority() — deterministic, not AI-generated."""
    trimmed_details = details.strip() if details else ""
    if not trimmed_details:
        trimmed_details = "No further details were available beyond the headline."

    prompt = f"""You are a financial-filing summarizer. Respond with STRICT JSON only.
No markdown, no preamble, no explanation outside the JSON object.

Filing headline: {headline}
Filing details: {trimmed_details[:6000]}

Return exactly this JSON structure and nothing else:
{{"sentiment": "🟢 Positive" or "⚪ Neutral" or "🔴 Negative", "summary": ["fact 1", "fact 2", "fact 3"]}}

Rules:
- "summary" must contain EXACTLY 3 to 4 items.
- Each item is a short, standalone factual bullet (dates, figures, names, actions). No leading dash — that is added later.
- Do NOT mention the word "Category" or restate the sentiment inside the summary bullets.
- Do NOT include any text outside the JSON object.
"""

    for attempt in range(len(API_KEYS)):
        try:
            client = get_next_client()
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.2
            )

            text = response.choices[0].message.content if response.choices else None
            if not text:
                continue

            data = json.loads(text)
            sentiment = data.get("sentiment", "⚪ Neutral")
            raw_bullets = data.get("summary", [])
            bullets = [
                b.strip().replace("**", "").replace("*", "")
                for b in raw_bullets if b and b.strip()
            ][:4]

            if not bullets:
                bullets = ["No summary could be generated for this filing."]

            summary_text = "\n".join(f"- {b}" for b in bullets)
            return sentiment, summary_text

        except Exception as e:
            print(f"[⚠️ Exception on Key Attempt {attempt + 1}] {e}. Trying next key...")
            continue

    return "⚪ Neutral", "- Unable to generate summary (all rotated Groq keys failed)."

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=8)
        if res.status_code != 200:
            print(f"[-] Telegram target rejected payload: {res.text}")
    except Exception as e:
        print(f"[-] Telegram dispatch network error: {e}")

def check_feed_cycle(is_baseline=False):
    configs = load_live_configs()
    tracking_mode = configs.get("tracking_mode", "All Stocks (Default)")
    watchlist = [s.upper() for s in configs.get("selected_watchlist", [])]

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        response = requests.get(RSS_URL, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"[⚠️] RSS stream unavailable: HTTP {response.status_code}")
            return

        root = ET.fromstring(response.content)
        items = root.findall('.//item')

        for item in reversed(items):
            title = item.find('title').text if item.find('title') is not None else "UNKNOWN"
            link = item.find('link').text if item.find('link') is not None else ""
            desc = item.find('description').text if item.find('description') is not None else ""
            pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""

            unique_key = f"{title}_{pub_date}"
            if unique_key in processed_cache:
                continue

            if is_baseline:
                processed_cache[unique_key] = datetime.now()
                continue

            matched = False
            if tracking_mode == "All Stocks (Default)":
                matched = True
            else:
                for symbol in watchlist:
                    if symbol in title.upper() or symbol in link.upper():
                        matched = True
                        break

            processed_cache[unique_key] = datetime.now()

            if not matched:
                continue

            print(f"[Match Found] Analyzing announcement: {title}")

            headline_subject, headline_detail = split_headline(desc)
            priority_category = classify_priority(headline_subject)

            pdf_text = extract_pdf_text(link)
            details_for_ai = pdf_text if pdf_text else headline_detail

            sentiment, clean_summary = analyze_with_ai(headline_subject, details_for_ai)

            doc_text = f"[View PDF Document]({link})" if link else "No Document"

            message = (
                f"🚨 *NEW NSE ANNOUNCEMENT* 🚨\n\n"
                f"⚡ *Company:* {title}\n\n"
                f"⏰ *Published:* {pub_date}\n\n"
                f"📄 *Category:* {priority_category} | {sentiment}\n\n"
                f"📝 *Headline:* {headline_subject}\n"
                f">> {headline_detail}\n\n"
                f"📝 *AI Summary:* \n{clean_summary}\n\n"
                f"📎 *Filing Document:* {doc_text}"
            )

            send_telegram_message(message)
            time.sleep(1)

    except Exception as e:
        print(f"[⚠️] Error during feed processing cycle loop: {e}")

def trigger_workflow_handover():
    if not GH_PAT or not GITHUB_REPOSITORY:
        print("[Handover] GitHub settings missing. Stopping loop pipeline.")
        send_telegram_message("⚠️ *Handover Failed:* GH_PAT or GITHUB_REPOSITORY secret is missing. Monitor loop has stopped.")
        sys.exit(1)

    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/workflows/run.yml/dispatches"
    headers = {
        "Authorization": f"token {GH_PAT}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        res = requests.post(url, json={"ref": "main"}, headers=headers, timeout=10)
        if res.status_code == 204:
            print("[🚀] Handover process chain successfully dispatched worker successor.")
            sys.exit(0)
        else:
            print(f"[❌] Handoff runner deployment failed: {res.status_code} - {res.text}")
            send_telegram_message(
                f"⚠️ *Handover Failed:* GitHub dispatch returned {res.status_code}.\n"
                f"Details: {res.text[:300]}\n\n"
                f"Monitor loop has stopped. Manually re-run the workflow from the Actions tab."
            )
            sys.exit(1)
    except Exception as e:
        print(f"[❌] Fatal link failure during handover transition: {e}")
        send_telegram_message(
            f"⚠️ *Handover Failed:* Network/exception error during handoff: {e}\n\n"
            f"Monitor loop has stopped. Manually re-run the workflow from the Actions tab."
        )
        sys.exit(1)

if __name__ == "__main__":
    print(f"[🔥] Launching Action Monitor System Node: {datetime.now()}")

    print("[Sync] Building tracking indexing baseline...")
    check_feed_cycle(is_baseline=True)
    print(f"[Sync] Complete. Indexed {len(processed_cache)} elements.")

    RUN_DURATION = 21000
    CHECK_INTERVAL = 10
    start_time = time.time()
    loop_count = 0

    while time.time() - start_time < RUN_DURATION:
        loop_count += 1
        if loop_count % 30 == 0:
            clean_expired_cache()

        check_feed_cycle(is_baseline=False)
        time.sleep(CHECK_INTERVAL)

    print("[⏰] Window limit reached. Initiating script handoff relay chain...")
    trigger_workflow_handover()
