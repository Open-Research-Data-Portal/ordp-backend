from django.db import models


class DatasetLicense(models.TextChoices):
    CC0_1_0 = "CC0-1.0", "CC0 1.0 Universal"
    CC_BY_4_0 = "CC-BY-4.0", "CC BY 4.0 International"
    CC_BY_SA_4_0 = "CC-BY-SA-4.0", "CC BY-SA 4.0 International"
    AASTU_RESTRICTED = "AASTU-RESTRICTED", "AASTU Restricted License"
    CC_BY_NC_4_0 = "CC-BY-NC-4.0", "CC BY-NC 4.0 International"


DEFAULT_LICENSE_KEY = "CC-BY-4.0"

LICENSE_CATALOG = {
    "CC0-1.0": {
        "key": "CC0-1.0",
        "short_name": "CC0 1.0",
        "name": "CC0 1.0 Universal",
        "full_name": "Creative Commons Zero v1.0 Universal (CC0 1.0)",
        "description": "Free to share, adapt, and build upon for any purpose with no copyright restrictions worldwide.",
        "url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "is_restricted": False,
        "badge_color": "green",
    },
    "CC-BY-4.0": {
        "key": "CC-BY-4.0",
        "short_name": "CC BY 4.0",
        "name": "Creative Commons Attribution 4.0",
        "full_name": "Creative Commons Attribution 4.0 International (CC BY 4.0)",
        "description": "Allows redistribution and adaptation in any format or medium, provided credit is appropriately given to the creators.",
        "url": "https://creativecommons.org/licenses/by/4.0/",
        "is_restricted": False,
        "badge_color": "blue",
    },
    "CC-BY-SA-4.0": {
        "key": "CC-BY-SA-4.0",
        "short_name": "CC BY-SA 4.0",
        "name": "Creative Commons Attribution-ShareAlike 4.0",
        "full_name": "Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)",
        "description": "Allows remixing and adaptation even commercially, provided attribution is given and derivative works are licensed under identical terms.",
        "url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "is_restricted": False,
        "badge_color": "indigo",
    },
    "AASTU-RESTRICTED": {
        "key": "AASTU-RESTRICTED",
        "short_name": "AASTU Restricted",
        "name": "AASTU Restricted Research License",
        "full_name": "AASTU-Approved Restricted Research Data License",
        "description": "Data access is subject to institutional approval, non-commercial academic research only, strict confidentiality, and institutional data-governance rules.",
        "url": "https://www.aastu.edu.et",
        "is_restricted": True,
        "badge_color": "amber",
    },
    "CC-BY-NC-4.0": {
        "key": "CC-BY-NC-4.0",
        "short_name": "CC BY-NC 4.0",
        "name": "Creative Commons Attribution-NonCommercial 4.0",
        "full_name": "Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)",
        "description": "Allows non-commercial reuse and remixing with proper attribution. Commercial exploitation is prohibited without prior consent.",
        "url": "https://creativecommons.org/licenses/by-nc/4.0/",
        "is_restricted": False,
        "badge_color": "purple",
    },
}


def get_all_licenses_map(include_inactive=False):
    """Return built-in licenses merged with admin-created CustomLicense records."""
    catalog = {k: {**v, "is_custom": False, "is_active": True} for k, v in LICENSE_CATALOG.items()}
    try:
        from apps.datasets.models import CustomLicense
        qs = CustomLicense.objects.all() if include_inactive else CustomLicense.objects.filter(is_active=True)
        for custom in qs:
            if custom.key not in catalog:
                catalog[custom.key] = custom.to_catalog_dict()
    except Exception:
        pass
    return catalog


def is_valid_license(license_key):
    return bool(license_key and license_key in get_all_licenses_map())


def get_supported_licenses():
    return list(get_all_licenses_map().values())


def get_license_info(license_key):
    catalog = get_all_licenses_map(include_inactive=True)
    return catalog.get(license_key) or catalog.get(DEFAULT_LICENSE_KEY)


def get_system_default_license():
    try:
        from apps.admin_panel.models import SystemSetting
        setting = SystemSetting.objects.filter(key="default_dataset_license").first()
        if setting and setting.value in get_all_licenses_map():
            return setting.value
    except Exception:
        pass
    return DEFAULT_LICENSE_KEY
