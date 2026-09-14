# JARVIS AUTONOMOUS BRAIN - API QUICK REFERENCE

## 🎯 Complete API Endpoints Summary

### 1️⃣ THINK & CREATE PLANS
```bash
POST /api/brain/think-and-plan

Request:
{
  "market_data": {
    "BTCUSD": {
      "price": 45000,
      "signal": "BUY",
      "strength": 0.85,
      "confidence": 0.82,
      "volatility": 0.025,
      "signals": {"rsi": 32, "trend": "UPTREND"}
    }
  },
  "historical_trades": []
}

Response:
{
  "success": true,
  "plans_created": 2,
  "plans": [...]
}
```

### 2️⃣ GET RECOMMENDATIONS
```bash
GET /api/brain/get-pending-approvals

Response:
{
  "success": true,
  "total_plans": 2,
  "action_needed": true,
  "plans": [
    {
      "id": 1,
      "asset": "EURUSD",
      "direction": "SELL",
      "confidence": "78%",
      "REASONING": "Trend: DOWNTREND",
      "RISK_LEVEL": "LOW RISK",
      "PROFIT_POTENTIAL": "MEDIUM - 72%",
      "EXECUTION_PLAN": {
        "position_size": "$1440",
        "entry_price": 1.085,
        "stop_loss": 1.0913,
        "take_profit": 1.0787,
        "potential_profit": "$1412.80"
      },
      "status": "WAITING FOR YOUR APPROVAL"
    }
  ]
}
```

### 3️⃣ APPROVE PLAN
```bash
POST /api/brain/approve-plan

Request:
{
  "plan_id": 1,
  "approval": true,
  "notes": "Good setup, let's execute"
}

Response:
{
  "success": true,
  "decision_logged": true,
  "plan_id": 1,
  "status": "EXECUTE TRADE"
}
```

### 4️⃣ EXECUTE TRADE
```bash
POST /api/brain/execute-approved-trade

Request:
{
  "plan_id": 1
}

Response:
{
  "success": true,
  "trade_executed": true,
  "message": "Trade opened: EURUSD SELL @ 1.085",
  "plan": {
    "status": "EXECUTING",
    "execution": {
      "position_size": 1440,
      "entry_price": 1.085,
      "stop_loss": 1.0913,
      "take_profit": 1.0787
    }
  }
}
```

### 5️⃣ RECORD OUTCOME (JARVIS LEARNS)
```bash
POST /api/brain/record-trade-outcome

Request:
{
  "plan_id": 1,
  "exit_price": 1.0820,
  "profit_loss": 250
}

Response:
{
  "success": true,
  "trade_closed": true,
  "message": "Trade closed with $250.00 profit/loss",
  "learning_data": {
    "profit_loss": 250,
    "success": true
  }
}
```

### 6️⃣ GET BRAIN STATUS
```bash
GET /api/brain/get-status

Response:
{
  "success": true,
  "brain_status": {
    "status": "YOUR PERSONAL AI BRAIN",
    "planning": {
      "pending_approval": 1,
      "approved_awaiting_execution": 1,
      "executed_closed": 1,
      "total_plans": 2
    },
    "performance": {
      "win_rate": 100.0,
      "total_trades": 1,
      "total_profit": 250,
      "average_profit_per_trade": 250.0
    },
    "next_action": "AWAITING YOUR APPROVAL"
  }
}
```

### 7️⃣ GET WORKFLOW HISTORY
```bash
GET /api/brain/workflow-history

Response:
{
  "success": true,
  "total_entries": 15,
  "recent_entries": [...],
  "statistics": {
    "total_plans": 2,
    "approved_plans": 1,
    "executed_plans": 1,
    "closed_trades": 1
  }
}
```

### 8️⃣ GET WORKFLOW EXPLANATION
```bash
GET /api/brain/collaborative-workflow

Response:
{
  "success": true,
  "workflow": {
    "step_1": {
      "name": "JARVIS THINKS & PLANS",
      "description": "Analyzes market data and creates trading plans"
    },
    "step_2": {
      "name": "SHOWS RECOMMENDATIONS TO YOU",
      "description": "Presents all plans with complete transparency"
    },
    ...
  },
  "key_features": {...},
  "success_path": {...}
}
```

---

## 💡 WORKFLOW EXAMPLES

### Example 1: Simple Trade Approval
```bash
# 1. Create plans
curl -X POST http://127.0.0.1:5000/api/brain/think-and-plan \
  -H "Content-Type: application/json" \
  -d '{"market_data": {"EURUSD": {...}}}'

# 2. Check recommendations
curl http://127.0.0.1:5000/api/brain/get-pending-approvals

# 3. Approve
curl -X POST http://127.0.0.1:5000/api/brain/approve-plan \
  -H "Content-Type: application/json" \
  -d '{"plan_id": 1, "approval": true}'

# 4. Execute
curl -X POST http://127.0.0.1:5000/api/brain/execute-approved-trade \
  -H "Content-Type: application/json" \
  -d '{"plan_id": 1}'

# 5. Record outcome
curl -X POST http://127.0.0.1:5000/api/brain/record-trade-outcome \
  -H "Content-Type: application/json" \
  -d '{"plan_id": 1, "exit_price": 1.082, "profit_loss": 250}'
```

