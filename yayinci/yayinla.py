"""GİZA veri servisi yayıncısı (GitHub Actions'ta çalışır).

EPİAŞ Şeffaflık Platformu'ndan PTF ve gerçekleşen YEKDEM birim maliyetini çeker, ``veri/`` altına
JSON olarak yazar ve tüm dosyaların SHA-256 özetini içeren ``veri/manifest.json`` dosyasını veri
anahtarıyla imzalar. GİZA Energy Suite bu imzayı doğrulamadan hiçbir veriyi kullanmaz.

Ortam değişkenleri (GitHub > Settings > Secrets and variables > Actions):
    EPIAS_KULLANICI, EPIAS_SIFRE   EPİAŞ Şeffaflık hesabı
    GIZA_VERI_ANAHTAR              veri imza anahtarı (64 hane hex; dosyadaki 2. satır)

Yerel deneme:  python yayinci/yayinla.py --kuru   (EPİAŞ'a bağlanır, imzalamaz, yazmaz)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ed25519  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VERI = ROOT / "veri"
TR = timezone(timedelta(hours=3))
LOGIN_URL = "https://giris.epias.com.tr/cas/v1/tickets"
BASE_URL = "https://seffaflik.epias.com.tr"
MCP_PATH = "/electricity-service/v1/markets/dam/data/mcp"
UNIT_COST_PATH = "/electricity-service/v1/renewables/data/unit-cost"
FIRST_MONTH = (2022, 1)
DATA_DOMAIN = b"GIZA-VERI-V1\n"
DATA_PUBLIC_KEY_HEX = "7500c00f1f5d443b80988986a13dcd57556f219d1b001395d8f8c2b86aac8baa"
TIMEOUT = (15, 90)


class Epias:
    def __init__(self, user: str, password: str):
        self.session = requests.Session()
        response = self.session.post(
            LOGIN_URL, data={"username": user, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/plain"}, timeout=TIMEOUT)
        text = (response.text or "").strip()
        if response.status_code not in (200, 201) or not text.startswith("TGT-"):
            raise SystemExit(f"EPİAŞ girişi başarısız (HTTP {response.status_code}). Kullanıcı/şifre secret'larını kontrol edin.")
        self.tgt = text

    def post(self, path: str, body: dict) -> dict:
        response = self.session.post(BASE_URL + path, json=body, timeout=TIMEOUT,
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              "TGT": self.tgt})
        if response.status_code not in (200, 201):
            raise RuntimeError(f"EPİAŞ HTTP {response.status_code}: {response.text[:200]}")
        return response.json()


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S+03:00")


def items(payload: dict) -> list:
    return payload.get("items") or (payload.get("body") or {}).get("items") or []


def local_moment(item: dict) -> datetime | None:
    raw = item.get("date")
    if not raw:
        return None
    moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if moment.tzinfo is not None:
        moment = moment.astimezone(TR).replace(tzinfo=None)
    hour = item.get("hour")
    if isinstance(hour, str) and ":" in hour and moment.hour == 0:
        moment = moment.replace(hour=int(hour.split(":")[0]))
    return moment


def price_of(item: dict) -> Decimal | None:
    value = item.get("price", item.get("ptf"))
    if value is None:
        return None
    return Decimal(str(value))


def fetch_hours(client: Epias, start: datetime, end: datetime) -> dict[datetime, Decimal]:
    payload = client.post(MCP_PATH, {"startDate": iso(start), "endDate": iso(end)})
    hours = {}
    for item in items(payload):
        moment, price = local_moment(item), price_of(item)
        if moment is not None and price is not None and start <= moment <= end:
            hours[moment] = price
    return hours


def month_hours(year: int, month: int) -> int:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - date(year, month, 1)).days * 24


def write_json(path: Path, value) -> bool:
    """Değiştiyse yazar; değiştiyse True."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def publish_month(client: Epias, year: int, month: int) -> bool:
    path = VERI / "ptf" / "aylik" / f"{year:04d}-{month:02d}.json"
    if path.exists():
        return False
    start = datetime(year, month, 1)
    end = start + timedelta(hours=month_hours(year, month) - 1)
    hours = fetch_hours(client, start, end)
    if len(hours) < month_hours(year, month):
        print(f"  {year}-{month:02d}: {len(hours)}/{month_hours(year, month)} saat, atlandı")
        return False
    rows = [[moment.strftime("%Y-%m-%dT%H:%M"), str(hours[moment])] for moment in sorted(hours)]
    return write_json(path, {"period": f"{year:04d}-{month:02d}", "source": "EPİAŞ Şeffaflık Platformu", "hours": rows})


