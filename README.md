# Cardiac Arrhythmia — Methodology Diagram

This repository implements an ECG arrhythmia detection and reporting pipeline. The following Mermaid diagram describes the end-to-end methodology: data sources, preprocessing, model inference, persistence, reporting, and UI.

## Diagram (Mermaid source)

# Cardiac Arrhythmia — Project Overview

This repository implements an end-to-end ECG arrhythmia detection, classification, and reporting system built with Flask.

Key capabilities:

- Upload ECG CSVs, ECG images, or enter manual values.
- Extract features from CSVs and images (peak detection, intervals).
- Classify arrhythmia using a pickled ML model (`NOTEBOOK_FILES/model.pkl`).
- Store patients, uploads and reports in `hospital.db` (SQLite).
- Generate PDF reports (ReportLab) and optionally email them.
- A doctor-facing dashboard to preview, filter, and download reports.

## Quick start

Requirements:

- Python 3.8+ and the packages in `requirements.txt`
- (Optional) Node.js + `@mermaid-js/mermaid-cli` if you want to export the methodology diagram to SVG/PNG

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the app (development):

```powershell
# from project root
python app.py
# Cardiac Arrhythmia — Project Overview

This repository implements an end-to-end ECG arrhythmia detection, classification, and reporting system built with Flask.

Key capabilities:

- Upload ECG CSVs, ECG images, or enter manual values.
- Extract features from CSVs and images (peak detection, intervals).
- Classify arrhythmia using a pickled ML model (`NOTEBOOK_FILES/model.pkl`).
- Store patients, uploads and reports in `hospital.db` (SQLite).
- Generate PDF reports (ReportLab) and optionally email them.
- A doctor-facing dashboard to preview, filter, and download reports.

## Quick start

Requirements:

- Python 3.8+ and the packages in `requirements.txt`
- (Optional) Node.js + `@mermaid-js/mermaid-cli` if you want to export the methodology diagram to SVG/PNG

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the app (development):

```powershell
# from project root
python app.py
# or if you prefer flask run style
set FLASK_APP=app.py
set FLASK_ENV=development
flask run
```

Open http://127.0.0.1:5000/ in your browser.

Notes:

- `init_db()` in `app.py` will create `hospital.db` automatically on first run.
- Place or train a model at `NOTEBOOK_FILES/model.pkl` if you want model-based predictions.