# FTP Schedule Processor with RTM API Integration

A Streamlit application that automatically connects to an FTP server, processes energy schedule files from ALL client folders simultaneously, generates JSON output for bidding, and submits bids directly to the RTM (Real-Time Market) API.

## Features

- **Auto-connects** to FTP server on page load using current date
- **Processes ALL client folders** simultaneously - no manual selection needed
- Automatically finds the latest revision files based on date and revision patterns
- Processes Excel files to extract time block values from all folders
- **Individual manual input** for each folder when FTP files are not available
- Generates separate JSON output for each client folder
- Integrates with CIPC mapping for portfolio and user information
- **Real-time processing** with immediate results display
- **Sidebar CIPC Mapping Management** - Add/remove portfolio mappings on the fly
- **RTM API Integration** - Direct submission of bids to SAMASTT RTM API
- **Orderbook Monitoring** - Check bid status through RTM orderbook API
- **Bulk Operations** - Submit all bids, check all orderbooks, download all JSONs at once

## Installation

1. Install required packages:
```bash
pip install -r requirements.txt
```

2. Ensure `CIPIC_Mapping.xlsx` is in the same directory as the application

## Usage

1. Run the Streamlit application:
```bash
streamlit run main.py
```

2. The application automatically:
   - Uses current date for processing
   - Connects to FTP server on page load
   - Processes ALL client folders simultaneously

3. For each folder, you can:
   - **Generate JSON**: Create RTM-compatible JSON output
   - **Submit to RTM API**: Send bid directly to SAMASTT RTM API
   - **Check Orderbook**: Monitor bid status through RTM API
   - **Download JSON**: Save JSON file locally

4. Use **Bulk Operations** to:
   - Submit all bids to RTM API at once
   - Check orderbook status for all portfolios
   - Download ZIP file with all JSON outputs

5. **Manual Input**: If IEX files are missing, enter 96 time block values manually

6. **CIPC Mapping**: Use sidebar to add/remove portfolio mappings as needed

## File Patterns

### Schedule Files
The application supports multiple schedule file patterns:

**Pattern 1 - Day Ahead Short:**
- Example: `Saisei Energy_S1KA0TPT0834_DA_27-08-2025_ DA RDA1.csv`
- Contains `_DA_` and revision `RDA1`, `RDA2`, etc.

**Pattern 2 - Intraday Short:**
- Example: `Company_Portfolio_ID_27-08-2025_ ID RID1.csv`
- Contains `_ID_` and revision `RID1`, `RID2`, etc.

**Pattern 3 - Intraday Full:**
- Example: `Iris Renewables_S1KA0TPT0832_IntraDay_27-08-2025_ IntraDay RID1.csv`
- Contains `IntraDay` and revision `RID1`, `RID2`, etc.

**Pattern 4 - Day Ahead Full:**
- Example: `Company_DayAhead_27-08-2025_ DayAhead RDA1.csv`
- Contains `DayAhead` and revision `RDA1`, `RDA2`, etc.

**Common Features:**
- Contains date in format `DD-MM-YYYY`
- Revision priority: RID (Intraday) > RDA (Day Ahead)
- Higher revision numbers are considered latest
- Values extracted from range E12:E107 (96 time blocks)

### IEX Files
- Pattern: `IEX250827SCH_MAN2074_CP0_Continuum_Power_Trading`
- Date format: `YYMMDD` (previous day)
- Values extracted from range F11:F106 (96 time blocks)
- Negative values are converted to positive

## JSON Output Format

The application generates JSON in the following format:
```json
{
    "portfoliocode": "portfolio_code",
    "user": "user_id",
    "bidType": "Block",
    "type": "Sell",
    "bidDate": "27-08-2025",
    "bid": [
        {
            "fromtime": "22:00",
            "totime": "22:15",
            "type": "S",
            "bidvalue": [
                {
                    "price": "10.0",
                    "value": "2.0",
                    "type": "S"
                }
            ]
        }
    ]
}
```

## Configuration

### FTP Configuration
FTP credentials are configured in the application:
- Host: 15.207.32.135
- User: partner
- Password: jEm9P6182x89
- Port: 21
- Base Path: /WIND/SCHEDULE/KA/CIP_Hatalageri/

### RTM API Configuration
RTM API settings for SAMASTT UAT environment:
- Base URL: http://52.172.198.122/samastt_QA1/api/rtm
- Submit Endpoint: /AddNewRTMBidService
- Orderbook Endpoint: /orderBookResponse
- Authentication: Bearer token (configured in application)
- Device: streamlit_app

### API Endpoints
1. **Submit Bid**: `POST /AddNewRTMBidService`
   - Submits RTM bids with portfolio, user, and time block data
   - Returns submission status and response

2. **Check Orderbook**: `GET /orderBookResponse`
   - Retrieves bid status and orderbook information
   - Requires portfolio code, user, bid type, and date

## Testing RTM API Integration

Use the provided test script to verify RTM API connectivity:

```bash
python test_rtm_api.py
```

This script tests:
- Basic API connectivity
- Bid submission endpoint
- Orderbook retrieval endpoint

## Troubleshooting

### RTM API Issues
- **Connection Error**: Check network connectivity and API URL
- **Authentication Error**: Verify the authorization token is valid
- **Timeout**: API may be slow, increase timeout values if needed
- **Invalid Response**: Check API documentation for expected payload format

### FTP Issues
- **Connection Failed**: Verify FTP credentials and network access
- **File Not Found**: Check date format and file naming patterns
- **Permission Denied**: Ensure FTP user has read access to folders

### Data Issues
- **Missing Portfolio Mapping**: Add mapping in sidebar CIPC management
- **Invalid Time Blocks**: Ensure 96 values (15-minute intervals for 24 hours)
- **No Positive Differences**: Check if schedule values exceed IEX values

## Notes

- Only positive differences between schedule and IEX values are included in the output
- Time blocks are 15-minute intervals (96 blocks for 24 hours)
- Price is fixed at 10.0 as specified
- The application handles various revision patterns (RDA, RID) automatically
- RTM API submissions are logged with status indicators
- Bulk operations include progress tracking and error handling
