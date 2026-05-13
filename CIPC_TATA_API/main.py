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

# FTP Configuration
FTP_HOST = "15.207.32.135"
FTP_USER = "partner"
FTP_PASSWORD = "jEm9P6182x89"
FTP_PORT = 21
FTP_BASE_PATH = "/WIND/SCHEDULE/KA/CIP_Hatalageri/"

# RTM API Configuration
RTM_BASE_URL = "http://52.172.198.122/samastt_QA1/api/rtm"
RTM_SUBMIT_ENDPOINT = "/AddNewRTMBidService"
RTM_ORDERBOOK_ENDPOINT = "/orderBookResponse"
RTM_AUTH_TOKEN = "eyJhdXRoIjoiVjJfQ29tcGxleE0iLCJzdWIiOiJVc2VyIksifQ~Xk7r9M2p!bFq#zL"
RTM_DEVICE = "postman"

class RTMAPIClient:
    """Client for interacting with RTM API endpoints"""

    def __init__(self):
        self.base_url = RTM_BASE_URL
        self.headers = {
            "Authorization": RTM_AUTH_TOKEN,
            "device": RTM_DEVICE,
            "Content-Type": "application/json"
        }

    def validate_bid_data(self, bid_data):
        """Validate bid data format before submission"""
        required_fields = ["portfoliocode", "user", "bidType", "type", "bidDate", "bid"]

        for field in required_fields:
            if field not in bid_data:
                return False, f"Missing required field: {field}"

        # Validate bid date
        date_valid, date_message = self.validate_bid_date(bid_data["bidDate"])
        if not date_valid:
            return False, date_message

        # Validate bid entries
        if not isinstance(bid_data["bid"], list) or len(bid_data["bid"]) == 0:
            return False, "Bid array is empty or invalid"

        for i, bid_entry in enumerate(bid_data["bid"]):
            required_bid_fields = ["fromtime", "totime", "type", "bidvalue"]
            for field in required_bid_fields:
                if field not in bid_entry:
                    return False, f"Missing field '{field}' in bid entry {i}"

            # Validate bidvalue
            if not isinstance(bid_entry["bidvalue"], list) or len(bid_entry["bidvalue"]) == 0:
                return False, f"Invalid bidvalue in bid entry {i}"

            for j, bidvalue in enumerate(bid_entry["bidvalue"]):
                required_bidvalue_fields = ["price", "value", "type"]
                for field in required_bidvalue_fields:
                    if field not in bidvalue:
                        return False, f"Missing field '{field}' in bidvalue {j} of bid entry {i}"

        return True, "Valid"

    def validate_bid_date(self, bid_date_str):
        """Validate that bid date is today or tomorrow"""
        try:
            today = datetime.now()
            tomorrow = today + timedelta(days=1)

            valid_dates = [
                today.strftime("%d-%m-%Y"),
                tomorrow.strftime("%d-%m-%Y")
            ]

            if bid_date_str in valid_dates:
                return True, "Valid date"
            else:
                return False, f"Invalid date. Only today ({valid_dates[0]}) and tomorrow ({valid_dates[1]}) are allowed"

        except Exception as e:
            return False, f"Date validation error: {str(e)}"

    def create_test_bid(self):
        """Create a test bid matching the Postman collection format exactly"""
        # Use today's date as API only accepts today and tomorrow
        today = datetime.now().strftime("%d-%m-%Y")

        return {
            "portfoliocode": "E1WB0TPT0008",
            "user": "deepraj",
            "bidType": "Block",
            "type": "Sell",
            "bidDate": today,
            "bid": [
                {
                    "fromtime": "20:30",
                    "totime": "21:30",
                    "type": "S",
                    "bidvalue": [
                        {
                            "price": "10",
                            "value": "10",
                            "type": "S"
                        }
                    ]
                }
            ]
        }

    def submit_bid(self, bid_data):
        """Submit RTM bid to the API"""
        try:
            # Validate bid data first
            is_valid, validation_message = self.validate_bid_data(bid_data)
            if not is_valid:
                st.error(f"❌ Invalid bid data: {validation_message}")
                return {
                    "success": False,
                    "message": f"Validation failed: {validation_message}"
                }

            url = f"{self.base_url}{RTM_SUBMIT_ENDPOINT}"

            st.info(f"🚀 Submitting bid to RTM API: {url}")

            # Debug: Show the payload being sent
            st.write("📦 **Request Payload:**")
            st.json(bid_data)

            response = requests.post(
                url,
                json=bid_data,
                headers=self.headers,
                timeout=30
            )

            if response.status_code == 200:
                st.success("✅ Bid submitted successfully!")
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "response": response.json() if response.content else {},
                    "message": "Bid submitted successfully"
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
                st.write(f"**Request Headers:**")
                st.json(dict(response.request.headers))

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
            st.error("🔌 Connection error - Unable to reach RTM API")
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

    def display_orderbook_table(self, response_data):
        """Display orderbook data in a formatted table"""
        try:
            if response_data.get("Status") == "1" and "IEXResponse" in response_data:
                # Parse the nested JSON string in IEXResponse
                iex_response_str = response_data["IEXResponse"]
                iex_data = json.loads(iex_response_str)

                if "RTMBlockBidDetails" in iex_data:
                    bid_details = iex_data["RTMBlockBidDetails"]

                    if bid_details:
                        st.write("📊 **Orderbook Details:**")

                        # Create DataFrame for table display
                        table_data = []
                        for bid in bid_details:
                            table_data.append({
                                "Time Block": f"{bid.get('FromPeriodId', 'N/A')} - {bid.get('ToPeriodId', 'N/A')}",
                                "Quantity": bid.get('Quantity', 'N/A'),
                                "Cleared Qty": bid.get('TotalExecutedQty', 'N/A'),
                                "Price": bid.get('Price', 'N/A'),
                                "Buy/Sell": bid.get('BuySell', 'N/A'),
                                "Order Status": bid.get('OrderStatus', 'N/A'),
                                "Order ID": bid.get('OrderId', 'N/A'),
                                "Bid Ref": bid.get('BidRef', 'N/A')
                            })

                        # Display as DataFrame table
                        df = pd.DataFrame(table_data)
                        st.dataframe(df, use_container_width=True)

                        # Summary statistics
                        total_orders = len(bid_details)
                        total_quantity = sum([abs(bid.get('Quantity', 0)) for bid in bid_details])
                        total_cleared = sum([abs(bid.get('TotalExecutedQty', 0)) for bid in bid_details])

                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            st.metric("Total Orders", total_orders)
                        with col2:
                            st.metric("Total Quantity (MW)", f"{total_quantity}")
                        with col3:
                            st.metric("Total Cleared (MW)", f"{total_cleared}")
                        with col4:
                            submitted_count = len([bid for bid in bid_details if bid.get('OrderStatus') == 'Submitted'])
                            executed_count = len([bid for bid in bid_details if bid.get('TotalExecutedQty', 0) > 0])
                            st.metric("Executed Orders", executed_count)
                    else:
                        st.info("📋 No bid details found in orderbook")
                else:
                    st.warning("⚠️ No RTMBlockBidDetails found in response")
            else:
                st.error("❌ Invalid orderbook response format")

        except json.JSONDecodeError as e:
            st.error(f"❌ Error parsing orderbook JSON: {str(e)}")
        except Exception as e:
            st.error(f"❌ Error displaying orderbook: {str(e)}")

    def check_orderbook(self, portfolio_code, user, bid_date):
        """Check orderbook status for submitted bids"""
        try:
            url = f"{self.base_url}{RTM_ORDERBOOK_ENDPOINT}"

            # Create request payload for orderbook check
            payload = {
                "portfoliocode": portfolio_code,
                "user": user,  # Use user from CIPC mapping
                "bidType": "Block",
                "bidDate": bid_date
            }

            st.info(f"📊 Checking orderbook status: {url}")

            response = requests.get(
                url,
                json=payload,
                headers=self.headers,
                timeout=30
            )

            if response.status_code == 200:
                st.success("✅ Orderbook retrieved successfully!")
                response_data = response.json() if response.content else {}

                # Display orderbook in table format
                self.display_orderbook_table(response_data)

                return {
                    "success": True,
                    "status_code": response.status_code,
                    "response": response_data,
                    "message": "Orderbook retrieved successfully"
                }
            else:
                st.error(f"❌ API Error: {response.status_code}")

                # Show detailed error information for orderbook
                st.write("🔍 **Orderbook Error Details:**")
                st.write(f"**Status Code:** {response.status_code}")
                st.write(f"**Response Headers:**")
                st.json(dict(response.headers))
                st.write(f"**Response Body:**")
                st.text(response.text)
                st.write(f"**Request URL:** {response.url}")
                st.write(f"**Request Headers:**")
                st.json(dict(response.request.headers))
                st.write(f"**Request Payload:**")
                st.json(payload)

                return {
                    "success": False,
                    "status_code": response.status_code,
                    "response": response.text,
                    "message": f"API returned status code {response.status_code}"
                }

        except Exception as e:
            st.error(f"💥 Error checking orderbook: {str(e)}")
            return {
                "success": False,
                "message": f"Error: {str(e)}"
            }

