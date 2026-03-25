<p align="center">
  <img src="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSRkEYUvRPZbirllxcPoYzDdATZPdKoYGUkEw&s" alt="ECG Icon" width="880" />
</p>

<h1 align="center">Detection & Classification of Cardiac Arrhythmia</h1>

<p align="center">
  <strong>An AI-powered diagnostic tool for real-time ECG analysis and clinical reporting</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/github/license/Jaideep193/Detection_And_Classification_Of_Cardiac_Arrhythmia_Using_Machine_Learning?color=blue&style=for-the-badge" alt="License" />
  <img src="https://img.shields.io/badge/Python-3.8%2B-blue?style=for-the-badge&logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/Flask-Framework-red?style=for-the-badge&logo=flask" alt="Flask" />
  <img src="https://img.shields.io/github/stars/Jaideep193/Detection_And_Classification_Of_Cardiac_Arrhythmia_Using_Machine_Learning?style=for-the-badge" alt="Stars" />
</p>

---

## 📝 Project Overview

This repository implements a state-of-the-art **ECG Arrhythmia Detection and Classification** system. Designed for clinicians and medical researchers, it bridges the gap between raw signal data and actionable medical insights using advanced Machine Learning.

### 🌟 Key Features

| Feature | Description |
| :--- | :--- |
| **🔍 Multi-Source Input** | Support for ECG CSVs, high-res images, and manual physiological data. |
| **🧠 Weighted KNN Intelligence** | Classifies 13+ arrhythmia types with high precision using a pre-trained Weighted KNN model. |
| **📊 Signal Visualization** | Interactive Plotly dashboards for wave analysis (P, QRS, T waves). |
| **📄 Clinical Reports** | Automated PDF generation with patient data and risk assessment. |
| **👨‍⚕️ Doctor Portal** | Dedicated dashboard for patient management and appointment scheduling. |
| **🔔 Instant Alerts** | Integrated Telegram and Email notifications for high-risk cardiac events. |

---

## 🏥 Clinical Workflow

1. **Patient Intake**: Register patient data and record vital signs.
2. **ECG Analysis**: Upload raw ECG data (image/CSV) or enter manual readings.
3. **Model Inference**: AI extracts features and predicts the arrhythmia category.
4. **Review & Report**: Doctor reviews the findings and generates a clinical PDF.
5. **Follow-up**: System triggers alerts to relevant medical staff if necessary.

---

## 🎨 Methodology & System Architecture

### 📊 Methodology Flowchart
```mermaid
graph LR
    subgraph Data_Acquisition ["📥 Data Acquisition"]
        A[ECG Signal CSV]
        B[ECG Image]
        C[Manual Vitals]
    end

    subgraph Preprocessing ["⚙️ Preprocessing & Feature Extraction"]
        D{Input Type?}
        E[Signal Filtering & Noise Removal]
        F[Image Thresholding & Segmenting]
        G[Feature Vector Generation]
    end

    subgraph AI_Engine ["🧠 AI Inference Engine"]
        H[Weighted KNN Classifier]
        I[Arrhythmia Type Prediction]
        J[Risk Level Assessment]
    end

    subgraph Clinical_Output ["🏥 Clinical Output"]
        K[(SQLite Databases)]
        L[PDF Medical Report]
        M[Telegram/Email Alerts]
    end

    A & B & C -.-> D
    D -.->|Signal| E
    D -.->|Image| F
    D -.->|Manual| G
    E & F -.-> G
    G -.-> H
    H -.-> I -.-> J
    J -.-> K & L & M

    linkStyle default stroke:#333,stroke-width:2px,stroke-dasharray: 5;
    style Data_Acquisition fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Preprocessing fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style AI_Engine fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    style Clinical_Output fill:#fce4ec,stroke:#880e4f,stroke-width:2px
```

### 🏗️ System Architecture
```mermaid
graph LR
    subgraph Client ["🌐 Frontend"]
        UI[Browser Interface]
        Plotly[Plotly Viewer]
    end

    subgraph Server ["🚀 Backend (Flask)"]
        API[API Routes]
        Auth[Auth/Session]
    end

    subgraph Logic ["🧠 Processing & ML"]
        PP[Preprocessing]
        KNN[Weighted KNN]
        PDF[PDF Engine]
    end

    subgraph Data ["💾 Data & Storage"]
        DB[(SQL Databases)]
        FS[Asset Storage]
    end

    subgraph Notify ["📩 Notifications"]
        Tele[Telegram API]
        Mail[SMTP Email]
    end

    UI <-.-> API
    API -.-> Auth
    API -.-> PP
    PP -.-> KNN
    KNN -.-> PDF
    API -.-> DB
    API -.-> FS
    PDF -.-> FS
    API -.-> Notify

    linkStyle default stroke:#555,stroke-width:2px,stroke-dasharray: 3;
    style Client fill:#e8f5e9,stroke:#2e7d32
    style Server fill:#fffde7,stroke:#fbc02d
    style Logic fill:#efebe9,stroke:#4e342e
    style Data fill:#f3e5f5,stroke:#7b1fa2
    style Notify fill:#e0f7fa,stroke:#006064
```

---

## 🛠️ Technology Stack

- **Backend**: Flask (Python 3.8+)
- **Database**: SQLite3 (Distributed `hospital.db` and `user_data.db`)
- **Machine Learning**: Scikit-Learn (Weighted KNN), NumPy, SciPy, Pandas
- **Image Analysis**: OpenCV, Matplotlib
- **Frontend**: HTML5, CSS3, JavaScript (Plotly.js for signals)
- **Reporting**: ReportLab (Professional PDF Engine)
- **Communication**: SMTP (Email), Telegram Bot API

---

## 📂 Project Structure

- `app.py`: Main Flask application containing all routes and logic.
- `NOTEBOOK_FILES/`: Contains the pre-trained `model.pkl`.
- `datasets/ecg_images/`: Directory for ECG image data.
- `static/`: Assets including CSS, images, and generated reports.
- `templates/`: Jinja2 templates for the clinical web interface.
- `requirements.txt`: Python dependencies.

---

## 🚀 Future Roadmap

- **☁️ Cloud Integration**: Sync patient reports with Azure/AWS Health.
- **📱 Mobile App**: Flutter-based companion for real-time patient monitoring.
- **⏱️ Real-time IoT**: Support for live ECG streaming from wearable sensors.
- **🤖 LLM Summaries**: Automated clinical notes generation using Gemini/GPT-4.

---

**Developed with ❤️ by Jaideep**  
*Empowering Healthcare with Intelligent Machine Learning*
