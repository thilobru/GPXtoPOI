"""Flask backend for GPXtoPOI – processes GPX files and queries Overpass for POIs."""
# pylint: disable=import-error  # packages are installed in the runtime environment

import dataclasses
import datetime
import math
from zoneinfo import ZoneInfo

import gpxpy
import numpy as np
import requests
from flask import Flask, render_template, request, jsonify
from geopy.distance import geodesic
from opening_hours.opening_hours import OpeningHours
from timezonefinder import TimezoneFinder

app = Flask(__name__)

OVERPASS_HEADERS = {'User-Agent': 'GPXtoPOI/1.0 (personal bike touring tool)'}
TRACK_THINNING_M = 200
CHUNK_SIZE = 150


@dataclasses.dataclass
class ETAWindow:
    """Arrival time estimates at a POI (avg, earliest, latest)."""
    avg: datetime.datetime
    earliest: datetime.datetime
    latest: datetime.datetime


@dataclasses.dataclass
class PoiConfig:
    """Settings controlling POI search and display filtering."""
    hide_closed: bool
    only_slower: bool
    search_mc: bool


def calculate_bearing(p1, p2):
    """Compass bearing from p1 to p2, in degrees (0-360)."""
    lat1, lon1 = math.radians(p1.latitude), math.radians(p1.longitude)
    lat2, lon2 = math.radians(p2.latitude), math.radians(p2.longitude)
    d_lon = lon2 - lon1
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def find_point_at_distance(target_dist, track_points_with_dist):
    """Interpolate the lat/lon position and bearing at a given
    cumulative distance along the track."""
    if target_dist < 0 or not track_points_with_dist or target_dist > track_points_with_dist[-1][1]:
        return None
    for i in range(1, len(track_points_with_dist)):
        p1, d1 = track_points_with_dist[i - 1]
        p2, d2 = track_points_with_dist[i]
        if d1 <= target_dist <= d2:
            if d2 == d1:
                return (p1.latitude, p1.longitude), 0
            fraction = (target_dist - d1) / (d2 - d1)
            lat = p1.latitude + fraction * (p2.latitude - p1.latitude)
            lon = p1.longitude + fraction * (p2.longitude - p1.longitude)
            return (lat, lon), calculate_bearing(p1, p2)
    return None


def thin_track(track_points_with_dist, min_spacing_m):
    """Downsample track points to at most one per min_spacing_m metres."""
    if not track_points_with_dist:
        return track_points_with_dist
    thinned = [track_points_with_dist[0]]
    for pt, dist in track_points_with_dist[1:]:
        if dist - thinned[-1][1] >= min_spacing_m:
            thinned.append((pt, dist))
    if thinned[-1] is not track_points_with_dist[-1]:
        thinned.append(track_points_with_dist[-1])
    return thinned


def _build_cumulative_track(gpx):
    """Extract all track points with cumulative distance from a parsed GPX object."""
    track_points_with_dist = []
    dist_so_far = 0.0
    for track in gpx.tracks:
        for segment in track.segments:
            for i, point in enumerate(segment.points):
                if i > 0:
                    dist_so_far += point.distance_2d(segment.points[i - 1])
                track_points_with_dist.append((point, dist_so_far))
    return track_points_with_dist


def _build_time_markers(thinned, speed_ms, start_datetime_utc):
    """Return a list of hourly time marker dicts (line coords + label) for the frontend."""
    time_markers = []
    total_duration_s = thinned[-1][1] / speed_ms
    target_tz = ZoneInfo("Europe/Berlin")

    start_hour = start_datetime_utc.hour
    next_full_hour = (start_hour + 1) % 24
    current_utc = start_datetime_utc.replace(hour=next_full_hour, minute=0, second=0, microsecond=0)
    if next_full_hour <= start_hour:
        current_utc += datetime.timedelta(days=1)

    while (current_utc - start_datetime_utc).total_seconds() <= total_duration_s:
        elapsed = (current_utc - start_datetime_utc).total_seconds()
        result = find_point_at_distance(speed_ms * elapsed, thinned)
        if result:
            coords, bearing = result
            end1 = geodesic(kilometers=0.5).destination(coords, (bearing + 90) % 360)
            end2 = geodesic(kilometers=0.5).destination(coords, (bearing - 90 + 360) % 360)
            time_markers.append({
                "line_coords": [[end1.latitude, end1.longitude], [end2.latitude, end2.longitude]],
                "label_pos": [end1.latitude, end1.longitude],
                "label_text": current_utc.astimezone(target_tz).strftime('%H:%M'),
            })
        current_utc += datetime.timedelta(hours=1)

    return time_markers


