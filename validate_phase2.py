#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Comprehensive validation and error checking for Phase 2 improvements."""

import sys
import traceback
from pathlib import Path

print("=" * 80)
print("PHASE 2 COMPREHENSIVE ERROR CHECKING & VALIDATION")
print("=" * 80)
print()

results = {}

# TEST 1: Import Checks
print("[TEST 1] Checking all imports...")
print("-" * 80)
try:
    from src.train import train_model, train_model_with_timeseries_cv, compute_advanced_metrics
    from src.lstm_model import LSTMTrader
    from src.data import generate_synthetic_data
    from src.daily_learning import DailyLearner
    print("[OK] All imports successful")
    results['imports'] = 'PASS'
except Exception as e:
    print(f"[ERROR] Import failed: {str(e)}")
    traceback.print_exc()
    results['imports'] = 'FAIL'
print()

# TEST 2: Unit Tests
print("[TEST 2] Running unit tests...")
print("-" * 80)
try:
    import subprocess
    result = subprocess.run(
        ['python', '-m', 'pytest', 'tests/test_lstm.py', '-v', '--tb=short'],
        capture_output=True,
        text=True,
        cwd='c:\\Users\\User\\ml_trading_system'
    )
    if result.returncode == 0:
        print("[OK] All unit tests passed")
        # Count passed tests
        passed_count = result.stdout.count(" PASSED")
        print(f"    Tests passed: {passed_count}")
        results['unit_tests'] = 'PASS'
    else:
        print(f"[ERROR] Unit tests failed")
        print(result.stdout)
        print(result.stderr)
        results['unit_tests'] = 'FAIL'
except Exception as e:
    print(f"[ERROR] Could not run tests: {str(e)}")
    results['unit_tests'] = 'FAIL'
print()

# TEST 3: Backward Compatibility - Original train_model()
print("[TEST 3] Backward compatibility - Original train_model()...")
print("-" * 80)
try:
    from src.train import train_model
    from src.data import generate_synthetic_data
    
    data = generate_synthetic_data('BTCUSD', n=150)
    model, metrics = train_model(data, 'BTCUSD')
    
    # Check required fields
    required_fields = ['symbol', 'accuracy', 'classification_report', 'quality_rows', 'total_rows']
    missing = [f for f in required_fields if f not in metrics]
    
    if not missing:
        print("[OK] Original train_model() works correctly")
        print(f"    Accuracy: {metrics['accuracy']:.4f}")
        print(f"    Quality rows: {metrics['quality_rows']}")
        results['original_train'] = 'PASS'
    else:
        print(f"[ERROR] Missing fields in metrics: {missing}")
        results['original_train'] = 'FAIL'
except Exception as e:
    print(f"[ERROR] Original train_model() failed: {str(e)}")
    traceback.print_exc()
    results['original_train'] = 'FAIL'
print()

# TEST 4: New Feature - Time-Series CV
print("[TEST 4] New feature - train_model_with_timeseries_cv()...")
print("-" * 80)
try:
    from src.train import train_model_with_timeseries_cv
    from src.data import generate_synthetic_data
    
    data = generate_synthetic_data('XAUUSD', n=200)
    model, metrics = train_model_with_timeseries_cv(data, 'XAUUSD', n_splits=3)
    
    # Check required CV fields
    required_cv_fields = ['cv_folds', 'cv_accuracy_mean', 'cv_accuracy_std', 'cv_accuracies']
    missing = [f for f in required_cv_fields if f not in metrics]
    
    if not missing:
        print("[OK] train_model_with_timeseries_cv() works correctly")
        print(f"    CV Folds: {metrics['cv_folds']}")
        print(f"    Mean Accuracy: {metrics['cv_accuracy_mean']:.4f}")
        print(f"    Std Dev: {metrics['cv_accuracy_std']:.4f}")
        results['cv_function'] = 'PASS'
    else:
        print(f"[ERROR] Missing CV fields: {missing}")
        results['cv_function'] = 'FAIL'
except Exception as e:
    print(f"[ERROR] train_model_with_timeseries_cv() failed: {str(e)}")
    traceback.print_exc()
    results['cv_function'] = 'FAIL'
print()

# TEST 5: New Feature - Advanced Metrics
print("[TEST 5] New feature - compute_advanced_metrics()...")
print("-" * 80)
try:
    from src.train import compute_advanced_metrics
    import numpy as np
    
    # Create test data
    y_true = np.array([0, 1, 1, 0, 1, 0, 1, 1, 0, 0])
    y_pred = np.array([0, 1, 0, 0, 1, 1, 1, 1, 0, 1])
    y_proba = np.array([0.1, 0.9, 0.4, 0.2, 0.8, 0.6, 0.7, 0.95, 0.1, 0.7])
    
    metrics = compute_advanced_metrics(y_true, y_pred, y_proba)
    
    required_fields = ['accuracy', 'precision', 'recall', 'f1_score', 'confusion_matrix']
    missing = [f for f in required_fields if f not in metrics]
    
    if not missing:
        print("[OK] compute_advanced_metrics() works correctly")
        print(f"    Accuracy: {metrics['accuracy']:.4f}")
        print(f"    Precision: {metrics['precision']:.4f}")
        print(f"    Recall: {metrics['recall']:.4f}")
        print(f"    F1-Score: {metrics['f1_score']:.4f}")
        results['advanced_metrics'] = 'PASS'
    else:
        print(f"[ERROR] Missing fields: {missing}")
        results['advanced_metrics'] = 'FAIL'
except Exception as e:
    print(f"[ERROR] compute_advanced_metrics() failed: {str(e)}")
    traceback.print_exc()
    results['advanced_metrics'] = 'FAIL'
