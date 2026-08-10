from django.contrib import admin
from .models import Category, Subject, Keyword, Metadata, FallbackThumbnail

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name"]


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ["name"]


admin.site.register(Keyword)
admin.site.register(Metadata)
@admin.register(FallbackThumbnail)
class FallbackThumbnailAdmin(admin.ModelAdmin):
    list_display = ["id", "category", "image_key", "usage_count"]
    list_filter = ["category"]
    readonly_fields = ["usage_count"]