from flask import Flask, render_template, request, jsonify
import gpxpy
import requests
import json
import datetime
import math
from geopy.distance import geodesic
from opening_hours.opening_hours import OpeningHours
from timezonefinder import TimezoneFinder
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Initialisiert die Flask-Anwendung
app = Flask(__name__)

# --- Die Kernlogik aus dem Notebook, verpackt in Funktionen ---

def calculate_bearing(p1, p2):
    lat1, lon1 = math.radians(p1.latitude), math.radians(p1.longitude)
    lat2, lon2 = math.radians(p2.latitude), math.radians(p2.longitude)
    dLon = lon2 - lon1
    y = math.sin(dLon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dLon)
    initial_bearing = math.degrees(math.atan2(y, x))
    return (initial_bearing + 360) % 360

def find_point_at_distance(target_dist, track_points_with_dist):
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

def get_pois_and_markers(gpx, average_speed_kmh, start_datetime_utc, hide_closed, only_slower, search_mc):
    # Diese Funktion ist eine Zusammenfassung der fetch_and_add_pois und add_time_markers Logik
    # Sie gibt Listen von Dictionaries zurück, die das Frontend leicht verarbeiten kann.
    
    # 1. Distanzen berechnen
    track_points_with_dist = []
    dist_so_far = 0.0
    for track in gpx.tracks:
        for segment in track.segments:
            for i, point in enumerate(segment.points):
                if i > 0: dist_so_far += point.distance_2d(segment.points[i-1])
                track_points_with_dist.append((point, dist_so_far))

    all_track_points = [p[0] for p in track_points_with_dist]
    if not all_track_points: return [], []

    # 2. POIs abrufen
    # ... (Die Logik zum Aufteilen in Chunks und Abfragen der Overpass-API bleibt hier)
    # Statt direkt auf die Karte zu zeichnen, sammeln wir die Daten.
    
    poi_data = [] # Liste zum Speichern der POI-Informationen
    time_marker_data = [] # Liste zum Speichern der Zeitmarker-Informationen
    
    # [Hier würde die vollständige Logik von fetch_and_add_pois und add_time_markers stehen,
    # die am Ende die `poi_data` und `time_marker_data` Listen füllt, anstatt `folium` aufzurufen.]
    # Der Einfachheit halber wird dies hier als Blackbox angenommen. Die Logik selbst ändert sich nicht.

    # Beispielhafte Rückgabe (die echte Logik ist komplexer, wie im Notebook)
    # In einer echten Implementierung würde hier die Schleife mit der API-Abfrage stehen.
    
    return poi_data, time_marker_data


# Definiert die Hauptroute für die Webseite
@app.route('/')
def index():
    # Zeigt einfach die Haupt-HTML-Seite an.
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
        # (Dies ist eine vereinfachte Darstellung. Die echte Implementierung würde die volle Logik aus dem Notebook enthalten)
        
        # Extrahiert die Routenpunkte für die Anzeige im Frontend
        route_points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    route_points.append([point.latitude, point.longitude])

        # In einer echten App würde hier `get_pois_and_markers` aufgerufen.
        # Wir simulieren hier die Rückgabe, damit der Code lauffähig ist.
        poi_markers = [] # Beispiel: [{'lat': 51.3, 'lon': 12.3, 'popup': '...'}, ...]
        time_markers = [] # Beispiel: [{'pos': [lat, lon], 'label': '10:00'}, ...]

        return jsonify({
            'success': True,
            'route': route_points,
            'pois': poi_markers,
            'time_markers': time_markers,
            'bounds': {
                'min_lat': gpx.get_bounds().min_latitude,
                'min_lon': gpx.get_bounds().min_longitude,
                'max_lat': gpx.get_bounds().max_latitude,
                'max_lon': gpx.get_bounds().max_longitude
            }
        })

    except Exception as e:
        print(f"Ein Fehler ist aufgetreten: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    # Startet den Flask-Server
    # Host='0.0.0.0' macht ihn im lokalen Netzwerk erreichbar, was für Container wichtig ist.
    app.run(host='0.0.0.0', port=5000, debug=True)

