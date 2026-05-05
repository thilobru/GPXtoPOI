from flask import Flask, render_template, request, jsonify
import gpxpy
import requests
import json
import datetime
import math
import os
from geopy.distance import geodesic
from opening_hours.opening_hours import OpeningHours
from timezonefinder import TimezoneFinder
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Initialisiert die Flask-Anwendung
app = Flask(__name__)

# --- Die Kernlogik aus dem Notebook, verpackt in Funktionen ---

def calculate_bearing(p1, p2):
    """Berechnet die Peilung (Kurs) zwischen zwei GPX-Punkten."""
    lat1, lon1 = math.radians(p1.latitude), math.radians(p1.longitude)
    lat2, lon2 = math.radians(p2.latitude), math.radians(p2.longitude)
    dLon = lon2 - lon1
    y = math.sin(dLon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dLon)
    initial_bearing = math.degrees(math.atan2(y, x))
    return (initial_bearing + 360) % 360

def find_point_at_distance(target_dist, track_points_with_dist):
    """Findet durch Interpolation den genauen Lat/Lon-Punkt und die Peilung für eine gegebene Distanz."""
    if target_dist < 0 or not track_points_with_dist or target_dist > track_points_with_dist[-1][1]:
        return None
    for i in range(1, len(track_points_with_dist)):
        p1, d1 = track_points_with_dist[i-1]
        p2, d2 = track_points_with_dist[i]
        if d1 <= target_dist <= d2:
            if (d2 - d1) == 0: return (p1.latitude, p1.longitude), 0
            fraction = (target_dist - d1) / (d2 - d1)
            lat = p1.latitude + fraction * (p2.latitude - p1.latitude)
            lon = p1.longitude + fraction * (p2.longitude - p1.longitude)
            bearing = calculate_bearing(p1, p2)
            return (lat, lon), bearing
    return None

def find_closest_point_on_track(poi_coords, track_points_with_dist):
    """Findet den nächstgelegenen Punkt auf der Route zu einem gegebenen POI."""
    min_dist = float('inf')
    closest_point_info = None
    for point, distance_from_start in track_points_with_dist:
        dist = geodesic(poi_coords, (point.latitude, point.longitude)).meters
        if dist < min_dist:
            min_dist = dist
            closest_point_info = (point, distance_from_start)
    return closest_point_info

