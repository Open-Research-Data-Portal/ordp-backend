from django.http import HttpResponse


def index(request):
    return HttpResponse(
        """
        <html>
            <head><title>ORDP Backend</title></head>
            <body style="font-family: Arial, sans-serif; padding: 2rem;">
                <h1>ORDP backend is running</h1>
                <p>Use these routes:</p>
                <ul>
                    <li><a href="/admin/">/admin/</a></li>
                    <li><a href="/api/accounts/">/api/accounts/</a></li>
                    <li><a href="/api/datasets/">/api/datasets/</a></li>
                    <li><a href="/api/metadata/">/api/metadata/</a></li>
                    <li><a href="/api/search/">/api/search/</a></li>
                </ul>
            </body>
        </html>
        """,
        content_type="text/html",
    )