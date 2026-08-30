from rest_framework.throttling import SimpleRateThrottle


class VerificationEmailRateThrottle(SimpleRateThrottle):
    scope = "verification_email"

    def get_cache_key(self, request, view):
        email = request.data.get("email", "").strip().lower()

        if not email:
            return None

        return self.cache_format % {
            "scope": self.scope,
            "ident": email,
        }


class VerificationEmailIPRateThrottle(SimpleRateThrottle):
    scope = "verification_email_ip"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)

        if not ident:
            return None

        return self.cache_format % {
            "scope": self.scope,
            "ident": ident,
        }