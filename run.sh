#!/bin/bash
# Simple runner script for DLNA Sharing TUI

echo "🚀 Starting DLNA Sharing TUI..."
echo "📺 Make sure your TV is turned on and connected to the same network"
echo ""
echo "Debug logs will show discovery progress..."
echo ""

uv run dlna_sharing.py 2>&1 | tee /tmp/dlna_debug.log
