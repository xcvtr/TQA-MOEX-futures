#!/bin/bash
cd /home/user/projects/TQA-MOEX
LINE=$(grep ALGOPACK_APIKEY .env)
TOKEN***
echo "Token len: ${#TOKEN}"

BASE="https://apim.moex.com/iss/datashop/algopack/fo"

for name in "tradestats" "obstats"; do
  data=$(curl -s -H "Authorization: Bearer $TOKEN" "$BASE/${name}.json?date=2025-06-17")
  total=$(echo "$data" | python3 -c "import sys,json;d=json.load(sys.stdin);c=d.get('data.cursor',{}).get('data',[]);print(c[0][1] if c else 0)")
  kb=$(echo "$data" | python3 -c "import sys;print(len(sys.stdin.buffer.read())/1024)")
  tickers=$(echo "$data" | python3 -c "
import sys, json
d = json.load(sys.stdin)['data']['data']
print(len(set(r[2] for r in d if len(r) > 3)) if d else 0)
")
  echo "${name}: ${total} rows, ${kb} KB, tickers=${tickers}"
done

futoi=$(curl -s -H "Authorization: Bearer $TOKEN" "https://apim.moex.com/iss/analyticalproducts/futoi/securities.json")
echo "FUTOI: $(echo "$futoi" | python3 -c "import sys,json;d=json.load(sys.stdin)['futoi']['data'];print(len(d),'rows')")"
