#!/bin/bash
# load_large_pdf.sh
# Usage: ./scripts/load_large_pdf.sh <large_pdf_path>

PDF_PATH=${1:-"tests/data/large.pdf"}
API_URL="http://localhost:8000/api/v1"

if [ ! -f "$PDF_PATH" ]; then
    echo "File not found: $PDF_PATH"
    echo "Usage: $0 <path_to_large_pdf>"
    exit 1
fi

echo "Uploading Large PDF: $PDF_PATH..."
response=$(curl -s -X POST -F "file=@$PDF_PATH" "$API_URL/upload")
echo "Response: $response"

task_id=$(echo $response | grep -o '"task_id":"[^"]*' | cut -d'"' -f4)

if [ -z "$task_id" ]; then
    echo "Upload failed"
    exit 1
fi

echo "Task ID: $task_id"
echo "Monitoring progress (this may take a while)..."

while true; do
    status_json=$(curl -s "$API_URL/status/$task_id")
    status=$(echo $status_json | grep -o '"status":"[^"]*' | cut -d'"' -f4)
    echo "Status: $status"
    
    if [ "$status" == "SUCCESS" ] || [ "$status" == "completed" ]; then
        echo "Processing Complete!"
        echo "Result: $status_json"
        break
    fi
    if [ "$status" == "FAILURE" ]; then
        echo "Processing Failed!"
        echo "Details: $status_json"
        exit 1
    fi
    sleep 5
done
