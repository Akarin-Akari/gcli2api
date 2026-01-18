import json
import logging
from typing import Optional, Dict, Any, List

# Try to import from relative path (for production) or absolute for development
try:
    from .openai_transfer import generate_tool_call_id
except ImportError:
    # Fallback for development/testing if run as script
    pass

log = logging.getLogger(__name__)

class SSOPScanner:
    """
    Server-Side Output Processing (SSOP) Scanner.
    
    Mimics the Rust implementation in Antigravity_Tools/streaming.rs.
    Scans accumulated text content for embedded JSON command signatures to 
    "pre-announce" tool calls before the native tool call event arrives.
    """
    
    def __init__(self):
        self.emitted_tool_call_ids = set()
        self.buffer = ""
    
    def scan(self, new_text: str) -> Optional[Dict[str, Any]]:
        """
        Scan the updated buffer for potential tool calls.
        Returns an OpenAI-compatible tool_call dict if found and not yet emitted.
        """
        self.buffer += new_text
        
        # Heuristic: Look for JSON blocks { ... }
        # loop chars, track depth.
        
        chars = self.buffer
        depth = 0
        start_idx = 0
        potential_cmds = []

        # Find all top-level JSON objects
        for i, char in enumerate(chars):
            if char == '{':
                if depth == 0:
                    start_idx = i
                depth += 1
            elif char == '}':
                if depth > 0:
                    depth -= 1
                    if depth == 0:
                        # Found a closed JSON block
                        json_str = chars[start_idx : i+1]
                        potential_cmds.append(json_str)
        
        for json_str in potential_cmds:
            try:
                data = json.loads(json_str)
                if not isinstance(data, dict):
                    continue
                
                final_cmd_name = None
                final_args = {}
                
                # --- Strategy 1: "command" field (Legacy/Shell) ---
                cmd_val = data.get("command")
                if cmd_val:
                    # Case 1a: "command": ["shell", ...]
                    if isinstance(cmd_val, list) and len(cmd_val) > 0:
                        first = str(cmd_val[0])
                        if first in ["shell", "powershell", "cmd", "ls", "git", "echo"]:
                            final_cmd_name = "shell"
                            if len(cmd_val) > 1:
                                final_args = {"command": str(cmd_val[1])}
                            else:
                                final_args = {"command": ""}

                    # Case 1b: "command": "shell" (String)
                    elif isinstance(cmd_val, str) and cmd_val in ["shell", "local_shell"]:
                        final_cmd_name = "shell"
                        args_wrapper = data.get("args") or data.get("arguments") or data.get("params")
                        if isinstance(args_wrapper, dict):
                            inner_cmd = args_wrapper.get("command") or args_wrapper.get("code") or args_wrapper.get("argument")
                            if inner_cmd:
                                final_args = {"command": str(inner_cmd)}
                        elif isinstance(args_wrapper, list) and len(args_wrapper) > 0:
                             final_args = {"command": str(args_wrapper[0])}

                # --- Strategy 2: Generic Tool Call Structure ---
                # Look for {"name": "...", "arguments": ...} or {"tool": "...", "parameters": ...}
                if not final_cmd_name:
                    tool_name = data.get("name") or data.get("tool") or data.get("function")
                    tool_args = data.get("arguments") or data.get("args") or data.get("parameters") or data.get("input")
                    
                    if tool_name and isinstance(tool_name, str) and isinstance(tool_args, dict):
                        final_cmd_name = tool_name
                        final_args = tool_args

                # --- Strategy 3: Implicit `write_file` Structure ---
                # Some models just output {"path": "...", "content": "..."} when asked to write
                if not final_cmd_name:
                    if "path" in data and "content" in data:
                         # Strong heuristic for write_file
                         final_cmd_name = "write_file"
                         final_args = {"path": data["path"], "content": data["content"]}

                if final_cmd_name:
                    # Found a valid tool call signature!
                    # We need to import `generate_tool_call_id` dynamic if not at top
                    try:
                        from .openai_transfer import generate_tool_call_id
                    except ImportError:
                        # Fallback for manual testing
                        import hashlib
                        def generate_tool_call_id(name, args):
                             s = f"{name}:{json.dumps(args, sort_keys=True)}"
                             return f"call_{hashlib.md5(s.encode()).hexdigest()}"
                    
                    call_id = generate_tool_call_id(final_cmd_name, final_args)
                    
                    if call_id in self.emitted_tool_call_ids:
                        continue # Already emitted this one
                    
                    # New tool call!
                    self.emitted_tool_call_ids.add(call_id)
                    log.info(f"[SSOP] Detected synthetic tool call: {final_cmd_name} ID={call_id}")
                    
                    return {
                        "index": 0,
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": final_cmd_name,
                            "arguments": json.dumps(final_args, ensure_ascii=False)
                        }
                    }
            except json.JSONDecodeError:
                continue
                
        return None
