import json
import server

server.init_db()
result = server.run_freshness_checks(force_frontdoor_refresh=True)
print(json.dumps(result, indent=2, ensure_ascii=False))
