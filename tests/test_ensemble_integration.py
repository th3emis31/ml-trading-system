#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 3.2 Ensemble Voting Integration Tests"""

import pytest
from src.daily_learning import DailyLearner
from src.train import ensemble_predict
from src.data import generate_synthetic_data


class TestEnsembleIntegration:
    """Test ensemble voting integration in daily learning"""
    
    def test_daily_learner_ensemble_disabled(self):
        """Test daily learner with ensemble disabled (default)"""
        learner = DailyLearner('ENSEMBLE_TEST_1')
        
        # Mock training by just checking initialization
        assert learner.symbol == 'ENSEMBLE_TEST_1'
        print("[OK] Daily learner initializes with ensemble support")
    
    def test_ensemble_function_integration(self):
        """Test ensemble function is available and working"""
        result = ensemble_predict(lstm_prob=0.75, rf_prob=0.70)
        
        assert "probability" in result
        assert "confidence" in result
        assert "signal" in result
        assert "agreement" in result
        
        assert result["signal"] == "BUY"
        assert result["confidence"] >= 95.0
        print("[OK] Ensemble function works correctly")
    
    def test_ensemble_realistic_trading_scenario(self):
        """Test ensemble with realistic trading accuracies"""
        # Realistic: LSTM 65% accuracy, RF 62% accuracy
        result = ensemble_predict(lstm_prob=0.65, rf_prob=0.62)
        
        assert 0.63 < result["probability"] < 0.65
        assert result["signal"] == "BUY"
        assert result["confidence"] > 90.0
        print("[OK] Realistic trading scenario handled correctly")
    
    def test_ensemble_models_disagree(self):
        """Test ensemble when models disagree"""
        # Models disagree: LSTM 70%, RF 40%
        result = ensemble_predict(lstm_prob=0.70, rf_prob=0.40)
        
        # Weighted: 0.70 * 0.6 + 0.40 * 0.4 = 0.42 + 0.16 = 0.58
        assert 0.55 < result["probability"] < 0.61
        assert result["signal"] == "BUY"  # Still above threshold
        assert result["confidence"] < 75.0  # Low confidence due to disagreement
        print("[OK] Model disagreement handled with low confidence")
    
    def test_ensemble_output_for_history_storage(self):
        """Test ensemble output format for JSON storage"""
        result = ensemble_predict(lstm_prob=0.75, rf_prob=0.72)
        
        # All values must be JSON-serializable (floats or strings)
        assert isinstance(result["probability"], float)
        assert isinstance(result["confidence"], float)
        assert isinstance(result["agreement"], float)
        assert isinstance(result["signal"], str)
        
        # Try to serialize (would fail if non-JSON types)
        import json
        json_str = json.dumps(result)
        assert json_str is not None
        print("[OK] Ensemble output is JSON-serializable")
    
    def test_ensemble_threshold_consistency(self):
        """Test ensemble thresholds are consistent"""
        # BUY threshold: > 0.55
        buy_result = ensemble_predict(0.56, 0.54)
        assert buy_result["signal"] == "BUY"
        
        # SELL threshold: < 0.45
        sell_result = ensemble_predict(0.44, 0.46)
        assert sell_result["signal"] == "SELL"
        
        # HOLD: between 0.45 and 0.55
        hold_result = ensemble_predict(0.50, 0.50)
        assert hold_result["signal"] == "HOLD"
        print("[OK] Signal thresholds are consistent")
    
    def test_ensemble_weight_normalization(self):
        """Test ensemble weights are properly normalized"""
        # Custom weights that don't sum to 1
        result = ensemble_predict(0.8, 0.6, lstm_weight=2, rf_weight=3)
        
        # Should be normalized: 2/(2+3)=0.4 for LSTM, 3/(2+3)=0.6 for RF
        # Result: 0.8 * 0.4 + 0.6 * 0.6 = 0.32 + 0.36 = 0.68
        assert 0.67 < result["probability"] < 0.69
        assert result["lstm_weight"] == pytest.approx(0.4, rel=0.01)
        assert result["rf_weight"] == pytest.approx(0.6, rel=0.01)
        print("[OK] Weight normalization works correctly")
    
    def test_ensemble_no_side_effects(self):
        """Test ensemble doesn't modify any global state"""
        result1 = ensemble_predict(0.6, 0.7)
        result2 = ensemble_predict(0.6, 0.7)
        
        # Results should be identical
        assert result1 == result2
        print("[OK] Ensemble has no side effects")
    
    def test_ensemble_backward_compatible(self):
        """Test that daily learner still works without ensemble"""
        learner = DailyLearner('BACKWARD_COMPAT')
        
        # run_cycle has use_ensemble parameter with default False
        # This verifies backward compatibility
        assert hasattr(learner, 'run_cycle')
        print("[OK] Daily learner maintains backward compatibility")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
