#!/usr/bin/env python3
"""Train RF/XGB on ML features dataset."""
import sys, os, warnings
warnings.filterwarnings('ignore')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

print("Loading dataset...")
ds = pd.read_parquet('reports/ml_features_GL.parquet')

# Features: всё кроме target и первых OHLCV+OI (сырые значения)
exclude = ['open','high','low','close','volume','fiz_buy','fiz_sell',
           'yur_buy','yur_sell','total_oi','target','atr14','adx14']
feature_cols = [c for c in ds.columns if c not in exclude]
print(f"Features: {len(feature_cols)}")

X = ds[feature_cols].values.astype(np.float32)
# Replace inf/nan
X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
y = ds['target'].values.astype(int)

# Chronological split: train 2023-2024, test 2025-2026
ds['year'] = ds.index.year
train_mask = ds['year'] <= 2024
test_mask = ds['year'] >= 2025

X_train, y_train = X[train_mask], y[train_mask]
X_test, y_test = X[test_mask], y[test_mask]

print(f"Train: {len(y_train)} ({y_train.sum()} targets)")
print(f"Test:  {len(y_test)} ({y_test.sum()} targets)")

# Random Forest
rf = RandomForestClassifier(
    n_estimators=300, max_depth=12, min_samples_leaf=5,
    class_weight='balanced', random_state=42, n_jobs=-1
)
rf.fit(X_train, y_train)

y_pred = rf.predict(X_test)
y_proba = rf.predict_proba(X_test)[:, 1]

print(f"\n{'='*60}")
print("  RANDOM FOREST — TEST 2025-2026")
print(f"{'='*60}")
print(classification_report(y_test, y_pred, target_names=['no_trade', 'trade']))
print(f"ROC-AUC: {roc_auc_score(y_test, y_proba):.3f}")

cm = confusion_matrix(y_test, y_pred)
tn, fp, fn, tp = cm.ravel()
print(f"\nConfusion Matrix:")
print(f"  TN={tn} FP={fp}")
print(f"  FN={fn} TP={tp}")
precision = tp / (tp + fp) if (tp+fp) > 0 else 0
recall = tp / (tp + fn) if (tp+fn) > 0 else 0
f1 = 2 * precision * recall / (precision + recall) if (precision+recall) > 0 else 0
print(f"\n  Precision: {precision:.3f}")
print(f"  Recall:    {recall:.3f}")
print(f"  F1:        {f1:.3f}")
print(f"  WR (if trade when TP>0.5 proba): {tp/(tp+fp)*100:.1f}%" if (tp+fp) > 0 else "  No trades predicted")

# Feature importance top-20
importances = pd.DataFrame({
    'feature': feature_cols,
    'importance': rf.feature_importances_
}).sort_values('importance', ascending=False)
print(f"\n  TOP-20 FEATURES:")
for i, (feat, imp) in enumerate(zip(importances['feature'][:20], importances['importance'][:20])):
    print(f"  {i+1:2d}. {feat:30s} {imp:.4f}")

# XGBoost
try:
    import xgboost as xgb
    dtrain = xgb.DMatrix(X_train, label=y_train)
    dtest = xgb.DMatrix(X_test, label=y_test)
    
    params = {
        'max_depth': 6, 'eta': 0.05, 'subsample': 0.8,
        'colsample_bytree': 0.8, 'scale_pos_weight': (len(y_train)-y_train.sum())/y_train.sum(),
        'eval_metric': 'auc', 'objective': 'binary:logistic',
        'seed': 42, 'nthread': -1
    }
    model = xgb.train(params, dtrain, num_boost_round=300,
                      evals=[(dtest, 'test')], verbose_eval=0,
                      early_stopping_rounds=30)
    
    y_pred_xgb = (model.predict(dtest) > 0.5).astype(int)
    y_proba_xgb = model.predict(dtest)
    
    print(f"\n{'='*60}")
    print("  XGBOOST — TEST 2025-2026")
    print(f"{'='*60}")
    print(classification_report(y_test, y_pred_xgb, target_names=['no_trade', 'trade']))
    print(f"ROC-AUC: {roc_auc_score(y_test, y_proba_xgb):.3f}")
    
    cm_xgb = confusion_matrix(y_test, y_pred_xgb)
    tn, fp, fn, tp = cm_xgb.ravel()
    precision_xgb = tp / (tp + fp) if (tp+fp) > 0 else 0
    recall_xgb = tp / (tp + fn) if (tp+fn) > 0 else 0
    print(f"\n  Precision: {precision_xgb:.3f}")
    print(f"  Recall:    {recall_xgb:.3f}")
    print(f"  WR: {tp/(tp+fp)*100:.1f}%" if (tp+fp) > 0 else "  No trades predicted")
    
    # XGB feature importance
    imp_xgb = pd.DataFrame({
        'feature': feature_cols,
        'importance': model.get_score(importance_type='gain')
    }).fillna(0).sort_values('importance', ascending=False)
    print(f"\n  TOP-20 XGB FEATURES (by gain):")
    for i, (feat, imp) in enumerate(zip(imp_xgb['feature'][:20], imp_xgb['importance'][:20])):
        print(f"  {i+1:2d}. {feat:30s} {imp:.4f}")
except ImportError:
    print("\nXGBoost not installed, skipping")
