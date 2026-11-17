"""Geçici tanı: EPİAŞ YEKDEM birim maliyet cevabının tamamı (tüm sürümler)."""
import json, os, sys
sys.path.insert(0, "yayinci")
from datetime import datetime
from yayinla import Epias, iso, items, UNIT_COST_PATH

client = Epias(os.environ["EPIAS_KULLANICI"], os.environ["EPIAS_SIFRE"])
for start, end in (((2026, 8, 1), (2026, 11, 1)),):
    body = {"startDate": iso(datetime(*start)), "endDate": iso(datetime(*end))}
    payload = client.post(UNIT_COST_PATH, body)
    print("BODY", body)
    print("KEYS", list(payload.keys()))
    for it in items(payload):
        print(json.dumps(it, ensure_ascii=False))
    print("RAW", json.dumps(payload, ensure_ascii=False)[:3000])
for path in ("/v1/renewables/data/unit-cost-realized", "/v1/renewables/data/realized-unit-cost"):
    try:
        print(path, json.dumps(client.post(path, {"startDate": iso(datetime(2026, 9, 1)), "endDate": iso(datetime(2026, 9, 1))}), ensure_ascii=False)[:800])
    except Exception as exc:
        print(path, "HATA", exc)
