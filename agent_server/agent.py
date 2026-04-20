import logging
import random
from datetime import datetime
from typing import Any, AsyncGenerator, Optional, Sequence, TypedDict

import mlflow
from databricks.sdk import WorkspaceClient
from databricks_langchain import ChatDatabricks
from fastapi import HTTPException
from langchain.agents import create_agent
from langchain_core.messages import AnyMessage
from langchain_core.tools import tool
from langgraph.graph.message import add_messages
from langgraph.store.base import BaseStore
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    to_chat_completions_input,
)
from typing_extensions import Annotated

from agent_server.prompts import SYSTEM_PROMPT
from agent_server.utils import (
    _get_or_create_thread_id,
    get_user_workspace_client,
    init_mcp_client,
    process_agent_astream_events,
)
from agent_server.utils_memory import (
    get_lakebase_access_error_message,
    get_user_id,
    init_lakebase_config,
    lakebase_context,
    memory_tools,
)

logger = logging.getLogger(__name__)
mlflow.langchain.autolog()
logging.getLogger("mlflow.utils.autologging_utils").setLevel(logging.ERROR)
sp_workspace_client = WorkspaceClient()

LLM_ENDPOINT_NAME = "databricks-claude-sonnet-4-5"
LAKEBASE_CONFIG = init_lakebase_config()


# ---------------------------------------------------------------------------
# Mock Data Store — Replace with real Databricks SQL / Unity Catalog queries
# in production
# ---------------------------------------------------------------------------

MOCK_ACCOUNTS = {
    "checking": {"balance": 12450.75, "account_number": "****4521", "currency": "INR"},
    "savings":  {"balance": 58320.00, "account_number": "****7893", "currency": "INR"},
}

MOCK_TRANSACTIONS = [
    {"date": "2026-04-16", "description": "Amazon Purchase",        "amount": -3450.00, "type": "debit",  "category": "Shopping"},
    {"date": "2026-04-15", "description": "Salary Credit",          "amount": 85000.00, "type": "credit", "category": "Income"},
    {"date": "2026-04-14", "description": "Electricity Bill",       "amount": -2100.00, "type": "debit",  "category": "Utilities"},
    {"date": "2026-04-13", "description": "Zomato Food Order",      "amount":  -650.00, "type": "debit",  "category": "Food & Dining"},
    {"date": "2026-04-12", "description": "Netflix Subscription",   "amount":  -649.00, "type": "debit",  "category": "Entertainment"},
    {"date": "2026-04-11", "description": "ATM Withdrawal",         "amount": -5000.00, "type": "debit",  "category": "Cash"},
    {"date": "2026-04-10", "description": "Freelance Payment",      "amount": 15000.00, "type": "credit", "category": "Income"},
    {"date": "2026-04-09", "description": "Uber Ride",              "amount":  -320.00, "type": "debit",  "category": "Transport"},
    {"date": "2026-04-08", "description": "Mobile Recharge",        "amount":  -299.00, "type": "debit",  "category": "Utilities"},
    {"date": "2026-04-07", "description": "Grocery Store",          "amount": -1850.00, "type": "debit",  "category": "Groceries"},
]

MOCK_LOAN = {
    "loan_id": "LN-20240312-881",
    "loan_type": "Home Loan",
    "principal": 2500000.00,
    "outstanding": 2187450.00,
    "emi": 22500.00,
    "emi_due_date": "5th of every month",
    "next_emi_date": "2026-05-05",
    "tenure_remaining_months": 97,
    "interest_rate": "8.5% p.a.",
    "currency": "INR",
}

MOCK_SPENDING = {
    "April 2026": {
        "Shopping":     3450.00,
        "Food & Dining": 650.00,
        "Utilities":    2399.00,
        "Entertainment": 649.00,
        "Transport":     320.00,
        "Cash":         5000.00,
        "Groceries":    1850.00,
    },
    "March 2026": {
        "Shopping":     6200.00,
        "Food & Dining": 980.00,
        "Utilities":    2100.00,
        "Entertainment": 649.00,
        "Transport":     870.00,
        "Cash":         8000.00,
        "Groceries":    2340.00,
    },
}


