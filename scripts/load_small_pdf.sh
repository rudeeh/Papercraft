#!/bin/bash
# load_small_pdf.sh
# Usage: ./scripts/load_small_pdf.sh <optional_pdf_path>

PDF_PATH=${1:-"tests/data/small.pdf"}
API_URL="http://localhost:8000/api/v1"

echo "Uploading $PDF_PATH..."
response=$(curl -s -X POST -F "file=@$PDF_PATH" "$API_URL/upload")
echo "Response: $response"

task_id=$(echo $response | grep -o '"task_id":"[^"]*' | cut -d'"' -f4)
doc_id=$(echo $response | grep -o '"doc_id":"[^"]*' | cut -d'"' -f4)

if [ -z "$task_id" ]; then
    echo "Upload failed or already exists (check response)"
    exit 1
fi

echo "Task ID: $task_id"
echo "Doc ID: $doc_id"

echo "Waiting for processing..."
while true; do
    status=$(curl -s "$API_URL/status/$task_id" | grep -o '"status":"[^"]*' | cut -d'"' -f4)
    echo "Status: $status"
    if [ "$status" == "SUCCESS" ] || [ "$status" == "completed" ]; then
        echo "Processing Complete!"
        break
    fi
    if [ "$status" == "FAILURE" ]; then
        echo "Processing Failed!"
        exit 1
    fi
    sleep 2
done
