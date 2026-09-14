"""
Code Validator for MQL5 and Pine Script - Checks for common trading code mistakes.
"""

from __future__ import annotations
from typing import Any
from enum import Enum
import re


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    SUGGESTION = "suggestion"


class ValidationIssue:
    """Represents a single code validation issue."""
    
    def __init__(self, severity: Severity, line: int | None, code: str, message: str, fix: str | None = None):
        self.severity = severity
        self.line = line
        self.code = code
        self.message = message
        self.fix = fix
    
    def to_dict(self) -> dict[str, Any]:
        return {
            'severity': self.severity.value,
            'line': self.line,
            'code': self.code,
            'message': self.message,
            'fix': self.fix,
        }


class CodeValidator:
    """Validates MQL5 and Pine Script code for quality and best practices."""
    
    def __init__(self):
        self.issues: list[ValidationIssue] = []
    
    def validate(self, code: str, language: str = 'mql5') -> dict[str, Any]:
        """
        Validate code and return results.
        
        Args:
            code: Code to validate
            language: 'mql5' or 'pine'
        
        Returns:
            Dict with 'valid', 'issues', 'score' (0-100)
        """
        self.issues = []
        
        if language.lower() in ['mql5', 'mql']:
            self._validate_mql5(code)
        elif language.lower() in ['pine', 'pinescript']:
            self._validate_pine(code)
        else:
            self.issues.append(ValidationIssue(
                Severity.ERROR,
                None,
                'UNKNOWN_LANGUAGE',
                f"Unknown language: {language}",
                f"Use 'mql5' or 'pine' for language parameter"
            ))
        
        # Calculate score
        score = self._calculate_score()
        
        return {
            'valid': len([i for i in self.issues if i.severity == Severity.ERROR]) == 0,
            'score': score,
            'issues': [issue.to_dict() for issue in self.issues],
            'issue_count': len(self.issues),
            'error_count': len([i for i in self.issues if i.severity == Severity.ERROR]),
            'warning_count': len([i for i in self.issues if i.severity == Severity.WARNING]),
            'suggestion_count': len([i for i in self.issues if i.severity == Severity.SUGGESTION]),
        }
    
    def _validate_mql5(self, code: str):
        """Validate MQL5 code."""
        lines = code.split('\n')
        
        # Check for missing required functions
        if 'OnTick()' not in code:
            self.issues.append(ValidationIssue(
                Severity.ERROR,
                None,
                'MISSING_ONTICK',
                'Missing OnTick() function - required for EA execution',
                'Add: void OnTick() { ... }'
            ))
        
        # Check for stop-loss logic
        if not self._has_stop_loss_logic(code):
            self.issues.append(ValidationIssue(
                Severity.WARNING,
                None,
                'MISSING_STOPLOSS',
                'No stop-loss logic detected in code',
                'Add OrderModify() with stoploss or use TP/SL in OrderSend()'
            ))
        
        # Check for take-profit logic
        if not self._has_take_profit_logic(code):
            self.issues.append(ValidationIssue(
                Severity.WARNING,
                None,
                'MISSING_TAKEPROFIT',
                'No take-profit logic detected',
                'Add TakeProfit parameter or use OrderModify() to set TP'
            ))
        
        # Check for position sizing logic
        if 'OrderSend' in code and not self._has_position_sizing(code):
            self.issues.append(ValidationIssue(
                Severity.WARNING,
                None,
                'HARDCODED_LOTSIZE',
                'Position size appears hardcoded - should be dynamic',
                'Use CalculateLotSize() or PositionSize = RiskPercentage * AccountBalance / (StopLoss * Pip)'
            ))
        
        # Check for unrealistic risk ratios
        risk_check = self._check_risk_ratios(code)
        if risk_check:
            self.issues.append(risk_check)
        
        # Check for hard-coded values
        hardcoded = self._find_hardcoded_values(code)
        for issue in hardcoded:
            self.issues.append(issue)
        
        # Check for magic numbers in OrderSend
        magic_check = self._check_magic_number(code)
        if magic_check:
            self.issues.append(magic_check)
        
        # Check for error handling
        if 'GetLastError()' not in code:
            self.issues.append(ValidationIssue(
                Severity.SUGGESTION,
                None,
                'NO_ERROR_HANDLING',
                'No error handling detected',
                'Add GetLastError() checks and error handling logic'
            ))
    
    def _validate_pine(self, code: str):
        """Validate Pine Script code."""
        lines = code.split('\n')
        
        # Check version declaration
        if '//@version' not in code:
            self.issues.append(ValidationIssue(
                Severity.ERROR,
                None,
                'MISSING_VERSION',
                'Missing @version declaration - Pine Script requires it',
                'Add: //@version=5 at the top'
            ))
        
        # Check strategy/indicator declaration
        if 'strategy(' not in code and 'indicator(' not in code:
            self.issues.append(ValidationIssue(
                Severity.ERROR,
                None,
                'MISSING_DECLARATION',
                'Missing strategy() or indicator() declaration',
                'Add: strategy() or indicator() function'
            ))
        
        # Check for stop-loss in strategy
        if 'strategy(' in code and not self._has_sl_in_pine(code):
            self.issues.append(ValidationIssue(
                Severity.WARNING,
                None,
                'MISSING_STOPLOSS_PINE',
                'No stop-loss detected in strategy.entry() calls',
                'Add stop parameter: strategy.entry(..., stop=...)'
            ))
        
        # Check for take-profit in strategy
        if 'strategy(' in code and not self._has_tp_in_pine(code):
            self.issues.append(ValidationIssue(
                Severity.WARNING,
                None,
                'MISSING_TAKEPROFIT_PINE',
                'No take-profit detected in strategy.entry() calls',
                'Add limit parameter: strategy.entry(..., limit=...)'
            ))
        
        # Check for hardcoded values
        hardcoded = self._find_hardcoded_values(code)
        for issue in hardcoded:
            self.issues.append(issue)
    
    def _has_stop_loss_logic(self, code: str) -> bool:
        """Check if code contains stop-loss logic."""
        return bool(
            re.search(r'StopLoss|stop_loss|OrderModify.*Stoploss|OrderSend.*\d+.*\d+', code, re.IGNORECASE) or
            (code.count('OrderModify') > 0)
        )
    
    def _has_take_profit_logic(self, code: str) -> bool:
        """Check if code contains take-profit logic."""
        return bool(
            re.search(r'TakeProfit|take_profit|OrderModify.*TakeProfit|OrderSend.*TakeProfit', code, re.IGNORECASE)
        )
    
    def _has_position_sizing(self, code: str) -> bool:
        """Check if code has dynamic position sizing."""
        return bool(
            re.search(r'CalculateLot|RiskPercentage|PositionSize|AccountBalance|volume|Lot', code, re.IGNORECASE)
        )
    
    def _has_sl_in_pine(self, code: str) -> bool:
        """Check if Pine Script has stop-loss in entry calls."""
        return bool(re.search(r'strategy\.entry\([^)]*stop\s*=', code, re.IGNORECASE))
    
    def _has_tp_in_pine(self, code: str) -> bool:
        """Check if Pine Script has take-profit in entry calls."""
        return bool(re.search(r'strategy\.entry\([^)]*limit\s*=', code, re.IGNORECASE))
    
    def _check_risk_ratios(self, code: str) -> ValidationIssue | None:
        """Check for unrealistic risk ratios."""
        # Look for patterns like risk:1 or 1:reward
        ratios = re.findall(r'(\d+\.?\d*)\s*:\s*(\d+\.?\d*)', code)
        for risk, reward in ratios:
            try:
                r = float(risk)
                w = float(reward)
                ratio = w / r if r > 0 else 0
                
                # Warn if RR < 1.5
                if 0 < ratio < 1.5:
                    return ValidationIssue(
                        Severity.WARNING,
                        None,
                        'LOW_RISK_REWARD',
                        f'Risk/Reward ratio too low ({ratio:.2f}:1) - minimum recommended is 1.5:1',
                        f'Increase take-profit or decrease stop-loss for better R:R'
                    )
                
                # Warn if RR > 1:10 (unrealistic)
                if ratio > 10:
                    return ValidationIssue(
                        Severity.SUGGESTION,
                        None,
                        'HIGH_RISK_REWARD',
                        f'Risk/Reward ratio very high ({ratio:.2f}:1) - verify targets are realistic',
                        'Check that take-profit levels are achievable'
                    )
            except (ValueError, ZeroDivisionError):
                pass
        
        return None
    
    def _find_hardcoded_values(self, code: str) -> list[ValidationIssue]:
        """Find hardcoded numeric values that should be parameters."""
        issues = []
        
        # Find naked numbers that might be hardcoded parameters
        hardcoded_patterns = [
            (r'0\.1|0\.5|1\.0', 'Fixed lot size', 'Use input() for lot size'),
            (r'\b50\b|\b100\b|\b200\b', 'Potential period value', 'Use input() for indicator periods'),
            (r'0\.\d{1,3}', 'Risk percentage', 'Use input() for risk parameters'),
        ]
        
        for pattern, description, fix in hardcoded_patterns:
            if re.search(pattern, code):
                issues.append(ValidationIssue(
                    Severity.SUGGESTION,
                    None,
                    'HARDCODED_VALUE',
                    f'Potential hardcoded value: {description}',
                    fix
                ))
        
        return issues[:3]  # Limit to top 3 issues
    
    def _check_magic_number(self, code: str) -> ValidationIssue | None:
        """Check if OrderSend has a magic number."""
        if 'OrderSend' in code:
            # Look for OrderSend with magic number
            if not re.search(r'magic\s*=|\b\d{6,}\b', code, re.IGNORECASE):
                return ValidationIssue(
                    Severity.SUGGESTION,
                    None,
                    'NO_MAGIC_NUMBER',
                    'No magic number detected in OrderSend() - helps identify orders',
                    'Add magic number parameter or constant for order identification'
                )
        
        return None
    
    def _calculate_score(self) -> int:
        """Calculate code quality score (0-100)."""
        base_score = 100
        
        for issue in self.issues:
            if issue.severity == Severity.ERROR:
                base_score -= 20
            elif issue.severity == Severity.WARNING:
                base_score -= 10
            elif issue.severity == Severity.SUGGESTION:
                base_score -= 5
        
        return max(0, min(100, base_score))
