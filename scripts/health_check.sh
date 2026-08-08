#!/bin/bash

# Comprehensive health check script for DocRAG GraphRAG system
# This script verifies the backend, tests, and frontend.

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Starting comprehensive health check...${NC}"

# 1. Check Python Environment
echo -e "\n${YELLOW}[1/4] Checking Python Environment and Tests...${NC}"
if ! command -v pytest &> /dev/null; then
    echo -e "${RED}pytest not found. Please install dependencies from requirements.txt${NC}"
    exit 1
fi

echo -e "Running all tests..."
export PYTHONPATH=$PYTHONPATH:.
pytest tests/test_ontology.py \
       tests/test_parser.py \
       tests/test_integration_phase_1_3.py \
       tests/test_citation_extraction.py \
       tests/test_api.py -v --tb=short

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed successfully!${NC}"
else
    echo -e "${RED}✗ Some tests failed.${NC}"
    exit 1
fi

# 2. Check Backend Startup
echo -e "\n${YELLOW}[2/4] Verifying Backend API Startup...${NC}"
PYTHONPATH=. uvicorn app.api.main:app --host 0.0.0.0 --port 8000 > /tmp/backend_check.log 2>&1 &
BACKEND_PID=$!

# Wait for backend to start
max_retries=10
count=0
success=false

while [ $count -lt $max_retries ]; do
    if curl -s http://localhost:8000/api/v1/health | grep -q "ok"; then
        success=true
        break
    fi
    echo -e "Waiting for backend... ($((count+1))/$max_retries)"
    sleep 2
    count=$((count+1))
done

kill $BACKEND_PID

if [ "$success" = true ]; then
    echo -e "${GREEN}✓ Backend API started and responded to health check!${NC}"
else
    echo -e "${RED}✗ Backend API failed to start or respond. Check /tmp/backend_check.log${NC}"
    exit 1
fi

# 3. Check Frontend Build
echo -e "\n${YELLOW}[3/4] Verifying Frontend Build...${NC}"
cd frontend-angular
if npm run build > /tmp/frontend_build.log 2>&1; then
    echo -e "${GREEN}✓ Frontend built successfully!${NC}"
else
    echo -e "${RED}✗ Frontend build failed. Check /tmp/frontend_build.log${NC}"
    cd ..
    exit 1
fi
cd ..

# 4. Final Summary
echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}HEALTH CHECK PASSED!${NC}"
echo -e "The system is working fine locally."
echo -e "${GREEN}========================================${NC}"
