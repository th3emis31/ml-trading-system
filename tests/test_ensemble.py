#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 3.2 Ensemble Voting Tests"""

import pytest
from src.train import ensemble_predict


class TestEnsemblePredict:
    """Test ensemble voting function"""
    
    def test_ensemble_both_agree_high(self):
        """Test when both models predict high (agree)"""
        result = ensemble_predict(lstm_prob=0.8, rf_prob=0.8)
        
        assert result["probability"] == pytest.approx(0.8, rel=0.01)
        assert result["signal"] == "BUY"
        assert result["confidence"] == 100.0  # Perfect agreement
        assert result["agreement"] == 1.0
    
    def test_ensemble_both_agree_low(self):
        """Test when both models predict low (agree)"""
        result = ensemble_predict(lstm_prob=0.2, rf_prob=0.2)
        
        assert result["probability"] == pytest.approx(0.2, rel=0.01)
        assert result["signal"] == "SELL"
        assert result["confidence"] == 100.0  # Perfect agreement
        assert result["agreement"] == 1.0
    
    def test_ensemble_both_agree_neutral(self):
        """Test when both models predict neutral (agree)"""
        result = ensemble_predict(lstm_prob=0.5, rf_prob=0.5)
        
        assert result["probability"] == pytest.approx(0.5, rel=0.01)
        assert result["signal"] == "HOLD"
        assert result["confidence"] == 100.0  # Perfect agreement
    
    def test_ensemble_slight_disagreement(self):
        """Test when models slightly disagree"""
        result = ensemble_predict(lstm_prob=0.72, rf_prob=0.68)
        
        assert 0.69 < result["probability"] < 0.71
        assert result["signal"] == "BUY"
        assert result["confidence"] > 95.0  # High confidence (slight disagreement)
        assert 0.95 < result["agreement"] < 1.0
    
    def test_ensemble_moderate_disagreement(self):
        """Test when models moderately disagree"""
        result = ensemble_predict(lstm_prob=0.7, rf_prob=0.5)
        
        assert 0.6 < result["probability"] <= 0.63  # Fixed: 0.7*0.6 + 0.5*0.4 = 0.62
        assert result["signal"] == "BUY"
        assert 80.0 <= result["confidence"] < 90.0  # Fixed: allow = 80.0
    
    def test_ensemble_extreme_disagreement(self):
        """Test when models strongly disagree"""
        result = ensemble_predict(lstm_prob=0.9, rf_prob=0.1)
        
        assert 0.5 < result["probability"] < 0.62  # Fixed: actual is 0.9*0.6 + 0.1*0.4 = 0.58
        assert result["signal"] == "BUY"  # Just barely > 0.55
        assert result["confidence"] < 30.0  # Low confidence (strong disagreement)
    
    def test_ensemble_weighted_average(self):
        """Test weighted average calculation"""
        # Default weights: LSTM 0.6, RF 0.4
        result = ensemble_predict(lstm_prob=0.8, rf_prob=0.4)
        
        # Expected: 0.8 * 0.6 + 0.4 * 0.4 = 0.48 + 0.16 = 0.64
        assert result["probability"] == pytest.approx(0.64, rel=0.01)
        assert result["lstm_weight"] == pytest.approx(0.6, rel=0.01)
        assert result["rf_weight"] == pytest.approx(0.4, rel=0.01)
    
    def test_ensemble_custom_weights(self):
        """Test custom weights"""
        # Equal weights: LSTM 0.5, RF 0.5
        result = ensemble_predict(lstm_prob=0.8, rf_prob=0.4, lstm_weight=0.5, rf_weight=0.5)
        
        # Expected: 0.8 * 0.5 + 0.4 * 0.5 = 0.4 + 0.2 = 0.6
        assert result["probability"] == pytest.approx(0.6, rel=0.01)
    
    def test_ensemble_boundary_buy(self):
        """Test BUY signal boundary (> 0.55)"""
        # Just above buy threshold
        result = ensemble_predict(lstm_prob=0.56, rf_prob=0.54)
        assert result["signal"] == "BUY"
        
        # Exactly at buy threshold (not included, must be > 0.55)
        result = ensemble_predict(lstm_prob=0.55, rf_prob=0.55)
        assert result["signal"] == "HOLD"  # Fixed: = is not >, must be strictly greater
        
        # Just below buy threshold
        result = ensemble_predict(lstm_prob=0.54, rf_prob=0.54)
        assert result["signal"] == "HOLD"
    
    def test_ensemble_boundary_sell(self):
        """Test SELL signal boundary (< 0.45)"""
        # Just below sell threshold
        result = ensemble_predict(lstm_prob=0.44, rf_prob=0.46)
        assert result["signal"] == "SELL"
        
        # Just at sell threshold
        result = ensemble_predict(lstm_prob=0.45, rf_prob=0.45)
        assert result["signal"] == "HOLD"
        
        # Just above sell threshold
        result = ensemble_predict(lstm_prob=0.46, rf_prob=0.44)
        assert result["signal"] == "HOLD"
    
    def test_ensemble_return_keys(self):
        """Test that all expected keys are in return dict"""
        result = ensemble_predict(lstm_prob=0.6, rf_prob=0.6)
        
        required_keys = [
            "probability", "confidence", "lstm_prob", "rf_prob",
            "agreement", "signal", "lstm_weight", "rf_weight"
        ]
        
        for key in required_keys:
            assert key in result, f"Missing key: {key}"
    
    def test_ensemble_return_types(self):
        """Test that return types are correct"""
        result = ensemble_predict(lstm_prob=0.6, rf_prob=0.6)
        
        assert isinstance(result["probability"], float)
        assert isinstance(result["confidence"], float)
        assert isinstance(result["lstm_prob"], float)
        assert isinstance(result["rf_prob"], float)
        assert isinstance(result["agreement"], float)
        assert isinstance(result["signal"], str)
        assert isinstance(result["lstm_weight"], float)
        assert isinstance(result["rf_weight"], float)
    
    def test_ensemble_invalid_lstm_prob(self):
        """Test error handling for invalid LSTM probability"""
        with pytest.raises(ValueError):
            ensemble_predict(lstm_prob=1.5, rf_prob=0.6)
        
        with pytest.raises(ValueError):
            ensemble_predict(lstm_prob=-0.1, rf_prob=0.6)
    
    def test_ensemble_invalid_rf_prob(self):
        """Test error handling for invalid RF probability"""
        with pytest.raises(ValueError):
            ensemble_predict(lstm_prob=0.6, rf_prob=1.5)
        
        with pytest.raises(ValueError):
            ensemble_predict(lstm_prob=0.6, rf_prob=-0.1)
    
    def test_ensemble_invalid_weights(self):
        """Test error handling for invalid weights (negative or zero)"""
        with pytest.raises(ValueError):
            ensemble_predict(lstm_prob=0.6, rf_prob=0.6, lstm_weight=0, rf_weight=0.5)
        
        with pytest.raises(ValueError):
            ensemble_predict(lstm_prob=0.6, rf_prob=0.6, lstm_weight=-0.5, rf_weight=0.5)
    
    def test_ensemble_realistic_scenario_1(self):
        """Test realistic scenario: Models mostly agree on BUY"""
        # LSTM confident, RF less confident
        result = ensemble_predict(lstm_prob=0.75, rf_prob=0.65)
        
        assert result["signal"] == "BUY"
        assert result["probability"] > 0.65
        assert result["confidence"] > 85.0
    
    def test_ensemble_realistic_scenario_2(self):
        """Test realistic scenario: Models disagree, moderate signal"""
        # LSTM says BUY, RF says HOLD
        result = ensemble_predict(lstm_prob=0.65, rf_prob=0.50)
        
        assert result["signal"] == "BUY"
        assert 0.55 < result["probability"] < 0.61
        assert 80.0 < result["confidence"] < 90.0  # Fixed: actual confidence is 0.85
    
    def test_ensemble_realistic_scenario_3(self):
        """Test realistic scenario: Models disagreed, weighted result"""
        # LSTM says HOLD, RF says SELL
        result = ensemble_predict(lstm_prob=0.50, rf_prob=0.30)
        
        # Weighted: 0.50 * 0.6 + 0.30 * 0.4 = 0.30 + 0.12 = 0.42, which is < 0.45 = SELL
        assert result["signal"] == "SELL"  # Weighted average goes below threshold
        assert result["probability"] < 0.45
        assert result["confidence"] > 70.0  # Disagreement but not extreme (50% vs 30%)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