def _classify_poi(tags):
    """Return (poi_type, icon_color, icon_symbol) for a set of OSM tags."""
    if tags.get('amenity') == 'fuel':
        return 'Tankstelle', 'red', 'tint'
    if tags.get('shop') == 'supermarket':
        return 'Supermarkt', 'blue', 'shopping-cart'
    if tags.get('shop') == 'convenience':
        return 'Kiosk', 'green', 'shopping-basket'
    if tags.get('amenity') == 'fast_food' and 'mcdonald' in tags.get('brand', '').lower():
        return "McDonald's", 'orange', 'cutlery'
    return 'Unbekannt', 'gray', 'question'


def _eta_window(distance_m, speed_ms, start_utc, only_slower):
    """Return an ETAWindow with avg/earliest/latest arrival times."""
    eta_avg = start_utc + datetime.timedelta(seconds=distance_m / speed_ms)
    eta_latest = start_utc + datetime.timedelta(seconds=distance_m / (speed_ms * 0.9))
    if only_slower:
        return ETAWindow(avg=eta_avg, earliest=eta_avg, latest=eta_latest)
    eta_earliest = start_utc + datetime.timedelta(seconds=distance_m / (speed_ms * 1.1))
    return ETAWindow(avg=eta_avg, earliest=eta_earliest, latest=eta_latest)


def _resolve_opening_status(opening_hours_str, lat, lon, eta_window, tf):
    """
    Parse opening hours and localize the ETA window.
    Returns (is_definitely_closed, status_text, localized_eta_window).
    Falls back to UTC times if timezone lookup fails.
    """
    if not opening_hours_str:
        return False, "<i>Öffnungszeiten unbekannt</i>", eta_window

    try:
        tz_str = tf.timezone_at(lng=lon, lat=lat)
        if not tz_str:
            return False, "<i>Zeitzone unbekannt</i>", eta_window

        local_tz = ZoneInfo(tz_str)
        localized = ETAWindow(
            avg=eta_window.avg.astimezone(local_tz),
            earliest=eta_window.earliest.astimezone(local_tz),
            latest=eta_window.latest.astimezone(local_tz),
        )
        is_open = OpeningHours(opening_hours_str).is_open(localized.avg)
        color = 'green' if is_open else 'red'
        word = 'Geöffnet' if is_open else 'Geschlossen'
        status = f"<strong style='color:{color};'>{word}</strong> bei Ankunft"
        return not is_open, status, localized

    except Exception:  # pylint: disable=broad-except
        return False, "<i>Fehler bei Auswertung der Öffnungszeiten</i>", eta_window


def _build_popup_html(poi_type, name, status_text, eta_window, opening_hours_str):
    """Build the HTML string for a POI popup."""
    window_str = (
        f"{eta_window.earliest.strftime('%H:%M')} – {eta_window.latest.strftime('%H:%M')}"
    )
    return (
        f"<b>{poi_type}: {name}</b><br>"
        f"<hr style='margin: 3px 0;'>"
        f"<b>Status:</b> {status_text}<br>"
        f"<b>Ankunft (ca.):</b> {eta_window.avg.strftime('%A, %H:%M')} Uhr<br>"
        f"<small><i>Fenster: {window_str}</i></small><br>"
        f"<small>Regel: {opening_hours_str or 'n/a'}</small>"
    )


