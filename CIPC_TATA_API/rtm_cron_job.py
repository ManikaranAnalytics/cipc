#!/usr/bin/env python3
"""
RTM Cron Job Script
Runs via cron (every 2 minutes) to check for new revision files and submit RTM bids.

Features:
- Tracks last used revision file in JSON
- Supports both RID (Intraday) and RDA (Day Ahead) revisions
- Calculates RTM values from GDAM + Revision files
- Falls back to direct revision values when GDAM is missing
- Checks email for GDAM files if missing from FTP
- Sends WhatsApp alerts for GDAM missing (10 PM, 11 PM)
- Sends success/failure messages via WhatsApp
- After 10:30 PM, processes next day's files
"""

import ftplib
import io
import json
import os
import re
import time
import base64
import email
import imaplib
import requests
import pandas as pd
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timedelta
from pathlib import Path
from email.header import decode_header

from market_file_utils import (
    extract_injection_percentages,
    extract_loss_percentages,
    extract_values_from_dataframe,
    matches_gdam_filename,
    read_table_from_buffer,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

# FTP Configuration
FTP_HOST = "15.207.32.135"
FTP_USER = "partner"
FTP_PASSWORD = "jEm9P6182x89"
FTP_PORT = 21
FTP_BASE_PATH = "/WIND/SCHEDULE/KA/CIP_Hatalageri/"

# Client Configuration - List of all clients
CLIENTS = [
    {
        "name": "IRIS_1",
        "folder": "IRIS_1",
        "portfolio_code": "S1KA0TPT0831",
        "file_prefix": "Iris_S1KA0TPT0831"
    },
    {
        "name": "IRIS_2",
        "folder": "IRIS_2",
        "portfolio_code": "S1KA0TPT0832",
        "file_prefix": "Iris Renewables_S1KA0TPT0832"
    },
    {
        "name": "Rinnovature",
        "folder": "Rinnovature",
        "portfolio_code": "S1KA0TPT0833",
        "file_prefix": "Rinnovatore Energy_S1KA0TPT0833"
    },
    {
        "name": "Saisei",
        "folder": "saisei",
        "portfolio_code": "S1KA0TPT0834",
        "file_prefix": "Saisei Energy_S1KA0TPT0834"
    }
]

# RTM API Configuration
RTM_BASE_URL = "https://samastt.tatapowertrading.com/api/rtm"
RTM_SUBMIT_ENDPOINT = "/AddNewRTMBidService"
RTM_ORDERBOOK_ENDPOINT = "/orderBookResponse"
RTM_AUTH_TOKEN = "eyJhdXRoIjoiVjJfQ29tcGxleE0iLCJzdWIiOiJVc2VyIksifQ~Xk7r9M2p!bFq#zL"
RTM_DEVICE = "postman"
RTM_USER = "S1KA0TPT0831"

# WhatsApp Configuration
WHATSAPP_TEXT_URL = "https://gate.whapi.cloud/messages/text"
WHATSAPP_DOC_URL = "https://gate.whapi.cloud/messages/document"
WHATSAPP_GROUP_ID = "120363404188496282@g.us"
WHATSAPP_AUTH = "Bearer UhMontkZC8KY8WBLgcrH7kz6HYXlntsZ"


#EMAIL CONFIG
EMAIL_HOST = "mx1.50hertz.in"
EMAIL_USER = "autom@manikarananalytics.in"
EMAIL_PASSWORD = "P3OKVUM6P2LTTLPV"
EMAIL_IMAP_PORT = 993

# State file to track last processed revisions.
# Resolve it relative to this script so cron and local runs use the same file.
BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "rtm_cron_state.json"

# Test mode - set to a specific date for testing, or None for production
TEST_MODE_DATE = None  # Set to datetime(2025, 12, 2) for testing

DEFAULT_STATE = {
    "last_revision_file": None,
    "last_gdam_alert_date": None,
    "last_gdam_alert_hour": None,
}

# =============================================================================
# WHATSAPP MESSAGING
# =============================================================================

def send_whatsapp_text(message: str) -> bool:
    """Send a text message via WhatsApp"""
    try:
        payload = {
            "typing_time": 0,
            "to": WHATSAPP_GROUP_ID,
            "body": message
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/json; charset=utf-8",
            "authorization": WHATSAPP_AUTH
        }
        response = requests.post(WHATSAPP_TEXT_URL, json=payload, headers=headers, timeout=30)
        print(f"[WhatsApp Text] Status: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        print(f"[WhatsApp Text Error] {e}")
        return False

def send_whatsapp_document(base64_file: str, filename: str, caption: str) -> bool:
    """Send a document via WhatsApp"""
    try:
        payload = {
            "to": WHATSAPP_GROUP_ID,
            "media": f"data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{base64_file}",
            "filename": filename,
            "caption": caption
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/json; charset=utf-8",
            "authorization": WHATSAPP_AUTH
        }
        response = requests.post(WHATSAPP_DOC_URL, json=payload, headers=headers, timeout=30)
        print(f"[WhatsApp Doc] Status: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        print(f"[WhatsApp Doc Error] {e}")
        return False

# =============================================================================
# STATE MANAGEMENT
# =============================================================================

def load_state() -> dict:
    """Load the state from JSON file"""
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r") as f:
                loaded_state = json.load(f)
            if isinstance(loaded_state, dict):
                return {**DEFAULT_STATE, **loaded_state}
        except (json.JSONDecodeError, OSError) as e:
            print(f"[STATE WARN] Failed to load {STATE_FILE}: {e}. Resetting state.")
    return DEFAULT_STATE.copy()

def save_state(state: dict):
    """Save the state to JSON file"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)

# =============================================================================
# FTP OPERATIONS
# =============================================================================

def connect_ftp():
    """Connect to FTP server"""
    ftp = ftplib.FTP()
    ftp.connect(FTP_HOST, FTP_PORT)
    ftp.login(FTP_USER, FTP_PASSWORD)
    return ftp

def list_files(ftp, path: str) -> list:
    """List files in a directory"""
    try:
        ftp.cwd(path)
        files = []
        ftp.retrlines('NLST', files.append)
        return files
    except Exception as e:
        print(f"[FTP Error] Failed to list {path}: {e}")
        return []

def download_file(ftp, path: str, filename: str) -> io.BytesIO:
    """Download a file to memory"""
    ftp.cwd(path)
    bio = io.BytesIO()
    ftp.retrbinary(f'RETR {filename}', bio.write)
    bio.seek(0)
    return bio

def upload_file_to_ftp(ftp, path: str, filename: str, file_data: io.BytesIO):
    """Upload a file to FTP, creating directory if needed"""
    try:
        # Try to change to directory, create if doesn't exist
        try:
            ftp.cwd(path)
        except:
            # Try to create the directory
            try:
                ftp.mkd(path)
                print(f"[FTP] Created directory: {path}")
                ftp.cwd(path)
            except Exception as mkdir_error:
                print(f"[FTP Error] Cannot create/access directory {path}: {mkdir_error}")
                return False

        file_data.seek(0)
        ftp.storbinary(f'STOR {filename}', file_data)
        print(f"[FTP] Uploaded {filename} to {path}")
        return True
    except Exception as e:
        print(f"[FTP Error] Failed to upload {filename}: {e}")
        return False

# =============================================================================
# EMAIL OPERATIONS (GDAM FILE CHECK)
# =============================================================================

def connect_email():
    """Connect to email server via IMAP"""
    try:
        mail = imaplib.IMAP4_SSL(EMAIL_HOST, EMAIL_IMAP_PORT)
        mail.login(EMAIL_USER, EMAIL_PASSWORD)
        print(f"[EMAIL] Connected to {EMAIL_HOST}")
        return mail
    except Exception as e:
        print(f"[EMAIL Error] Failed to connect: {e}")
        return None

def search_gdam_email(mail, portfolio_code: str, target_date: datetime) -> tuple:
    """
    Search for GDAM email with subject like:
    'GDAM Energy Schedule of S1KA0TPT0831 for 04-12-2025'

    Returns: (found: bool, attachment_data: BytesIO, filename: str)
    """
    try:
        mail.select("INBOX")

        # Format date for subject search
        date_str = target_date.strftime("%d-%m-%Y")
        subject_pattern = f"GDAM Energy Schedule of {portfolio_code} for {date_str}"

        print(f"[EMAIL] Searching for subject: {subject_pattern}")

        # Search for emails with matching subject
        search_criteria = f'(SUBJECT "{subject_pattern}")'
        status, messages = mail.search(None, search_criteria)

        if status != "OK" or not messages[0]:
            print(f"[EMAIL] No matching email found")
            return False, None, None

        # Get the latest matching email
        email_ids = messages[0].split()
        latest_email_id = email_ids[-1]  # Get most recent

        print(f"[EMAIL] Found {len(email_ids)} matching email(s), using latest")

        # Fetch the email
        status, msg_data = mail.fetch(latest_email_id, "(RFC822)")

        if status != "OK":
            print(f"[EMAIL] Failed to fetch email")
            return False, None, None

        # Parse the email
        email_body = msg_data[0][1]
        msg = email.message_from_bytes(email_body)

        # Look for Excel attachment
        for part in msg.walk():
            content_disposition = str(part.get("Content-Disposition"))

            if "attachment" in content_disposition:
                filename = part.get_filename()

                if filename:
                    # Decode filename if needed
                    if decode_header(filename)[0][1]:
                        filename = decode_header(filename)[0][0].decode(decode_header(filename)[0][1])

                    # Check if it's an Excel file
                    if filename.lower().endswith(('.xlsx', '.xls')):
                        print(f"[EMAIL] Found attachment: {filename}")
                        attachment_data = io.BytesIO(part.get_payload(decode=True))
                        return True, attachment_data, filename

        print(f"[EMAIL] No Excel attachment found in email")
        return False, None, None

    except Exception as e:
        print(f"[EMAIL Error] Search failed: {e}")
        import traceback
        traceback.print_exc()
        return False, None, None

def check_and_fetch_gdam_from_email(ftp, client: dict, target_date: datetime) -> str:
    """
    Check email for GDAM file and upload to FTP if found.
    Returns the filename if successful, None otherwise.
    """
    print(f"[EMAIL] Checking email for GDAM file for {target_date.strftime('%d-%m-%Y')}")

    mail = connect_email()
    if not mail:
        return None

    try:
        found, attachment_data, filename = search_gdam_email(mail, client["portfolio_code"], target_date)

        if found and attachment_data:
            # Upload to FTP
            gdam_path = f"{FTP_BASE_PATH}{client['folder']}/GDAM_Acceptance"

            if upload_file_to_ftp(ftp, gdam_path, filename, attachment_data):
                print(f"[EMAIL] Successfully uploaded GDAM from email: {filename}")
                send_whatsapp_text(f"📧 GDAM file fetched from email and uploaded to FTP!\n\n📅 Date: {target_date.strftime('%d/%m/%Y')}\n📁 File: {filename}")
                return filename
            else:
                print(f"[EMAIL] Failed to upload GDAM to FTP")
                return None
        else:
            print(f"[EMAIL] No GDAM file found in email")
            return None

    except Exception as e:
        print(f"[EMAIL Error] {e}")
        return None
    finally:
        try:
            mail.logout()
        except:
            pass

# =============================================================================
# FILE PARSING
# =============================================================================

def parse_date_from_gdam_filename(filename: str) -> datetime:
    """Parse date from GDAM filename like G-DAM_IEX251202SCH_..."""
    match = re.search(r'IEX(\d{6})SCH', filename)
    if match:
        date_str = match.group(1)  # YYMMDD
        return datetime.strptime(date_str, "%y%m%d")
    return None

def parse_date_from_revision_filename(filename: str) -> datetime:
    """Parse date from revision filename like ..._02.12.2025_..."""
    match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', filename)
    if match:
        day, month, year = match.groups()
        return datetime(int(year), int(month), int(day))
    return None

def get_revision_number(filename: str) -> int:
    """Extract revision number from filename (RID2 -> 2, RDA3 -> 3)"""
    match = re.search(r'RI[DA](\d+)', filename)
    if match:
        return int(match.group(1))
    return -1

def get_revision_type(filename: str) -> str:
    """Get revision type: RID (Intraday) or RDA (Day Ahead)"""
    if 'RID' in filename:
        return 'RID'
    elif 'RDA' in filename:
        return 'RDA'
    return None

def find_latest_revision_file(files: list, target_date: datetime) -> str:
    """Find the latest revision file for a given date"""
    # Support both date formats: dots (10.01.2026) and dashes (10-01-2026)
    date_str_dots = target_date.strftime("%d.%m.%Y")
    date_str_dashes = target_date.strftime("%d-%m-%Y")

    matching_files = [
        f for f in files
        if (date_str_dots in f or date_str_dashes in f)
        and ("RID" in f or "RDA" in f)
        and f.lower().endswith((".csv", ".xls", ".xlsx"))
    ]

    if not matching_files:
        return None

    # Sort by revision number (highest first)
    matching_files.sort(key=lambda x: get_revision_number(x), reverse=True)
    return matching_files[0]

def find_gdam_file(files: list, target_date: datetime) -> str:
    """Find GDAM file for a given date"""
    for f in files:
        if matches_gdam_filename(f, target_date):
            return f
    return None

# =============================================================================
# DATA PROCESSING
# =============================================================================

def parse_injection_percentage(text: str) -> float:
    """Parse injection percentage from text like 'Karnataka->Injection: 2.87%...'"""
    match = re.search(r'Injection:\s*([\d.]+)%', str(text))
    if match:
        return float(match.group(1))
    return 0.0


def round_down(value: float, decimals: int = 1) -> float:
    """Round down using decimal truncation (Excel-style ROUNDDOWN)."""
    quantizer = Decimal("1").scaleb(-decimals)
    return float(Decimal(str(value)).quantize(quantizer, rounding=ROUND_DOWN))

def read_gdam_values(ftp, gdam_path: str, gdam_file: str) -> tuple:
    """Read GDAM values and applicable loss percentages"""
    bio = download_file(ftp, gdam_path, gdam_file)
    df = read_table_from_buffer(bio, gdam_file)

    # Accepted market energy lives in column F, rows 11-106.
    gdam_values = extract_values_from_dataframe(df, "F11:F106", absolute=True)

    loss_percentages = extract_loss_percentages(df)
    applicable_losses = [
        loss_percentages.get("area_loss"),
        loss_percentages.get("state_loss"),
    ]
    total_loss = sum(loss for loss in applicable_losses if loss is not None)

    # Fall back to the legacy generic injection scan when named loss rows are
    # missing so older workbook layouts still work.
    if total_loss == 0.0 and not loss_percentages:
        injection_percentages = extract_injection_percentages(df)
        total_loss = sum(injection_percentages[:2])

    multiplier = max(0.0, (100 - total_loss) / 100)

    return gdam_values, multiplier, total_loss

def read_revision_values(ftp, rev_path: str, rev_file: str) -> list:
    """Read revision values from CSV or Excel file"""
    bio = download_file(ftp, rev_path, rev_file)
    df = read_table_from_buffer(bio, rev_file)
    return extract_values_from_dataframe(df, "E12:E107")

def calculate_rtm_values(rev_values: list, gdam_values: list, multiplier: float) -> list:
    """Calculate RTM bid values"""
    rtm_values = []
    for i in range(96):
        diff = rev_values[i] - gdam_values[i]
        if diff > 0:
            rtm_val = round_down(diff * multiplier, 1)
        else:
            rtm_val = 0.0
        rtm_values.append(rtm_val)
    return rtm_values

def calculate_rtm_values_no_gdam(rev_values: list) -> list:
    """Calculate RTM values when GDAM is missing (use revision values directly)"""
    return [round_down(v, 1) for v in rev_values]

# =============================================================================
# RTM BID TIMING LOGIC
# =============================================================================

def is_closing_time() -> bool:
    """
    Check if current time is during RTM bid closing windows.
    Avoid xx:28-xx:31 and xx:58-xx:01
    """
    now = datetime.now()
    minute = now.minute

    # Avoid matches XX:58 to XX:01 and XX:28 to XX:31
    if minute >= 58 or minute <= 1 or (28 <= minute <= 31):
        return True
    return False

def get_earliest_active_block() -> int:
    """
    Calculate the earliest active time block based on current time.

    Logic:
    - Bid at 1:05 PM → Window 1:00-1:30 → Active from 2:30 PM
    - Bid at 1:35 PM → Window 1:30-2:00 → Active from 3:00 PM
    - After 10:30 PM → Bidding for next day, all 96 blocks active

    Formula: Window start + 90 minutes = earliest active time

    Returns the 0-indexed time block number (0-95)
    """
    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute

    # After 10:30 PM, we're bidding for the next day - all blocks are active
    if (current_hour == 22 and current_minute >= 30) or current_hour >= 23:
        return 0  # All 96 blocks for next day are available

    # Determine current window start
    if current_minute < 30:
        window_start_hour = current_hour
        window_start_minute = 0
    else:
        window_start_hour = current_hour
        window_start_minute = 30

    # Add 90 minutes to get earliest active time
    earliest_active_minute = window_start_minute + 90
    earliest_active_hour = window_start_hour + (earliest_active_minute // 60)
    earliest_active_minute = earliest_active_minute % 60

    # Handle day overflow (if we reach this point before 10:30 PM but calculation goes past midnight)
    if earliest_active_hour >= 24:
        return 96  # No valid blocks for today

    # Convert to block number (each block is 15 minutes)
    # Block 0 = 00:00-00:15, Block 1 = 00:15-00:30, etc.
    earliest_block = (earliest_active_hour * 4) + (earliest_active_minute // 15)

    return earliest_block

def get_time_from_block(block: int) -> str:
    """Convert block number to time string"""
    hour = block // 4
    minute = (block % 4) * 15
    return f"{hour:02d}:{minute:02d}"

# =============================================================================
# RTM API SUBMISSION
# =============================================================================

def create_rtm_bid_payload(rtm_values: list, target_date: datetime, portfolio_code: str) -> dict:
    """
    Create RTM bid payload for API submission.
    Only includes time blocks that are still active (not expired).
    """
    bid_date = target_date.strftime("%d-%m-%Y")

    # Get earliest active block
    earliest_block = get_earliest_active_block()
    print(f"[INFO] Earliest active block: {earliest_block} ({get_time_from_block(earliest_block) if earliest_block < 96 else 'None - day ended'})")

    # Build bid array with only positive values AND active blocks
    bid_array = []
    skipped_blocks = 0

    for i in range(96):
        if rtm_values[i] > 0:
            # Skip blocks that are no longer active
            if i < earliest_block:
                skipped_blocks += 1
                continue

            hour = i // 4
            minute_start = (i % 4) * 15
            minute_end = minute_start + 15

            from_time = f"{hour:02d}:{minute_start:02d}"
            # For block 95 (23:45-00:00), use 24:00 as end time since API rejects 00:00 > 23:45
            if hour == 23 and minute_end == 60:
                to_time = "24:00"
            elif minute_end < 60:
                to_time = f"{hour:02d}:{minute_end:02d}"
            else:
                to_time = f"{(hour+1):02d}:00"

            bid_entry = {
                "fromtime": from_time,
                "totime": to_time,
                "type": "S",
                "bidvalue": [
                    {
                        "price": "250",
                        "value": str(rtm_values[i]),
                        "type": "S"
                    }
                ]
            }
            bid_array.append(bid_entry)

    if skipped_blocks > 0:
        print(f"[INFO] Skipped {skipped_blocks} expired time blocks")

    payload = {
        "portfoliocode": portfolio_code,
        "user": portfolio_code,
        "bidType": "Single",
        "type": "Sell",
        "bidDate": bid_date,
        "bid": bid_array
    }

    return payload

def submit_rtm_bid(payload: dict, max_retries: int = 3) -> tuple:
    """Submit RTM bid to API with retry logic for connection errors"""
    url = f"{RTM_BASE_URL}{RTM_SUBMIT_ENDPOINT}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": RTM_AUTH_TOKEN,
        "device": RTM_DEVICE
    }
    print(f"[DEBUG] RTM API URL: {url}")
    print(f"[DEBUG] Payload bid count: {len(payload.get('bid', []))}")

    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[INFO] Attempt {attempt}/{max_retries}...")
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            print(f"[DEBUG] Response status code: {response.status_code}")
            print(f"[DEBUG] Response text: {response.text[:500] if response.text else 'Empty'}")

            try:
                result = response.json()
            except:
                result = {"raw_response": response.text}

            if result.get("Status") == "1" or result.get("status") == "1":
                return True, result
            else:
                return False, result

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_error = e
            print(f"[WARN] Connection error on attempt {attempt}: {str(e)[:100]}")
            if attempt < max_retries:
                wait_time = attempt * 5  # 5, 10, 15 seconds
                print(f"[INFO] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            continue

        except Exception as e:
            import traceback
            return False, {"error": str(e), "traceback": traceback.format_exc()}

    # All retries exhausted
    return False, {"error": f"Connection failed after {max_retries} attempts: {str(last_error)}"}


def get_current_orderbook(target_date: datetime, portfolio_code: str) -> dict:
    """Get current RTM orderbook from API to check existing bids"""
    url = f"{RTM_BASE_URL}{RTM_ORDERBOOK_ENDPOINT}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": RTM_AUTH_TOKEN,
        "device": RTM_DEVICE
    }

    bid_date = target_date.strftime("%d-%m-%Y")
    payload = {
        "portfoliocode": portfolio_code,
        "user": portfolio_code,
        "bidType": "Single",
        "bidDate": bid_date
    }

    try:
        response = requests.get(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            return {"success": True, "data": response.json() if response.content else {}}
        else:
            return {"success": False, "error": f"Status {response.status_code}: {response.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def parse_orderbook_to_rtm_values(orderbook_data: dict) -> list:
    """Parse orderbook response to extract current RTM bid values as a list of 96 blocks"""
    rtm_values = [0.0] * 96

    try:
        # IEXResponse is a JSON string that needs to be parsed
        iex_response_str = orderbook_data.get("data", {}).get("IEXResponse", "")
        if not iex_response_str:
            return rtm_values

        # Parse the nested JSON string
        iex_response = json.loads(iex_response_str) if isinstance(iex_response_str, str) else iex_response_str

        # Get the RTMBlockBidDetails array
        bids = iex_response.get("RTMBlockBidDetails", [])

        for bid in bids:
            from_time = bid.get("FromPeriodId", "")
            quantity = abs(float(bid.get("Quantity", 0)))  # Quantity is negative for sells

            if from_time:
                # Parse from_time to get block index (e.g., "14:30" -> block 58)
                parts = from_time.split(":")
                if len(parts) >= 2:
                    hour = int(parts[0])
                    minute = int(parts[1])
                    # Handle 24:00 as block 95
                    if hour == 24:
                        block = 95
                    else:
                        block = hour * 4 + minute // 15

                    if 0 <= block < 96:
                        rtm_values[block] = quantity
    except Exception as e:
        print(f"[WARN] Error parsing orderbook: {e}")

    return rtm_values


# =============================================================================
# EXCEL GENERATION
# =============================================================================

def generate_rtm_excel(rtm_values: list, rev_values: list, gdam_values: list,
                       multiplier: float, target_date: datetime,
                       gdam_file: str, rev_file: str) -> tuple:
    """Generate Excel file with RTM bid values and return as base64"""
    output_data = []
    for i in range(96):
        hour = i // 4
        minute_start = (i % 4) * 15
        minute_end = minute_start + 15

        from_time = f"{hour:02d}:{minute_start:02d}:00"
        to_time = f"{hour:02d}:{minute_end:02d}:00" if minute_end < 60 else f"{(hour+1)%24:02d}:00:00"

        diff = rev_values[i] - gdam_values[i] if gdam_values is not None else rev_values[i]

        output_data.append({
            "Time Block": i + 1,
            "From Time": from_time,
            "To Time": to_time,
            "Revision Value (MW)": rev_values[i],
            "GDAM Value (MW)": gdam_values[i] if gdam_values is not None else "N/A",
            "Difference": round_down(diff, 2) if gdam_values is not None else "N/A",
            "Multiplier": round_down(multiplier, 4) if gdam_values is not None else "N/A",
            "RTM Bid Value (MW)": rtm_values[i],
            "RTM Bid Created": "Yes" if rtm_values[i] > 0 else "No"
        })

    df = pd.DataFrame(output_data)

    # Create Excel in memory
    excel_bio = io.BytesIO()
    with pd.ExcelWriter(excel_bio, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='RTM Bids', index=False)

        # Add metadata sheet
        metadata = pd.DataFrame({
            "Field": ["Date", "GDAM File", "Revision File", "Total RTM MW", "Positive Blocks", "Multiplier"],
            "Value": [
                target_date.strftime("%d/%m/%Y"),
                gdam_file if gdam_file else "Not Available",
                rev_file,
                sum(rtm_values),
                sum(1 for v in rtm_values if v > 0),
                round_down(multiplier, 4) if gdam_values is not None else "N/A"
            ]
        })
        metadata.to_excel(writer, sheet_name='Metadata', index=False)

    excel_bio.seek(0)
    base64_content = base64.b64encode(excel_bio.read()).decode('utf-8')
    filename = f"RTM_Bids_{target_date.strftime('%Y%m%d')}_{datetime.now().strftime('%H%M%S')}.xlsx"

    return base64_content, filename

# =============================================================================
# MAIN PROCESSING LOGIC
# =============================================================================

def get_target_date() -> datetime:
    """Get the target date based on current time (after 10:30 PM, use next day)"""
    # Test mode override
    if TEST_MODE_DATE:
        return TEST_MODE_DATE

    now = datetime.now()
    if now.hour >= 22 and now.minute >= 30:
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif now.hour >= 23:
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)

def should_check_gdam_alert() -> tuple:
    """Check if we should alert for missing GDAM (10 PM or 11 PM for next day)"""
    now = datetime.now()
    if now.hour in [22, 23]:
        next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return True, next_day, now.hour
    return False, None, None

def process_client(ftp, client: dict, state: dict) -> dict:
    """Process a single client folder"""
    client_name = client["name"]
    client_folder = client["folder"]
    portfolio_code = client["portfolio_code"]

    print(f"\n{'='*60}")
    print(f"Processing: {client_name} ({portfolio_code})")
    print(f"{'='*60}")

    base_path = f"{FTP_BASE_PATH}{client_folder}"
    gdam_path = f"{base_path}/GDAM_Acceptance"

    # Get target date
    target_date = get_target_date()
    print(f"[INFO] Target date: {target_date.strftime('%d/%m/%Y')}")
    print(f"[INFO] Current time: {datetime.now().strftime('%H:%M:%S')}")

    # List files
    main_files = list_files(ftp, base_path)
    gdam_files = list_files(ftp, gdam_path)

    # Find latest revision file for target date
    latest_rev = find_latest_revision_file(main_files, target_date)

    if not latest_rev:
        print(f"[INFO] No revision file found for {target_date.strftime('%d/%m/%Y')}")
        return state

    print(f"[INFO] Latest revision: {latest_rev}")

    # Check if this is a new revision (using client-specific key)
    state_key = f"last_revision_{client_name}"
    if state.get(state_key) == latest_rev:
        print(f"[INFO] No new revision. Skipping.")
        return state

    print(f"[INFO] New revision detected! Previous: {state.get(state_key)}")

    # Find GDAM file
    gdam_file = find_gdam_file(gdam_files, target_date)

    # If GDAM not found on FTP, check email
    if not gdam_file:
        print(f"[INFO] GDAM file not on FTP, checking email...")
        email_gdam_file = check_and_fetch_gdam_from_email(ftp, client, target_date)
        if email_gdam_file:
            gdam_file = email_gdam_file
            # Refresh gdam_files list
            gdam_files = list_files(ftp, gdam_path)

    # Read revision values
    rev_values = read_revision_values(ftp, base_path, latest_rev)
    print(f"[INFO] Revision values loaded: {len(rev_values)} blocks")

    if gdam_file:
        print(f"[INFO] GDAM file found: {gdam_file}")
        # Calculate with GDAM
        gdam_values, multiplier, total_loss = read_gdam_values(ftp, gdam_path, gdam_file)
        print(f"[INFO] GDAM values loaded. Multiplier: {multiplier:.4f} (Total loss: {total_loss}%)")
        rtm_values = calculate_rtm_values(rev_values, gdam_values, multiplier)
    else:
        print(f"[WARN] No GDAM file found (FTP + Email). Using revision values directly.")
        gdam_values = None
        multiplier = 1.0
        rtm_values = calculate_rtm_values_no_gdam(rev_values)

    # Count positive blocks
    positive_blocks = sum(1 for v in rtm_values if v > 0)
    total_rtm = sum(rtm_values)
    print(f"[INFO] RTM calculation complete: {positive_blocks} positive blocks, {total_rtm:.1f} MW total")

    # Check if total MWh (sum of all positive values / 4) is >= 0.5
    # Each block is 15 minutes, so divide by 4 to get MWh
    total_mwh = sum(v / 4 for v in rtm_values if v > 0)
    print(f"[INFO] Total MWh (all 96 blocks): {total_mwh:.2f}")

    if total_mwh < 0.5:
        print(f"[WARN] Total MWh ({total_mwh:.2f}) is less than 0.5. Not submitting RTM bid.")
        # Generate Excel for verification
        base64_excel, excel_filename = generate_rtm_excel(
            rtm_values, rev_values, gdam_values,
            multiplier, target_date, gdam_file, latest_rev
        )
        cancel_msg = f"⚠️ RTM Bid Not Submitted - Low Quantity\n\n📁 Client: {client_name}\n📅 Date: {target_date.strftime('%d/%m/%Y')}\n📁 GDAM: {gdam_file or 'Not Available'}\n📁 Revision: {latest_rev}\n⚡ Total MWh: {total_mwh:.2f}\n\n🚫 Total MWh is less than 0.5. Please cancel current bid manually if needed."
        send_whatsapp_document(base64_excel, excel_filename, cancel_msg)
        state[state_key] = latest_rev
        save_state(state)
        return state

    # Check current orderbook from API to compare with new RTM values
    print(f"[INFO] Checking current orderbook from API...")
    orderbook_result = get_current_orderbook(target_date, portfolio_code)
    if orderbook_result["success"]:
        current_orderbook_values = parse_orderbook_to_rtm_values(orderbook_result)
        print(f"[INFO] Current orderbook has {sum(1 for v in current_orderbook_values if v > 0)} blocks with bids")

        # Compare new RTM values with current orderbook (only for active blocks)
        earliest_block = get_earliest_active_block()
        new_active_values = rtm_values[earliest_block:] if earliest_block < 96 else []
        current_active_values = current_orderbook_values[earliest_block:] if earliest_block < 96 else []

        if new_active_values == current_active_values:
            print(f"[INFO] RTM values match current orderbook. Skipping submission.")
            state[state_key] = latest_rev
            save_state(state)
            return state
    else:
        print(f"[WARN] Could not fetch orderbook: {orderbook_result.get('error', 'Unknown error')}. Proceeding with submission.")

    # Check if we're in a closing time window (xx:29-30 or xx:59-00)
    if is_closing_time():
        print(f"[WARN] Currently in RTM closing window (minute: {datetime.now().minute}). Waiting for next run.")
        # Don't update state - we'll retry on next run
        return state

    # Create and submit RTM bid
    payload = create_rtm_bid_payload(rtm_values, target_date, portfolio_code)

    # If no active blocks, add a minimal bid (0.1 MW) to cancel previous bids
    # API rejects empty bid arrays, so we need at least one entry
    if len(payload['bid']) == 0:
        earliest_block = get_earliest_active_block()
        if earliest_block < 96:
            hour = earliest_block // 4
            minute_start = (earliest_block % 4) * 15
            minute_end = minute_start + 15

            from_time = f"{hour:02d}:{minute_start:02d}"
            if hour == 23 and minute_end == 60:
                to_time = "24:00"
            elif minute_end < 60:
                to_time = f"{hour:02d}:{minute_end:02d}"
            else:
                to_time = f"{(hour+1):02d}:00"

            minimal_bid = {
                "fromtime": from_time,
                "totime": to_time,
                "type": "S",
                "bidvalue": [
                    {
                        "price": "250",
                        "value": "0.1",
                        "type": "S"
                    }
                ]
            }
            payload['bid'].append(minimal_bid)
            print(f"[INFO] No positive RTM values. Added minimal bid (0.1 MW) at block {earliest_block} to cancel previous bids.")
        else:
            print(f"[INFO] All blocks have expired. No active blocks to submit.")
            state[state_key] = latest_rev
            save_state(state)
            return state

    print(f"[INFO] Submitting RTM bid with {len(payload['bid'])} entries...")

    success, result = submit_rtm_bid(payload)

    # Generate Excel
    base64_excel, excel_filename = generate_rtm_excel(
        rtm_values, rev_values, gdam_values,
        multiplier, target_date, gdam_file, latest_rev
    )

    # Count only active blocks for the message
    active_blocks = len(payload['bid'])
    active_total_mw = sum(float(b['bidvalue'][0]['value']) for b in payload['bid'])

    if success:
        print(f"[SUCCESS] RTM bid submitted successfully!")
        submission_time = datetime.now().strftime('%H:%M:%S')
        caption = f"✅ RTM Bid Submitted Successfully!\n\n🕒 Time: {submission_time}\n👤 Client: {client_name}\n📅 Date: {target_date.strftime('%d/%m/%Y')}\n📁 GDAM: {gdam_file or 'Not Available'}\n📁 Revision: {latest_rev}\n📊 Active Blocks: {active_blocks}\n⚡ Active MW: {active_total_mw:.1f}"
        send_whatsapp_document(base64_excel, excel_filename, caption)
        # Only update last_revision_file on successful submission
        state[state_key] = latest_rev
        save_state(state)
    else:
        print(f"[ERROR] RTM bid submission failed: {result}")
        # Include payload in message for debugging (truncate if too long)
        payload_str = json.dumps(payload, indent=2)
        if len(payload_str) > 2000:
            payload_str = payload_str[:2000] + "\n... (truncated)"
        error_msg = f"❌ RTM Bid Submission Failed!\n\n📁 Client: {client_name}\n📅 Date: {target_date.strftime('%d/%m/%Y')}\n📁 GDAM: {gdam_file or 'Not Available'}\n📁 Revision: {latest_rev}\n\n🚫 Error: {json.dumps(result, indent=2)}\n\n📋 Payload:\n{payload_str}"
        send_whatsapp_text(error_msg)
        # Do NOT update state on failure - will retry on next run

    return state

def check_gdam_alert(ftp, client: dict, state: dict) -> dict:
    """Check for missing GDAM file at 10 PM and 11 PM (checks FTP first, then email)"""
    should_check, check_date, current_hour = should_check_gdam_alert()

    if not should_check:
        return state

    client_name = client["name"]
    client_folder = client["folder"]

    # Check if we already alerted for this date, hour, and client
    alert_key = f"{client_name}_{check_date.strftime('%Y%m%d')}_{current_hour}"
    if state.get(f"last_gdam_alert_{client_name}") == alert_key:
        print(f"[INFO] Already alerted for GDAM on {check_date.strftime('%d/%m/%Y')} at {current_hour}:00 for {client_name}")
        return state

    gdam_path = f"{FTP_BASE_PATH}{client_folder}/GDAM_Acceptance"
    gdam_files = list_files(ftp, gdam_path)
    gdam_file = find_gdam_file(gdam_files, check_date)

    if not gdam_file:
        print(f"[INFO] GDAM not on FTP for {check_date.strftime('%d/%m/%Y')}, checking email...")
        # Try to fetch from email before alerting
        email_gdam_file = check_and_fetch_gdam_from_email(ftp, client, check_date)

        if email_gdam_file:
            print(f"[INFO] GDAM fetched from email: {email_gdam_file}")
            gdam_file = email_gdam_file
        else:
            # Still not found, send alert
            print(f"[ALERT] No GDAM file found (FTP + Email) for {check_date.strftime('%d/%m/%Y')} at {current_hour}:00")
            msg = f"⚠️ Cannot Find G-DAM Acceptance file for {check_date.strftime('%d/%m/%Y')}\n\n🕐 Alert Time: {datetime.now().strftime('%H:%M:%S')}\n📁 Client: {client_name}\n\n📧 Email checked: No matching email found"
            send_whatsapp_text(msg)
            state[f"last_gdam_alert_{client_name}"] = alert_key
            save_state(state)
    else:
        print(f"[INFO] GDAM file exists for {check_date.strftime('%d/%m/%Y')}: {gdam_file}")

    return state

# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """Main entry point for cron job"""
    print("\n" + "=" * 70)
    print(f"RTM CRON JOB - Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Processing {len(CLIENTS)} clients: {', '.join(c['name'] for c in CLIENTS)}")
    print("=" * 70)

    try:
        # Load state
        state = load_state()
        print(f"[INFO] Loaded state: {json.dumps(state, indent=2)}")

        # Connect to FTP
        ftp = connect_ftp()
        print(f"[INFO] Connected to FTP: {FTP_HOST}")

        # Process each client
        for client in CLIENTS:
            try:
                # Check for GDAM alert (10 PM / 11 PM)
                state = check_gdam_alert(ftp, client, state)

                # Process client
                state = process_client(ftp, client, state)
            except Exception as client_error:
                print(f"[ERROR] Failed to process {client['name']}: {client_error}")
                import traceback
                traceback.print_exc()
                error_msg = f"🚨 RTM Error for {client['name']}!\n\n🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n🚫 Error: {str(client_error)}"
                send_whatsapp_text(error_msg)
                # Continue with next client

        # Close FTP
        ftp.quit()
        print(f"[INFO] FTP connection closed")

    except Exception as e:
        print(f"[CRITICAL ERROR] {e}")
        import traceback
        traceback.print_exc()

        # Send error notification
        error_msg = f"🚨 RTM Cron Job Critical Error!\n\n🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n🚫 Error: {str(e)}"
        send_whatsapp_text(error_msg)

    print(f"\n[INFO] Cron job completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

if __name__ == "__main__":
    main()
