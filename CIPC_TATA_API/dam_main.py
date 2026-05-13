import streamlit as st
import pandas as pd
import ftplib
import io
import json
from datetime import datetime, timedelta
import re
import os
import requests
import time

from market_file_utils import extract_values_from_dataframe, read_table_from_buffer

# DAM API Configuration
DAM_BASE_URL = "http://52.172.198.122/samastt_QA1"
DAM_BID_ENDPOINT = "/api/dam/AddNewDAMBidService"
DAM_ORDERBOOK_ENDPOINT = "/api/dam/orderBookResponse"
DAM_AUTH = "ZCI6InNlY3JldF9jbGllbnRfaWQiLCJzY29wZSI6WyJhcGlzY29wZSJdfQ.iWLQUtWPmbW3GNyZW8Pimu-Kj-AC0D9_IAMRgh"
DAM_DEVICE = "postman"

class DAMAPIClient:
    def __init__(self):
        self.base_url = DAM_BASE_URL
        self.headers = {
            "Authorization": DAM_AUTH,
            "device": DAM_DEVICE,
            "Content-Type": "application/json"
        }
    
    def validate_bid_data(self, bid_data):
        """Validate bid data according to DAM API requirements"""
        errors = []

        # Validate main structure
        required_fields = ["portfoliocode", "user", "bidType", "type", "bidDate", "bid"]
        for field in required_fields:
            if field not in bid_data:
                errors.append(f"Missing required field: {field}")

        # Validate DAM bid date against 11 AM cutoff rule
        if "bidDate" in bid_data:
            try:
                bid_date_str = bid_data["bidDate"]
                bid_date = datetime.strptime(bid_date_str, "%d-%m-%Y").date()

                now = datetime.now()
                current_time = now.time()
                cutoff_time = datetime.strptime("11:00", "%H:%M").time()

                if current_time >= cutoff_time:
                    # After 11 AM - minimum bid date is day after tomorrow
                    min_allowed_date = now.date() + timedelta(days=2)
                else:
                    # Before 11 AM - minimum bid date is tomorrow
                    min_allowed_date = now.date() + timedelta(days=1)

                if bid_date < min_allowed_date:
                    current_time_str = now.strftime("%H:%M")
                    errors.append(f"Invalid bid date: Current time is {current_time_str}. Minimum allowed bid date is {min_allowed_date.strftime('%d-%m-%Y')}")

            except ValueError:
                errors.append("Invalid bid date format. Use DD-MM-YYYY format")

        # Business rule validation for Carry_Forward_To_RTM
        if "Carry_Forward_To_RTM" in bid_data and bid_data["Carry_Forward_To_RTM"] == "yes":
            bid_type = bid_data.get("bidType", "")
            main_type = bid_data.get("type", "")

            # Carry_Forward_To_RTM is not allowed for Single bid type with Sell
            if bid_type == "Single" and main_type == "Sell":
                errors.append("Carry_Forward_To_RTM is not allowed for Single bid type with Sell")

            # Additional business rules can be added here
            if bid_type == "Single" and main_type in ["Sell", "Both"]:
                errors.append(f"Carry_Forward_To_RTM is not allowed for Single bid type with {main_type}")

        # Validate 15-minute time block alignment
        valid_time_blocks = generate_15_minute_blocks()
        valid_times = set()
        for block in valid_time_blocks:
            valid_times.add(block[0])  # from time
            valid_times.add(block[1])  # to time

        if "bid" in bid_data:
            bids = bid_data["bid"]

            # Max 96 bid records
            if len(bids) > 96:
                errors.append(f"Too many bid records: {len(bids)} (max 96)")

            for i, bid in enumerate(bids):
                # Validate time format and logic
                if "fromtime" in bid and "totime" in bid:
                    from_time_str = bid["fromtime"]
                    to_time_str = bid["totime"]

                    try:
                        from_time = datetime.strptime(from_time_str, "%H:%M")
                        to_time = datetime.strptime(to_time_str, "%H:%M")
                        if from_time >= to_time:
                            errors.append(f"Bid {i}: fromtime must be less than totime")
                    except ValueError:
                        errors.append(f"Bid {i}: Invalid time format (use HH:MM)")

                    # Validate 15-minute alignment
                    if from_time_str not in valid_times:
                        errors.append(f"Bid {i}: fromtime '{from_time_str}' must align with 15-minute intervals")
                    if to_time_str not in valid_times:
                        errors.append(f"Bid {i}: totime '{to_time_str}' must align with 15-minute intervals")

                    # Validate that the time range is valid for DAM (must be proper 15-minute blocks)
                    if from_time_str in valid_times and to_time_str in valid_times:
                        # Check if it's a valid block combination
                        valid_block_found = False
                        for block in valid_time_blocks:
                            if block[0] == from_time_str:
                                # For single blocks, to_time should match block end
                                if bid_data.get("bidType") == "Single" and block[1] != to_time_str:
                                    # Allow only if it's exactly one 15-minute block
                                    pass
                                valid_block_found = True
                                break

                        if not valid_block_found:
                            errors.append(f"Bid {i}: Time range {from_time_str}-{to_time_str} is not a valid 15-minute block combination")

                # Validate bid values
                if "bidvalue" in bid:
                    bid_values = bid["bidvalue"]

                    # Must have at least one price-quantity pair
                    if len(bid_values) == 0:
                        errors.append(f"Bid {i}: Must have at least one price-quantity pair in bidvalue")

                    # Max 49 price points per bid
                    if len(bid_values) > 49:
                        errors.append(f"Bid {i}: Too many price points: {len(bid_values)} (max 49)")

                    for j, bid_value in enumerate(bid_values):
                        # Validate required fields in bidvalue
                        required_bv_fields = ["price", "value", "type"]
                        for field in required_bv_fields:
                            if field not in bid_value:
                                errors.append(f"Bid {i}, Price {j}: Missing required field '{field}' in bidvalue")

                        # Validate price (10-10000 Rs/KWh, up to 3 decimal places)
                        if "price" in bid_value:
                            try:
                                price = float(bid_value["price"])
                                if price < 10 or price > 10000:
                                    errors.append(f"Bid {i}, Price {j}: Price must be between 10-10000 Rs/KWh")
                                # Check decimal places
                                price_str = str(price)
                                if '.' in price_str and len(price_str.split('.')[-1]) > 3:
                                    errors.append(f"Bid {i}, Price {j}: Price can have max 3 decimal places")
                            except ValueError:
                                errors.append(f"Bid {i}, Price {j}: Invalid price format")

                        # Validate quantity (min 0.1 MW, 1 decimal place)
                        if "value" in bid_value:
                            try:
                                quantity = float(bid_value["value"])
                                if abs(quantity) < 0.1:
                                    errors.append(f"Bid {i}, Price {j}: Minimum quantity is 0.1 MW")
                                # Check decimal places
                                qty_str = str(abs(quantity))
                                if '.' in qty_str and len(qty_str.split('.')[-1]) > 1:
                                    errors.append(f"Bid {i}, Price {j}: Quantity can have max 1 decimal place")

                                # Only Sell bids allowed in DAM
                                if bid_value.get("type") not in ["S"]:
                                    errors.append(f"Bid {i}, Price {j}: Only Sell (S) bids are allowed in DAM")

                                # Sell bids must be positive (quantity represents MW to sell)
                                if bid_value.get("type") == "S" and quantity <= 0:
                                    errors.append(f"Bid {i}, Price {j}: Sell bid quantity must be positive")

                            except ValueError:
                                errors.append(f"Bid {i}, Price {j}: Invalid quantity format")

                        # Validate type consistency and DAM restrictions
                        if "type" in bid_value:
                            bv_type = bid_value["type"]
                            bid_type_from_entry = bid.get("type", "")

                            # Only allow Sell types in DAM
                            if bv_type not in ["S"]:
                                errors.append(f"Bid {i}, Price {j}: Only 'S' (Sell) type allowed in DAM, got '{bv_type}'")

                            if bid_type_from_entry not in ["S"]:
                                errors.append(f"Bid {i}: Only 'S' (Sell) type allowed in bid entry, got '{bid_type_from_entry}'")

                            if bv_type != bid_type_from_entry:
                                errors.append(f"Bid {i}, Price {j}: bidvalue type '{bv_type}' must match bid entry type '{bid_type_from_entry}'")

        return errors
    
    def submit_dam_bid(self, bid_data):
        """Submit DAM bid to API"""
        try:
            # Validate bid data first
            validation_errors = self.validate_bid_data(bid_data)
            if validation_errors:
                st.error("❌ Validation Errors:")
                for error in validation_errors:
                    st.error(f"• {error}")
                return {
                    "success": False,
                    "message": f"Validation failed: {'; '.join(validation_errors)}"
                }
            
            url = f"{self.base_url}{DAM_BID_ENDPOINT}"
            st.info(f"🚀 Submitting DAM bid to: {url}")
            
            # Show request details
            with st.expander("📦 Request Details"):
                st.write("**Request Payload:**")
                st.json(bid_data)
                st.write("**Request Headers:**")
                st.json(dict(self.headers))
            
            response = requests.post(
                url,
                json=bid_data,
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code == 200:
                try:
                    response_data = response.json() if response.content else {}
                except json.JSONDecodeError as e:
                    st.error("❌ Invalid JSON response from API")
                    st.write("🔍 **Response Details:**")
                    st.write(f"**Status Code:** {response.status_code}")
                    st.write(f"**Raw Response:** {response.text[:1000]}...")
                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "response": response.text,
                        "message": f"Invalid JSON response: {str(e)}"
                    }

                # Check for successful submission
                if response_data.get("result") == True:
                    st.success("✅ DAM Bid submitted successfully!")
                    st.json(response_data)
                    return {
                        "success": True,
                        "status_code": response.status_code,
                        "response": response_data,
                        "message": response_data.get("message", "DAM Bid submitted successfully")
                    }
                else:
                    # Enhanced error reporting for failed bids
                    error_msg = response_data.get('message', 'Unknown error')
                    error_details = response_data.get('Error', '')
                    status = response_data.get('Status', 'Unknown')

                    st.error(f"❌ DAM Bid submission failed")

                    # Show detailed error information
                    with st.expander("🔍 Error Details", expanded=True):
                        st.write(f"**Status:** {status}")
                        st.write(f"**Error Message:** {error_msg}")
                        if error_details:
                            st.write(f"**Error Details:** {error_details}")
                        st.write(f"**HTTP Status:** {response.status_code}")
                        st.write("**Full Response:**")
                        st.json(response_data)

                    # Create detailed error message
                    detailed_message = f"Status: {status}"
                    if error_msg and error_msg != 'Unknown error':
                        detailed_message += f", Message: {error_msg}"
                    if error_details:
                        detailed_message += f", Details: {error_details}"

                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "response": response_data,
                        "message": detailed_message,
                        "api_status": status,
                        "api_error": error_msg,
                        "api_details": error_details
                    }
            else:
                st.error(f"❌ API Error: {response.status_code}")
                
                # Show detailed error information
                st.write("🔍 **Error Details:**")
                st.write(f"**Status Code:** {response.status_code}")
                st.write(f"**Response Headers:**")
                st.json(dict(response.headers))
                st.write(f"**Response Body:**")
                st.text(response.text)
                st.write(f"**Request URL:** {response.url}")
                
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "response": response.text,
                    "message": f"API returned status code {response.status_code}"
                }
                
        except requests.exceptions.Timeout:
            st.error("⏰ Request timeout - API took too long to respond")
            return {
                "success": False,
                "message": "Request timeout"
            }
        except requests.exceptions.ConnectionError:
            st.error("🔌 Connection error - Unable to reach DAM API")
            return {
                "success": False,
                "message": "Connection error"
            }
        except Exception as e:
            st.error(f"💥 Unexpected error: {str(e)}")
            return {
                "success": False,
                "message": f"Unexpected error: {str(e)}"
            }

    def get_orderbook(self, portfolio_code, user, bid_type, bid_date):
        """Get DAM orderbook response"""
        try:
            url = f"{self.base_url}{DAM_ORDERBOOK_ENDPOINT}"

            # Prepare request body
            request_body = {
                "portfoliocode": portfolio_code,
                "user": user,
                "bidType": bid_type,
                "bidDate": bid_date
            }

            st.info(f"🔍 Fetching orderbook from: {url}")

            # Show request details
            with st.expander("📦 Orderbook Request Details"):
                st.write("**Request Payload:**")
                st.json(request_body)
                st.write("**Request Headers:**")
                st.json(dict(self.headers))

            response = requests.get(
                url,
                json=request_body,
                headers=self.headers,
                timeout=30
            )

            if response.status_code == 200:
                response_data = response.json() if response.content else {}
                st.success("✅ Orderbook retrieved successfully!")
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "response": response_data
                }
            else:
                st.error(f"❌ Orderbook API Error: {response.status_code}")
                st.write("🔍 **Error Details:**")
                st.write(f"**Status Code:** {response.status_code}")
                st.write(f"**Response Body:**")
                st.text(response.text)

                return {
                    "success": False,
                    "status_code": response.status_code,
                    "response": response.text
                }

        except Exception as e:
            st.error(f"💥 Orderbook error: {str(e)}")
            return {
                "success": False,
                "message": f"Orderbook error: {str(e)}"
            }