def _fetch_poi_markers(thinned, speed_ms, start_utc, config):  # pylint: disable=too-many-locals
    """Query Overpass in chunks and return a list of POI marker dicts."""
    all_points = [p[0] for p in thinned]
    processed_ids = set()
    poi_markers = []
    tf = TimezoneFinder()

    # precompute arrays once for fast vectorised closest-point search
    track_lats = np.array([p.latitude for p in all_points])
    track_lons = np.array([p.longitude for p in all_points])
    cos_lat = np.cos(np.radians(np.mean(track_lats)))

    mc_clause = 'nwr.r["amenity"="fast_food"]["brand"~"McDonald\'s",i];' if config.search_mc else ""

    for i in range(0, len(all_points), CHUNK_SIZE):
        chunk = all_points[i:i + CHUNK_SIZE + 1]
        points_str = ",".join(str(c) for pt in chunk for c in (pt.latitude, pt.longitude))
        query = f"""
        [out:json][timeout:90];
        nwr(around:1000,{points_str}) -> .r;
        (
          nwr.r["amenity"="fuel"];
          nwr.r["shop"="supermarket"];
          nwr.r["shop"="convenience"];
          {mc_clause}
        );out center;
        """
        try:
            resp = requests.post(
                "https://overpass-api.de/api/interpreter",
                data={'data': query},
                headers=OVERPASS_HEADERS,
                timeout=120,
            )
            resp.raise_for_status()
            elements = resp.json().get('elements', [])
            print(f"Chunk {i // CHUNK_SIZE + 1}: {len(elements)} elements received.")
        except requests.RequestException as exc:
            print(f"Chunk {i // CHUNK_SIZE + 1} failed: {exc}")
            continue

        for element in elements:
            eid = element['id']
            if eid in processed_ids:
                continue
            processed_ids.add(eid)

            lat = element.get('lat') or element.get('center', {}).get('lat')
            lon = element.get('lon') or element.get('center', {}).get('lon')
            if not lat or not lon:
                continue

            # flat-earth approx -- accurate enough within a 1km search radius
            dlat = track_lats - lat
            dlon = (track_lons - lon) * cos_lat
            idx = int(np.argmin(dlat ** 2 + dlon ** 2))
            distance_m = thinned[idx][1]

            eta_w = _eta_window(distance_m, speed_ms, start_utc, config.only_slower)
            opening_hours_str = element.get('tags', {}).get('opening_hours')
            is_closed, status_text, eta_wl = _resolve_opening_status(
                opening_hours_str, lat, lon, eta_w, tf
            )

            # only skip if we actually know it's closed; no hours = always show
            if config.hide_closed and is_closed:
                continue

            tags = element.get('tags', {})
            poi_type, icon_color, icon_symbol = _classify_poi(tags)
            popup = _build_popup_html(
                poi_type, tags.get('name', 'Unbenannt'),
                status_text, eta_wl, opening_hours_str
            )
            poi_markers.append({
                "lat": lat, "lon": lon, "popup": popup,
                "icon": {"color": icon_color, "symbol": icon_symbol},
            })

    print(f"Total POIs found: {len(poi_markers)}")
    return poi_markers


def process_gpx_data(gpx, average_speed_kmh, start_datetime_utc, config):
    """Run all calculations and return (poi_markers, time_markers) for the frontend."""
    track_points_with_dist = _build_cumulative_track(gpx)
    if not track_points_with_dist:
        return [], []

    thinned = thin_track(track_points_with_dist, TRACK_THINNING_M)
    speed_ms = (average_speed_kmh * 1000) / 3600
    if speed_ms == 0:
        return [], []

    time_markers = _build_time_markers(thinned, speed_ms, start_datetime_utc)
    poi_markers = _fetch_poi_markers(thinned, speed_ms, start_datetime_utc, config)
    return poi_markers, time_markers


@app.route('/')
def index():
    """Serve the main page."""
    return render_template('index.html')


@app.route('/generate-map', methods=['POST'])
def generate_map():
    """Accept a GPX file upload and return route, POIs, and time markers as JSON."""
    try:
        gpx_file = request.files['gpxFile']
        average_speed_kmh = float(request.form['averageSpeed'])
        start_date_str = request.form['startDate']
        start_time_str = request.form['startTime']
        poi_config = PoiConfig(
            hide_closed=request.form.get('hideClosed') == 'true',
            only_slower=request.form.get('onlySlower') == 'true',
            search_mc=request.form.get('searchMc') == 'true',
        )

        gpx = gpxpy.parse(gpx_file.read().decode('utf-8'))
        dt_fmt = "%Y-%m-%d %H:%M"
        naive_dt = datetime.datetime.strptime(f"{start_date_str} {start_time_str}", dt_fmt)
        start_utc = naive_dt.astimezone().astimezone(datetime.timezone.utc)

        poi_markers, time_markers = process_gpx_data(
            gpx, average_speed_kmh, start_utc, poi_config
        )

        route_points = [
            [pt.latitude, pt.longitude]
            for track in gpx.tracks
            for segment in track.segments
            for pt in segment.points
        ]
        bounds = gpx.get_bounds()
        return jsonify({
            'success': True,
            'route': route_points,
            'pois': poi_markers,
            'time_markers': time_markers,
            'bounds': {
                'min_lat': bounds.min_latitude,
                'min_lon': bounds.min_longitude,
                'max_lat': bounds.max_latitude,
                'max_lon': bounds.max_longitude,
            } if bounds else None,
        })

    except (ValueError, KeyError) as exc:
        print(f"Bad request: {exc}")
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Unexpected error: {exc}")
        return jsonify({'success': False, 'error': str(exc)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
