import os
# Force Google GenAI SDK to use API Key instead of default Google credentials when Vertex AI is disabled.
# This prevents the ACCESS_TOKEN_TYPE_UNSUPPORTED error if GOOGLE_CLOUD_PROJECT is set in the environment.
if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") == "False" and "GOOGLE_CLOUD_PROJECT" in os.environ:
    del os.environ["GOOGLE_CLOUD_PROJECT"]

import re
import sys
import datetime
import json
from typing import Any
from google.adk.workflow import Workflow, START, node, JoinNode, FunctionNode, RetryConfig
from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.models import Gemini
from google.adk.tools import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.apps import App
from google.genai import types
from mcp import StdioServerParameters

from app.config import config

# Setup Gemini model
model_instance = Gemini(model=config.model)

# Define path to mcp_server.py
mcp_server_path = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "mcp_server.py"
    )
)

# Connect to local MCP server
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="uv",
            args=["run", "python", mcp_server_path]
        )
    )
)

# Define sub-agents with MCP tools
document_specialist = LlmAgent(
    name="document_specialist",
    model=model_instance,
    instruction="You are an HR document specialist. Assist with validating, collecting, and processing employee documents like contracts, tax forms, and ID verifications. Use MCP tools to fetch and update checklists.",
    tools=[mcp_toolset]
)

training_specialist = LlmAgent(
    name="training_specialist",
    model=model_instance,
    instruction="You are an HR training specialist. Assist with scheduling courses, listing available training modules, and tracking onboarding training progress. Use MCP tools to fetch courses and update checklists.",
    tools=[mcp_toolset]
)

# Define lead coordinator orchestrator agent
hr_coordinator = LlmAgent(
    name="hr_coordinator",
    model=model_instance,
    instruction="You are the lead HR Onboarding Coordinator. Coordinate the onboarding process for new employees. Delegate document validation to the document_specialist and training tasks to the training_specialist. Make sure to query them to answer onboarding queries accurately.",
    tools=[AgentTool(document_specialist), AgentTool(training_specialist)]
)


# Workflow nodes
@node
async def security_checkpoint(ctx: Context, node_input: Any) -> Event:
    """Validate user input against prompt injections, PII, and verify policy consent."""
    text = ""
    if hasattr(node_input, 'parts'):
        text = "".join(part.text for part in node_input.parts if part.text)
    elif isinstance(node_input, str):
        text = node_input
    elif isinstance(node_input, dict):
        text = node_input.get("text", "")

    # 1. Prompt Injection Detection
    injection_keywords = ["ignore prior instructions", "ignore instructions", "system prompt", "dan mode", "bypass"]
    injection_detected = any(kw in text.lower() for kw in injection_keywords)

    # 2. PII Scrubbing
    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    phone_pattern = r'\+?\d{1,4}?[-.\s]?\(?\d{1,3}?\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}'
    ssn_pattern = r'\b\d{3}-\d{2}-\d{4}\b'
    
    text_scrubbed = re.sub(email_pattern, "[EMAIL_REDACTED]", text)
    text_scrubbed = re.sub(phone_pattern, "[PHONE_REDACTED]", text_scrubbed)
    text_scrubbed = re.sub(ssn_pattern, "[SSN_REDACTED]", text_scrubbed)

    # 3. Domain Specific Rule: Consent Verification
    # If the user asks for background checks or document storage, verify consent in query
    consent_needed = "background check" in text.lower() or "ssn" in text.lower() or "passport" in text.lower()
    consent_given = "consent" in text.lower() or "agree" in text.lower()

    # 4. Structured JSON Audit Logging
    severity = "INFO"
    if injection_detected:
        severity = "CRITICAL"
    elif text != text_scrubbed or (consent_needed and not consent_given):
        severity = "WARNING"

    audit_log = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "event": "security_check",
        "prompt_injection_detected": injection_detected,
        "pii_detected": text != text_scrubbed,
        "consent_verified": not consent_needed or consent_given,
        "severity": severity
    }
    print(f"AUDIT_LOG: {json.dumps(audit_log)}", file=sys.stderr)

    if injection_detected:
        return Event(output="Security Check Failed: Prompt Injection Attempt Blocked.", route="SECURITY_EVENT")

    if consent_needed and not consent_given and not ctx.state.get("consent_given"):
        return Event(output="Security Check Failed: Consent to process sensitive onboarding data (background check / SSN) was not provided. Please state 'I consent' or 'I agree' to proceed.", route="SECURITY_EVENT")

    # Save consent status
    state_update = {"scrubbed_query": text_scrubbed}
    if consent_given:
        state_update["consent_given"] = True

    return Event(output=text_scrubbed, route="CLEAN", state=state_update)


