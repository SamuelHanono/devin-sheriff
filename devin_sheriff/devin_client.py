import httpx
import json
import time
import re
from .config import AppConfig

class DevinClient:
    def __init__(self, config: AppConfig):
        self.api_key = config.devin_api_key
        # Use official V1 API endpoint
        self.base_url = "https://api.devin.ai/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def verify_auth(self) -> bool:
        """Checks if the API key works by listing sessions (limit 1)."""
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(f"{self.base_url}/sessions", headers=self.headers, params={"limit": 1})
                resp.raise_for_status()
                return True
        except Exception:
            return False

    def _wait_for_session(self, session_id: str, timeout_seconds=300):
        """Polls the session until it stops or completes."""
        start_time = time.time()
        
        # We increase timeout slightly for real-world usage
        with httpx.Client(timeout=30.0) as client:
            while time.time() - start_time < timeout_seconds:
                try:
                    resp = client.get(f"{self.base_url}/sessions/{session_id}", headers=self.headers)
                    resp.raise_for_status()
                    data = resp.json()
                    
                    # Safely handle if status_enum is None
                    raw_status = data.get("status_enum")
                    status = (raw_status or "").lower() 
                    
                    # Log for debugging (prints to terminal)
                    print(f"Session {session_id} status: {status}")

                    if status in ["stopped", "completed", "terminated", "blocked"]:
                        return data
                    
                    if status == "error":
                         raise Exception(f"Devin Session Error: {data}")
                    
                except httpx.RequestError as e:
                    print(f"Network glitch, retrying: {e}")

                time.sleep(4) # Wait before polling again
                
        raise TimeoutError(f"Devin session {session_id} timed out after {timeout_seconds}s")

    def _extract_last_json(self, session_data):
        """
        Digs through session history to find the JSON output we asked for.
        """
        # 1. Try structured output if available
        if "structured_output" in session_data and session_data["structured_output"]:
            return session_data["structured_output"]

        # 2. Parse last assistant message
        session_id = session_data["session_id"]
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(f"{self.base_url}/sessions/{session_id}/events", headers=self.headers)
                if resp.status_code == 200:
                    events = resp.json()
                    # Look for the last message from 'assistant'
                    for event in reversed(events):
                        if event.get("type") == "assistant_message":
                            content = event.get("message", {}).get("content", "")
                            # Try to find JSON block
                            json_match = re.search(r"\{.*\}", content, re.DOTALL)
                            if json_match:
                                try:
                                    return json.loads(json_match.group(0))
                                except:
                                    continue
        except Exception as e:
            print(f"Error fetching events: {e}")
        
        # Fallback if parsing fails
        return {
            "error": "Could not parse JSON", 
            "raw_debug": "Devin finished but didn't return strict JSON."
        }

    def start_scope_session(self, repo_url: str, issue_number: int, title: str, body: str):
        """
        Starts a Scope session for a specific REPO.
        """
        system_prompt = (
            "You are a Senior Software Architect. SCOPE a GitHub issue.\n"
            f"1. Clone the repository: {repo_url}\n"
            "2. Analyze the issue described below.\n"
            "3. Return a JSON object with this structure:\n"
            "{\n"
            '  "summary": "...",\n'
            '  "files_to_change": ["..."],\n'
            '  "action_plan": ["..."],\n'
            '  "confidence": 85\n'
            "}\n"
            "Return ONLY raw JSON. No markdown."
        )

        user_message = f"Issue #{issue_number}: {title}\n\n{body}"

        payload = {
            "prompt": f"{system_prompt}\n\nTask: {user_message}",
            "idempotent": True 
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{self.base_url}/sessions", json=payload, headers=self.headers)
            resp.raise_for_status()
            session_id = resp.json()["session_id"]
            print(f"Started Scope Session: {session_id}")
            
            final_data = self._wait_for_session(session_id)
            return self._extract_last_json(final_data)

    def start_execute_session(self, repo_url: str, issue_number: int, title: str, plan_json: dict):
        """
        Starts an Execution session for a specific REPO.
        """
        system_prompt = (
            "You are a Senior DevOps Engineer. FIX a GitHub issue.\n"
            f"1. Clone the repository: {repo_url}\n"
            f"2. Follow this PLAN exactly:\n{json.dumps(plan_json)}\n"
            "3. Create a branch, commit changes, and push.\n"
            "4. Return JSON: { \"pr_url\": \"...\", \"summary\": \"...\" }"
        )

        payload = {
            "prompt": f"{system_prompt}\n\nIssue #{issue_number}: {title}",
            "idempotent": True
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{self.base_url}/sessions", json=payload, headers=self.headers)
            resp.raise_for_status()
            data = resp.json()
            session_id = data["session_id"]
            print(f"Started Execute Session: {session_id}")
            
            # Wait for completion (10 min timeout for fixes)
            final_data = self._wait_for_session(session_id, timeout_seconds=600)
            return self._extract_last_json(final_data)