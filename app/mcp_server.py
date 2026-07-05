from fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("HR Onboarding Server")

# In-memory mock database for onboarding checklist
CHECKLISTS = {
    "John Doe": {
        "Signing Employment Contract": "completed",
        "Form I-9 Verification": "pending",
        "Direct Deposit Details": "pending",
        "W-4 Tax Form": "pending",
        "IT Laptop Setup": "completed"
    },
    "Jane Smith": {
        "Signing Employment Contract": "completed",
        "Form I-9 Verification": "completed",
        "Direct Deposit Details": "completed",
        "W-4 Tax Form": "completed",
        "IT Laptop Setup": "completed"
    }
}

# In-memory mock training course list
COURSES = [
    {"id": "SEC101", "name": "Information Security Awareness", "required": True},
    {"id": "HR101", "name": "Workplace Conduct & Anti-Harassment", "required": True},
    {"id": "DEV201", "name": "Introduction to Development Best Practices", "required": False}
]

# In-memory mock training progress tracking
TRAINING_PROGRESS = {
    "John Doe": {
        "SEC101": "pending",
        "HR101": "completed",
        "DEV201": "pending"
    },
    "Jane Smith": {
        "SEC101": "completed",
        "HR101": "completed",
        "DEV201": "completed"
    }
}


@mcp.tool()
def get_onboarding_checklist(employee_name: str) -> str:
    """Retrieve the onboarding checklist and task status for a given employee.

    Args:
        employee_name: The full name of the employee.
    """
    checklist = CHECKLISTS.get(employee_name)
    if not checklist:
        # Create a new checklist for unknown employees to support testing
        CHECKLISTS[employee_name] = {
            "Signing Employment Contract": "pending",
            "Form I-9 Verification": "pending",
            "Direct Deposit Details": "pending",
            "W-4 Tax Form": "pending",
            "IT Laptop Setup": "pending"
        }
        checklist = CHECKLISTS[employee_name]
    
    status_str = f"Onboarding Checklist for {employee_name}:\n"
    for task, status in checklist.items():
        status_str += f"- {task}: {status}\n"
    return status_str


@mcp.tool()
def update_checklist_item(employee_name: str, item_name: str, status: str) -> str:
    """Update the status of a specific task in the onboarding checklist for an employee.

    Args:
        employee_name: The full name of the employee.
        item_name: The task name or keyword matching a task (e.g. 'I-9', 'Tax', 'IT').
        status: The new status ('completed', 'pending', or 'in_progress').
    """
    checklist = CHECKLISTS.get(employee_name)
    if not checklist:
        return f"Employee '{employee_name}' not found."

    # Search for matching checklist item
    matched_item = None
    for task in checklist.keys():
        if item_name.lower() in task.lower():
            matched_item = task
            break

    if not matched_item:
        return f"No task matching '{item_name}' found on {employee_name}'s checklist."

    checklist[matched_item] = status
    return f"Updated task '{matched_item}' status to '{status}' for {employee_name}."


@mcp.tool()
def get_available_training_courses() -> str:
    """Retrieve the catalog of all available onboarding training courses."""
    courses_str = "Available Training Courses:\n"
    for c in COURSES:
        req_label = "Required" if c["required"] else "Optional"
        courses_str += f"- [{c['id']}] {c['name']} ({req_label})\n"
    return courses_str


@mcp.tool()
def get_employee_training_progress(employee_name: str) -> str:
    """Check training progress and course completion status for an employee.

    Args:
        employee_name: The full name of the employee.
    """
    progress = TRAINING_PROGRESS.get(employee_name)
    if not progress:
        # Initialize default progress
        TRAINING_PROGRESS[employee_name] = {
            "SEC101": "pending",
            "HR101": "pending",
            "DEV201": "pending"
        }
        progress = TRAINING_PROGRESS[employee_name]

    progress_str = f"Training Progress for {employee_name}:\n"
    for course_id, status in progress.items():
        # Match course name
        course_name = next((c["name"] for c in COURSES if c["id"] == course_id), "Unknown Course")
        progress_str += f"- [{course_id}] {course_name}: {status}\n"
    return progress_str


if __name__ == "__main__":
    mcp.run(transport="stdio")