class FTPScheduleProcessor:
    def __init__(self):
        self.ftp_host = "122.15.71.85"
        self.ftp_user = "cipc"
        self.ftp_pass = "cipc@123"
        self.ftp_port = 21
        
    def connect_ftp(self):
        """Connect to FTP server"""
        try:
            ftp = ftplib.FTP()
            ftp.connect(self.ftp_host, self.ftp_port)
            ftp.login(self.ftp_user, self.ftp_pass)
            return ftp
        except Exception as e:
            st.error(f"FTP Connection Error: {str(e)}")
            return None
    
    def get_folders(self):
        """Get list of folders from FTP"""
        ftp = self.connect_ftp()
        if not ftp:
            return []
        
        try:
            folders = []
            items = ftp.nlst()
            for item in items:
                try:
                    ftp.cwd(item)
                    folders.append(item)
                    ftp.cwd('..')
                except:
                    continue
            ftp.quit()
            return sorted(folders)
        except Exception as e:
            st.error(f"Error getting folders: {str(e)}")
            return []
    
    def generate_time_blocks(self):
        """Generate 96 time blocks for 24 hours (15-minute intervals)"""
        time_blocks = []
        for hour in range(24):
            for minute in [0, 15, 30, 45]:
                from_time = f"{hour:02d}:{minute:02d}"
                to_minute = minute + 15
                to_hour = hour
                if to_minute >= 60:
                    to_minute = 0
                    to_hour += 1

                # Special case for the last block: 23:45-24:00 instead of 23:45-00:00
                if hour == 23 and minute == 45:
                    to_time = "24:00"
                elif to_hour >= 24:
                    to_hour = 0
                    to_time = f"{to_hour:02d}:{to_minute:02d}"
                else:
                    to_time = f"{to_hour:02d}:{to_minute:02d}"

                time_blocks.append((from_time, to_time))
        return time_blocks

    def load_cipc_mapping(self):
        """Load CIPC mapping from Excel file"""
        try:
            if os.path.exists("CIPIC_Mapping.xlsx"):
                df = pd.read_excel("CIPIC_Mapping.xlsx")
                mapping = {}
                for _, row in df.iterrows():
                    folder = str(row.iloc[0]).strip()
                    portfolio = str(row.iloc[1]).strip()
                    user = str(row.iloc[2]).strip()
                    mapping[folder] = {
                        'portfolio_code': portfolio,
                        'user_id': user
                    }
                return mapping
            else:
                st.warning("⚠️ CIPIC_Mapping.xlsx not found. Please upload the mapping file.")
                return {}
        except Exception as e:
            st.error(f"Error loading CIPC mapping: {str(e)}")
            return {}

    def find_schedule_file(self, ftp, folder, target_date):
        """Find schedule file for the given date"""
        try:
            ftp.cwd(folder)
            files = ftp.nlst()

            date_str = target_date.strftime("%d%m%Y")
            schedule_pattern = f"Schedule_{date_str}.xlsx"

            for file in files:
                if file == schedule_pattern:
                    ftp.cwd('..')
                    return file

            ftp.cwd('..')
            return None
        except Exception as e:
            st.error(f"Error finding schedule file: {str(e)}")
            return None

    def find_iex_file(self, ftp, folder, target_date):
        """Find IEX file for the given date"""
        try:
            ftp.cwd(folder)
            files = ftp.nlst()

            date_str = target_date.strftime("%d%m%Y")
            iex_pattern = f"IEX_{date_str}.xlsx"
            market_pattern = target_date.strftime("%y%m%d")

            for file in files:
                if file == iex_pattern or f"IEX{market_pattern}SCH" in file.upper():
                    ftp.cwd('..')
                    return file

            ftp.cwd('..')
            return None
        except Exception as e:
            st.error(f"Error finding IEX file: {str(e)}")
            return None

    def download_and_read_file(self, filename, sheet_range):
        """Download file from FTP and read data"""
        ftp = self.connect_ftp()
        if not ftp:
            return None

        try:
            # Download file to memory
            file_data = io.BytesIO()
            ftp.retrbinary(f'RETR {filename}', file_data.write)
            file_data.seek(0)

            df = read_table_from_buffer(file_data, filename)
            values = extract_values_from_dataframe(
                df,
                sheet_range,
                absolute=sheet_range.strip().upper().startswith("F"),
            )

            ftp.quit()
            return values

        except Exception as e:
            st.error(f"Error reading file {filename}: {str(e)}")
            ftp.quit()
            return None

    def generate_dam_bid_json(self, differences, portfolio_code, user_id, bid_date):
        """Generate DAM bid JSON output for sell bids only (positive differences)"""
        time_blocks = self.generate_time_blocks()
        bid_entries = []

        for i, diff in enumerate(differences):
            if diff > 0:  # Only positive values (sell bids)
                from_time, to_time = time_blocks[i]
                bid_entry = {
                    "fromtime": from_time,
                    "totime": to_time,
                    "type": "S",
                    "bidvalue": [
                        {
                            "price": "50.0",  # Default sell price for DAM
                            "value": f"{diff:.1f}",
                            "type": "S"
                        }
                    ]
                }
                bid_entries.append(bid_entry)

        json_output = {
            "portfoliocode": str(portfolio_code),
            "user": str(user_id),
            "bidType": "Block",
            "type": "Sell",  # DAM will always be sell only
            "bidDate": bid_date.strftime("%d-%m-%Y"),
            "Carry_Forward_To_RTM": "no",  # Block Sell can have carry forward, but default to no
            "Price_Variation": "0",
            "bid": bid_entries
        }

        return json_output

