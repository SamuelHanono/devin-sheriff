import httpx
import json
import time
import re
import logging
from typing import Optional, Dict, Any
from .config import AppConfig

# --- LOGGING SETUP ---
logger = logging.getLogger("devin_client")
logging.basicConfig(level=logging.INFO)

class DevinClient:
    def __init__(self, config: AppConfig):
        self.api_key = config.devin_api_key
        self.base_url = config.devin_api_url
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
        except Exception as e:
            logger.error(f"Devin Auth Failed: {e}")
            return False

    def _wait_for_session(self, session_id: str, timeout_seconds=300) -> Dict[str, Any]:
        """Polls the session until it stops or completes."""
        start_time = time.time()
        
        logger.info(f"⏳ Waiting for Session {session_id} (Timeout: {timeout_seconds}s)...")
        
        with httpx.Client(timeout=30.0) as client:
            while time.time() - start_time < timeout_seconds:
                try:
                    resp = client.get(f"{self.base_url}/sessions/{session_id}", headers=self.headers)
                    resp.raise_for_status()
                    data = resp.json()
                    
                    # Safely handle if status_enum is None
                    raw_status = data.get("status_enum")
                    status = (raw_status or "").lower() 
                    
                    if status in ["stopped", "completed", "terminated", "blocked", "finished"]:
                        logger.info(f"✅ Session {session_id} finished with status: {status}")
                        return data
                    
                    if status == "error":
                         raise Exception(f"Devin Session Error: {data}")
                    
                except httpx.RequestError as e:
                    logger.warning(f"Network glitch, retrying: {e}")

                time.sleep(5) # Poll every 5 seconds
                
        raise TimeoutError(f"Devin session {session_id} timed out after {timeout_seconds}s")

    def _extract_last_json(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Digs through session history to find the JSON output we asked for.
        """
        # 1. Try structured output if available (Future-proofing)
        if "structured_output" in session_data and session_data["structured_output"]:
            logger.info("Found structured_output in session data")
            return session_data["structured_output"]

        # 2. Parse last assistant message
        session_id = session_data.get("session_id")
        if not session_id:
            logger.error("No session_id in session data")
            return {"error": "Invalid session data", "raw": session_data}

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(f"{self.base_url}/sessions/{session_id}/events", headers=self.headers)
                logger.info(f"Fetched events for session {session_id}, status: {resp.status_code}")
                
                if resp.status_code == 200:
                    events = resp.json()
                    logger.info(f"Found {len(events)} events in session")
                    
                    # Look for the last message from 'assistant'
                    for event in reversed(events):
                        if event.get("type") == "assistant_message":
                            content = event.get("message", {}).get("content", "")
                            logger.info(f"Found assistant message, length: {len(content)}")
                            
                            # Try to find JSON block
                            json_match = re.search(r"\{.*\}", content, re.DOTALL)
                            if json_match:
                                try:
                                    result = json.loads(json_match.group(0))
                                    logger.info(f"Successfully parsed JSON with keys: {list(result.keys())}")
                                    return result
                                except json.JSONDecodeError as e:
                                    logger.warning(f"JSON decode error: {e}")
                                    continue
                else:
                    logger.error(f"Failed to fetch events: {resp.status_code}")
        except Exception as e:
            logger.error(f"Error fetching events: {e}")
        
        # Fallback if parsing fails
        logger.warning("Could not extract JSON from session, returning error dict")
        return {
            "error": "Could not parse JSON", 
            "raw_debug": "Devin finished but didn't return strict JSON."
        }

    def start_scope_session(self, repo_url: str, issue_number: int, title: str, body: str):
        """
        Starts a Scope session. Timeout: 5 minutes.
        """
        system_prompt = (
            "You are a Senior Software Architect. Your goal is to SCOPE a GitHub issue.\n"
            f"1. Clone the repository: {repo_url}\n"
            "2. Analyze the issue described below. Read the code to understand the root cause.\n"
            "3. Return a JSON object with this EXACT structure:\n"
            "{\n"
            '  "summary": "Brief summary of the problem",\n'
            '  "files_to_change": ["list", "of", "files"],\n'
            '  "action_plan": ["step 1", "step 2", "step 3"],\n'
            '  "confidence": 85\n'
            "}\n"
            "Return ONLY raw JSON. No markdown formatting."
        )

        user_message = f"Issue #{issue_number}: {title}\n\n{body}"

        payload = {
            "prompt": f"{system_prompt}\n\nTask: {user_message}",
            "idempotent": True 
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(f"{self.base_url}/sessions", json=payload, headers=self.headers)
                resp.raise_for_status()
                session_id = resp.json()["session_id"]
                logger.info(f"🚀 Started Scope Session: {session_id}")
                
                final_data = self._wait_for_session(session_id, timeout_seconds=300)
                return self._extract_last_json(final_data)
        except Exception as e:
            logger.error(f"Scope Session Failed: {e}")
            raise e

    def start_rescope_session(self, repo_url: str, issue_number: int, title: str, body: str, 
                               previous_plan: dict, refinement_notes: str):
        """
        Re-scope an issue with user refinement notes.
        This passes the previous plan and user's feedback to generate a better plan.
        """
        system_prompt = (
            "You are a Senior Software Architect. Your goal is to RE-SCOPE a GitHub issue based on user feedback.\n"
            f"1. Clone the repository: {repo_url}\n"
            "2. Review the PREVIOUS PLAN that was generated:\n"
            f"{json.dumps(previous_plan, indent=2)}\n\n"
            "3. The user has provided the following REFINEMENT NOTES:\n"
            f'"{refinement_notes}"\n\n'
            "4. Generate a NEW, IMPROVED plan that incorporates the user's feedback.\n"
            "5. Return a JSON object with this EXACT structure:\n"
            "{\n"
            '  "summary": "Brief summary of the problem",\n'
            '  "files_to_change": ["list", "of", "files"],\n'
            '  "action_plan": ["step 1", "step 2", "step 3"],\n'
            '  "confidence": 85,\n'
            '  "refinement_applied": "Brief note on how user feedback was incorporated"\n'
            "}\n"
            "Return ONLY raw JSON. No markdown formatting."
        )

        user_message = f"Issue #{issue_number}: {title}\n\n{body}"

        payload = {
            "prompt": f"{system_prompt}\n\nOriginal Issue: {user_message}",
            "idempotent": True 
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(f"{self.base_url}/sessions", json=payload, headers=self.headers)
                resp.raise_for_status()
                session_id = resp.json()["session_id"]
                logger.info(f"🔄 Started Re-Scope Session: {session_id}")
                
                final_data = self._wait_for_session(session_id, timeout_seconds=300)
                return self._extract_last_json(final_data)
        except Exception as e:
            logger.error(f"Re-Scope Session Failed: {e}")
            raise e

    def start_execute_session(self, repo_url: str, issue_number: int, title: str, plan_json: dict):
        """
        Starts an Execution session. Timeout: 10 minutes.
        """
        system_prompt = (
            "You are a Senior DevOps Engineer. Your goal is to FIX a GitHub issue.\n"
            f"1. Clone the repository: {repo_url}\n"
            f"2. Follow this APPROVED PLAN exactly:\n{json.dumps(plan_json)}\n"
            "3. Create a new branch, write the code, run tests to verify.\n"
            "4. Commit changes and push the branch.\n"
            f"5. Open a Pull Request. **IMPORTANT:** The PR description MUST include 'Closes #{issue_number}'.\n"
            "6. Return JSON: { \"pr_url\": \"...\", \"summary\": \"...\" }"
        )

        payload = {
            "prompt": f"{system_prompt}\n\nIssue #{issue_number}: {title}",
            "idempotent": True
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(f"{self.base_url}/sessions", json=payload, headers=self.headers)
                resp.raise_for_status()
                data = resp.json()
                session_id = data["session_id"]
                logger.info(f"🚀 Started Execute Session: {session_id}")
                
                # Wait for completion (10 min timeout for fixes)
                final_data = self._wait_for_session(session_id, timeout_seconds=600)
                return self._extract_last_json(final_data)
        except Exception as e:
            logger.error(f"Execute Session Failed: {e}")
            raise e