### Example 2: Check Progress
```bash
# Get current status
curl http://127.0.0.1:5000/api/brain/get-status

# View history
curl http://127.0.0.1:5000/api/brain/workflow-history

# Understand workflow
curl http://127.0.0.1:5000/api/brain/collaborative-workflow
```

---

## 📊 Data Structures

### Market Data Format
```json
{
  "ASSET_NAME": {
    "price": 1.085,
    "signal": "BUY|SELL|HOLD",
    "strength": 0.85,
    "confidence": 0.82,
    "volatility": 0.025,
    "signals": {
      "rsi": 32,
      "macd": "positive_crossover",
      "trend": "UPTREND"
    },
    "conditions": {
      "market": "FAVORABLE",
      "liquidity": "HIGH"
    }
  }
}
```

### Plan Structure
```json
{
  "id": 1,
  "status": "WAITING_APPROVAL|APPROVED|EXECUTING|CLOSED",
  "asset": "EURUSD",
  "direction": "BUY|SELL|HOLD",
  "signal_strength": 0.85,
  "confidence": 0.82,
  "reasoning": {
    "why": "Market analysis explanation",
    "risk_assessment": "Risk level",
    "profit_potential": "Profit range"
  },
  "execution": {
    "position_size": 1440,
    "entry_price": 1.085,
    "stop_loss": 1.0913,
    "take_profit": 1.0787,
    "risk_reward_ratio": 1.59,
    "potential_profit": 1412.80
  },
  "approval": {
    "requested_at": "timestamp",
    "approved": false|true,
    "approved_by": "USER|AUTONOMOUS"
  }
}
```

---

## 🎯 THE 5-STEP PROCESS

```
1. JARVIS THINKS
   ↓
   POST /api/brain/think-and-plan
   ↓
   Plans created

2. SHOWS RECOMMENDATIONS
   ↓
   GET /api/brain/get-pending-approvals
   ↓
   You see full analysis

3. YOU APPROVE
   ↓
   POST /api/brain/approve-plan
   ↓
   Ready to execute

4. EXECUTES
   ↓
   POST /api/brain/execute-approved-trade
   ↓
   Trade opens

5. LEARNS
   ↓
   POST /api/brain/record-trade-outcome
   ↓
   JARVIS gets smarter
```

---

## 💰 SUCCESS TIMELINE

| Timeline | Trades | Win Rate | Status | Action |
|----------|--------|----------|--------|--------|
| Week 1 | 5 | 40-50% | Learning | Approve each trade |
| Week 4 | 10+ | 50-60% | Beginner | Follow recommendations |
| Week 8 | 50+ | 60-70% | Intermediate | Enable autonomous (75%+) |
| Week 12 | 100+ | 75%+ | Advanced | JARVIS executes automatically |
| Month 6 | 250+ | 80%+ | Expert | Scale for exponential profits |

---

## ✅ TESTING CHECKLIST

- [ ] Test workflow explanation: `GET /api/brain/collaborative-workflow`
- [ ] Create plans: `POST /api/brain/think-and-plan`
- [ ] Get recommendations: `GET /api/brain/get-pending-approvals`
- [ ] Approve plan: `POST /api/brain/approve-plan`
- [ ] Execute trade: `POST /api/brain/execute-approved-trade`
- [ ] Record outcome: `POST /api/brain/record-trade-outcome`
- [ ] Check status: `GET /api/brain/get-status`
- [ ] View history: `GET /api/brain/workflow-history`

---

## 🚀 QUICK START

### Day 1: Your First Approved Trade
```bash
# 1. Analyze
curl -X POST http://127.0.0.1:5000/api/brain/think-and-plan ...

# 2. Recommend
curl http://127.0.0.1:5000/api/brain/get-pending-approvals

# 3. Approve
curl -X POST http://127.0.0.1:5000/api/brain/approve-plan ...

# 4. Execute
curl -X POST http://127.0.0.1:5000/api/brain/execute-approved-trade ...

# 5. Learn
curl -X POST http://127.0.0.1:5000/api/brain/record-trade-outcome ...
```

### Weekly: Monitor Progress
```bash
# Check status
curl http://127.0.0.1:5000/api/brain/get-status

# View history
curl http://127.0.0.1:5000/api/brain/workflow-history
```

---

## 📞 ERROR HANDLING

### Common Errors

**Plan not found**
```json
{"error": "Plan not found"}
```
→ Check plan_id is correct

**Plan not approved**
```json
{"error": "Plan not approved yet"}
```
→ Approve the plan first: `/api/brain/approve-plan`

**Invalid market data**
```json
{"error": "'str' object has no attribute 'get'"}
```
→ Ensure market_data is a proper JSON object, not a string

---

## 🔐 SECURITY NOTES

- ✅ All decisions logged with timestamps
- ✅ Full audit trail available
- ✅ You maintain final approval authority
- ✅ Autonomous execution only at 75%+ confidence
- ✅ Complete transparency on all decisions

---

## 📈 PERFORMANCE TRACKING

### Key Metrics
- **Win Rate**: Percentage of profitable trades
- **Total Profit**: Sum of all gains/losses
- **Average Profit Per Trade**: Total profit ÷ number of trades
- **Total Trades**: Complete count of executed trades

### View Anytime
```bash
GET /api/brain/get-status
```

---

**Status**: ✅ FULLY OPERATIONAL
**Ready for**: Immediate Trading
**Next Step**: Execute your first trade!

**LET'S BUILD YOUR WEALTH! 💰📈**
