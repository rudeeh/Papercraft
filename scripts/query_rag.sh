
# Usage: ./scripts/query_rag.sh "Question"

QUERY=${1:-"What is the content of this document?"}
API_URL="http://localhost:8000/api/v1"

echo "Querying: $QUERY"

# Construct JSON payload
# escape quotes
SAFE_QUERY=$(echo "$QUERY" | sed 's/"/\\"/g')
PAYLOAD="{\"query\": \"$SAFE_QUERY\"}"

response=$(curl -s -X POST -H "Content-Type: application/json" -d "$PAYLOAD" "$API_URL/chat")

echo "Response:"
echo "$response"