def calculate_min_dam_bid_date():
    """Calculate minimum allowed DAM bid date based on 11 AM cutoff rule"""
    now = datetime.now()
    current_time = now.time()
    cutoff_time = datetime.strptime("11:00", "%H:%M").time()

    if current_time >= cutoff_time:
        # After 11 AM - minimum bid date is day after tomorrow
        return now.date() + timedelta(days=2)
    else:
        # Before 11 AM - minimum bid date is tomorrow
        return now.date() + timedelta(days=1)

def generate_15_minute_blocks():
    """Generate 96 time blocks for 24 hours (15-minute intervals) - same as RTM"""
    time_blocks = []
    for hour in range(24):
        for minute in [0, 15, 30, 45]:
            from_time = f"{hour:02d}:{minute:02d}"
            to_minute = minute + 15
            to_hour = hour
            if to_minute >= 60:
                to_minute = 0
                to_hour += 1

            # Special case for the last block: 23:45-24:00 instead of 23:45-00:00
            if hour == 23 and minute == 45:
                to_time = "24:00"
            elif to_hour >= 24:
                to_hour = 0
                to_time = f"{to_hour:02d}:{to_minute:02d}"
            else:
                to_time = f"{to_hour:02d}:{to_minute:02d}"

            time_blocks.append((from_time, to_time))
    return time_blocks

@st.cache_resource
def get_processor():
    """Create and cache the FTP processor"""
    return FTPScheduleProcessor()

def main():
    st.set_page_config(
        page_title="DAM API Testing & Bid Processor",
        page_icon="⚡",
        layout="wide"
    )

    st.title("⚡ DAM (Day-Ahead Market) API Testing & Bid Processor")
    st.markdown("Test DAM APIs and process FTP schedule files for bid submission")

    # Sidebar for navigation
    st.sidebar.title("🧭 Navigation")
    page = st.sidebar.selectbox(
        "Select Page",
        ["🧪 DAM API Testing", "📊 FTP Schedule Processing"],
        index=0
    )

    if page == "🧪 DAM API Testing":
        dam_api_testing_page()
    else:
        ftp_processing_page()

