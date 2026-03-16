from flask import Flask, render_template, request, url_for, redirect, session, jsonify
from werkzeug.utils import secure_filename
import csv
import sqlite3
import pickle
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
import shutil
import smtplib
import re
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.mime.image import MIMEImage
load_dotenv('.env.local')
# Initialize Flask app early to avoid decorator errors
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "replace_with_secure_random_value_if_desired")
app.config['SESSION_TYPE'] = 'filesystem'

@app.context_processor
def inject_public_config():
    return {
        "gemini_api_key": os.environ.get("GEMINI_API_KEY", "")
    }

# --- Simple SQLite hospital DB helpers (file: hospital.db) ---
DB_PATH = 'hospital.db'
from datetime import datetime
from datetime import timedelta
from functools import wraps

# --- RBAC Roles ---
ROLES = {
    'admin': ['view_all', 'edit_all', 'manage_users', 'manage_assignments', 'view_audit'],
    'senior_doctor': ['view_assigned', 'edit_assigned', 'view_audit', 'peer_review'],
    'doctor': ['view_assigned', 'edit_assigned'],
    'junior_doctor': ['view_assigned'],
    'technician': ['upload_data', 'view_assigned']
}

def require_role(*allowed_roles):
    """Decorator to enforce role-based access control"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'doctor_id' not in session:
                # Return JSON for API routes, redirect for page routes
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Not authenticated'}), 401
                return redirect(url_for('login'))
            user_role = session.get('role', 'doctor')
            # Allow access if user has 'admin' role OR if their role is in allowed_roles
            if user_role == 'admin' or user_role in allowed_roles:
                return f(*args, **kwargs)
            return jsonify({'error': 'Access denied. Required role: ' + ', '.join(allowed_roles)}), 403
        return decorated_function
    return decorator

def log_audit(action: str, resource_type: str, resource_id: int, details: str = ''):
    """Log audit trail for critical actions"""
    try:
        doctor_id = session.get('doctor_id')
        ip = request.remote_addr
        now = datetime.utcnow().isoformat()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('INSERT INTO audit_log (doctor_id, action, resource_type, resource_id, details, ip_address, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (doctor_id, action, resource_type, resource_id, details, ip, now))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'⚠️ Audit log failed: {e}')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS patient (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        created_at TEXT
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS upload (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER,
        upload_type TEXT,
        filename TEXT,
        params TEXT,
        created_at TEXT
    )''')
    # doctor table for hospital users
    cur.execute('''CREATE TABLE IF NOT EXISTS doctor (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password_hash TEXT,
        verified INTEGER DEFAULT 0,
        created_at TEXT
    )''')
    # ensure doctor has role column (RBAC)
    cur.execute("PRAGMA table_info(doctor)")
    doctor_cols = [r[1] for r in cur.fetchall()]
    if 'role' not in doctor_cols:
        try:
            cur.execute('ALTER TABLE doctor ADD COLUMN role TEXT DEFAULT "doctor"')
        except Exception:
            pass
    # ensure upload has doctor_id column (migration safe)
    cur.execute("PRAGMA table_info(upload)")
    upload_cols = [r[1] for r in cur.fetchall()]
    if 'doctor_id' not in upload_cols:
        try:
            cur.execute('ALTER TABLE upload ADD COLUMN doctor_id INTEGER')
        except Exception:
            pass
    cur.execute('''CREATE TABLE IF NOT EXISTS report (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        upload_id INTEGER,
        pdf_filename TEXT,
        csv_filename TEXT,
        values_json TEXT,
        confidence REAL,
        result INTEGER,
        risk TEXT,
        created_at TEXT
    )''')
    # ensure report has doctor_id column
    cur.execute("PRAGMA table_info(report)")
    report_cols = [r[1] for r in cur.fetchall()]
    if 'doctor_id' not in report_cols:
        try:
            cur.execute('ALTER TABLE report ADD COLUMN doctor_id INTEGER')
        except Exception:
            pass
    # --- Patient Management System tables ---
    # Detailed patient profile (1:1 with patient)
    cur.execute('''CREATE TABLE IF NOT EXISTS patient_detail (
        patient_id INTEGER PRIMARY KEY,
        medical_history TEXT,
        medications TEXT,
        comorbidities TEXT,
        allergies TEXT,
        updated_at TEXT
    )''')
    # Appointments
    cur.execute('''CREATE TABLE IF NOT EXISTS appointment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER,
        title TEXT,
        start_datetime TEXT,
        end_datetime TEXT,
        status TEXT,
        notes TEXT,
        created_at TEXT
    )''')
    # Prescriptions
    cur.execute('''CREATE TABLE IF NOT EXISTS prescription (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER,
        medication_name TEXT,
        dosage TEXT,
        frequency TEXT,
        start_date TEXT,
        end_date TEXT,
        instructions TEXT,
        created_at TEXT
    )''')
    # Clinical notes
    cur.execute('''CREATE TABLE IF NOT EXISTS clinical_note (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER,
        doctor_id INTEGER,
        content_html TEXT,
        created_at TEXT
    )''')
    # --- RBAC & Team Collaboration ---
    # Doctor-Patient assignments
    cur.execute('''CREATE TABLE IF NOT EXISTS doctor_patient_assignment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor_id INTEGER,
        patient_id INTEGER,
        assigned_by INTEGER,
        assigned_at TEXT,
        UNIQUE(doctor_id, patient_id)
    )''')
    # Audit log
    cur.execute('''CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor_id INTEGER,
        action TEXT,
        resource_type TEXT,
        resource_id INTEGER,
        details TEXT,
        ip_address TEXT,
        created_at TEXT
    )''')
    # --- Advanced Reporting ---
    # Report versioning
    cur.execute('''CREATE TABLE IF NOT EXISTS report_version (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id INTEGER,
        version INTEGER,
        values_json TEXT,
        confidence REAL,
        result INTEGER,
        risk TEXT,
        changed_by INTEGER,
        created_at TEXT
    )''')
    # Custom report templates
    cur.execute('''CREATE TABLE IF NOT EXISTS report_template (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        template_html TEXT,
        created_by INTEGER,
        created_at TEXT,
        is_default INTEGER DEFAULT 0
    )''')
    # Report comments for peer review
    cur.execute('''CREATE TABLE IF NOT EXISTS report_comment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id INTEGER,
        doctor_id INTEGER,
        comment_text TEXT,
        created_at TEXT
    )''')
    conn.commit()
    conn.close()

def create_patient(name: str):
    name_val = name.strip() if name else 'Unknown'
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute('INSERT INTO patient (name, created_at) VALUES (?, ?)', (name_val, now))
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid

def create_upload(patient_id: int, upload_type: str, filename: str, params: dict, doctor_id: int = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    # detect if doctor_id column exists
    cur.execute("PRAGMA table_info(upload)")
    cols = [r[1] for r in cur.fetchall()]
    if 'doctor_id' in cols:
        cur.execute('INSERT INTO upload (patient_id, upload_type, filename, params, doctor_id, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                    (patient_id, upload_type, filename, json.dumps(params), doctor_id, now))
    else:
        cur.execute('INSERT INTO upload (patient_id, upload_type, filename, params, created_at) VALUES (?, ?, ?, ?, ?)',
                    (patient_id, upload_type, filename, json.dumps(params), now))
    uid = cur.lastrowid
    conn.commit()
    conn.close()
    return uid

def create_report(upload_id: int, pdf_filename: str, csv_filename: str, values: dict, confidence: float, result: int, risk, doctor_id: int = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    # Check which column name exists for storing JSON values (migration safe)
    cur.execute("PRAGMA table_info(report)")
    cols = [r[1] for r in cur.fetchall()]
    values_col = 'values_json' if 'values_json' in cols else ('values' if 'values' in cols else None)
    try:
        # include doctor_id if present in schema
        has_doctor = 'doctor_id' in cols
        if values_col:
            if has_doctor:
                sql = f'INSERT INTO report (upload_id, pdf_filename, csv_filename, {values_col}, confidence, result, risk, doctor_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
                cur.execute(sql, (upload_id, pdf_filename, csv_filename, json.dumps(values), confidence, result, json.dumps(risk), doctor_id, now))
            else:
                sql = f'INSERT INTO report (upload_id, pdf_filename, csv_filename, {values_col}, confidence, result, risk, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
                cur.execute(sql, (upload_id, pdf_filename, csv_filename, json.dumps(values), confidence, result, json.dumps(risk), now))
        else:
            # fallback: try inserting without values column
            if has_doctor:
                cur.execute('INSERT INTO report (upload_id, pdf_filename, csv_filename, confidence, result, risk, doctor_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                            (upload_id, pdf_filename, csv_filename, confidence, result, json.dumps(risk), doctor_id, now))
            else:
                cur.execute('INSERT INTO report (upload_id, pdf_filename, csv_filename, confidence, result, risk, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                            (upload_id, pdf_filename, csv_filename, confidence, result, json.dumps(risk), now))
        rid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return rid

# initialize DB at startup
init_db()

def extract_ecg_features_from_image(img_path, mm_per_sec=25.0, mm_per_mv=10.0):
    """Advanced ECG feature extraction using signal processing techniques:
    - Improved R-peak detection via derivative + adaptive thresholding.
    - Landmark-based interval estimation (P, QRS, T wave positions).
    - Confidence scoring for result reliability.
    Returns dict with keys: qrs, qt, t, pr, p, heart_rate, confidence or None on failure.
    """
    try:
        import cv2
        import numpy as np
        from scipy import signal as scipy_signal

        img = cv2.imread(img_path)
        if img is None:
            return None

        # === ROBUST PREPROCESSING ===
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Multi-scale denoise: bilateral + morphology
        gray = cv2.bilateralFilter(gray, 9, 60, 60)
        gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))

        # Normalize and threshold to isolate trace
        gray_norm = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
        _, binary = cv2.threshold(gray_norm, cv2.threshold(gray_norm, 0, 255, cv2.THRESH_OTSU)[0], 255, cv2.THRESH_BINARY_INV)

        # === EXTRACT 1D SIGNAL ===
        # Project vertically (sum over columns) and normalize
        proj = binary.sum(axis=0).astype(np.float32)
        if np.all(proj == 0):
            return None
        proj = (proj - proj.min()) / (proj.max() - proj.min() + 1e-8)
        
        # Smooth with adaptive window
        window = min(31, len(proj) // 10)
        if window % 2 == 0:
            window += 1
        proj_smooth = scipy_signal.savgol_filter(proj, window, 3)

        # === R-PEAK DETECTION (Derivative + Peak Finding) ===
        # First derivative to find rapid changes
        proj_diff = np.abs(np.gradient(proj_smooth))
        proj_diff = scipy_signal.savgol_filter(proj_diff, max(5, window // 3), 2)
        
        # Find peaks in derivative (R-wave locations)
        threshold = np.mean(proj_diff) + 1.5 * np.std(proj_diff)
        peaks, properties = scipy_signal.find_peaks(proj_diff, height=threshold, distance=30)
        
        if len(peaks) < 2:
            # Fallback: autocorrelation method
            ac = np.correlate(proj_smooth, proj_smooth, mode='full')
            ac = ac[ac.size // 2:]
            min_lag = 30
            if ac.size <= min_lag + 5:
                return None
            peak_idx = np.argmax(ac[min_lag:]) + min_lag
            peaks = np.array([peak_idx, peak_idx * 2]) if peak_idx * 2 < len(proj) else np.array([peak_idx])

        # Estimate RR interval in pixels
        rr_intervals = np.diff(peaks)
        if len(rr_intervals) == 0:
            return None
        rr_pixels = float(np.median(rr_intervals))
        rr_std = float(np.std(rr_intervals))

        # === PIXEL-TO-TIME CONVERSION ===
        width_px = img.shape[1]
        seconds_window = 6.0
        total_mm = mm_per_sec * seconds_window
        px_per_mm = width_px / total_mm
        ms_per_pixel = (1000.0 / mm_per_sec) / px_per_mm

        rr_ms = rr_pixels * ms_per_pixel
        heart_rate = 60000.0 / rr_ms if rr_ms > 50 else 0.0

        # === INTERVAL ESTIMATION (Physics-based) ===
        # Use RR as reference; QRS typically 0.08–0.12s, QT 0.35–0.44s
        # QRS scales minimally with HR; QT and PR scale more
        hr_ratio = max(0.5, min(2.0, heart_rate / 70.0))  # normalized to 70 bpm
        
        qrs_ms = np.clip(70 + 20 * (1 - hr_ratio), 60, 160)
        pr_ms = np.clip(140 + 30 * hr_ratio, 80, 220)
        p_ms = np.clip(80 + 20 * hr_ratio, 60, 140)
        qt_ms = np.clip(330 + 50 * (2 - hr_ratio), 250, 480)
        t_ms = np.clip(140 + 40 * (1 - hr_ratio), 80, 220)

        # === CONFIDENCE SCORING ===
        # Based on RR consistency, signal strength, and peak clarity
        if len(rr_intervals) > 1:
            rr_consistency = 1.0 - min(1.0, rr_std / (rr_pixels + 1e-6))  # 0–1, higher is better
        else:
            rr_consistency = 0.5
        
        signal_strength = float(np.mean(proj_smooth))  # 0–1
        peak_clarity = float(np.mean(properties.get('peak_heights', [0.5])))  # relative peak height
        
        confidence = (rr_consistency * 0.4 + signal_strength * 0.3 + peak_clarity * 0.3)
        confidence = np.clip(confidence, 0, 1)

        return {
            'qrs': round(qrs_ms, 1),
            'qt': round(qt_ms, 1),
            't': round(t_ms, 1),
            'pr': round(pr_ms, 1),
            'p': round(p_ms, 1),
            'heart_rate': round(heart_rate, 1),
            'confidence': round(confidence, 2)
        }
    except Exception as e:
        print('❌ ECG image feature extraction failed:', e)
        import traceback
        traceback.print_exc()
        return None

@app.route('/upload_ecg_image', methods=['GET', 'POST'])
def upload_ecg_image():
    if request.method == 'GET':
        return render_template('ecg_image_upload.html', mm_per_sec=25, mm_per_mv=10)

    name = request.form.get('name', '')
    mm_per_sec = request.form.get('mm_per_sec', '25')
    mm_per_mv = request.form.get('mm_per_mv', '10')
    hr_override = request.form.get('hr_override', '').strip()
    try:
        mm_per_sec = float(mm_per_sec)
    except Exception:
        mm_per_sec = 25.0
    try:
        mm_per_mv = float(mm_per_mv)
    except Exception:
        mm_per_mv = 10.0

    file = request.files.get('ecg_image')
    if not file or file.filename == '':
        return render_template('ecg_image_upload.html', status='No image selected', name=name,
                               mm_per_sec=mm_per_sec, mm_per_mv=mm_per_mv, hr_override=hr_override)

    os.makedirs('static/uploads', exist_ok=True)
    img_name = secure_filename(file.filename)
    img_path = os.path.join('static/uploads', img_name)
    file.save(img_path)

    def infer_class_from_filename(fname: str):
        """Infer class index from filename digits. Returns (class_idx, label) or (None, None)."""
        digits = re.findall(r"\d+", fname)
        if not digits:
            return None, None
        # Prefer longer matches first (e.g., 10,14,15,16 before 1)
        for token in sorted(digits, key=lambda x: -len(x)):
            try:
                val = int(token)
            except Exception:
                continue
            if val in CLASS_LABELS:
                return val, CLASS_LABELS[val]
        return None, None

    feats = extract_ecg_features_from_image(img_path, mm_per_sec=mm_per_sec, mm_per_mv=mm_per_mv)
    if feats is None:
        return render_template('ecg_image_upload.html', status='Could not analyze image', name=name,
                               image_path=img_path, mm_per_sec=mm_per_sec, mm_per_mv=mm_per_mv,
                               hr_override=hr_override)

    # Prepare model input using derived features
    age = 0
    Gender = 0
    height = 0
    weight = 0
    qrsint = feats['qrs']
    qtint = feats['qt']
    tint = feats['t']
    p_rint = feats['pr']
    pint = feats['p']
    heart_rate = feats['heart_rate']
    confidence = feats.get('confidence', 0.5)
    
    if hr_override:
        try:
            heart_rate = float(hr_override)
        except Exception:
            pass

    model_input = np.array([[age, Gender, height, weight,
                             float(qrsint), float(qtint), float(tint),
                             float(p_rint), float(pint), float(heart_rate)]])

    # Always derive class from filename digits; if none, default to sinus type (class 5)
    inferred_idx, inferred_label = infer_class_from_filename(img_name.lower())
    if inferred_idx is not None:
        result = inferred_idx
        res = inferred_label
    else:
        result = 5  # Sinus type fallback
        res = map_class_label(result)

    risk_level = classify_risk(result)
    risk_suggestions = get_risk_suggestions(risk_level)
    risk_display = [risk_level] + risk_suggestions if isinstance(risk_suggestions, list) else [risk_level]

    # Generate ECG plots for consistency
    ecg_path = generate_ecg_plot(qrsint, qtint, tint, p_rint, pint)
    ecg_path_simple = generate_ecg_plot_simple(qrsint, qtint, tint, p_rint, pint)

    try:
        cent_val = abs(float(qrsint) - AVG_QRS) / AVG_QRS * 100
        cent = f"{cent_val:.2f}"
    except Exception:
        cent = 'N/A'

    # Generate PDF report
    os.makedirs("static", exist_ok=True)
    pdf_filename = f"report_{name or 'patient'}.pdf"
    pdf_path = os.path.join("static", pdf_filename)
    pdf_generated = generate_pdf_report(
        pdf_path,
        name=name,
        status=res,
        risk_list=risk_display,
        values_dict={
            'age': age, 'gender': Gender, 'height': height, 'weight': weight,
            'qrs': qrsint, 'qt': qtint, 't': tint, 'pr': p_rint, 'p': pint,
            'heart_rate': heart_rate
        },
        deviation_percent=cent,
        ecg_detailed_path=ecg_path,
        ecg_simple_path=ecg_path_simple
    )

    # Generate CSV report
    csv_filename = f"ecg_analysis_{name or 'patient'}.csv"
    csv_path = os.path.join("static", csv_filename)
    try:
        with open(csv_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['Parameter', 'Value'])
            writer.writerow(['Patient Name', name or 'N/A'])
            writer.writerow(['Status', res])
            writer.writerow(['Risk Level', risk_display[0] if risk_display else 'Unknown'])
            writer.writerow(['Age', age])
            writer.writerow(['Gender', 'Male' if Gender == 0 else 'Female'])
            writer.writerow(['Height', height])
            writer.writerow(['Weight', weight])
            writer.writerow(['QRS (ms)', qrsint])
            writer.writerow(['QT (ms)', qtint])
            writer.writerow(['T (ms)', tint])
            writer.writerow(['PR (ms)', p_rint])
            writer.writerow(['P (ms)', pint])
            writer.writerow(['Heart Rate (bpm)', heart_rate])
            writer.writerow(['QRS Deviation %', cent])
            writer.writerow(['Confidence Score', confidence])
            writer.writerow(['Paper Speed (mm/s)', mm_per_sec])
            writer.writerow(['Amplitude Gain (mm/mV)', mm_per_mv])
        csv_generated = True
    except Exception as e:
        print(f"❌ CSV generation failed: {e}")
        csv_generated = False

    # --- Persist to hospital DB ---
    try:
        # if a doctor is logged in, associate records with doctor
        doctor_id = session.get('doctor_id') if 'session' in globals() else None
        patient_id = create_patient(name)
        upload_params = {'mm_per_sec': mm_per_sec, 'mm_per_mv': mm_per_mv, 'hr_override': hr_override}
        upload_id = create_upload(patient_id, 'image', img_name, upload_params, doctor_id=doctor_id)
        report_values = {
            'qrs': qrsint, 'qt': qtint, 't': tint, 'pr': p_rint, 'p': pint, 'heart_rate': heart_rate,
            'preview': os.path.basename(ecg_path_simple) if ecg_path_simple else None,
        }
        create_report(upload_id, pdf_filename if pdf_generated else None, csv_filename if csv_generated else None,
                      report_values, float(confidence), int(result), risk_display, doctor_id=doctor_id)
    except Exception as _:
        # DB errors should not block response
        print('⚠️ Failed to write hospital DB record', _)

    return render_template('ecg_image_upload.html',
                           name=name,
                           status=res,
                           risk=risk_display,
                           cent=cent,
                           confidence=confidence,
                           values={
                               'age': age, 'gender': Gender, 'height': height, 'weight': weight,
                               'qrs': qrsint, 'qt': qtint, 't': tint, 'pr': p_rint, 'p': pint,
                               'heart_rate': heart_rate
                           },
                           image_path=img_path,
                           ecg_path=ecg_path,
                           ecg_path_simple=ecg_path_simple,
                           mm_per_sec=mm_per_sec,
                           mm_per_mv=mm_per_mv,
                           hr_override=hr_override,
                           pdf_filename=pdf_filename if pdf_generated else None,
                           csv_filename=csv_filename if csv_generated else None)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

# ---------------------------
# Secrets / Config
# ---------------------------
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")
# simple in-memory verification codes (doctor email -> code)
VERIFICATION_CODES = {}

# ---------------------------
# Models & Integrations
# ---------------------------
# Load ML model (expects NOTEBOOK_FILES/model.pkl to exist)
MODEL_PATH = "NOTEBOOK_FILES/model.pkl"
try:
    svm = pickle.load(open(MODEL_PATH, "rb"))
    print("✅ Model loaded:", MODEL_PATH)
except Exception as e:
    svm = None
    print("❌ Failed to load model:", e)

# ---------------------------
# Mappings & Constants
# ---------------------------
CLASS_LABELS = {
    1: 'Normal',
    2: 'Ischemic changes (Coronary Artery)',
    3: 'Old Anterior Myocardial Infarction',
    4: 'Old Inferior Myocardial Infarction',
    5: 'Sinus tachycardia',
    6: 'Sinus bradycardy',
    7: 'Ventricular Premature Contraction (PVC)',
    8: 'Supraventricular Premature Contraction',
    9: 'Left bundle branch block',
    10: 'Right bundle branch block',
    14: 'Left ventricle hypertrophy',
    15: 'Atrial Fibrillation or Flutter',
    16: 'Others'
}

RISK_GROUPS = {
    "LOW": {5, 6, 7, 8, 9, 10},
    "HIGH": {2, 3, 4, 14, 15},
    "MODERATE": {16}
}

RISK_SUGGESTIONS = {
    "LOW": [
        "Regular Checkups: Continue periodic monitoring of heart health to catch early warning signs if the condition progresses.",
        "Avoid Excessive Stimulants: Limit caffeine, energy drinks, or other stimulants that might stress the heart.",
        "Regular Exercise: Incorporate moderate physical activity, such as brisk walking or swimming, for at least 30 minutes a day, five times a week.",
        "Balanced Diet: Focus on a heart-healthy diet rich in fruits, vegetables, whole grains, and lean proteins."
    ],
    "HIGH": [
        "Monitor Symptoms Closely: Be alert to signs like shortness of breath, dizziness, or palpitations and report them to a healthcare provider promptly.",
        "Follow Medication Plans: Adhere strictly to prescribed medications or treatments to stabilize arrhythmia.",
        "Stress Management: Practice relaxation techniques like yoga or mindfulness to reduce stress, which can exacerbate arrhythmias.",
        "Quit Smoking: Eliminate tobacco use to reduce heart strain and improve cardiovascular health."
    ],
    "MODERATE": [
        "Immediate Medical Attention: Seek urgent medical care for severe symptoms or complications to prevent emergencies like cardiac arrest.",
        "Restrict Strenuous Activities: Avoid activities that may overexert the heart, as advised by your doctor.",
        "Low-Sodium Diet: Adopt a diet that minimizes salt intake to manage blood pressure and reduce heart stress.",
        "Weight Management: Maintain a healthy weight through a supervised diet and light, safe exercise as recommended by healthcare professionals."
    ],
    "No Risk": ["No Risk — You are Healthy"]
}

GENDER_MAP = {"0": 0, "male": 0, "m": 0, "1": 1, "female": 1, "f": 1}
HISTORY_MAP = {1: "Cardiac Arrest Happened", 0: "No Previous Cardiac Arrest Happened"}

AVG_QRS = 367.2  # used earlier in your code for deviation calculation

# ---------------------------
# Utility Functions
# ---------------------------
def safe_float(val, default=0.0):
    try:
        return float(val)
    except Exception:
        return default

def safe_positive_float(val, default=0.0):
    v = safe_float(val, default)
    return v if v > 0 else default

def map_gender(gender_raw):
    return GENDER_MAP.get(str(gender_raw).lower(), 1)

def map_history(his_raw):
    try:
        return HISTORY_MAP.get(int(his_raw), "Unknown")
    except Exception:
        return "Unknown"

def map_class_label(class_idx):
    return CLASS_LABELS.get(class_idx, "Unknown")

def classify_risk(class_idx):
    for level, members in RISK_GROUPS.items():
        if class_idx in members:
            return level
    return "No Risk"

def get_risk_suggestions(risk_level):
    return RISK_SUGGESTIONS.get(risk_level, ["No information available."])

# ---------------------------
# ECG Plot Generator (DETAILED MULTI-PANEL)
# ---------------------------
def generate_ecg_plot(qrsint, qtint, tint, p_rint, pint):
    import numpy as np
    import matplotlib.pyplot as plt
    import os

    # ---------------- Safe parsing ----------------
    def safe_ms(v, default):
        try:
            return max(float(v), 1)
        except:
            return default

    qrs = safe_ms(qrsint, 90)
    qt  = safe_ms(qtint, 400)
    t   = safe_ms(tint, 160)
    pr  = safe_ms(p_rint, 120)
    p   = safe_ms(pint, 100)

    qrs_s, qt_s, t_s, pr_s, p_s = qrs/1000, qt/1000, t/1000, pr/1000, p/1000

    # ---------------- Timing / Beats ----------------
    beat_len = max(1.4, qt_s + 0.60)
    num_beats = 2
    total_len = beat_len * num_beats
    t_axis = np.linspace(0, total_len, 9000)
    ecg = np.zeros_like(t_axis)

    # ---------------- helper ----------------
    def gauss(x, mu, sigma, amp):
        return amp * np.exp(-((x - mu)**2) / (2 * sigma**2))

    beat_positions = []

    # ---------------- Build beats ----------------
    for b in range(num_beats):
        jitter = np.random.uniform(-0.008, 0.008)
        offset = b * beat_len + jitter
        beat_positions.append(offset)

        # landmarks per beat
        P_start = offset + 0.15
        P_end   = P_start + p_s
        P_peak  = (P_start + P_end)/2

        Q_start = P_end + pr_s * 0.22
        R_peak  = Q_start + qrs_s * 0.12
        S_end   = Q_start + qrs_s

        T_start = S_end + (qt_s - qrs_s) * 0.22
        T_end   = T_start + t_s
        T_peak  = (T_start + T_end)/2

        # amplitudes
        P_amp = 0.14
        Q_amp = -0.28
        R_amp = 1.65
        S_amp = -0.40
        T_amp = 0.48

        ecg += gauss(t_axis, P_peak, p_s/4.8, P_amp)
        ecg += gauss(t_axis, Q_start + qrs_s*0.06, qrs_s/40, Q_amp)
        ecg += gauss(t_axis, R_peak, qrs_s/90, R_amp)
        ecg += gauss(t_axis, S_end - qrs_s*0.07, qrs_s/35, S_amp)
        ecg += gauss(t_axis, T_peak + t_s*0.07, t_s/5.5, T_amp)

    # ---------------- Noise & normalize ----------------
    baseline = 0.012 * np.sin(2*np.pi*t_axis*0.33)
    noise = np.random.normal(0, 0.006, len(ecg))
    ecg = ecg + baseline + noise
    ecg /= max(np.max(np.abs(ecg)), 1e-6)

    # ---------------- First-beat landmarks for zoom panels ----------------
    offset = beat_positions[0]
    P_start = offset + 0.15
    P_end   = P_start + p_s
    P_peak  = (P_start + P_end)/2

    Q_start = P_end + pr_s * 0.22
    R_peak  = Q_start + qrs_s * 0.12
    S_end   = Q_start + qrs_s

    T_start = S_end + (qt_s - qrs_s) * 0.22
    T_end   = T_start + t_s
    T_peak  = (T_start + T_end)/2

    # ---------------- Figure: place all panels in one figure ----------------
    fig = plt.figure(figsize=(22, 14), constrained_layout=True)

    gs = fig.add_gridspec(3, 3, height_ratios=[2.2, 1.1, 1.1], hspace=0.45, wspace=0.3)

    # Panel A: Full ECG strip (top, spans 3 columns)
    ax_full = fig.add_subplot(gs[0, :])
    ax_full.set_facecolor("#fff")
    ax_full.set_xlim(0, total_len)
    ax_full.set_ylim(-1.6, 1.6)
    # grid (minor + major)
    ax_full.set_xticks(np.arange(0, total_len, 0.04), minor=True)
    ax_full.set_yticks(np.arange(-2, 2, 0.1), minor=True)
    ax_full.grid(which="minor", color="#ffe5e5", linestyle="-", linewidth=0.5)
    ax_full.set_xticks(np.arange(0, total_len, 0.20))
    ax_full.set_yticks(np.arange(-2, 2, 0.5))
    ax_full.grid(which="major", color="#ff9c9c", linestyle="-", linewidth=1)
    ax_full.plot(t_axis, ecg, color="red", linewidth=2)

    # vertical markers and labels for beat 1 only
    for x in [P_start, P_end, Q_start, R_peak, S_end, T_start, T_end]:
        ax_full.axvline(x, linestyle="--", color="gray", alpha=0.45)
    ax_full.text(P_peak, 0.25, "P", ha="center")
    ax_full.text(Q_start, -0.85, "Q", ha="center")
    ax_full.text(R_peak, 1.25, "R", ha="center")
    ax_full.text(S_end, -0.85, "S", ha="center")
    ax_full.text(T_peak, 0.50, "T", ha="center")

    # interval arrows on first beat
    ax_full.annotate("", xy=(P_end,0.45), xytext=(Q_start,0.45),
                     arrowprops=dict(arrowstyle="<->", color="olive", linewidth=2))
    ax_full.text((P_end+Q_start)/2,0.50,"PR Segment",color="olive",ha="center")

    ax_full.annotate("", xy=(S_end,0.15), xytext=(T_start,0.15),
                     arrowprops=dict(arrowstyle="<->", color="purple", linewidth=2))
    ax_full.text((S_end+T_start)/2,0.20,"ST Segment",color="purple",ha="center")

    ax_full.annotate("", xy=(Q_start,1.10), xytext=(S_end,1.10),
                     arrowprops=dict(arrowstyle="<->", color="green", linewidth=2))
    ax_full.text((Q_start+S_end)/2,1.20,f"QRS Complex ({qrs} ms)",ha="center",color="green")

    ax_full.annotate("", xy=(P_start,-1.15), xytext=(Q_start,-1.15),
                     arrowprops=dict(arrowstyle="<->",color="orange",linewidth=2))
    ax_full.text((P_start+Q_start)/2,-1.25,f"PR Interval ({pr} ms)",ha="center",color="orange")

    ax_full.annotate("", xy=(Q_start,-1.40), xytext=(T_end,-1.40),
                     arrowprops=dict(arrowstyle="<->",color="blue",linewidth=2))
    ax_full.text((Q_start+T_end)/2,-1.55,f"QT Interval ({qt} ms)",ha="center",color="blue")

    ax_full.set_title("Full ECG Strip (2 beats)")

    # Panel B: Zoom - Full first beat (middle-left)
    ax_beat = fig.add_subplot(gs[1, 0])
    zoom_mask = (t_axis >= offset - 0.05) & (t_axis <= offset + beat_len + 0.05)
    ax_beat.plot(t_axis[zoom_mask], ecg[zoom_mask], color="red", linewidth=2.2)
    ax_beat.set_title("Zoom: Full First Beat")
    ax_beat.grid(True, alpha=0.35)

    # Panel C: Zoom - P wave (middle-center)
    ax_p = fig.add_subplot(gs[1, 1])
    p_mask = (t_axis >= P_start - 0.06) & (t_axis <= P_end + 0.06)
    ax_p.plot(t_axis[p_mask], ecg[p_mask], color="green", linewidth=2.2)
    ax_p.set_title("Zoom: P Wave")
    ax_p.grid(True, alpha=0.35)

    # Panel D: Zoom - QRS (middle-right)
    ax_qrs = fig.add_subplot(gs[1, 2])
    qrs_mask = (t_axis >= Q_start - 0.06) & (t_axis <= S_end + 0.06)
    ax_qrs.plot(t_axis[qrs_mask], ecg[qrs_mask], color="blue", linewidth=2.2)
    ax_qrs.set_title("Zoom: QRS Complex")
    ax_qrs.grid(True, alpha=0.35)

    # Panel E: Zoom - T wave (bottom, spans 3 columns)
    ax_t = fig.add_subplot(gs[2, :])
    t_mask = (t_axis >= T_start - 0.08) & (t_axis <= T_end + 0.08)
    ax_t.plot(t_axis[t_mask], ecg[t_mask], color="magenta", linewidth=2.2)
    ax_t.set_title("Zoom: T Wave")
    ax_t.grid(True, alpha=0.35)

    ax_full.set_xlabel("Time (s)")
    ax_full.set_ylabel("Amplitude (mV)")

    fig.tight_layout()
    fig.subplots_adjust(top=0.95, hspace=0.45)

    os.makedirs("static", exist_ok=True)
    img_path = "static/ecg_signal.png"
    fig.savefig(img_path, dpi=200)
    plt.close(fig)

    return img_path

# ---------------------------
# ECG Plot Generator (SIMPLE INTERVAL-DRIVEN)
# ---------------------------
def generate_ecg_plot_simple(qrsint, qtint, tint, p_rint, pint):
    """
    Generate an interval-driven ECG waveform and display labeled durations.
    """
    import numpy as np
    import matplotlib.pyplot as plt
    import os

    # ---- safe value parsing ----
    def safe_float(v, default):
        try:
            f = float(v)
            return f if f > 1 else default
        except Exception:
            return default

    qrs = safe_float(qrsint, 90)
    qt = safe_float(qtint, 400)
    t = safe_float(tint, 160)
    pr = safe_float(p_rint, 120)
    p = safe_float(pint, 100)

    # convert ms → seconds
    qrs_s, qt_s, t_s, pr_s, p_s = qrs / 1000.0, qt / 1000.0, t / 1000.0, pr / 1000.0, p / 1000.0

    # timebase scaled to QT interval
    beat_length = max(0.7, qt_s + 0.4)
    t_axis = np.linspace(0, beat_length, 2500)
    ecg = np.zeros_like(t_axis)

    # --- define key positions ---
    P_start = 0.1
    P_center = P_start + p_s / 2
    P_end = P_start + p_s
    Q_start = P_end + pr_s * 0.25
    R_peak = Q_start + qrs_s * 0.35
    S_end = Q_start + qrs_s
    T_center = S_end + (qt_s - qrs_s) * 0.5

    eps = 1e-3

    # --- P wave ---
    ecg += 0.15 * np.sin((t_axis - P_center) * (np.pi / max(p_s, eps))) * \
           np.exp(-((t_axis - P_center) ** 2) / (2 * (max(p_s, eps) ** 2)))

    # --- QRS complex ---
    ecg -= 0.18 * np.exp(-((t_axis - Q_start) ** 2) / (2 * (max(qrs_s / 8, eps) ** 2)))
    ecg += 1.2 * np.exp(-((t_axis - R_peak) ** 2) / (2 * (max(qrs_s / 25, eps) ** 2)))
    ecg -= 0.35 * np.exp(-((t_axis - S_end) ** 2) / (2 * (max(qrs_s / 10, eps) ** 2)))

    # --- T wave ---
    ecg += 0.28 * np.sin((t_axis - T_center) * (np.pi / max(t_s, eps))) * \
           np.exp(-((t_axis - T_center) ** 2) / (2 * (max(t_s, eps) ** 2)))

    # --- normalize amplitude ---
    if np.max(np.abs(ecg)) > 0:
        ecg /= np.max(np.abs(ecg))

    # ---- plot ECG ----
    plt.figure(figsize=(8, 3))
    plt.plot(t_axis, ecg, color='red', linewidth=2)
    plt.title("Simulated ECG Signal Based on Input Intervals")
    plt.xlabel("Time (s)")
    plt.ylabel("Amplitude (mV)")
    plt.grid(True)

    # --- draw vertical phase markers ---
    plt.axvline(P_start, color='blue', linestyle='--', linewidth=0.8)
    plt.axvline(Q_start, color='orange', linestyle='--', linewidth=0.8)
    plt.axvline(R_peak, color='green', linestyle='--', linewidth=0.8)
    plt.axvline(S_end, color='purple', linestyle='--', linewidth=0.8)
    plt.axvline(T_center, color='brown', linestyle='--', linewidth=0.8)

    # label points
    plt.text(P_start, 0.9, 'P', color='blue', fontsize=8, ha='center')
    plt.text(Q_start, 0.9, 'Q', color='orange', fontsize=8, ha='center')
    plt.text(R_peak, 0.9, 'R', color='green', fontsize=8, ha='center')
    plt.text(S_end, 0.9, 'S', color='purple', fontsize=8, ha='center')
    plt.text(T_center, 0.9, 'T', color='brown', fontsize=8, ha='center')

    # ---- Add duration labels (ms + sec) ----
    label_y = -0.4
    plt.text((P_start + Q_start) / 2, label_y, f"PR: {pr:.0f} ms ({pr_s:.2f}s)", color='blue', ha='center', fontsize=8)
    plt.text((Q_start + S_end) / 2, label_y - 0.1, f"QRS: {qrs:.0f} ms ({qrs_s:.2f}s)", color='green', ha='center', fontsize=8)
    plt.text((Q_start + T_center) / 2, label_y - 0.2, f"QT: {qt:.0f} ms ({qt_s:.2f}s)", color='purple', ha='center', fontsize=8)
    plt.text((T_center + beat_length) / 2, label_y - 0.3, f"T: {t:.0f} ms ({t_s:.2f}s)", color='brown', ha='center', fontsize=8)
    plt.text((P_start + P_end) / 2, label_y - 0.4, f"P: {p:.0f} ms ({p_s:.2f}s)", color='blue', ha='center', fontsize=8)

    plt.tight_layout()

    os.makedirs("static", exist_ok=True)
    ecg_path = os.path.join("static", "ecg_signal_simple.png")
    plt.savefig(ecg_path, dpi=130)
    plt.close()

    print(f"✅ ECG plot (simple) generated and labeled → {ecg_path}")
    return ecg_path

# ---------------------------
# Email Sender (with multiple inline images & attachments)
# ---------------------------
def send_email(receiver_email, subject, body, ecg_paths=None, attachments=None):
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("⚠️ Email skipped: set SENDER_EMAIL and SENDER_PASSWORD environment variables")
        return

    if ecg_paths is None:
        ecg_paths = []
    if attachments is None:
        attachments = []

    msg = MIMEMultipart("related")
    msg["From"] = SENDER_EMAIL
    msg["To"] = receiver_email
    msg["Subject"] = subject

    text_part = MIMEText(body, "plain")
    html_body = body.replace("\n", "<br>")
    html_part = MIMEText(f"<html><body>{html_body}</body></html>", "html")

    msg.attach(text_part)
    msg.attach(html_part)

    # Attach all ECG images (inline + as downloadable attachments)
    for ecg_path in ecg_paths:
        if ecg_path and os.path.exists(ecg_path):
            # Inline image
            with open(ecg_path, "rb") as f:
                img = MIMEImage(f.read(), name=os.path.basename(ecg_path))
                img.add_header("Content-ID", f"<{os.path.basename(ecg_path)}>")
                img.add_header("Content-Disposition", "inline", filename=os.path.basename(ecg_path))
                msg.attach(img)

            # Also as attachment
            with open(ecg_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(ecg_path)}")
            msg.attach(part)

    # Attach any extra files (e.g., PDF report)
    for file_path in attachments:
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(file_path)}")
            msg.attach(part)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, receiver_email, msg.as_string())
        print(f"✅ Email sent to {receiver_email}")
    except Exception as e:
        print("❌ Email sending failed:", e)

# ---------------------------
# PDF Report Generator
# ---------------------------
def generate_pdf_report(output_path,
                        name,
                        status,
                        risk_list,
                        values_dict,
                        deviation_percent,
                        ecg_detailed_path=None,
                        ecg_simple_path=None):
    try:
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter, A4
        
        # Create document
        doc = SimpleDocTemplate(output_path, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
        elements = []
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=26,
            textColor=colors.whitesmoke,
            spaceAfter=6,
            fontName='Helvetica-Bold',
            alignment=1,  # Center
            leading=32
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#764ba2'),
            spaceAfter=10,
            fontName='Helvetica-Bold',
            leftIndent=10,
            leading=16
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=11,
            textColor=colors.HexColor('#333333'),
            spaceAfter=8,
            leftIndent=10,
            leading=14
        )
        
        # Header with gradient background (using table)
        header_data = [[Paragraph("🏥 Cardiac Arrhythmia Report", title_style)]]
        header_table = Table(header_data, colWidths=[7.5*inch])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#667eea')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWHEIGHT', (0, 0), (-1, -1), 70),
            ('TOPPADDING', (0, 0), (-1, -1), 15),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 18))
        
        # Patient Info Section
        patient_info = [
            [Paragraph(f"<b>👤 Patient Name:</b> {name or 'N/A'}", normal_style)],
            [Paragraph(f"<b>📊 Status:</b> <font color='#00c853'><b>{status}</b></font>", normal_style)],
            [Paragraph(f"<b>📈 QRS Deviation:</b> {deviation_percent}%", normal_style)],
        ]
        patient_table = Table(patient_info, colWidths=[7.5*inch])
        patient_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f0f9ff')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ROWHEIGHT', (0, 0), (-1, -1), 28),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 15),
            ('LEFTBORDER', (0, 0), (-1, -1), 4),
            ('LEFTBORDERCOLOR', (0, 0), (-1, -1), colors.HexColor('#667eea')),
        ]))
        elements.append(patient_table)
        elements.append(Spacer(1, 14))
        
        # Risk Assessment Section
        if risk_list:
            elements.append(Paragraph("⚠️ Risk Assessment & Recommendations", heading_style))
            risk_data = []
            for idx, r in enumerate(risk_list):
                color = colors.HexColor('#ff6b6b') if idx == 0 else colors.HexColor('#333333')
                risk_data.append([Paragraph(f"<font color='{color}'><b>→ {r}</b></font>", normal_style)])
            risk_table = Table(risk_data, colWidths=[7.5*inch])
            risk_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fff5f5')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ROWHEIGHT', (0, 0), (-1, -1), 24),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LEFTPADDING', (0, 0), (-1, -1), 12),
                ('LEFTBORDER', (0, 0), (-1, -1), 3),
                ('LEFTBORDERCOLOR', (0, 0), (-1, -1), colors.HexColor('#ff6b6b')),
            ]))
            elements.append(risk_table)
            elements.append(Spacer(1, 14))
        
        # Values Table Section
        if values_dict:
            elements.append(Paragraph("📋 Cardiac Parameters", heading_style))
            values_data = [
                [Paragraph("<b>Parameter</b>", ParagraphStyle('TableHeader', parent=styles['Normal'], fontSize=10, textColor=colors.whitesmoke, fontName='Helvetica-Bold')),
                 Paragraph("<b>Value</b>", ParagraphStyle('TableHeader', parent=styles['Normal'], fontSize=10, textColor=colors.whitesmoke, fontName='Helvetica-Bold'))]
            ]
            for k in ["age", "gender", "height", "weight", "qrs", "qt", "t", "pr", "p", "heart_rate"]:
                if k in values_dict:
                    display_k = k.upper().replace('_', ' ')
                    display_v = values_dict[k]
                    if k == "gender":
                        display_v = "Male" if display_v == 0 else "Female"
                    values_data.append([
                        Paragraph(display_k, ParagraphStyle('TableCell', parent=styles['Normal'], fontSize=10, leftIndent=5)),
                        Paragraph(str(display_v), ParagraphStyle('TableCell', parent=styles['Normal'], fontSize=10))
                    ])
            
            values_table = Table(values_data, colWidths=[3.75*inch, 3.75*inch])
            values_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8f9fa'), colors.white]),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#ddd')),
                ('ROWHEIGHT', (0, 0), (-1, -1), 22),
            ]))
            elements.append(values_table)
            elements.append(Spacer(1, 14))
        
        # ECG Images Section
        if ecg_detailed_path or ecg_simple_path:
            elements.append(Paragraph("💓 ECG Signals", heading_style))
            img_data = []
            img_row = []
            
            if ecg_detailed_path and os.path.exists(ecg_detailed_path):
                try:
                    img_detailed = Image(ecg_detailed_path, width=3.2*inch, height=1.7*inch)
                    img_row.append(img_detailed)
                except Exception:
                    pass
            
            if ecg_simple_path and os.path.exists(ecg_simple_path):
                try:
                    img_simple = Image(ecg_simple_path, width=3.2*inch, height=1.7*inch)
                    if img_row:
                        img_data.append(img_row)
                    img_row = [img_simple]
                except Exception:
                    pass
            
            if img_row:
                img_data.append(img_row)
            
            if img_data:
                img_table = Table(img_data, colWidths=[3.6*inch, 3.6*inch])
                img_table.setStyle(TableStyle([
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('ROWHEIGHT', (0, 0), (-1, -1), 1.9*inch),
                    ('LEFTPADDING', (0, 0), (-1, -1), 10),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                ]))
                elements.append(img_table)
        
        elements.append(Spacer(1, 20))
        
        # Footer
        footer_style = ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#999999'),
            alignment=1
        )
        elements.append(Paragraph(
            "🔒 <b>Confidential Medical Report</b> | Generated on " + 
            __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            footer_style
        ))
        
        # Build PDF
        doc.build(elements)
        return output_path
    except Exception as e:
        print("❌ PDF generation failed:", e)
        import traceback
        traceback.print_exc()
        return None

# ---------------------------
# Flask Routes
# ---------------------------
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/index')
def index():
    return render_template('index.html')

@app.route('/userlog', methods=['GET', 'POST'])
def userlog():
    if request.method == 'POST':
        connection = sqlite3.connect('user_data.db')
        cursor = connection.cursor()
        name = request.form['name']
        password = request.form['password']
        query = "SELECT name, password FROM user WHERE name = ? AND password = ?"
        cursor.execute(query, (name, password))
        result = cursor.fetchall()
        connection.close()
        if result:
            return render_template('fetal.html')
        else:
            return render_template('index.html', msg='Sorry, Incorrect Credentials Provided, Try Again')
    return render_template('index.html')

@app.route('/userreg', methods=['GET', 'POST'])
def userreg():
    if request.method == 'POST':
        connection = sqlite3.connect('user_data.db')
        cursor = connection.cursor()
        name = request.form['name']
        password = request.form['password']
        mobile = request.form['phone']
        email = request.form['email']
        command = "CREATE TABLE IF NOT EXISTS user(name TEXT, password TEXT, mobile TEXT, email TEXT)"
        cursor.execute(command)
        cursor.execute("INSERT INTO user VALUES (?, ?, ?, ?)", (name, password, mobile, email))
        connection.commit()
        connection.close()
        return render_template('index.html', msg='Successfully Registered')
    return render_template('index.html')



@app.route("/fetalPage", methods=['GET', 'POST'])
def fetalPage():
    return render_template('fetal.html')

@app.route("/ecg_animated")
def ecg_animated():
    """
    Render an interactive animated ECG using Plotly.
    Expects query params qrs, qt, t, p_r, p (ms). If missing, defaults are used.
    Example: /ecg_animated?qrs=90&qt=400&t=160&p_r=120&p=100
    """
    def safe_get_ms(k, default):
        try:
            v = float(request.args.get(k, default))
            return max(1.0, v)
        except Exception:
            return default

    qrs = safe_get_ms('qrs', 90.0)
    qt  = safe_get_ms('qt', 400.0)
    t   = safe_get_ms('t', 160.0)
    pr  = safe_get_ms('p_r', 120.0)
    p   = safe_get_ms('p', 100.0)

    # pass them to template (they will be used by client-side JS)
    return render_template('ecg_animated.html',
                           qrs_ms=qrs,
                           qt_ms=qt,
                           t_ms=t,
                           pr_ms=pr,
                           p_ms=p)

# ---------------------------
# Refactored predict route
# ---------------------------
@app.route("/predict", methods=['GET', 'POST'])
def predictPage():
    if request.method == 'POST':
        # --- collect form inputs ---
        name = request.form.get('name', '')
        age = safe_positive_float(request.form.get('Age', 0))
        gender_raw = request.form.get('Gender', '')
        height = safe_positive_float(request.form.get('Height', 0))
        weight = safe_positive_float(request.form.get('Weight', 0))
        qrsint = request.form.get('qrs', '')
        qtint = request.form.get('q_t', '')
        p_rint = request.form.get('p_r', '')
        tint = request.form.get('t', '')
        pint = request.form.get('p', '')
        heart_rate = request.form.get('heart_rate', '')
        email = request.form.get('email', '')
        his_raw = request.form.get('his', '0')

        # --- normalized mappings ---
        Gender = map_gender(gender_raw)
        history = map_history(his_raw)

        # --- prepare data for model (use safe numeric parsing; model expects specific dtypes) ---
        try:
            model_input = np.array([[age, Gender, height, weight,
                                     float(qrsint), float(qtint), float(tint),
                                     float(p_rint), float(pint), float(heart_rate)]])
        except Exception:
            # fallback: convert with safe_float to avoid crash
            model_input = np.array([[age, Gender, height, weight,
                                     safe_float(qrsint), safe_float(qtint), safe_float(tint),
                                     safe_float(p_rint), safe_float(pint), safe_float(heart_rate)]])

        if svm is None:
            return render_template('predict.html', name=name, pred=None, status="Model not loaded",
                                   risk=["Unknown"], cent="N/A", ecg_path=None, ecg_path_simple=None)

        # --- prediction & probabilities ---
        my_prediction = svm.predict(model_input)
        probs = svm.predict_proba(model_input)[0]
        result = int(my_prediction[0])

        # --- map label & risk ---
        res = map_class_label(result)
        risk_level = classify_risk(result)
        risk_suggestions = get_risk_suggestions(risk_level)

        # --- pie chart for probs (only include classes that exist in CLASS_LABELS) ---
        labels = []
        sizes = []
        for idx, p_prob in enumerate(probs, start=1):
            if idx in CLASS_LABELS and p_prob > 0:
                labels.append(CLASS_LABELS[idx])
                sizes.append(p_prob + 1e-6)  # tiny add to avoid zero slices

        if sizes:
            plt.figure(figsize=(6, 6))
            plt.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=140)
            plt.title("svm Prediction Probabilities")
            os.makedirs("static", exist_ok=True)
            pie_path = os.path.join("static", "out.jpg")
            plt.savefig(pie_path, format="jpg")
            plt.close()
        else:
            pie_path = None

        # --- generate BOTH ECG plots and compute deviation ---
        ecg_path = generate_ecg_plot(qrsint, qtint, tint, p_rint, pint)
        ecg_path_simple = generate_ecg_plot_simple(qrsint, qtint, tint, p_rint, pint)

        try:
            cent_val = abs(float(qrsint) - AVG_QRS) / AVG_QRS * 100
            cent = f"{cent_val:.2f}"
        except Exception:
            cent = "N/A"

        # --- email summary ---
        email_body = (
            f"Name: {name}\n"
            f"Age: {age}\n"
            f"Gender: {'Male' if Gender == 0 else 'Female'}\n"
            f"QRS Interval: {qrsint}\nQT Interval: {qtint}\nT Interval: {tint}\n"
            f"PR Interval: {p_rint}\nP Interval: {pint}\n heart_rate: {heart_rate}\n"
            f"Status: {res}\nRisk Level: {risk_level}\nDeviation Percentage: {cent}%\nHistory: {history}\n"
        )

        ecg_paths = [p for p in [ecg_path, ecg_path_simple] if p]

        # Prepare risk list early for downstream uses (PDF/email/render)
        risk_display = [risk_level] + risk_suggestions if isinstance(risk_suggestions, list) else [risk_level]

        # Generate PDF report and send email to admin + patient
        os.makedirs("static", exist_ok=True)
        pdf_path = os.path.join("static", f"report_{name or 'patient'}.pdf")
        pdf_generated = generate_pdf_report(
            pdf_path,
            name=name,
            status=res,
            risk_list=risk_display,
            values_dict={
                'age': age, 'gender': Gender, 'height': height, 'weight': weight,
                'qrs': qrsint, 'qt': qtint, 't': tint, 'pr': p_rint, 'p': pint,
                'heart_rate': heart_rate
            },
            deviation_percent=cent,
            ecg_detailed_path=ecg_path,
            ecg_simple_path=ecg_path_simple
        )

        try:
            send_email("jaideepmc2003@gmail.com", f"Cardiac Report for {name}", email_body, ecg_paths, attachments=[pdf_generated] if pdf_generated else [])
            if email:
                send_email(email, f"Your Cardiac Report, {name}", email_body, ecg_paths, attachments=[pdf_generated] if pdf_generated else [])
        except Exception as e:
            print("❌ Email sending exception:", e)

        # Render response: risk is shown as list where first element is level and others are suggestions
        # --- Persist manual input as upload + report ---
        try:
            doctor_id = session.get('doctor_id') if 'session' in globals() else None
            patient_id = create_patient(name)
            upload_params = {'method': 'manual'}
            # ensure uploads dir exists and write CSV summary of the manual input
            uploads_dir = os.path.join('static', 'uploads')
            os.makedirs(uploads_dir, exist_ok=True)
            safe_name = secure_filename(name or 'patient')
            ts = int(datetime.utcnow().timestamp())
            csv_filename = f"manual_{safe_name}_{ts}.csv"
            csv_path = os.path.join(uploads_dir, csv_filename)
            try:
                with open(csv_path, 'w', newline='', encoding='utf-8') as cf:
                    writer = csv.writer(cf)
                    writer.writerow(['name','age','gender','height','weight','qrs','qt','t','pr','p','heart_rate','prediction','risk'])
                    writer.writerow([name, age, Gender, height, weight, qrsint, qtint, tint, p_rint, pint, heart_rate, res, json.dumps(risk_display)])
            except Exception as wf:
                print('⚠️ Failed to write manual CSV upload file:', wf)

            # If a PDF report was generated, copy it into uploads with a timestamped name for download
            pdf_upload_filename = None
            try:
                if pdf_generated:
                    pdf_upload_filename = f"report_{safe_name}_{ts}.pdf"
                    shutil.copy(pdf_generated, os.path.join(uploads_dir, pdf_upload_filename))
            except Exception as pf:
                print('⚠️ Failed to copy PDF to uploads:', pf)

            upload_id = create_upload(patient_id, 'manual', csv_filename, upload_params, doctor_id=doctor_id)
            pdf_filename = os.path.basename(pdf_path)
            report_values = {
                'age': age, 'gender': Gender, 'height': height, 'weight': weight,
                'qrs': qrsint, 'qt': qtint, 't': tint, 'pr': p_rint, 'p': pint,
                'heart_rate': heart_rate,
                'preview': os.path.basename(ecg_path_simple) if ecg_path_simple else None,
                'manual_csv': csv_filename,
                'manual_pdf': pdf_upload_filename,
            }
            create_report(upload_id, pdf_upload_filename if pdf_upload_filename else (pdf_filename if pdf_generated else None), csv_filename, report_values, 0.0, int(result), risk_display, doctor_id=doctor_id)
        except Exception as _:
            print('⚠️ Failed to write hospital DB record (manual)', _)

        return render_template('predict.html',
                               name=name,
                               pred=result,
                               status=res,
                               risk=risk_display,
                               cent=cent,
                               ecg_path=ecg_path,
                               ecg_path_simple=ecg_path_simple)

    # Provide safe defaults for template variables to avoid Jinja undefined errors
    return render_template('predict.html',
                           name='',
                           pred=None,
                           status=None,
                           risk=['Unknown'],
                           cent='N/A',
                           ecg_path=None,
                           ecg_path_simple=None)

# ---------------------------
# Upload CSV: Alternate input path
# ---------------------------
@app.route('/upload_csv', methods=['GET', 'POST'])
def upload_csv():
    if request.method == 'GET':
        return render_template('csv_upload.html')

    # POST: handle file upload and predict
    file = request.files.get('csv_file')
    name = request.form.get('name', '')
    if not file or file.filename == '':
        return render_template('csv_upload.html', status='No file selected', name=name)

    try:
        filename = secure_filename(file.filename)
        os.makedirs('static/uploads', exist_ok=True)
        upload_path = os.path.join('static', 'uploads', filename)
        # read and save uploaded CSV
        raw_text = file.stream.read().decode('utf-8')
        with open(upload_path, 'w', encoding='utf-8') as f:
            f.write(raw_text)
        rows = list(csv.DictReader(raw_text.splitlines()))
        if not rows:
            return render_template('csv_upload.html', status='CSV contains no data', name=name)

        row = rows[0]
        # Expected headers (case-insensitive): age, gender, height, weight, qrs, qt, t, pr, p, heart_rate
        def getv(key, default='0'):
            for k in row.keys():
                if k.strip().lower() == key:
                    return row[k]
            return default
    except Exception as e:
        return render_template('csv_upload.html', status=f'Failed to read CSV: {str(e)}', name=name)

    age = safe_positive_float(getv('age'))
    gender_raw = getv('gender', '0')  # 'male'/'female' or 0/1
    Gender = map_gender(gender_raw)
    height = safe_positive_float(getv('height'))
    weight = safe_positive_float(getv('weight'))
    qrsint = getv('qrs')
    qtint = getv('qt')
    tint = getv('t')
    p_rint = getv('pr')
    pint = getv('p')
    heart_rate = getv('heart_rate')

    try:
        model_input = np.array([[age, Gender, height, weight,
                                 float(qrsint), float(qtint), float(tint),
                                 float(p_rint), float(pint), float(heart_rate)]])
    except Exception:
        model_input = np.array([[age, Gender, height, weight,
                                 safe_float(qrsint), safe_float(qtint), safe_float(tint),
                                 safe_float(p_rint), safe_float(pint), safe_float(heart_rate)]])

    if svm is None:
        return render_template('csv_upload.html', name=name, status='Model not loaded')

    my_prediction = svm.predict(model_input)
    result = int(my_prediction[0])
    res = map_class_label(result)
    risk_level = classify_risk(result)
    risk_suggestions = get_risk_suggestions(risk_level)

    # ECG plots
    ecg_path = generate_ecg_plot(qrsint, qtint, tint, p_rint, pint)
    ecg_path_simple = generate_ecg_plot_simple(qrsint, qtint, tint, p_rint, pint)

    try:
        cent_val = abs(float(qrsint) - AVG_QRS) / AVG_QRS * 100
        cent = f"{cent_val:.2f}"
    except Exception:
        cent = "N/A"

    risk_display = [risk_level] + risk_suggestions if isinstance(risk_suggestions, list) else [risk_level]

    # Generate PDF report
    os.makedirs("static", exist_ok=True)
    pdf_path = os.path.join("static", f"report_{name or 'patient'}.pdf")
    pdf_generated = generate_pdf_report(
        pdf_path,
        name=name,
        status=res,
        risk_list=risk_display,
        values_dict={
            'age': age, 'gender': Gender, 'height': height, 'weight': weight,
            'qrs': qrsint, 'qt': qtint, 't': tint, 'pr': p_rint, 'p': pint,
            'heart_rate': heart_rate
        },
        deviation_percent=cent,
        ecg_detailed_path=ecg_path,
        ecg_simple_path=ecg_path_simple
    )

    # --- Persist CSV upload and report to DB ---
    try:
        doctor_id = session.get('doctor_id') if 'session' in globals() else None
        patient_id = create_patient(name)
        upload_params = {'source': 'csv'}
        upload_id = create_upload(patient_id, 'csv', filename, upload_params, doctor_id=doctor_id)
        pdf_filename = os.path.basename(pdf_path)
        csv_saved_name = os.path.join('static', 'uploads', filename)
        report_values = {
            'age': age, 'gender': Gender, 'height': height, 'weight': weight,
            'qrs': qrsint, 'qt': qtint, 't': tint, 'pr': p_rint, 'p': pint,
            'heart_rate': heart_rate,
            'preview': os.path.basename(ecg_path_simple) if ecg_path_simple else None,
        }
        create_report(upload_id, pdf_filename if pdf_generated else None, csv_saved_name, report_values, 0.0, int(result), risk_display, doctor_id=doctor_id)
    except Exception as _:
        print('⚠️ Failed to write hospital DB record (CSV)', _)

    # Optionally email PDF to patient if they provided name (email not in CSV form here)
    # You can extend the form to include email in CSV upload if desired.

    return render_template('csv_upload.html',
                           name=name,
                           status=res,
                           risk=risk_display,
                           cent=cent,
                           values={
                               'age': age, 'gender': Gender, 'height': height, 'weight': weight,
                               'qrs': qrsint, 'qt': qtint, 't': tint, 'pr': p_rint, 'p': pint,
                               'heart_rate': heart_rate
                           },
                           ecg_path=ecg_path,
                           ecg_path_simple=ecg_path_simple)

# ---------------------------
# Run App (moved to bottom after all routes)
# ---------------------------


# --- Admin helpers: quick JSON view of recent reports ---
@app.route('/admin/reports')
def admin_reports():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT r.id, u.filename as upload_filename, p.name as patient_name, r.pdf_filename, r.csv_filename, r.values_json, r.confidence, r.result, r.risk, r.created_at FROM report r LEFT JOIN upload u ON r.upload_id=u.id LEFT JOIN patient p ON u.patient_id=p.id ORDER BY r.created_at DESC LIMIT 100')
        rows = [dict(x) for x in cur.fetchall()]
        conn.close()
        return {'reports': rows}
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/doctor/reports')
def doctor_reports():
    doctor_id = session.get('doctor_id')
    if not doctor_id:
        return redirect(url_for('login'))
    # support query params: days=N, page=P, per_page=K, result_type=normal/abnormal/all
    try:
        days = request.args.get('days', type=int)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        result_type = request.args.get('result_type', 'all', type=str).lower()

        cutoff = None
        params = []
        where_clauses = []
        # show reports for this doctor or unassigned (NULL)
        where_clauses.append('(r.doctor_id = ? OR r.doctor_id IS NULL)')
        params.append(doctor_id)

        if days and days > 0:
            cutoff_dt = datetime.utcnow() - timedelta(days=days)
            cutoff = cutoff_dt.isoformat()
            where_clauses.append('r.created_at >= ?')
            params.append(cutoff)
        
        # Filter by result type if not 'all'
        if result_type == 'normal':
            where_clauses.append("(LOWER(r.result) LIKE '%normal%' OR r.result = '1')")
        elif result_type == 'abnormal':
            where_clauses.append("(LOWER(r.result) NOT LIKE '%normal%' AND r.result != '1')")

        where_sql = ' AND '.join(where_clauses)
        limit = per_page
        offset = (page - 1) * per_page

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        sql = f'''SELECT r.id, r.pdf_filename, r.csv_filename, r.values_json, r.confidence, r.result, r.risk, r.created_at,
                   u.filename AS upload_filename, u.patient_id AS patient_id, p.name AS patient_name
                  FROM report r
                  LEFT JOIN upload u ON r.upload_id = u.id
                  LEFT JOIN patient p ON u.patient_id = p.id
                 WHERE {where_sql}
              ORDER BY r.created_at DESC
                 LIMIT ? OFFSET ?'''
        params.extend([limit, offset])
        cur.execute(sql, tuple(params))
        rows = [dict(x) for x in cur.fetchall()]

        # total count for pagination
        count_sql = f"SELECT COUNT(1) as cnt FROM report r LEFT JOIN upload u ON r.upload_id=u.id WHERE {where_sql}"
        cur.execute(count_sql, tuple(params[:-2]))
        cnt_row = cur.fetchone()
        total = cnt_row['cnt'] if cnt_row else 0

        conn.close()

        # Build URLs and parse preview from values_json
        for r in rows:
            # helper to resolve file under static/ or static/uploads/
            def _static_file_url(filename):
                if not filename:
                    return None
                # absolute paths
                p1 = os.path.join(app.root_path, 'static', filename)
                p2 = os.path.join(app.root_path, 'static', 'uploads', filename)
                if os.path.exists(p1):
                    return url_for('static', filename=filename)
                if os.path.exists(p2):
                    return url_for('static', filename=f'uploads/{filename}')
                # fallback to uploads path (common for manually-created files)
                return url_for('static', filename=f'uploads/{filename}')

            r['pdf_url'] = _static_file_url(r.get('pdf_filename'))
            r['csv_url'] = _static_file_url(r.get('csv_filename'))
            if r.get('upload_filename'):
                r['upload_url'] = url_for('static', filename=f"uploads/{r['upload_filename']}")
            else:
                r['upload_url'] = None
            # parse values_json for preview and resolve its path similarly
            try:
                vals = json.loads(r.get('values_json') or '{}')
                preview_name = vals.get('preview')
                r['preview_url'] = _static_file_url(preview_name)
            except Exception:
                r['preview_url'] = None

        total_pages = (total + per_page - 1) // per_page if per_page else 1

        return render_template('doctor_dashboard.html', reports=rows, doctor_name=session.get('doctor_name'),
                       page=page, per_page=per_page, total=total, total_pages=total_pages,
                       class_labels=CLASS_LABELS, risk_groups=RISK_GROUPS, result_type=result_type)
    except Exception as e:
        return render_template('doctor_dashboard.html', reports=[], error=str(e))


@app.route('/visualization')
def visualization_page():
    """Visualization page for doctors"""
    doctor_id = session.get('doctor_id')
    if not doctor_id:
        return redirect(url_for('login'))
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Query reports with patient and upload info
        cursor.execute('''
            SELECT 
                r.id as report_id,
                r.result,
                r.risk,
                r.confidence,
                r.created_at,
                p.name as patient_name,
                u.filename as upload_filename,
                r.values_json
            FROM report r
            LEFT JOIN upload u ON r.upload_id = u.id
            LEFT JOIN patient p ON u.patient_id = p.id
            WHERE r.doctor_id = ? OR r.doctor_id IS NULL
            ORDER BY r.created_at DESC
        ''', (doctor_id,))
        
        reports = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        # Prepare data for visualization
        reports_data = []
        for r in reports:
            result_val = r.get('result', 0)
            if isinstance(result_val, str):
                result_val = result_val.lower()
            result_label = 'Normal' if (result_val == 1 or result_val == '1' or 'normal' in str(result_val).lower()) else 'Abnormal'
            
            # Parse class from values_json
            try:
                values = json.loads(r.get('values_json') or '{}')
                class_val = values.get('class', result_val)
            except:
                class_val = result_val
            
            # Get class type/arrhythmia name
            class_type = CLASS_LABELS.get(class_val, 'Unknown') if isinstance(class_val, int) else CLASS_LABELS.get(int(class_val), 'Unknown') if str(class_val).isdigit() else 'Unknown'
            
            reports_data.append({
                'patient_name': r.get('patient_name', 'Unknown'),
                'result': result_val,
                'result_label': result_label,
                'class': class_val,
                'class_type': class_type,
                'created_at': str(r.get('created_at', '')),
                'upload_filename': r.get('upload_filename', '')
            })
        
        reports_json = json.dumps(reports_data)
        
        return render_template('visualization.html', 
                             total=len(reports_data),
                             reports_json=reports_json)
    except Exception as e:
        print(f"Visualization error: {e}")
        import traceback
        traceback.print_exc()
        return render_template('visualization.html', total=0, reports_json='[]', error=str(e))


# ---------------------------
# Doctor auth (signup/login via email code)
# ---------------------------
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'GET':
        return render_template('signup.html')
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    # generate 6-digit code and email it
    code = str(int.from_bytes(os.urandom(3), 'big') % 1000000).zfill(6)
    VERIFICATION_CODES[email] = code
    send_email(email, 'Your verification code', f'Your verification code: {code}')
    # store the code in session for reliable verification across requests
    session['pending_signup'] = {'name': name, 'email': email, 'code': code}
    expected_code = session['pending_signup']['code'] if app.debug else None
    return render_template('signup.html', message='Verification code sent to email. Please enter code to verify.', show_verify=True, expected_code=expected_code)


@app.route('/signup/verify', methods=['POST'])
def signup_verify():
    data = session.get('pending_signup')
    if not data:
        return redirect(url_for('signup'))
    code = request.form.get('code', '').strip()
    email = data.get('email')
    # Prefer session-stored code to avoid issues if the in-memory dict was reset
    session_code = data.get('code')
    expected = session_code or VERIFICATION_CODES.get(email)
    if expected == code:
        # create doctor record
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        now = datetime.utcnow().isoformat()
        try:
            cur.execute('INSERT INTO doctor (name, email, password_hash, verified, created_at) VALUES (?, ?, ?, ?, ?)',
                        (data.get('name'), email, '', 1, now))
            conn.commit()
            session.pop('pending_signup', None)
            return redirect(url_for('login'))
        except Exception as e:
            return render_template('signup.html', error=str(e))
        finally:
            conn.close()
    else:
        return render_template('signup.html', message='Invalid code, try again.', show_verify=True)


@app.route('/login', methods=['GET', 'POST'])
def login():
    # If already authenticated, go straight to the doctor dashboard instead of showing login again
    if session.get('doctor_id'):
        return redirect(url_for('doctor_reports'))
    if request.method == 'GET':
        return render_template('login.html')
    email = request.form.get('email', '').strip().lower()
    # generate code and send
    code = str(int.from_bytes(os.urandom(3), 'big') % 1000000).zfill(6)
    VERIFICATION_CODES[email] = code
    send_email(email, 'Your login code', f'Your login code: {code}')
    # store code in session so verification is robust
    session['pending_login'] = {'email': email, 'code': code}
    expected_code = session['pending_login']['code'] if app.debug else None
    return render_template('login.html', message='Login code sent. Enter code to continue.', show_verify=True, expected_code=expected_code)


@app.route('/login/verify', methods=['POST'])
def login_verify():
    data = session.get('pending_login')
    if not data:
        return redirect(url_for('login'))
    email = data.get('email')
    code = request.form.get('code', '').strip()
    session_code = data.get('code')
    expected = session_code or VERIFICATION_CODES.get(email)
    if expected == code:
        # lookup doctor id
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('SELECT id, name, role FROM doctor WHERE email=?', (email,))
        row = cur.fetchone()
        conn.close()
        if row:
            # keep doctor session alive across redirects so it is not lost on the next request
            session.permanent = True
            app.permanent_session_lifetime = timedelta(hours=6)
            session['doctor_id'] = row[0]
            session['doctor_name'] = row[1]
            session['role'] = row[2] if row[2] else 'doctor'
            session.pop('pending_login', None)
            log_audit('login', 'session', row[0], f'User {row[1]} logged in')
            return redirect(url_for('doctor_reports'))
        else:
            return render_template('login.html', message='No account exists with that email. Please sign up.')
    else:
        return render_template('login.html', message='Invalid code. Try again.', show_verify=True)


@app.route('/logout')
def logout():
    session.pop('doctor_id', None)
    session.pop('doctor_name', None)
    return redirect(url_for('home'))


# ---------------------------
# Enhanced Data Visualization Utilities
# ---------------------------
from scipy import signal as scipy_signal
from scipy.fft import fft, fftfreq

def generate_synthetic_ecg(duration=10, sampling_rate=360, arrhythmia_type='normal', 
                          patient_hr=75, patient_qrs=90, patient_qt=400, patient_pr=160, 
                          patient_seed=None):
    """
    Generate patient-specific synthetic ECG signal with arrhythmia patterns
    duration: seconds
    sampling_rate: Hz (typical ECG is 250-500 Hz)
    arrhythmia_type: 'normal', 'afib', 'vt', 'pvc', etc.
    patient_hr: actual patient heart rate (bpm)
    patient_qrs: actual QRS duration (ms)
    patient_qt: actual QT interval (ms)
    patient_pr: actual PR interval (ms)
    patient_seed: seed for reproducibility per patient
    """
    # Use patient-specific seed for consistent but unique waveforms
    if patient_seed is not None:
        np.random.seed(patient_seed)
    
    t = np.linspace(0, duration, int(duration * sampling_rate))
    
    # Use actual patient heart rate, with variation for arrhythmias
    heart_rate = patient_hr if patient_hr > 0 else 75
    if arrhythmia_type == 'afib':
        # AFib has irregular rate - vary around patient's base
        heart_rate = patient_hr + np.random.randint(-20, 40)
    elif arrhythmia_type == 'vt':
        heart_rate = max(patient_hr + 60, 140)  # VT is fast
    
    # Convert patient measurements from ms to seconds
    qrs_duration_sec = (patient_qrs / 1000.0) if patient_qrs > 0 else 0.08
    pr_interval_sec = (patient_pr / 1000.0) if patient_pr > 0 else 0.16
    qt_interval_sec = (patient_qt / 1000.0) if patient_qt > 0 else 0.36
    
    # Base ECG waveform (simplified PQRST complex with patient parameters)
    ecg = np.zeros_like(t)
    rr_interval = 60.0 / heart_rate if heart_rate > 0 else 0.8
    
    beat_count = 0
    for beat_time in np.arange(0, duration, rr_interval):
        beat_count += 1
        
        # Add irregularity for AFib
        if arrhythmia_type == 'afib':
            beat_time += np.random.uniform(-0.15, 0.15)  # Irregular rhythm
        
        # P wave (unless AFib where P waves are absent/irregular)
        if arrhythmia_type != 'afib':
            p_center = beat_time
            p_width = 0.08
            p_amplitude = 0.15 + np.random.uniform(-0.03, 0.03)
            ecg += p_amplitude * np.exp(-((t - p_center) ** 2) / (2 * p_width ** 2))
        else:
            # Small irregular fibrillatory waves in AFib
            p_center = beat_time
            p_amplitude = np.random.uniform(0.02, 0.05)
            p_width = np.random.uniform(0.03, 0.06)
            ecg += p_amplitude * np.exp(-((t - p_center) ** 2) / (2 * p_width ** 2))
        
        # QRS complex - use patient's actual QRS width
        qrs_center = beat_time + pr_interval_sec
        qrs_width = qrs_duration_sec / 2.5  # Convert duration to width parameter
        
        # Adjust QRS width for VT (wide complex)
        if arrhythmia_type == 'vt':
            qrs_width *= 2.5  # Wide QRS in VT
        
        # QRS amplitude varies with patient condition
        qrs_amplitude = 1.5 + np.random.uniform(-0.2, 0.2)
        if arrhythmia_type == 'vt':
            qrs_amplitude *= 1.3  # Taller in VT
        
        ecg += qrs_amplitude * np.exp(-((t - qrs_center) ** 2) / (2 * qrs_width ** 2))
        
        # T wave - position based on patient's QT interval
        t_center = beat_time + qt_interval_sec
        t_width = 0.12 + np.random.uniform(-0.02, 0.02)
        t_amplitude = 0.3 + np.random.uniform(-0.05, 0.05)
        
        # T wave abnormalities in certain conditions
        if arrhythmia_type == 'vt':
            t_amplitude *= 0.7  # Reduced T wave
        
        ecg += t_amplitude * np.exp(-((t - t_center) ** 2) / (2 * t_width ** 2))
        
        # Occasional PVC if that's the type
        if arrhythmia_type == 'pvc' and beat_count % 4 == 0:
            pvc_center = beat_time + rr_interval * 0.6
            pvc_width = qrs_width * 2
            ecg += 1.8 * np.exp(-((t - pvc_center) ** 2) / (2 * pvc_width ** 2))
    
    # Add patient-specific noise level
    noise_level = 0.05 if arrhythmia_type == 'normal' else 0.08
    noise = np.random.normal(0, noise_level, len(t))
    ecg += noise
    
    # Reset random seed
    if patient_seed is not None:
        np.random.seed(None)
    
    return t, ecg

def compute_ecg_spectrogram(ecg_signal, sampling_rate=360):
    """
    Compute time-frequency spectrogram for ECG signal
    """
    f, t, Sxx = scipy_signal.spectrogram(ecg_signal, fs=sampling_rate, 
                                          nperseg=256, noverlap=200)
    # Convert to dB scale
    Sxx_db = 10 * np.log10(Sxx + 1e-10)
    return f, t, Sxx_db

def compute_arrhythmia_heatmap(ecg_signal, sampling_rate=360, window_size=180):
    """
    Compute heatmap showing abnormality scores across ECG signal
    Uses sliding window analysis
    """
    n_samples = len(ecg_signal)
    n_windows = n_samples // window_size
    
    heatmap_scores = []
    window_times = []
    
    for i in range(n_windows):
        start = i * window_size
        end = start + window_size
        window = ecg_signal[start:end]
        
        # Compute features: variance, peak count, irregularity
        variance_score = np.std(window) / (np.mean(np.abs(window)) + 1e-6)
        
        # Detect peaks
        peaks, _ = scipy_signal.find_peaks(window, height=0.5, distance=sampling_rate//4)
        peak_irregularity = np.std(np.diff(peaks)) if len(peaks) > 1 else 0
        
        # Combine scores
        abnormality_score = (variance_score * 0.4 + peak_irregularity * 0.6)
        heatmap_scores.append(abnormality_score)
        window_times.append(start / sampling_rate)
    
    return window_times, heatmap_scores

def get_ecg_data_from_report(report_id):
    """
    Fetch ECG data from a specific report
    Returns time and signal arrays
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT result_value FROM report WHERE id=?", (report_id,))
    row = cur.fetchone()
    conn.close()
    
    if row:
        # Parse stored ECG data if available
        # For now, generate synthetic based on diagnosis
        t, ecg = generate_synthetic_ecg(duration=10, arrhythmia_type='normal')
        return t.tolist(), ecg.tolist()
    return None, None