class FTPScheduleProcessor:
    def __init__(self):
        self.ftp = None
        self.mapping_data = None
        self.load_mapping_data()
    
    def load_mapping_data(self):
        """Load CIPC mapping data from Excel file"""
        try:
            if os.path.exists("CIPIC_Mapping.xlsx"):
                self.mapping_data = pd.read_excel("CIPIC_Mapping.xlsx", engine='openpyxl')
                st.success(f"CIPC Mapping data loaded successfully ({len(self.mapping_data)} entries)")
            else:
                st.error("CIPIC_Mapping.xlsx not found in current directory")
                self.mapping_data = None
        except Exception as e:
            st.error(f"Error loading mapping data: {str(e)}")
            self.mapping_data = None
    
    def connect_ftp(self):
        """Connect to FTP server"""
        try:
            self.ftp = ftplib.FTP()
            self.ftp.connect(FTP_HOST, FTP_PORT)
            self.ftp.login(FTP_USER, FTP_PASSWORD)
            self.ftp.cwd(FTP_BASE_PATH)
            return True
        except Exception as e:
            st.error(f"FTP Connection Error: {str(e)}")
            return False
    
    def get_client_folders(self):
        """Get list of client folders from FTP"""
        if not self.ftp:
            if not self.connect_ftp():
                return []
        
        try:
            folders = []
            self.ftp.retrlines('LIST', folders.append)
            client_folders = []
            for folder in folders:
                if folder.startswith('d'):  # Directory
                    folder_name = folder.split()[-1]
                    if folder_name not in ['.', '..']:
                        client_folders.append(folder_name)
            return client_folders
        except Exception as e:
            st.error(f"Error getting folders: {str(e)}")
            return []
    
    def find_latest_schedule_file(self, client_folder, target_date):
        """Find the latest schedule file for given date"""
        try:
            self.ftp.cwd(f"{FTP_BASE_PATH}{client_folder}")

            # Try NLST first to get full filenames
            try:
                filenames = []
                self.ftp.retrlines('NLST', filenames.append)
            except:
                # Fallback to LIST if NLST fails
                files = []
                self.ftp.retrlines('LIST', files.append)
                filenames = []
                for file_info in files:
                    if file_info.startswith('-'):  # Only files, not directories
                        filename = file_info.split()[-1]
                        filenames.append(filename)

            schedule_files = []
            date_str = target_date.strftime("%d-%m-%Y")

            for filename in filenames:
                # Check if file contains the target date
                if date_str in filename:
                    # Check for various schedule patterns
                    is_schedule = False
                    revision_type = None
                    revision_num = 0

                    # Pattern 1: _DA_ with RDA
                    if '_DA_' in filename:
                        revision_match = re.search(r'RDA(\d+)', filename)
                        if revision_match:
                            is_schedule = True
                            revision_type = 'DA'
                            revision_num = int(revision_match.group(1))

                    # Pattern 2: _ID_ with RID
                    elif '_ID_' in filename:
                        revision_match = re.search(r'RID(\d+)', filename)
                        if revision_match:
                            is_schedule = True
                            revision_type = 'ID'
                            revision_num = int(revision_match.group(1))

                    # Pattern 3: _IntraDay_ with RID
                    elif '_IntraDay_' in filename or 'IntraDay' in filename:
                        revision_match = re.search(r'RID(\d+)', filename)
                        if revision_match:
                            is_schedule = True
                            revision_type = 'ID'  # Treat IntraDay as ID type
                            revision_num = int(revision_match.group(1))

                    # Pattern 4: _DayAhead_ with RDA
                    elif '_DayAhead_' in filename or 'DayAhead' in filename:
                        revision_match = re.search(r'RDA(\d+)', filename)
                        if revision_match:
                            is_schedule = True
                            revision_type = 'DA'
                            revision_num = int(revision_match.group(1))

                    if is_schedule:
                        schedule_files.append({
                            'filename': filename,
                            'revision_type': revision_type,
                            'revision_num': revision_num
                        })

            if not schedule_files:
                return None

            # Sort by revision type (ID > DA) and revision number (higher is latest)
            schedule_files.sort(key=lambda x: (x['revision_type'] == 'ID', x['revision_num']), reverse=True)
            return schedule_files[0]['filename']

        except Exception as e:
            st.error(f"Error finding schedule file: {str(e)}")
            return None
    
    def find_iex_file(self, client_folder, target_date):
        """Find IEX file for given date"""
        try:
            self.ftp.cwd(f"{FTP_BASE_PATH}{client_folder}")
            files = []
            self.ftp.retrlines('LIST', files.append)
            
            # Market acceptance files are stamped with the target delivery date.
            date_pattern = target_date.strftime("%y%m%d")
            
            for file_info in files:
                if not file_info.startswith('-'):
                    continue
                filename = file_info.split()[-1]
                if f"IEX{date_pattern}SCH" in filename:
                    return filename
            
            return None
            
        except Exception as e:
            st.error(f"Error finding IEX file: {str(e)}")
            return None
    
    def download_and_read_file(self, filename, sheet_range):
        """
        Download file from FTP and read data using the requested spreadsheet range.
        """
        try:
            # Download file to memory
            bio = io.BytesIO()
            self.ftp.retrbinary(f'RETR {filename}', bio.write)
            bio.seek(0)

            df = read_table_from_buffer(bio, filename)
            return extract_values_from_dataframe(
                df,
                sheet_range,
                absolute=sheet_range.strip().upper().startswith("F"),
            )

        except Exception as e:
            st.error(f"Error reading file {filename}: {str(e)}")
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
    
    def get_current_time_block_index(self):
        """Get the current time block index (0-95) based on current time"""
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute

        # Find which 15-minute block we're in
        block_in_hour = current_minute // 15
        current_block_index = current_hour * 4 + block_in_hour

        return current_block_index

    def get_start_block_index(self):
        """Get the starting block index (1.5 hours = 6 blocks after current)"""
        current_index = self.get_current_time_block_index()
        start_index = current_index + 6  # 1.5 hours = 6 blocks of 15 minutes

        # If we go past midnight, wrap to next day (but RTM only allows today/tomorrow)
        if start_index >= 96:
            start_index = start_index - 96

        return start_index

    def generate_json_output(self, differences, portfolio_code, user_id, bid_date):
        """Generate JSON output for positive differences, starting 1.5 hours from now"""
        time_blocks = self.generate_time_blocks()
        bid_entries = []

        # Get the starting block index (1.5 hours from now)
        current_block_index = self.get_current_time_block_index()
        start_block_index = self.get_start_block_index()

        # Debug info for time filtering
        current_time = datetime.now().strftime("%H:%M")
        if current_block_index < 96:
            current_block_time = time_blocks[current_block_index]
            st.info(f"⏰ Current time: {current_time} (Block {current_block_index}: {current_block_time[0]}-{current_block_time[1]})")

        if start_block_index < 96:
            start_block_time = time_blocks[start_block_index]
            st.info(f"🚀 Starting bids from block {start_block_index}: {start_block_time[0]}-{start_block_time[1]} (1.5 hours later)")

        for i, diff in enumerate(differences):
            # Only include blocks from start_block_index onwards
            if i >= start_block_index and diff > 0:  # Only positive values and future blocks
                from_time, to_time = time_blocks[i]
                bid_entry = {
                    "fromtime": from_time,
                    "totime": to_time,
                    "type": "S",
                    "bidvalue": [
                        {
                            "price": "10",
                            "value": f"{diff:.1f}",
                            "type": "S"
                        }
                    ]
                }
                bid_entries.append(bid_entry)

        # Ensure date is in correct format
        if isinstance(bid_date, datetime):
            date_str = bid_date.strftime("%d-%m-%Y")
        else:
            date_str = str(bid_date)

        json_output = {
            "portfoliocode": str(portfolio_code),
            "user": str(user_id),  # Use user from CIPC mapping
            "bidType": "Block",
            "type": "Sell",
            "bidDate": date_str,
            "bid": bid_entries
        }

        return json_output
    
    def get_portfolio_info(self, folder_name):
        """Get portfolio code and user ID from mapping data"""
        if self.mapping_data is None:
            return None, None

        # Try exact match first
        exact_match = self.mapping_data[self.mapping_data['Folder'].str.lower() == folder_name.lower()]
        if not exact_match.empty:
            portfolio = exact_match.iloc[0]['Portfolio']
            user = exact_match.iloc[0]['User']
            return str(portfolio), str(user)

        # Try partial match - folder contains mapping name
        partial_match = self.mapping_data[self.mapping_data['Folder'].str.contains(folder_name, case=False, na=False)]
        if not partial_match.empty:
            portfolio = partial_match.iloc[0]['Portfolio']
            user = partial_match.iloc[0]['User']
            return str(portfolio), str(user)

        # Try reverse partial match - mapping name contains folder
        for _, row in self.mapping_data.iterrows():
            if folder_name.lower() in row['Folder'].lower() or row['Folder'].lower() in folder_name.lower():
                return str(row['Portfolio']), str(row['User'])

        return None, None

    def add_mapping(self, folder, portfolio, user):
        """Add a new mapping to the CIPC mapping data"""
        try:
            # Create new row
            new_row = pd.DataFrame({
                'Folder': [folder],
                'Portfolio': [portfolio],
                'User': [user]
            })

            # Add to existing data
            if self.mapping_data is not None:
                self.mapping_data = pd.concat([self.mapping_data, new_row], ignore_index=True)
            else:
                self.mapping_data = new_row

            # Save to Excel file
            self.mapping_data.to_excel("CIPIC_Mapping.xlsx", index=False)
            return True

        except Exception as e:
            st.error(f"Error adding mapping: {str(e)}")
            return False

    def remove_mapping(self, folder):
        """Remove a mapping from the CIPC mapping data"""
        try:
            if self.mapping_data is not None:
                # Remove rows matching the folder
                self.mapping_data = self.mapping_data[self.mapping_data['Folder'] != folder]

                # Save to Excel file
                self.mapping_data.to_excel("CIPIC_Mapping.xlsx", index=False)
                return True
            return False

        except Exception as e:
            st.error(f"Error removing mapping: {str(e)}")
            return False

