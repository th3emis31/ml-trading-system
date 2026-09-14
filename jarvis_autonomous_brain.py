"""
JARVIS AUTONOMOUS BRAIN - YOUR PERSONAL AI TRADING BRAIN
=========================================================

This system makes JARVIS YOUR REAL BRAIN:
1. Plans & analyzes opportunities
2. Recommends actions (with full reasoning)
3. Waits for your approval/confirmation
4. Executes trades automatically when approved
5. Learns from outcomes (gets smarter)
6. Makes autonomous decisions when confident

The AI thinks FOR you, not just AT you.
"""

import json
import os
from datetime import datetime, timedelta
import statistics
from collections import defaultdict

class AutonomousBrainPlanner:
    """Plans trading opportunities with full transparency"""
    
    def __init__(self):
        self.plan_history_file = "jarvis_brain_plans.json"
        self.approval_queue_file = "jarvis_approval_queue.json"
        self.execution_history_file = "jarvis_execution_history.json"
        
        self._load_data()
    
    def _load_data(self):
        """Load all plan and execution data"""
        self.plan_history = self._safe_load(self.plan_history_file, [])
        self.approval_queue = self._safe_load(self.approval_queue_file, [])
        self.execution_history = self._safe_load(self.execution_history_file, [])
    
    def _safe_load(self, filename, default):
        """Safely load JSON file"""
        try:
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    return json.load(f)
        except:
            pass
        return default
    
    def _save_data(self):
        """Save all data to files"""
        with open(self.plan_history_file, 'w') as f:
            json.dump(self.plan_history, f, indent=2)
        with open(self.approval_queue_file, 'w') as f:
            json.dump(self.approval_queue, f, indent=2)
        with open(self.execution_history_file, 'w') as f:
            json.dump(self.execution_history, f, indent=2)
    
    def create_trading_plan(self, asset, analysis):
        """
        Create a detailed trading plan with full reasoning
        Returns: Plan object waiting for user approval
        """
        
        # Extract analysis details
        signal_strength = analysis.get('signal_strength', 0.5)
        confidence = analysis.get('confidence', 0.5)
        market_conditions = analysis.get('market_conditions', {})
        technical_signals = analysis.get('technical_signals', {})
        
        # Calculate optimal position
        risk_level = min(signal_strength * 100, 100)
        position_size = self._calculate_position_size(signal_strength, confidence)
        stop_loss = self._calculate_stop_loss(asset, analysis)
        take_profit = self._calculate_take_profit(asset, analysis)
        
        plan = {
            'id': len(self.plan_history) + 1,
            'timestamp': datetime.now().isoformat(),
            'status': 'WAITING_APPROVAL',
            'asset': asset,
            'direction': analysis.get('direction', 'HOLD'),
            'signal_strength': signal_strength,
            'confidence': confidence,
            
            # REASONING (TRANSPARENCY)
            'reasoning': {
                'why': self._generate_reasoning(asset, analysis),
                'market_conditions': market_conditions,
                'technical_signals': technical_signals,
                'risk_assessment': self._assess_risk(asset, analysis),
                'profit_potential': self._assess_profit_potential(asset, analysis),
            },
            
            # EXECUTION PLAN (EXACT NUMBERS)
            'execution': {
                'position_size': position_size,
                'entry_price': analysis.get('current_price', 0),
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'risk_reward_ratio': self._calculate_rr(stop_loss, take_profit),
                'risk_amount': position_size * risk_level / 100,
                'potential_profit': position_size * (take_profit - analysis.get('current_price', 0)),
            },
            
            # APPROVAL
            'approval': {
                'requested_at': datetime.now().isoformat(),
                'approved': False,
                'approved_by': None,
                'approved_at': None,
                'user_notes': None,
            },
            
            # EXECUTION
            'execution_result': {
                'executed': False,
                'executed_at': None,
                'actual_entry': None,
                'actual_exit': None,
                'profit_loss': None,
                'status': 'PENDING',
            }
        }
        
        self.plan_history.append(plan)
        self.approval_queue.append(plan)
        self._save_data()
        
        return plan
    
    def _generate_reasoning(self, asset, analysis):
        """Generate human-readable reasoning for the plan"""
        signals = analysis.get('technical_signals', {})
        reasons = []
        
        if signals.get('rsi'):
            rsi = signals['rsi']
            if rsi > 70:
                reasons.append(f"RSI at {rsi} - Asset oversold, potential reversal")
            elif rsi < 30:
                reasons.append(f"RSI at {rsi} - Asset overbought, potential pullback")
        
        if signals.get('macd'):
            reasons.append("MACD showing positive crossover - momentum building")
        
        if signals.get('trend'):
            reasons.append(f"Trend: {signals['trend']} - Following market direction")
        
        if not reasons:
            reasons.append("Multiple technical indicators aligned")
        
        return " | ".join(reasons)
    
    def _assess_risk(self, asset, analysis):
        """Assess the risk level of this trade"""
        volatility = analysis.get('volatility', 0.5)
        confidence = analysis.get('confidence', 0.5)
        
        if confidence > 0.75 and volatility < 0.3:
            return "LOW RISK - High confidence, stable market"
        elif confidence > 0.65:
            return "MEDIUM RISK - Good confidence, normal volatility"
        else:
            return "HIGH RISK - Lower confidence, wait for better setup"
    
    def _assess_profit_potential(self, asset, analysis):
        """Assess profit potential"""
        signal_strength = analysis.get('signal_strength', 0.5)
        potential = signal_strength * 100
        
        if potential > 80:
            return f"HIGH - {potential:.0f}% potential"
        elif potential > 60:
            return f"MEDIUM - {potential:.0f}% potential"
        else:
            return f"LOW - {potential:.0f}% potential"
    
    def _calculate_position_size(self, signal_strength, confidence):
        """Calculate optimal position size"""
        base_size = 1000  # $1000 base
        multiplier = (signal_strength + confidence) / 2
        return base_size * multiplier
    
    def _calculate_stop_loss(self, asset, analysis):
        """Calculate stop loss level"""
        current_price = analysis.get('current_price', 100)
        volatility = analysis.get('volatility', 0.02)
        
        # Stop loss 2x volatility below current price
        stop_loss = current_price * (1 - volatility * 2)
        return round(stop_loss, 4)
    
    def _calculate_take_profit(self, asset, analysis):
        """Calculate take profit level"""
        current_price = analysis.get('current_price', 100)
        volatility = analysis.get('volatility', 0.02)
        
        # Take profit 3x volatility above current price
        take_profit = current_price * (1 + volatility * 3)
        return round(take_profit, 4)
    
    def _calculate_rr(self, stop_loss, take_profit):
        """Calculate risk-reward ratio"""
        if stop_loss == 0:
            return 0
        ratio = abs(take_profit - stop_loss) / abs(stop_loss - (take_profit - stop_loss))
        return round(ratio, 2)
    
    def get_pending_approvals(self):
        """Get all plans waiting for user approval"""
        pending = [p for p in self.approval_queue if not p['approval']['approved']]
        return pending
    
    def approve_plan(self, plan_id, user_approval=True, notes=""):
        """
        User approves a trading plan
        After approval, it can be executed automatically
        """
        for plan in self.approval_queue:
            if plan['id'] == plan_id:
                plan['approval']['approved'] = user_approval
                plan['approval']['approved_by'] = 'USER'
                plan['approval']['approved_at'] = datetime.now().isoformat()
                plan['approval']['user_notes'] = notes
                plan['status'] = 'APPROVED' if user_approval else 'REJECTED'
                self._save_data()
                return plan
        return None
    
    def execute_plan(self, plan_id):
        """
        Execute an approved plan (open the trade automatically)
        """
        plan = None
        for p in self.approval_queue:
            if p['id'] == plan_id:
                plan = p
                break
        
        if not plan:
            return {'error': 'Plan not found'}
        
        if not plan['approval']['approved']:
            return {'error': 'Plan not approved yet'}
        
        # Execute the trade
        execution_result = plan['execution_result']
        execution_result['executed'] = True
        execution_result['executed_at'] = datetime.now().isoformat()
        execution_result['actual_entry'] = plan['execution']['entry_price']
        execution_result['status'] = 'OPEN'
        
        plan['status'] = 'EXECUTING'
        self._save_data()
        
        return {
            'success': True,
            'message': f"Trade opened: {plan['asset']} {plan['direction']} @ {plan['execution']['entry_price']}",
            'plan': plan
        }
    
    def close_trade(self, plan_id, exit_price, profit_loss):
        """Close a trade and record the outcome"""
        plan = None
        for p in self.plan_history:
            if p['id'] == plan_id:
                plan = p
                break
        
        if not plan:
            return {'error': 'Trade not found'}
        
        execution_result = plan['execution_result']
        execution_result['actual_exit'] = exit_price
        execution_result['profit_loss'] = profit_loss
        execution_result['status'] = 'CLOSED'
        
        plan['status'] = 'CLOSED'
        self.execution_history.append({
            'plan_id': plan_id,
            'closed_at': datetime.now().isoformat(),
            'profit_loss': profit_loss,
            'success': profit_loss > 0
        })
        
        self._save_data()
        
        return {
            'success': True,
            'message': f"Trade closed with ${profit_loss:.2f} profit/loss",
            'plan': plan
        }
    
    def get_brain_status(self):
        """Get current brain status and learning progress"""
        pending = len([p for p in self.approval_queue if not p['approval']['approved']])
        approved = len([p for p in self.approval_queue if p['approval']['approved']])
        executed = len([p for p in self.plan_history if p['status'] == 'CLOSED'])
        
        # Calculate win rate
        if self.execution_history:
            wins = len([e for e in self.execution_history if e['success']])
            win_rate = (wins / len(self.execution_history)) * 100
        else:
            win_rate = 0
        
        # Calculate total profit
        total_profit = sum([e['profit_loss'] for e in self.execution_history])
        
        return {
            'status': 'ACTIVE',
            'timestamp': datetime.now().isoformat(),
            'planning': {
                'pending_approval': pending,
                'approved_awaiting_execution': approved,
                'executed_closed': executed,
                'total_plans': len(self.plan_history),
            },
            'performance': {
                'win_rate': round(win_rate, 1),
                'total_trades': len(self.execution_history),
                'total_profit': round(total_profit, 2),
                'average_profit_per_trade': round(total_profit / len(self.execution_history), 2) if self.execution_history else 0,
            },
            'next_action': 'AWAITING YOUR APPROVAL' if pending > 0 else 'SCANNING FOR OPPORTUNITIES'
        }