@tool
def get_account_balance(account_type: str) -> str:
    """
    Get the current balance of a bank account.

    Args:
        account_type: The type of account — 'checking' or 'savings'.

    Returns:
        A formatted string showing the account balance and account number.
    """
    account_type = account_type.lower().strip()
    if account_type not in MOCK_ACCOUNTS:
        return (
            f"Account type '{account_type}' not found. "
            "Available account types are: checking, savings."
        )
    acc = MOCK_ACCOUNTS[account_type]
    return (
        f"**{account_type.capitalize()} Account** ({acc['account_number']})\n"
        f"Current Balance: **{acc['currency']} {acc['balance']:,.2f}**\n"
        f"As of: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"
    )


@tool
def get_recent_transactions(limit: int = 5) -> str:
    """
    Retrieve the most recent transactions from the customer's account.

    Args:
        limit: Number of recent transactions to retrieve (default: 5, max: 10).

    Returns:
        A formatted list of recent transactions with date, description, amount, and category.
    """
    limit = min(max(1, limit), 10)
    transactions = MOCK_TRANSACTIONS[:limit]

    lines = [f"**Last {limit} Transactions:**\n"]
    for txn in transactions:
        sign = "+" if txn["type"] == "credit" else "-"
        amount_str = f"{sign}₹{abs(txn['amount']):,.2f}"
        lines.append(
            f"• **{txn['date']}** | {txn['description']}\n"
            f"  Amount: {amount_str} | Category: {txn['category']}"
        )
    return "\n".join(lines)


@tool
def get_loan_details() -> str:
    """
    Retrieve the customer's active loan details including outstanding balance,
    EMI amount, next due date, and interest rate.

    Returns:
        A formatted summary of the customer's loan details.
    """
    loan = MOCK_LOAN
    return (
        f"**Loan Details — {loan['loan_type']}**\n"
        f"• Loan ID: {loan['loan_id']}\n"
        f"• Principal Amount: ₹{loan['principal']:,.2f}\n"
        f"• Outstanding Balance: ₹{loan['outstanding']:,.2f}\n"
        f"• Monthly EMI: ₹{loan['emi']:,.2f}\n"
        f"• EMI Due Date: {loan['emi_due_date']}\n"
        f"• Next EMI Date: {loan['next_emi_date']}\n"
        f"• Remaining Tenure: {loan['tenure_remaining_months']} months\n"
        f"• Interest Rate: {loan['interest_rate']}"
    )


@tool
def transfer_funds(from_account: str, to_account: str, amount: float) -> str:
    """
    Transfer funds between the customer's accounts.
    NOTE: Always confirm with the user BEFORE calling this tool.

    Args:
        from_account: Source account type — 'checking' or 'savings'.
        to_account:   Destination account type — 'checking' or 'savings'.
        amount:       Amount to transfer (must be positive).

    Returns:
        A success or failure message for the transfer.
    """
    from_account = from_account.lower().strip()
    to_account = to_account.lower().strip()

    # Validation
    if from_account not in MOCK_ACCOUNTS:
        return f"Invalid source account: '{from_account}'. Use 'checking' or 'savings'."
    if to_account not in MOCK_ACCOUNTS:
        return f"Invalid destination account: '{to_account}'. Use 'checking' or 'savings'."
    if from_account == to_account:
        return "Source and destination accounts cannot be the same."
    if amount <= 0:
        return "Transfer amount must be greater than zero."
    if MOCK_ACCOUNTS[from_account]["balance"] < amount:
        return (
            f"Insufficient funds. Your {from_account} account balance is "
            f"₹{MOCK_ACCOUNTS[from_account]['balance']:,.2f}, "
            f"which is less than the requested ₹{amount:,.2f}."
        )

    # Execute transfer (mock — updates in-memory data)
    MOCK_ACCOUNTS[from_account]["balance"] -= amount
    MOCK_ACCOUNTS[to_account]["balance"] += amount

    ref_number = f"TXN{random.randint(1000000, 9999999)}"
    return (
        f"✅ **Transfer Successful!**\n"
        f"• From: {from_account.capitalize()} Account\n"
        f"• To: {to_account.capitalize()} Account\n"
        f"• Amount: ₹{amount:,.2f}\n"
        f"• Reference Number: {ref_number}\n"
        f"• Date & Time: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}\n\n"
        f"Updated Balances:\n"
        f"• {from_account.capitalize()}: ₹{MOCK_ACCOUNTS[from_account]['balance']:,.2f}\n"
        f"• {to_account.capitalize()}: ₹{MOCK_ACCOUNTS[to_account]['balance']:,.2f}"
    )


