#!/bin/bash
set -e
cd "$(dirname "$0")"
set -a; source .env; set +a
SNAP="${1:-sd_mp4wuyw93tuqj2gxd}"
echo "Progress endpoint:"
curl -s -H "Authorization: Bearer $BRIGHT_DATA_API_TOKEN" \
  "https://api.brightdata.com/datasets/v3/progress/$SNAP"
echo
echo "Snapshot status (just HTTP code + headers):"
curl -s -o /dev/null -w "HTTP %{http_code}, %{size_download} bytes\n" \
  -H "Authorization: Bearer $BRIGHT_DATA_API_TOKEN" \
  "https://api.brightdata.com/datasets/v3/snapshot/$SNAP?format=json"
