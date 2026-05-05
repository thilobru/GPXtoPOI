# GPXtoPOI

A GPX route enrichment tool for bike touring. Upload a GPX file and get an interactive map with nearby amenities (fuel stations, supermarkets, convenience stores, McDonald's) along the route, including estimated arrival times and opening hours checks.

## Features

- Parses GPX tracks and queries the [Overpass API](https://overpass-api.de) for POIs within 1 km of the route
- Estimates arrival time at each POI based on average speed
- Checks opening hours against estimated arrival time (using OSM opening hours data)
- Displays hourly distance markers perpendicular to the route
- Configurable speed, start time, and POI filter options

## Usage

### Web App (Flask)

```bash
pip install -r requirements.txt
python app.py
```

Then open [http://localhost:5000](http://localhost:5000).

### Docker

```bash
docker build -t gpxtopoi .
docker run -p 5000:5000 gpxtopoi
```

Then open [http://localhost:5000](http://localhost:5000).

### Notebook

Open `test.ipynb` in Jupyter or VS Code. Run all cells. Edit the parameters at the top of the second cell:

| Parameter | Description |
|-----------|-------------|
| `average_speed_kmh` | Average cycling speed in km/h |
| `start_date` | Departure date (YYYY-MM-DD) |
| `start_time_str` | Departure time (HH:MM, local time) |
| `hide_closed_shops` | Hide POIs confirmed closed at arrival |
| `only_slower_window` | Arrival window only extends slower (−10%), not faster |
| `search_mcdonalds` | Include McDonald's in POI search |
| `gpx_file_path` | Path to your GPX file |

The notebook saves the result as `<gpx-filename>_routenkarte.html` in the working directory.

## Requirements

- Python 3.12+
- See `requirements.txt` for Flask app dependencies
- Notebook additionally requires `folium`

## Project Structure

```
app.py                  # Flask backend
templates/
  index.html            # Web UI
requirements.txt        # Flask app dependencies
Dockerfile              # Production container (Gunicorn on port 5000)
test.ipynb              # Standalone Jupyter notebook version
data/                   # GPX input files
```
