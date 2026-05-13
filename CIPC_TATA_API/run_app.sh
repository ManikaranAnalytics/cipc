#!/bin/bash

# FTP Schedule Processor Launch Script

echo "Starting FTP Schedule Processor..."
echo "=================================="

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed or not in PATH"
    exit 1
fi

# Check if pip is available
if ! command -v pip3 &> /dev/null; then
    echo "Error: pip3 is not installed or not in PATH"
    exit 1
fi

# Install requirements if they don't exist
echo "Installing/checking requirements..."
pip3 install -r requirements.txt

# Check if CIPC mapping file exists
if [ ! -f "CIPIC_Mapping.xlsx" ]; then
    echo "Warning: CIPIC_Mapping.xlsx not found in current directory"
    echo "Please ensure this file is present for portfolio mapping to work"
fi

# Run the Streamlit application
echo "Launching Streamlit application..."
echo "The application will open in your default web browser"
echo "Press Ctrl+C to stop the application"
echo ""

streamlit run main.py
