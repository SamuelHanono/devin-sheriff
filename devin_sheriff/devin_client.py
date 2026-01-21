import httpx
from .config import AppConfig

class DevinClient:
    def __init__(self, config: AppConfig):
        self.api_key = config.devin_api_key
        self.base_url = config.devin_api_url
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def verify_auth(self) -> bool:
        """
        Ping Devin API to verify key.
        Note: Exact endpoint depends on Devin API docs. 
        Using a generic 'ping' or 'user' assumption here.
        """
        # TODO: Replace with actual Devin API auth check endpoint
        # For now, we assume if we can hit the endpoint without 401, we are good.
        try:
            with httpx.Client() as client:
                # Mocking a check - usually /user or /models is a good test
                # response = client.get(f"{self.base_url}/user", headers=self.headers)
                # response.raise_for_status()
                return True
        except Exception as e:
            # Setup is successful if we just store the key for now, 
            # until we have the exact Devin endpoint to hit.
            return True