def process_gpx_data(gpx, average_speed_kmh, start_datetime_utc, hide_closed, only_slower, search_mc):
    """Die Hauptfunktion, die alle Berechnungen durchführt und Daten für das Frontend vorbereitet."""
    
    # 1. Distanzen für jeden Punkt der Route berechnen
    track_points_with_dist = []
    dist_so_far = 0.0
    for track in gpx.tracks:
        for segment in track.segments:
            for i, point in enumerate(segment.points):
                if i > 0: dist_so_far += point.distance_2d(segment.points[i-1])
                track_points_with_dist.append((point, dist_so_far))
    
    all_track_points = [p[0] for p in track_points_with_dist]
    if not all_track_points: return [], [], []

    # 2. Zeitmarker (Stunden) berechnen
    time_markers = []
    total_distance_m = track_points_with_dist[-1][1]
    speed_ms = (average_speed_kmh * 1000) / 3600
    if speed_ms > 0:
        total_duration_s = total_distance_m / speed_ms
        start_hour = start_datetime_utc.hour
        next_full_hour = (start_hour + 1) % 24
        current_marker_time_utc = start_datetime_utc.replace(hour=next_full_hour, minute=0, second=0, microsecond=0)
        if next_full_hour <= start_hour:
            current_marker_time_utc += datetime.timedelta(days=1)

        target_tz = ZoneInfo("Europe/Berlin")
        while (current_marker_time_utc - start_datetime_utc).total_seconds() <= total_duration_s:
            elapsed_seconds = (current_marker_time_utc - start_datetime_utc).total_seconds()
            distance_marker = speed_ms * elapsed_seconds
            result = find_point_at_distance(distance_marker, track_points_with_dist)
            if result:
                point_coords, bearing = result
                perp_bearing1 = (bearing + 90) % 360
                perp_bearing2 = (bearing - 90 + 360) % 360
                line_end1 = geodesic(kilometers=0.5).destination(point_coords, perp_bearing1)
                line_end2 = geodesic(kilometers=0.5).destination(point_coords, perp_bearing2)
                display_time = current_marker_time_utc.astimezone(target_tz)
                time_markers.append({
                    "line_coords": [[line_end1.latitude, line_end1.longitude], [line_end2.latitude, line_end2.longitude]],
                    "label_pos": [line_end1.latitude, line_end1.longitude],
                    "label_text": display_time.strftime('%H:%M')
                })
            current_marker_time_utc += datetime.timedelta(hours=1)

    # 3. POIs abrufen und verarbeiten
    poi_markers = []
    CHUNK_SIZE = 150
    processed_poi_ids = set()
    tf = TimezoneFinder()
    num_chunks = (len(all_track_points) + CHUNK_SIZE - 1) // CHUNK_SIZE

    for i in range(0, len(all_track_points), CHUNK_SIZE):
        chunk = all_track_points[i:i + CHUNK_SIZE + 1]
        flat_coords = [str(coord) for point in chunk for coord in (point.latitude, point.longitude)]
        points_str = ",".join(flat_coords)
        
        mcdonalds_query_part = ""
        if search_mc:
            mcdonalds_query_part = f'nwr(around:1000,{points_str})["amenity"="fast_food"]["brand"~"McDonald\'s",i];'

        overpass_query = f"""
        [out:json][timeout:90];(
          nwr(around:1000,{points_str})["amenity"="fuel"];
          nwr(around:1000,{points_str})["shop"="supermarket"];
          nwr(around:1000,{points_str})["shop"="convenience"];
          {mcdonalds_query_part}
        );out center;
        """
        try:
            response = requests.post("https://overpass-api.de/api/interpreter", data={'data': overpass_query})
            response.raise_for_status()
            data = response.json()
            print(f"Abschnitt {i // CHUNK_SIZE + 1}: {len(data['elements'])} POIs von Overpass erhalten.")
            for element in data['elements']:
                element_id = element['id']
                if element_id in processed_poi_ids: continue
                processed_poi_ids.add(element_id)

                lat = element.get('lat') or element.get('center', {}).get('lat')
                lon = element.get('lon') or element.get('center', {}).get('lon')
                if not lat or not lon: continue

                closest_point_tuple = find_closest_point_on_track((lat, lon), track_points_with_dist)
                if not closest_point_tuple: continue
                
                distance_to_poi_m = closest_point_tuple[1]
                
                travel_time_avg_s = distance_to_poi_m / speed_ms if speed_ms > 0 else 0
                eta_avg_utc = start_datetime_utc + datetime.timedelta(seconds=travel_time_avg_s)

                if only_slower:
                    speed_slower_ms = speed_ms * 0.9
                    travel_time_slower_s = distance_to_poi_m / speed_slower_ms if speed_slower_ms > 0 else 0
                    eta_earliest_utc = eta_avg_utc
                    eta_latest_utc = start_datetime_utc + datetime.timedelta(seconds=travel_time_slower_s)
                else: 
                    speed_faster_ms = speed_ms * 1.1
                    speed_slower_ms = speed_ms * 0.9
                    travel_time_faster_s = distance_to_poi_m / speed_faster_ms if speed_faster_ms > 0 else 0
                    travel_time_slower_s = distance_to_poi_m / speed_slower_ms if speed_slower_ms > 0 else 0
                    eta_earliest_utc = start_datetime_utc + datetime.timedelta(seconds=travel_time_faster_s)
                    eta_latest_utc = start_datetime_utc + datetime.timedelta(seconds=travel_time_slower_s)

                opening_hours_str = element.get('tags', {}).get('opening_hours')
                # FIX: Zwei separate Flags für "ist geschlossen" und "Öffnungszeiten bekannt"
                is_definitely_closed = False
                opening_hours_known = False
                status_text = "<i>Öffnungszeiten unbekannt</i>"
                eta_avg_local, eta_earliest_local, eta_latest_local = eta_avg_utc, eta_earliest_utc, eta_latest_utc

                if opening_hours_str:
                    try:
                        tz_str = tf.timezone_at(lng=lon, lat=lat)
                        if tz_str:
                           local_tz = ZoneInfo(tz_str)
                           eta_avg_local = eta_avg_utc.astimezone(local_tz)
                           eta_earliest_local = eta_earliest_utc.astimezone(local_tz)
                           eta_latest_local = eta_latest_utc.astimezone(local_tz)
                           
                           opening_hours_known = True # Wir haben eine Regel gefunden
                           is_open = OpeningHours(opening_hours_str).is_open(eta_avg_local)
                           is_definitely_closed = not is_open

                           status_color = 'green' if is_open else 'red'
                           status_word = 'Geöffnet' if is_open else 'Geschlossen'
                           status_text = f"<strong style='color:{status_color};'>{status_word}</strong> bei Ankunft"
                    except Exception:
                        status_text = "<i>Fehler bei Auswertung der Öffnungszeiten</i>"
                
                # FIX: Filterlogik korrigiert
                # Blendet nur aus, wenn die Option aktiv ist UND wir wissen, dass der Shop geschlossen ist.
                if hide_closed and is_definitely_closed:
                    continue

                # --- Marker-Daten für Frontend vorbereiten ---
                tags = element.get('tags', {})
                poi_type, icon_color, icon_symbol = "Unbekannt", "gray", "question"
                if tags.get('amenity') == 'fuel': poi_type, icon_color, icon_symbol = 'Tankstelle', 'red', 'tint'
                elif tags.get('shop') == 'supermarket': poi_type, icon_color, icon_symbol = 'Supermarkt', 'blue', 'shopping-cart'
                elif tags.get('shop') == 'convenience': poi_type, icon_color, icon_symbol = 'Kiosk', 'green', 'shopping-basket'
                elif tags.get('amenity') == 'fast_food' and 'mcdonald' in tags.get('brand','').lower():
                    poi_type, icon_color, icon_symbol = "McDonald's", 'orange', 'cutlery'
                
                popup_html = f"""<b>{poi_type}: {tags.get('name', 'Unbenannt')}</b><br><hr style='margin: 3px 0;'><b>Status:</b> {status_text}<br><b>Ankunft (ca.):</b> {eta_avg_local.strftime('%A, %H:%M')} Uhr<br><small><i>Fenster: {eta_earliest_local.strftime('%H:%M')} - {eta_latest_local.strftime('%H:%M')}</i></small><br><small>Regel: {opening_hours_str or 'n/a'}</small>"""
                
                poi_markers.append({
                    "lat": lat, "lon": lon, "popup": popup_html,
                    "icon": {"color": icon_color, "symbol": icon_symbol}
                })
        except Exception as e:
            print(f"Fehler bei Overpass-Abfrage für Abschnitt {i // CHUNK_SIZE + 1}: {e}")
    print(f"Insgesamt {len(poi_markers)} POIs werden an das Frontend gesendet.")
    return poi_markers, time_markers