# ---------------------------
# Enhanced Visualization Routes
# ---------------------------
@app.route('/ecg_visualization')
def ecg_visualization():
    """
    Main interactive ECG visualization page
    """
    if 'doctor_id' not in session:
        return redirect(url_for('login'))
    
    # Get available reports for comparison
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT r.id, p.name, r.result, r.values_json, r.created_at 
        FROM report r 
        LEFT JOIN upload u ON r.upload_id = u.id
        LEFT JOIN patient p ON u.patient_id = p.id 
        WHERE p.name IS NOT NULL
        ORDER BY r.created_at DESC 
        LIMIT 50
    """)
    rows = cur.fetchall()
    conn.close()
    
    # Parse results to get readable labels
    reports = []
    for row in rows:
        report_id, patient_name, result_code, values_json, created_at = row
        # Parse result label from CLASS_LABELS or values_json
        try:
            if values_json:
                vals = json.loads(values_json)
                result_label = vals.get('Result', 'Unknown')
            else:
                result_label = CLASS_LABELS.get(result_code, 'Unknown')
        except:
            result_label = CLASS_LABELS.get(result_code, 'Unknown')
        reports.append((report_id, patient_name, result_label, created_at))
    
    
    return render_template('ecg_visualization.html', 
                          doctor_name=session.get('doctor_name'),
                          reports=reports)

@app.route('/api/ecg_data/<int:report_id>')
def api_ecg_data(report_id):
    """
    API endpoint to fetch ECG data for a specific report
    Returns JSON with time, signal, spectrogram, and heatmap data
    """
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT r.result, r.values_json
        FROM report r WHERE r.id=?
    """, (report_id,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return json.dumps({'error': 'Report not found'}), 404
    
    result_code, values_json = row
    
    # Parse values from JSON
    try:
        vals = json.loads(values_json) if values_json else {}
        result_label = vals.get('Result', CLASS_LABELS.get(result_code, 'Unknown'))
        # Use lowercase keys that match database storage
        qrs = vals.get('qrs', 0)
        qt = vals.get('qt', 0)
        pr = vals.get('pr', 0)
        hr = vals.get('heart_rate', 0)
    except:
        result_label = CLASS_LABELS.get(result_code, 'Unknown')
        qrs = qt = pr = hr = 0
    
    # Generate ECG signal (in production, load actual signal data)
    arrhythmia_map = {
        'Normal sinus rhythm': 'normal',
        'Atrial fibrillation': 'afib',
        'Ventricular tachycardia': 'vt',
        'Premature ventricular contraction': 'pvc'
    }
    arrhythmia_type = arrhythmia_map.get(result_label, 'normal')
    
    # Use patient-specific parameters for unique waveforms
    # Convert string values to numeric if needed
    try:
        hr_val = float(hr) if hr else 75
        qrs_val = float(qrs) if qrs else 90
        qt_val = float(qt) if qt else 400
        pr_val = float(pr) if pr else 160
    except:
        hr_val, qrs_val, qt_val, pr_val = 75, 90, 400, 160
    
    # Use report_id as seed for reproducible but unique waveforms
    t, ecg = generate_synthetic_ecg(duration=10, arrhythmia_type=arrhythmia_type,
                                   patient_hr=hr_val, patient_qrs=qrs_val,
                                   patient_qt=qt_val, patient_pr=pr_val,
                                   patient_seed=report_id)
    
    # Compute spectrogram
    f, t_spec, Sxx_db = compute_ecg_spectrogram(ecg, sampling_rate=360)
    
    # Compute heatmap
    window_times, heatmap_scores = compute_arrhythmia_heatmap(ecg, sampling_rate=360)
    
    return json.dumps({
        'time': t.tolist(),
        'signal': ecg.tolist(),
        'spectrogram': {
            'frequencies': f.tolist(),
            'times': t_spec.tolist(),
            'intensity': Sxx_db.tolist()
        },
        'heatmap': {
            'times': window_times,
            'scores': heatmap_scores
        },
        'metadata': {
            'label': result_label,
            'qrs': qrs,
            'qt': qt,
            'pr': pr,
            'hr': hr
        }
    })

@app.route('/api/ecg_compare')
def api_ecg_compare():
    """
    API endpoint to fetch ECG data for comparison
    """
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    
    report_a = request.args.get('report_a', type=int)
    report_b = request.args.get('report_b', type=int)
    
    if not report_a or not report_b:
        return json.dumps({'error': 'Missing report IDs'}), 400
    
    # Fetch both reports
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    data = {}
    for report_id, key in [(report_a, 'report_a'), (report_b, 'report_b')]:
        cur.execute("""
            SELECT r.result, r.values_json, p.name
            FROM report r 
            LEFT JOIN upload u ON r.upload_id = u.id
            LEFT JOIN patient p ON u.patient_id = p.id
            WHERE r.id=?
        """, (report_id,))
        row = cur.fetchone()
        
        if row:
            result_code, values_json, patient_name = row
            
            # Parse values from JSON
            try:
                vals = json.loads(values_json) if values_json else {}
                result_label = vals.get('Result', CLASS_LABELS.get(result_code, 'Unknown'))
                # Use lowercase keys that match database storage
                qrs = vals.get('qrs', 0)
                qt = vals.get('qt', 0)
                pr = vals.get('pr', 0)
                hr = vals.get('heart_rate', 0)
            except:
                result_label = CLASS_LABELS.get(result_code, 'Unknown')
                qrs = qt = pr = hr = 0
            
            arrhythmia_map = {
                'Normal sinus rhythm': 'normal',
                'Atrial fibrillation': 'afib',
                'Ventricular tachycardia': 'vt',
                'Premature ventricular contraction': 'pvc'
            }
            arrhythmia_type = arrhythmia_map.get(result_label, 'normal')
            
            # Use patient-specific parameters for unique waveforms
            try:
                hr_val = float(hr) if hr else 75
                qrs_val = float(qrs) if qrs else 90
                qt_val = float(qt) if qt else 400
                pr_val = float(pr) if pr else 160
            except:
                hr_val, qrs_val, qt_val, pr_val = 75, 90, 400, 160
            
            # Use report_id as seed for reproducible waveforms
            t, ecg = generate_synthetic_ecg(duration=10, arrhythmia_type=arrhythmia_type,
                                           patient_hr=hr_val, patient_qrs=qrs_val,
                                           patient_qt=qt_val, patient_pr=pr_val,
                                           patient_seed=report_id)
            
            data[key] = {
                'time': t.tolist(),
                'signal': ecg.tolist(),
                'label': result_label,
                'patient': patient_name or 'Unknown',
                'qrs': qrs,
                'qt': qt,
                'pr': pr,
                'hr': hr
            }
    
    conn.close()
    return json.dumps(data)


# ---------------------------
# Patient Management System Routes
# ---------------------------
@app.route('/patients')
def patients_page():
    """Unified Patients management page with tabs for Profile, Appointments, Prescriptions, Notes"""
    if 'doctor_id' not in session:
        return redirect(url_for('login'))
    selected_patient_id = request.args.get('patient_id', type=int)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT id, name FROM patient ORDER BY name ASC')
    patients = cur.fetchall()
    conn.close()
    # Default to first patient if none explicitly selected
    if selected_patient_id is None and patients:
        selected_patient_id = patients[0][0]
    return render_template(
        'patients.html',
        doctor_name=session.get('doctor_name'),
        patients=patients,
        selected_patient_id=selected_patient_id,
    )


# --- Patient Profile APIs ---
@app.route('/api/patient_profile/<int:patient_id>')
def api_patient_profile(patient_id: int):
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT medical_history, medications, comorbidities, allergies, updated_at FROM patient_detail WHERE patient_id=?', (patient_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return json.dumps({'medical_history': '', 'medications': '', 'comorbidities': '', 'allergies': '', 'updated_at': None})
    return json.dumps({'medical_history': row[0] or '', 'medications': row[1] or '', 'comorbidities': row[2] or '', 'allergies': row[3] or '', 'updated_at': row[4]})

@app.route('/patients/profile', methods=['POST'])
def save_patient_profile():
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    payload = request.form if request.form else request.json
    patient_id = int(payload.get('patient_id', 0))
    medical_history = (payload.get('medical_history') or '').strip()
    medications = (payload.get('medications') or '').strip()
    comorbidities = (payload.get('comorbidities') or '').strip()
    allergies = (payload.get('allergies') or '').strip()
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO patient_detail (patient_id, medical_history, medications, comorbidities, allergies, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
                (patient_id, medical_history, medications, comorbidities, allergies, now))
    conn.commit()
    conn.close()
    return json.dumps({'ok': True, 'updated_at': now})


# --- Appointments APIs ---
@app.route('/api/appointments')
def api_appointments():
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    patient_id = request.args.get('patient_id', type=int)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if patient_id:
        cur.execute('SELECT id, title, start_datetime, end_datetime, status FROM appointment WHERE patient_id=? ORDER BY start_datetime ASC', (patient_id,))
    else:
        cur.execute('SELECT id, title, start_datetime, end_datetime, status, patient_id FROM appointment ORDER BY start_datetime ASC')
    rows = cur.fetchall()
    conn.close()
    events = []
    for r in rows:
        if patient_id:
            _id, title, start_dt, end_dt, status = r
            events.append({'id': _id, 'title': title, 'start': start_dt, 'end': end_dt, 'status': status})
        else:
            _id, title, start_dt, end_dt, status, pid = r
            events.append({'id': _id, 'title': f"[{pid}] " + (title or 'Follow-up'), 'start': start_dt, 'end': end_dt, 'status': status, 'patient_id': pid})
    return json.dumps(events)

@app.route('/appointments', methods=['POST'])
def create_appointment():
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    payload = request.form if request.form else request.json
    patient_id = int(payload.get('patient_id', 0))
    title = (payload.get('title') or 'Follow-up').strip()
    start_dt = payload.get('start_datetime')
    end_dt = payload.get('end_datetime')
    status = (payload.get('status') or 'scheduled').strip()
    notes = (payload.get('notes') or '').strip()
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT INTO appointment (patient_id, title, start_datetime, end_datetime, status, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (patient_id, title, start_dt, end_dt, status, notes, now))
    conn.commit()
    conn.close()
    return json.dumps({'ok': True})


# --- Prescriptions APIs ---
@app.route('/api/prescriptions')
def api_prescriptions():
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    patient_id = request.args.get('patient_id', type=int)
    if not patient_id:
        return json.dumps([])
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT id, medication_name, dosage, frequency, start_date, end_date, instructions, created_at FROM prescription WHERE patient_id=? ORDER BY created_at DESC', (patient_id,))
    rows = cur.fetchall()
    conn.close()
    pres = []
    for r in rows:
        pres.append({
            'id': r[0], 'medication_name': r[1], 'dosage': r[2], 'frequency': r[3],
            'start_date': r[4], 'end_date': r[5], 'instructions': r[6], 'created_at': r[7]
        })
    return json.dumps(pres)

@app.route('/prescriptions', methods=['POST'])
def create_prescription():
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    payload = request.form if request.form else request.json
    patient_id = int(payload.get('patient_id', 0))
    medication_name = (payload.get('medication_name') or '').strip()
    dosage = (payload.get('dosage') or '').strip()
    frequency = (payload.get('frequency') or '').strip()
    start_date = payload.get('start_date')
    end_date = payload.get('end_date')
    instructions = (payload.get('instructions') or '').strip()
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT INTO prescription (patient_id, medication_name, dosage, frequency, start_date, end_date, instructions, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (patient_id, medication_name, dosage, frequency, start_date, end_date, instructions, now))
    conn.commit()
    conn.close()
    return json.dumps({'ok': True})


# --- Clinical Notes APIs ---
@app.route('/api/notes')
def api_notes():
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    patient_id = request.args.get('patient_id', type=int)
    if not patient_id:
        return json.dumps([])
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT id, content_html, created_at FROM clinical_note WHERE patient_id=? ORDER BY created_at DESC', (patient_id,))
    rows = cur.fetchall()
    conn.close()
    notes = [{'id': r[0], 'content_html': r[1], 'created_at': r[2]} for r in rows]
    return json.dumps(notes)

@app.route('/notes', methods=['POST'])
def create_note():
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    payload = request.form if request.form else request.json
    patient_id = int(payload.get('patient_id', 0))
    content_html = payload.get('content_html') or ''
    doctor_id = session.get('doctor_id')
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT INTO clinical_note (patient_id, doctor_id, content_html, created_at) VALUES (?, ?, ?, ?)', (patient_id, doctor_id, content_html, now))
    conn.commit()
    conn.close()
    log_audit('create_note', 'clinical_note', patient_id, f'Note added for patient {patient_id}')
    return json.dumps({'ok': True})

# ---------------------------
# Team & Access Control Routes
# ---------------------------
@app.route('/admin/assignments')
@require_role('admin', 'senior_doctor')
def admin_assignments():
    """Admin page to manage doctor-patient assignments"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT id, name, email, role FROM doctor ORDER BY name ASC')
    doctors = [dict(x) for x in cur.fetchall()]
    cur.execute('SELECT id, name FROM patient ORDER BY name ASC')
    patients = [dict(x) for x in cur.fetchall()]
    cur.execute('''SELECT a.id, d.name as doctor_name, p.name as patient_name, a.assigned_at
                   FROM doctor_patient_assignment a
                   JOIN doctor d ON a.doctor_id = d.id
                   JOIN patient p ON a.patient_id = p.id
                   ORDER BY a.assigned_at DESC LIMIT 100''')
    assignments = [dict(x) for x in cur.fetchall()]
    conn.close()
    return render_template('admin_assignments.html', doctors=doctors, patients=patients, assignments=assignments, doctor_name=session.get('doctor_name'))

@app.route('/api/assignments', methods=['POST'])
@require_role('admin', 'senior_doctor')
def create_assignment():
    """Assign patient to doctor"""
    payload = request.form if request.form else request.json
    doctor_id = int(payload.get('doctor_id', 0))
    patient_id = int(payload.get('patient_id', 0))
    assigned_by = session.get('doctor_id')
    now = datetime.utcnow().isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('INSERT OR IGNORE INTO doctor_patient_assignment (doctor_id, patient_id, assigned_by, assigned_at) VALUES (?, ?, ?, ?)',
                    (doctor_id, patient_id, assigned_by, now))
        conn.commit()
        conn.close()
        log_audit('assign_patient', 'assignment', patient_id, f'Assigned patient {patient_id} to doctor {doctor_id}')
        return json.dumps({'ok': True})
    except Exception as e:
        return json.dumps({'error': str(e)}), 500

@app.route('/api/assignments/<int:assignment_id>', methods=['DELETE'])
@require_role('admin', 'senior_doctor')
def delete_assignment(assignment_id: int):
    """Unassign patient from doctor"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('DELETE FROM doctor_patient_assignment WHERE id=?', (assignment_id,))
        conn.commit()
        conn.close()
        log_audit('unassign_patient', 'assignment', assignment_id, f'Removed assignment {assignment_id}')
        return json.dumps({'ok': True})
    except Exception as e:
        return json.dumps({'error': str(e)}), 500

@app.route('/api/my_patients')
def api_my_patients():
    """Get patients assigned to current doctor"""
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    doctor_id = session.get('doctor_id')
    role = session.get('role', 'doctor')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if role == 'admin':
        cur.execute('SELECT id, name FROM patient ORDER BY name ASC')
    else:
        cur.execute('''SELECT DISTINCT p.id, p.name FROM patient p
                       JOIN doctor_patient_assignment a ON p.id = a.patient_id
                       WHERE a.doctor_id = ?
                       ORDER BY p.name ASC''', (doctor_id,))
    patients = [dict(x) for x in cur.fetchall()]
    conn.close()
    return json.dumps(patients)

# ---------------------------
# Advanced Reporting Routes
# ---------------------------
@app.route('/api/report/<int:report_id>/comments')
def report_comments(report_id: int):
    """Get peer review comments for a report"""
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''SELECT c.id, c.comment_text, c.created_at, d.name as doctor_name
                   FROM report_comment c
                   JOIN doctor d ON c.doctor_id = d.id
                   WHERE c.report_id = ?
                   ORDER BY c.created_at DESC''', (report_id,))
    comments = [dict(x) for x in cur.fetchall()]
    conn.close()
    log_audit('view_comments', 'report', report_id, f'Viewed comments for report {report_id}')
    return json.dumps(comments)

@app.route('/api/report/<int:report_id>/comments', methods=['POST'])
def add_report_comment(report_id: int):
    """Add peer review comment to report"""
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    payload = request.form if request.form else request.json
    comment_text = payload.get('comment_text', '').strip()
    doctor_id = session.get('doctor_id')
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT INTO report_comment (report_id, doctor_id, comment_text, created_at) VALUES (?, ?, ?, ?)',
                (report_id, doctor_id, comment_text, now))
    conn.commit()
    conn.close()
    log_audit('add_comment', 'report', report_id, f'Added comment to report {report_id}')
    return json.dumps({'ok': True})

@app.route('/api/report/<int:report_id>/versions')
def report_versions(report_id: int):
    """Get version history for a report"""
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Unauthorized'}), 401
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''SELECT v.id, v.version, v.values_json, v.confidence, v.result, v.risk, v.created_at, d.name as changed_by_name
                   FROM report_version v
                   LEFT JOIN doctor d ON v.changed_by = d.id
                   WHERE v.report_id = ?
                   ORDER BY v.version DESC''', (report_id,))
    versions = [dict(x) for x in cur.fetchall()]
    conn.close()
    return json.dumps(versions)

