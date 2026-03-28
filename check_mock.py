import urllib.request, json

jid = "7a499b01-dc90-470a-be0d-206f7818b187"

# Test LLM analysis endpoint
r2 = urllib.request.urlopen(f"http://localhost:8000/api/v1/llm/jobs/{jid}/llm-analysis")
d2 = json.loads(r2.read())
e = d2.get("extracted", {})
print("LLM endpoint OK")
print("  od=%s  max_od=%s  id=%s  len=%s" % (e.get("od_in"), e.get("max_od_in"), e.get("id_in"), e.get("length_in")))
print("  valid=%s  confidence=%s" % (d2.get("valid"), d2["validation"]["overall_confidence"]))

# Test file serving
r = urllib.request.urlopen(f"http://localhost:8000/api/v1/jobs/{jid}/files/outputs/inferred_stack.json")
d = json.loads(r.read())
segs = d.get("segments", [])
print("Stack file OK: %d segments" % len(segs))
for s in segs:
    print("  OD=%.3f  ID=%.3f  z=%.3f-%.3f" % (s["od_diameter"], s["id_diameter"], s["z_start"], s["z_end"]))