def dam_api_testing_page():
    """DAM API Testing Interface"""
    st.header("🧪 DAM API Testing Interface")
    st.markdown("Test the DAM APIs with custom parameters and view responses")

    # Initialize DAM client
    dam_client = DAMAPIClient()

    # API Configuration Display
    with st.expander("🔧 API Configuration", expanded=False):
        st.write("**Base URL:**", DAM_BASE_URL)
        st.write("**Bid Endpoint:**", DAM_BID_ENDPOINT)
        st.write("**Orderbook Endpoint:**", DAM_ORDERBOOK_ENDPOINT)
        st.write("**Authorization:**", DAM_AUTH[:50] + "..." if len(DAM_AUTH) > 50 else DAM_AUTH)
        st.write("**Device:**", DAM_DEVICE)

    # Business Rules Display
    with st.expander("📋 DAM API Business Rules", expanded=False):
        st.markdown("""
        ### 🚫 **Carry Forward to RTM Restrictions**
        - ❌ **Single + Sell**: Not allowed
        - ❌ **Single + Both**: Not allowed
        - ✅ **Single + Buy**: Allowed
        - ✅ **Block + Any Type**: Allowed

        ### 💰 **Price & Quantity Rules**
        - **Price Range**: 10-10000 Rs/KWh (max 3 decimal places)
        - **Quantity**: Minimum 0.1 MW (max 1 decimal place)
        - **Bid Type**: Only Sell (S) bids allowed in DAM
        - **Sell Bids**: Quantity must be positive (represents MW to sell)

        ### ⏰ **Time & Limit Rules**
        - **Time Format**: HH:MM (24-hour) - Must align with 15-minute intervals
        - **15-minute Blocks**: 00:00, 00:15, 00:30, 00:45, 01:00, etc.
        - **Time Logic**: fromtime < totime
        - **Max Bid Records**: 96 per submission (24 hours × 4 blocks/hour)
        - **Max Price Points**: 49 per bid record
        - **No Overlapping**: Time periods cannot overlap

        ### � **DAM Bid Date Rules**
        - **11:00 AM Cutoff**: Bids must be submitted before 11:00 AM
        - **Before 11:00 AM**: Can bid for next day (T+1)
        - **After 11:00 AM**: Can only bid for day after tomorrow (T+2)
        - **Example**: If today is 7th Oct and time is 2:00 PM, earliest bid date is 9th Oct

        ### �🔄 **Bid Type Rules**
        - **Single**: One-time bid for specific time slot (typically 15-minute blocks)
        - **Block**: Multi-hour block bid (multiple 15-minute intervals)
        - **Buy/Sell/Both**: Determines allowed operations

        ### 📅 **15-Minute Time Block Examples**
        - **Single Block**: 14:30-14:45, 16:00-16:15, 21:45-22:00
        - **Multi Block**: 10:00-12:00 (8 blocks), 20:00-22:00 (8 blocks)
        - **Valid Times**: :00, :15, :30, :45 minutes only
        """)

    # Time Block Reference
    with st.expander("🕐 15-Minute Time Block Reference", expanded=False):
        st.markdown("### 📋 All Valid 15-Minute Time Blocks")

        # Display time blocks in a nice format
        time_blocks = generate_15_minute_blocks()

        # Group by hour for better display
        col1, col2, col3, col4 = st.columns(4)

        for i, (from_time, to_time) in enumerate(time_blocks):
            hour = int(from_time.split(':')[0])
            col_idx = i % 4

            if col_idx == 0:
                with col1:
                    if i < 24:  # First 6 hours
                        st.write(f"**{from_time} - {to_time}**")
            elif col_idx == 1:
                with col2:
                    if 24 <= i < 48:  # Next 6 hours
                        st.write(f"**{from_time} - {to_time}**")
            elif col_idx == 2:
                with col3:
                    if 48 <= i < 72:  # Next 6 hours
                        st.write(f"**{from_time} - {to_time}**")
            else:
                with col4:
                    if 72 <= i < 96:  # Last 6 hours
                        st.write(f"**{from_time} - {to_time}**")

        st.info("💡 **Tip**: Use these exact time values in your bid entries for proper 15-minute alignment")


    # Tab layout for different API operations
    tab1, tab2, tab3 = st.tabs(["📤 Submit DAM Bid", "📋 Get Orderbook", "🎯 Quick Test"])

    with tab1:
        st.subheader("📤 Submit New DAM Bid")

        # Basic bid parameters
        col1, col2 = st.columns(2)

        with col1:
            portfolio_code = st.text_input("Portfolio Code", value="E1WB0TPT0008", help="Portfolio code for the bid")
            user = st.text_input("User", value="deepraj", help="User ID (always use 'deepraj' for RTM)")
            bid_type = st.selectbox("Bid Type", ["Single", "Block"], help="Type of bid")

        with col2:
            bid_main_type = st.selectbox("Main Type", ["Sell"], help="DAM only supports Sell bids", disabled=True)

            # Calculate minimum allowed bid date based on 11 AM cutoff
            min_bid_date = calculate_min_dam_bid_date()
            now = datetime.now()
            current_time = now.time()
            cutoff_time = datetime.strptime("11:00", "%H:%M").time()

            if current_time >= cutoff_time:
                cutoff_message = f"⏰ After 11:00 AM - Earliest bid date: {min_bid_date.strftime('%d-%m-%Y')}"
            else:
                cutoff_message = f"⏰ Before 11:00 AM - Earliest bid date: {min_bid_date.strftime('%d-%m-%Y')}"

            st.info(cutoff_message)

            bid_date = st.date_input(
                "Bid Date",
                value=min_bid_date,
                min_value=min_bid_date,
                help="DAM bids must be submitted before 11:00 AM for next day delivery"
            )

            # DAM only supports Sell bids - no carry forward allowed
            st.info("ℹ️ **Carry Forward to RTM**: Not applicable for DAM Sell bids")
            carry_forward = "no"  # Always no for DAM

            price_variation = st.text_input("Price Variation", value="0", help="Price variation parameter")

        # Display DAM-specific business rule warnings
        st.warning("⚠️ **DAM Business Rules**: Only Sell bids allowed | Price range: 10-10000 Rs/KWh | No Carry Forward to RTM")

        st.divider()

        # Bid entries section
        st.subheader("⏰ Time Block Bids")

        # Initialize session state for bid entries
        if 'dam_bid_entries' not in st.session_state:
            st.session_state.dam_bid_entries = []

        # Add new bid entry with 15-minute time blocks
        with st.expander("➕ Add New Bid Entry (15-minute blocks)", expanded=True):
            col1, col2, col3 = st.columns(3)

            with col1:
                st.write("**Select Time Block:**")
                # Generate 15-minute time block options
                time_blocks = generate_15_minute_blocks()
                time_block_options = [f"{block[0]} - {block[1]}" for block in time_blocks]

                selected_block = st.selectbox(
                    "Time Block",
                    time_block_options,
                    index=58,  # Default to 14:30-14:45
                    help="Select a 15-minute time block"
                )

                # Parse selected block
                selected_index = time_block_options.index(selected_block)
                from_time_str, to_time_str = time_blocks[selected_index]

                st.info(f"Selected: {from_time_str} to {to_time_str}")

            with col2:
                entry_type = st.selectbox("Entry Type", ["S"], help="DAM only supports Sell (S) bids", disabled=True)

                # Option for custom time range (multiple blocks)
                use_custom_range = st.checkbox("Custom Range", help="Select multiple consecutive blocks - creates separate 15-min entries")

                if use_custom_range:
                    end_block = st.selectbox(
                        "End Block",
                        time_block_options[selected_index+1:selected_index+25],  # Max 6 hours
                        help="Select ending time block"
                    )
                    end_index = time_block_options.index(end_block)
                    to_time_str = time_blocks[end_index][1]
                    blocks_count = end_index - selected_index + 1
                    st.info(f"Range: {from_time_str} to {to_time_str} ({blocks_count} blocks)")
                    st.warning(f"⚠️ Will create {blocks_count} separate 15-minute bid entries")

            with col3:
                # Default price and quantity for new entry
                default_price = st.number_input("Default Price", min_value=10.0, max_value=10000.0, value=50.0, step=0.001, key="default_price")
                default_qty = st.number_input("Default Quantity", min_value=0.1, value=10.0, step=0.1, key="default_qty")

                if st.button("➕ Add Bid Entry"):
                    if use_custom_range:
                        # For custom range, create separate entries for each 15-minute block
                        start_idx = selected_index
                        end_idx = time_block_options.index(end_block)

                        entries_added = 0
                        for block_idx in range(start_idx, end_idx + 1):
                            block_from_time, block_to_time = time_blocks[block_idx]

                            new_entry = {
                                "fromtime": block_from_time,
                                "totime": block_to_time,
                                "type": entry_type,
                                "bidvalue": [
                                    {
                                        "price": str(default_price),
                                        "value": str(default_qty),
                                        "type": entry_type
                                    }
                                ]
                            }
                            st.session_state.dam_bid_entries.append(new_entry)
                            entries_added += 1

                        st.success(f"✅ Added {entries_added} bid entries for custom range!")
                    else:
                        # Single 15-minute block entry
                        new_entry = {
                            "fromtime": from_time_str,
                            "totime": to_time_str,
                            "type": entry_type,
                            "bidvalue": [
                                {
                                    "price": str(default_price),
                                    "value": str(default_qty),
                                    "type": entry_type
                                }
                            ]
                        }
                        st.session_state.dam_bid_entries.append(new_entry)
                        st.success("✅ Bid entry added!")

                    st.rerun()

        # Display and manage existing bid entries
        if st.session_state.dam_bid_entries:
            st.subheader("📋 Current Bid Entries")

            for i, entry in enumerate(st.session_state.dam_bid_entries):
                with st.expander(f"🕐 {entry['fromtime']} - {entry['totime']} ({entry['type']})", expanded=False):
                    col1, col2 = st.columns([3, 1])

                    with col1:
                        st.write(f"**Time:** {entry['fromtime']} - {entry['totime']}")
                        st.write(f"**Type:** {'Buy' if entry['type'] == 'B' else 'Sell'}")

                        # Price-quantity pairs for this entry
                        st.write("**Price-Quantity Pairs:**")

                        # Add price-quantity pair
                        pcol1, pcol2, pcol3, pcol4 = st.columns(4)
                        with pcol1:
                            price = st.number_input(f"Price (Rs/KWh)", min_value=10.0, max_value=10000.0, value=50.0, step=0.001, key=f"price_{i}")
                        with pcol2:
                            quantity = st.number_input(f"Quantity (MW)", min_value=0.1, value=10.0, step=0.1, key=f"qty_{i}")
                        with pcol3:
                            pq_type = st.selectbox("Type", ["S"], index=0, key=f"pqtype_{i}", disabled=True, help="DAM only supports Sell (S) bids")
                        with pcol4:
                            if st.button("➕ Add P-Q", key=f"addpq_{i}"):
                                new_pq = {
                                    "price": str(price),
                                    "value": str(quantity),
                                    "type": pq_type
                                }
                                st.session_state.dam_bid_entries[i]["bidvalue"].append(new_pq)
                                st.success("✅ Price-Quantity added!")

                        # Display existing price-quantity pairs
                        if entry["bidvalue"]:
                            pq_df = pd.DataFrame(entry["bidvalue"])
                            st.dataframe(pq_df, use_container_width=True)

                            # Option to remove price-quantity pairs
                            if len(entry["bidvalue"]) > 1:  # Keep at least one
                                remove_pq_idx = st.selectbox(
                                    "Remove P-Q Pair",
                                    range(len(entry["bidvalue"])),
                                    format_func=lambda x: f"Price: {entry['bidvalue'][x]['price']}, Qty: {entry['bidvalue'][x]['value']}",
                                    key=f"remove_pq_{i}"
                                )
                                if st.button("🗑️ Remove P-Q", key=f"remove_pq_btn_{i}"):
                                    st.session_state.dam_bid_entries[i]["bidvalue"].pop(remove_pq_idx)
                                    st.rerun()
                        else:
                            st.warning("⚠️ No price-quantity pairs! Add at least one.")

                    with col2:
                        if st.button("🗑️ Remove", key=f"remove_{i}"):
                            st.session_state.dam_bid_entries.pop(i)
                            st.rerun()

        # Bulk entry creation
        if st.session_state.dam_bid_entries:
            st.divider()
            with st.expander("⚡ Bulk Entry Creation", expanded=False):
                st.write("Create multiple consecutive 15-minute block entries with same price-quantity")
                st.info("ℹ️ Each time block creates a separate bid entry (required by DAM API)")

                bcol1, bcol2, bcol3 = st.columns(3)

                with bcol1:
                    time_blocks = generate_15_minute_blocks()
                    time_block_options = [f"{block[0]} - {block[1]}" for block in time_blocks]

                    bulk_start = st.selectbox("Start Block", time_block_options, key="bulk_start")
                    bulk_count = st.number_input("Number of Blocks", min_value=1, max_value=20, value=4, key="bulk_count")

                with bcol2:
                    bulk_type = st.selectbox("Bulk Type", ["S"], key="bulk_type", disabled=True, help="DAM only supports Sell (S) bids")
                    bulk_price = st.number_input("Bulk Price", min_value=10.0, max_value=10000.0, value=50.0, step=0.001, key="bulk_price")
                    bulk_qty = st.number_input("Bulk Quantity", min_value=0.1, value=10.0, step=0.1, key="bulk_qty")

                with bcol3:
                    st.write("") # Spacer
                    if st.button("➕ Create Bulk Entries", key="bulk_create"):
                        start_idx = time_block_options.index(bulk_start)
                        entries_created = 0

                        for i in range(bulk_count):
                            if start_idx + i < len(time_blocks):
                                from_time, to_time = time_blocks[start_idx + i]

                                bulk_entry = {
                                    "fromtime": from_time,
                                    "totime": to_time,
                                    "type": bulk_type,
                                    "bidvalue": [
                                        {
                                            "price": str(bulk_price),
                                            "value": str(bulk_qty),
                                            "type": bulk_type
                                        }
                                    ]
                                }
                                st.session_state.dam_bid_entries.append(bulk_entry)
                                entries_created += 1

                        st.success(f"✅ Created {entries_created} individual 15-minute block entries!")
                        st.rerun()

        # Clear all entries
        if st.session_state.dam_bid_entries:
            if st.button("🗑️ Clear All Entries", type="secondary"):
                st.session_state.dam_bid_entries = []
                st.rerun()

        st.divider()

        # Submit bid and download options
        submit_disabled = not st.session_state.dam_bid_entries

        # Check if all entries have bidvalue data
        if st.session_state.dam_bid_entries:
            for entry in st.session_state.dam_bid_entries:
                if not entry.get("bidvalue") or len(entry["bidvalue"]) == 0:
                    submit_disabled = True
                    st.error("❌ All bid entries must have at least one price-quantity pair!")
                    break

        # Action buttons
        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("🚀 Submit DAM Bid", type="primary", disabled=submit_disabled):
                submit_bid_action = True
            else:
                submit_bid_action = False

        with col2:
            download_disabled = not st.session_state.dam_bid_entries or submit_disabled
            if st.button("📥 Download JSON", disabled=download_disabled):
                # Generate the final payload for download
                valid_entries = []
                for entry in st.session_state.dam_bid_entries:
                    if entry.get("bidvalue") and len(entry["bidvalue"]) > 0:
                        # Ensure all bidvalue entries have proper format
                        cleaned_bidvalues = []
                        for bv in entry["bidvalue"]:
                            if bv.get("price") and bv.get("value") and bv.get("type"):
                                cleaned_bidvalues.append(bv)

                        if cleaned_bidvalues:
                            entry_copy = entry.copy()
                            entry_copy["bidvalue"] = cleaned_bidvalues
                            valid_entries.append(entry_copy)

                if valid_entries:
                    bid_payload = {
                        "portfoliocode": portfolio_code,
                        "user": "deepraj",
                        "bidType": bid_type,
                        "type": bid_main_type,
                        "bidDate": bid_date.strftime("%d-%m-%Y"),
                        "Price_Variation": "0",
                        "bid": valid_entries
                    }

                    # Convert to JSON string with proper formatting
                    json_str = json.dumps(bid_payload, indent=2)

                    # Create download
                    filename = f"dam_bid_{bid_date.strftime('%Y%m%d')}_{bid_type.lower()}_{bid_main_type.lower()}.json"
                    st.download_button(
                        label="💾 Download Payload",
                        data=json_str,
                        file_name=filename,
                        mime="application/json",
                        help="Download the complete DAM bid payload as JSON file",
                        key="download_payload"
                    )
                else:
                    st.error("❌ No valid entries to download!")

        with col3:
            if st.button("🔄 Clear All Entries"):
                st.session_state.dam_bid_entries = []
                st.success("✅ All entries cleared!")
                st.rerun()

        if submit_bid_action:
            # Validate all entries have proper bidvalue data
            valid_entries = []
            for entry in st.session_state.dam_bid_entries:
                if entry.get("bidvalue") and len(entry["bidvalue"]) > 0:
                    # Ensure all bidvalue entries have proper format
                    valid_bidvalues = []
                    for bv in entry["bidvalue"]:
                        if "price" in bv and "value" in bv and "type" in bv:
                            valid_bidvalues.append(bv)

                    if valid_bidvalues:
                        entry_copy = entry.copy()
                        entry_copy["bidvalue"] = valid_bidvalues
                        valid_entries.append(entry_copy)

            if not valid_entries:
                st.error("❌ No valid bid entries found! Each entry must have price-quantity pairs.")
                return

            # Construct bid payload
            bid_payload = {
                "portfoliocode": portfolio_code,
                "user": user,
                "bidType": bid_type,
                "type": bid_main_type,
                "bidDate": bid_date.strftime("%d-%m-%Y"),
                "Carry_Forward_To_RTM": carry_forward,
                "Price_Variation": price_variation,
                "bid": valid_entries
            }

            # Show final payload before submission
            with st.expander("📦 Final Bid Payload", expanded=False):
                st.json(bid_payload)

            # Submit the bid
            result = dam_client.submit_dam_bid(bid_payload)

            if result["success"]:
                st.balloons()
                st.success("🎉 DAM Bid submitted successfully!")

                # Show success details
                if result.get("response"):
                    with st.expander("✅ Success Details", expanded=False):
                        st.json(result["response"])
            else:
                st.error("❌ DAM Bid submission failed!")

                # Show detailed error information
                with st.expander("🔍 Failure Details", expanded=True):
                    st.write(f"**Error Message:** {result.get('message', 'Unknown error')}")

                    if result.get('api_status'):
                        st.write(f"**API Status:** {result['api_status']}")
                    if result.get('api_error'):
                        st.write(f"**API Error:** {result['api_error']}")
                    if result.get('api_details'):
                        st.write(f"**API Details:** {result['api_details']}")
                    if result.get('status_code'):
                        st.write(f"**HTTP Status Code:** {result['status_code']}")

                    if result.get("response"):
                        st.write("**Full API Response:**")
                        if isinstance(result["response"], str):
                            st.text(result["response"])
                        else:
                            st.json(result["response"])

    with tab2:
        st.subheader("📋 Get DAM Orderbook")

        # Orderbook parameters
        col1, col2 = st.columns(2)

        with col1:
            ob_portfolio = st.text_input("Portfolio Code", value="E1WB0TPT0008", key="ob_portfolio")
            ob_user = st.text_input("User", value="deepraj", key="ob_user")

        with col2:
            ob_bid_type = st.selectbox("Bid Type", ["Single", "Block"], key="ob_bid_type")
            ob_bid_date = st.date_input("Bid Date", value=datetime.now().date(), key="ob_bid_date")

        if st.button("📋 Get Orderbook", type="primary"):
            result = dam_client.get_orderbook(
                ob_portfolio,
                ob_user,
                ob_bid_type,
                ob_bid_date.strftime("%d-%m-%Y")
            )

            if result["success"]:
                st.success("✅ Orderbook retrieved successfully!")

                # Display orderbook data
                response_data = result["response"]

                if response_data:
                    st.subheader("📊 Orderbook Data")
                    st.json(response_data)

                    # Try to parse and display in table format if possible
                    if isinstance(response_data, dict) and "orders" in response_data:
                        orders = response_data["orders"]
                        if orders:
                            df = pd.DataFrame(orders)
                            st.dataframe(df, use_container_width=True)
                else:
                    st.info("ℹ️ No orderbook data returned")
            else:
                st.error(f"❌ Failed to retrieve orderbook: {result.get('message', 'Unknown error')}")

    with tab3:
        st.subheader("🎯 Quick API Test")
        st.markdown("Test the DAM API with predefined sample data")

        # Sample data options
        sample_type = st.selectbox(
            "Select Sample Data",
            ["Single Sell Bid", "Block Sell Bid"]
        )

        # Generate sample data based on selection
        sample_data = generate_sample_bid_data(sample_type)

        # Display sample data
        with st.expander("📦 Sample Bid Data", expanded=True):
            st.json(sample_data)

        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("🚀 Test Submit Bid", type="primary"):
                result = dam_client.submit_dam_bid(sample_data)
                if result["success"]:
                    st.success("✅ Sample bid submitted successfully!")
                    if result.get("response"):
                        with st.expander("✅ Success Details", expanded=False):
                            st.json(result["response"])
                else:
                    st.error("❌ Sample bid submission failed!")

                    # Show detailed error information
                    with st.expander("🔍 Error Details", expanded=True):
                        st.write(f"**Error Message:** {result.get('message', 'Unknown error')}")

                        if result.get('api_status'):
                            st.write(f"**API Status:** {result['api_status']}")
                        if result.get('api_error'):
                            st.write(f"**API Error:** {result['api_error']}")
                        if result.get('api_details'):
                            st.write(f"**API Details:** {result['api_details']}")
                        if result.get('status_code'):
                            st.write(f"**HTTP Status Code:** {result['status_code']}")

                        if result.get("response"):
                            st.write("**Full API Response:**")
                            if isinstance(result["response"], str):
                                st.text(result["response"])
                            else:
                                st.json(result["response"])

        with col2:
            # Download sample data as JSON
            json_str = json.dumps(sample_data, indent=2)
            filename = f"sample_{sample_type.lower().replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            st.download_button(
                label="📥 Download Sample JSON",
                data=json_str,
                file_name=filename,
                mime="application/json",
                help="Download the sample bid payload as JSON file"
            )

        with col3:
            if st.button("📋 Test Get Orderbook", type="secondary"):
                result = dam_client.get_orderbook(
                    sample_data["portfoliocode"],
                    sample_data["user"],
                    sample_data["bidType"],
                    sample_data["bidDate"]
                )
                if result["success"]:
                    st.success("✅ Orderbook retrieved successfully!")
                    st.json(result["response"])
                else:
                    st.error(f"❌ Orderbook retrieval failed: {result.get('message', 'Unknown error')}")

