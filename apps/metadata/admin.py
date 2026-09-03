from django.contrib import admin
from .models import Category, Language, Keyword, Metadata, FallbackThumbnail

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name"]


admin.site.register(Keyword)
admin.site.register(Metadata)
admin.register(FallbackThumbnail)
class FallbackThumbnailAdmin(admin.ModelAdmin):
    list_display = ("category", "image_key", "usage_count")
    list_filter = ("category",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "category":
            kwargs["queryset"] = Category.objects.filter(
                origin=Category.Origin.STANDARD
            ).order_by("name")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ["name", "status"]
    list_filter = ["status"]