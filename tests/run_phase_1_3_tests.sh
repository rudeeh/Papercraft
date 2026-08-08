#!/bin/bash

# Test runner for Phase 1-3 components
# Validates ontology, parser, and integration

set -e

echo "========================================"
echo "Phase 1-3 Test Suite"
echo "========================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if pytest is available
if ! command -v pytest &> /dev/null; then
    echo -e "${RED}pytest not found. Installing...${NC}"
    pip install pytest pytest-asyncio pytest-cov
fi

echo -e "${YELLOW}Running ontology tests...${NC}"
pytest tests/test_ontology.py -v --tb=short
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Ontology tests passed${NC}"
else
    echo -e "${RED}✗ Ontology tests failed${NC}"
    exit 1
fi
echo ""

echo -e "${YELLOW}Running parser tests...${NC}"
pytest tests/test_parser.py -v --tb=short
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Parser tests passed${NC}"
else
    echo -e "${RED}✗ Parser tests failed${NC}"
    exit 1
fi
echo ""

echo -e "${YELLOW}Running integration tests...${NC}"
pytest tests/test_integration_phase_1_3.py -v --tb=short
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Integration tests passed${NC}"
else
    echo -e "${RED}✗ Integration tests failed${NC}"
    exit 1
fi
echo ""

# Run all tests with coverage
echo -e "${YELLOW}Running all Phase 1-3 tests with coverage...${NC}"
pytest tests/test_ontology.py tests/test_parser.py tests/test_integration_phase_1_3.py \
    --cov=app/graph \
    --cov=app/paper \
    --cov-report=term-missing \
    -v

echo ""
echo -e "${GREEN}========================================"
echo "All Phase 1-3 tests completed successfully!"
echo "========================================${NC}"