def generate_sample_bid_data(sample_type):
    """Generate sample bid data based on type"""
    # Calculate correct bid date based on 11 AM cutoff
    min_bid_date = calculate_min_dam_bid_date()
    base_date = min_bid_date.strftime("%d-%m-%Y")

    samples = {
        "Single Sell Bid": {
            "portfoliocode": "E1WB0TPT0008",
            "user": "deepraj",
            "bidType": "Single",
            "type": "Sell",
            "bidDate": base_date,

            "Price_Variation": "0",
            "bid": [
                {
                    "fromtime": "16:00",
                    "totime": "16:15",  # 15-minute block
                    "type": "S",
                    "bidvalue": [
                        {
                            "price": "50.0",
                            "value": "15.0",
                            "type": "S"
                        }
                    ]
                }
            ]
        },

        "Block Sell Bid": {
            "portfoliocode": "E1WB0TPT0008",
            "user": "deepraj",
            "bidType": "Block",
            "type": "Sell",
            "bidDate": base_date,

            "Price_Variation": "0",
            "bid": [
                {
                    "fromtime": "20:00",
                    "totime": "22:00",  # 2-hour block (8 x 15-minute intervals)
                    "type": "S",
                    "bidvalue": [
                        {
                            "price": "60.0",
                            "value": "30.0",
                            "type": "S"
                        }
                    ]
                }
            ]
        }
    }

    return samples.get(sample_type, samples["Single Sell Bid"])