def publish_day(client: Epias, day: date) -> bool:
    path = VERI / "ptf" / "gunluk" / f"{day.isoformat()}.json"
    if path.exists():
        return False
    start = datetime(day.year, day.month, day.day)
    hours = fetch_hours(client, start, start + timedelta(hours=23))
    if len(hours) < 24:
        print(f"  {day}: {len(hours)}/24 saat (henüz yayımlanmamış olabilir)")
        return False
    prices = [str(hours[start + timedelta(hours=h)]) for h in range(24)]
    return write_json(path, {"date": day.isoformat(), "prices": prices})


_COST_KEYS = ("unitcost", "yekdemunitcost", "yekunitcost", "cost", "value", "price")


def version_rank(version: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", version or "")) or (0,)


def fetch_yekdem(client: Epias, year: int, month: int) -> dict | None:
    start = iso(datetime(year, month, 1))
    best = None
    for item in items(client.post(UNIT_COST_PATH, {"startDate": start, "endDate": start})):
        lowered = {k.lower(): v for k, v in item.items()}
        cost = next((lowered[k] for k in _COST_KEYS if lowered.get(k) is not None), None)
        if cost is None:
            continue
        version = str(lowered.get("version") or lowered.get("versionno") or "")
        raw = item.get("period") or item.get("date")
        if raw:
            try:
                stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if (stamp.year, stamp.month) != (year, month):
                    continue
            except ValueError:
                pass
        candidate = {"unit_cost": str(Decimal(str(cost))), "version": version}
        if best is None or version_rank(version) >= version_rank(best["version"]):
            best = candidate
    return best


def months_between(first: tuple[int, int], last: tuple[int, int]):
    year, month = first
    while (year, month) <= last:
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def build_manifest(secret: bytes | None) -> None:
    files = {}
    for path in sorted(VERI.rglob("*.json")):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("veri/manifest"):
            continue
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"schema": 1, "generated_at": datetime.now(TR).isoformat(timespec="seconds"), "files": files}
    body = json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
    if secret is None:
        print(f"[kuru] manifest {len(files)} dosya")
        return
    signature = base64.b64encode(ed25519.sign(secret, DATA_DOMAIN + body)).decode("ascii")
    assert ed25519.verify(bytes.fromhex(DATA_PUBLIC_KEY_HEX), DATA_DOMAIN + body, base64.b64decode(signature))
    (VERI / "manifest.json").write_bytes(body)
    (VERI / "manifest.sig").write_text(signature + "\n", encoding="ascii")
    print(f"manifest: {len(files)} dosya imzalandı")


def read_secret() -> bytes:
    raw = os.environ.get("GIZA_VERI_ANAHTAR", "").strip()
    match = re.search(r"\b[0-9a-fA-F]{64}\b", raw)
    if not match:
        raise SystemExit("GIZA_VERI_ANAHTAR secret'ı tanımlı değil veya 64 haneli değil.")
    secret = bytes.fromhex(match.group(0))
    if ed25519.public_key(secret).hex() != DATA_PUBLIC_KEY_HEX:
        raise SystemExit("GIZA_VERI_ANAHTAR programdaki veri anahtarıyla eşleşmiyor.")
    return secret


def main() -> None:
    dry = "--kuru" in sys.argv
    secret = None if dry else read_secret()
    user, password = os.environ.get("EPIAS_KULLANICI", ""), os.environ.get("EPIAS_SIFRE", "")
    if not user or not password:
        raise SystemExit("EPIAS_KULLANICI / EPIAS_SIFRE secret'ları tanımlı değil.")
    client = Epias(user, password)
    now = datetime.now(TR)
    today = now.date()
    last_complete = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)

    print("Günlük PTF")
    for day in (today, today + timedelta(days=1)):
        try:
            if not dry and publish_day(client, day):
                print(f"  {day} eklendi")
        except Exception as exc:
            print(f"  {day}: {exc}")

    print("Aylık PTF")
    for year, month in months_between(FIRST_MONTH, last_complete):
        try:
            if not dry and publish_month(client, year, month):
                print(f"  {year}-{month:02d} eklendi")
        except Exception as exc:
            print(f"  {year}-{month:02d}: {exc}")

    print("Gerçekleşen YEKDEM")
    yekdem_path = VERI / "yekdem.json"
    table = json.loads(yekdem_path.read_text(encoding="utf-8")) if yekdem_path.exists() else {}
    recent = list(months_between(FIRST_MONTH, (today.year, today.month)))[-4:]
    for year, month in months_between(FIRST_MONTH, (today.year, today.month)):
        key = f"{year:04d}-{month:02d}"
        if key in table and (year, month) not in recent:
            continue
        try:
            value = fetch_yekdem(client, year, month)
        except Exception as exc:
            print(f"  {key}: {exc}")
            continue
        if value and table.get(key) != value:
            table[key] = value
            print(f"  {key}: {value['unit_cost']} (sürüm {value['version'] or '-'})")
    if not dry:
        write_json(yekdem_path, dict(sorted(table.items())))
    build_manifest(secret)


if __name__ == "__main__":
    main()
