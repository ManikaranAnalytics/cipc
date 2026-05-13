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

# GDAM API Configuration
GDAM_BASE_URL = "http://52.172.198.122/samastt_QA1/api/gdam"
GDAM_BID_ENDPOINT = "/AddNewGDAMBidService"
GDAM_ORDERBOOK_ENDPOINT = "/GetGDAMOrderBookService"

class GDAMAPIClient:
    def __init__(self):
        self.base_url = GDAM_BASE_URL
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": "ZCI6InNlY3JldF9jbGllbnRfaWQiLCJzY29wZSI6WyJhcGlzY29wZSJdfQ.iWLQUtWPmbW3GNyZW8Pimu-Kj-AC0D9_IAMRgh",
            "device": "postman"
        }
    
    def submit_gdam_bid(self, bid_data):
        """Submit GDAM bid to API"""
        try:
            # Validate bid data first
            validation_errors = self.validate_bid_data(bid_data)
            if validation_errors:
                return {
                    "success": False,
                    "message": f"Validation failed: {'; '.join(validation_errors[:3])}{'...' if len(validation_errors) > 3 else ''}",
                    "errors": validation_errors
                }

            # Submit to API
            url = f"{self.base_url}{GDAM_BID_ENDPOINT}"

            # Log API call details
            api_call_info = {
                "url": url,
                "method": "POST",
                "headers": self.headers,
                "endpoint": GDAM_BID_ENDPOINT,
                "base_url": self.base_url
            }

            response = requests.post(url, headers=self.headers, json=bid_data, timeout=30)
            
            # Parse response
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "message": f"Invalid JSON response from API (HTTP {response.status_code})",
                    "response_text": response.text[:500],
                    "status_code": response.status_code,
                    "api_call_info": api_call_info
                }

            # Check if request was successful
            if response.status_code == 200:
                # Check API response status
                api_status = response_data.get('Status', '').lower()
                api_result = response_data.get('result', False)

                if api_result or api_status == 'success' or api_status == '1':
                    return {
                        "success": True,
                        "message": "GDAM bid submitted successfully",
                        "response": response_data,
                        "api_call_info": api_call_info
                    }
                else:
                    # API returned error
                    error_msg = response_data.get('message', 'Unknown error')
                    error_details = response_data.get('Error', '')
                    status = response_data.get('Status', 'Unknown')

                    return {
                        "success": False,
                        "message": f"GDAM Bid submission failed: {error_msg}",
                        "error_details": error_details,
                        "status": status,
                        "response": response_data,
                        "api_call_info": api_call_info
                    }
            else:
                return {
                    "success": False,
                    "message": f"HTTP Error {response.status_code}: {response.reason}",
                    "response": response_data,
                    "status_code": response.status_code,
                    "api_call_info": api_call_info
                }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "message": "Request timeout - API took too long to respond",
                "api_call_info": api_call_info
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "message": "Connection error - Unable to reach GDAM API",
                "api_call_info": api_call_info
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Unexpected error: {str(e)}",
                "api_call_info": api_call_info
            }

    def validate_bid_data(self, bid_data):
        """Validate GDAM bid data structure and business rules"""
        errors = []

        # Validate main structure
        required_fields = ["portfoliocode", "user", "bidType", "type", "bidDate", "OCF", "OCF_Option", "OCF_Value", "bid"]
        for field in required_fields:
            if field not in bid_data:
                errors.append(f"Missing required field: {field}")

        # Validate OCF fields
        if "OCF" in bid_data:
            if bid_data["OCF"] not in ["Y", "N"]:
                errors.append("OCF must be 'Y' or 'N'")

        if "OCF_Option" in bid_data:
            if bid_data["OCF_Option"] not in ["Premium", "discount"]:
                errors.append("OCF_Option must be 'Premium' or 'discount'")

        if "OCF_Value" in bid_data:
            try:
                ocf_value = float(bid_data["OCF_Value"])
                if ocf_value < 0 or ocf_value > 100:
                    errors.append("OCF_Value must be between 0-100")
            except (ValueError, TypeError):
                errors.append("OCF_Value must be a valid number")

        if "bid" in bid_data and isinstance(bid_data["bid"], list):
            # Max 96 bid records
            if len(bid_data["bid"]) > 96:
                errors.append(f"Too many bid records: {len(bid_data['bid'])} (max 96)")
            
            for i, bid_entry in enumerate(bid_data["bid"]):
                # Validate bid entry structure
                required_bid_fields = ["fromtime", "totime", "type", "bidvalue"]
                for field in required_bid_fields:
                    if field not in bid_entry:
                        errors.append(f"Bid {i}: Missing required field '{field}'")

                # Validate bidvalue array
                if "bidvalue" in bid_entry:
                    if not isinstance(bid_entry["bidvalue"], list) or len(bid_entry["bidvalue"]) == 0:
                        errors.append(f"Bid {i}: bidvalue must be a non-empty array")
                    elif len(bid_entry["bidvalue"]) > 49:
                        errors.append(f"Bid {i}: Too many price points: {len(bid_entry['bidvalue'])} (max 49)")
                    else:
                        for j, bid_value in enumerate(bid_entry["bidvalue"]):
                            # Validate required fields in bidvalue
                            required_bv_fields = ["price", "value", "type"]
                            for field in required_bv_fields:
                                if field not in bid_value:
                                    errors.append(f"Bid {i}, Price {j}: Missing required field '{field}'")

                            # Validate price (10-10000 Rs/KWh, up to 3 decimal places)
                            if "price" in bid_value:
                                try:
                                    price = float(bid_value["price"])
                                    if price < 10 or price > 10000:
                                        errors.append(f"Bid {i}, Price {j}: Price must be between 10-10000 Rs/KWh")
                                    
                                    # Check decimal places
                                    price_str = str(bid_value["price"])
                                    if '.' in price_str and len(price_str.split('.')[1]) > 3:
                                        errors.append(f"Bid {i}, Price {j}: Price can have maximum 3 decimal places")
                                except (ValueError, TypeError):
                                    errors.append(f"Bid {i}, Price {j}: Invalid price format")

                            # Validate quantity (minimum 0.1 MW, up to 1 decimal place)
                            if "value" in bid_value:
                                try:
                                    quantity = float(bid_value["value"])
                                    if quantity < 0.1:
                                        errors.append(f"Bid {i}, Price {j}: Quantity must be at least 0.1 MW")
                                    
                                    # Check decimal places
                                    qty_str = str(bid_value["value"])
                                    if '.' in qty_str and len(qty_str.split('.')[1]) > 1:
                                        errors.append(f"Bid {i}, Price {j}: Quantity can have maximum 1 decimal place")
                                except (ValueError, TypeError):
                                    errors.append(f"Bid {i}, Price {j}: Invalid quantity format")

                            # GDAM restricted to Sell bids only
                            if "type" in bid_value:
                                bv_type = bid_value["type"]
                                if bv_type == "B":
                                    errors.append(f"Bid {i}, Price {j}: GDAM currently supports Sell (S) bids only")
                                elif bv_type == "S" and quantity <= 0:
                                    errors.append(f"Bid {i}, Price {j}: Sell bid quantity must be positive")
                                elif bv_type not in ["S"]:
                                    errors.append(f"Bid {i}, Price {j}: Type must be 'S' (Sell), got '{bv_type}'")

        return errors

