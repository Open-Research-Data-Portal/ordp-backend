from rest_framework.throttling import AnonRateThrottle


class WorkerRateThrottle(AnonRateThrottle):
    """Rate throttle for internal AI worker endpoints (120 requests/minute)."""
    rate = "120/min"