def ftp_processing_page():
    """FTP Schedule Processing Interface"""
    st.header("📊 FTP Schedule Processing")
    st.markdown("Process FTP schedule files and submit DAM bids")

    # Initialize processor
    processor = get_processor()

    # Date selection with 11 AM cutoff logic
    st.subheader("📅 Select Target Date")

    # Calculate minimum allowed bid date based on 11 AM cutoff
    min_bid_date = calculate_min_dam_bid_date()
    now = datetime.now()
    current_time = now.time()
    cutoff_time = datetime.strptime("11:00", "%H:%M").time()

    if current_time >= cutoff_time:
        cutoff_message = f"⏰ After 11:00 AM - Earliest DAM bid date: {min_bid_date.strftime('%d-%m-%Y')}"
    else:
        cutoff_message = f"⏰ Before 11:00 AM - Earliest DAM bid date: {min_bid_date.strftime('%d-%m-%Y')}"

    st.info(cutoff_message)

    target_date = st.date_input(
        "Select date for processing",
        value=min_bid_date,
        min_value=min_bid_date,
        help="DAM bids must be submitted before 11:00 AM for next day delivery"
    )

    # Quick DAM API Test Section
    with st.expander("🧪 Quick DAM API Test", expanded=False):
        st.subheader("Test DAM API with Sample Data")

        if st.button("🚀 Test DAM API"):
            # Sample DAM bid data
            test_bid = {
                "portfoliocode": "E1WB0TPT0008",
                "user": "deepraj",
                "bidType": "Single",
                "type": "Sell",
                "bidDate": target_date.strftime("%d-%m-%Y"),
                "Carry_Forward_To_RTM": "yes",
                "Price_Variation": "0",
                "bid": [
                    {
                        "fromtime": "14:30",
                        "totime": "15:30",
                        "type": "S",
                        "bidvalue": [
                            {
                                "price": "5.0",
                                "value": "10.0",
                                "type": "S"
                            }
                        ]
                    }
                ]
            }

            dam_client = DAMAPIClient()
            result = dam_client.submit_dam_bid(test_bid)

            if result["success"]:
                st.success("✅ DAM API Test Successful!")
            else:
                st.error(f"❌ DAM API Test Failed: {result['message']}")

    # Load CIPC mapping
    st.subheader("📋 CIPC Portfolio Mapping")
    mapping = processor.load_cipc_mapping()

    if mapping:
        st.success(f"✅ Loaded mapping for {len(mapping)} portfolios")
        with st.expander("View Mapping"):
            st.json(mapping)
    else:
        st.error("❌ No mapping loaded. Please ensure CIPIC_Mapping.xlsx exists.")
        return

    # FTP Connection and Processing
    st.subheader("🔗 FTP Connection & Processing")

    if st.button("🔄 Connect and Process All Folders"):
        with st.spinner("Connecting to FTP and processing folders..."):
            folders = processor.get_folders()

            if not folders:
                st.error("❌ No folders found or FTP connection failed")
                return

            st.success(f"✅ Found {len(folders)} folders")

            # Initialize session state
            if 'dam_folders' not in st.session_state:
                st.session_state.dam_folders = []
            if 'dam_folder_data' not in st.session_state:
                st.session_state.dam_folder_data = {}

            st.session_state.dam_folders = folders

            # Process each folder
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, folder in enumerate(folders):
                status_text.text(f"Processing {folder}... ({i+1}/{len(folders)})")
                progress_bar.progress(i / len(folders))

                # Get mapping info
                folder_mapping = mapping.get(folder, {})
                portfolio_code = folder_mapping.get('portfolio_code')
                user_id = folder_mapping.get('user_id')

                if not portfolio_code or not user_id:
                    st.warning(f"⚠️ {folder}: No mapping found, skipping")
                    continue

                # Process folder
                ftp = processor.connect_ftp()
                if ftp:
                    try:
                        # Find and process schedule file
                        schedule_file = processor.find_schedule_file(ftp, folder, target_date)
                        schedule_values = None
                        if schedule_file:
                            ftp.cwd(folder)
                            schedule_values = processor.download_and_read_file(schedule_file, "E12:E107")
                            ftp.cwd('..')

                        # Find and process IEX file
                        iex_file = processor.find_iex_file(ftp, folder, target_date)
                        iex_values = None
                        if iex_file:
                            ftp.cwd(folder)
                            iex_values = processor.download_and_read_file(iex_file, "F11:F106")
                            ftp.cwd('..')

                        # Store data
                        st.session_state.dam_folder_data[folder] = {
                            'portfolio_code': portfolio_code,
                            'user_id': user_id,
                            'schedule_file': schedule_file,
                            'iex_file': iex_file,
                            'schedule_values': schedule_values or [0.0] * 96,
                            'iex_values': iex_values or [0.0] * 96
                        }

                        ftp.quit()

                    except Exception as e:
                        st.error(f"Error processing {folder}: {str(e)}")
                        try:
                            ftp.quit()
                        except:
                            pass

            progress_bar.progress(1.0)
            status_text.text("Processing completed!")
            st.success("✅ All folders processed successfully!")

    # Display processed folders
    if 'dam_folders' in st.session_state and st.session_state.dam_folders:
        st.divider()
        st.subheader("📊 Processed Folders")

        for folder in st.session_state.dam_folders:
            folder_info = st.session_state.dam_folder_data.get(folder, {})

            with st.expander(f"📁 {folder}", expanded=False):
                col1, col2 = st.columns(2)

                with col1:
                    st.write(f"**Portfolio:** {folder_info.get('portfolio_code', 'N/A')}")
                    st.write(f"**User:** {folder_info.get('user_id', 'N/A')}")
                    st.write(f"**Schedule File:** {folder_info.get('schedule_file', 'Not found')}")
                    st.write(f"**IEX File:** {folder_info.get('iex_file', 'Not found')}")

                with col2:
                    # Calculate differences for DAM (can be positive or negative)
                    schedule_vals = folder_info.get('schedule_values', [0.0] * 96)
                    iex_vals = folder_info.get('iex_values', [0.0] * 96)

                    # Allow manual input
                    if st.checkbox(f"Manual Input for {folder}", key=f"manual_{folder}"):
                        st.write("Enter 96 values (comma-separated):")
                        manual_input = st.text_area(
                            f"IEX Values for {folder}",
                            value=",".join([str(v) for v in iex_vals]),
                            key=f"manual_input_{folder}"
                        )

                        try:
                            manual_values = [float(x.strip()) for x in manual_input.split(',')]
                            if len(manual_values) == 96:
                                iex_vals = manual_values
                                st.session_state.dam_folder_data[folder]['manual_values'] = manual_values
                                st.success("✅ Manual values applied")
                            else:
                                st.error(f"❌ Expected 96 values, got {len(manual_values)}")
                        except:
                            st.error("❌ Invalid format. Use comma-separated numbers.")

                    # Calculate differences (schedule - iex) - only positive for DAM sell bids
                    differences = [max(0, s - i) for s, i in zip(schedule_vals, iex_vals)]

                    # Count sell bids only (DAM is sell-only)
                    sell_count = sum(1 for d in differences if d > 0)  # Positive differences (surplus to sell)
                    total_sell = sum(d for d in differences if d > 0)

                    st.metric("Sell Bids", f"{sell_count} blocks", f"{total_sell:.1f} MW")
                    st.info("ℹ️ DAM bids are sell-only (positive differences)")

                    # Generate and display DAM JSON
                    if st.button(f"📄 Generate DAM JSON", key=f"json_{folder}"):
                        if folder_info.get('portfolio_code') and folder_info.get('user_id'):
                            json_output = processor.generate_dam_bid_json(
                                differences,
                                folder_info['portfolio_code'],
                                folder_info['user_id'],
                                target_date
                            )

                            st.json(json_output)

                            # Store JSON for later use
                            st.session_state.dam_folder_data[folder]['json_output'] = json_output

                            # Download button
                            json_str = json.dumps(json_output, indent=2)
                            st.download_button(
                                label=f"📥 Download JSON",
                                data=json_str,
                                file_name=f"dam_bid_{folder}_{target_date.strftime('%Y%m%d')}.json",
                                mime="application/json",
                                key=f"download_{folder}"
                            )

                            # Submit to DAM API
                            if st.button(f"🚀 Submit to DAM API", key=f"submit_{folder}"):
                                dam_client = DAMAPIClient()
                                result = dam_client.submit_dam_bid(json_output)

                                if result["success"]:
                                    st.success(f"✅ {folder}: DAM bid submitted successfully!")
                                else:
                                    st.error(f"❌ {folder}: {result['message']}")
                        else:
                            st.error("❌ Missing portfolio mapping")

        # Bulk DAM Operations
        st.divider()
        st.subheader("🚀 Bulk DAM Operations")

        col1, col2 = st.columns(2)

        with col1:
            if st.button("📤 Submit All DAM Bids", type="primary"):
                dam_client = DAMAPIClient()
                successful_submissions = 0
                total_folders = len(st.session_state.dam_folders)

                progress_bar = st.progress(0)
                status_text = st.empty()

                for i, folder in enumerate(st.session_state.dam_folders):
                    folder_info = st.session_state.dam_folder_data.get(folder, {})

                    progress = (i + 1) / total_folders
                    progress_bar.progress(progress)
                    status_text.text(f"Processing {folder} ({i+1}/{total_folders})")

                    if not folder_info.get('portfolio_code') or not folder_info.get('user_id'):
                        st.warning(f"⚠️ Skipping {folder} - missing portfolio mapping")
                        continue

                    try:
                        # Get values
                        schedule_vals = folder_info.get('schedule_values', [0.0] * 96)
                        if 'manual_values' in folder_info and folder_info['manual_values']:
                            iex_vals = folder_info['manual_values']
                        else:
                            iex_vals = folder_info.get('iex_values', [0.0] * 96)

                        # Calculate differences (only positive for DAM sell bids)
                        differences = [max(0, s - i) for s, i in zip(schedule_vals, iex_vals)]
                        bid_count = sum(1 for d in differences if d > 0)

                        if bid_count > 0:
                            json_output = processor.generate_dam_bid_json(
                                differences,
                                folder_info['portfolio_code'],
                                folder_info['user_id'],
                                target_date
                            )

                            result = dam_client.submit_dam_bid(json_output)
                            if result["success"]:
                                successful_submissions += 1
                                st.success(f"✅ {folder}: DAM bid submitted successfully")
                            else:
                                st.error(f"❌ {folder}: {result['message']}")
                        else:
                            st.info(f"ℹ️ {folder}: No positive bid entries to submit")

                    except Exception as e:
                        st.error(f"💥 {folder}: Error - {str(e)}")

                    time.sleep(0.5)  # Rate limiting

                progress_bar.progress(1.0)
                status_text.text("Bulk submission completed!")
                st.success(f"🎉 Bulk submission completed! {successful_submissions}/{total_folders} folders submitted successfully")

        with col2:
            if st.button("📊 Generate All JSONs"):
                st.info("Generating JSON files for all folders...")

                for folder in st.session_state.dam_folders:
                    folder_info = st.session_state.dam_folder_data.get(folder, {})

                    if folder_info.get('portfolio_code') and folder_info.get('user_id'):
                        schedule_vals = folder_info.get('schedule_values', [0.0] * 96)
                        if 'manual_values' in folder_info and folder_info['manual_values']:
                            iex_vals = folder_info['manual_values']
                        else:
                            iex_vals = folder_info.get('iex_values', [0.0] * 96)

                        differences = [max(0, s - i) for s, i in zip(schedule_vals, iex_vals)]
                        json_output = processor.generate_dam_bid_json(
                            differences,
                            folder_info['portfolio_code'],
                            folder_info['user_id'],
                            target_date
                        )

                        st.session_state.dam_folder_data[folder]['json_output'] = json_output

                st.success("✅ All JSON files generated!")

if __name__ == "__main__":
    main()