def process_folder_files(processor, folder, target_date):
    """Process files for a specific folder"""
    folder_info = {
        'folder_name': folder,
        'schedule_file': None,
        'schedule_values': [],
        'iex_file': None,
        'iex_values': [],
        'portfolio_code': None,
        'user_id': None,
        'status': 'processing',
        'error_details': None
    }

    try:
        # Get portfolio info
        portfolio_code, user_id = processor.get_portfolio_info(folder)
        folder_info['portfolio_code'] = portfolio_code
        folder_info['user_id'] = user_id

        # Find schedule file
        schedule_file = processor.find_latest_schedule_file(folder, target_date)
        if schedule_file:
            folder_info['schedule_file'] = schedule_file
            schedule_values = processor.download_and_read_file(schedule_file, "E12:E107")
            folder_info['schedule_values'] = schedule_values

        # Find IEX file
        iex_file = processor.find_iex_file(folder, target_date)
        if iex_file:
            folder_info['iex_file'] = iex_file
            iex_values_raw = processor.download_and_read_file(iex_file, "F11:F106")
            folder_info['iex_values'] = [abs(val) for val in iex_values_raw]

        folder_info['status'] = 'completed'

    except Exception as e:
        folder_info['status'] = f'error: {str(e)}'

    return folder_info

