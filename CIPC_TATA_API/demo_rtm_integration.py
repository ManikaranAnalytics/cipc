#!/usr/bin/env python3
"""
Demo script showing RTM API integration
Creates sample JSON and demonstrates API calls
"""

import json
from datetime import datetime
from main import RTMAPIClient, FTPScheduleProcessor

def create_sample_bid_data():
    """Create sample bid data for demonstration"""
    
    # Sample time blocks with positive differences
    sample_differences = [0] * 96  # Initialize all to 0
    
    # Add some positive values for demonstration
    sample_differences[80] = 2.5   # 20:00-20:15
    sample_differences[81] = 1.8   # 20:15-20:30
    sample_differences[82] = 3.2   # 20:30-20:45
    sample_differences[83] = 1.5   # 20:45-21:00
    
    # Create processor instance
    processor = FTPScheduleProcessor()
    
    # Generate JSON using the processor's method
    json_output = processor.generate_json_output(
        differences=sample_differences,
        portfolio_code="E1WB0TPT0008",
        user_id="deepraj",
        bid_date=datetime.now()
    )
    
    return json_output

def demo_rtm_submission():
    """Demonstrate RTM bid submission"""
    print("🚀 RTM API Integration Demo")
    print("=" * 50)
    
    # Create sample bid data
    print("📦 Creating sample bid data...")
    bid_data = create_sample_bid_data()
    
    print(f"✅ Generated bid with {len(bid_data['bid'])} time blocks:")
    for i, bid in enumerate(bid_data['bid'][:3]):  # Show first 3 entries
        print(f"   {i+1}. {bid['fromtime']}-{bid['totime']}: {bid['bidvalue'][0]['value']} MW")
    
    if len(bid_data['bid']) > 3:
        print(f"   ... and {len(bid_data['bid']) - 3} more entries")
    
    print(f"\n📊 Bid Summary:")
    print(f"   Portfolio: {bid_data['portfoliocode']}")
    print(f"   User: {bid_data['user']}")
    print(f"   Date: {bid_data['bidDate']}")
    print(f"   Total Entries: {len(bid_data['bid'])}")
    
    # Show full JSON
    print(f"\n📄 Complete JSON Payload:")
    print(json.dumps(bid_data, indent=2))
    
    # Create RTM client
    print(f"\n🔗 Initializing RTM API Client...")
    rtm_client = RTMAPIClient()
    
    # Ask user if they want to submit
    print(f"\n⚠️  This will submit a real bid to the RTM API!")
    response = input("Do you want to proceed with submission? (y/N): ")
    
    if response.lower() == 'y':
        print(f"\n🚀 Submitting bid to RTM API...")
        result = rtm_client.submit_bid(bid_data)
        
        if result["success"]:
            print(f"✅ SUCCESS: Bid submitted successfully!")
            print(f"📊 Status Code: {result['status_code']}")
            if result.get("response"):
                print(f"📄 API Response:")
                print(json.dumps(result["response"], indent=2))
        else:
            print(f"❌ FAILED: {result['message']}")
            if result.get("response"):
                print(f"📄 Error Response: {result['response']}")
    else:
        print(f"ℹ️  Submission cancelled by user")
    
    # Demonstrate orderbook check
    print(f"\n📊 Checking orderbook...")
    orderbook_result = rtm_client.check_orderbook(
        portfolio_code=bid_data['portfoliocode'],
        user=bid_data['user'],
        bid_date=bid_data['bidDate']
    )
    
    if orderbook_result["success"]:
        print(f"✅ Orderbook retrieved successfully!")
        if orderbook_result.get("response"):
            print(f"📄 Orderbook Data:")
            print(json.dumps(orderbook_result["response"], indent=2))
    else:
        print(f"❌ Failed to get orderbook: {orderbook_result['message']}")

def demo_json_structure():
    """Show the JSON structure that matches RTM API requirements"""
    print("\n📋 RTM API JSON Structure")
    print("=" * 50)
    
    sample_json = {
        "portfoliocode": "E1WB0TPT0008",
        "user": "deepraj", 
        "bidType": "Block",
        "type": "Sell",
        "bidDate": "11-09-2025",
        "bid": [
            {
                "fromtime": "20:30",
                "totime": "20:45", 
                "type": "S",
                "bidvalue": [
                    {
                        "price": "10.0",
                        "value": "2.5",
                        "type": "S"
                    }
                ]
            }
        ]
    }
    
    print("📄 Expected JSON format for RTM API:")
    print(json.dumps(sample_json, indent=2))
    
    print(f"\n📝 Field Descriptions:")
    print(f"   portfoliocode: Client portfolio identifier")
    print(f"   user: User/client name")
    print(f"   bidType: Always 'Block' for block bids")
    print(f"   type: Always 'Sell' for selling excess power")
    print(f"   bidDate: Date in DD-MM-YYYY format")
    print(f"   bid: Array of time block entries")
    print(f"   fromtime/totime: 15-minute time slots")
    print(f"   price: Fixed at 10.0")
    print(f"   value: Positive difference (Schedule - IEX)")

def main():
    """Run the RTM integration demo"""
    try:
        # Show JSON structure first
        demo_json_structure()
        
        # Run the submission demo
        demo_rtm_submission()
        
        print(f"\n🎉 Demo completed!")
        print(f"💡 Use the Streamlit app for full functionality with FTP integration")
        
    except Exception as e:
        print(f"💥 Demo error: {str(e)}")
        print(f"🔧 Make sure the RTM API is accessible and credentials are valid")

if __name__ == "__main__":
    main()