@tool
def get_spending_summary(month: str = "April 2026") -> str:
    """
    Get a category-wise spending summary for a given month.

    Args:
        month: The month to get the summary for (e.g., 'April 2026', 'March 2026').

    Returns:
        A formatted category-wise breakdown of spending for that month.
    """
    if month not in MOCK_SPENDING:
        available = ", ".join(MOCK_SPENDING.keys())
        return f"Spending data for '{month}' not available. Available months: {available}."

    categories = MOCK_SPENDING[month]
    total = sum(categories.values())

    lines = [f"**Spending Summary — {month}**\n"]
    for category, amount in sorted(categories.items(), key=lambda x: x[1], reverse=True):
        pct = (amount / total) * 100
        lines.append(f"• {category:<20} ₹{amount:>9,.2f}  ({pct:.1f}%)")

    lines.append(f"\n**Total Spent: ₹{total:,.2f}**")
    return "\n".join(lines)


@tool
def get_current_time() -> str:
    """Get the current date and time."""
    return datetime.now().isoformat()


class StatefulAgentState(TypedDict, total=False):
    messages: Annotated[Sequence[AnyMessage], add_messages]
    custom_inputs: dict[str, Any]
    custom_outputs: dict[str, Any]




# ---------------------------------------------------------------------------
# Agent Initialization — registers all banking tools
# ---------------------------------------------------------------------------

BANKING_TOOLS = [
    get_current_time,
    get_account_balance,
    get_recent_transactions,
    get_loan_details,
    transfer_funds,
    get_spending_summary,
]


async def init_agent(
    store: BaseStore,
    workspace_client: Optional[WorkspaceClient] = None,
    checkpointer: Optional[Any] = None,
):
    tools = memory_tools() + BANKING_TOOLS
    # To use MCP server tools instead, uncomment the below lines:
    # mcp_client = init_mcp_client(workspace_client or sp_workspace_client)
    # try:
    #     tools.extend(await mcp_client.get_tools())
    # except Exception:
    #     logger.warning("Failed to fetch MCP tools. Continuing without MCP tools.", exc_info=True)

    model = ChatDatabricks(endpoint=LLM_ENDPOINT_NAME)

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
        store=store,
        state_schema=StatefulAgentState,
    )


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    outputs = [
        event.item
        async for event in stream_handler(request)
        if event.type == "response.output_item.done"
    ]

    custom_outputs: dict[str, Any] = {}
    if user_id := get_user_id(request):
        custom_outputs["user_id"] = user_id
    return ResponsesAgentResponse(output=outputs, custom_outputs=custom_outputs)


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    thread_id = _get_or_create_thread_id(request)
    mlflow.update_current_trace(metadata={"mlflow.trace.session": thread_id})

    user_id = get_user_id(request)
    if not user_id:
        logger.warning("No user_id provided - memory features will not be available")

    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if user_id:
        config["configurable"]["user_id"] = user_id

    input_state: dict[str, Any] = {
        "messages": to_chat_completions_input([i.model_dump() for i in request.input]),
        "custom_inputs": dict(request.custom_inputs or {}),
    }

    try:
        async with lakebase_context(LAKEBASE_CONFIG) as (checkpointer, store):
            config["configurable"]["store"] = store

            # By default, uses service principal credentials.
            # For on-behalf-of user authentication, pass get_user_workspace_client() to init_agent.
            agent = await init_agent(store=store, checkpointer=checkpointer)

            async for event in process_agent_astream_events(
                agent.astream(input_state, config, stream_mode=["updates", "messages"])
            ):
                yield event
    except Exception as e:
        error_msg = str(e).lower()
        # Check for Lakebase access/connection errors
        if any(
            keyword in error_msg
            for keyword in ["lakebase", "pg_hba", "postgres", "database instance"]
        ):
            logger.error("Lakebase access error: %s", e)
            raise HTTPException(
                status_code=503,
                detail=get_lakebase_access_error_message(LAKEBASE_CONFIG.description),
            ) from e
        raise
