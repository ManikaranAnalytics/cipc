# DAM (Day-Ahead Market) API Testing Interface

## 🚀 Overview

This Streamlit application provides a comprehensive testing interface for the DAM (Day-Ahead Market) APIs. It includes both manual API testing capabilities and automated FTP schedule processing for bid submission.

## 🔧 API Configuration

### Base Configuration
- **Base URL**: `http://52.172.198.122/samastt_QA1`
- **Authorization**: `ZCI6InNlY3JldF9jbGllbnRfaWQiLCJzY29wZSI6WyJhcGlzY29wZSJdfQ.iWLQUtWPmbW3GNyZW8Pimu-Kj-AC0D9_IAMRgh`
- **Device**: `postman`

### API Endpoints
1. **Submit Bid**: `/api/dam/AddNewDAMBidService` (POST)
2. **Get Orderbook**: `/api/dam/orderBookResponse` (GET)

## 📋 Features

### 🧪 DAM API Testing Interface

#### 1. Submit DAM Bid Tab
- **Interactive Bid Builder**: Create custom DAM bids with multiple time blocks
- **Real-time Validation**: Validates bid data according to DAM API requirements
- **Price-Quantity Management**: Add multiple price-quantity pairs per time block
- **Flexible Parameters**: Configure portfolio, user, bid type, dates, and RTM carry-forward

#### 2. Get Orderbook Tab
- **Orderbook Retrieval**: Fetch DAM orderbook data for specific portfolios and dates
- **Response Visualization**: Display orderbook data in JSON and table formats
- **Parameter Configuration**: Customize portfolio code, user, bid type, and date

#### 3. Quick Test Tab
- **Predefined Samples**: Test with pre-configured bid scenarios
- **Sample Types**:
  - Single Buy Bid
  - Single Sell Bid
  - Block Buy Bid
  - Block Sell Bid
  - Mixed Bid (Both Buy & Sell)

### 📊 FTP Schedule Processing
- **Automated Processing**: Connect to FTP and process all client folders
- **CIPC Mapping**: Load portfolio mappings from Excel file
- **Schedule Analysis**: Compare schedule vs IEX files
- **Bulk Operations**: Submit multiple DAM bids simultaneously

## 🔍 API Validation Rules

### Price Validation
- **Range**: 0-10 Rs/KWh
- **Decimal Places**: Maximum 3 decimal places
- **Format**: Numeric string

### Quantity Validation
- **Minimum**: 0.1 MW
- **Decimal Places**: Maximum 1 decimal place
- **Buy Bids**: Must be positive
- **Sell Bids**: Handled internally as negative

### Time Validation
- **Format**: HH:MM (24-hour format)
- **Logic**: fromtime < totime
- **No Overlapping**: Time periods cannot overlap

### Limits
- **Maximum Bid Records**: 96 per submission
- **Maximum Price Points**: 49 per bid record

## 📤 Request Format

### Submit Bid Request
```json
{
  "portfoliocode": "E1WB0TPT0008",
  "user": "deepraj",
  "bidType": "Single",
  "type": "Buy",
  "bidDate": "08-10-2025",
  "Carry_Forward_To_RTM": "yes",
  "Price_Variation": "0",
  "bid": [
    {
      "fromtime": "14:30",
      "totime": "15:30",
      "type": "B",
      "bidvalue": [
        {
          "price": "10",
          "value": "10",
          "type": "B"
        }
      ]
    }
  ]
}
```

### Get Orderbook Request
```json
{
  "portfoliocode": "E1WB0TPT0008",
  "user": "deepraj",
  "bidType": "Single",
  "bidDate": "07-10-2025"
}
```

## 📥 Response Format

### Success Response (200)
```json
{
  "status": "200",
  "message": "DAM Bid added successfully",
  "result": true
}
```

### Error Response (400)
```json
{
  "status": "400",
  "message": "Price should be between 0 and 10 Rs/KWh",
  "result": false
}
```

### Unauthorized (401)
```json
{
  "status": "401",
  "message": "Unauthorized access due to invalid credentials",
  "result": false
}
```

## 🚀 Getting Started

### Prerequisites
```bash
pip install streamlit pandas requests ftplib openpyxl
```

### Running the Application
```bash
streamlit run dam_main.py
```

### Navigation
1. **🧪 DAM API Testing**: Manual API testing with custom parameters
2. **📊 FTP Schedule Processing**: Automated schedule processing and bid submission

## 🔐 Authentication

The application uses Bearer token authentication:
- **Header**: `Authorization`
- **Value**: `ZCI6InNlY3JldF_cGllbnRfaWQiLCJzY29wZSI6WyJhcGlzY29wZSJdfQ.iWLQUtWPmbW3GNyZW8Pimu-Kj-AC0D9_IAMRgh`
- **Device Header**: `device: postman`

## 🎯 Key Features

### ✅ Validation Engine
- Real-time bid data validation
- Error highlighting and suggestions
- Compliance with DAM API requirements

### 📊 Response Analysis
- Detailed API response display
- Error debugging information
- Success/failure tracking

### 🔄 Bulk Operations
- Process multiple portfolios simultaneously
- Automated bid generation from schedule files
- Progress tracking and status updates

### 💾 Data Management
- Session state management for bid entries
- JSON export functionality
- Historical data preservation

## 🛠️ Troubleshooting

### Common Issues
1. **Connection Errors**: Check network connectivity and API endpoint availability
2. **Authentication Failures**: Verify authorization token and device header
3. **Validation Errors**: Review bid data against validation rules
4. **Timeout Issues**: Increase timeout values for slow network connections

### Debug Features
- Request/response logging
- Detailed error messages
- API configuration display
- Network diagnostics

## 📞 Support

For technical support or API-related queries, please refer to the DAM API documentation or contact the development team.

---

**Version**: 1.0  
**Last Updated**: October 2025  
**Environment**: UAT (User Acceptance Testing)
