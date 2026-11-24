"""Server-side adapter for an explicitly configured OSRM-compatible router."""

import json
import math
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from django.conf import settings


class RoutingUnavailable(Exception):
    pass


class RoutingNotConfigured(RoutingUnavailable):
    pass


class RouteNotFound(Exception):
    pass


_demo_lock = threading.Lock()
_demo_last_request_at = 0.0
_DEMO_HOSTS = {"router.project-osrm.org", "routing.openstreetmap.de", "router.openstreetmap.de"}


def _endpoint():
    base_url = getattr(settings, "EYEVIORA_OSRM_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise RoutingNotConfigured("Chua cau hinh dich vu dinh tuyen OSRM.")
    parsed = urlsplit(base_url)
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in local_hosts):
        raise RoutingUnavailable("Dich vu dinh tuyen phai su dung HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RoutingUnavailable("Dia chi dich vu dinh tuyen khong hop le.")
    return base_url, parsed.hostname.lower()


def _respect_demo_rate_limit(host):
    """Keep development requests below the public OSRM demo's 1 req/s limit."""
    global _demo_last_request_at
    if host not in _DEMO_HOSTS:
        return
    with _demo_lock:
        wait_seconds = 1.05 - (time.monotonic() - _demo_last_request_at)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _demo_last_request_at = time.monotonic()


def _valid_coordinate_pair(pair):
    if not isinstance(pair, list) or len(pair) != 2:
        return False
    lon, lat = pair
    return (
        isinstance(lon, (int, float)) and not isinstance(lon, bool) and math.isfinite(lon) and -180 <= lon <= 180
        and isinstance(lat, (int, float)) and not isinstance(lat, bool) and math.isfinite(lat) and -90 <= lat <= 90
    )


def fetch_osrm_route(origin, destination, referer=None):
    """Return validated road-route data. User coordinates are never logged or persisted."""
    base_url, host = _endpoint()
    origin_lat, origin_lon = origin
    destination_lat, destination_lon = destination
    for value, minimum, maximum in (
        (origin_lat, -90, 90), (origin_lon, -180, 180),
        (destination_lat, -90, 90), (destination_lon, -180, 180),
    ):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise RoutingUnavailable("Toa do dinh tuyen khong hop le.")

    url = f"{base_url}/route/v1/driving"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "EyeVioraClinic/1.0 (clinic directions)",
    }
    if referer:
        referer_parts = urlsplit(referer)
        if referer_parts.scheme in {"http", "https"} and referer_parts.netloc and not referer_parts.username:
            headers["Referer"] = referer
    token = getattr(settings, "EYEVIORA_OSRM_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    _respect_demo_rate_limit(host)
    if host in _DEMO_HOSTS:
        # The hosted OSRM demo accepts the documented GET route form; keep the
        # user coordinates server-side instead of placing them in the page URL.
        route_coordinates = f"{origin_lon},{origin_lat};{destination_lon},{destination_lat}"
        query = urlencode({"overview": "full", "geometries": "geojson", "steps": "false"})
        request = Request(f"{url}/{route_coordinates}?{query}", headers=headers, method="GET")
    else:
        request_body = json.dumps({
            "coordinates": [[origin_lon, origin_lat], [destination_lon, destination_lat]],
            "overview": "full",
            "geometries": "geojson",
            "steps": False,
        }).encode("utf-8")
        request = Request(url, data=request_body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read(4_000_001))
    except HTTPError as exc:
        try:
            error_payload = json.loads(exc.read(64_001))
        except (OSError, json.JSONDecodeError):
            error_payload = {}
        if isinstance(error_payload, dict) and error_payload.get("code") in {"NoRoute", "NoSegment"}:
            raise RouteNotFound("Khong tim thay tuyen duong tren du lieu duong hien co.") from None
        raise RoutingUnavailable(f"Dich vu dinh tuyen tra ve HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise RoutingUnavailable("Khong ket noi duoc dich vu dinh tuyen.") from None

    if not isinstance(payload, dict):
        raise RoutingUnavailable("Dich vu dinh tuyen tra ve du lieu khong hop le.")
    if payload.get("code") == "NoRoute":
        raise RouteNotFound("Khong tim thay tuyen duong giua hai diem.")
    if payload.get("code") != "Ok":
        raise RoutingUnavailable("Dich vu dinh tuyen khong tra ve ket qua hop le.")
    routes = payload.get("routes")
    route = routes[0] if isinstance(routes, list) and routes else None
    geometry = route.get("geometry") if isinstance(route, dict) else None
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    distance = route.get("distance") if isinstance(route, dict) else None
    duration = route.get("duration") if isinstance(route, dict) else None
    if (
        not isinstance(geometry, dict) or geometry.get("type") != "LineString"
        or not isinstance(coordinates, list) or not 2 <= len(coordinates) <= 100_000
        or not all(_valid_coordinate_pair(pair) for pair in coordinates)
        or not isinstance(distance, (int, float)) or isinstance(distance, bool) or not math.isfinite(distance) or distance < 0
        or not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration < 0
    ):
        raise RoutingUnavailable("Dich vu dinh tuyen tra ve hinh hoc khong hop le.")

    return {
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "distance_meters": distance,
        "duration_seconds": duration,
    }
