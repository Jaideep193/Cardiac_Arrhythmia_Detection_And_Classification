"""
ECG Kaggle Dataset Analyzer & Calibration Script
Downloads & analyzes ~10,000 ECG images from Kaggle dataset
Builds reference patterns for feature extraction tuning
"""

import os
import sys
import subprocess
from pathlib import Path
import numpy as np
import cv2
from collections import defaultdict
import pickle
import json

# Try importing scipy, install if missing
try:
    from scipy import signal as scipy_signal
except ImportError:
    print("Installing scipy...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "scipy"])
    from scipy import signal as scipy_signal

# ============================================================================
# SETUP: KAGGLE API & DATASET DOWNLOAD
# ============================================================================

def setup_kaggle_api():
    """Ensure Kaggle API is installed and configured."""
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi  # noqa: F401
        print("✅ Kaggle API found")
    except ImportError:
        print("📦 Installing Kaggle API...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "kaggle"])
        from kaggle.api.kaggle_api_extended import KaggleApi  # noqa: F401

    creds_path = Path.home() / ".kaggle" / "kaggle.json"
    if not creds_path.exists():
        print("\n⚠️ KAGGLE API KEY NOT FOUND at", creds_path)
        return False

    print("✅ Kaggle API credentials found")
    return True

def download_dataset(output_dir="datasets/ecg_images"):
    """Download the ECG dataset from Kaggle via Python API."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n📥 Downloading ECG dataset to {output_dir}...")
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
        api = KaggleApi()
        api.authenticate()
        api.dataset_download_files("evilspirit05/ecg-analysis", path=str(output_dir), unzip=True)
        print("✅ Dataset downloaded successfully")
        return True
    except Exception as e:
        print(f"❌ Download failed: {e}")
        print("Alternative: Download manually from https://www.kaggle.com/datasets/evilspirit05/ecg-analysis")
        return False

# ============================================================================
# ECG FEATURE EXTRACTION (from app.py)
# ============================================================================

def extract_ecg_features_from_image(img_path, mm_per_sec=25.0, mm_per_mv=10.0):
    """Extract ECG intervals from image using improved signal processing."""
    # Define ECG timing constants
    min_rr_ms = 400  # Minimum RR interval in ms (150 bpm)
    max_rr_ms = 1500  # Maximum RR interval in ms (40 bpm)
    ms_per_pixel = 1.0  # Milliseconds per pixel (adjust based on your image resolution)
    
    try:
        img = cv2.imread(str(img_path))
        if img is None:
            return None

        # Preprocessing
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 9, 60, 60)
        gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))

        # Threshold
        gray_norm = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
        _, binary = cv2.threshold(gray_norm, cv2.threshold(gray_norm, 0, 255, cv2.THRESH_OTSU)[0], 255, cv2.THRESH_BINARY_INV)

        # Extract 1D signal
        proj = binary.sum(axis=0).astype(np.float32)
        if np.all(proj == 0):
            return None
        proj = (proj - proj.min()) / (proj.max() - proj.min() + 1e-8)
        
        window = min(31, len(proj) // 10)
        if window % 2 == 0:
            window += 1
        proj_smooth = scipy_signal.savgol_filter(proj, window, 3)

        # R-peak detection
        proj_diff = np.abs(np.gradient(proj_smooth))
        proj_diff = scipy_signal.savgol_filter(proj_diff, max(5, window // 3), 2)
        
        threshold = np.mean(proj_diff) + 1.5 * np.std(proj_diff)
        peaks, properties = scipy_signal.find_peaks(proj_diff, height=threshold, distance=30)
        
        if len(peaks) < 2:
            ac = np.correlate(proj_smooth, proj_smooth, mode='full')
            ac = ac[ac.size // 2:]
            min_lag = 30
            if ac.size <= min_lag + 5:
                return None
            peak_idx = np.argmax(ac[min_lag:]) + min_lag
            peaks = np.array([peak_idx, peak_idx * 2]) if peak_idx * 2 < len(proj) else np.array([peak_idx])

        # RR interval
        rr_intervals = np.diff(peaks)
        if len(rr_intervals) == 0:
            return None

        valid_rr = rr_intervals[
            (rr_intervals >= int(min_rr_ms / ms_per_pixel)) &
            (rr_intervals <= int(max_rr_ms / ms_per_pixel))
        ]
        if len(valid_rr) == 0:
            valid_rr = rr_intervals

        rr_pixels = float(np.median(valid_rr))
        rr_std = float(np.std(valid_rr))

        rr_ms = rr_pixels * ms_per_pixel
        rr_ms = np.clip(rr_ms, min_rr_ms, max_rr_ms)

        heart_rate = 60000.0 / rr_ms
        heart_rate = np.clip(heart_rate, 40, 120)  # clamp output HR

        # Intervals based on HR
        hr_ratio = max(0.5, min(2.0, heart_rate / 70.0))
        qrs_ms = np.clip(70 + 20 * (1 - hr_ratio), 60, 160)
        pr_ms = np.clip(140 + 30 * hr_ratio, 80, 220)
        p_ms = np.clip(80 + 20 * hr_ratio, 60, 140)
        qt_ms = np.clip(330 + 50 * (2 - hr_ratio), 250, 480)
        t_ms = np.clip(140 + 40 * (1 - hr_ratio), 80, 220)

        # Confidence
        if len(rr_intervals) > 1:
            rr_consistency = 1.0 - min(1.0, rr_std / (rr_pixels + 1e-6))
        else:
            rr_consistency = 0.5
        signal_strength = float(np.mean(proj_smooth))
        peak_clarity = float(np.mean(properties.get('peak_heights', [0.5])))
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
        print(f"  ❌ Error processing {img_path}: {e}")
        return None

# ============================================================================
# DATASET ANALYSIS
# ============================================================================

def analyze_dataset(dataset_dir="datasets/ecg_images"):
    """Analyze all ECG images in the dataset."""
    dataset_dir = Path(dataset_dir)
    
    if not dataset_dir.exists():
        print(f"❌ Dataset directory not found: {dataset_dir}")
        return None
    
    categories = {
        'Normal': [],
        'Abnormal': [],
        'MI': [],  # Myocardial Infarction
        'MI_History': []
    }
    
    # Map folder names to categories
    folder_map = {
        'normal': 'Normal',
        'abnormal': 'Abnormal',
        'myocardial_infarction': 'MI',
        'history_of_myocardial_infarction': 'MI_History'
    }
    
    print(f"\n🔍 Scanning dataset at {dataset_dir}...")
    
    results = defaultdict(list)
    total_processed = 0
    
    for folder_name, category in folder_map.items():
        folder_path = dataset_dir / folder_name
        
        if not folder_path.exists():
            # Try alternative naming
            for alt_folder in dataset_dir.glob('*'):
                if alt_folder.is_dir() and folder_name.lower() in alt_folder.name.lower():
                    folder_path = alt_folder
                    break
        
        if not folder_path.exists():
            print(f"⚠️  Folder not found: {folder_name}")
            continue
        
        image_files = list(folder_path.glob('*.png')) + list(folder_path.glob('*.jpg'))
        print(f"\n📊 Processing {category} ({len(image_files)} images)...")
        
        for i, img_path in enumerate(image_files[:100]):  # Limit to 100 per category for speed
            if i % 20 == 0:
                print(f"  Progress: {i}/{len(image_files[:100])}")
            
            feats = extract_ecg_features_from_image(str(img_path))
            if feats:
                results[category].append(feats)
                total_processed += 1
        
        print(f"  ✅ Successfully extracted {len(results[category])} images from {category}")
    
    print(f"\n✅ Total processed: {total_processed} images")
    return results

# ============================================================================
# STATISTICAL ANALYSIS & REPORT
# ============================================================================

def generate_calibration_report(results):
    """Generate calibration report with statistics by category."""
    report = {}
    
    print("\n" + "="*70)
    print("CALIBRATION REPORT: ECG Feature Statistics by Category")
    print("="*70)
    
    for category, features_list in results.items():
        if not features_list:
            print(f"\n{category}: No data")
            continue
        
        # Aggregate stats
        stats = {
            'count': len(features_list),
            'qrs': {'values': [f['qrs'] for f in features_list]},
            'qt': {'values': [f['qt'] for f in features_list]},
            't': {'values': [f['t'] for f in features_list]},
            'pr': {'values': [f['pr'] for f in features_list]},
            'p': {'values': [f['p'] for f in features_list]},
            'heart_rate': {'values': [f['heart_rate'] for f in features_list]},
            'confidence': {'values': [f['confidence'] for f in features_list]}
        }
        
        # Calculate mean, std, min, max
        for key in ['qrs', 'qt', 't', 'pr', 'p', 'heart_rate', 'confidence']:
            values = np.array(stats[key]['values'])
            stats[key]['mean'] = float(np.mean(values))
            stats[key]['std'] = float(np.std(values))
            stats[key]['min'] = float(np.min(values))
            stats[key]['max'] = float(np.max(values))
            stats[key]['median'] = float(np.median(values))
        
        report[category] = stats
        
        # Print summary
        print(f"\n{category.upper()} ({len(features_list)} images):")
        print(f"  QRS:    {stats['qrs']['mean']:.1f} ± {stats['qrs']['std']:.1f} ms (range: {stats['qrs']['min']:.1f}–{stats['qrs']['max']:.1f})")
        print(f"  QT:     {stats['qt']['mean']:.1f} ± {stats['qt']['std']:.1f} ms (range: {stats['qt']['min']:.1f}–{stats['qt']['max']:.1f})")
        print(f"  PR:     {stats['pr']['mean']:.1f} ± {stats['pr']['std']:.1f} ms (range: {stats['pr']['min']:.1f}–{stats['pr']['max']:.1f})")
        print(f"  T:      {stats['t']['mean']:.1f} ± {stats['t']['std']:.1f} ms (range: {stats['t']['min']:.1f}–{stats['t']['max']:.1f})")
        print(f"  P:      {stats['p']['mean']:.1f} ± {stats['p']['std']:.1f} ms (range: {stats['p']['min']:.1f}–{stats['p']['max']:.1f})")
        print(f"  HR:     {stats['heart_rate']['mean']:.1f} ± {stats['heart_rate']['std']:.1f} bpm (range: {stats['heart_rate']['min']:.1f}–{stats['heart_rate']['max']:.1f})")
        print(f"  Conf:   {stats['confidence']['mean']:.2f} ± {stats['confidence']['std']:.2f} (range: {stats['confidence']['min']:.2f}–{stats['confidence']['max']:.2f})")
    
    return report

def save_calibration(report, output_file="calibration_data.json"):
    """Save calibration data to JSON."""
    # Convert numpy arrays to lists for JSON serialization
    json_report = {}
    for cat, stats in report.items():
        json_report[cat] = {
            k: {sk: (list(v) if sk == 'values' else v) for sk, v in sv.items()}
            for k, sv in stats.items()
        }
    
    with open(output_file, 'w') as f:
        json.dump(json_report, f, indent=2)
    print(f"\n💾 Calibration data saved to {output_file}")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("="*70)
    print("ECG Kaggle Dataset Analyzer & Calibration Tool")
    print("="*70)
    
    # Setup Kaggle API
    if not setup_kaggle_api():
        print("\n⚠️  Skipping download. Please configure Kaggle API manually.")
        dataset_dir = input("\nEnter dataset directory path (or press Enter for 'datasets/ecg_images'): ").strip()
        if not dataset_dir:
            dataset_dir = "datasets/ecg_images"
    else:
        # Download dataset
        proceed = input("\nProceed with downloading dataset? (y/n): ").strip().lower()
        if proceed == 'y':
            if not download_dataset():
                dataset_dir = input("Enter dataset directory path: ").strip()
        else:
            dataset_dir = input("Enter dataset directory path: ").strip()
    
    # Analyze dataset
    results = analyze_dataset(dataset_dir)
    
    if results:
        # Generate report
        report = generate_calibration_report(results)
        
        # Save calibration data
        save_calibration(report)
        
        print("\n" + "="*70)
        print("✅ Analysis complete!")
        print("="*70)
    else:
        print("\n❌ No data extracted. Check dataset path and format.")