# Definiert die Hauptroute für die Webseite
@app.route('/')
def index():
    return render_template('index.html')

# Definiert die API-Route, die die GPX-Daten verarbeitet
@app.route('/generate-map', methods=['POST'])
def generate_map():
    try:
        # Empfängt die Daten vom Frontend
        gpx_file = request.files['gpxFile']
        average_speed_kmh = float(request.form['averageSpeed'])
        start_date_str = request.form['startDate']
        start_time_str = request.form['startTime']
        hide_closed = request.form.get('hideClosed') == 'true'
        only_slower = request.form.get('onlySlower') == 'true'
        search_mc = request.form.get('searchMc') == 'true'

        # Liest und parst die GPX-Datei
        gpx_content = gpx_file.read().decode('utf-8')
        gpx = gpxpy.parse(gpx_content)

        # Bereitet die Startzeit vor
        naive_datetime = datetime.datetime.strptime(f"{start_date_str} {start_time_str}", "%Y-%m-%d %H:%M")
        start_datetime_local = naive_datetime.astimezone()
        start_datetime_utc = start_datetime_local.astimezone(datetime.timezone.utc)
        
        # Ruft die Kernlogik auf
        poi_markers, time_markers = process_gpx_data(gpx, average_speed_kmh, start_datetime_utc, hide_closed, only_slower, search_mc)
        
        # Extrahiert die Routenpunkte für die Anzeige im Frontend
        route_points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    route_points.append([point.latitude, point.longitude])
        
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
                'max_lon': bounds.max_longitude
            } if bounds else None
        })

    except Exception as e:
        print(f"Ein Fehler ist aufgetreten: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    # Startet den Flask-Server
    app.run(host='0.0.0.0', port=5000)

