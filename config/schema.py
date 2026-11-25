from drf_spectacular.openapi import AutoSchema
from drf_spectacular.types import OpenApiTypes


class WritableBodySchema(AutoSchema):
    """Show a JSON box in Swagger for every body method that has no serializer."""

    def get_request_serializer(self):
        serializer = super().get_request_serializer()
        if serializer is not None or self.method not in {"POST", "PUT", "PATCH"}:
            return serializer
        return {"application/json": OpenApiTypes.OBJECT}