def calculate_min_gdam_bid_date():
    """Calculate minimum allowed GDAM bid date based on 11 AM cutoff rule"""
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
    """Generate all 96 15-minute time blocks for a day"""
    blocks = []
    current_time = datetime.strptime("00:00", "%H:%M")
    
    for i in range(96):  # 24 hours * 4 blocks per hour
        from_time = current_time.strftime("%H:%M")
        current_time += timedelta(minutes=15)
        to_time = current_time.strftime("%H:%M")
        blocks.append((from_time, to_time))
    
    return blocks

def gdam_api_testing_page():
    """Main GDAM API testing interface"""
    st.header("🌱 GDAM API Testing")
    st.markdown("Test the Green Day-Ahead Market (GDAM) API for bid submission and orderbook retrieval")

    # Initialize GDAM client
    gdam_client = GDAMAPIClient()

    # Show API configuration
    with st.expander("⚙️ GDAM API Configuration", expanded=False):
        st.write(f"**Base URL:** {GDAM_BASE_URL}")
        st.write(f"**Submit Bid Endpoint:** {GDAM_BID_ENDPOINT}")
        st.write(f"**Orderbook Endpoint:** {GDAM_ORDERBOOK_ENDPOINT}")
        st.write("**Authentication:** 'auth' and 'device' headers")
        st.write("**Supported Bid Types:** Sell (S) only")
        st.write("**Price Range:** 10-10000 Rs/KWh (up to 3 decimal places)")
        st.write("**Quantity Range:** Minimum 0.1 MW (up to 1 decimal place)")
        st.write("**OCF Fields:** OCF (Y/N), OCF_Option (Premium/discount), OCF_Value (0-100)")
        st.write("**Max Limits:** 96 bid records, 49 price points per bid")

    # Create tabs for different functionalities
    tab1, tab2, tab3 = st.tabs(["📝 Manual Bid Entry", "📊 Sample Data Testing", "📈 Order Book"])

    with tab1:
        st.subheader("📝 Manual GDAM Bid Entry")

        # Basic bid information
        col1, col2 = st.columns(2)

        with col1:
            portfolio_code = st.text_input("Portfolio Code", value="E1WB0TPT0008", help="Portfolio code for the bid")
            user = st.text_input("User", value="deepraj", help="User identifier")

        with col2:
            bid_type = st.selectbox("Bid Type", ["Single", "Block", "Multi-Block"], help="Type of GDAM bid")

            # Calculate minimum allowed date
            min_date = calculate_min_gdam_bid_date()
            bid_date = st.date_input("Bid Date", value=min_date, min_value=min_date, help=f"Minimum date: {min_date} (11 AM cutoff rule)")

        # Main type - GDAM restricted to Sell only
        bid_main_type = st.selectbox("Main Type", ["Sell"], help="GDAM currently supports Sell bids only")

        # OCF (Open Cycle Factor) fields
        st.subheader("🔧 OCF Configuration")
        col1, col2, col3 = st.columns(3)

        with col1:
            ocf = st.selectbox("OCF", ["Y", "N"], index=1, help="Open Cycle Factor - Y/N")

        with col2:
            ocf_option = st.selectbox("OCF Option", ["Premium", "discount"], help="OCF pricing option")

        with col3:
            ocf_value = st.number_input("OCF Value", min_value=0, max_value=100, value=0, step=1, help="OCF value (0-100, integer only)")

        # Time block selection
        st.subheader("⏰ Time Block Selection")

        # Generate 15-minute blocks
        time_blocks = generate_15_minute_blocks()
        time_block_options = [f"{from_time} - {to_time}" for from_time, to_time in time_blocks]

        col1, col2 = st.columns(2)

        with col1:
            use_custom_range = st.checkbox("Use Custom Time Range", help="Select a range of time blocks")

            if use_custom_range:
                selected_index = st.selectbox("Start Time Block", range(len(time_block_options)),
                                            format_func=lambda x: time_block_options[x],
                                            help="Select starting time block")
                end_block = st.selectbox("End Time Block", time_block_options[selected_index:],
                                       help="Select ending time block")
            else:
                selected_index = st.selectbox("Time Block", range(len(time_block_options)),
                                            format_func=lambda x: time_block_options[x],
                                            help="Select a single time block")

        with col2:
            # Entry type - GDAM restricted to Sell only
            entry_type = st.selectbox("Entry Type", ["S"], help="S = Sell (GDAM restricted to Sell only)")

            # Default values for bid entries
            default_price = st.number_input("Default Price (Rs/KWh)", min_value=10.0, max_value=10000.0, value=100.0, step=0.001,
                                          help="Price between 10-10000 Rs/KWh (up to 3 decimal places)")
            default_qty = st.number_input("Default Quantity (MW)", min_value=0.1, value=10.0, step=0.1,
                                        help="Quantity minimum 0.1 MW (up to 1 decimal place)")

        # Add bid entry button
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
                    st.session_state.gdam_bid_entries.append(new_entry)
                    entries_added += 1

                st.success(f"✅ Added {entries_added} bid entries for custom range!")
            else:
                # Single time block
                from_time, to_time = time_blocks[selected_index]

                new_entry = {
                    "fromtime": from_time,
                    "totime": to_time,
                    "type": entry_type,
                    "bidvalue": [
                        {
                            "price": str(default_price),
                            "value": str(default_qty),
                            "type": entry_type
                        }
                    ]
                }
                st.session_state.gdam_bid_entries.append(new_entry)
                st.success("✅ Bid entry added!")

        # Display current bid entries
        if st.session_state.gdam_bid_entries:
            st.subheader("📋 Current Bid Entries")

            for i, entry in enumerate(st.session_state.gdam_bid_entries):
                with st.expander(f"Entry {i+1}: {entry['fromtime']} - {entry['totime']}", expanded=False):
                    col1, col2, col3 = st.columns([2, 2, 1])

                    with col1:
                        st.write(f"**Time:** {entry['fromtime']} - {entry['totime']}")
                        st.write(f"**Type:** {entry['type']}")

                    with col2:
                        if entry['bidvalue']:
                            bv = entry['bidvalue'][0]
                            st.write(f"**Price:** {bv['price']} Rs/KWh")
                            st.write(f"**Quantity:** {bv['value']} MW")

                    with col3:
                        if st.button("🗑️ Remove", key=f"remove_{i}"):
                            st.session_state.gdam_bid_entries.pop(i)
                            st.rerun()

            # Clear all entries
            if st.button("🗑️ Clear All Entries"):
                st.session_state.gdam_bid_entries = []
                st.success("✅ All entries cleared!")
                st.rerun()

        # Generate and submit bid
        if st.session_state.gdam_bid_entries:
            st.subheader("🚀 Submit GDAM Bid")

            # Create bid payload
            bid_payload = {
                "portfoliocode": portfolio_code,
                "user": user,
                "bidType": bid_type,
                "type": bid_main_type,
                "bidDate": bid_date.strftime("%d-%m-%Y"),
                "OCF": ocf,
                "OCF_Option": ocf_option,
                "OCF_Value": str(int(ocf_value)),
                "bid": st.session_state.gdam_bid_entries
            }

            # Validate bid data
            validation_errors = gdam_client.validate_bid_data(bid_payload)

            if validation_errors:
                st.error("❌ Validation Errors:")
                for error in validation_errors:
                    st.write(f"• {error}")
            else:
                st.success("✅ Bid data validation passed!")

            # Show bid payload
            with st.expander("📄 Bid Payload (JSON)", expanded=False):
                st.json(bid_payload)

            # Download JSON
            json_str = json.dumps(bid_payload, indent=2)
            st.download_button(
                label="📥 Download JSON",
                data=json_str,
                file_name=f"gdam_bid_{bid_date.strftime('%Y%m%d')}.json",
                mime="application/json"
            )

            # Submit bid
            col1, col2 = st.columns(2)

            with col1:
                if st.button("🚀 Submit GDAM Bid", type="primary", disabled=bool(validation_errors)):
                    with st.spinner("Submitting GDAM bid..."):
                        result = gdam_client.submit_gdam_bid(bid_payload)

                        if result["success"]:
                            st.success("✅ GDAM bid submitted successfully!")

                            # Show success details
                            with st.expander("✅ Success Details", expanded=True):
                                st.write("**Status:** Success")
                                st.write("**Message:** Bid submitted successfully")

                                # Show API call information
                                if "api_call_info" in result:
                                    api_info = result["api_call_info"]
                                    st.write("**API Call Details:**")
                                    st.write(f"• **URL:** {api_info['url']}")
                                    st.write(f"• **Method:** {api_info['method']}")
                                    st.write(f"• **Endpoint:** {api_info['endpoint']}")
                                    st.write(f"• **Base URL:** {api_info['base_url']}")

                                if "response" in result:
                                    st.write("**API Response:**")
                                    st.json(result["response"])
                        else:
                            st.error("❌ GDAM Bid submission failed")

                            # Show detailed error information
                            with st.expander("🔍 Error Details", expanded=True):
                                st.write(f"**Error Message:** {result['message']}")

                                # Show API call information
                                if "api_call_info" in result:
                                    api_info = result["api_call_info"]
                                    st.write("**API Call Details:**")
                                    st.write(f"• **URL:** {api_info['url']}")
                                    st.write(f"• **Method:** {api_info['method']}")
                                    st.write(f"• **Endpoint:** {api_info['endpoint']}")
                                    st.write(f"• **Base URL:** {api_info['base_url']}")

                                    # Show headers (without sensitive auth token)
                                    st.write("**Headers:**")
                                    safe_headers = api_info['headers'].copy()
                                    if 'auth' in safe_headers:
                                        safe_headers['auth'] = safe_headers['auth'][:20] + "..." if len(safe_headers['auth']) > 20 else safe_headers['auth']
                                    st.json(safe_headers)

                                if "error_details" in result and result["error_details"]:
                                    st.write(f"**Error Details:** {result['error_details']}")

                                if "status" in result:
                                    st.write(f"**Status:** {result['status']}")

                                if "status_code" in result:
                                    st.write(f"**HTTP Status:** {result['status_code']}")

                                if "response" in result:
                                    st.write("**Full Response:**")
                                    st.json(result["response"])

                                if "errors" in result:
                                    st.write("**Validation Errors:**")
                                    for error in result["errors"]:
                                        st.write(f"• {error}")

            with col2:
                if st.button("🔄 Validate Only"):
                    with st.spinner("Validating bid data..."):
                        if validation_errors:
                            st.error(f"❌ Found {len(validation_errors)} validation errors")
                        else:
                            st.success("✅ Bid data is valid!")

    with tab2:
        st.subheader("📊 Sample Data Testing")
        st.markdown("Test the GDAM API with predefined sample data")

        # Sample bid data
        base_date = calculate_min_gdam_bid_date()

        sample_data = {
            "Single Sell Bid": {
                "portfoliocode": "E1WB0TPT0008",
                "user": "deepraj",
                "bidType": "Single",
                "type": "Sell",
                "bidDate": base_date.strftime("%d-%m-%Y"),
                "OCF": "N",
                "OCF_Option": "Premium",
                "OCF_Value": "0",
                "bid": [
                    {
                        "fromtime": "14:30",
                        "totime": "14:45",
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
            },
            "Block Sell Bid": {
                "portfoliocode": "E1WB0TPT0008",
                "user": "deepraj",
                "bidType": "Block",
                "type": "Sell",
                "bidDate": base_date.strftime("%d-%m-%Y"),
                "OCF": "Y",
                "OCF_Option": "Premium",
                "OCF_Value": "5",
                "bid": [
                    {
                        "fromtime": "10:00",
                        "totime": "10:15",
                        "type": "S",
                        "bidvalue": [
                            {
                                "price": "3.5",
                                "value": "15.0",
                                "type": "S"
                            }
                        ]
                    },
                    {
                        "fromtime": "10:15",
                        "totime": "10:30",
                        "type": "S",
                        "bidvalue": [
                            {
                                "price": "3.5",
                                "value": "15.0",
                                "type": "S"
                            }
                        ]
                    }
                ]
            }
        }

        # Sample selection
        selected_sample = st.selectbox("Select Sample Data", list(sample_data.keys()))

        # Display selected sample
        st.write("**Selected Sample:**")
        st.json(sample_data[selected_sample])

        # Test sample data
        col1, col2 = st.columns(2)

        with col1:
            if st.button("🧪 Test Sample Data", type="primary"):
                with st.spinner("Testing sample data..."):
                    result = gdam_client.submit_gdam_bid(sample_data[selected_sample])

                    if result["success"]:
                        st.success("✅ Sample data test successful!")
                        with st.expander("✅ Success Details", expanded=True):
                            # Show API call information
                            if "api_call_info" in result:
                                api_info = result["api_call_info"]
                                st.write("**API Call Details:**")
                                st.write(f"• **URL:** {api_info['url']}")
                                st.write(f"• **Method:** {api_info['method']}")
                                st.write(f"• **Endpoint:** {api_info['endpoint']}")

                            st.write("**API Response:**")
                            st.json(result["response"])
                    else:
                        st.error("❌ Sample data test failed")
                        with st.expander("🔍 Error Details", expanded=True):
                            st.write(f"**Error:** {result['message']}")

                            # Show API call information
                            if "api_call_info" in result:
                                api_info = result["api_call_info"]
                                st.write("**API Call Details:**")
                                st.write(f"• **URL:** {api_info['url']}")
                                st.write(f"• **Method:** {api_info['method']}")
                                st.write(f"• **Endpoint:** {api_info['endpoint']}")

                            if "response" in result:
                                st.write("**API Response:**")
                                st.json(result["response"])

        with col2:
            # Download sample JSON
            sample_json = json.dumps(sample_data[selected_sample], indent=2)
            st.download_button(
                label="📥 Download Sample JSON",
                data=sample_json,
                file_name=f"gdam_sample_{selected_sample.lower().replace(' ', '_')}.json",
                mime="application/json"
            )

    with tab3:
        st.subheader("📈 GDAM Order Book")
        st.markdown("Retrieve and display GDAM order book information")

        # Order book parameters
        col1, col2 = st.columns(2)

        with col1:
            ob_portfolio = st.text_input("Portfolio Code", value="E1WB0TPT0008", key="ob_portfolio")
            ob_user = st.text_input("User", value="deepraj", key="ob_user")

        with col2:
            ob_date = st.date_input("Order Book Date", value=base_date, min_value=base_date, key="ob_date")

        if st.button("📊 Get Order Book"):
            st.info("🚧 Order book functionality will be implemented when the endpoint is available")

            # Placeholder for order book API call
            # This would call the GetGDAMOrderBookService endpoint
            # when it becomes available

def main():
    st.set_page_config(
        page_title="GDAM API Testing Interface",
        page_icon="🌱",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    st.title("🌱 GDAM API Testing Interface")
    st.markdown("**Green Day-Ahead Market (GDAM) Bid Submission & Testing**")

    # Initialize session state
    if 'gdam_bid_entries' not in st.session_state:
        st.session_state.gdam_bid_entries = []

    gdam_api_testing_page()

if __name__ == "__main__":
    main()