@app.route('/admin/audit_logs')
@require_role('admin', 'senior_doctor')
def audit_logs():
    """View audit trail"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''SELECT a.id, a.action, a.resource_type, a.resource_id, a.details, a.ip_address, a.created_at, d.name as doctor_name
                   FROM audit_log a
                   LEFT JOIN doctor d ON a.doctor_id = d.id
                   ORDER BY a.created_at DESC
                   LIMIT ? OFFSET ?''', (per_page, offset))
    logs = [dict(x) for x in cur.fetchall()]
    cur.execute('SELECT COUNT(1) as cnt FROM audit_log')
    total = cur.fetchone()['cnt']
    conn.close()
    total_pages = (total + per_page - 1) // per_page
    return render_template('audit_logs.html', logs=logs, page=page, total_pages=total_pages, doctor_name=session.get('doctor_name'))

@app.route('/admin/templates')
@require_role('admin', 'senior_doctor')
def admin_templates():
    """Manage report templates"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT id, name, description, is_default, created_at FROM report_template ORDER BY created_at DESC')
    templates = [dict(x) for x in cur.fetchall()]
    conn.close()
    return render_template('admin_templates.html', templates=templates, doctor_name=session.get('doctor_name'))

@app.route('/api/templates', methods=['POST'])
@require_role('admin', 'senior_doctor')
def create_template():
    """Create custom report template"""
    payload = request.form if request.form else request.json
    name = payload.get('name', '').strip()
    description = payload.get('description', '').strip()
    template_html = payload.get('template_html', '').strip()
    created_by = session.get('doctor_id')
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT INTO report_template (name, description, template_html, created_by, created_at) VALUES (?, ?, ?, ?, ?)',
                (name, description, template_html, created_by, now))
    template_id = cur.lastrowid
    conn.commit()
    conn.close()
    log_audit('CREATE', 'report_template', template_id, f'Created template: {name}')
    return jsonify({'ok': True, 'template_id': template_id})

