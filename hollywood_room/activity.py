"""Human-readable action receipts; never publish internal reasoning or raw tool output."""


def action_text(item, completed):
    kind = item.get("type")
    failed = item.get("status") in {"failed", "declined"} or item.get("success") is False
    if kind == "commandExecution":
        verb = "Command failed" if failed else "Ran command" if completed else "Running command"
        return f"{verb}\n{item.get('command', '')[:1200]}"
    if kind == "fileChange":
        verb = "File edit failed" if failed else "Updated files" if completed else "Editing files"
        paths = "\n".join(change.get("path", "") for change in item.get("changes", [])[:12])
        return f"{verb}\n{paths[:1200]}"
    if kind in {"mcpToolCall", "dynamicToolCall"}:
        tool = item.get("tool", "tool")
        # Room contributions are already represented by their own durable messages.
        if tool.startswith("hollywood_"):
            return None
        verb = "Tool failed" if failed else "Finished" if completed else "Using"
        return f"{verb} {tool[:120]}"
    if kind == "webSearch":
        return "Finished web search" if completed else "Searching the web"
    if kind == "plan" and completed:
        return f"Plan\n{item.get('text', '')[:1600]}"
    return None
