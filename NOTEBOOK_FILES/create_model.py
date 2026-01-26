import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import accuracy_score
import pickle

# ---------------- Load Dataset ----------------
df = pd.read_csv('test1.csv')

# Feature selection
features = df[["Age","Sex","Height","Weight","qrs","q_t","t","p_r","p","heart_rate"]]
target = df["diagnosis"]

# Clean data
features = features.replace([np.inf, -np.inf], np.nan)
features = features.apply(pd.to_numeric, errors='coerce')

# Impute missing values
imp = SimpleImputer(strategy='median')
features = imp.fit_transform(features)

# Train-test split
X_train, X_test, y_train, y_test = train_test_split(
    features, target, test_size=0.2, random_state=100
)

# ---------------- Train Weighted KNN ----------------
pipeline = Pipeline([
    ('scaler', StandardScaler()),
    ('clf', KNeighborsClassifier(
        n_neighbors=13,
        weights='distance'      # Weighted KNN
    ))
])

pipeline.fit(X_train, y_train)

y_pred = pipeline.predict(X_test)
print("Accuracy:", accuracy_score(y_test, y_pred))

with open('model.pkl', 'wb') as f:
    pickle.dump(pipeline, f)

print("\nModel saved successfully!")

# ---------------- Verify ----------------
loaded = pickle.load(open('model.pkl', 'rb'))
print("Input feature count:", loaded.named_steps["scaler"].n_features_in_)
print("KNN weighting:", loaded.named_steps["clf"].weights)
print("======================================")
print(" Model Evaluation")
print("======================================")
print(f"Accuracy: {accuracy_score(y_test, loaded.predict(X_test))}")