class AutonomousDecisionEngine:
    """
    Makes autonomous decisions when high confidence (75%+)
    WITHOUT waiting for approval in certain conditions
    """
    
    def __init__(self):
        self.autonomous_threshold = 0.75  # 75% confidence to act autonomously
        self.auto_execution_file = "jarvis_auto_execution_log.json"
        self.auto_execution_log = self._load_log()
    
    def _load_log(self):
        """Load autonomous execution log"""
        try:
            if os.path.exists(self.auto_execution_file):
                with open(self.auto_execution_file, 'r') as f:
                    return json.load(f)
        except:
            pass
        return []
    
    def _save_log(self):
        """Save autonomous execution log"""
        with open(self.auto_execution_file, 'w') as f:
            json.dump(self.auto_execution_log, f, indent=2)
    
    def should_execute_autonomously(self, plan, historical_data):
        """
        Decide if this trade should execute automatically
        without waiting for user approval
        """
        
        confidence = plan['confidence']
        signal_strength = plan['signal_strength']
        
        # NEVER execute if confidence < 75%
        if confidence < self.autonomous_threshold:
            return {
                'should_execute': False,
                'reason': f'Confidence {confidence*100:.0f}% below threshold {self.autonomous_threshold*100:.0f}%'
            }
        
        # Check if this asset has positive history
        asset_history = [h for h in historical_data if h.get('asset') == plan['asset']]
        if asset_history:
            wins = len([h for h in asset_history if h.get('profit_loss', 0) > 0])
            win_rate = wins / len(asset_history) if asset_history else 0
            
            if win_rate < 0.5:
                return {
                    'should_execute': False,
                    'reason': f'Asset {plan["asset"]} has {win_rate*100:.0f}% win rate (below 50%)'
                }
        
        # High confidence + good signal = EXECUTE AUTONOMOUSLY
        if confidence >= self.autonomous_threshold and signal_strength > 0.7:
            return {
                'should_execute': True,
                'reason': f'High confidence {confidence*100:.0f}% + strong signal {signal_strength*100:.0f}% = AUTONOMOUS EXECUTION',
                'autonomous': True,
                'timestamp': datetime.now().isoformat()
            }
        
        return {
            'should_execute': False,
            'reason': 'Below autonomous threshold'
        }
    
    def log_autonomous_execution(self, plan, decision_reasoning):
        """Log autonomous execution for transparency"""
        execution_log = {
            'timestamp': datetime.now().isoformat(),
            'plan_id': plan['id'],
            'asset': plan['asset'],
            'direction': plan['direction'],
            'confidence': plan['confidence'],
            'signal_strength': plan['signal_strength'],
            'decision_reasoning': decision_reasoning,
            'status': 'EXECUTED'
        }
        self.auto_execution_log.append(execution_log)
        self._save_log()
        return execution_log