def generate_folder_json(processor, folder, folder_info, target_date):
    """Generate JSON output for a specific folder"""
    portfolio_code = folder_info.get('portfolio_code')
    user_id = folder_info.get('user_id')

    if not portfolio_code or not user_id:
        st.error(f"Portfolio code or User ID not found for {folder}")
        return

    # Get schedule values
    schedule_vals = folder_info.get('schedule_values', [])

    # Prefer manual values over IEX if present
    if 'manual_values' in folder_info and folder_info['manual_values']:
        iex_vals = folder_info['manual_values']
    else:
        iex_vals = folder_info.get('iex_values', [])


    # Ensure we have 96 values - pad with zeros if missing
    if len(schedule_vals) != 96:
        if len(schedule_vals) == 0:
            schedule_vals = [0.0] * 96
            st.warning(f"No schedule file found for {folder}. Using zeros for all time blocks.")
        else:
            st.error(f"Schedule file for {folder} has {len(schedule_vals)} values instead of 96")
            return


    if len(iex_vals) != 96:
        if len(iex_vals) == 0:
            iex_vals = [0.0] * 96
            st.warning(f"No IEX file or manual input for {folder}. Using zeros for all time blocks.")
        else:
            st.error(f"IEX/manual values for {folder} has {len(iex_vals)} values instead of 96")
            return


    # Calculate differences
    differences = [max(0, s - i) for s, i in zip(schedule_vals, iex_vals)]


    # Generate JSON
    json_output = processor.generate_json_output(
    differences, portfolio_code, user_id, target_date
    )


    st.subheader(f"Generated JSON Output for {folder}")
    st.json(json_output)


    # Show summary
    positive_count = sum(1 for d in differences if d > 0)
    total_value = sum(d for d in differences if d > 0)
    st.info(f"Generated {positive_count} bid entries from {len(differences)} time blocks (Total value: {total_value:.2f})")


    # Download button
    json_str = json.dumps(json_output, indent=2)
    col1, col2, col3 = st.columns(3)

    with col1:
        st.download_button(
            label=f"📥 Download JSON",
            data=json_str,
            file_name=f"schedule_output_{folder}_{target_date.strftime('%Y%m%d')}.json",
            mime="application/json",
            key=f"download_{folder}"
        )

    # RTM API Integration
    with col2:
        if st.button(f"🚀 Submit to RTM API", key=f"submit_{folder}"):
            if positive_count > 0:
                rtm_client = RTMAPIClient()
                with st.spinner("Submitting bid to RTM API..."):
                    result = rtm_client.submit_bid(json_output)

                    if result["success"]:
                        st.success(f"✅ Bid submitted successfully for {folder}!")
                        if result.get("response"):
                            st.json(result["response"])
                    else:
                        st.error(f"❌ Failed to submit bid: {result['message']}")
                        if result.get("response"):
                            st.text(result["response"])
            else:
                st.warning("⚠️ No positive bid entries to submit")

    with col3:
        if st.button(f"📊 Check Orderbook", key=f"orderbook_{folder}"):
            rtm_client = RTMAPIClient()
            with st.spinner("Checking orderbook status..."):
                result = rtm_client.check_orderbook(
                    portfolio_code,
                    user_id,
                    target_date.strftime("%d-%m-%Y")
                )

                if result["success"]:
                    st.success(f"✅ Orderbook retrieved for {folder}!")
                    if result.get("response"):
                        st.json(result["response"])
                else:
                    st.error(f"❌ Failed to get orderbook: {result['message']}")
                    if result.get("response"):
                        st.text(result["response"])