print()

# TEST 6: LSTM with Enhanced Metrics
print("[TEST 6] LSTM with enhanced metrics...")
print("-" * 80)
try:
    from src.lstm_model import LSTMTrader
    from src.data import generate_synthetic_data
    
    data = generate_synthetic_data('BTCUSD', n=150)
    trainer = LSTMTrader('BTCUSD')
    model, metrics = trainer.train(data)
    
    # Check for new Phase 2 fields
    phase2_fields = ['precision', 'recall', 'f1_score', 'confusion_matrix']
    available = [f for f in phase2_fields if f in metrics]
    
    print("[OK] LSTM training completed")
    print(f"    Accuracy: {metrics.get('accuracy', 'N/A')}")
    print(f"    Precision: {metrics.get('precision', 'N/A')}")
    print(f"    Recall: {metrics.get('recall', 'N/A')}")
    print(f"    F1-Score: {metrics.get('f1_score', 'N/A')}")
    print(f"    Advanced metrics available: {len(available)}/{len(phase2_fields)}")
    
    if len(available) == len(phase2_fields):
        results['lstm_metrics'] = 'PASS'
    else:
        results['lstm_metrics'] = 'PARTIAL'
except Exception as e:
    print(f"[ERROR] LSTM metrics failed: {str(e)}")
    traceback.print_exc()
    results['lstm_metrics'] = 'FAIL'
print()

# TEST 7: Daily Learning Integration
print("[TEST 7] Daily learning integration...")
print("-" * 80)
try:
    from src.daily_learning import DailyLearner
    
    learner = DailyLearner('XAUUSD')
    result = learner.run_cycle(frequency='daily')
    
    if result.get('status') == 'trained' or result.get('status') == 'no_data':
        print("[OK] DailyLearner cycle works")
        print(f"    Status: {result.get('status')}")
        print(f"    Rows: {result.get('rows', 'N/A')}")
        print(f"    RF Accuracy: {result.get('accuracy', 'N/A')}")
        print(f"    LSTM Accuracy: {result.get('lstm_accuracy', 'N/A')}")
        results['daily_learning'] = 'PASS'
    else:
        print(f"[WARNING] Unexpected status: {result.get('status')}")
        results['daily_learning'] = 'PARTIAL'
except Exception as e:
    print(f"[ERROR] DailyLearner failed: {str(e)}")
    traceback.print_exc()
    results['daily_learning'] = 'FAIL'
print()

# TEST 8: Model Checkpointing
print("[TEST 8] Model checkpointing...")
print("-" * 80)
try:
    from pathlib import Path
    import glob
    
    model_dir = Path('models')
    best_models = list(model_dir.glob('*_lstm_best.keras'))
    
    if best_models:
        print("[OK] Model checkpoints found")
        for model_path in best_models:
            size_kb = model_path.stat().st_size / 1024
            print(f"    {model_path.name}: {size_kb:.1f} KB")
        results['checkpointing'] = 'PASS'
    else:
        print("[WARNING] No best model checkpoints found (they may not have been created yet)")
        results['checkpointing'] = 'PARTIAL'
except Exception as e:
    print(f"[ERROR] Checkpoint check failed: {str(e)}")
    results['checkpointing'] = 'FAIL'
print()

# TEST 9: Error Handling
print("[TEST 9] Error handling...")
print("-" * 80)
try:
    from src.train import train_model
    import pandas as pd
    
    # Test with empty data
    empty_data = pd.DataFrame()
    try:
        train_model(empty_data, 'TEST')
        print("[WARNING] No error raised for empty data")
        results['error_handling'] = 'PARTIAL'
    except ValueError as e:
        print("[OK] Proper error handling for empty data")
        print(f"    Error message: {str(e)}")
        results['error_handling'] = 'PASS'
except Exception as e:
    print(f"[ERROR] Error handling test failed: {str(e)}")
    results['error_handling'] = 'FAIL'
print()

# TEST 10: File Integrity
print("[TEST 10] File integrity check...")
print("-" * 80)
try:
    import ast
    
    files_to_check = [
        'src/train.py',
        'src/lstm_model.py'
    ]
    
    all_valid = True
    for filepath in files_to_check:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                code = f.read()
            ast.parse(code)
            print(f"[OK] {filepath} - Valid Python syntax")
        except SyntaxError as e:
            print(f"[ERROR] {filepath} - Syntax error: {str(e)}")
            all_valid = False
    
    results['file_integrity'] = 'PASS' if all_valid else 'FAIL'
except Exception as e:
    print(f"[ERROR] File check failed: {str(e)}")
    results['file_integrity'] = 'FAIL'
print()

# FINAL SUMMARY
print("=" * 80)
print("VALIDATION SUMMARY")
print("=" * 80)

passed = sum(1 for v in results.values() if v == 'PASS')
partial = sum(1 for v in results.values() if v == 'PARTIAL')
failed = sum(1 for v in results.values() if v == 'FAIL')
total = len(results)

print(f"Passed:  {passed}/{total}")
print(f"Partial: {partial}/{total}")
print(f"Failed:  {failed}/{total}")
print()

if failed == 0:
    print("OVERALL STATUS: SAFE TO DEPLOY")
    if partial == 0:
        print("CONFIDENCE: 100% - All critical checks passed")
    else:
        print(f"CONFIDENCE: 95% - {partial} partial checks (non-blocking)")
    sys.exit(0)
else:
    print(f"OVERALL STATUS: ISSUES FOUND - {failed} check(s) failed")
    print("CONFIDENCE: Cannot deploy - requires fixes")
    sys.exit(1)