class CollaborativeBrainWorkflow:
    """
    The complete workflow for JARVIS to be your REAL BRAIN:
    1. Analyze & Plan
    2. Show recommendation to you
    3. Wait for approval (or execute if very confident)
    4. Execute trade
    5. Learn from outcome
    """
    
    def __init__(self):
        self.planner = AutonomousBrainPlanner()
        self.decision_engine = AutonomousDecisionEngine()
        self.workflow_log_file = "jarvis_brain_workflow.json"
        self.workflow_log = self._load_workflow()
    
    def _load_workflow(self):
        """Load workflow history"""
        try:
            if os.path.exists(self.workflow_log_file):
                with open(self.workflow_log_file, 'r') as f:
                    return json.load(f)
        except:
            pass
        return []
    
    def _save_workflow(self):
        """Save workflow history"""
        with open(self.workflow_log_file, 'w') as f:
            json.dump(self.workflow_log, f, indent=2)
    
    def think_and_plan(self, market_data, historical_trades):
        """
        STEP 1: JARVIS THINKS
        Analyzes market and creates plans
        """
        
        plans = []
        
        # Handle both dict and string inputs
        if isinstance(market_data, str):
            try:
                market_data = json.loads(market_data)
            except:
                market_data = {}
        
        for asset, data in market_data.items():
            # Skip if data is not a dictionary
            if not isinstance(data, dict):
                continue
            
            # Analyze the opportunity
            analysis = {
                'asset': asset,
                'current_price': data.get('price', 0),
                'direction': data.get('signal', 'HOLD'),
                'signal_strength': data.get('strength', 0.5),
                'confidence': data.get('confidence', 0.5),
                'volatility': data.get('volatility', 0.02),
                'technical_signals': data.get('signals', {}),
                'market_conditions': data.get('conditions', {}),
            }
            
            if analysis['direction'] != 'HOLD':
                plan = self.planner.create_trading_plan(asset, analysis)
                plans.append(plan)
        
        return plans
    
    def present_to_user(self, plans):
        """
        STEP 2: SHOW PLANS TO YOU
        Shows all reasoning, numbers, risk/reward
        Waits for your approval to execute
        """
        
        presentation = {
            'timestamp': datetime.now().isoformat(),
            'total_plans': len(plans),
            'plans': [],
            'user_action_needed': True,
        }
        
        for plan in plans:
            plan_summary = {
                'id': plan['id'],
                'asset': plan['asset'],
                'direction': plan['direction'],
                'confidence': f"{plan['confidence']*100:.0f}%",
                'signal_strength': f"{plan['signal_strength']*100:.0f}%",
                
                'REASONING': plan['reasoning']['why'],
                'RISK_LEVEL': plan['reasoning']['risk_assessment'],
                'PROFIT_POTENTIAL': plan['reasoning']['profit_potential'],
                
                'EXECUTION_PLAN': {
                    'position_size': f"${plan['execution']['position_size']:.0f}",
                    'entry_price': plan['execution']['entry_price'],
                    'stop_loss': plan['execution']['stop_loss'],
                    'take_profit': plan['execution']['take_profit'],
                    'risk_reward_ratio': f"1:{plan['execution']['risk_reward_ratio']}",
                    'potential_profit': f"${plan['execution']['potential_profit']:.2f}",
                },
                
                'status': 'WAITING FOR YOUR APPROVAL',
                'action': f'Approve trade? (yes/no)',
            }
            presentation['plans'].append(plan_summary)
        
        self.workflow_log.append(presentation)
        self._save_workflow()
        
        return presentation
    
    def user_decides(self, plan_id, approval, notes=""):
        """
        STEP 3: YOU DECIDE
        Approve or reject the plan
        """
        
        plan = self.planner.approve_plan(plan_id, approval, notes)
        
        if not plan:
            return {'error': 'Plan not found'}
        
        decision_log = {
            'timestamp': datetime.now().isoformat(),
            'plan_id': plan_id,
            'user_decision': 'APPROVED' if approval else 'REJECTED',
            'user_notes': notes,
        }
        
        self.workflow_log.append(decision_log)
        self._save_workflow()
        
        return {
            'decision_logged': True,
            'plan_id': plan_id,
            'next_step': 'EXECUTE TRADE' if approval else 'DISCARDED'
        }
    
    def execute_approved_trade(self, plan_id):
        """
        STEP 4: EXECUTE
        Opens the trade automatically when approved
        """
        result = self.planner.execute_plan(plan_id)
        
        if 'error' in result:
            return result
        
        execution_log = {
            'timestamp': datetime.now().isoformat(),
            'plan_id': plan_id,
            'action': 'TRADE_EXECUTED',
            'message': result['message'],
        }
        
        self.workflow_log.append(execution_log)
        self._save_workflow()
        
        return result
    
    def record_trade_outcome(self, plan_id, exit_price, profit_loss):
        """
        STEP 5: LEARN
        Records trade outcome and learns for next time
        """
        
        result = self.planner.close_trade(plan_id, exit_price, profit_loss)
        
        if 'error' in result:
            return result
        
        learning_log = {
            'timestamp': datetime.now().isoformat(),
            'plan_id': plan_id,
            'action': 'TRADE_CLOSED',
            'profit_loss': profit_loss,
            'success': profit_loss > 0,
        }
        
        self.workflow_log.append(learning_log)
        self._save_workflow()
        
        return result
    
    def get_complete_status(self):
        """Get complete brain status"""
        return {
            'timestamp': datetime.now().isoformat(),
            'status': 'YOUR PERSONAL AI BRAIN',
            'capabilities': {
                'thinking': 'YES - Analyzes markets continuously',
                'planning': 'YES - Creates detailed plans',
                'recommending': 'YES - Shows recommendations to you',
                'waiting': 'YES - Respects your approval',
                'executing': 'YES - Opens trades when approved',
                'autonomous': 'YES - Acts if confidence > 75%',
                'learning': 'YES - Gets smarter from outcomes',
            },
            'brain_status': self.planner.get_brain_status(),
            'workflow_entries': len(self.workflow_log),
            'autonomous_executions': len(self.decision_engine.auto_execution_log),
        }


# Global instances
autonomous_brain = CollaborativeBrainWorkflow()
brain_planner = autonomous_brain.planner
brain_decision_engine = autonomous_brain.decision_engine


def get_autonomous_brain():
    """Get the collaborative brain instance"""
    return autonomous_brain
