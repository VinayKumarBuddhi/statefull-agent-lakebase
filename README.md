# 🏦 FinBot — SecureBank AI Banking Assistant

A secure, conversational AI banking chatbot built on **LangGraph**, **MLflow**, and **Databricks**.  
FinBot helps customers check balances, review transactions, manage loans, transfer funds, and analyze spending — with full short-term and long-term memory.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 💬 **Conversational Chat UI** | Built-in Next.js chat interface at `http://localhost:8000` |
| 🧠 **Short-Term Memory** | Remembers context within a session (via Lakebase checkpoints) |
| 🗄️ **Long-Term Memory** | Persists user preferences across sessions (via Lakebase vector store) |
| 🔐 **Secure Transfers** | Always confirms before executing fund transfers |
| 📊 **Spending Analytics** | Category-wise spending summaries by month |
| 🔍 **MLflow Tracing** | Full audit trail of every agent decision in Databricks |
| ⚡ **Streaming Responses** | Real-time streamed responses via SSE |
| 🔄 **Background Tasks** | Handles long-running tasks without HTTP timeouts |

---

## 🛠️ Banking Tools

FinBot has access to the following tools:

| Tool | Description |
|------|-------------|
| `get_account_balance(account_type)` | Returns balance for `checking` or `savings` account |
| `get_recent_transactions(limit)` | Returns last N transactions (max 10) with category |
| `get_loan_details()` | Returns loan ID, EMI, outstanding balance, and next due date |
| `transfer_funds(from, to, amount)` | Transfers money between accounts with validation |
| `get_spending_summary(month)` | Category-wise spending breakdown for a given month |
| `get_current_time()` | Returns the current date and time |
| `get_user_memory(query)` | Searches long-term memory for user preferences |
| `save_user_memory(key, data)` | Saves user facts to long-term memory |
| `delete_user_memory(key)` | Deletes a specific memory entry |

---

## 📁 Project Structure

```
banking_chatbot/
├── agent_server/
│   ├── agent.py           # Banking tools + LangGraph agent initialization
│   ├── prompts.py         # FinBot system prompt & banking behavior rules
│   ├── start_server.py    # FastAPI server + MLflow setup
│   ├── utils.py           # Helper utilities
│   ├── utils_memory.py    # Short-term & long-term memory (Lakebase)
│   └── evaluate_agent.py  # Agent evaluation with MLflow
├── scripts/
│   ├── quickstart.py      # One-command setup script
│   ├── start_app.py       # Starts backend + frontend together
│   └── discover_tools.py  # Discovers available Databricks tools
├── .env                   # Local environment variables (DO NOT COMMIT)
├── .env.example           # Template for environment variables
├── app.yaml               # Databricks App configuration
├── databricks.yml         # Databricks Asset Bundle config
└── pyproject.toml         # Python project + dependencies
```

---

## Quick start

Run the `uv run quickstart` script to quickly set up your local environment and start the agent server. At any step, if there are issues, refer to the manual local development loop setup below.

This script will:

1. Verify uv, nvm, and Databricks CLI installations
2. Configure Databricks authentication
3. Configure Lakebase for memory storage
4. Configure agent tracing, by creating and linking an MLflow experiment to your app
5. Start the agent server and chat app

```bash
uv run quickstart
```

After the setup is complete, you can start the agent server and the chat app locally with:

```bash
uv run start-app
```

This will start the agent server and the chat app at http://localhost:8000.


---

## 💬 Example Conversations

```
User:  What is my account balance?
FinBot: Checking Account (****4521): ₹12,450.75
        Savings Account (****7893): ₹58,320.00

User:  Show my last 5 transactions
FinBot: Last 5 Transactions:
        • 2026-04-16 | Amazon Purchase       -₹3,450.00  (Shopping)
        • 2026-04-15 | Salary Credit         +₹85,000.00 (Income)
        • 2026-04-14 | Electricity Bill      -₹2,100.00  (Utilities)
        ...

User:  Transfer ₹5000 from checking to savings
FinBot: Please confirm the following transfer:
        • From: Checking Account
        • To:   Savings Account
        • Amount: ₹5,000.00
        Shall I proceed? (Yes / No)

User:  Yes
FinBot: ✅ Transfer Successful! Reference: TXN8472913
```

---

## 🧠 Memory System

### Short-Term Memory (within a session)
Pass a `thread_id` in `custom_inputs` to maintain context across multiple messages in the same session. Stored in Lakebase PostgreSQL checkpoint tables.

### Long-Term Memory (across sessions)
Pass a `user_id` in `custom_inputs` to enable cross-session memory. FinBot will remember:
- Your preferred language
- Your primary account type
- Any preferences you explicitly ask it to remember

---

## 📈 MLflow Tracing

Every conversation is traced in MLflow at:  
`Databricks Workspace → Machine Learning → Experiments → banking-chatbot-traces`

Each trace includes the full input, output, tools called, latency, and token usage — critical for banking auditability.

---

## 🚀 Deploying to Databricks Apps

```bash
# 1. Validate the bundle
databricks bundle validate

# 2. Deploy resources
databricks bundle deploy

# 3. Start the app
databricks bundle run banking_chatbot
```

---

## ⚠️ Important Notes

- **Mock Data**: The banking tools currently use simulated data. Replace the `MOCK_*` dictionaries in `agent.py` with real Databricks SQL / Unity Catalog queries for production.
- **Authentication**: The agent uses the `DEFAULT` Databricks profile locally. On Databricks Apps, it uses the app's service principal automatically.
- **Lakebase Permissions**: After deploying, grant your app's service principal access to the Lakebase PostgreSQL schemas. See the `databricks.yml` comments for the SQL grant commands.