@st.cache_resource
def get_processor():
    """Create and cache the FTP processor - updated for orderbook table display"""
    return FTPScheduleProcessor()

@st.cache_resource
def get_rtm_client():
    """Create and cache the RTM API client"""
    return RTMAPIClient()

def main():
    st.title("FTP Schedule Processor with RTM API Integration")
    st.write("Automatically processing all client folders for today's schedule files and submitting to RTM API")

    # RTM API Status
    st.info(f"🔗 **RTM API Endpoint**: {RTM_BASE_URL}")
    st.info(f"📡 **Device**: {RTM_DEVICE}")

    # RTM API Test Section
    with st.expander("🧪 RTM API Test (Postman Format)", expanded=False):
        st.write("Test the RTM API with the exact format from the Postman collection")
        st.info("📅 **Note**: RTM API only accepts bids for today and tomorrow")

        if st.button("🚀 Test RTM API with Postman Format"):
            rtm_client = RTMAPIClient()
            test_bid = rtm_client.create_test_bid()

            st.write("**Test Payload:**")
            st.json(test_bid)

            with st.spinner("Testing RTM API..."):
                result = rtm_client.submit_bid(test_bid)

                if result["success"]:
                    st.success("✅ RTM API Test Successful!")
                    if result.get("response"):
                        st.json(result["response"])
                else:
                    st.error(f"❌ RTM API Test Failed: {result['message']}")
                    if result.get("response"):
                        st.text(result["response"])

    processor = get_processor()
    rtm_client = get_rtm_client()

    # Sidebar for CIPC Mapping Management
    with st.sidebar:
        st.header("📋 CIPC Mapping Management")

        # Show current mappings
        if processor.mapping_data is not None and not processor.mapping_data.empty:
            st.subheader("Current Mappings")
            for _, row in processor.mapping_data.iterrows():
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"**{row['Folder']}**")
                    st.write(f"Portfolio: {row['Portfolio']}")
                    st.write(f"User: {row['User']}")
                with col2:
                    if st.button("🗑️", key=f"delete_{row['Folder']}", help=f"Delete {row['Folder']}"):
                        if processor.remove_mapping(row['Folder']):
                            st.success(f"Removed mapping for {row['Folder']}")
                            st.rerun()
                st.divider()
        else:
            st.info("No mappings found")

        # Add new mapping
        st.subheader("Add New Mapping")
        with st.form("add_mapping_form"):
            new_folder = st.text_input("Folder Name", placeholder="e.g., IRIS_3")
            new_portfolio = st.text_input("Portfolio Code", placeholder="e.g., S1KA0TPT0835")
            new_user = st.text_input("User Name", placeholder="e.g., New Energy Company")

            if st.form_submit_button("➕ Add Mapping"):
                if new_folder and new_portfolio and new_user:
                    # Check if folder already exists
                    if processor.mapping_data is not None:
                        existing = processor.mapping_data[processor.mapping_data['Folder'].str.lower() == new_folder.lower()]
                        if not existing.empty:
                            st.error(f"Mapping for folder '{new_folder}' already exists!")
                        else:
                            if processor.add_mapping(new_folder, new_portfolio, new_user):
                                st.success(f"Added mapping for {new_folder}")
                                st.rerun()
                    else:
                        if processor.add_mapping(new_folder, new_portfolio, new_user):
                            st.success(f"Added mapping for {new_folder}")
                            st.rerun()
                else:
                    st.error("Please fill in all fields")

    # Ensure mapping data is loaded
    if processor.mapping_data is None:
        st.error("Failed to load CIPC mapping data. Please check if CIPIC_Mapping.xlsx exists.")
        return

    # Initialize session state
    if 'folders' not in st.session_state:
        st.session_state.folders = []
    if 'folder_data' not in st.session_state:
        st.session_state.folder_data = {}

    # Use current date automatically
    target_date = datetime.now()
    st.info(f"Processing files for date: {target_date.strftime('%d-%m-%Y')}")
    st.warning("⚠️ **RTM API Restriction**: Bids can only be submitted for today and tomorrow")

    # Auto-connect to FTP on page load
    if not st.session_state.folders:
        with st.spinner("Connecting to FTP and loading folders..."):
            if processor.connect_ftp():
                st.session_state.folders = processor.get_client_folders()
                st.success(f"Connected! Found {len(st.session_state.folders)} client folders")

                # Process all folders automatically
                with st.spinner("Processing files from all folders..."):
                    for folder in st.session_state.folders:
                        folder_info = process_folder_files(processor, folder, target_date)
                        st.session_state.folder_data[folder] = folder_info
            else:
                st.error("Failed to connect to FTP server")
                return

    # Display results for all folders
    if 'folders' in st.session_state and st.session_state.folders:
        st.subheader("Processing Results for All Client Folders")

        for folder in st.session_state.folders:
            folder_info = st.session_state.folder_data.get(folder, {})

            with st.expander(f"📁 {folder}", expanded=True):
                col1, col2 = st.columns(2)

                with col1:
                    st.write("**Portfolio Info:**")
                    if folder_info.get('portfolio_code') and folder_info.get('user_id'):
                        st.success(f"✅ Portfolio: {folder_info['portfolio_code']}")
                        st.success(f"✅ User: {folder_info['user_id']}")
                    else:
                        st.error(f"❌ Portfolio mapping not found for folder: '{folder}'")

                    st.write("**Schedule File:**")
                    if folder_info.get('schedule_file'):
                        st.success(f"Found: {folder_info['schedule_file']}")
                        st.info(f"Values extracted: {len(folder_info.get('schedule_values', []))}")
                    else:
                        st.warning("Schedule file not found - will use zeros")

                    st.write("**IEX File:**")
                    if folder_info.get('iex_file'):
                        st.success(f"Found: {folder_info['iex_file']}")
                        st.info(f"Values extracted: {len(folder_info.get('iex_values', []))}")
                    else:
                        # Check if manual values are available
                        current_folder_info = st.session_state.folder_data.get(folder, folder_info)
                        if current_folder_info.get('manual_values'):
                            st.info(f"Manual values: {len(current_folder_info['manual_values'])}")
                        else:
                            st.warning("IEX file not found - will use zeros if no manual input")

                with col2:
                    st.write("**Manual Input (if IEX file not found):**")
                    manual_key = f"manual_input_{folder}"
                    manual_input = st.text_area(
                        f"Enter 96 values for {folder}:",
                        height=150,
                        key=manual_key,
                        help="Enter 96 time block values, one per line or comma-separated"
                    )

                    if st.button(f"Parse Manual Input", key=f"parse_{folder}"):
                        if manual_input.strip():
                            try:
                                if ',' in manual_input:
                                    values = [float(x.strip()) for x in manual_input.split(',')]
                                else:
                                    values = [float(x.strip()) for x in manual_input.split('\n') if x.strip()]

                                if len(values) == 96:
                                    st.session_state.folder_data[folder]['manual_values'] = [abs(val) for val in values]
                                    st.success(f"Successfully parsed {len(values)} values")
                                else:
                                    st.error(f"Expected 96 values, got {len(values)}")
                            except Exception as e:
                                st.error(f"Error parsing values: {str(e)}")
                        else:
                            # If no manual input provided, set to zeros
                            st.session_state.folder_data[folder]['manual_values'] = [0.0] * 96
                            st.info("No manual input provided. Using zeros for all time blocks.")

                # Generate JSON for this folder
                if st.button(f"Generate JSON for {folder}", key=f"json_{folder}"):
                    # Get the latest folder info including any manual values from session state
                    current_folder_info = st.session_state.folder_data.get(folder, folder_info)
                    generate_folder_json(processor, folder, current_folder_info, target_date)

    # Bulk RTM Operations
    if 'folders' in st.session_state and st.session_state.folders:
        st.divider()
        st.subheader("🚀 Bulk RTM Operations")

        col1, col2 = st.columns(2)

        with col1:
            if st.button("📤 Submit All Bids to RTM API", type="primary"):
                rtm_client = RTMAPIClient()
                successful_submissions = 0
                total_folders = len(st.session_state.folders)

                progress_bar = st.progress(0)
                status_text = st.empty()

                for i, folder in enumerate(st.session_state.folders):
                    folder_info = st.session_state.folder_data.get(folder, {})

                    # Update progress
                    progress = (i + 1) / total_folders
                    progress_bar.progress(progress)
                    status_text.text(f"Processing {folder} ({i+1}/{total_folders})")

                    # Check if folder has valid portfolio info
                    if not folder_info.get('portfolio_code') or not folder_info.get('user_id'):
                        st.warning(f"⚠️ Skipping {folder} - missing portfolio mapping")
                        continue

                    # Generate JSON for this folder
                    try:
                        # Get values
                        schedule_vals = folder_info.get('schedule_values', [0.0] * 96)
                        if 'manual_values' in folder_info and folder_info['manual_values']:
                            iex_vals = folder_info['manual_values']
                        else:
                            iex_vals = folder_info.get('iex_values', [0.0] * 96)

                        # Ensure 96 values
                        if len(schedule_vals) != 96:
                            schedule_vals = [0.0] * 96
                        if len(iex_vals) != 96:
                            iex_vals = [0.0] * 96

                        # Calculate differences and generate JSON
                        differences = [max(0, s - i) for s, i in zip(schedule_vals, iex_vals)]
                        positive_count = sum(1 for d in differences if d > 0)

                        if positive_count > 0:
                            json_output = processor.generate_json_output(
                                differences,
                                folder_info['portfolio_code'],
                                folder_info['user_id'],
                                target_date
                            )

                            # Submit to RTM API
                            result = rtm_client.submit_bid(json_output)
                            if result["success"]:
                                successful_submissions += 1
                                st.success(f"✅ {folder}: Bid submitted successfully")
                            else:
                                st.error(f"❌ {folder}: {result['message']}")
                        else:
                            st.info(f"ℹ️ {folder}: No positive bid entries to submit")

                    except Exception as e:
                        st.error(f"💥 {folder}: Error - {str(e)}")

                    # Small delay to avoid overwhelming the API
                    time.sleep(0.5)

                progress_bar.progress(1.0)
                status_text.text("Bulk submission completed!")
                st.success(f"🎉 Bulk submission completed! {successful_submissions}/{total_folders} folders submitted successfully")

        with col2:
            if st.button("📊 Check All Orderbooks"):
                rtm_client = RTMAPIClient()

                for folder in st.session_state.folders:
                    folder_info = st.session_state.folder_data.get(folder, {})

                    if folder_info.get('portfolio_code') and folder_info.get('user_id'):
                        st.subheader(f"📊 Orderbook for {folder}")
                        result = rtm_client.check_orderbook(
                            folder_info['portfolio_code'],
                            folder_info['user_id'],
                            target_date.strftime("%d-%m-%Y")
                        )

                        if result["success"]:
                            # The table display is handled in display_orderbook_table method
                            pass
                        else:
                            st.error(f"Error: {result['message']}")

                        st.divider()  # Add separator between orderbooks
                    else:
                        st.warning(f"⚠️ {folder}: Missing portfolio mapping")



    # Add refresh buttons
    col1, col2 = st.columns(2)

    with col1:
        if st.button("🔄 Refresh and Reprocess All Folders"):
            # Clear session state to trigger reprocessing
            st.session_state.folders = []
            st.session_state.folder_data = {}
            st.rerun()

    with col2:
        if st.button("🗑️ Clear Cache and Restart"):
            # Clear all caches
            st.cache_resource.clear()
            st.session_state.clear()
            st.rerun()

if __name__ == "__main__":
    main()