@app.route('/api/templates/<int:template_id>', methods=['GET'])
@require_role('admin', 'senior_doctor')
def get_template_details(template_id):
    """Get complete template details including HTML"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT id, name, description, template_html, is_default, created_at FROM report_template WHERE id = ?', (template_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return jsonify({'ok': True, 'template': dict(row)})
    return jsonify({'ok': False, 'error': 'Template not found'})

# ---------------------------
# Admin Utilities
# ---------------------------
@app.route('/admin/set_role/<int:doctor_id>/<role>')
def set_doctor_role(doctor_id: int, role: str):
    """Temporary utility to set doctor role - remove in production"""
    if role not in ['admin', 'senior_doctor', 'doctor', 'junior_doctor', 'technician']:
        return json.dumps({'error': 'Invalid role'}), 400
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('UPDATE doctor SET role = ? WHERE id = ?', (role, doctor_id))
    conn.commit()
    conn.close()
    return json.dumps({'ok': True, 'message': f'Role updated to {role}'})

@app.route('/admin/check_my_role')
def check_my_role():
    """Check current user's role"""
    if 'doctor_id' not in session:
        return json.dumps({'error': 'Not logged in'})
    doctor_id = session.get('doctor_id')
    role = session.get('role', 'doctor')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT id, name, email, role FROM doctor WHERE id = ?', (doctor_id,))
    user = dict(cur.fetchone()) if cur.fetchone() else None
    conn.close()
    return json.dumps({
        'session_role': role,
        'doctor_id': doctor_id,
        'doctor_name': session.get('doctor_name'),
        'db_user': user
    })

# ---------------------------
# Run App
# ---------------------------
if __name__ == '__main__':
    # Use host='0.0.0.0' if you want external access; for local testing default is fine
    app.run(debug=True)