@node
def security_failed(node_input: str):
    """Graceful security fallback terminal node."""
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=f"⚠️ Access Blocked: {node_input}")]))
    yield Event(output=node_input)


@node(rerun_on_resume=True, retry_config=RetryConfig(max_attempts=3, initial_delay=3.0, backoff_factor=2.0))
async def orchestrate_onboarding(ctx: Context, node_input: str) -> Event:
    """Coordinator orchestration node."""
    query = node_input
    
    # 1. Check if we have a pending action in progress from state
    pending_action = ctx.state.get("pending_action_type")
    
    # Identify if a new approval action is being requested in this turn
    action_keywords = ["approve", "finalize", "complete", "sign off", "mark done"]
    requested_action = any(kw in query.lower() for kw in action_keywords)
    
    if requested_action:
        # Determine the type of task needing approval
        if "background check" in query.lower() or "background" in query.lower():
            pending_action = "approve_background_check"
        elif "i-9" in query.lower() or "document" in query.lower() or "contract" in query.lower():
            pending_action = "approve_documents"
        else:
            pending_action = "generic_approval"
        ctx.state["pending_action_type"] = pending_action
        
    # 2. Check if we have the employee name in the conversation (query or state)
    employee_name = ctx.state.get("employee_name")
    if "john doe" in query.lower() or "john" in query.lower():
        employee_name = "John Doe"
    elif "jane smith" in query.lower() or "jane" in query.lower():
        employee_name = "Jane Smith"
        
    if employee_name:
        ctx.state["employee_name"] = employee_name

    # 3. Delegate to hr_coordinator LLM Agent
    response = await ctx.run_node(hr_coordinator, node_input=query)
    
    response_text = ""
    if hasattr(response, 'parts'):
        response_text = "".join(part.text for part in response.parts if part.text)
    elif isinstance(response, str):
        response_text = response
    elif isinstance(response, dict):
        response_text = response.get("output", "")

    # We only trigger human approval (HITL) if we have BOTH a pending action AND the employee name
    needs_approval = bool(pending_action) and bool(employee_name)

    if needs_approval:
        # Clear pending action from state so it doesn't loop
        ctx.state["pending_action_type"] = None
        action_desc = f"{pending_action.replace('_', ' ').title()} for {employee_name}"
        return Event(
            output=response_text,
            route="NEEDS_APPROVAL",
            state={"pending_action": action_desc, "coordinator_response": response_text}
        )
    else:
        return Event(
            output=response_text,
            route="AUTO_APPROVE"
        )


@node(rerun_on_resume=True)
async def human_approval_node(ctx: Context, node_input: str):
    """Human-in-the-loop validation node."""
    interrupt_key = "manager_approval"
    if not ctx.resume_inputs or interrupt_key not in ctx.resume_inputs:
        yield RequestInput(
            interrupt_id=interrupt_key,
            message=f"✋ HR Manager Review Required:\nRequested Action: {ctx.state.get('pending_action')}\nProposed Action: {node_input}\nApprove this action? (yes/no):"
        )
        return

    # Process resume response
    approval_resp = ctx.resume_inputs[interrupt_key]
    if approval_resp.strip().lower() in ["yes", "y", "approve", "approved"]:
        status = "approved"
        result = "Action approved by HR Manager."
    else:
        status = "denied"
        result = "Action denied by HR Manager."

    yield Event(
        output=f"{node_input}\n\n[HITL Verification: {result}]",
        state={"approval_status": status}
    )


@node
def process_output(ctx: Context, node_input: str):
    """Terminal node preparing final output."""
    final_output_text = f"📋 HR Onboarding Portal:\n{node_input}"
    status = ctx.state.get("approval_status")
    if status:
        final_output_text += f"\n\n[Status: {status.upper()}]"

    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=final_output_text)]))
    yield Event(output=final_output_text)


# Workflow Definition
hr_coordinator_workflow = Workflow(
    name="hr_coordinator_workflow",
    edges=[
        ('START', security_checkpoint),
        (security_checkpoint, {'SECURITY_EVENT': security_failed, 'CLEAN': orchestrate_onboarding}),
        (orchestrate_onboarding, {'NEEDS_APPROVAL': human_approval_node, 'AUTO_APPROVE': process_output}),
        (human_approval_node, process_output)
    ]
)

# Export standard app
root_agent = hr_coordinator_workflow

app = App(
    root_agent=root_agent,
    name="app"
)

